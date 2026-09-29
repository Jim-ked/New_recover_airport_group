"""Focused browser checks for Run copy and horizontally constrained page tabs."""

from __future__ import annotations

from pathlib import Path
from threading import Thread

import pytest
from flask import Flask, session
from werkzeug.serving import make_server

from backend.web.flask_ui import create_ui_blueprint


playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]
SCREENSHOT_DIR = ROOT / "runtime/temp/run-navigation-responsive"


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
        "run-navigation-responsive",
        template_folder=str(ROOT / "frontend/templates"),
        static_folder=str(ROOT / "frontend/static"),
        static_url_path="/static",
    )
    app.secret_key = "run-navigation-responsive-secret"
    app.config["GIS_TILE_TEMPLATE"] = ""

    @app.before_request
    def authenticate_test_user():
        session["user"] = {
            "user_id": "RESPONSIVE-USER",
            "login_name": "responsive-user",
            "display_name": "界面验证员",
            "role": "operator",
        }

    app.register_blueprint(create_ui_blueprint())
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=3)


def fulfill_api(route):
    path = route.request.url.split("/api/", 1)[-1].split("?", 1)[0]
    if path == "me":
        payload = {
            "user_id": "RESPONSIVE-USER",
            "role": "operator",
            "display_name": "界面验证员",
            "permissions": ["runs.read", "runs.execute", "catalog.read", "catalog.write"],
        }
    elif path == "situations":
        payload = {"items": [{"situation_id": "S1", "name": "三任务六机场演示"}], "total": 1}
    elif path == "situations/S1":
        payload = {
            "situation": {
                "situation_id": "S1",
                "name": "三任务六机场演示",
                "airports": [
                    {
                        "airport": {"airport_id": f"AP{index}", "airport_name": f"机场{index}"},
                        "operational_profile": {
                            "aircraft_support": [
                                {"aircraft_type_id": "fighter"},
                                {"aircraft_type_id": "transport"},
                            ],
                        },
                    }
                    for index in range(1, 7)
                ],
                "missions": [
                    {
                        "mission": {"mission_id": f"M{index}"},
                        "aircraft_requirements": [{"aircraft_type_id": "fighter"}],
                    }
                    for index in range(1, 4)
                ],
                "damage_scenarios": [],
            },
        }
    elif path == "runs/worker-status":
        payload = {"connected": True, "reason": None}
    elif path == "runs/validate":
        payload = {
            "can_submit": True,
            "validated_input_hash": "a" * 64,
            "checks": [{"code": "run_configuration", "status": "passed", "message": "运行配置完整"}],
            "input_summary": {"airport_count": 6, "mission_count": 3, "od_pair_count": 18},
        }
    elif path == "runs":
        payload = {"items": [], "total": 0}
    elif path in {"airports", "missions"}:
        payload = {"items": [], "total": 0}
    elif path in {"aircraft-types", "resource-types", "aircraft-resource-requirements"}:
        payload = {"items": []}
    else:
        payload = {"items": []}
    route.fulfill(status=200, content_type="application/json", json=payload)


def new_page(browser, width, height=900):
    page = browser.new_page(viewport={"width": width, "height": height})
    page.route("**/api/**", fulfill_api)
    return page


