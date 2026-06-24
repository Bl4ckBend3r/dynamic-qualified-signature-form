from __future__ import annotations

from sqlalchemy import inspect


REQUIRED_P4_SCHEMA = {
    "submission_files": [
        "id",
        "submission_id",
        "public_submission_id",
        "form_slug",
        "document_id",
        "document_type",
        "filename",
        "original_filename",
        "storage_path",
        "mime_type",
        "size_bytes",
        "signed",
        "checksum_sha256",
        "status",
        "signature_status",
        "signature_validation_result",
        "agreement_number",
        "training_key",
        "generated_at",
        "signed_at",
        "created_at",
        "updated_at",
    ],
    "submission_workflow_events": [
        "id",
        "submission_id",
        "public_submission_id",
        "form_slug",
        "previous_status",
        "new_status",
        "previous_step",
        "new_step",
        "actor_id",
        "actor_email",
        "actor_role",
        "reason",
        "source",
        "created_at",
    ],
    "submission_decisions": [
        "id",
        "submission_id",
        "public_submission_id",
        "form_slug",
        "decision",
        "justification",
        "officer_id",
        "officer_email",
        "previous_status",
        "target_status",
        "email_requested",
        "email_sent",
        "email_log_id",
        "decided_at",
        "created_at",
    ],
}


class P4SchemaCheckService:
    def check_schema(self, engine_or_connection) -> dict:
        inspector = inspect(engine_or_connection)
        existing_tables = set(inspector.get_table_names())
        missing_tables = [
            table_name for table_name in REQUIRED_P4_SCHEMA if table_name not in existing_tables
        ]
        missing_columns = {}
        for table_name, required_columns in REQUIRED_P4_SCHEMA.items():
            if table_name in missing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            missing = [column for column in required_columns if column not in existing_columns]
            if missing:
                missing_columns[table_name] = missing
        return {
            "schema_ready": not missing_tables and not missing_columns,
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
            "errors": [],
        }
