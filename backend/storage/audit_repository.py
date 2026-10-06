from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from backend.storage.run_repository import canonical_json


RESOURCE_LABELS = {
    "airports": "机场",
    "missions": "任务模板",
    "aircraft-types": "机型",
    "resource-types": "保障资源",
    "base-data": "基础数据导入",
    "aircraft-resource-requirements": "机型资源关系",
    "situations": "情境",
    "indicators": "指标",
    "indicator-sets": "指标集",
    "experts": "专家",
    "expert-scores": "专家评分",
    "indicator-weights": "指标权重",
    "runs": "算法运行",
    "results": "结果分析",
    "users": "用户",
    "auth": "认证",
    "audit-events": "审计日志",
    "me": "当前账户",
}


def audit_action_label(
    *,
    request_method: str,
    request_path: str,
    resource_type: Optional[str],
) -> str:
    """Return the business action wording shown in the audit workspace."""

    method = str(request_method or "").upper()
    path = str(request_path or "")
    resource = RESOURCE_LABELS.get(resource_type or "", "系统资源")

    if path == "/api/auth/login":
        return "登录"
    if path == "/api/auth/logout":
        return "退出登录"
    if path == "/api/auth/change-password":
        return "修改密码"

    if resource_type == "users":
        if path.endswith("/role"):
            return "修改用户角色"
        if path.endswith("/disabled"):
            return "修改用户状态"
        if path.endswith("/reset-password"):
            return "重置用户密码"
        if method == "POST":
            return "新建用户"
        return "浏览账户"

    if resource_type == "runs":
        if path == "/api/runs/validate":
            return "校验运行条件"
        if path == "/api/runs" and method == "POST":
            return "提交算法运行"
        if path.endswith("/cancel"):
            return "取消运行"
        if path.endswith("/retry"):
            return "重试运行"
        if path.endswith("/events"):
            return "查看运行日志"
        if path.endswith("/metrics"):
            return "查看运行指标"
        if path.endswith("/solution"):
            return "查看运行结果"
        if path.endswith("/runtime"):
            return "查看运行态势"
        if path.endswith("/situation"):
            return "查看运行情境"
        return "查看算法运行"

    if resource_type == "results":
        if "export" in path:
            return "导出分析结果"
        if "damage-candidates" in path:
            return "查看结果比较候选"
        if "comparable-runs" in path:
            return "查看可比较运行"
        if "comparison" in path:
            return "查看结果比较"
        return "查看结果分析"

    if resource_type == "situations":
        if "/working-copy/" in path:
            return "处理情境工作副本"
        if method == "POST":
            return "新建情境"
        if method == "PUT":
            return "修改情境"
        if method == "DELETE":
            return "删除情境"
        return "查看情境"

    nouns = {
        "airports": "机场",
        "missions": "任务模板",
        "aircraft-types": "机型",
        "resource-types": "保障资源",
        "indicators": "指标",
        "indicator-sets": "指标集",
        "experts": "专家",
        "expert-scores": "专家评分",
    }
    noun = nouns.get(resource_type or "")
    if noun:
        if method == "POST":
            return f"新建{noun}"
        if method == "PUT":
            return f"修改{noun}"
        if method == "DELETE":
            return f"删除{noun}"
        return f"查看{noun}"

    if method == "GET":
        return f"查看{resource}"
    if method == "PUT":
        return f"修改{resource}"
    if method == "DELETE":
        return f"删除{resource}"
    return f"执行{resource}操作"


def _keyword_matches(record: "AuditEventRecord", query: str) -> bool:
    needle = str(query).strip().casefold()
    if not needle:
        return True
    values = (
        audit_action_label(
            request_method=record.request_method,
            request_path=record.request_path,
            resource_type=record.resource_type,
        ),
        RESOURCE_LABELS.get(record.resource_type or ""),
        record.action,
        record.request_path,
        record.resource_type,
        record.resource_id,
        record.actor_user_id,
        record.actor_role,
        record.source_address,
        record.response_status,
        record.outcome,
    )
    return any(
        needle in str(value).casefold()
        for value in values
        if value is not None and str(value) != ""
    )


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
            "action_label": audit_action_label(
                request_method=self.request_method,
                request_path=self.request_path,
                resource_type=self.resource_type,
            ),
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
            row = conn.execute(
                """
                INSERT INTO audit_events (
                    actor_user_id, actor_role, action, resource_type, resource_id,
                    request_method, request_path, source_address, response_status,
                    outcome, details_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                RETURNING *
                """,
                (
                    actor_user_id, actor_role, action.strip(), resource_type, resource_id,
                    request_method.strip().upper(), request_path.strip(), source_address,
                    response_status, outcome, details_json,
                ),
            ).fetchone()
            if row is None:
                raise AuditRepositoryError("audit event disappeared after insert")
            record = self._row(row)
        return record

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
        if created_after is not None:
            clauses.append("created_at>=?")
            params.append(created_after)
        if created_before is not None:
            clauses.append("created_at<=?")
            params.append(created_before)
        where = " AND ".join(clauses)

        if action_query is not None:
            # The UI shows business labels such as “提交算法运行” and “查看机场”,
            # while the stored action is a route fact such as “POST /api/runs”.
            # Keyword search therefore evaluates both the business wording and the
            # technical/object fields before applying pagination.
            with self.connect() as conn:
                rows = conn.execute(
                    f"SELECT * FROM audit_events WHERE {where} ORDER BY created_at DESC, audit_id DESC",
                    params,
                ).fetchall()
            records = [self._row(row) for row in rows]
            matched = [record for record in records if _keyword_matches(record, action_query)]
            total = len(matched)
            return matched[offset:offset + limit], total

        with self.connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM audit_events WHERE {where}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT * FROM audit_events WHERE {where} ORDER BY created_at DESC, audit_id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._row(row) for row in rows], total


__all__ = [
    "AuditRepository", "AuditRepositoryError", "AuditEventRecord",
    "RESOURCE_LABELS", "audit_action_label",
]
