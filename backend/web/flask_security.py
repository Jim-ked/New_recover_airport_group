from __future__ import annotations

from typing import Optional

from backend.auth.principal import Principal
from backend.web.error_mapping import map_expected_error
from backend.web.flask_runs import MutationGuard, PrincipalResolver
from backend.web.http import ApiResponse, error_body
from backend.web.security_api import SecurityApi, SecurityDownload


def create_security_blueprint(
    *,
    api: SecurityApi,
    principal_resolver: PrincipalResolver,
    mutation_guard: Optional[MutationGuard] = None,
):
    try:
        from flask import Blueprint, current_app, jsonify, make_response, request
        from werkzeug.exceptions import BadRequest
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask is required to bind security routes") from exc

    blueprint = Blueprint("security_v1", __name__, url_prefix="/api/security")

    def principal():
        value = principal_resolver(request)
        if value is None:
            return None, (jsonify(error_body("AUTHENTICATION_REQUIRED", "Authentication is required")), 401)
        if not isinstance(value, Principal):
            raise TypeError("principal_resolver must return Principal or None")
        return value, None

    def json_body():
        value = request.get_json(force=False, silent=False)
        if value is None:
            raise BadRequest("JSON request body is required")
        return value

    def invoke(call, *, mutate: bool = False):
        actor, denied = principal()
        if denied is not None:
            return denied
        try:
            if mutate and mutation_guard is not None:
                mutation_guard(request, actor)
            response = call(actor)
            if isinstance(response, SecurityDownload):
                result = make_response(response.content)
                result.headers["Content-Type"] = response.mimetype
                result.headers["Content-Disposition"] = f'attachment; filename="{response.filename}"'
                result.headers["Cache-Control"] = "private, no-store"
                return result
            if isinstance(response, ApiResponse):
                return jsonify(dict(response.body)), response.status
            raise TypeError("security API returned an unsupported response")
        except BadRequest as exc:
            return jsonify(error_body("INVALID_JSON", str(exc))), 400
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return jsonify(dict(mapped.body)), mapped.status
            current_app.logger.exception("Unhandled security API error")
            return jsonify(error_body("INTERNAL_ERROR", "Unexpected server error")), 500

    @blueprint.get("/alerts")
    def alerts():
        return invoke(lambda actor: api.list_alerts(principal=actor, severity=request.args.get("severity")))

    @blueprint.post("/alerts")
    def create_alert():
        return invoke(lambda actor: api.raise_alert(json_body(), principal=actor), mutate=True)

    @blueprint.get("/backups")
    def backups():
        return invoke(lambda actor: api.list_backups(principal=actor))

    @blueprint.post("/backups")
    def create_backup():
        return invoke(lambda actor: api.create_backup(json_body(), principal=actor), mutate=True)

    @blueprint.post("/backups/<backup_id>/restore")
    def restore_backup(backup_id: str):
        return invoke(lambda actor: api.restore_backup(backup_id, principal=actor), mutate=True)

    @blueprint.delete("/backups/<backup_id>")
    def delete_backup(backup_id: str):
        return invoke(lambda actor: api.delete_backup(backup_id, principal=actor), mutate=True)

    @blueprint.get("/audit-reports")
    def audit_reports():
        return invoke(lambda actor: api.list_audit_reports(principal=actor))

    @blueprint.post("/audit-reports")
    def create_audit_report():
        return invoke(lambda actor: api.generate_audit_report(json_body(), principal=actor), mutate=True)

    @blueprint.get("/audit-reports/<report_id>/file")
    def audit_report_file(report_id: str):
        return invoke(lambda actor: api.download_audit_report(report_id, principal=actor))

    return blueprint


__all__ = ["create_security_blueprint"]
