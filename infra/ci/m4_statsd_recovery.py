"""Bounded Service-path qualification after the disposable StatsD pod restarts."""

import json
import re
import subprocess
import sys
import time

NAMESPACE = "market-pulse-gitops-pilot"
SERVICE = "statsd-exporter"
HOST = f"{SERVICE}.{NAMESPACE}.svc.cluster.local"
METRIC = "market_pulse_airflow_task_duration_seconds"
CLIENT = "m4-metric-client"
CLIENT_SCRIPT = """
import socket, sys, urllib.request
mode, host, label = sys.argv[1:]
if mode == 'send':
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(
        f'airflow.dag.market_pulse.{label}.duration:1250|ms'.encode(), (host, 9125))
elif mode == 'unmatched':
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'unmatched.test.run_123:1|c', (host, 9125))
else:
    print(urllib.request.urlopen(f'http://{host}:9102/metrics', timeout=3).read().decode())
"""


def endpoint(state, old_uid):
    service = state["service"]["spec"]
    if service.get("selector") != {"app": SERVICE} or {
        (p["name"], p["port"], p["protocol"]) for p in service["ports"]
    } != {("metrics", 9102, "TCP"), ("statsd", 9125, "UDP")}:
        raise ValueError("unexpected StatsD Service selector or ports")
    pods = [p for p in state["pods"]["items"] if p["metadata"]["uid"] != old_uid]
    if len(pods) != 1:
        return None
    pod = pods[0]
    if not any(c["type"] == "Ready" and c["status"] == "True" for c in pod.get("status", {}).get("conditions", [])):
        return None
    uid, ip = pod["metadata"]["uid"], pod["status"].get("podIP")
    if not ip:
        return None
    for port in (("metrics", 9102, "TCP"), ("statsd", 9125, "UDP")):
        ready = [e for s in state["slices"]["items"]
                 if port in {(p["name"], p["port"], p["protocol"]) for p in s.get("ports", [])}
                 for e in s.get("endpoints", [])
                 if e.get("conditions", {}).get("ready") is not False
                 and e.get("conditions", {}).get("terminating") is not True]
        if len(ready) != 1 or ready[0].get("targetRef", {}).get("uid") != uid or ready[0].get("addresses") != [ip]:
            return None
    return uid, ip


def histogram(metrics, label):
    values = {}
    for line in metrics.splitlines():
        match = re.fullmatch(rf"{METRIC}_(count|sum|bucket)\{{([^}}]+)\}}\s+([\d.]+)", line)
        if not match:
            continue
        suffix, raw_labels, value = match.groups()
        labels = dict(re.findall(r'(\w+)="([^"]*)"', raw_labels))
        expected = {"task": label, **({"le": "5"} if suffix == "bucket" else {})}
        if labels != expected:
            continue
        if suffix in values:
            raise ValueError(f"duplicate {suffix} histogram sample")
        values[suffix] = float(value)
    if not values:
        return None
    if len(values) != 3:
        return None
    if values != {"count": 1.0, "sum": 1.25, "bucket": 1.0}:
        raise ValueError(f"incorrect histogram for {label}: {values}")
    return values


