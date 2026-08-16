from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Union

from backend.domain.run import RunRecord
from backend.domain.run_config import RunConfig
from backend.domain.run_snapshot import RunSnapshot
from backend.services.od_distance_service import ODDistanceService
from backend.services.run_service import RunService
from backend.services.run_snapshot_service import RunSnapshotService
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.situation_repository import SituationRepository


class RunSubmissionServiceError(RuntimeError):
    pass


class RunSubmissionSituationNotFoundError(RunSubmissionServiceError):
    pass


class RunSubmissionBlockedError(RunSubmissionServiceError):
    def __init__(self, report: "RunValidationResult"):
        super().__init__("Run preflight validation failed")
        self.report = report


class RunSubmissionStaleValidationError(RunSubmissionServiceError):
    pass


@dataclass(frozen=True)
class SolverProbeResult:
    available: bool
    message: str


SolverProbe = Callable[[], SolverProbeResult]


def default_solver_probe() -> SolverProbeResult:
    try:
        from pyscipopt import Model  # noqa: F401
    except Exception as exc:
        return SolverProbeResult(False, f"PySCIPOpt solver service unavailable: {exc}")
    return SolverProbeResult(True, "PySCIPOpt solver service is importable")


@dataclass(frozen=True)
class RunValidationCheck:
    code: str
    status: str
    message: str
    field: Optional[str] = None
    details: Optional[Mapping[str, Any]] = None

    def __post_init__(self) -> None:
        if self.status not in {"passed", "warning", "failed"}:
            raise ValueError("validation check status must be passed|warning|failed")

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "code": self.code,
            "status": self.status,
            "message": self.message,
        }
        if self.field is not None:
            out["field"] = self.field
        if self.details is not None:
            out["details"] = dict(self.details)
        return out


@dataclass(frozen=True)
class RunValidationResult:
    situation_id: str
    situation_content_hash: str
    validated_input_hash: str
    run_config: Dict[str, Any]
    airport_count: int
    mission_count: int
    od_pair_count: int
    checks: Sequence[RunValidationCheck]

    @property
    def can_submit(self) -> bool:
        return not any(check.status == "failed" for check in self.checks)

    @property
    def status(self) -> str:
        if not self.can_submit:
            return "failed"
        if any(check.status == "warning" for check in self.checks):
            return "warning"
        return "passed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "can_submit": self.can_submit,
            "situation_id": self.situation_id,
            "situation_content_hash": self.situation_content_hash,
            "validated_input_hash": self.validated_input_hash,
            "run_config": dict(self.run_config),
            "input_summary": {
                "airport_count": self.airport_count,
                "mission_count": self.mission_count,
                "od_pair_count": self.od_pair_count,
            },
            "checks": [check.to_dict() for check in self.checks],
        }


