from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from services.access_token_service import AccessTokenService
from services.process_service import build_initial_process_fields, build_legacy_process_fields


class SubmissionService:
    def __init__(
        self,
        submission_repository,
        workflow_service=None,
        notification_service=None,
        audit_log_service=None,
        access_token_service: AccessTokenService | None = None,
    ) -> None:
        self.submission_repository = submission_repository
        self.workflow_service = workflow_service
        self.notification_service = notification_service
        self.audit_log_service = audit_log_service
        self.access_token_service = access_token_service or AccessTokenService()

    def create_submission(self, form_slug: str, form_config: dict, form_data: dict) -> dict:
        submission_id = str(uuid4())
        documents = form_config.get("documents", [])
        document_ids = {document.get("id") for document in documents if document.get("enabled", True)}
        submission = {
            "submission_id": submission_id,
            "form_slug": form_slug,
            "created_at": datetime.now().strftime("%d.%m.%Y"),
            "form_name": form_config.get("title", form_slug),
            "access_token": self.access_token_service.generate_token(),
            **form_data,
            **build_initial_process_fields(
                declaration_required="declaration" in document_ids,
                agreement_required="agreement" in document_ids,
            ),
            **build_legacy_process_fields(),
        }
        self.submission_repository.create(submission)
        if self.audit_log_service:
            self.audit_log_service.log_event("FORM_SUBMITTED", submission_id, form_slug)
        if self.notification_service:
            self.notification_service.notify_event("FORM_SUBMITTED", submission, form_config)
        return submission
