"""Focused tests for each bronze validation rule in isolation (schema-level
coverage lives in test_ingestion_schema.py; this file drills into each rule)."""
import pandas as pd
import pytest

from ingestion.ingest_bronze import validate_ohlcv_frame

BASE_ROW = {
    "ticker": "AAPL",
    "date": pd.Timestamp("2024-01-02").date(),
    "open": 100.0,
    "high": 102.0,
    "low": 99.0,
    "close": 101.0,
    "adj_close": 101.0,
    "volume": 1000,
}


def _frame(**overrides):
    row = {**BASE_ROW, **overrides}
    return pd.DataFrame([row])


@pytest.mark.parametrize("required_field", ["ticker", "date", "open", "high", "low", "close", "volume"])
def test_null_required_fields_are_dropped(required_field):
    df = _frame(**{required_field: None})
    assert validate_ohlcv_frame(df).empty


def test_null_adj_close_is_allowed():
    df = _frame(adj_close=None)
    out = validate_ohlcv_frame(df)
    assert len(out) == 1
    assert pd.isna(out.iloc[0]["adj_close"])


@pytest.mark.parametrize("volume", [-1, -1000])
def test_negative_volume_is_dropped(volume):
    df = _frame(volume=volume)
    assert validate_ohlcv_frame(df).empty


def test_zero_volume_is_allowed():
    df = _frame(volume=0)
    assert len(validate_ohlcv_frame(df)) == 1


@pytest.mark.parametrize("close", [0, -1.0, -100.5])
def test_non_positive_close_is_dropped(close):
    df = _frame(close=close)
    assert validate_ohlcv_frame(df).empty


def test_low_greater_than_high_is_dropped():
    df = _frame(low=105.0, high=100.0)
    assert validate_ohlcv_frame(df).empty


def test_low_equal_to_high_is_allowed():
    df = _frame(low=100.0, high=100.0, open=100.0, close=100.0)
    assert len(validate_ohlcv_frame(df)) == 1


def test_open_outside_low_high_range_is_dropped():
    df = _frame(open=98.0, low=99.0, high=102.0)  # open < low
    assert validate_ohlcv_frame(df).empty


def test_close_outside_low_high_range_is_dropped():
    df = _frame(close=103.0, low=99.0, high=102.0)  # close > high
    assert validate_ohlcv_frame(df).empty


def test_open_and_close_at_the_range_boundary_are_allowed():
    df = _frame(open=99.0, close=102.0, low=99.0, high=102.0)
    assert len(validate_ohlcv_frame(df)) == 1


def test_adj_close_is_never_checked_against_raw_low_high():
    # adj_close is deliberately excluded from the range check — split/
    # dividend adjustment can legitimately move it outside the raw day's
    # low/high band (see docs/data-model.md).
    df = _frame(adj_close=500.0, low=99.0, high=102.0)
    assert len(validate_ohlcv_frame(df)) == 1
