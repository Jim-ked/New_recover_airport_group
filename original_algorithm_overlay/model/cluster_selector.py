# -*- coding: utf-8 -*-
"""Simulated-annealing airport-cluster selector for the existing optimizer chain.

Batch-3 rule: cluster LP evaluation and final MIP must consume the same complete-sortie
``X_PATH`` model and the same path-level objective coefficients from ``model_facts``.
The SA search policy is intentionally retained: deterministic seed heuristics + random
seed, swap/two-swap/destroy-repair neighbourhoods, Metropolis acceptance and geometric
cooling.  This module does not introduce a second objective implementation.
"""

from __future__ import annotations

import copy
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .decision_vars import build_base_path_map, build_path_map_from_base, build_var_index
from .model_builder import build_model
from .model_facts import ModelFactError, objective_coefficients, resolved_alpha

CLUSTER_LP_TIME_LIMIT_S: float = 10.0
_OBJECTIVE_EQ_TOL = 1e-6


class ClusterEvalError(RuntimeError):
    pass


def _airport_ids(ds: Mapping[str, Any]) -> List[str]:
    return [str(a["airport_id"]) for a in ds["static"]["airports"]]


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _seed_core_weight_map(runtime: Optional[Mapping[str, Any]], airports: List[str]) -> Dict[str, float]:
    """Core-airport weights are used only for the old seed heuristic.

    Objective coefficients remain exclusively owned by ``model_facts``. Canonical
    RunConfig represents core airports as an ID list, so permissive legacy dict parsing
    is deliberately not retained here.
    """
    raw = (runtime or {}).get("core_airports") or []
    if not isinstance(raw, (list, tuple)):
        raise ModelFactError("core_airports must be canonical ID list")
    unknown = sorted(set(raw) - set(airports))
    if unknown:
        raise ModelFactError(f"unknown core airports: {unknown}")
    core = set(raw)
    return {aid: (2.0 if aid in core else 1.0) for aid in airports}


def _estimate_var_scale(base_maps, ds: Dict[str, Any]) -> int:
    """Estimate actual path-model size, without double-counting x_out/x_ret views."""
    maps_all = build_path_map_from_base(base_maps, None)
    idx = build_var_index(maps_all, ds)
    return int(len(idx["XPATH"]) + len(idx["ZIDX"]))


