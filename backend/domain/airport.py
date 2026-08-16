from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, NoReturn, Optional, Sequence, Tuple, Union

JsonNumber = Union[int, float]

FACILITY_TYPES = frozenset({"large_airport", "medium_airport", "small_airport"})
AIRPORT_ROLES = frozenset({"civil", "joint", "military"})

_AIRPORT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_RUNWAY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")

AIRPORT_FIELDS = frozenset(
    {
        "airport_id",
        "airport_name",
        "facility_type",
        "role",
        "icao_code",
        "iata_code",
        "region",
        "municipality",
        "longitude",
        "latitude",
        "elevation_m",
        "scheduled_service",
        "runway_count",
        "max_runway_length_m",
        "runways",
    }
)

RUNWAY_FIELDS = frozenset(
    {
        "runway_id",
        "length_m",
        "width_m",
        "surface",
        "lighted",
        "low_end",
        "high_end",
    }
)

RUNWAY_END_FIELDS = frozenset(
    {
        "ident",
        "latitude",
        "longitude",
        "elevation_m",
        "heading_deg_true",
        "displaced_threshold_m",
    }
)


class AirportValidationError(ValueError):
    """Airport static master data violates the confirmed schema."""

    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise AirportValidationError(message, field=field)


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(field, f"{field} must be a nonblank string")
    return value


def _optional_string(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(field, f"{field} must be a nonblank string or null")
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> JsonNumber:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(field, f"{field} must be a JSON number")
    numeric = float(value)
    if not math.isfinite(numeric):
        _fail(field, f"{field} must be finite")
    if minimum is not None and numeric < minimum:
        _fail(field, f"{field} must be greater than or equal to {minimum:g}")
    if maximum is not None and numeric > maximum:
        _fail(field, f"{field} must be less than or equal to {maximum:g}")
    return value


def _optional_number(
    value: Any,
    field: str,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> Optional[JsonNumber]:
    if value is None:
        return None
    return _number(value, field, minimum=minimum, maximum=maximum)


def _required_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        _fail(field, f"{field} must be boolean")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: Sequence[str], prefix: str = "") -> None:
    unknown = [str(key) for key in value if key not in allowed]
    if unknown:
        field = f"{prefix}{unknown[0]}"
        _fail(field, f"unknown field: {field}")


@dataclass(frozen=True)
class RunwayEnd:
    ident: Optional[str] = None
    latitude: Optional[JsonNumber] = None
    longitude: Optional[JsonNumber] = None
    elevation_m: Optional[JsonNumber] = None
    heading_deg_true: Optional[JsonNumber] = None
    displaced_threshold_m: Optional[JsonNumber] = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, field: str) -> "RunwayEnd":
        if not isinstance(value, Mapping):
            _fail(field, f"{field} must be an object")
        _reject_unknown(value, RUNWAY_END_FIELDS, prefix=f"{field}.")
        return cls(
            ident=_optional_string(value.get("ident"), f"{field}.ident"),
            latitude=_optional_number(
                value.get("latitude"), f"{field}.latitude", minimum=-90, maximum=90
            ),
            longitude=_optional_number(
                value.get("longitude"), f"{field}.longitude", minimum=-180, maximum=180
            ),
            elevation_m=_optional_number(value.get("elevation_m"), f"{field}.elevation_m"),
            heading_deg_true=_optional_number(
                value.get("heading_deg_true"),
                f"{field}.heading_deg_true",
                minimum=0,
                maximum=360,
            ),
            displaced_threshold_m=_optional_number(
                value.get("displaced_threshold_m"),
                f"{field}.displaced_threshold_m",
                minimum=0,
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ident": self.ident,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "elevation_m": self.elevation_m,
            "heading_deg_true": self.heading_deg_true,
            "displaced_threshold_m": self.displaced_threshold_m,
        }


@dataclass(frozen=True)
class RunwayBase:
    runway_id: str
    length_m: Optional[JsonNumber] = None
    width_m: Optional[JsonNumber] = None
    surface: Optional[str] = None
    lighted: Optional[bool] = None
    low_end: Optional[RunwayEnd] = None
    high_end: Optional[RunwayEnd] = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, index: int) -> "RunwayBase":
        field = f"runways[{index}]"
        if not isinstance(value, Mapping):
            _fail(field, f"{field} must be an object")
        _reject_unknown(value, RUNWAY_FIELDS, prefix=f"{field}.")

        runway_id = _required_string(value.get("runway_id"), f"{field}.runway_id")
        if not _RUNWAY_ID_RE.fullmatch(runway_id):
            _fail(f"{field}.runway_id", "runway_id contains unsupported characters or is too long")

        lighted = value.get("lighted")
        if lighted is not None and not isinstance(lighted, bool):
            _fail(f"{field}.lighted", f"{field}.lighted must be boolean or null")

        low_end = value.get("low_end")
        high_end = value.get("high_end")
        return cls(
            runway_id=runway_id,
            length_m=_optional_number(value.get("length_m"), f"{field}.length_m", minimum=0),
            width_m=_optional_number(value.get("width_m"), f"{field}.width_m", minimum=0),
            surface=_optional_string(value.get("surface"), f"{field}.surface"),
            lighted=lighted,
            low_end=(RunwayEnd.from_mapping(low_end, field=f"{field}.low_end") if low_end is not None else None),
            high_end=(RunwayEnd.from_mapping(high_end, field=f"{field}.high_end") if high_end is not None else None),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runway_id": self.runway_id,
            "length_m": self.length_m,
            "width_m": self.width_m,
            "surface": self.surface,
            "lighted": self.lighted,
            "low_end": self.low_end.to_dict() if self.low_end is not None else None,
            "high_end": self.high_end.to_dict() if self.high_end is not None else None,
        }


