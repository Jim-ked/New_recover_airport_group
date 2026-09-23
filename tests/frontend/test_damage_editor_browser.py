"""Exercise the production damage editor in an isolated real Chromium page.

Only map/panel rendering and HTTP persistence boundaries are stubbed. Editor,
DOM bindings, draft state and generator modules are loaded from the workspace.
"""
from pathlib import Path
import json
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
                instance = runtime.chromium.launch(headless=True, **({"executable_path": str(executable)} if executable else {}))
                break
            except playwright.Error as error:
                failures.append(str(error).splitlines()[0])
        else:
            pytest.skip("No runnable Chromium: " + "; ".join(failures))
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    page = browser.new_page(viewport={"width": 440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    state_source = (MODULES / "situation-state.js").read_text(encoding="utf-8")
    ids = re.findall(r"byId\('([^']+)'\)", state_source)
    body = "".join(f'<{"select" if key == "situationSelect" else "div"} id="{key}"></{"select" if key == "situationSelect" else "div"}>' for key in ids)
    body = body.replace('<div id="situationConfirmCancel"></div>', '<button id="situationConfirmCancel">Cancel</button>').replace('<div id="situationConfirmAction"></div>', '<button id="situationConfirmAction">Confirm</button>')
    html = '<style>.hidden{display:none}</style><main>' + body + '<div class="situation-tools"></div><button id="overviewEditSituationInfo"></button></main><script type="module" src="/situations.js"></script>'

    def serve(route):
        name = route.request.url.rsplit("/", 1)[-1]
        if name in ("", "index.html"):
            route.fulfill(body=html, content_type="text/html")
            return
        if name == "situation-map.js":
            source = "export const " + ",".join(f"{name}=()=>{{}}" for name in ["beginMissionLocationPick", "configureMap", "drawMap", "fitMap", "focusObject", "initMap", "destroyMap"]) + ";"
        elif name == "situation-panels.js":
            source = "export const " + ",".join(f"{name}=()=>{{}}" for name in ["clearConflict", "collapseOverview", "configurePanels", "destroyPanels", "initPanels", "setInspectorOpen", "showConflict", "syncWorkspaceChrome"]) + ";"
        elif name == "api-client.js":
            source = '''export class ApiError extends Error {};
                export async function apiFetch(path, options={}) {
                  if(path.includes('canonicalize')) return {situation:structuredClone(options.body.situation)};
                  if(options.method==='PUT'||options.method==='POST') {window.saved=structuredClone(options.body.situation);return {situation:window.saved,content_hash:'saved'};}
                  if(path.includes('?')) return {items:[]};
                  return {situation:structuredClone(window.saved),content_hash:'saved'};
                }'''
        else:
            path = MODULES / name
            if not path.exists():
                route.abort()
                return
            source = path.read_text(encoding="utf-8")
            if name == "situations.js":
                source += '''\nbindSituationDom(document.querySelector('main')); mounted=true;
                window.editor={state, renderDamageEditor, applyDamagePresetDraft,
                  fill:(replace=false)=>fillDamagePreview(replace), bindPanelDraft,
                  saveSituation, openSituation}; window.ready=true;'''
        route.fulfill(body=source, content_type="text/javascript")

    page.route("http://damage.test/**", serve)
    page.goto("http://damage.test/index.html")
    page.wait_for_function("window.ready===true")
    yield page
    page.close()
    assert not errors, errors


def open_editor(page, category="high", readonly=False, existing=True):
    page.evaluate("""({category,readonly,existing})=>{
      const airport=id=>({airport:{airport_id:id,airport_name:id},configuration_status:'complete',operational_profile:{configuration_complete:true,capacity_per_window:20,departure_capacity_per_window:20,arrival_capacity_per_window:20},aircraft_inventory:[],resource_inventory:[]});
      const event={event_id:'E1',sequence:3,target:{airport_id:'AP001',target_type:'airport',target_id:null},damage_type:'capacity_damage',start_slot:4,end_slot:8,effect:{closed:false,remaining_capacity_per_window:5},recovery_mode:'instant',recovery_duration_slots:null};
      Object.assign(editor.state,{me:{permissions:readonly?[]:['situations.write']},working:{situation_id:'S1',name:'Test',airports:[airport('AP001'),airport('AP002')],missions:[{mission_id:'M1',name:'Task',window_start_slot:3,window_end_slot:30}],damage_scenarios:existing?[{damage_scenario_id:'D1',name:'Historical',category,events:[event]}]:[]},dirty:false,panelDraftDirty:false,persisted:true,mode:'damage'});
      editor.renderDamageEditor(existing?'D1':null);
    }""", {"category": category, "readonly": readonly, "existing": existing})


def generate(page):
    if not page.locator("#damagePresetPanel").evaluate("el=>el.open"):
        page.locator("#damagePresetPanel > summary").click()
    if page.locator("#damagePresetScope").count():
        page.locator("#damagePresetScope").select_option("all")
    else:
        page.locator("#damagePresetAirports").select_option(["AP001", "AP002"])
    page.locator("#damagePresetStart").fill("3")
    if page.locator("#damagePresetEnd").count():
        page.locator("#damagePresetEnd").fill("30")
        page.locator("#damagePresetSeed").fill("browser-seed")
    page.locator("#generateDamagePreview").click()
    page.wait_for_function("!document.getElementById('appendDamagePreview').disabled")


@pytest.mark.parametrize("category", ["low", "medium", "high", "custom"])
def test_historical_category_only_changes_after_apply(page, category):
    open_editor(page, category)
    assert page.locator("#damageScenarioCategory").count() == 0
    assert page.evaluate("editor.state.working.damage_scenarios[0].category") == category
    page.locator("#damageScenarioName").fill("Edited")
    page.locator("#applyDamageScenario").click()
    page.wait_for_function("editor.state.working.damage_scenarios[0].name==='Edited'")
    assert page.evaluate("editor.state.working.damage_scenarios[0].category") == "custom"


@pytest.mark.parametrize("existing", [True, False])
def test_collapsed_tool_preview_does_not_dirty_form(page, existing):
    open_editor(page, existing=existing)
    assert not page.locator("#damagePresetPanel").evaluate("el=>el.open")
    before = page.locator("#damageEventRows").inner_html()
    generate(page)
    assert page.locator("#damageEventRows").inner_html() == before
    assert not page.evaluate("editor.state.panelDraftDirty || editor.state.dirty")
    page.locator("#damagePresetStart").fill("4")
    assert page.locator("#appendDamagePreview").is_disabled()


def test_append_preserves_invalid_input_and_prevents_double_fill(page):
    open_editor(page)
    page.locator(".ev-cap").fill("")
    page.locator(".ev-id").fill(" unfinished id ")
    page.locator(".ev-seq").fill("41")
    page.locator("#damageScenarioName").fill("Pending name")
    generate(page)
    page.locator("#appendDamagePreview").click()
    assert page.locator(".damage-event").count() == 3
    assert page.locator(".ev-cap").first.input_value() == ""
    assert page.locator(".ev-id").first.input_value() == " unfinished id "
    assert page.locator(".ev-seq").first.input_value() == "41"
    assert page.locator("#damageScenarioName").input_value() == "Pending name"
    assert page.evaluate("editor.state.panelDraftDirty")
    ids = page.locator(".ev-id").evaluate_all("els=>els.map(e=>e.value)")
    sequences = page.locator(".ev-seq").evaluate_all("els=>els.map(e=>e.value)")
    assert len(set(ids)) == len(ids)
    assert len(set(sequences)) == len(sequences)
    page.evaluate("editor.fill()")
    assert page.locator(".damage-event").count() == 3


def test_replace_cancel_keeps_every_input_and_confirmation_replaces_only_events(page):
    open_editor(page)
    page.locator(".ev-cap").fill("")
    page.locator("#damageScenarioName").fill("Pending name")
    generate(page)
    before = page.locator("#damageEventRows").inner_html()
    page.locator("#replaceDamagePreview").click()
    page.locator("#situationConfirmCancel").click()
    assert page.locator("#damageEventRows").inner_html() == before
    assert page.locator(".ev-cap").input_value() == ""
    assert page.evaluate("editor.state.panelDraftDirty")
    page.locator("#replaceDamagePreview").click()
    assert "替换" in page.locator("#situationConfirmBody").inner_text()
    page.locator("#situationConfirmAction").click()
    assert page.locator(".damage-event").count() == 2
    assert page.locator("#damageScenarioName").input_value() == "Pending name"
    assert page.locator("#damageScenarioId").input_value() == "D1"


def test_hand_edit_and_capacity_changes_invalidate_preview(page):
    open_editor(page)
    generate(page)
    page.locator(".ev-end").fill("10")
    assert page.locator("#appendDamagePreview").is_disabled()
    generate(page)
    page.evaluate("editor.state.working.airports[0].operational_profile.capacity_per_window=40; editor.fill()")
    assert page.locator(".damage-event").count() == 1
    assert page.locator("#appendDamagePreview").is_disabled()


def test_readonly_hides_tool_and_guards_direct_actions(page):
    open_editor(page, readonly=True)
    assert page.locator("#damagePresetPanel").count() == 0
    before = page.locator("#damageEventRows").inner_html()
    page.evaluate("async()=>{await editor.applyDamagePresetDraft(); await editor.fill();}")
    assert page.locator("#damageEventRows").inner_html() == before
    assert not page.evaluate("editor.state.panelDraftDirty || editor.state.dirty")


def test_apply_save_reopen_edit_cycle(page):
    open_editor(page)
    generate(page)
    page.locator("#appendDamagePreview").click()
    page.locator("#applyDamageScenario").click()
    page.wait_for_function("editor.state.working.damage_scenarios[0].events.length===3")
    page.evaluate("editor.saveSituation()")
    page.evaluate("editor.openSituation('S1',{force:true})")
    page.evaluate("editor.renderDamageEditor('D1')")
    assert page.locator(".damage-event").count() == 3
    page.locator("#damageScenarioName").fill("Second edit")
    page.locator("#applyDamageScenario").click()
    page.wait_for_function("editor.state.working.damage_scenarios[0].name==='Second edit'")



def test_cancel_replacement_preserves_clean_draft_state(page):
    open_editor(page)
    generate(page)
    page.locator("#replaceDamagePreview").click()
    page.locator("#situationConfirmCancel").click()
    assert not page.evaluate("editor.state.panelDraftDirty || editor.state.dirty")
    assert page.locator(".damage-event").count() == 1
    assert not page.locator("#appendDamagePreview").is_disabled()


def test_permission_is_rechecked_after_replacement_confirmation(page):
    open_editor(page)
    generate(page)
    page.locator("#replaceDamagePreview").click()
    page.evaluate("editor.state.me.permissions=[]")
    page.locator("#situationConfirmAction").click()
    assert page.locator(".damage-event").count() == 1
    assert not page.evaluate("editor.state.panelDraftDirty || editor.state.dirty")


def test_repeated_render_and_bind_does_not_duplicate_filled_events(page):
    open_editor(page)
    page.evaluate("for(let i=0;i<4;i++){editor.renderDamageEditor('D1');editor.bindPanelDraft();}")
    generate(page)
    page.locator("#appendDamagePreview").click()
    assert page.locator(".damage-event").count() == 3
    page.locator(".ev-cap").last.fill("7")
    assert page.locator(".ev-cap").last.input_value() == "7"
    assert page.evaluate("editor.state.panelDraftDirty")
