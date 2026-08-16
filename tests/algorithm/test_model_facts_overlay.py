from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.algorithm.snapshot_adapter import build_algorithm_input
from tests.algorithm.test_snapshot_adapter import make_snapshot
from original_algorithm_overlay.model import decision_vars as dv
from original_algorithm_overlay.model import model_facts as mf


class ModelFactsOverlayTests(unittest.TestCase):
    def _fixture(self):
        b = build_algorithm_input(make_snapshot())
        maps = dv.build_path_map(b.ds, b.run_params, {"enabled": True, "S": ["A1", "A2"]})
        return b, maps

    def test_positive_demand_has_paths(self):
        b, maps = self._fixture()
        mf.validate_hard_demand_paths(b.ds, maps)
        rows = mf.demand_rows(b.ds, maps)
        self.assertEqual(2, rows[("M1", "fighter")][0])
        self.assertTrue(rows[("M1", "fighter")][1])

    def test_no_path_for_positive_demand_fails_fast(self):
        b, maps = self._fixture()
        maps.path_records = []
        with self.assertRaisesRegex(mf.ModelFactError, "no feasible path"):
            mf.validate_hard_demand_paths(b.ds, maps)

    def test_resource_consumption_uses_complete_sortie_flight_and_work(self):
        b, maps = self._fixture()
        p = next(x for x in maps.path_records if x.origin_airport_id == "A1" and x.return_airport_id == "A1")
        rows = {r.resource_type_id: r.amount for r in mf.path_resource_use(p, b.run_params)}
        expected_hours = (p.outbound_flight_slots + p.tau_work_windows + p.return_flight_slots) * 0.25
        self.assertAlmostEqual((1.5 * expected_hours) / (1.0 - 0.2), rows["FUEL-A"])
        self.assertAlmostEqual(0.5, rows["MAT-1"])


    def test_fuel_reserve_ratio_is_applied_to_demand_not_stock_limit(self):
        b, maps = self._fixture()
        p = next(x for x in maps.path_records if x.origin_airport_id == "A1" and x.return_airport_id == "A1" and x.depart_slot == 0)
        hours = (p.outbound_flight_slots + p.tau_work_windows + p.return_flight_slots) * 0.25
        base = 1.5 * hours
        actual = base / (1.0 - 0.2)
        # Pick stock strictly between base and reserve-adjusted demand: it must fail.
        limit = (2.0 * base + 2.0 * actual) / 2.0
        b.ds["timeview"]["resources"]["A1"]["FUEL-A"] = [limit] * b.ds["timeview"]["T"]
        with self.assertRaisesRegex(mf.ModelFactError, "shared resource violated"):
            mf.validate_schedule_base(b.ds, maps, b.run_params, {p.key: 2})

    def test_invalid_reserve_ratio_fails_fast(self):
        b, maps = self._fixture()
        p = maps.path_records[0]
        b.run_params["aircrafts"]["fighter"]["reserve_ratio"] = 1.0
        with self.assertRaisesRegex(mf.ModelFactError, "0 <= r < 1"):
            mf.path_resource_use(p, b.run_params)

    def test_capacity_uses_separate_departure_and_arrival_factors(self):
        b, maps = self._fixture()
        dep, arr = mf.capacity_coefficients(maps, b.run_params)
        p = maps.path_records[0]
        self.assertEqual(1.0, dep[(p.origin_airport_id, p.depart_slot, p.key)])
        self.assertEqual(0.8, arr[(p.return_airport_id, p.landing_slot, p.key)])

    def test_aircraft_flow_indexes_use_full_path_identity(self):
        b, maps = self._fixture()
        departures, ready = mf.aircraft_events(maps)
        p = maps.path_records[0]
        self.assertIn(p.key, departures[(p.origin_airport_id, p.aircraft_type_id, p.depart_slot)])
        self.assertIn(p.key, ready[(p.return_airport_id, p.aircraft_type_id, p.ready_slot)])

    def test_objective_preserves_original_three_part_structure(self):
        b, maps = self._fixture()
        rows = mf.objective_coefficients(b.ds, maps, b.run_params, b.runtime)
        p = next(x for x in maps.path_records if x.origin_airport_id == "A1")
        c = rows[p.key]
        # A1 is core -> 2.0; fighter user weight = 1.2.
        self.assertAlmostEqual(2.4, c.f1)
        self.assertGreaterEqual(c.f2, 0.0)
        self.assertLessEqual(c.f3, 1.0)
        weights = mf.resolved_alpha(b.runtime)
        self.assertEqual((0.8, 0.1, 0.1), (weights.sortie, weights.resource, weights.time))

    def test_independent_schedule_validator_checks_confirmed_invariants(self):
        b, maps = self._fixture()
        p = next(x for x in maps.path_records if x.origin_airport_id == "A1" and x.return_airport_id == "A1" and x.depart_slot == 0)
        mf.validate_schedule_base(b.ds, maps, b.run_params, {p.key: 2})

        with self.assertRaisesRegex(mf.ModelFactError, "hard demand violated"):
            mf.validate_schedule_base(b.ds, maps, b.run_params, {p.key: 1})

        b.ds["timeview"]["cap"]["A1"][0] = 1
        with self.assertRaisesRegex(mf.ModelFactError, "capacity violated"):
            mf.validate_schedule_base(b.ds, maps, b.run_params, {p.key: 2})

    def test_shared_resource_validator_does_not_split_pool_by_aircraft(self):
        b, maps = self._fixture()
        p = next(x for x in maps.path_records if x.origin_airport_id == "A1" and x.return_airport_id == "A1" and x.depart_slot == 0)
        b.ds["timeview"]["resources"]["A1"]["FUEL-A"] = [1.0] * b.ds["timeview"]["T"]
        with self.assertRaisesRegex(mf.ModelFactError, "shared resource violated"):
            mf.validate_schedule_base(b.ds, maps, b.run_params, {p.key: 2})


    def test_replenishment_capacity_alone_does_not_create_stock_but_actual_schedule_does(self):
        from backend.domain.situation import ResourceReplenishment

        no_supply = build_algorithm_input(
            make_snapshot(a1_fuel_initial=0, a1_replenishment_capacity=10)
        )
        maps_no = dv.build_path_map(
            no_supply.ds, no_supply.run_params, {"enabled": True, "S": ["A1", "A2"]}
        )
        p_no = next(
            x for x in maps_no.path_records
            if x.origin_airport_id == "A1" and x.return_airport_id == "A1" and x.depart_slot == 0
        )
        with self.assertRaisesRegex(mf.ModelFactError, "shared resource violated"):
            mf.validate_schedule_base(no_supply.ds, maps_no, no_supply.run_params, {p_no.key: 2})

        supplied = build_algorithm_input(
            make_snapshot(
                a1_fuel_initial=0,
                a1_replenishment_capacity=10,
                a1_replenishments=(ResourceReplenishment("FUEL-A", 4, 5),),
            )
        )
        maps_yes = dv.build_path_map(
            supplied.ds, supplied.run_params, {"enabled": True, "S": ["A1", "A2"]}
        )
        p_yes = next(
            x for x in maps_yes.path_records
            if x.origin_airport_id == "A1" and x.return_airport_id == "A1" and x.depart_slot == 0
        )
        mf.validate_schedule_base(supplied.ds, maps_yes, supplied.run_params, {p_yes.key: 2})


if __name__ == "__main__":
    unittest.main()
