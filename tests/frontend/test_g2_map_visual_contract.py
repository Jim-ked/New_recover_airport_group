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

    def test_runtime_activity_respects_current_three_six_all_and_never_future(self) -> None:
        result = run_module(
            "runtime-map-activity.js",
            """
const frames = Array.from({length:8}, (_, index) => ({departures:[], returns:[]}));
for (let index = 0; index < 8; index += 1) frames[index].departures.push({path_id:`p${index}`});
for (const index of [0,2,4,6,7]) frames[index].returns.push({path_id:`r${index}`});
const view = (trail) => {
  const result = target.collectRuntimeActivity(frames, 6, trail);
  return {
    departures:[...result.departures], returns:[...result.returns],
    currentDepartures:[...result.currentDepartures], currentReturns:[...result.currentReturns],
  };
};
process.stdout.write(JSON.stringify({current:view(1), three:view(3), six:view(6), all:view(Infinity)}));
""",
        )
        self.assertEqual(["p6"], result["current"]["departures"])
        self.assertEqual(["r6"], result["current"]["returns"])
        self.assertEqual(["p4", "p5", "p6"], result["three"]["departures"])
        self.assertEqual(["r4", "r6"], result["three"]["returns"])
        self.assertEqual(["p1", "p2", "p3", "p4", "p5", "p6"], result["six"]["departures"])
        self.assertEqual(["p0", "p1", "p2", "p3", "p4", "p5", "p6"], result["all"]["departures"])
        self.assertNotIn("p7", result["all"]["departures"])
        self.assertNotIn("r7", result["all"]["returns"])

    def test_situation_preserves_business_layers_and_label_priorities(self) -> None:
        source = SITUATION_JS.read_text(encoding="utf-8")
        panels = (MODULES / "situations.js").read_text(encoding="utf-8")
        self.assertIn("for (const item of state.working.airports)", source)
        self.assertIn("for (const mission of state.working.missions)", source)
        self.assertIn("if (state.mode === 'airport')", source)
        self.assertIn("export async function setCatalogLayer", source)
        self.assertIn("airportRoleClass(airport.role)", source)
        self.assertIn("airportRoleLabel(airport.role)", panels)
        self.assertIn("selected: 100", source)
        self.assertIn("airport: 80", source)
        self.assertIn("mission: 75", source)
        catalog = source[source.index("export async function setCatalogLayer"):source.index("export async function initMap")]
        self.assertNotIn("permanent: true", catalog)

    def test_runtime_marker_statuses_are_orthogonal_and_maintenance_is_filtered(self) -> None:
        source = RUNTIME_JS.read_text(encoding="utf-8")
        for token in (
            "airportRoleClass(item.role)", "'ordinary'", "'participating'", "'selected-cluster'", "'core'",
            "'selected-object'", "'damage'", "important && maintenance > 0",
            "selected: 100", "core: 90", "selectedCluster: 80", "participating: 70", "mission: 60",
        ):
            self.assertIn(token, source)
        self.assertNotIn("/api/airports", source)
        for forbidden in ("military-core", "joint-participating", "civil-core", "military-selected-cluster"):
            self.assertNotIn(forbidden, source)

    def test_runtime_draws_only_activity_sets_with_separate_outbound_and_return_switches(self) -> None:
        source = RUNTIME_JS.read_text(encoding="utf-8")
        self.assertIn("activity.departures.has(route.path_id)", source)
        self.assertIn("activity.returns.has(route.path_id)", source)
        self.assertIn("activity.currentDepartures.has(route.path_id)", source)
        self.assertIn("activity.currentReturns.has(route.path_id)", source)
        self.assertIn("layerEnabled('outbound')", source)
        self.assertIn("layerEnabled('return')", source)

    def test_css_uses_small_role_shapes_and_mission_is_not_joint(self) -> None:
        runtime = "".join(RUNTIME_CSS.read_text(encoding="utf-8").split())
        situation = "".join(SITUATION_CSS.read_text(encoding="utf-8").split())
        for css in (runtime, situation):
            self.assertIn(".airport-role-civil", css)
            self.assertIn(".airport-role-military", css)
            self.assertIn(".airport-role-joint", css)
            self.assertIn(".airport-role-unknown", css)
        for token in ("width:7px;height:7px", "width:9px;height:9px", "width:10px;height:10px", "width:11px;height:11px"):
            self.assertIn(token, runtime)
        self.assertIn("width:9px;height:9px", situation)
        self.assertIn("width:8px;height:8px", runtime)
        self.assertNotIn("runtime-route", runtime)

    def test_user_facing_runtime_language_uses_task_activity_not_airline_routes(self) -> None:
        source = RUNTIME_JS.read_text(encoding="utf-8")
        html = RUNTIME_HTML.read_text(encoding="utf-8")
        self.assertNotIn("航线", source + html)
        for token in ("任务态势", "出动", "返航", "态势范围", "任务执行连接"):
            self.assertIn(token, source + html)


if __name__ == "__main__":
    unittest.main()
