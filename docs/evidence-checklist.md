# Evidence checklist

None of the screenshots below exist yet in this repository — this is the
capture list, not a record of what's done. Run `docker compose up -d` and
walk through the Quick Start in the README first (it populates real data),
then work through this list. Save files into `docs/assets/<subsystem>/`
with descriptive names (e.g. `docs/assets/superset/dashboard-full.png`) and
link them from the README once they exist.

**Before saving anything**: redact ports/hostnames if you're not running
purely `localhost`, and never include `.env` contents, MinIO secret keys,
Slack webhook URLs, or the generated Airflow admin password in a
screenshot's visible chrome (browser address bar, terminal history, etc.).

## Superset (`docs/assets/superset/`)
- [ ] Full dashboard, all 6 panels visible
- [ ] Cumulative-return-by-ticker line chart
- [ ] Price-vs-moving-average line chart
- [ ] Volatility heatmap
- [ ] Volume-anomalies table
- [ ] Avg-daily-return-by-sector bar chart

## Airflow (`docs/assets/airflow/`)
- [ ] DAG graph view (`market_pulse`, 3 tasks: ingest_bronze → dbt_build → validate_gold)
- [ ] A successful DAG run (green across all tasks)
- [ ] Task duration view (Gantt or task-duration chart)
- [ ] A failed task + retry, if you've deliberately triggered one (e.g. by
      pointing PIPELINE_ACCESS_KEY at something invalid temporarily)

## dbt (`docs/assets/dbt/`)
- [ ] Lineage graph (`dbt docs generate && dbt docs serve`, or the
      published GitHub Pages site from `.github/workflows/docs.yml`)
- [ ] Terminal output of a successful `dbt build`
- [ ] Terminal output of `dbt test` showing pass counts
- [ ] A gold model's generated documentation page (column descriptions,
      tests)

## Trino (`docs/assets/trino/`)
- [ ] A representative query against `gold.fct_daily_metrics` (e.g.
      `benchmarks/queries/iceberg.sql`) and its result
- [ ] `EXPLAIN` output or the Trino UI's query-plan view for that query
- [ ] Trino UI query history showing multiple executed queries

## Iceberg (`docs/assets/iceberg/`)
- [ ] Table list (`show tables in iceberg.gold`)
- [ ] A table's schema (`describe iceberg.gold.fct_daily_metrics`)
- [ ] Snapshot history (`select * from iceberg.gold."fct_daily_metrics$snapshots"`)
- [ ] Partition info (`select * from iceberg.gold."fct_daily_metrics$partitions"`)

## Terraform / MinIO (`docs/assets/architecture/` or a new `terraform/` subfolder)
- [ ] `terraform apply` succeeding (redact the access/secret key outputs —
      they're marked `sensitive` for a reason)
- [ ] MinIO console showing the `warehouse` bucket
- [ ] The IAM policy JSON attached to the pipeline user

## Demo (`docs/assets/demo/`)
- [ ] Full local start (`docker compose up -d` completing)
- [ ] Fixture or backfill ingestion running
- [ ] `dbt build` output
- [ ] `quality/validate_gold.py` passing
- [ ] Superset dashboard query loading

Once a subsystem's checklist is fully checked off, update the README's
"Screenshots / evidence" section to link the real files instead of pointing
here.
