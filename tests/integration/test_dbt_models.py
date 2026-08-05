"""Real dbt-model correctness (silver/gold SQL against live Trino/Iceberg)
is intentionally NOT tested here. Trino-specific window-function and
Iceberg-specific SQL cannot be faithfully run against a lightweight
in-process substitute (see docs/testing.md), so this repository's approach
is:
  - dbt parse + SQLFluff in pull-request CI (static checks, no live warehouse)
  - dbt build + dbt test against a fixture ticker/date range in the manual/
    scheduled E2E workflow (.github/workflows/e2e.yml), which starts the
    real docker-compose stack
  - the pure-formula logic (returns, SMA, volatility, anomaly detection) is
    covered as pandas spec tests in tests/unit/test_metric_calculations.py

This file exists as the documented placeholder for that decision, and is
skipped by default. Run it manually with `RUN_DBT_INTEGRATION=1 pytest
tests/integration/test_dbt_models.py` against a running local stack once
`dbt build --profiles-dir dbt/market_pulse` has populated gold.*.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_DBT_INTEGRATION"),
    reason="requires a live Trino/Iceberg stack; see module docstring",
)


def test_gold_grain_is_ticker_and_trade_date():
    from trino.dbapi import connect

    conn = connect(host="trino", port=8080, user="admin", catalog="iceberg")
    cur = conn.cursor()
    cur.execute(
        "select ticker, trade_date, count(*) from gold.fct_daily_metrics "
        "group by 1, 2 having count(*) > 1"
    )
    dupes = cur.fetchall()
    assert dupes == [], f"duplicate (ticker, trade_date) rows in gold: {dupes[:5]}"
