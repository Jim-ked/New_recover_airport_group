"""Exercise the Situation mission workspace against the real page and modules."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from threading import Thread

import pytest
from flask import Flask, session
from werkzeug.serving import make_server

from backend.web.flask_ui import create_ui_blueprint


playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]
SCREENSHOT_DIR = ROOT / "runtime/temp/mission-module-browser"


def mission(mission_id: str, name: str, longitude: float, latitude: float, start: int, end: int):
    return {
        "mission_id": mission_id,
        "name": name,
        "longitude": longitude,
        "latitude": latitude,
        "window_start_slot": start,
        "window_end_slot": end,
        "aircraft_requirements": [
            {"aircraft_type_id": "fighter", "required_sorties": 12, "tau_work_windows": 2},
            {"aircraft_type_id": "transport", "required_sorties": 4, "tau_work_windows": 3},
        ],
    }


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


@pytest.fixture(scope="module")
def situation_server():
    app = Flask(
        "mission-workspace-test",
        template_folder=str(ROOT / "frontend/templates"),
        static_folder=str(ROOT / "frontend/static"),
        static_url_path="/static",
    )
    app.secret_key = "mission-workspace-test-secret"
    app.config["GIS_TILE_TEMPLATE"] = "/tiles/{source}/{z}/{x}/{y}.jpg"
    app.register_blueprint(create_ui_blueprint())

    @app.before_request
    def authenticate_test_operator():
        session["user"] = {
            "user_id": "mission-operator", "login_name": "mission-operator",
            "display_name": "任务验证员", "role": "operator",
        }

    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=3)


def initial_store():
    current = [
        mission("M-CURRENT-1", "近距巡逻", 116.2, 39.8, 4, 16),
        mission("M-CURRENT-2", "远程保障", 117.4, 38.9, 18, 42),
    ]
    catalog = [
        {"mission": deepcopy(current[0]), "metadata": {"revision": 3}},
        {"mission": mission("M-CATALOG", "基础库突击", 116.2, 39.8, 8, 28),
         "metadata": {"revision": 1}},
        {"mission": mission("M-REFERENCE-PICK", "参考取点测试", 116.8, 39.35, 8, 28),
         "metadata": {"revision": 1}},
    ]
    history = [
        {"mission": mission("M-HISTORY", "历史侦察", 113.8, 36.4, 2, 22),
         "source_run_id": "RUN-1234567890abcdef", "source_situation_id": "ST-OLD"},
        {"mission": mission("M-CATALOG", "历史同号不同内容", 121.1, 31.2, 30, 50),
         "source_run_id": "RUN-fedcba0987654321", "source_situation_id": "ST-OLD-2"},
    ]
    situation = {
        "situation_id": "ST-MISSIONS", "name": "任务模块浏览器验证", "description": None,
        "airports": [], "missions": current, "damage_scenarios": [],
    }
    return {
        "situation": situation,
        "saved": deepcopy(situation),
        "catalog": catalog,
        "history": history,
        "catalog_reads": 0,
        "history_reads": 0,
        "copy_requests": [],
        "save_requests": [],
    }


def serve_api(route, store):
    request = route.request
    path = request.url.split("?", 1)[0].split("/api", 1)[-1]
    method = request.method
    if path == "/me":
        payload = {"user_id": "mission-operator", "role": "operator",
                   "display_name": "任务验证员", "permissions": ["situations.read", "situations.write"]}
    elif path == "/aircraft-types":
        payload = {"items": [
            {"aircraft_type": {"aircraft_type_id": key, "name": label}}
            for key, label in (("fighter", "战斗机"), ("transport", "运输机"), ("bomber", "轰炸机"))
        ]}
    elif path == "/resource-types":
        payload = {"items": []}
    elif path == "/situations" and method == "GET":
        payload = {"items": [{"situation_id": "ST-MISSIONS", "name": "任务模块浏览器验证"}], "total": 1}
    elif path == "/situations/ST-MISSIONS" and method == "GET":
        payload = {"situation": deepcopy(store["saved"]), "content_hash": "mission-hash",
                   "active_run_count": 0, "historical_run_count": 2}
    elif path == "/situations/ST-MISSIONS" and method == "PUT":
        posted = json.loads(request.post_data or "{}")
        store["save_requests"].append(deepcopy(posted))
        store["saved"] = deepcopy(posted["situation"])
        payload = {"situation": deepcopy(store["saved"]), "content_hash": "mission-hash-saved"}
    elif path == "/missions":
        store["catalog_reads"] += 1
        payload = {"items": deepcopy(store["catalog"]), "total": len(store["catalog"])}
    elif path == "/missions/history":
        store["history_reads"] += 1
        payload = {"items": deepcopy(store["history"])}
    elif path == "/situations/working-copy/canonicalize":
        posted = json.loads(request.post_data or "{}")
        payload = {"situation": deepcopy(posted["situation"]), "working_copy_hash": "working-hash"}
    elif path == "/situations/working-copy/copy-mission":
        posted = json.loads(request.post_data or "{}")
        store["copy_requests"].append(deepcopy(posted))
        copied = deepcopy(posted["situation"])
        if "mission_id" in posted:
            source = next(row["mission"] for row in store["catalog"]
                          if row["mission"]["mission_id"] == posted["mission_id"])
        else:
            source = posted["mission"]
        if any(row["mission_id"] == source["mission_id"] for row in copied["missions"]):
            route.fulfill(status=409, body=json.dumps({"error": {"message": "duplicate mission"}}),
                          content_type="application/json")
            return
        copied["missions"].append(deepcopy(source))
        payload = {"situation": copied, "working_copy_hash": "copy-hash", "persisted": False}
    else:
        route.fulfill(status=404, body=json.dumps({"error": {"message": f"Unhandled {method} {path}"}}),
                      content_type="application/json")
        return
    route.fulfill(body=json.dumps(payload, ensure_ascii=False), content_type="application/json")


@pytest.fixture
def mission_page(browser, situation_server):
    page = browser.new_page(viewport={"width": 1366, "height": 768})
    errors = []
    store = initial_store()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", lambda route: serve_api(route, store))
    page.goto(f"{situation_server}/situations")
    page.wait_for_selector('#situationSelect option[value="ST-MISSIONS"]', state="attached")
    page.wait_for_selector('.situation-mission-marker')
    yield page, store
    page.close()
    assert not errors, errors


@pytest.fixture
def fallback_mission_page(browser, situation_server):
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    errors = []
    store = initial_store()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route(
        "**/static/vendor/leaflet/leaflet.js",
        lambda route: route.fulfill(body="", content_type="text/javascript"),
    )
    page.route("**/api/**", lambda route: serve_api(route, store))
    page.goto(f"{situation_server}/situations")
    page.wait_for_selector('.fallback-object.mission[data-id="M-CURRENT-1"]')
    yield page, store
    page.close()
    assert not errors, errors


def open_missions(page):
    page.locator('[data-mode="mission"]').click()
    page.wait_for_selector('.mission-primary-tabs [data-mission-tab="current"].active')


def rect(page, selector):
    return page.locator(selector).evaluate(
        "el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right}}"
    )


def test_defaults_to_current_situation_without_loading_sources(mission_page):
    page, store = mission_page
    open_missions(page)

    assert page.locator('#missionCurrentList .mission-card').count() == 2
    first = page.locator('.mission-card[data-mission-id="M-CURRENT-1"]')
    assert first.get_by_text("近距巡逻").is_visible()
    assert first.get_by_text("T4–T16").is_visible()
    assert first.get_by_text("战斗机 12 架次").is_visible()
    assert store["catalog_reads"] == 0
    assert store["history_reads"] == 0
    assert page.locator("#saveSituationButton").is_disabled()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / "mission-current-list.png"))


def test_current_list_single_double_detail_and_map_selection_are_synchronized(mission_page):
    page, _ = mission_page
    open_missions(page)

    first = page.locator('.mission-card[data-mission-id="M-CURRENT-1"] .mission-card-main')
    first.click()
    page.wait_for_timeout(260)
    assert page.locator('.mission-card[data-mission-id="M-CURRENT-1"]').get_attribute("aria-current") == "true"
    selected_marker = page.locator('.situation-mission-marker[data-mission-id="M-CURRENT-1"]')
    assert "map-state-selected" in selected_marker.get_attribute("class")
    assert "map-state-focused" not in selected_marker.get_attribute("class")
    assert page.locator("#missionDetailView").count() == 0

    page.locator('.mission-card[data-mission-id="M-CURRENT-2"] .mission-card-main').dblclick()
    page.wait_for_selector("#missionDetailView")
    assert page.get_by_text("远程保障").first.is_visible()
    assert page.locator("#editMission").is_visible()
    page.locator("#backToMissionList").click()
    assert page.locator('.mission-card[data-mission-id="M-CURRENT-2"]').get_attribute("aria-current") == "true"

    page.locator('.situation-mission-marker[data-mission-id="M-CURRENT-2"]').click()
    page.wait_for_selector("#missionDetailView")
    assert page.locator("#inspectorSubtitle").inner_text().startswith("M-CURRENT-2")
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / "mission-map-selected-detail.png"))


def test_catalog_and_history_preview_do_not_dirty_until_explicit_add(mission_page):
    page, store = mission_page
    open_missions(page)
    page.locator('[data-mission-tab="add"]').click()
    page.wait_for_selector('.mission-source-card[data-source-kind="catalog"]')
    assert store["catalog_reads"] == 1
    assert store["history_reads"] == 1
    assert page.locator('[data-source-group="catalog"]').is_visible()
    assert page.locator('[data-source-group="history"]').is_visible()
    assert page.get_by_text("R-12345678").is_visible()

    source = page.locator('.mission-source-card[data-source-kind="catalog"][data-mission-id="M-CATALOG"]')
    source.locator(".mission-card-main").click()
    page.wait_for_timeout(260)
    assert page.locator('.situation-mission-marker.mission-source-preview[data-mission-id="M-CATALOG"]').count() == 1
    assert page.locator("#saveSituationButton").is_disabled()
    assert store["copy_requests"] == []

    source.locator(".mission-card-details").click()
    page.wait_for_selector("#sourceMissionDetail")
    assert page.locator("#addSourceMission").is_enabled()
    assert page.locator("#saveSituationButton").is_disabled()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / "mission-catalog-preview.png"))
    page.locator("#addSourceMission").click()
    page.wait_for_selector('.mission-primary-tabs [data-mission-tab="current"].active')
    assert page.locator('#missionCurrentList .mission-card').count() == 3
    assert page.locator('.mission-card[data-mission-id="M-CATALOG"]').get_attribute("aria-current") == "true"
    assert page.locator("#saveSituationButton").is_enabled()
    assert len(store["copy_requests"]) == 1

    page.locator('[data-mission-tab="add"]').click()
    duplicate = page.locator('.mission-source-card[data-source-kind="catalog"][data-mission-id="M-CURRENT-1"]')
    assert duplicate.get_by_text("已加入").is_visible()
    duplicate.locator(".mission-card-details").click()
    assert page.locator("#addSourceMission").is_disabled()
    assert page.locator("#addSourceMission").inner_text() == "已加入"


def test_new_mission_joins_current_list_without_using_copy_endpoint(mission_page):
    page, store = mission_page
    open_missions(page)
    page.locator('[data-mission-tab="add"]').click()
    page.locator("#newMissionAction").click()

    page.locator("#sitMissionId").fill("M-NEW")
    page.locator("#sitMissionName").fill("新建救援")
    page.locator("#sitMissionLon").fill("119.25")
    page.locator("#sitMissionLat").fill("35.75")
    page.locator("#sitMissionStart").fill("6")
    page.locator("#sitMissionEnd").fill("20")
    page.locator("#sitAddMissionReq").click()
    row = page.locator(".mission-req").last
    row.locator(".row-aircraft").select_option("bomber")
    row.locator(".row-sorties").fill("5")
    row.locator(".row-work").fill("4")
    page.locator("#applyMission").click()

    page.wait_for_selector('.mission-primary-tabs [data-mission-tab="current"].active')
    created = page.locator('.mission-card[data-mission-id="M-NEW"]')
    assert created.count() == 1
    assert created.get_attribute("aria-current") == "true"
    assert created.get_by_text("新建救援").is_visible()
    assert page.locator('.situation-mission-marker[data-mission-id="M-NEW"]').count() == 1
    assert page.locator("#saveSituationButton").is_enabled()
    assert store["copy_requests"] == []


def test_fallback_map_single_click_opens_detail_and_preserves_list_selection(fallback_mission_page):
    page, _ = fallback_mission_page
    open_missions(page)
    page.locator('.fallback-object.mission[data-id="M-CURRENT-1"]').click()
    page.wait_for_selector("#missionDetailView")
    assert page.locator("#inspectorSubtitle").inner_text().startswith("M-CURRENT-1")
    marker = page.locator('.fallback-object.mission[data-id="M-CURRENT-1"]')
    assert "map-state-selected" in marker.get_attribute("class")
    assert "map-state-focused" not in marker.get_attribute("class")
    page.locator("#backToMissionList").click()
    assert page.locator('.mission-card[data-mission-id="M-CURRENT-1"]').get_attribute("aria-current") == "true"


def test_reference_missions_are_lower_and_do_not_capture_location_pick(mission_page):
    page, _ = mission_page
    page.locator("#layerScopeButton").click()
    page.locator("#showAllMissions").check()
    page.wait_for_function("document.querySelectorAll('.catalog-mission-marker').length === 2")
    assert page.locator('.catalog-mission-marker[data-object-id="M-CURRENT-1"]').count() == 0

    current = page.locator('.situation-mission-marker[data-mission-id="M-CURRENT-1"]')
    hit_class = current.evaluate("""element => {
      const rect = element.getBoundingClientRect();
      return document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
        ?.closest('.leaflet-marker-icon')?.className || '';
    }""")
    assert "situation-mission-marker" in hit_class

    open_missions(page)
    page.locator('.mission-card[data-mission-id="M-CURRENT-1"] .mission-card-details').click()
    page.locator("#editMission").click()
    page.locator("#pickMissionLocation").click()
    reference = page.locator('.catalog-mission-marker[data-object-id="M-REFERENCE-PICK"]')
    assert reference.evaluate("element => getComputedStyle(element).pointerEvents") == "none"
    box = reference.bounding_box()
    assert box
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_function("document.getElementById('pickMissionLocation').textContent === '从地图取点'")
    assert page.locator("#sitMissionLon").input_value() != "116.2"
    assert reference.evaluate("element => getComputedStyle(element).pointerEvents") != "none"

    page.locator("#pickMissionLocation").click()
    assert reference.evaluate("element => getComputedStyle(element).pointerEvents") == "none"
    page.locator("#cancelMissionEdit").click()
    assert reference.evaluate("element => getComputedStyle(element).pointerEvents") != "none"


def test_edit_cancel_apply_save_reopen_and_draft_switch_confirmation(mission_page):
    page, store = mission_page
    open_missions(page)
    page.locator('.mission-card[data-mission-id="M-CURRENT-1"] .mission-card-details').click()
    page.locator("#editMission").click()
    page.locator("#sitMissionName").fill("未应用名称")

    page.locator('.situation-mission-marker[data-mission-id="M-CURRENT-2"]').click()
    page.wait_for_selector("#panelDraftWarning")
    assert page.locator("#sitMissionName").input_value() == "未应用名称"
    page.locator("#continuePanelEditing").click()
    assert page.locator("#sitMissionName").input_value() == "未应用名称"

    page.locator("#cancelMissionEdit").click()
    page.wait_for_selector("#missionDetailView")
    assert page.get_by_text("近距巡逻").first.is_visible()

    page.locator("#editMission").click()
    page.locator("#sitMissionName").fill("已应用巡逻")
    page.locator("#applyMission").click()
    page.wait_for_selector("#missionDetailView")
    assert page.get_by_text("已应用巡逻").first.is_visible()
    page.locator("#saveSituationButton").click()
    page.wait_for_function("document.getElementById('situationSaveState').innerText.includes('已保存')")
    assert store["save_requests"][-1]["situation"]["missions"][0]["name"] == "已应用巡逻"

    page.select_option("#situationSelect", "ST-MISSIONS")
    page.wait_for_selector('.situation-mission-marker[data-mission-id="M-CURRENT-1"]')
    open_missions(page)
    assert page.locator('.mission-card[data-mission-id="M-CURRENT-1"]').get_by_text("已应用巡逻").is_visible()


@pytest.mark.parametrize(("width", "height"), ((1792, 915), (1366, 768), (1280, 520)))
def test_editor_footer_is_fixed_visible_and_clickable_at_real_viewports(mission_page, width, height):
    page, _ = mission_page
    page.set_viewport_size({"width": width, "height": height})
    open_missions(page)
    page.locator('.mission-card[data-mission-id="M-CURRENT-1"] .mission-card-details').click()
    page.locator("#editMission").click()

    inspector = rect(page, "#situationInspector")
    for button_id in ("cancelMissionEdit", "applyMission"):
        button = page.locator(f"#{button_id}")
        assert button.is_visible()
        box = rect(page, f"#{button_id}")
        assert 0 <= box["y"] and box["bottom"] <= height
        assert inspector["y"] <= box["y"] and box["bottom"] <= inspector["bottom"]
        assert page.evaluate("""id=>{const el=document.getElementById(id),r=el.getBoundingClientRect();
          return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===el}""", button_id)

    scroll = page.locator(".mission-editor-scroll")
    footer_positions = []
    for ratio in (0, 0.5, 1):
        scroll.evaluate("(el,ratio)=>{el.scrollTop=(el.scrollHeight-el.clientHeight)*ratio}", ratio)
        footer_positions.append(rect(page, ".inspector-footer"))
    assert max(row["y"] for row in footer_positions) - min(row["y"] for row in footer_positions) < 0.5

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / f"mission-editor-ST-MISSIONS-M-CURRENT-1-{width}x{height}.png"))
    page.locator("#applyMission").click()
    page.wait_for_selector("#missionDetailView")
