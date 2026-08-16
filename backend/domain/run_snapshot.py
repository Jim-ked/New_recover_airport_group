from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, NoReturn, Sequence, Tuple

from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.domain.run_config import RunConfig
from backend.domain.situation import Situation

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
SNAPSHOT_SCHEMA = "run_input_snapshot_v4"


class RunSnapshotValidationError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise RunSnapshotValidationError(message, field=field)


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _ID_RE.fullmatch(value):
        _fail(field, f"{field} must be a nonblank stable identifier")
    return value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        _fail("snapshot", f"snapshot must be finite JSON: {exc}")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ODDistance:
    """Derived/cached airport→mission distance for one frozen run input."""

    airport_id: str
    mission_id: str
    distance_km: float

    def __post_init__(self) -> None:
        _id(self.airport_id, "airport_id")
        _id(self.mission_id, "mission_id")
        if isinstance(self.distance_km, bool) or not isinstance(self.distance_km, (int, float)):
            _fail("distance_km", "distance_km must be a finite nonnegative number")
        val = float(self.distance_km)
        if not math.isfinite(val) or val < 0:
            _fail("distance_km", "distance_km must be a finite nonnegative number")
        object.__setattr__(self, "distance_km", val)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "airport_id": self.airport_id,
            "mission_id": self.mission_id,
            "distance_km": self.distance_km,
        }


