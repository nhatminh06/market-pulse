# Testing

## M1 image recovery (verified 2026-11-23)

The October 5, 2026 scheduled E2E run failed at `Start MinIO` while pulling
`minio/mc:latest`. Both official Docker Hub image repositories were withdrawn;
reports also show anonymous Quay pulls failing. The proposed Compose change
uses `ghcr.io/intikhab49/minio-from-source@sha256:1025a6dd8452e6fe8d3fdf16dbce7c18f8417acd22e5abd7376d8322aee5edab` for
both services. Its [published Dockerfile](https://github.com/intikhab49/minio-from-source/blob/main/Dockerfile)
builds MinIO server from upstream commit `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a`
and `mc` from `7394ce0dd2a80935aded936b09fa12cbb3cb8096`, and includes
`sh` and `curl`. The publisher documents `linux/amd64` and `linux/arm64` builds
and a storage round-trip smoke test. The [upstream server release](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)
contains a session-policy security fix. This is an unofficial, source-built
image; upstream MinIO community source is archived, so future security fixes
are not assured. Review its security and maintenance status periodically.
The [public GHCR package page](https://github.com/intikhab49/minio-from-source/pkgs/container/minio-from-source)
lists this digest for the release. [E2E run 45209879444](https://github.com/nhatminh06/market-pulse/actions/runs/45209879444)
pulled it anonymously on GitHub's `linux/amd64` runner at commit
`cdfbb69cd6dee45e9f4d36dcd51883b2f408181a`.

The server command, S3 endpoint, root credentials, and Terraform bucket/user
provisioning stay the same. The image includes `mc`, so the existing `mc-init`
alias command still applies. The server health check uses the image's bundled
`curl`. It runs as root to retain access to volumes created by the former
root-running image; this retains the former local permission model.
The fixture run verified the existing S3, Terraform, PyIceberg, and Trino path.
`mc-init` started, but its completion was not separately asserted.

Rejected candidates: the deleted Docker Hub images cannot be pulled; Quay
cannot be assumed public; frozen third-party mirrors of the last official
image predate the October 2025 security fix; SILO is a maintained fork but
changes the storage implementation and needs broader compatibility testing.

The successful run loaded 6 fixture rows for AAPL and MSFT, reported dbt
`PASS=21 WARN=0 ERROR=0 SKIP=0 TOTAL=21`, passed all 4 Python gold checks,
returned computed AAPL/MSFT rows from Trino, and removed the stack and volumes.
It used disposable local-development credentials and no live market ingestion.

To repeat the manual E2E workflow on a branch:

```bash
gh workflow run e2e.yml --ref YOUR_BRANCH
gh run list --workflow e2e.yml --branch YOUR_BRANCH --limit 1
# Use the run ID from the previous command:
gh run watch RUN_ID --exit-status
gh run view RUN_ID --log-failed
```

The E2E workflow pulls the image and checks its Linux architecture, binary
versions, and required tools before building the stack. Ref-scoped workflow
concurrency is retained because the original run `45140233576` remains stuck
in the former workflow-wide group. Other floating image tags in Compose
remain a reproducibility risk; they are outside this image recovery.

## What runs where

| Layer | Tool | Runs in PR CI? | Runs in scheduled/manual E2E? |
|---|---|---|---|
| Python unit/integration/smoke tests | pytest | Yes (`python-tests` job) | Yes (transitively, same repo state) |
| Python lint | ruff | Yes (`python-lint` job) | No |
| dbt project parse + Jinja/YAML validity | `dbt parse` | Yes (`dbt-lint-parse` job) | No |
| SQL style | SQLFluff | Yes (`dbt-lint-parse` job) | No |
| dbt model **execution** (`dbt build`) | dbt-trino against live Trino | **No** | Yes (`.github/workflows/e2e.yml`) |
| dbt **tests** (not_null, unique, relationships, etc.) | `dbt build` runs these too | **No** | Yes |
| Gold data-quality gate (`quality/validate_gold.py`) | Python + live Trino query | Only its check-interpretation logic, via a stubbed cursor (`tests/integration/test_gold_validation.py`) | Yes, for real |
| Airflow DAG import + structure | `DagBag` | Yes (`dag-import` job) | N/A |
| docker-compose syntax/interpolation | `docker compose config` | Yes (`compose-config` job) | N/A |
| Terraform fmt/init/validate | terraform CLI | Yes (`terraform` job) | N/A |
| Full pipeline (ingest → bronze → silver → gold → quality gate) against live infra | full docker-compose stack | **No** | Yes, against a fixture (not live yfinance) |

**Verified:** [run 45209879444](https://github.com/nhatminh06/market-pulse/actions/runs/45209879444) (2026-11-23) — 21/21 dbt model+test checks passed, 4/4 Python quality-gate checks passed, representative Trino query returned real values. The earlier [August 6 run 31067450380](https://github.com/nhatminh06/market-pulse/actions/runs/31067450380) also passed those checks. Re-check the latest run before relying on it.

**Why the split:** Trino, Iceberg REST, and MinIO need to actually be
running for dbt SQL or the quality gate's queries to mean anything, and
GitHub-hosted PR runners are not a reliable place to boot a 6+ service
stack on every push. Pull-request CI instead validates everything that
*can* be checked statically or against fixtures — which is most of the
logic that actually breaks in practice (a bad Jinja expression, a lint
violation, a broken DAG import, a wrong window-function formula) — and the
manual/weekly E2E workflow is where the SQL is proven to actually execute
correctly against real Trino/Iceberg.

## Why gold-layer SQL isn't tested with SQLite/an in-process substitute

`fct_daily_metrics.sql` uses Trino-specific window-function syntax
(`rows between N preceding and current row`, `stddev()` defaulting to
sample standard deviation, NULL-skipping aggregate semantics). Emulating
that faithfully in SQLite would require re-implementing Trino's frame and
NULL-handling semantics — a large surface area to get subtly wrong, and a
second source of truth that could drift from the actual SQL without anyone
noticing.

Instead: `tests/unit/test_metric_calculations.py` re-implements each
formula in plain pandas (documented inline, one function per formula,
mirroring the SQL exactly) and tests properties any correct implementation
must have — first-row nulls, partial windows, division-by-zero guards,
threshold behavior. These are **specification/regression tests on the
formulas**, not proof the SQL itself is bug-free; that proof only comes
from `dbt test` against live Trino in the E2E workflow.

## Current test inventory

Counts below are generated, not hand-maintained — recount before quoting
them anywhere (e.g. `pytest tests/ --collect-only -q | tail -1` and
`grep -c` over the dbt schema.yml files) since they will drift as tests are
added.

```bash
# Python test count
pytest tests/ --collect-only -q | tail -1

# dbt test count (approximate — counts YAML test entries, not custom
# singular tests, of which there are currently none)
grep -rE "tests:|- not_null|- unique|- accepted_values|- relationships|dbt_utils\." \
  dbt/market_pulse/models/*/schema.yml | wc -l
```
