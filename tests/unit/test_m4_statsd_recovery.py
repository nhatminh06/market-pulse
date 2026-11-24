"""Deterministic checks for the disposable StatsD restart qualification."""

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[2] / "infra/ci/m4_statsd_recovery.py"
spec = importlib.util.spec_from_file_location("m4_statsd_recovery", MODULE)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def state(uid="new", ready=True, endpoint_uid="new", ip="10.0.0.2", old_uid="old"):
    return {
        "service": {"spec": {"selector": {"app": "statsd-exporter"}, "ports": [
            {"name": "metrics", "port": 9102, "protocol": "TCP"},
            {"name": "statsd", "port": 9125, "protocol": "UDP"},
        ]}},
        "pods": {"items": [{"metadata": {"uid": uid}, "status": {
            "podIP": ip, "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "containerStatuses": [{"restartCount": 0}],
        }}]},
        "slices": {"items": [{"ports": [{"name": name, "port": port, "protocol": protocol}],
                              "endpoints": [{"conditions": {"ready": ready},
                                             "targetRef": {"uid": endpoint_uid}, "addresses": [ip]}]}
                             for name, port, protocol in (("metrics", 9102, "TCP"), ("statsd", 9125, "UDP"))]},
    }


def metrics(label, count=1):
    base = recovery.METRIC
    return (f'{base}_count{{task="{label}"}} {count}\n'
            f'{base}_sum{{task="{label}"}} {count * 1.25}\n'
            f'{base}_bucket{{task="{label}",le="5"}} {count}\n')


class Clock:
    def __init__(self):
        self.seconds = 0.0

    def now(self):
        return self.seconds

    def sleep(self, seconds):
        self.seconds += seconds


class FakeKube:
    def __init__(self, *, endpoint_delay=0, http_delay=0, deliver_on=1, stale=False,
                 duplicate=False, unmatched_exported=False):
        self.endpoint_delay = endpoint_delay
        self.http_delay = http_delay
        self.deliver_on = deliver_on
        self.stale = stale
        self.duplicate = duplicate
        self.unmatched_exported = unmatched_exported
        self.states = self.http_calls = 0
        self.sent = []
        self.diagnostic = None

    def state(self):
        self.states += 1
        if self.states <= self.endpoint_delay:
            result = state()
            result["slices"]["items"][0]["endpoints"] = []
            return result
        return state()

    def client(self, mode, label="", host=recovery.HOST):
        if mode == "send":
            self.sent.append((label, host))
            return ""
        if mode == "unmatched":
            self.sent.append(("unmatched", host))
            return ""
        self.http_calls += 1
        if self.http_calls <= self.http_delay:
            raise RuntimeError("unreachable")
        result = metrics("gitops_probe", 3) if self.stale else ""
        for name, address in self.sent:
            if address == recovery.HOST and (name == "gitops_recovery_final" or
                                             name.startswith("gitops_warm_") and
                                             int(name.rsplit("_", 1)[1]) >= self.deliver_on):
                result += metrics(name, 2 if self.duplicate else 1)
        if self.unmatched_exported and ("unmatched", recovery.HOST) in self.sent:
            result += "unmatched_test_run_123 1\n"
        return result

    def diagnostics(self, *args):
        self.diagnostic = args


