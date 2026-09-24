"""Exercise airport list/map activation and the Situation airport editor in Chromium."""

import json
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
            {"resource_type_id": resource_id, "initial_quantity": 100 + index * 10,
             "replenishment_capacity_per_window": 6 + index}
            for index, resource_id in enumerate(("MAT-1", "MAT-2", "MAT-3", "MUN-1", "MUN-2", "fuel"))
        ],
    })
    bundle["resource_replenishments"] = [
        {"resource_type_id": "MUN-2", "slot": 30, "quantity": 8},
        {"resource_type_id": "fuel", "slot": 36, "quantity": 10},
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
        "airports": [bundle], "missions": [], "damage_scenarios": [],
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
    page.evaluate("""()=>{const inspector=document.getElementById('situationInspector'),header=inspector.querySelector('header');for(const id of ['inspectorTitle','inspectorSubtitle','panelDraftStatus'])header.append(document.getElementById(id));inspector.append(document.getElementById('inspectorBody'));Object.assign(editor.state,{me:{permissions:['situations.write']},working:{situation_id:'S1',name:'测试',airports:[window.bundles.AP002.airport?{airport:window.bundles.AP002.airport,operational_profile:window.bundles.AP002.operational_profile,resource_replenishments:[]}:null],missions:[],damage_scenarios:[]},airportCatalog:Object.values(window.bundles).map(x=>({...x.airport,configuration_complete:true})),mode:'airport',selected:null,tempAirportIds:new Set(),dirty:false,panelDraftDirty:false});editor.initMap();editor.renderAirportCandidates();}""")
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

    row.dblclick()
    page.wait_for_selector("text=全空域（仿真假设）")
    assert page.evaluate("editor.state.mode") == "candidate-detail"
    assert page.locator("#inspectorBody").get_by_text("实际机位").count() == 1


def test_added_airport_double_click_opens_situation_editor_and_footer_stays_visible(page):
    page.evaluate("editor.selectObject('airport','AP002',{locate:true})")
    page.wait_for_selector("#sitEmergencyResponseLevel", state="attached")
    assert page.locator('.airport-detail-tabs [data-airport-pane="basic"]').count() == 1
    page.locator('.airport-detail-tabs [data-airport-pane="operations"]').click()
    assert page.locator("#sitEmergencyResponseLevel").is_visible()
    assert page.locator("#sitEmergencyResponseLevel").input_value() == "level_3"
    assert page.locator("#cancelAirportEdit").is_visible()
    assert page.locator("#applyAirport").is_visible()
    footer_box = page.locator("#applyAirport").bounding_box()
    layout = page.evaluate("""()=>{const x=document.getElementById('situationInspector'),b=document.getElementById('inspectorBody');return {tag:x.tagName,classes:x.className,kind:x.dataset.kind,inspector:x.getBoundingClientRect().toJSON(),body:b.getBoundingClientRect().toJSON(),position:getComputedStyle(x).position,height:getComputedStyle(x).height,bodyDisplay:getComputedStyle(b).display}}""")
    assert footer_box and footer_box["y"] + footer_box["height"] <= 520, layout


def test_read_only_airport_editor_disables_mutations(page):
    page.evaluate("editor.state.me={permissions:[]};editor.renderAirportEditor('AP002')")
    page.locator('.airport-detail-tabs [data-airport-pane="operations"]').click()
    assert page.locator("#sitEmergencyResponseLevel").is_visible()
    assert page.locator("#applyAirport").is_disabled()
    assert page.locator("#removeAirport").is_disabled()
    assert page.locator("#sitEmergencyResponseLevel").is_disabled()


VIEWPORTS = ((1792, 915), (1366, 768), (1280, 520))
FOOTER_BUTTONS = ("cancelAirportEdit", "restoreAirportBase", "removeAirport", "applyAirport")


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
    initial_values = page.evaluate("""()=>({
      support:[...document.querySelectorAll('.support-row')].map(row=>[row.querySelector('.row-aircraft').value,row.querySelector('.row-initial').value,row.querySelector('.row-reset').value]),
      stocks:[...document.querySelectorAll('.stock-row')].map(row=>[row.querySelector('.row-resource').value,row.querySelector('.row-stock').value,row.querySelector('.row-cap').value]),
      replenish:[...document.querySelectorAll('.replenish-row')].map(row=>[row.querySelector('.row-resource').value,row.querySelector('.row-slot').value,row.querySelector('.row-qty').value])
    })""")
    assert len(initial_values["support"]) == 3
    assert len(initial_values["stocks"]) == 6
    assert len(initial_values["replenish"]) == 2

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
      stocks:[...document.querySelectorAll('.stock-row')].map(row=>[row.querySelector('.row-resource').value,row.querySelector('.row-stock').value,row.querySelector('.row-cap').value]),
      replenish:[...document.querySelectorAll('.replenish-row')].map(row=>[row.querySelector('.row-resource').value,row.querySelector('.row-slot').value,row.querySelector('.row-qty').value])
    })""")
    assert retained_values == initial_values

    page.locator("#applyAirport").click()
    page.wait_for_selector("#applyAirport")
    assert page.locator("#saveSituationButton").is_enabled()


def test_actual_situation_page_preserves_restore_and_editor_cancel_paths(actual_situation_page):
    page = actual_situation_page
    page.set_viewport_size({"width": 1366, "height": 768})
    open_actual_airport_editor(page)

    page.locator("#restoreAirportBase").click()
    assert page.locator("#situationConfirmModal").get_attribute("aria-hidden") == "false"
    assert "替换" in page.locator("#situationConfirmBody").inner_text()
    page.locator("#situationConfirmCancel").click()
    page.wait_for_function("document.getElementById('situationConfirmModal')?.getAttribute('aria-hidden')==='true'")
    assert page.locator("#sitSupportRows .support-row").count() == 3

    page.locator("#cancelAirportEdit").click()
    assert page.locator("#situationInspector").get_attribute("aria-hidden") == "true"


def test_actual_situation_page_preserves_remove_confirmation_path(actual_situation_page):
    page = actual_situation_page
    page.set_viewport_size({"width": 1366, "height": 768})
    open_actual_airport_editor(page)

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
