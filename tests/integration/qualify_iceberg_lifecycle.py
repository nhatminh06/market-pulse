"""Disposable REST/Trino lifecycle qualification; run only inside the E2E stack."""

import json
import sys
from datetime import date
from uuid import uuid4

import pyarrow as pa
import pyiceberg
from pyiceberg.exceptions import ValidationError
from pyiceberg.expressions import EqualTo
from pyiceberg.types import LongType, StringType
from trino.dbapi import connect

sys.path.insert(0, "/opt/airflow")
from ingestion.ingest_bronze import get_catalog
from ingestion.inspect_iceberg import inventory, render_text


def main() -> None:
    cat = get_catalog()
    bronze_before = cat.load_table("bronze.prices")
    bronze_snapshot = bronze_before.current_snapshot().snapshot_id
    bronze_rows = bronze_before.scan().to_arrow().to_pylist()
    namespace = f"lifecycle_m2_{uuid4().hex}"
    identifier = f"{namespace}.prices"
    conn = connect(host="trino", port=8080, user="admin", catalog="iceberg")
    cursor = conn.cursor()
    cursor.execute("SELECT version()")
    print(f"Trino version: {cursor.fetchone()[0]}")
    print(f"PyIceberg version: {pyiceberg.__version__}")
    cat.create_namespace(namespace)
    original_error = None
    try:
        schema = pa.schema([("ticker", pa.string()), ("date", pa.date32()),
                            ("close", pa.float64()), ("volume", pa.int32())])
        table = cat.create_table(identifier, schema=schema)
        print(f"Iceberg table format version: {table.metadata.format_version}")
        initial = pa.table({"ticker": ["AAPL", "MSFT"], "date": [date(2024, 1, 2)] * 2,
                            "close": [100.0, 200.0], "volume": pa.array([1, 2], type=pa.int32())})
        table.append(initial)
        first_id = cat.load_table(identifier).current_snapshot().snapshot_id
        correction = initial.slice(0, 1).set_column(2, "close", pa.array([101.5]))
        cat.load_table(identifier).overwrite(correction, overwrite_filter=EqualTo("ticker", "AAPL"))
        current = cat.load_table(identifier)
        assert len(current.scan().to_arrow()) == 2
        assert current.scan(row_filter=EqualTo("ticker", "AAPL")).to_arrow()["close"][0].as_py() == 101.5
        old = current.scan(snapshot_id=first_id).to_arrow().to_pylist()
        assert len(old) == 2 and next(r for r in old if r["ticker"] == "AAPL")["close"] == 100.0
        cursor.execute(f'SELECT close FROM iceberg.{namespace}.prices FOR VERSION AS OF {first_id} '
                       "WHERE ticker = 'AAPL'")
        assert float(cursor.fetchone()[0]) == 100.0
        cursor.execute(f'SELECT count(*) FROM iceberg.{namespace}."prices$snapshots"')
        assert cursor.fetchone()[0] >= 2
        report = inventory(cat, identifier, allow_disposable=True)
        assert report["current_snapshot_id"] == current.current_snapshot().snapshot_id
        assert any(snapshot["id"] == first_id for snapshot in report["snapshots"])
        assert report["location"].startswith("s3://warehouse/")
        encoded = json.dumps(report)
        assert json.loads(encoded)["table"] == identifier
        assert str(first_id) in render_text(report)
        print(f"Inventory JSON: {encoded}")
        print(f"Inventory text:\n{render_text(report)}")

        old_aapl = pa.Table.from_pylist([r for r in old if r["ticker"] == "AAPL"], schema=schema)
        current.overwrite(old_aapl, overwrite_filter=EqualTo("ticker", "AAPL"))
        recovered = cat.load_table(identifier)
        assert len(recovered.scan().to_arrow()) == 2
        cursor.execute(f"SELECT ticker, close FROM iceberg.{namespace}.prices ORDER BY ticker")
        assert [(r[0], float(r[1])) for r in cursor.fetchall()] == [("AAPL", 100.0), ("MSFT", 200.0)]

        old_schema = recovered.schema()
        with recovered.update_schema() as update:
            update.add_column("source_note", StringType())
        evolved = cat.load_table(identifier)
        assert evolved.schema().schema_id != old_schema.schema_id
        assert evolved.schema().find_field("ticker").field_id == old_schema.find_field("ticker").field_id
        assert evolved.schema().find_field("source_note").field_id > max(f.field_id for f in old_schema.fields)
        assert evolved.scan().to_arrow()["source_note"].null_count == 2
        assert len(evolved.scan(snapshot_id=first_id).to_arrow()) == 2
        evolved.append(pa.table({"ticker": ["GOOG"], "date": [date(2024, 1, 3)], "close": [300.0],
                                 "volume": pa.array([3], type=pa.int32()), "source_note": ["fixture"]}))
        with cat.load_table(identifier).update_schema() as update:
            update.update_column("volume", field_type=LongType())
        widened = cat.load_table(identifier)
        assert widened.schema().schema_id != evolved.schema().schema_id
        assert widened.schema().find_field("volume").field_id == old_schema.find_field("volume").field_id
        try:
            widened.update_schema().update_column("volume", field_type=StringType())
        except ValidationError:
            pass
        else:
            raise AssertionError("Incompatible schema change was accepted")
        cursor.execute(f"SELECT ticker, source_note FROM iceberg.{namespace}.prices ORDER BY ticker")
        assert cursor.fetchall() == [("AAPL", None), ("GOOG", "fixture"), ("MSFT", None)]
        cursor.execute("SELECT column_name, data_type FROM iceberg.information_schema.columns "
                       f"WHERE table_schema = '{namespace}' AND table_name = 'prices'")
        types = dict(cursor.fetchall())
        assert types["source_note"] == "varchar" and types["volume"] == "bigint", types
        print(f"Lifecycle REST/Trino qualification passed: {identifier}, prior={first_id}")
    except BaseException as exc:
        original_error = exc
        raise
    finally:
        cleanup_error = None
        for cleanup in (lambda: cat.purge_table(identifier) if cat.table_exists(identifier) else None,
                        lambda: cat.drop_namespace(namespace)):
            try:
                cleanup()
            except Exception as exc:  # noqa: BLE001 - attempt both cleanups without masking the test failure
                cleanup_error = cleanup_error or exc
                print(f"Disposable resource cleanup failed: {exc}", file=sys.stderr)
        if original_error is None and cleanup_error is not None:
            raise cleanup_error
        if original_error is None:
            assert not cat.table_exists(identifier)
            bronze_after = cat.load_table("bronze.prices")
            assert bronze_after.current_snapshot().snapshot_id == bronze_snapshot
            assert bronze_after.scan().to_arrow().to_pylist() == bronze_rows
            print(f"Disposable lifecycle table and namespace removed: {identifier}")
            print("Persistent Bronze snapshot and rows unchanged")


if __name__ == "__main__":
    main()
