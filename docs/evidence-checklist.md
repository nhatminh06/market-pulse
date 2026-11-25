# Evidence checklist

No application screenshots are committed. This is a capture plan, not
proof that the listed UIs, panels, or views have been visually verified.
Only capture against an already-qualified, disposable runtime with fresh
credentials. **Do not start, reset, or remove preserved local Docker
containers/volumes to obtain portfolio screenshots.** Store approved images in
`docs/assets/<subsystem>/`, and link them only after the files exist.
Include a caption with the commit SHA, environment, date, and demonstrated
behavior. Public GitHub Actions run links can establish CI execution without
turning an Actions screenshot into false application-UI evidence.

**Before saving anything**: redact ports/hostnames if you're not running
purely `localhost`, and never include `.env` contents, MinIO secret keys,
Slack webhook URLs, or the generated Airflow admin password in a
screenshot's visible chrome (browser address bar, terminal history, etc.).

## Superset (`docs/assets/superset/`)
- [ ] Confirm whether the intended six-panel dashboard actually exists; record the dashboard ID/revision (do not assume provisioning)
- [ ] Full dashboard with actual loaded data and all existing panels visible
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


## Observability — M3 (`docs/assets/observability/`)
- [ ] Prometheus targets and a sample `market_pulse_observation_success` query, with fixture-mode flag visible
- [ ] Grafana **Market Pulse — Pipeline Operations** dashboard showing real rendered panels (not just dashboard API metadata)
- [ ] One warehouse freshness/row-count panel and one pipeline-health panel with actual query results
- [ ] Annotate missing task-duration metrics as unverified; do not turn absent samples into zero
- [ ] If demonstrating alert firing, use a disposable fault with recovery evidence; never imply notification delivery without a real integration

## Kubernetes / Flux pilot — M4 (`docs/assets/gitops/`)
- [ ] Prefer stable [M4 Linux/amd64 E2E run 45298404500](https://github.com/nhatminh06/market-pulse/actions/runs/45298404500) and [merged PR #11](https://github.com/nhatminh06/market-pulse/pull/11) as the primary CI evidence
- [ ] If a separate disposable kind environment is recreated safely, capture Flux GitRepository/Kustomization Ready state and reconciled revisions
- [ ] Capture StatsD exporter Service/EndpointSlice recovery and a real metrics response
- [ ] Capture MinIO PVC identity and object-hash persistence across rollout, using sanitized test output
- [ ] Label these **pilots**, not a Kubernetes-native deployment of Airflow, dbt, Trino, or Superset

## Capture metadata and review
- [ ] Save the source revision, date, isolated environment name, and exact commands alongside captures
- [ ] Remove tokens, terminal history, temporary secrets, password fields, and identifying host details
- [ ] Review each final file before linking it in the README; no mockups or reconstructed interfaces
- [ ] Keep CI run references separate from visual UI proof; see [architecture/evidence register](architecture-and-evidence.md)

Once a subsystem's checklist is fully checked off, update the README's
"Screenshots / evidence" section to link the real files instead of pointing
here.
