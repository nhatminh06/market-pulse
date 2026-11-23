#!/usr/bin/env bash
set -euo pipefail
umask 077

test "$(uname -sm)" = 'Linux x86_64'
test -n "${GITHUB_RUN_ID:-}" && test -n "${GITHUB_RUN_ATTEMPT:-}"
cluster="market-pulse-m4-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
scratch=$(mktemp -d "${RUNNER_TEMP:-/tmp}/market-pulse-m4.XXXXXX")
export KUBECONFIG="$scratch/kubeconfig"
export KIND_CONTEXT="kind-$cluster"
git_server=market-pulse-m4-git
node_volumes=
minio_image=ghcr.io/intikhab49/minio-from-source@sha256:1025a6dd8452e6fe8d3fdf16dbce7c18f8417acd22e5abd7376d8322aee5edab
statsd_image=prom/statsd-exporter:v0.31.0@sha256:1d5d341c4eb7abdd3445a3f85c3d498402b676ad29b4cf0421f5d280943871ab
python_image=python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89
node_image=kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed
source_image=ghcr.io/fluxcd/source-controller@sha256:1405a9e460833df9aff920664243c0df271f42be05e0bf38db31d2b7e9ebbaab
kustomize_image=ghcr.io/fluxcd/kustomize-controller@sha256:e122808057ef0f6a3cb7fbd8e256b5a528d8a59f41d237f2d6664f54481c26d9

k() { kubectl --kubeconfig "$KUBECONFIG" "$@"; }
cleanup() {
  result=$?
  trap - EXIT
  set +e
  if [ "$result" -ne 0 ] && [ -f "$KUBECONFIG" ]; then
    echo 'M4 qualification failed; selected non-secret diagnostics:'
    k get pods -A -o wide
    k -n flux-system get gitrepositories,kustomizations
    k -n market-pulse-m4-storage get statefulset,pvc,service
    k get nodes
  fi
  if [ -f "$KUBECONFIG" ]; then
    k -n market-pulse-m4-storage delete pod/m4-s3-client --ignore-not-found --wait=true >/dev/null 2>&1
    k -n market-pulse-gitops-pilot delete pod/m4-metric-client --ignore-not-found --wait=true >/dev/null 2>&1
  fi
  docker rm -f "$git_server" >/dev/null 2>&1
  kind delete cluster --name "$cluster" --kubeconfig "$KUBECONFIG"
  for volume in $node_volumes; do
    if docker volume inspect "$volume" >/dev/null 2>&1; then
      echo "Disposable kind volume remains: $volume" >&2
      result=1
    fi
  done
  if kind get clusters | grep -Fxq "$cluster" || docker ps -a --filter "label=io.x-k8s.kind.cluster=$cluster" --format '{{.Names}}' | grep -q . || docker ps -a --filter "name=^/$git_server$" --format '{{.Names}}' | grep -q .; then
    echo 'Disposable teardown incomplete' >&2
    result=1
  fi
  rm -rf "$scratch"
  if [ "$result" -eq 0 ]; then echo 'M4 qualification and named-resource teardown passed'; fi
  exit "$result"
}
trap cleanup EXIT

test -z "$(kind get clusters | grep -Fx "$cluster" || true)"
test -z "$(docker ps -a --filter "name=^/$git_server$" --format '{{.Names}}')"
test "$(kubectl version --client -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["clientVersion"]["gitVersion"])')" = v1.36.4
test "$(flux --version)" = 'flux version 2.10.1'
echo "Qualifying PR head $(git rev-parse HEAD) on Linux/amd64"

for image in "$node_image" "$source_image" "$kustomize_image" "$statsd_image" "$minio_image" "$python_image"; do
  docker pull --platform linux/amd64 "$image" >/dev/null
  test "$(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}')" = linux/amd64
  echo "AMD64 image verified: $image"
done
docker run --rm --platform linux/amd64 --entrypoint minio "$minio_image" --version | grep -F 'RELEASE.2025-10-15T17-29-55Z'
docker run --rm --platform linux/amd64 --entrypoint mc "$minio_image" --version | grep -F 'RELEASE.2025-08-13T08-35-41Z'
baseline_mem=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
baseline_swap=$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)
printf 'Runner baseline MemAvailable=%s kB SwapFree=%s kB\n' "$baseline_mem" "$baseline_swap"

