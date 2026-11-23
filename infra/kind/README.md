# Disposable local kind foundation

This is the M4.1 configuration for one cluster named `market-pulse-m4`.
It has one ARM64-compatible control-plane node and no host mounts, host-port
mapping, workload, or shared volume. It does not install Flux. Keep the
existing Compose services and named volumes running; the cluster is separate.

Use [kind v0.33.0](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0)
and [kubectl v1.36.4](https://dl.k8s.io/v1.36.4/bin/darwin/arm64/kubectl)
on an ARM64 Mac. Verify the official binaries before executing them:

```sh
curl -fsSL https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-darwin-arm64 -o /private/tmp/kind-darwin-arm64
curl -fsSL https://dl.k8s.io/v1.36.4/bin/darwin/arm64/kubectl -o /private/tmp/kubectl-darwin-arm64
printf '%s  %s\n' 0c8c7dbe5e23594a198b786c4bc13dacc101fa6196b0cb0b23a1ca44e61f4b4f /private/tmp/kind-darwin-arm64 | shasum -a 256 -c -
printf '%s  %s\n' c9e4f713d6fee0043a3d835cca13077cda2bc0973840eb9779360df0b5bdfc69 /private/tmp/kubectl-darwin-arm64 | shasum -a 256 -c -
chmod 700 /private/tmp/kind-darwin-arm64 /private/tmp/kubectl-darwin-arm64
```

From the repository root, first check Docker VM capacity, existing kind
clusters, and current contexts. **Stop** if `market-pulse-m4` already exists;
do not reuse or delete a cluster whose ownership is unknown. Do not stop
Compose services to make room.

```sh
docker info --format 'cpus={{.NCPU}} memory_bytes={{.MemTotal}} architecture={{.Architecture}}'
/private/tmp/kind-darwin-arm64 get clusters
kubectl config get-contexts -o name
mkdir -m 700 /private/tmp/market-pulse-m4-run
umask 077
touch /private/tmp/market-pulse-m4-run/kubeconfig
/private/tmp/kind-darwin-arm64 create cluster \
  --name market-pulse-m4 \
  --config infra/kind/cluster.yaml \
  --kubeconfig /private/tmp/market-pulse-m4-run/kubeconfig \
  --wait 5m
/private/tmp/kubectl-darwin-arm64 --kubeconfig /private/tmp/market-pulse-m4-run/kubeconfig get nodes -o wide
```

The temporary path must be unused before `mkdir`; choose another private path
if it exists. The explicit `--kubeconfig` prevents changes to the user's
default contexts. Check `/readyz?verbose`, `get pods -A`, in-cluster DNS, node
pressure conditions, `docker stats` for the node container, and Docker VM
`MemAvailable` in several samples. The M4.1 [qualification record](../../docs/kubernetes-gitops-plan.md)
contains the exact checks and observed limits. A later run must gather its
own evidence rather than relying on those measurements.

Before teardown, export diagnostics and confirm the node's name, image, and
container ID are the ones recorded during **this** run. The commands below
refuse a different node name. Delete only the named cluster, then check that
it is gone and compare Compose container IDs and volume names with their
pre-run inventories. Do not use broad Docker cleanup commands.

```sh
test "$(docker ps -a --filter label=io.x-k8s.kind.cluster=market-pulse-m4 --format '{{.Names}}')" = market-pulse-m4-control-plane
docker inspect market-pulse-m4-control-plane --format '{{.Id}} {{.Config.Image}}'
/private/tmp/kind-darwin-arm64 export logs --name market-pulse-m4 /private/tmp/market-pulse-m4-run/logs
/private/tmp/kind-darwin-arm64 delete cluster \
  --name market-pulse-m4 \
  --kubeconfig /private/tmp/market-pulse-m4-run/kubeconfig
/private/tmp/kind-darwin-arm64 get clusters
docker ps -a --filter label=io.x-k8s.kind.cluster=market-pulse-m4
rm /private/tmp/market-pulse-m4-run/kubeconfig
```

Never point Kubernetes at the Compose MinIO/PostgreSQL volumes or Terraform
state. No Kubernetes workload or PVC is part of M4.1.

M4.2 reuses this exact cluster and isolated kubeconfig for the
[disposable Flux fixture](../flux/README.md). Its [qualification evidence](../../docs/kubernetes-gitops-plan.md)
records controller readiness, Git revision propagation, drift recovery,
resources, and teardown; it does not deploy a Market Pulse service.
M4.3 uses the same isolated foundation for the
[Flux-managed StatsD pilot](../k8s/statsd/README.md), then removes the named
cluster without touching Compose.

## Prepared Linux/amd64 qualification

M4.5A adds a [label-gated workflow](../../.github/workflows/m4-gitops-e2e.yml)
for a future GitHub-hosted runner. It uses the same node-image digest and
kind v0.33.0, but names the disposable cluster
`market-pulse-m4-ci-<run-id>-<attempt>` and passes an isolated kubeconfig to
every command. The [tool installer](../ci/install-m4-tools.sh) checks the
published SHA-256 values before execution. This workflow was prepared in
M4.5A; Linux/amd64 cluster behavior is still unverified until M4.5B runs.
