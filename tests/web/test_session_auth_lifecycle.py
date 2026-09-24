from __future__ import annotations

import time
from pathlib import Path

from flask import Flask, g, jsonify

from backend.storage.user_repository import UserRepository
from backend.web.flask_auth import create_auth_blueprint, install_session_auth


def _app(tmp_path: Path, *, idle: int = 1800, absolute: int = 28800):
    tmp_path.mkdir(parents=True, exist_ok=True)
    repository = UserRepository(tmp_path / "auth.db")
    repository.init_schema()
    repository.create_user(
        user_id="U1", login_name="operator", password="Initial-pass-1",
        role="operator", display_name="Operator",
    )
    app = Flask(__name__)
    app.secret_key = "test-session-secret"
    app.config.update(TESTING=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    install_session_auth(
        app, user_repository=repository,
        idle_timeout_seconds=idle, absolute_timeout_seconds=absolute,
    )
    app.register_blueprint(create_auth_blueprint(
        user_repository=repository,
        idle_timeout_seconds=idle, absolute_timeout_seconds=absolute,
    ))

    @app.get("/protected")
    def protected():
        if g.current_principal is None:
            return jsonify({"error": "authentication required"}), 401
        return jsonify({"user_id": g.current_principal.user_id})

    return app, repository


def _cookie_headers(response):
    return response.headers.getlist("Set-Cookie")


def _login(client, password="Initial-pass-1"):
    return client.post("/api/auth/login", json={"username": "operator", "password": password})


def _csrf(client):
    cookie = client.get_cookie("csrftoken")
    return cookie.value if cookie else None


def test_login_uses_matching_browser_session_cookies_and_logout_clears_both(tmp_path):
    app, _ = _app(tmp_path)
    client = app.test_client()
    response = _login(client)
    assert response.status_code == 200
    cookies = _cookie_headers(response)
    session_cookie = next(value for value in cookies if value.startswith("session="))
    csrf_cookie = next(value for value in cookies if value.startswith("csrftoken="))
    for value in (session_cookie, csrf_cookie):
        assert "Expires=" not in value
        assert "Max-Age=" not in value
        assert "SameSite=Lax" in value
    assert "HttpOnly" in session_cookie
    assert "HttpOnly" not in csrf_cookie

    response = client.post("/api/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
    assert response.status_code == 200
    cleared = _cookie_headers(response)
    assert any(value.startswith("session=;") and "Max-Age=0" in value for value in cleared)
    assert any(value.startswith("csrftoken=;") and "Max-Age=0" in value for value in cleared)


def test_new_browser_session_requires_login_but_explicit_cookie_restore_remains_valid(tmp_path):
    app, _ = _app(tmp_path)
    original = app.test_client()
    assert _login(original).status_code == 200
    session_value = original.get_cookie("session").value

    clean_browser = app.test_client()
    assert clean_browser.get("/protected").status_code == 401

    restored_browser = app.test_client()
    restored_browser.set_cookie("session", session_value)
    assert restored_browser.get("/protected").status_code == 200


def test_invalidated_sessions_reject_access_and_clear_session_and_csrf(tmp_path):
    for reason in ("disabled", "revision", "idle", "absolute"):
        app, repository = _app(tmp_path / reason, idle=10, absolute=20)
        client = app.test_client()
        assert _login(client).status_code == 200
        with client.session_transaction() as session:
            if reason == "idle":
                session["user"]["last_seen_at"] = int(time.time()) - 11
            elif reason == "absolute":
                session["user"]["issued_at"] = int(time.time()) - 21
            session.modified = True
        if reason == "disabled":
            repository.set_disabled("U1", True)
        elif reason == "revision":
            repository.set_role("U1", "viewer")

        response = client.get("/protected")
        assert response.status_code == 401
        cleared = _cookie_headers(response)
        assert any(value.startswith("session=;") and "Max-Age=0" in value for value in cleared)
        assert any(value.startswith("csrftoken=;") and "Max-Age=0" in value for value in cleared)


def test_password_change_clears_both_cookies_and_requires_new_password(tmp_path):
    app, _ = _app(tmp_path)
    client = app.test_client()
    assert _login(client).status_code == 200
    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "Initial-pass-1", "new_password": "Changed-pass-2"},
        headers={"X-CSRF-Token": _csrf(client)},
    )
    assert response.status_code == 200
    cleared = _cookie_headers(response)
    assert any(value.startswith("session=;") and "Max-Age=0" in value for value in cleared)
    assert any(value.startswith("csrftoken=;") and "Max-Age=0" in value for value in cleared)
    assert _login(client).status_code == 401
    assert _login(client, "Changed-pass-2").status_code == 200
