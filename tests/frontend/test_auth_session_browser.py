"""Real-browser checks for session-cookie and logout behavior."""

from __future__ import annotations

from pathlib import Path
from threading import Thread

import pytest
from flask import Flask, g, jsonify
from werkzeug.serving import make_server

from backend.storage.user_repository import UserRepository
from backend.web.flask_auth import create_auth_blueprint, install_session_auth
from backend.web.flask_ui import create_ui_blueprint

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]
SCREENSHOT_DIR = ROOT / "runtime/temp/auth-session-browser"


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as runtime:
        candidates = [None, Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                      Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")]
        failures = []
        for executable in candidates:
            if executable is not None and not executable.exists():
                continue
            try:
                instance = runtime.chromium.launch(
                    headless=True,
                    **({"executable_path": str(executable)} if executable else {}),
                )
                break
            except playwright.Error as error:
                failures.append(str(error).splitlines()[0])
        else:
            pytest.skip("No runnable Chromium: " + "; ".join(failures))
        yield instance
        instance.close()


@pytest.fixture
def auth_server(tmp_path):
    calls = {"me": 0}
    repository = UserRepository(tmp_path / "auth-browser.db")
    repository.init_schema()
    repository.create_user(
        user_id="BROWSER-USER", login_name="browser-user",
        password="Browser-pass-1", role="operator", display_name="浏览器验证员",
    )
    app = Flask(
        "auth-session-browser",
        template_folder=str(ROOT / "frontend/templates"),
        static_folder=str(ROOT / "frontend/static"),
        static_url_path="/static",
    )
    app.secret_key = "auth-session-browser-secret"
    app.config["GIS_TILE_TEMPLATE"] = "/tiles/{source}/{z}/{x}/{y}.jpg"
    install_session_auth(app, user_repository=repository)
    app.register_blueprint(create_auth_blueprint(user_repository=repository))
    app.register_blueprint(create_ui_blueprint())

    def require_principal():
        if g.current_principal is None:
            return jsonify({"error": {"code": "AUTHENTICATION_REQUIRED", "message": "需要重新登录"}}), 401
        return None

    @app.get("/api/me")
    def me():
        calls["me"] += 1
        denied = require_principal()
        if denied:
            return denied
        return jsonify({
            "user_id": g.current_principal.user_id, "role": "operator",
            "display_name": "浏览器验证员", "permissions": ["situations.read", "situations.write"],
        })

    @app.get("/api/aircraft-types")
    @app.get("/api/resource-types")
    def empty_catalog():
        denied = require_principal()
        return denied or jsonify({"items": []})

    @app.get("/api/situations")
    def situations():
        denied = require_principal()
        return denied or jsonify({"items": [], "total": 0})

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", calls
    server.shutdown()
    thread.join(timeout=3)


def _login(page, base_url):
    page.goto(f"{base_url}/login?next=/situations")
    page.locator("#loginName").fill("browser-user")
    page.locator("#loginPassword").fill("Browser-pass-1")
    page.locator("#loginButton").click()
    page.wait_for_url(f"{base_url}/situations")
    page.wait_for_selector("#accountTrigger")


def test_browser_session_restore_and_logout_failure_contract(browser, auth_server):
    auth_server, calls = auth_server
    context = browser.new_context()
    page = context.new_page()
    _login(page, auth_server)
    page.wait_for_function("() => document.documentElement.dataset.role === 'operator'")
    assert calls["me"] == 1

    cookies = {cookie["name"]: cookie for cookie in context.cookies(auth_server)}
    assert cookies["session"]["expires"] == -1
    assert cookies["csrftoken"]["expires"] == -1
    assert cookies["session"]["httpOnly"] is True
    assert cookies["csrftoken"]["httpOnly"] is False
    saved_state = context.storage_state()
    context.close()

    clean_context = browser.new_context()
    clean_page = clean_context.new_page()
    clean_page.goto(f"{auth_server}/situations")
    clean_page.wait_for_url("**/login?**")
    clean_context.close()

    restored_context = browser.new_context(storage_state=saved_state)
    restored_page = restored_context.new_page()
    restored_page.goto(f"{auth_server}/situations")
    restored_page.wait_for_selector("#accountTrigger")
    restored_page.wait_for_function("() => document.documentElement.dataset.role === 'operator'")
    assert restored_page.url == f"{auth_server}/situations"
    assert calls["me"] == 2

    restored_page.route(
        "**/api/auth/logout",
        lambda route: route.fulfill(
            status=500, content_type="application/json",
            body='{"error":{"message":"temporary logout failure"}}',
        ),
    )
    restored_page.locator("#accountTrigger").click()
    restored_page.locator("#logoutAction").click()
    restored_page.wait_for_selector("#logoutMessage", state="visible")
    assert restored_page.url == f"{auth_server}/situations"
    assert "退出未完成" in restored_page.locator("#logoutMessage").inner_text()
    assert {cookie["name"] for cookie in restored_context.cookies(auth_server)} >= {"session", "csrftoken"}
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    restored_page.screenshot(path=str(SCREENSHOT_DIR / "logout-failure-retains-session.png"))

    restored_page.unroute("**/api/auth/logout")
    restored_page.locator("#logoutAction").click()
    restored_page.wait_for_url("**/login?**")
    remaining = {cookie["name"] for cookie in restored_context.cookies(auth_server)}
    assert "session" not in remaining
    assert "csrftoken" not in remaining
    restored_context.close()
