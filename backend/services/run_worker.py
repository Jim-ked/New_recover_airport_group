from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from backend.algorithm.runner import (
    AlgorithmInfeasibleError,
    AlgorithmRunError,
    AlgorithmRunResult,
    run_once,
)
from backend.domain.run import RunRecord
from backend.services.run_result_service import RunResultService
from backend.services.run_service import RunService
from backend.storage.run_snapshot_repository import RunSnapshotRepository


class RunWorkerError(RuntimeError):
    pass


class RunWorkerCancelled(RunWorkerError):
    pass


AlgorithmRunner = Callable[..., AlgorithmRunResult]


# Public RunEvent stages are deliberately coarser than algorithm-internal stages.
# They are labels, not a monotonic state machine: candidate generation / evaluation may
# interleave inside SA. The frontend must not infer solver truth from display order.
_INTERNAL_STAGE_TO_PUBLIC: Dict[str, str] = {
    "prepare": "data_preparation",
    "cluster": "candidate_generation",
    "paths": "candidate_generation",
    "model": "exact_optimization",
    "solve": "exact_optimization",
    "solution": "persistence",
    "complete": "persistence",
}


def _event_level(value: Any) -> str:
    text = str(value or "INFO").strip().upper()
    if text not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        return "INFO"
    return text


class RunWorker:
    """Synchronous application worker for one queued Run.

    Queue scheduling/thread/process ownership is intentionally outside this class. The
    worker consumes only the immutable RunSnapshot identified by run_id and owns the
    lifecycle transition from queued -> running -> terminal.

    Cancellation is cooperative at algorithm stage boundaries. If a cancel request arrives
    during a blocking solver optimize() call, canonical success is still prevented and the
    Run becomes cancelled immediately after control returns to the worker. Solver-level
    interrupt wiring is a later PySCIPOpt integration concern and is not faked here.
    """

    def __init__(
        self,
        *,
        run_service: RunService,
        result_service: RunResultService,
        snapshot_repository: RunSnapshotRepository,
        algorithm_runner: AlgorithmRunner = run_once,
    ) -> None:
        self.run_service = run_service
        self.results = result_service
        self.snapshots = snapshot_repository
        self.algorithm_runner = algorithm_runner

    def _check_cancel(self, run_id: str) -> None:
        if self.run_service.worker_cancel_requested(run_id):
            raise RunWorkerCancelled("Run cancellation requested")

    def _append(
        self,
        run_id: str,
        *,
        level: str,
        stage: str,
        event: str,
        message: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.run_service.append_worker_event(
            run_id,
            level=level,
            stage=stage,
            event=event,
            message=message,
            payload=payload,
        )

    def _algorithm_event_callback(self, run_id: str):
        def callback(raw: Mapping[str, Any]) -> None:
            # Check before persisting each new algorithm activity so cancellation never
            # appears to advance after the request was observed by this process.
            self._check_cancel(run_id)
            internal_stage = str(raw.get("stage") or "").strip()
            public_stage = _INTERNAL_STAGE_TO_PUBLIC.get(internal_stage)
            if public_stage is None:
                raise RunWorkerError(f"unknown algorithm stage: {internal_stage!r}")
            payload = dict(raw.get("payload") or {})
            payload.update({
                "internal_stage": internal_stage,
                "algorithm_progress": float(raw.get("progress") or 0.0),
            })
            if internal_stage == "cluster":
                # The current SA selector interleaves candidate generation and LP quick
                # evaluation. Preserve that fact explicitly instead of inventing a false
                # sequential public event.
                payload["activity_semantics"] = "candidate_generation_and_quick_evaluation_interleaved"
            self._append(
                run_id,
                level=_event_level(raw.get("level")),
                stage=public_stage,
                event=str(raw.get("type") or "algorithm_stage"),
                message=str(raw.get("message") or internal_stage),
                payload=payload,
            )
        return callback

    def execute(self, run_id: str, **algorithm_kwargs: Any) -> RunRecord:
        """Execute one queued Run synchronously and return its terminal RunRecord."""
        try:
            self.run_service.claim_for_worker(run_id)
        except Exception as exc:
            raise RunWorkerError(f"cannot claim queued Run {run_id}: {exc}") from exc

        self._append(
            run_id,
            level="INFO",
            stage="data_preparation",
            event="worker_started",
            message="Run worker claimed immutable snapshot",
        )

        snapshot = self.snapshots.get(run_id)
        if snapshot is None:
            record = self.run_service.mark_failed_by_worker(
                run_id, message="immutable RunSnapshot is missing", code="SNAPSHOT_MISSING"
            )
            self._append(
                run_id, level="ERROR", stage="data_preparation", event="run_failed",
                message="Immutable RunSnapshot is missing",
            )
            return record

        try:
            self._check_cancel(run_id)
            result = self.algorithm_runner(
                snapshot,
                event_cb=self._algorithm_event_callback(run_id),
                **algorithm_kwargs,
            )
            self._check_cancel(run_id)
            record = self.results.persist_success(result=result)
            self._append(
                run_id,
                level="INFO",
                stage="persistence",
                event="run_succeeded",
                message="Canonical Solution and Metrics persisted",
                payload={
                    "solver_status": result.solver_status,
                    "objective": result.objective,
                },
            )
            return record

        except RunWorkerCancelled:
            record = self.run_service.mark_cancelled_by_worker(run_id)
            self._append(
                run_id,
                level="WARNING",
                stage="persistence",
                event="run_cancelled",
                message="Run cancelled after worker observed cancellation request",
            )
            return record

        except AlgorithmInfeasibleError as exc:
            # If cancellation arrived while the solver was blocking, cancellation wins over
            # infeasible/failure publication because the user explicitly withdrew the Run.
            if self.run_service.worker_cancel_requested(run_id):
                record = self.run_service.mark_cancelled_by_worker(run_id)
                self._append(
                    run_id, level="WARNING", stage="persistence", event="run_cancelled",
                    message="Run cancelled after solver returned",
                )
                return record
            record = self.run_service.mark_failed_by_worker(
                run_id, message=str(exc), code="INFEASIBLE"
            )
            self._append(
                run_id,
                level="ERROR",
                stage="exact_optimization",
                event="run_failed",
                message=str(exc),
                payload={"failure_code": "INFEASIBLE"},
            )
            return record

        except AlgorithmRunError as exc:
            if self.run_service.worker_cancel_requested(run_id):
                record = self.run_service.mark_cancelled_by_worker(run_id)
                self._append(
                    run_id, level="WARNING", stage="persistence", event="run_cancelled",
                    message="Run cancelled after algorithm returned",
                )
                return record
            record = self.run_service.mark_failed_by_worker(
                run_id, message=str(exc), code="ALGORITHM_ERROR"
            )
            self._append(
                run_id,
                level="ERROR",
                stage="exact_optimization",
                event="run_failed",
                message=str(exc),
                payload={"failure_code": "ALGORITHM_ERROR"},
            )
            return record

        except Exception as exc:
            if self.run_service.worker_cancel_requested(run_id):
                record = self.run_service.mark_cancelled_by_worker(run_id)
                self._append(
                    run_id, level="WARNING", stage="persistence", event="run_cancelled",
                    message="Run cancelled after worker regained control",
                )
                return record
            record = self.run_service.mark_failed_by_worker(
                run_id, message=str(exc), code="WORKER_ERROR"
            )
            self._append(
                run_id,
                level="ERROR",
                stage="persistence",
                event="run_failed",
                message=str(exc),
                payload={"failure_code": "WORKER_ERROR"},
            )
            return record


__all__ = ["RunWorker", "RunWorkerError", "RunWorkerCancelled"]