class RecoveryTests(unittest.TestCase):
    def run_qualification(self, api, limit=6):
        clock = Clock()
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            recovery.qualify(api, "old", now=clock.now, sleep=clock.sleep, limit=limit)
        return output.getvalue()

    def test_no_replacement_endpoint(self):
        current = state()
        current["slices"]["items"][0]["endpoints"] = []
        self.assertIsNone(recovery.endpoint(current, "old"))

    def test_old_pod_endpoint_rejected(self):
        self.assertIsNone(recovery.endpoint(state(endpoint_uid="old"), "old"))

    def test_udp_endpoint_still_points_to_old_pod(self):
        current = state()
        current["slices"]["items"][1]["endpoints"][0]["targetRef"]["uid"] = "old"
        self.assertIsNone(recovery.endpoint(current, "old"))

    def test_multiple_ready_endpoints_rejected(self):
        current = state()
        current["slices"]["items"][0]["endpoints"].append(current["slices"]["items"][0]["endpoints"][0])
        self.assertIsNone(recovery.endpoint(current, "old"))

    def test_unready_pod_and_endpoint_rejected(self):
        self.assertIsNone(recovery.endpoint(state(ready=False), "old"))

    def test_nil_endpoint_ready_uses_api_default(self):
        current = state()
        for item in current["slices"]["items"]:
            item["endpoints"][0]["conditions"].pop("ready")
        self.assertEqual(recovery.endpoint(current, "old"), ("new", "10.0.0.2"))

    def test_endpoint_becomes_ready_after_delay(self):
        api = FakeKube(endpoint_delay=2)
        self.assertIn("recovery final histogram", self.run_qualification(api))
        self.assertEqual(api.states, 3)

    def test_http_can_succeed_without_udp(self):
        api = FakeKube(deliver_on=100)
        with self.assertRaisesRegex(TimeoutError, "no Service UDP"):
            self.run_qualification(api, limit=3)
        self.assertEqual(api.diagnostic[2:4], (True, False))
        self.assertTrue(all(host == recovery.HOST for _, host in api.sent if _ != "gitops_pod_diag"))
        self.assertIn(("gitops_pod_diag", "10.0.0.2"), api.sent)

    def test_warmup_uses_unique_labels_until_delivery(self):
        api = FakeKube(deliver_on=2)
        output = self.run_qualification(api)
        self.assertIn("after 2 unique attempts", output)
        self.assertEqual([name for name, _ in api.sent[:2]], ["gitops_warm_1", "gitops_warm_2"])
        self.assertEqual([name for name, _ in api.sent].count("gitops_recovery_final"), 1)

    def test_timeout_without_endpoint(self):
        api = FakeKube(endpoint_delay=100)
        with self.assertRaisesRegex(TimeoutError, "EndpointSlice"):
            self.run_qualification(api, limit=2)
        self.assertEqual(api.diagnostic[1], None)

    def test_exact_histogram_and_duplicate_rejection(self):
        self.assertEqual(recovery.histogram(metrics("test"), "test"),
                         {"count": 1.0, "sum": 1.25, "bucket": 1.0})
        with self.assertRaisesRegex(ValueError, "incorrect histogram"):
            recovery.histogram(metrics("test", 2), "test")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            recovery.histogram(metrics("test") + metrics("test"), "test")

    def test_metric_history_must_reset(self):
        api = FakeKube(stale=True)
        with self.assertRaisesRegex(ValueError, "old StatsD histogram"):
            self.run_qualification(api)

    def test_http_recovers_before_udp_probe(self):
        api = FakeKube(http_delay=2)
        self.assertIn("Service TCP metrics reachable", self.run_qualification(api))

    def test_unmatched_metric_remains_dropped(self):
        with self.assertRaisesRegex(ValueError, "unmatched StatsD metric"):
            self.run_qualification(FakeKube(unmatched_exported=True))

    def test_final_confirmation_must_fit_deadline(self):
        with self.assertRaisesRegex(TimeoutError, "insufficient time"):
            self.run_qualification(FakeKube(), limit=0.5)

    def test_diagnostics_do_not_print_raw_logs(self):
        api = recovery.Kube()
        with (
            patch.object(api, "state", return_value=state()),
            patch.object(api, "client", return_value='statsd_exporter_udp_packets_total 2\n'
                                                     'statsd_exporter_udp_packet_drops_total 0\n'
                                                     'unrelated{secret="private"} 7\n'),
            patch.object(api, "call", return_value="ERROR private-token\n"),
            contextlib.redirect_stderr(io.StringIO()) as output,
        ):
            api.diagnostics("old", "new", True, False, 2.5)
        self.assertIn("udp_packet_drops_total 0", output.getvalue())
        self.assertIn("log warning/error lines: 1", output.getvalue())
        self.assertNotIn("private", output.getvalue())


if __name__ == "__main__":
    unittest.main()
