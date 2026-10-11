"""Control response ordering while exercising the real results page and modules."""
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest
from flask import Flask, session
from werkzeug.serving import make_server

from backend.web.flask_ui import create_ui_blueprint

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as runtime:
        for executable in (None, Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                           Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")):
            if executable is not None and not executable.exists():
                continue
            try:
                instance = runtime.chromium.launch(headless=True, **(
                    {"executable_path": str(executable)} if executable else {}))
                break
            except playwright.Error:
                continue
        else:
            pytest.skip("No runnable Chromium")
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def server_url():
    app = Flask(__name__, template_folder=str(ROOT / "frontend/templates"),
                static_folder=str(ROOT / "frontend/static"), static_url_path="/static")
    app.secret_key = "results-initialization-test-only"

    @app.before_request
    def test_session():
        session["user"] = {"user_id": "U1", "role": "operator", "display_name": "Test"}

    app.register_blueprint(create_ui_blueprint())
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=3)


def api_payload(path):
    if path == "/api/me":
        return {"user_id": "U1", "role": "operator", "permissions": []}
    if path.endswith("/overview"):
        return {"airport_ids": ["AP179"], "scenario_keys": ["RUN-TEST"],
                "run_cells": {"AP179": {"RUN-TEST": {
                    "metric_kind": "capacity_availability_v1", "matrix_value": .4,
                    "source_run_id": "RUN-TEST"}}}}
    if path.endswith("/curves"):
        return {"series": [{"source_run_id": "RUN-TEST", "scenario_label": "Test Run",
                            "metric_kind": "capacity_availability_v1", "steps": [1, 2],
                            "values": [.8, 1]}]}
    if path.endswith("/evaluations"):
        return {"items": [{"run_id": "RUN-TEST", "status": "available",
                           "provenance_status": "current", "support_index": .64,
                           "indicators": [{"name": "Test indicator", "raw_value": .8,
                                           "normalized_value": .8, "weight": 1}]}]}
    return {"items": [], "total": 0}


@pytest.fixture
def page_state(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    state = {"page": page, "errors": [], "console": [], "network": [],
             "delay": None, "held": []}
    page.on("pageerror", lambda error: state["errors"].append(str(error)))
    page.on("console", lambda message: state["console"].append(message.text)
            if message.type == "error" and message.location.get("url", "").endswith("results.js") else None)
    page.on("response", lambda response: state["network"].append(response.status)
            if "/api/results/assessment/" in response.url else None)

    def route_api(route):
        path = urlsplit(route.request.url).path
        if path == state["delay"]:
            state["held"].append(route)
        else:
            route.fulfill(status=200, json=api_payload(path))

    page.route("**/api/**", route_api)
    yield state
    context.close()


def release(state):
    assert state["held"], "The intended response must still be held during the switch"
    state["delay"] = None
    for route in state["held"]:
        route.fulfill(status=200, json=api_payload(urlsplit(route.request.url).path))
    state["held"].clear()


def initialized(page):
    playwright.expect(page.locator("#resultsPage")).to_have_attribute("aria-busy", "false")


def assessment_visible(state):
    page = state["page"]
    playwright.expect(page.locator('[data-workspace="assessment"]')).to_have_class("active")
    playwright.expect(page.locator("#assessmentWorkbench")).to_be_visible()
    playwright.expect(page.locator("#assessmentHeatmap tbody tr")).to_have_count(1)
    playwright.expect(page.locator("#assessmentCurves svg")).to_be_visible()
    playwright.expect(page.locator("#assessmentIndicatorTable tbody tr")).to_have_count(1)
    playwright.expect(page.locator("#assessmentComposite")).to_contain_text("64.00")
    assert not state["errors"]
    assert not state["console"]
    assert len(state["network"]) == 4 and all(status == 200 for status in state["network"])


def test_a_assessment_before_initial_response_stays_active(page_state, server_url):
    state = page_state
    page = state["page"]
    state["delay"] = "/api/results/damage-candidates"
    with page.expect_request("**/api/results/damage-candidates"):
        page.goto(server_url + "/results", wait_until="domcontentloaded")
    page.locator('[data-workspace="assessment"]').click()
    assessment_visible(state)
    release(state)
    initialized(page)
    assessment_visible(state)


def test_b_assessment_after_initialization(page_state, server_url):
    page = page_state["page"]
    page.goto(server_url + "/results")
    initialized(page)
    page.locator('[data-workspace="assessment"]').click()
    assessment_visible(page_state)


def test_c_last_damage_choice_survives_initial_response(page_state, server_url):
    state = page_state
    page = state["page"]
    state["delay"] = "/api/results/damage-candidates"
    with page.expect_request("**/api/results/damage-candidates"):
        page.goto(server_url + "/results", wait_until="domcontentloaded")
    page.locator('[data-workspace="assessment"]').click()
    assessment_visible(state)
    page.locator('[data-workspace="damage"]').click()
    release(state)
    initialized(page)
    playwright.expect(page.locator('[data-workspace="damage"]')).to_have_class("active")
    playwright.expect(page.locator("#assessmentWorkbench")).to_be_hidden()
    assert not state["errors"] and not state["console"]


def test_d_late_assessment_response_keeps_comparison_ui(page_state, server_url):
    state = page_state
    page = state["page"]
    page.goto(server_url + "/results")
    initialized(page)
    state["delay"] = "/api/results/assessment/overview"
    with page.expect_request("**/api/results/assessment/overview"):
        page.locator('[data-workspace="assessment"]').click()
    page.locator('[data-workspace="configuration"]').click()
    playwright.expect(page.locator('[data-workspace="configuration"]')).to_have_class("active")
    with page.expect_response("**/api/results/assessment/curves?airport_id=AP179"):
        release(state)
    page.wait_for_load_state("networkidle")
    playwright.expect(page.locator('[data-workspace="configuration"]')).to_have_class("active")
    playwright.expect(page.locator("#assessmentWorkbench")).to_be_hidden()
    playwright.expect(page.locator("#comparisonCondition")).to_be_visible()
    assert not state["errors"] and not state["console"]
    assert len(state["network"]) == 4 and all(status == 200 for status in state["network"])
