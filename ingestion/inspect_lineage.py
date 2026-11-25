"""Inspect retained Bronze run markers without reading rows or changing Iceberg."""

import argparse
import json
import re

from ingestion.ingest_bronze import get_catalog

RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
# Arbitrary summary properties may contain credentials or paths. Export only typed fields.
COUNTS = ("added-data-files", "deleted-data-files", "added-records", "deleted-records",
          "total-records", "total-data-files", "total-files-size")
OPERATIONS = {"append", "replace", "overwrite", "delete"}


def validate_run_id(run_id):
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("Run ID must be 32 lowercase hexadecimal characters")


def inspect_run(catalog, run_id):
    """One fresh table load; classifications describe metadata evidence, never row state."""
    validate_run_id(run_id)
    report = {"table": "bronze.prices", "run_id": run_id, "evidence": "UNKNOWN",
              "snapshot_relation": "UNKNOWN", "current_snapshot_id": None,
              "inspected_snapshots": 0, "matches": [], "error": None}
    try:
        metadata = catalog.load_table("bronze.prices").metadata
        snapshots = list(metadata.snapshots)
        current = metadata.current_snapshot_id
        ids = [s.snapshot_id for s in snapshots]
        if (any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids)
                or (current is not None and (type(current) is not int or current not in ids))):
            raise ValueError
        report.update(current_snapshot_id=current, inspected_snapshots=len(snapshots))
        matches = []
        for snapshot in snapshots:
            if (type(snapshot.timestamp_ms) is not int or snapshot.timestamp_ms < 0
                    or (snapshot.parent_snapshot_id is not None and
                        (type(snapshot.parent_snapshot_id) is not int or snapshot.parent_snapshot_id <= 0))):
                raise ValueError
            if snapshot.summary is None:
                raise ValueError
            summary = snapshot.summary.model_dump(mode="json")
            if not isinstance(summary, dict) or summary.get("operation") not in OPERATIONS:
                raise ValueError
            marker = summary.get("market_pulse_run_id")
            if marker is not None:
                validate_run_id(marker)
            safe = {"operation": summary["operation"]}
            if marker is not None:
                safe["market_pulse_run_id"] = marker
            for key in COUNTS:
                if key in summary:
                    value = summary[key]
                    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}", value):
                        raise ValueError
                    safe[key] = value
            if marker == run_id:
                matches.append({"snapshot_id": snapshot.snapshot_id,
                                "timestamp_ms": snapshot.timestamp_ms,
                                "parent_snapshot_id": snapshot.parent_snapshot_id,
                                "operation": summary["operation"], "summary": safe,
                                "omitted_summary_fields": len(set(summary) - set(safe))})
        report["matches"] = sorted(matches, key=lambda item: (item["timestamp_ms"], item["snapshot_id"]))
        report["evidence"] = "MARKER_FOUND" if matches else "MARKER_NOT_FOUND"
        report["snapshot_relation"] = (
            "CURRENT_SNAPSHOT_MATCH" if any(s["snapshot_id"] == current for s in matches)
            else "HISTORICAL_MARKER" if matches else None)
    except Exception:  # noqa: BLE001 — sanitize every catalog/metadata failure at this output boundary.
        # Never serialize upstream exception text: it can contain connection credentials.
        report.update(evidence="UNKNOWN", snapshot_relation="UNKNOWN", matches=[],
                      error="Catalog lookup or retained snapshot metadata could not be verified")
    return report


def render_text(report):
    lines = [f"{report['table']}: {report['evidence']} ({report['snapshot_relation']})",
             f"Current snapshot: {report['current_snapshot_id']}"]
    for item in report["matches"]:
        lines.append(f"Snapshot {item['snapshot_id']} timestamp_ms={item['timestamp_ms']} "
                     f"parent={item['parent_snapshot_id']} operation={item['operation']}")
    if report["error"]:
        lines.append(report["error"])
    lines.append("Markers establish retained Bronze metadata evidence only; rows and downstream lineage are unverified.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        validate_run_id(args.run_id)
    except ValueError:
        print(json.dumps({"evidence": "UNKNOWN", "error": "Invalid run ID"}, sort_keys=True))
        return 2
    try:
        catalog = get_catalog()
    except Exception:  # noqa: BLE001 — catalog construction errors must not leak credentials.
        # Use the same sanitized report for catalog construction and table lookup failures.
        catalog = None
    report = inspect_run(catalog, args.run_id)
    print(json.dumps(report, sort_keys=True, indent=2) if args.json else render_text(report))
    return 1 if report["evidence"] == "UNKNOWN" else 0


if __name__ == "__main__":
    raise SystemExit(main())
