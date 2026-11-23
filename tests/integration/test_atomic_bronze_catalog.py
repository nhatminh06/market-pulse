"""Exercise PyIceberg 0.7.1 transactions with a real local Iceberg catalog."""
from datetime import UTC, date, datetime

import pandas as pd
import pyarrow as pa
import pytest
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import CommitFailedException
from pyiceberg.table import Transaction

from ingestion.ingest_bronze import _replacement_filter, write_bronze_table


@pytest.fixture
def catalog(tmp_path):
    return load_catalog(
        "local", type="sql", uri=f"sqlite:///{tmp_path}/catalog.db", warehouse=f"file://{tmp_path}/warehouse"
    )


@pytest.fixture
def batch(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "valid_ohlcv.csv", parse_dates=["date"])
    df["date"] = df["date"].dt.date
    df["_ingested_at"] = datetime(2024, 1, 5, tzinfo=UTC)
    return df


def current_rows(catalog):
    return {(r["ticker"], r["date"]): r for r in catalog.load_table("bronze.prices").scan().to_arrow().to_pylist()}


def test_real_atomic_exact_key_replacement_and_conflict(catalog, batch):
    assert write_bronze_table(catalog, batch, ["AAPL", "MSFT"], "2024-01-01") == 6
    before = current_rows(catalog)
    table = catalog.load_table("bronze.prices")
    correction = batch.iloc[[0]].copy()
    correction["close"] = 101.5
    arrow = pa.Table.from_pandas(correction, preserve_index=False)
    predicate = _replacement_filter(correction)

    # Staging can write orphan files, but cannot publish a partial delete.
    abandoned = table.transaction()
    abandoned.overwrite(arrow, overwrite_filter=predicate)
    assert current_rows(catalog) == before

    first = catalog.load_table("bronze.prices").transaction()
    second = catalog.load_table("bronze.prices").transaction()
    first.overwrite(arrow, overwrite_filter=predicate)
    second.overwrite(arrow, overwrite_filter=predicate)
    first.commit_transaction()
    with pytest.raises(CommitFailedException):
        second.commit_transaction()

    after = current_rows(catalog)
    assert len(after) == 6
    assert after[("AAPL", date(2024, 1, 2))]["close"] == 101.5
    for key in before.keys() - {("AAPL", date(2024, 1, 2))}:
        assert after[key] == before[key]
    assert catalog.load_table("bronze.prices").current_snapshot().summary.model_dump()["operation"] == "append"


def test_real_catalog_reconciles_lost_commit_acknowledgement(catalog, batch, monkeypatch):
    write_bronze_table(catalog, batch, ["AAPL", "MSFT"], "2024-01-01")
    original = Transaction.commit_transaction

    def commit_then_timeout(self):
        original(self)
        raise TimeoutError("lost acknowledgement")

    monkeypatch.setattr(Transaction, "commit_transaction", commit_then_timeout)
    correction = batch.iloc[[0]].copy()
    correction["close"] = 101.5
    assert write_bronze_table(catalog, correction, ["AAPL"], "2024-01-01") == 1
    assert current_rows(catalog)[("AAPL", date(2024, 1, 2))]["close"] == 101.5
    snapshots = catalog.load_table("bronze.prices").metadata.snapshots
    attempt_ids = [snapshot.summary.model_dump().get("market_pulse_run_id") for snapshot in snapshots]
    assert attempt_ids[-1] == attempt_ids[-2] != attempt_ids[0]
