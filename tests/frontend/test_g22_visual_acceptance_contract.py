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
            SITUATION_JS.index("export async function setCatalogLayer"):
            SITUATION_JS.index("export async function initMap")
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

    def test_activity_exposes_canonical_sorties_without_future_or_outside_trail(self) -> None:
        result = run_module(
            "runtime-map-activity.js",
            """
const frames = [
  {departures:[{path_id:'old',sorties:2}],returns:[]},
  {departures:[{path_id:'current-depart',sorties:4}],returns:[{path_id:'current-return',sorties:3}]},
  {departures:[{path_id:'future',sorties:9}],returns:[]},
];
const activity = target.collectRuntimeActivity(frames, 1, 1);
process.stdout.write(JSON.stringify({
  departure:[...activity.departureSorties],
  returns:[...activity.returnSorties],
}));
""",
        )
        self.assertEqual([["current-depart", 4]], result["departure"])
        self.assertEqual([["current-return", 3]], result["returns"])

    def test_connection_count_labels_follow_direction_switch_and_priority(self) -> None:
        for token in (
            "activity.departureSorties.get(route.path_id)",
            "activity.returnSorties.get(route.path_id)",
            "出动 ${sorties}",
            "返航 ${sorties}",
            "quadraticPointAt(points, 0.5)",
            "interactive: false",
            "current ? 68 : 45",
            "layerEnabled('outbound') && activity.departures.has(route.path_id)",
            "layerEnabled('return') && activity.returns.has(route.path_id)",
        ):
            self.assertIn(token, RUNTIME_JS)
        self.assertIn("task-connection-count-outbound", RUNTIME_CSS)
        self.assertIn("task-connection-count-return", RUNTIME_CSS)
        self.assertIn("historical", RUNTIME_CSS)


if __name__ == "__main__":
    unittest.main()
