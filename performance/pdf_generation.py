from __future__ import annotations

import argparse
import base64
import json
import math
import os
import statistics
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from flask import Flask

from pdf_generator import generate_pdf_from_html


ONE_PIXEL_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
).decode("ascii")


SCENARIOS = {
    "simple": "<h1>Deklaracja</h1><p>{{ value }}</p>",
    "medium": "<h1>Umowa</h1>" + "".join(f"<p>Paragraf {index}: {{{{ value }}}}</p>" for index in range(30)),
    "table": "<h1>Lista</h1><table><tbody>" + "".join(f"<tr><td>{index}</td><td>{{{{ value }}}}</td></tr>" for index in range(80)) + "</tbody></table>",
    "logo": f'<img alt="synthetic logo" src="data:image/png;base64,{ONE_PIXEL_PNG}"><h1>Dokument z logo</h1><p>{{{{ value }}}}</p>',
}


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _one_generation(app: Flask, template: str, output_dir: Path, sequence: int) -> float:
    output = output_dir / f"result-{sequence}.pdf"
    started = time.perf_counter()
    generate_pdf_from_html(app, template, {"value": "synthetic value"}, output)
    duration = time.perf_counter() - started
    output.unlink(missing_ok=True)
    return duration


def benchmark(*, repetitions: int, concurrency_levels: list[int], work_dir: Path) -> dict:
    app = Flask(__name__, root_path=str(Path(__file__).resolve().parents[1]))
    results = []
    output_dir = work_dir / f"run-{os.getpid()}-{time.time_ns()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    tracemalloc.start()
    for name, template in SCENARIOS.items():
        for concurrency in concurrency_levels:
            durations = []
            failures = 0
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(_one_generation, app, template, output_dir, index)
                    for index in range(repetitions)
                ]
                for future in as_completed(futures):
                    try:
                        durations.append(future.result())
                    except Exception:
                        failures += 1
            elapsed = time.perf_counter() - started
            results.append(
                {
                    "scenario": name,
                    "concurrency": concurrency,
                    "requests": repetitions,
                    "failures": failures,
                    "p50_ms": round(percentile(durations, 0.50) * 1000, 3) if durations else None,
                    "p95_ms": round(percentile(durations, 0.95) * 1000, 3) if durations else None,
                    "p99_ms": round(percentile(durations, 0.99) * 1000, 3) if durations else None,
                    "throughput_per_second": round(len(durations) / elapsed, 3) if elapsed else 0,
                }
            )
    _, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"results": results, "peak_memory_bytes": peak_memory}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=4)
    parser.add_argument("--concurrency", default="1,5,10,20")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=Path("tmp/p3-pdf-performance"))
    args = parser.parse_args()
    levels = [int(item) for item in args.concurrency.split(",") if int(item) > 0]
    report = benchmark(repetitions=args.repetitions, concurrency_levels=levels, work_dir=args.work_dir)
    report.update({"commit_sha": os.getenv("GITHUB_SHA"), "tool": "Playwright production PDF pipeline"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 1 if any(item["failures"] for item in report["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
