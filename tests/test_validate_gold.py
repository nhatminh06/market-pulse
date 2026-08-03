from quality.validate_gold import run_checks


class FakeCursor:
    def __init__(self, results):
        self._results = results
        self._last_query = None

    def execute(self, query):
        self._last_query = query

    def fetchone(self):
        return (self._results[self._last_query],)


def test_run_checks_all_pass():
    checks = {"no nulls": "select 1", "sane returns": "select 2"}
    cursor = FakeCursor({"select 1": 0, "select 2": 0})

    assert run_checks(cursor, checks) == []


def test_run_checks_reports_failed_check_names():
    checks = {"no nulls": "select 1", "sane returns": "select 2"}
    cursor = FakeCursor({"select 1": 0, "select 2": 5})

    assert run_checks(cursor, checks) == ["sane returns"]


def test_run_checks_can_fail_multiple():
    checks = {"a": "select a", "b": "select b", "c": "select c"}
    cursor = FakeCursor({"select a": 1, "select b": 0, "select c": 2})

    assert run_checks(cursor, checks) == ["a", "c"]
