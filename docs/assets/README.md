# docs/assets/

Screenshot evidence goes here, organized by subsystem. **Empty today** — no
screenshots have been captured in this environment (no Docker daemon
available to bring the stack up). See `docs/evidence-checklist.md` for
exactly what to capture and where each file should go; the root README
links to that checklist instead of showing broken image placeholders.

```
architecture/   -- architecture diagram source/exports
airflow/        -- DAG graph, successful run, task durations
dbt/            -- lineage graph, dbt build output, test summary
superset/       -- dashboard panels
trino/          -- query + query plan
iceberg/        -- table schema, snapshots, partitions
demo/           -- full walkthrough screenshots, matching docs/demo-script.md
```

When you add a real screenshot, reference it from the README with a
concise caption — do not add an `<img>`/`![]()` tag pointing at a file that
doesn't exist yet.
