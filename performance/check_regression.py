from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def read_locust_stats(path: Path) -> dict[str, dict[str, float]]:
    result = {}
    with path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            name = row.get("Name")
            if not name or name == "Aggregated":
                continue
            result[name] = {
                "p95_ms": float(row.get("95%") or 0),
                "failure_count": float(row.get("Failure Count") or 0),
                "throughput": float(row.get("Requests/s") or 0),
            }
    return result


def compare(current: dict, baseline: dict, tolerance_percent: float) -> list[str]:
    failures = []
    multiplier = 1 + tolerance_percent / 100
    for scenario, expected in baseline.items():
        actual = current.get(scenario)
        if actual is None:
            failures.append(f"missing scenario: {scenario}")
            continue
        if actual["failure_count"] > float(expected.get("failure_count", 0)):
            failures.append(f"{scenario}: failures {actual['failure_count']} > {expected.get('failure_count', 0)}")
        expected_p95 = float(expected.get("p95_ms") or 0)
        if expected_p95 and actual["p95_ms"] > expected_p95 * multiplier:
            failures.append(f"{scenario}: p95 {actual['p95_ms']} ms exceeds baseline+tolerance")
        expected_throughput = float(expected.get("throughput") or 0)
        if expected_throughput and actual["throughput"] < expected_throughput / multiplier:
            failures.append(f"{scenario}: throughput {actual['throughput']} is below baseline tolerance")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare controlled Locust output with an owner-approved baseline.")
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--tolerance-percent", type=float, required=True)
    args = parser.parse_args()
    failures = compare(
        read_locust_stats(args.current),
        json.loads(args.baseline.read_text(encoding="utf-8")),
        args.tolerance_percent,
    )
    if failures:
        print("\n".join(failures))
        return 1
    print("Performance results are within the configured baseline tolerance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
