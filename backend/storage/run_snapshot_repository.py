from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from backend.domain.run_snapshot import RunSnapshot
from backend.storage.database import initialize_database


class RunSnapshotConflictError(ValueError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __enter__(self):
        return super().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


class RunSnapshotRepository:
    """Insert-only persistence for immutable run input snapshots."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        initialize_database(self.db_path)

    def save_new(self, snapshot: RunSnapshot) -> None:
        payload = snapshot.to_dict()
        situation_hash = payload.get("situation_content_hash")
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
                        situation_hash,
                        snapshot.content_hash,
                        snapshot.payload_json,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed: run_input_snapshots.run_id" in str(exc):
                raise RunSnapshotConflictError(
                    f"run snapshot already exists and is immutable: {snapshot.run_id}"
                ) from exc
            raise

    def get(self, run_id: str) -> Optional[RunSnapshot]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, situation_id, snapshot_hash, payload_json
                FROM run_input_snapshots
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return RunSnapshot(
            run_id=row["run_id"],
            situation_id=row["situation_id"],
            content_hash=row["snapshot_hash"],
            payload_json=row["payload_json"],
        )
