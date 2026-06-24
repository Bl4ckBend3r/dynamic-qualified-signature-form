from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from repositories.audit_log_repository import StorageAuditLogRepository
from repositories.storage_repository import StorageRepository
from repositories.submission_repository import CsvSubmissionRepository, PostgresSubmissionRepository
from services.access_token_service import AccessTokenService
from services.audit_log_service import AuditLogService
from services.document_service import DocumentService
from services.documents.agreement_flow_service import AgreementFlowService
from services.documents.declaration_flow_service import DeclarationFlowService
from services.documents.document_access_service import DocumentAccessService
from services.documents.document_download_service import DocumentDownloadService
from services.documents.document_signing_service import DocumentSigningService
from services.form_config_service import FormConfigService
from services.legacy_fallback_report_service import LegacyFallbackReportService
from services.legacy_fallback_readiness_service import LegacyFallbackReadinessService
from services.mail_dispatch_service import MailDispatchService
from services.nextcloud_storage import create_nextcloud_storage_from_env
from services.notification_service import NotificationService
from services.rules_service import RulesService
from services.strict_mode_stabilization_service import StrictModeStabilizationService
from services.submission_document_service import SubmissionDocumentService
from services.submission_decision_service import SubmissionDecisionService
from services.submission_service import SubmissionService
from services.submission_workflow_history_service import SubmissionWorkflowHistoryService
from services.workflow_service import WorkflowService


@dataclass
class ServiceContainer:
    storage: object
    storage_repository: StorageRepository
    submission_repository: object
    submission_service: SubmissionService
    workflow_service: WorkflowService
    document_service: DocumentService
    document_access_service: DocumentAccessService
    document_download_service: DocumentDownloadService
    document_signing_service: DocumentSigningService
    declaration_flow_service: DeclarationFlowService
    agreement_flow_service: AgreementFlowService
    notification_service: NotificationService
    mail_dispatch_service: MailDispatchService
    submission_document_service: SubmissionDocumentService
    submission_workflow_history_service: SubmissionWorkflowHistoryService
    submission_decision_service: SubmissionDecisionService
    legacy_fallback_report_service: LegacyFallbackReportService
    legacy_fallback_readiness_service: LegacyFallbackReadinessService
    strict_mode_stabilization_service: StrictModeStabilizationService
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
    mail_dispatch_service = MailDispatchService(
        notification_service=notification_service,
        submission_repository=submission_repository,
        audit_log_service=audit_log_service,
    )
    submission_document_service = SubmissionDocumentService(
        submission_repository=submission_repository,
        storage=storage,
        log=app.logger,
    )
    submission_workflow_history_service = SubmissionWorkflowHistoryService(
        submission_repository=submission_repository,
        log=app.logger,
        strict_workflow_history_read=bool(app.config.get("STRICT_WORKFLOW_HISTORY_READ")),
    )
    submission_decision_service = SubmissionDecisionService(
        submission_repository=submission_repository,
        log=app.logger,
        strict_decision_audit_read=bool(app.config.get("STRICT_DECISION_AUDIT_READ")),
    )
    legacy_fallback_report_service = LegacyFallbackReportService(
        output_dir=app.config.get("NEXTCLOUD_OUTPUT_DIR", "output"),
        strict_document_metadata_read=bool(app.config.get("STRICT_DOCUMENT_METADATA_READ")),
        strict_workflow_history_read=bool(app.config.get("STRICT_WORKFLOW_HISTORY_READ")),
        strict_decision_audit_read=bool(app.config.get("STRICT_DECISION_AUDIT_READ")),
    )
    legacy_fallback_readiness_service = LegacyFallbackReadinessService(
        report_service=LegacyFallbackReportService(output_dir=app.config.get("NEXTCLOUD_OUTPUT_DIR", "output"))
    )
    strict_mode_stabilization_service = StrictModeStabilizationService(
        report_service=LegacyFallbackReportService(output_dir=app.config.get("NEXTCLOUD_OUTPUT_DIR", "output")),
        strict_flags={
            "STRICT_DOCUMENT_METADATA_READ": bool(app.config.get("STRICT_DOCUMENT_METADATA_READ")),
            "STRICT_WORKFLOW_HISTORY_READ": bool(app.config.get("STRICT_WORKFLOW_HISTORY_READ")),
            "STRICT_DECISION_AUDIT_READ": bool(app.config.get("STRICT_DECISION_AUDIT_READ")),
        },
    )
    rules_service = RulesService()
    declaration_flow_service = DeclarationFlowService()
    agreement_flow_service = AgreementFlowService()
    document_access_service = DocumentAccessService()
    document_download_service = DocumentDownloadService(access_service=document_access_service)
    document_service = DocumentService(
        storage=storage,
        submission_repository=submission_repository,
        audit_log_service=audit_log_service,
        access_token_service=access_token_service,
        submission_document_service=submission_document_service,
        strict_document_metadata_read=bool(app.config.get("STRICT_DOCUMENT_METADATA_READ")),
    )
    submission_service = SubmissionService(
        submission_repository,
        storage=storage,
        workflow_service=workflow_service,
        document_service=document_service,
        notification_service=notification_service,
        audit_log_service=audit_log_service,
        access_token_service=access_token_service,
        submission_document_service=submission_document_service,
    )
    document_signing_service = DocumentSigningService(
        storage=storage,
        submission_repository=submission_repository,
        submission_service=submission_service,
        document_service=document_service,
        document_storage_service=document_service.document_storage_service,
        submission_document_service=submission_document_service,
    )

    return ServiceContainer(
        storage=storage,
        storage_repository=storage_repository,
        submission_repository=submission_repository,
        submission_service=submission_service,
        workflow_service=workflow_service,
        document_service=document_service,
        document_access_service=document_access_service,
        document_download_service=document_download_service,
        document_signing_service=document_signing_service,
        declaration_flow_service=declaration_flow_service,
        agreement_flow_service=agreement_flow_service,
        notification_service=notification_service,
        mail_dispatch_service=mail_dispatch_service,
        submission_document_service=submission_document_service,
        submission_workflow_history_service=submission_workflow_history_service,
        submission_decision_service=submission_decision_service,
        legacy_fallback_report_service=legacy_fallback_report_service,
        legacy_fallback_readiness_service=legacy_fallback_readiness_service,
        strict_mode_stabilization_service=strict_mode_stabilization_service,
        audit_log_service=audit_log_service,
        access_token_service=access_token_service,
        form_config_service=form_config_service,
        rules_service=rules_service,
    )
