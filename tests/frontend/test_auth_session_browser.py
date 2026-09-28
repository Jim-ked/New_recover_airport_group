"""Real-browser checks for session-cookie and logout behavior."""

from __future__ import annotations

from pathlib import Path
from threading import Thread

import pytest
from flask import Flask, g, jsonify, request
from werkzeug.serving import make_server

from backend.storage.user_repository import UserRepository
from backend.storage.audit_repository import AuditRepository
from backend.storage.database import initialize_database
from backend.web.flask_audit import install_audit_hook
from backend.web.flask_auth import (
    create_auth_blueprint,
    install_session_auth,
    session_principal_resolver,
)
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
    calls = {"me": 0, "logout": 0}
    database_path = tmp_path / "auth-browser.db"
    initialize_database(database_path)
    repository = UserRepository(database_path)
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
    audit_repository = AuditRepository(database_path)
    calls["audit_repository"] = audit_repository
    install_audit_hook(
        app,
        repository=audit_repository,
        principal_resolver=session_principal_resolver,
    )

    @app.before_request
    def count_logout_requests():
        if request.path == "/api/auth/logout":
            calls["logout"] += 1

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
            "display_name": "浏览器验证员",
            "permissions": ["situations.read", "situations.write", "catalog.write"],
        })

    @app.get("/api/aircraft-types")
    @app.get("/api/resource-types")
    @app.get("/api/aircraft-resource-requirements")
    def empty_catalog():
        denied = require_principal()
        return denied or jsonify({"items": []})

    @app.get("/api/airports")
    @app.get("/api/missions")
    def empty_paginated_catalog():
        denied = require_principal()
        return denied or jsonify({"items": [], "total": 0})

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


def _login(page, base_url, next_path="/situations"):
    page.goto(f"{base_url}/login?next={next_path}")
    page.locator("#loginName").fill("browser-user")
    page.locator("#loginPassword").fill("Browser-pass-1")
    page.locator("#loginButton").click()
    page.wait_for_url(f"{base_url}{next_path}")
    page.wait_for_selector("#accountTrigger")


def _open_logout(page):
    if page.locator("#accountTrigger").get_attribute("aria-expanded") != "true":
        page.locator("#accountTrigger").click()
    page.locator("#logoutAction").click()


def _beforeunload_is_protected(page):
    return page.evaluate("""() => {
      const event = new Event('beforeunload', {cancelable: true});
      window.dispatchEvent(event);
      return event.defaultPrevented;
    }""")


def _wait_for_beforeunload_guard(page):
    page.wait_for_function("""() => {
      const event = new Event('beforeunload', {cancelable: true});
      window.dispatchEvent(event);
      return event.defaultPrevented;
    }""")


def _wait_for_situation_mount(page):
    page.wait_for_function("""async () => {
      const {state} = await import('/static/js/modules/situation-state.js');
      return Boolean(state.me);
    }""")


def _logout_audit_count(calls):
    records, _ = calls["audit_repository"].query(limit=500)
    return sum(record.request_path == "/api/auth/logout" for record in records)


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


def test_base_data_dirty_logout_cancel_failure_and_retry(browser, auth_server):
    base_url, calls = auth_server
    context = browser.new_context()
    page = context.new_page()
    logout_requests = []
    unload_dialogs = []
    page.on("request", lambda item: logout_requests.append(item.url)
            if item.url.endswith("/api/auth/logout") else None)
    page.on("dialog", lambda dialog: (unload_dialogs.append(dialog.type), dialog.accept()))
    _login(page, base_url, "/base-data")
    page.wait_for_selector("#baseDataTableWrap table")
    page.locator('#baseDataTabs button[data-tab="resource_types"]').click()
    page.wait_for_function("document.querySelector('#baseDataTableTitle').textContent === '保障资源类型'")
    page.locator("#baseDataAddButton").click()
    page.locator("#edResourceName").fill("未保存资源")

    _open_logout(page)
    page.wait_for_selector("#baseDataConfirmModal.open")
    page.locator("#baseDataConfirmCancel").click()
    assert calls["logout"] == 0
    assert _logout_audit_count(calls) == 0
    assert logout_requests == []
    assert page.locator("#edResourceName").input_value() == "未保存资源"
    assert _beforeunload_is_protected(page)

    page.route(
        "**/api/auth/logout",
        lambda route: route.fulfill(
            status=500, content_type="application/json",
            body='{"error":{"message":"temporary logout failure"}}',
        ),
    )
    _open_logout(page)
    page.wait_for_selector("#baseDataConfirmModal.open")
    page.locator("#baseDataConfirmAction").click()
    page.wait_for_selector("#logoutMessage", state="visible")
    assert "退出未完成" in page.locator("#logoutMessage").inner_text()
    assert page.locator("#edResourceName").input_value() == "未保存资源"
    assert _beforeunload_is_protected(page)

    page.unroute("**/api/auth/logout")
    _open_logout(page)
    page.wait_for_selector("#baseDataConfirmModal.open")
    page.locator("#baseDataConfirmAction").click()
    page.wait_for_url("**/login?**")
    assert calls["logout"] == 1
    assert _logout_audit_count(calls) == 1
    assert len(logout_requests) == 2
    assert "beforeunload" not in unload_dialogs
    context.close()