@dataclass(frozen=True)
class RunSnapshot:
    """
    Immutable, self-contained input closure for one run.

    The canonical representation is `payload_json`, not live domain objects. This is
    intentional: after creation, workers/algorithms must read this snapshot only and must
    not re-read mutable Airport/Situation/catalog records.

    `run_config` is a canonical RunConfig with all preset/custom objective weights already
    resolved. Workers must not re-read mutable runtime configuration.
    """

    run_id: str
    situation_id: str
    content_hash: str
    payload_json: str

    def __post_init__(self) -> None:
        _id(self.run_id, "run_id")
        _id(self.situation_id, "situation_id")
        if not isinstance(self.payload_json, str) or not self.payload_json:
            _fail("payload_json", "payload_json must be a nonblank canonical JSON string")
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError as exc:
            _fail("payload_json", f"invalid snapshot JSON: {exc}")
        if not isinstance(payload, dict):
            _fail("payload_json", "snapshot payload must be an object")
        if payload.get("schema") != SNAPSHOT_SCHEMA:
            _fail("payload_json.schema", f"snapshot schema must be {SNAPSHOT_SCHEMA}")
        if payload.get("run_id") != self.run_id:
            _fail("payload_json.run_id", "payload run_id must match snapshot run_id")
        situation = payload.get("situation") or {}
        if situation.get("situation_id") != self.situation_id:
            _fail("payload_json.situation.situation_id", "payload situation_id must match snapshot")
        expected = _sha256(self.payload_json)
        if self.content_hash != expected:
            _fail("content_hash", "content_hash does not match payload_json")

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        situation: Situation,
        aircraft_types: Sequence[AircraftType],
        resource_types: Sequence[ResourceType],
        aircraft_resource_requirements: Sequence[AircraftResourceRequirement],
        od_distances: Sequence[ODDistance],
        run_config: RunConfig,
    ) -> "RunSnapshot":
        run_id = _id(run_id, "run_id")
        if not isinstance(situation, Situation):
            _fail("situation", "situation must be Situation")

        # A run may only freeze operationally complete Situation airport state.
        for item in situation.airports:
            if not item.operational_profile.configuration_complete:
                _fail(
                    f"situation.airports[{item.airport_id}].operational_profile",
                    "all Situation airports must have complete operational configuration before Run snapshot",
                )

        ac_by_id: Dict[str, AircraftType] = {}
        for item in aircraft_types:
            if item.aircraft_type_id in ac_by_id:
                _fail("aircraft_types", f"duplicate aircraft_type_id: {item.aircraft_type_id}")
            ac_by_id[item.aircraft_type_id] = item

        res_by_id: Dict[str, ResourceType] = {}
        for item in resource_types:
            if item.resource_type_id in res_by_id:
                _fail("resource_types", f"duplicate resource_type_id: {item.resource_type_id}")
            res_by_id[item.resource_type_id] = item

        # Relevant aircraft types are those referenced by Situation airports or missions.
        used_aircraft = set()
        used_resources = set()
        for ap in situation.airports:
            used_aircraft.update(row.aircraft_type_id for row in ap.operational_profile.aircraft_support)
            used_resources.update(row.resource_type_id for row in ap.operational_profile.resource_stocks)
        for mission in situation.missions:
            used_aircraft.update(row.aircraft_type_id for row in mission.aircraft_requirements)

        missing_aircraft = sorted(used_aircraft - set(ac_by_id))
        if missing_aircraft:
            _fail("aircraft_types", f"missing aircraft catalog rows: {missing_aircraft}")

        req_rows: Dict[Tuple[str, str, str], AircraftResourceRequirement] = {}
        for row in aircraft_resource_requirements:
            key = (row.aircraft_type_id, row.resource_type_id, row.basis)
            if key in req_rows:
                _fail("aircraft_resource_requirements", f"duplicate requirement relation: {key}")
            req_rows[key] = row
            if row.aircraft_type_id in used_aircraft:
                used_resources.add(row.resource_type_id)

        missing_resources = sorted(used_resources - set(res_by_id))
        if missing_resources:
            _fail("resource_types", f"missing resource catalog rows: {missing_resources}")

        relevant_ac = [ac_by_id[k] for k in sorted(used_aircraft)]
        relevant_res = [res_by_id[k] for k in sorted(used_resources)]
        relevant_req = [
            row for _, row in sorted(req_rows.items())
            if row.aircraft_type_id in used_aircraft and row.resource_type_id in used_resources
        ]

        # Distance cache is a complete airport × mission cross-product, keyed by IDs.
        expected_pairs = {
            (ap.airport_id, mission.mission_id)
            for ap in situation.airports
            for mission in situation.missions
        }
        actual: Dict[Tuple[str, str], ODDistance] = {}
        for row in od_distances:
            key = (row.airport_id, row.mission_id)
            if key in actual:
                _fail("od_distances", f"duplicate OD pair: {key}")
            actual[key] = row
        actual_pairs = set(actual)
        missing_pairs = sorted(expected_pairs - actual_pairs)
        extra_pairs = sorted(actual_pairs - expected_pairs)
        if missing_pairs:
            _fail("od_distances", f"missing airport-mission OD pairs: {missing_pairs[:5]}")
        if extra_pairs:
            _fail("od_distances", f"OD pairs outside Situation: {extra_pairs[:5]}")

        if not isinstance(run_config, RunConfig):
            _fail("run_config", "run_config must be canonical RunConfig")
        run_config.validate_against(
            airport_ids=[a.airport_id for a in situation.airports],
            damage_scenario_ids=[s.damage_scenario_id for s in situation.damage_scenarios],
            aircraft_type_ids=list(ac_by_id),
        )

        # Every aircraft type referenced by this Run must have the operational fields
        # needed by path/capacity calculations. Missing is invalid, never silently zero.
        for item in relevant_ac:
            required = {
                "speed_kmh": item.speed_kmh,
                "max_range_km": item.max_range_km,
                "reserve_ratio": item.reserve_ratio,
                "departure_capacity_occupancy_factor": item.departure_capacity_occupancy_factor,
                "arrival_capacity_occupancy_factor": item.arrival_capacity_occupancy_factor,
            }
            for field, value in required.items():
                if value is None:
                    _fail(f"aircraft_types.{item.aircraft_type_id}.{field}", f"{field} is required for a Run")

        config_copy = run_config.to_dict()
        situation_dict = situation.canonical_dict()

        payload = {
            "schema": SNAPSHOT_SCHEMA,
            "run_id": run_id,
            "situation": situation_dict,
            "situation_content_hash": situation.content_hash(),
            "catalogs": {
                "aircraft_types": [x.to_dict() for x in relevant_ac],
                "resource_types": [x.to_dict() for x in relevant_res],
                "aircraft_resource_requirements": [x.to_dict() for x in relevant_req],
            },
            "od_distances": [actual[k].to_dict() for k in sorted(actual)],
            "run_config": config_copy,
        }
        payload_json = _canonical_json(payload)
        return cls(
            run_id=run_id,
            situation_id=situation.situation_id,
            content_hash=_sha256(payload_json),
            payload_json=payload_json,
        )

    def clone_for_run(self, new_run_id: str) -> "RunSnapshot":
        """Clone this exact immutable input closure for a new Run identity.

        Retry must never re-read the mutable Situation or catalogs. The only payload field
        changed here is ``run_id``; every business input remains byte-for-byte equivalent
        after canonical JSON normalization.
        """
        new_run_id = _id(new_run_id, "run_id")
        payload = self.to_dict()
        payload["run_id"] = new_run_id
        payload_json = _canonical_json(payload)
        return RunSnapshot(
            run_id=new_run_id,
            situation_id=self.situation_id,
            content_hash=_sha256(payload_json),
            payload_json=payload_json,
        )

    def to_dict(self) -> Dict[str, Any]:
        # New object every call: callers cannot mutate the frozen internal representation.
        return json.loads(self.payload_json)
