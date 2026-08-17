from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

from backend.domain.airport import AirportValidationError
from backend.services.airport_master_parser import (
    AirportMasterParseError,
    parse_airport_master_document,
)
from backend.storage.airport_repository import AirportRepository


class AirportSeedError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise AirportSeedError(message, field=field)


def bootstrap_airport_master(
    repository: AirportRepository,
    seed_path: str | Path,
) -> int:
    """
    Explicit one-time bootstrap from the project's canonical airport master seed.

    This is deliberately *not* a general import feature:
    - target airport authority must be empty;
    - no aliases, coercion, duplicate merging or coordinate repair are attempted;
    - existing business data is never overwritten;
    - AirportBase strict validation owns every airport row.

    Document parsing is shared with the Web import path through
    ``AirportMasterV1Parser`` (parse_airport_master_document).
    """
    path = Path(seed_path)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("seed_path", f"cannot read canonical airport seed: {exc}")

    try:
        airports = parse_airport_master_document(raw)
    except (AirportMasterParseError, AirportValidationError) as exc:
        field = getattr(exc, "field", None)
        _fail(field or "seed", str(exc))

    repository.init_schema()
    existing = repository.count_airports()
    if existing != 0:
        _fail("airports", f"airport authority is not empty ({existing} records); bootstrap refuses to overwrite")

    count = len(airports)
    repository.save_airports(list(airports))
    stored = repository.count_airports()
    if stored != count:
        _fail("airports", f"bootstrap verification failed: expected {count}, stored {stored}")
    return stored
