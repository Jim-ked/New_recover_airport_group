from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from backend.domain.run import RUN_EVENT_LEVELS, RUN_STAGES, RUN_STATUSES, RunEvent, RunRecord
from backend.domain.run_snapshot import RunSnapshot
from backend.storage.database import initialize_database


class RunRepositoryError(RuntimeError):
    pass


class RunConflictError(RunRepositoryError):
    pass


class RunTransitionError(RunRepositoryError):
    pass


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RunRepositoryError(f"value is not finite canonical JSON: {exc}") from exc


def content_hash(value: Any) -> Tuple[str, str]:
    text = canonical_json(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), text


class _ClosingConnection(sqlite3.Connection):
    def __enter__(self):
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


class RunRepository:
    """Single SQLite authority for Run lifecycle, events and canonical result payloads."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        initialize_database(self.db_path)

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            owner_user_id=row["owner_user_id"],
            situation_id=row["situation_id"],
            snapshot_hash=row["snapshot_hash"],
            status=row["status"],
            cancel_requested=bool(row["cancel_requested"]),
            failure_code=row["failure_code"],
            failure_message=row["failure_message"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            solution_hash=row["solution_hash"],
            metrics_hash=row["metrics_hash"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> RunEvent:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise RunRepositoryError("stored Run event payload must be an object")
        return RunEvent(
            run_id=row["run_id"],
            seq=int(row["seq"]),
            level=row["level"],
            stage=row["stage"],
            event=row["event"],
            message=row["message"],
            payload=payload,
            created_at=row["created_at"],
        )

    def create_queued(self, *, snapshot: RunSnapshot, owner_user_id: str) -> RunRecord:
        """Atomically persist the immutable snapshot and its queued Run record."""
        if not isinstance(snapshot, RunSnapshot):
            raise TypeError("snapshot must be RunSnapshot")
        if not isinstance(owner_user_id, str) or not owner_user_id.strip():
            raise RunRepositoryError("owner_user_id must be nonblank")
        payload = snapshot.to_dict()
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO run_input_snapshots (
                        run_id, situation_id, situation_content_hash, snapshot_hash, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.run_id,
                        snapshot.situation_id,
                        payload.get("situation_content_hash"),
                        snapshot.content_hash,
                        snapshot.payload_json,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, owner_user_id, situation_id, snapshot_hash, status
                    ) VALUES (?, ?, ?, ?, 'queued')
                    """,
                    (snapshot.run_id, owner_user_id, snapshot.situation_id, snapshot.content_hash),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise RunConflictError(f"run already exists: {snapshot.run_id}") from exc
            raise
        record = self.get(snapshot.run_id)
        if record is None:
            raise RunRepositoryError("queued Run disappeared after insert")
        return record

    def get(self, run_id: str) -> Optional[RunRecord]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT r.*,
                       rr.solution_hash AS solution_hash,
                       rr.metrics_hash AS metrics_hash
                FROM runs r
                LEFT JOIN run_results rr ON rr.run_id = r.run_id
                WHERE r.run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return None if row is None else self._record_from_row(row)

    def search_for_owner(
        self,
        owner_user_id: str,
        *,
        statuses: Optional[Sequence[str]] = None,
        situation_id: Optional[str] = None,
        run_id_query: Optional[str] = None,
        task_id: Optional[str] = None,
        selected_airport_id: Optional[str] = None,
        damage_scenario_id: Optional[str] = None,
        no_damage: Optional[bool] = None,
        cluster_enabled: Optional[bool] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[RunRecord], int]:
        """Query owner-scoped Run history without making the frontend inspect snapshots.

        Filters on task, damage configuration and selected airport deliberately execute
        against the immutable snapshot/result JSON stored with each Run.  This keeps
        historical search tied to the same facts as detail/comparison pages instead of
        the mutable current Situation.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 500):
            raise RunRepositoryError("limit must be an integer in [1,500]")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise RunRepositoryError("offset must be a nonnegative integer")
        if not isinstance(owner_user_id, str) or not owner_user_id.strip():
            raise RunRepositoryError("owner_user_id must be nonblank")
        if no_damage is not None and not isinstance(no_damage, bool):
            raise RunRepositoryError("no_damage must be bool or None")
        if cluster_enabled is not None and not isinstance(cluster_enabled, bool):
            raise RunRepositoryError("cluster_enabled must be bool or None")
        if no_damage is True and damage_scenario_id is not None:
            raise RunRepositoryError("no_damage and damage_scenario_id cannot both be set")

        clauses: List[str] = ["r.owner_user_id = ?"]
        params: List[Any] = [owner_user_id]
        if statuses:
            vals = tuple(str(x) for x in statuses)
            unknown = sorted(set(vals) - set(RUN_STATUSES))
            if unknown:
                raise RunRepositoryError(f"unknown Run statuses: {unknown}")
            placeholders = ",".join("?" for _ in vals)
            clauses.append(f"r.status IN ({placeholders})")
            params.extend(vals)
        if situation_id is not None:
            clauses.append("r.situation_id = ?")
            params.append(str(situation_id))
        if run_id_query is not None:
            clauses.append("lower(r.run_id) LIKE ? ESCAPE '\\'")
            escaped = str(run_id_query).lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        if task_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(rs.payload_json, '$.situation.missions') jm "
                "WHERE json_extract(jm.value, '$.mission_id') = ?)"
            )
            params.append(str(task_id))
        if selected_airport_id is not None:
            clauses.append(
                "rr.run_id IS NOT NULL AND EXISTS (SELECT 1 FROM json_each(rr.solution_json, '$.selected_cluster') ja "
                "WHERE CAST(ja.value AS TEXT) = ?)"
            )
            params.append(str(selected_airport_id))
        if no_damage is True:
            clauses.append("json_extract(rs.payload_json, '$.run_config.damage_scenario_id') IS NULL")
        elif damage_scenario_id is not None:
            clauses.append("json_extract(rs.payload_json, '$.run_config.damage_scenario_id') = ?")
            params.append(str(damage_scenario_id))
        if cluster_enabled is not None:
            clauses.append("json_extract(rs.payload_json, '$.run_config.cluster_enabled') = ?")
            params.append(1 if cluster_enabled else 0)
        if created_after is not None:
            clauses.append("r.created_at >= ?")
            params.append(str(created_after))
        if created_before is not None:
            clauses.append("r.created_at <= ?")
            params.append(str(created_before))

        where = " AND ".join(clauses)
        base = f"""
            FROM runs r
            JOIN run_input_snapshots rs ON rs.run_id = r.run_id
            LEFT JOIN run_results rr ON rr.run_id = r.run_id
            WHERE {where}
        """
        with self.connect() as conn:
            total_row = conn.execute(f"SELECT COUNT(*) AS n {base}", params).fetchone()
            query_params = [*params, limit, offset]
            rows = conn.execute(
                f"""
                SELECT r.*,
                       rr.solution_hash AS solution_hash,
                       rr.metrics_hash AS metrics_hash
                {base}
                ORDER BY r.created_at DESC, r.run_id DESC
                LIMIT ? OFFSET ?
                """,
                query_params,
            ).fetchall()
        return [self._record_from_row(row) for row in rows], int(total_row["n"] if total_row else 0)

    def list_for_owner(
        self,
        owner_user_id: str,
        *,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[RunRecord]:
        records, _total = self.search_for_owner(
            owner_user_id, statuses=statuses, limit=limit, offset=offset
        )
        return records

    def claim_running(self, run_id: str) -> RunRecord:
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE runs
                SET status='running', started_at=CURRENT_TIMESTAMP
                WHERE run_id=? AND status='queued' AND cancel_requested=0
                """,
                (run_id,),
            )
            if cur.rowcount != 1:
                current = conn.execute("SELECT status, cancel_requested FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if current is None:
                    raise RunTransitionError(f"run not found: {run_id}")
                raise RunTransitionError(
                    f"cannot claim Run from status={current['status']} cancel_requested={bool(current['cancel_requested'])}"
                )
        record = self.get(run_id)
        assert record is not None
        return record

    def request_cancel(self, run_id: str) -> RunRecord:
        """Request cancellation without inventing an external `cancelling` status.

        queued Run -> cancelled immediately.
        running Run -> remains running with cancel_requested=true until the worker stops.
        terminal Run -> idempotently unchanged.
        """
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise RunTransitionError(f"run not found: {run_id}")
            status = row["status"]
            if status == "queued":
                conn.execute(
                    """
                    UPDATE runs
                    SET status='cancelled', cancel_requested=1, finished_at=CURRENT_TIMESTAMP
                    WHERE run_id=? AND status='queued'
                    """,
                    (run_id,),
                )
            elif status == "running":
                conn.execute(
                    "UPDATE runs SET cancel_requested=1 WHERE run_id=? AND status='running'",
                    (run_id,),
                )
            elif status not in ("succeeded", "failed", "cancelled"):
                raise RunTransitionError(f"unknown stored Run status: {status}")
        record = self.get(run_id)
        assert record is not None
        return record

    def mark_cancelled(self, run_id: str) -> RunRecord:
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE runs
                SET status='cancelled', cancel_requested=1, finished_at=CURRENT_TIMESTAMP
                WHERE run_id=? AND status='running' AND cancel_requested=1
                """,
                (run_id,),
            )
            if cur.rowcount != 1:
                raise RunTransitionError("running Run can be cancelled only after an explicit cancel request")
        record = self.get(run_id)
        assert record is not None
        return record

    def mark_failed(self, run_id: str, *, message: str, code: Optional[str] = None) -> RunRecord:
        if not isinstance(message, str) or not message.strip():
            raise RunRepositoryError("failed Run requires a nonblank message")
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE runs
                SET status='failed', cancel_requested=0,
                    failure_code=?, failure_message=?, finished_at=CURRENT_TIMESTAMP
                WHERE run_id=? AND status IN ('queued','running')
                """,
                (code, message, run_id),
            )
            if cur.rowcount != 1:
                raise RunTransitionError("only queued/running Run can transition to failed")
        record = self.get(run_id)
        assert record is not None
        return record

    def save_success(
        self,
        run_id: str,
        *,
        solution: Mapping[str, Any],
        metrics: Mapping[str, Any],
    ) -> RunRecord:
        solution_hash, solution_json = content_hash(solution)
        metrics_hash, metrics_json = content_hash(metrics)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, cancel_requested FROM runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise RunTransitionError(f"run not found: {run_id}")
            if row["status"] != "running":
                raise RunTransitionError("only running Run can persist a successful result")
            if bool(row["cancel_requested"]):
                raise RunTransitionError("cancel-requested Run cannot be marked succeeded")
            existing = conn.execute("SELECT 1 FROM run_results WHERE run_id=?", (run_id,)).fetchone()
            if existing is not None:
                raise RunConflictError("canonical Run result is insert-only")
            conn.execute(
                """
                INSERT INTO run_results (
                    run_id, solution_hash, solution_json, metrics_hash, metrics_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, solution_hash, solution_json, metrics_hash, metrics_json),
            )
            conn.execute(
                """
                UPDATE runs
                SET status='succeeded', finished_at=CURRENT_TIMESTAMP
                WHERE run_id=? AND status='running' AND cancel_requested=0
                """,
                (run_id,),
            )
        record = self.get(run_id)
        assert record is not None
        return record

    def get_result_payloads(self, run_id: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT solution_json, metrics_json FROM run_results WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        solution = json.loads(row["solution_json"])
        metrics = json.loads(row["metrics_json"])
        if not isinstance(solution, dict) or not isinstance(metrics, dict):
            raise RunRepositoryError("stored canonical result payload must be JSON objects")
        return solution, metrics

    def append_event(
        self,
        run_id: str,
        *,
        level: str,
        stage: str,
        event: str,
        message: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> RunEvent:
        # Validate the public event envelope before touching persistence. `created_at` and
        # the final seq are assigned by SQLite, so placeholders are sufficient here.
        RunEvent(
            run_id=run_id, seq=1, level=level, stage=stage, event=event, message=message,
            payload=dict(payload or {}), created_at="pending",
        )
        payload_json = canonical_json(dict(payload or {}))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if exists is None:
                raise RunRepositoryError(f"run not found: {run_id}")
            row = conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS next_seq FROM run_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
            seq = int(row["next_seq"])
            conn.execute(
                """
                INSERT INTO run_events (
                    run_id, seq, level, stage, event, message, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, seq, level, stage, event, message, payload_json),
            )
        events = self.list_events(run_id, after_seq=seq - 1, limit=1)
        if len(events) != 1:
            raise RunRepositoryError("Run event disappeared after insert")
        return events[0]

    def list_events(self, run_id: str, *, after_seq: int = 0, limit: int = 200) -> List[RunEvent]:
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0:
            raise RunRepositoryError("after_seq must be a nonnegative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 1000):
            raise RunRepositoryError("limit must be an integer in [1,1000]")
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM run_events
                WHERE run_id=? AND seq>?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (run_id, after_seq, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]


__all__ = [
    "RunRepository",
    "RunRepositoryError",
    "RunConflictError",
    "RunTransitionError",
    "canonical_json",
    "content_hash",
]
