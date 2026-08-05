"""Tests quality.validate_gold's check-interpretation logic against a stubbed
DB-API cursor — no live Trino connection required. This is "integration" in
the sense that it exercises the full run_checks() query-iteration loop, not
because it touches real infrastructure."""
from quality.validate_gold import run_checks


class FakeCursor:
    def __init__(self, results: dict[str, int]):
        self._results = results
        self._last_query = None

    def execute(self, query):
        self._last_query = query

    def fetchone(self):
        return (self._results[self._last_query],)


def test_all_checks_pass():
    checks = {"no nulls": "select 1", "sane returns": "select 2"}
    cursor = FakeCursor({"select 1": 0, "select 2": 0})
    assert run_checks(cursor, checks, min_rows_checks=set()) == []


def test_single_failed_check_is_reported_by_name():
    checks = {"no nulls": "select 1", "sane returns": "select 2"}
    cursor = FakeCursor({"select 1": 0, "select 2": 5})
    assert run_checks(cursor, checks, min_rows_checks=set()) == ["sane returns"]


def test_multiple_failed_checks_are_all_reported():
    checks = {"a": "select a", "b": "select b", "c": "select c"}
    cursor = FakeCursor({"select a": 1, "select b": 0, "select c": 2})
    assert run_checks(cursor, checks, min_rows_checks=set()) == ["a", "c"]


def test_empty_gold_table_fails_the_min_rows_check():
    checks = {"gold table is not empty": "select count(*) from gold.fct_daily_metrics"}
    cursor = FakeCursor({"select count(*) from gold.fct_daily_metrics": 0})
    failed = run_checks(cursor, checks, min_rows_checks={"gold table is not empty"})
    assert failed == ["gold table is not empty"]


def test_non_empty_gold_table_passes_the_min_rows_check():
    checks = {"gold table is not empty": "select count(*) from gold.fct_daily_metrics"}
    cursor = FakeCursor({"select count(*) from gold.fct_daily_metrics": 59800})
    failed = run_checks(cursor, checks, min_rows_checks={"gold table is not empty"})
    assert failed == []


def test_default_checks_and_polarity_are_wired_correctly():
    """Uses the module's real CHECKS/MIN_ROWS_CHECKS to catch drift between
    the two dicts (e.g. a check added to CHECKS but forgotten in
    MIN_ROWS_CHECKS, which would silently flip its pass/fail polarity)."""
    from quality.validate_gold import CHECKS, MIN_ROWS_CHECKS

    assert MIN_ROWS_CHECKS.issubset(CHECKS.keys())
    # A gold table with zero rows should fail exactly the min-rows check(s)
    # and vacuously pass every count-of-bad-rows check.
    cursor = FakeCursor({query: 0 for query in CHECKS.values()})
    failed = run_checks(cursor)
    assert set(failed) == MIN_ROWS_CHECKS
