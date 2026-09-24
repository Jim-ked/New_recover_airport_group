"""Browser regressions for Situation load/save lifecycle ordering."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from threading import Event, Lock, Thread

import pytest
from flask import Flask, jsonify, request, session
from werkzeug.serving import make_server

from backend.web.flask_ui import create_ui_blueprint

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]
SCREENSHOT_DIR = ROOT / "runtime/temp/situation-lifecycle-browser"


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


def _situation(situation_id, name):
    return {
        "situation_id": situation_id, "name": name, "description": None,
        "airports": [], "missions": [], "damage_scenarios": [],
    }


@pytest.fixture
def lifecycle_server():
    app = Flask(
        "situation-lifecycle-browser",
        template_folder=str(ROOT / "frontend/templates"),
        static_folder=str(ROOT / "frontend/static"),
        static_url_path="/static",
    )
    app.secret_key = "situation-lifecycle-browser-secret"
    app.config["GIS_TILE_TEMPLATE"] = "/tiles/{source}/{z}/{x}/{y}.jpg"
    app.register_blueprint(create_ui_blueprint())
    state = {
        "situations": {
            "ST001": _situation("ST001", "情境一"),
            "ST002": _situation("ST002", "情境二"),
        },
        "hashes": {"ST001": "hash-1", "ST002": "hash-2"},
        "delay_st002": False,
        "fail_next_detail": None,
        "st002_started": Event(),
        "release_st002": Event(),
        "delay_save": False,
        "save_started": Event(),
        "release_save": Event(),
        "save_requests": 0,
        "conflict_next_save": False,
        "fail_next_list": False,
        "list_requests": 0,
        "lock": Lock(),
    }

    @app.before_request
    def authenticate_test_operator():
        session["user"] = {
            "user_id": "lifecycle-operator", "login_name": "lifecycle-operator",
            "display_name": "生命周期验证员", "role": "operator",
        }

    @app.get("/api/me")
    def me():
        return jsonify({
            "user_id": "lifecycle-operator", "role": "operator",
            "display_name": "生命周期验证员",
            "permissions": ["situations.read", "situations.write"],
        })

    @app.get("/api/aircraft-types")
    def aircraft_types():
        return jsonify({"items": []})

    @app.get("/api/resource-types")
    def resource_types():
        return jsonify({"items": []})

    @app.get("/api/situations")
    def list_situations():
        with state["lock"]:
            state["list_requests"] += 1
            if state["fail_next_list"]:
                state["fail_next_list"] = False
                return jsonify({"error": {"message": "list refresh failed"}}), 500
            rows = [
                {"situation_id": item["situation_id"], "name": item["name"]}
                for item in state["situations"].values()
            ]
        return jsonify({"items": rows, "total": len(rows)})

    @app.get("/api/situations/<situation_id>")
    def get_situation(situation_id):
        if state["fail_next_detail"] == situation_id:
            state["fail_next_detail"] = None
            return jsonify({"error": {"message": "detail load failed"}}), 500
        if situation_id == "ST002" and state["delay_st002"]:
            state["st002_started"].set()
            state["release_st002"].wait(timeout=5)
        with state["lock"]:
            item = deepcopy(state["situations"][situation_id])
            content_hash = state["hashes"][situation_id]
        return jsonify({
            "situation": item, "content_hash": content_hash,
            "active_run_count": 0, "historical_run_count": 0,
        })

    @app.post("/api/situations/working-copy/canonicalize")
    def canonicalize():
        return jsonify({"situation": request.json["situation"], "working_copy_hash": "working"})

    @app.put("/api/situations/<situation_id>")
    def save_situation(situation_id):
        with state["lock"]:
            state["save_requests"] += 1
            if state["conflict_next_save"]:
                state["conflict_next_save"] = False
                return jsonify({"error": {"message": "stale working copy"}}), 409
        if state["delay_save"]:
            state["save_started"].set()
            state["release_save"].wait(timeout=5)
        with state["lock"]:
            state["situations"][situation_id] = deepcopy(request.json["situation"])
            state["hashes"][situation_id] = f"saved-{state['save_requests']}"
            payload = deepcopy(state["situations"][situation_id])
            content_hash = state["hashes"][situation_id]
        return jsonify({"situation": payload, "content_hash": content_hash})

    @app.delete("/api/situations/<situation_id>")
    def delete_situation(situation_id):
        with state["lock"]:
            state["situations"].pop(situation_id, None)
            state["hashes"].pop(situation_id, None)
        return jsonify({"situation_id": situation_id, "deleted": True})

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    state["release_st002"].set()
    state["release_save"].set()
    server.shutdown()
    thread.join(timeout=3)


@pytest.fixture
def lifecycle_page(browser, lifecycle_server):
    base_url, state = lifecycle_server
    page = browser.new_page(viewport={"width": 1366, "height": 768})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{base_url}/situations")
    page.wait_for_selector('#situationSelect option[value="ST002"]', state="attached")
    page.wait_for_function("document.querySelector('#situationSelect').value === 'ST001'")
    yield page, state
    page.close()
    assert not errors, errors


def _open_info_editor(page):
    if page.locator("#editSituationName").is_visible():
        return
    if "open" not in (page.locator("#situationOverview").get_attribute("class") or "").split():
        page.locator("#overviewTrigger").click()
    page.locator("#overviewEditSituationInfo").click()


def _edit_name(page, value):
    _open_info_editor(page)
    page.locator("#editSituationName").fill(value)
    page.locator("#applySituationInfo").click()
    page.wait_for_function("document.querySelector('#situationSaveState').textContent.includes('未保存')")


def test_out_of_order_situation_response_cannot_replace_latest_selection(lifecycle_page):
    page, state = lifecycle_page
    state["delay_st002"] = True
    page.locator("#situationSelect").select_option("ST002", no_wait_after=True)
    assert state["st002_started"].wait(timeout=3)
    page.locator("#situationSelect").select_option("ST001", no_wait_after=True)
    page.wait_for_function("document.querySelector('#situationSelect').value === 'ST001'")
    state["release_st002"].set()
    page.wait_for_timeout(300)

    assert page.locator("#situationSelect").input_value() == "ST001"
    page.locator("#overviewTrigger").click()
    page.locator("#overviewEditSituationInfo").click()
    assert page.locator("#editSituationName").input_value() == "情境一"
    page.locator("#cancelSituationInfo").click()
    assert page.locator("#situationSaveState").inner_text() == "已保存"
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / "latest-selection-wins.png"))


def test_detail_failure_restores_selection_and_unmounted_request_cannot_return(lifecycle_page):
    page, state = lifecycle_page
    state["fail_next_detail"] = "ST002"
    page.locator("#situationSelect").select_option("ST002")
    page.wait_for_function("document.querySelector('#situationMessage').textContent.includes('detail load failed')")
    assert page.locator("#situationSelect").input_value() == "ST001"

    state["delay_st002"] = True
    page.locator("#situationSelect").select_option("ST002", no_wait_after=True)
    assert state["st002_started"].wait(timeout=3)
    page.locator('[data-workspace-link][href="/base-data"]').click()
    page.wait_for_url("**/base-data")
    state["release_st002"].set()
    page.wait_for_timeout(250)
    assert page.url.endswith("/base-data")


def test_save_locks_mutations_and_navigation_then_survives_list_refresh_failure(lifecycle_page):
    page, state = lifecycle_page
    _edit_name(page, "情境一已修改")
    state["delay_save"] = True
    state["fail_next_list"] = True
    page.locator("#saveSituationButton").click(no_wait_after=True)
    assert state["save_started"].wait(timeout=3)

    assert page.locator("#situationSelect").is_disabled()
    assert page.locator("#saveSituationButton").is_disabled()
    assert page.locator("#newSituationButton").is_disabled()
    assert page.locator("#editSituationName").is_disabled()
    page.locator('[data-workspace-link][href="/base-data"]').click()
    page.wait_for_timeout(150)
    assert page.url.endswith("/situations")
    assert state["save_requests"] == 1

    state["release_save"].set()
    page.wait_for_function("document.querySelector('#situationSaveState').textContent === '已保存'")
    page.wait_for_selector("#situationMessage", state="visible")
    assert "情境已保存，但列表刷新失败" in page.locator("#situationMessage").inner_text()
    assert page.locator("#situationSelect").input_value() == "ST001"
    assert not page.locator("#situationSelect").is_disabled()
    assert state["situations"]["ST001"]["name"] == "情境一已修改"
    assert state["save_requests"] == 1

    page.get_by_role("button", name="重试刷新").click()
    page.wait_for_function("document.querySelector('#situationMessage').textContent.includes('情境列表已刷新')")
    assert page.locator("#situationSelect").input_value() == "ST001"
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / "saved-list-refresh-recovered.png"))


def test_save_conflict_can_keep_local_or_reload_server_copy(lifecycle_page):
    page, state = lifecycle_page
    _edit_name(page, "本地冲突版本")
    state["conflict_next_save"] = True
    page.locator("#saveSituationButton").click()
    page.wait_for_selector("#situationConflictBar", state="visible")
    assert page.locator("#situationSaveState").inner_text() == "未保存"

    page.locator("#keepLocalConflict").click()
    assert page.locator("#situationConflictBar").is_hidden()
    _edit_name(page, "本地继续修改")
    state["conflict_next_save"] = True
    page.locator("#saveSituationButton").click()
    page.wait_for_selector("#situationConflictBar", state="visible")
    page.locator("#reloadConflict").click()
    page.wait_for_selector("#situationConfirmModal.open")
    page.locator("#situationConfirmAction").click()
    page.wait_for_function("document.querySelector('#situationSaveState').textContent === '已保存'")
    _open_info_editor(page)
    assert page.locator("#editSituationName").input_value() == "情境一"
    page.locator("#cancelSituationInfo").click()


def test_delete_success_is_not_reclassified_when_list_refresh_fails(lifecycle_page):
    page, state = lifecycle_page
    page.locator("#situationSelect").select_option("ST002")
    page.wait_for_function("document.querySelector('#situationSelect').value === 'ST002'")
    state["fail_next_list"] = True
    page.locator("#deleteSituationButton").click()
    page.wait_for_selector("#situationConfirmModal.open")
    page.locator("#situationConfirmAction").click()
    page.wait_for_function("document.querySelector('#situationMessage').textContent.includes('情境已删除，但列表刷新失败')")

    assert page.locator('#situationSelect option[value="ST002"]').count() == 0
    assert "ST002" not in state["situations"]
    page.get_by_role("button", name="重试刷新").click()
    page.wait_for_function("document.querySelector('#situationMessage').textContent.includes('情境列表已刷新')")
