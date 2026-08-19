from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REGION = (ROOT / "frontend/static/js/modules/region-display.js").read_text(encoding="utf-8")
BASE_DATA = (ROOT / "frontend/static/js/modules/base-data.js").read_text(encoding="utf-8")
SITUATIONS = (ROOT / "frontend/static/js/modules/situations.js").read_text(encoding="utf-8")


class RegionDisplayContractTests(unittest.TestCase):
    def test_all_current_mainland_region_codes_have_one_shared_display_mapping(self) -> None:
        for code, name in (("CN-11", "北京市"), ("CN-31", "上海市"), ("CN-32", "江苏省"), ("CN-33", "浙江省"), ("CN-65", "新疆维吾尔自治区")):
            self.assertIn(f"'{code}':'{name}'", REGION)
        self.assertIn("regionDisplayName", BASE_DATA)
        self.assertIn("regionDisplayWithCode", BASE_DATA)
        self.assertIn("regionDisplayName", SITUATIONS)
        self.assertIn("regionDisplayWithCode", SITUATIONS)


if __name__ == "__main__":
    unittest.main()
