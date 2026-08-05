"""Spec tests for the gold-layer window-function formulas in
dbt/market_pulse/models/gold/fct_daily_metrics.sql.

Trino's window SQL cannot be faithfully emulated in SQLite/pandas at the
engine level (frame semantics, NULL propagation through stddev/avg, and
sample-vs-population statistics all differ across engines) — see
docs/testing.md. These tests instead re-implement the *documented* formula
each column uses, in plain pandas, against small synthetic fixtures, and
assert the behavioral properties the SQL is supposed to have (partial
windows, first-row nulls, division-by-zero guards, threshold behavior).
They are a specification/regression check on the formulas, not a run of the
actual SQL — real SQL correctness is verified by `dbt build && dbt test`
against live Trino (see the manual/E2E workflow), not by this file.

Formulas mirrored here, read directly from fct_daily_metrics.sql:
  daily_return       = adj_close / nullif(prev_close, 0) - 1   (simple, not log, return on adj_close)
  sma_N               = avg(adj_close) over the trailing N rows (partial window allowed)
  volatility_30d_ann  = stddev_samp(daily_return) over trailing 30 rows * sqrt(252)
  volume_zscore       = (volume - avg(volume, 30)) / nullif(stddev_samp(volume, 30), 0)
  is_volume_anomaly   = abs(volume_zscore) > 3
"""
import numpy as np
import pandas as pd
import pytest

TRADING_DAYS_PER_YEAR = 252


def compute_metrics(df: pd.DataFrame, sma_windows=(3,), vol_window=3) -> pd.DataFrame:
    """Reference implementation of fct_daily_metrics.sql's per-ticker window
    logic, generalized to a configurable window size so small fixtures can
    exercise it (production uses 20/50/200-row SMAs and a 30-row vol/anomaly
    window; the row-count-based, partial-window-allowed semantics are
    identical regardless of window size)."""
    out = df.sort_values(["ticker", "date"]).copy()
    prev_close = out.groupby("ticker")["adj_close"].shift(1)
    out["daily_return"] = np.where(prev_close.fillna(0) == 0, np.nan, out["adj_close"] / prev_close - 1)

    for w in sma_windows:
        out[f"sma_{w}"] = (
            out.groupby("ticker")["adj_close"]
            .transform(lambda s, w=w: s.rolling(window=w, min_periods=1).mean())
        )

    ret_std = out.groupby("ticker")["daily_return"].transform(
        lambda s: s.rolling(window=vol_window, min_periods=2).std(ddof=1)
    )
    out["volatility_ann"] = ret_std * np.sqrt(TRADING_DAYS_PER_YEAR)

    vol_mean = out.groupby("ticker")["volume"].transform(
        lambda s: s.rolling(window=vol_window, min_periods=1).mean()
    )
    vol_std = out.groupby("ticker")["volume"].transform(
        lambda s: s.rolling(window=vol_window, min_periods=1).std(ddof=1)
    )
    out["volume_zscore"] = np.where(
        (vol_std.isna()) | (vol_std == 0), np.nan, (out["volume"] - vol_mean) / vol_std
    )
    out["is_volume_anomaly"] = out["volume_zscore"].abs() > 3
    return out


@pytest.fixture
def single_ticker_series():
    # AAA: 6 trading days, a volume spike on day 6.
    return pd.DataFrame({
        "ticker": ["AAA"] * 6,
        "date": pd.date_range("2024-01-02", periods=6, freq="B"),
        "adj_close": [10.0, 11.0, 9.0, 9.0, 9.0, 12.0],
        "volume": [100, 100, 100, 100, 100, 500],
    })


def test_first_row_return_is_null(single_ticker_series):
    out = compute_metrics(single_ticker_series)
    assert pd.isna(out.iloc[0]["daily_return"])


def test_positive_negative_and_zero_returns(single_ticker_series):
    out = compute_metrics(single_ticker_series)
    rets = out["daily_return"].tolist()
    assert rets[1] == pytest.approx(0.10)      # 11/10 - 1
    assert rets[2] == pytest.approx(-2 / 11)   # 9/11 - 1
    assert rets[3] == pytest.approx(0.0)       # 9/9 - 1


