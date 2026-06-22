import os
import json
import logging
import urllib.request
from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator


def alert_on_failure(context):
    """Posts a formatted failure message to Slack. Never raises — a broken
    alerter must not mask or compound the original task failure."""
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    ti = context.get("task_instance")
    dag_id = ti.dag_id if ti else context.get("dag").dag_id
    task_id = ti.task_id if ti else "unknown"
    exec_ts = context.get("ts", "unknown")
    log_url = getattr(ti, "log_url", "n/a")
    try_no = getattr(ti, "try_number", "?")

    text = (
        f":red_circle: *Airflow task failed*\n"
        f"*DAG:* `{dag_id}`\n"
        f"*Task:* `{task_id}`  (attempt {try_no})\n"
        f"*When:* {exec_ts}\n"
        f"*Logs:* {log_url}"
    )

    if not webhook:
        logging.warning("SLACK_WEBHOOK_URL not set; skipping Slack alert. Message was:\n%s", text)
        return

    try:
        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            webhook, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                logging.error("Slack returned HTTP %s", resp.status)
    except Exception as exc:  # noqa: BLE001 — alerter must swallow everything
        logging.error("Failed to send Slack alert: %s", exc)


default_args = {
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
    "on_failure_callback": alert_on_failure,
}

with DAG(
    dag_id="market_pulse",
    start_date=datetime(2026, 1, 1),
    schedule="@daily",
    catchup=False,
    default_args=default_args,
    dagrun_timeout=timedelta(minutes=30),
    tags=["lakehouse", "market-pulse"],
) as dag:

    ingest = BashOperator(
        task_id="ingest_bronze",
        bash_command="python /opt/airflow/ingestion/ingest_bronze.py --days 5",
    )

    transform = BashOperator(
        task_id="dbt_build",
        bash_command=(
            "cd /opt/airflow/dbt/market_pulse && "
            "dbt deps && dbt build --profiles-dir ."
        ),
    )

    quality = BashOperator(
        task_id="validate_gold",
        bash_command="python /opt/airflow/quality/validate_gold.py",
    )

    ingest >> transform >> quality