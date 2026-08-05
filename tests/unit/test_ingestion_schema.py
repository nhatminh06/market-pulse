import pandas as pd
import pytest

from ingestion.ingest_bronze import REQUIRED_COLUMNS, validate_ohlcv_frame


def _load(fixtures_dir, name):
    return pd.read_csv(fixtures_dir / name, parse_dates=["date"])


def test_valid_fixture_passes_unchanged(fixtures_dir):
    df = _load(fixtures_dir, "valid_ohlcv.csv")
    out = validate_ohlcv_frame(df)
    assert len(out) == len(df)


def test_missing_required_column_raises():
    df = pd.DataFrame({"ticker": ["AAPL"], "date": ["2024-01-02"]})
    with pytest.raises(ValueError, match="Missing required column"):
        validate_ohlcv_frame(df)


def test_empty_frame_returns_empty(fixtures_dir):
    df = _load(fixtures_dir, "valid_ohlcv.csv").iloc[0:0]
    out = validate_ohlcv_frame(df)
    assert out.empty


def test_invalid_fixture_drops_only_the_valid_row(fixtures_dir):
    df = _load(fixtures_dir, "invalid_prices.csv")
    out = validate_ohlcv_frame(df)

    # 6 input rows: 1 valid, 1 empty ticker, 1 null close, 1 negative close,
    # 1 low > high, 1 negative volume.
    assert len(out) == 1
    assert out.iloc[0]["ticker"] == "AAPL"
    assert out.iloc[0]["date"] == pd.Timestamp("2024-01-02")


def test_all_required_columns_present_in_fixture(fixtures_dir):
    df = _load(fixtures_dir, "valid_ohlcv.csv")
    assert set(REQUIRED_COLUMNS).issubset(df.columns)
