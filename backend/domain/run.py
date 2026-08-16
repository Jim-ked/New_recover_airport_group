from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

RUN_STATUSES: Tuple[str, ...] = ("queued", "running", "succeeded", "failed", "cancelled")
RUN_STAGES: Tuple[str, ...] = (
    "data_preparation",
    "candidate_generation",
    "quick_evaluation",
    "exact_optimization",
    "persistence",
)
RUN_EVENT_LEVELS: Tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")


class RunValidationError(ValueError):
    pass


def _nonblank(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunValidationError(f"{field} must be a nonblank string")
    return value


def _run_id(value: Any) -> str:
    value = _nonblank(value, "run_id")
    if not _RUN_ID_RE.fullmatch(value):
        raise RunValidationError("run_id must be a stable identifier")
    return value


def _hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise RunValidationError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    owner_user_id: str
    situation_id: str
    snapshot_hash: str
    status: str
    cancel_requested: bool
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    solution_hash: Optional[str] = None
    metrics_hash: Optional[str] = None

    def __post_init__(self) -> None:
        _run_id(self.run_id)
        _nonblank(self.owner_user_id, "owner_user_id")
        _nonblank(self.situation_id, "situation_id")
        _hash(self.snapshot_hash, "snapshot_hash")
        if self.status not in RUN_STATUSES:
            raise RunValidationError(f"status must be one of {RUN_STATUSES}")
        if not isinstance(self.cancel_requested, bool):
            raise RunValidationError("cancel_requested must be bool")
        _nonblank(self.created_at, "created_at")
        if self.started_at is not None:
            _nonblank(self.started_at, "started_at")
        if self.finished_at is not None:
            _nonblank(self.finished_at, "finished_at")
        if self.status == "failed":
            _nonblank(self.failure_message, "failure_message")
        elif self.failure_code is not None or self.failure_message is not None:
            raise RunValidationError("failure fields are only valid for failed Run")
        if self.status == "succeeded":
            _hash(self.solution_hash, "solution_hash")
            _hash(self.metrics_hash, "metrics_hash")
        elif self.solution_hash is not None or self.metrics_hash is not None:
            raise RunValidationError("canonical Solution/Metrics only belong to succeeded Run")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "owner_user_id": self.owner_user_id,
            "situation_id": self.situation_id,
            "snapshot_hash": self.snapshot_hash,
            "status": self.status,
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "failure": None if self.status != "failed" else {
                "code": self.failure_code,
                "message": self.failure_message,
            },
            "solution_hash": self.solution_hash,
            "metrics_hash": self.metrics_hash,
        }


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    seq: int
    level: str
    stage: str
    event: str
    message: str
    payload: Mapping[str, Any]
    created_at: str

    def __post_init__(self) -> None:
        _run_id(self.run_id)
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq <= 0:
            raise RunValidationError("seq must be a positive integer")
        if self.level not in RUN_EVENT_LEVELS:
            raise RunValidationError(f"level must be one of {RUN_EVENT_LEVELS}")
        if self.stage not in RUN_STAGES:
            raise RunValidationError(f"stage must be one of {RUN_STAGES}")
        _nonblank(self.event, "event")
        _nonblank(self.message, "message")
        if not isinstance(self.payload, Mapping):
            raise RunValidationError("payload must be a mapping")
        _nonblank(self.created_at, "created_at")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "seq": self.seq,
            "time": self.created_at,
            "level": self.level,
            "stage": self.stage,
            "event": self.event,
            "message": self.message,
            "payload": dict(self.payload),
        }


__all__ = [
    "RUN_STATUSES",
    "RUN_STAGES",
    "RUN_EVENT_LEVELS",
    "RunRecord",
    "RunEvent",
    "RunValidationError",
]
