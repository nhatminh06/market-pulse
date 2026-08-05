"""Summarize one or more run_query_benchmark.py JSON result files: count,
median (the headline number), min, max, mean, stdev, and p95 when there are
enough runs. With two result files, also reports the percentage difference
between their medians — this is how any "N% faster" claim should be
produced, not hand-picked from the fastest run.

Usage:
    python benchmarks/summarize_results.py benchmarks/results/iceberg.json
    python benchmarks/summarize_results.py \
        benchmarks/results/iceberg.json benchmarks/results/raw_parquet.json \
        --compare
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def summarize(timings: list[float]) -> dict:
    if not timings:
        return {"count": 0}
    out = {
        "count": len(timings),
        "median_seconds": statistics.median(timings),
        "min_seconds": min(timings),
        "max_seconds": max(timings),
        "mean_seconds": statistics.mean(timings),
    }
    if len(timings) >= 2:
        out["stdev_seconds"] = statistics.stdev(timings)
    if len(timings) >= 20:
        out["p95_seconds"] = statistics.quantiles(timings, n=100)[94]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--compare", action="store_true", help="report %% diff between the first two files' medians")
    args = ap.parse_args()

    summaries = []
    for f in args.files:
        data = json.loads(f.read_text())
        s = summarize(data.get("timings_seconds", []))
        s["file"] = str(f)
        s["engine"] = data.get("engine")
        s["label"] = data.get("label")
        if data.get("failures"):
            s["failures"] = len(data["failures"])
        summaries.append(s)
        print(json.dumps(s, indent=2))

    if args.compare and len(summaries) >= 2:
        a, b = summaries[0], summaries[1]
        if a.get("median_seconds") and b.get("median_seconds"):
            # Positive = `a` is faster than `b` by this percentage.
            pct = (b["median_seconds"] - a["median_seconds"]) / b["median_seconds"] * 100
            print(
                f"\n{a['file']} median vs {b['file']} median: "
                f"{pct:+.1f}% (positive = first file is faster)"
            )
        else:
            print("\nCannot compare: one or both files have no successful runs.")


if __name__ == "__main__":
    main()
