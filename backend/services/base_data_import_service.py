from __future__ import annotations

import csv
import io
import json
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


class BaseDataImportError(ValueError):
    pass


SUPPORTED_DATASETS = frozenset({
    "airports",
    "missions",
    "aircraft_types",
    "resource_types",
    "aircraft_resource_requirements",
})


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
    return None if value is None else float(value)


def _int_or_none(value: Any) -> Optional[int]:
    value = _none_if_blank(value)
    return None if value is None else int(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
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
        """Replace the current airport dataset from a full airport_master_v1 document.

        Shares ``parse_airport_master_document`` with the seed bootstrap path.  The
        master document only carries airport base facts, so operational profiles of
        the replaced dataset are reset (same replace-current-state semantics as the
        generic JSON import).
        """
        try:
            airports = parse_airport_master_document(raw)
        except (AirportMasterParseError, AirportValidationError) as exc:
            raise BaseDataImportError(f"airport master document is invalid: {exc}") from exc
        bundles = [(airport, None) for airport in airports]
        summary = self.airports.replace_airport_bundles_current(bundles)
        return BaseDataReplaceResult(
            dataset="airports", source_format="airport_master_v1", **summary
        )

    def replace_csv(self, dataset: str, text: str) -> BaseDataReplaceResult:
        dataset = self._dataset(dataset)
        if not isinstance(text, str) or not text.strip():
            raise BaseDataImportError("CSV content is empty")
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
        except csv.Error as exc:
            raise BaseDataImportError(f"invalid CSV: {exc}") from exc
        if not rows:
            # An explicit header-only CSV is a valid replacement with an empty current
            # dataset.  Missing headers are not.
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames:
                raise BaseDataImportError("CSV header is required")
        items = [self._csv_row(dataset, row, index=i) for i, row in enumerate(rows)]
        return self._replace(dataset, items, source_format="csv")

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
                    profile = AirportOperationalProfile.from_mapping(profile_raw)
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
                    "longitude": float(row.get("longitude")),
                    "latitude": float(row.get("latitude")),
                    "elevation_m": _float_or_none(row.get("elevation_m")),
                    "scheduled_service": _bool(row.get("scheduled_service")),
                    "runways": _json_cell(row.get("runways_json"), field="runways_json", default=None),
                }
                profile = _json_cell(row.get("operational_profile_json"), field="operational_profile_json", default=None)
                return {"airport": airport, "operational_profile": profile}
            if dataset == "missions":
                return {
                    "mission_id": row.get("mission_id"),
                    "name": row.get("name"),
                    "longitude": float(row.get("longitude")),
                    "latitude": float(row.get("latitude")),
                    "window_start_slot": int(row.get("window_start_slot")),
                    "window_end_slot": int(row.get("window_end_slot")),
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
                    "quantity": float(row.get("quantity")),
                }
        except (TypeError, ValueError) as exc:
            raise BaseDataImportError(f"CSV row {index + 2} contains an invalid scalar value") from exc
        raise AssertionError(dataset)


__all__ = [
    "BaseDataImportService",
    "BaseDataImportError",
    "BaseDataReplaceResult",
    "SUPPORTED_DATASETS",
]
