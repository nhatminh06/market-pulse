"""Lineage inspection on a disposable local SQL catalog, never the REST service."""
import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.expressions import EqualTo

from ingestion.inspect_lineage import inspect_run


def test_real_snapshot_summaries_and_inspection_preserve_state(tmp_path):
    cat = load_catalog("lineage", type="sql", uri=f"sqlite:///{tmp_path}/catalog.db",
                       warehouse=f"file://{tmp_path}/warehouse")
    cat.create_namespace("bronze")
    data = pa.table({"ticker": ["AAPL", "MSFT"], "close": [100.0, 200.0]})
    table = cat.create_table("bronze.prices", schema=data.schema)
    assert inspect_run(cat, "a" * 32)["evidence"] == "MARKER_NOT_FOUND"
    table.append(data, snapshot_properties={"market_pulse_run_id": "a" * 32})
    table = cat.load_table("bronze.prices")
    tx = table.transaction()
    tx.overwrite(pa.table({"ticker": ["AAPL"], "close": [101.0]}),
                 overwrite_filter=EqualTo("ticker", "AAPL"),
                 snapshot_properties={"market_pulse_run_id": "b" * 32})
    tx.commit_transaction()
    before = cat.load_table("bronze.prices")
    metadata_path = before.metadata_location
    rows = before.scan().to_arrow().to_pylist()
    report = inspect_run(cat, "b" * 32)
    assert report["snapshot_relation"] == "CURRENT_SNAPSHOT_MATCH"
    assert len(report["matches"]) == 2
    assert inspect_run(cat, "a" * 32)["snapshot_relation"] == "HISTORICAL_MARKER"
    after = cat.load_table("bronze.prices")
    assert after.metadata_location == metadata_path
    assert after.scan().to_arrow().to_pylist() == rows
