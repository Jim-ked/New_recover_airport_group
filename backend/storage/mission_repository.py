from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.domain.mission import Mission, MissionAircraftRequirement
from backend.storage.database import initialize_database


class MissionConflictError(ValueError):
    pass


class MissionReferenceError(ValueError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class MissionRepository:
    """Reusable MissionRecord library. Copying into Situation creates an independent value snapshot."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        initialize_database(self.db_path)

    def save(self, mission: Mission) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO mission_records (
                    mission_id, name, longitude, latitude, window_start_slot, window_end_slot
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(mission_id) DO UPDATE SET
                    name=excluded.name,
                    longitude=excluded.longitude,
                    latitude=excluded.latitude,
                    window_start_slot=excluded.window_start_slot,
                    window_end_slot=excluded.window_end_slot
                """,
                (
                    mission.mission_id, mission.name, mission.longitude, mission.latitude,
                    mission.window_start_slot, mission.window_end_slot,
                ),
            )
            conn.execute("DELETE FROM mission_record_aircraft_requirements WHERE mission_id = ?", (mission.mission_id,))
            for row in mission.aircraft_requirements:
                conn.execute(
                    """
                    INSERT INTO mission_record_aircraft_requirements (
                        mission_id, aircraft_type_id, required_sorties, tau_work_windows
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (mission.mission_id, row.aircraft_type_id, row.required_sorties, row.tau_work_windows),
                )

    def get(self, mission_id: str) -> Mission:
        with self.connect() as conn:
            root = conn.execute("SELECT * FROM mission_records WHERE mission_id = ?", (mission_id,)).fetchone()
            if root is None:
                raise KeyError(f"mission record not found: {mission_id}")
            rows = conn.execute(
                """
                SELECT aircraft_type_id, required_sorties, tau_work_windows
                FROM mission_record_aircraft_requirements
                WHERE mission_id = ? ORDER BY aircraft_type_id
                """,
                (mission_id,),
            ).fetchall()
        return Mission(
            mission_id=root["mission_id"], name=root["name"],
            longitude=root["longitude"], latitude=root["latitude"],
            window_start_slot=root["window_start_slot"], window_end_slot=root["window_end_slot"],
            aircraft_requirements=tuple(
                MissionAircraftRequirement(r["aircraft_type_id"], r["required_sorties"], r["tau_work_windows"])
                for r in rows
            ),
        )

    def list(self) -> List[Mission]:
        with self.connect() as conn:
            ids = [r["mission_id"] for r in conn.execute("SELECT mission_id FROM mission_records ORDER BY mission_id")]
        return [self.get(mid) for mid in ids]

    def delete(self, mission_id: str) -> None:
        with self.connect() as conn:
            result = conn.execute("DELETE FROM mission_records WHERE mission_id = ?", (mission_id,))
            if result.rowcount == 0:
                raise KeyError(f"mission record not found: {mission_id}")

    def get_metadata(self, mission_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT mission_id, revision, created_at, updated_at FROM mission_records WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def get_bundle(self, mission_id: str) -> Dict[str, Any]:
        mission = self.get(mission_id)
        meta = self.get_metadata(mission_id)
        if meta is None:
            raise KeyError(f"mission record not found: {mission_id}")
        return {"mission": mission.to_dict(), "metadata": meta}

    def list_bundles(
        self, *, query: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= 500):
            raise ValueError("limit must be in [1,500]")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be nonnegative")
        params: List[Any] = []
        where = ""
        if query is not None and query.strip():
            q = f"%{query.strip()}%"
            where = "WHERE mission_id LIKE ? OR name LIKE ?"
            params.extend([q, q])
        with self.connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) AS c FROM mission_records {where}", tuple(params)).fetchone()["c"])
            ids = [r["mission_id"] for r in conn.execute(
                f"SELECT mission_id FROM mission_records {where} ORDER BY COALESCE(updated_at,created_at) DESC, mission_id LIMIT ? OFFSET ?",
                tuple(params + [limit, offset]),
            ).fetchall()]
        return [self.get_bundle(mid) for mid in ids], total

    def save_versioned(
        self, mission: Mission, *, create_only: bool = False, expected_revision: Optional[int] = None
    ) -> Dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT revision FROM mission_records WHERE mission_id=?", (mission.mission_id,)).fetchone()
            exists = row is not None
            if create_only and exists:
                raise MissionConflictError(f"mission already exists: {mission.mission_id}")
            if not create_only and not exists:
                raise KeyError(f"mission record not found: {mission.mission_id}")
            if exists and expected_revision is not None and int(row["revision"]) != int(expected_revision):
                raise MissionConflictError(
                    f"mission revision conflict: expected {expected_revision}, current {row['revision']}"
                )
            if exists:
                conn.execute(
                    """
                    UPDATE mission_records SET name=?, longitude=?, latitude=?, window_start_slot=?, window_end_slot=?,
                        revision=revision+1, updated_at=CURRENT_TIMESTAMP
                    WHERE mission_id=?
                    """,
                    (mission.name, mission.longitude, mission.latitude, mission.window_start_slot,
                     mission.window_end_slot, mission.mission_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO mission_records (
                        mission_id,name,longitude,latitude,window_start_slot,window_end_slot,
                        revision,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                    """,
                    (mission.mission_id, mission.name, mission.longitude, mission.latitude,
                     mission.window_start_slot, mission.window_end_slot),
                )
            conn.execute("DELETE FROM mission_record_aircraft_requirements WHERE mission_id=?", (mission.mission_id,))
            for req in mission.aircraft_requirements:
                conn.execute(
                    "INSERT INTO mission_record_aircraft_requirements (mission_id,aircraft_type_id,required_sorties,tau_work_windows) VALUES (?,?,?,?)",
                    (mission.mission_id, req.aircraft_type_id, req.required_sorties, req.tau_work_windows),
                )
        return self.get_bundle(mission.mission_id)

    def delete_versioned(self, mission_id: str, *, expected_revision: Optional[int] = None) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT revision FROM mission_records WHERE mission_id=?", (mission_id,)).fetchone()
            if row is None:
                raise KeyError(f"mission record not found: {mission_id}")
            if expected_revision is not None and int(row["revision"]) != int(expected_revision):
                raise MissionConflictError(
                    f"mission revision conflict: expected {expected_revision}, current {row['revision']}"
                )
            refs = int(conn.execute(
                "SELECT COUNT(*) AS c FROM situation_missions WHERE mission_id=?", (mission_id,)
            ).fetchone()["c"])
            if refs:
                raise MissionReferenceError(
                    f"mission is present in {refs} saved Situation(s); remove it there before deleting the template"
                )
            conn.execute("DELETE FROM mission_records WHERE mission_id=?", (mission_id,))

    def replace_current(self, missions: Sequence[Mission]) -> Dict[str, int]:
        """Atomically replace the current reusable mission template dataset.

        Situation mission rows remain independent value snapshots. ``revision`` is kept
        only as a single-record optimistic-concurrency token, not as version history.
        """
        ids = [x.mission_id for x in missions]
        if len(ids) != len(set(ids)):
            raise MissionConflictError("bulk mission import contains duplicate mission_id")
        with self.connect() as conn:
            old_ids = {str(r[0]) for r in conn.execute("SELECT mission_id FROM mission_records").fetchall()}
            for mission in missions:
                exists = mission.mission_id in old_ids
                if exists:
                    conn.execute(
                        """UPDATE mission_records SET name=?,longitude=?,latitude=?,window_start_slot=?,window_end_slot=?,
                           revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE mission_id=?""",
                        (mission.name,mission.longitude,mission.latitude,mission.window_start_slot,mission.window_end_slot,mission.mission_id),
                    )
                else:
                    conn.execute(
                        """INSERT INTO mission_records
                           (mission_id,name,longitude,latitude,window_start_slot,window_end_slot,revision,created_at,updated_at)
                           VALUES (?,?,?,?,?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                        (mission.mission_id,mission.name,mission.longitude,mission.latitude,mission.window_start_slot,mission.window_end_slot),
                    )
                conn.execute("DELETE FROM mission_record_aircraft_requirements WHERE mission_id=?", (mission.mission_id,))
                for req in mission.aircraft_requirements:
                    conn.execute(
                        "INSERT INTO mission_record_aircraft_requirements (mission_id,aircraft_type_id,required_sorties,tau_work_windows) VALUES (?,?,?,?)",
                        (mission.mission_id,req.aircraft_type_id,req.required_sorties,req.tau_work_windows),
                    )
            new_ids = set(ids)
            for mission_id in sorted(old_ids - new_ids):
                conn.execute("DELETE FROM mission_records WHERE mission_id=?", (mission_id,))
        return {
            "added": len(new_ids - old_ids),
            "updated": len(new_ids & old_ids),
            "deleted": len(old_ids - new_ids),
            "total": len(new_ids),
        }

