from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from backend.domain.run_snapshot import RunSnapshot
from backend.domain.solution import Solution
from backend.algorithm.snapshot_adapter import build_algorithm_input
from original_algorithm_overlay.model.cluster_selector import select_cluster
from original_algorithm_overlay.model.decision_vars import build_base_path_map, build_path_map_from_base
from original_algorithm_overlay.model.model_builder import build_model
from original_algorithm_overlay.model.model_facts import (
    ModelFactError,
    objective_coefficients,
    objective_component_totals,
    resolved_alpha,
)
from original_algorithm_overlay.utils.solution_dump import SolutionDumpError, build_solution


class AlgorithmRunError(RuntimeError):
    pass


class AlgorithmInfeasibleError(AlgorithmRunError):
    def __init__(
        self,
        message: str,
        *,
        solver_status: str,
        best_bound: Optional[float],
        solve_time_s: Optional[float],
    ) -> None:
        super().__init__(message)
        self.solver_status = solver_status
        self.best_bound = best_bound
        self.gap = None
        self.solve_time_s = solve_time_s


EventCallback = Optional[Callable[[Dict[str, Any]], None]]


@dataclass(frozen=True)
class AlgorithmRunResult:
    run_id: str
    solver_status: str
    objective: float
    cluster_cfg: Mapping[str, Any]
    cluster_leaderboard: Tuple[Mapping[str, Any], ...]
    solution: Solution
    best_bound: Optional[float]
    gap: Optional[float]
    solve_time_s: Optional[float]
    cluster_lp_objective: Optional[float]
    f1: float
    f2: float
    f3: float
    unmet_demand_total: float
    unmet_demand_penalty: float


def _emit(callback: EventCallback, *, stage: str, progress: float, message: str, payload=None) -> None:
    if callback is None:
        return
    callback({
        "type": "algorithm_stage",
        "stage": stage,
        "progress": float(progress),
        "level": "info",
        "message": str(message),
        "payload": dict(payload or {}),
    })


def _solver_has_solution(model: Any, solver_status: str) -> bool:
    if "infeasible" in solver_status.lower():
        return False
    try:
        return int(model.getNSols()) > 0
    except Exception:
        try:
            return model.getBestSol() is not None
        except Exception as exc:
            raise AlgorithmRunError("solver solution state is unavailable") from exc


def _optional_solver_float(
    model: Any,
    method_name: str,
    *,
    nonnegative: bool = False,
) -> Optional[float]:
    method = getattr(model, method_name, None)
    if not callable(method):
        return None
    try:
        value = float(method())
    except Exception:
        return None
    if not isfinite(value) or (nonnegative and value < 0):
        return None
    is_infinity = getattr(model, "isInfinity", None)
    if callable(is_infinity):
        try:
            if bool(is_infinity(abs(value))):
                return None
        except Exception:
            return None
    return value


def _required_objective(model: Any) -> float:
    value = _optional_solver_float(model, "getObjVal")
    if value is None:
        raise AlgorithmRunError("solver objective is unavailable despite feasible solution")
    return value


