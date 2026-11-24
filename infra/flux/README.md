# Disposable M4.2 Flux fixture

This fixture tests Git reconciliation without deploying Market Pulse services.
Use it only with the named `market-pulse-m4` kind cluster from
[`infra/kind/cluster.yaml`](../kind/cluster.yaml). The Git server has no
published host port or credentials; Git's unauthenticated receive-pack is
disabled. Its bare repository
and kubeconfig live under a private temporary directory. `fixture.yaml` tells
Flux to manage only `m4-gitops-value` in `m4-gitops-test`; create that namespace
before applying the fixture. Never point it at a real repository or shared data.

Use verified kind v0.33.0 and kubectl v1.36.4 as described in the
[kind runbook](../kind/README.md). Download Flux CLI v2.10.1 from the
[official release](https://github.com/fluxcd/flux2/releases/tag/v2.10.1).
The `darwin_arm64` archive SHA-256 is
`c0a8dc285c568905ae38e25074985f3181c82a3c8d66316959561f27a626909d`;
verify it against the signed release checksum file before extraction. The
release installs source-controller and kustomize-controller v1.10.2 with:

```sh
KUBECONFIG="$scratch/kubeconfig" "$flux" install \
  --components=source-controller,kustomize-controller
```

Set `scratch` to a new mode-0700 directory under `/private/tmp`, and set
`flux`, `kind`, and `kubectl` to the verified binaries. Use `umask 077`.
Check Docker VM memory, original Compose container IDs and volume names, and
that `market-pulse-m4` is absent before creating it. The kind runbook gives
the isolated `--kubeconfig` creation and deletion commands. Wait for node,
API, CoreDNS, and both Flux deployments to be Ready. Stop if VM available
memory approaches 1 GiB or Compose services become unstable.

Create a real, disposable local Git history and serve it only on kind's Docker
network. The Python image digest below was pulled on ARM64 in M4.2. The Alpine
`git-daemon` package supplies `git-http-backend`, which Flux requires; a plain
static HTTP server is insufficient.

```sh
mkdir -p "$scratch/git/work/config"
cd "$scratch/git/work"
git init -q -b main
git config user.name 'M4 Fixture'
git config user.email 'fixture@example.invalid'
cat > config/value.yaml <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: m4-gitops-value
  namespace: m4-gitops-test
data:
  value: initial
EOF
cat > config/kustomization.yaml <<'EOF'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - value.yaml
EOF
git add config && git commit -qm 'Initial disposable ConfigMap'
git clone -q --bare . "$scratch/git/fixture.git"
cd - >/dev/null
docker run -d --name market-pulse-m4-git --network kind \
  --mount "type=bind,src=$scratch/git,dst=/srv,readonly" \
  --mount "type=bind,src=$PWD/infra/flux/git-http.py,dst=/git-http.py,readonly" \
  python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89 \
  sh -c 'apk add --no-cache git git-daemon >/dev/null && python /git-http.py'
```

Run these commands from the **repository root**. Check `docker inspect` for network `kind` and no
published ports, then verify a smart-HTTP response at
`http://market-pulse-m4-git/fixture.git/info/refs?service=git-upload-pack`
from the kind node. Apply `fixture.yaml` only after the server is ready:

```sh
"$kubectl" --kubeconfig "$scratch/kubeconfig" create namespace m4-gitops-test
"$kubectl" --kubeconfig "$scratch/kubeconfig" apply -f infra/flux/fixture.yaml
"$kubectl" --kubeconfig "$scratch/kubeconfig" -n flux-system wait \
  gitrepository/m4-fixture --for=condition=Ready --timeout=90s
"$kubectl" --kubeconfig "$scratch/kubeconfig" -n flux-system wait \
  kustomization/m4-fixture --for=condition=Ready --timeout=90s
```

Compare `.status.artifact.revision` of the GitRepository with the initial
`git rev-parse HEAD`, `.status.lastAppliedRevision` of the Kustomization, and
the ConfigMap's `data.value`. Then change only the **local fixture**:

```sh
sed -i '' 's/value: initial/value: updated/' "$scratch/git/work/config/value.yaml"
git -C "$scratch/git/work" add config/value.yaml
git -C "$scratch/git/work" commit -m 'Update disposable ConfigMap'
git -C "$scratch/git/work" push "$scratch/git/fixture.git" HEAD:main
git -C "$scratch/git/work" rev-parse HEAD
"$kubectl" --kubeconfig "$scratch/kubeconfig" -n flux-system get \
  gitrepository/m4-fixture kustomization/m4-fixture -o yaml
"$kubectl" --kubeconfig "$scratch/kubeconfig" -n m4-gitops-test get \
  configmap/m4-gitops-value -o yaml
"$kubectl" --kubeconfig "$scratch/kubeconfig" -n m4-gitops-test patch \
  configmap/m4-gitops-value --type merge -p '{"data":{"value":"manual-drift"}}'
"$kubectl" --kubeconfig "$scratch/kubeconfig" -n flux-system rollout restart \
  deployment/kustomize-controller
"$kubectl" --kubeconfig "$scratch/kubeconfig" -n flux-system rollout status \
  deployment/kustomize-controller --timeout=120s
```

Poll for both Flux revisions and the ConfigMap value to match the new SHA;
do not force reconciliation for the propagation check. After the drift patch,
wait for Flux to restore `updated` **before** restarting the controller.
Patch the ConfigMap again after restart and verify another automatic restore.
Record timestamps, conditions, logs, and a timeout for each assertion. An
explicit reconcile after repairing a broken fixture server does not prove
automatic polling.

Take at least five steady samples of `docker stats` for the node, `crictl
stats --output json` inside the node for Flux containers, and the node's
`/proc/meminfo` for VM `MemAvailable` and `SwapFree`. Inspect node pressure,
pod restarts, OOM/eviction events, and existing Compose health. Before
teardown, check exact test container IDs and ownership. Then remove only
`market-pulse-m4-git` and the named kind cluster, compare the original Compose
IDs/volumes, and remove only the temporary test directory. Do not use broad
Docker cleanup or delete the pre-existing Compose volumes.

## M4.3 StatsD pilot

The [StatsD pilot runbook](../k8s/statsd/README.md) reuses this isolated
cluster and local Git server. Its separate [Flux resources](statsd-pilot.yaml)
point to `./infra/k8s/statsd` in the disposable fixture repository. Copy the
pilot manifests and canonical mapping into that repository, verify equality,
and commit before applying the Flux resources. Flux creates the pilot namespace
and workload; no Market Pulse service is applied directly with kubectl.

## M4.4 isolated MinIO pilot

The [MinIO runbook](../k8s/minio/README.md) reuses the same two-controller
installation and local Git fixture. Commit its manifests beside the StatsD
pilot in the **disposable** Git history. Reconcile the MinIO namespace first,
bootstrap a unique Secret out of band, then apply
[`minio-app.yaml`](minio-app.yaml). The MinIO StatefulSet and Services are
Flux-managed. The Secret values remain outside Git; the generated PVC and
test client are not Flux-managed.
The namespace's prune exemption and StatefulSet PVC retention protect data
during ordinary configuration reconciliation. Deleting only the named kind
cluster after qualification intentionally discards its local PV.

## Prepared CI qualification

The [M4 integration script](../ci/m4-gitops-e2e.sh) recreates the local Git
fixture on a disposable Linux/amd64 runner, makes two actual fixture commits,
and observes automatic source/Kustomization revision propagation. It installs
only source-controller and kustomize-controller from Flux CLI v2.10.1's
exported manifest, replacing their release tags with the qualified immutable
index digests before apply. The checked-in Flux resources keep credentials
out of Git; the job bootstraps a unique Secret after namespace reconciliation.
The first [Linux/amd64 run](https://github.com/nhatminh06/market-pulse/actions/runs/45290391256)
reached Ready kind and Flux controllers, then both GitRepositories received
HTTP 500 from the fixture server. The server's stderr was not retained, so
the specific CI error remains unconfirmed. A focused Docker test reproduced
the same CGI HTTP 500 with process exit 0 when a root Git 2.54.1 backend read
a UID-1001 bare repository. Configuring `safe.directory=/srv/fixture.git` for
that Git process restored the smart HTTP advertisement. The fixture now makes
only that exact-repository exception, checks the advertisement and expected
commit from a separate Docker-network client before applying Flux resources,
and retains only classified, non-secret server diagnostics on failure.
