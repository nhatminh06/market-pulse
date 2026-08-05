.DEFAULT_GOAL := help
.PHONY: help env lint test terraform-validate dbt-parse dbt-test compose-validate \
        fixture-pipeline up down observability-up observability-down benchmark clean-generated

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

env: ## Copy .env.example to .env if .env doesn't already exist
	@test -f .env || cp .env.example .env
	@echo "Edit .env before running 'make up' — see .env.example for what each variable does."

lint: ## Run ruff (Python) and SQLFluff (dbt SQL) — no live services required
	ruff check ingestion quality tests benchmarks
	cd dbt/market_pulse && sqlfluff lint models --dialect trino

test: ## Run the pytest suite — no live services or network required
	pytest tests/ -v

terraform-validate: ## terraform fmt/init/validate against infra/terraform (no backend, no apply)
	terraform -chdir=infra/terraform fmt -check
	terraform -chdir=infra/terraform init -backend=false
	terraform -chdir=infra/terraform validate

dbt-parse: ## Static dbt project/YAML/Jinja validation — no Trino connection required
	cd dbt/market_pulse && dbt deps && dbt parse --profiles-dir .

dbt-test: ## Run dbt build + dbt test against a REAL running stack (requires `make up` first)
	docker compose exec airflow-scheduler bash -c \
		"cd /opt/airflow/dbt/market_pulse && dbt deps --profiles-dir . && dbt build --profiles-dir ."

compose-validate: env ## Validate docker-compose YAML/interpolation without starting anything
	docker compose -f docker-compose.yaml config --quiet
	docker compose -f docker-compose.observability.yml config --quiet

up: env ## Build and start the full local stack
	docker compose build
	docker compose up -d

down: ## Stop the stack, preserving volumes
	docker compose stop

observability-up: ## Start Prometheus + Grafana (requires the main stack already running)
	docker compose -f docker-compose.observability.yml up -d

observability-down: ## Stop Prometheus + Grafana
	docker compose -f docker-compose.observability.yml stop

fixture-pipeline: ## Load the test fixture into bronze and run dbt build (requires `make up` first, no live yfinance call)
	docker compose exec -T airflow-scheduler python /opt/airflow/ingestion/ingest_bronze.py --start 2024-01-02 --tickers AAPL,MSFT
	$(MAKE) dbt-test
	docker compose exec airflow-scheduler python /opt/airflow/quality/validate_gold.py

benchmark: ## Run the Iceberg-vs-raw-Parquet benchmark (requires `make up` first; see benchmarks/README.md)
	python benchmarks/run_query_benchmark.py --engine trino --query benchmarks/queries/iceberg.sql \
		--warmup 2 --runs 10 --out benchmarks/results/iceberg.json
	@echo "Raw-Parquet side needs a local Parquet export first — see benchmarks/README.md"

clean-generated: ## Remove generated/cache files (NOT docker volumes — use `docker compose down -v` for that, deliberately not wired here)
	rm -rf .pytest_cache .ruff_cache dbt/market_pulse/target dbt/market_pulse/logs
	find . -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
