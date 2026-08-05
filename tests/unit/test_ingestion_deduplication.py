import pandas as pd

from ingestion.ingest_bronze import deduplicate_prices


def _load(fixtures_dir):
    return pd.read_csv(
        fixtures_dir / "duplicate_ohlcv.csv",
        parse_dates=["date", "_ingested_at"],
    )


def test_duplicate_ticker_date_collapses_to_one_row(fixtures_dir):
    df = _load(fixtures_dir)
    out = deduplicate_prices(df)

    key = out.groupby(["ticker", "date"]).size()
    assert (key == 1).all()
    assert len(out) == 3  # 4 input rows, 1 duplicate pair


def test_duplicate_keeps_the_latest_ingested_row(fixtures_dir):
    df = _load(fixtures_dir)
    out = deduplicate_prices(df)

    aapl_jan2 = out[(out["ticker"] == "AAPL") & (out["date"] == pd.Timestamp("2024-01-02"))]
    assert len(aapl_jan2) == 1
    assert aapl_jan2.iloc[0]["close"] == 101.5  # the later-ingested value, not 101.0


def test_different_tickers_same_date_are_not_deduplicated_away(fixtures_dir):
    df = _load(fixtures_dir)
    out = deduplicate_prices(df)

    jan2 = out[out["date"] == pd.Timestamp("2024-01-02")]
    assert set(jan2["ticker"]) == {"AAPL", "MSFT"}


def test_same_ticker_different_dates_are_not_deduplicated_away(fixtures_dir):
    df = _load(fixtures_dir)
    out = deduplicate_prices(df)

    aapl = out[out["ticker"] == "AAPL"]
    assert set(aapl["date"]) == {pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")}


def test_empty_frame_is_a_no_op():
    empty = pd.DataFrame(columns=["ticker", "date", "close", "_ingested_at"])
    out = deduplicate_prices(empty)
    assert out.empty
