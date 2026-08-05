# Testing

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
