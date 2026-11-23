# M4.4 disposable MinIO pilot

This local ARM64 qualification adds one Flux-managed MinIO StatefulSet beside
the [StatsD pilot](../statsd/README.md). It uses a new 1 GiB kind-local PVC and
new credentials. It never mounts a Compose or warehouse volume. The pinned
image is an [unofficial third-party build](https://github.com/intikhab49/minio-from-source),
so use only synthetic data and disposable credentials. Review its security
updates before reuse. The [M4.4 evidence](../../../docs/kubernetes-gitops-plan.md)
records the exact qualified digest, revisions, and limits.

## Bootstrap

Follow the [kind](../../kind/README.md) and [Flux](../../flux/README.md)
runbooks for verified kind v0.33.0, Kubernetes v1.36.4, Flux v2.10.1,
isolated kubeconfig, Docker VM and Compose preflight, and two-controller
installation. Before cluster creation, verify `market-pulse-m4` is absent and
record the running Compose IDs and Docker volume names. Use only a new private
directory under `/private/tmp`; set `umask 077`. Never use the default
Kubernetes context for this test.

Copy `infra/k8s/statsd/`, `infra/k8s/minio/`, and the canonical
`observability/statsd-mappings.yml` into matching paths in a **separate**
local Git worktree. Run `sh infra/k8s/statsd/check-mapping.sh` there, commit,
clone it to a bare `fixture.git`, and serve it with the [local Git HTTP
fixture](../../flux/README.md) on Docker's `kind` network with no published
host port. The real Market Pulse repository remains uncommitted. From the
repository root, with `K` pointing to the verified kubectl binary and `KC`
to the isolated kubeconfig:

```sh
"$K" --kubeconfig "$KC" apply -f infra/flux/statsd-pilot.yaml
"$K" --kubeconfig "$KC" apply -f infra/flux/minio-pilot.yaml
"$K" --kubeconfig "$KC" -n flux-system wait gitrepository/m4-minio \
  kustomization/m4-minio-namespace --for=condition=Ready --timeout=120s
"$K" --kubeconfig "$KC" get storageclass standard \
  -o jsonpath='{.provisioner} {.volumeBindingMode} {.reclaimPolicy}{"\n"}'
```

The namespace is reconciled before the Secret. Generate unique credentials in
mode-0600 files in the private directory. Never print their contents or put
them in Git. The following commands use only the new namespace:

```sh
SCRATCH_DIR="$scratch" python3 - <<'PY'
from pathlib import Path
import os, secrets
p = Path(os.environ['SCRATCH_DIR'])
(p / 'root-user').write_text('m4' + secrets.token_hex(10))
(p / 'root-password').write_text(secrets.token_urlsafe(36))
(p / 'root-user').chmod(0o600)
(p / 'root-password').chmod(0o600)
PY
"$K" --kubeconfig "$KC" -n market-pulse-m4-storage create secret generic \
  minio-credentials --from-file=root-user="$scratch/root-user" \
  --from-file=root-password="$scratch/root-password"
"$K" --kubeconfig "$KC" apply -f infra/flux/minio-app.yaml
"$K" --kubeconfig "$KC" -n flux-system wait kustomization/m4-minio-app \
  --for=condition=Ready --timeout=180s
"$K" --kubeconfig "$KC" -n market-pulse-m4-storage rollout status \
  statefulset/minio --timeout=180s
```

Do not interpolate a credential value into shell command text.
Inspect Flux source/applied revisions, StatefulSet inventory, image ID, pod
readiness and restarts, and the bound PVC/PV. The PV's backing path must be
inside the disposable kind node, not a host or Compose data path. The
StatefulSet explicitly retains its generated PVC on deletion and scale-down;
Flux does not inventory that PVC. The namespace has Flux pruning disabled so
a routine Git change cannot silently remove its data. The StorageClass's
`Delete` reclaim policy applies when the PVC is explicitly deleted. Neither
retention setting makes the data survive cluster deletion.

## S3, restart, and Git recovery

The test client is separate from the Flux application overlay. It uses the
same pinned ARM64 image and the out-of-band Secret. Its `mc` config must go to
writable `/tmp/.mc` because the non-root image has no writable default home:

```sh
"$K" --kubeconfig "$KC" apply -f infra/k8s/minio/client.yaml
"$K" --kubeconfig "$KC" -n market-pulse-m4-storage wait pod/m4-s3-client \
  --for=condition=Ready --timeout=120s
"$K" --kubeconfig "$KC" -n market-pulse-m4-storage exec m4-s3-client -- \
  sh -ec 'mc --config-dir /tmp/.mc alias set pilot http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null; printf "Market Pulse M4.4 disposable object v1\n" >/tmp/object; mc --config-dir /tmp/.mc mb pilot/m4-disposable >/dev/null; mc --config-dir /tmp/.mc cp /tmp/object pilot/m4-disposable/object.txt >/dev/null; sha256sum /tmp/object'
expected_sha=$("$K" --kubeconfig "$KC" -n market-pulse-m4-storage \
  exec m4-s3-client -- sha256sum /tmp/object | awk '{print $1}')
KUBECTL="$K" sh infra/k8s/minio/check-object.sh "$KC" "$expected_sha"
```

Compare `expected_sha` with the printed digest. Record pod UID, PVC UID, PV name,
and image ID; delete **only** `minio-0`, wait for its replacement to be Ready,
assert a different pod UID and unchanged PVC UID/PV name, and run
`check-object.sh` again. Then commit only a pod-template annotation change in
the disposable Git worktree and push **only** to its local bare fixture. Poll
the GitRepository artifact revision, application Kustomization applied
revision, annotation, and StatefulSet readiness for up to three minutes;
do not force reconciliation for this check. Run `check-object.sh` again.
Patch only that annotation to a different value with `kubectl`, wait for Flux
to restore the Git value, and verify readiness, claim identity, and digest.
Never drift the PVC, Secret, or object bytes.

Keep StatsD Ready while MinIO runs. A disposable pinned Python client can send
`airflow.dag.market_pulse.gitops_probe.duration:1250|ms` to the StatsD Service
UDP 9125 and assert a `market_pulse_airflow_task_duration_seconds_count` sample
for `task="gitops_probe"` at TCP 9102 `/metrics`. This is synthetic telemetry.
Take five steady samples of kind node `docker stats`, per-container `crictl
stats`, Docker VM `MemAvailable` and `SwapFree`; inspect node pressure, pod
restarts, OOM/eviction events, and Compose service status. Stop and tear down
if additional steady RAM exceeds 2 GiB, CPU exceeds two cores, VM headroom
falls below 1 GiB, or existing services become unstable.

## Teardown

Record final non-secret diagnostics, then delete only the disposable clients,
`market-pulse-m4-git` HTTP container, and `market-pulse-m4` kind cluster with
the isolated kubeconfig. Remove the private directory, including credential
files and kubeconfig. Verify no named cluster/node remains; compare original
Compose container IDs, running status, and Docker volume names to preflight;
verify the user's original Kubernetes context is unchanged. Never use
`docker compose down -v`, volume prune, or `terraform destroy`.

M4.5A prepares a separate [Linux/amd64 integration script](../../ci/m4-gitops-e2e.sh)
using a run-qualified kind context. `check-object.sh` accepts `KIND_CONTEXT`
for that disposable context while keeping `kind-market-pulse-m4` as the local
default. The remote workflow remains unexecuted until the M4.5B review gate.
