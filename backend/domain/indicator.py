from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, NoReturn, Optional

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SET_STATUSES = frozenset({"draft", "published", "disabled"})
NODE_KINDS = frozenset({"CATEGORY", "ABSTRACT", "DIRECT"})
DIRECTIONS = frozenset({"positive", "negative", "neutral"})
SCORE_STATUSES = frozenset({"draft", "submitted"})


class IndicatorValidationError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise IndicatorValidationError(message, field=field)


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _ID_RE.fullmatch(value):
        _fail(field, f"{field} must be a nonblank stable identifier")
    return value


def _text(value: Any, field: str, *, optional: bool = False) -> Optional[str]:
    if value is None and optional:
        return None
    if not isinstance(value, str) or (not optional and not value.strip()):
        _fail(field, f"{field} must be a string" + (" or null" if optional else ""))
    return value


@dataclass(frozen=True)
class IndicatorSet:
    id: str
    name: str
    version: str
    is_default: bool
    status: str
    description: Optional[str] = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "IndicatorSet":
        allowed = {"id", "name", "version", "is_default", "status", "description"}
        unknown = [k for k in raw if k not in allowed]
        if unknown:
            _fail(str(unknown[0]), f"unknown field: {unknown[0]}")
        default = raw.get("is_default")
        if not isinstance(default, bool):
            _fail("is_default", "is_default must be boolean")
        status = raw.get("status")
        if status not in SET_STATUSES:
            _fail("status", f"status must be one of {sorted(SET_STATUSES)}")
        return cls(
            id=_id(raw.get("id"), "id"),
            name=_text(raw.get("name"), "name") or "",
            version=_text(raw.get("version"), "version") or "",
            is_default=default,
            status=status,
            description=_text(raw.get("description"), "description", optional=True),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "is_default": self.is_default, "status": self.status,
            "description": self.description,
        }


@dataclass(frozen=True)
class IndicatorNode:
    id: str
    indicator_set_id: str
    parent_id: Optional[str]
    code: str
    name: str
    level: int
    node_kind: str
    unit: Optional[str] = None
    direction: Optional[str] = None
    weight: Optional[float] = None
    description: Optional[str] = None
    is_core: bool = False
    editable: bool = True
    enabled: bool = True
    display_order: int = 0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "IndicatorNode":
        allowed = {
            "id", "indicator_set_id", "parent_id", "code", "name", "level", "node_kind",
            "unit", "direction", "weight", "description", "is_core", "editable", "enabled", "display_order",
        }
        unknown = [k for k in raw if k not in allowed]
        if unknown:
            _fail(str(unknown[0]), f"unknown field: {unknown[0]}")
        level = raw.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or level not in (1, 2, 3):
            _fail("level", "level must be 1, 2 or 3")
        kind = raw.get("node_kind")
        if kind not in NODE_KINDS:
            _fail("node_kind", f"node_kind must be one of {sorted(NODE_KINDS)}")
        if level < 3 and kind != "CATEGORY":
            _fail("node_kind", "level 1/2 nodes must be CATEGORY")
        if level == 3 and kind == "CATEGORY":
            _fail("node_kind", "level 3 nodes must be ABSTRACT or DIRECT")
        parent = raw.get("parent_id")
        if level == 1 and parent is not None:
            _fail("parent_id", "level 1 parent_id must be null")
        if level > 1:
            parent = _id(parent, "parent_id")
        direction = raw.get("direction")
        if direction is not None and direction not in DIRECTIONS:
            _fail("direction", f"direction must be one of {sorted(DIRECTIONS)} or null")
        weight = raw.get("weight")
        if weight is not None:
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(float(weight)) or float(weight) < 0:
                _fail("weight", "weight must be a finite nonnegative number or null")
            weight = float(weight)
        bools = {}
        for key, default in (("is_core", False), ("editable", True), ("enabled", True)):
            value = raw.get(key, default)
            if not isinstance(value, bool):
                _fail(key, f"{key} must be boolean")
            bools[key] = value
        order = raw.get("display_order", 0)
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            _fail("display_order", "display_order must be a nonnegative integer")
        return cls(
            id=_id(raw.get("id"), "id"), indicator_set_id=_id(raw.get("indicator_set_id"), "indicator_set_id"),
            parent_id=parent, code=_id(raw.get("code"), "code"), name=_text(raw.get("name"), "name") or "",
            level=level, node_kind=kind, unit=_text(raw.get("unit"), "unit", optional=True), direction=direction,
            weight=weight, description=_text(raw.get("description"), "description", optional=True),
            is_core=bools["is_core"], editable=bools["editable"], enabled=bools["enabled"], display_order=order,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "indicator_set_id": self.indicator_set_id, "parent_id": self.parent_id,
            "code": self.code, "name": self.name, "level": self.level, "node_kind": self.node_kind,
            "unit": self.unit, "direction": self.direction, "weight": self.weight,
            "description": self.description, "is_core": self.is_core, "editable": self.editable,
            "enabled": self.enabled, "display_order": self.display_order,
        }


@dataclass(frozen=True)
class Expert:
    expert_id: str
    name: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Expert":
        allowed = {"expert_id", "name"}
        unknown = [k for k in raw if k not in allowed]
        if unknown:
            _fail(str(unknown[0]), f"unknown field: {unknown[0]}")
        return cls(expert_id=_id(raw.get("expert_id"), "expert_id"), name=_text(raw.get("name"), "name") or "")

    def to_dict(self) -> Dict[str, Any]:
        return {"expert_id": self.expert_id, "name": self.name}


@dataclass(frozen=True)
class ExpertScore:
    indicator_set_id: str
    indicator_id: str
    expert_id: str
    score: float
    status: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ExpertScore":
        allowed = {"indicator_set_id", "indicator_id", "expert_id", "score", "status"}
        unknown = [k for k in raw if k not in allowed]
        if unknown:
            _fail(str(unknown[0]), f"unknown field: {unknown[0]}")
        score = raw.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)) or not (0 <= float(score) <= 100):
            _fail("score", "score must be in [0,100]")
        status = raw.get("status")
        if status not in SCORE_STATUSES:
            _fail("status", f"status must be one of {sorted(SCORE_STATUSES)}")
        return cls(
            indicator_set_id=_id(raw.get("indicator_set_id"), "indicator_set_id"),
            indicator_id=_id(raw.get("indicator_id"), "indicator_id"),
            expert_id=_id(raw.get("expert_id"), "expert_id"),
            score=float(score), status=status,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "indicator_set_id": self.indicator_set_id, "indicator_id": self.indicator_id,
            "expert_id": self.expert_id, "score": self.score, "status": self.status,
        }