def _input_fingerprint(snapshot: RunSnapshot) -> str:
    payload = snapshot.to_dict()
    payload.pop("run_id", None)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RunSubmissionService:
    """Prepare/validate Run input without exposing derived OD data to Web callers."""

    VALIDATION_RUN_ID = "RUN-VALIDATION"

    def __init__(
        self,
        *,
        situation_repository: SituationRepository,
        snapshot_repository: RunSnapshotRepository,
        snapshot_service: RunSnapshotService,
        run_service: RunService,
        od_distance_service: ODDistanceService,
        solver_probe: SolverProbe = default_solver_probe,
    ) -> None:
        self.situations = situation_repository
        self.snapshot_repository = snapshot_repository
        self.snapshots = snapshot_service
        self.runs = run_service
        self.od = od_distance_service
        self.solver_probe = solver_probe

    def _prepare(
        self,
        *,
        run_id: str,
        owner_user_id: str,
        is_admin: bool,
        situation_id: str,
        run_config: Union[Mapping[str, Any], RunConfig],
    ):
        situation = self.situations.get_situation_for_actor(
            situation_id, actor_user_id=owner_user_id, is_admin=is_admin
        )
        if situation is None:
            raise RunSubmissionSituationNotFoundError(f"situation not found: {situation_id}")
        distances = self.od.build_for_situation(situation)
        snapshot = self.snapshots.build_snapshot(
            run_id=run_id,
            situation_id=situation_id,
            run_config=run_config,
            od_distances=distances,
        )
        return situation, snapshot, distances

    def _preflight(
        self,
        *,
        owner_user_id: str,
        situation,
        snapshot: RunSnapshot,
        od_pair_count: int,
    ) -> RunValidationResult:
        payload = snapshot.to_dict()
        checks: list[RunValidationCheck] = []

        checks.append(RunValidationCheck(
            "situation_saved",
            "passed",
            "Situation exists in authoritative storage and was frozen for validation",
            field="situation_id",
        ))

        if not situation.airports:
            checks.append(RunValidationCheck(
                "airport_presence", "failed", "Situation must contain at least one airport",
                field="situation.airports",
            ))
        else:
            checks.append(RunValidationCheck(
                "airport_presence", "passed", f"{len(situation.airports)} airport(s) available",
                field="situation.airports",
            ))

        if not situation.missions:
            checks.append(RunValidationCheck(
                "mission_presence", "failed", "Situation must contain at least one mission",
                field="situation.missions",
            ))
        else:
            checks.append(RunValidationCheck(
                "mission_presence", "passed", f"{len(situation.missions)} mission(s) available",
                field="situation.missions",
            ))

        checks.append(RunValidationCheck(
            "run_configuration",
            "passed",
            "Damage selection, cluster/core settings, solver limit and objective weights are valid",
            field="run_config",
        ))
        checks.append(RunValidationCheck(
            "od_closure",
            "passed",
            f"Complete canonical airport × mission OD closure contains {od_pair_count} pair(s)",
        ))

        solver = self.solver_probe()
        checks.append(RunValidationCheck(
            "solver_service",
            "passed" if solver.available else "failed",
            solver.message,
        ))

        active = self.runs.list(
            actor_user_id=owner_user_id,
            statuses=("queued", "running"),
            limit=500,
            offset=0,
        )
        checks.append(RunValidationCheck(
            "queue_access",
            "passed",
            "Persistent Run queue is accessible",
            details={"owner_active_run_count": len(active)},
        ))

        target_fp = _input_fingerprint(snapshot)
        duplicate_ids: list[str] = []
        for record in active:
            other = self.snapshot_repository.get(record.run_id)
            if other is not None and _input_fingerprint(other) == target_fp:
                duplicate_ids.append(record.run_id)
        if duplicate_ids:
            checks.append(RunValidationCheck(
                "duplicate_active_run",
                "warning",
                "An active Run with the same frozen business input already exists",
                details={"run_ids": duplicate_ids},
            ))
        else:
            checks.append(RunValidationCheck(
                "duplicate_active_run", "passed", "No identical queued/running Run exists for this owner"
            ))

        return RunValidationResult(
            situation_id=situation.situation_id,
            situation_content_hash=str(payload["situation_content_hash"]),
            validated_input_hash=target_fp,
            run_config=dict(payload["run_config"]),
            airport_count=len(situation.airports),
            mission_count=len(situation.missions),
            od_pair_count=od_pair_count,
            checks=tuple(checks),
        )

    def validate(
        self,
        *,
        owner_user_id: str,
        is_admin: bool = False,
        situation_id: str,
        run_config: Union[Mapping[str, Any], RunConfig],
    ) -> RunValidationResult:
        situation, snapshot, distances = self._prepare(
            run_id=self.VALIDATION_RUN_ID,
            owner_user_id=owner_user_id,
            is_admin=is_admin,
            situation_id=situation_id,
            run_config=run_config,
        )
        return self._preflight(
            owner_user_id=owner_user_id,
            situation=situation,
            snapshot=snapshot,
            od_pair_count=len(distances),
        )

    def submit(
        self,
        *,
        run_id: str,
        owner_user_id: str,
        is_admin: bool = False,
        situation_id: str,
        run_config: Union[Mapping[str, Any], RunConfig],
        expected_input_hash: Optional[str] = None,
    ) -> RunRecord:
        situation, snapshot, distances = self._prepare(
            run_id=run_id,
            owner_user_id=owner_user_id,
            is_admin=is_admin,
            situation_id=situation_id,
            run_config=run_config,
        )
        if expected_input_hash is not None:
            if not isinstance(expected_input_hash, str) or len(expected_input_hash) != 64:
                raise RunSubmissionStaleValidationError("expected_input_hash must be a 64-character validation fingerprint")
            actual_input_hash = _input_fingerprint(snapshot)
            if actual_input_hash != expected_input_hash:
                raise RunSubmissionStaleValidationError(
                    "Run input changed after validation; validate the current input again before submitting"
                )
        report = self._preflight(
            owner_user_id=owner_user_id,
            situation=situation,
            snapshot=snapshot,
            od_pair_count=len(distances),
        )
        if not report.can_submit:
            raise RunSubmissionBlockedError(report)
        # Persist the exact closure used by preflight; do not re-read mutable Situation.
        return self.runs.submit_snapshot(snapshot=snapshot, owner_user_id=owner_user_id)


__all__ = [
    "RunSubmissionService",
    "RunSubmissionServiceError",
    "RunSubmissionSituationNotFoundError",
    "RunSubmissionBlockedError",
    "RunSubmissionStaleValidationError",
    "RunValidationResult",
    "RunValidationCheck",
    "SolverProbeResult",
    "SolverProbe",
    "default_solver_probe",
]
