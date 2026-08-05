"""Run a query file N times against either Trino/Iceberg or DuckDB/raw-Parquet
and record per-run timings as JSON. Does not compute statistics itself —
see summarize_results.py.

Usage:
    python benchmarks/run_query_benchmark.py \
        --engine trino --query benchmarks/queries/iceberg.sql \
        --warmup 2 --runs 10 --out benchmarks/results/iceberg.json

    python benchmarks/run_query_benchmark.py \
        --engine duckdb --query benchmarks/queries/raw_parquet.sql \
        --parquet-glob "/path/to/warehouse/gold/fct_daily_metrics/data/*.parquet" \
        --warmup 2 --runs 10 --out benchmarks/results/raw_parquet.json

Requires a running stack for --engine trino (`docker compose up`), or a
local copy of the Iceberg table's Parquet data files for --engine duckdb.
Neither is available in this environment — this script is provided so
results can be produced and committed by whoever runs it, not run here.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def run_trino(query: str, host: str, port: int, catalog: str) -> float:
    from trino.dbapi import connect

    conn = connect(host=host, port=port, user="benchmark", catalog=catalog)
    cur = conn.cursor()
    start = time.perf_counter()
    cur.execute(query)
    cur.fetchall()
    return time.perf_counter() - start


def run_duckdb(query: str, parquet_glob: str) -> float:
    import duckdb

    sql = query.format(parquet_glob=parquet_glob)
    start = time.perf_counter()
    duckdb.sql(sql).fetchall()
    return time.perf_counter() - start


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["trino", "duckdb"], required=True)
    ap.add_argument("--query", required=True, type=Path)
    ap.add_argument("--parquet-glob", default=None, help="required for --engine duckdb")
    ap.add_argument("--trino-host", default="localhost")
    ap.add_argument("--trino-port", type=int, default=8080)
    ap.add_argument("--trino-catalog", default="iceberg")
    ap.add_argument("--warmup", type=int, default=2, help="untimed warm-up runs")
    ap.add_argument("--runs", type=int, default=10, help="timed runs")
    ap.add_argument("--label", default=None, help="condition label, e.g. 'fresh-service-start'")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    if args.engine == "duckdb" and not args.parquet_glob:
        ap.error("--parquet-glob is required for --engine duckdb")

    query = args.query.read_text()

    for i in range(args.warmup):
        try:
            if args.engine == "trino":
                run_trino(query, args.trino_host, args.trino_port, args.trino_catalog)
            else:
                run_duckdb(query, args.parquet_glob)
        except Exception as exc:
            print(f"warm-up run {i} failed: {exc}", file=sys.stderr)
            raise

    timings = []
    failures = []
    for i in range(args.runs):
        try:
            if args.engine == "trino":
                elapsed = run_trino(query, args.trino_host, args.trino_port, args.trino_catalog)
            else:
                elapsed = run_duckdb(query, args.parquet_glob)
            timings.append(elapsed)
            print(f"run {i}: {elapsed:.4f}s")
        except Exception as exc:  # noqa: BLE001
            failures.append(str(exc))
            print(f"run {i}: FAILED ({exc})", file=sys.stderr)

    result = {
        "engine": args.engine,
        "query_file": str(args.query),
        "label": args.label,
        "warmup_runs": args.warmup,
        "requested_runs": args.runs,
        "completed_runs": len(timings),
        "timings_seconds": timings,
        "failures": failures,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print(f"Wrote {len(timings)} timing(s) to {args.out}")


if __name__ == "__main__":
    main()
