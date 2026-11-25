# Bronze lineage inspection and operator recovery (M6.1)

## Scope and existing guarantees

This is **Bronze ingestion-attempt inspection**, not verified Bronze → Silver →
Gold lineage. `write_bronze_table` generates `uuid.uuid4().hex` for each nonempty
validated attempt after its exact-key predicate passes the 10,000-literal guard.
The logged `ingestion_run_id` becomes `market_pulse_run_id` in snapshot summaries.
It is 32 lowercase hexadecimal characters, not an Airflow DAG-run ID. Empty
valid batches produce no new marker or commit. Repeated invocations get new IDs.

PyIceberg 0.7.1 stages exact returned-key overwrite changes and explicitly
commits once. One metadata commit may publish several snapshots with the same
marker; they are not separate application attempts. Missing tickers/dates are
preserved, not deleted. Known commit conflicts propagate. For other commit
exceptions the ingester reloads Bronze, checks for its marker across retained
snapshots, and compares current exact-key rows with its prepared batch. Only
both checks allow acknowledgement recovery. Otherwise the outcome stays unknown.
Airflow ingestion has `retries=0`; dbt and Gold validation retain their existing
retry settings. None of these behaviors is changed by M6.1.

## Read-only CLI

Within an **already running**, configured development environment:

```sh
python -m ingestion.inspect_lineage --run-id 0123456789abcdef0123456789abcdef --json
python -m ingestion.inspect_lineage --run-id 0123456789abcdef0123456789abcdef
# Optional container form; do not start services merely to run this command:
docker compose exec airflow-scheduler python -m ingestion.inspect_lineage \
  --run-id 0123456789abcdef0123456789abcdef --json
```

Use the real attempt ID from ingestion logs. The CLI reuses the existing catalog
configuration and credential environment; it accepts no endpoint or table override.
It only loads `bronze.prices` metadata, not row data or manifests. Catalog access
needs to be reachable from the execution environment; the existing REST hostname
is a Compose service name. No credentials, connection exceptions, table location,
manifest paths or arbitrary summary strings are printed. Output includes matching
snapshot IDs, parent IDs, integer Unix timestamps in milliseconds, operation,
allowlisted numeric commit-summary properties, and omitted-property counts.
Omission is deliberate: custom properties may contain sensitive data. This is not
a complete raw metadata export. JSON keys and match ordering are deterministic
for the same metadata state; no observation clock is injected.

| Field/value | Meaning |
| --- | --- |
| `evidence=MARKER_FOUND` | A matching marker exists in inspected retained metadata. |
| `evidence=MARKER_NOT_FOUND` | No match exists in inspected retained metadata. |
| `snapshot_relation=CURRENT_SNAPSHOT_MATCH` | The current snapshot directly carries the marker. |
| `snapshot_relation=HISTORICAL_MARKER` | Matches exist only in earlier retained snapshots. |
| `evidence=UNKNOWN`, `snapshot_relation=UNKNOWN` | Catalog or metadata could not be reliably inspected. |
| `snapshot_relation=null` | Reliable inspection found no marker. |

Exit codes: **0** reliable inspection (including not found or an empty history),
**1** lookup/metadata failure, **2** invalid CLI input/run ID. Malformed, absent
summaries or inconsistent current-snapshot references make the result UNKNOWN;
partial matches are not presented as a reliable classification. A valid summary
without a run marker is allowed because other writers may omit it. An expired
parent need not be retained. No snapshots with a null current ID is a valid
empty-table history. Even on error, any reported current ID is an observation,
not proof that the rest of the metadata was readable.

Marker presence does not prove that rows remain unchanged, that an attempted
batch was complete, that dbt succeeded, or that an end-to-end run passed. Absence
does not prove a commit never happened: metadata may have expired or the wrong
catalog/table may be under investigation. A metadata read is not a lock or a
promise that a subsequent writer has not changed the table. The tool performs
no rollback, overwrite, expiration, orphan cleanup, or retry.

## Operator decision guide

First prevent additional operator-triggered writes while investigating. Record
Airflow DAG/task/attempt IDs separately from ingestion IDs, coverage logs,
prepared input if retained, current snapshot and this report. Preserve evidence
in private incident storage; row exports and task logs can contain sensitive
information. Do not attach credentials or raw connection errors to reports.

