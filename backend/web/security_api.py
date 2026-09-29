from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.auth.principal import Principal
from backend.services.security_service import SecurityService
from backend.web.error_mapping import map_expected_error
from backend.web.http import ApiInputError, ApiResponse, reject_unknown, require_object, required_nonblank_string


@dataclass(frozen=True)
class SecurityDownload:
    content: bytes
    mimetype: str
    filename: str


class SecurityApi:
    def __init__(self, service: SecurityService) -> None:
        self.service = service

    @staticmethod
    def _handle(call) -> ApiResponse:
        try:
            return call()
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return mapped
            raise

    def list_alerts(self, *, principal: Principal, severity: Any = None) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("audit.read")
            parsed = None
            if severity is not None:
                if not isinstance(severity, str) or not severity.strip():
                    raise ApiInputError("severity must be a nonblank string", field="severity")
                parsed = severity.strip()
            return ApiResponse({"items": [item.__dict__ for item in self.service.list_alerts(severity=parsed)]}, 200)
        return self._handle(action)

    def raise_alert(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("users.admin")
            body = require_object(raw_body)
            reject_unknown(body, {"kind", "message", "severity"})
            alert = self.service.raise_alert(
                required_nonblank_string(body, "kind"),
                required_nonblank_string(body, "message"),
                severity=body.get("severity", "warning"),
            )
            return ApiResponse(alert.__dict__, 201)
        return self._handle(action)

    def list_backups(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("audit.read")
            return ApiResponse({"items": [item.__dict__ for item in self.service.list_backups()]}, 200)
        return self._handle(action)

    def create_backup(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("users.admin")
            body = require_object(raw_body)
            reject_unknown(body, {"kind", "encrypted"})
            encrypted = body.get("encrypted", False)
            if not isinstance(encrypted, bool):
                raise ApiInputError("encrypted must be boolean", field="encrypted")
            record = self.service.create_backup(kind=body.get("kind", "full"), encrypted=encrypted)
            return ApiResponse(record.__dict__, 201)
        return self._handle(action)

    def restore_backup(self, backup_id: str, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("users.admin")
            return ApiResponse(self.service.restore_backup(backup_id).__dict__, 200)
        return self._handle(action)

    def delete_backup(self, backup_id: str, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("users.admin")
            removed = self.service.delete_backup(backup_id)
            return ApiResponse({"deleted": removed, "backup_id": backup_id}, 200 if removed else 404)
        return self._handle(action)

    def list_audit_reports(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("audit.read")
            return ApiResponse({"items": [item.__dict__ for item in self.service.list_audit_reports()]}, 200)
        return self._handle(action)

    def generate_audit_report(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("users.admin")
            body = require_object(raw_body)
            reject_unknown(body, {"format", "period_start", "period_end"})
            report = self.service.generate_audit_report(
                format=body.get("format", "csv"),
                period_start=body.get("period_start"),
                period_end=body.get("period_end"),
            )
            return ApiResponse(report.__dict__, 201)
        return self._handle(action)

    def download_audit_report(self, report_id: str, *, principal: Principal) -> SecurityDownload | ApiResponse:
        try:
            principal.require_permission("audit.read")
            content, mimetype, filename = self.service.read_audit_report(report_id)
            return SecurityDownload(content=content, mimetype=mimetype, filename=filename)
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return mapped
            raise


__all__ = ["SecurityApi", "SecurityDownload"]
