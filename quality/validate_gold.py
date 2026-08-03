import sys

CHECKS = {
    "no null tickers in gold":
        "select count(*) from gold.fct_daily_metrics where ticker is null",
    "returns within sane bounds":
        "select count(*) from gold.fct_daily_metrics where abs(daily_return) > 0.9",
    "volatility non-negative":
        "select count(*) from gold.fct_daily_metrics where volatility_30d_ann < 0",
}


def run_checks(cursor, checks=CHECKS) -> list[str]:
    """Run each quality query via the given DB-API cursor, printing PASS/FAIL per
    check. Returns the list of failed check names (empty if all passed)."""
    failed = []
    for name, query in checks.items():
        cursor.execute(query)
        bad = cursor.fetchone()[0]
        status = "PASS" if bad == 0 else f"FAIL ({bad} rows)"
        print(f"[{status}] {name}")
        if bad != 0:
            failed.append(name)
    return failed


if __name__ == "__main__":
    from trino.dbapi import connect

    conn = connect(host="trino", port=8080, user="admin", catalog="iceberg")
    cur = conn.cursor()

    failed_checks = run_checks(cur)
    if failed_checks:
        print("Quality gate FAILED:", failed_checks)
        sys.exit(1)
    print("All quality checks passed.")
