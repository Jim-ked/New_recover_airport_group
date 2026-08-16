from __future__ import annotations

import importlib.util
import pathlib
import unittest

from backend.algorithm.snapshot_adapter import build_algorithm_input
from tests.algorithm.test_snapshot_adapter import make_snapshot


MODULE_PATH = pathlib.Path(__file__).resolve().parents[2] / "original_algorithm_overlay" / "model" / "decision_vars.py"
spec = importlib.util.spec_from_file_location("decision_vars_overlay", MODULE_PATH)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class DecisionVarsOverlayTests(unittest.TestCase):
    def _input(self):
        bundle = build_algorithm_input(make_snapshot())
        return bundle.ds, bundle.run_params

    def test_return_airport_must_support_aircraft_even_when_quantity_zero_is_allowed(self):
        ds, rp = self._input()
        # A2 supports fighter with quantity=0, therefore it is a valid return airport.
        bm = mod.build_base_path_map(ds, rp)
        self.assertTrue(any(p.origin_airport_id == "A1" and p.return_airport_id == "A2" for p in bm.path_records))

        # Remove support relation from A2 entirely: cross-return path must disappear.
        ds["static"]["airports"][1]["supported_aircraft"] = {}
        ds["static"]["airports"][1]["tau_reset"] = {}
        bm2 = mod.build_base_path_map(ds, rp)
        self.assertFalse(any(p.return_airport_id == "A2" and p.aircraft_type_id == "fighter" for p in bm2.path_records))

    def test_max_range_is_enforced_per_leg(self):
        ds, rp = self._input()
        rp["aircrafts"]["fighter"]["max_range"] = 110.0
        # A1->M1=100 is valid; M1->A2=120 is not.
        bm = mod.build_base_path_map(ds, rp)
        self.assertTrue(any(p.origin_airport_id == "A1" and p.return_airport_id == "A1" for p in bm.path_records))
        self.assertFalse(any(p.return_airport_id == "A2" for p in bm.path_records))

    def test_navigation_delay_is_time_specific_not_global_max(self):
        ds, rp = self._input()
        T = ds["timeview"]["T"]
        ds["timeview"]["radar_out_delay"]["A1"] = [0] * T
        ds["timeview"]["radar_out_delay"]["A1"][1] = 1
        bm = mod.build_base_path_map(ds, rp)
        rows0 = [p for p in bm.path_records if p.origin_airport_id == "A1" and p.depart_slot == 0]
        rows1 = [p for p in bm.path_records if p.origin_airport_id == "A1" and p.depart_slot == 1]
        self.assertTrue(rows0)
        self.assertTrue(rows1)
        self.assertTrue(all(p.departure_delay_slots == 0 for p in rows0))
        self.assertTrue(all(p.departure_delay_slots == 1 for p in rows1))

    def test_cluster_filter_preserves_original_semantics(self):
        ds, rp = self._input()
        bm = mod.build_base_path_map(ds, rp)
        no_cluster = mod.build_path_map_from_base(bm, None)
        self.assertTrue(all(p.origin_airport_id == p.return_airport_id for p in no_cluster.path_records))

        grouped = mod.build_path_map_from_base(bm, {"enabled": True, "S": ["A1", "A2"]})
        self.assertTrue(any(p.origin_airport_id == "A1" and p.return_airport_id == "A2" for p in grouped.path_records))

    def test_full_path_index_is_retained_without_removing_old_indexes(self):
        ds, rp = self._input()
        maps = mod.build_path_map(ds, rp, {"enabled": True, "S": ["A1", "A2"]})
        idx = mod.build_var_index(maps, ds)
        self.assertIn("XPATH", idx)
        self.assertIn("XOUT", idx)
        self.assertIn("XRET", idx)
        self.assertEqual(len(idx["XPATH"]), len(set(idx["XPATH"])))
        self.assertGreater(len(idx["XPATH"]), len(idx["XOUT"]))

    def test_ontime_score_uses_half_open_window(self):
        ds, rp = self._input()
        bm = mod.build_base_path_map(ds, rp)
        # Mission window becomes [0,4) after cropping. Outbound flight A1->M1 takes 1 slot.
        row = next(p for p in bm.path_records if p.origin_airport_id == "A1" and p.depart_slot == 0)
        self.assertEqual(1, row.mission_arrival_slot)
        self.assertEqual(1.0, row.ontime_score)


if __name__ == "__main__":
    unittest.main()
