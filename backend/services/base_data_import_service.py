from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from backend.domain.airport import AirportBase, AirportValidationError
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.domain.mission import Mission
from backend.services.airport_master_parser import (
    AirportMasterParseError,
    parse_airport_master_document,
)
from backend.storage.airport_repository import AirportRepository
from backend.storage.mission_repository import MissionRepository
from backend.security.sm4 import SM4Error, SM4FileCipher
from backend.services import data_codec_service as codec


class BaseDataImportError(ValueError):
    pass


SUPPORTED_DATASETS = frozenset({
    "airports",
    "missions",
    "aircraft_types",
    "resource_types",
    "aircraft_resource_requirements",
})

# Stable file columns. Optional domain fields may be omitted on input, but an
# unknown column is never silently discarded. Nested values use JSON cells.
TABULAR_COLUMNS = {
    "airports": ("airport_id", "airport_name", "facility_type", "role", "icao_code", "iata_code",
                 "region", "municipality", "longitude", "latitude", "elevation_m", "scheduled_service",
                 "parking_stand_count", "runway_count", "max_runway_length_m", "runways_json", "operational_profile_json"),
    "missions": ("mission_id", "name", "longitude", "latitude", "window_start_slot", "window_end_slot", "aircraft_requirements_json"),
    "aircraft_types": ("aircraft_type_id", "name", "speed_kmh", "max_range_km", "reserve_ratio",
                       "departure_capacity_occupancy_factor", "arrival_capacity_occupancy_factor"),
    "resource_types": ("resource_type_id", "name", "category", "unit"),
    "aircraft_resource_requirements": ("aircraft_type_id", "resource_type_id", "basis", "quantity"),
}
REQUIRED_COLUMNS = {
    "airports": {"airport_id", "airport_name", "facility_type", "role", "longitude", "latitude", "scheduled_service"},
    "missions": {"mission_id", "name", "longitude", "latitude", "window_start_slot", "window_end_slot"},
    "aircraft_types": {"aircraft_type_id", "name"},
    "resource_types": set(TABULAR_COLUMNS["resource_types"]),
    "aircraft_resource_requirements": set(TABULAR_COLUMNS["aircraft_resource_requirements"]),
}


def validate_columns(dataset: str, fields: Iterable[str]) -> None:
    fields = set(fields)
    missing = REQUIRED_COLUMNS[dataset] - fields
    unknown = fields - set(TABULAR_COLUMNS[dataset])
    if missing:
        raise BaseDataImportError(f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise BaseDataImportError(f"unknown fields: {', '.join(sorted(str(x) for x in unknown))}")


@dataclass(frozen=True)
class BaseDataReplaceResult:
    dataset: str
    source_format: str
    added: int
    updated: int
    deleted: int
    total: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset": self.dataset,
            "source_format": self.source_format,
            "mode": "replace_current_state",
            "version_history": False,
            "added": self.added,
            "updated": self.updated,
            "deleted": self.deleted,
            "total": self.total,
        }


