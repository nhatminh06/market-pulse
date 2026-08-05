-- Raw Parquet baseline: logically equivalent aggregation, run directly
-- against the underlying Parquet data files (bypassing Iceberg's catalog,
-- manifests, and column statistics) via DuckDB. This isolates what Iceberg's
-- metadata layer buys you versus a bare file scan — NOT a second SQL engine
-- vs. Trino comparison, since that would conflate engine differences with
-- table-format differences. See run_query_benchmark.py --engine duckdb.
select
    ticker,
    date_trunc('month', trade_date) as month,
    avg(volatility_30d_ann) as avg_monthly_volatility
from read_parquet('{parquet_glob}')
where ticker = 'AAPL'
group by ticker, date_trunc('month', trade_date)
order by month
