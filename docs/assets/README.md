# docs/assets/

Screenshot evidence goes here, organized by subsystem. **No screenshots
are currently committed.** See `docs/evidence-checklist.md` for
exactly what to capture and where each file should go; the root README
links to that checklist instead of showing broken image placeholders.

```
architecture/   -- architecture diagram source/exports
airflow/        -- DAG graph, successful run, task durations
observability/ -- real Prometheus / Grafana render evidence (M3)
gitops/        -- disposable Flux, StatsD and MinIO pilot evidence (M4)
dbt/            -- lineage graph, dbt build output, test summary
superset/       -- dashboard panels
trino/          -- query + query plan
iceberg/        -- table schema, snapshots, partitions
demo/           -- full walkthrough screenshots, matching docs/demo-script.md
```

When you add a real screenshot, reference it from the README with a
concise caption — do not add an `<img>`/`![]()` tag pointing at a file that
doesn't exist yet.


GitHub Actions [M3](https://github.com/nhatminh06/market-pulse/actions/runs/45271006974)
and [M4](https://github.com/nhatminh06/market-pulse/actions/runs/45298404500)
results are already linked from the documentation as CI evidence. Do not add
screenshots of CI pages as a substitute for application UI screenshots. Never
commit invented panels or images from a different runtime.
