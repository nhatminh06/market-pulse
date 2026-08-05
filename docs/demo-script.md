# Demo script (~90 seconds)

A suggested sequence for a short recorded walkthrough. Timings are
approximate targets for what to *show*, not a transcript of a real
recording — no demo has been recorded in this environment. Assumes the
stack is already up and has data (run the Quick Start first; don't record
the multi-minute backfill live).

| # | ~Seconds | Show | Command / action | Hide |
|---|---|---|---|---|
| 1 | 0–10 | Architecture diagram | `architecture.png` in the README | — |
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
