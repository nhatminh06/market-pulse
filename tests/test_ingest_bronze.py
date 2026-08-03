import csv
from unittest.mock import patch

import pandas as pd
import pytest

from ingestion.ingest_bronze import fetch, load_tickers


def _write_ticker_csv(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ticker", "sector"])
        writer.writerows(rows)


def test_load_tickers_reads_ticker_column(tmp_path):
    csv_path = tmp_path / "ticker_sector_map.csv"
    _write_ticker_csv(csv_path, [["AAPL", "Technology"], ["JPM", "Financials"]])

    assert load_tickers(csv_path) == ["AAPL", "JPM"]


def _multiindex_frame(tickers):
    dates = pd.date_range("2024-01-01", periods=3, name="Date")
    columns = pd.MultiIndex.from_product(
        [tickers, ["Open", "High", "Low", "Close", "Adj Close", "Volume"]]
    )
    data = {}
    for t in tickers:
        for field in ["Open", "High", "Low", "Close", "Adj Close"]:
            data[(t, field)] = [100.0, 101.0, 102.0]
        data[(t, "Volume")] = [1000, 1100, 1200]
    return pd.DataFrame(data, index=dates, columns=columns)


def test_fetch_parses_multiindex_columns():
    raw = _multiindex_frame(["AAPL", "MSFT"])
    with patch("ingestion.ingest_bronze.yf.download", return_value=raw):
        df = fetch("2024-01-01", tickers=["AAPL", "MSFT"])

    assert set(df["ticker"]) == {"AAPL", "MSFT"}
    assert len(df) == 6
    assert list(df.columns) == [
        "ticker", "date", "open", "high", "low", "close", "adj_close", "volume", "_ingested_at",
    ]


def test_fetch_warns_on_missing_ticker(caplog):
    raw = _multiindex_frame(["AAPL"])
    with patch("ingestion.ingest_bronze.yf.download", return_value=raw):
        with caplog.at_level("WARNING"):
            df = fetch("2024-01-01", tickers=["AAPL", "ZZZZ"])

    assert set(df["ticker"]) == {"AAPL"}
    assert "ZZZZ" in caplog.text


def test_fetch_raises_when_no_ticker_has_data():
    raw = _multiindex_frame(["AAPL"])
    with patch("ingestion.ingest_bronze.yf.download", return_value=raw):
        with pytest.raises(RuntimeError, match="no data"):
            fetch("2024-01-01", tickers=["ZZZZ"])
