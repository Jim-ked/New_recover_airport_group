from __future__ import annotations

from copy import deepcopy
from math import isclose
from typing import Any, Dict, Mapping, Sequence

from backend.domain.catalog import AircraftResourceRequirement, ResourceType
from backend.domain.run_config import RunConfigValidationError


RESEARCH_OBJECTIVE_PROFILE_ID = "research_objective_v1"
RESEARCH_OBJECTIVE_PROFILE_LABEL = "研究目标函数 v1"
RESEARCH_OBJECTIVE_SLOT_MINUTES = 15

_AIRCRAFT_WEIGHTS = {
    "fighter": 1.0,
    "bomber": 1.0,
    "transport": 0.9,
}
_F2_REFERENCES = {
    "fuel": 1.5625,
    "MAT-1": 1.0,
    "MAT-2": 1.0,
    "MAT-3": 1.0,
    "MUN-1": 2.0,
    "MUN-2": 1.0,
}
_F2_WEIGHTS = {
    "fuel": 1.0 / 3.0,
    "MAT-1": 1.0 / 9.0,
    "MAT-2": 1.0 / 9.0,
    "MAT-3": 1.0 / 9.0,
    "MUN-1": 1.0 / 6.0,
    "MUN-2": 1.0 / 6.0,
}
_RESOURCE_SEMANTICS = {
    "fuel": ("fuel", "t", "per_hour"),
    "MAT-1": ("material", "t", "per_sortie"),
    "MAT-2": ("material", "t", "per_sortie"),
    "MAT-3": ("material", "t", "per_sortie"),
    "MUN-1": ("munition", "t", "per_sortie"),
    "MUN-2": ("munition", "t", "per_sortie"),
}

_FIXED_SCALARS = {
    "f3_time_reference_slots": 16.0,
    "f3_tardiness_coefficient": 1.0,
    "unmet_demand_penalty": 3.0,
    "core_airport_reward_weight": 0.0,
}


def _fail(field: str, message: str) -> None:
    raise RunConfigValidationError(message, field=field)


def _same_number(left: Any, right: float) -> bool:
    if isinstance(left, bool) or not isinstance(left, (int, float)):
        return False
    return isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)


def _merge_fixed_mapping(
    raw: Mapping[str, Any], *, field: str, fixed: Mapping[str, float]
) -> Dict[str, float]:
    supplied = raw.get(field, {})
    if supplied is None:
        supplied = {}
    if not isinstance(supplied, Mapping):
        _fail(field, f"{field} must be an object")
    merged = dict(fixed)
    for key, value in supplied.items():
        stable_id = str(key)
        if stable_id in fixed and not _same_number(value, fixed[stable_id]):
            _fail(
                f"{field}.{stable_id}",
                f"{field}.{stable_id} conflicts with {RESEARCH_OBJECTIVE_PROFILE_ID}",
            )
        merged[stable_id] = value  # RunConfig performs finite/positive validation.
    return merged


def resolve_objective_profile(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Resolve one explicit profile before canonical RunConfig validation.

    Historical snapshots are read through RunConfig directly and are never passed here.
    New Run validation and submission both use this resolver in RunSnapshotService.
    Explicit extra aircraft/resources are permitted for semantically compatible extensions,
    while the established entries cannot be silently redefined under the same profile ID.
    """

    if not isinstance(raw, Mapping):
        _fail("run_config", "run_config must be an object")
    profile_id = raw.get("objective_profile_id")
    if profile_id != RESEARCH_OBJECTIVE_PROFILE_ID:
        _fail(
            "objective_profile_id",
            f"objective_profile_id must be {RESEARCH_OBJECTIVE_PROFILE_ID}",
        )

    resolved: Dict[str, Any] = deepcopy(dict(raw))
    resolved["aircraft_type_weight"] = _merge_fixed_mapping(
        raw, field="aircraft_type_weight", fixed=_AIRCRAFT_WEIGHTS
    )
    resolved["f2_resource_reference_quantities"] = _merge_fixed_mapping(
        raw, field="f2_resource_reference_quantities", fixed=_F2_REFERENCES
    )
    resolved["f2_resource_weights"] = _merge_fixed_mapping(
        raw, field="f2_resource_weights", fixed=_F2_WEIGHTS
    )
    for field, value in _FIXED_SCALARS.items():
        if field in raw and raw[field] is not None and not _same_number(raw[field], value):
            _fail(field, f"{field} conflicts with {RESEARCH_OBJECTIVE_PROFILE_ID}")
        resolved[field] = value
    return resolved


def validate_research_profile_semantics(
    *,
    profile_id: str | None,
    resource_types: Sequence[ResourceType],
    requirements: Sequence[AircraftResourceRequirement],
    used_aircraft_type_ids: Sequence[str],
    slot_minutes: int,
) -> None:
    if profile_id != RESEARCH_OBJECTIVE_PROFILE_ID:
        return
    if slot_minutes != RESEARCH_OBJECTIVE_SLOT_MINUTES:
        _fail(
            "objective_profile_id",
            f"{RESEARCH_OBJECTIVE_PROFILE_ID} requires 15-minute time slots",
        )

    resources = {row.resource_type_id: row for row in resource_types}
    used_aircraft = set(used_aircraft_type_ids)
    for resource_id, (category, unit, basis) in _RESOURCE_SEMANTICS.items():
        row = resources.get(resource_id)
        if row is not None and (row.category != category or row.unit != unit):
            _fail(
                f"resource_types.{resource_id}",
                f"{resource_id} must retain category={category!r} and unit={unit!r}",
            )
        incompatible = [
            req
            for req in requirements
            if req.aircraft_type_id in used_aircraft
            and req.resource_type_id == resource_id
            and float(req.quantity) > 0
            and req.basis != basis
        ]
        if incompatible:
            _fail(
                f"aircraft_resource_requirements.{resource_id}.basis",
                f"{resource_id} must retain consumption basis {basis!r}",
            )


def objective_profile_public() -> Dict[str, Any]:
    return {
        "profile_id": RESEARCH_OBJECTIVE_PROFILE_ID,
        "label": RESEARCH_OBJECTIVE_PROFILE_LABEL,
        "slot_minutes": RESEARCH_OBJECTIVE_SLOT_MINUTES,
        "aircraft_type_weight": dict(_AIRCRAFT_WEIGHTS),
        "f3_definition": "relative_completion_delay_plus_tardiness",
    }


__all__ = [
    "RESEARCH_OBJECTIVE_PROFILE_ID",
    "RESEARCH_OBJECTIVE_PROFILE_LABEL",
    "RESEARCH_OBJECTIVE_SLOT_MINUTES",
    "resolve_objective_profile",
    "validate_research_profile_semantics",
    "objective_profile_public",
]
