# Iceberg lifecycle and recovery (V2-M2)

## Scope and evidence

The inventory command reads only catalog metadata and manifest files for four
allowlisted pipeline tables. It cannot expire snapshots, delete objects, or
change table metadata. Local disposable SQL-catalog tests verify inventory,
time travel, recovery by a new commit, and schema evolution with PyIceberg
0.7.1. REST/MinIO/Trino qualification is prepared in `e2e.yml` but has not
run for this branch. The current Compose file uses `trinodb/trino:latest`;
the prior E2E log shows that tag was pulled but does not record a version or
digest. Record `SELECT node_version FROM system.runtime.nodes LIMIT 1` in the
new E2E before assigning version-specific Trino capabilities to that run.

## Inspect a table

With the development stack running and its disposable development credentials
available in the Airflow container:

```bash
docker compose exec airflow-scheduler python -m ingestion.inspect_iceberg bronze.prices
docker compose exec airflow-scheduler python -m ingestion.inspect_iceberg bronze.prices --json
docker compose exec trino trino --execute 'SELECT * FROM iceberg.bronze."prices$snapshots"'
```

The report includes table location, current snapshot, snapshot IDs, parents,
times, operations, summary properties, manifest-list paths, per-snapshot file
counts and bytes where readable, schema and partition IDs, retention-related
properties, and snapshot references. A missing table or manifest read error
must be investigated; no object listing is interpreted as an orphan list.
Find an ingestion attempt by matching the logged `ingestion_run_id` to the
`market_pulse_run_id` snapshot summary field. A filtered overwrite can publish
multiple snapshots under one catalog commit; inspect all summaries for the ID.

## Recovery investigation

1. Stop new writes to the affected table and record the current snapshot ID,
   attempt ID, catalog location, and current rows for the affected keys.
2. Compare the current snapshot history and attempt ID with the Airflow log.
   For an uncertain commit acknowledgement, check both the attempt ID and
   current rows. If either cannot be verified, keep the run unresolved.
3. Read a prior snapshot without changing the table:

   ```sql
   SELECT ticker, date, close
   FROM iceberg.bronze.prices FOR VERSION AS OF <snapshot_id>
   WHERE ticker = 'AAPL' AND date = DATE '2024-01-02';
   ```

4. Confirm that the prior snapshot exists, its files remain readable, and
   the proposed recovery rows have the intended keys and values. Save the
   inventory report and query output before any write.
5. Test recovery on an isolated table first. PyIceberg 0.7.1 has no native
   rollback method. The local test reads a prior snapshot and restores the
   chosen key with a new filtered-overwrite commit. This preserves a new audit
   point and requires deliberate key scoping. Do not use it as an automatic
   production rollback recipe.

The qualified Trino version may support `rollback_to_snapshot`; this remains
unverified for this repository. No workflow calls it. A missing or expired
snapshot cannot be recovered by time travel alone; restore from a verified
external backup only after identifying the affected files and metadata.

## Retention and file lifecycle proposal

For local development, **propose** retaining at least 30 days of history and
at least 10 snapshots per table, with a named tag for any active recovery
point. These are planning thresholds, not applied configuration. Keep older
snapshots when an open incident, backfill, or rollback window needs them.
References such as tags and branches can protect snapshots; inspect all refs
before considering expiration. Snapshot expiration removes old metadata and
data files that no retained snapshot references. It can end time-travel and
recovery access. Orphan cleanup considers objects not referenced by table
metadata, including files staged by an M1 transaction that has not committed.
Coordinate with every writer and wait beyond the maximum write/commit duration
before considering orphan cleanup.

Neither PyIceberg 0.7.1 nor the currently identified Trino image provides a
verified, trustworthy file-deletion preview in this environment. **Dry-run
deletion enumeration is unsupported.** This tool produces an inventory and
policy proposal only. Do not infer candidate deletions from a partial S3
listing. Before any future destructive maintenance, establish an exact engine
version, verify catalog/object-store consistency, enumerate protected refs,
quiesce writers, preserve backup and recovery evidence, and test the command
on a disposable table. If catalog metadata and object storage disagree, stop
and investigate; do not clean up to make counts match. No expiration or orphan
cleanup is scheduled or executed in M2.

## Schema evolution

The local disposable test verifies adding a nullable column, reading old rows
as null, appending new rows, widening `int` to `long`, rejecting an incompatible
`long` to `string` change, and preserving existing field IDs. The new E2E step
will check REST/Trino visibility. No Bronze, Silver, or Gold schema changes are
made. Before a real Bronze change, review `stg_prices.sql` and dependent dbt
models and tests, then qualify the migration against the real stack. A new
Bronze column does not automatically become part of Silver or Gold.

## Reproduction and limits

```bash
python -m pytest tests/integration/test_iceberg_lifecycle_catalog.py -q
# Once this branch is pushed and approved for a remote run:
gh workflow run e2e.yml --ref feature/v2-iceberg-lifecycle
```

The added E2E step creates a uniquely named `lifecycle_m2_*` namespace and
table, checks snapshot metadata, time travel, recovery, and schema evolution,
and purges that table in `finally`. It uses fixture rows and the existing
disposable E2E stack. The existing Bronze fixture and dbt checks remain in
place. The workflow does not run snapshot expiration or orphan deletion.
