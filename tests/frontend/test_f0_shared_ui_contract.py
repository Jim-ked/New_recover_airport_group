from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = (ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
TOKENS = (ROOT / "frontend/static/css/tokens.css").read_text(encoding="utf-8")
SHELL = (ROOT / "frontend/static/css/shell.css").read_text(encoding="utf-8")
COMPONENTS = (ROOT / "frontend/static/css/components.css").read_text(encoding="utf-8")
SHELL_JS = (ROOT / "frontend/static/js/modules/shell.js").read_text(encoding="utf-8")
API_JS = (ROOT / "frontend/static/js/modules/api-client.js").read_text(encoding="utf-8")
ASSET_README = (ROOT / "frontend/static/assets/README.md").read_text(encoding="utf-8")
LEAFLET_README = (ROOT / "frontend/static/vendor/leaflet/README.md").read_text(encoding="utf-8")
PAGE_CSS = [
    ROOT / "frontend/static/css/run.css",
    ROOT / "frontend/static/css/single-run.css",
    ROOT / "frontend/static/css/gis-runtime.css",
    ROOT / "frontend/static/css/results.css",
]


class SharedUiF0ContractTests(unittest.TestCase):
    def test_shared_css_loads_before_page_css(self):
        self.assertLess(BASE.index("css/tokens.css"), BASE.index("{% block head %}"))
        self.assertLess(BASE.index("css/shell.css"), BASE.index("{% block head %}"))
        self.assertLess(BASE.index("css/components.css"), BASE.index("{% block head %}"))

    def test_page_css_no_longer_owns_application_shell_or_global_controls(self):
        global_selector_patterns = (
            r"(?m)(?:^|})\s*html\s*,\s*body\s*\{",
            r"(?m)(?:^|})\s*\.app-shell\s*\{",
            r"(?m)(?:^|})\s*\.topbar\s*\{",
            r"(?m)(?:^|})\s*\.sidebar\s*\{",
            r"(?m)(?:^|})\s*\.nav\s*\{",
            r"(?m)(?:^|})\s*\.btn\s*\{",
            r"(?m)(?:^|})\s*\.control\s*\{",
        )
        for path in PAGE_CSS:
            text = path.read_text(encoding="utf-8")
            for pattern in global_selector_patterns:
                self.assertIsNone(
                    re.search(pattern, text),
                    f"{path.name} still owns global selector pattern {pattern}",
                )

    def test_shared_spacing_and_motion_are_explicit(self):
        for token in ("--space-1", "--space-4", "--space-7", "--motion-fast", "--motion-base", "--lh-ui", "--lh-copy"):
            self.assertIn(token, TOKENS)
        for token in (".segmented", ".disclosure", ".dock", ".empty-state", ".data-table"):
            self.assertIn(token, COMPONENTS)

    def test_session_loss_is_global_not_page_specific(self):
        self.assertIn("app:auth-required", API_JS)
        self.assertIn("app:auth-required", SHELL_JS)
        self.assertIn("window.location.replace", SHELL_JS)
        self.assertIn("/api/me", SHELL_JS)

    def test_visual_assets_are_manual_local_slots_not_network_dependencies(self):
        self.assertIn("manual", ASSET_README.lower())
        self.assertIn("existing local", LEAFLET_README.lower())
        self.assertIn("No CDN fallback", LEAFLET_README)
        for text in (ASSET_README, LEAFLET_README):
            self.assertNotIn("https://", text)
            self.assertNotIn("http://", text)


if __name__ == "__main__":
    unittest.main()
