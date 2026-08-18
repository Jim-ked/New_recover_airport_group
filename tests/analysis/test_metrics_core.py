from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.algorithm.runner import run_once
from backend.algorithm.snapshot_adapter import build_algorithm_input
from backend.analysis.metrics import (
    METRICS_SCHEMA_VERSION,
    MetricsBuildError,
    _build_demand_breakdown,
    build_metrics_core,
)
from backend.domain.solution import Solution, SortieChain
from original_algorithm_overlay.model.decision_vars import build_base_path_map
from tests.algorithm.test_runner import RunnerFakeModel, fixed_cluster_selector
from tests.algorithm.test_snapshot_adapter import make_snapshot


class MetricsCoreTests(unittest.TestCase):
    def _fixture(self):
        snapshot = make_snapshot()
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
        return snapshot, result, metrics

    def test_matches_single_run_frontend_core_facts_with_canonical_demand_breakdown(self):
        _snapshot, result, metrics = self._fixture()
        self.assertEqual(METRICS_SCHEMA_VERSION, metrics["schema_version"])
        self.assertEqual("R1", metrics["run_id"])
        self.assertEqual(15, metrics["time_axis"]["slot_minutes"])
        self.assertEqual(2, metrics["summary"]["selected_cluster_count"])
        self.assertEqual(1, metrics["summary"]["participating_airport_count"])
        self.assertEqual(1, metrics["summary"]["core_airport_count"])
        self.assertEqual(1, metrics["summary"]["mission_count"])
        self.assertEqual(2, metrics["summary"]["required_sorties_total"])
        self.assertEqual(2, metrics["summary"]["fulfilled_sorties_total"])
        self.assertEqual(0, metrics["summary"]["unmet_sorties_total"])
        self.assertEqual(0, metrics["summary"]["additional_sorties_total"])
        self.assertEqual(2, metrics["summary"]["scheduled_sorties_total"])
        self.assertEqual(1.0, metrics["summary"]["completion_ratio"])
        self.assertEqual(2, metrics["summary"]["returned_sorties_total"])
        self.assertEqual(
            {"airport_id": "A1", "sorties": 2, "share": 1.0},
            metrics["summary"]["max_airport_departure"],
        )
        self.assertNotIn("top", metrics["airports"])
        self.assertEqual(result.objective, metrics["technical"]["objective"])
        task = metrics["tasks"]["M1"]
        self.assertEqual(2, task["required_total"])
        self.assertEqual(2, task["fulfilled_total"])
        self.assertEqual(0, task["unmet_total"])
        self.assertEqual(0, task["additional_total"])
        self.assertEqual(2, task["scheduled_total"])
        self.assertEqual(1.0, task["completion_ratio"])

    def test_demand_breakdown_covers_under_exact_and_over_scheduling(self):
        cases = (
            (5, 3, {"fulfilled": 3, "unmet": 2, "additional": 0}),
            (5, 5, {"fulfilled": 5, "unmet": 0, "additional": 0}),
            (5, 8, {"fulfilled": 5, "unmet": 0, "additional": 3}),
        )
        for required, scheduled, expected in cases:
            with self.subTest(required=required, scheduled=scheduled):
                row = _build_demand_breakdown(
                    {"fighter": required}, {"fighter": scheduled}
                )
                self.assertEqual(expected["fulfilled"], row["fulfilled_by_aircraft"]["fighter"])
                self.assertEqual(expected["unmet"], row["unmet_by_aircraft"]["fighter"])
                self.assertEqual(expected["additional"], row["additional_by_aircraft"]["fighter"])
                self.assertEqual(scheduled, row["fulfilled_total"] + row["additional_total"])
                self.assertEqual(required, row["fulfilled_total"] + row["unmet_total"])

    def test_demand_breakdown_isolated_by_mission_and_aircraft_and_aggregates(self):
        rows = {
            "M1": _build_demand_breakdown(
                {"fighter": 5, "bomber": 2},
                {"fighter": 7, "bomber": 1},
            ),
            "M2": _build_demand_breakdown(
                {"fighter": 3, "transport": 4},
                {"fighter": 2, "transport": 6},
            ),
        }

        self.assertEqual(2, rows["M1"]["additional_by_aircraft"]["fighter"])
        self.assertEqual(1, rows["M1"]["unmet_by_aircraft"]["bomber"])
        self.assertEqual(1, rows["M2"]["unmet_by_aircraft"]["fighter"])
        self.assertEqual(2, rows["M2"]["additional_by_aircraft"]["transport"])

        totals = {
            key: sum(row[key] for row in rows.values())
            for key in (
                "required_total",
                "scheduled_total",
                "fulfilled_total",
                "unmet_total",
                "additional_total",
            )
        }
        self.assertEqual(totals["scheduled_total"], totals["fulfilled_total"] + totals["additional_total"])
        self.assertEqual(totals["required_total"], totals["fulfilled_total"] + totals["unmet_total"])

    def test_all_airports_are_preserved_even_when_one_has_zero_sorties(self):
        _snapshot, _result, metrics = self._fixture()
        self.assertEqual({"A1", "A2"}, set(metrics["airports"]))
        self.assertEqual(2, metrics["airports"]["A1"]["departures_total"])
        self.assertEqual(0, metrics["airports"]["A2"]["departures_total"])
        self.assertEqual(1.0, metrics["airports"]["A1"]["departure_share"])
        self.assertEqual(0.0, metrics["airports"]["A2"]["departure_share"])

    def test_timeline_uses_absolute_15_minute_windows_and_complete_chain_events(self):
        _snapshot, result, metrics = self._fixture()
        chain = result.solution.sortie_chains[0]
        windows = metrics["time_axis"]["windows"]
        dep_i = windows.index(chain.depart_window)
        ret_i = windows.index(chain.return_window)
        self.assertEqual(2, metrics["timeline"]["departures_total"][dep_i])
        self.assertEqual(2, metrics["timeline"]["returns_total"][ret_i])
        self.assertEqual(2, metrics["timeline"]["by_mission"]["M1"]["departures"][dep_i])
        self.assertEqual(2, metrics["timeline"]["by_aircraft"]["fighter"]["returns"][ret_i])

    def test_resource_usage_reuses_model_fact_formula_and_ratio_uses_initial_stock(self):
        _snapshot, result, metrics = self._fixture()
        chain = result.solution.sortie_chains[0]
        windows = metrics["time_axis"]["windows"]
        dep_i = windows.index(chain.depart_window)

        fuel = metrics["resources"]["by_airport"]["A1"]["FUEL-A"]
        material = metrics["resources"]["by_airport"]["A1"]["MAT-1"]
        # Path: 1 outbound slot + 1 work slot + 1 return slot = 0.75 h.
        # Base fuel = 1.5 * 0.75 = 1.125; confirmed reserve formula => / (1-0.2).
        # Two sorties consume 2.8125 units at the departure window.
        self.assertAlmostEqual(2.8125, fuel["consumed_increment"][dep_i], places=8)
        self.assertAlmostEqual(97.1875, fuel["remaining"][dep_i], places=8)
        self.assertAlmostEqual(0.971875, fuel["remaining_ratio_initial"][dep_i], places=8)
        self.assertAlmostEqual(1.0, material["consumed_increment"][dep_i], places=8)
        self.assertAlmostEqual(19.0, material["remaining"][dep_i], places=8)
        self.assertAlmostEqual(0.95, material["remaining_ratio_initial"][dep_i], places=8)
        self.assertEqual(
            {
                "ratio": 0.971875,
                "airport_id": "A1",
                "resource_type_id": "FUEL-A",
                "window": chain.depart_window,
                "scope": "participating_airports",
                "denominator": "initial_stock",
            },
            metrics["resources"]["category_min_remaining_ratio"]["fuel"],
        )
        fuel_timeline = metrics["resources"]["category_min_remaining_ratio_timeline"]["fuel"]
        self.assertEqual(len(metrics["time_axis"]["windows"]), len(fuel_timeline))
        self.assertAlmostEqual(0.971875, fuel_timeline[dep_i]["ratio"], places=8)
        self.assertEqual("A1", fuel_timeline[dep_i]["airport_id"])
        self.assertEqual("FUEL-A", fuel_timeline[dep_i]["resource_type_id"])
        self.assertEqual(chain.depart_window, fuel_timeline[dep_i]["window"])

    def test_capacity_uses_departure_and_arrival_factors_at_their_actual_windows(self):
        _snapshot, result, metrics = self._fixture()
        chain = result.solution.sortie_chains[0]
        windows = metrics["time_axis"]["windows"]
        dep_i = windows.index(chain.depart_window)
        ret_i = windows.index(chain.return_window)
        cap = metrics["airports"]["A1"]["capacity"]
        self.assertAlmostEqual(2.0, cap["used_departure"][dep_i])
        self.assertAlmostEqual(1.6, cap["used_arrival"][ret_i])
        self.assertAlmostEqual(0.4, cap["utilization"][dep_i])
        self.assertAlmostEqual(0.32, cap["utilization"][ret_i])

    def test_cross_return_collaboration_is_derived_from_complete_sortie_chain(self):
        _snapshot, _result, metrics = self._fixture()
        self.assertEqual(0, metrics["collaboration"]["cross_return_sorties"])
        self.assertEqual(0.0, metrics["collaboration"]["cross_return_ratio"])
        self.assertEqual(["A1"], metrics["collaboration"]["origin_airports"])
        self.assertEqual(["A1"], metrics["collaboration"]["return_airports"])
        self.assertEqual(["A1"], metrics["collaboration"]["participating_airports"])

    def test_confirmed_peak_is_native_15_minute_slot_and_hhi_is_raw_departure_share(self):
        _snapshot, result, metrics = self._fixture()
        chain = result.solution.sortie_chains[0]
        self.assertEqual(
            {"window": chain.depart_window, "sorties": 2, "slot_minutes": 15},
            metrics["summary"]["peak_departure_slot"],
        )
        self.assertEqual(1.0, metrics["collaboration"]["departure_hhi"])

    def test_aircraft_is_retained_recyclable_not_a_consumable_resource(self):
        _snapshot, result, metrics = self._fixture()
        chain = result.solution.sortie_chains[0]
        windows = metrics["time_axis"]["windows"]
        dep_i = windows.index(chain.depart_window)
        ready_i = windows.index(chain.ready_window)
        inv = metrics["aircraft_inventory"]
        self.assertEqual("retained_recyclable", inv["state_model"])
        row = inv["by_airport"]["A1"]["fighter"]
        self.assertEqual(2, row["baseline_initial_quantity"])
        self.assertEqual(2, row["available_before_departure"][dep_i])
        self.assertEqual(0.0, row["available_after_departure"][dep_i])
        self.assertEqual(2.0, row["in_use"][dep_i])
        self.assertEqual(2, row["ready_releases"][ready_i])
        self.assertEqual(2.0, row["available_before_departure"][ready_i])
        self.assertEqual(1.0, row["available_ratio_initial"][ready_i])
        self.assertEqual("consumable_stock_with_replenishment", metrics["resources"]["state_model"])

    def test_terminal_ready_boundary_stays_outside_operational_timelines(self):
        snapshot = make_snapshot()
        bundle = build_algorithm_input(snapshot)
        t_min, t_max = bundle.ds["range"]
        operational_slots = t_max - t_min + 1
        paths = build_base_path_map(bundle.ds, bundle.run_params).path_records
        path = next(
            row for row in paths
            if row.origin_airport_id == "A1"
            and row.return_airport_id == "A1"
            and row.ready_slot == operational_slots
        )
        chain = SortieChain(
            path_id="P/terminal-ready",
            origin_airport_id=path.origin_airport_id,
            mission_id=path.mission_id,
            return_airport_id=path.return_airport_id,
            aircraft_type=path.aircraft_type_id,
            depart_window=t_min + path.depart_slot,
            return_window=t_min + path.landing_slot,
            ready_window=t_min + path.ready_slot,
            sorties=1,
        )
        solution = Solution.build(
            run_id=snapshot.run_id,
            selected_cluster=("A1", "A2"),
            sortie_chains=(chain,),
        )

        metrics = build_metrics_core(snapshot, solution)

        windows = metrics["time_axis"]["windows"]
        self.assertEqual(list(range(t_min, t_max + 1)), windows)
        self.assertEqual(operational_slots, len(windows))
        self.assertEqual(1, metrics["timeline"]["departures_total"][path.depart_slot])
        self.assertEqual(1, metrics["timeline"]["returns_total"][path.landing_slot])
        aircraft = metrics["aircraft_inventory"]["by_airport"]["A1"]["fighter"]
        self.assertEqual([0] * operational_slots, aircraft["ready_releases"])
        self.assertEqual(1.0, aircraft["in_use"][-1])
        self.assertEqual(1.0, aircraft["available_before_departure"][-1])
        self.assertEqual(1.0, aircraft["available_after_departure"][-1])

    def test_ready_after_terminal_boundary_is_rejected_by_frozen_path_set(self):
        snapshot = make_snapshot()
        bundle = build_algorithm_input(snapshot)
        t_min, t_max = bundle.ds["range"]
        operational_slots = t_max - t_min + 1
        path = next(
            row for row in build_base_path_map(bundle.ds, bundle.run_params).path_records
            if row.ready_slot == operational_slots
        )
        solution = Solution.build(
            run_id=snapshot.run_id,
            selected_cluster=("A1", "A2"),
            sortie_chains=(SortieChain(
                path_id="P/ready-after-terminal",
                origin_airport_id=path.origin_airport_id,
                mission_id=path.mission_id,
                return_airport_id=path.return_airport_id,
                aircraft_type=path.aircraft_type_id,
                depart_window=t_min + path.depart_slot,
                return_window=t_min + path.landing_slot,
                ready_window=t_min + operational_slots + 1,
                sorties=1,
            ),),
        )

        with self.assertRaisesRegex(MetricsBuildError, "not present in the frozen RunSnapshot path set"):
            build_metrics_core(snapshot, solution)

    def test_solution_from_another_path_set_is_rejected_instead_of_guessed(self):
        snapshot, result, _metrics = self._fixture()
        row = result.solution.sortie_chains[0]
        bad = Solution.build(
            run_id="R1",
            selected_cluster=result.solution.selected_cluster,
            sortie_chains=[SortieChain(
                path_id="P/tampered",
                origin_airport_id=row.origin_airport_id,
                mission_id=row.mission_id,
                return_airport_id=row.return_airport_id,
                aircraft_type=row.aircraft_type,
                depart_window=row.depart_window + 99,
                return_window=row.return_window + 99,
                ready_window=row.ready_window + 99,
                sorties=row.sorties,
            )],
        )
        with self.assertRaisesRegex(MetricsBuildError, "not present"):
            build_metrics_core(snapshot, bad)


    def test_actual_replenishment_is_reported_as_flow_and_ratio_keeps_initial_denominator(self):
        from backend.domain.situation import ResourceReplenishment

        snapshot = make_snapshot(
            a1_replenishment_capacity=10,
            a1_replenishments=(ResourceReplenishment("FUEL-A", 4, 5),),
        )
        result = run_once(
            snapshot,
            cluster_selector_fn=fixed_cluster_selector,
            model_factory=RunnerFakeModel,
        )
        metrics = build_metrics_core(snapshot, result.solution)
        fuel = metrics["resources"]["by_airport"]["A1"]["FUEL-A"]
        chain = result.solution.sortie_chains[0]
        i = metrics["time_axis"]["windows"].index(chain.depart_window)
        self.assertEqual(10.0, fuel["replenishment_capacity_per_window"][i])
        self.assertEqual(5.0, fuel["replenishment_actual"][i])
        self.assertEqual(5.0, fuel["replenishment_cumulative"][i])
        self.assertEqual(100.0, fuel["damage_adjusted_base_boundary"][i])
        self.assertEqual(105.0, fuel["available_before_consumption"][i])
        self.assertAlmostEqual(102.1875, fuel["remaining"][i], places=8)
        # Confirmed denominator stays the frozen initial stock (100), not 105.
        self.assertAlmostEqual(1.021875, fuel["remaining_ratio_initial"][i], places=8)

    def test_zero_initial_stock_can_be_supplied_but_remaining_ratio_is_null(self):
        from backend.domain.situation import ResourceReplenishment

        snapshot = make_snapshot(
            a1_fuel_initial=0,
            a1_replenishment_capacity=10,
            a1_replenishments=(ResourceReplenishment("FUEL-A", 4, 5),),
        )
        result = run_once(
            snapshot,
            cluster_selector_fn=fixed_cluster_selector,
            model_factory=RunnerFakeModel,
        )
        metrics = build_metrics_core(snapshot, result.solution)
        fuel = metrics["resources"]["by_airport"]["A1"]["FUEL-A"]
        i = metrics["time_axis"]["windows"].index(result.solution.sortie_chains[0].depart_window)
        self.assertGreater(fuel["remaining"][i], 0)
        self.assertIsNone(fuel["remaining_ratio_initial"][i])


if __name__ == "__main__":
    unittest.main()
