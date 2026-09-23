from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
import uuid
from pathlib import Path

from sqlalchemy import event

from app import create_app
from config import Config
from database import create_engine
from models import Base
from performance.seed_data import seed


CSRF_PATTERN = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


class EmptyStorage:
    @staticmethod
    def list_form_files():
        return []


def benchmark_dataset(count: int, work_dir: Path, repetitions: int) -> list[dict]:
    database_path = work_dir / f"submissions-{count}-{uuid.uuid4().hex}.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    context_path = work_dir / f"context-{count}.json"
    seed(database_url, count, context_path)
    context = json.loads(context_path.read_text(encoding="utf-8"))

    class BenchmarkConfig(Config):
        DATABASE_URL = database_url
        TESTING = True
        ENV = "test"
        DEBUG = False
        AUTO_CREATE_DB_SCHEMA = True
        AUTO_DB_MIGRATE = False
        SECRET_KEY = "synthetic-performance-benchmark"
        TEMP_DIR = work_dir / f"temp-{count}"
        LOG_LEVEL = "WARNING"
        LOG_FORMAT = "text"

    app = create_app(config_object=BenchmarkConfig, storage_override=EmptyStorage())
    client = app.test_client()
    login_page = client.get("/admin/")
    csrf = CSRF_PATTERN.search(login_page.get_data(as_text=True)).group(1)
    login = client.post(
        "/admin/",
        data={"email": context["admin_email"], "password": context["admin_password"], "csrf_token": csrf},
    )
    if login.status_code >= 400:
        raise RuntimeError("Synthetic benchmark admin login failed.")

    scenarios = {
        "all": "/admin/submissions?page=1&per_page=50",
        "mine": "/admin/submissions?queue=mine&page=1&per_page=50",
        "unassigned": "/admin/submissions?queue=unassigned&page=1&per_page=50",
        "overdue": "/admin/submissions?queue=overdue&page=1&per_page=50",
        "filtered": "/admin/submissions?status=FORM_SUBMITTED&page=1&per_page=25",
        "pagination": "/admin/submissions?page=2&per_page=25",
    }
    results = []
    for scenario, path in scenarios.items():
        durations = []
        query_counts = []
        failures = 0
        for _ in range(repetitions):
            queries = 0

            def count_query(*_args, **_kwargs):
                nonlocal queries
                queries += 1

            event.listen(engine, "before_cursor_execute", count_query)
            started = time.perf_counter()
            response = client.get(path)
            durations.append((time.perf_counter() - started) * 1000)
            event.remove(engine, "before_cursor_execute", count_query)
            query_counts.append(queries)
            if response.status_code != 200 or not response.headers.get("X-Request-ID"):
                failures += 1
        results.append(
            {
                "dataset_size": count,
                "scenario": scenario,
                "requests": repetitions,
                "failures": failures,
                "p50_ms": round(percentile(durations, 0.50), 3),
                "p95_ms": round(percentile(durations, 0.95), 3),
                "p99_ms": round(percentile(durations, 0.99), 3),
                "sql_queries_min": min(query_counts),
                "sql_queries_max": max(query_counts),
            }
        )
    engine.dispose()
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="100,1000")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--work-dir", type=Path, default=Path("tmp/p3-performance"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for count in [int(item) for item in args.sizes.split(",")]:
        results.extend(benchmark_dataset(count, args.work_dir, args.repetitions))
    report = {
        "tool": "Flask test client + SQLAlchemy query counter",
        "commit_sha": os.getenv("GITHUB_SHA"),
        "repetitions": args.repetitions,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 1 if any(item["failures"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
