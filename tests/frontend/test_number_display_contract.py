from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "frontend/static/js/modules/number-display.js"


class NumberDisplayContractTests(unittest.TestCase):
    def test_shared_formatters_keep_zero_and_reject_missing_values(self):
        script = f"""
import {{ formatInteger, formatDecimal, formatPercent, formatCoordinate,
  formatDistance, formatSeconds, formatWeight, formatHhi }} from {json.dumps(MODULE.as_uri())};
const out = {{
  zero: formatInteger(0),
  count: formatInteger(1234),
  decimal: formatDecimal(1.00000000002),
  rounded: formatDecimal(0.3333333333333333),
  percent: formatPercent(0.12345, {{ digits: 1 }}),
  gap: formatPercent(0.0058123, {{ digits: 2 }}),
  coordinate: formatCoordinate(118.7968769239283),
  distance: formatDistance(125.678),
  seconds: formatSeconds(20),
  weight: formatWeight(0.3333333333333333),
  hhi: formatHhi(0.1234567),
  missing: formatDecimal(null),
  invalid: formatPercent(Number.NaN),
}};
process.stdout.write(JSON.stringify(out));
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            {
                "zero": "0",
                "count": "1,234",
                "decimal": "1",
                "rounded": "0.33",
                "percent": "12.3%",
                "gap": "0.58%",
                "coordinate": "118.79688",
                "distance": "125.7 km",
                "seconds": "20.00s",
                "weight": "0.333",
                "hhi": "0.1235",
                "missing": "—",
                "invalid": "—",
            },
            json.loads(completed.stdout),
        )

    def test_requested_pages_import_the_shared_display_authority(self):
        for relative in (
            "base-data.js",
            "situations.js",
            "run.js",
            "single-run.js",
            "gis-runtime.js",
            "results.js",
            "indicators.js",
        ):
            source = (ROOT / "frontend/static/js/modules" / relative).read_text(encoding="utf-8")
            self.assertIn("./number-display.js", source, relative)


if __name__ == "__main__":
    unittest.main()
