# Market Pulse observability (V2-M3)

This optional stack uses Airflow 3.0.6 StatsD metrics, StatsD exporter, a read-only warehouse exporter, Prometheus, and Grafana. It never writes to Iceberg. The pipeline continues without this stack; StatsD packets are best-effort UDP and may be lost while the exporter is down.

## Run locally

After the [core stack](../README.md) is running and `.env` has been populated by Terraform:

```sh
docker compose -f docker-compose.observability.yml up -d --build
open http://localhost:3000
```

Grafana defaults to `admin` / `admin` for a disposable localhost demo. Set `GRAFANA_ADMIN_PASSWORD` in the shell or `.env` for any longer-lived setup. Prometheus is at `http://localhost:9090`. Only Grafana and Prometheus bind to localhost; the two exporters stay on the Compose network. Teardown: `docker compose -f docker-compose.observability.yml down`. This does not remove the warehouse volumes.

The dashboard, **Market Pulse — Pipeline Operations**, and Prometheus datasource are provisioned from `observability/grafana/`. Alert rules are local to Prometheus; no Slack, email, or PagerDuty delivery is configured.

## Sources and units

Airflow 3.0.6 emits `ti_failures`, `dag.<dag>.<task>.duration` timers, `dagrun.duration.<state>.<dag>` timers, and `scheduler_heartbeat`. [Airflow's 3.0.6 metrics reference](https://airflow.apache.org/docs/apache-airflow/3.0.6/administration-and-deployment/logging-monitoring/metrics.html) requires the `statsd` extra; the pipeline image now installs it at the pinned Airflow version. The 3.0.6 installed source emits `ti_failures` as an Airflow-wide counter; it cannot identify a specific DAG through classic StatsD. Duration mappings are limited to `market_pulse`. Unmatched paths are dropped. The reference also lists a legacy `ti.finish` path, but the installed 3.0.6 source did not show an emission call, so no success/outcome panel relies on it. A retry counter is not documented for this version, so none is shown.

The warehouse exporter refreshes in the background every five minutes after success and every 15 seconds after failure. HTTP scrapes return only cached values. The REST `/v1/config` probe has a 5-second timeout; Trino requests have a 5-second request timeout and a 10-second query execution limit. The SQL allowlist contains only `bronze.prices`, `gold.fct_daily_metrics`, and the Bronze `$snapshots` metadata table. Gold and Bronze aggregates can still scan table data once per refresh; this simple local design is unsuitable for a very large warehouse without metadata-backed aggregates or a separate reporting table.

| Metric | Meaning |
| --- | --- |
| `market_pulse_observation_success` | 1 if the last observation succeeded and its cache is at most 10 minutes old; otherwise 0 |
| `market_pulse_observation_timestamp_seconds` | Unix seconds of the last attempted observation |
| `market_pulse_observation_last_success_timestamp_seconds` | Unix seconds of the last successful observation; history, not fresh data |
| `market_pulse_bronze_latest_ingestion_timestamp_seconds` | Maximum committed Bronze `_ingested_at`, Unix seconds; absent if Bronze is empty |
| `market_pulse_gold_latest_trade_date_timestamp_seconds` | Maximum Gold `trade_date` at UTC midnight, Unix seconds; absent if Gold is empty |
| `market_pulse_gold_row_count` | Current Gold fact row count; zero is valid for an empty table |
| `market_pulse_iceberg_snapshot_count` | Number of Bronze snapshots in catalog metadata |
| `market_pulse_iceberg_latest_snapshot_timestamp_seconds` | Latest Bronze snapshot commit time, Unix seconds; absent without snapshots |
| `market_pulse_live_feed_expected` | Operator flag; default 0 for fixture demos |

All custom warehouse metrics are Prometheus gauges with no labels. Timestamp metrics use Unix seconds; counts and booleans are unitless. They are refreshed together from one REST availability check and three read-only Trino queries, then served from cache. On any query failure, the data gauges disappear rather than retaining the last success. An absent Bronze or Gold timestamp means the table is empty or the observation failed; check `market_pulse_observation_success` to distinguish those cases. An absent last-success timestamp means no observation has succeeded. The attempt timestamp is absent until the first attempt. The live-feed flag remains present even during failure. Airflow failure and scheduler metrics are counters; task and DAG-run durations are histogram observations in seconds. Only the task duration histogram has a `task` label, and only the DAG-run duration histogram has a `state` label. No warehouse metric uses labels.

These are different clocks: observation success says monitoring could read the warehouse; Bronze `_ingested_at` describes a committed row; Gold market date describes the price data; Airflow task outcome describes execution. A task can succeed with partial upstream data. A Friday trading date can remain latest throughout a weekend or holiday. No daily-date alert is configured. The Bronze stale alert requires `MARKET_PULSE_LIVE_FEED_EXPECTED=1` and a seven-day age, sustained for 30 minutes. The static 2024 fixture is historical and must not be presented as fresh production data.

On a failed or stale observation, data gauges disappear; only failure state, attempt time, and historical last-success time remain. Unknown is never emitted as zero. Metrics have no ticker, run, snapshot ID, error, or credential labels.

## Dashboard and alerts

The dashboard groups panels into pipeline health (target availability, Airflow-wide task failures, Market Pulse task duration), data freshness/quality (Bronze ingestion, Gold market date and rows, observation state), and Iceberg lifecycle (snapshot count and latest snapshot age). Missing samples display as unknown or no task runs, rather than zero.

Prometheus evaluates every 30 seconds. Rules are: warehouse exporter down for 5 minutes, warehouse observation failing for 15 minutes, any Airflow task failure in a 15-minute window sustained for 2 minutes, and Bronze ingestion older than seven days for 30 minutes when live feed is explicitly expected. The failure counter covers all DAGs in this Airflow instance; the local stack currently configures only Market Pulse. Snapshot growth is shown but has no alert: there is no justified universal threshold for this small fixture versus backfills. `up` and the observation gauge distinguish no scrape from a warehouse query failure. Rules do not send notifications.

## Troubleshooting and qualification

Use `docker compose -f docker-compose.observability.yml ps`, `logs warehouse-exporter`, `curl http://localhost:9090/api/v1/targets`, and `curl http://localhost:9090/api/v1/rules`. A missing data gauge with `market_pulse_observation_success=0` means REST, Trino, or a query failed; exporter logs contain the exception class and message. If Airflow metrics are missing, verify `statsd` is installed in the Airflow image and the scheduler can resolve `statsd-exporter` on `marketpulse_default`.

Local unit/config tests cover read-only query shapes, UTC conversion, empty results, failure/cache recovery, bounded metric names, and provisioning references. The manual E2E workflow now has an observability step to inspect real StatsD exposition, Prometheus targets/queries, Grafana datasource/dashboard, and warehouse data isolation. **That step has not run yet on this branch.** Real metric names, Trino `$snapshots` compatibility, dashboard rendering, and alert behavior remain unqualified until a GitHub-hosted E2E run. The monitoring images use floating `latest` tags and the default Grafana password is for localhost demos only; pin reviewed digests and set unique credentials before any shared deployment.