kind create cluster --name "$cluster" --config infra/kind/cluster.yaml --kubeconfig "$KUBECONFIG" --wait 120s
node_volumes=$(docker inspect "$cluster-control-plane" --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}} {{end}}{{end}}')
test "$(k config current-context)" = "$KIND_CONTEXT"
k wait node/"$cluster-control-plane" --for=condition=Ready --timeout=120s
k -n kube-system rollout status deployment/coredns --timeout=120s
test "$(k get node "$cluster-control-plane" -o jsonpath='{.status.nodeInfo.kubeletVersion}')" = v1.36.4
test "$(k get node "$cluster-control-plane" -o jsonpath='{.status.nodeInfo.architecture}')" = amd64
k run m4-dns-probe --image="$python_image" --restart=Never --overrides='{"spec":{"automountServiceAccountToken":false}}' --command -- sleep 120
k wait pod/m4-dns-probe --for=condition=Ready --timeout=120s
k exec m4-dns-probe -- python -c 'import socket; assert socket.getaddrinfo("kubernetes.default.svc.cluster.local", 443)'
k delete pod/m4-dns-probe --wait=true
test "$(k get storageclass standard -o jsonpath='{.provisioner} {.volumeBindingMode} {.reclaimPolicy}')" = 'rancher.io/local-path WaitForFirstConsumer Delete'

flux install --export --components=source-controller,kustomize-controller > "$scratch/flux-install.yaml"
python3 - "$scratch/flux-install.yaml" "$source_image" "$kustomize_image" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
for old, new in (("ghcr.io/fluxcd/source-controller:v1.10.2", sys.argv[2]),
                 ("ghcr.io/fluxcd/kustomize-controller:v1.10.2", sys.argv[3])):
    assert s.count(old) == 1, old
    s = s.replace(old, new)
p.write_text(s)
PY
k apply --server-side -f "$scratch/flux-install.yaml" >/dev/null
k -n flux-system rollout status deployment/source-controller --timeout=180s
k -n flux-system rollout status deployment/kustomize-controller --timeout=180s
test "$(k -n flux-system get deployment/source-controller -o jsonpath='{.spec.template.spec.containers[0].image}')" = "$source_image"
test "$(k -n flux-system get deployment/kustomize-controller -o jsonpath='{.spec.template.spec.containers[0].image}')" = "$kustomize_image"

mkdir -p "$scratch/git/work/infra/k8s" "$scratch/git/work/observability"
cp -R infra/k8s/statsd infra/k8s/minio "$scratch/git/work/infra/k8s/"
cp observability/statsd-mappings.yml "$scratch/git/work/observability/"
(cd "$scratch/git/work" && sh infra/k8s/statsd/check-mapping.sh && git init -q -b main && git config user.name 'M4 Fixture' && git config user.email 'fixture@example.invalid' && git add infra observability && git commit -qm 'Initial disposable M4 manifests')
initial_sha=$(git -C "$scratch/git/work" rev-parse HEAD)
git clone -q --bare "$scratch/git/work" "$scratch/git/fixture.git"
docker run -d --name "$git_server" --network kind --mount "type=bind,src=$scratch/git,dst=/srv,readonly" --mount "type=bind,src=$PWD/infra/flux/git-http.py,dst=/git-http.py,readonly" "$python_image" sh -c 'apk add --no-cache git git-daemon >/dev/null && python /git-http.py' >/dev/null
test "$(docker inspect "$git_server" --format '{{json .NetworkSettings.Ports}}')" = '{}'
k apply -f infra/flux/statsd-pilot.yaml -f infra/flux/minio-pilot.yaml
k -n flux-system wait gitrepository/m4-statsd gitrepository/m4-minio --for=condition=Ready --timeout=180s
k -n flux-system wait kustomization/m4-statsd kustomization/m4-minio-namespace --for=condition=Ready --timeout=180s
test "$(k -n flux-system get gitrepository/m4-minio -o jsonpath='{.status.artifact.revision}')" = "main@sha1:$initial_sha"
test "$(k -n flux-system get kustomization/m4-minio-namespace -o jsonpath='{.status.lastAppliedRevision}')" = "main@sha1:$initial_sha"
test "$(k -n flux-system get kustomization/m4-statsd -o jsonpath='{.status.lastAppliedRevision}')" = "main@sha1:$initial_sha"
k -n market-pulse-gitops-pilot rollout status deployment/statsd-exporter --timeout=120s
test "$(k -n market-pulse-gitops-pilot get deployment/statsd-exporter -o jsonpath='{.spec.template.spec.containers[0].image}')" = "$statsd_image"

