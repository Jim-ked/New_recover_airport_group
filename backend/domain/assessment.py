from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, NoReturn, Optional


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class AssessmentValidationError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise AssessmentValidationError(message, field=field)


def _id(value: Any, field: str, *, optional: bool = False) -> Optional[str]:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _fail(field, f"{field} is invalid")
    return value


def _text(value: Any, field: str, *, optional: bool = False) -> Optional[str]:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(field, f"{field} must be a nonblank string")
    return value.strip()


def _number(
    value: Any,
    field: str,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
    optional: bool = False,
) -> Optional[float]:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(field, f"{field} must be a number")
    numeric = float(value)
    if not math.isfinite(numeric):
        _fail(field, f"{field} must be finite")
    if minimum is not None and numeric < minimum:
        _fail(field, f"{field} must be >= {minimum:g}")
    if maximum is not None and numeric > maximum:
        _fail(field, f"{field} must be <= {maximum:g}")
    return numeric


def _reject_unknown(raw: Mapping[str, Any], allowed: set[str]) -> None:
    unknown = [str(key) for key in raw if key not in allowed]
    if unknown:
        _fail(unknown[0], f"unknown field: {unknown[0]}")


def _hash(value: Any, field: str, *, optional: bool = False) -> Optional[str]:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        _fail(field, f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _count(value: Any, field: str, *, optional: bool = False) -> Optional[int]:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(field, f"{field} must be a nonnegative integer")
    return value


@dataclass(frozen=True)
class AssessmentResult:
    id: str
    airport_id: str
    scenario_type: str
    curves: tuple[tuple[int, float], ...]
    decline_rate: float
    recovery_slope: float
    initial_capacity: float
    task_threshold: Optional[float] = None
    metric_kind: str = "support_index_v1"
    matrix_value: Optional[float] = None
    source_run_id: Optional[str] = None
    snapshot_hash: Optional[str] = None
    metrics_hash: Optional[str] = None
    effective_windows: Optional[int] = None
    unavailable_windows: Optional[int] = None
    created_at: Optional[str] = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AssessmentResult":
        if not isinstance(raw, Mapping):
            _fail("body", "assessment result must be an object")
        _reject_unknown(
            raw,
            {
                "id",
                "airport_id",
                "scenario_type",
                "curves",
                "decline_rate",
                "recovery_slope",
                "task_threshold",
                "initial_capacity",
                "metric_kind",
                "matrix_value",
                "source_run_id",
                "snapshot_hash",
                "metrics_hash",
                "effective_windows",
                "unavailable_windows",
                "created_at",
            },
        )
        curves_raw = raw.get("curves")
        if not isinstance(curves_raw, Mapping) or not curves_raw:
            _fail("curves", "curves must be a nonempty time-to-support object")
        curves: list[tuple[int, float]] = []
        seen: set[int] = set()
        for raw_step, raw_support in curves_raw.items():
            try:
                step = int(raw_step)
            except (TypeError, ValueError):
                _fail("curves", "curve time steps must be integers")
            if step < 0 or step in seen:
                _fail("curves", "curve time steps must be unique nonnegative integers")
            seen.add(step)
            support = _number(raw_support, "curves", minimum=0.0, maximum=1.0)
            curves.append((step, float(support)))
        return cls(
            id=str(_id(raw.get("id"), "id")),
            airport_id=str(_id(raw.get("airport_id"), "airport_id")),
            scenario_type=str(_id(raw.get("scenario_type"), "scenario_type")),
            curves=tuple(sorted(curves)),
            decline_rate=float(_number(raw.get("decline_rate"), "decline_rate", minimum=0.0, maximum=1.0)),
            recovery_slope=float(_number(raw.get("recovery_slope"), "recovery_slope", minimum=0.0)),
            task_threshold=_number(raw.get("task_threshold"), "task_threshold", minimum=0.0, optional=True),
            initial_capacity=float(_number(raw.get("initial_capacity", 1.0), "initial_capacity", minimum=0.0)),
            metric_kind=str(_id(raw.get("metric_kind", "support_index_v1"), "metric_kind")),
            matrix_value=_number(raw.get("matrix_value"), "matrix_value", minimum=0.0, maximum=1.0, optional=True),
            source_run_id=_id(raw.get("source_run_id"), "source_run_id", optional=True),
            snapshot_hash=_hash(raw.get("snapshot_hash"), "snapshot_hash", optional=True),
            metrics_hash=_hash(raw.get("metrics_hash"), "metrics_hash", optional=True),
            effective_windows=_count(raw.get("effective_windows"), "effective_windows", optional=True),
            unavailable_windows=_count(raw.get("unavailable_windows"), "unavailable_windows", optional=True),
            created_at=_text(raw.get("created_at"), "created_at", optional=True),
        )


@dataclass(frozen=True)
class AssessmentAirportRisk:
    airport_id: str
    risk_level: int
    risk_score: float
    updated_at: Optional[str] = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AssessmentAirportRisk":
        if not isinstance(raw, Mapping):
            _fail("body", "assessment risk must be an object")
        _reject_unknown(raw, {"airport_id", "risk_level", "risk_score", "updated_at"})
        level = raw.get("risk_level")
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 5:
            _fail("risk_level", "risk_level must be an integer in [1,5]")
        return cls(
            airport_id=str(_id(raw.get("airport_id"), "airport_id")),
            risk_level=level,
            risk_score=float(_number(raw.get("risk_score", 0.0), "risk_score", minimum=0.0, maximum=1.0)),
            updated_at=_text(raw.get("updated_at"), "updated_at", optional=True),
        )


@dataclass(frozen=True)
class AssessmentComparison:
    id: str
    base_airport_id: Optional[str] = None
    base_scenario_type: Optional[str] = None
    compared_airport_id: Optional[str] = None
    compared_scenario_type: Optional[str] = None
    conclusion: Optional[str] = None
    log: Optional[str] = None
    created_at: Optional[str] = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AssessmentComparison":
        if not isinstance(raw, Mapping):
            _fail("body", "assessment comparison must be an object")
        _reject_unknown(
            raw,
            {
                "id",
                "base_airport_id",
                "base_scenario_type",
                "compared_airport_id",
                "compared_scenario_type",
                "conclusion",
                "log",
                "created_at",
            },
        )
        return cls(
            id=str(_id(raw.get("id"), "id")),
            base_airport_id=_id(raw.get("base_airport_id"), "base_airport_id", optional=True),
            base_scenario_type=_id(raw.get("base_scenario_type"), "base_scenario_type", optional=True),
            compared_airport_id=_id(raw.get("compared_airport_id"), "compared_airport_id", optional=True),
            compared_scenario_type=_id(raw.get("compared_scenario_type"), "compared_scenario_type", optional=True),
            conclusion=_text(raw.get("conclusion"), "conclusion", optional=True),
            log=_text(raw.get("log"), "log", optional=True),
            created_at=_text(raw.get("created_at"), "created_at", optional=True),
        )


@dataclass(frozen=True)
class IndicatorAssessmentValue:
    indicator_id: str
    code: str
    name: str
    raw_value: float
    normalized_value: float
    weight: float
    weight_source: str
    calculation_type: str
    evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        _id(self.indicator_id, "indicator_id")
        _id(self.code, "code")
        _text(self.name, "name")
        _number(self.raw_value, "raw_value", minimum=0.0, maximum=1.0)
        _number(self.normalized_value, "normalized_value", minimum=0.0, maximum=1.0)
        _number(self.weight, "weight", minimum=0.0)
        if self.weight_source not in {"expert", "ahp"}:
            _fail("weight_source", "weight_source must be expert or ahp")
        _text(self.calculation_type, "calculation_type")
        if not isinstance(self.evidence, Mapping):
            _fail("evidence", "evidence must be an object")

    def to_dict(self) -> dict[str, Any]:
        return {
            "indicator_id": self.indicator_id,
            "code": self.code,
            "name": self.name,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "weight": self.weight,
            "weight_source": self.weight_source,
            "calculation_type": self.calculation_type,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class RunIndicatorAssessment:
    run_id: str
    snapshot_hash: str
    metrics_hash: str
    indicator_set_id: str
    indicator_set_version: str
    indicators: tuple[IndicatorAssessmentValue, ...]
    status: str
    calculation_version: str
    weight_source: str
    weight_trace: Mapping[str, Any]
    support_index: Optional[float] = None
    overall_membership: Optional[Mapping[str, float]] = None
    weighted_composite: Optional[float] = None
    risk_score: Optional[float] = None
    risk_level: Optional[int] = None
    risk_status: str = "unavailable"
    risk_reason: Optional[str] = None
    unavailable_reason: Optional[str] = None
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        _id(self.run_id, "run_id")
        _hash(self.snapshot_hash, "snapshot_hash")
        _hash(self.metrics_hash, "metrics_hash")
        _id(self.indicator_set_id, "indicator_set_id")
        _text(self.indicator_set_version, "indicator_set_version")
        if self.status not in {"available", "unavailable"}:
            _fail("status", "status must be available or unavailable")
        if self.weight_source not in {"expert", "ahp"}:
            _fail("weight_source", "weight_source must be expert or ahp")
        if self.status == "available":
            if not self.indicators:
                _fail("indicators", "available assessment requires indicators")
            _number(self.support_index, "support_index", minimum=0.0, maximum=1.0)
            _number(self.weighted_composite, "weighted_composite", minimum=0.0, maximum=1.0)
            if self.unavailable_reason is not None:
                _fail("unavailable_reason", "available assessment cannot have unavailable_reason")
        elif not self.unavailable_reason:
            _fail("unavailable_reason", "unavailable assessment requires a reason")
        if self.risk_status not in {"available", "unavailable"}:
            _fail("risk_status", "risk_status must be available or unavailable")
        if self.risk_status == "available":
            _number(self.risk_score, "risk_score", minimum=0.0, maximum=1.0)
            if isinstance(self.risk_level, bool) or not isinstance(self.risk_level, int) or not 1 <= self.risk_level <= 5:
                _fail("risk_level", "risk_level must be in [1,5]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "snapshot_hash": self.snapshot_hash,
            "metrics_hash": self.metrics_hash,
            "indicator_set": {"id": self.indicator_set_id, "version": self.indicator_set_version},
            "indicators": [item.to_dict() for item in self.indicators],
            "status": self.status,
            "unavailable_reason": self.unavailable_reason,
            "calculation_version": self.calculation_version,
            "weight_source": self.weight_source,
            "weight_trace": dict(self.weight_trace),
            "support_index": self.support_index,
            "overall_membership": None if self.overall_membership is None else dict(self.overall_membership),
            "weighted_composite": self.weighted_composite,
            "risk": {
                "status": self.risk_status,
                "score": self.risk_score,
                "level": self.risk_level,
                "reason": self.risk_reason,
            },
            "created_at": self.created_at,
        }
__all__ = [
    "AssessmentValidationError",
    "AssessmentResult",
    "AssessmentAirportRisk",
    "AssessmentComparison",
    "IndicatorAssessmentValue",
    "RunIndicatorAssessment",
]