| Incident | Known / unknown | Read-only investigation | Retry and manual intervention |
| --- | --- | --- | --- |
| Known Iceberg commit conflict | This transaction was rejected; other writers may have committed. | Inspect fresh snapshot history, competing attempt markers and current affected keys. | No automatic ingestion retry. Operator may authorize a fresh fetch/plan after writer coordination; never replay a stale transaction. |
| Lost acknowledgement after successful write | Success is known only if the existing ingester confirmed its marker **and** prepared current rows. A marker alone is weaker evidence. | Inspect the marker report, original reconciliation log, exact affected-key rows and saved prepared batch. | Do not replay a confirmed successful batch. If confirmation is absent, reconcile manually before a new attempt. |
| Unknown outcome, readback unavailable | Neither success nor failure is established. | Restore read-only catalog reachability, inspect all retained marker matches and compare current rows with the original prepared batch. | Automatic retry is unsafe. Keep unresolved until evidence supports an explicit operator decision; missing original input limits reconciliation. |
| Partial ticker/date coverage | Only valid returned keys were replaced; missing keys retained their previous rows. Completeness is unknown. | Inspect requested/returned ticker logs, per-ticker spans, current affected rows and upstream coverage. | No silent retry or deletion. Operator decides whether missing coverage warrants a fresh scoped fetch; this creates a new attempt. |
| Empty valid batch | No new commit/marker; absence alone cannot establish why the source was empty. | Inspect validation/drop counts, requested tickers/dates and provider logs. | Do not treat zero rows as a completeness guarantee. Investigate upstream/invalid input before deciding on a new invocation. |
| Airflow ingestion failure | Task failure may precede commit, follow a rejected commit, or follow an uncertain commit. | Correlate the task log to the ingestion marker and error class; inspect current metadata and rows. | Preserve `retries=0`. Resolve commit uncertainty before clearing/re-running ingestion. DAG serialization does not prevent external writers. |
| dbt failure after Bronze success | Bronze may be committed; Silver/Gold publication can be incomplete or older. Consumed Bronze revision is not recorded. | Inspect Bronze marker and current rows, dbt run results/logs and read-only downstream queries. | Existing dbt retries remain configured, but do not imply snapshot-pinned replay. Operator checks intervening Bronze writes before authorizing downstream rerun; do not re-ingest merely to repair dbt. |
| Gold validation failure | At least one quality assertion failed; a Bronze marker does not diagnose the cause. | Inspect the four check outputs, dbt results and affected Gold/Silver/Bronze rows. | Existing validation retries are read-only; they do not repair data. Investigate before upstream reruns or any approved correction. |
| Missing/expired metadata | The inspected retained history cannot demonstrate the old attempt; prior commits may still have happened. | Check retention policy, refs, private incident evidence and verified backups without changing them. | No inference of failure and no blind replay. Operator determines whether independent evidence or approved recovery is possible. Do not expire snapshots or delete files. |

For controlled row inspection and lifecycle limitations, see
[Iceberg operations](iceberg-operations.md). The inspector does not compare
prepared batches, recover expired data, prove general concurrent-write safety,
or provide exactly-once execution. Current rows can be corrected after a marker
was committed, even when its snapshot is retained.

## Qualification and next milestone

Local tests exercise fake read-only catalogs, sanitized failures, deterministic
reports, CLI exit codes, and a disposable SQLite Iceberg catalog with PyIceberg
0.7.1. The local-catalog integration verifies current/historical markers and a
two-snapshot overwrite, plus unchanged table metadata location and rows after
inspection. It does **not** qualify the REST catalog, MinIO, Trino, Airflow runtime,
remote Linux, or full E2E. No persistent development data is modified.

M6.2 should first design a durable downstream execution record: ingestion attempt
ID, exact Bronze snapshot consumed, dbt invocation/results and resulting Silver/
Gold revisions, with explicit failure/unknown states. Recording a snapshot before
dbt starts is insufficient unless reads are pinned to that revision or writer
coordination proves it stayed stable. Review that design and disposable integration
checks before implementation. M6.1 adds no such downstream association.

### Local validation record

Prepared from verified local/remote `main`
`da2c1e36790026d7efc90b5be8aa20a317fbf87a` on
`feature/v2-lineage-recovery`. This record describes local qualification;
publication as a draft does not establish live-stack qualification.
The full available suite returned **130 passed, 2 skipped, 8 warnings,
3 subtests passed** with Python 3.12 and PyIceberg 0.7.1. This includes
26 new unit cases and one new disposable local-catalog integration test.
Skips were the live Trino/dbt test and opt-in Docker ownership test. Warnings
were dependency deprecations and existing no-match delete notices.
Ruff, actionlint, both reference Compose configuration checks, Python syntax,
documentation links and diff whitespace checks passed. No services or remote
jobs were started. M5.1 remains separately checkpointed and runtime-blocked.

Reproduce with the existing CI Python dependencies installed:

```sh
python -m pytest tests/unit/test_lineage_audit.py tests/integration/test_lineage_catalog.py -q
python -m pytest tests/ -q -rs
ruff check ingestion quality observability tests
```
