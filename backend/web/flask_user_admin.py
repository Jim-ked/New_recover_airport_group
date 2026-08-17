from __future__ import annotations

from typing import Any, Callable, Optional

from backend.auth.principal import Principal
from backend.web.error_mapping import map_expected_error
from backend.web.http import error_body
from backend.web.user_admin_api import UserAdminApi


PrincipalResolver = Callable[[Any], Optional[Principal]]
MutationGuard = Callable[[Any, Principal], None]


def create_user_admin_blueprint(
    *,
    api: UserAdminApi,
    principal_resolver: PrincipalResolver,
    mutation_guard: Optional[MutationGuard] = None,
):
    try:
        from flask import Blueprint, current_app, jsonify, request
        from werkzeug.exceptions import BadRequest
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask runtime dependency is required to bind User Admin API") from exc

    bp = Blueprint("user_admin_v1", __name__, url_prefix="/api")

    def principal_or_401():
        principal = principal_resolver(request)
        if principal is None:
            return None, (jsonify(error_body("AUTHENTICATION_REQUIRED", "Authentication is required")), 401)
        if not isinstance(principal, Principal):
            raise TypeError("principal_resolver must return Principal or None")
        return principal, None

    def guard(principal: Principal):
        if mutation_guard is not None:
            mutation_guard(request, principal)

    def json_body():
        try:
            value = request.get_json(force=False, silent=False)
        except BadRequest:
            raise
        if value is None:
            raise BadRequest("JSON request body is required")
        return value

    def render(response):
        return jsonify(dict(response.body)), response.status

    def invoke(fn):
        try:
            return fn()
        except BadRequest as exc:
            return jsonify(error_body("INVALID_JSON", str(exc))), 400
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return jsonify(dict(mapped.body)), mapped.status
            current_app.logger.exception("Unhandled User Admin API error")
            return jsonify(error_body("INTERNAL_ERROR", "Unexpected server error")), 500

    @bp.get("/users")
    def list_users():
        def action():
            principal, denied = principal_or_401()
            if denied is not None: return denied
            return render(api.list(principal=principal))
        return invoke(action)

    @bp.post("/users")
    def create_user():
        def action():
            principal, denied = principal_or_401()
            if denied is not None: return denied
            guard(principal)
            return render(api.create(json_body(), principal=principal))
        return invoke(action)

    @bp.put("/users/<user_id>/role")
    def set_role(user_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None: return denied
            guard(principal)
            return render(api.set_role(user_id, json_body(), principal=principal))
        return invoke(action)

    @bp.put("/users/<user_id>/disabled")
    def set_disabled(user_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None: return denied
            guard(principal)
            return render(api.set_disabled(user_id, json_body(), principal=principal))
        return invoke(action)

    @bp.post("/users/<user_id>/reset-password")
    def reset_password(user_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None: return denied
            guard(principal)
            return render(api.reset_password(user_id, json_body(), principal=principal))
        return invoke(action)

    return bp


__all__ = ["create_user_admin_blueprint"]
