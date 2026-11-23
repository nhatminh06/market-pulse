"""Deterministic read-only monitoring tests."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from observability.warehouse_exporter import METRIC_HELP, Cache, observe


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.queries = []

    def execute(self, query):
        assert query.startswith("SELECT ")
        assert "iceberg.bronze.prices" in query or "iceberg.gold.fct_daily_metrics" in query or 'iceberg.bronze."prices$snapshots"' in query
        self.queries.append(query)

    def fetchone(self):
        return next(self.rows)


class Connection:
    def __init__(self, rows):
        self.cur = Cursor(rows)
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def test_observe_uses_only_three_read_queries_and_utc():
    conn = Connection([
        (datetime(2024, 1, 2, 12, tzinfo=UTC),),
        (date(2024, 1, 5), 6),
        (2, datetime(2024, 1, 6, 12, tzinfo=UTC)),
    ])
    called = []

    def connect(**kwargs):
        assert kwargs["request_timeout"] == 5
        assert kwargs["session_properties"]["query_max_execution_time"] == "10s"
        return conn

    result = observe(connect, lambda: called.append(True))
    assert called == [True]
    assert len(conn.cur.queries) == 3 and conn.closed
    assert result["market_pulse_bronze_latest_ingestion_timestamp_seconds"] == 1704196800
    assert result["market_pulse_gold_latest_trade_date_timestamp_seconds"] == 1704412800
    assert result["market_pulse_gold_row_count"] == 6


def test_empty_tables_are_unknown_not_zero():
    conn = Connection([(None,), (None, 0), (0, None)])
    result = observe(lambda **kwargs: conn, lambda: None)
    assert "market_pulse_bronze_latest_ingestion_timestamp_seconds" not in result
    assert "market_pulse_gold_latest_trade_date_timestamp_seconds" not in result
    assert result["market_pulse_gold_row_count"] == 0
    assert result["market_pulse_iceberg_snapshot_count"] == 0


def test_rest_failure_prevents_trino_query():
    def fail_rest():
        raise TimeoutError("REST unavailable")

    with pytest.raises(TimeoutError):
        observe(lambda **kwargs: pytest.fail("Trino should not be queried"), fail_rest)


def test_failed_query_closes_connection():
    class FailingConnection(Connection):
        def cursor(self):
            raise RuntimeError("Trino unavailable")

    conn = FailingConnection([])
    with pytest.raises(RuntimeError):
        observe(lambda **kwargs: conn, lambda: None)
    assert conn.closed


@pytest.mark.parametrize("failure", ["rest", "trino", "query"])
def test_failure_clears_data_but_preserves_last_success(failure):
    now = [1000]
    cache = Cache(clock=lambda: now[0])
    cache.refresh(lambda: {"market_pulse_gold_row_count": 6})
    now[0] = 1300
    cache.refresh(lambda: (_ for _ in ()).throw(RuntimeError(failure)))
    output = cache.render()
    assert "market_pulse_observation_success 0" in output
    assert "market_pulse_observation_timestamp_seconds 1300" in output
    assert "market_pulse_observation_last_success_timestamp_seconds 1000" in output
    assert "market_pulse_gold_row_count" not in output
    now[0] = 1600
    cache.refresh(lambda: {"market_pulse_gold_row_count": 7})
    assert "market_pulse_gold_row_count 7" in cache.render()


def test_metric_names_and_cardinality_are_fixed():
    cache = Cache()
    cache.refresh(lambda: {"market_pulse_gold_row_count": 1})
    output = cache.render()
    assert all(line.split()[0] in METRIC_HELP for line in output.splitlines() if not line.startswith("#"))
    assert "ticker=" not in output and "run_id=" not in output


def test_expired_cache_does_not_present_old_values_as_fresh():
    now = [0]
    cache = Cache(clock=lambda: now[0])
    cache.refresh(lambda: {"market_pulse_gold_row_count": 6})
    now[0] = 601
    assert "market_pulse_observation_success 0" in cache.render()
    assert "market_pulse_gold_row_count" not in cache.render()


def test_fixture_and_weekend_policy():
    cache = Cache()
    assert "market_pulse_live_feed_expected 0" in cache.render()
    assert "market_pulse_live_feed_expected 1" in cache.render(live_feed_expected=True)
    rules = yaml.safe_load((Path(__file__).resolve().parents[2] / "observability/alerts.yml").read_text())
    stale = next(rule for rule in rules["groups"][0]["rules"] if rule["alert"] == "MarketPulseBronzeStale")
    assert "market_pulse_live_feed_expected == 1" in stale["expr"]
    assert "604800" in stale["expr"]  # Seven days; Friday is not stale on Monday.


def test_monitoring_config_references_real_metrics():
    root = Path(__file__).resolve().parents[2] / "observability"
    prometheus = yaml.safe_load((root / "prometheus.yml").read_text())
    alerts = yaml.safe_load((root / "alerts.yml").read_text())
    mappings = yaml.safe_load((root / "statsd-mappings.yml").read_text())
    dashboard = json.loads((root / "grafana/json/market-pulse.json").read_text())
    datasource = yaml.safe_load((root / "grafana/datasources/prometheus.yml").read_text())
    provider = yaml.safe_load((root / "grafana/dashboards/provider.yml").read_text())
    assert {job["job_name"] for job in prometheus["scrape_configs"]} == {"airflow", "warehouse", "prometheus"}
    assert len(alerts["groups"][0]["rules"]) == 4
    assert mappings["mappings"][-1]["action"] == "drop"
    assert len(dashboard["panels"]) >= 10
    assert datasource["datasources"][0]["uid"] == "prometheus"
    assert provider["providers"][0]["options"]["path"] == "/var/lib/grafana/dashboards"
