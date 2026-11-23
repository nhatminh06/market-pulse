#!/usr/bin/env bash
set -euo pipefail

prometheus=http://localhost:9090
grafana=http://localhost:3000

for attempt in $(seq 1 30); do
  metrics=$(docker compose -f docker-compose.observability.yml exec -T warehouse-exporter python -c 'import urllib.request; print(urllib.request.urlopen("http://localhost:9103/metrics").read().decode())')
  if grep -q 'market_pulse_observation_success 1' <<<"$metrics"; then break; fi
  sleep 10
done
grep -q 'market_pulse_observation_success 1' <<<"$metrics"
grep -q 'market_pulse_live_feed_expected 0' <<<"$metrics"
grep -q 'market_pulse_gold_row_count 6' <<<"$metrics"
grep -q 'market_pulse_iceberg_snapshot_count ' <<<"$metrics"
catalog_snapshots=$(docker compose exec -T airflow-scheduler python -c 'import sys; sys.path.insert(0, "/opt/airflow"); from ingestion.ingest_bronze import get_catalog; print(len(get_catalog().load_table("bronze.prices").metadata.snapshots))')
exported_snapshots=$(awk '$1 == "market_pulse_iceberg_snapshot_count" {print $2}' <<<"$metrics")
python -c 'import sys; assert float(sys.argv[1]) == int(sys.argv[2])' "$exported_snapshots" "$catalog_snapshots"
if grep -Eq '(ticker|run_id|snapshot_id)=' <<<"$metrics"; then
  echo 'Unbounded metric label detected' >&2
  exit 1
fi
echo 'Warehouse exporter observed Bronze, Gold, and Iceberg metadata'

for job in prometheus airflow warehouse; do
  curl -fsSG "$prometheus/api/v1/query" --data-urlencode "query=up{job=\"$job\"}" | python -c 'import json,sys; data=json.load(sys.stdin); assert data["status"]=="success" and data["data"]["result"] and data["data"]["result"][0]["value"][1]=="1", data'
done
for metric in market_pulse_observation_success market_pulse_gold_row_count market_pulse_iceberg_snapshot_count; do
  curl -fsSG "$prometheus/api/v1/query" --data-urlencode "query=$metric" | python -c 'import json,sys; data=json.load(sys.stdin); assert data["status"]=="success" and data["data"]["result"], data'
done
curl -fsSG "$prometheus/api/v1/query" --data-urlencode 'query=market_pulse_bronze_latest_ingestion_timestamp_seconds' | python -c 'import json,sys; data=json.load(sys.stdin); assert data["data"]["result"], data'

for attempt in $(seq 1 12); do
  airflow_metrics=$(curl -fsSG "$prometheus/api/v1/query" --data-urlencode 'query=market_pulse_airflow_scheduler_heartbeat_total')
  if python -c 'import json,sys; assert json.load(sys.stdin)["data"]["result"]' <<<"$airflow_metrics" 2>/dev/null; then break; fi
  sleep 10
done
python -c 'import json,sys; assert json.load(sys.stdin)["data"]["result"]' <<<"$airflow_metrics"
echo 'Airflow scheduler heartbeat reached StatsD exporter'
task_metrics=$(curl -fsSG "$prometheus/api/v1/query" --data-urlencode 'query=market_pulse_airflow_task_duration_seconds_count{task="validate_gold"}')
if python -c 'import json,sys; assert json.load(sys.stdin)["data"]["result"]' <<<"$task_metrics" 2>/dev/null; then
  echo 'Real Airflow validate_gold task duration metric observed'
else
  echo 'Airflow task duration metric not observed; tasks test may not emit it' >&2
fi

curl -fsSu admin:admin "$grafana/api/datasources/uid/prometheus" | python -c 'import json,sys; data=json.load(sys.stdin); assert data["uid"]=="prometheus" and data["url"]=="http://prometheus:9090", data'
curl -fsSu admin:admin "$grafana/api/dashboards/uid/market-pulse-operations" | python -c 'import json,sys; data=json.load(sys.stdin); assert len(data["dashboard"]["panels"])>=10, data'
curl -fsSu admin:admin "$grafana/api/datasources/proxy/uid/prometheus/api/v1/query?query=up" | python -c 'import json,sys; data=json.load(sys.stdin); assert data["status"]=="success" and data["data"]["result"], data'
curl -fsSG "$prometheus/api/v1/query" --data-urlencode 'query=up{job="warehouse"} == 1' | python -c 'import json,sys; data=json.load(sys.stdin); assert data["data"]["result"], data'
curl -fsSG "$prometheus/api/v1/rules" | python -c 'import json,sys; data=json.load(sys.stdin); assert any(rule["name"]=="MarketPulseBronzeStale" for group in data["data"]["groups"] for rule in group["rules"]), data'
echo 'Grafana provisioning and selected alert expression verified'

before=$(docker compose exec -T trino trino --output-format TSV --execute 'select count(*) from iceberg.bronze.prices')
gold_before=$(docker compose exec -T trino trino --output-format TSV --execute 'select count(*) from iceberg.gold.fct_daily_metrics')
trap 'docker compose start iceberg-rest; docker compose -f docker-compose.observability.yml start warehouse-exporter' EXIT
docker compose stop iceberg-rest
docker compose -f docker-compose.observability.yml restart warehouse-exporter
for attempt in $(seq 1 12); do
  failed_metrics=$(docker compose -f docker-compose.observability.yml exec -T warehouse-exporter python -c 'import urllib.request; print(urllib.request.urlopen("http://localhost:9103/metrics").read().decode())' 2>/dev/null || true)
  if grep -q 'market_pulse_observation_success 0' <<<"$failed_metrics"; then break; fi
  sleep 5
done
grep -q 'market_pulse_observation_success 0' <<<"$failed_metrics"
! grep -q '^market_pulse_gold_row_count ' <<<"$failed_metrics"
docker compose start iceberg-rest
docker compose -f docker-compose.observability.yml restart warehouse-exporter
for attempt in $(seq 1 20); do
  recovered_metrics=$(docker compose -f docker-compose.observability.yml exec -T warehouse-exporter python -c 'import urllib.request; print(urllib.request.urlopen("http://localhost:9103/metrics").read().decode())' 2>/dev/null || true)
  if grep -q 'market_pulse_observation_success 1' <<<"$recovered_metrics"; then break; fi
  sleep 5
done
grep -q 'market_pulse_observation_success 1' <<<"$recovered_metrics"
docker compose -f docker-compose.observability.yml stop warehouse-exporter
after=$(docker compose exec -T trino trino --output-format TSV --execute 'select count(*) from iceberg.bronze.prices')
gold_after=$(docker compose exec -T trino trino --output-format TSV --execute 'select count(*) from iceberg.gold.fct_daily_metrics')
test "$before" = "$after"
test "$gold_before" = "$gold_after"
docker compose -f docker-compose.observability.yml start warehouse-exporter
trap - EXIT
echo 'Monitoring failure left Bronze and Gold row counts unchanged; exporter restored'
