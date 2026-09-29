# -*- coding: utf-8 -*-
"""Path-variable model builder for the existing SA -> LP -> MIP chain.

This is an in-place replacement for the old ``model/model_builder.py`` semantics, not a
second optimizer.  The decision variable is one complete feasible sortie path.  Legacy
``x_out`` / ``x_ret`` views are exported only as aggregated linear expressions so
existing evaluation code can migrate without reconstructing sortie identity.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

try:  # Real deployment / solver environment.
    from pyscipopt import Model, quicksum as scip_quicksum
except ModuleNotFoundError:  # Allows solver-free contract tests for this overlay.
    Model = None
    scip_quicksum = None

from .decision_vars import PathKey, PathMaps, build_var_index
from .model_facts import (
    ModelFactError,
    aircraft_events,
    capacity_coefficients,
    demand_rows,
    objective_coefficients,
    resolved_alpha,
    resource_use_by_path,
)


def _sets(ds: Mapping[str, Any]):
    static = ds["static"]
    tv = ds["timeview"]
    A = [a["airport_id"] for a in static["airports"]]
    M = [m["mission_id"] for m in static["missions"]]
    K = set()
    for m in static["missions"]:
        K.update((m.get("tau_work") or {}).keys())
        K.update((m.get("required_sorties") or {}).keys())
    for a in static["airports"]:
        K.update((a.get("supported_aircraft") or {}).keys())
    return A, M, sorted(K), int(tv["T"])


def _shock_at(shock: Mapping[str, Any], airport_id: str, aircraft_type_id: str, slot: int) -> float:
    seq = ((shock.get(airport_id) or {}).get(aircraft_type_id) or [])
    if isinstance(seq, dict):
        return float(seq.get(slot, 0.0))
    return float(seq[slot]) if slot < len(seq) else 0.0


def _aggregate_views(maps: PathMaps, x_path: Mapping[PathKey, Any]):
    """Compatibility views; values are expressions, never independent variables."""
    x_out: Dict[Tuple[str, str, str, int], Any] = {}
    x_ret: Dict[Tuple[str, str, str, int], Any] = {}
    for p in maps.path_records:
        var = x_path[p.key]
        out_key = (p.origin_airport_id, p.mission_id, p.aircraft_type_id, p.depart_slot)
        ret_key = (p.mission_id, p.return_airport_id, p.aircraft_type_id, p.landing_slot)
        x_out[out_key] = x_out.get(out_key, 0) + var
        x_ret[ret_key] = x_ret.get(ret_key, 0) + var
    return x_out, x_ret


def _add_aircraft_flow(model, ds, maps: PathMaps, x_path, z, A, K, T, sum_terms):
    departures, ready = aircraft_events(maps)
    z0 = ds["timeview"].get("z0") or {}
    shock = ds["timeview"].get("aircraft_shock") or {}

    for aid in A:
        for f in K:
            initial = float((z0.get(aid) or {}).get(f, 0.0))
            model.addCons(z[aid][f][0] == initial, name=f"ZINIT__{aid}__{f}")
            for t in range(T):
                dep = sum_terms(x_path[pid] for pid in departures.get((aid, f, t), ()))
                ret = sum_terms(x_path[pid] for pid in ready.get((aid, f, t), ()))
                delta = _shock_at(shock, aid, f, t)
                # ready/shock at slot t are applied before departures at t.  z[t+1]>=0
                # therefore enforces that loss + departures cannot exceed availability.
                model.addCons(
                    z[aid][f][t + 1] == z[aid][f][t] + ret + delta - dep,
                    name=f"ZFLOW__{aid}__{f}__{t}",
                )


def _add_capacity(model, ds, maps: PathMaps, run_params, x_path, A, T, sum_terms):
    dep_coef, arr_coef = capacity_coefficients(maps, run_params)
    cap = ds["timeview"].get("cap") or {}
    for aid in A:
        seq = cap.get(aid)
        if not isinstance(seq, list) or len(seq) < T:
            raise ModelFactError(f"capacity series missing/short: {aid}")
        for t in range(T):
            terms = []
            for (a, slot, pid), coef in dep_coef.items():
                if a == aid and slot == t:
                    terms.append(coef * x_path[pid])
            for (a, slot, pid), coef in arr_coef.items():
                if a == aid and slot == t:
                    terms.append(coef * x_path[pid])
            model.addCons(sum_terms(terms) <= float(seq[t]), name=f"CAP__{aid}__{t}")



def _add_demand(model, ds, maps: PathMaps, x_path, *, vtype: str, sum_terms):
    """Add soft baseline-demand constraints.

    executed(mid,f) + unmet(mid,f) >= required(mid,f)

    There is deliberately no ``executed <= required`` constraint.  Once the baseline
    demand is covered, additional sorties remain available to express regional support
    capacity subject to the existing aircraft/capacity/resource facts.
    """
    unmet: Dict[Tuple[str, str], Any] = {}
    for (mid, f), (required, paths) in demand_rows(ds, maps).items():
        var = model.addVar(lb=0.0, vtype=vtype, name=f"UNMET__{mid}__{f}")
        unmet[(mid, f)] = var
        model.addCons(
            sum_terms(x_path[pid] for pid in paths) + var >= float(required),
            name=f"REQ__{mid}__{f}",
        )
    return unmet


def _add_dynamic_inventory_resources(
    model,
    ds,
    maps: PathMaps,
    run_params,
    x_path,
    A,
    T,
    sum_terms,
    *,
    integer_vars: bool,
):
    """Add explicit airport/resource/slot stock conservation for archived v5 inputs."""
    timeview = ds["timeview"]
    facts = timeview.get("dynamic_inventory") or {}
    opening = facts.get("opening_inventory") or {}
    storage = facts.get("storage_capacity") or {}
    loss_caps = facts.get("physical_loss_cap") or {}
    arrivals = timeview.get("resource_replenishment_actual") or {}
    arrival_caps = timeview.get("resource_replenishment_capacity") or {}
    uses = resource_use_by_path(maps, run_params)
    used_pairs = {
        (row.airport_id, row.resource_type_id)
        for rows in uses.values()
        for row in rows
        if row.amount > 0
    }
    for aid, resource_id in sorted(used_pairs):
        if resource_id not in (opening.get(aid) or {}):
            raise ModelFactError(
                f"dynamic inventory resource missing: {aid}/{resource_id}"
            )

    ending = {}
    after_loss = {}
    selectors = {}
    for aid in A:
        ending[aid] = {}
        after_loss[aid] = {}
        selectors[aid] = {}
        for resource_id, raw_opening in sorted((opening.get(aid) or {}).items()):
            cap_seq = (storage.get(aid) or {}).get(resource_id)
            loss_seq = (loss_caps.get(aid) or {}).get(resource_id)
            arrival_seq = (arrivals.get(aid) or {}).get(resource_id)
            arrival_cap_seq = (arrival_caps.get(aid) or {}).get(resource_id)
            if any(
                not isinstance(seq, list) or len(seq) < T
                for seq in (cap_seq, loss_seq, arrival_seq, arrival_cap_seq)
            ):
                raise ModelFactError(
                    f"dynamic inventory series missing/short: {aid}/{resource_id}"
                )
            max_capacity = max(float(value) for value in cap_seq) if cap_seq else 0.0
            if max_capacity < 0:
                raise ModelFactError(
                    f"negative storage capacity: {aid}/{resource_id}"
                )
            ending[aid][resource_id] = {}
            after_loss[aid][resource_id] = {}
            selectors[aid][resource_id] = {}
            by_depart: Dict[int, list] = {}
            for path in maps.path_records:
                if path.origin_airport_id != aid:
                    continue
                amount = sum(
                    row.amount
                    for row in uses[path.key]
                    if row.resource_type_id == resource_id
                )
                if amount > 0:
                    by_depart.setdefault(path.depart_slot, []).append(
                        amount * x_path[path.key]
                    )

            for slot in range(T):
                inherited = (
                    float(raw_opening)
                    if slot == 0
                    else ending[aid][resource_id][slot - 1]
                )
                after = model.addVar(
                    lb=0.0,
                    vtype="C",
                    name=f"INV_AFTER_LOSS__{aid}__{resource_id}__{slot}",
                )
                end = model.addVar(
                    lb=0.0,
                    vtype="C",
                    name=f"INV_END__{aid}__{resource_id}__{slot}",
                )
                after_loss[aid][resource_id][slot] = after
                ending[aid][resource_id][slot] = end
                loss_cap = loss_seq[slot]
                if loss_cap is None:
                    model.addCons(
                        after == inherited,
                        name=f"INV_NO_LOSS__{aid}__{resource_id}__{slot}",
                    )
                else:
                    cap_value = float(loss_cap)
                    if cap_value < 0:
                        raise ModelFactError(
                            f"invalid physical loss cap: {aid}/{resource_id}/slot={slot}"
                        )
                    if cap_value >= max_capacity - 1e-9:
                        model.addCons(
                            after == inherited,
                            name=(
                                f"INV_LOSS_CAP_INACTIVE__{aid}__{resource_id}__{slot}"
                            ),
                        )
                    else:
                        selector = model.addVar(
                            lb=0.0,
                            vtype="B" if integer_vars else "C",
                            name=f"INV_MIN_SEL__{aid}__{resource_id}__{slot}",
                        )
                        selectors[aid][resource_id][slot] = selector
                        if not integer_vars:
                            model.addCons(
                                selector <= 1.0,
                                name=(
                                    f"INV_MIN_SEL_UB__{aid}__{resource_id}__{slot}"
                                ),
                            )
                        big_m = max_capacity
                        model.addCons(
                            after <= inherited,
                            name=f"INV_MIN_A__{aid}__{resource_id}__{slot}",
                        )
                        model.addCons(
                            after <= cap_value,
                            name=f"INV_MIN_B__{aid}__{resource_id}__{slot}",
                        )
                        model.addCons(
                            after >= inherited - big_m * selector,
                            name=f"INV_MIN_C__{aid}__{resource_id}__{slot}",
                        )
                        model.addCons(
                            after >= cap_value - big_m * (1.0 - selector),
                            name=f"INV_MIN_D__{aid}__{resource_id}__{slot}",
                        )

                arrival = float(arrival_seq[slot])
                arrival_cap = float(arrival_cap_seq[slot])
                if arrival < -1e-9 or arrival > arrival_cap + 1e-9:
                    raise ModelFactError(
                        "actual replenishment exceeds capacity: "
                        f"{aid}/{resource_id}/slot={slot}"
                    )
                model.addCons(
                    after + arrival <= float(cap_seq[slot]),
                    name=f"INV_STORAGE__{aid}__{resource_id}__{slot}",
                )
                consumption = sum_terms(by_depart.get(slot, ()))
                model.addCons(
                    end == after + arrival - consumption,
                    name=f"INV_BALANCE__{aid}__{resource_id}__{slot}",
                )
    return {
        "ending": ending,
        "after_loss": after_loss,
        "loss_selectors": selectors,
    }


def _add_shared_resources(
    model,
    ds,
    maps: PathMaps,
    run_params,
    x_path,
    A,
    T,
    sum_terms,
    *,
    integer_vars: bool,
):
    """Airport-local shared consumable pools, including reserve-adjusted fuel."""
    if ds["timeview"].get("resource_inventory_model") == "physical_inventory_v1":
        return _add_dynamic_inventory_resources(
            model,
            ds,
            maps,
            run_params,
            x_path,
            A,
            T,
            sum_terms,
            integer_vars=integer_vars,
        )
    uses = resource_use_by_path(maps, run_params)
    limits = ds["timeview"].get("resources") or {}

    # Every resource actually consumed must have an authoritative availability series.
    used_pairs = {
        (row.airport_id, row.resource_type_id)
        for rows in uses.values()
        for row in rows
        if row.amount > 0
    }
    for aid, rid in sorted(used_pairs):
        seq = (limits.get(aid) or {}).get(rid)
        if not isinstance(seq, list) or len(seq) < T:
            raise ModelFactError(f"resource series missing/short: {aid}/{rid}")

    for aid in A:
        for rid, seq in sorted((limits.get(aid) or {}).items()):
            if not isinstance(seq, list) or len(seq) < T:
                raise ModelFactError(f"resource series missing/short: {aid}/{rid}")
            cumulative = 0
            by_depart: Dict[int, list] = {}
            for p in maps.path_records:
                if p.origin_airport_id != aid:
                    continue
                amount = sum(row.amount for row in uses[p.key] if row.resource_type_id == rid)
                if amount > 0:
                    by_depart.setdefault(p.depart_slot, []).append(amount * x_path[p.key])
            for t in range(T):
                cumulative = cumulative + sum_terms(by_depart.get(t, ()))
                model.addCons(cumulative <= float(seq[t]), name=f"RES__{aid}__{rid}__{t}")
    return None


def _resolve_unmet_penalty(runtime: Mapping[str, Any]) -> float:
    raw = runtime.get("unmet_demand_penalty")
    if raw is None:
        raise ModelFactError("unmet_demand_penalty is required")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ModelFactError("unmet_demand_penalty must be numeric") from exc
    if value <= 0:
        raise ModelFactError("unmet_demand_penalty must be positive")
    return value


def _set_objective(model, ds, maps: PathMaps, run_params, runtime, x_path, unmet_demand, sum_terms):
    weights = resolved_alpha(runtime)
    coeffs = objective_coefficients(ds, maps, run_params, runtime)
    terms = []
    for pid, row in coeffs.items():
        coef = weights.sortie * row.f1 - weights.resource * row.f2 - weights.time * row.f3
        if coef != 0.0:
            terms.append(coef * x_path[pid])

    unmet_penalty = _resolve_unmet_penalty(runtime)
    for var in unmet_demand.values():
        terms.append(-unmet_penalty * var)

    model.setObjective(sum_terms(terms), "maximize")
    return unmet_penalty


def build_model(
    ds: Dict[str, Any],
    run_params: Dict[str, Any],
    maps: PathMaps,
    integer_vars: bool = True,
    runtime: Optional[Dict[str, Any]] = None,
    *,
    model_factory=None,
):
    """Build the final/LP model from full sortie paths.

    ``model_factory`` is test-only injection. Existing production callers use the same
    public call shape as before.
    """
    if not isinstance(maps, PathMaps):
        raise ModelFactError("maps must be PathMaps")
    A, M, K, T = _sets(ds)
    if T <= 0:
        raise ModelFactError("timeview.T must be positive")
    runtime = runtime or {}

    factory = model_factory or Model
    if factory is None:
        raise RuntimeError("PySCIPOpt is required to build the optimization model")
    sum_terms = sum if model_factory is not None else scip_quicksum
    model = factory("AirportClusterModel")

    try:
        model.setRealParam("limits/gap", 0.01)
    except Exception:
        pass
    time_limit = int(runtime.get("mip_time_limit_s", 0) or 0)
    if time_limit > 0:
        try:
            model.setRealParam("limits/time", float(time_limit))
        except Exception:
            pass

    vtype = "I" if integer_vars else "C"
    idx = build_var_index(maps, ds)

    # One variable == one complete origin -> mission -> return -> ready path.
    x_path: Dict[PathKey, Any] = {}
    for pid in idx["XPATH"]:
        j, h, k, f, t_dep, t_ld, t_ready = pid
        x_path[pid] = model.addVar(
            lb=0.0,
            vtype=vtype,
            name=f"X_PATH__{j}__{h}__{k}__{f}__{t_dep}__{t_ld}__{t_ready}",
        )

    z: Dict[str, Dict[str, Dict[int, Any]]] = {}
    for aid in A:
        z[aid] = {}
        for f in K:
            z[aid][f] = {}
            for t in range(T + 1):
                z[aid][f][t] = model.addVar(lb=0.0, vtype=vtype, name=f"Z__{aid}__{f}__{t}")

    _add_aircraft_flow(model, ds, maps, x_path, z, A, K, T, sum_terms)
    _add_capacity(model, ds, maps, run_params, x_path, A, T, sum_terms)
    unmet_demand = _add_demand(model, ds, maps, x_path, vtype=vtype, sum_terms=sum_terms)
    inventory = _add_shared_resources(
        model,
        ds,
        maps,
        run_params,
        x_path,
        A,
        T,
        sum_terms,
        integer_vars=integer_vars,
    )
    unmet_penalty = _set_objective(
        model, ds, maps, run_params, runtime, x_path, unmet_demand, sum_terms
    )

    x_out, x_ret = _aggregate_views(maps, x_path)
    pack = {
        "x_path": x_path,
        "x_out": x_out,
        "x_ret": x_ret,
        "z": z,
        "unmet_demand": unmet_demand,
        "unmet_demand_penalty": unmet_penalty,
        "path_records": tuple(maps.path_records),
        "sets": {"A": A, "M": M, "K": K, "T": T},
        "dynamic_inventory": inventory,
    }
    return model, pack


__all__ = ["build_model"]
