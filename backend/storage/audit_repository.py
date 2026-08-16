from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from backend.storage.run_repository import canonical_json


class AuditRepositoryError(RuntimeError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __enter__(self):
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


@dataclass(frozen=True)
class AuditEventRecord:
    audit_id: int
    actor_user_id: Optional[str]
    actor_role: Optional[str]
    action: str
    resource_type: Optional[str]
    resource_id: Optional[str]
    request_method: str
    request_path: str
    source_address: Optional[str]
    response_status: int
    outcome: str
    details: Dict[str, Any]
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "actor_user_id": self.actor_user_id,
            "actor_role": self.actor_role,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "request_method": self.request_method,
            "request_path": self.request_path,
            "source_address": self.source_address,
            "response_status": self.response_status,
            "outcome": self.outcome,
            "details": dict(self.details),
            "created_at": self.created_at,
        }


class AuditRepository:
    """Append-only operation audit authority.

    Request/response bodies are intentionally not stored here.  The audit record captures
    identity, route/action, object identity, source address and outcome without duplicating
    potentially sensitive business payloads.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _row(row: sqlite3.Row) -> AuditEventRecord:
        details = json.loads(row["details_json"])
        if not isinstance(details, dict):
            raise AuditRepositoryError("stored audit details must be a JSON object")
        return AuditEventRecord(
            audit_id=int(row["audit_id"]),
            actor_user_id=row["actor_user_id"],
            actor_role=row["actor_role"],
            action=row["action"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            request_method=row["request_method"],
            request_path=row["request_path"],
            source_address=row["source_address"],
            response_status=int(row["response_status"]),
            outcome=row["outcome"],
            details=details,
            created_at=row["created_at"],
        )

    def append(
        self,
        *,
        action: str,
        request_method: str,
        request_path: str,
        response_status: int,
        outcome: str,
        actor_user_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        source_address: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> AuditEventRecord:
        if not isinstance(action, str) or not action.strip():
            raise AuditRepositoryError("action must be nonblank")
        if not isinstance(request_method, str) or not request_method.strip():
            raise AuditRepositoryError("request_method must be nonblank")
        if not isinstance(request_path, str) or not request_path.strip():
            raise AuditRepositoryError("request_path must be nonblank")
        if isinstance(response_status, bool) or not isinstance(response_status, int) or not (100 <= response_status <= 599):
            raise AuditRepositoryError("response_status must be an HTTP status code")
        if outcome not in {"success", "denied", "error"}:
            raise AuditRepositoryError("outcome must be success, denied or error")
        details_json = canonical_json(dict(details or {}))
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_events (
                    actor_user_id, actor_role, action, resource_type, resource_id,
                    request_method, request_path, source_address, response_status,
                    outcome, details_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    actor_user_id, actor_role, action.strip(), resource_type, resource_id,
                    request_method.strip().upper(), request_path.strip(), source_address,
                    response_status, outcome, details_json,
                ),
            )
            audit_id = int(cur.lastrowid)
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM audit_events WHERE audit_id=?", (audit_id,)).fetchone()
        if row is None:
            raise AuditRepositoryError("audit event disappeared after insert")
        return self._row(row)

    def query(
        self,
        *,
        actor_user_id: Optional[str] = None,
        action_query: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        outcome: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[list[AuditEventRecord], int]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 500):
            raise AuditRepositoryError("limit must be in [1,500]")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise AuditRepositoryError("offset must be nonnegative")
        if outcome is not None and outcome not in {"success", "denied", "error"}:
            raise AuditRepositoryError("unknown audit outcome")
        clauses = ["1=1"]
        params: list[Any] = []
        for field, value in (("actor_user_id", actor_user_id), ("resource_type", resource_type), ("resource_id", resource_id), ("outcome", outcome)):
            if value is not None:
                clauses.append(f"{field}=?")
                params.append(value)
        if action_query is not None:
            clauses.append("lower(action) LIKE ?")
            params.append(f"%{action_query.lower()}%")
        if created_after is not None:
            clauses.append("created_at>=?")
            params.append(created_after)
        if created_before is not None:
            clauses.append("created_at<=?")
            params.append(created_before)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM audit_events WHERE {where}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT * FROM audit_events WHERE {where} ORDER BY created_at DESC, audit_id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._row(row) for row in rows], total


__all__ = ["AuditRepository", "AuditRepositoryError", "AuditEventRecord"]