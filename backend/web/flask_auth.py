from __future__ import annotations

import secrets
import time
from typing import Any, Optional

from backend.auth.csrf import require_csrf_match
from backend.auth.principal import Principal, principal_from_session_user
from backend.auth.session_policy import (
    DEFAULT_ABSOLUTE_TIMEOUT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    validate_session,
)
from backend.storage.user_repository import (
    AccountDisabledError,
    AuthenticationFailedError,
    UserNotFoundError,
    UserRepository,
)
from backend.web.http import error_body


def _issue_csrf(response: Any, *, secure: bool) -> Any:
    try:
        from flask import session
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask runtime dependency is required") from exc
    token = secrets.token_urlsafe(32)
    session["csrf_token"] = token
    response.set_cookie(
        "csrftoken",
        token,
        httponly=False,
        samesite="Lax",
        secure=bool(secure),
    )
    return response


def _clear_session(response: Any) -> Any:
    try:
        from flask import session
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask runtime dependency is required") from exc
    session.clear()
    response.delete_cookie("csrftoken")
    return response


def install_session_auth(
    app: Any,
    *,
    user_repository: UserRepository,
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS,
    absolute_timeout_seconds: int = DEFAULT_ABSOLUTE_TIMEOUT_SECONDS,
) -> None:
    """Install authority-backed session validation before every request."""

    @app.before_request
    def _resolve_session():
        from flask import g, session

        g.current_principal = None
        g.session_invalid_reason = None
        raw = session.get("user")
        if raw is None:
            return None
        user_id = raw.get("user_id") if isinstance(raw, dict) else None
        if not isinstance(user_id, str) or not user_id:
            session.clear()
            g.session_invalid_reason = "malformed"
            return None
        try:
            authority = user_repository.get(user_id)
        except UserNotFoundError:
            authority = None
        result = validate_session(
            raw,
            authority,
            now=int(time.time()),
            idle_timeout_seconds=idle_timeout_seconds,
            absolute_timeout_seconds=absolute_timeout_seconds,
        )
        if not result.valid:
            session.clear()
            g.session_invalid_reason = result.reason
            return None
        principal = result.principal
        assert principal is not None
        g.current_principal = principal
        if result.refresh_last_seen:
            raw = dict(raw)
            raw["last_seen_at"] = int(time.time())
            # Role/display facts are refreshed from the account authority. auth_revision
            # protects against stale authorization even when the signed cookie survives.
            raw["role"] = authority["role"]
            raw["login_name"] = authority["login_name"]
            raw["display_name"] = authority.get("display_name")
            session["user"] = raw
        return None


def session_principal_resolver(_request: Any) -> Optional[Principal]:
    """Resolve Principal from validated Flask request context, with legacy fallback."""
    try:
        from flask import g, session
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask runtime dependency is required") from exc
    principal = getattr(g, "current_principal", None)
    if isinstance(principal, Principal):
        return principal
    # Fallback keeps isolated adapter tests/backwards embedding working when the full
    # session-auth before_request hook was intentionally not installed.
    return principal_from_session_user(session.get("user"))


def session_csrf_mutation_guard(request: Any, _principal: Principal) -> None:
    try:
        from flask import session
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask runtime dependency is required") from exc
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    require_csrf_match(expected=session.get("csrf_token"), supplied=supplied)


def create_auth_blueprint(
    *,
    user_repository: UserRepository,
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS,
    absolute_timeout_seconds: int = DEFAULT_ABSOLUTE_TIMEOUT_SECONDS,
):
    try:
        from flask import Blueprint, current_app, jsonify, make_response, request, session
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask runtime dependency is required to bind authentication") from exc

    bp = Blueprint("auth_v1", __name__, url_prefix="/api/auth")

    @bp.post("/login")
    def login():
        data = request.get_json(silent=True) or request.form or {}
        login_name = str(data.get("username") or data.get("login_name") or "").strip()
        password = str(data.get("password") or "")
        if not login_name or not password:
            return jsonify(error_body("LOGIN_INPUT_REQUIRED", "用户名和密码不能为空")), 400
        try:
            user = user_repository.authenticate(login_name, password)
        except AuthenticationFailedError:
            return jsonify(error_body("LOGIN_FAILED", "用户名或密码错误")), 401
        except AccountDisabledError:
            return jsonify(error_body("ACCOUNT_DISABLED", "账号已禁用")), 403
        now = int(time.time())
        session.clear()
        session.permanent = True
        session["user"] = {
            "user_id": user["user_id"],
            "login_name": user["login_name"],
            "display_name": user.get("display_name"),
            "role": user["role"],
            "auth_revision": user["auth_revision"],
            "issued_at": now,
            "last_seen_at": now,
        }
        response = make_response(jsonify({
            "user_id": user["user_id"],
            "login_name": user["login_name"],
            "display_name": user.get("display_name"),
            "role": user["role"],
            "idle_timeout_seconds": idle_timeout_seconds,
            "absolute_timeout_seconds": absolute_timeout_seconds,
        }))
        return _issue_csrf(response, secure=bool(current_app.config.get("SESSION_COOKIE_SECURE", False)))

    @bp.post("/logout")
    def logout():
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        try:
            require_csrf_match(expected=session.get("csrf_token"), supplied=supplied)
        except Exception:
            # Logout remains idempotent even after expiry. A stale browser can always
            # discard its local cookie/session state instead of getting trapped by CSRF.
            pass
        return _clear_session(make_response(jsonify({"logged_out": True})))

    @bp.post("/change-password")
    def change_password():
        principal = session_principal_resolver(request)
        if principal is None:
            return jsonify(error_body("AUTHENTICATION_REQUIRED", "需要重新登录")), 401
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        try:
            require_csrf_match(expected=session.get("csrf_token"), supplied=supplied)
        except Exception:
            return jsonify(error_body("CSRF_FAILED", "CSRF 校验失败")), 403
        data = request.get_json(silent=True) or {}
        current_password = str(data.get("current_password") or "")
        new_password = str(data.get("new_password") or "")
        try:
            user_repository.change_password(
                principal.user_id,
                current_password=current_password,
                new_password=new_password,
            )
        except AuthenticationFailedError:
            return jsonify(error_body("CURRENT_PASSWORD_INVALID", "当前密码不正确")), 400
        except AccountDisabledError:
            return jsonify(error_body("ACCOUNT_DISABLED", "账号已禁用")), 403
        except ValueError as exc:
            return jsonify(error_body("PASSWORD_INVALID", str(exc))), 400
        # Changing a password increments auth_revision. Clear this browser too so every
        # old session is invalid immediately and the user consciously re-authenticates.
        return _clear_session(make_response(jsonify({"password_changed": True, "reauthentication_required": True})))

    return bp


__all__ = [
    "session_principal_resolver",
    "session_csrf_mutation_guard",
    "install_session_auth",
    "create_auth_blueprint",
]
