from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunWorkerStatus:
    """Small deployment-local heartbeat shared by Web and the queue worker."""

    def __init__(self, db_path: str | Path, *, stale_after_s: float = 8.0) -> None:
        self.db_path = Path(db_path).resolve()
        self.path = self.db_path.parent.parent / "worker-status.json"
        self.stale_after_s = float(stale_after_s)

    def _write(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix="worker-status-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(dict(payload), handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def heartbeat(self, *, status: str = "running", current_run_id: str | None = None) -> None:
        self._write({
            "db_path": str(self.db_path),
            "pid": os.getpid(),
            "status": status,
            "current_run_id": current_run_id,
            "last_seen_at": _now(),
        })

    def stopped(self) -> None:
        self.heartbeat(status="stopped")

    def read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"connected": False, "reason": "heartbeat_missing"}
        if not isinstance(payload, dict) or payload.get("db_path") != str(self.db_path):
            return {"connected": False, "reason": "heartbeat_wrong_database"}
        try:
            last_seen = datetime.fromisoformat(str(payload.get("last_seen_at")))
            age = max(0.0, (datetime.now(timezone.utc) - last_seen).total_seconds())
        except (TypeError, ValueError):
            return {"connected": False, "reason": "heartbeat_invalid"}
        connected = payload.get("status") == "running" and age <= self.stale_after_s
        return {
            "connected": connected,
            "status": payload.get("status"),
            "pid": payload.get("pid"),
            "current_run_id": payload.get("current_run_id"),
            "last_seen_at": payload.get("last_seen_at"),
            "age_seconds": age,
            "reason": None if connected else "heartbeat_stale",
        }


__all__ = ["RunWorkerStatus"]
