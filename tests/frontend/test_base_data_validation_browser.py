"""Focused browser checks for Base Data editor validation feedback."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

import pytest
from flask import Flask, session
from werkzeug.serving import make_server

from backend.web.flask_ui import create_ui_blueprint


playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as runtime:
        candidates = [
            None,
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ]
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


@pytest.fixture(scope="module")
def ui_server():
    app = Flask(
        "base-data-validation-test",
        template_folder=str(ROOT / "frontend/templates"),
        static_folder=str(ROOT / "frontend/static"),
        static_url_path="/static",
    )
    app.secret_key = "base-data-validation-test-secret"
    app.register_blueprint(create_ui_blueprint())

    @app.before_request
    def authenticate_test_operator():
        session["user"] = {
            "user_id": "validation-operator",
            "login_name": "validation-operator",
            "display_name": "校验测试员",
            "role": "operator",
        }

    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=3)


@pytest.fixture
def editor_page(browser, ui_server):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    state = {"mutation_calls": 0, "reject_resource": True, "resources": []}
    page_errors = []
    console_problems = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "console",
        lambda message: console_problems.append(f"{message.type}: {message.text}")
        if message.type in {"error", "warning"}
        else None,
    )

    def serve_api(route):
        request = route.request
        path = urlparse(request.url).path
        method = request.method.upper()
        status = 200
        if path == "/api/me":
            payload = {
                "user_id": "validation-operator",
                "role": "operator",
                "display_name": "校验测试员",
                "permissions": ["catalog.read", "catalog.write"],
            }
        elif path == "/api/airports":
            if method != "GET":
                state["mutation_calls"] += 1
            payload = {"items": [], "total": 0}
        elif path == "/api/missions":
            payload = {"items": [], "total": 0}
        elif path == "/api/aircraft-types":
            payload = {"items": []}
        elif path == "/api/aircraft-resource-requirements":
            payload = {"items": []}
        elif path == "/api/resource-types" and method == "GET":
            payload = {"items": state["resources"]}
        elif path == "/api/resource-types" and method == "POST":
            state["mutation_calls"] += 1
            posted = json.loads(request.post_data or "{}")
            if state["reject_resource"]:
                status = 422
                payload = {
                    "error": {
                        "code": "VALIDATION_FAILED",
                        "message": "单位不符合后端校验要求",
                        "field": "resource_type.unit",
                    }
                }
            else:
                saved = {
                    "resource_type": posted["resource_type"],
                    "metadata": {"revision": 1, "updated_at": "2026-09-30T00:00:00Z"},
                }
                state["resources"] = [saved]
                payload = saved
                status = 201
        else:
            status = 404
            payload = {"error": {"message": f"Unhandled {method} {path}"}}
        route.fulfill(status=status, body=json.dumps(payload, ensure_ascii=False), content_type="application/json")

    page.route("**/api/**", serve_api)
    page.goto(f"{ui_server}/base-data")
    page.wait_for_selector("#baseDataAddButton:not([disabled])")
    yield page, state
    page.close()
    assert not page_errors, page_errors
    unexpected = [
        message for message in console_problems
        if "422 (Unprocessable Entity)" not in message
    ]
    assert not unexpected, unexpected


def _open_resource_editor(page):
    page.locator('[data-tab="resource_types"]').click()
    page.wait_for_function("document.getElementById('baseDataTableTitle').innerText.includes('资源')")
    page.locator("#baseDataAddButton").click()
    page.wait_for_selector("#edResourceName")


def test_required_fields_show_inline_message_without_calling_backend(editor_page):
    page, state = editor_page
    page.locator("#baseDataAddButton").click()
    page.wait_for_selector("#edAirportName")
    page.locator("#editorSave").click()

    assert page.locator("#edAirportName").locator("xpath=..").locator(".field-error").inner_text() == "此项为必填项"
    assert page.locator("#edLon").locator("xpath=..").locator(".field-error").inner_text() == "此项为必填项"
    assert page.locator("#edLat").locator("xpath=..").locator(".field-error").inner_text() == "此项为必填项"
    assert page.locator("#editorMessage").inner_text() == "保存失败，请检查标红字段"
    assert state["mutation_calls"] == 0


def test_out_of_range_number_shows_short_inline_error(editor_page):
    page, state = editor_page
    page.locator("#baseDataAddButton").click()
    page.wait_for_selector("#edAirportName")
    page.locator("#edAirportName").fill("范围测试机场")
    page.locator("#edLon").fill("181")
    page.locator("#edLat").fill("32")
    page.locator("#editorSave").click()

    assert page.locator("#edLon").locator("xpath=..").locator(".field-error").inner_text() == "请输入 -180 至 180 之间的数值"
    assert page.locator("#edLon").get_attribute("aria-invalid") == "true"
    assert state["mutation_calls"] == 0


def test_backend_validation_error_maps_to_corresponding_field(editor_page):
    page, state = editor_page
    _open_resource_editor(page)
    page.locator("#edResourceId").fill("R-TEST")
    page.locator("#edResourceName").fill("测试资源")
    page.locator("#edResourceUnit").fill("bad-unit")
    page.locator("#editorSave").click()

    page.wait_for_selector("#edResourceUnit[aria-invalid=true]")
    assert page.locator("#edResourceUnit").locator("xpath=..").locator(".field-error").inner_text() == "单位不符合后端校验要求"
    assert page.locator("#editorMessage").inner_text() == "保存失败，请检查标红字段"
    assert state["mutation_calls"] == 1


def test_successful_retry_clears_previous_errors(editor_page):
    page, state = editor_page
    _open_resource_editor(page)
    page.locator("#edResourceId").fill("R-OK")
    page.locator("#edResourceName").fill("可保存资源")
    page.locator("#edResourceUnit").fill("unit")
    page.locator("#editorSave").click()
    page.wait_for_selector("#edResourceUnit[aria-invalid=true]")

    state["reject_resource"] = False
    page.locator("#editorSave").click()
    page.wait_for_function("!document.getElementById('edResourceUnit')")

    assert page.locator(".field-error").count() == 0
    assert page.locator('[aria-invalid="true"]').count() == 0
    assert state["mutation_calls"] == 2
