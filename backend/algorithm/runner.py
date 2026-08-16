from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from backend.domain.run_snapshot import RunSnapshot
from backend.domain.solution import Solution
from backend.algorithm.snapshot_adapter import build_algorithm_input
from original_algorithm_overlay.model.cluster_selector import select_cluster
from original_algorithm_overlay.model.decision_vars import build_base_path_map, build_path_map_from_base
from original_algorithm_overlay.model.model_builder import build_model
from original_algorithm_overlay.utils.solution_dump import SolutionDumpError, build_solution


class AlgorithmRunError(RuntimeError):
    pass


class AlgorithmInfeasibleError(AlgorithmRunError):
    pass


EventCallback = Optional[Callable[[Dict[str, Any]], None]]


@dataclass(frozen=True)
class AlgorithmRunResult:
    run_id: str
    solver_status: str
    objective: float
    cluster_cfg: Mapping[str, Any]
    cluster_leaderboard: Tuple[Mapping[str, Any], ...]
    solution: Solution


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


def _solver_has_solution(model: Any) -> bool:
    try:
        status = str(model.getStatus()).lower()
    except Exception as exc:
        raise AlgorithmRunError("solver status is unavailable") from exc
    if "infeasible" in status:
        return False
    try:
        return int(model.getNSols()) > 0
    except Exception:
        try:
            return model.getBestSol() is not None
        except Exception as exc:
            raise AlgorithmRunError("solver solution state is unavailable") from exc


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
    if not _solver_has_solution(model):
        raise AlgorithmInfeasibleError(f"solver produced no feasible solution: status={solver_status}")

    try:
        objective = float(model.getObjVal())
    except Exception as exc:
        raise AlgorithmRunError("solver objective is unavailable despite feasible solution") from exc

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
    )


__all__ = [
    "AlgorithmRunError",
    "AlgorithmInfeasibleError",
    "AlgorithmRunResult",
    "run_once",
]
