"""Pull daily OHLCV from Yahoo (yfinance) and land it raw into bronze.prices (Iceberg).
Backfill mode (default): full history. Incremental: pass --days N for a daily append.

Split into testable stages so unit tests don't need network access or a live
Iceberg/MinIO stack:
  fetch_market_data      -- the only function that calls yfinance
  normalize_ohlcv_frame  -- multiindex -> tidy long frame, casing, typing
  validate_ohlcv_frame   -- required columns/values, drops+reports bad rows
  deduplicate_prices     -- collapse duplicate (ticker, date) within a batch
  determine_incremental_start -- --days N -> an ISO start date
  write_bronze_table     -- the only function that touches Iceberg
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import yfinance as yf
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import And, GreaterThanOrEqual, In

# Avoid SQLite lock contention from yfinance's on-disk cache when fetching
# multiple tickers concurrently inside a container — /tmp is always writable
# and ephemeral, which is fine for a once-a-day batch job.
yf.set_tz_cache_location("/tmp/yf_cache")

# Single source of truth for the ticker universe, shared with dbt's
# gold/dim_ticker.sql via the same seed file — do not hardcode tickers elsewhere.
TICKER_SECTOR_MAP_PATH = (
    Path(__file__).resolve().parent.parent
    / "dbt" / "market_pulse" / "seeds" / "ticker_sector_map.csv"
)

REQUIRED_COLUMNS = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]
BRONZE_COLUMNS = REQUIRED_COLUMNS + ["_ingested_at"]

logger = logging.getLogger(__name__)


def load_tickers(path: Path = TICKER_SECTOR_MAP_PATH) -> list[str]:
    with open(path, newline="") as f:
        return [row["ticker"] for row in csv.DictReader(f)]


def determine_incremental_start(default_start: str, days: int | None, now: pd.Timestamp | None = None) -> str:
    """Return an ISO date string: `default_start` for a full backfill, or
    `now - days` for an incremental run."""
    if not days:
        return default_start
    now = now or pd.Timestamp.utcnow()
    return (now - pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def fetch_market_data(tickers: list[str], start: str) -> pd.DataFrame:
    """The only function in this module that calls out to yfinance. Kept thin
    and untested directly — its output shape is exercised via fixtures in
    normalize_ohlcv_frame instead."""
    return yf.download(tickers, start=start, auto_adjust=False, group_by="ticker", progress=False)


def normalize_ohlcv_frame(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Turn yfinance's wide (possibly multiindex-by-ticker) frame into one tidy
    row per ticker/date, with normalized column names and upper-cased tickers.
    Tickers with no returned data are logged and skipped, not silently dropped."""
    frames = []
    missing_tickers = []
    is_multiindex = isinstance(raw.columns, pd.MultiIndex)
    available_tickers = set(raw.columns.get_level_values(0)) if is_multiindex else set()

    for t in tickers:
        if is_multiindex:
            if t not in available_tickers:
                missing_tickers.append(t)
                continue
            s = raw[t].reset_index()
        elif len(tickers) == 1 and not raw.empty:
            s = raw.reset_index()
        else:
            missing_tickers.append(t)
            continue

        s.columns = [c.lower().replace(" ", "_") for c in s.columns]
        s["ticker"] = t.upper()
        frames.append(s)

    if missing_tickers:
        logger.warning("No data returned for tickers: %s", ", ".join(missing_tickers))
    if not frames:
        raise RuntimeError(f"yfinance returned no data for any requested tickers: {', '.join(tickers)}")

    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for c in ["open", "high", "low", "close", "adj_close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")
    df["_ingested_at"] = datetime.now(timezone.utc)
    return df[BRONZE_COLUMNS]


