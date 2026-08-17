from __future__ import annotations

from typing import Any, Mapping

from backend.auth.principal import Principal
from backend.domain.situation import Situation
from backend.domain.mission import Mission
from backend.services.situation_service import copy_airport_into_situation, copy_mission_into_situation
from backend.storage.airport_repository import AirportRepository
from backend.storage.mission_repository import MissionRepository
from backend.storage.situation_repository import SituationRepository
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


class SituationApi:
    """Canonical whole-aggregate Situation API.

    The public mutation boundary deliberately stays at the whole Working Copy. Airports,
    missions and damage events are not given independent mutation endpoints, so the UI
    can keep one dirty/save/conflict state and the repository can validate the aggregate.
    """

    def __init__(
        self,
        *,
        situation_repository: SituationRepository,
        airport_repository: AirportRepository | None = None,
        mission_repository: MissionRepository | None = None,
    ) -> None:
        self.situations = situation_repository
        self.airports = airport_repository
        self.missions = mission_repository

    @staticmethod
    def _handle(call) -> ApiResponse:
        try:
            return call()
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return mapped
            raise

    @staticmethod
    def _parse_situation(raw: Any, *, allow_hash: bool) -> tuple[Situation, str | None]:
        body = require_object(raw)
        allowed = {"situation", "expected_content_hash"} if allow_hash else {"situation"}
        reject_unknown(body, allowed)
        raw_situation = body.get("situation")
        if not isinstance(raw_situation, Mapping):
            raise ApiInputError("situation must be a JSON object", field="situation")
        expected = None
        if allow_hash:
            expected = required_nonblank_string(body, "expected_content_hash")
        return Situation.from_mapping(raw_situation), expected

    def list(
        self,
        *,
        principal: Principal,
        query: Any = None,
        limit: Any = None,
        offset: Any = None,
    ) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("situations.read")
            parsed_limit = parse_positive_int(limit, field="limit", default=200, maximum=500)
            parsed_offset = parse_nonnegative_int(offset, field="offset", default=0, maximum=2_147_483_647)
            if query is not None and (not isinstance(query, str) or not query.strip()):
                raise ApiInputError("q must be a nonblank string", field="q")
            rows, total = self.situations.search_visible(
                actor_user_id=principal.user_id,
                is_admin=principal.is_admin,
                query=None if query is None else query.strip(),
                limit=parsed_limit,
                offset=parsed_offset,
            )
            return ApiResponse({"items": rows, "total": total, "limit": parsed_limit, "offset": parsed_offset}, 200)

        return self._handle(action)

    def detail(self, situation_id: str, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("situations.read")
            metadata = self.situations.get_metadata(situation_id)
            if metadata is None:
                return ApiResponse(error_body("SITUATION_NOT_FOUND", f"situation not found: {situation_id}"), 404)
            situation = self.situations.get_situation_for_actor(
                situation_id,
                actor_user_id=principal.user_id,
                is_admin=principal.is_admin,
            )
            if situation is None:
                return ApiResponse(error_body("SITUATION_NOT_FOUND", f"situation not found: {situation_id}"), 404)
            return ApiResponse(
                {
                    "situation": situation.to_dict(),
                    "content_hash": metadata.get("content_hash"),
                    "owner_user_id": metadata.get("owner_user_id"),
                    "created_at": metadata.get("created_at"),
                    "updated_at": metadata.get("updated_at"),
                    "historical_run_count": int(metadata.get("historical_run_count") or 0),
                    "active_run_count": int(metadata.get("active_run_count") or 0),
                },
                200,
            )

        return self._handle(action)

    def create(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("situations.write")
            situation, _ = self._parse_situation(raw_body, allow_hash=False)
            content_hash = self.situations.create_situation(situation, owner_user_id=principal.user_id)
            metadata = self.situations.get_metadata(situation.situation_id) or {}
            return ApiResponse(
                {
                    "situation": situation.to_dict(),
                    "content_hash": content_hash,
                    "owner_user_id": metadata.get("owner_user_id"),
                    "created_at": metadata.get("created_at"),
                    "updated_at": metadata.get("updated_at"),
                },
                201,
            )
        return self._handle(action)

    def update(self, situation_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("situations.write")
            situation, expected = self._parse_situation(raw_body, allow_hash=True)
            if situation.situation_id != situation_id:
                raise ApiInputError("situation_id must match URL", field="situation.situation_id")
            assert expected is not None
            content_hash = self.situations.update_situation_for_actor(
                situation,
                actor_user_id=principal.user_id,
                is_admin=principal.is_admin,
                expected_content_hash=expected,
            )
            metadata = self.situations.get_metadata(situation_id) or {}
            return ApiResponse(
                {
                    "situation": situation.to_dict(),
                    "content_hash": content_hash,
                    "owner_user_id": metadata.get("owner_user_id"),
                    "created_at": metadata.get("created_at"),
                    "updated_at": metadata.get("updated_at"),
                },
                200,
            )
        return self._handle(action)

    def canonicalize_working_copy(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        """Validate and normalize one unsaved Situation Working Copy without persistence.

        This is an interaction boundary, not a separate user-facing validation workflow:
        editor "Apply to Situation" actions use the same domain parser as final save so
        the browser never needs to duplicate Situation business validation.
        """
        def action() -> ApiResponse:
            principal.require_permission("situations.write")
            body = require_object(raw_body)
            reject_unknown(body, {"situation"})
            raw_situation = body.get("situation")
            if not isinstance(raw_situation, Mapping):
                raise ApiInputError("situation must be a JSON object", field="situation")
            situation = Situation.from_mapping(raw_situation)
            return ApiResponse({
                "situation": situation.to_dict(),
                "working_copy_hash": situation.content_hash(),
                "persisted": False,
                "operation": "canonicalize",
            }, 200)
        return self._handle(action)

    def copy_airport_to_working_copy(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        """Apply current Base Data airport/profile values to an unsaved Working Copy.

        The operation is intentionally non-persistent.  It supports both Add Airport and
        Restore Base without forcing the frontend to know which nested fields constitute
        the canonical base/profile copy boundary.
        """
        def action() -> ApiResponse:
            principal.require_permission("situations.write")
            if self.airports is None:
                raise RuntimeError("airport repository is not configured")
            body = require_object(raw_body)
            reject_unknown(body, {"situation", "airport_id"})
            raw_situation = body.get("situation")
            if not isinstance(raw_situation, Mapping):
                raise ApiInputError("situation must be a JSON object", field="situation")
            airport_id = required_nonblank_string(body, "airport_id")
            try:
                airport = self.airports.get_airport(airport_id)
            except KeyError:
                return ApiResponse(error_body("CATALOG_NOT_FOUND", f"airport base not found: {airport_id}"), 404)
            try:
                profile = self.airports.get_operational_profile(airport_id)
            except KeyError:
                profile = None
            updated = copy_airport_into_situation(
                Situation.from_mapping(raw_situation), airport, profile
            )
            return ApiResponse({
                "situation": updated.to_dict(),
                "working_copy_hash": updated.content_hash(),
                "persisted": False,
                "operation": "copy_airport_base",
                "airport_id": airport_id,
            }, 200)
        return self._handle(action)

    def copy_mission_to_working_copy(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        """Copy a reusable or historical mission value into an unsaved Working Copy."""
        def action() -> ApiResponse:
            principal.require_permission("situations.write")
            body = require_object(raw_body)
            reject_unknown(body, {"situation", "mission_id", "mission"})
            raw_situation = body.get("situation")
            if not isinstance(raw_situation, Mapping):
                raise ApiInputError("situation must be a JSON object", field="situation")
            has_id = body.get("mission_id") is not None
            has_value = body.get("mission") is not None
            if has_id == has_value:
                raise ApiInputError("provide exactly one of mission_id or mission", field="mission_id")
            if has_id:
                if self.missions is None:
                    raise RuntimeError("mission repository is not configured")
                mission_id = required_nonblank_string(body, "mission_id")
                try:
                    mission = self.missions.get(mission_id)
                except KeyError:
                    return ApiResponse(error_body("CATALOG_NOT_FOUND", f"mission not found: {mission_id}"), 404)
                if mission is None:
                    return ApiResponse(error_body("CATALOG_NOT_FOUND", f"mission not found: {mission_id}"), 404)
            else:
                raw_mission = body.get("mission")
                if not isinstance(raw_mission, Mapping):
                    raise ApiInputError("mission must be a JSON object", field="mission")
                mission = Mission.from_mapping(raw_mission)
                mission_id = mission.mission_id
            updated = copy_mission_into_situation(Situation.from_mapping(raw_situation), mission)
            return ApiResponse({
                "situation": updated.to_dict(),
                "working_copy_hash": updated.content_hash(),
                "persisted": False,
                "operation": "copy_mission",
                "mission_id": mission_id,
            }, 200)
        return self._handle(action)


    def delete(self, situation_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("situations.write")
            body = require_object(raw_body)
            reject_unknown(body, {"expected_content_hash"})
            expected = required_nonblank_string(body, "expected_content_hash")
            result = self.situations.delete_situation_for_actor(
                situation_id,
                actor_user_id=principal.user_id,
                is_admin=principal.is_admin,
                expected_content_hash=expected,
            )
            return ApiResponse(result, 200)
        return self._handle(action)


__all__ = ["SituationApi"]
