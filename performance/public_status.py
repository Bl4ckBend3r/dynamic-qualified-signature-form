from locust import HttpUser, between, task

from performance.common import load_context


class PublicStatusUser(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self):
        self.performance_context = load_context()

    def _get_status(self, path, *, headers, name, expected):
        with self.client.get(path, headers=headers, name=name, catch_response=True) as response:
            if response.status_code != expected:
                response.failure(f"unexpected status {response.status_code}")
            elif not response.headers.get("X-Request-ID"):
                response.failure("missing X-Request-ID")
            else:
                response.success()

    @task(4)
    def valid_token(self):
        submission = self.performance_context["public_submissions"][0]
        self._get_status(
            f"/api/submissions/{submission['submission_id']}/acceptance-status",
            headers={"Authorization": f"Bearer {submission['token']}"},
            name="public_status_valid",
            expected=200,
        )

    @task
    def invalid_token(self):
        submission = self.performance_context["public_submissions"][0]
        self._get_status(
            f"/api/submissions/{submission['submission_id']}/acceptance-status",
            headers={"Authorization": "Bearer synthetic-invalid-token"},
            name="public_status_invalid_token",
            expected=404,
        )

    @task
    def token_for_other_submission(self):
        first, second = self.performance_context["public_submissions"][:2]
        self._get_status(
            f"/api/submissions/{first['submission_id']}/workflow-status",
            headers={"Authorization": f"Bearer {second['token']}"},
            name="public_status_cross_token",
            expected=404,
        )