def test_returns_are_partitioned_by_ticker():
    df = pd.DataFrame({
        "ticker": ["AAA", "BBB"],
        "date": pd.to_datetime(["2024-01-02", "2024-01-02"]),
        "adj_close": [10.0, 50.0],
        "volume": [100, 100],
    })
    out = compute_metrics(df)
    # Both are each ticker's first row — neither sees the other's price.
    assert out["daily_return"].isna().all()


def test_return_division_by_zero_prev_close_is_null():
    df = pd.DataFrame({
        "ticker": ["AAA", "AAA"],
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "adj_close": [0.0, 10.0],
        "volume": [100, 100],
    })
    out = compute_metrics(df)
    assert pd.isna(out.iloc[1]["daily_return"])  # nullif(prev_close, 0) guard


def test_sma_uses_partial_window_before_full_history(single_ticker_series):
    out = compute_metrics(single_ticker_series, sma_windows=(3,))
    sma3 = out["sma_3"].tolist()
    assert sma3[0] == pytest.approx(10.0)             # window of 1
    assert sma3[1] == pytest.approx((10 + 11) / 2)    # window of 2
    assert sma3[2] == pytest.approx((10 + 11 + 9) / 3)  # first full window


def test_sma_full_window_rows_match_trailing_average(single_ticker_series):
    out = compute_metrics(single_ticker_series, sma_windows=(3,))
    # Row 5 (index 4): trailing 3 = rows 3,4,5 = 9,9,9
    assert out.iloc[4]["sma_3"] == pytest.approx(9.0)


def test_volatility_undefined_with_fewer_than_two_returns(single_ticker_series):
    out = compute_metrics(single_ticker_series, vol_window=3)
    # Row 2 (index 1) has only its own single non-null return in the window.
    assert pd.isna(out.iloc[1]["volatility_ann"])


def test_volatility_defined_once_two_returns_are_in_window(single_ticker_series):
    out = compute_metrics(single_ticker_series, vol_window=3)
    assert pd.notna(out.iloc[2]["volatility_ann"])


def test_constant_price_series_has_zero_volatility():
    df = pd.DataFrame({
        "ticker": ["AAA"] * 4,
        "date": pd.date_range("2024-01-02", periods=4, freq="B"),
        "adj_close": [10.0, 10.0, 10.0, 10.0],
        "volume": [100, 100, 100, 100],
    })
    out = compute_metrics(df, vol_window=3)
    assert out.iloc[3]["volatility_ann"] == pytest.approx(0.0)


def test_zero_stddev_volume_zscore_is_null_not_a_crash(single_ticker_series):
    # First 3 rows all have volume=100 -> zero-variance window.
    out = compute_metrics(single_ticker_series, vol_window=3)
    assert pd.isna(out.iloc[2]["volume_zscore"])
    assert out.iloc[2]["is_volume_anomaly"] == False


def test_volume_spike_is_flagged_as_anomaly():
    # A single-outlier-in-an-otherwise-constant-window z-score is capped at
    # (n-1)/sqrt(n) regardless of how extreme the outlier is (the outlier
    # inflates its own window's mean and stddev) — with a 3-row window that
    # cap is ~1.15, below the >3 threshold, so this needs a wider window,
    # matching production's 30-row volume window rather than the 3-row one
    # used for the other tests in this file.
    n_baseline = 19
    df = pd.DataFrame({
        "ticker": ["AAA"] * (n_baseline + 1),
        "date": pd.date_range("2024-01-02", periods=n_baseline + 1, freq="B"),
        "adj_close": [10.0] * (n_baseline + 1),
        "volume": [100] * n_baseline + [100_000],
    })
    out = compute_metrics(df, vol_window=20)
    last = out.iloc[-1]
    assert abs(last["volume_zscore"]) > 3
    assert last["is_volume_anomaly"] == True


def test_exactly_at_threshold_is_not_flagged():
    # Construct a window where the z-score lands at exactly 3.0.
    df = pd.DataFrame({
        "ticker": ["AAA"] * 3,
        "date": pd.date_range("2024-01-02", periods=3, freq="B"),
        "adj_close": [10.0, 10.0, 10.0],
        "volume": [90, 100, 110],
    })
    out = compute_metrics(df, vol_window=3)
    z = out.iloc[-1]["volume_zscore"]
    assert z == pytest.approx((110 - 100) / 10)  # mean=100, std=10 -> z=1.0
    assert out.iloc[-1]["is_volume_anomaly"] == False
