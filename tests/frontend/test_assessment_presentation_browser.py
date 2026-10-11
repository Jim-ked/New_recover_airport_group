"""Exercise presentation with 21 stored Runs, long history and real CSS/modules."""
from urllib.parse import urlsplit

import pytest

from tests.frontend.test_results_initialization_browser import (
    api_payload, browser, initialized, playwright, server_url,
)


RUNS = [f"RUN-{i:032x}" for i in range(21)]
LABEL = "完整正式情境 · 重度损毁 · 组群关闭 · sortie_max"
CONCLUSION = "履行未变化；缺口未变化；实际出动 -53；J -39.1997；" * 25


def presentation_payload(path):
    if path.endswith("/overview"):
        return {"airport_ids": [f"AP{i}" for i in range(6)],
                "airport_names": {f"AP{i}": f"Airport {i} International Airport" for i in range(6)},
                "scenario_keys": RUNS, "scenario_labels": {run: LABEL for run in RUNS},
                "run_cells": {f"AP{i}": {run: {
                    "source_run_id": run, "metric_kind": "capacity_availability_v1",
                    "matrix_value": .4, "snapshot_hash": "stored-snapshot",
                    "metrics_hash": "stored-metrics"} for run in RUNS} for i in range(6)}}
    if path.endswith("/curves"):
        return {"series": [{"source_run_id": run, "scenario_label": LABEL,
                            "metric_kind": "capacity_availability_v1", "steps": [9, 14, 19],
                            "values": [.2, .6, .8]} for run in RUNS]}
    if path.endswith("/evaluations"):
        return {"items": [{"run_id": run, "status": "available", "provenance_status": "current",
                           "support_index": .6427, "indicators": [], "weight_source": "ahp",
                           "risk": {"status": "available", "level": 2, "score": .2}}
                          for run in RUNS[:8]]}
    if path.endswith("/comparisons"):
        return {"total": 1, "items": [{"id": "CMP-stored", "comparison_mode": "damage_effect",
                    "base_run_id": RUNS[0], "compared_run_id": RUNS[1], "conclusion": CONCLUSION,
                    "created_by_user_id": "admin", "created_at": "2026-10-08T08:09:57+00:00",
                    "provenance_status": "current", "result_json": {
                        "schema_version": "comparison-record.v1", "focus_comparison": {
                            "conclusions": [CONCLUSION], "values": {
                                "fulfilled_sorties_total": {"baseline": 0, "current": None, "delta": None}},
                            "objective_components": {"J": {"baseline": 0, "current": 1, "delta": None,
                                "definition_comparable": False, "recorded": True, "reasons": ["定义不同"]}}},
                        "pair_facts": {"stored": True}, "eligibility_evidence": {"qualified": True}}}]}
    return api_payload(path)


@pytest.fixture
def presentation(browser, server_url):
    context = browser.new_context(viewport={"width": 1920, "height": 1440})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", lambda route: route.fulfill(status=200, json=presentation_payload(urlsplit(route.request.url).path)))
    page.goto(server_url + "/results")
    initialized(page)
    page.locator('[data-workspace="assessment"]').click()
    playwright.expect(page.locator("#assessmentHeatmap tbody tr")).to_have_count(6)
    yield page
    assert not errors
    context.close()


def test_run_selection_keeps_every_matrix_column_and_changes_only_curve_display(presentation):
    page = presentation
    playwright.expect(page.locator("#assessmentHeatmap thead th[data-run-id]")).to_have_count(21)
    playwright.expect(page.locator("#assessmentHeatmap tbody td")).to_have_count(126)
    playwright.expect(page.locator("#assessmentCurves polyline")).to_have_count(1)
    playwright.expect(page.locator("#assessmentCurveLegend span")).to_have_count(1)
    assert LABEL in page.locator('[data-run-id]').first.get_attribute('title')
    page.locator("#assessmentCurveRunSelect").select_option(RUNS[20])
    playwright.expect(page.locator("#assessmentCurveRunSelect")).to_have_value(RUNS[20])
    assert RUNS[20] in page.locator("#assessmentCurveLegend span").get_attribute("title")
    playwright.expect(page.locator("#assessmentRunSelect")).to_have_value(RUNS[0])
    page.locator("#assessmentRunSelect").select_option(RUNS[1])
    playwright.expect(page.locator("#assessmentCurveRunSelect")).to_have_value("")
    assert RUNS[1] in page.locator("#assessmentCurveLegend span").get_attribute("title")
    playwright.expect(page.locator('[data-run-id][aria-current="true"]')).to_have_attribute("data-run-id", RUNS[1])
    playwright.expect(page.locator("#assessmentCurves svg")).to_contain_text("T9")
    playwright.expect(page.locator("#assessmentCurves svg")).to_contain_text("T19")
    page.locator("#assessmentHeatmap").evaluate("e => e.scrollLeft = 900")
    assert page.locator("#assessmentHeatmap tbody th").first.bounding_box()["x"] < 260


@pytest.mark.parametrize("width,height", [(1920, 1440), (1920, 1080), (1600, 900), (1366, 768)])
def test_responsive_summary_and_expanded_facts_have_no_horizontal_overflow(presentation, width, height):
    page = presentation
    page.set_viewport_size({"width": width, "height": height})
    summary = page.locator(".comparison-record-summary")
    assert summary.get_attribute("title") == CONCLUSION
    assert summary.evaluate("e => getComputedStyle(e).webkitLineClamp") == "2"
    page.locator(".comparison-record-details > summary").click()
    playwright.expect(page.locator(".comparison-record-detail-body")).to_contain_text("不可直接比较")
    playwright.expect(page.locator(".comparison-record-detail-body")).to_contain_text("定义不同")
    playwright.expect(page.locator(".comparison-record-detail-body")).to_contain_text("未记录")
    playwright.expect(page.locator(".comparison-record-detail-body td").first).to_have_text("0")
    assert CONCLUSION in page.locator(".comparison-record-conclusion").inner_text()
    measurements = page.evaluate("""() => ({
        pageWidth: document.documentElement.clientWidth, pageScroll: document.documentElement.scrollWidth,
        comparisons: (() => {const e = document.querySelector('#assessmentComparisons'); return [e.clientWidth, e.scrollWidth]})(),
        detail: (() => {const e = document.querySelector('.comparison-record-detail-body'); return [e.clientWidth, e.scrollWidth]})(),
        mainHeight: document.querySelector('.assessment-grid-main').getBoundingClientRect().height,
        darkScrollbar: getComputedStyle(document.querySelector('#assessmentHeatmap')).scrollbarColor,
        controlsInside: [...document.querySelectorAll('.assessment-toolbar select, .assessment-toolbar button, .assessment-curve-controls select')].every(e => {
          const a = e.getBoundingClientRect(), b = e.closest('header').getBoundingClientRect(); return a.right <= b.right + 1 && a.left >= b.left - 1;
        })})""")
    assert measurements["pageScroll"] <= measurements["pageWidth"] + 1
    assert measurements["comparisons"][1] <= measurements["comparisons"][0] + 1
    assert measurements["detail"][1] <= measurements["detail"][0] + 1
    assert measurements["mainHeight"] == 420
    assert measurements["darkScrollbar"] != "auto"
    assert measurements["controlsInside"]
