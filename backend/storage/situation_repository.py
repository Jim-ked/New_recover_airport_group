from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backend.domain.airport import AirportBase, RunwayBase, RunwayEnd
from backend.domain.airport_operations import AirportAircraftSupport, AirportOperationalProfile, AirportResourceStock
from backend.domain.mission import Mission, MissionAircraftRequirement
from backend.domain.damage import DamageEvent, DamageScenario
from backend.domain.situation import ResourceReplenishment, Situation, SituationAirport
from backend.storage.database import initialize_database


class SituationConflictError(ValueError):
    pass


class SituationOwnershipError(ValueError):
    pass


class SituationAccessError(PermissionError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class SituationRepository:
    """Whole-aggregate persistence for mutable Situation working copies."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        initialize_database(self.db_path)

    def allocate_situation_id(self) -> str:
        """Reserve the next monotonic project Situation ID for a new working copy."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT next_value FROM identifier_sequences WHERE namespace='situation'"
            ).fetchone()
            next_value = int(row["next_value"]) if row else 1
            current = max(
                (
                    int(match.group(1))
                    for item in conn.execute("SELECT situation_id FROM situations WHERE situation_id LIKE 'ST%'")
                    if (match := re.fullmatch(r"ST([0-9]+)", item["situation_id"]))
                ),
                default=0,
            )
            next_value = max(next_value, current + 1)
            conn.execute(
                "INSERT INTO identifier_sequences(namespace, next_value) VALUES ('situation', ?) "
                "ON CONFLICT(namespace) DO UPDATE SET next_value=excluded.next_value",
                (next_value + 1,),
            )
        return f"ST{next_value:03d}"

    @staticmethod
    def _advance_situation_sequence(conn: sqlite3.Connection, situation_id: str) -> None:
        match = re.fullmatch(r"ST([0-9]+)", situation_id)
        if match is None:
            return
        conn.execute(
            "INSERT INTO identifier_sequences(namespace, next_value) VALUES ('situation', ?) "
            "ON CONFLICT(namespace) DO UPDATE SET next_value=MAX(identifier_sequences.next_value, excluded.next_value)",
            (int(match.group(1)) + 1,),
        )

    @staticmethod
    def _end_values(end: Optional[RunwayEnd]):
        if end is None:
            return (None, None, None, None, None, None)
        return (
            end.ident,
            end.latitude,
            end.longitude,
            end.elevation_m,
            end.heading_deg_true,
            end.displaced_threshold_m,
        )

    def save_situation(
        self,
        situation: Situation,
        *,
        owner_user_id: Optional[str] = None,
        expected_content_hash: Optional[str] = None,
    ) -> str:
        """Explicit whole-aggregate Save with stable ownership and optimistic hash check.

        New canonical Situation records require an explicit owner. Existing owner values
        are preserved and cannot be silently reassigned by a normal save. Legacy rows
        created before v012 may be explicitly assigned once by supplying owner_user_id.
        """
        if owner_user_id is not None and (not isinstance(owner_user_id, str) or not owner_user_id.strip()):
            raise SituationOwnershipError("owner_user_id must be a nonblank string")
        new_hash = situation.content_hash()
        with self.connect() as conn:
            current = conn.execute(
                "SELECT content_hash, owner_user_id FROM situations WHERE situation_id = ?",
                (situation.situation_id,),
            ).fetchone()
            if expected_content_hash is not None:
                actual = None if current is None else current["content_hash"]
                if actual != expected_content_hash:
                    raise SituationConflictError(
                        f"stale Situation working copy: expected {expected_content_hash}, current {actual}"
                    )

            current_owner = None if current is None else current["owner_user_id"]
            if current_owner is not None and owner_user_id is not None and current_owner != owner_user_id:
                raise SituationOwnershipError("Situation owner cannot be changed by save_situation")
            effective_owner = current_owner or owner_user_id
            if effective_owner is None:
                raise SituationOwnershipError("new Situation records require owner_user_id")

            conn.execute(
                """
                INSERT INTO situations (
                    situation_id, name, description, content_hash, owner_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(situation_id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    content_hash=excluded.content_hash,
                    owner_user_id=COALESCE(situations.owner_user_id, excluded.owner_user_id),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (situation.situation_id, situation.name, situation.description, new_hash, effective_owner),
            )
            self._advance_situation_sequence(conn, situation.situation_id)
            # Replace all children so one call represents the entire saved Working Copy.
            # Damage events reference SituationAirport with RESTRICT: delete them first.
            conn.execute("DELETE FROM situation_damage_events WHERE situation_id = ?", (situation.situation_id,))
            conn.execute("DELETE FROM situation_damage_scenarios WHERE situation_id = ?", (situation.situation_id,))
            conn.execute("DELETE FROM situation_resource_replenishments WHERE situation_id = ?", (situation.situation_id,))
            conn.execute("DELETE FROM situation_airports WHERE situation_id = ?", (situation.situation_id,))
            conn.execute("DELETE FROM situation_missions WHERE situation_id = ?", (situation.situation_id,))

            for item in situation.airports:
                ap = item.airport
                op = item.operational_profile
                conn.execute(
                    """
                    INSERT INTO situation_airports (
                        situation_id, airport_id, airport_name, facility_type, role,
                        icao_code, iata_code, region, municipality,
                        longitude, latitude, elevation_m, scheduled_service, runways_known,
                        configuration_complete, capacity_per_window, support_level
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        situation.situation_id, ap.airport_id, ap.airport_name, ap.facility_type, ap.role,
                        ap.icao_code, ap.iata_code, ap.region, ap.municipality,
                        ap.longitude, ap.latitude, ap.elevation_m, int(ap.scheduled_service),
                        int(ap.runways is not None), int(op.configuration_complete),
                        op.capacity_per_window, op.support_level,
                    ),
                )
                if ap.runways is not None:
                    for rw in ap.runways:
                        conn.execute(
                            """
                            INSERT INTO situation_runways (
                                situation_id, airport_id, runway_id, length_m, width_m, surface, lighted,
                                low_ident, low_latitude, low_longitude, low_elevation_m,
                                low_heading_deg_true, low_displaced_threshold_m,
                                high_ident, high_latitude, high_longitude, high_elevation_m,
                                high_heading_deg_true, high_displaced_threshold_m
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                situation.situation_id, ap.airport_id, rw.runway_id,
                                rw.length_m, rw.width_m, rw.surface,
                                None if rw.lighted is None else int(rw.lighted),
                                *self._end_values(rw.low_end), *self._end_values(rw.high_end),
                            ),
                        )
                for row in op.aircraft_support:
                    conn.execute(
                        """
                        INSERT INTO situation_aircraft_support (
                            situation_id, airport_id, aircraft_type_id, initial_quantity, tau_reset_windows
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            situation.situation_id, ap.airport_id, row.aircraft_type_id,
                            row.initial_quantity, row.tau_reset_windows,
                        ),
                    )
                for row in op.resource_stocks:
                    conn.execute(
                        """
                        INSERT INTO situation_resource_stocks (
                            situation_id, airport_id, resource_type_id, quantity,
                            replenishment_capacity_per_window
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            situation.situation_id,
                            ap.airport_id,
                            row.resource_type_id,
                            row.initial_quantity,
                            row.replenishment_capacity_per_window,
                        ),
                    )
                for row in item.resource_replenishments:
                    conn.execute(
                        """
                        INSERT INTO situation_resource_replenishments (
                            situation_id, airport_id, resource_type_id, slot, quantity
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            situation.situation_id,
                            ap.airport_id,
                            row.resource_type_id,
                            row.slot,
                            row.quantity,
                        ),
                    )

            for scenario in sorted(situation.damage_scenarios, key=lambda x: x.damage_scenario_id):
                conn.execute(
                    """
                    INSERT INTO situation_damage_scenarios (
                        situation_id, damage_scenario_id, name, category
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (situation.situation_id, scenario.damage_scenario_id, scenario.name, scenario.category),
                )
                for event in sorted(scenario.events, key=lambda e: (e.sequence, e.event_id)):
                    conn.execute(
                        """
                        INSERT INTO situation_damage_events (
                            situation_id, damage_scenario_id, event_id, sequence_no,
                            airport_id, target_type, target_id, damage_type, start_slot, end_slot,
                            effect_json, recovery_mode, recovery_duration_slots
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            situation.situation_id, scenario.damage_scenario_id, event.event_id, event.sequence,
                            event.target.airport_id, event.target.target_type, event.target.target_id,
                            event.damage_type, event.start_slot, event.end_slot,
                            json.dumps(event.effect.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                            event.recovery_mode, event.recovery_duration_slots,
                        ),
                    )

            for mission in situation.missions:
                conn.execute(
                    """
                    INSERT INTO situation_missions (
                        situation_id, mission_id, name, longitude, latitude,
                        window_start_slot, window_end_slot
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        situation.situation_id, mission.mission_id, mission.name,
                        mission.longitude, mission.latitude,
                        mission.window_start_slot, mission.window_end_slot,
                    ),
                )
                for row in mission.aircraft_requirements:
                    conn.execute(
                        """
                        INSERT INTO situation_mission_aircraft_requirements (
                            situation_id, mission_id, aircraft_type_id, required_sorties, tau_work_windows
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            situation.situation_id, mission.mission_id, row.aircraft_type_id,
                            row.required_sorties, row.tau_work_windows,
                        ),
                    )


        return new_hash

    @staticmethod
    def _end_from_row(row: sqlite3.Row, prefix: str) -> Optional[RunwayEnd]:
        values = [
            row[f"{prefix}_ident"], row[f"{prefix}_latitude"], row[f"{prefix}_longitude"],
            row[f"{prefix}_elevation_m"], row[f"{prefix}_heading_deg_true"],
            row[f"{prefix}_displaced_threshold_m"],
        ]
        if all(v is None for v in values):
            return None
        return RunwayEnd(
            ident=row[f"{prefix}_ident"],
            latitude=row[f"{prefix}_latitude"],
            longitude=row[f"{prefix}_longitude"],
            elevation_m=row[f"{prefix}_elevation_m"],
            heading_deg_true=row[f"{prefix}_heading_deg_true"],
            displaced_threshold_m=row[f"{prefix}_displaced_threshold_m"],
        )


    def get_metadata(self, situation_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT s.situation_id, s.name, s.description, s.content_hash, s.owner_user_id,
                       s.created_at, s.updated_at,
                       (SELECT COUNT(*) FROM situation_airports a WHERE a.situation_id = s.situation_id) AS airport_count,
                       (SELECT COUNT(*) FROM situation_missions m WHERE m.situation_id = s.situation_id) AS mission_count,
                       (SELECT COUNT(*) FROM situation_damage_scenarios d WHERE d.situation_id = s.situation_id) AS damage_scenario_count,
                       (SELECT COUNT(*) FROM runs r WHERE r.situation_id = s.situation_id) AS historical_run_count,
                       (SELECT COUNT(*) FROM runs r WHERE r.situation_id = s.situation_id AND r.status IN ('queued','running')) AS active_run_count
                FROM situations s WHERE s.situation_id = ?
                """,
                (situation_id,),
            ).fetchone()
        return None if row is None else dict(row)

    @staticmethod
    def _assert_visible(metadata: Dict[str, Any], *, actor_user_id: str, is_admin: bool) -> None:
        if is_admin:
            return
        if metadata.get("owner_user_id") != actor_user_id:
            raise SituationAccessError("Situation is not accessible to current user")

    def search_visible(
        self,
        *,
        actor_user_id: str,
        is_admin: bool = False,
        query: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[List[Dict[str, Any]], int]:
        if not isinstance(actor_user_id, str) or not actor_user_id.strip():
            raise ValueError("actor_user_id must be nonblank")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("invalid list pagination")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("invalid list pagination")
        clauses: list[str] = []
        params: list[Any] = []
        if not is_admin:
            clauses.append("s.owner_user_id = ?")
            params.append(actor_user_id)
        if query is not None and query.strip():
            q = f"%{query.strip()}%"
            clauses.append("(s.situation_id LIKE ? OR s.name LIKE ? OR COALESCE(s.description,'') LIKE ?)")
            params.extend([q, q, q])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        base = f"FROM situations s {where}"
        with self.connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) {base}", tuple(params)).fetchone()[0])
            rows = conn.execute(
                f"""
                SELECT s.situation_id, s.name, s.description, s.content_hash, s.owner_user_id,
                       s.created_at, s.updated_at,
                       (SELECT COUNT(*) FROM situation_airports a WHERE a.situation_id = s.situation_id) AS airport_count,
                       (SELECT COUNT(*) FROM situation_missions m WHERE m.situation_id = s.situation_id) AS mission_count,
                       (SELECT COUNT(*) FROM situation_damage_scenarios d WHERE d.situation_id = s.situation_id) AS damage_scenario_count,
                       (SELECT COUNT(*) FROM runs r WHERE r.situation_id = s.situation_id) AS historical_run_count,
                       (SELECT COUNT(*) FROM runs r WHERE r.situation_id = s.situation_id AND r.status IN ('queued','running')) AS active_run_count
                {base}
                ORDER BY COALESCE(s.updated_at, s.created_at) DESC, s.situation_id
                LIMIT ? OFFSET ?
                """,
                tuple([*params, limit, offset]),
            ).fetchall()
        return [dict(row) for row in rows], total

    def list_visible(
        self,
        *,
        actor_user_id: str,
        is_admin: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        items, _total = self.search_visible(
            actor_user_id=actor_user_id, is_admin=is_admin, limit=limit, offset=offset
        )
        return items

    def get_situation_for_actor(
        self,
        situation_id: str,
        *,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> Optional[Situation]:
        metadata = self.get_metadata(situation_id)
        if metadata is None:
            return None
        self._assert_visible(metadata, actor_user_id=actor_user_id, is_admin=is_admin)
        return self.get_situation(situation_id)

    def get_content_hash(self, situation_id: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT content_hash FROM situations WHERE situation_id = ?",
                (situation_id,),
            ).fetchone()
        return None if row is None else row["content_hash"]

    def get_situation(self, situation_id: str) -> Optional[Situation]:
        with self.connect() as conn:
            root = conn.execute(
                "SELECT situation_id, name, description FROM situations WHERE situation_id = ?",
                (situation_id,),
            ).fetchone()
            if root is None:
                return None

            airport_rows = conn.execute(
                "SELECT * FROM situation_airports WHERE situation_id = ? ORDER BY airport_id",
                (situation_id,),
            ).fetchall()
            airports: List[SituationAirport] = []
            for a in airport_rows:
                runway_rows = conn.execute(
                    "SELECT * FROM situation_runways WHERE situation_id = ? AND airport_id = ? ORDER BY runway_id",
                    (situation_id, a["airport_id"]),
                ).fetchall()
                runways = None
                if bool(a["runways_known"]):
                    runways = tuple(
                        RunwayBase(
                            runway_id=r["runway_id"], length_m=r["length_m"], width_m=r["width_m"],
                            surface=r["surface"], lighted=None if r["lighted"] is None else bool(r["lighted"]),
                            low_end=self._end_from_row(r, "low"), high_end=self._end_from_row(r, "high"),
                        )
                        for r in runway_rows
                    )
                ap = AirportBase(
                    airport_id=a["airport_id"], airport_name=a["airport_name"],
                    facility_type=a["facility_type"], role=a["role"],
                    longitude=a["longitude"], latitude=a["latitude"],
                    scheduled_service=bool(a["scheduled_service"]), icao_code=a["icao_code"],
                    iata_code=a["iata_code"], region=a["region"], municipality=a["municipality"],
                    elevation_m=a["elevation_m"], runways=runways,
                )
                support_rows = conn.execute(
                    """
                    SELECT aircraft_type_id, initial_quantity, tau_reset_windows
                    FROM situation_aircraft_support
                    WHERE situation_id = ? AND airport_id = ? ORDER BY aircraft_type_id
                    """,
                    (situation_id, a["airport_id"]),
                ).fetchall()
                stock_rows = conn.execute(
                    """
                    SELECT resource_type_id, quantity, replenishment_capacity_per_window
                    FROM situation_resource_stocks
                    WHERE situation_id = ? AND airport_id = ? ORDER BY resource_type_id
                    """,
                    (situation_id, a["airport_id"]),
                ).fetchall()
                op = AirportOperationalProfile(
                    airport_id=a["airport_id"], configuration_complete=bool(a["configuration_complete"]),
                    capacity_per_window=a["capacity_per_window"], support_level=a["support_level"],
                    aircraft_support=tuple(
                        AirportAircraftSupport(r["aircraft_type_id"], r["initial_quantity"], r["tau_reset_windows"])
                        for r in support_rows
                    ),
                    resource_stocks=tuple(
                        AirportResourceStock(
                            resource_type_id=r["resource_type_id"],
                            initial_quantity=r["quantity"],
                            replenishment_capacity_per_window=r["replenishment_capacity_per_window"],
                        )
                        for r in stock_rows
                    ),
                )
                replenishment_rows = conn.execute(
                    """
                    SELECT resource_type_id, slot, quantity
                    FROM situation_resource_replenishments
                    WHERE situation_id = ? AND airport_id = ?
                    ORDER BY slot, resource_type_id
                    """,
                    (situation_id, a["airport_id"]),
                ).fetchall()
                airports.append(
                    SituationAirport(
                        ap,
                        op,
                        tuple(
                            ResourceReplenishment(
                                resource_type_id=r["resource_type_id"],
                                slot=r["slot"],
                                quantity=r["quantity"],
                            )
                            for r in replenishment_rows
                        ),
                    )
                )

            mission_rows = conn.execute(
                "SELECT * FROM situation_missions WHERE situation_id = ? ORDER BY mission_id",
                (situation_id,),
            ).fetchall()
            missions: List[Mission] = []
            for m in mission_rows:
                req_rows = conn.execute(
                    """
                    SELECT aircraft_type_id, required_sorties, tau_work_windows
                    FROM situation_mission_aircraft_requirements
                    WHERE situation_id = ? AND mission_id = ? ORDER BY aircraft_type_id
                    """,
                    (situation_id, m["mission_id"]),
                ).fetchall()
                missions.append(
                    Mission(
                        mission_id=m["mission_id"], name=m["name"], longitude=m["longitude"], latitude=m["latitude"],
                        window_start_slot=m["window_start_slot"], window_end_slot=m["window_end_slot"],
                        aircraft_requirements=tuple(
                            MissionAircraftRequirement(r["aircraft_type_id"], r["required_sorties"], r["tau_work_windows"])
                            for r in req_rows
                        ),
                    )
                )

            scenario_rows = conn.execute(
                """
                SELECT damage_scenario_id, name, category
                FROM situation_damage_scenarios
                WHERE situation_id = ?
                ORDER BY damage_scenario_id
                """,
                (situation_id,),
            ).fetchall()
            damage_scenarios = []
            for scenario_row in scenario_rows:
                event_rows = conn.execute(
                    """
                    SELECT event_id, sequence_no, airport_id, target_type, target_id, damage_type,
                           start_slot, end_slot, effect_json, recovery_mode, recovery_duration_slots
                    FROM situation_damage_events
                    WHERE situation_id = ? AND damage_scenario_id = ?
                    ORDER BY sequence_no, event_id
                    """,
                    (situation_id, scenario_row["damage_scenario_id"]),
                ).fetchall()
                events = tuple(
                    DamageEvent.from_mapping({
                        "event_id": r["event_id"],
                        "sequence": r["sequence_no"],
                        "target": {
                            "airport_id": r["airport_id"],
                            "target_type": r["target_type"],
                            "target_id": r["target_id"],
                        },
                        "damage_type": r["damage_type"],
                        "start_slot": r["start_slot"],
                        "end_slot": r["end_slot"],
                        "effect": json.loads(r["effect_json"]),
                        "recovery_mode": r["recovery_mode"],
                        "recovery_duration_slots": r["recovery_duration_slots"],
                    })
                    for r in event_rows
                )
                damage_scenarios.append(
                    DamageScenario(
                        damage_scenario_id=scenario_row["damage_scenario_id"],
                        name=scenario_row["name"],
                        category=scenario_row["category"],
                        events=events,
                    )
                )

            return Situation(
                situation_id=root["situation_id"], name=root["name"], description=root["description"],
                airports=tuple(airports), missions=tuple(missions), damage_scenarios=tuple(damage_scenarios),
            )

    def create_situation(self, situation: Situation, *, owner_user_id: str) -> str:
        if self.get_metadata(situation.situation_id) is not None:
            raise SituationConflictError(f"Situation already exists: {situation.situation_id}")
        return self.save_situation(situation, owner_user_id=owner_user_id)

    def update_situation_for_actor(
        self,
        situation: Situation,
        *,
        actor_user_id: str,
        is_admin: bool = False,
        expected_content_hash: str,
    ) -> str:
        metadata = self.get_metadata(situation.situation_id)
        if metadata is None:
            raise KeyError(f"situation not found: {situation.situation_id}")
        self._assert_visible(metadata, actor_user_id=actor_user_id, is_admin=is_admin)
        # Preserve the original owner. Admin edit does not transfer ownership.
        return self.save_situation(
            situation,
            owner_user_id=metadata.get("owner_user_id") or actor_user_id,
            expected_content_hash=expected_content_hash,
        )

    def delete_situation_for_actor(
        self,
        situation_id: str,
        *,
        actor_user_id: str,
        is_admin: bool = False,
        expected_content_hash: str,
    ) -> Dict[str, Any]:
        metadata = self.get_metadata(situation_id)
        if metadata is None:
            raise KeyError(f"situation not found: {situation_id}")
        self._assert_visible(metadata, actor_user_id=actor_user_id, is_admin=is_admin)
        if metadata.get("content_hash") != expected_content_hash:
            raise SituationConflictError(
                f"stale Situation delete: expected {expected_content_hash}, current {metadata.get('content_hash')}"
            )
        # Run snapshots are immutable and contain the full Situation, so deleting the
        # mutable current record cannot alter queued/running/history inputs. The response
        # reports references so the UI can explain that historical Runs remain available.
        with self.connect() as conn:
            row = conn.execute("SELECT content_hash FROM situations WHERE situation_id=?", (situation_id,)).fetchone()
            if row is None:
                raise KeyError(f"situation not found: {situation_id}")
            if row["content_hash"] != expected_content_hash:
                raise SituationConflictError("Situation changed before delete completed")
            conn.execute("DELETE FROM situations WHERE situation_id=?", (situation_id,))
        return {
            "situation_id": situation_id,
            "deleted": True,
            "historical_run_count": int(metadata.get("historical_run_count") or 0),
            "active_run_count": int(metadata.get("active_run_count") or 0),
        }
