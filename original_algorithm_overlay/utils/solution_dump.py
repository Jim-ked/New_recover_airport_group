# -*- coding: utf-8 -*-
"""Canonical complete-sortie Solution export for the existing optimizer chain."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Mapping, Optional

from backend.domain.solution import Solution, SortieChain
from original_algorithm_overlay.model.decision_vars import PathKey, PathMaps, SortiePath
from original_algorithm_overlay.model.model_facts import ModelFactError, validate_schedule_base


class SolutionDumpError(RuntimeError):
    pass


def _has_solution(model: Any) -> bool:
    try:
        status = str(model.getStatus()).lower()
    except Exception as exc:
        raise SolutionDumpError("solver status is unavailable") from exc
    if "infeasible" in status:
        return False
    try:
        if int(model.getNSols()) > 0:
            return True
    except Exception:
        pass
    try:
        return model.getBestSol() is not None
    except Exception as exc:
        raise SolutionDumpError("solver solution state is unavailable") from exc


def _path_id(path: SortiePath, *, absolute_offset: int) -> str:
    """Reversible ID; '/' is safe because canonical domain IDs do not allow slash."""
    return "/".join((
        "P",
        path.origin_airport_id,
        path.mission_id,
        path.return_airport_id,
        path.aircraft_type_id,
        str(absolute_offset + path.depart_slot),
        str(absolute_offset + path.landing_slot),
        str(absolute_offset + path.ready_slot),
    ))


def _read_integer_quantities(model: Any, pack: Mapping[str, Any], *, tol: float) -> Dict[PathKey, int]:
    x_path = pack.get("x_path")
    if not isinstance(x_path, dict):
        raise SolutionDumpError("canonical Solution requires pack.x_path")
    out: Dict[PathKey, int] = {}
    for pid, var in x_path.items():
        try:
            value = float(model.getVal(var))
        except Exception as exc:
            raise SolutionDumpError(f"cannot read path variable: {pid}") from exc
        if value < -tol:
            raise SolutionDumpError(f"negative solved sortie quantity: {pid}={value}")
        if value <= tol:
            continue
        rounded = int(round(value))
        if abs(value - rounded) > tol:
            raise SolutionDumpError(f"MIP Solution contains non-integer sortie quantity: {pid}={value}")
        if rounded > 0:
            out[pid] = rounded
    return out



def build_solution(
    ds: Dict[str, Any],
    maps: PathMaps,
    pack: Mapping[str, Any],
    model: Any,
    *,
    run_id: str,
    run_params: Mapping[str, Any],
    cluster_cfg: Optional[Mapping[str, Any]] = None,
    tol: float = 1e-6,
) -> Solution:
    """Build canonical Solution only after a successful, independently valid MIP result."""
    if not _has_solution(model):
        raise SolutionDumpError("canonical Solution is forbidden when solver has no feasible solution")
    quantities = _read_integer_quantities(model, pack, tol=tol)
    if not quantities:
        raise SolutionDumpError("solver reported a solution but no positive sortie path was selected")

    try:
        validate_schedule_base(
            ds, maps, run_params, quantities, integer_required=True, tolerance=tol
        )
    except ModelFactError as exc:
        raise SolutionDumpError(f"solved schedule failed invariant validation: {exc}") from exc

    path_by_key = {p.key: p for p in maps.path_records}
    offset = int((ds.get("range") or (0, 0))[0])
    chains = []
    for pid, sorties in quantities.items():
        path = path_by_key.get(pid)
        if path is None:
            raise SolutionDumpError(f"solved path is absent from PathMaps: {pid}")
        chains.append(SortieChain(
            path_id=_path_id(path, absolute_offset=offset),
            origin_airport_id=path.origin_airport_id,
            mission_id=path.mission_id,
            return_airport_id=path.return_airport_id,
            aircraft_type=path.aircraft_type_id,
            depart_window=offset + path.depart_slot,
            return_window=offset + path.landing_slot,
            ready_window=offset + path.ready_slot,
            sorties=sorties,
        ))

    selected = []
    if cluster_cfg and bool(cluster_cfg.get("enabled")):
        selected = list(cluster_cfg.get("S") or [])
    return Solution.build(run_id=run_id, selected_cluster=selected, sortie_chains=chains)


def dump_solution(
    ds: Dict[str, Any],
    maps: PathMaps,
    pack: Mapping[str, Any],
    model: Any,
    out_path: str,
    *,
    run_id: str,
    run_params: Mapping[str, Any],
    cluster_cfg: Optional[Mapping[str, Any]] = None,
    tol: float = 1e-6,
) -> Solution:
    solution = build_solution(
        ds, maps, pack, model,
        run_id=run_id, run_params=run_params, cluster_cfg=cluster_cfg, tol=tol,
    )
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(solution.to_dict(), f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)
    return solution


__all__ = ["SolutionDumpError", "build_solution", "dump_solution"]
