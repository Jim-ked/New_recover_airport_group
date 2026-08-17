from __future__ import annotations

from typing import Any, Mapping, NoReturn

from backend.domain.airport import AirportBase

"""Single parsing entry for the canonical airport_master_v1 document.

System init (seed bootstrap) and Web import must both go through this parser.
Parsing only: it validates document metadata and per-item airport facts through
the existing AirportBase/Runway domain validation, then returns a validated
AirportBase collection.  Database writes remain repository/service concerns.
"""

MASTER_SCHEMA = "airport_master_v1"
MASTER_CRS = "WGS84"
MASTER_METADATA_FIELDS = frozenset(
    {"schema", "generated_date", "coordinate_reference_system", "count", "airports"}
)


class AirportMasterParseError(ValueError):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


def _fail(field: str, message: str) -> NoReturn:
    raise AirportMasterParseError(message, field=field)


def parse_airport_master_document(raw: Any) -> tuple[AirportBase, ...]:
    if not isinstance(raw, Mapping):
        _fail("body", "airport master document must be a JSON object")
    unknown = [key for key in raw if key not in MASTER_METADATA_FIELDS]
    if unknown:
        _fail(str(unknown[0]), f"unknown airport master metadata field: {unknown[0]}")
    if raw.get("schema") != MASTER_SCHEMA:
        _fail("schema", "airport master schema must be airport_master_v1")
    if raw.get("coordinate_reference_system") != MASTER_CRS:
        _fail(
            "coordinate_reference_system",
            "airport master coordinate reference system must be WGS84",
        )

    airports_raw = raw.get("airports")
    if not isinstance(airports_raw, list):
        _fail("airports", "airports must be an array")
    count = raw.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(airports_raw):
        _fail("count", "count must exactly match the airports array length")

    airports = tuple(AirportBase.from_mapping(item) for item in airports_raw)
    ids = [item.airport_id for item in airports]
    if len(ids) != len(set(ids)):
        _fail("airports", "airport_id values must be unique in the airport master document")
    return airports


__all__ = [
    "AirportMasterParseError",
    "MASTER_CRS",
    "MASTER_SCHEMA",
    "parse_airport_master_document",
]
