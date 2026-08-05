# Benchmark methodology

**Status: not yet run.** This document defines the methodology and the two
benchmarks the README's performance claims should be backed by. No results
exist yet in this repository — the scripts in `benchmarks/` are ready to run
against a live local stack, which this working environment does not have
(no Docker daemon available). Until someone runs them and commits a results
file, any specific percentage or duration is **unverified** and must not
appear in the README. See `docs/resume-evidence.md` for the current
verified/unverified status of every such claim.

## Benchmark A: Raw Parquet vs. Iceberg-via-Trino

**What it measures:** whether Iceberg's metadata layer (manifests, column
stats, partition pruning) measurably speeds up an analytical aggregation
query over the gold layer, compared to scanning the same data's underlying
Parquet files directly with no metadata layer.

**What it does NOT measure:** Trino vs. some other engine — both sides use
the identical Parquet files and a logically equivalent query
(`benchmarks/queries/iceberg.sql` / `raw_parquet.sql`); the "raw Parquet"
side uses DuckDB only because it can point `read_parquet()` at a file glob
with zero setup. If Trino has a separate `hive`/local-filesystem catalog
available, an in-Trino comparison would isolate the table-format variable
more cleanly than an engine change does — that's a documented improvement,
not what's implemented here.

**To run it, record:**
- Dataset: ticker count, row count, date range (from the fixture or real
  ingested data actually used for the run — do not assume the values in
  `docs/resume-evidence.md`; count them directly, e.g.
  `select count(*) from iceberg.gold.fct_daily_metrics`).
- File count and total size of the gold table's Parquet files.
- Partition strategy in effect (`ARRAY['ticker']`, per `fct_daily_metrics.sql`).
- Trino version, Iceberg REST catalog image tag (both pinned in
  `docker-compose.yaml` — record the exact tags/digests in use at run time).
- Host CPU/RAM and whether other containers were competing for resources.
- Docker resource limits, if any were set (none are set by default today —
  see Known limitations in the README).
- Warm-up run count and measured run count (`--warmup`, `--runs`).

**Conditions to label explicitly** (do not use "cold" unless a service was
actually restarted):
- `fresh-service-start`: first query immediately after `docker compose up`.
- `first-query`: first query after the *container* has been running a while
  but this specific query hasn't executed yet.
- `warm-repeated-query`: the same query run back-to-back, letting Trino's
  and the OS's caches warm up.

**Headline number:** the percentage difference between the two engines'
**median** run time (via `summarize_results.py --compare`), never the
fastest individual run of either side.

**If Iceberg is not faster** on a small local dataset (plausible — Iceberg's
metadata-pruning advantage grows with file count and data volume, and a
20-ticker/single-decade dataset is small): report the actual number, and
explain that Iceberg's value here also includes schema evolution, ACID
transactions, time travel, and snapshot isolation — none of which a raw
Parquet directory provides, independent of query latency.

## Benchmark B: Startup / cold-start time

**Definition used:** wall-clock time from `docker compose up -d` (image
already built) to `curl -sf http://localhost:8080/v1/info` succeeding
against Trino, as a proxy for "the stack is usable." This is one of several
plausible definitions (image-build time, Airflow-scheduler-ready time,
first-task-execution time) — pick and state one; do not average across
different definitions.

**Old-setup vs. new-setup:** the README previously claimed "~8 minutes to
~15 seconds," attributed to switching the Dockerfile from `pip` to `uv`. No
benchmark artifact for the "~8 minute" baseline exists in this repository's
history — it cannot be reproduced without rebuilding the old Dockerfile and
measuring it directly. Until that comparison is actually run and recorded,
this specific number must be treated as **unverified** (see
`docs/resume-evidence.md`) and either qualified as an unverified historical
claim or removed from the README, not restated as fact.

**To reproduce it yourself:**
```bash
# Old setup: check out the commit before the Dockerfile used uv, rebuild,
# and time from `docker compose build` completion to Trino responding.
# New setup: same, on the current Dockerfile.
time docker compose build
docker compose up -d
until curl -sf http://localhost:8080/v1/info; do sleep 1; done
```
Run each at least 3 times with a cold Docker build cache
(`docker builder prune` between old/new, not between repeated runs of the
same setup) and report the median.