@dataclass(frozen=True)
class AirportBase:
    """
    Static airport master data only.

    Operational capacity, aircraft inventory/support, resource stock, support_level,
    tasks, damage and run-time state do NOT belong here. They have different life cycles.

    `runways` preserves source knowledge state:
    - None: structured runway data is unknown / unavailable.
    - (): structured runway data is known and there are zero active runways.
    - non-empty tuple: known active runways.
    """

    airport_id: str
    airport_name: str
    facility_type: str
    role: str
    longitude: JsonNumber
    latitude: JsonNumber
    scheduled_service: bool
    icao_code: Optional[str] = None
    iata_code: Optional[str] = None
    region: Optional[str] = None
    municipality: Optional[str] = None
    elevation_m: Optional[JsonNumber] = None
    runways: Optional[Tuple[RunwayBase, ...]] = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AirportBase":
        if not isinstance(value, Mapping):
            _fail("body", "AirportBase must be a JSON object")
        _reject_unknown(value, AIRPORT_FIELDS)

        airport_id = _required_string(value.get("airport_id"), "airport_id")
        if not _AIRPORT_ID_RE.fullmatch(airport_id):
            _fail("airport_id", "airport_id contains unsupported characters or is longer than 64 characters")

        airport_name = _required_string(value.get("airport_name"), "airport_name")

        facility_type = _required_string(value.get("facility_type"), "facility_type")
        if facility_type not in FACILITY_TYPES:
            _fail("facility_type", f"facility_type must be one of: {', '.join(sorted(FACILITY_TYPES))}")

        role = _required_string(value.get("role"), "role")
        if role not in AIRPORT_ROLES:
            _fail("role", f"role must be one of: {', '.join(sorted(AIRPORT_ROLES))}")

        if "longitude" not in value or value.get("longitude") is None:
            _fail("longitude", "longitude is required")
        if "latitude" not in value or value.get("latitude") is None:
            _fail("latitude", "latitude is required")
        if "scheduled_service" not in value:
            _fail("scheduled_service", "scheduled_service is required")

        longitude = _number(value["longitude"], "longitude", minimum=-180, maximum=180)
        latitude = _number(value["latitude"], "latitude", minimum=-90, maximum=90)
        scheduled_service = _required_bool(value["scheduled_service"], "scheduled_service")

        raw_runways = value.get("runways")
        runways: Optional[Tuple[RunwayBase, ...]]
        if raw_runways is None:
            runways = None
        else:
            if not isinstance(raw_runways, list):
                _fail("runways", "runways must be an array or null")
            parsed = tuple(RunwayBase.from_mapping(item, index=i) for i, item in enumerate(raw_runways))
            ids = [item.runway_id for item in parsed]
            if len(ids) != len(set(ids)):
                _fail("runways", "runway_id values must be unique within an airport")
            runways = parsed

        # runway_count / max_runway_length_m are source summary fields, not independent authority.
        # If present, validate them against the structured runway list to catch corrupt seed data.
        source_count = value.get("runway_count")
        source_max = value.get("max_runway_length_m")
        if runways is None:
            if source_count is not None:
                _fail("runway_count", "runway_count must be null when runways is null")
            if source_max is not None:
                _fail("max_runway_length_m", "max_runway_length_m must be null when runways is null")
        else:
            if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count < 0:
                _fail("runway_count", "runway_count must be a nonnegative integer when runways is known")
            if source_count != len(runways):
                _fail("runway_count", "runway_count does not match runways")
            lengths = [float(r.length_m) for r in runways if r.length_m is not None]
            expected_max = max(lengths) if lengths else None
            if expected_max is None:
                if source_max is not None:
                    _fail("max_runway_length_m", "max_runway_length_m must be null when no runway length is known")
            else:
                if source_max is None:
                    _fail("max_runway_length_m", "max_runway_length_m is required when runway lengths are known")
                numeric_max = float(_number(source_max, "max_runway_length_m", minimum=0))
                if abs(numeric_max - expected_max) > 1e-6:
                    _fail("max_runway_length_m", "max_runway_length_m does not match runways")

        return cls(
            airport_id=airport_id,
            airport_name=airport_name,
            facility_type=facility_type,
            role=role,
            longitude=longitude,
            latitude=latitude,
            scheduled_service=scheduled_service,
            icao_code=_optional_string(value.get("icao_code"), "icao_code"),
            iata_code=_optional_string(value.get("iata_code"), "iata_code"),
            region=_optional_string(value.get("region"), "region"),
            municipality=_optional_string(value.get("municipality"), "municipality"),
            elevation_m=_optional_number(value.get("elevation_m"), "elevation_m"),
            runways=runways,
        )

    @property
    def runway_count(self) -> Optional[int]:
        return None if self.runways is None else len(self.runways)

    @property
    def max_runway_length_m(self) -> Optional[float]:
        if self.runways is None:
            return None
        lengths = [float(r.length_m) for r in self.runways if r.length_m is not None]
        return max(lengths) if lengths else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "airport_id": self.airport_id,
            "airport_name": self.airport_name,
            "facility_type": self.facility_type,
            "role": self.role,
            "icao_code": self.icao_code,
            "iata_code": self.iata_code,
            "region": self.region,
            "municipality": self.municipality,
            "longitude": self.longitude,
            "latitude": self.latitude,
            "elevation_m": self.elevation_m,
            "scheduled_service": self.scheduled_service,
            "runway_count": self.runway_count,
            "max_runway_length_m": self.max_runway_length_m,
            "runways": None if self.runways is None else [r.to_dict() for r in self.runways],
        }
