from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

from backend.algorithm.runner import AlgorithmRunResult
from backend.analysis.comparison import (
    build_configuration_comparison,
    build_multi_scenario_comparison,
    build_r0_r1_r2_comparison,
    check_configuration_comparable,
    check_multi_scenario_comparable,
    check_r0_r1_r2,
)
from backend.analysis.metrics import build_metrics_core
from backend.domain.run import RunRecord
from backend.domain.run_snapshot import RunSnapshot
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository


class RunResultServiceError(RuntimeError):
    pass


class RunResultAccessError(RunResultServiceError):
    pass


class RunResultNotFoundError(RunResultServiceError):
    pass


class RunResultNotReadyError(RunResultServiceError):
    pass


class RunResultService:
    """Stable service boundary consumed later by Single Run, GIS and Results APIs.

    Web callers never receive algorithm packs, solver variables or result-directory paths.
    Canonical Solution and Metrics are insert-only successful Run facts.
    """

    def __init__(
        self,
        *,
        run_repository: RunRepository,
        snapshot_repository: RunSnapshotRepository,
    ) -> None:
        self.runs = run_repository
        self.snapshots = snapshot_repository

    @staticmethod
    def _assert_access(record: RunRecord, actor_user_id: str, *, is_admin: bool) -> None:
        if not is_admin and record.owner_user_id != actor_user_id:
            raise RunResultAccessError("Run is not owned by actor")

    def _record(self, run_id: str, *, actor_user_id: str, is_admin: bool) -> RunRecord:
        record = self.runs.get(run_id)
        if record is None:
            raise RunResultNotFoundError(f"run not found: {run_id}")
        self._assert_access(record, actor_user_id, is_admin=is_admin)
        return record

    def _successful_bundle(
        self,
        run_id: str,
        *,
        actor_user_id: str,
        is_admin: bool,
    ) -> Tuple[RunRecord, RunSnapshot, Dict[str, Any], Dict[str, Any]]:
        record = self._record(run_id, actor_user_id=actor_user_id, is_admin=is_admin)
        if record.status != "succeeded":
            raise RunResultNotReadyError(
                f"canonical Solution/Metrics are available only for succeeded Run; status={record.status}"
            )
        snapshot = self.snapshots.get(run_id)
        if snapshot is None:
            raise RunResultServiceError("succeeded Run is missing immutable snapshot")
        result = self.runs.get_result_payloads(run_id)
        if result is None:
            raise RunResultServiceError("succeeded Run is missing canonical result payloads")
        solution, metrics = result
        if solution.get("run_id") != run_id or metrics.get("run_id") != run_id:
            raise RunResultServiceError("stored result run_id drift")
        return record, snapshot, solution, metrics

    def persist_success(
        self,
        *,
        result: AlgorithmRunResult,
        technical: Optional[Mapping[str, Any]] = None,
    ) -> RunRecord:
        """Build Metrics from the same frozen snapshot and atomically persist success.

        Any exception before `save_success` leaves the Run in running state so the worker
        can transition it to failed. There is never a partial canonical Solution without
        Metrics and never a succeeded Run without both.
        """
        if not isinstance(result, AlgorithmRunResult):
            raise TypeError("result must be AlgorithmRunResult")
        snapshot = self.snapshots.get(result.run_id)
        if snapshot is None:
            raise RunResultServiceError(f"snapshot not found for result: {result.run_id}")
        record = self.runs.get(result.run_id)
        if record is None:
            raise RunResultServiceError(f"Run record not found for result: {result.run_id}")
        if record.status != "running":
            raise RunResultNotReadyError("successful result may be persisted only while Run is running")
        if record.cancel_requested:
            raise RunResultNotReadyError("cancel-requested Run may not publish successful result")

        technical_block: Dict[str, Any] = {
            "solver_status": result.solver_status,
            "objective": result.objective,
        }
        if technical:
            technical_block.update(dict(technical))

        metrics = build_metrics_core(snapshot, result.solution, technical=technical_block)
        return self.runs.save_success(
            result.run_id,
            solution=result.solution.to_dict(),
            metrics=metrics,
        )


    def get_run_detail(
        self,
        run_id: str,
        *,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        record = self._record(run_id, actor_user_id=actor_user_id, is_admin=is_admin)
        snapshot = self.snapshots.get(run_id)
        if snapshot is None:
            raise RunResultServiceError("Run is missing immutable snapshot")
        if snapshot.content_hash != record.snapshot_hash:
            raise RunResultServiceError("Run snapshot hash drift")
        payload = snapshot.to_dict()
        situation = payload.get("situation") or {}
        out = record.to_dict()
        out["run_config"] = payload.get("run_config")
        out["situation"] = {
            "situation_id": snapshot.situation_id,
            "name": situation.get("name"),
            "content_hash": payload.get("situation_content_hash"),
        }
        return out

    @staticmethod
    def _metrics_compatible(base: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
        if base.get("schema_version") != other.get("schema_version"):
            return False
        a_axis = base.get("time_axis") or {}
        b_axis = other.get("time_axis") or {}
        if a_axis.get("slot_minutes") != b_axis.get("slot_minutes"):
            return False
        if list(a_axis.get("windows") or []) != list(b_axis.get("windows") or []):
            return False
        return True

    def list_comparable_successful(
        self,
        base_run_id: str,
        *,
        mode: str,
        actor_user_id: str,
        is_admin: bool = False,
        limit: int = 500,
    ) -> Dict[str, Any]:
        base_record, base_snapshot, _base_solution, base_metrics = self._successful_bundle(
            base_run_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        if mode not in {"multi_scenario", "configuration"}:
            raise RunResultServiceError("mode must be multi_scenario or configuration")
        candidates = self.runs.list_for_owner(
            base_record.owner_user_id, statuses=("succeeded",), limit=limit, offset=0
        )
        items = []
        for record in candidates:
            if record.run_id == base_run_id:
                continue
            snapshot = self.snapshots.get(record.run_id)
            result = self.runs.get_result_payloads(record.run_id)
            if snapshot is None or result is None:
                raise RunResultServiceError("succeeded comparison candidate is missing snapshot/result")
            _solution, metrics = result
            check = (
                check_multi_scenario_comparable(base_snapshot, snapshot)
                if mode == "multi_scenario"
                else check_configuration_comparable(base_snapshot, snapshot)
            )
            if not check.comparable or not self._metrics_compatible(base_metrics, metrics):
                continue
            payload = snapshot.to_dict()
            items.append({
                "run_id": record.run_id,
                "created_at": record.created_at,
                "finished_at": record.finished_at,
                "run_config": payload.get("run_config"),
            })
        return {
            "base_run_id": base_run_id,
            "mode": mode,
            "items": items,
        }


    def list_damage_comparison_candidates(
        self,
        *,
        actor_user_id: str,
        is_admin: bool = False,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Return backend-approved R0/R1/R2 triples for the damage workspace.

        This is deliberately a candidate query, not an automatic selection or ranking.
        Every returned triple has already passed the fixed R0/R1/R2 comparability rules
        and the canonical Metrics schema/time-axis compatibility check.
        """
        records = self.runs.list_for_owner(
            actor_user_id, statuses=("succeeded",), limit=min(max(int(limit), 1), 200), offset=0
        )
        bundles: Dict[str, Tuple[RunRecord, RunSnapshot, Dict[str, Any], Dict[str, Any]]] = {}
        for record in records:
            bundle = self._successful_bundle(
                record.run_id, actor_user_id=actor_user_id, is_admin=is_admin
            )
            bundles[record.run_id] = bundle

        def cfg(bundle):
            payload = bundle[1].to_dict()
            value = payload.get("run_config")
            return value if isinstance(value, dict) else {}

        r0s = [b for b in bundles.values() if cfg(b).get("damage_scenario_id") is None and not bool(cfg(b).get("cluster_enabled"))]
        r1s = [b for b in bundles.values() if cfg(b).get("damage_scenario_id") is not None and not bool(cfg(b).get("cluster_enabled"))]
        r2s = [b for b in bundles.values() if cfg(b).get("damage_scenario_id") is not None and bool(cfg(b).get("cluster_enabled"))]

        triples = []
        for r1 in r1s:
            for r0 in r0s:
                for r2 in r2s:
                    check = check_r0_r1_r2(r0[1], r1[1], r2[1])
                    if not check.comparable:
                        continue
                    if not self._metrics_compatible(r0[3], r1[3]) or not self._metrics_compatible(r1[3], r2[3]):
                        continue
                    triples.append({
                        "r0_run_id": r0[0].run_id,
                        "r1_run_id": r1[0].run_id,
                        "r2_run_id": r2[0].run_id,
                        "damage_scenario_id": cfg(r1).get("damage_scenario_id"),
                        "preference_mode": cfg(r1).get("preference_mode"),
                    })
        triples.sort(key=lambda x: (x["r1_run_id"], x["r0_run_id"], x["r2_run_id"]))
        return {"items": triples}

    def get_solution(
        self,
        run_id: str,
        *,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        _, _, solution, _ = self._successful_bundle(
            run_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        return solution

    def get_metrics(
        self,
        run_id: str,
        *,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        _, _, _, metrics = self._successful_bundle(
            run_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        return metrics

    def get_snapshot_situation(
        self,
        run_id: str,
        *,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        """Read-only historical Situation strictly from the frozen RunSnapshot."""
        _, snapshot, _, _ = self._successful_bundle(
            run_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        payload = snapshot.to_dict()
        situation = payload.get("situation")
        if not isinstance(situation, dict):
            raise RunResultServiceError("snapshot Situation is missing")
        return situation

    def get_single_run(
        self,
        run_id: str,
        *,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        record, snapshot, solution, metrics = self._successful_bundle(
            run_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        payload = snapshot.to_dict()
        return {
            "run": record.to_dict(),
            "snapshot": payload,
            "run_config": payload.get("run_config"),
            "situation": payload.get("situation"),
            "solution": solution,
            "metrics": metrics,
        }

    def compare_multi_scenario(
        self,
        *,
        run_ids: Tuple[str, ...],
        actor_user_id: str,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        if not isinstance(run_ids, tuple):
            run_ids = tuple(run_ids)
        bundles = [
            self._successful_bundle(rid, actor_user_id=actor_user_id, is_admin=is_admin)
            for rid in run_ids
        ]
        return build_multi_scenario_comparison(
            [(bundle[1], bundle[3]) for bundle in bundles]
        )

    def compare_configuration(
        self,
        *,
        run_ids: Tuple[str, ...],
        baseline_run_id: str,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        if not isinstance(run_ids, tuple):
            run_ids = tuple(run_ids)
        bundles = [
            self._successful_bundle(rid, actor_user_id=actor_user_id, is_admin=is_admin)
            for rid in run_ids
        ]
        return build_configuration_comparison(
            [(bundle[1], bundle[3]) for bundle in bundles],
            baseline_run_id=baseline_run_id,
        )

    def compare_r0_r1_r2(
        self,
        *,
        r0_run_id: str,
        r1_run_id: str,
        r2_run_id: str,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        bundles = [
            self._successful_bundle(rid, actor_user_id=actor_user_id, is_admin=is_admin)
            for rid in (r0_run_id, r1_run_id, r2_run_id)
        ]
        return build_r0_r1_r2_comparison(
            r0_snapshot=bundles[0][1], r0_metrics=bundles[0][3],
            r1_snapshot=bundles[1][1], r1_metrics=bundles[1][3],
            r2_snapshot=bundles[2][1], r2_metrics=bundles[2][3],
        )


__all__ = [
    "RunResultService",
    "RunResultServiceError",
    "RunResultAccessError",
    "RunResultNotFoundError",
    "RunResultNotReadyError",
]
