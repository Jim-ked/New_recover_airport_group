from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from backend.auth.principal import Principal
from backend.storage.audit_repository import AuditRepository
from backend.web.audit_api import AuditApi
from backend.web.http import error_body


PrincipalResolver = Callable[[Any], Optional[Principal]]


def derive_resource_target(path: str) -> Tuple[Optional[str], Optional[str]]:
    parts = [part for part in str(path).split("/") if part]
    if not parts or parts[0] != "api" or len(parts) < 2:
        return None, None
    resource_type = parts[1]
    resource_id = None
    # Collection/action endpoints do not invent object identity.  A concrete second
    # segment after the collection is treated as an ID unless it is a known collection
    # action word.
    if len(parts) >= 3 and parts[2] not in {
        "validate", "history", "comparable-runs", "damage-candidates",
        "damage-comparison", "scenario-comparison", "config-comparison",
        "drafts", "export-data",
    }:
        resource_id = parts[2]
    return resource_type, resource_id


def install_audit_hook(
    app: Any,
    *,
    repository: AuditRepository,
    principal_resolver: PrincipalResolver,
) -> None:
    """Record API outcomes after Flask has produced the response.

    Auditing is best-effort with respect to the user response: an audit storage failure is
    logged by Flask but must not replace an already-computed business response.  Deployment
    monitoring should still alert on those storage failures.
    """

    @app.after_request
    def _audit_after_request(response):
        try:
            from flask import request, g

            if not request.path.startswith("/api/"):
                return response
            try:
                principal = principal_resolver(request)
            except Exception:
                principal = None
            rule = request.url_rule.rule if request.url_rule is not None else request.path
            resource_type, resource_id = derive_resource_target(request.path)
            status = int(response.status_code)
            outcome = "success" if status < 400 else ("denied" if status in {401, 403} else "error")
            repository.append(
                actor_user_id=(principal.user_id if isinstance(principal, Principal) else None),
                actor_role=(principal.role if isinstance(principal, Principal) else None),
                action=f"{request.method.upper()} {rule}",
                resource_type=resource_type,
                resource_id=resource_id,
                request_method=request.method,
                request_path=request.path,
                source_address=request.remote_addr,
                response_status=status,
                outcome=outcome,
                details={
                    **({"endpoint": request.endpoint} if request.endpoint else {}),
                    **(getattr(g, "audit_details", {}) or {}),
                },
            )
        except Exception:
            app.logger.exception("Failed to persist operation audit event")
        return response


def create_audit_blueprint(*, api: AuditApi, principal_resolver: PrincipalResolver):
    try:
        from flask import Blueprint, jsonify, request
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask runtime dependency is required to bind Audit API") from exc

    bp = Blueprint("audit_v1", __name__, url_prefix="/api")

    @bp.get("/audit-events")
    def list_audit_events():
        principal = principal_resolver(request)
        if principal is None:
            return jsonify(error_body("AUTHENTICATION_REQUIRED", "Authentication is required")), 401
        if not isinstance(principal, Principal):
            raise TypeError("principal_resolver must return Principal or None")
        response = api.list(
            principal=principal,
            actor_user_id=request.args.get("actor_user_id"),
            q=request.args.get("q"),
            resource_type=request.args.get("resource_type"),
            resource_id=request.args.get("resource_id"),
            outcome=request.args.get("outcome"),
            created_after=request.args.get("created_after"),
            created_before=request.args.get("created_before"),
            limit=request.args.get("limit"),
            offset=request.args.get("offset"),
        )
        return jsonify(dict(response.body)), response.status

    return bp


__all__ = ["create_audit_blueprint", "install_audit_hook", "derive_resource_target"]
