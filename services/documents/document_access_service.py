from __future__ import annotations


class DocumentAccessService:
    def verify_download_access(self, *, document_service, submission: dict, token: str | None) -> bool:
        if not submission:
            return False
        return document_service.verify_download_token(submission, token)
