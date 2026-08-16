from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class ApiResponse:
    body: Mapping[str, Any]
    status: int = 200


class ApiInputError(ValueError):
    def __init__(self, message: str, *, field: Optional[str] = None, code: str = "INVALID_REQUEST"):
        super().__init__(message)
        self.field = field
        self.code = code


def error_body(code: str, message: str, *, field: Optional[str] = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if field is not None:
        error["field"] = field
    return {"error": error}


def require_object(value: Any, *, field: str = "body") -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ApiInputError(f"{field} must be a JSON object", field=field)
    return value


def reject_unknown(value: Mapping[str, Any], allowed: set[str], *, prefix: str = "") -> None:
    unknown = sorted(str(k) for k in value if k not in allowed)
    if unknown:
        field = f"{prefix}{unknown[0]}"
        raise ApiInputError(f"unknown field: {field}", field=field)


def required_nonblank_string(value: Mapping[str, Any], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ApiInputError(f"{key} must be a nonblank string", field=key)
    return raw



def required_nonblank_string_list(
    value: Mapping[str, Any],
    key: str,
    *,
    minimum: int,
    maximum: int,
    unique: bool = True,
) -> list[str]:
    raw = value.get(key)
    if not isinstance(raw, list):
        raise ApiInputError(f"{key} must be a JSON array", field=key)
    if not minimum <= len(raw) <= maximum:
        raise ApiInputError(f"{key} must contain {minimum} to {maximum} items", field=key)
    out: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise ApiInputError(f"{key}[{i}] must be a nonblank string", field=f"{key}[{i}]")
        out.append(item)
    if unique and len(set(out)) != len(out):
        raise ApiInputError(f"{key} must contain distinct values", field=key)
    return out

def parse_nonnegative_int(raw: Any, *, field: str, default: int, maximum: int) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise ApiInputError(f"{field} must be an integer", field=field)
    try:
        if isinstance(raw, str):
            if not raw or not raw.isdigit():
                raise ValueError
            value = int(raw)
        elif isinstance(raw, int):
            value = raw
        else:
            raise ValueError
    except (TypeError, ValueError):
        raise ApiInputError(f"{field} must be an integer", field=field)
    if value < 0 or value > maximum:
        raise ApiInputError(f"{field} must be in [0,{maximum}]", field=field)
    return value


def parse_positive_int(raw: Any, *, field: str, default: int, maximum: int) -> int:
    value = parse_nonnegative_int(raw, field=field, default=default, maximum=maximum)
    if value < 1:
        raise ApiInputError(f"{field} must be in [1,{maximum}]", field=field)
    return value


__all__ = [
    "ApiResponse",
    "ApiInputError",
    "error_body",
    "require_object",
    "reject_unknown",
    "required_nonblank_string",
    "required_nonblank_string_list",
    "parse_nonnegative_int",
    "parse_positive_int",
]
