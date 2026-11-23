# M4.3 disposable StatsD pilot

This overlay runs only the existing StatsD exporter and mapping in a dedicated
namespace. Flux applies it from a disposable local Git repository; do not
apply the Deployment directly. The canonical mapping is
[`observability/statsd-mappings.yml`](../../../observability/statsd-mappings.yml).
Run `sh infra/k8s/statsd/check-mapping.sh` before every fixture commit; it
fails if the overlay copy differs. After reconciliation, compare the deployed
ConfigMap's `mappings.yml` bytes with the canonical file as well.

Use the [M4.2 Flux fixture runbook](../../flux/README.md) for the exact kind
v0.33.0, Kubernetes v1.36.4, Flux v2.10.1, isolated kubeconfig, controller
installation, local smart-HTTP Git service, resource checks, and teardown.
For M4.3, copy `infra/k8s/statsd/` and
`observability/statsd-mappings.yml` into the disposable Git worktree, run its
`check-mapping.sh`, commit them, and serve the bare repository. Apply
[`statsd-pilot.yaml`](../../flux/statsd-pilot.yaml) to create the Flux source
and Kustomization. Do not apply the overlay with kubectl. The namespace is a
Git-managed resource and must be absent before this step.

Wait for both Flux resources to be Ready, then compare the Git worktree's
`git rev-parse HEAD` to the GitRepository artifact revision and the
Kustomization's last applied revision. Inspect the Kustomization inventory;
it must contain the pilot Namespace, ConfigMap, ClusterIP Service, and
Deployment. Wait for the Deployment to be Available and the pod Ready with
zero restarts. Check the deployed image identity and ConfigMap contents.

Use the pinned ARM64-compatible
`python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89`
as a disposable client pod in the pilot namespace. From that pod, send
`airflow.dag.market_pulse.gitops_probe.duration:1250|ms` over UDP to
`statsd-exporter.market-pulse-gitops-pilot.svc.cluster.local:9125` three
times. Query that Service's TCP 9102 `/metrics` endpoint. Assert the
`market_pulse_airflow_task_duration_seconds` histogram has
`task="gitops_probe"`, count increased by three, sum increased by 3.75
seconds, and the `le="5"` bucket increased by three. Send
`unmatched.test.run_123:1|c` and assert neither `unmatched_test` nor
`run_123` appears in the exposition. Inspect all pilot histogram labels for
unexpected run IDs or credentials. This is synthetic telemetry, not an
Airflow task observation.

For Git propagation, add only a harmless pod-template annotation to the
disposable Git worktree's Deployment, commit, and push **only to the local
bare repository**. With no manual reconcile, poll at most 3 minutes for the
GitRepository artifact revision, Kustomization last applied revision,
Deployment annotation, and Available condition to match the new commit.
Record commit and observation timestamps and the old/new pod UIDs. Repeat
the synthetic metric test on the new pod; its in-memory counters can restart
at zero. Then patch the same annotation manually to `manual-drift`, wait for
Flux to restore the Git value, and wait for Deployment readiness. Delete only
the pilot exporter pod, assert a different UID becomes Ready, and repeat the
metric test again.

Take five steady samples of node `docker stats`, container-runtime `crictl
stats`, VM `MemAvailable` and `SwapFree`. Inspect node pressure, warning
events, pod restarts, OOM/eviction, and all pre-existing Compose container
IDs and volumes. Remove the client pod, local Git fixture, and exact named
kind cluster. Remove only the experiment's private temporary directory and
kubeconfig. Verify the default context, Compose identities, named volumes,
and cluster absence. Never run broad Docker cleanup or attach a Compose
volume to this pilot.

M4.5A adds a future Linux/amd64 check using the same mapping and image digest.
Its [integration script](../../ci/m4-gitops-e2e.sh) asserts a fresh histogram
before and after controlled exporter-pod replacement. That remote check is
prepared but unexecuted; the local ARM64 result above remains separate.
