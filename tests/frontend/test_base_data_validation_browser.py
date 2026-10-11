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
    state = {"mutation_calls": 0, "reject_resource": True, "resources": [],
             "airports": [], "aircraft": [], "airport_updates": [],
             "requirements": [], "aircraft_updates": []}
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
            payload = {"items": [item["airport"] for item in state["airports"]],
                       "total": len(state["airports"])}
        elif path == "/api/airports/AP-TEST" and method in {"GET", "PUT"}:
            saved = state["airports"][0]
            if method == "PUT":
                posted = json.loads(request.post_data or "{}")
                state["airport_updates"].append(posted)
                state["mutation_calls"] += 1
                saved = {"airport": posted["airport"],
                         "operational_profile": posted["operational_profile"],
                         "metadata": {"revision": saved["metadata"]["revision"] + 1}}
                state["airports"] = [saved]
            payload = saved
        elif path == "/api/missions":
            payload = {"items": [], "total": 0}
        elif path == "/api/aircraft-types":
            payload = {"items": state["aircraft"]}
        elif path == "/api/aircraft-types/fighter" and method == "PUT":
            posted = json.loads(request.post_data or "{}")
            saved = {"aircraft_type": posted["aircraft_type"], "metadata": {"revision": 2}}
            state["aircraft"] = [saved]
            payload = saved
        elif path == "/api/aircraft-types/fighter/resource-requirements" and method == "PUT":
            posted = json.loads(request.post_data or "{}")
            state["aircraft_updates"].append(posted)
            state["requirements"] = posted["requirements"]
            payload = {"aircraft_type": state["aircraft"][0]["aircraft_type"], "metadata": {"revision": 3}}
        elif path == "/api/aircraft-resource-requirements":
            payload = {"items": state["requirements"]}
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


def test_airport_operations_editor_switches_adds_removes_and_saves(editor_page):
    page, state = editor_page
    state["airports"] = [{
        "airport": {"airport_id": "AP-TEST", "airport_name": "运行保障测试机场",
                    "facility_type": "medium_airport", "role": "military",
                    "longitude": 116.2, "latitude": 39.8, "runways": []},
        "operational_profile": {"airport_id": "AP-TEST", "capacity_per_window": 8,
                                "support_level": "L2", "emergency_response_level": "level_3",
                                "aircraft_support": [], "resource_stocks": []},
        "metadata": {"revision": 1},
    }]
    state["aircraft"] = [{"aircraft_type": {"aircraft_type_id": "fighter", "name": "战斗机"}}]
    state["resources"] = [{"resource_type": {"resource_type_id": "fuel", "name": "航空燃油"}}]
    page.reload()
    page.locator("#baseDataTableWrap tbody tr").click()
    page.locator("#baseDataEditButton").click()
    basic = page.locator('.airport-data-pane-body[data-airport-section="basic"]')
    operations = page.locator('.airport-data-pane-body[data-airport-section="operations"]')
    playwright.expect(basic).to_be_visible()
    playwright.expect(operations).to_have_count(1)
    playwright.expect(operations).to_be_hidden()
    page.locator('[data-airport-pane="operations"]').click()
    playwright.expect(basic).to_be_hidden()
    playwright.expect(operations).to_be_visible()
    for label in ("容量/时间窗", "应急处理能力", "支持机型", "资源配置"):
        playwright.expect(operations.get_by_text(label, exact=True)).to_be_visible()
    playwright.expect(page.locator("#edCapacity")).to_have_value("8")
    playwright.expect(page.locator("#edEmergencyResponseLevel")).to_have_value("level_3")
    page.locator("#edCapacity").fill("12")
    page.locator("#edEmergencyResponseLevel").select_option("level_4")
    page.locator("#addSupportRow").click()
    page.locator("#addStockRow").click()
    page.locator("#supportRows .row-aircraft").select_option("fighter")
    page.locator("#supportRows .row-initial").fill("5")
    page.locator("#supportRows .row-reset").fill("2")
    page.locator("#stockRows .row-resource").select_option("fuel")
    page.locator("#stockRows .row-stock").fill("125.5")
    page.locator("#addSupportRow").click()
    page.locator("#addStockRow").click()
    playwright.expect(page.locator("#supportRows .support-row")).to_have_count(2)
    playwright.expect(page.locator("#stockRows .stock-row")).to_have_count(2)
    page.locator("#supportRows .remove-row").last.click()
    page.locator("#stockRows .remove-row").last.click()
    playwright.expect(page.locator("#supportRows .support-row")).to_have_count(1)
    playwright.expect(page.locator("#stockRows .stock-row")).to_have_count(1)
    page.locator('[data-airport-pane="basic"]').click()
    playwright.expect(basic).to_be_visible()
    playwright.expect(operations).to_be_hidden()
    page.locator("#editorSave").click()
    playwright.expect(page.locator("#editorSave")).to_have_count(0)
    assert state["mutation_calls"] == 1
    posted = state["airport_updates"][0]
    assert posted["expected_revision"] == 1
    assert posted["operational_profile"] == {
        "airport_id": "AP-TEST", "capacity_per_window": 12, "support_level": "L2",
        "emergency_response_level": "level_4",
        "aircraft_support": [{"aircraft_type_id": "fighter", "initial_quantity": 5, "tau_reset_windows": 2}],
        "resource_stocks": [{"resource_type_id": "fuel", "initial_quantity": 125.5}],
    }
    page.reload()
    page.locator("#baseDataTableWrap tbody tr").click()
    page.locator("#baseDataEditButton").click()
    page.locator('[data-airport-pane="operations"]').click()
    playwright.expect(page.locator("#edCapacity")).to_have_value("12")
    playwright.expect(page.locator("#edEmergencyResponseLevel")).to_have_value("level_4")
    playwright.expect(page.locator("#supportRows .row-aircraft")).to_have_value("fighter")
    playwright.expect(page.locator("#supportRows .row-initial")).to_have_value("5")
    playwright.expect(page.locator("#supportRows .row-reset")).to_have_value("2")
    playwright.expect(page.locator("#stockRows .row-resource")).to_have_value("fuel")
    playwright.expect(page.locator("#stockRows .row-stock")).to_have_value("125.5")


