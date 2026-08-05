"""End-to-end smoke test for the ingestion-side pipeline stages against a
fixture, with no network or live services: normalize -> validate -> dedupe.
This does not cover the dbt/Trino half of the pipeline (silver/gold SQL) —
that requires a live stack and is exercised by the manual/scheduled E2E
workflow (.github/workflows/e2e.yml), not by pytest."""
import pandas as pd

from ingestion.ingest_bronze import (
    deduplicate_prices,
    normalize_ohlcv_frame,
    validate_ohlcv_frame,
)


def test_fixture_flows_through_normalize_validate_dedupe(fixtures_dir):
    raw = pd.read_csv(fixtures_dir / "valid_ohlcv.csv")
    # normalize_ohlcv_frame expects yfinance's raw shape (single-ticker,
    # non-multiindex, already-flat columns); build that shape directly here
    # since this fixture is already tidy — the multiindex parsing path is
    # covered separately in tests/unit/test_ingestion_schema.py via fetch().
    tickers = sorted(raw["ticker"].unique())
    frames = []
    for t in tickers:
        sub = raw[raw["ticker"] == t].drop(columns=["ticker"]).reset_index(drop=True)
        sub.columns = [c.title() if c != "date" else "Date" for c in sub.columns]
        normalized = normalize_ohlcv_frame(sub, [t])
        frames.append(normalized)
    df = pd.concat(frames, ignore_index=True)

    df = validate_ohlcv_frame(df)
    df = deduplicate_prices(df)

    assert len(df) == len(raw)
    assert set(df["ticker"]) == set(tickers)
    assert df.groupby(["ticker", "date"]).size().eq(1).all()
