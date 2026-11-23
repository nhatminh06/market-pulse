"""Deterministic transaction tests; the real catalog is exercised by e2e.yml."""
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pytest
from pyiceberg.exceptions import (
    CommitFailedException,
    NoSuchTableError,
    TableAlreadyExistsError,
)
from pyiceberg.expressions import And, EqualTo, In, Or

from ingestion.ingest_bronze import (
    CommitOutcomeUnknownError,
    _replacement_filter,
    determine_incremental_start,
    write_bronze_table,
)


def matches(row, expr):
    if isinstance(expr, And):
        return matches(row, expr.left) and matches(row, expr.right)
    if isinstance(expr, Or):
        return matches(row, expr.left) or matches(row, expr.right)
    if isinstance(expr, EqualTo):
        return str(row[expr.term.name]) == str(expr.literal.value)
    if isinstance(expr, In):
        return str(row[expr.term.name]) in {str(lit.value) for lit in expr.literals}
    raise AssertionError(type(expr))


class FakeScan:
    def __init__(self, table, predicate):
        self.table = table
        self.predicate = predicate

    def to_arrow(self):
        rows = [row for row in self.table.rows.to_pylist() if matches(row, self.predicate)]
        return pa.Table.from_pylist(rows, schema=self.table.rows.schema)


class FakeTable:
    def __init__(self, schema):
        self.rows = pa.Table.from_batches([], schema=schema)
        self.version = 0
        self.failure = None
        self.commit_count = 0
        self.metadata = SimpleNamespace(snapshots=[])

    def transaction(self):
        return FakeTransaction(self)

    def scan(self, row_filter):
        return FakeScan(self, row_filter)


class FakeTransaction:
    def __init__(self, table):
        self.table = table
        self.base_version = table.version
        self.staged = None

    def overwrite(self, arrow, overwrite_filter, snapshot_properties=None):
        if self.table.failure == "prepare":
            raise RuntimeError("failed before staging")
        retained = [row for row in self.table.rows.to_pylist() if not matches(row, overwrite_filter)]
        if self.table.failure == "staging":
            raise RuntimeError("failed after staging delete")
        self.staged = pa.Table.from_pylist(retained + arrow.to_pylist(), schema=self.table.rows.schema)
        self.properties = snapshot_properties or {}

    def commit_transaction(self):
        if self.base_version != self.table.version or self.table.failure == "conflict":
            raise CommitFailedException("stale snapshot")
        if self.table.failure == "ambiguous_before":
            raise TimeoutError("catalog acknowledgement lost before commit")
        self.table.rows = self.staged
        self.table.version += 1
        self.table.commit_count += 1
        self.table.metadata.snapshots.append(
            SimpleNamespace(summary=SimpleNamespace(model_dump=lambda: self.properties))
        )
        if self.table.failure == "ambiguous_after":
            raise TimeoutError("catalog acknowledgement lost after commit")


class FakeCatalog:
    def __init__(self):
        self.tables = {}
        self.create_failure = None

    def create_namespace_if_not_exists(self, namespace):
        pass

    def load_table(self, name):
        if name not in self.tables:
            raise NoSuchTableError(name)
        return self.tables[name]

    def create_table(self, name, schema):
        if self.create_failure == "error":
            raise RuntimeError("create failed")
        if name in self.tables:
            raise TableAlreadyExistsError(name)
        self.tables[name] = FakeTable(schema)
        return self.tables[name]


@pytest.fixture
def bronze_df(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "valid_ohlcv.csv", parse_dates=["date"])
    df["date"] = df["date"].dt.date
    df["_ingested_at"] = datetime(2024, 1, 5, tzinfo=UTC)
    return df


def rows(cat):
    return {(r["ticker"], r["date"]): r for r in cat.tables["bronze.prices"].rows.to_pylist()}


def test_incremental_start():
    assert determine_incremental_start("2015-01-01", None) == "2015-01-01"
    assert determine_incremental_start("2015-01-01", 5, pd.Timestamp("2024-01-10", tz="UTC")) == "2024-01-05"


def test_initial_creation_and_identical_retry(bronze_df):
    cat = FakeCatalog()
    assert write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01") == 6
    before = rows(cat)
    assert write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01") == 6
    assert rows(cat) == before
    assert cat.tables["bronze.prices"].commit_count == 2


def test_identical_retry_after_process_restart(bronze_df):
    original_process = FakeCatalog()
    write_bronze_table(original_process, bronze_df, ["AAPL", "MSFT"], "2024-01-01")
    restarted_process = FakeCatalog()
    restarted_process.tables = original_process.tables
    assert write_bronze_table(restarted_process, bronze_df, ["AAPL", "MSFT"], "2024-01-01") == 6
    assert len(rows(restarted_process)) == 6


def test_exact_key_correction_preserves_missing_ticker_date_and_outside_scope(bronze_df):
    cat = FakeCatalog()
    write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01")
    before = rows(cat)
    correction = bronze_df.iloc[[0]].copy()
    correction["close"] = 101.5
    write_bronze_table(cat, correction, ["AAPL", "MSFT"], "2024-01-01")
    after = rows(cat)
    assert len(after) == 6
    assert after[("AAPL", date(2024, 1, 2))]["close"] == 101.5
    for key in before.keys() - {("AAPL", date(2024, 1, 2))}:
        assert after[key] == before[key]


