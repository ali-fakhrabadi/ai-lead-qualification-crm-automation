"""Summarise the resource samples taken during the benchmark (mean and peak per container).

python evals/summarize_resources.py resources.csv resources.json [--model-host host.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

UNITS = {
    "b": 1 / 2**20,
    "kib": 1 / 1024,
    "mib": 1,
    "gib": 1024,
    "kb": 1 / 1000,
    "mb": 1,
    "gb": 1000,
}


def mib(value: str) -> float:
    match = re.match(r"([\d.]+)\s*([a-zA-Z]+)", value.strip())
    if not match:
        return 0.0
    return float(match.group(1)) * UNITS.get(match.group(2).lower(), 1)


def summarise(path: Path) -> dict[str, Any]:
    cpu: dict[str, list[float]] = defaultdict(list)
    mem: dict[str, list[float]] = defaultdict(list)
    stamps = set()
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = row["container"].replace("lead-automation-", "").rsplit("-", 1)[0]
            cpu[name].append(float(row["cpu_percent"].rstrip("%") or 0))
            mem[name].append(mib(row["mem_used"]))
            stamps.add(row["ts"])
    return {
        "samples": len(stamps),
        "interval_s": 15,
        "containers": {
            name: {
                "cpu_percent_mean": round(mean(cpu[name]), 1),
                "cpu_percent_peak": round(max(cpu[name]), 1),
                "mem_mib_mean": round(mean(mem[name]), 1),
                "mem_mib_peak": round(max(mem[name]), 1),
            }
            for name in sorted(cpu)
        },
        "definition": "docker stats every 15 s on the application host; 100 % = one CPU.",
    }


def summarise_host(path: Path) -> dict[str, Any]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    col = lambda k: [float(r[k]) for r in rows]  # noqa: E731
    return {
        "samples": len(rows),
        "logical_cpus": int(rows[0]["logical_cpus"]) if rows else None,
        "host_cpu_percent_mean": round(mean(col("host_cpu_percent")), 1) if rows else None,
        "host_cpu_percent_peak": round(max(col("host_cpu_percent")), 1) if rows else None,
        "ollama_cpu_percent_mean": round(mean(col("ollama_cpu_percent")), 1) if rows else None,
        "ollama_cpu_percent_peak": round(max(col("ollama_cpu_percent")), 1) if rows else None,
        "ollama_rss_mib_peak": round(max(col("ollama_rss_mib")), 1) if rows else None,
        "definition": "Model host, every 15 s: whole-host CPU (100 % = all CPUs) and the "
        "Ollama processes' CPU (100 % = one CPU) and resident memory.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--model-host", type=Path)
    args = parser.parse_args()
    result: dict[str, Any] = {"app_host": summarise(args.csv)}
    if args.model_host and args.model_host.exists():
        result["model_host"] = summarise_host(args.model_host)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
