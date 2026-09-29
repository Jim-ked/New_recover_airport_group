from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "frontend/static/js/modules"
SITUATION_JS = (MODULES / "situation-map.js").read_text(encoding="utf-8")
RUNTIME_JS = (MODULES / "gis-runtime.js").read_text(encoding="utf-8")
AIRPORT_DISPLAY = (MODULES / "airport-display.js").read_text(encoding="utf-8")
SITUATION_CSS = (ROOT / "frontend/static/css/situations.css").read_text(encoding="utf-8")
RUNTIME_CSS = (ROOT / "frontend/static/css/gis-runtime.css").read_text(encoding="utf-8")
RUN_CSS = (ROOT / "frontend/static/css/run.css").read_text(encoding="utf-8")
RUN_HTML = (ROOT / "frontend/templates/pages/run.html").read_text(encoding="utf-8")
RUN_JS = (MODULES / "run.js").read_text(encoding="utf-8")
RUNTIME_HTML = (ROOT / "frontend/templates/pages/gis_runtime.html").read_text(encoding="utf-8")


def run_module(module_name: str, body: str) -> object:
    module_uri = (MODULES / module_name).as_uri()
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", f'import * as target from {json.dumps(module_uri)};\n{body}'],
        cwd=ROOT,
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


class G22VisualAcceptanceContractTests(unittest.TestCase):
    def test_airport_map_text_helpers_never_add_airport_id(self) -> None:
        result = run_module(
            "airport-display.js",
            """
const airport = {airport_id:'AP058', airport_name:'Baotou Donghe International Airport'};
process.stdout.write(JSON.stringify({
  label: target.airportMapLabel(airport),
  tooltip: target.airportMapTooltip(airport),
}));
""",
        )
        self.assertEqual("Baotou Donghe", result["label"])
        self.assertEqual("Baotou Donghe International Airport", result["tooltip"])
        self.assertNotIn("AP058", result["label"] + result["tooltip"])

    def test_situation_and_runtime_airport_tooltips_use_shared_dark_contract(self) -> None:
        catalog = SITUATION_JS[
            SITUATION_JS.index("function renderCatalogLayer"):
            SITUATION_JS.index("function refreshCatalogLayers")
        ]
        self.assertNotIn("<br>${escapeHtml(id)}", catalog)
        self.assertIn("airportMapTooltip(item)", catalog)
        self.assertIn("airportMapTooltip(airport)", SITUATION_JS)
        self.assertIn("airportMapTooltip(item)", RUNTIME_JS)
        self.assertIn("map-object-tooltip", SITUATION_JS)
        self.assertIn("map-object-tooltip", RUNTIME_JS)
        self.assertIn(".leaflet-tooltip.map-object-tooltip", SITUATION_CSS)
        self.assertIn(".leaflet-tooltip.map-object-tooltip", RUNTIME_CSS)
        self.assertNotIn("机场性质：${airportRoleLabel", SITUATION_JS)
        self.assertNotIn("机场性质：${airportRoleLabel", RUNTIME_JS)
        self.assertIn("airport.airport_id", SITUATION_JS)
        self.assertIn("item.airport_id", RUNTIME_JS)

    def test_map_labels_are_dark_compact_and_single_line(self) -> None:
        for css in (SITUATION_CSS, RUNTIME_CSS):
            compact = "".join(css.split())
            self.assertIn("background:rgba(5,20,31,.9)", compact)
            self.assertIn("color:#eaf4fa", compact)
            self.assertIn("white-space:nowrap", compact)
            self.assertIn("border-radius:3px", compact)

    def test_run_tables_have_independent_compact_status_and_action_columns(self) -> None:
        compact = "".join(RUN_CSS.split())
        self.assertIn('class="run-table queue-table"', RUN_HTML)
        self.assertIn('class="run-table history-table"', RUN_HTML)
        self.assertIn(".history-status-col{width:84px}", compact)
        self.assertIn(".history-actions-col{width:136px}", compact)
        self.assertIn(".queue-status-col{width:84px}", compact)
        self.assertIn(".queue-actions-col{width:108px}", compact)
        self.assertIn("查看结果", RUN_JS)
        self.assertIn("查看日志", RUN_JS)
        self.assertIn("取消排队", RUN_JS)

    def test_runtime_header_is_two_compact_rows_with_frozen_run_metadata(self) -> None:
        compact = "".join(RUNTIME_CSS.split())
        self.assertIn("runtime-head-main", RUNTIME_HTML)
        self.assertIn("‹ 返回", RUNTIME_HTML)
        self.assertIn("运行态势", RUNTIME_HTML)
        self.assertIn("只读", RUNTIME_HTML)
        self.assertNotIn("min-width:470px", compact)
        self.assertIn("max-width:460px", compact)
        self.assertIn("min-height:56px", compact)
        self.assertIn("state.run?.situation?.name", RUNTIME_JS)
        self.assertIn("state.runtime.time_axis.slot_minutes", RUNTIME_JS)

    def test_aggregate_relation_exposes_canonical_sorties_and_path_ids(self) -> None:
        result = run_module(
            "runtime-map-activity.js",
            """
const routes = [
  {path_id:'p1',origin_airport_id:'A1',mission_id:'M1',return_airport_id:'A1',aircraft_type:'T1',sorties:2,depart_window:1,return_window:4,ready_window:6},
  {path_id:'p2',origin_airport_id:'A1',mission_id:'M1',return_airport_id:'A2',aircraft_type:'T2',sorties:4,depart_window:2,return_window:5,ready_window:7},
  {path_id:'future',origin_airport_id:'A1',mission_id:'M1',return_airport_id:'A1',aircraft_type:'T1',sorties:9,depart_window:7,return_window:8,ready_window:9},
];
const relation = target.aggregateRuntimeRelations(routes, 'execution', 3)[0];
process.stdout.write(JSON.stringify({sorties:relation.sorties,pathIds:relation.pathIds,aircraft:relation.aircraft}));
""",
        )
        self.assertEqual(6, result["sorties"])
        self.assertEqual(["p1", "p2"], result["pathIds"])
        self.assertEqual({"T1": 2, "T2": 4}, result["aircraft"])

    def test_aggregate_count_and_explicit_path_drilldown_are_separate(self) -> None:
        for token in (
            "aggregateRuntimeRelations",
            "relation.pathIds.includes",
            "selectPath(pathButton.dataset.pathId)",
            "pointAt(points)",
            "interactive: false",
            "drawSelectedPath()",
        ):
            self.assertIn(token, RUNTIME_JS)
        self.assertIn("runtime-relation-count", RUNTIME_CSS)
        self.assertIn("runtime-path-drilldown", RUNTIME_CSS)
        self.assertNotIn("runtime-route-event-marker", RUNTIME_CSS)
        self.assertNotIn("task-connection-count-outbound", RUNTIME_CSS)
        self.assertNotIn("task-connection-count-return", RUNTIME_CSS)


if __name__ == "__main__":
    unittest.main()
