from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repositories.audit_log_repository import StorageAuditLogRepository
from repositories.storage_repository import StorageRepository
from repositories.submission_repository import CsvSubmissionRepository, PostgresSubmissionRepository
from services.access_token_service import AccessTokenService
from services.audit_log_service import AuditLogService
from services.document_service import DocumentService
from services.form_config_service import FormConfigService
from services.nextcloud_storage import create_nextcloud_storage_from_env
from services.notification_service import NotificationService
from services.rules_service import RulesService
from services.submission_service import SubmissionService
from services.workflow_service import WorkflowService


@dataclass
class ServiceContainer:
    storage: object
    storage_repository: StorageRepository
    submission_repository: object
    submission_service: SubmissionService
    workflow_service: WorkflowService
    document_service: DocumentService
    notification_service: NotificationService
    audit_log_service: AuditLogService
    access_token_service: AccessTokenService
    form_config_service: FormConfigService
    rules_service: RulesService


def create_services(app, storage_override=None) -> ServiceContainer:
    storage = storage_override or create_nextcloud_storage_from_env()
    form_config_service = FormConfigService()
    access_token_service = AccessTokenService()

    form_slugs = []
    try:
        form_slugs = [Path(filename).stem for filename in storage.list_form_files()]
    except Exception:
        app.logger.warning("Nie udało się odczytać listy formularzy podczas startu aplikacji.", exc_info=True)

    storage_repository = StorageRepository(storage)
    if app.config.get("DATABASE_URL"):
        submission_repository = PostgresSubmissionRepository(
            app.config["DATABASE_URL"],
            create_schema=bool(app.config.get("AUTO_CREATE_DB_SCHEMA")),
        )
        app.logger.info("Submission repository: PostgreSQL")
    else:
        submission_repository = CsvSubmissionRepository(storage, form_slugs=form_slugs)
        app.logger.info("Submission repository: CSV/Nextcloud")
    audit_repository = StorageAuditLogRepository(storage, output_dir=app.config.get("NEXTCLOUD_OUTPUT_DIR", "output"))
    audit_log_service = AuditLogService(Path(app.config["TEMP_DIR"]) / "audit_log.jsonl", repository=audit_repository)
    workflow_service = WorkflowService(submission_repository, audit_log_service=audit_log_service)
    notification_service = NotificationService(
        submission_repository,
        audit_log_service=audit_log_service,
        storage=storage,
    )
    rules_service = RulesService()
    document_service = DocumentService(
        storage=storage,
        submission_repository=submission_repository,
        audit_log_service=audit_log_service,
        access_token_service=access_token_service,
    )
    submission_service = SubmissionService(
        submission_repository,
        storage=storage,
        workflow_service=workflow_service,
        document_service=document_service,
        notification_service=notification_service,
        audit_log_service=audit_log_service,
        access_token_service=access_token_service,
    )

    return ServiceContainer(
        storage=storage,
        storage_repository=storage_repository,
        submission_repository=submission_repository,
        submission_service=submission_service,
        workflow_service=workflow_service,
        document_service=document_service,
        notification_service=notification_service,
        audit_log_service=audit_log_service,
        access_token_service=access_token_service,
        form_config_service=form_config_service,
        rules_service=rules_service,
    )


def install_legacy_helpers(flask_app, container: ServiceContainer) -> None:
    """Keep legacy helper functions usable while routes move to services."""

    import legacy_app

    legacy_app.app = flask_app
    legacy_app.storage = container.storage
    legacy_app.access_token_service = container.access_token_service
