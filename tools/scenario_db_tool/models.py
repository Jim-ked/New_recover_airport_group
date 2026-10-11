from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from backend.domain.catalog import AircraftType, ResourceType, AircraftResourceRequirement
from backend.domain.situation import Situation


@dataclass(frozen=True)
class ValidationIssue:
    sheet: str
    row: int
    field: str
    message: str
    severity: str = 'error'

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ExcelRow:
    sheet: str
    row: int
    values: dict[str, Any]
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class ExcelData:
    source: Path
    tables: dict[str, list[ExcelRow]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    metadata_rows: dict[str, int] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class CompiledScenario:
    aircraft_types: tuple[AircraftType, ...]
    resource_types: tuple[ResourceType, ...]
    requirements: tuple[AircraftResourceRequirement, ...]
    situation: Situation


@dataclass
class ValidationResult:
    issues: list[ValidationIssue]
    scenario: CompiledScenario | None = None

    @property
    def ok(self) -> bool:
        return self.scenario is not None and not any(i.severity == 'error' for i in self.issues)


class ScenarioToolError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__('; '.join(f'{i.sheet}/{i.row}/{i.field}: {i.message}' for i in issues))
