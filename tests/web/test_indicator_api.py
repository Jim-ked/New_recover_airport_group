from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.auth.principal import Principal
from backend.storage.database import initialize_database
from backend.storage.indicator_repository import IndicatorRepository
from backend.web.indicator_api import IndicatorApi


class IndicatorApiTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = Path(self.td.name) / "app.sqlite"
        initialize_database(self.db)
        self.repo = IndicatorRepository(self.db)
        self.api = IndicatorApi(repository=self.repo)
        self.viewer = Principal("U1", role="viewer")
        self.operator = Principal("U1", role="operator")
        self.admin = Principal("ADMIN", is_admin=True)

    def tearDown(self):
        self.td.cleanup()

    def test_default_tree_is_seeded_and_core_nodes_are_present(self):
        response = self.api.tree(principal=self.viewer)
        self.assertEqual(200, response.status)
        self.assertEqual("默认指标集 V1.1", response.body["indicator_set"]["name"])
        self.assertEqual("published", response.body["indicator_set"]["status"])
        roots = [x for x in response.body["nodes"] if x["level"] == 1]
        self.assertEqual(
            {"机场保障节点能力", "机场群保障能力", "区域顽存能力"},
            {x["name"] for x in roots},
        )
        core = {x["name"] for x in response.body["nodes"] if x["is_core"]}
        self.assertEqual({"任务完成率", "专业物资保障率", "群内机场分布均衡度"}, core)

    def test_default_tree_matches_frozen_three_level_baseline(self):
        tree = self.api.tree(principal=self.viewer).body
        nodes = tree["nodes"]
        self.assertEqual(3, sum(1 for x in nodes if x["level"] == 1))
        self.assertEqual(14, sum(1 for x in nodes if x["level"] == 2))
        self.assertEqual(58, sum(1 for x in nodes if x["level"] == 3))
        self.assertEqual(75, len(nodes))

        l2_names = {x["name"] for x in nodes if x["level"] == 2}
        self.assertEqual({
            "基础设施保障能力", "物资保障能力", "装备技术保障能力", "战勤保障能力", "机务保障能力",
            "任务需求适配能力", "节点保障约束能力", "时序持续保障能力", "群内协同调配能力",
            "任务出动与执行效率", "资源保障效能", "节点网络协同效果", "防护能力", "应急响应能力",
        }, l2_names)

        l3_names = {x["name"] for x in nodes if x["level"] == 3}
        for required in (
            "跑道保障能力", "油料保障能力", "机务保障时效", "任务保障覆盖能力",
            "持续保障时长", "资源调配能力", "任务完成率", "专业物资保障率",
            "群内机场分布均衡度", "保障能力下降速率", "恢复斜率", "恢复概率",
        ):
            self.assertIn(required, l3_names)

        regional_root = next(x for x in nodes if x["level"] == 1 and x["name"] == "区域顽存能力")
        regional_l2_ids = {x["id"] for x in nodes if x["parent_id"] == regional_root["id"]}
        regional_l3 = [x for x in nodes if x["parent_id"] in regional_l2_ids]
        self.assertTrue(regional_l3)
        self.assertTrue(all(x["node_kind"] == "DIRECT" for x in regional_l3))

    def test_admin_clones_draft_and_can_add_extension_l3_but_not_edit_core(self):
        current = self.api.tree(principal=self.admin).body
        source = current["indicator_set"]
        draft_response = self.api.create_draft({
            "source_indicator_set_id": source["id"],
            "name": "Draft",
            "version": "V1.1-draft-test",
            "description": None,
            "expected_revision": source["revision"],
        }, principal=self.admin)
        self.assertEqual(201, draft_response.status)
        draft = draft_response.body["indicator_set"]
        parent = next(x for x in draft_response.body["nodes"] if x["code"] == "mission_execution")
        new_node = {
            "id": f"{draft['id']}:custom_metric", "indicator_set_id": draft["id"],
            "parent_id": parent["id"], "code": "custom_metric", "name": "扩展指标",
            "level": 3, "node_kind": "DIRECT", "unit": None, "direction": None,
            "weight": None, "description": "test", "is_core": False,
            "editable": True, "enabled": True, "display_order": 99,
        }
        created = self.api.create_node({
            "indicator": new_node, "expected_set_revision": draft["revision"]
        }, principal=self.admin)
        self.assertEqual(201, created.status)

        updated_tree = self.api.tree(principal=self.admin, indicator_set_id=draft["id"]).body
        core = next(x for x in updated_tree["nodes"] if x["is_core"])
        core["name"] = "should not change"
        blocked = self.api.update_node(core["id"], {
            "indicator": {k: core[k] for k in (
                "id","indicator_set_id","parent_id","code","name","level","node_kind","unit","direction",
                "weight","description","is_core","editable","enabled","display_order"
            )},
            "expected_set_revision": updated_tree["indicator_set"]["revision"],
        }, principal=self.admin)
        self.assertEqual(409, blocked.status)
        self.assertEqual("INDICATOR_OPERATION_BLOCKED", blocked.body["error"]["code"])

    def test_score_sheet_is_atomic_revisioned_and_submit_requires_all_enabled_l3(self):
        expert = self.api.create_expert({"expert_id": "E1", "name": "Expert 1"}, principal=self.admin)
        self.assertEqual(201, expert.status)
        tree = self.api.tree(principal=self.viewer).body
        set_id = tree["indicator_set"]["id"]
        l3 = [x for x in tree["nodes"] if x["level"] == 3 and x["enabled"]]

        draft = self.api.put_score_sheet("E1", {
            "indicator_set_id": set_id, "status": "draft", "expected_revision": 0,
            "scores": [{"indicator_id": l3[0]["id"], "score": 80}],
        }, principal=self.operator)
        self.assertEqual(200, draft.status)
        self.assertEqual("draft", draft.body["status"])
        self.assertEqual(1, draft.body["revision"])
        self.assertFalse(draft.body["weights_recalculated"])

        stale = self.api.put_score_sheet("E1", {
            "indicator_set_id": set_id, "status": "draft", "expected_revision": 0,
            "scores": [{"indicator_id": l3[0]["id"], "score": 81}],
        }, principal=self.operator)
        self.assertEqual(409, stale.status)

        incomplete = self.api.put_score_sheet("E1", {
            "indicator_set_id": set_id, "status": "submitted", "expected_revision": 1,
            "scores": [{"indicator_id": l3[0]["id"], "score": 80}],
        }, principal=self.operator)
        self.assertEqual(409, incomplete.status)
        self.assertEqual("INDICATOR_OPERATION_BLOCKED", incomplete.body["error"]["code"])

        submitted = self.api.put_score_sheet("E1", {
            "indicator_set_id": set_id, "status": "submitted", "expected_revision": 1,
            "scores": [{"indicator_id": x["id"], "score": 80} for x in l3],
        }, principal=self.operator)
        self.assertEqual(200, submitted.status)
        self.assertEqual("submitted", submitted.body["status"])

    def test_weights_report_frozen_rule_and_become_available_after_submit(self):
        response = self.api.weights(principal=self.viewer)
        self.assertEqual(200, response.status)
        self.assertEqual("unavailable", response.body["status"])
        self.assertEqual(
            "submitted_expert_mean_sibling_normalization_v1",
            response.body["calculation_rule"],
        )
        self.assertTrue(any(x["weight"] is None for x in response.body["items"]))

        self.api.create_expert({"expert_id": "E-W", "name": "Weight Expert"}, principal=self.admin)
        tree = self.api.tree(principal=self.viewer).body
        set_id = tree["indicator_set"]["id"]
        l3 = [x for x in tree["nodes"] if x["level"] == 3 and x["enabled"]]
        submitted = self.api.put_score_sheet("E-W", {
            "indicator_set_id": set_id,
            "status": "submitted",
            "expected_revision": 0,
            "scores": [{"indicator_id": x["id"], "score": 60} for x in l3],
        }, principal=self.operator)
        self.assertEqual(200, submitted.status)
        self.assertTrue(submitted.body["weights_recalculated"])
        weighted = self.api.weights(principal=self.viewer)
        self.assertEqual("available", weighted.body["status"])
        self.assertTrue(all(x["weight"] is not None for x in weighted.body["items"]))


    def test_publishing_custom_set_does_not_replace_system_default(self):
        default_tree = self.api.tree(principal=self.admin).body
        default_set = default_tree["indicator_set"]
        draft_response = self.api.create_draft({
            "source_indicator_set_id": default_set["id"],
            "name": "用户自建指标集",
            "version": "CUSTOM-1",
            "description": "custom",
            "expected_revision": default_set["revision"],
        }, principal=self.admin)
        draft = draft_response.body["indicator_set"]
        published = self.api.publish(draft["id"], {
            "expected_revision": draft["revision"],
        }, principal=self.admin)
        self.assertEqual(200, published.status)
        self.assertEqual("published", published.body["indicator_set"]["status"])
        self.assertFalse(published.body["indicator_set"]["is_default"])

        still_default = self.api.tree(principal=self.viewer).body["indicator_set"]
        self.assertEqual(default_set["id"], still_default["id"])
        self.assertTrue(still_default["is_default"])

        listed = self.api.list_sets(principal=self.viewer).body["items"]
        self.assertEqual(2, len(listed))
        self.assertEqual(1, sum(1 for item in listed if item["is_default"]))

    def test_viewer_cannot_edit_and_operator_cannot_manage_experts(self):
        denied = self.api.create_expert({"expert_id": "E2", "name": "E2"}, principal=self.operator)
        self.assertEqual(403, denied.status)
        self.assertEqual("PERMISSION_DENIED", denied.body["error"]["code"])


if __name__ == "__main__":
    unittest.main()
