from __future__ import annotations

import time
from pathlib import Path

from flask import Blueprint, Flask, g, jsonify

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

    tiles = Blueprint("tiles_v1", __name__, url_prefix="/tiles")

    @tiles.get("/<source>/<int:z>/<int:x>/<int:y>.jpg")
    def tile(source, z, x, y):
        return b"tile", 200, {"Content-Type": "image/jpeg"}

    app.register_blueprint(tiles)

    @app.get("/protected")
    def protected():
        if g.current_principal is None:
            return jsonify({"error": "authentication required"}), 401
        return jsonify({"user_id": g.current_principal.user_id})

    @app.get("/api/runs")
    @app.get("/api/runs/worker-status")
    @app.get("/api/runs/<run_id>")
    @app.get("/api/runs/<run_id>/events")
    def run_poll(run_id=None):
        if g.current_principal is None:
            return jsonify({"error": "authentication required"}), 401
        return jsonify({"run_id": run_id, "items": []})

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


def test_public_assets_skip_authority_and_run_polls_do_not_refresh_idle(tmp_path):
    app, repository = _app(tmp_path)
    client = app.test_client()
    assert _login(client).status_code == 200
    with client.session_transaction() as session:
        session["user"]["last_seen_at"] = int(time.time()) - 5
        original_last_seen = session["user"]["last_seen_at"]
        session.modified = True

    calls = 0
    original_get = repository.get

    def counted_get(user_id):
        nonlocal calls
        calls += 1
        return original_get(user_id)

    repository.get = counted_get
    assert client.get("/static/missing.css").status_code == 404
    assert client.get("/tiles/world/4/1/2.jpg").status_code == 200
    assert calls == 0

    for path in ("/api/runs", "/api/runs/worker-status", "/api/runs/R1", "/api/runs/R1/events"):
        separator = "&" if "?" in path else "?"
        path = f"{path}{separator}_background_poll=1"
        assert client.get(path).status_code == 200
    assert calls == 4
    with client.session_transaction() as session:
        assert session["user"]["last_seen_at"] == original_last_seen

    assert client.get("/api/runs").status_code == 200
    with client.session_transaction() as session:
        assert session["user"]["last_seen_at"] > original_last_seen

    with client.session_transaction() as session:
        session["user"]["last_seen_at"] = original_last_seen
        session.modified = True
    assert client.get("/protected").status_code == 200
    assert calls == 6
    with client.session_transaction() as session:
        assert session["user"]["last_seen_at"] > original_last_seen


def test_run_poll_still_rejects_expired_disabled_and_revised_sessions(tmp_path):
    for reason in ("idle", "absolute", "disabled", "revision"):
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
        assert client.get("/api/runs/R1/events?_background_poll=1").status_code == 401
