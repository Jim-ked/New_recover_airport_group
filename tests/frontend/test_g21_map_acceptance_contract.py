from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "frontend/static/js/modules"
SITUATION_JS = (MODULES / "situation-map.js").read_text(encoding="utf-8")
RUNTIME_JS = (MODULES / "gis-runtime.js").read_text(encoding="utf-8")
SITUATION_CSS = (ROOT / "frontend/static/css/situations.css").read_text(encoding="utf-8")
RUNTIME_CSS = (ROOT / "frontend/static/css/gis-runtime.css").read_text(encoding="utf-8")
PROVINCES = ROOT / "frontend/static/gis/china_provinces.geojson"


class G21MapAcceptanceContractTests(unittest.TestCase):
    def test_catalog_airports_are_visible_but_smaller_than_current_airports(self) -> None:
        compact = "".join(SITUATION_CSS.split())
        self.assertIn(".situation-airport-markerspan{display:block;width:9px;height:9px", compact)
        self.assertIn(".catalog-airport-markerspan,", compact)
        self.assertIn("width:6px;height:6px", compact)
        self.assertIn("border:1pxsolidrgba(132,182,210,.9)", compact)
        self.assertIn("background:rgba(82,120,141,.6)", compact)

    def test_catalog_airports_keep_role_shapes_without_permanent_labels(self) -> None:
        catalog = SITUATION_JS[
            SITUATION_JS.index("export async function setCatalogLayer"):
            SITUATION_JS.index("export async function initMap")
        ]
        self.assertIn("airportRoleClass(item.role)", catalog)
        self.assertNotIn("permanent: true", catalog)

    def test_province_resource_is_the_confirmed_feature_collection(self) -> None:
        data = json.loads(PROVINCES.read_text(encoding="utf-8"))
        self.assertEqual("FeatureCollection", data["type"])
        self.assertEqual(34, len(data["features"]))
        self.assertEqual({"Polygon", "MultiPolygon"}, {item["geometry"]["type"] for item in data["features"]})

    def test_shared_reference_loads_only_provinces_below_business_layers(self) -> None:
        source = (MODULES / "map-reference.js").read_text(encoding="utf-8")
        self.assertIn("/static/gis/china_provinces.geojson", source)
        self.assertNotIn("custom.geojson", source)
        self.assertNotIn("populated_places", source)
        self.assertIn("interactive: false", source)
        self.assertIn("fill: false", source)
        self.assertIn("zIndex = '300'", source)
        self.assertIn("pointerEvents = 'none'", source)
        self.assertIn("./map-reference.js", SITUATION_JS)
        self.assertIn("./map-reference.js", RUNTIME_JS)

    def test_reference_failure_degrades_without_rejecting(self) -> None:
        module_uri = (MODULES / "map-reference.js").as_uri()
        script = f"""
import {{ createMapReference }} from {json.dumps(module_uri)};
globalThis.fetch = async () => {{ throw new Error('missing'); }};
globalThis.L = {{ geoJSON: () => {{ throw new Error('must not render'); }} }};
console.error = () => {{}};
const pane = {{ style: {{}} }};
const map = {{ getPane: () => null, createPane: () => pane }};
const reference = createMapReference(map);
await reference.ready;
process.stdout.write(JSON.stringify({{ settled: true, zIndex: pane.style.zIndex }}));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
        self.assertEqual({"settled": True, "zIndex": "300"}, json.loads(completed.stdout))

    def test_airport_map_labels_are_short_single_line_names(self) -> None:
        self.assertIn("bindPermanentLabel(marker, airportMapLabel(airport)", SITUATION_JS)
        self.assertIn("bindMapLabel(marker, airportMapLabel(item)", RUNTIME_JS)
        self.assertIn("white-space: nowrap", SITUATION_CSS)
        self.assertIn("white-space:nowrap", RUNTIME_CSS)
        self.assertIn("airportDisplay(id)", RUNTIME_JS)
        self.assertIn("airport.airport_id", SITUATION_JS)


if __name__ == "__main__":
    unittest.main()
