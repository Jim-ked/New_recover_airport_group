from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOGIN_HTML = (ROOT / "frontend/templates/pages/login.html").read_text(encoding="utf-8")
LOGIN_CSS = (ROOT / "frontend/static/css/login.css").read_text(encoding="utf-8")
BASE_HTML = (ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
SHELL_CSS = (ROOT / "frontend/static/css/shell.css").read_text(encoding="utf-8")


class BrandAssetsFrontendContractTests(unittest.TestCase):
    def test_login_uses_existing_background_and_formal_brand_emblem(self):
        self.assertTrue((ROOT / "frontend/static/images/login/login-bg.jpg").is_file())
        self.assertTrue((ROOT / "frontend/static/icons/airforce-emblem.png").is_file())
        logo = ROOT / "frontend/static/icons/logo.webp"
        self.assertTrue(logo.is_file())
        self.assertEqual(b"RIFF", logo.read_bytes()[:4])
        self.assertEqual(b"WEBP", logo.read_bytes()[8:12])
        self.assertFalse((ROOT / "frontend/static/icons/logo.png").exists())
        self.assertIn("images/login/login-bg.jpg", LOGIN_CSS)
        self.assertNotIn("images/login_bg.png", LOGIN_CSS)
        self.assertIn("icons/airforce-emblem.png", LOGIN_HTML)
        self.assertNotIn("icons/logo.png", LOGIN_HTML)
        self.assertNotIn("icons/logo.webp", LOGIN_HTML)
        self.assertNotIn('class="login-aircraft"', LOGIN_HTML)
        self.assertNotIn("login-aircraft.svg", LOGIN_HTML)
        self.assertNotIn("login-brand", LOGIN_HTML)
        self.assertNotIn("login-card-intro", LOGIN_HTML)
        self.assertNotIn("login-foot", LOGIN_HTML)

    def test_login_visual_hierarchy_has_one_brand_focus(self):
        compact = "".join(LOGIN_CSS.split())
        self.assertIn("background-size:cover", compact)
        self.assertIn(".login-emblem", LOGIN_CSS)
        self.assertIn("object-fit:contain", compact)
        self.assertNotIn(".login-aircraft", LOGIN_CSS)
        self.assertNotIn("@keyframes", LOGIN_CSS)
        self.assertNotIn("animation:", compact)

    def test_shell_uses_only_the_formal_emblem_for_branding(self):
        brand = BASE_HTML[BASE_HTML.index('class="brand"'):BASE_HTML.index("</div>", BASE_HTML.index('class="brand"'))]
        self.assertIn("icons/airforce-emblem.png", brand)
        self.assertNotIn("icons/logo.png", brand)
        self.assertNotIn("icons/logo.webp", brand)
        self.assertNotIn('#i-brand', brand)
        compact = "".join(SHELL_CSS.split())
        self.assertIn(".brand-emblem-image", SHELL_CSS)
        self.assertIn("object-fit:contain", compact)


if __name__ == "__main__":
    unittest.main()