SCRATCH_DIR="$scratch" python3 - <<'PY'
import os
import secrets
from pathlib import Path
p = Path(os.environ["SCRATCH_DIR"])
for name, value in (("root-user", "m4" + secrets.token_hex(10)),
                    ("root-password", secrets.token_urlsafe(36))):
    path = p / name
    path.write_text(value)
    path.chmod(0o600)
PY
k -n market-pulse-m4-storage create secret generic minio-credentials --from-file=root-user="$scratch/root-user" --from-file=root-password="$scratch/root-password" >/dev/null
k apply -f infra/flux/minio-app.yaml
k -n flux-system wait kustomization/m4-minio-app --for=condition=Ready --timeout=180s
k -n market-pulse-m4-storage rollout status statefulset/minio --timeout=180s
test "$(k -n flux-system get kustomization/m4-minio-app -o jsonpath='{.status.lastAppliedRevision}')" = "main@sha1:$initial_sha"
test "$(k -n market-pulse-m4-storage get pvc/data-minio-0 -o jsonpath='{.status.phase}')" = Bound
pvc_uid=$(k -n market-pulse-m4-storage get pvc/data-minio-0 -o jsonpath='{.metadata.uid}')
pv=$(k -n market-pulse-m4-storage get pvc/data-minio-0 -o jsonpath='{.spec.volumeName}')
pv_path=$(k get pv "$pv" -o jsonpath='{.spec.hostPath.path}')
case "$pv_path" in /var/local-path-provisioner/*) ;; *) echo 'Unsafe PV path' >&2; exit 1;; esac
docker inspect "$cluster-control-plane" --format '{{json .Mounts}}' | python3 -c 'import json,sys; mounts=json.load(sys.stdin); assert not any((m.get("Name") or "").startswith("marketpulse_") for m in mounts),mounts'
running_image=$(k -n market-pulse-m4-storage get pod/minio-0 -o jsonpath='{.status.containerStatuses[0].imageID}')
case "$running_image" in
  "$minio_image"|ghcr.io/intikhab49/minio-from-source@sha256:dc4dabfc3d059eb5a51eaad284b4aadabaec2ad86a0ce72725ae349df3213870) ;;
  *) echo "Unexpected MinIO runtime image: $running_image" >&2; exit 1;;
esac
echo "MinIO image $running_image Ready with disposable PVC $pvc_uid and PV $pv"

k apply -f infra/k8s/minio/client.yaml
k -n market-pulse-m4-storage wait pod/m4-s3-client --for=condition=Ready --timeout=120s
k -n market-pulse-m4-storage exec m4-s3-client -- sh -ec 'mc --config-dir /tmp/.mc alias set pilot http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null; printf "Market Pulse M4.4 disposable object v1\n" >/tmp/object; mc --config-dir /tmp/.mc mb pilot/m4-disposable >/dev/null; mc --config-dir /tmp/.mc cp /tmp/object pilot/m4-disposable/object.txt >/dev/null'
expected_sha=$(printf 'Market Pulse M4.4 disposable object v1\n' | sha256sum | awk '{print $1}')
check_object() { KUBECTL=kubectl sh infra/k8s/minio/check-object.sh "$KUBECONFIG" "$expected_sha"; }
check_claim() {
  test "$(k -n market-pulse-m4-storage get pvc/data-minio-0 -o jsonpath='{.metadata.uid}')" = "$pvc_uid"
  test "$(k -n market-pulse-m4-storage get pvc/data-minio-0 -o jsonpath='{.spec.volumeName}')" = "$pv"
  test "$(k get pv "$pv" -o jsonpath='{.status.phase}')" = Bound
}
check_object
old_uid=$(k -n market-pulse-m4-storage get pod/minio-0 -o jsonpath='{.metadata.uid}')
k -n market-pulse-m4-storage delete pod/minio-0 --wait=true
k -n market-pulse-m4-storage wait pod/minio-0 --for=condition=Ready --timeout=180s
test "$(k -n market-pulse-m4-storage get pod/minio-0 -o jsonpath='{.metadata.uid}')" != "$old_uid"
check_claim
check_object
pre_git_uid=$(k -n market-pulse-m4-storage get pod/minio-0 -o jsonpath='{.metadata.uid}')

python3 - "$scratch/git/work/infra/k8s/minio/app/statefulset.yaml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
old = '  template:\n    metadata:\n      labels:'
assert s.count(old) == 1
p.write_text(s.replace(old, '  template:\n    metadata:\n      annotations:\n        m4.market-pulse/revision: "2"\n      labels:'))
PY
git -C "$scratch/git/work" add infra/k8s/minio/app/statefulset.yaml
git -C "$scratch/git/work" commit -qm 'Annotate disposable MinIO pod template'
git -C "$scratch/git/work" push -q "$scratch/git/fixture.git" HEAD:main
updated_sha=$(git -C "$scratch/git/work" rev-parse HEAD)
echo "Waiting for automatic Flux polling of local fixture $updated_sha"
for _ in $(seq 1 36); do
  source_rev=$(k -n flux-system get gitrepository/m4-minio -o jsonpath='{.status.artifact.revision}')
  applied_rev=$(k -n flux-system get kustomization/m4-minio-app -o jsonpath='{.status.lastAppliedRevision}')
  annotation=$(k -n market-pulse-m4-storage get statefulset/minio -o jsonpath='{.spec.template.metadata.annotations.m4\.market-pulse/revision}')
  ready=$(k -n market-pulse-m4-storage get statefulset/minio -o jsonpath='{.status.readyReplicas}')
  updated=$(k -n market-pulse-m4-storage get statefulset/minio -o jsonpath='{.status.updatedReplicas}')
  revisions=$(k -n market-pulse-m4-storage get statefulset/minio -o jsonpath='{.status.currentRevision} {.status.updateRevision}')
  if [ "$source_rev" = "main@sha1:$updated_sha" ] && [ "$applied_rev" = "main@sha1:$updated_sha" ] && [ "$annotation" = 2 ] && [ "$ready" = 1 ] && [ "$updated" = 1 ] && [ "${revisions% *}" = "${revisions#* }" ]; then break; fi
  sleep 5
done
test "$source_rev" = "main@sha1:$updated_sha" && test "$applied_rev" = "main@sha1:$updated_sha" && test "$annotation" = 2 && test "$ready" = 1 && test "$updated" = 1 && test "${revisions% *}" = "${revisions#* }"
test "$(k -n market-pulse-m4-storage get pod/minio-0 -o jsonpath='{.metadata.uid}')" != "$pre_git_uid"
check_claim
check_object
k -n market-pulse-m4-storage patch statefulset/minio --type merge -p '{"spec":{"template":{"metadata":{"annotations":{"m4.market-pulse/revision":"manual-drift"}}}}}' >/dev/null
for _ in $(seq 1 36); do
  annotation=$(k -n market-pulse-m4-storage get statefulset/minio -o jsonpath='{.spec.template.metadata.annotations.m4\.market-pulse/revision}')
  ready=$(k -n market-pulse-m4-storage get statefulset/minio -o jsonpath='{.status.readyReplicas}')
  if [ "$annotation" = 2 ] && [ "$ready" = 1 ]; then break; fi
  sleep 5
done
test "$annotation" = 2 && test "$ready" = 1
check_claim
check_object

k -n market-pulse-gitops-pilot run m4-metric-client --image="$python_image" --restart=Never --overrides='{"spec":{"automountServiceAccountToken":false}}' --command -- sleep 3600
k -n market-pulse-gitops-pilot wait pod/m4-metric-client --for=condition=Ready --timeout=120s
check_statsd() {
  local expected=$1
  k -n market-pulse-gitops-pilot exec -i m4-metric-client -- python - "$expected" <<'PY'
import socket, sys, time, urllib.request
host = 'statsd-exporter.market-pulse-gitops-pilot.svc.cluster.local'
count = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for _ in range(count):
    sock.sendto(b'airflow.dag.market_pulse.gitops_probe.duration:1250|ms', (host, 9125))
sock.sendto(b'unmatched.test.run_123:1|c', (host, 9125))
time.sleep(2)
metrics = urllib.request.urlopen(f'http://{host}:9102/metrics', timeout=5).read().decode()
def sample(suffix, label=''):
    matches = [float(line.split()[-1]) for line in metrics.splitlines()
               if line.startswith('market_pulse_airflow_task_duration_seconds_' + suffix + '{')
               and 'task="gitops_probe"' in line and label in line]
    assert len(matches) == 1, (suffix, matches)
    return matches[0]
assert sample('count') == count
assert sample('sum') == count * 1.25
assert sample('bucket', 'le="5"') == count
assert 'run_123' not in metrics
print(f'Synthetic StatsD histogram count={count}, sum={count * 1.25}')
PY
}
check_statsd 3
old_statsd_uid=$(k -n market-pulse-gitops-pilot get pod -l app=statsd-exporter -o jsonpath='{.items[0].metadata.uid}')
k -n market-pulse-gitops-pilot delete pod -l app=statsd-exporter --wait=true
k -n market-pulse-gitops-pilot rollout status deployment/statsd-exporter --timeout=120s
test "$(k -n market-pulse-gitops-pilot get pod -l app=statsd-exporter -o jsonpath='{.items[0].metadata.uid}')" != "$old_statsd_uid"
check_statsd 1
check_claim
check_object

for sample in 1 2 3 4 5; do
  docker stats --no-stream --format 'kind node memory={{.MemUsage}} cpu={{.CPUPerc}}' "$cluster-control-plane"
  vm_mem=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
  swap_free=$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)
  printf 'sample=%s VM MemAvailable=%s kB SwapFree=%s kB\n' "$sample" "$vm_mem" "$swap_free"
  test "$vm_mem" -ge 1048576
  test "$swap_free" -ge "$((baseline_swap > 262144 ? baseline_swap - 262144 : 0))"
  docker stats --no-stream --format '{{json .}}' "$cluster-control-plane" | python3 -c 'import json,re,sys; d=json.load(sys.stdin); cpu=float(d["CPUPerc"].strip("%")); m=re.fullmatch(r"([0-9.]+)([KMG]iB|B)",d["MemUsage"].split("/")[0].strip()); assert m; mib=float(m[1])*{"B":1/1048576,"KiB":1/1024,"MiB":1,"GiB":1024}[m[2]]; assert cpu < 200 and mib < 2048,(cpu,mib)'
  docker exec "$cluster-control-plane" crictl stats --output json | python3 -c 'import json,sys; d=json.load(sys.stdin); relevant=("source-controller","kustomize-controller","statsd-exporter","minio-0"); print("pod MiB:",[(p,int(x.get("memory",{}).get("workingSetBytes",{}).get("value",0))//1048576) for x in d.get("stats",[]) if any(t in (p:=x.get("attributes",{}).get("labels",{}).get("io.kubernetes.pod.name","")) for t in relevant)])'
  sleep 8
done
k get node "$cluster-control-plane" -o json | python3 -c 'import json,sys; d=json.load(sys.stdin); c={x["type"]:x["status"] for x in d["status"]["conditions"]}; assert c["Ready"]=="True" and all(c[x]=="False" for x in ("MemoryPressure","DiskPressure","PIDPressure")); print("Node pressure clear")'
k get pods -A -o json | python3 -c 'import json,sys; d=json.load(sys.stdin); bad=[(p["metadata"]["name"],s["restartCount"]) for p in d["items"] for s in p.get("status",{}).get("containerStatuses",[]) if s["restartCount"]]; assert not bad,bad; print("Pod restarts: 0")'
echo "M4 assertions passed; fixture revisions $initial_sha -> $updated_sha; object SHA-256 $expected_sha"