def test_missing_historical_date_and_overlapping_backfill(bronze_df):
    cat = FakeCatalog()
    write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01")
    batch = bronze_df[bronze_df["date"].isin([date(2024, 1, 2), date(2024, 1, 4)])].copy()
    batch.loc[batch["ticker"] == "AAPL", "close"] = 101.5
    write_bronze_table(cat, batch, ["AAPL", "MSFT"], "2024-01-01")
    assert len(rows(cat)) == 6
    assert ("AAPL", date(2024, 1, 3)) in rows(cat)


@pytest.mark.parametrize("failure", ["prepare", "staging", "conflict", "ambiguous_before"])
def test_failures_do_not_publish_partial_replacement(bronze_df, failure):
    cat = FakeCatalog()
    write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01")
    table = cat.tables["bronze.prices"]
    before = rows(cat)
    table.failure = failure
    expected = CommitFailedException if failure == "conflict" else (
        CommitOutcomeUnknownError if failure == "ambiguous_before" else RuntimeError
    )
    with pytest.raises(expected):
        write_bronze_table(cat, bronze_df.iloc[[0]], ["AAPL"], "2024-01-01")
    assert rows(cat) == before


def test_ambiguous_after_commit_reconciles_before_return(bronze_df):
    cat = FakeCatalog()
    write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01")
    table = cat.tables["bronze.prices"]
    table.failure = "ambiguous_after"
    assert write_bronze_table(cat, bronze_df.iloc[[0]], ["AAPL"], "2024-01-01") == 1
    assert len(rows(cat)) == 6


def test_failed_creation_is_safe_to_retry(bronze_df):
    cat = FakeCatalog()
    cat.create_failure = "error"
    with pytest.raises(RuntimeError, match="create failed"):
        write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01")
    assert cat.tables == {}
    cat.create_failure = None
    assert write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01") == 6


def test_two_concurrent_transactions_detect_conflict(bronze_df):
    cat = FakeCatalog()
    write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01")
    table = cat.tables["bronze.prices"]
    first, second = table.transaction(), table.transaction()
    arrow = pa.Table.from_pandas(bronze_df.iloc[[0]], preserve_index=False)
    predicate = _replacement_filter(bronze_df.iloc[[0]])
    first.overwrite(arrow, predicate)
    second.overwrite(arrow, predicate)
    first.commit_transaction()
    with pytest.raises(CommitFailedException):
        second.commit_transaction()
    assert len(rows(cat)) == 6


def test_invalid_empty_and_out_of_scope_batches_never_write(bronze_df):
    cat = FakeCatalog()
    assert write_bronze_table(cat, bronze_df.iloc[0:0], ["AAPL"], "2024-01-01") == 0
    invalid = bronze_df.copy()
    invalid.loc[0, "close"] = -1
    with pytest.raises(ValueError):
        write_bronze_table(cat, invalid, ["AAPL", "MSFT"], "2024-01-01")
    with pytest.raises(ValueError):
        write_bronze_table(cat, bronze_df, ["AAPL"], "2024-01-01")
    with pytest.raises(ValueError):
        write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-03")
    assert cat.tables == {}


def test_exact_key_filter_excludes_missing_keys(bronze_df):
    sparse = bronze_df[(bronze_df["ticker"] == "AAPL") & (bronze_df["date"] != date(2024, 1, 3))]
    predicate = _replacement_filter(sparse)
    assert matches({"ticker": "AAPL", "date": date(2024, 1, 2)}, predicate)
    assert not matches({"ticker": "AAPL", "date": date(2024, 1, 3)}, predicate)
    assert not matches({"ticker": "MSFT", "date": date(2024, 1, 2)}, predicate)


def test_exact_key_filter_does_not_add_cartesian_keys(bronze_df):
    sparse = bronze_df[
        ((bronze_df["ticker"] == "AAPL") & (bronze_df["date"] == date(2024, 1, 2)))
        | ((bronze_df["ticker"] == "MSFT") & (bronze_df["date"] == date(2024, 1, 3)))
    ]
    predicate = _replacement_filter(sparse)
    assert matches({"ticker": "AAPL", "date": date(2024, 1, 2)}, predicate)
    assert matches({"ticker": "MSFT", "date": date(2024, 1, 3)}, predicate)
    assert not matches({"ticker": "AAPL", "date": date(2024, 1, 3)}, predicate)
    assert not matches({"ticker": "MSFT", "date": date(2024, 1, 2)}, predicate)


def test_oversized_filter_fails_before_storage_mutation(bronze_df, monkeypatch):
    import ingestion.ingest_bronze as ingester

    monkeypatch.setattr(ingester, "MAX_FILTER_LITERALS", 2)
    cat = FakeCatalog()
    with pytest.raises(ValueError, match="Exact-key filter"):
        write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01")
    assert cat.tables == {}


def test_shared_historical_dates_use_one_grouped_predicate():
    days = pd.bdate_range("2020-01-01", periods=1000).date
    historical = pd.DataFrame(
        [(ticker, day) for ticker in ("AAPL", "MSFT") for day in days],
        columns=["ticker", "date"],
    )
    predicate = _replacement_filter(historical)
    assert isinstance(predicate, And)
    assert matches({"ticker": "AAPL", "date": days[500]}, predicate)
    assert not matches({"ticker": "TSLA", "date": days[500]}, predicate)
