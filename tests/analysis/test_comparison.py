from __future__ import annotations

import copy
import json
import unittest

from backend.algorithm.runner import run_once
from backend.analysis.comparison import (
    COMPARISON_SCHEMA_VERSION,
    ComparisonError,
    build_configuration_comparison,
    build_multi_scenario_comparison,
    build_r0_r1_r2_comparison,
    check_configuration_comparable,
    check_multi_scenario_comparable,
    check_objective_comparable,
    check_r0_r1_r2,
)
from backend.analysis.metrics import build_metrics_core
from backend.domain.damage import DamageScenario
from tests.algorithm.test_runner import RunnerFakeModel, fixed_cluster_selector
from tests.algorithm.test_snapshot_adapter import make_snapshot


def scenario() -> DamageScenario:
    return DamageScenario.from_mapping({
        "damage_scenario_id": "DS1",
        "name": "Damage",
        "category": "custom",
        "events": [{
            "event_id": "E1",
            "sequence": 0,
            "target": {"airport_id": "A2", "target_type": "airport", "target_id": None},
            "damage_type": "capacity_damage",
            "start_slot": 4,
            "end_slot": 6,
            "effect": {"closed": False, "remaining_capacity_per_window": 3},
            "recovery_mode": "instant",
            "recovery_duration_slots": None,
        }],
    })


def solve(snapshot):
    result = run_once(
        snapshot,
        cluster_selector_fn=fixed_cluster_selector,
        model_factory=RunnerFakeModel,
    )
    metrics = build_metrics_core(
        snapshot,
        result.solution,
        technical={"solver_status": result.solver_status, "objective": result.objective},
    )
    return result, metrics


