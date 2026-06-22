"""Pull daily OHLCV from Yahoo (yfinance) and land it raw into bronze.prices (Iceberg).
Backfill mode (default): full history. Incremental: pass --days N for a daily append.
"""
import argparse
import pandas as pd
import pyarrow as pa
import yfinance as yf
from datetime import datetime, timezone
from pyiceberg.catalog import load_catalog

# Avoid SQLite lock contention from yfinance's on-disk cache when fetching
# multiple tickers concurrently inside a container — /tmp is always writable
# and ephemeral, which is fine for a once-a-day batch job.
yf.set_tz_cache_location("/tmp/yf_cache")

TICKERS = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","JPM","XOM","JNJ",
           "V","WMT","PG","MA","HD","BAC","KO","PEP","COST","DIS"]  # expand freely

def get_catalog():
    return load_catalog("rest", **{
        "type": "rest",
        "uri": "http://iceberg-rest:8181",
        "warehouse": "s3://warehouse/",
        "s3.endpoint": "http://minio:9000",
        "s3.access-key-id": "minioadmin",
        "s3.secret-access-key": "minioadmin",
        "s3.path-style-access": "true",
    })

def fetch(start: str, tickers=None) -> pd.DataFrame:
    tickers = tickers or TICKERS
    raw = yf.download(tickers, start=start, auto_adjust=False, group_by="ticker", progress=False)
    frames = []
    for t in tickers:
        if t not in raw.columns.get_level_values(0):
            continue
        s = raw[t].reset_index()
        s.columns = [c.lower().replace(" ", "_") for c in s.columns]
        s["ticker"] = t
        frames.append(s)
    df = pd.concat(frames, ignore_index=True)
    # Enforce clean, predictable types so the Iceberg schema is stable run-to-run
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for c in ["open", "high", "low", "close", "adj_close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    df["_ingested_at"] = datetime.now(timezone.utc)
    return df[["ticker","date","open","high","low","close","adj_close","volume","_ingested_at"]]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--days", type=int, default=None, help="if set, only fetch last N days")
    ap.add_argument("--tickers", default=None, help="comma-separated override, e.g. TSLA")
    args = ap.parse_args()
    start = args.start
    if args.days:
        start = (pd.Timestamp.utcnow() - pd.Timedelta(days=args.days)).strftime("%Y-%m-%d")

    tickers = args.tickers.split(",") if args.tickers else None
    df = fetch(start, tickers=tickers)
    arrow = pa.Table.from_pandas(df, preserve_index=False)

    cat = get_catalog()
    cat.create_namespace_if_not_exists("bronze")
    try:
        table = cat.load_table("bronze.prices")
    except Exception:
        table = cat.create_table("bronze.prices", schema=arrow.schema)
    table.append(arrow)
    print(f"Appended {arrow.num_rows} rows to bronze.prices")

if __name__ == "__main__":
    main()