from repositories.submission_repository import CsvSubmissionRepository


class FakeStorage:
    def __init__(self):
        self.rows = {}

    def append_csv_row(self, slug, row):
        self.rows.setdefault(slug, []).append(dict(row))

    def read_csv_rows(self, slug):
        return list(self.rows.get(slug, []))

    def update_csv_row_by_submission_id(self, slug, submission_id, updates):
        for row in self.rows.get(slug, []):
            if row["submission_id"] == submission_id:
                row.update(updates)
                return True
        return False


def test_csv_submission_repository_crud():
    storage = FakeStorage()
    repository = CsvSubmissionRepository(storage)

    repository.create({"submission_id": "abc", "form_slug": "test", "email": "a@example.com"})

    assert repository.get_by_id("abc")["email"] == "a@example.com"
    assert repository.update("abc", {"email": "b@example.com"})
    assert repository.list_by_form("test")[0]["email"] == "b@example.com"
