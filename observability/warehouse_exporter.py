"""Cached, read-only warehouse observations for Prometheus."""

import os
import threading
import time
from datetime import UTC, date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

INTERVAL_SECONDS = 300
METRIC_HELP = {
    "market_pulse_observation_success": "Whether the latest warehouse observation succeeded",
    "market_pulse_observation_timestamp_seconds": "UTC time of the latest observation attempt",
    "market_pulse_observation_last_success_timestamp_seconds": "UTC time of the last successful observation",
    "market_pulse_bronze_latest_ingestion_timestamp_seconds": "Latest committed Bronze row ingestion time",
    "market_pulse_gold_latest_trade_date_timestamp_seconds": "Latest Gold market date at UTC midnight",
    "market_pulse_gold_row_count": "Current Gold fact row count",
    "market_pulse_iceberg_snapshot_count": "Bronze Iceberg snapshot count",
    "market_pulse_iceberg_latest_snapshot_timestamp_seconds": "Latest Bronze snapshot commit time",
    "market_pulse_live_feed_expected": "Whether live ingestion freshness alerts are enabled",
}


def observe(connect, rest_check):
    """Query only the Bronze/Gold allowlist; never mutate the catalog."""
    rest_check()
    conn = connect(
        host="trino", port=8080, user="observer", catalog="iceberg",
        request_timeout=5, session_properties={"query_max_execution_time": "10s"},
    )
    try:
        cursor = conn.cursor()
        values = {}
        for query, keys in (
            ('SELECT max(_ingested_at) FROM iceberg.bronze.prices', ("market_pulse_bronze_latest_ingestion_timestamp_seconds",)),
            ('SELECT max(trade_date), count(*) FROM iceberg.gold.fct_daily_metrics', ("market_pulse_gold_latest_trade_date_timestamp_seconds", "market_pulse_gold_row_count")),
            ('SELECT count(*), max(committed_at) FROM iceberg.bronze."prices$snapshots"', ("market_pulse_iceberg_snapshot_count", "market_pulse_iceberg_latest_snapshot_timestamp_seconds")),
        ):
            cursor.execute(query)
            row = cursor.fetchone()
            for key, value in zip(keys, row, strict=True):
                if value is None:
                    continue  # Unknown is absent, never zero.
                if isinstance(value, datetime):
                    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value
                    value = value.timestamp()
                elif isinstance(value, date):
                    value = datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp()
                values[key] = float(value)
        return values
    finally:
        conn.close()


def check_rest():
    with urlopen("http://iceberg-rest:8181/v1/config", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"Iceberg REST returned HTTP {response.status}")


class Cache:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.values = {}
        self.last_success = None
        self.last_attempt = None
        self.success = False

    def refresh(self, observer):
        try:
            values = observer()
        except Exception as exc:  # noqa: BLE001 - monitoring failures must never stop the exporter
            # Log the error server-side; do not leak query details through metrics.
            print(f"warehouse observation failed: {type(exc).__name__}: {exc}", flush=True)
            with self.lock:
                self.values = {}
                self.success = False
                self.last_attempt = self.clock()
        else:
            with self.lock:
                self.values = values
                self.success = True
                self.last_attempt = self.clock()
                self.last_success = self.last_attempt

    def render(self, live_feed_expected=False):
        with self.lock:
            fresh = self.success and self.last_attempt is not None and self.clock() - self.last_attempt <= 2 * INTERVAL_SECONDS
            values = {
                "market_pulse_observation_success": int(fresh),
                "market_pulse_live_feed_expected": int(live_feed_expected),
                **(self.values if fresh else {}),
            }
            if self.last_attempt is not None:
                values["market_pulse_observation_timestamp_seconds"] = self.last_attempt
            if self.last_success is not None:
                values["market_pulse_observation_last_success_timestamp_seconds"] = self.last_success
        lines = []
        for name, value in values.items():
            lines += [f"# HELP {name} {METRIC_HELP[name]}", f"# TYPE {name} gauge", f"{name} {value}"]
        return "\n".join(lines) + "\n"


def main():
    from trino.dbapi import connect

    cache = Cache()
    live = os.getenv("MARKET_PULSE_LIVE_FEED_EXPECTED", "0") == "1"

    def refresh_loop():
        while True:
            cache.refresh(lambda: observe(connect, check_rest))
            time.sleep(INTERVAL_SECONDS if cache.success else 15)

    threading.Thread(target=refresh_loop, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = cache.render(live).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    ThreadingHTTPServer(("0.0.0.0", 9103), Handler).serve_forever()


if __name__ == "__main__":
    main()