def _resolve_search_plan(
    runtime: Optional[Mapping[str, Any]],
    var_scale: int,
    seed_count_base: int,
) -> Dict[str, Any]:
    """Retain the old tiered SA budget/parallel policy.

    Canonical RunConfig does not currently expose these technical knobs, so in normal
    snapshot execution the defaults below apply.  Existing internal callers can still
    provide ``cluster_sa``, ``cluster_budget`` and ``cluster_parallel`` while migration
    remains in progress.
    """
    rt = runtime or {}
    sa_cfg = rt.get("cluster_sa", {}) or {}
    bcfg = rt.get("cluster_budget", {}) or {}
    pcfg = rt.get("cluster_parallel", {}) or {}

    auto_budget = bool(bcfg.get("auto", True))
    small_max = _safe_int(bcfg.get("small_max", 2200), 2200)
    medium_max = _safe_int(bcfg.get("medium_max", 7000), 7000)
    if var_scale <= small_max:
        scale_tier = "small"
    elif var_scale <= medium_max:
        scale_tier = "medium"
    else:
        scale_tier = "large"

    base_iters = max(1, _safe_int(sa_cfg.get("max_iters_per_seed", 20), 20))
    if not auto_budget:
        iters = base_iters
        extra_random_seeds = max(0, _safe_int(bcfg.get("extra_random_seeds", 0), 0))
        batch_size = max(1, _safe_int(bcfg.get("batch_size", 1), 1))
        patience = max(2, _safe_int(bcfg.get("patience", 6), 6))
    elif scale_tier == "small":
        iters = max(6, int(base_iters * 0.5))
        extra_random_seeds = 0
        batch_size = 1
        patience = 4
    elif scale_tier == "medium":
        iters = max(12, int(base_iters * 1.0))
        extra_random_seeds = max(2, seed_count_base // 2)
        batch_size = 3
        patience = 8
    else:
        iters = max(24, int(base_iters * 1.8))
        extra_random_seeds = max(6, seed_count_base)
        batch_size = 6
        patience = 12

    mip_t = _safe_float(rt.get("mip_time_limit_s", 120.0), 120.0)
    ratio = 0.15 if scale_tier == "small" else (0.30 if scale_tier == "medium" else 0.45)
    if not auto_budget:
        ratio = _safe_float(bcfg.get("time_cap_ratio", 0.25), 0.25)
    time_cap_s = max(1.0, _safe_float(bcfg.get("time_cap_s", mip_t * ratio), mip_t * ratio))

    cpu_n = max(1, os.cpu_count() or 1)
    parallel_enabled = bool(pcfg.get("enabled", True))
    batch_parallel_enabled = bool(pcfg.get("batch_enabled", True))
    auto_workers = 1 if scale_tier == "small" else (2 if scale_tier == "medium" else min(4, cpu_n))
    outer_workers = max(1, min(cpu_n, _safe_int(pcfg.get("workers", auto_workers), auto_workers)))
    batch_workers = max(1, min(cpu_n, _safe_int(pcfg.get("batch_workers", auto_workers), auto_workers)))
    if scale_tier == "small":
        outer_workers = 1
        batch_workers = 1
    if not parallel_enabled:
        outer_workers = 1
    if not batch_parallel_enabled:
        batch_workers = 1

    return {
        "scale_tier": scale_tier,
        "var_scale": int(var_scale),
        "max_iters_per_seed": int(iters),
        "extra_random_seeds": int(extra_random_seeds),
        "batch_size": int(max(1, batch_size)),
        "patience": int(max(2, patience)),
        "time_cap_s": float(time_cap_s),
        "outer_workers": int(outer_workers),
        "batch_workers": int(batch_workers),
    }


def _solution_components(model, pack: Mapping[str, Any], coeffs: Mapping[Tuple, Any]):
    """Read LP path quantities and report F1/F2/F3 from the shared coefficient table."""
    x_path = pack.get("x_path")
    if not isinstance(x_path, dict):
        raise ClusterEvalError("cluster LP pack must expose canonical x_path variables")
    f1 = f2 = f3 = 0.0
    quantities: Dict[Tuple, float] = {}
    for pid, var in x_path.items():
        value = float(model.getVal(var))
        if value <= 1e-12:
            continue
        row = coeffs.get(pid)
        if row is None:
            raise ClusterEvalError(f"objective coefficient missing for path: {pid}")
        quantities[pid] = value
        f1 += float(row.f1) * value
        f2 += float(row.f2) * value
        f3 += float(row.f3) * value
    return f1, f2, f3, quantities


def _eval_cluster_lp(
    base_maps,
    ds: Dict[str, Any],
    run_params: Dict[str, Any],
    runtime: Optional[Dict[str, Any]],
    K: int,
    S: List[str],
    cache: Dict[Tuple[str, ...], Dict[str, Any]],
    trace_level: int = 0,
    *,
    model_factory=None,
) -> Dict[str, Any]:
    """Evaluate one cluster by the exact path model used by the final MIP."""
    key = tuple(sorted(S))
    if key in cache:
        return cache[key]
    if not S:
        raise ClusterEvalError("empty airport cluster S")

    cluster_cfg = {"enabled": True, "K": K, "S": list(S)}
    maps = build_path_map_from_base(base_maps, cluster_cfg)
    rt = copy.deepcopy(runtime) if runtime is not None else {}
    if "preference_mode" not in rt:
        rt["preference_mode"] = "sortie_max"
    rt["mip_time_limit_s"] = CLUSTER_LP_TIME_LIMIT_S

    try:
        model, pack = build_model(
            ds,
            run_params,
            maps,
            integer_vars=False,
            runtime=rt,
            model_factory=model_factory,
        )
    except (ModelFactError, ValueError) as exc:
        res = {
            "S": list(S), "F1": 0.0, "F2": 0.0, "F3": 0.0,
            "Z": -1e18, "status": "infeasible_precheck", "detail": str(exc),
        }
        cache[key] = res
        return res

    if trace_level <= 0:
        try:
            model.hideOutput(True)
        except Exception:
            pass

    try:
        model.optimize()
    except Exception as exc:
        if trace_level >= 1:
            print(f"[cluster_eval] S={list(S)} LP solve error: {exc}")
        res = {
            "S": list(S), "F1": 0.0, "F2": 0.0, "F3": 0.0,
            "Z": -1e18, "status": "error", "detail": str(exc),
        }
        cache[key] = res
        return res

    try:
        z_model = float(model.getObjVal())
    except Exception:
        res = {
            "S": list(S), "F1": 0.0, "F2": 0.0, "F3": 0.0,
            "Z": -1e18, "status": "no_solution",
        }
        cache[key] = res
        return res

    coeffs = objective_coefficients(ds, maps, run_params, rt)
    f1, f2, f3, _ = _solution_components(model, pack, coeffs)
    weights = resolved_alpha(rt)
    z_facts = weights.sortie * f1 - weights.resource * f2 + weights.time * f3
    if abs(z_model - z_facts) > _OBJECTIVE_EQ_TOL * (1.0 + abs(z_model)):
        raise ClusterEvalError(
            f"cluster LP objective drift: model={z_model}, shared_facts={z_facts}"
        )

    res = {
        "S": list(S), "F1": f1, "F2": f2, "F3": f3,
        "Z": z_model, "status": "ok",
    }
    cache[key] = res
    return res


_BATCH_WORKER_CTX: Dict[str, Any] = {}


def _init_batch_worker(base_maps, ds, run_params, runtime, K: int, trace_level: int) -> None:
    global _BATCH_WORKER_CTX
    _BATCH_WORKER_CTX = {
        "base_maps": base_maps,
        "ds": ds,
        "run_params": run_params,
        "runtime": runtime,
        "K": K,
        "trace_level": trace_level,
        "cache": {},
    }


def _batch_worker_eval(S: List[str]) -> Dict[str, Any]:
    ctx = _BATCH_WORKER_CTX
    return _eval_cluster_lp(
        ctx["base_maps"], ctx["ds"], ctx["run_params"], ctx["runtime"],
        ctx["K"], S, ctx["cache"], ctx["trace_level"],
    )


def _eval_candidate_batch(
    candidates: List[List[str]],
    *,
    cache: Dict[Tuple[str, ...], Dict[str, Any]],
    base_maps,
    ds: Dict[str, Any],
    run_params: Dict[str, Any],
    runtime: Optional[Dict[str, Any]],
    K: int,
    trace_level: int,
    batch_executor: Optional[ProcessPoolExecutor],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    to_eval: List[List[str]] = []
    for S in candidates:
        key = tuple(sorted(S))
        if key in cache:
            results.append(cache[key])
        else:
            to_eval.append(S)

    if batch_executor is None:
        for S in to_eval:
            results.append(_eval_cluster_lp(base_maps, ds, run_params, runtime, K, S, cache, trace_level))
        return results

    futures = [batch_executor.submit(_batch_worker_eval, list(S)) for S in to_eval]
    for fut in as_completed(futures):
        try:
            ev = fut.result()
        except Exception as exc:
            if trace_level >= 1:
                print(f"[batch_eval] worker failed: {exc}")
            continue
        k = tuple(sorted(ev.get("S", []) or []))
        if k:
            cache[k] = ev
        results.append(ev)
    return results


def _seed_core_first(A: List[str], K: int, w_core: Mapping[str, float], cap_sum: Mapping[str, float]) -> List[str]:
    scored = [(w_core.get(a, 1.0) * cap_sum.get(a, 0.0), a) for a in A]
    scored.sort(reverse=True)
    return [a for _, a in scored[:K]]


def _seed_capacity_top(A: List[str], K: int, cap_sum: Mapping[str, float]) -> List[str]:
    scored = [(cap_sum.get(a, 0.0), a) for a in A]
    scored.sort(reverse=True)
    return [a for _, a in scored[:K]]


def _seed_random(A: List[str], K: int, rng: random.Random) -> List[str]:
    values = list(A)
    rng.shuffle(values)
    return values[:K]


def _generate_seeds(
    A: List[str], K: int, ds: Dict[str, Any], w_core: Mapping[str, float],
    rng: random.Random, extra_random_seeds: int = 0,
) -> List[List[str]]:
    cap = ds["timeview"].get("cap", {}) or {}
    cap_sum = {a: float(sum(cap.get(a, []) or [])) for a in A}
    seeds = [
        _seed_core_first(A, K, w_core, cap_sum),
        _seed_capacity_top(A, K, cap_sum),
        _seed_random(A, K, rng),
    ]
    seeds.extend(_seed_random(A, K, rng) for _ in range(max(0, int(extra_random_seeds))))
    uniq: List[List[str]] = []
    seen = set()
    for S in seeds:
        if len(S) < K:
            continue
        key = tuple(sorted(S[:K]))
        if key not in seen:
            uniq.append(list(key))
            seen.add(key)
    return uniq or [_seed_random(A, K, rng)]


def _neighbour_swap(S: List[str], A: List[str], rng: random.Random) -> List[str]:
    if not S or len(S) >= len(A):
        return list(S)
    out = rng.choice(S)
    pool = [a for a in A if a not in S]
    if not pool:
        return list(S)
    return [a for a in S if a != out] + [rng.choice(pool)]


def _neighbour_two_swap(S: List[str], A: List[str], rng: random.Random) -> List[str]:
    if len(S) < 2 or len(S) >= len(A):
        return list(S)
    out2 = rng.sample(S, k=2)
    rest = [a for a in A if a not in S]
    if len(rest) < 2:
        return _neighbour_swap(S, A, rng)
    remain = [a for a in S if a not in out2]
    return remain + rng.sample(rest, k=2)


def _neighbour_destroy_repair(S: List[str], A: List[str], rng: random.Random, remove_n: int = 2) -> List[str]:
    if not S or len(S) >= len(A):
        return list(S)
    k = len(S)
    rn = max(1, min(remove_n, k - 1))
    removed = set(rng.sample(S, k=rn))
    remain = [a for a in S if a not in removed]
    pool = [a for a in A if a not in remain]
    rng.shuffle(pool)
    while len(remain) < k and pool:
        remain.append(pool.pop())
    return remain[:k]


def _make_candidate_batch(cur_S: List[str], A: List[str], rng: random.Random, batch_size: int) -> List[List[str]]:
    target = max(1, int(batch_size))
    seen = {tuple(sorted(cur_S))}
    out: List[List[str]] = []
    attempts = max(6, target * 5)
    while len(out) < target and attempts > 0:
        attempts -= 1
        r = rng.random()
        if r < 0.60:
            cand = _neighbour_swap(cur_S, A, rng)
        elif r < 0.90:
            cand = _neighbour_two_swap(cur_S, A, rng)
        else:
            cand = _neighbour_destroy_repair(cur_S, A, rng, remove_n=2)
        key = tuple(sorted(cand))
        if key not in seen:
            seen.add(key)
            out.append(cand)
    return out


def _search_sa(
    base_maps,
    ds: Dict[str, Any],
    run_params: Dict[str, Any],
    runtime: Optional[Dict[str, Any]],
    K: int,
    A: List[str],
    seeds: List[List[str]],
    rng: random.Random,
    search_plan: Optional[Dict[str, Any]] = None,
    trace_level: int = 1,
):
    cache: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    trajectory: List[Dict[str, Any]] = []
    best_eval: Optional[Dict[str, Any]] = None
    step = 0

    sa_cfg = (runtime or {}).get("cluster_sa", {}) or {}
    T0 = float(sa_cfg.get("T0", 1.0) or 1.0)
    alpha = float(sa_cfg.get("alpha", 0.9) or 0.9)
    T_min = 1e-6
    sp = search_plan or {}
    max_iters_per_seed = int(sp.get("max_iters_per_seed", sa_cfg.get("max_iters_per_seed", 20) or 20))
    batch_size = int(max(1, sp.get("batch_size", 1)))
    patience = int(max(2, sp.get("patience", 6)))
    time_cap_s = float(max(1.0, sp.get("time_cap_s", 120.0)))
    batch_workers = int(max(1, sp.get("batch_workers", 1)))

    batch_executor: Optional[ProcessPoolExecutor] = None
    if batch_workers > 1 and batch_size > 1:
        try:
            batch_executor = ProcessPoolExecutor(
                max_workers=batch_workers,
                initializer=_init_batch_worker,
                initargs=(base_maps, ds, run_params, runtime, K, trace_level),
            )
        except Exception as exc:
            if trace_level >= 1:
                print(f"[batch_eval] executor init failed, fallback serial: {exc}")

    started = time.time()
    try:
        for si, S0 in enumerate(seeds):
            cur = _eval_cluster_lp(base_maps, ds, run_params, runtime, K, S0, cache, trace_level)
            trajectory.append({"step": step, "seed": si, "S": list(cur["S"]), "Z": cur["Z"]})
            step += 1
            if best_eval is None or cur["Z"] > best_eval["Z"]:
                best_eval = cur
            if trace_level >= 1:
                print(f"[seed {si}] S={cur['S']} Z={cur['Z']:.4f}")

            temperature = T0
            no_improve = 0
            for it in range(max_iters_per_seed):
                if time.time() - started >= time_cap_s:
                    if trace_level >= 1:
                        print(f"[search] time cap reached ({time_cap_s:.1f}s), stop early")
                    break
                candidates = _make_candidate_batch(cur["S"], A, rng, batch_size)
                if not candidates:
                    break
                evals = _eval_candidate_batch(
                    candidates, cache=cache, base_maps=base_maps, ds=ds,
                    run_params=run_params, runtime=runtime, K=K,
                    trace_level=trace_level, batch_executor=batch_executor,
                )
                if not evals:
                    continue
                for ev in evals:
                    trajectory.append({"step": step, "seed": si, "S": list(ev["S"]), "Z": ev["Z"]})
                    step += 1
                cand = max(evals, key=lambda e: e["Z"])
                dz = cand["Z"] - cur["Z"]
                accept = dz >= 0 or rng.random() < math.exp(dz / max(temperature, T_min))
                improved = False
                if accept:
                    cur = cand
                    if best_eval is None or cand["Z"] > best_eval["Z"]:
                        best_eval = cand
                        improved = True
                    if trace_level >= 2:
                        print(f"  [SA] it={it} accept dZ={dz:.4f} Z={cand['Z']:.4f} S={cand['S']}")
                if improved:
                    no_improve = 0
                else:
                    no_improve += 1
                    if no_improve >= patience:
                        break
                temperature *= alpha
                if temperature < T_min:
                    break
    finally:
        if batch_executor is not None:
            batch_executor.shutdown(wait=True, cancel_futures=False)

    if best_eval is None:
        raise ClusterEvalError("cluster search produced no evaluated candidate")
    leaderboard = sorted(cache.values(), key=lambda e: e["Z"], reverse=True)
    return best_eval, leaderboard, trajectory


def _search_seed_worker(payload: Dict[str, Any]) -> Dict[str, Any]:
    sp = dict(payload.get("search_plan", {}) or {})
    sp["batch_workers"] = 1
    best, leaderboard, trajectory = _search_sa(
        payload["base_maps"], payload["ds"], payload["run_params"], payload["runtime"],
        payload["K"], payload["A"], [payload["seed"]],
        random.Random(int(payload.get("seed_rng", 0))), sp, int(payload.get("trace_level", 0)),
    )
    return {
        "seed_index": int(payload["seed_index"]),
        "best_eval": best,
        "leaderboard": leaderboard,
        "trajectory": trajectory,
    }


def select_cluster(
    ds: Dict[str, Any],
    run_params: Dict[str, Any],
    runtime: Optional[Dict[str, Any]],
    K: int,
    random_seed: int = 42,
    trace_level: int = 1,
) -> Dict[str, Any]:
    """Existing public entrypoint. Search policy is unchanged; LP facts are unified."""
    A = _airport_ids(ds)
    if K <= 0 or K > len(A):
        raise ValueError(f"invalid K={K}; expected 1..{len(A)}")

    rng = random.Random(random_seed)
    w_core = _seed_core_weight_map(runtime, A)
    base_maps = build_base_path_map(ds, run_params)
    var_scale = _estimate_var_scale(base_maps, ds)
    search_plan = _resolve_search_plan(runtime, var_scale, seed_count_base=max(1, len(A) // 2))
    seeds = _generate_seeds(
        A, K, ds, w_core, rng,
        extra_random_seeds=int(search_plan.get("extra_random_seeds", 0)),
    )

    if trace_level >= 1:
        print(
            f"[select_cluster] tier={search_plan['scale_tier']} var_scale={var_scale} "
            f"iters={search_plan['max_iters_per_seed']} batch={search_plan['batch_size']} "
            f"time_cap={search_plan['time_cap_s']:.1f}s"
        )
        print(f"[select_cluster] seeds={seeds}")

    outer_workers = int(max(1, search_plan.get("outer_workers", 1)))
    if outer_workers <= 1 or len(seeds) <= 1:
        best, leaderboard, trajectory = _search_sa(
            base_maps, ds, run_params, runtime, K, A, seeds, rng, search_plan, trace_level
        )
    else:
        worker_results: List[Dict[str, Any]] = []
        try:
            with ProcessPoolExecutor(max_workers=outer_workers) as ex:
                futures = []
                for si, S0 in enumerate(seeds):
                    futures.append(ex.submit(_search_seed_worker, {
                        "seed_index": si,
                        "seed": S0,
                        "seed_rng": int(random_seed * 10007 + si * 97 + 17),
                        "search_plan": search_plan,
                        "base_maps": base_maps,
                        "ds": ds,
                        "run_params": run_params,
                        "runtime": runtime,
                        "K": K,
                        "A": A,
                        "trace_level": trace_level,
                    }))
                for fut in as_completed(futures):
                    worker_results.append(fut.result())
        except Exception as exc:
            if trace_level >= 1:
                print(f"[outer_parallel] failed, fallback serial: {exc}")
            best, leaderboard, trajectory = _search_sa(
                base_maps, ds, run_params, runtime, K, A, seeds, rng, search_plan, trace_level
            )
        else:
            best = max((wr["best_eval"] for wr in worker_results), key=lambda e: e["Z"])
            lb_map: Dict[Tuple[str, ...], Dict[str, Any]] = {}
            for wr in worker_results:
                for row in wr.get("leaderboard") or []:
                    k = tuple(sorted(row.get("S", []) or []))
                    old = lb_map.get(k)
                    if old is None or row.get("Z", -1e18) > old.get("Z", -1e18):
                        lb_map[k] = row
            leaderboard = sorted(lb_map.values(), key=lambda e: e["Z"], reverse=True)
            trajectory = []
            gstep = 0
            for wr in sorted(worker_results, key=lambda x: x["seed_index"]):
                for tr in wr.get("trajectory") or []:
                    row = dict(tr)
                    row["seed"] = int(wr["seed_index"])
                    row["step"] = gstep
                    gstep += 1
                    trajectory.append(row)

    return {
        "cluster_cfg": {"enabled": True, "K": K, "S": list(best["S"])},
        "leaderboard": leaderboard,
        "trajectory": trajectory,
        "search_plan": search_plan,
    }


__all__ = ["ClusterEvalError", "select_cluster"]