def _none_if_blank(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _float_or_none(value: Any) -> Optional[float]:
    value = _none_if_blank(value)
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    return None if value is None else float(value)


def _int_or_none(value: Any) -> Optional[int]:
    value = _none_if_blank(value)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("value must be an integer")
    return int(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in {"1", "true", "yes", "y", "是"}:
        return True
    if text in {"0", "false", "no", "n", "否"}:
        return False
    raise BaseDataImportError(f"invalid boolean value: {value!r}")


def _json_cell(value: Any, *, field: str, default: Any) -> Any:
    value = _none_if_blank(value)
    if value is None:
        return default
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise BaseDataImportError(f"{field} must contain valid JSON") from exc
    return parsed


class BaseDataImportService:
    """Replace the *current* Base Data catalog without maintaining version history.

    Single-record CRUD may still expose a monotonically increasing ``revision`` for
    optimistic concurrency.  That revision is not a browsable data version and is never
    used to select algorithm input.  Bulk import replaces one current dataset in a single
    SQLite transaction and returns only a change summary suitable for the unified audit
    log.
    """

    def __init__(self, *, airport_repository: AirportRepository, mission_repository: MissionRepository) -> None:
        self.airports = airport_repository
        self.missions = mission_repository

    def replace_json(self, dataset: str, raw_items: Any) -> BaseDataReplaceResult:
        dataset = self._dataset(dataset)
        if not isinstance(raw_items, list):
            raise BaseDataImportError("items must be an array")
        return self._replace(dataset, raw_items, source_format="json")

    def replace_airport_master_document(self, raw: Any) -> BaseDataReplaceResult:
        """Replace the current airport base dataset from a full airport_master_v1 document.

        Shares ``parse_airport_master_document`` with the seed bootstrap path.  The
        master document only carries AirportBase facts, so existing operational
        profiles of surviving airports are preserved; only airports removed from
        the dataset follow the replace-current-state deletion rules.
        """
        try:
            airports = parse_airport_master_document(raw)
        except (AirportMasterParseError, AirportValidationError) as exc:
            raise BaseDataImportError(f"airport master document is invalid: {exc}") from exc
        summary = self.airports.replace_airport_bases_preserving_profiles(airports)
        return BaseDataReplaceResult(
            dataset="airports", source_format="airport_master_v1", **summary
        )

    def replace_csv(self, dataset: str, text: str) -> BaseDataReplaceResult:
        dataset = self._dataset(dataset)
        if not isinstance(text, str) or not text.strip():
            raise BaseDataImportError("CSV content is empty")
        try:
            # Validate headers even for the legacy, explicitly header-only replacement.
            headers = next(csv.reader(io.StringIO(text.lstrip("\ufeff"))), [])
            validate_columns(dataset, (header.strip() for header in headers))
            rows = codec.decode_records(text.encode("utf-8"), fmt="csv")
        except (csv.Error, codec.DataCodecError) as exc:
            raise BaseDataImportError(str(exc)) from exc
        items = [self._csv_row(dataset, row, index=i) for i, row in enumerate(rows)]
        return self._replace(dataset, items, source_format="csv")

    def parse_multi_format(self, dataset: str, data: bytes, *, format: str,
                           encrypted: bool = False, cipher: Optional[SM4FileCipher] = None) -> Any:
        """Decrypt and parse a file; no database write occurs in this method."""
        dataset = self._dataset(dataset)
        try:
            fmt = codec.normalize_format(format)
            if not isinstance(data, bytes) or not data or len(data) > codec.MAX_FILE_BYTES:
                raise BaseDataImportError("file is empty or exceeds 50 MB")
            if encrypted or SM4FileCipher.is_encrypted(data):
                data = (cipher or SM4FileCipher.from_environment()).decrypt_file(data)
            parsed = codec.decode_records(data, fmt=fmt)
        except (codec.DataCodecError, SM4Error) as exc:
            raise BaseDataImportError(str(exc)) from exc
        if fmt == "json":
            if isinstance(parsed, Mapping):
                if parsed.get("schema") == "airport_master_v1":
                    if dataset != "airports":
                        raise BaseDataImportError("airport_master_v1 requires dataset airports")
                    # Preserve the existing master-document profile-preserving path.
                    try:
                        airports = parse_airport_master_document(parsed)
                    except (AirportMasterParseError, AirportValidationError) as exc:
                        raise BaseDataImportError(str(exc)) from exc
                    if not airports:
                        raise BaseDataImportError("file contains no records; replacement refused")
                    return parsed
                if set(parsed) - {"dataset", "items", "exported_at"}:
                    raise BaseDataImportError("JSON document contains unknown fields")
                if "dataset" in parsed and parsed["dataset"] != dataset:
                    raise BaseDataImportError("file dataset does not match selected dataset")
                parsed = parsed.get("items")
            if not isinstance(parsed, list):
                raise BaseDataImportError("JSON must be an array, items document or airport_master_v1")
            if dataset == "airports":
                for index, item in enumerate(parsed):
                    if isinstance(item, Mapping) and "airport" in item and set(item) - {"airport", "operational_profile"}:
                        raise BaseDataImportError(f"items[{index}] airport bundle contains unknown fields")
        else:
            for index, row in enumerate(parsed):
                try:
                    validate_columns(dataset, row)
                except BaseDataImportError as exc:
                    raise BaseDataImportError(f"record {index + 1}: {exc}") from exc
            parsed = [self._csv_row(dataset, row, index=i) for i, row in enumerate(parsed)]
        if not parsed:
            raise BaseDataImportError("file contains no records; replacement refused")
        if len(parsed) > codec.MAX_ROWS:
            raise BaseDataImportError("file exceeds 100000 records")
        return parsed

    def replace_multi_format(self, dataset: str, data: bytes, *, format: str,
                             encrypted: bool = False, cipher: Optional[SM4FileCipher] = None) -> BaseDataReplaceResult:
        dataset = self._dataset(dataset)
        parsed = self.parse_multi_format(dataset, data, format=format, encrypted=encrypted, cipher=cipher)
        if isinstance(parsed, Mapping) and parsed.get("schema") == "airport_master_v1":
            return self.replace_airport_master_document(parsed)
        return self._replace(dataset, parsed, source_format=codec.normalize_format(format))

    @staticmethod
    def _dataset(value: Any) -> str:
        if not isinstance(value, str) or value.strip() not in SUPPORTED_DATASETS:
            raise BaseDataImportError(f"dataset must be one of: {', '.join(sorted(SUPPORTED_DATASETS))}")
        return value.strip()

    def _replace(self, dataset: str, raw_items: Sequence[Any], *, source_format: str) -> BaseDataReplaceResult:
        if dataset == "airports":
            bundles = []
            for i, raw in enumerate(raw_items):
                if not isinstance(raw, Mapping):
                    raise BaseDataImportError(f"items[{i}] must be an object")
                airport_raw = raw.get("airport") if "airport" in raw else raw
                if not isinstance(airport_raw, Mapping):
                    raise BaseDataImportError(f"items[{i}].airport must be an object")
                airport = AirportBase.from_mapping(airport_raw)
                profile_raw = raw.get("operational_profile") if "airport" in raw else None
                profile = None
                if profile_raw is not None:
                    if not isinstance(profile_raw, Mapping):
                        raise BaseDataImportError(f"items[{i}].operational_profile must be an object or null")
                    profile = AirportOperationalProfile.from_mapping(
                        profile_raw, derive_configuration=True
                    )
                    if profile.airport_id != airport.airport_id:
                        raise BaseDataImportError(f"items[{i}] profile airport_id does not match airport")
                bundles.append((airport, profile))
            summary = self.airports.replace_airport_bundles_current(bundles)
        elif dataset == "missions":
            items = [Mission.from_mapping(self._mapping(x, i)) for i, x in enumerate(raw_items)]
            summary = self.missions.replace_current(items)
        elif dataset == "aircraft_types":
            items = [AircraftType.from_mapping(self._mapping(x, i)) for i, x in enumerate(raw_items)]
            summary = self.airports.replace_aircraft_types_current(items)
        elif dataset == "resource_types":
            items = [ResourceType.from_mapping(self._mapping(x, i)) for i, x in enumerate(raw_items)]
            summary = self.airports.replace_resource_types_current(items)
        elif dataset == "aircraft_resource_requirements":
            items = [AircraftResourceRequirement.from_mapping(self._mapping(x, i)) for i, x in enumerate(raw_items)]
            summary = self.airports.replace_aircraft_resource_requirements_current(items)
        else:  # pragma: no cover - guarded by _dataset
            raise AssertionError(dataset)
        return BaseDataReplaceResult(dataset=dataset, source_format=source_format, **summary)

    @staticmethod
    def _mapping(value: Any, index: int) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise BaseDataImportError(f"items[{index}] must be an object")
        return value

    def _csv_row(self, dataset: str, row: Mapping[str, Any], *, index: int) -> Mapping[str, Any]:
        try:
            if dataset == "airports":
                airport = {
                    "airport_id": row.get("airport_id"),
                    "airport_name": row.get("airport_name"),
                    "facility_type": row.get("facility_type"),
                    "role": row.get("role"),
                    "icao_code": _none_if_blank(row.get("icao_code")),
                    "iata_code": _none_if_blank(row.get("iata_code")),
                    "region": _none_if_blank(row.get("region")),
                    "municipality": _none_if_blank(row.get("municipality")),
                    "longitude": _float_or_none(row.get("longitude")),
                    "latitude": _float_or_none(row.get("latitude")),
                    "elevation_m": _float_or_none(row.get("elevation_m")),
                    "scheduled_service": _bool(row.get("scheduled_service")),
                    "parking_stand_count": _int_or_none(row.get("parking_stand_count")),
                    "runways": _json_cell(row.get("runways_json"), field="runways_json", default=None),
                }
                runways = airport["runways"]
                if runways is not None:
                    if not isinstance(runways, list) or any(not isinstance(r, Mapping) for r in runways):
                        raise BaseDataImportError("runways_json must contain an array of runway objects")
                    lengths = [float(r["length_m"]) for r in runways if r.get("length_m") is not None]
                    airport["runway_count"] = len(runways) if _none_if_blank(row.get("runway_count")) is None else _int_or_none(row["runway_count"])
                    airport["max_runway_length_m"] = (max(lengths) if lengths else None) if _none_if_blank(row.get("max_runway_length_m")) is None else _float_or_none(row["max_runway_length_m"])
                else:
                    airport["runway_count"] = _int_or_none(row.get("runway_count"))
                    airport["max_runway_length_m"] = _float_or_none(row.get("max_runway_length_m"))
                profile = _json_cell(row.get("operational_profile_json"), field="operational_profile_json", default=None)
                return {"airport": airport, "operational_profile": profile}
            if dataset == "missions":
                return {
                    "mission_id": row.get("mission_id"),
                    "name": row.get("name"),
                    "longitude": _float_or_none(row.get("longitude")),
                    "latitude": _float_or_none(row.get("latitude")),
                    "window_start_slot": _int_or_none(row.get("window_start_slot")),
                    "window_end_slot": _int_or_none(row.get("window_end_slot")),
                    "aircraft_requirements": _json_cell(
                        row.get("aircraft_requirements_json"), field="aircraft_requirements_json", default=[]
                    ),
                }
            if dataset == "aircraft_types":
                return {
                    "aircraft_type_id": row.get("aircraft_type_id"),
                    "name": row.get("name"),
                    "speed_kmh": _float_or_none(row.get("speed_kmh")),
                    "max_range_km": _float_or_none(row.get("max_range_km")),
                    "reserve_ratio": _float_or_none(row.get("reserve_ratio")),
                    "departure_capacity_occupancy_factor": _float_or_none(row.get("departure_capacity_occupancy_factor")),
                    "arrival_capacity_occupancy_factor": _float_or_none(row.get("arrival_capacity_occupancy_factor")),
                }
            if dataset == "resource_types":
                return {
                    "resource_type_id": row.get("resource_type_id"),
                    "name": row.get("name"),
                    "category": row.get("category"),
                    "unit": row.get("unit"),
                }
            if dataset == "aircraft_resource_requirements":
                return {
                    "aircraft_type_id": row.get("aircraft_type_id"),
                    "resource_type_id": row.get("resource_type_id"),
                    "basis": row.get("basis"),
                    "quantity": _float_or_none(row.get("quantity")),
                }
        except (TypeError, ValueError) as exc:
            raise BaseDataImportError(f"record {index + 1} (table row {index + 2}): {exc}") from exc
        raise AssertionError(dataset)


__all__ = [
    "BaseDataImportService",
    "BaseDataImportError",
    "BaseDataReplaceResult",
    "SUPPORTED_DATASETS",
]
