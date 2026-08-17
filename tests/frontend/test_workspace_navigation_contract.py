from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class WorkspaceNavigationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = (ROOT / "frontend/templates/base.html").read_text(encoding="utf-8")
        cls.base_data_html = (ROOT / "frontend/templates/pages/base_data.html").read_text(encoding="utf-8")
        cls.situations_html = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        cls.base_data_js = (ROOT / "frontend/static/js/modules/base-data.js").read_text(encoding="utf-8")
        cls.situations_js = (ROOT / "frontend/static/js/modules/situations.js").read_text(encoding="utf-8")
        cls.map_js = (ROOT / "frontend/static/js/modules/situation-map.js").read_text(encoding="utf-8")
        cls.navigation_js = (ROOT / "frontend/static/js/modules/workspace-navigation.js").read_text(encoding="utf-8")

    def test_one_shell_navigation_authority_is_loaded_globally(self):
        self.assertEqual(self.base.count("js/modules/workspace-navigation.js"), 1)
        self.assertNotIn("MutationObserver", self.navigation_js)
        self.assertNotIn("iframe", self.navigation_js.lower())

    def test_supported_workspaces_declare_identity_module_and_assets(self):
        for html, identity, module in (
            (self.base_data_html, 'data-workspace="base-data"', "js/modules/base-data.js"),
            (self.situations_html, 'data-workspace="situations"', "js/modules/situations.js"),
        ):
            self.assertIn(identity, html)
            self.assertIn("data-workspace-module", html)
            self.assertIn(module, html)
            self.assertIn("data-workspace-asset", html)

    def test_page_modules_have_explicit_reentrant_lifecycle(self):
        for source in (self.base_data_js, self.situations_js):
            self.assertIn("export async function mount(", source)
            self.assertIn("export async function beforeLeave(", source)
            self.assertIn("export function unmount(", source)
            self.assertNotRegex(source, r"(?m)^init\(\);\s*$")
        self.assertIn("export function destroyMap(", self.map_js)

    def test_navigation_parses_documents_guards_races_and_owns_history(self):
        for token in ("DOMParser", "AbortController", "popstate", "pushState"):
            self.assertIn(token, self.navigation_js)
        self.assertIn("beforeLeave", self.navigation_js)
        self.assertIn("unmount", self.navigation_js)
        self.assertIn("mount", self.navigation_js)


if __name__ == "__main__":
    unittest.main()
