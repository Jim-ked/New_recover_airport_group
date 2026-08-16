from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, NoReturn

from backend.domain.airport import AirportBase
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
    """
    path = Path(seed_path)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("seed_path", f"cannot read canonical airport seed: {exc}")

    if not isinstance(raw, Mapping):
        _fail("seed", "airport seed root must be an object")
    allowed = {"schema", "generated_date", "coordinate_reference_system", "count", "airports"}
    unknown = [key for key in raw if key not in allowed]
    if unknown:
        _fail(str(unknown[0]), f"unknown seed metadata field: {unknown[0]}")
    if raw.get("schema") != "airport_master_v1":
        _fail("schema", "airport seed schema must be airport_master_v1")
    if raw.get("coordinate_reference_system") != "WGS84":
        _fail("coordinate_reference_system", "airport seed coordinate reference system must be WGS84")

    airports_raw = raw.get("airports")
    if not isinstance(airports_raw, list):
        _fail("airports", "airports must be an array")
    count = raw.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(airports_raw):
        _fail("count", "seed count must exactly match the airports array length")

    repository.init_schema()
    existing = repository.count_airports()
    if existing != 0:
        _fail("airports", f"airport authority is not empty ({existing} records); bootstrap refuses to overwrite")

    airports = [AirportBase.from_mapping(item) for item in airports_raw]
    ids = [item.airport_id for item in airports]
    if len(ids) != len(set(ids)):
        _fail("airports", "airport_id values must be unique in canonical seed")

    repository.save_airports(airports)
    stored = repository.count_airports()
    if stored != count:
        _fail("airports", f"bootstrap verification failed: expected {count}, stored {stored}")
    return stored
