from __future__ import annotations

import json
import os
import re
from pathlib import Path

from locust import HttpUser, between
from locust.exception import StopUser


CSRF_PATTERN = re.compile(r'name="csrf_token"\s+value="([^"]+)"')


def load_context() -> dict:
    path = Path(os.getenv("PERF_CONTEXT", ".performance/context.json"))
    if not path.is_file():
        raise RuntimeError(f"Synthetic performance context not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


class AuthenticatedAdminUser(HttpUser):
    abstract = True
    wait_time = between(0.2, 1.0)

    def on_start(self):
        self.performance_context = load_context()
        response = self.client.get("/admin/", name="admin_login_form")
        match = CSRF_PATTERN.search(response.text)
        if not match:
            raise StopUser("Admin CSRF token unavailable")
        login = self.client.post(
            "/admin/",
            data={
                "email": self.performance_context["admin_email"],
                "password": self.performance_context["admin_password"],
                "csrf_token": match.group(1),
            },
            name="admin_login",
        )
        if login.status_code >= 400:
            raise StopUser("Synthetic admin login failed")

    def get_checked(self, path: str, *, name: str, expected=(200,)):
        with self.client.get(path, name=name, catch_response=True) as response:
            if response.status_code not in expected:
                response.failure(f"unexpected status {response.status_code}")
            elif not response.headers.get("X-Request-ID"):
                response.failure("missing X-Request-ID")
            return response
