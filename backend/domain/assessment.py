from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, NoReturn, Optional


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


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


__all__ = [
    "AssessmentValidationError",
    "AssessmentResult",
    "AssessmentAirportRisk",
    "AssessmentComparison",
]