def test_airport_operations_dependency_note_is_inside_operations_pane(editor_page):
    page, state = editor_page
    page.locator("#baseDataAddButton").click()
    operations = page.locator('.airport-data-pane-body[data-airport-section="operations"]')
    playwright.expect(operations).to_have_count(1)
    playwright.expect(operations).to_be_hidden()
    page.locator('[data-airport-pane="operations"]').click()
    playwright.expect(operations.locator(".operations-dependency-note")).to_be_visible()
    playwright.expect(operations).to_contain_text("机型、保障资源基础库为空")
    for control in ("edCapacity", "edEmergencyResponseLevel", "addSupportRow", "addStockRow"):
        playwright.expect(operations.locator(f"#{control}")).to_be_visible()
    assert state["mutation_calls"] == 0


@pytest.mark.parametrize("category", ["fuel", "material", "munition"])
def test_aircraft_resource_basis_follows_category_and_payload(editor_page, category):
    page, state = editor_page
    state["aircraft"] = [{"aircraft_type": {"aircraft_type_id": "fighter", "name": "战斗机"},
                          "metadata": {"revision": 1}}]
    state["resources"] = [{"resource_type": {"resource_type_id": key, "name": key,
                                            "category": key, "unit": "t"}}
                          for key in ("fuel", "material", "munition")]
    expected = "per_hour" if category == "fuel" else "per_sortie"
    # Archived invalid basis must display the category's basis on entering edit.
    state["requirements"] = [{"aircraft_type_id": "fighter", "resource_type_id": category,
                              "basis": "per_sortie" if category == "fuel" else "per_hour", "quantity": 1.5}]
    page.reload()
    page.locator('[data-tab="aircraft_types"]').click()
    page.locator("#baseDataTableWrap tbody tr").click()
    page.locator("#baseDataEditButton").click()
    row = page.locator("#aircraftResRows .aircraft-res-row")
    basis = row.locator(".row-basis")
    playwright.expect(basis).to_have_value(expected)
    playwright.expect(basis.locator("option")).to_have_count(1)
    playwright.expect(basis.locator("option")).to_have_text("每飞行小时" if category == "fuel" else "每架次")
    for resource, value in (("material", "per_sortie"), ("fuel", "per_hour"),
                            ("material", "per_sortie"), ("munition", "per_sortie")):
        row.locator(".row-resource").select_option(resource)
        playwright.expect(basis).to_have_value(value)
        playwright.expect(basis.locator("option")).to_have_count(1)
    page.locator("#addAircraftRes").click()
    added = page.locator("#aircraftResRows .aircraft-res-row").last
    added.locator(".row-resource").select_option("fuel")
    playwright.expect(added.locator(".row-basis")).to_have_value("per_hour")
    added.locator(".row-quantity").fill("2")
    # Submit an intentionally tampered control: payload must still derive from category.
    added.locator(".row-basis").evaluate("el => { el.innerHTML = '<option value=\"per_sortie\">每架次</option>'; }")
    page.locator("#editorSave").click()
    playwright.expect(page.locator("#editorSave")).to_have_count(0)
    assert state["aircraft_updates"][0]["requirements"] == [
        {"aircraft_type_id": "fighter", "resource_type_id": "munition", "basis": "per_sortie", "quantity": 1.5},
        {"aircraft_type_id": "fighter", "resource_type_id": "fuel", "basis": "per_hour", "quantity": 2},
    ]
