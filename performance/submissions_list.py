from locust import task

from performance.common import AuthenticatedAdminUser


class SubmissionsListUser(AuthenticatedAdminUser):
    @task(3)
    def all_paginated(self):
        self.get_checked("/admin/submissions?page=1&per_page=50", name="submissions_all_paginated")

    @task
    def mine(self):
        self.get_checked("/admin/submissions?queue=mine&page=1&per_page=50", name="submissions_mine")

    @task
    def unassigned(self):
        self.get_checked("/admin/submissions?queue=unassigned&page=1&per_page=50", name="submissions_unassigned")

    @task
    def overdue(self):
        self.get_checked("/admin/submissions?queue=overdue&page=1&per_page=50", name="submissions_overdue")

    @task
    def filtered(self):
        self.get_checked("/admin/submissions?status=FORM_SUBMITTED&page=2&per_page=25", name="submissions_filtered")
