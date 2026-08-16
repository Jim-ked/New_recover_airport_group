from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, NoReturn, Optional, Sequence, Tuple

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
PREFERENCE_MODES = frozenset({"sortie_max", "resource_min", "time_min", "custom"})
PRESET_OBJECTIVE_WEIGHTS = {
    "sortie_max": (0.8, 0.1, 0.1),
    "resource_min": (0.1, 0.8, 0.1),
    "time_min": (0.1, 0.1, 0.8),
}
MAX_CORE_AIRPORTS = 2
MAX_CLUSTER_SIZE = 8
CUSTOM_ZERO_FLOOR = 0.05


class RunConfigValidationError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise RunConfigValidationError(message, field=field)


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _ID_RE.fullmatch(value):
        _fail(field, f"{field} must be a nonblank stable identifier")
    return value


def _optional_id(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    return _id(value, field)


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(field, f"{field} must be a positive finite number")
    out = float(value)
    if not math.isfinite(out) or out <= 0:
        _fail(field, f"{field} must be a positive finite number")
    return out


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(field, f"{field} must be a nonnegative integer")
    if value > 2_147_483_647:
        _fail(field, f"{field} must be <= 2147483647")
    return value


def _normalize_custom_alpha(value: Any) -> Tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        _fail("alpha", "custom alpha must contain exactly three numbers")
    vals = []
    for i, raw in enumerate(value):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            _fail(f"alpha[{i}]", "alpha values must be finite nonnegative numbers")
        v = float(raw)
        if not math.isfinite(v) or v < 0:
            _fail(f"alpha[{i}]", "alpha values must be finite nonnegative numbers")
        # Temporary compatibility with the already-used objective rule: an explicitly
        # zero dimension is retained at a small non-zero floor before normalization.
        vals.append(CUSTOM_ZERO_FLOOR if v == 0.0 else v)
    total = sum(vals)
    if total <= 0:
        _fail("alpha", "alpha must have positive total weight")
    return tuple(v / total for v in vals)  # type: ignore[return-value]


@dataclass(frozen=True)
class RunConfig:
    """Canonical, already-resolved user run configuration.

    Preset modes freeze their resolved objective weights. Custom mode applies the
    existing non-zero-floor rule and then normalizes to sum to one. `core_airports`
    contains only identities; the algorithm's core-airport benefit multiplier is not a
    user data field.
    """

    damage_scenario_id: Optional[str]
    preference_mode: str
    alpha: Tuple[float, float, float]
    cluster_enabled: bool
    cluster_size: Optional[int]
    core_airports: Tuple[str, ...]
    aircraft_type_weights: Tuple[Tuple[str, float], ...]
    mip_time_limit_s: float
    algorithm_seed: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "RunConfig":
        if not isinstance(raw, Mapping):
            _fail("run_config", "run_config must be an object")
        allowed = {
            "damage_scenario_id", "preference_mode", "alpha", "cluster_enabled",
            "cluster_size", "core_airports", "aircraft_type_weight", "mip_time_limit_s", "algorithm_seed",
        }
        unknown = set(raw) - allowed
        if unknown:
            _fail(str(sorted(unknown)[0]), f"unknown run_config field: {sorted(unknown)[0]}")

        mode = raw.get("preference_mode")
        if mode not in PREFERENCE_MODES:
            _fail("preference_mode", f"preference_mode must be one of {sorted(PREFERENCE_MODES)}")
        if mode == "custom":
            if "alpha" not in raw:
                _fail("alpha", "alpha is required when preference_mode=custom")
            alpha = _normalize_custom_alpha(raw.get("alpha"))
        else:
            alpha = PRESET_OBJECTIVE_WEIGHTS[str(mode)]

        cluster_enabled = raw.get("cluster_enabled")
        if not isinstance(cluster_enabled, bool):
            _fail("cluster_enabled", "cluster_enabled must be boolean")

        raw_core = raw.get("core_airports", [])
        if not isinstance(raw_core, list):
            _fail("core_airports", "core_airports must be an array")
        core = tuple(sorted(_id(v, f"core_airports[{i}]") for i, v in enumerate(raw_core)))
        if len(core) != len(set(core)):
            _fail("core_airports", "core_airports must not contain duplicates")
        if len(core) > MAX_CORE_AIRPORTS:
            _fail("core_airports", f"at most {MAX_CORE_AIRPORTS} core airports are allowed")

        raw_size = raw.get("cluster_size")
        cluster_size: Optional[int]
        if cluster_enabled:
            if isinstance(raw_size, bool) or not isinstance(raw_size, int):
                _fail("cluster_size", "cluster_size must be an integer when clustering is enabled")
            if raw_size < 1 or raw_size > MAX_CLUSTER_SIZE:
                _fail("cluster_size", f"cluster_size must be in [1,{MAX_CLUSTER_SIZE}]")
            if raw_size < len(core):
                _fail("cluster_size", "cluster_size cannot be smaller than core_airports count")
            cluster_size = raw_size
        else:
            if raw_size not in (None, 0):
                _fail("cluster_size", "cluster_size must be null/0 when clustering is disabled")
            if core:
                _fail("core_airports", "core_airports must be empty when clustering is disabled")
            cluster_size = None

        raw_weights = raw.get("aircraft_type_weight", {})
        if not isinstance(raw_weights, Mapping):
            _fail("aircraft_type_weight", "aircraft_type_weight must be an object")
        weight_rows = []
        for key, value in raw_weights.items():
            aircraft_id = _id(key, f"aircraft_type_weight.{key}")
            weight_rows.append((aircraft_id, _positive_number(value, f"aircraft_type_weight.{key}")))
        weight_rows.sort(key=lambda x: x[0])

        return cls(
            damage_scenario_id=_optional_id(raw.get("damage_scenario_id"), "damage_scenario_id"),
            preference_mode=str(mode),
            alpha=alpha,
            cluster_enabled=cluster_enabled,
            cluster_size=cluster_size,
            core_airports=core,
            aircraft_type_weights=tuple(weight_rows),
            mip_time_limit_s=_positive_number(raw.get("mip_time_limit_s"), "mip_time_limit_s"),
            algorithm_seed=_nonnegative_int(raw.get("algorithm_seed", 42), "algorithm_seed"),
        )

    def validate_against(
        self,
        *,
        airport_ids: Sequence[str],
        damage_scenario_ids: Sequence[str],
        aircraft_type_ids: Sequence[str],
    ) -> None:
        airports = set(airport_ids)
        unknown_core = sorted(set(self.core_airports) - airports)
        if unknown_core:
            _fail("core_airports", f"core airports not in Situation: {unknown_core}")
        if self.cluster_enabled and self.cluster_size is not None and self.cluster_size > len(airports):
            _fail("cluster_size", "cluster_size cannot exceed Situation airport count")

        scenarios = set(damage_scenario_ids)
        if self.damage_scenario_id is not None and self.damage_scenario_id not in scenarios:
            _fail("damage_scenario_id", "selected damage_scenario_id is not in Situation")

        known_types = set(aircraft_type_ids)
        unknown_types = sorted({k for k, _ in self.aircraft_type_weights} - known_types)
        if unknown_types:
            _fail("aircraft_type_weight", f"unknown aircraft type weights: {unknown_types}")

    @property
    def aircraft_type_weight(self) -> Dict[str, float]:
        return dict(self.aircraft_type_weights)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "damage_scenario_id": self.damage_scenario_id,
            "preference_mode": self.preference_mode,
            "alpha": [float(v) for v in self.alpha],
            "cluster_enabled": self.cluster_enabled,
            "cluster_size": self.cluster_size,
            "core_airports": list(self.core_airports),
            "aircraft_type_weight": {k: v for k, v in self.aircraft_type_weights},
            "mip_time_limit_s": self.mip_time_limit_s,
            "algorithm_seed": self.algorithm_seed,
        }
