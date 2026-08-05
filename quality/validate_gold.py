"""Gold-layer data-quality gate. Runs a fixed set of read-only SQL checks
against gold.fct_daily_metrics via Trino and exits non-zero if any fail.

Query construction, execution, and CLI exit behavior are kept separate so
run_checks() can be unit-tested against a stubbed cursor without a live
Trino connection (see tests/integration/test_gold_validation.py). All check
queries are static strings with no user-controlled identifiers, so there is
no SQL-injection surface here — do not parameterize table/column names from
external input without revisiting this.

An empty gold.fct_daily_metrics table is NOT treated as a passing run: the
row-count check below fails the gate explicitly instead of every other
check vacuously passing over zero rows.
"""
import sys

CHECKS = {
    "gold table is not empty":
        "select count(*) from gold.fct_daily_metrics",
    "no null tickers in gold":
        "select count(*) from gold.fct_daily_metrics where ticker is null",
    "returns within sane bounds":
        "select count(*) from gold.fct_daily_metrics where abs(daily_return) > 0.9",
    "volatility non-negative":
        "select count(*) from gold.fct_daily_metrics where volatility_30d_ann < 0",
}

# The row-count check above is a MINIMUM-rows check (bad = 0 means "empty",
# which is a failure), inverted relative to every other check (bad = 0 means
# "pass"). Listed here so run_checks() can apply the right polarity.
MIN_ROWS_CHECKS = {"gold table is not empty"}


def run_checks(cursor, checks=CHECKS, min_rows_checks=MIN_ROWS_CHECKS) -> list[str]:
    """Run each quality query via the given DB-API cursor, printing PASS/FAIL
    per check. Returns the list of failed check names (empty if all passed)."""
    failed = []
    for name, query in checks.items():
        cursor.execute(query)
        result = cursor.fetchone()[0]
        if name in min_rows_checks:
            ok = result > 0
        else:
            ok = result == 0
        status = "PASS" if ok else f"FAIL ({result} rows)"
        print(f"[{status}] {name}")
        if not ok:
            failed.append(name)
    return failed


def _connect():
    from trino.dbapi import connect
    return connect(host="trino", port=8080, user="admin", catalog="iceberg")


if __name__ == "__main__":
    conn = _connect()
    cur = conn.cursor()

    failed_checks = run_checks(cur)
    if failed_checks:
        print("Quality gate FAILED:", failed_checks)
        sys.exit(1)
    print("All quality checks passed.")
