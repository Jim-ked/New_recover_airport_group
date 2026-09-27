"""Exercise airport list/map activation and the Situation airport editor in Chromium."""

import json
import os
from pathlib import Path
import re
from threading import Thread

import pytest
from flask import Flask, session
from werkzeug.serving import make_server

from backend.web.flask_ui import create_ui_blueprint

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "frontend/static/js/modules"
SCREENSHOT_DIR = ROOT / "runtime/temp/airport-editor-layout"
DAMAGE_BADGE_EVIDENCE_DIR = ROOT / "docs/verification/damage-badge-2026-09-27"


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


def airport(airport_id, name, *, added=False):
    base = {
        "airport_id": airport_id, "airport_name": name, "facility_type": "medium_airport",
        "role": "military", "icao_code": "ZAAA", "iata_code": None, "region": "CN-11",
        "municipality": "测试市", "longitude": 116.2 if airport_id == "AP001" else 117.3,
        "latitude": 39.8 if airport_id == "AP001" else 40.2, "elevation_m": 42,
        "scheduled_service": False, "parking_stand_count": 12,
        "runway_count": 1, "max_runway_length_m": 2800,
        "runways": [{"runway_id": f"RW-{airport_id}", "length_m": 2800, "width_m": 45,
                     "surface": "CON", "lighted": True, "low_end": None, "high_end": None}],
    }
    profile = {
        "airport_id": airport_id, "configuration_complete": True, "capacity_per_window": 8,
        "support_level": "L2", "emergency_response_level": "level_3",
        "aircraft_support": [], "resource_stocks": [],
    }
    if added:
        return {"airport": base, "operational_profile": profile, "resource_replenishments": []}
    return {"airport": base, "operational_profile": profile, "metadata": {"revision": 1}}


def long_airport():
    bundle = airport("AP190", "Yancheng Nanyang International Airport", added=True)
    bundle["operational_profile"].update({
        "aircraft_support": [
            {"aircraft_type_id": "fighter", "initial_quantity": 12, "tau_reset_windows": 2},
            {"aircraft_type_id": "transport", "initial_quantity": 5, "tau_reset_windows": 3},
            {"aircraft_type_id": "support", "initial_quantity": 4, "tau_reset_windows": 2},
        ],
        "resource_stocks": [
            {"resource_type_id": resource_id, "initial_quantity": 100 + index * 10}
            for index, resource_id in enumerate(("MAT-1", "MAT-2", "MAT-3", "MUN-1", "MUN-2", "fuel"))
        ],
    })
    bundle["resource_replenishments"] = [
        {"resource_type_id": "MUN-2", "start_slot": 30, "end_slot": 34, "quantity": 8},
        {"resource_type_id": "fuel", "start_slot": 36, "end_slot": 42, "quantity": 10},
    ]
    return bundle


@pytest.fixture(scope="module")
def actual_situation_server():
    app = Flask(
        "airport-editor-layout-test",
        template_folder=str(ROOT / "frontend/templates"),
        static_folder=str(ROOT / "frontend/static"),
        static_url_path="/static",
    )
    app.secret_key = "airport-editor-layout-test-secret"
    app.config["GIS_TILE_TEMPLATE"] = "/tiles/{source}/{z}/{x}/{y}.jpg"
    app.register_blueprint(create_ui_blueprint())

    @app.before_request
    def authenticate_test_operator():
        session["user"] = {
            "user_id": "layout-operator", "login_name": "layout-operator",
            "display_name": "布局验证员", "role": "operator",
        }

    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=3)