def test_situation_dirty_and_panel_draft_logout_lifecycle(browser, auth_server):
    base_url, calls = auth_server

    for dirty_field in ("dirty", "panelDraftDirty"):
        context = browser.new_context()
        page = context.new_page()
        logout_requests = []
        unload_dialogs = []
        page.on("request", lambda item: logout_requests.append(item.url)
                if item.url.endswith("/api/auth/logout") else None)
        page.on("dialog", lambda dialog: (unload_dialogs.append(dialog.type), dialog.accept()))
        _login(page, base_url)
        page.wait_for_function("() => document.documentElement.dataset.role === 'operator'")
        _wait_for_situation_mount(page)
        page.evaluate("""async (field) => {
          const {state} = await import('/static/js/modules/situation-state.js');
          state[field] = true;
        }""", dirty_field)
        _wait_for_beforeunload_guard(page)

        _open_logout(page)
        page.wait_for_selector("#situationConfirmModal.open")
        page.locator("#situationConfirmCancel").click()
        assert logout_requests == []
        assert page.evaluate("""async (field) => {
          const {state} = await import('/static/js/modules/situation-state.js');
          return state[field];
        }""", dirty_field)
        assert _beforeunload_is_protected(page)

        _open_logout(page)
        page.wait_for_selector("#situationConfirmModal.open")
        page.locator("#situationConfirmAction").click()
        page.wait_for_url("**/login?**")
        assert len(logout_requests) == 1
        assert "beforeunload" not in unload_dialogs
        context.close()

    assert calls["logout"] == 2


def test_situation_save_in_progress_blocks_logout(browser, auth_server):
    base_url, calls = auth_server
    context = browser.new_context()
    page = context.new_page()
    logout_requests = []
    page.on("request", lambda item: logout_requests.append(item.url)
            if item.url.endswith("/api/auth/logout") else None)
    _login(page, base_url)
    _wait_for_situation_mount(page)
    page.evaluate("""async () => {
      const {state} = await import('/static/js/modules/situation-state.js');
      state.saving = true;
    }""")

    _open_logout(page)
    page.wait_for_function("document.querySelector('#situationMessage').textContent.includes('正在保存')")
    assert logout_requests == []
    assert calls["logout"] == 0
    assert page.locator("#logoutAction").is_enabled()
    context.close()


def test_dirty_workspace_navigation_and_direct_leave_guards_remain(browser, auth_server):
    base_url, _ = auth_server
    context = browser.new_context()
    page = context.new_page()
    _login(page, base_url, "/base-data")
    page.wait_for_selector("#baseDataTableWrap table")
    page.locator('#baseDataTabs button[data-tab="resource_types"]').click()
    page.locator("#baseDataAddButton").click()
    page.locator("#edResourceName").fill("导航保护")
    _wait_for_beforeunload_guard(page)
    page.wait_for_timeout(100)

    page.locator('[data-workspace-link][href="/situations"]').click()
    page.wait_for_selector("#baseDataConfirmModal.open")
    page.locator("#baseDataConfirmCancel").click()
    assert page.url.endswith("/base-data")
    assert page.locator("#edResourceName").input_value() == "导航保护"
    assert _beforeunload_is_protected(page)

    page.locator('[data-workspace-link][href="/situations"]').click()
    page.wait_for_selector("#baseDataConfirmModal.open")
    page.locator("#baseDataConfirmAction").click()
    page.wait_for_url("**/situations")
    _wait_for_situation_mount(page)
    page.evaluate("""async () => {
      const {state} = await import('/static/js/modules/situation-state.js');
      state.dirty = true;
    }""")
    _wait_for_beforeunload_guard(page)
    page.locator('[data-workspace-link][href="/base-data"]').click()
    page.wait_for_selector("#situationConfirmModal.open")
    page.locator("#situationConfirmCancel").click()
    assert page.url.endswith("/situations")
    assert _beforeunload_is_protected(page)

    page.locator('[data-workspace-link][href="/base-data"]').click()
    page.wait_for_selector("#situationConfirmModal.open")
    page.locator("#situationConfirmAction").click()
    page.wait_for_url("**/base-data")
    context.close()


def test_network_indeterminate_logout_restores_leave_guard(browser, auth_server):
    base_url, calls = auth_server
    context = browser.new_context()
    page = context.new_page()
    _login(page, base_url)
    _wait_for_situation_mount(page)
    page.evaluate("""async () => {
      const {state} = await import('/static/js/modules/situation-state.js');
      state.dirty = true;
    }""")
    _wait_for_beforeunload_guard(page)
    page.route("**/api/auth/logout", lambda route: route.abort("internetdisconnected"))
    page.route("**/api/me", lambda route: route.abort("internetdisconnected"))

    _open_logout(page)
    page.wait_for_selector("#situationConfirmModal.open")
    page.locator("#situationConfirmAction").click()
    page.wait_for_function("document.querySelector('#logoutMessage').textContent.includes('状态无法确认')")
    assert calls["logout"] == 0
    assert _logout_audit_count(calls) == 0
    assert _beforeunload_is_protected(page)
    assert page.locator("#logoutAction").is_enabled()
    context.close()


def test_repeated_logout_click_sends_one_request_and_one_audit_event(browser, auth_server):
    base_url, calls = auth_server
    context = browser.new_context()
    page = context.new_page()
    requests = []
    page.on("request", lambda item: requests.append(item.url)
            if item.url.endswith("/api/auth/logout") else None)
    _login(page, base_url, "/base-data")
    page.wait_for_selector("#baseDataTableWrap table")
    page.locator("#accountTrigger").click()
    page.evaluate("""() => {
      const button = document.querySelector('#logoutAction');
      button.click();
      button.click();
    }""")
    page.wait_for_url("**/login?**")
    assert len(requests) == 1
    assert calls["logout"] == 1
    assert _logout_audit_count(calls) == 1
    context.close()