def validate_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Enforce the bronze contract: required columns present; ticker/date/
    open/high/low/close/volume all non-null (a price bar missing any of
    these is not usable and is dropped, not coerced); close positive and
    volume non-negative; low <= open <= high and low <= close <= high.
    These range checks compare RAW open/high/low/close only — adj_close is
    deliberately excluded, since split/dividend adjustment is applied
    uniformly to a whole day's OHLC and adjusted values are never compared
    against raw ones anywhere in this pipeline (see docs/data-model.md).
    adj_close itself MAY be null — yfinance can omit it for freshly-listed
    or delisted tickers, and the gold layer's daily_return calc already
    guards against a missing prior adj_close via nullif(). Invalid rows are
    dropped and the count is logged — never silently discarded without a
    trace in the run log."""
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required column(s): {missing_cols}")
    if df.empty:
        return df

    # Coerce to numeric first: pandas' vectorized `|`/`&` evaluate both sides
    # eagerly (no short-circuiting), so an object-dtype column containing
    # None raises TypeError on `<=` even inside an isna()-guarded branch.
    low = pd.to_numeric(df["low"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")

    def _within_range(series: pd.Series) -> pd.Series:
        series = pd.to_numeric(series, errors="coerce")
        return series.notna() & (low <= series) & (series <= high)

    valid = (
        df["ticker"].notna()
        & (df["ticker"].astype(str).str.strip() != "")
        & df["date"].notna()
        & low.notna()
        & high.notna()
        & df["close"].notna()
        & (df["close"] > 0)
        & df["volume"].notna()
        & (df["volume"] >= 0)
        & (low <= high)
        & _within_range(df["open"])
        & _within_range(df["close"])
    )
    n_invalid = (~valid).sum()
    if n_invalid:
        logger.warning("Dropping %d row(s) failing bronze validation", n_invalid)
    return df[valid].reset_index(drop=True)


def deduplicate_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate (ticker, date) rows within a single fetched batch,
    keeping the most recently ingested one. Cross-batch/rerun duplicates are
    handled separately by write_bronze_table's delete-then-append and by the
    silver dbt model's merge; this only protects a single fetch() call."""
    if df.empty:
        return df
    return (
        df.sort_values("_ingested_at")
        .drop_duplicates(subset=["ticker", "date"], keep="last")
        .reset_index(drop=True)
    )


def get_catalog() -> Catalog:
    try:
        access_key = os.environ["PIPELINE_ACCESS_KEY"]
        secret_key = os.environ["PIPELINE_SECRET_KEY"]
    except KeyError as exc:
        raise RuntimeError(
            f"Missing required env var {exc}: set PIPELINE_ACCESS_KEY and "
            "PIPELINE_SECRET_KEY (see .env.example)."
        ) from None

    return load_catalog("rest", **{
        "type": "rest",
        "uri": "http://iceberg-rest:8181",
        "warehouse": "s3://warehouse/",
        "s3.endpoint": "http://minio:9000",
        "s3.access-key-id": access_key,
        "s3.secret-access-key": secret_key,
        "s3.path-style-access": "true",
    })


def write_bronze_table(cat: Catalog, df: pd.DataFrame, requested_tickers: list[str], start: str) -> int:
    """Delete-then-append into bronze.prices, scoped to the tickers/date range
    just fetched, so reruns of the same window are idempotent at the bronze
    layer (no duplicate ticker/date rows survive a rerun covering the same
    range). Reruns with a different --start/--tickers window are NOT
    deduplicated against unrelated prior data by this function alone — the
    silver layer's merge on (ticker, trade_date) is the final guarantee."""
    arrow = pa.Table.from_pandas(df, preserve_index=False)
    cat.create_namespace_if_not_exists("bronze")
    try:
        table = cat.load_table("bronze.prices")
        table.delete(And(GreaterThanOrEqual("date", start), In("ticker", requested_tickers)))
    except NoSuchTableError:
        table = cat.create_table("bronze.prices", schema=arrow.schema)
    table.append(arrow)
    return arrow.num_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--days", type=int, default=None, help="if set, only fetch last N days")
    ap.add_argument("--tickers", default=None, help="comma-separated override, e.g. TSLA")
    args = ap.parse_args()

    start = determine_incremental_start(args.start, args.days)
    tickers = [t.upper() for t in args.tickers.split(",")] if args.tickers else load_tickers()

    raw = fetch_market_data(tickers, start)
    df = normalize_ohlcv_frame(raw, tickers)
    df = validate_ohlcv_frame(df)
    df = deduplicate_prices(df)

    if df.empty:
        logger.warning("No valid rows to write after validation/dedup; skipping bronze write.")
        return

    cat = get_catalog()
    n_rows = write_bronze_table(cat, df, tickers, start)
    logger.info("Appended %d rows to bronze.prices", n_rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
