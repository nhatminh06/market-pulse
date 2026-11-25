"""Bronze metadata evidence is not row-state or downstream lineage evidence."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ingestion import inspect_lineage as lineage

RUN = "a" * 32
OTHER = "b" * 32


def snapshot(identifier=1, marker=RUN, **kwargs):
    data = {"operation": "append", "total-records": "6", "private-path": "/private/secret"}
    if marker is not None:
        data["market_pulse_run_id"] = marker
    return SimpleNamespace(snapshot_id=identifier, timestamp_ms=1000 + identifier,
                           parent_snapshot_id=None, summary=SimpleNamespace(model_dump=lambda **_: data), **kwargs)


def catalog(snapshots, current=None):
    # No mutation/row-scan methods exist on these fakes.
    table = SimpleNamespace(metadata=SimpleNamespace(snapshots=snapshots, current_snapshot_id=current))
    return SimpleNamespace(load_table=Mock(return_value=table))


@pytest.mark.parametrize("value", [RUN, "0123456789abcdef" * 2])
def test_valid_ids(value):
    lineage.validate_run_id(value)


@pytest.mark.parametrize("value", ["", "A" * 32, "a" * 31, RUN + "\n", "../" + RUN, "attempt-1", None])
def test_invalid_ids(value):
    with pytest.raises(ValueError):
        lineage.validate_run_id(value)


def test_empty_history_and_unknown_run():
    assert lineage.inspect_run(catalog([]), RUN)["evidence"] == "MARKER_NOT_FOUND"
    report = lineage.inspect_run(catalog([snapshot(marker=OTHER)], 1), RUN)
    assert report["evidence"] == "MARKER_NOT_FOUND" and report["snapshot_relation"] is None


def test_current_match_read_only_and_safe_summary():
    cat = catalog([snapshot()], 1)
    report = lineage.inspect_run(cat, RUN)
    assert report["evidence"] == "MARKER_FOUND"
    assert report["snapshot_relation"] == "CURRENT_SNAPSHOT_MATCH"
    assert report["matches"][0]["summary"] == {
        "operation": "append", "total-records": "6", "market_pulse_run_id": RUN}
    assert report["matches"][0]["omitted_summary_fields"] == 1
    assert "/private/secret" not in json.dumps(report)
    cat.load_table.assert_called_once_with("bronze.prices")


def test_multiple_snapshots_and_deterministic_order():
    a = lineage.inspect_run(catalog([snapshot(2), snapshot(1)], 2), RUN)
    b = lineage.inspect_run(catalog([snapshot(1), snapshot(2)], 2), RUN)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert [s["snapshot_id"] for s in a["matches"]] == [1, 2]
    assert a["current_snapshot_id"] == 2
    assert a["matches"][0]["timestamp_ms"] == 1001
    assert "Snapshot 1" in lineage.render_text(a)


def test_historical_match():
    report = lineage.inspect_run(catalog([snapshot(), snapshot(2, OTHER)], 2), RUN)
    assert report["snapshot_relation"] == "HISTORICAL_MARKER"


@pytest.mark.parametrize("defect", ["missing", "operation", "marker", "count", "timestamp", "current", "duplicate"])
def test_malformed_metadata_is_unknown(defect):
    s = snapshot()
    current = 1
    if defect == "missing":
        s.summary = None
    elif defect in ("operation", "marker", "count"):
        key = {"operation": "operation", "marker": "market_pulse_run_id", "count": "total-records"}[defect]
        s.summary = SimpleNamespace(model_dump=lambda **_: {"operation": "append", key: "secret://password"})
    elif defect == "timestamp":
        s.timestamp_ms = "secret://password"
    elif defect == "current":
        current = "secret://password"
    cat = catalog([s, s] if defect == "duplicate" else [s], current)
    report = lineage.inspect_run(cat, RUN)
    assert report["evidence"] == report["snapshot_relation"] == "UNKNOWN"
    assert "secret://password" not in json.dumps(report)


def test_catalog_failure_is_sanitized():
    cat = SimpleNamespace(load_table=Mock(side_effect=RuntimeError("https://user:password@host/private")))
    report = lineage.inspect_run(cat, RUN)
    assert report["evidence"] == "UNKNOWN"
    assert "password" not in json.dumps(report)


@pytest.mark.parametrize("mode", [[], ["--json"]])
def test_cli_success_and_not_found(monkeypatch, capsys, mode):
    monkeypatch.setattr(lineage, "get_catalog", lambda: catalog([snapshot()], 1))
    assert lineage.main(["--run-id", RUN, *mode]) == 0
    assert "MARKER_FOUND" in capsys.readouterr().out
    assert lineage.main(["--run-id", OTHER, *mode]) == 0
    assert "MARKER_NOT_FOUND" in capsys.readouterr().out


def test_cli_invalid_and_lookup_exit_codes(monkeypatch, capsys):
    def fail():
        raise RuntimeError("password /private/path")
    monkeypatch.setattr(lineage, "get_catalog", fail)
    assert lineage.main(["--run-id", "bad", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "Invalid run ID"
    assert lineage.main(["--run-id", RUN, "--json"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["evidence"] == "UNKNOWN"
    assert "password" not in output and "/private/path" not in output


def test_unmarked_snapshot_and_expired_parent_are_valid():
    s = snapshot(marker=None)
    s.parent_snapshot_id = 42
    report = lineage.inspect_run(catalog([s], 1), RUN)
    assert report["evidence"] == "MARKER_NOT_FOUND"


def test_invalid_id_never_accesses_catalog():
    cat = catalog([])
    with pytest.raises(ValueError):
        lineage.inspect_run(cat, "bad")
    cat.load_table.assert_not_called()
