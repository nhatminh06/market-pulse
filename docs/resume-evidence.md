# Resume / README claim verification

Every public-facing claim about this project should trace to one of the
rows below. If you can't find the claim here, don't state it publicly until
it's added and verified. Counts were generated on 2026-08-05 against this
branch's working tree — re-run the commands before reusing a number, since
they drift as the repo changes.

| Claim | Evidence | Status |
|---|---|---|
| 20 configured equities | `dbt/market_pulse/seeds/ticker_sector_map.csv` (20 data rows) | **Verified** |
| Bronze → silver → gold medallion architecture | `ingestion/ingest_bronze.py` (bronze), `dbt/market_pulse/models/silver/stg_prices.sql` (silver), `dbt/market_pulse/models/gold/*.sql` (gold) | **Verified** |
| Apache Iceberg storage | `ingestion/ingest_bronze.py:get_catalog()` (REST catalog), `trino/catalog/iceberg.properties` | **Verified** |
| Terraform-provisioned MinIO bucket + least-privilege IAM | `infra/terraform/storage.tf` — policy scoped to `s3:GetObject/PutObject/DeleteObject/ListBucket` on exactly one bucket's ARNs | **Verified** |
| dbt test count | 16 test instances across 3 of 4 SQL models (`stg_prices`, `fct_daily_metrics`, `dim_ticker`); `dim_date` has zero test coverage today. Recount: see `docs/testing.md` | **Verified** (16, as of this branch) |
| "75% of models covered by tests" | 3 of 4 models (`stg_prices`, `fct_daily_metrics`, `dim_ticker`) have at least one test; `dim_date` has none | **Verified** (75%, coincidentally matches the prior claim — but recount before reusing, it will drift) |
| Python test count | `pytest tests/ --collect-only -q` → 55 tests (54 run + 1 intentionally skipped without a live Trino stack) | **Verified** (as of this branch) |
| ~60,000 rows across the pipeline | No data has been ingested in this environment (no Docker daemon) | **Unverified — requires manual run**: `select count(*) from iceberg.bronze.prices` after a full backfill |
| ~60% faster analytical queries vs. raw Parquet | No benchmark has been run — see `docs/benchmarks.md` | **Unverified — removed from README**, framework exists in `benchmarks/` to produce a real number |
| Cold-start reduced from ~8 min to ~15 sec | No before/after benchmark exists in this repo's history | **Unverified — removed from README**, methodology documented in `docs/benchmarks.md` |
| CI validates Terraform, dbt, SQL lint, DAG imports, Python tests, Docker Compose config, YAML, GitHub Actions syntax on every PR | `.github/workflows/ci.yml` — 8 jobs, all confirmed to run against this repo's actual files (dbt parse, sqlfluff, pytest, terraform validate, compose config, yamllint, actionlint, DAG structure check) | **Verified** (workflow contents reviewed; GitHub Actions execution itself not observed from this environment — no way to trigger a real Actions run here) |
| dbt build + dbt test + gold validation run against real Trino/Iceberg | `.github/workflows/e2e.yml`, executed manually via `gh workflow run` on 2026-08-06 — [run 31067450380](https://github.com/nhatminh06/market-pulse/actions/runs/31067450380), took ~2.5 minutes end-to-end, concluded `success` | **Verified** — real run: loaded 6 fixture rows into bronze, `dbt build` completed 21/21 model+test checks as PASS (0 errors), the Python quality gate reported 4/4 PASS (including the not-empty-table check), and the representative Trino query returned real computed `daily_return`/`sma_20`/`is_volume_anomaly` values (first-row-per-ticker nulls behaving as documented) |
| Dashboard has 6 Superset panels | README's existing dashboard table (Total Volume Anomalies, Cumulative Return, Price vs MA, Volatility Heatmap, Avg Daily Return by Sector, Volume Anomalies table) | **Unverified** — no Superset dashboard JSON/assets are checked into the repo to confirm these panels exist as built; treat as a design intent until screenshotted (see `docs/evidence-checklist.md`) |
| "One-command" / "$0 cloud cost" operation | Quick Start requires `docker compose build`, `docker compose up -d`, `terraform apply`, then a manual backfill command — multiple commands, not one | **Reworded in README** to describe the actual multi-step Quick Start; "no paid cloud infrastructure required" is accurate (everything runs on local MinIO/Postgres/Trino) and kept, reworded to not imply electricity/internet/cloud migration are free |
| MIT license | `LICENSE` (root) | **Verified** — file added, README updated to point at it directly |

## How to regenerate the verified counts

```bash
# Python tests
pytest tests/ --collect-only -q | tail -1

# dbt tests + model/test coverage
cd dbt/market_pulse && python3 - <<'PY'
import yaml, glob
models = glob.glob("models/*/schema.yml")
total_tests = 0
tested_models = set()
all_models = set()
for f in models:
    d = yaml.safe_load(open(f))
    for m in d.get("models", []):
        all_models.add(m["name"])
        n = len(m.get("tests", [])) + sum(len(c.get("tests", [])) for c in m.get("columns", []))
        total_tests += n
        if n:
            tested_models.add(m["name"])
print("dbt tests:", total_tests)
print("models with >=1 test:", len(tested_models), "/", len(all_models))
PY

# Row counts (requires the stack running with data ingested)
docker compose exec trino trino --execute "select count(*) from iceberg.bronze.prices"
```
