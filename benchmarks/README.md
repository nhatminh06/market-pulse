# Benchmarks

Scripts for producing reproducible query-performance numbers. Full
methodology and (once run) results live in [`docs/benchmarks.md`](../docs/benchmarks.md) —
this directory holds only the mechanics.

**Status: scaffolding only.** These scripts have not been run in this
environment (no live Docker stack available) — see `docs/benchmarks.md` for
what's verified vs. what requires manual execution.

```
benchmarks/
├── queries/
│   ├── iceberg.sql       -- gold-layer aggregation via Trino/Iceberg
│   └── raw_parquet.sql   -- same aggregation via DuckDB directly on Parquet files
├── run_query_benchmark.py  -- runs one query N times, records timings as JSON
├── summarize_results.py    -- median/min/max/stdev + %% diff between two result files
└── results/                -- gitignored (machine-specific); commit a labeled
                                example manually if you want one checked in
```

## Running a benchmark

Requires the full stack up (`docker compose up -d`) for the Trino side, and
`pip install duckdb trino` for both scripts.

```bash
python benchmarks/run_query_benchmark.py \
  --engine trino --query benchmarks/queries/iceberg.sql \
  --warmup 2 --runs 10 --label "warm-repeated-query" \
  --out benchmarks/results/iceberg.json

python benchmarks/run_query_benchmark.py \
  --engine duckdb --query benchmarks/queries/raw_parquet.sql \
  --parquet-glob "$(pwd)/warehouse-export/gold/fct_daily_metrics/data/*.parquet" \
  --warmup 2 --runs 10 --label "warm-repeated-query" \
  --out benchmarks/results/raw_parquet.json

python benchmarks/summarize_results.py \
  benchmarks/results/iceberg.json benchmarks/results/raw_parquet.json --compare
```

The DuckDB side needs a local copy of the gold table's Parquet files (export
them from MinIO first, e.g. `mc cp --recursive local/warehouse/gold ./warehouse-export/gold`).