def _solution_objective_components(model: Any, pack: Mapping[str, Any], coefficients):
    x_path = pack.get("x_path")
    if not isinstance(x_path, dict):
        raise AlgorithmRunError("model pack is missing canonical path variables")
    quantities = {}
    for path_id, variable in x_path.items():
        try:
            value = float(model.getVal(variable))
        except Exception as exc:
            raise AlgorithmRunError(f"cannot read solved path quantity: {path_id}") from exc
        if not isfinite(value) or value < -1e-7:
            raise AlgorithmRunError(f"invalid solved path quantity: {path_id}={value}")
        if value > 1e-12:
            quantities[path_id] = value
    try:
        components = objective_component_totals(coefficients, quantities)
    except ModelFactError as exc:
        raise AlgorithmRunError(str(exc)) from exc

    unmet = pack.get("unmet_demand")
    if not isinstance(unmet, dict):
        raise AlgorithmRunError("model pack is missing unmet-demand variables")
    unmet_total = 0.0
    for variable in unmet.values():
        try:
            value = float(model.getVal(variable))
        except Exception as exc:
            raise AlgorithmRunError("cannot read solved unmet-demand quantity") from exc
        if not isfinite(value) or value < -1e-7:
            raise AlgorithmRunError(f"invalid unmet-demand quantity: {value}")
        unmet_total += max(0.0, value)
    try:
        unmet_penalty = float(pack["unmet_demand_penalty"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AlgorithmRunError("model pack is missing unmet-demand penalty") from exc
    if not isfinite(unmet_penalty) or unmet_penalty <= 0:
        raise AlgorithmRunError("model pack has invalid unmet-demand penalty")
    return components, unmet_total, unmet_penalty


def _selected_cluster_lp_objective(
    cluster_result: Optional[Mapping[str, Any]],
    cluster_cfg: Mapping[str, Any],
) -> Optional[float]:
    if not cluster_cfg.get("enabled") or not isinstance(cluster_result, Mapping):
        return None
    selected = tuple(sorted(str(value) for value in cluster_cfg.get("S") or ()))
    for row in cluster_result.get("leaderboard") or ():
        if not isinstance(row, Mapping) or row.get("status") != "ok":
            continue
        if tuple(sorted(str(value) for value in row.get("S") or ())) != selected:
            continue
        try:
            value = float(row["Z"])
        except (KeyError, TypeError, ValueError):
            return None
        return value if isfinite(value) else None
    return None


def run_once(
    snapshot: RunSnapshot,
    *,
    event_cb: EventCallback = None,
    cluster_selector_fn=select_cluster,
    model_builder_fn=build_model,
    model_factory=None,
) -> AlgorithmRunResult:
    """Execute one immutable RunSnapshot through the original SA -> LP -> MIP chain.

    This is the new algorithm execution boundary. It deliberately accepts no scene path,
    parameter path, runtime path, repository or mutable domain object. Persistence and Run
    lifecycle ownership belong to the service/storage layers outside this function.
    """
    if not isinstance(snapshot, RunSnapshot):
        raise TypeError("run_once requires RunSnapshot")

    _emit(event_cb, stage="prepare", progress=0.05, message="Build immutable algorithm input")
    bundle = build_algorithm_input(snapshot)
    ds, run_params, runtime = bundle.ds, bundle.run_params, bundle.runtime

    cluster_result = None
    if bool(runtime.get("cluster_enabled")):
        K = runtime.get("cluster_size")
        if isinstance(K, bool) or not isinstance(K, int) or K <= 0:
            raise AlgorithmRunError("canonical cluster_size is missing/invalid")
        _emit(event_cb, stage="cluster", progress=0.20, message="Evaluate airport clusters")
        cluster_result = cluster_selector_fn(
            ds=ds,
            run_params=run_params,
            runtime=runtime,
            K=K,
            random_seed=int(runtime["algorithm_seed"]),
            trace_level=0,
        )
        cluster_cfg = cluster_result.get("cluster_cfg")
        if not isinstance(cluster_cfg, dict) or not cluster_cfg.get("S"):
            raise AlgorithmRunError("cluster selector returned no selected cluster")
    else:
        cluster_cfg = {"enabled": False, "K": 0, "S": []}

    _emit(event_cb, stage="paths", progress=0.40, message="Build complete feasible sortie paths")
    base_maps = build_base_path_map(ds, run_params)
    maps = build_path_map_from_base(base_maps, cluster_cfg if cluster_cfg.get("enabled") else None)

    _emit(event_cb, stage="model", progress=0.60, message="Build MIP from full sortie paths")
    kwargs = {
        "ds": ds,
        "run_params": run_params,
        "maps": maps,
        "integer_vars": True,
        "runtime": runtime,
    }
    if model_factory is not None:
        kwargs["model_factory"] = model_factory
    model, pack = model_builder_fn(**kwargs)

    _emit(event_cb, stage="solve", progress=0.75, message="Solve MIP")
    try:
        model.optimize()
    except Exception as exc:
        raise AlgorithmRunError(f"solver execution failed: {exc}") from exc

    try:
        solver_status = str(model.getStatus())
    except Exception as exc:
        raise AlgorithmRunError("solver status is unavailable") from exc
    has_solution = _solver_has_solution(model, solver_status)
    best_bound = _optional_solver_float(model, "getDualbound")
    solve_time_s = _optional_solver_float(model, "getSolvingTime", nonnegative=True)
    if not has_solution:
        raise AlgorithmInfeasibleError(
            f"solver produced no feasible solution: status={solver_status}",
            solver_status=solver_status,
            best_bound=best_bound,
            solve_time_s=solve_time_s,
        )

    objective = _required_objective(model)
    gap = _optional_solver_float(model, "getGap", nonnegative=True)

    _emit(event_cb, stage="solution", progress=0.90, message="Validate and build canonical Solution")
    try:
        solution = build_solution(
            ds,
            maps,
            pack,
            model,
            run_id=snapshot.run_id,
            run_params=run_params,
            cluster_cfg=cluster_cfg,
        )
    except SolutionDumpError as exc:
        raise AlgorithmRunError(str(exc)) from exc

    coefficients = objective_coefficients(ds, maps, run_params, runtime)
    components, unmet_total, unmet_penalty = _solution_objective_components(
        model, pack, coefficients
    )
    weights = resolved_alpha(runtime)
    reconstructed_objective = (
        weights.sortie * components.f1
        - weights.resource * components.f2
        - weights.time * components.f3
        - unmet_penalty * unmet_total
    )
    if not isclose(objective, reconstructed_objective, rel_tol=1e-6, abs_tol=1e-6):
        raise AlgorithmRunError(
            "final MIP objective drift: "
            f"solver={objective}, shared_facts={reconstructed_objective}"
        )

    _emit(event_cb, stage="complete", progress=1.0, message="Algorithm run completed")
    leaderboard = ()
    if isinstance(cluster_result, dict):
        leaderboard = tuple(cluster_result.get("leaderboard") or ())
    return AlgorithmRunResult(
        run_id=snapshot.run_id,
        solver_status=solver_status,
        objective=objective,
        cluster_cfg=dict(cluster_cfg),
        cluster_leaderboard=leaderboard,
        solution=solution,
        best_bound=best_bound,
        gap=gap,
        solve_time_s=solve_time_s,
        cluster_lp_objective=_selected_cluster_lp_objective(cluster_result, cluster_cfg),
        f1=components.f1,
        f2=components.f2,
        f3=components.f3,
        unmet_demand_total=unmet_total,
        unmet_demand_penalty=unmet_penalty,
    )


__all__ = [
    "AlgorithmRunError",
    "AlgorithmInfeasibleError",
    "AlgorithmRunResult",
    "run_once",
]
