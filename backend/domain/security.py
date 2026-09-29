from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, NoReturn, Optional


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class SecurityValidationError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise SecurityValidationError(message, field=field)


def _text(value: Any, field: str, *, optional: bool = False) -> Optional[str]:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(field, f"{field} must be a nonblank string")
    return value.strip()


def _id(value: Any, field: str) -> str:
    text = _text(value, field)
    if not _ID_RE.fullmatch(str(text)):
        _fail(field, f"{field} is invalid")
    return str(text)


@dataclass(frozen=True)
class SecurityAlert:
    id: str
    kind: str
    message: str
    severity: str = "warning"
    created_at: Optional[str] = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SecurityAlert":
        severity = raw.get("severity", "warning")
        if severity not in {"info", "warning", "critical"}:
            _fail("severity", "severity must be info, warning or critical")
        return cls(
            id=_id(raw.get("id"), "id"),
            kind=str(_text(raw.get("kind"), "kind")),
            message=str(_text(raw.get("message"), "message")),
            severity=severity,
            created_at=_text(raw.get("created_at"), "created_at", optional=True),
        )


@dataclass(frozen=True)
class BackupRecord:
    id: str
    kind: str
    path: str
    encrypted: bool
    created_at: Optional[str] = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "BackupRecord":
        if raw.get("kind") != "full":
            _fail("kind", "only full backup records are supported")
        encrypted = raw.get("encrypted")
        if not isinstance(encrypted, bool):
            _fail("encrypted", "encrypted must be boolean")
        return cls(
            id=_id(raw.get("id"), "id"),
            kind="full",
            path=str(_text(raw.get("path"), "path")),
            encrypted=encrypted,
            created_at=_text(raw.get("created_at"), "created_at", optional=True),
        )


@dataclass(frozen=True)
class AuditReport:
    id: str
    report_path: str
    format: str = "csv"
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    created_at: Optional[str] = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AuditReport":
        format_name = raw.get("format", "csv")
        if format_name not in {"csv", "pdf"}:
            _fail("format", "format must be csv or pdf")
        return cls(
            id=_id(raw.get("id"), "id"),
            report_path=str(_text(raw.get("report_path"), "report_path")),
            format=format_name,
            period_start=_text(raw.get("period_start"), "period_start", optional=True),
            period_end=_text(raw.get("period_end"), "period_end", optional=True),
            created_at=_text(raw.get("created_at"), "created_at", optional=True),
        )


__all__ = ["SecurityValidationError", "SecurityAlert", "BackupRecord", "AuditReport"]