@pytest.mark.parametrize("width", [1024, 1280, 1440])
def test_run_page_copy_controls_and_responsive_columns(browser, ui_server, width):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page = new_page(browser, width)
    console_errors = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.goto(f"{ui_server}/run")
    page.wait_for_function("!document.querySelector('#situationSelect').disabled")

    body_text = page.locator("body").inner_text()
    for forbidden in ("当前目标函数", "研究目标函数 v1", "固定15分钟时间粒度", "目标参数配置", "research_objective_v1", "preflight"):
        assert forbidden not in body_text
    assert page.locator("#runPage").get_attribute("data-objective-profile-id") == "research_objective_v1"
    assert page.locator("#advancedSummary").inner_text() == "求解时限 120秒"
    assert page.locator("#submitButton").is_disabled()
    assert page.locator("#submitButton").get_attribute("title") == "请先校验运行条件"

    initial_geometry = page.evaluate("""() => {
      const config = document.querySelector('.config-panel').getBoundingClientRect();
      const runtime = document.querySelector('.runtime').getBoundingClientRect();
      const preference = document.querySelector('#preferenceGroup').getBoundingClientRect();
      const x = preference.left + preference.width / 2;
      const y = preference.top + preference.height / 2;
      const hit = document.elementFromPoint(x, y);
      return {
        config: {left: config.left, right: config.right, top: config.top, bottom: config.bottom, height: config.height},
        runtime: {left: runtime.left, right: runtime.right, top: runtime.top, bottom: runtime.bottom, height: runtime.height},
        preference: {left: preference.left, right: preference.right, top: preference.top, bottom: preference.bottom},
        hit: hit ? `${hit.tagName}.${hit.className}` : null,
      };
    }""")
    page.screenshot(path=str(SCREENSHOT_DIR / f"run-{width}x900-initial.png"), full_page=False)
    if width == 1024:
        assert initial_geometry["runtime"]["top"] >= initial_geometry["config"]["bottom"], initial_geometry

    page.select_option("#situationSelect", "S1")
    page.wait_for_function("!document.querySelector('#damageSelect').disabled")
    page.locator('#preferenceGroup label:has(input[value="sortie_max"])').click()
    page.locator("#validateButton").click()
    page.wait_for_function("!document.querySelector('#submitButton').disabled")
    assert page.locator("#submitButton").get_attribute("title") in {None, ""}
    assert "6机场 · 3任务" in page.locator("#validationSummary").inner_text()
    assert "OD" not in page.locator("#validationSummary").inner_text()

    geometry = page.evaluate("""() => {
      const config = document.querySelector('.config-panel').getBoundingClientRect();
      const runtime = document.querySelector('.runtime').getBoundingClientRect();
      return {
        documentOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        config: {left: config.left, right: config.right, top: config.top, bottom: config.bottom},
        runtime: {left: runtime.left, right: runtime.right, top: runtime.top, bottom: runtime.bottom},
      };
    }""")
    assert not geometry["documentOverflow"]
    if width == 1024:
        assert abs(geometry["config"]["left"] - geometry["runtime"]["left"]) < 2
        assert geometry["runtime"]["top"] >= geometry["config"]["bottom"]
    else:
        assert geometry["runtime"]["left"] >= geometry["config"]["right"]

    page.screenshot(path=str(SCREENSHOT_DIR / f"run-{width}x900.png"), full_page=False)
    assert not console_errors
    page.close()


def test_page_tabs_keep_single_line_and_scroll_inside_a_300px_navigation_area(browser, ui_server):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page = new_page(browser, 1024, 768)
    console_errors = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.goto(f"{ui_server}/base-data")
    page.wait_for_selector("#baseDataTabs")
    page.locator(".workspace-navigation").evaluate(
        "element => { element.style.flex = '0 0 300px'; element.style.width = '300px'; }",
    )

    before = page.evaluate("""() => {
      const nav = document.querySelector('#baseDataTabs');
      return {
        width: nav.getBoundingClientRect().width,
        clientWidth: nav.clientWidth,
        scrollWidth: nav.scrollWidth,
        pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        buttons: [...nav.querySelectorAll('button')].map(button => ({
          text: button.textContent.trim(),
          height: button.getBoundingClientRect().height,
          scrollHeight: button.scrollHeight,
          whiteSpace: getComputedStyle(button).whiteSpace,
          flexShrink: getComputedStyle(button).flexShrink,
        })),
      };
    }""")
    assert before["width"] < 160
    assert before["scrollWidth"] > before["clientWidth"]
    assert not before["pageOverflow"]
    assert all(item["whiteSpace"] == "nowrap" and item["flexShrink"] == "0" for item in before["buttons"])
    assert all(item["scrollHeight"] <= item["height"] + 1 for item in before["buttons"])

    page.locator('#baseDataTabs button[data-tab="resource_types"]').click()
    active = page.locator('#baseDataTabs button[data-tab="resource_types"]')
    assert active.evaluate("element => element.classList.contains('active')")
    assert active.evaluate("element => { const nav = element.parentElement; const a = element.getBoundingClientRect(); const n = nav.getBoundingClientRect(); return a.left >= n.left && a.right <= n.right + 1; }")
    page.screenshot(path=str(SCREENSHOT_DIR / "base-data-tabs-300px.png"), full_page=False)
    assert not console_errors
    page.close()
