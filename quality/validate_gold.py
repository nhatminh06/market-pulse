import sys
from trino.dbapi import connect

conn = connect(host="trino", port=8080, user="admin", catalog="iceberg")
cur = conn.cursor()

checks = {
    "no null tickers in gold":
        "select count(*) from gold.fct_daily_metrics where ticker is null",
    "returns within sane bounds":
        "select count(*) from gold.fct_daily_metrics where abs(daily_return) > 0.9",
    "volatility non-negative":
        "select count(*) from gold.fct_daily_metrics where volatility_30d_ann < 0",
}

failed = []
for name, q in checks.items():
    cur.execute(q)
    bad = cur.fetchone()[0]
    status = "PASS" if bad == 0 else f"FAIL ({bad} rows)"
    print(f"[{status}] {name}")
    if bad != 0:
        failed.append(name)

if failed:
    print("Quality gate FAILED:", failed)
    sys.exit(1)
print("All quality checks passed.")