from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.algorithm.snapshot_adapter import build_algorithm_input
from tests.algorithm.test_snapshot_adapter import make_snapshot
from original_algorithm_overlay.model import decision_vars as dv
from original_algorithm_overlay.model import model_builder as mb


class Constraint:
    def __init__(self, left, op, right):
        self.left, self.op, self.right = as_expr(left), op, as_expr(right)


class Expr:
    def __init__(self, terms=None, const=0.0):
        self.terms = dict(terms or {})
        self.const = float(const)

    def _combine(self, other, sign=1.0):
        other = as_expr(other)
        terms = dict(self.terms)
        for name, coef in other.terms.items():
            terms[name] = terms.get(name, 0.0) + sign * coef
        return Expr(terms, self.const + sign * other.const)

    def __add__(self, other): return self._combine(other, 1.0)
    def __radd__(self, other): return as_expr(other)._combine(self, 1.0)
    def __sub__(self, other): return self._combine(other, -1.0)
    def __rsub__(self, other): return as_expr(other)._combine(self, -1.0)
    def __mul__(self, scalar):
        scalar = float(scalar)
        return Expr({k: scalar*v for k, v in self.terms.items()}, scalar*self.const)
    def __rmul__(self, scalar): return self.__mul__(scalar)
    def __le__(self, other): return Constraint(self, "<=", other)
    def __ge__(self, other): return Constraint(self, ">=", other)
    def __eq__(self, other): return Constraint(self, "==", other)


class Var(Expr):
    def __init__(self, name):
        self.name = name
        super().__init__({name: 1.0}, 0.0)



def as_expr(value):
    if isinstance(value, Expr):
        return value
    return Expr(const=float(value))


class FakeModel:
    last = None
    def __init__(self, name):
        self.name = name
        self.vars = []
        self.cons = {}
        self.params = {}
        self.objective = None
        FakeModel.last = self
    def addVar(self, *, lb, vtype, name):
        v = Var(name)
        self.vars.append((v, lb, vtype))
        return v
    def addCons(self, cons, *, name):
        self.cons[name] = cons
        return cons
    def setRealParam(self, key, value):
        self.params[key] = value
    def setObjective(self, expr, sense):
        self.objective = (as_expr(expr), sense)


class ModelBuilderOverlayTests(unittest.TestCase):
    def _fixture(self):
        b = build_algorithm_input(make_snapshot())
        maps = dv.build_path_map(b.ds, b.run_params, {"enabled": True, "S": ["A1", "A2"]})
        model, pack = mb.build_model(
            b.ds, b.run_params, maps, integer_vars=True, runtime=b.runtime, model_factory=FakeModel
        )
        return b, maps, model, pack

    def test_one_decision_variable_per_complete_path(self):
        b, maps, model, pack = self._fixture()
        self.assertEqual(len(maps.path_records), len(pack["x_path"]))
        names = [v.name for v, _, _ in model.vars]
        self.assertEqual(len(maps.path_records), sum(n.startswith("X_PATH__") for n in names))
        self.assertFalse(any(n.startswith("X_OUT__") or n.startswith("X_RET__") for n in names))
        self.assertTrue(pack["x_out"])
        self.assertTrue(pack["x_ret"])

    def test_positive_demand_is_always_materialized_as_constraint(self):
        _, _, model, _ = self._fixture()
        self.assertIn("REQ__M1__fighter", model.cons)

    def test_departure_and_arrival_capacity_share_same_path_variable(self):
        _, maps, model, pack = self._fixture()
        p = next(x for x in maps.path_records if x.origin_airport_id == "A1")
        name = pack["x_path"][p.key].name
        dep = model.cons[f"CAP__A1__{p.depart_slot}"].left.terms.get(name, 0.0)
        arr = model.cons[f"CAP__{p.return_airport_id}__{p.landing_slot}"].left.terms.get(name, 0.0)
        self.assertAlmostEqual(1.0, dep)
        self.assertAlmostEqual(0.8, arr)

    def test_fuel_is_one_shared_airport_pool_with_reserve_adjusted_coefficient(self):
        b, maps, model, pack = self._fixture()
        p = next(x for x in maps.path_records if x.origin_airport_id == "A1" and x.return_airport_id == "A1" and x.depart_slot == 0)
        name = pack["x_path"][p.key].name
        cons = model.cons["RES__A1__FUEL-A__0"]
        actual = cons.left.terms[name]
        hours = (p.outbound_flight_slots + p.tau_work_windows + p.return_flight_slots) * 0.25
        expected = (1.5 * hours) / (1.0 - 0.2)
        self.assertAlmostEqual(expected, actual)
        self.assertFalse(any(key.startswith("FUEL__A1__fighter") for key in model.cons))

    def test_aggregate_xout_can_keep_cluster_eval_read_compatibility(self):
        _, maps, _, pack = self._fixture()
        p = maps.path_records[0]
        key = (p.origin_airport_id, p.mission_id, p.aircraft_type_id, p.depart_slot)
        expr = pack["x_out"][key]
        same = [x for x in maps.path_records if (x.origin_airport_id, x.mission_id, x.aircraft_type_id, x.depart_slot) == key]
        self.assertEqual(len(same), len([v for v in expr.terms.values() if v != 0]))


if __name__ == "__main__":
    unittest.main()