class ComparisonTests(unittest.TestCase):
    def _roles(self):
        ds = scenario()
        common = (ds,)
        r0 = make_snapshot(cluster_enabled=False, available_scenarios=common, run_id="R0")
        r1 = make_snapshot(scenario=ds, cluster_enabled=False, available_scenarios=common, run_id="R1")
        r2 = make_snapshot(scenario=ds, cluster_enabled=True, available_scenarios=common, run_id="R2")
        return r0, r1, r2

    def test_r0_r1_r2_roles_and_strict_common_solver_controls(self):
        r0, r1, r2 = self._roles()
        check = check_r0_r1_r2(r0, r1, r2)
        self.assertTrue(check.comparable, check.reasons)

        ds = scenario()
        bad = make_snapshot(
            scenario=ds,
            available_scenarios=(ds,),
            cluster_enabled=True,
            mip_time_limit_s=30,
            run_id="R2B",
        )
        check = check_r0_r1_r2(r0, r1, bad)
        self.assertFalse(check.comparable)
        self.assertIn("run_config.mip_time_limit_s differs", check.reasons)

    def test_multi_scenario_allows_only_damage_selection_to_change(self):
        ds = scenario()
        common = (ds,)
        r0 = make_snapshot(cluster_enabled=False, available_scenarios=common, run_id="R0")
        r1 = make_snapshot(scenario=ds, cluster_enabled=False, available_scenarios=common, run_id="R1")
        self.assertTrue(check_multi_scenario_comparable(r0, r1).comparable)

        different_seed = make_snapshot(
            scenario=ds, cluster_enabled=False, algorithm_seed=99,
            available_scenarios=common, run_id="R1B"
        )
        check = check_multi_scenario_comparable(r0, different_seed)
        self.assertFalse(check.comparable)
        self.assertIn("run_config.algorithm_seed differs", check.reasons)

    def test_configuration_compare_allows_business_configuration_but_not_seed_or_damage(self):
        ds = scenario()
        common = (ds,)
        off = make_snapshot(scenario=ds, cluster_enabled=False, available_scenarios=common, run_id="OFF")
        on = make_snapshot(scenario=ds, cluster_enabled=True, available_scenarios=common, run_id="ON")
        self.assertTrue(check_configuration_comparable(off, on).comparable)

        bad_seed = make_snapshot(scenario=ds, cluster_enabled=True, algorithm_seed=7, available_scenarios=common, run_id="ON2")
        check = check_configuration_comparable(off, bad_seed)
        self.assertFalse(check.comparable)
        self.assertIn("run_config.algorithm_seed differs", check.reasons)

        no_damage = make_snapshot(cluster_enabled=True, available_scenarios=common, run_id="NO")
        check = check_configuration_comparable(on, no_damage)
        self.assertFalse(check.comparable)
        self.assertIn("damage_scenario_id differs", check.reasons)

    def test_project_airport_and_situation_ids_are_preserved_in_comparison_projection(self):
        base = make_snapshot(
            cluster_enabled=False,
            run_id="RUN-base",
            situation_id="ST001",
            airport_ids=("AP001", "AP002"),
        )
        configured = make_snapshot(
            cluster_enabled=False,
            preference_mode="resource_min",
            run_id="RUN-configured",
            situation_id="ST001",
            airport_ids=("AP001", "AP002"),
        )
        _base_result, base_metrics = solve(base)
        _configured_result, configured_metrics = solve(configured)
        comparison = build_configuration_comparison(
            [(base, base_metrics), (configured, configured_metrics)],
            baseline_run_id="RUN-base",
        )
        payload = json.dumps(comparison, ensure_ascii=False)

        self.assertNotIn("oa:", payload)
        self.assertEqual("AP001", next(iter(comparison["labels"]["airports"])))

    def test_backend_builds_roles_full_airport_rows_and_r1_r0_r2_r1_deltas(self):
        r0, r1, r2 = self._roles()
        _a0, m0 = solve(r0)
        _a1, m1 = solve(r1)
        _a2, m2 = solve(r2)

        # Make arithmetic visibly non-zero without asking the front end to derive it.
        m1 = copy.deepcopy(m1)
        m2 = copy.deepcopy(m2)
        m1["summary"]["participating_airport_count"] = 2
        m2["summary"]["participating_airport_count"] = 3
        m1["airports"]["A2"]["departures_total"] = 1
        m2["airports"]["A2"]["departures_total"] = 4
        m1["airports"]["A2"]["departure_share"] = 0.25
        m2["airports"]["A2"]["departure_share"] = 0.50

        out = build_r0_r1_r2_comparison(
            r0_snapshot=r0, r0_metrics=m0,
            r1_snapshot=r1, r1_metrics=m1,
            r2_snapshot=r2, r2_metrics=m2,
        )
        self.assertEqual(COMPARISON_SCHEMA_VERSION, out["schema_version"])
        self.assertEqual({"R0": "R0", "R1": "R1", "R2": "R2"}, out["roles"])
        self.assertEqual("R1-R0", out["definitions"]["damage_delta"])
        self.assertEqual("R2-R1", out["definitions"]["cluster_delta"])
        self.assertEqual({"A1", "A2"}, set(out["airports"]))
        self.assertEqual(1.0, out["airports"]["A2"]["departures_total"]["damage_delta"])
        self.assertEqual(3.0, out["airports"]["A2"]["departures_total"]["cluster_delta"])
        self.assertEqual(0.25, out["airports"]["A2"]["departure_share"]["damage_delta"])
        self.assertEqual(0.25, out["airports"]["A2"]["departure_share"]["cluster_delta"])
        self.assertEqual(1.0, out["summary"]["participating_airport_count"]["damage_delta"])
        self.assertEqual(1.0, out["summary"]["participating_airport_count"]["cluster_delta"])
        self.assertIn("comparison_summary", out)
        self.assertIn("difference_overview", out)
        self.assertIn("peak_sorties", out["difference_overview"])
        self.assertIn("by_airport", out["timeline"])
        self.assertIn("A1", out["timeline"]["by_airport"])
        self.assertIn("scheme", out)
        self.assertFalse(out["objective_comparable"])
        self.assertIn("run_config.core_airports differs", out["objective_comparability_reasons"])

    def test_all_comparison_modes_share_canonical_run_summaries_tasks_and_frozen_labels(self):
        r0, r1, r2 = self._roles()
        _a0, m0 = solve(r0)
        _a1, m1 = solve(r1)
        _a2, m2 = solve(r2)
        outputs = (
            build_r0_r1_r2_comparison(
                r0_snapshot=r0, r0_metrics=m0,
                r1_snapshot=r1, r1_metrics=m1,
                r2_snapshot=r2, r2_metrics=m2,
            ),
            build_multi_scenario_comparison([(r0, m0), (r1, m1)]),
            build_configuration_comparison(
                [(r1, m1), (r2, m2)], baseline_run_id="R1"
            ),
        )
        expected_fields = {
            "mission_count",
            "required_sorties_total",
            "scheduled_sorties_total",
            "returned_sorties_total",
            "selected_cluster_count",
            "participating_airport_count",
            "peak_departure_slot",
            "max_airport_departure",
            "minimum_resource_remaining",
            "departure_hhi",
            "cross_return_ratio",
        }
        metrics_by_run = {"R0": m0, "R1": m1, "R2": m2}

        for output in outputs:
            self.assertIn("run_summaries", output)
            self.assertIn("labels", output)
            for run_id, projection in output["run_summaries"].items():
                metrics = metrics_by_run[run_id]
                summary = metrics["summary"]
                self.assertEqual(expected_fields, set(projection))
                for field in (
                    "mission_count", "required_sorties_total",
                    "scheduled_sorties_total", "returned_sorties_total",
                    "selected_cluster_count", "participating_airport_count",
                ):
                    self.assertEqual(summary[field], projection[field])
                self.assertEqual(summary["peak_departure_slot"], projection["peak_departure_slot"])
                self.assertEqual(summary["max_airport_departure"], projection["max_airport_departure"])
                self.assertEqual(
                    metrics["collaboration"]["departure_hhi"], projection["departure_hhi"]
                )
                self.assertEqual(
                    metrics["collaboration"]["cross_return_ratio"],
                    projection["cross_return_ratio"],
                )

        payload = r0.to_dict()
        self.assertEqual(
            payload["situation"]["airports"][0]["airport"]["airport_name"],
            outputs[0]["labels"]["airports"][payload["situation"]["airports"][0]["airport"]["airport_id"]],
        )
        self.assertEqual(
            payload["situation"]["missions"][0]["name"],
            outputs[0]["labels"]["missions"][payload["situation"]["missions"][0]["mission_id"]],
        )
        self.assertEqual(
            payload["catalogs"]["aircraft_types"][0]["name"],
            outputs[0]["labels"]["aircraft"][payload["catalogs"]["aircraft_types"][0]["aircraft_type_id"]],
        )

        self.assertEqual(m0["tasks"]["M1"]["required_total"], outputs[0]["tasks"]["M1"]["required_total"]["R0"])
        self.assertEqual(m0["tasks"]["M1"]["scheduled_total"], outputs[0]["tasks"]["M1"]["scheduled_total"]["R0"])
        self.assertEqual(m0["tasks"]["M1"]["required_total"], outputs[1]["tasks"]["M1"]["R0"]["required_total"])
        self.assertEqual(m0["tasks"]["M1"]["scheduled_total"], outputs[1]["tasks"]["M1"]["R0"]["scheduled_total"])
        self.assertEqual(m1["tasks"]["M1"]["required_total"], outputs[2]["tasks"]["M1"]["R1"]["required_total"])
        self.assertEqual(m1["tasks"]["M1"]["scheduled_total"], outputs[2]["tasks"]["M1"]["R1"]["scheduled_total"])

    def test_missing_object_metric_stays_missing_instead_of_becoming_zero(self):
        r0, r1, r2 = self._roles()
        _a0, m0 = solve(r0)
        _a1, m1 = solve(r1)
        _a2, m2 = solve(r2)
        m0 = copy.deepcopy(m0)
        del m0["airports"]["A2"]["departure_share"]

        output = build_r0_r1_r2_comparison(
            r0_snapshot=r0, r0_metrics=m0,
            r1_snapshot=r1, r1_metrics=m1,
            r2_snapshot=r2, r2_metrics=m2,
        )

        self.assertIsNone(output["airports"]["A2"]["departure_share"]["R0"])
        self.assertIsNone(output["airports"]["A2"]["departure_share"]["damage_delta"])

    def test_scheduled_growth_after_full_demand_is_reported_as_additional_only(self):
        r0, r1, r2 = self._roles()
        _a0, m0 = solve(r0)
        _a1, m1 = solve(r1)
        _a2, m2 = solve(r2)
        for metrics, scheduled in ((m0, 2), (m1, 2), (m2, 5)):
            summary = metrics["summary"]
            summary.update({
                "required_sorties_total": 2,
                "fulfilled_sorties_total": 2,
                "unmet_sorties_total": 0,
                "additional_sorties_total": scheduled - 2,
                "scheduled_sorties_total": scheduled,
                "completion_ratio": 1.0,
            })

        out = build_r0_r1_r2_comparison(
            r0_snapshot=r0, r0_metrics=m0,
            r1_snapshot=r1, r1_metrics=m1,
            r2_snapshot=r2, r2_metrics=m2,
        )

        self.assertEqual(0.0, out["summary"]["fulfilled_sorties_total"]["cluster_delta"])
        self.assertEqual(0.0, out["summary"]["unmet_sorties_total"]["cluster_delta"])
        self.assertEqual(3.0, out["summary"]["additional_sorties_total"]["cluster_delta"])
        self.assertEqual(3.0, out["summary"]["scheduled_sorties_total"]["cluster_delta"])

    def test_unmet_reduction_and_completion_improvement_are_explicit(self):
        r0, r1, r2 = self._roles()
        _a0, m0 = solve(r0)
        _a1, m1 = solve(r1)
        _a2, m2 = solve(r2)
        for metrics, fulfilled in ((m0, 2), (m1, 1), (m2, 2)):
            summary = metrics["summary"]
            summary.update({
                "required_sorties_total": 2,
                "fulfilled_sorties_total": fulfilled,
                "unmet_sorties_total": 2 - fulfilled,
                "additional_sorties_total": 0,
                "scheduled_sorties_total": fulfilled,
                "completion_ratio": fulfilled / 2,
            })

        out = build_r0_r1_r2_comparison(
            r0_snapshot=r0, r0_metrics=m0,
            r1_snapshot=r1, r1_metrics=m1,
            r2_snapshot=r2, r2_metrics=m2,
        )

        self.assertEqual(-1.0, out["summary"]["unmet_sorties_total"]["cluster_delta"])
        self.assertEqual(1.0, out["summary"]["fulfilled_sorties_total"]["cluster_delta"])
        self.assertEqual(0.5, out["summary"]["completion_ratio"]["cluster_delta"])

    def test_objective_comparability_uses_coefficient_definition_not_cluster_toggle(self):
        ds = scenario()
        common = (ds,)
        same_a = make_snapshot(
            scenario=ds, cluster_enabled=False, available_scenarios=common, run_id="SAME-A"
        )
        same_b = make_snapshot(
            scenario=ds, cluster_enabled=False, available_scenarios=common, run_id="SAME-B"
        )
        self.assertTrue(check_objective_comparable(same_a, same_b).comparable)

        core_changed = make_snapshot(
            scenario=ds, cluster_enabled=True, available_scenarios=common, run_id="CORE"
        )
        check = check_objective_comparable(same_a, core_changed)
        self.assertFalse(check.comparable)
        self.assertIn("run_config.core_airports differs", check.reasons)

        weight_changed = make_snapshot(
            scenario=ds,
            cluster_enabled=False,
            preference_mode="time_min",
            available_scenarios=common,
            run_id="WEIGHT",
        )
        check = check_objective_comparable(same_a, weight_changed)
        self.assertFalse(check.comparable)
        self.assertIn("run_config.preference_mode differs", check.reasons)
        self.assertIn("run_config.alpha differs", check.reasons)

    def test_comparison_rejects_metrics_from_wrong_run(self):
        r0, r1, r2 = self._roles()
        _a0, m0 = solve(r0)
        _a1, m1 = solve(r1)
        _a2, m2 = solve(r2)
        m2 = copy.deepcopy(m2)
        m2["run_id"] = "WRONG"
        with self.assertRaisesRegex(ComparisonError, "does not match snapshot"):
            build_r0_r1_r2_comparison(
                r0_snapshot=r0, r0_metrics=m0,
                r1_snapshot=r1, r1_metrics=m1,
                r2_snapshot=r2, r2_metrics=m2,
            )


    def test_multi_scenario_builder_reports_extrema_without_best_ranking(self):
        ds1 = self._roles()[1].to_dict()["situation"]["damage_scenarios"][0]
        d1 = DamageScenario.from_mapping(ds1)
        d2 = DamageScenario.from_mapping({
            "damage_scenario_id": "DS2",
            "name": "Damage2",
            "category": "custom",
            "events": [],
        })
        s1 = make_snapshot(
            scenario=d1, cluster_enabled=False, available_scenarios=(d2,), run_id="S1"
        )
        s2 = make_snapshot(
            scenario=d2, cluster_enabled=False, available_scenarios=(d1,), run_id="S2"
        )
        _r1, m1 = solve(s1)
        _r2, m2 = solve(s2)
        m2 = copy.deepcopy(m2)
        m2["summary"]["peak_departure_slot"]["sorties"] = (
            m1["summary"]["peak_departure_slot"]["sorties"] + 3
        )
        m2["summary"]["participating_airport_count"] = (
            m1["summary"]["participating_airport_count"] + 1
        )
        out = build_multi_scenario_comparison([(s1, m1), (s2, m2)])
        self.assertEqual("multi_scenario", out["mode"])
        self.assertEqual(["S1", "S2"], out["run_ids"])
        self.assertEqual(
            ["S2"], out["difference_overview"]["peak_sorties"]["highest"]["run_ids"]
        )
        self.assertEqual({"A1", "A2"}, set(out["airports"]))
        self.assertNotIn("best", str(out).lower())

    def test_configuration_builder_uses_explicit_baseline_for_all_deltas(self):
        ds = scenario()
        base = make_snapshot(
            scenario=ds, cluster_enabled=False, available_scenarios=(ds,), run_id="BASE"
        )
        changed = make_snapshot(
            scenario=ds, cluster_enabled=True, available_scenarios=(ds,), run_id="CHANGED"
        )
        _rb, mb = solve(base)
        _rc, mc = solve(changed)
        mc = copy.deepcopy(mc)
        mc["summary"]["participating_airport_count"] = (
            mb["summary"]["participating_airport_count"] + 2
        )
        mc["airports"]["A2"]["departures_total"] = 4
        out = build_configuration_comparison(
            [(base, mb), (changed, mc)], baseline_run_id="BASE"
        )
        self.assertEqual("configuration", out["mode"])
        self.assertEqual("BASE", out["baseline_run_id"])
        self.assertEqual(
            2.0,
            out["summary_deltas_vs_baseline"]["CHANGED"]["participating_airport_count_delta"],
        )
        self.assertEqual(4.0, out["airports"]["A2"]["CHANGED"]["departures_total_delta"])
        self.assertEqual(0.0, out["airports"]["A2"]["BASE"]["departures_total_delta"])

    def test_comparison_builders_enforce_run_count_distinctness_and_baseline_membership(self):
        ds = scenario()
        s = make_snapshot(scenario=ds, cluster_enabled=False, available_scenarios=(ds,), run_id="ONE")
        _r, metrics = solve(s)
        with self.assertRaisesRegex(ComparisonError, "2 to 6"):
            build_multi_scenario_comparison([(s, metrics)])
        with self.assertRaisesRegex(ComparisonError, "distinct"):
            build_multi_scenario_comparison([(s, metrics), (s, metrics)])
        s2 = make_snapshot(scenario=ds, cluster_enabled=True, available_scenarios=(ds,), run_id="TWO")
        _r2, metrics2 = solve(s2)
        with self.assertRaisesRegex(ComparisonError, "baseline_run_id"):
            build_configuration_comparison(
                [(s, metrics), (s2, metrics2)], baseline_run_id="MISSING"
            )


if __name__ == "__main__":
    unittest.main()
