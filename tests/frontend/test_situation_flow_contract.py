from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SituationFlowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "frontend/templates/pages/situations.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "frontend/static/js/modules/situations.js").read_text(encoding="utf-8")
        cls.panels = (ROOT / "frontend/static/js/modules/situation-panels.js").read_text(encoding="utf-8")
        cls.state = (ROOT / "frontend/static/js/modules/situation-state.js").read_text(encoding="utf-8")

    def test_new_situation_is_an_inspector_editor_not_a_modal(self):
        self.assertNotIn("situationCreateModal", self.html + self.js + self.state)
        self.assertIn("renderNewSituationEditor", self.js)
        for token in ("newSituationId", "newSituationName", "newSituationDescription"):
            self.assertIn(token, self.js)

    def test_panel_draft_switching_uses_local_inline_choice(self):
        self.assertIn("当前修改尚未应用", self.js)
        self.assertIn("继续编辑", self.js)
        self.assertIn("放弃并切换", self.js)
        self.assertIn("requestPanelTransition", self.js)
        self.assertNotIn("canDiscardPanelDraft", self.js)

    def test_empty_state_does_not_turn_the_tools_into_a_wizard(self):
        self.assertNotIn("先加入机场", self.panels)
        self.assertNotIn("下一步", self.panels + self.js + self.html)

    def test_irreversible_actions_keep_the_center_confirmation(self):
        self.assertIn("situationConfirmModal", self.html)
        self.assertIn("删除情境", self.js)
        self.assertIn("confirmAction", self.js)


if __name__ == "__main__":
    unittest.main()
