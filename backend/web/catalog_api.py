from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence

from backend.auth.principal import Principal
from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.domain.mission import Mission
from backend.storage.airport_repository import AirportRepository
from backend.storage.mission_repository import MissionRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.services.base_data_import_service import BaseDataImportService, BaseDataImportError
from backend.web.error_mapping import map_expected_error
from backend.web.http import (
    ApiInputError,
    ApiResponse,
    error_body,
    parse_nonnegative_int,
    parse_positive_int,
    reject_unknown,
    require_object,
    required_nonblank_string,
)


class CatalogApi:
    """Frontend-facing Base Data/catalog contract.

    Airport static facts and the reusable operational profile remain distinct domain
    objects, but one API bundle returns them together so Situation Add/restore never has
    to reconstruct hidden baseline state from unrelated requests.
    """

    def __init__(
        self,
        *,
        airport_repository: AirportRepository,
        mission_repository: MissionRepository,
        run_repository: Optional[RunRepository] = None,
        snapshot_repository: Optional[RunSnapshotRepository] = None,
    ) -> None:
        self.airports = airport_repository
        self.missions = mission_repository
        self.runs = run_repository
        self.snapshots = snapshot_repository
        self.import_service = BaseDataImportService(
            airport_repository=airport_repository, mission_repository=mission_repository
        )

    @staticmethod
    def _handle(call) -> ApiResponse:
        try:
            return call()
        except KeyError as exc:
            return ApiResponse(error_body("CATALOG_NOT_FOUND", str(exc).strip("'")), 404)
        except BaseDataImportError as exc:
            return ApiResponse(error_body("BASE_DATA_IMPORT_INVALID", str(exc)), 400)
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return mapped
            raise

    @staticmethod
    def _revision(body: Mapping[str, Any]) -> int:
        raw = body.get("expected_revision")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise ApiInputError("expected_revision must be a positive integer", field="expected_revision")
        return raw

    def list_airports(
        self,
        *,
        principal: Principal,
        query: Any = None,
        roles: Optional[Sequence[str]] = None,
        regions: Optional[Sequence[str]] = None,
        limit: Any = None,
        offset: Any = None,
    ) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.read")
            q = None if query is None else str(query)
            parsed_limit = parse_positive_int(limit, field="limit", default=50, maximum=500)
            parsed_offset = parse_nonnegative_int(offset, field="offset", default=0, maximum=2_147_483_647)
            items, total = self.airports.list_airport_bundles(
                query=q, roles=roles, regions=regions, limit=parsed_limit, offset=parsed_offset
            )
            return ApiResponse({"items": items, "total": total, "limit": parsed_limit, "offset": parsed_offset}, 200)
        return self._handle(action)

    def airport_detail(self, airport_id: str, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.read")
            return ApiResponse(self.airports.get_airport_bundle(airport_id), 200)
        return self._handle(action)

    @staticmethod
    def _airport_bundle(raw: Any, *, updating: bool) -> tuple[AirportBase, Optional[AirportOperationalProfile], Optional[int]]:
        body = require_object(raw)
        allowed = {"airport", "operational_profile", "expected_revision"} if updating else {"airport", "operational_profile"}
        reject_unknown(body, allowed)
        raw_airport = body.get("airport")
        if not isinstance(raw_airport, Mapping):
            raise ApiInputError("airport must be a JSON object", field="airport")
        airport = AirportBase.from_mapping(raw_airport)
        raw_profile = body.get("operational_profile")
        profile = None
        if raw_profile is not None:
            if not isinstance(raw_profile, Mapping):
                raise ApiInputError("operational_profile must be object or null", field="operational_profile")
            profile = AirportOperationalProfile.from_mapping(raw_profile)
            if profile.airport_id != airport.airport_id:
                raise ApiInputError("operational_profile.airport_id must match airport.airport_id", field="operational_profile.airport_id")
        revision = CatalogApi._revision(body) if updating else None
        return airport, profile, revision

    def create_airport(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            airport, profile, _ = self._airport_bundle(raw_body, updating=False)
            return ApiResponse(self.airports.save_airport_bundle(
                airport=airport, operational_profile=profile, create_only=True
            ), 201)
        return self._handle(action)

    def update_airport(self, airport_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            airport, profile, revision = self._airport_bundle(raw_body, updating=True)
            if airport.airport_id != airport_id:
                raise ApiInputError("airport_id must match URL", field="airport.airport_id")
            return ApiResponse(self.airports.save_airport_bundle(
                airport=airport, operational_profile=profile, expected_revision=revision
            ), 200)
        return self._handle(action)

    def delete_airport(self, airport_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body)
            reject_unknown(body, {"expected_revision"})
            revision = self._revision(body)
            self.airports.delete_airport(airport_id, expected_revision=revision)
            return ApiResponse({"airport_id": airport_id, "deleted": True}, 200)
        return self._handle(action)

    def list_missions(self, *, principal: Principal, query: Any = None, limit: Any = None, offset: Any = None) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.read")
            parsed_limit = parse_positive_int(limit, field="limit", default=50, maximum=500)
            parsed_offset = parse_nonnegative_int(offset, field="offset", default=0, maximum=2_147_483_647)
            items, total = self.missions.list_bundles(
                query=None if query is None else str(query), limit=parsed_limit, offset=parsed_offset
            )
            return ApiResponse({"items": items, "total": total, "limit": parsed_limit, "offset": parsed_offset}, 200)
        return self._handle(action)

    def mission_detail(self, mission_id: str, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.read")
            return ApiResponse(self.missions.get_bundle(mission_id), 200)
        return self._handle(action)

    def create_mission(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body)
            reject_unknown(body, {"mission"})
            raw = body.get("mission")
            if not isinstance(raw, Mapping):
                raise ApiInputError("mission must be a JSON object", field="mission")
            return ApiResponse(self.missions.save_versioned(Mission.from_mapping(raw), create_only=True), 201)
        return self._handle(action)

    def update_mission(self, mission_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body)
            reject_unknown(body, {"mission", "expected_revision"})
            raw = body.get("mission")
            if not isinstance(raw, Mapping):
                raise ApiInputError("mission must be a JSON object", field="mission")
            mission = Mission.from_mapping(raw)
            if mission.mission_id != mission_id:
                raise ApiInputError("mission_id must match URL", field="mission.mission_id")
            return ApiResponse(self.missions.save_versioned(
                mission, expected_revision=self._revision(body)
            ), 200)
        return self._handle(action)

    def delete_mission(self, mission_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body)
            reject_unknown(body, {"expected_revision"})
            self.missions.delete_versioned(mission_id, expected_revision=self._revision(body))
            return ApiResponse({"mission_id": mission_id, "deleted": True}, 200)
        return self._handle(action)

    def list_aircraft_types(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.read")
            return ApiResponse({"items": self.airports.list_aircraft_types_with_metadata()}, 200)
        return self._handle(action)

    def create_aircraft_type(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body)
            reject_unknown(body, {"aircraft_type"})
            raw = body.get("aircraft_type")
            if not isinstance(raw, Mapping):
                raise ApiInputError("aircraft_type must be a JSON object", field="aircraft_type")
            return ApiResponse(self.airports.create_aircraft_type_versioned(AircraftType.from_mapping(raw)), 201)
        return self._handle(action)

    def update_aircraft_type(self, aircraft_type_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body)
            reject_unknown(body, {"aircraft_type", "expected_revision"})
            raw = body.get("aircraft_type")
            if not isinstance(raw, Mapping):
                raise ApiInputError("aircraft_type must be a JSON object", field="aircraft_type")
            item = AircraftType.from_mapping(raw)
            if item.aircraft_type_id != aircraft_type_id:
                raise ApiInputError("aircraft_type_id must match URL", field="aircraft_type.aircraft_type_id")
            return ApiResponse(self.airports.save_aircraft_type_versioned(
                item, expected_revision=self._revision(body)
            ), 200)
        return self._handle(action)

    def delete_aircraft_type(self, aircraft_type_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body); reject_unknown(body, {"expected_revision"})
            self.airports.delete_aircraft_type_versioned(aircraft_type_id, expected_revision=self._revision(body))
            return ApiResponse({"aircraft_type_id": aircraft_type_id, "deleted": True}, 200)
        return self._handle(action)

    def replace_aircraft_resource_requirements(self, aircraft_type_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body)
            reject_unknown(body, {"requirements", "expected_revision"})
            raw_rows = body.get("requirements")
            if not isinstance(raw_rows, list):
                raise ApiInputError("requirements must be an array", field="requirements")
            rows = []
            for i, raw in enumerate(raw_rows):
                if not isinstance(raw, Mapping):
                    raise ApiInputError("requirement row must be object", field=f"requirements[{i}]")
                row = AircraftResourceRequirement.from_mapping(raw)
                if row.aircraft_type_id != aircraft_type_id:
                    raise ApiInputError("aircraft_type_id must match URL", field=f"requirements[{i}].aircraft_type_id")
                rows.append(row)
            return ApiResponse(self.airports.replace_aircraft_resource_requirements(
                aircraft_type_id, rows, expected_aircraft_revision=self._revision(body)
            ), 200)
        return self._handle(action)

    def list_resource_types(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.read")
            return ApiResponse({"items": self.airports.list_resource_types_with_metadata()}, 200)
        return self._handle(action)

    def create_resource_type(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body); reject_unknown(body, {"resource_type"})
            raw = body.get("resource_type")
            if not isinstance(raw, Mapping):
                raise ApiInputError("resource_type must be object", field="resource_type")
            return ApiResponse(self.airports.create_resource_type_versioned(ResourceType.from_mapping(raw)), 201)
        return self._handle(action)

    def update_resource_type(self, resource_type_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body); reject_unknown(body, {"resource_type", "expected_revision"})
            raw = body.get("resource_type")
            if not isinstance(raw, Mapping):
                raise ApiInputError("resource_type must be object", field="resource_type")
            item = ResourceType.from_mapping(raw)
            if item.resource_type_id != resource_type_id:
                raise ApiInputError("resource_type_id must match URL", field="resource_type.resource_type_id")
            return ApiResponse(self.airports.save_resource_type_versioned(item, expected_revision=self._revision(body)), 200)
        return self._handle(action)

    def delete_resource_type(self, resource_type_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body); reject_unknown(body, {"expected_revision"})
            self.airports.delete_resource_type_versioned(resource_type_id, expected_revision=self._revision(body))
            return ApiResponse({"resource_type_id": resource_type_id, "deleted": True}, 200)
        return self._handle(action)

    def list_aircraft_resource_requirements(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.read")
            return ApiResponse({"items": [x.to_dict() for x in self.airports.list_aircraft_resource_requirements()]}, 200)
        return self._handle(action)

    def replace_base_data_json(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            body = require_object(raw_body)
            reject_unknown(body, {"dataset", "items"})
            dataset = required_nonblank_string(body, "dataset")
            result = self.import_service.replace_json(dataset, body.get("items"))
            return ApiResponse(result.to_dict(), 200)
        return self._handle(action)

    def replace_base_data_csv(self, dataset: Any, text: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.write")
            if not isinstance(dataset, str) or not dataset.strip():
                raise ApiInputError("dataset query parameter is required", field="dataset")
            if not isinstance(text, str):
                raise ApiInputError("CSV request body must be text", field="body")
            result = self.import_service.replace_csv(dataset.strip(), text)
            return ApiResponse(result.to_dict(), 200)
        return self._handle(action)

    def mission_history(self, *, principal: Principal, limit: Any = None) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("catalog.read")
            if self.runs is None or self.snapshots is None:
                raise RuntimeError("mission history dependencies are not configured")
            parsed_limit = parse_positive_int(limit, field="limit", default=100, maximum=500)
            runs = self.runs.list_for_owner(
                principal.user_id,
                statuses=("succeeded", "failed", "cancelled"),
                limit=min(parsed_limit, 500),
                offset=0,
            )
            seen: set[tuple[str, str]] = set()
            items = []
            for record in runs:
                snap = self.snapshots.get(record.run_id)
                if snap is None:
                    continue
                payload = snap.to_dict()
                situation = payload.get("situation") or {}
                for raw in situation.get("missions") or []:
                    try:
                        mission = Mission.from_mapping(raw)
                    except Exception:
                        continue
                    key = (mission.mission_id, json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append({
                        "mission": mission.to_dict(),
                        "source_run_id": record.run_id,
                        "source_situation_id": record.situation_id,
                        "source_run_created_at": record.created_at,
                    })
            return ApiResponse({"items": items}, 200)
        return self._handle(action)


__all__ = ["CatalogApi"]
