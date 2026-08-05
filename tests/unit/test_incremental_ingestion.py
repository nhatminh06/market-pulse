"""Tests for the incremental-window calculation and for bronze write
idempotency, using an in-memory fake catalog/table so no MinIO/Iceberg REST
stack is required.

Design note on idempotency: write_bronze_table deletes rows matching the
ticker/date window being written, then appends the freshly fetched batch —
i.e. delete-and-replace-partition, scoped to exactly what was just fetched.
It is idempotent for reruns covering the *same* ticker/date window (a retry,
or `--days N` re-fetching the last few days); it does not deduplicate against
unrelated prior windows on its own. The silver dbt model's merge on
(ticker, trade_date) is what guarantees no duplicates survive end-to-end.

The fake below evaluates the real pyiceberg boolean expression that
write_bronze_table builds (And/GreaterThanOrEqual/EqualTo/In over date and
ticker) so the delete-then-append scoping is faithfully exercised without a
live Iceberg REST catalog.
"""
from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
import pytest
from pyiceberg.exceptions import NoSuchTableError
from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, In

from ingestion.ingest_bronze import determine_incremental_start, write_bronze_table


def _row_matches(row: dict, expr) -> bool:
    if isinstance(expr, And):
        return _row_matches(row, expr.left) and _row_matches(row, expr.right)
    if isinstance(expr, GreaterThanOrEqual):
        # ISO date strings sort identically to date ordering, so compare as
        # strings to sidestep date-vs-str literal type mismatches.
        return str(row[expr.term.name]) >= str(expr.literal.value)
    if isinstance(expr, EqualTo):
        return row[expr.term.name] == expr.literal.value
    if isinstance(expr, In):
        return row[expr.term.name] in {lit.value for lit in expr.literals}
    raise NotImplementedError(f"FakeTable doesn't support {type(expr)}")


class FakeTable:
    def __init__(self, schema):
        self.schema = schema
        self.rows = pa.Table.from_batches([], schema=schema)
        self.delete_calls = 0
        self.append_calls = 0

    def delete(self, expr):
        self.delete_calls += 1
        if self.rows.num_rows == 0:
            return
        keep_mask = [not _row_matches(row, expr) for row in self.rows.to_pylist()]
        self.rows = self.rows.filter(pa.array(keep_mask))

    def append(self, arrow_table):
        self.append_calls += 1
        self.rows = pa.concat_tables([self.rows, arrow_table])


class FakeCatalog:
    def __init__(self):
        self.tables = {}

    def create_namespace_if_not_exists(self, ns):
        pass

    def load_table(self, name):
        if name not in self.tables:
            raise NoSuchTableError(name)
        return self.tables[name]

    def create_table(self, name, schema):
        table = FakeTable(schema)
        self.tables[name] = table
        return table


@pytest.fixture
def bronze_df(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "valid_ohlcv.csv", parse_dates=["date"])
    df["date"] = df["date"].dt.date
    df["_ingested_at"] = datetime.now(timezone.utc)
    return df


def test_determine_incremental_start_backfill_uses_default_start():
    assert determine_incremental_start("2015-01-01", None) == "2015-01-01"


def test_determine_incremental_start_days_offset_is_relative_to_now():
    now = pd.Timestamp("2024-01-10", tz="UTC")
    assert determine_incremental_start("2015-01-01", 5, now=now) == "2024-01-05"


def test_write_bronze_table_creates_table_on_empty_catalog(bronze_df):
    cat = FakeCatalog()
    n = write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01")

    assert n == len(bronze_df)
    table = cat.tables["bronze.prices"]
    assert table.rows.num_rows == len(bronze_df)
    assert table.delete_calls == 0  # table didn't exist yet, nothing to delete


def test_rerunning_the_same_window_does_not_duplicate_rows(bronze_df):
    cat = FakeCatalog()
    write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01")
    write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01")

    table = cat.tables["bronze.prices"]
    assert table.rows.num_rows == len(bronze_df)  # not 2x
    assert table.delete_calls == 1  # deleted once, on the second (rerun) call


def test_new_data_after_existing_max_date_is_additive(bronze_df):
    cat = FakeCatalog()
    write_bronze_table(cat, bronze_df, ["AAPL", "MSFT"], "2024-01-01")

    new_row = bronze_df.iloc[[0]].copy()
    new_row["date"] = pd.Timestamp("2024-01-05").date()
    write_bronze_table(cat, new_row, ["AAPL"], "2024-01-05")

    table = cat.tables["bronze.prices"]
    assert table.rows.num_rows == len(bronze_df) + 1
