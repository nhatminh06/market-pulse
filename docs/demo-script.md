# Demo script (~90 seconds)

A suggested sequence for a short recorded walkthrough. Timings are
approximate targets for what to *show*, not a transcript of a real
recording — no demo has been recorded in this environment. Assumes the
stack is already up and has data (run the Quick Start first; don't record
the multi-minute backfill live).

| # | ~Seconds | Show | Command / action | Hide |
|---|---|---|---|---|
| 1 | 0–10 | Local architecture and its boundary | `architecture.png` and [separate GitOps pilot](architecture-and-evidence.md) | — |
| 2 | 10–20 | Healthy services | `docker compose ps` | — |
| 3 | 20–30 | Bronze data exists | Trino: `select count(*) from iceberg.bronze.prices` | — |
| 4 | 30–45 | dbt build running | `docker compose exec airflow-scheduler bash -c "cd /opt/airflow/dbt/market_pulse && dbt build --profiles-dir ."` | — |
| 5 | 45–55 | Tests passing | Same output, scroll to the `PASS`/test summary lines | — |
| 6 | 55–65 | Gold data | Trino: `select * from iceberg.gold.fct_daily_metrics order by trade_date desc limit 5` | — |
| 7 | 65–80 | Superset dashboard | Open `http://localhost:8088`, click through 2–3 panels | admin password field |
| 8 | 80–90 | Airflow DAG | Open `http://localhost:8085`, show the `market_pulse` graph view | any auth token in the URL bar |
| 9 | (optional) | Terraform-managed MinIO | MinIO console bucket view at `http://localhost:9001` | root credentials |

## Sensitive values to hide before recording/sharing

- MinIO root/pipeline access & secret keys (terminal history, `.env`, MinIO console header)
- `SLACK_WEBHOOK_URL` if set
- Superset/Grafana admin password fields
- The generated Airflow password in `airflow_auth/simple_auth_manager_passwords.json.generated`

## Notes

- Don't fabricate a video or GIF automatically — record this manually once
  the checklist in `docs/evidence-checklist.md` has real screenshots to
  draw from, or capture it live.
- If recording asynchronously (not a live screen-share), a terminal
  recording (`asciinema` or similar) for steps 3–6 is lighter-weight than
  screen video and still shows real output.


## Optional evidence-backed M3/M4 segment (not part of the 90-second demo)

- Show the real [M3 Compose E2E](https://github.com/nhatminh06/market-pulse/actions/runs/45271006974) and explain what the dashboard **API** checks prove; only display Grafana panels if you have separately captured a genuine rendering.
- Show the [M4 Linux/amd64 GitOps E2E](https://github.com/nhatminh06/market-pulse/actions/runs/45298404500) and [merged PR #11](https://github.com/nhatminh06/market-pulse/pull/11). Describe Flux, StatsD recovery, and MinIO persistence as **disposable pilot** qualifications.
- State that M5.1 benchmarks are **blocked** awaiting a suitable isolated runtime. There are no speedup, startup, median, or p95 results to demonstrate.
- These links are public validation artifacts. They do not constitute screenshots of live Superset/Airflow/Grafana interfaces.

Do not start or wipe preexisting Docker resources for a recording. If no
qualified disposable runtime is available, a walkthrough of the source,
architecture and verified Actions results is more defensible than a simulated
application demo.
