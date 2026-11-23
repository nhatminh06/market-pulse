"""Read-only Iceberg inventory for the pipeline's known tables."""

import argparse
import json
import re
from datetime import UTC, datetime

from ingestion.ingest_bronze import get_catalog

ALLOWED_TABLES = ("bronze.prices", "silver.stg_prices", "gold.fct_daily_metrics", "gold.dim_ticker")
DISPOSABLE_TABLE = re.compile(r"lifecycle_m2_[0-9a-f]{32}\.prices\Z")


def inventory(catalog, identifier: str, *, allow_disposable: bool = False) -> dict:
    if identifier not in ALLOWED_TABLES and not (allow_disposable and DISPOSABLE_TABLE.fullmatch(identifier)):
        raise ValueError(f"Table is not allowlisted: {identifier}")
    table = catalog.load_table(identifier)
    metadata = table.metadata
    snapshots = []
    for snapshot in metadata.snapshots:
        item = {
            "id": snapshot.snapshot_id,
            "parent_id": snapshot.parent_snapshot_id,
            "timestamp": datetime.fromtimestamp(snapshot.timestamp_ms / 1000, UTC).isoformat(),
            "operation": snapshot.summary.operation.value if snapshot.summary else None,
            "summary": snapshot.summary.model_dump() if snapshot.summary else {},
            "manifest_list": snapshot.manifest_list,
        }
        try:
            files = table.inspect.files(snapshot_id=snapshot.snapshot_id)
            item["data_files"] = len(files)
            item["data_bytes"] = sum(files.column("file_size_in_bytes").to_pylist())
        except (NotImplementedError, ValueError, KeyError):
            item["data_files"] = None
            item["data_bytes"] = None
        snapshots.append(item)
    return {
        "table": identifier,
        "location": metadata.location,
        "current_snapshot_id": metadata.current_snapshot_id,
        "snapshots": snapshots,
        "history": [entry.model_dump(mode="json", by_alias=True) for entry in metadata.snapshot_log],
        "schema_id": metadata.current_schema_id,
        "schema": table.schema().model_dump(mode="json", by_alias=True),
        "partition_spec": table.spec().model_dump(mode="json", by_alias=True),
        "properties": {key: value for key, value in metadata.properties.items() if
                       key.startswith(("history.", "write.metadata.", "gc."))},
        "refs": {name: ref.model_dump(mode="json", by_alias=True) for name, ref in metadata.refs.items()},
        "maintenance_plan": "Inspection only. Retain at least 30 days and 10 snapshots; coordinate writers before any cleanup. File-deletion preview unsupported.",
    }


def render_text(report: dict) -> str:
    lines = [f"{report['table']} at {report['location']}",
             f"Current snapshot: {report['current_snapshot_id']}  Schema: {report['schema_id']}",
             f"Fields: {', '.join(f['name'] + '#' + str(f['id']) for f in report['schema']['fields'])}",
             f"Partition spec: {json.dumps(report['partition_spec'], sort_keys=True)}",
             f"References: {json.dumps(report['refs'], sort_keys=True)}",
             f"Retention properties: {json.dumps(report['properties'], sort_keys=True)}"]
    for item in report["snapshots"]:
        lines.append(f"{item['id']} {item['timestamp']} parent={item['parent_id']} "
                     f"operation={item['operation']} files={item['data_files']} bytes={item['data_bytes']} "
                     f"manifest={item['manifest_list']} summary={json.dumps(item['summary'], sort_keys=True)}")
    lines.append(report["maintenance_plan"])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table", help="allowlisted pipeline table")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()
    report = inventory(get_catalog(), args.table)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))


if __name__ == "__main__":
    main()
