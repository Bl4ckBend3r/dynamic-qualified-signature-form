from pathlib import Path
import json

import pytest


def test_nextcloud_list_children_keeps_only_immediate_entries(monkeypatch):
    from services.nextcloud_storage import NextcloudStorage

    storage = NextcloudStorage("https://cloud.example", "user", "secret")
    xml = """<d:multistatus xmlns:d="DAV:">
      <d:response><d:href>/remote.php/dav/files/user/output/form/submissions/id/attachments/</d:href></d:response>
      <d:response><d:href>/remote.php/dav/files/user/output/form/submissions/id/attachments/certificate/</d:href>
        <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
      </d:response>
      <d:response><d:href>/remote.php/dav/files/user/output/form/submissions/id/attachments/deeper/file.pdf</d:href></d:response>
    </d:multistatus>"""
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs["headers"]["Depth"]))
        return type("Response", (), {"status_code": 207, "text": xml})()

    monkeypatch.setattr(storage, "_request", request)
    children = storage.list_children("output/form/submissions/id/attachments")
    assert children == [("certificate", True)]
    assert calls == [("PROPFIND", "output/form/submissions/id/attachments", "1")]


def test_audit_log_requires_remote_storage(monkeypatch, tmp_path):
    from services.audit_log_service import AuditLogService

    class FailingAuditRepository:
        def append(self, entry):
            raise OSError("Nextcloud unavailable")

    monkeypatch.chdir(tmp_path)
    with pytest.raises(OSError, match="Nextcloud unavailable"):
        AuditLogService(repository=FailingAuditRepository()).log_event(
            "FORM_SUBMITTED", "submission-1", "example-form"
        )
    assert not (tmp_path / "storage" / "audit_log.jsonl").exists()


def test_audit_repository_writes_independent_event_files():
    from repositories.audit_log_repository import StorageAuditLogRepository
    from testing_support import InMemoryStorage

    storage = InMemoryStorage({})
    repository = StorageAuditLogRepository(storage)
    event_ids = (
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    )
    for event_id in event_ids:
        repository.append({"event_id": event_id, "form_slug": "example-form", "event_type": "TEST"})

    for event_id in event_ids:
        path = f"output/example-form/audit/events/{event_id}.jsonl"
        assert json.loads(storage.direct_files[path]) == {
            "event_id": event_id,
            "form_slug": "example-form",
            "event_type": "TEST",
        }
    assert "output/example-form/audit/audit_log.jsonl" not in storage.direct_files


def test_audit_service_reuses_explicit_event_id_for_idempotent_retry():
    from repositories.audit_log_repository import StorageAuditLogRepository
    from services.audit_log_service import AuditLogService
    from testing_support import InMemoryStorage

    storage = InMemoryStorage({})
    service = AuditLogService(StorageAuditLogRepository(storage))
    event_id = "00000000-0000-4000-8000-000000000003"

    first = service.log_event("CORRECTION_RESUBMITTED", "abc", "sample", event_id=event_id)
    second = service.log_event("CORRECTION_RESUBMITTED", "abc", "sample", event_id=event_id)

    assert first["event_id"] == second["event_id"] == event_id
    assert list(storage.direct_files) == [f"output/sample/audit/events/{event_id}.jsonl"]


def test_required_audit_uses_deterministic_operation_and_event_ids():
    from services.audit_log_service import AuditLogService

    service = AuditLogService(repository=object())
    first = {"data_json": {}}
    second = {"data_json": {}}

    first_marker = service.stage_pending(
        first,
        event_type="RETURNED_FOR_CORRECTION",
        submission_id="submission-1",
        form_slug="sample",
        operation_key="2026-10-30T10:00:00+00:00",
    )
    second_marker = service.stage_pending(
        second,
        event_type="RETURNED_FOR_CORRECTION",
        submission_id="submission-1",
        form_slug="sample",
        operation_key="2026-10-30T10:00:00+00:00",
    )

    assert first_marker["operation_id"] == second_marker["operation_id"]
    assert first_marker["event"]["event_id"] == second_marker["event"]["event_id"]


def test_required_audit_event_list_retries_after_second_write_without_duplicates():
    from repositories.audit_log_repository import StorageAuditLogRepository
    from services.audit_log_service import AuditLogService
    from testing_support import InMemoryStorage

    storage = InMemoryStorage({})
    persistent = StorageAuditLogRepository(storage)

    class FailSecondOnce:
        calls = 0
        failed = False

        def append(self, entry):
            self.calls += 1
            if self.calls == 2 and not self.failed:
                self.failed = True
                raise OSError("Nextcloud unavailable after first event")
            persistent.append(entry)

    service = AuditLogService(FailSecondOnce())
    submission = {"data_json": {}}
    marker = service.stage_pending_events(
        submission,
        submission_id="submission-1",
        form_slug="sample",
        operation_key="signed-training-agreement:file-17",
        events=[
            {"event_type": "SIGNED_DOCUMENT_UPLOADED", "metadata": {"filename": "signed.pdf"}},
            {"event_type": "SIGNATURE_VERIFIED", "metadata": {"result": "VALID_SIGNATURE"}},
        ],
    )

    with pytest.raises(OSError, match="after first event"):
        service.write_pending(marker)
    first_path = f"output/sample/audit/events/{marker['events'][0]['event_id']}.jsonl"
    second_path = f"output/sample/audit/events/{marker['events'][1]['event_id']}.jsonl"
    assert list(storage.direct_files) == [first_path]
    assert "_audit_pending" in submission["data_json"]

    service.write_pending(marker)

    assert set(storage.direct_files) == {first_path, second_path}
    assert len(marker["events"]) == 2
    assert len({item["event_id"] for item in marker["events"]}) == 2


@pytest.mark.parametrize(
    "entry",
    [
        {"event_id": "../escape", "form_slug": "example-form"},
        {"event_id": "00000000-0000-4000-8000-000000000001", "form_slug": "../escape"},
    ],
)
def test_audit_repository_rejects_unsafe_event_paths(entry):
    from repositories.audit_log_repository import StorageAuditLogRepository
    from testing_support import InMemoryStorage

    storage = InMemoryStorage({})
    with pytest.raises(ValueError):
        StorageAuditLogRepository(storage).append(entry)
    assert storage.direct_files == {}


@pytest.fixture(autouse=True)
def stub_form_pdf(monkeypatch):
    import services.submission_service as submission_module

    def fake_generate_pdf(app, template_name, context, output_path):
        Path(output_path).write_bytes(b"%PDF-1.4\n% test pdf\n")

    monkeypatch.setattr(submission_module, "generate_pdf", fake_generate_pdf)


FORM_URL = "/submit/formularz_zgloszeniowy"


def test_submit_valid_form_creates_csv_file(app, client, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    response = client.post(FORM_URL, data=valid_form_data, follow_redirects=True)

    assert response.status_code == 200
    assert len(app.testing_storage.csv_rows) == 1


def test_csv_contains_submitted_candidate_data(app, client, valid_form_data, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "validate_submission", lambda *args, **kwargs: {})

    response = client.post(FORM_URL, data=valid_form_data, follow_redirects=True)

    assert response.status_code == 200

    row = app.testing_storage.csv_rows[0]
    assert row["imie"] == valid_form_data["imie"]
    assert row["nazwisko"] == valid_form_data["nazwisko"]
    assert row["email"] == valid_form_data["email"]
