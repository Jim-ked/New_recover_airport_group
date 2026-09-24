"""Exercise airport list/map activation and the Situation airport editor in Chromium."""

from pathlib import Path
import re

import pytest

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "frontend/static/js/modules"


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
