"""Lifecycle checks against disposable PyIceberg 0.7.1 SQL-catalog tables."""

from datetime import date

import pyarrow as pa
import pytest
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError, ValidationError
from pyiceberg.expressions import EqualTo
from pyiceberg.types import LongType, StringType

from ingestion.inspect_iceberg import inventory, render_text


@pytest.fixture
def catalog(tmp_path):
    cat = load_catalog("lifecycle", type="sql", uri=f"sqlite:///{tmp_path}/catalog.db",
                       warehouse=f"file://{tmp_path}/warehouse")
    cat.create_namespace("bronze")
    return cat


@pytest.fixture
def table(catalog):
    schema = pa.schema([("ticker", pa.string()), ("date", pa.date32()),
                        ("close", pa.float64()), ("volume", pa.int32())])
    return catalog.create_table("bronze.prices", schema=schema)


def rows(table, snapshot_id=None):
    return table.scan(snapshot_id=snapshot_id).to_arrow().to_pylist()


def test_inventory_is_read_only_and_reports_snapshots(catalog, table):
    assert inventory(catalog, "bronze.prices")["snapshots"] == []
    original = pa.table({"ticker": ["AAPL", "MSFT"], "date": [date(2024, 1, 2)] * 2,
                         "close": [100.0, 200.0], "volume": pa.array([1, 2], type=pa.int32())})
    table.append(original, snapshot_properties={"market_pulse_run_id": "attempt-1"})
    prior_id = catalog.load_table("bronze.prices").current_snapshot().snapshot_id
    catalog.load_table("bronze.prices").manage_snapshots().create_tag(prior_id, "recovery_point").commit()
    correction = original.slice(0, 1).set_column(2, "close", pa.array([101.5]))
    table = catalog.load_table("bronze.prices")
    table.overwrite(correction, overwrite_filter=EqualTo("ticker", "AAPL"),
                    snapshot_properties={"market_pulse_run_id": "attempt-2"})
    before = catalog.load_table("bronze.prices").metadata.current_snapshot_id
    report = inventory(catalog, "bronze.prices")
    assert report["current_snapshot_id"] == before
    assert any(s["id"] == prior_id and s["summary"].get("market_pulse_run_id") == "attempt-1"
               for s in report["snapshots"])
    assert report["refs"]["recovery_point"]["snapshot-id"] == prior_id
    assert all(s["manifest_list"] and s["data_files"] >= 1 for s in report["snapshots"])
    assert catalog.load_table("bronze.prices").metadata.current_snapshot_id == before
    assert len(rows(catalog.load_table("bronze.prices"))) == 2
    assert str(prior_id) in render_text(report)
    with pytest.raises(ValueError, match="allowlisted"):
        inventory(catalog, "other.table")
    with pytest.raises(ValueError, match="allowlisted"):
        inventory(catalog, "lifecycle_m2_" + "a" * 32 + ".prices")


def test_missing_table_and_recovery_from_prior_snapshot(catalog, table):
    with pytest.raises(NoSuchTableError):
        inventory(catalog, "silver.stg_prices")
    initial = pa.table({"ticker": ["AAPL"], "date": [date(2024, 1, 2)],
                        "close": [100.0], "volume": pa.array([1], type=pa.int32())})
    table.append(initial)
    old_id = catalog.load_table("bronze.prices").current_snapshot().snapshot_id
    changed = initial.set_column(2, "close", pa.array([101.5]))
    table = catalog.load_table("bronze.prices")
    table.overwrite(changed, overwrite_filter=EqualTo("ticker", "AAPL"))
    table = catalog.load_table("bronze.prices")
    assert rows(table)[0]["close"] == 101.5
    historical = rows(table, snapshot_id=old_id)
    assert len(historical) == 1 and historical[0]["close"] == 100.0
    # PyIceberg 0.7.1 has no rollback API; restore the known row as a new commit.
    table.overwrite(pa.Table.from_pylist(historical, schema=table.scan().to_arrow().schema),
                    overwrite_filter=EqualTo("ticker", "AAPL"))
    restored = catalog.load_table("bronze.prices")
    assert len(rows(restored)) == 1 and rows(restored)[0]["close"] == 100.0
    with pytest.raises(ValueError):
        rows(restored, snapshot_id=-1)


def test_disposable_inventory_requires_explicit_opt_in(catalog):
    namespace = "lifecycle_m2_" + "a" * 32
    identifier = f"{namespace}.prices"
    catalog.create_namespace(namespace)
    catalog.create_table(identifier, schema=pa.schema([("ticker", pa.string())]))
    with pytest.raises(ValueError, match="allowlisted"):
        inventory(catalog, identifier)
    assert inventory(catalog, identifier, allow_disposable=True)["table"] == identifier
    with pytest.raises(ValueError, match="allowlisted"):
        inventory(catalog, "lifecycle_m2_not_a_uuid.prices", allow_disposable=True)


def test_disposable_schema_evolution(catalog, table):
    table.append(pa.table({"ticker": ["AAPL"], "date": [date(2024, 1, 2)],
                           "close": [100.0], "volume": pa.array([1], type=pa.int32())}))
    old_id = catalog.load_table("bronze.prices").current_snapshot().snapshot_id
    old_schema = table.schema()
    with table.update_schema() as update:
        update.add_column("source_note", StringType())
    table = catalog.load_table("bronze.prices")
    added = table.schema()
    assert added.schema_id != old_schema.schema_id
    assert added.find_field("ticker").field_id == old_schema.find_field("ticker").field_id
    assert added.find_field("source_note").field_id > max(f.field_id for f in old_schema.fields)
    assert rows(table, old_id)[0]["ticker"] == "AAPL"
    assert rows(table)[0]["source_note"] is None
    table.append(pa.table({"ticker": ["MSFT"], "date": [date(2024, 1, 3)], "close": [200.0],
                           "volume": pa.array([2], type=pa.int32()), "source_note": ["fixture"]}))
    table = catalog.load_table("bronze.prices")
    assert len(rows(table)) == 2
    with table.update_schema() as update:
        update.update_column("volume", field_type=LongType())
    widened = catalog.load_table("bronze.prices")
    assert widened.schema().find_field("volume").field_id == old_schema.find_field("volume").field_id
    with pytest.raises(ValidationError):
        widened.update_schema().update_column("volume", field_type=StringType())
