from __future__ import annotations

from typing import Any, Callable, Optional

from backend.auth.principal import Principal
from backend.web.account_api import AccountApi
from backend.web.http import error_body


PrincipalResolver = Callable[[Any], Optional[Principal]]


def create_account_blueprint(*, api: AccountApi, principal_resolver: PrincipalResolver):
    try:
        from flask import Blueprint, jsonify, request
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask runtime dependency is required to bind Account API") from exc

    bp = Blueprint("account_v1", __name__, url_prefix="/api")

    @bp.get("/me")
    def current_account():
        principal = principal_resolver(request)
        if principal is None:
            return jsonify(error_body("AUTHENTICATION_REQUIRED", "Authentication is required")), 401
        if not isinstance(principal, Principal):
            raise TypeError("principal_resolver must return Principal or None")
        response = api.current(principal=principal)
        return jsonify(dict(response.body)), response.status

    return bp


__all__ = ["create_account_blueprint"]
