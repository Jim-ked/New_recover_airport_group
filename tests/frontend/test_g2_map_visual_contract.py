from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "frontend/static/js/modules"
RUNTIME_JS = MODULES / "gis-runtime.js"
SITUATION_JS = MODULES / "situation-map.js"
RUNTIME_CSS = ROOT / "frontend/static/css/gis-runtime.css"
SITUATION_CSS = ROOT / "frontend/static/css/situations.css"
RUNTIME_HTML = ROOT / "frontend/templates/pages/gis_runtime.html"
SITUATION_HTML = ROOT / "frontend/templates/pages/situations.html"


def run_module(module_name: str, body: str) -> object:
    module_uri = (MODULES / module_name).as_uri()
    script = f'import * as target from {json.dumps(module_uri)};\n{body}'
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


class G2MapVisualContractTests(unittest.TestCase):
    def test_airport_role_helper_keeps_four_distinct_semantics(self) -> None:
        result = run_module(
            "airport-display.js",
            """
const roles = ['civil', 'military', 'joint', null, 'unexpected'];
process.stdout.write(JSON.stringify(roles.map((role) => ({
  role,
  className: target.airportRoleClass(role),
  label: target.airportRoleLabel(role),
}))));
""",
        )
        self.assertEqual(
            ["机场性质：民用", "机场性质：军用", "机场性质：军民两用", "机场性质：未标注", "机场性质：未标注"],
            [f"机场性质：{row['label']}" for row in result],
        )
        self.assertEqual(
            ["airport-role-civil", "airport-role-military", "airport-role-joint", "airport-role-unknown", "airport-role-unknown"],
            [row["className"] for row in result],
        )
        self.assertNotEqual(result[0]["className"], result[3]["className"])

    def test_label_layout_keeps_priority_and_force_visible_labels(self) -> None:
        result = run_module(
            "map-label-layout.js",
            """
function element(name, rect) {
  const classes = new Set();
  return {
    name, classes,
    classList: { toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value) },
    getBoundingClientRect: () => rect,
  };
}
const high = element('high', {left:0, top:0, right:40, bottom:14, width:40, height:14});
const low = element('low', {left:10, top:0, right:50, bottom:14, width:40, height:14});
const forced = element('forced', {left:5, top:0, right:45, bottom:14, width:40, height:14});
target.layoutMapLabels([
  {element: low, priority: 10},
  {element: high, priority: 80},
]);
const priority = {high:[...high.classes], low:[...low.classes]};
target.layoutMapLabels([
  {element: high, priority: 80},
  {element: forced, priority: 1, forceVisible: true},
]);
process.stdout.write(JSON.stringify({priority, forced:[...forced.classes], displaced:[...high.classes]}));
""",
        )
        self.assertNotIn("map-label-hidden", result["priority"]["high"])
        self.assertIn("map-label-hidden", result["priority"]["low"])
        self.assertNotIn("map-label-hidden", result["forced"])
        self.assertIn("map-label-hidden", result["displaced"])

    def test_label_layout_is_generic_and_reacts_to_map_view_changes(self) -> None:
        source = (MODULES / "map-label-layout.js").read_text(encoding="utf-8")
        for forbidden in ("airport", "mission", "Situation", "Runtime", "core", "cluster"):
            self.assertNotIn(forbidden, source)
        self.assertIn("moveend zoomend resize", source)
        self.assertIn("priority", source)
        self.assertIn("forceVisible", source)

    def test_runtime_views_use_only_the_requested_time_boundary(self) -> None:
        result = run_module(
            "runtime-map-activity.js",
            """
const routes = [
  {path_id:'past',origin_airport_id:'A1',mission_id:'M1',return_airport_id:'A1',aircraft_type:'T1',sorties:2,depart_window:1,return_window:3,ready_window:4},
  {path_id:'current',origin_airport_id:'A1',mission_id:'M1',return_airport_id:'A2',aircraft_type:'T1',sorties:4,depart_window:3,return_window:6,ready_window:8},
  {path_id:'future',origin_airport_id:'A1',mission_id:'M1',return_airport_id:'A1',aircraft_type:'T1',sorties:9,depart_window:7,return_window:8,ready_window:9},
];
const ids = (view, window) => target.filterRuntimeRoutes(routes, view, window).map((item) => item.path_id);
process.stdout.write(JSON.stringify({departure:ids('departure',3), execution:ids('execution',4), landing:ids('landing',3), maintenance:ids('maintenance',3)}));
""",
        )
        self.assertEqual(["current"], result["departure"])
        self.assertEqual(["current"], result["execution"])
        self.assertEqual(["past"], result["landing"])
        self.assertEqual(["past"], result["maintenance"])

    def test_situation_preserves_business_layers_and_label_priorities(self) -> None:
        source = SITUATION_JS.read_text(encoding="utf-8")
        panels = (MODULES / "situations.js").read_text(encoding="utf-8")
        self.assertIn("for (const item of state.working.airports)", source)
        self.assertIn("for (const mission of state.working.missions)", source)
        self.assertIn("visibleCandidates()", source)
        self.assertIn("syncCandidateLeafletMarkers()", source)
        self.assertIn("export async function setCatalogLayer", source)
        self.assertIn("airportRoleClass(airport.role)", source)
        self.assertIn("airportRoleLabel(airport.role)", panels)
        self.assertIn("selected: 100", source)
        self.assertIn("focused: 90", source)
        self.assertIn("primary: 85", source)
        self.assertIn("airport: 80", source)
        self.assertIn("mission: 75", source)
        for token in (
            "map-state-selected", "map-state-focused", "candidate-queued",
            "has-damage-config", "map-label-selected", "map-label-focused",
        ):
            self.assertIn(token, source)
        catalog = source[source.index("export async function setCatalogLayer"):source.index("export async function initMap")]
        self.assertNotIn("permanent: true", catalog)

    def test_runtime_marker_statuses_are_orthogonal_and_maintenance_uses_return_airport(self) -> None:
        source = RUNTIME_JS.read_text(encoding="utf-8")
        for token in (
            "'ordinary'", "'participating'", "'selected-cluster'", "'core'",
            "'selected-object'", "'damage'", "drawMaintenanceCount",
            "summarizeRuntimeRouteState",
            "selected: 110", "core: 90", "selectedCluster: 82", "participating: 72", "mission: 60",
        ):
            self.assertIn(token, source)
        self.assertNotIn("airportRoleClass(item.role)", source)
        self.assertNotIn("/api/airports", source)
        for forbidden in ("military-core", "joint-participating", "civil-core", "military-selected-cluster"):
            self.assertNotIn(forbidden, source)

    def test_runtime_aggregates_current_view_and_preserves_explicit_path_drilldown(self) -> None:
        source = RUNTIME_JS.read_text(encoding="utf-8")
        activity = (MODULES / "runtime-map-activity.js").read_text(encoding="utf-8")
        self.assertIn("aggregateRuntimeRelations", source)
        self.assertIn("filters: currentFilters()", source)
        self.assertIn("function drawSelectedPath()", source)
        self.assertIn("pathIds", activity)
        self.assertIn("routes: relation.routes.slice()", activity)
        self.assertNotIn("buildRuntimePathActivities", source)

    def test_runtime_css_uses_unified_circle_while_situation_keeps_role_shapes(self) -> None:
        runtime = "".join(RUNTIME_CSS.read_text(encoding="utf-8").split())
        situation = "".join(SITUATION_CSS.read_text(encoding="utf-8").split())
        for token in (".airport-role-civil", ".airport-role-military", ".airport-role-joint", ".airport-role-unknown"):
            self.assertIn(token, situation)
            self.assertNotIn(token, runtime)
        for token in ("display:block;box-sizing:border-box", "width:14px;height:14px", "border-radius:50%", ".runtime-airport-marker.selected-cluster::before"):
            self.assertIn(token, runtime)
        for token in ("width:13px;height:13px", "width:11px;height:11px", "width:8px;height:8px"):
            self.assertIn(token, situation)
        self.assertNotIn(".runtime-airport-marker>span{width:7px;height:7px", runtime)
        self.assertIn("runtime-relation-count", runtime)

    def test_situation_airport_marker_spec_has_distinct_sizes_colors_and_legend(self) -> None:
        source = "".join(SITUATION_JS.read_text(encoding="utf-8").split())
        css = "".join(SITUATION_CSS.read_text(encoding="utf-8").split())
        html = SITUATION_HTML.read_text(encoding="utf-8")

        for token in (
            "iconSize:[24,24],iconAnchor:[12,12]",
            "iconSize:[20,20],iconAnchor:[10,10]",
            "iconSize:[16,16],iconAnchor:[8,8]",
        ):
            self.assertIn(token, source)
        for token in (
            ".situation-airport-markerspan{display:block;box-sizing:border-box;width:13px;height:13px",
            ".situation-candidate-markerspan{display:block;box-sizing:border-box;width:11px;height:11px",
            ".catalog-airport-markerspan{box-sizing:border-box;width:8px;height:8px",
            ".airport-role-civil{--airport-fill:",
            ".airport-role-military{--airport-fill:",
            ".airport-role-joint{--airport-fill:",
        ):
            self.assertIn(token, css)
        for label in ("机场类别", "民用", "军用", "军民两用", "当前情境", "待加入候选", "基础参考", "损毁事件配置"):
            self.assertIn(label, html)

    def test_user_facing_runtime_language_uses_task_activity_not_airline_routes(self) -> None:
        source = RUNTIME_JS.read_text(encoding="utf-8")
        html = RUNTIME_HTML.read_text(encoding="utf-8")
        self.assertNotIn("航线", source + html)
        for token in ("总体态势筛选", "执行态势", "出动态势", "落地态势", "整备态势", "完整业务链"):
            self.assertIn(token, source + html)


if __name__ == "__main__":
    unittest.main()
