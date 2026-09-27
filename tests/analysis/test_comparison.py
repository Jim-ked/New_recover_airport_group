from __future__ import annotations

import copy
import hashlib
import json
import unittest

from backend.algorithm.runner import run_once
from backend.analysis.comparison import (
    COMPARISON_SCHEMA_VERSION,
    ComparisonError,
    build_configuration_comparison,
    build_exploratory_comparison,
    build_multi_scenario_comparison,
    build_object_comparison,
    build_r0_r1_r2_comparison,
    check_configuration_comparable,
    check_exploratory_comparable,
    check_multi_scenario_comparable,
    check_objective_comparable,
    check_r0_r1_r2,
)
from backend.analysis.metrics import build_metrics_core
from backend.domain.damage import DamageScenario
from backend.domain.run_snapshot import RunSnapshot
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


def early_scenario() -> DamageScenario:
    return DamageScenario.from_mapping({
        "damage_scenario_id": "DS-EARLY",
        "name": "Early damage",
        "category": "custom",
        "events": [{
            "event_id": "E-EARLY",
            "sequence": 0,
            "target": {"airport_id": "A2", "target_type": "airport", "target_id": None},
            "damage_type": "capacity_damage",
            "start_slot": 1,
            "end_slot": 3,
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
        technical={
            "solver_status": result.solver_status,
            "objective": result.objective,
            "best_bound": result.best_bound,
            "gap": result.gap,
            "solve_time_s": result.solve_time_s,
            "cluster_lp_objective": result.cluster_lp_objective,
            "f1": result.f1,
            "f2": result.f2,
            "f3": result.f3,
        },
    )
    return result, metrics


def as_archived_v5(snapshot: RunSnapshot) -> RunSnapshot:
    payload = snapshot.to_dict()
    payload["schema"] = "run_input_snapshot_v5"
    payload["run_config"]["resource_inventory_model"] = "physical_inventory_v1"
    payload["dynamic_inventory"] = {
        "semantics": "physical_inventory_v1",
        "storage_capacity": {
            item["airport"]["airport_id"]: {
                stock["resource_type_id"]: float(stock["initial_quantity"])
                for stock in item["operational_profile"]["resource_stocks"]
            }
            for item in payload["situation"]["airports"]
        },
    }
    payload_json = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return RunSnapshot(
        run_id=snapshot.run_id,
        situation_id=snapshot.situation_id,
        content_hash=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        payload_json=payload_json,
    )


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

    def test_archived_v5_roles_use_read_only_comparison_projection(self):
        r0, r1, r2 = (as_archived_v5(item) for item in self._roles())

        check = check_r0_r1_r2(r0, r1, r2)

        self.assertTrue(check.comparable, check.reasons)

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

    def test_unused_damage_scenario_does_not_break_effective_input_comparability(self):
        base = make_snapshot(
            cluster_enabled=False,
            available_scenarios=(),
            run_id="BASE",
        )
        appended = make_snapshot(
            cluster_enabled=False,
            available_scenarios=(early_scenario(),),
            run_id="APPENDED",
        )

        self.assertNotEqual(
            base.to_dict()["situation_content_hash"],
            appended.to_dict()["situation_content_hash"],
        )
        self.assertTrue(
            check_multi_scenario_comparable(base, appended).comparable,
            check_multi_scenario_comparable(base, appended).reasons,
        )
        self.assertTrue(
            check_configuration_comparable(base, appended).comparable,
            check_configuration_comparable(base, appended).reasons,
        )

    def test_selected_damage_that_changes_effective_horizon_is_not_strictly_comparable(self):
        damage = early_scenario()
        base = make_snapshot(
            cluster_enabled=False,
            available_scenarios=(damage,),
            run_id="BASE",
        )
        changed = make_snapshot(
            scenario=damage,
            cluster_enabled=False,
            available_scenarios=(damage,),
            run_id="CHANGED",
        )

        check = check_multi_scenario_comparable(base, changed)

        self.assertFalse(check.comparable)
        self.assertIn("effective time range differs", check.reasons)

    def test_exploratory_comparison_accepts_task_pressure_change_and_lists_it(self):
        base = make_snapshot(
            cluster_enabled=False,
            mission_required_sorties=2,
            run_id="BASE",
        )
        pressure = make_snapshot(
            cluster_enabled=False,
            mission_required_sorties=3,
            run_id="PRESSURE",
        )

        check = check_exploratory_comparable(base, pressure)

        self.assertTrue(check.comparable)
        self.assertIn("base mission inputs differ", check.differences)

        _base_result, base_metrics = solve(base)
        _pressure_result, pressure_metrics = solve(pressure)
        out = build_exploratory_comparison(
            [(base, base_metrics), (pressure, pressure_metrics)],
            baseline_run_id="BASE",
        )
        self.assertEqual("exploratory", out["mode"])
        self.assertTrue(out["descriptive_only"])
        self.assertIn(
            "base mission inputs differ",
            out["comparability"]["PRESSURE"]["differences"],
        )
        self.assertEqual(
            list(base_metrics["collaboration"]["selected_cluster"]),
            out["scheme"]["BASE"]["selected_cluster"],
        )
        self.assertEqual(
            base_metrics["collaboration"]["cross_return_ratio"],
            out["scheme"]["BASE"]["cross_return_ratio"],
        )

    def test_exploratory_summary_collection_is_not_limited_by_legacy_chart_count(self):
        base = make_snapshot(cluster_enabled=False, run_id="EXP-0")
        _result, base_metrics = solve(base)
        rows = []
        for index in range(7):
            snapshot = base if index == 0 else base.clone_for_run(f"EXP-{index}")
            metrics = copy.deepcopy(base_metrics)
            metrics["run_id"] = snapshot.run_id
            rows.append((snapshot, metrics))

        out = build_exploratory_comparison(rows, baseline_run_id="EXP-0")

        self.assertEqual(7, len(out["run_ids"]))
        self.assertTrue(out["object_query"]["required_for_timelines"])

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
        self.assertTrue(out["objective_comparable"])
        self.assertEqual([], out["objective_comparability_reasons"])

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
            "fulfilled_sorties_total",
            "unmet_sorties_total",
            "additional_sorties_total",
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
            scenario=ds,
            cluster_enabled=True,
            available_scenarios=common,
            core_airport_reward_weight=9.0,
            run_id="CORE",
        )
        check = check_objective_comparable(same_a, core_changed)
        self.assertTrue(check.comparable)
        self.assertEqual((), check.reasons)

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
        self.assertNotIn("best_run_id", out)
        self.assertNotIn("recommendation", out)

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

    def test_solver_facts_and_maximization_bounds_are_compared_conservatively(self):
        base = make_snapshot(cluster_enabled=False, run_id="BASE")
        changed = make_snapshot(cluster_enabled=True, run_id="CHANGED")
        _base_result, base_metrics = solve(base)
        _changed_result, changed_metrics = solve(changed)
        base_metrics["technical"].update({
            "solver_status": "gaplimit",
            "objective": 10.0,
            "best_bound": 11.0,
            "gap": 0.1,
            "solve_time_s": 30.0,
            "f1": 20.0,
            "f2": 2.0,
            "f3": 1.0,
        })
        changed_metrics["technical"].update({
            "solver_status": "optimal",
            "objective": 12.0,
            "best_bound": 12.0,
            "gap": 0.0,
            "solve_time_s": 8.0,
            "f1": 24.0,
            "f2": 2.5,
            "f3": 1.2,
        })

        out = build_configuration_comparison(
            [(base, base_metrics), (changed, changed_metrics)],
            baseline_run_id="BASE",
        )

        solver = out["solver_comparison"]
        self.assertEqual("gaplimit", solver["by_run"]["BASE"]["solver_status"])
        self.assertEqual(2.0, solver["by_run"]["CHANGED"]["objective_delta_vs_baseline"])
        self.assertEqual(
            [{
                "higher_run_id": "CHANGED",
                "lower_run_id": "BASE",
                "higher_lower_bound": 12.0,
                "lower_upper_bound": 11.0,
            }],
            solver["proven_strict_orderings"],
        )

        del base_metrics["technical"]["best_bound"]
        del base_metrics["technical"]["gap"]
        compatible = build_configuration_comparison(
            [(base, base_metrics), (changed, changed_metrics)],
            baseline_run_id="BASE",
        )["solver_comparison"]
        self.assertIn("best_bound", compatible["by_run"]["BASE"]["missing_fields"])
        self.assertIn("gap", compatible["by_run"]["BASE"]["missing_fields"])
        self.assertEqual([], compatible["proven_strict_orderings"])

    def test_objective_delta_is_suppressed_when_objective_definition_differs(self):
        base = make_snapshot(
            cluster_enabled=False,
            preference_mode="sortie_max",
            run_id="BASE",
        )
        changed = make_snapshot(
            cluster_enabled=False,
            preference_mode="time_min",
            run_id="CHANGED",
        )
        _base_result, base_metrics = solve(base)
        _changed_result, changed_metrics = solve(changed)

        out = build_configuration_comparison(
            [(base, base_metrics), (changed, changed_metrics)],
            baseline_run_id="BASE",
        )["solver_comparison"]

        self.assertFalse(out["objective_definition_comparable"])
        self.assertIsNone(out["by_run"]["CHANGED"]["objective_delta_vs_baseline"])
        self.assertEqual([], out["proven_strict_orderings"])

    def test_task_object_query_returns_aircraft_breakdown_baseline_and_delta(self):
        base = make_snapshot(cluster_enabled=False, run_id="BASE")
        changed = make_snapshot(cluster_enabled=True, run_id="CHANGED")
        base_result, base_metrics = solve(base)
        changed_result, changed_metrics = solve(changed)
        task = changed_metrics["tasks"]["M1"]
        task.update({
            "scheduled_by_aircraft": {"fighter": 1},
            "fulfilled_by_aircraft": {"fighter": 1},
            "unmet_by_aircraft": {"fighter": 1},
            "additional_by_aircraft": {"fighter": 0},
            "scheduled_total": 1,
            "fulfilled_total": 1,
            "unmet_total": 1,
            "additional_total": 0,
        })

        out = build_object_comparison(
            [
                (base, base_result.solution.to_dict(), base_metrics),
                (changed, changed_result.solution.to_dict(), changed_metrics),
            ],
            comparison_type="configuration",
            baseline_run_id="BASE",
            object_type="task",
            object_id="M1",
        )

        self.assertEqual(2, out["baseline"]["absolute"]["by_aircraft"]["fighter"]["required"])
        self.assertEqual(
            -1.0,
            out["runs"]["CHANGED"]["delta_vs_baseline"]["by_aircraft"]["fighter"]["scheduled"],
        )
        self.assertEqual(
            1.0,
            out["runs"]["CHANGED"]["delta_vs_baseline"]["by_aircraft"]["fighter"]["unmet"],
        )

    def test_airport_resource_and_aircraft_queries_return_only_selected_timelines(self):
        base = make_snapshot(cluster_enabled=False, run_id="BASE")
        changed = make_snapshot(cluster_enabled=True, run_id="CHANGED")
        base_result, base_metrics = solve(base)
        changed_result, changed_metrics = solve(changed)
        rows = [
            (base, base_result.solution.to_dict(), base_metrics),
            (changed, changed_result.solution.to_dict(), changed_metrics),
        ]

        airport = build_object_comparison(
            rows,
            comparison_type="configuration",
            baseline_run_id="BASE",
            object_type="airport",
            object_id="A1",
        )
        self.assertEqual(
            base_metrics["airports"]["A1"]["capacity"]["used_total"],
            airport["baseline"]["absolute"]["capacity"]["used_total"],
        )

        resource = build_object_comparison(
            rows,
            comparison_type="configuration",
            baseline_run_id="BASE",
            object_type="resource",
            airport_id="A1",
            resource_type_id="FUEL-A",
        )
        self.assertEqual(
            base_metrics["resources"]["by_airport"]["A1"]["FUEL-A"]["consumed_increment"],
            resource["baseline"]["absolute"]["consumed_increment"],
        )
        self.assertEqual(
            [
                base_metrics["resources"]["by_airport"]["A1"]["FUEL-A"]["initial"] - value
                for value in base_metrics["resources"]["by_airport"]["A1"]["FUEL-A"]["damage_adjusted_base_boundary"]
            ],
            resource["baseline"]["absolute"]["damage_adjusted_loss"],
        )
        self.assertFalse(resource["baseline"]["absolute"]["permanent_loss_recorded"])
        self.assertIsNone(resource["baseline"]["absolute"]["permanent_loss"])

        recorded_zero = copy.deepcopy(base_metrics)
        recorded_zero["resources"]["by_airport"]["A1"]["FUEL-A"]["permanent_loss"] = [
            0.0 for _ in recorded_zero["time_axis"]["windows"]
        ]
        resource_with_zero = build_object_comparison(
            [
                (base, base_result.solution.to_dict(), recorded_zero),
                (changed, changed_result.solution.to_dict(), changed_metrics),
            ],
            comparison_type="exploratory",
            baseline_run_id="BASE",
            object_type="resource",
            airport_id="A1",
            resource_type_id="FUEL-A",
        )
        self.assertTrue(resource_with_zero["baseline"]["absolute"]["permanent_loss_recorded"])
        self.assertEqual(
            [0.0 for _ in recorded_zero["time_axis"]["windows"]],
            resource_with_zero["baseline"]["absolute"]["permanent_loss"],
        )

        aircraft = build_object_comparison(
            rows,
            comparison_type="configuration",
            baseline_run_id="BASE",
            object_type="aircraft",
            airport_id="A1",
            aircraft_type_id="fighter",
        )
        self.assertEqual(
            base_metrics["aircraft_inventory"]["by_airport"]["A1"]["fighter"]["ready_releases"],
            aircraft["baseline"]["absolute"]["ready_releases"],
        )

    def test_collaboration_query_matches_complete_chain_business_key_not_path_id(self):
        base = make_snapshot(cluster_enabled=False, run_id="BASE")
        changed = make_snapshot(cluster_enabled=True, run_id="CHANGED")
        base_result, base_metrics = solve(base)
        changed_result, changed_metrics = solve(changed)
        changed_solution = copy.deepcopy(base_result.solution.to_dict())
        changed_solution["run_id"] = "CHANGED"
        changed_solution["sortie_chains"][0]["path_id"] = "RUN-LOCAL-PATH-ID"

        same_chain = build_object_comparison(
            [
                (base, base_result.solution.to_dict(), base_metrics),
                (changed, changed_solution, changed_metrics),
            ],
            comparison_type="configuration",
            baseline_run_id="BASE",
            object_type="collaboration",
        )
        self.assertEqual(1, len(same_chain["chain_changes"]))
        self.assertNotIn("path_id", same_chain["chain_changes"][0]["chain"])
        self.assertEqual(
            0.0,
            same_chain["chain_changes"][0]["by_run"]["CHANGED"]["delta_vs_baseline"],
        )

        changed_solution["sortie_chains"][0]["return_airport_id"] = "A2"
        cross_return = build_object_comparison(
            [
                (base, base_result.solution.to_dict(), base_metrics),
                (changed, changed_solution, changed_metrics),
            ],
            comparison_type="configuration",
            baseline_run_id="BASE",
            object_type="collaboration",
        )
        self.assertEqual(2, len(cross_return["chain_changes"]))
        self.assertTrue(any(row["chain"]["cross_airport_return"] for row in cross_return["chain_changes"]))

        mission_id = changed_solution["sortie_chains"][0]["mission_id"]
        aircraft_type_id = changed_solution["sortie_chains"][0]["aircraft_type"]
        filtered = build_object_comparison(
            [
                (base, base_result.solution.to_dict(), base_metrics),
                (changed, changed_solution, changed_metrics),
            ],
            comparison_type="configuration",
            baseline_run_id="BASE",
            object_type="collaboration",
            object_id=mission_id,
            airport_id="A2",
            aircraft_type_id=aircraft_type_id,
        )
        self.assertEqual(1, len(filtered["chain_changes"]))
        self.assertEqual("A2", filtered["chain_changes"][0]["chain"]["return_airport_id"])
        self.assertEqual(
            0.0,
            filtered["chain_changes"][0]["by_run"]["BASE"]["sorties"],
        )

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
