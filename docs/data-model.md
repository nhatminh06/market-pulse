# Data model

## Layer summary

| Layer | Table | Grain | Key | Materialization | Partitioning |
|---|---|---|---|---|---|
| Bronze | `bronze.prices` | one row per (ticker, date) within any window that's been (re)fetched | none enforced (raw landing zone) | Iceberg table, exact-key transaction overwrite | `ticker` |
| Silver | `silver.stg_prices` | one row per (ticker, trade_date) | `(ticker, trade_date)`, enforced by `dbt_utils.unique_combination_of_columns` | table | `ticker` |
| Gold | `gold.fct_daily_metrics` | one row per (ticker, trade_date) | `(ticker, trade_date)`, enforced by `dbt_utils.unique_combination_of_columns` | table | `ticker` |
| Gold | `gold.dim_ticker` | one row per ticker | `ticker`, enforced by `unique` | seed-joined view | n/a |

## Bronze: `bronze.prices`

Raw landing zone. Bronze is **not append-only**. `write_bronze_table` validates
and deduplicates the returned batch, then builds a predicate for exactly those
`(ticker, date)` keys. It stages a PyIceberg 0.7.1 filtered overwrite and
explicitly commits the transaction. Delete/rewrite and append may create
multiple staged Iceberg snapshots, but one catalog metadata commit publishes
the final state; a failed pre-commit stage leaves the previous table visible.
The code does not use PyIceberg 0.7.1's transaction context manager because its
`__exit__` commits even after an exception in the body.

- **Current-state grain:** one row per returned key written by this ingester.
  Repeated runs replace the same keys. Existing duplicate keys created by
  other writers are outside this writer's guarantee until those keys are
  explicitly replaced.
- **Missing-data policy:** missing tickers or dates do not mean deletion.
  Only returned, valid keys are replaced; absent keys retain their old rows.
  Invalid rows are counted in logs and omitted before the write. Empty valid
  batches publish nothing. No market-calendar completeness is inferred.
- **Recovery:** a known catalog conflict fails so a new invocation can fetch
  and plan against fresh state. After an uncertain commit acknowledgement,
  the writer reloads the catalog and confirms both its per-attempt run ID in
  Iceberg snapshot metadata and matching current rows before reporting
  success. Otherwise it raises `CommitOutcomeUnknownError`. Airflow does not
  automatically retry ingestion; an operator must reconcile an unresolved
  result using the logged run ID. A new process has a new run ID, so it cannot
  prove a prior ambiguous commit by ID alone.
- **Concurrency:** one Airflow DAG run is active at a time, but external
  writers can overlap. Iceberg snapshot requirements reject a stale writer;
  the application does not silently replay a conflicting batch. The REST
  catalog and Trino path still need branch E2E qualification.
- **Cost:** PyIceberg's filtered delete uses copy-on-write for partially
  matching Parquet files. The exact-key expression groups common date/ticker
  coverage and refuses more than 10,000 predicate literals. A larger backfill
  must be planned explicitly; the code does not silently split a write into
  several visible commits. Pre-commit files may become orphaned after failure
  and require normal Iceberg maintenance.

Prior snapshots remain available for time travel until expiration. The
`_ingested_at` value changes on a fresh ingestion, even if market prices are
unchanged; logical price rows remain unique by key.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `ticker` | string | no | upper-cased in `normalize_ohlcv_frame` |
| `date` | date | no | |
| `open`, `high`, `low`, `close` | double | no | raw (unadjusted) prices; a bar missing any of these is dropped by `validate_ohlcv_frame`, not written to bronze |
| `adj_close` | double | yes | split/dividend-adjusted close; can be null for freshly-listed/delisted tickers |
| `volume` | bigint | no | must be >= 0 |
| `_ingested_at` | timestamp | no | used as the dedup tiebreaker in silver and as the dbt source freshness field |

## Silver: `silver.stg_prices`

Cleaned, typed, deduplicated. Built from `bronze.prices` by:
1. Filtering `close is not null and close > 0 and volume >= 0 and low <= high`,
   plus `open`/`close` each within `[low, high]` — the same OHLC-consistency
   rule `ingestion/ingest_bronze.py:validate_ohlcv_frame` already applies
   before a row ever reaches bronze; kept here too as defense-in-depth in
   case bronze is ever populated by something other than that script.
2. Deduplicating to one row per (ticker, trade_date), keeping the row with
   the latest `_ingested_at`.

Consumers: `gold.fct_daily_metrics`, `gold.dim_ticker`.

## Gold: `gold.dim_ticker`

Sourced from `dbt/market_pulse/seeds/ticker_sector_map.csv` — the single
source of truth for the ticker universe and sector mapping, also read
directly by `ingestion/ingest_bronze.py` so the two never drift
independently. Tickers present in `stg_prices` but absent from the seed
map get `sector = 'UNMAPPED'`, which the `accepted_values` test on `sector`
will correctly fail — that failure means the seed needs updating, not that
the pipeline is broken.

## Gold: `gold.fct_daily_metrics`

One row per (ticker, trade_date), adding return/moving-average/volatility/
anomaly columns computed via window functions partitioned by `ticker` and
ordered by `trade_date`. See `dbt/market_pulse/models/gold/fct_daily_metrics.sql`
for the exact formulas and `docs/testing.md` / `tests/unit/test_metric_calculations.py`
for how they're specified and tested.

| Column | Formula (informal) | Null when |
|---|---|---|
| `daily_return` | `adj_close / prev_adj_close - 1` | first row for a ticker, or prior `adj_close` is 0/null |
| `sma_20`, `sma_50`, `sma_200` | trailing N-row average of `adj_close`, partial window allowed | never (uses however many rows exist) |
| `volatility_30d_ann` | trailing 30-row sample stddev of `daily_return`, × √252 | fewer than 2 non-null returns in the window |
| `volume_zscore` | `(volume - trailing_30d_avg) / trailing_30d_stddev` | trailing stddev is 0 or undefined |
| `trend_regime` | `'bullish'` if `sma_50 >= sma_200` else `'bearish'` | never (compares whatever SMA values exist, including partial-window ones early in a ticker's history) |
| `is_volume_anomaly` | `abs(volume_zscore) > 3` | follows `volume_zscore`'s nullability |

**Adjusted vs. raw prices:** `daily_return`, all SMAs, and volatility use
`adj_close` (split/dividend-adjusted) throughout, so they're comparable
across corporate actions. `bronze`/`silver`'s `low <= high` validation
compares **raw** `low`/`high`/`close` — not `adj_close` — since adjustment
factors are applied uniformly to a whole day's OHLC and would not break
that invariant, but adjusted and raw values are intentionally never
compared against each other directly anywhere in this pipeline.