def serve_layout_api(route, situation):
    request = route.request
    path = request.url.split("?", 1)[0].split("/api", 1)[-1]
    if path == "/me":
        payload = {"user_id": "layout-operator", "role": "operator",
                   "display_name": "布局验证员", "permissions": ["situations.read", "situations.write"]}
    elif path == "/aircraft-types":
        payload = {"items": [
            {"aircraft_type": {"aircraft_type_id": key, "name": label}}
            for key, label in (("fighter", "战斗机"), ("transport", "运输机"), ("support", "保障机"))
        ]}
    elif path == "/resource-types":
        payload = {"items": [
            {"resource_type": {"resource_type_id": key, "name": label}}
            for key, label in (("MAT-1", "通用航材"), ("MAT-2", "动力航材"),
                               ("MAT-3", "电子航材"), ("MUN-1", "通用航弹"),
                               ("MUN-2", "重型航弹"), ("fuel", "航空燃油"))
        ]}
    elif path == "/situations":
        payload = {"items": [{"situation_id": "ST-LAYOUT", "name": "长运行保障表单验证情境"}], "total": 1}
    elif path == "/situations/ST-LAYOUT":
        payload = {"situation": situation, "content_hash": "layout-hash", "active_run_count": 0,
                   "historical_run_count": 0}
    elif path == "/situations/working-copy/canonicalize":
        posted = json.loads(request.post_data or "{}")
        payload = {"situation": posted["situation"], "working_copy_hash": "layout-working-hash"}
    elif path == "/situations/working-copy/copy-airport":
        posted = json.loads(request.post_data or "{}")
        index = int(posted["airport_id"][3:])
        copied = {
            **airport(f"APX{index:03d}", f"Candidate {index:03d}")["airport"],
            "configuration_complete": True,
            "longitude": 100 + (index % 30) * 0.2,
            "latitude": 20 + (index // 30) * 0.2,
        }
        copied_profile = {
            "airport_id": copied["airport_id"], "configuration_complete": True,
            "capacity_per_window": 1, "support_level": "L1",
            "emergency_response_level": "level_1", "aircraft_support": [], "resource_stocks": [],
        }
        updated = json.loads(json.dumps(posted["situation"], ensure_ascii=False))
        updated["airports"].append({"airport": copied, "operational_profile": copied_profile,
                                    "resource_replenishments": []})
        payload = {"situation": updated}
    elif path == "/airports":
        items = [situation["airports"][0]["airport"]]
        items.extend({
            **airport(f"APX{index:03d}", f"Candidate {index:03d}")["airport"],
            "configuration_complete": True,
            "longitude": situation["airports"][0]["airport"]["longitude"]
            if index == 0 else 100 + (index % 30) * 0.2,
            "latitude": situation["airports"][0]["airport"]["latitude"]
            if index == 0 else 20 + (index // 30) * 0.2,
        } for index in range(565))
        offset = int(request.url.split("offset=", 1)[1].split("&", 1)[0]) if "offset=" in request.url else 0
        limit = int(request.url.split("limit=", 1)[1].split("&", 1)[0]) if "limit=" in request.url else 500
        payload = {"items": items[offset:offset + limit], "total": len(items)}
    else:
        route.fulfill(status=404, body=json.dumps({"error": {"message": f"Unhandled {path}"}}),
                      content_type="application/json")
        return
    route.fulfill(body=json.dumps(payload, ensure_ascii=False), content_type="application/json")


@pytest.fixture
def actual_situation_page(browser, actual_situation_server):
    page = browser.new_page()
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    bundle = long_airport()
    situation = {
        "situation_id": "ST-LAYOUT", "name": "长运行保障表单验证情境", "description": None,
        "airports": [bundle], "missions": [{
            "mission_id": "M-LAYOUT", "name": "布局验证任务", "longitude": 117.2,
            "latitude": 33.4, "window_start_slot": 12, "window_end_slot": 48,
            "aircraft_requirements": [],
        }], "damage_scenarios": [],
    }
    page.route("**/api/**", lambda route: serve_layout_api(route, situation))
    page.goto(f"{actual_situation_server}/situations")
    page.wait_for_selector('#situationSelect option[value="ST-LAYOUT"]', state="attached")
    page.wait_for_selector(".situation-airport-marker")
    assert not page_errors, page_errors
    yield page
    page.close()


@pytest.fixture
def page(browser):
    page = browser.new_page(viewport={"width": 520, "height": 520})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    ids = re.findall(r"byId\('([^']+)'\)", (MODULES / "situation-state.js").read_text(encoding="utf-8"))
    body = "".join(
        f'<{"select" if key == "situationSelect" else "div"} id="{key}"></{"select" if key == "situationSelect" else "div"}>'
        for key in ids
    )
    body = body.replace('<div id="inspectorBody"></div>', '<div id="inspectorBody" class="inspector-content"></div>')
    body = body.replace('<div id="situationInspector"></div>', '<aside id="situationInspector" class="situation-inspector overlay-surface"><header><strong id="unused"></strong></header></aside>')
    css = "".join(f'<link rel="stylesheet" href="/{name}.css">' for name in ("tokens", "shell", "components", "situations"))
    html = css + '<style>html,body,main{margin:0;width:100%;height:100%}.situation-inspector{position:fixed!important;top:14px!important;right:12px!important}.hidden{display:none!important}</style><main class="situation-page shell-content">' + body + '<div class="situation-tools"></div><button id="overviewEditSituationInfo"></button><script type="module" src="/situations.js"></script></main>'

    bundles = {"AP001": airport("AP001", "候选机场"), "AP002": airport("AP002", "情境机场")}

    def serve(route):
        name = route.request.url.rsplit("/", 1)[-1]
        if name in ("", "index.html"):
            route.fulfill(body=html, content_type="text/html")
            return
        if name.endswith(".css"):
            route.fulfill(body=(ROOT / "frontend/static/css" / name).read_text(encoding="utf-8"), content_type="text/css")
            return
        if name == "situation-panels.js":
            source = "export const " + ",".join(f"{item}=()=>{{}}" for item in ["clearConflict", "collapseOverview", "configurePanels", "destroyPanels", "initPanels", "setInspectorOpen", "showConflict", "syncWorkspaceChrome"]) + ";"
        elif name == "api-client.js":
            source = """export class ApiError extends Error {};
                export async function apiFetch(path, options={}) {
                  if(path.includes('canonicalize')) return {situation:structuredClone(options.body.situation)};
                  if(path.startsWith('/api/airports/AP')) return structuredClone(window.bundles[path.split('/').pop()]);
                  if(path.startsWith('/api/airports?')) return {items:Object.values(window.bundles).map(x=>({...x.airport,configuration_complete:true})),total:2};
                  return {items:[]};
                }"""
        else:
            path = MODULES / name
            if not path.exists():
                route.abort()
                return
            source = path.read_text(encoding="utf-8")
            if name == "situations.js":
                source += """\nbindSituationDom(document.querySelector('main')); mounted=true;
                  configureMap({selectObject,highlightObject,openCandidateDetails,toggleCandidate,visibleCandidateAirports});
                  window.editor={state,renderAirportCandidates,renderAirportEditor,selectObject,highlightObject,openCandidateDetails,initMap,drawMap};window.ready=true;"""
        route.fulfill(body=source, content_type="text/javascript")

    page.route("http://airport.test/**", serve)
    page.add_init_script("window.bundles=" + __import__("json").dumps(bundles, ensure_ascii=False))
    page.goto("http://airport.test/index.html")
    page.wait_for_function("window.ready===true")
    page.evaluate("""()=>{const inspector=document.getElementById('situationInspector'),header=inspector.querySelector('header');for(const id of ['inspectorTitle','inspectorSubtitle','panelDraftStatus'])header.append(document.getElementById(id));inspector.append(document.getElementById('inspectorBody'));Object.assign(editor.state,{me:{permissions:['situations.write']},working:{situation_id:'S1',name:'测试',airports:[window.bundles.AP002.airport?{airport:window.bundles.AP002.airport,operational_profile:window.bundles.AP002.operational_profile,resource_replenishments:[]}:null],missions:[],damage_scenarios:[{damage_scenario_id:'D1',name:'配置提示',category:'custom',events:[{event_id:'E1',sequence:0,target:{airport_id:'AP002',target_type:'airport',target_id:null},damage_type:'capacity_damage',start_slot:0,end_slot:1,effect:{closed:false,remaining_capacity_per_window:1},recovery_mode:'instant',recovery_duration_slots:null}]}]},airportCatalog:Object.values(window.bundles).map(x=>({...x.airport,configuration_complete:true})),mode:'airport',selected:null,tempAirportIds:new Set(),dirty:false,panelDraftDirty:false});editor.initMap();editor.renderAirportCandidates();}""")
    yield page
    page.close()
    assert not errors, errors


def test_candidate_click_locates_double_click_opens_and_checkbox_only_selects(page):
    row = page.locator('.candidate-row[data-airport-id="AP001"]')
    checkbox = row.locator('input')
    row.click()
    page.wait_for_timeout(260)
    assert page.evaluate("editor.state.candidateFocusId") == "AP001"
    assert not checkbox.is_checked()

    checkbox.check()
    assert page.evaluate("editor.state.tempAirportIds.has('AP001')")
    assert page.evaluate("editor.state.mode") == "airport"

    marker = page.locator('.fallback-object.candidate[data-id="AP001"]')
    marker.click()
    page.wait_for_timeout(260)
    assert page.evaluate("editor.state.candidateFocusId") == "AP001"
    assert checkbox.is_checked()
    marker_class = marker.get_attribute("class")
    assert "candidate-queued" in marker_class
    assert "map-state-focused" in marker_class
    assert "map-state-selected" not in marker_class

    row.dblclick()
    page.wait_for_selector("text=全空域")
    assert page.evaluate("editor.state.mode") == "candidate-detail"
    assert page.locator("#inspectorBody").get_by_text("实际机位").count() == 1


def test_added_airport_double_click_opens_situation_editor_and_footer_stays_visible(page):
    page.evaluate("editor.selectObject('airport','AP002',{locate:true})")
    page.wait_for_selector("#sitEmergencyResponseLevel", state="attached")
    current_marker = page.locator('.fallback-object.airport[data-id="AP002"]')
    assert "map-state-selected" in current_marker.get_attribute("class")
    assert "map-state-focused" not in current_marker.get_attribute("class")
    assert "has-damage-config" in current_marker.get_attribute("class")
    assert page.locator('.airport-detail-tabs [data-airport-pane="basic"]').count() == 1
    page.locator('.airport-detail-tabs [data-airport-pane="operations"]').click()
    assert page.locator("#sitEmergencyResponseLevel").is_visible()
    assert page.locator("#sitEmergencyResponseLevel").input_value() == "level_3"
    assert page.locator("#cancelAirportEdit").is_visible()
    assert page.locator("#applyAirport").is_visible()
    assert page.locator("#restoreAirportBase").count() == 0
    footer_box = page.locator("#applyAirport").bounding_box()
    layout = page.evaluate("""()=>{const x=document.getElementById('situationInspector'),b=document.getElementById('inspectorBody');return {tag:x.tagName,classes:x.className,kind:x.dataset.kind,inspector:x.getBoundingClientRect().toJSON(),body:b.getBoundingClientRect().toJSON(),position:getComputedStyle(x).position,height:getComputedStyle(x).height,bodyDisplay:getComputedStyle(b).display}}""")
    assert footer_box and footer_box["y"] + footer_box["height"] <= 520, layout


def test_fallback_focused_airport_stacks_above_different_mission_at_same_coordinate(page):
    page.evaluate("""async () => {
      const {state} = await import('/situation-state.js');
      const {drawMap} = await import('/situation-map.js');
      const airport = state.working.airports[0].airport;
      state.working.missions = [{
        mission_id: 'M-SAME-COORD', name: '同坐标任务',
        longitude: airport.longitude, latitude: airport.latitude,
        window_start_slot: 0, window_end_slot: 1, aircraft_requirements: [],
      }];
      state.mapFocus = {type: 'airport', id: airport.airport_id};
      document.getElementById('situationInspector').style.display = 'none';
      drawMap();
    }""")
    airport = page.locator('.fallback-object.airport[data-id="AP002"]')
    mission = page.locator('.fallback-object.mission[data-id="M-SAME-COORD"]')
    airport_box = airport.bounding_box()
    assert airport_box is not None
    hit = page.evaluate(
        "({x,y}) => document.elementFromPoint(x,y)?.closest('.fallback-object')?.dataset.type",
        {"x": airport_box["x"] + airport_box["width"] / 2,
         "y": airport_box["y"] + airport_box["height"] / 2},
    )
    assert hit == "airport"
    assert int(airport.evaluate("e => getComputedStyle(e).zIndex")) > int(
        mission.evaluate("e => getComputedStyle(e).zIndex")
    )


def test_read_only_airport_editor_disables_mutations(page):
    page.evaluate("editor.state.me={permissions:[]};editor.renderAirportEditor('AP002')")
    page.locator('.airport-detail-tabs [data-airport-pane="operations"]').click()
    assert page.locator("#sitEmergencyResponseLevel").is_visible()
    assert page.locator("#applyAirport").is_disabled()
    assert page.locator("#removeAirport").is_disabled()
    assert page.locator("#sitEmergencyResponseLevel").is_disabled()


VIEWPORTS = ((1792, 915), (1366, 768), (1280, 520))
FOOTER_BUTTONS = ("cancelAirportEdit", "applyAirport")


def open_actual_airport_editor(page):
    page.locator("#searchToggleButton").click()
    page.locator("#situationSearch").fill("Yancheng")
    page.locator('#situationSearchResults [data-type="airport"][data-id="AP190"]').click()
    page.wait_for_selector("#applyAirport")
    page.locator('[data-airport-pane="operations"]').click()
    page.wait_for_selector("#sitSupportRows .support-row")


def rect(page, selector):
    return page.locator(selector).evaluate(
        "el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right}}"
    )


@pytest.mark.parametrize(("width", "height"), VIEWPORTS)
def test_actual_situation_page_keeps_airport_footer_fixed_and_clickable(
    actual_situation_page, width, height,
):
    page = actual_situation_page
    page.set_viewport_size({"width": width, "height": height})
    open_actual_airport_editor(page)

    inspector = rect(page, "#situationInspector")
    overview = rect(page, "#situationOverview")
    assert overview["right"] <= inspector["x"] - 7
    assert page.locator("#sitConfigComplete").count() == 0
    assert page.locator("#sitSupportLevel").count() == 0
    assert page.locator("#restoreAirportBase").count() == 0
    assert page.locator(".inspector-footer #removeAirport").count() == 0
    initial_values = page.evaluate("""()=>({
      support:[...document.querySelectorAll('.support-row')].map(row=>[row.querySelector('.row-aircraft').value,row.querySelector('.row-initial').value,row.querySelector('.row-reset').value]),
      stocks:[...document.querySelectorAll('.stock-row')].map(row=>[row.querySelector('.row-resource').value,row.querySelector('.row-stock').value]),
      replenish:[...document.querySelectorAll('.replenish-row')].map(row=>[row.querySelector('.row-resource').value,row.querySelector('.row-start').value,row.querySelector('.row-end').value,row.querySelector('.row-qty').value])
    })""")
    assert len(initial_values["support"]) == 3
    assert len(initial_values["stocks"]) == 6
    assert len(initial_values["replenish"]) == 6
    assert ["MUN-2", "30", "34", "8"] in initial_values["replenish"]
    assert ["MAT-1", "12", "48", "0"] in initial_values["replenish"]

    for button_id in FOOTER_BUTTONS:
        button = page.locator(f"#{button_id}")
        assert button.is_visible()
        button_rect = rect(page, f"#{button_id}")
        assert button_rect["y"] >= 0 and button_rect["bottom"] <= height, button_rect
        assert button_rect["y"] >= inspector["y"] and button_rect["bottom"] <= inspector["bottom"], button_rect
        assert page.evaluate("""id=>{const el=document.getElementById(id),r=el.getBoundingClientRect();
          return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===el}""", button_id)

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    image = SCREENSHOT_DIR / f"airport-editor-ST-LAYOUT-AP190-{width}x{height}.png"
    page.screenshot(path=str(image))
    print(f"Browser screenshot: {image}")

    scroll = page.locator(".inspector-scroll")
    scroll_metrics = scroll.evaluate(
        "el=>({clientHeight:el.clientHeight,scrollHeight:el.scrollHeight,overflowY:getComputedStyle(el).overflowY})"
    )
    assert scroll_metrics["scrollHeight"] > scroll_metrics["clientHeight"]
    assert scroll_metrics["overflowY"] == "auto"
    footer_positions = []
    for ratio in (0, 0.5, 1):
        scroll.evaluate("(el,ratio)=>{el.scrollTop=(el.scrollHeight-el.clientHeight)*ratio}", ratio)
        footer_positions.append(rect(page, ".inspector-footer"))
    assert max(item["y"] for item in footer_positions) - min(item["y"] for item in footer_positions) < 0.5
    assert max(item["bottom"] for item in footer_positions) - min(item["bottom"] for item in footer_positions) < 0.5
    print(f"Footer positions {width}x{height}: {json.dumps(footer_positions)}")

    page.locator('[data-airport-pane="basic"]').click()
    assert page.locator("#applyAirport").is_visible()
    page.locator('[data-airport-pane="operations"]').click()
    retained_values = page.evaluate("""()=>({
      support:[...document.querySelectorAll('.support-row')].map(row=>[row.querySelector('.row-aircraft').value,row.querySelector('.row-initial').value,row.querySelector('.row-reset').value]),
      stocks:[...document.querySelectorAll('.stock-row')].map(row=>[row.querySelector('.row-resource').value,row.querySelector('.row-stock').value]),
      replenish:[...document.querySelectorAll('.replenish-row')].map(row=>[row.querySelector('.row-resource').value,row.querySelector('.row-start').value,row.querySelector('.row-end').value,row.querySelector('.row-qty').value])
    })""")
    assert retained_values == initial_values

    page.locator("#applyAirport").click()
    page.wait_for_selector("#applyAirport")
    assert page.locator("#saveSituationButton").is_enabled()


def test_actual_situation_page_preserves_editor_cancel_and_restores_full_overview_width(actual_situation_page):
    page = actual_situation_page
    page.set_viewport_size({"width": 1366, "height": 768})
    open_actual_airport_editor(page)

    page.locator(".replenish-row").first.locator(".row-qty").fill("17")
    page.locator('[data-airport-pane="basic"]').click()
    page.locator('[data-airport-pane="operations"]').click()
    assert page.locator(".replenish-row").first.locator(".row-qty").input_value() == "17"

    page.locator("#cancelAirportEdit").click()
    assert page.locator("#situationInspector").get_attribute("aria-hidden") == "true"
    overview = rect(page, "#situationOverview")
    assert overview["right"] >= 1366 - 15

    open_actual_airport_editor(page)
    assert page.locator(".replenish-row").first.locator(".row-qty").input_value() == "8"


def test_actual_situation_page_edits_multiple_interval_replenishments(actual_situation_page):
    page = actual_situation_page
    page.set_viewport_size({"width": 1366, "height": 768})
    posted = []
    page.on(
        "request",
        lambda request: posted.append(json.loads(request.post_data or "{}"))
        if "/api/situations/working-copy/canonicalize" in request.url
        else None,
    )
    open_actual_airport_editor(page)

    page.locator(".stock-row").first.locator(".row-stock").fill("222")
    page.evaluate("""()=>{
      const row=[...document.querySelectorAll('.replenish-row')]
        .find(item=>item.querySelector('.row-resource').value==='MAT-1');
      row.querySelector('.remove-row').click();
    }""")
    page.locator("#sitAddReplenish").click()
    added = page.locator(".replenish-row").last
    added.locator(".row-resource").select_option("fuel")
    added.locator(".row-start").fill("42")
    added.locator(".row-end").fill("45")
    added.locator(".row-qty").fill("3")

    page.locator('[data-airport-pane="basic"]').click()
    page.locator('[data-airport-pane="operations"]').click()
    added = page.locator(".replenish-row").last
    assert added.locator(".row-resource").input_value() == "fuel"
    assert added.locator(".row-start").input_value() == "42"
    assert added.locator(".row-end").input_value() == "45"
    assert added.locator(".row-qty").input_value() == "3"

    page.locator("#applyAirport").click()
    page.wait_for_selector("#applyAirport")

    airport = posted[-1]["situation"]["airports"][0]
    self_stock = airport["operational_profile"]["resource_stocks"][0]
    assert self_stock == {"resource_type_id": "MAT-1", "initial_quantity": 222}
    assert airport["resource_replenishments"] == [
        {"resource_type_id": "MUN-2", "start_slot": 30, "end_slot": 34, "quantity": 8},
        {"resource_type_id": "fuel", "start_slot": 36, "end_slot": 42, "quantity": 10},
        {"resource_type_id": "fuel", "start_slot": 42, "end_slot": 45, "quantity": 3},
    ]


def test_candidate_map_reuses_current_objects_and_diffs_debounced_search(actual_situation_page):
    page = actual_situation_page
    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("""() => {
      window.__mapPerf = {adds: 0, removes: 0, longTasks: []};
      const marker = L.marker;
      L.marker = (...args) => { window.__mapPerf.adds += 1; return marker(...args); };
      const remove = L.Marker.prototype.remove;
      L.Marker.prototype.remove = function(...args) {
        window.__mapPerf.removes += 1;
        return remove.apply(this, args);
      };
      window.__currentAirportNode = document.querySelector('.situation-airport-marker');
      window.__currentMissionNode = document.querySelector('.situation-mission-marker');
      new PerformanceObserver(list => window.__mapPerf.longTasks.push(
        ...list.getEntries().map(item => item.duration)
      )).observe({type: 'longtask', buffered: true});
    }""")

    started = page.evaluate("performance.now()")
    page.locator('[data-mode="airport"]').click()
    page.wait_for_function("document.querySelectorAll('.situation-candidate-marker').length === 565")
    opened_ms = page.evaluate("start => performance.now() - start", started)
    opened = page.evaluate("""() => ({
      ...window.__mapPerf,
      candidates: document.querySelectorAll('.situation-candidate-marker').length,
      currentAirportPreserved: document.querySelector('.situation-airport-marker') === window.__currentAirportNode,
      currentMissionPreserved: document.querySelector('.situation-mission-marker') === window.__currentMissionNode,
    })""")
    assert opened["adds"] == 565, opened
    assert opened["removes"] == 0, opened
    assert opened["currentAirportPreserved"] and opened["currentMissionPreserved"], opened

    search = page.locator("#airportCandidateSearch")
    search.fill("Candidate 00")
    page.wait_for_timeout(180)
    assert page.locator(".situation-candidate-marker").count() == 10
    narrow = page.evaluate("structuredClone(window.__mapPerf)")
    assert narrow["adds"] == 565
    assert narrow["removes"] == 555
    assert page.evaluate("document.querySelector('.situation-airport-marker') === window.__currentAirportNode")
    assert page.evaluate("document.querySelector('.situation-mission-marker') === window.__currentMissionNode")

    search.fill("Candidate 0")
    page.wait_for_timeout(180)
    assert page.locator(".situation-candidate-marker").count() == 100
    expanded = page.evaluate("structuredClone(window.__mapPerf)")
    assert expanded["adds"] == 655

    search.fill("")
    page.wait_for_function("document.querySelectorAll('.situation-candidate-marker').length === 565")
    cleared = page.evaluate("structuredClone(window.__mapPerf)")
    assert cleared["adds"] == 1120
    page.locator('.candidate-row[data-airport-id="APX000"] input').check()
    page.locator("#addAirportsToSituation").click()
    page.wait_for_function("document.querySelectorAll('.situation-airport-marker').length === 2")
    assert page.locator(".situation-candidate-marker").count() == 564
    print(json.dumps({"open_ms": opened_ms, "opened": opened, "narrow": narrow,
                      "expanded": expanded, "cleared": cleared}, ensure_ascii=False))


def test_reference_airports_exclude_current_and_visible_candidates(actual_situation_page):
    page = actual_situation_page
    page.set_viewport_size({"width": 1440, "height": 900})
    page.locator("#layerScopeButton").click()
    page.locator("#showAllAirports").check()
    page.wait_for_function("document.querySelectorAll('.catalog-airport-marker').length === 565")

    current = page.locator('.situation-airport-marker[data-object-id="AP190"]')
    assert current.count() == 1
    hit_class = current.evaluate("""element => {
      const rect = element.getBoundingClientRect();
      return document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
        ?.closest('.leaflet-marker-icon')?.className || '';
    }""")
    assert "situation-airport-marker" in hit_class
    pane_levels = page.evaluate("""() => ({
      current: Number(getComputedStyle(document.querySelector('.situation-airport-marker').parentElement).zIndex),
      catalog: Number(getComputedStyle(document.querySelector('.catalog-airport-marker').parentElement).zIndex),
    })""")
    assert pane_levels["catalog"] < pane_levels["current"], pane_levels

    page.locator('[data-mode="airport"]').click()
    page.wait_for_function("document.querySelectorAll('.situation-candidate-marker').length === 565")
    assert page.locator(".catalog-airport-marker").count() == 0
    page.locator("#airportCandidateSearch").fill("Candidate 00")
    page.wait_for_function("document.querySelectorAll('.situation-candidate-marker').length === 10")
    page.wait_for_function("document.querySelectorAll('.catalog-airport-marker').length === 555")
    assert page.locator('.catalog-airport-marker[data-object-id="APX000"]').count() == 0

    page.locator("#airportCandidateSearch").fill("")
    page.wait_for_function("document.querySelectorAll('.situation-candidate-marker').length === 565")
    page.wait_for_function("document.querySelectorAll('.catalog-airport-marker').length === 0")

    page.locator('.candidate-row[data-airport-id="APX000"] input').check()
    page.locator("#addAirportsToSituation").click()
    page.wait_for_function("document.querySelectorAll('.situation-airport-marker').length === 2")
    page.wait_for_function("document.querySelectorAll('.situation-candidate-marker').length === 564")
    assert page.locator(".catalog-airport-marker").count() == 0

    page.locator('[data-mode="mission"]').click()
    page.wait_for_function("document.querySelectorAll('.catalog-airport-marker').length === 564")
    assert page.locator('.catalog-airport-marker[data-object-id="APX000"]').count() == 0

    page.locator("#layerScopeButton").click()
    page.locator("#showAllAirports").uncheck()
    assert page.locator(".catalog-airport-marker").count() == 0


def test_current_airport_focus_and_formal_selection_are_distinct(actual_situation_page):
    page = actual_situation_page
    page.evaluate("""async () => {
      const {state} = await import('/static/js/modules/situation-state.js');
      const {drawMap} = await import('/static/js/modules/situation-map.js');
      state.working.damage_scenarios = [{
        damage_scenario_id: 'D-LAYOUT', name: '地图损毁配置', category: 'custom',
        events: [{event_id: 'E-LAYOUT', sequence: 0,
          target: {airport_id: 'AP190', target_type: 'airport', target_id: null},
          damage_type: 'capacity_damage', start_slot: 0, end_slot: 1,
          effect: {closed: false, remaining_capacity_per_window: 4},
          recovery_mode: 'instant', recovery_duration_slots: null}],
      }];
      drawMap();
    }""")
    marker = page.locator('.situation-airport-marker[data-object-id="AP190"]')
    marker.click()
    page.wait_for_timeout(260)
    assert "map-state-focused" in marker.get_attribute("class")
    assert "map-state-selected" not in marker.get_attribute("class")
    assert "has-damage-config" in marker.get_attribute("class")

    marker.dblclick()
    page.wait_for_selector("#applyAirport")
    marker = page.locator('.situation-airport-marker[data-object-id="AP190"]')
    assert "map-state-selected" in marker.get_attribute("class")
    assert "map-state-focused" not in marker.get_attribute("class")
    assert "has-damage-config" in marker.get_attribute("class")
    label = page.locator(".situation-map-label.map-label-selected")
    assert label.count() == 1


def test_damage_badge_is_attached_to_each_airport_role_without_obscuring_selection(
    actual_situation_page,
):
    page = actual_situation_page
    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("""async () => {
      const {state} = await import('/static/js/modules/situation-state.js');
      const {drawMap, fitMap} = await import('/static/js/modules/situation-map.js');
      const seed = state.working.airports[0];
      const roles = ['civil', 'military', 'joint'];
      state.working.missions = [];
      state.mapFocus = null;
      state.working.airports = roles.flatMap((role, roleIndex) => ['plain', 'damage'].map((kind, kindIndex) => ({
        ...structuredClone(seed),
        airport: {
          ...structuredClone(seed.airport),
          airport_id: `BADGE-${role}-${kind}`,
          airport_name: `${role} ${kind}`,
          role,
          longitude: 110 + roleIndex * 4,
          latitude: 30 + kindIndex * 4,
        },
      })));
      state.working.damage_scenarios = [{
        damage_scenario_id: 'D-BADGE', name: '角标视觉验证', category: 'custom',
        events: roles.map((role, index) => ({
          event_id: `E-BADGE-${role}`, sequence: index,
          target: {airport_id: `BADGE-${role}-damage`, target_type: 'airport', target_id: null},
          damage_type: 'capacity_damage', start_slot: 0, end_slot: 1,
          effect: {closed: false, remaining_capacity_per_window: 4},
          recovery_mode: 'instant', recovery_duration_slots: null,
        })),
      }];
      state.selected = {type: 'airport', id: 'BADGE-civil-damage'};
      drawMap();
      fitMap();
    }""")
    page.wait_for_function("document.querySelectorAll('.situation-airport-marker').length === 6")

    capture_prefix = os.environ.get("MAP_DAMAGE_BADGE_EVIDENCE")
    if capture_prefix:
        DAMAGE_BADGE_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        page.locator("#situationMap").screenshot(
            path=str(DAMAGE_BADGE_EVIDENCE_DIR / f"{capture_prefix}-full-map.png")
        )

    expected_shapes = {
        "civil": {"radius": "50%", "clipped": False},
        "military": {"radius": "0px", "clipped": True},
        "joint": {"radius": "0px", "clipped": True},
    }
    for role, expected in expected_shapes.items():
        for selected_kind in ("damage", "plain"):
            page.evaluate("""async ({role, selectedKind}) => {
              const {state} = await import('/static/js/modules/situation-state.js');
              const {drawMap} = await import('/static/js/modules/situation-map.js');
              state.selected = {type: 'airport', id: `BADGE-${role}-${selectedKind}`};
              drawMap();
            }""", {"role": role, "selectedKind": selected_kind})
            selected = page.locator(
                f'.situation-airport-marker[data-object-id="BADGE-{role}-{selected_kind}"]'
            )
            damage = page.locator(
                f'.situation-airport-marker[data-object-id="BADGE-{role}-damage"]'
            )
            assert "map-state-selected" in selected.get_attribute("class")
            assert ("has-damage-config" in selected.get_attribute("class")) == (
                selected_kind == "damage"
            )
            shape = selected.locator("span").evaluate("""node => {
              const style = getComputedStyle(node);
              return {width: style.width, height: style.height,
                radius: style.borderRadius, clip: style.clipPath};
            }""")
            assert shape["width"] == shape["height"] == "13px"
            assert shape["radius"] == expected["radius"]
            assert (shape["clip"] != "none") == expected["clipped"]

            if capture_prefix and selected_kind == "damage":
                marker_box = selected.bounding_box()
                page.screenshot(
                    path=str(DAMAGE_BADGE_EVIDENCE_DIR / f"{capture_prefix}-{role}-selected-damage-local.png"),
                    clip={
                        "x": max(0, marker_box["x"] - 24),
                        "y": max(0, marker_box["y"] - 24),
                        "width": 220,
                        "height": 72,
                    },
                )

            badge = damage.evaluate("""node => {
              const style = getComputedStyle(node, '::after');
              const marker = node.getBoundingClientRect();
              const shape = node.querySelector('span').getBoundingClientRect();
              const left = Number.parseFloat(style.left);
              const top = Number.parseFloat(style.top);
              const width = Number.parseFloat(style.width);
              const height = Number.parseFloat(style.height);
              const hit = document.elementFromPoint(
                marker.left + left + width / 2,
                marker.top + top + height / 2,
              );
              return {
                width: style.width, height: style.height,
                radius: style.borderRadius, transform: style.transform,
                background: style.backgroundColor, border: style.borderColor,
                pointerEvents: style.pointerEvents, boxSizing: style.boxSizing,
                hitIsMarker: hit === node || hit.closest('.situation-airport-marker') === node,
                overlapX: Math.min(left + width, shape.right - marker.left) - Math.max(left, shape.left - marker.left),
                overlapY: Math.min(top + height, shape.bottom - marker.top) - Math.max(top, shape.top - marker.top),
              };
            }""")
            assert badge["width"] == badge["height"] == "6px"
            assert badge["radius"] == "50%"
            assert badge["transform"] == "none"
            assert badge["background"] == "rgb(239, 91, 52)"
            assert badge["border"] == "rgb(71, 32, 22)"
            assert badge["pointerEvents"] == "none"
            assert badge["boxSizing"] == "border-box"
            assert badge["hitIsMarker"]
            assert 0 < badge["overlapX"] < 4
            assert 0 < badge["overlapY"] < 4

    page.wait_for_timeout(300)  # Allow Leaflet's removed tooltip fade transition to finish.
    selected = page.locator('.situation-airport-marker.map-state-selected')
    selected_box = selected.bounding_box()
    selected_label_box = page.get_by_role("tooltip", name="joint plain", exact=True).bounding_box()
    assert selected_label_box["x"] >= selected_box["x"] + 23

    page.evaluate("""async () => {
      const {state} = await import('/static/js/modules/situation-state.js');
      const {drawMap, setCatalogLayer} = await import('/static/js/modules/situation-map.js');
      state.selected = {type: 'airport', id: 'BADGE-joint-damage'};
      drawMap();
      await setCatalogLayer('airports', true);
    }""")
    page.wait_for_function("document.querySelectorAll('.catalog-airport-marker').length > 500")
    selected = page.locator('.situation-airport-marker[data-object-id="BADGE-joint-damage"]')
    assert "map-state-selected" in selected.get_attribute("class")
    hit_class = selected.evaluate("""node => {
      const rect = node.getBoundingClientRect();
      return document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
        ?.closest('.leaflet-marker-icon')?.className || '';
    }""")
    assert "situation-airport-marker" in hit_class

    if capture_prefix:
        page.locator("#situationMap").screenshot(
            path=str(DAMAGE_BADGE_EVIDENCE_DIR / f"{capture_prefix}-dense-reference-full-map-dpr1.png")
        )


def test_damage_badge_renders_at_device_scale_factor_two(browser, actual_situation_server):
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        device_scale_factor=2,
    )
    page = context.new_page()
    bundle = long_airport()
    bundle["airport"]["role"] = "joint"
    situation = {
        "situation_id": "ST-LAYOUT", "name": "DPR 2 角标验证", "description": None,
        "airports": [bundle], "missions": [],
        "damage_scenarios": [{
            "damage_scenario_id": "D-DPR2", "name": "角标验证", "category": "custom",
            "events": [{
                "event_id": "E-DPR2", "sequence": 0,
                "target": {"airport_id": "AP190", "target_type": "airport", "target_id": None},
                "damage_type": "capacity_damage", "start_slot": 0, "end_slot": 1,
                "effect": {"closed": False, "remaining_capacity_per_window": 4},
                "recovery_mode": "instant", "recovery_duration_slots": None,
            }],
        }],
    }
    page.route("**/api/**", lambda route: serve_layout_api(route, situation))
    page.goto(f"{actual_situation_server}/situations")
    page.wait_for_selector('.situation-airport-marker.has-damage-config')
    assert page.evaluate("window.devicePixelRatio") == 2
    marker = page.locator('.situation-airport-marker.has-damage-config')
    styles = marker.evaluate("""node => {
      const badge = getComputedStyle(node, '::after');
      const shape = getComputedStyle(node.querySelector('span'));
      return {
        badge: [badge.width, badge.height, badge.borderRadius, badge.transform],
        shape: [shape.width, shape.height, shape.clipPath],
      };
    }""")
    assert styles["badge"] == ["6px", "6px", "50%", "none"]
    assert styles["shape"][:2] == ["13px", "13px"]
    assert styles["shape"][2] != "none"

    capture_prefix = os.environ.get("MAP_DAMAGE_BADGE_EVIDENCE")
    if capture_prefix:
        DAMAGE_BADGE_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        page.locator("#situationMap").screenshot(
            path=str(DAMAGE_BADGE_EVIDENCE_DIR / f"{capture_prefix}-full-map-dpr2.png")
        )
        marker_box = marker.bounding_box()
        page.screenshot(
            path=str(DAMAGE_BADGE_EVIDENCE_DIR / f"{capture_prefix}-joint-damage-local-dpr2.png"),
            clip={
                "x": max(0, marker_box["x"] - 24),
                "y": max(0, marker_box["y"] - 24),
                "width": 220,
                "height": 72,
            },
        )
    context.close()


def test_fallback_damage_badge_matches_leaflet_and_keeps_single_hit_target(page):
    marker = page.locator('.fallback-object.airport[data-id="AP002"]')
    assert "has-damage-config" in marker.get_attribute("class")
    result = marker.evaluate("""node => {
      const style = getComputedStyle(node, '::after');
      const rect = node.getBoundingClientRect();
      const x = rect.left + Number.parseFloat(style.left) + Number.parseFloat(style.width) / 2;
      const y = rect.top + Number.parseFloat(style.top) + Number.parseFloat(style.height) / 2;
      const hit = document.elementFromPoint(x, y);
      return {
        width: style.width, height: style.height, radius: style.borderRadius,
        transform: style.transform, background: style.backgroundColor,
        border: style.borderColor, pointerEvents: style.pointerEvents,
        boxSizing: style.boxSizing,
        hitIsAirportButton: hit === node || hit.closest('.fallback-object') === node,
      };
    }""")
    assert result == {
        "width": "6px", "height": "6px", "radius": "50%", "transform": "none",
        "background": "rgb(239, 91, 52)", "border": "rgb(71, 32, 22)",
        "pointerEvents": "none", "boxSizing": "border-box", "hitIsAirportButton": True,
    }


def test_airport_marker_spec_renders_three_roles_sources_and_legend(actual_situation_page):
    page = actual_situation_page
    page.evaluate("""async () => {
      const {state} = await import('/static/js/modules/situation-state.js');
      const {drawMap, setCatalogLayer} = await import('/static/js/modules/situation-map.js');
      const seed = state.working.airports[0];
      state.working.airports = ['civil', 'military', 'joint'].map((role, index) => ({
        ...structuredClone(seed),
        airport: {
          ...structuredClone(seed.airport),
          airport_id: `ROLE-${role}`,
          airport_name: `Role ${role}`,
          role,
          longitude: 112 + index * 3,
          latitude: 30 + index * 2,
        },
      }));
      drawMap();
      await setCatalogLayer('airports', true);
    }""")

    rendered = page.locator(".situation-airport-marker")
    assert rendered.count() == 3
    styles = page.locator(".situation-airport-marker").evaluate_all("""nodes => nodes.map(node => {
      const body = getComputedStyle(node.querySelector('span'));
      const container = getComputedStyle(node);
      return {
        classes: node.className,
        container: [container.width, container.height],
        body: [body.width, body.height],
        fill: body.backgroundColor,
        radius: body.borderRadius,
        clip: body.clipPath,
      };
    })""")
    assert all(row["container"] == ["24px", "24px"] for row in styles)
    assert all(row["body"] == ["13px", "13px"] for row in styles)
    assert len({row["fill"] for row in styles}) == 3
    by_role = {next(role for role in ("civil", "military", "joint") if f"airport-role-{role}" in row["classes"]): row
               for row in styles}
    assert by_role["civil"]["radius"] == "50%"
    assert by_role["military"]["clip"] != "none"
    assert by_role["joint"]["clip"] != "none"

    reference = page.locator(".catalog-airport-marker").first
    reference_style = reference.evaluate("""node => ({
      container: [getComputedStyle(node).width, getComputedStyle(node).height],
      body: [getComputedStyle(node.querySelector('span')).width, getComputedStyle(node.querySelector('span')).height],
    })""")
    assert reference_style == {"container": ["16px", "16px"], "body": ["8px", "8px"]}

    page.locator('[data-mode="airport"]').click()
    page.wait_for_function("document.querySelectorAll('.situation-candidate-marker').length > 500")
    candidate_style = page.locator(".situation-candidate-marker").first.evaluate("""node => ({
      container: [getComputedStyle(node).width, getComputedStyle(node).height],
      body: [getComputedStyle(node.querySelector('span')).width, getComputedStyle(node.querySelector('span')).height],
    })""")
    assert candidate_style == {"container": ["20px", "20px"], "body": ["11px", "11px"]}

    page.locator("#layerScopeButton").click()
    legend = page.locator(".map-legend")
    assert legend.is_visible()
    for label in (
        "机场类别", "民用", "军用", "军民两用", "当前情境", "待加入候选", "基础参考",
        "损毁事件配置（非实时受损）",
    ):
        assert label in legend.inner_text()


def test_actual_situation_page_preserves_remove_confirmation_path(actual_situation_page):
    page = actual_situation_page
    page.set_viewport_size({"width": 1366, "height": 768})
    open_actual_airport_editor(page)

    page.locator('[data-airport-pane="basic"]').click()
    page.locator("#removeAirport").click()
    page.wait_for_function("document.getElementById('situationConfirmBody')?.innerText.includes('190')")
    remove_confirmation = page.evaluate("""()=>({
      body:document.getElementById('situationConfirmBody').innerText,
      action:document.getElementById('situationConfirmAction').innerText,
      classes:document.getElementById('situationConfirmModal').className
    })""")
    assert "190" in remove_confirmation["body"], remove_confirmation
    assert remove_confirmation["action"], remove_confirmation
    page.locator("#situationConfirmCancel").click()
    assert page.locator("#applyAirport").is_visible()
