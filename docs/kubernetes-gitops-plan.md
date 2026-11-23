# V2-M4.0 — local Kubernetes and Flux feasibility plan

Status: M4.0 architecture reviewed; M4.1–M4.4 local qualifications recorded below,
2026-11-23. Baseline is merged `main` at
`43c650cf5f7d390866f33cfbe4c7c47cb53a4277`. No cluster, Kubernetes
workload, or Flux controller was installed or qualified in M4.0. Docker Compose
remains the working reference: the [M3 E2E run](https://github.com/nhatminh06/market-pulse/actions/runs/45271006974)
passed real MinIO, Iceberg REST, Trino, Terraform, fixture ingestion, M1/M2,
dbt, Gold quality, and monitoring checks. The run did not observe Airflow task
duration telemetry; see [testing evidence](testing.md).

## Current architecture and constraints

The main Compose project creates `marketpulse_default`. MinIO persists warehouse
objects in `minio_data`; the `mc-init` container checks its alias. Terraform
uses the local MinIO endpoint to create the `warehouse` bucket and a pipeline
user, then writes disposable credentials to `.env`. Iceberg REST uses that S3
endpoint. Trino reads Iceberg REST and S3 through its catalog file. The catalog
file currently contains literal development access keys, so it must not be
copied into a Kubernetes ConfigMap unchanged. Bronze ingestion uses PyIceberg
0.7.1; dbt uses Trino to build Silver and Gold. Airflow 3.0.6 uses PostgreSQL
`pg_data`, runs the ingestion/dbt/Gold DAG, and emits StatsD telemetry.
Superset waits for PostgreSQL and Trino, installs its Trino drivers at startup,
and has not been part of the E2E path. The optional monitoring project joins
the main network: StatsD exporter receives Airflow UDP metrics; Prometheus
scrapes it and the read-only warehouse exporter; Grafana reads Prometheus.
Airflow must resolve StatsD before starting or it can fall back to
`NoStatsLogger` (the failed [M3 run](https://github.com/nhatminh06/market-pulse/actions/runs/45243230929)
demonstrated this).

PR CI checks Python lint/tests, DAG import, dbt lint/parse, Terraform
format/init/validate, Compose configuration, YAML/actionlint, and Prometheus
config/rule scenarios. The manual/weekly E2E additionally checks the pinned
MinIO pull and binaries, real Terraform provisioning, fixture ingestion, M1
atomic correction/conflict behavior, M2 lifecycle/recovery, dbt 21/21, Gold
quality 4/4, Trino results, observability, and teardown. Neither suite
validates Kubernetes today. Do not replace these gates during migration.

On this Apple Silicon host, `uname -m` returned `arm64`; Docker Desktop's
Linux VM reported 10 CPUs, 7.748 GiB RAM, and `aarch64`. It had 10 active
containers and 5 active local volumes. `docker stats --no-stream` showed about
1.05 GiB for Trino, 0.60 GiB for Grafana, and 1.39 GiB for an unrelated
container, among other uses; these are one-time observations, not peak
measurements. The host filesystem reported 311 GiB free, which does not imply
that Docker's VM has that much free space. `kubectl` and Docker are installed;
`kind`, `k3d`, `flux`, and `helm` are not installed. No cluster was created,
and no existing Compose service, credential, state file, or volume was changed.

## Cluster choice and resource budget

Choose **kind v0.33.0**, one ARM64 control-plane node, with the release's
`kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed`.
The [kind v0.33.0 release](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0)
lists this digest, AMD64/ARM64 support, and Kubernetes 1.36.4. This version
meets [Flux v2.10.0's minimum for Kubernetes 1.36](https://github.com/fluxcd/flux2/releases/tag/v2.10.0)
(1.36.1). M4.2 selected the [v2.10.1 maintenance release](https://github.com/fluxcd/flux2/releases/tag/v2.10.1)
and verified its ARM64 archive checksum and controller manifests before installation.
Kind's [quick start](https://kind.sigs.k8s.io/docs/user/quick-start/)
documents Docker Desktop, ARM Mac binaries, explicit node images, local image
loading, and named-cluster deletion. A single node is enough for functional
GitOps tests, not high-availability claims.

| Option | Benefits | Costs and decision |
| --- | --- | --- |
| kind | Upstream Kubernetes node images with published ARM64 digest; simple named cluster creation/deletion; direct match to GitHub-hosted Linux CI and `kubectl` examples. | Docker Desktop VM overhead and no production-grade storage; selected for one-node, disposable qualification. |
| k3d | [k3d](https://k3d.io/stable/) runs lightweight k3s in Docker and supports [image import](https://k3d.io/stable/usage/commands/k3d_image_import/); attractive if kind exceeds the memory budget. | Adds k3s defaults/add-ons and another version/image compatibility surface; a k3s image meeting current Flux requirements and its ARM64 manifest have not been selected or verified. |

For M4.1–M4.3, assume the 7.748 GiB Docker VM remains unchanged and other
Compose containers stay running. Reserve at least 1 GiB VM headroom. The
**incremental** cluster/control-plane, Flux, and pilot workload target is
at most 2 GiB observed working memory and 2 CPU at steady state; stop the
pilot on OOM, eviction, or sustained contention. These are acceptance
budgets, not benchmark results. Proposed Kubernetes container requests/limits
for the first pilot: StatsD exporter 50m/64Mi request and 250m/128Mi limit;
one synthetic metric client 50m/64Mi and 250m/128Mi; Flux source and Kustomize
controllers 100m/128Mi and 500m/256Mi **each**. Total proposed requests are
300m/384Mi and limits are 1.5 CPU/768Mi, excluding node/control-plane
overhead. Record actual node allocatable resources, pod peaks, and Docker VM
usage in M4.1; raise limits only from measured need. Full Trino/Airflow/
Grafana coexistence is **not** feasible to assert from this snapshot.
[Kubernetes resource semantics](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)
make requests scheduling inputs and memory limits OOM boundaries.

## Image and runtime compatibility inventory

On 2026-11-23, read-only `docker buildx imagetools inspect` queries returned
`linux/arm64` and `linux/amd64` in the published manifest lists below.
These mutable-tag digests are a point-in-time registry observation, not a
commit to deploy those versions or proof that the application starts on ARM64.
Do not copy locally cached AMD64 images into an ARM64 cluster.

| Current image reference | Observed manifest digest | ARM64 manifest | Runtime gap |
| --- | --- | --- | --- |
| MinIO server and mc-init `ghcr.io/intikhab49/minio-from-source@sha256:1025a6dd8452e6fe8d3fdf16dbce7c18f8417acd22e5abd7376d8322aee5edab` | Same pinned digest | Yes | Unofficial build; ARM64 S3/`mc` round trip not qualified here. |
| `apache/iceberg-rest-fixture:latest` | `sha256:009f9f6897cc1e8fd6e8133c401c7a3e7c333c71293a621d06a71570ae989c29` | Yes | Fixture catalog persistence/restart behavior on Kubernetes unverified. |
| `trinodb/trino:latest` | `sha256:2704eb53935af5e582d63d6033e9bf99d20c325e9b0e30b1016ef59a4773979f` | Yes | JVM sizing, S3 credentials, startup, and ARM64 query path unverified. |
| `postgres:16` | `sha256:3950ad7c3148ca2175ef1b6c36c9efbf78957ae8fbe8b3a5d7c35af50500382f` | Yes | Fresh PVC and Airflow migrations unverified. |
| Base `apache/airflow:3.0.6-python3.11` | `sha256:5a9c24f3a8ab9135f4fac0993072e837271cc8c8f80b8bb7e17a5e60eb8f7ef3` | Yes | Repository Dockerfile adds unpinned Python subdependencies; final ARM64 image/build unverified. |
| `apache/superset:latest` | `sha256:16b50bbef6648912a79e3293d418fefa743ce555d36c5af6b85d859119ed7f88` | Yes | Runtime driver install and health endpoint unqualified by E2E. |
| `prom/statsd-exporter:latest` | `sha256:1d5d341c4eb7abdd3445a3f85c3d498402b676ad29b4cf0421f5d280943871ab` | Yes | M4.3 qualified this digest as version v0.31.0 in a separate Kubernetes pilot; Compose still uses its unchanged `latest` tag. |
| `prom/prometheus:latest` | `sha256:e04987641b57704f408e31f6373ba4138ecf27cb4a165d24aabb6c38fddd0f06` | Yes | Retention/storage and alerts in-cluster unverified. |
| `grafana/grafana:latest` | `sha256:0615ec4fbd4072004b7d86b46ded63013a74b7d6eb1119273dd89e8fa25c13cf` | Yes | Provisioning and persistent dashboard edits unverified. |

The locally built `warehouse-exporter` also uses the repository Dockerfile;
its final ARM64 image is unverified. Verify each selected image's manifest,
pull, binary/health endpoint, and S3/Trino behavior on both Apple Silicon and
GitHub's Linux/amd64 runner before its migration. Pin a **version plus digest**
in the proposed manifests and review provenance/security updates manually;
never let `latest` silently promote. The MinIO digest stays unchanged unless
a separate, qualified storage decision is approved.

## Proposed Kubernetes architecture

Use `flux-system` for Flux and `market-pulse-dev` for the disposable pilot.
Only later, if isolated storage is justified, add `market-pulse-data-dev`.
Keep all Services `ClusterIP`; use `kubectl port-forward` for local inspection
instead of public ingress. Put non-sensitive mappings, scrape rules, Trino
catalog templates, DAG/dbt references, and dashboard JSON in ConfigMaps or
image content. Inject credentials through named Kubernetes Secret references
created locally outside Git; never commit Secret values, base64 data,
Terraform outputs, or `.env`. The present Trino catalog's literal S3 keys
must be replaced with a Secret-backed delivery method before Trino migration.
[Kubernetes Secrets are not encrypted by default](https://kubernetes.io/docs/concepts/security/secrets-good-practices/),
so use disposable credentials, least-privilege RBAC, local-only access, and
delete the entire disposable cluster after tests. NetworkPolicy enforcement
requires a supporting CNI; do not claim isolation from a policy manifest
without a packet-level test ([Kubernetes docs](https://kubernetes.io/docs/concepts/services-networking/network-policies/)).

| Component | Proposed workload and exposure | Storage and readiness |
| --- | --- | --- |
| StatsD exporter | One Deployment, UDP 9125 and HTTP 9102 ClusterIP, mapping ConfigMap. | No PVC; HTTP `/metrics` readiness and a real mapped synthetic counter. |
| Warehouse exporter | One Deployment only after isolated REST/Trino exist; HTTP 9103 ClusterIP. | No PVC; `/metrics` plus successful/failing observation state, not just process liveness. |
| Prometheus, Grafana | One Deployment each for a disposable pilot; internal ClusterIPs and port-forward. HelmRelease only if vetted chart complexity warrants it later. | `emptyDir` first; data loss on restart is acceptable for the pilot. PVC only if retention becomes a requirement. HTTP readiness and datasource query. |
| MinIO | Single-replica StatefulSet, headless plus ClusterIP Service, server credentials Secret, separate fresh PVC; bucket setup as idempotent Job. | Never mount Compose `minio_data`; S3 health and authenticated object round trip. A PVC is local-cluster data, not a backup. |
| Iceberg REST | One Deployment, ClusterIP 8181; S3 configuration and Secret references. | No own PVC in current fixture setup; `/v1/config` readiness plus catalog transaction probe. Treat fixture image/state durability as unverified. |
| PostgreSQL | Single-replica StatefulSet, headless/ClusterIP, fresh PVC and Secret. | Never mount Compose `pg_data`; `pg_isready`, fresh-db migration, restart retention. |
| Trino | One coordinator Deployment, ClusterIP 8080, reviewed memory/JVM settings and Secret-backed catalog. | No PVC for initial query service; HTTP `/v1/info` then `SELECT 1` and Iceberg read. |
| Airflow 3.0.6 | Separate migration Job, scheduler, DAG processor and API server; choose executor/log/DAG-distribution plan before deployment. | PostgreSQL Secret/PVC belong to data boundary; scheduler heartbeat, DAG parse, API health and real task result each checked separately. |
| dbt, ingestion, Gold checks | Explicit Jobs or Airflow tasks using the same pinned application image; never a second writer against Compose data. | Fresh fixture-only bucket/catalog; validate M1 atomic, M2 recovery, dbt 21/21, Gold 4/4 and Trino query. |
| Superset | Defer; then one Deployment with reviewed prebuilt drivers and ClusterIP. | Fresh metadata DB, Secret, `/health` test; no production claims from current Compose config. |

Use [startup, readiness, and liveness probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/)
for the actual process behaviors above, not mere TCP-open checks. Kubernetes
Deployments suit replaceable pods and [StatefulSets](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)
suit identity plus fresh PVCs. Inspect kind's StorageClass before creating
any claim; do not assume dynamic provisioning or that deleting a StatefulSet
deletes its PVCs. Set per-container requests/limits from observed peaks, with
separate bounded Jobs for initialization; avoid running Terraform against the
Compose MinIO endpoint. A later isolated-cluster Terraform experiment needs
its own state and credentials and must not alter `infra/terraform` state.

Airflow is a distinct design gate. The repository's Compose setup uses
`LocalExecutor`, bind-mounted DAG/dbt/ingestion code, a migration container,
PostgreSQL, and a scheduler. Airflow 3 also needs separate DAG processing and
an API server for full operation; its task execution and logs require an
explicit shared-code/log strategy. The [official Airflow chart](https://airflow.apache.org/docs/helm-chart/stable/index.html)
supports LocalExecutor and documents Kubernetes/Helm prerequisites, but chart
version, compatibility with pinned Airflow 3.0.6, storage, migrations, and
resource footprint must be tested before a HelmRelease is chosen. Do not
translate the current scheduler container into a lone Deployment.

## Flux repository design and recovery

Proposed layout (not created in M4.0):

```text
clusters/local-dev/            # Flux GitRepository + root Kustomizations
platform/flux/                 # pinned Flux install/version references
apps/market-pulse/base/        # non-secret workload definitions
apps/market-pulse/local-dev/   # disposable overlays and image digests
```

Use one public read-only `GitRepository` at
`source.toolkit.fluxcd.io/v1` in `flux-system`, tracking a reviewed branch
and reporting its resolved commit. Use `kustomize.toolkit.fluxcd.io/v1`
`Kustomization` boundaries for namespace/config, monitoring pilot, and only
later isolated data services. Set health checks and `dependsOn` so a failed
foundation blocks dependent workloads. Use `prune: true` for disposable
stateless pilot objects; keep PVC/Secret lifecycle outside automatic pruning
and explicitly inventory/delete them at teardown. Start with Flux
source-controller and kustomize-controller; add helm-controller only when a
specific chart (likely Airflow) is justified. `HelmRelease` uses
`helm.toolkit.fluxcd.io/v2` and can detect/correct drift, but is unnecessary
for a single StatsD Deployment. These APIs and reconciliation behaviors are
from the current [GitRepository](https://fluxcd.io/flux/components/source/gitrepositories/),
[Kustomization](https://fluxcd.io/flux/components/kustomize/kustomizations/),
and [HelmRelease](https://fluxcd.io/flux/components/helm/helmreleases/)
references. Pin Flux release/controller versions before installation.

M4.2 may install Flux controllers without invoking bootstrap's repository
write path; the latter [pushes manifests](https://fluxcd.io/flux/installation/)
and is unnecessary for a read-only public source pilot. The local cluster
reads reviewed Git commits. A change is proposed on a branch, checked in PR,
and promoted only by a deliberate reviewed update of the tracked ref or merge;
disable image automation. Test propagation by changing a harmless ConfigMap
value, recording GitRepository artifact revision, Kustomization `Ready`, and
the observed pod configuration. Test drift by manually changing a disposable
ConfigMap and observing Flux restore it; never mutate warehouse/PVC data to
demonstrate drift. For rollback, revert the reviewed Git commit, reconcile,
and verify a known good image/config and workload health. For failed data
changes, a Git revert is insufficient: restore from an isolated backup or
destroy the disposable cluster. Record controller events and conditions on
failure; suspend only for diagnosis, with a reviewed resume.

## Migration sequence and measurable gates

| Milestone | Scope and acceptance evidence |
| --- | --- |
| M4.1 cluster foundation | Install pinned kind and compatible kubectl/Flux CLIs only after review. Create **one named disposable** ARM64 cluster with pinned node digest. Record node version/architecture, `Ready`, DNS, StorageClass presence, Docker VM/pod memory and CPU, and delete that named cluster with no effect on Compose containers/volumes. Fail on resource-budget breach. Run equivalent AMD64 boot in GitHub Actions only after a dedicated CI workflow is reviewed. |
| M4.2 GitOps foundation | Install pinned Flux source/Kustomize controllers, reconcile public GitRepository and root Kustomization to `Ready`, and record exact artifact commit. Validate all YAML with Kustomize build, API/server dry-run, schema checks and Flux validation. Demonstrate Git ConfigMap change propagation and controlled drift repair, then rollback. Inspect RBAC and prove no credentials appear in Git, rendered manifests, logs or diffs. CI can validate manifests; local cluster proves reconciliation. |
| M4.3 first real Market Pulse workload | Deploy only the project's StatsD mapping and exporter via Flux, plus a bounded synthetic metric client. Assert pod rollout/readiness, mapping to expected `market_pulse_*` metric without unbounded labels, and restart after pod deletion. Observe requests/limits and peak use within pilot budget. Remove its namespace and cluster; verify Compose service/volume IDs remain unchanged. No warehouse dependency or live feed. |
| M4.4 isolated stateful pilot | Add only one Flux-managed MinIO StatefulSet with fresh credentials and a 1 GiB claim. Qualify ARM64 image, S3 object round trip, same-PVC pod replacement, Git propagation, drift repair, StatsD coexistence, resource limits, and named-cluster teardown. Do not attach Compose volumes or claim backup durability. |
| M4.5A CI design and release preparation | Add checksum-verified static validation and a narrowly label-gated, disposable Linux/amd64 workflow for only the M4 StatsD and MinIO pilots. Audit secrets, image pinning, isolation, cleanup, and the full diff. Do not run CI or publish in this phase. |
| M4.5B remote qualification | After review and explicit authorization, run one labeled PR qualification on GitHub-hosted Linux/amd64. Assert real Flux revisions, StatsD metrics, MinIO object integrity across pod replacement and Git rollout, drift repair, resource bounds, and teardown. Keep the PR draft until the executed evidence is reviewed. |

For each milestone, retain a baseline `git` SHA, image digests, commands,
Kubernetes/Flux versions, readiness conditions, measured resource peaks,
test output, teardown inventory, and the existing Compose CI/E2E regression
status. A local pass is not a GitHub Actions pass. CI on Linux/amd64 cannot
prove Apple Silicon behavior; local ARM64 cannot prove hosted-runner sizing.
Use explicit assertions for data isolation and teardown, not merely attempted
cleanup. M4.0 provides design and manifest-platform evidence only.

## Non-goals and open decisions

No cluster installation, manifests, Helm chart, Flux bootstrap, cloud service,
paid infrastructure, production data, shared warehouse/PVC, deployment,
Terraform-state change, GitHub secret, or application refactor belongs to
M4.0. No image/platform query here proves service startup or security.
Before M4.1, review this plan and the cluster resource budget. Before M4.4,
resolve fixture catalog persistence, fresh-volume cleanup, Trino credential
injection, and whether the host has enough spare Docker VM memory. Before
M4.5, resolve Airflow 3 chart/version, DAG distribution, executor, logging,
migrations, and isolation from Compose state. Keep the successful Compose
E2E workflow as the reference until equivalent Kubernetes evidence exists.

## M4.1 local foundation qualification (2026-11-23)

One `market-pulse-m4` cluster was created and deleted on this ARM64 Mac using
the [pinned configuration](../infra/kind/cluster.yaml) and an isolated
mode-0600 kubeconfig in a mode-0700 temporary directory. The reproducible
commands and ownership checks are in the [kind runbook](../infra/kind/README.md).
No Flux controller or Market Pulse workload was deployed. The M4.0 plan file
was preserved as uncommitted work throughout. This is **local ARM64 evidence**,
not GitHub Actions/AMD64 qualification.

| Check | Observed result |
| --- | --- |
| Tools and node image | Official kind v0.33.0 `darwin/arm64` binary matched release SHA-256 `0c8c7dbe5e23594a198b786c4bc13dacc101fa6196b0cb0b23a1ca44e61f4b4f`. Official kubectl v1.36.4 `darwin/arm64` matched published SHA-256 `c9e4f713d6fee0043a3d835cca13077cda2bc0973840eb9779360df0b5bdfc69`. Docker Desktop client/server 29.9.3, Linux VM `aarch64`, 10 CPUs, 7.748 GiB RAM. The node used exactly `kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed`. |
| Boot and health | kind reported control-plane `Ready` after 17 seconds. kubectl client/server both reported v1.36.4; node was `Ready` on an ARM64 Linux kernel. API `/readyz?verbose` passed. Both CoreDNS pods, etcd, control-plane components, kindnet, kube-proxy, and local-path provisioner were `Running` with 0 restarts. Node `MemoryPressure`, `DiskPressure`, and `PIDPressure` were all `False`. |
| DNS and storage | A disposable `busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec` pod (ARM64 manifest verified) resolved `kubernetes.default.svc.cluster.local` to `10.96.0.1` and `kube-dns.kube-system.svc.cluster.local` to `10.96.0.10`, then was deleted. The `standard` local-path StorageClass existed; no PV or PVC was created. |
| Resources | Before creation, Docker VM `MemAvailable` was 3,734,808 kB and swap was unused. Across five samples about 10 seconds apart, the node used 799.2–819 MiB and 10.73–13.87% of one CPU; VM `MemAvailable` was 2,951,632–3,037,624 kB (minimum about 2.81 GiB). Both the 2 GiB/2 CPU additional-use target and 1 GiB headroom target passed for this short steady window. The fall in VM available memory from baseline was about 0.75 GiB at its lowest point. |
| Disk | The host filesystem had 311 GiB free before creation. `docker system df` showed 11.23 GB of images, 1.313 GB of containers, 4.061 GB of local volumes, and 4.772 GB of build cache. Docker VM free disk space was not separately measured; no disk-pressure condition appeared on the node. |
| Pressure caveat | `SwapFree` fell from 1,048,572 kB to 860,272 kB during the samples (about 184 MiB of swap used) and had not recovered immediately after teardown. No OOM, eviction, pod restart, sustained PSI memory pressure, or node pressure condition was observed. Transient `FailedScheduling`, `NodeNotReady`, and readiness-probe warnings occurred during cluster bootstrap and cleared; all required components were healthy at qualification time. Peak load and prolonged coexistence with the Compose stack remain unverified. |
| Teardown and isolation | Diagnostics were exported to a private temporary directory before deletion. `kind delete cluster --name market-pulse-m4` removed the exact recorded node container; kind then listed no clusters. The temporary context was absent and the kubeconfig was removed; the user's default kubectl still had no current context. All 9 pre-existing Market Pulse Compose container IDs were unchanged and running, as was the unrelated container. The 5 Docker volume names, including `marketpulse_minio_data` and `marketpulse_pg_data`, were identical before and after. No Kubernetes PV/PVC or named Docker volume was left behind. |

**M4.1 verdict: PASS for the disposable local foundation.** The resource
margin is adequate for the foundation, not a forecast for Flux or the data
platform. Before M4.2, review the swap increase and remeasure with Flux's
controller footprint; stop if available VM memory approaches 1 GiB or any
existing Compose service shows pressure. The original Docker Desktop kubectl
binary (v1.36.1) did not match the upstream published checksum, so the run
used a separately verified v1.36.4 client in the private temporary directory.

## M4.2 local Flux qualification (2026-11-23)

**PASS for disposable local GitOps reconciliation.** This did not deploy a
Market Pulse workload, run the application E2E suite, or qualify GitHub
Actions/AMD64. The [fixture runbook](../infra/flux/README.md) and
[`fixture.yaml`](../infra/flux/fixture.yaml) reproduce the test. All fixture
Git commits were local; no GitHub write or credential was used.

| Check | Observed result |
| --- | --- |
| Versions and provenance | kind v0.33.0 with the M4.1 pinned `kindest/node` digest; Kubernetes v1.36.4 ARM64. Official Flux CLI v2.10.1 `darwin_arm64` archive SHA-256 `c0a8dc285c568905ae38e25074985f3181c82a3c8d66316959561f27a626909d` matched the official checksum file. Only source-controller and kustomize-controller v1.10.2 were installed. Their deployed image identities were `ghcr.io/fluxcd/source-controller@sha256:1405a9e460833df9aff920664243c0df271f42be05e0bf38db31d2b7e9ebbaab` and `ghcr.io/fluxcd/kustomize-controller@sha256:e122808057ef0f6a3cb7fbd8e256b5a528d8a59f41d237f2d6664f54481c26d9`; published indices included ARM64 manifests. These are observed image digests, while the release install manifest used v1.10.2 tags. |
| Platform readiness | Node `Ready`, API `/readyz` returned `ok`, CoreDNS rollout succeeded before Flux installation. Both Flux Deployments rolled out, both GitRepository and Kustomization CRDs existed, both controller pods were Ready with zero restarts, and logs showed recurring reconciliation. Node `MemoryPressure`, `DiskPressure`, and `PIDPressure` were `False`. Startup probe/scheduling warnings cleared. |
| Git source | A bare fixture repository was served by `git http-backend` through a temporary Python HTTP container on Docker's `kind` network with no published port. Plain static HTTP failed with `unexpected EOF` because it lacked smart Git HTTP; after installing Alpine `git-daemon` and an explicit recovery reconcile, GitRepository became Ready at `main@sha1:09decdb37d9af37c88b530a8bc6f50e777343f0f`. Kustomization became Ready at that revision and Flux created the ConfigMap with `value: initial`. The explicit recovery reconcile is not counted as automatic polling. |
| Automatic Git change | A second **local-only** Git commit `d8e1fa3a54f79f4b709e375f090764e4d7e7e7af` changed `value` to `updated` at 22:25:03 UTC. Without a manual reconcile, GitRepository artifact revision and Kustomization last-applied revision both matched that SHA, and the ConfigMap read `updated` by 22:25:15 UTC, within the 90-second observation limit. |
| Drift and restart | A manual patch set `manual-drift` at 22:25:27 UTC; Flux restored `updated` by 22:25:32 UTC. The kustomize-controller Deployment was restarted and became Ready; its Kustomization remained Ready at the second SHA. A second patch after restart was reverted by 22:26:24 UTC, within 20 seconds. This demonstrates recovery of this disposable ConfigMap, not general rollback or data recovery. |
| Resource gates | Before cluster creation, Docker VM `MemAvailable` was 3,254,444 kB and `SwapFree` 849,784 kB. Before Flux installation, the kind node used 670.3 MiB and 12% of one CPU; VM available memory was 2,973,316 kB and swap free was 487,116 kB. Across five post-Flux samples about 11 seconds apart, node working memory was 1.243–1.256 GiB and CPU 16.72–26.87% of one CPU. Source-controller working memory was about 72 MiB; kustomize-controller was 71.1–80.5 MiB. Their observed CPU samples were 0.0005–0.0807 core each. VM `MemAvailable` was 2,894,148–2,937,916 kB (at least 2.76 GiB). `SwapFree` was 528,624–529,140 kB. The node's increase from its pre-Flux snapshot was about 0.59 GiB; VM swap had already declined before Flux installation, so that drop cannot be attributed solely to Flux. The 2 GiB/2 CPU and 1 GiB headroom targets passed in this short steady window. |
| Isolation and teardown | No PVC, Market Pulse workload, Secret, or external endpoint was created. The fixture container and exact named kind node were removed; kind listed no clusters. The default kubectl context remained unset. All nine original Compose containers kept their IDs and running status, and all five original Docker volume identities, including `marketpulse_minio_data` and `marketpulse_pg_data`, remained. A kind-created anonymous volume was removed with the named cluster. No OOM/eviction, sustained pressure, or Compose instability was observed. |

The controller image references produced by `flux install` use release tags;
future persistent GitOps deployment should pin reviewed image digests. The
temporary Git server binds port 80 only inside Docker's `kind` network and is
appropriate solely for a disposable local fixture. Long-running resource
behavior, controller failure across node loss, remote CI, secret isolation
under real workloads, and all Market Pulse application behavior in Kubernetes
remain unverified. M4.3 can proceed after review, with the same resource and
Compose-isolation gates, to deploy only the StatsD pilot.

## M4.3 local Flux-managed StatsD pilot (2026-11-23)

**PASS for the isolated ARM64 StatsD pilot.** The
[pilot manifests and runbook](../infra/k8s/statsd/README.md) deploy only the
project's existing StatsD mapping and exporter. The test made no repository
commit or GitHub write. It did not run Airflow, Prometheus, Grafana, the
warehouse, or GitHub Actions.

| Check | Observed result |
| --- | --- |
| Image and platform | The official `prom/statsd-exporter:v0.31.0` manifest list included Linux/ARM64 (`sha256:d1a405eb0de9ca555f7811de718a383af743f4d49380f2e6567ccf8d9bb37de0`) and Linux/AMD64. The pinned manifest-list digest deployed was `sha256:1d5d341c4eb7abdd3445a3f85c3d498402b676ad29b4cf0421f5d280943871ab`; the running exporter reported v0.31.0 and `linux/arm64`. The test client was the pinned ARM64-capable `python:3.12-alpine@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89`. |
| Flux and inventory | kind v0.33.0, the pinned Kubernetes v1.36.4 node, and Flux v2.10.1 source/Kustomize controllers from M4.2 were reused. A local-only Git commit `f9a85dd9ff460bd6a00c6f6f29fef3af92585fc8` contained the pilot manifests and mapping. GitRepository and Kustomization both became Ready at `main@sha1:f9a85dd9ff460bd6a00c6f6f29fef3af92585fc8`. Flux inventory listed the `market-pulse-gitops-pilot` Namespace, `statsd-mappings-265gfh76cg` ConfigMap, `statsd-exporter` ClusterIP Service, and one `statsd-exporter` Deployment. The Service exposed only TCP 9102 and UDP 9125. The Deployment became Available with one Ready pod and zero restarts. No application manifest was directly applied. |
| Canonical mapping and metric | `check-mapping.sh` passed before the local Git commit. The deployed ConfigMap's `mappings.yml` matched `observability/statsd-mappings.yml` byte for byte; SHA-256 was `24c242a6217400801efa60f6e400d00a348925ab3a630defa543ea81ebe09f5e`. Three synthetic UDP `1250|ms` timer samples produced `market_pulse_airflow_task_duration_seconds_count{task="gitops_probe"} 3`, `_sum` 3.75, and `le="5"` bucket 3. The only pilot histogram label besides bucket `le` was the bounded synthetic task name. An unmatched `unmatched.test.run_123` metric did not appear. These are synthetic measurements, not real Airflow task telemetry. |
| Automatic Git update | A second **local fixture** commit `4d6b618fd555a78a757723fb3e9c63bbe16edff8` added a pod-template annotation at 22:42:19 UTC. Without manual reconcile, the source artifact and applied revision both advanced to that SHA, the Deployment reflected annotation `2`, and a new pod became Ready by 22:42:40 UTC (about 21 seconds). On that new pod, two further timer samples increased the histogram count from 0 to 2 and sum to 2.5 seconds. In-memory metric history was not expected to survive rollout. |
| Drift and pod recovery | At 22:43:20 UTC, a manual patch changed the Deployment annotation to `manual-drift`. Flux restored Git value `2`, and the Deployment was Available by 22:43:41 UTC. The exporter pod was then deleted; a different UID became Ready with zero restarts. Its fresh histogram count increased from 0 to 1 after another synthetic timer, with sum 1.25 seconds. This proves this Deployment's reconciliation and pod replacement, not durable metric retention. |
| Resource and safety gates | Before creation, Docker VM `MemAvailable` was 5,067,076 kB and `SwapFree` 752,244 kB. The kind node used 659.8 MiB and 11.69% of one CPU before Flux. Across five steady post-pilot samples about 11 seconds apart, the node used 949.5–960 MiB and 9.97–20.99% of one CPU. Source-controller used 28.9 MiB, kustomize-controller 82.5–91.9 MiB, StatsD 10.3–10.5 MiB, and the idle client about 0.6 MiB; sampled controller/StatsD CPU remained below 0.01 core each. VM `MemAvailable` was 4,269,836–4,311,000 kB (at least about 4.07 GiB), and `SwapFree` stayed 753,760–753,768 kB. The 2 GiB/2 CPU and 1 GiB headroom targets passed in this short window. No continued swap decline, OOM, eviction, node pressure, or Compose instability appeared. Transient startup readiness/scheduling warnings cleared; final pilot and Flux pods were Ready with zero restarts. |
| Isolation and teardown | No PVC, pilot Secret, NodePort, host port, or persistent data mount existed. The synthetic client, local Git server, and exact named kind cluster were deleted; kind listed no clusters and the default kubectl context remained unset. All nine original Compose containers retained their IDs and running status, and all five original Docker volumes, including `marketpulse_minio_data` and `marketpulse_pg_data`, remained. The kind-created anonymous volume disappeared with the cluster. |

The exporter image is pinned in the pilot manifest, while the existing Compose
`latest` reference was not changed. No real Airflow telemetry, Prometheus
scrape, long-duration stability, remote AMD64 cluster, or stateful workload was
qualified here. M4.4 can be planned after review, but it must use fresh isolated
credentials and storage.

## M4.4 isolated stateful GitOps qualification (2026-11-23)

**Verdict: PASS for local ARM64 disposable stateful recovery.** This remains a
single-node experiment, not backup, high availability, or migration of the
Compose warehouse. The [MinIO runbook](../infra/k8s/minio/README.md) reproduces
the deployment and checks.

| Gate | Observed evidence |
| --- | --- |
| Image and isolation | Anonymous pull of `ghcr.io/intikhab49/minio-from-source@sha256:1025a6dd8452e6fe8d3fdf16dbce7c18f8417acd22e5abd7376d8322aee5edab` succeeded. Its OCI index contains `linux/arm64` manifest `sha256:9fd297fa2c232bf6a56abd6d7c8cae5aba16cb758b14b02c41f404b5b1e563a4`. The non-root ARM64 binaries reported MinIO `RELEASE.2025-10-15T17-29-55Z` and mc `RELEASE.2025-08-13T08-35-41Z`. This is an unofficial third-party build from pinned public upstream commits; the publisher's workflow describes provenance attestation and SBOM, but this run did not cryptographically verify them or scan for vulnerabilities. Only new disposable credentials and synthetic object data were used. |
| Cluster and storage | kind v0.33.0, Kubernetes v1.36.4 ARM64, and Flux v2.10.1 source/Kustomize controllers. `standard` uses `rancher.io/local-path`, `WaitForFirstConsumer`, and PV reclaim policy `Delete`. Fresh 1 GiB PVC `market-pulse-m4-storage/data-minio-0` UID `a66e004d-20e3-47c8-bc8c-e09b9ab60e3d` bound PV `pvc-a66e004d-20e3-47c8-bc8c-e09b9ab60e3d` under the kind node's `/var/local-path-provisioner/`, not a Compose mount. StatefulSet PVC retention was `Retain` for deletion and scale-down; Flux does not own the generated PVC. Namespace pruning was disabled to avoid accidental loss while a claim exists. |
| GitOps and startup | Local fixture commit `951255bcf388b0112a3567de3a9217a5e8774175` was observed in both GitRepository resources and all three Flux Kustomizations. Namespace reconciled first; a unique Secret was bootstrapped out of band; then Flux created the StatefulSet and internal Services. MinIO pod became Ready with zero restarts and the pinned image digest. The test client's initial `mc` command failed because non-root `$HOME` was `/`; directing its config to `/tmp/.mc` resolved this without changing the MinIO workload. |
| S3 and pod recovery | The synthetic `m4-disposable/object.txt` SHA-256 was `17f0bf980b05018070b10e4fec3e5ff12d656d0db089b61321163512ecd8185e` on write and read. Deleting only `minio-0` changed its pod UID from `365c714c-77ad-4015-8f90-07c88adb9af8` to `754121bd-7d40-4734-9210-77a229366516`; the same PVC UID and PV name remained, and a fresh S3 read returned the same digest. |
| Git update and drift | A second real, local-only fixture commit `5e6fa8677723cb016a745d509a1b8319c2a597d5` added pod-template annotation `m4.market-pulse/revision: "2"` at 23:04:20 UTC. Automatic polling advanced the GitRepository and application Kustomization revisions, applied the annotation, and returned MinIO to Ready by 23:05:14 UTC without a manual reconcile. Manual annotation drift at 23:05:28 UTC was restored to `2` by 23:05:49 UTC; the same PVC/PV and S3 digest remained. |
| Coexistence and resources | StatsD stayed Ready and exposed `market_pulse_airflow_task_duration_seconds_count{task="gitops_probe"} 1` after one synthetic UDP sample. Five samples from 23:05:59–23:06:36 UTC showed kind node working memory 1.030–1.043 GiB and CPU 20.43–27.75% of one core. Source controller used 33 MiB, Kustomize controller 95–108 MiB, StatsD 10 MiB, and MinIO 74 MiB. Docker VM `MemAvailable` was 4,048,436–4,113,572 kB, versus 4,920,512 kB before cluster creation; `SwapFree` was 757,720–757,760 kB, versus 756,336 kB before. Incremental RAM stayed below 2 GiB, CPU below 2 cores, and headroom above 1 GiB. All pods had zero restarts; node memory, disk, and PID pressure were False. Transient bootstrap readiness warnings cleared. |
| Teardown | Both test clients, the private Git HTTP container, named kind cluster, temporary kubeconfig, and credential files were deleted. Original nine Compose container identities and running status, and five Docker volume names including MinIO and PostgreSQL data, matched the preflight inventory. The default Kubernetes context remained unchanged. No real repository commit, push, or remote workflow was made. |

M4.5 should add reviewed CI for the disposable cluster and an explicit
credential-bootstrap path before any PR. AMD64 Kubernetes execution, image
attestation verification, vulnerability scanning, long-running load, backup,
and full-platform migration remain unverified.

## M4.5A CI design and security audit (2026-11-23)

**Prepared, not remotely executed.** M4.1–M4.4 remain local ARM64 PASS only.
The proposed Linux/amd64 evidence is a separate M4.5B gate. No application,
Compose, Terraform, fixture data, or M1–M3 validation path changed in M4.5A.

The existing `ci.yml` gains one static job. Its checksum-verified tools are
kind v0.33.0, kubectl v1.36.4, Flux CLI v2.10.1, and kubeconform v0.8.0.
It renders all three Kustomize overlays, checks nine Kubernetes objects
against Kubernetes 1.36.4 schemas from immutable registry commit
`0c4dd99d9873f7b4c2da24c008476f5841adbe2d`, and checks seven Flux
objects against CRD OpenAPI schemas exported by the pinned Flux release.
It also checks mapping equality, approved image digests, namespace/Service/
Secret/PVC boundaries, absence of host paths and privileged settings, local
documentation links, shell/Python syntax, and unchanged M1–M3 Compose and
Terraform paths on the proposed feature PR. The existing actionlint job
validates the new workflow. Static schemas do not replace API admission or
runtime checks.

The new `m4-gitops-e2e.yml` uses only `pull_request: labeled` against `main`.
The job requires the exact `m4-e2e-approved` label event, same-repository PR,
and `feature/v2-gitops` head. It checks out the event's exact head SHA with
credential persistence disabled, uses `permissions: contents: read`, has a
45-minute timeout and PR-scoped concurrency, and has no `workflow_dispatch`
or `pull_request_target`. [GitHub's event documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)
documents labeled PR events and head-vs-merge SHA behavior; the first label
event still needs to be observed on GitHub before scheduling can be claimed.
After static CI passes, a reviewer should add the label only once for the
specific PR head; removing and re-adding it can queue another run. No
workflow was dispatched in M4.5A.

The proposed job downloads checksum-verified tools, checks Linux/amd64 pulls
for the pinned node, Flux, StatsD, MinIO, and test-client images, then creates
only a run-ID-qualified kind cluster. It installs the same two Flux
controllers with immutable image digests, serves a real two-commit Git fixture
inside the kind Docker network, reconciles the StatsD pilot and MinIO
namespace, and creates a unique Secret from mode-0600 files **after** the
namespace is Ready. Flux then applies MinIO; credentials never enter Git.
It checks S3 content SHA-256 across MinIO pod replacement, Git-driven rollout,
and drift correction; it checks fresh StatsD histograms before and after
exporter pod replacement, five resource samples, node pressure, and pod
restarts. An EXIT trap records only selected non-secret statuses on failure,
deletes only named disposable resources, removes credential files and
kubeconfig, checks the kind-created Docker volume is gone, and preserves the
test failure exit status. GitHub Actions logs
are the only retained diagnostics; no artifact uploads are configured.

This credential method is suitable only for a disposable kind cluster.
Kubernetes Secret values are present in that cluster's etcd and in temporary
pod environments, and kind's local-path PV is lost when the cluster is
deleted. The [MinIO publisher](https://github.com/intikhab49/minio-from-source)
is unofficial; its attestation and vulnerability status remain unverified.
The local Git HTTP fixture installs Alpine Git packages at runtime, an
external availability and version-drift dependency. Both the fixture and
the two Flux controllers have broad privileges inside a throwaway cluster;
the Git source is limited to the local Docker network, the workflow receives
no protected secrets, and no warehouse volume is mounted. The workflow is
not a production GitOps secret or supply-chain design. The StatsD and MinIO
application pods and disposable test clients disable service-account token
mounting; the MinIO application and client still receive the disposable Secret
through environment references. This narrow hardening change awaits the
Linux/amd64 runtime check. The Git HTTP fixture's Alpine Git packages remain
unpinned because a separately built and verified image would expand this
milestone's supply-chain scope; package availability and version drift remain
runtime risks.

Before one draft PR and one authorized label-triggered AMD64 run, review the
uncommitted file inventory and the complete diff against `main`. Remote
M4.5B PASS requires the actual job and cleanup assertions to finish; local
schema validation or the previous ARM64 runs cannot substitute for it.