class Kube:
    deadline = None

    def call(self, *args, input=None):
        timeout = min(8, max(0.1, self.deadline - time.monotonic())) if self.deadline else 8
        try:
            result = subprocess.run(
                ["kubectl", "-n", NAMESPACE, *args], input=input, text=True,
                capture_output=True, check=False, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Kubernetes query or client timed out") from exc
        if result.returncode:
            raise RuntimeError("Kubernetes query or client command failed")
        return result.stdout

    def state(self):
        return {
            "service": json.loads(self.call("get", "service", SERVICE, "-o", "json")),
            "pods": json.loads(self.call("get", "pods", "-l", f"app={SERVICE}", "-o", "json")),
            "slices": json.loads(self.call("get", "endpointslices", "-l", f"kubernetes.io/service-name={SERVICE}", "-o", "json")),
        }

    def client(self, mode, label="", host=HOST):
        return self.call("exec", "-i", CLIENT, "--", "python", "-", mode, host, label, input=CLIENT_SCRIPT)

    def diagnostics(self, old_uid, new_uid, tcp_ready, udp_seen, elapsed):
        print(f"StatsD recovery diagnostic: old_uid={old_uid} new_uid={new_uid or 'none'} "
              f"tcp_ready={tcp_ready} udp_seen={udp_seen} elapsed={elapsed:.1f}s", file=sys.stderr)
        try:
            state = self.state()
            print("Service selector/ports:", state["service"]["spec"].get("selector"),
                  [(p["name"], p["port"], p["protocol"]) for p in state["service"]["spec"]["ports"]], file=sys.stderr)
            for p in state["pods"]["items"]:
                print("Pod:", p["metadata"]["uid"], p.get("status", {}).get("podIP"),
                      [(c["type"], c["status"]) for c in p.get("status", {}).get("conditions", []) if c["type"] == "Ready"],
                      [c["restartCount"] for c in p.get("status", {}).get("containerStatuses", [])], file=sys.stderr)
            for s in state["slices"]["items"]:
                for e in s.get("endpoints", []):
                    print("Endpoint:", [(p.get("name"), p.get("port"), p.get("protocol")) for p in s.get("ports", [])],
                          e.get("conditions", {}).get("ready"),
                          e.get("targetRef", {}).get("uid"), e.get("addresses"), file=sys.stderr)
        except (RuntimeError, KeyError, ValueError):
            print("Kubernetes diagnostic query unavailable", file=sys.stderr)
        try:
            metrics = self.client("metrics")
            for name in ("statsd_exporter_udp_packets_total", "statsd_exporter_udp_packet_drops_total",
                         "statsd_exporter_samples_total", "statsd_exporter_sample_errors_total"):
                values = [float(line.split()[-1]) for line in metrics.splitlines()
                          if line.startswith((name + " ", name + "{"))]
                print("Exporter self-metric:", name, sum(values) if values else "absent", file=sys.stderr)
        except (RuntimeError, ValueError):
            print("Exporter self-metrics unavailable", file=sys.stderr)
        try:
            logs = self.call("logs", "deployment/statsd-exporter", "--tail=30")
            print("Exporter log warning/error lines:", sum("warn" in x.lower() or "error" in x.lower()
                                                      for x in logs.splitlines()), file=sys.stderr)
        except RuntimeError:
            print("Exporter logs unavailable", file=sys.stderr)


def qualify(api, old_uid, now=time.monotonic, sleep=time.sleep, limit=30):
    start = now()
    deadline = start + limit
    api.deadline = deadline
    new_uid = None
    tcp_ready = udp_seen = False
    final_seen = False
    pod_ip = None
    try:
        while now() < deadline:
            found = endpoint(api.state(), old_uid)
            if found:
                new_uid, pod_ip = found
                print(f"StatsD replacement endpoint Ready: old={old_uid} new={new_uid} after {now() - start:.1f}s")
                break
            sleep(0.5)
        if not new_uid:
            raise TimeoutError("replacement EndpointSlice did not converge")
        while now() < deadline:
            try:
                metrics = api.client("metrics")
                tcp_ready = True
                if any(line.startswith(f'{METRIC}_count{{task="gitops_probe"}}') for line in metrics.splitlines()):
                    raise ValueError("old StatsD histogram survived pod replacement")
                break
            except RuntimeError:
                sleep(0.5)
        if not tcp_ready:
            raise TimeoutError("Service TCP metrics did not become reachable")
        print(f"StatsD Service TCP metrics reachable after {now() - start:.1f}s")
        attempt = 0
        while now() < deadline:
            attempt += 1
            label = f"gitops_warm_{attempt}"
            api.client("send", label)
            until = min(deadline, now() + 2)
            while now() < until:
                if histogram(api.client("metrics"), label):
                    udp_seen = True
                    break
                sleep(0.25)
            if udp_seen:
                break
        if not udp_seen:
            raise TimeoutError(f"no Service UDP warm-up sample after {attempt} unique attempts")
        print(f"StatsD Service UDP warm-up observed after {attempt} unique attempts and {now() - start:.1f}s")
        api.client("send", "gitops_recovery_final")
        api.client("unmatched")
        while now() < deadline:
            metrics = api.client("metrics")
            if histogram(metrics, "gitops_recovery_final"):
                final_seen = True
                if deadline - now() < 1:
                    raise TimeoutError("insufficient time to confirm final Service UDP histogram")
                sleep(1)
                metrics = api.client("metrics")
                if not histogram(metrics, "gitops_recovery_final"):
                    raise ValueError("fresh Service UDP histogram disappeared")
                if "run_123" in metrics:
                    raise ValueError("unmatched StatsD metric was exported")
                print(f"StatsD recovery final histogram count=1 sum=1.25 bucket=1 after {now() - start:.1f}s")
                return
            sleep(0.25)
        raise TimeoutError("fresh Service UDP histogram did not appear")
    except (RuntimeError, TimeoutError, ValueError) as exc:
        api.deadline = None
        print(f"StatsD recovery failed: {exc}", file=sys.stderr)
        api.diagnostics(old_uid, new_uid, tcp_ready, udp_seen, now() - start)
        if pod_ip and tcp_ready and not final_seen and (not udp_seen or isinstance(exc, TimeoutError)):
            try:
                api.client("send", "gitops_pod_diag", pod_ip)
                until = now() + 3
                while now() < until:
                    if histogram(api.client("metrics"), "gitops_pod_diag"):
                        print("Pod-IP UDP diagnostic observed; Service UDP remains required", file=sys.stderr)
                        break
                    sleep(0.25)
                else:
                    print("Pod-IP UDP diagnostic not observed", file=sys.stderr)
            except (RuntimeError, ValueError):
                print("Pod-IP UDP diagnostic unavailable", file=sys.stderr)
        raise


if __name__ == "__main__":
    qualify(Kube(), sys.argv[1])
