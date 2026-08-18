from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from backend.domain.situation import Situation
from backend.storage.database import initialize_database
from backend.storage.situation_repository import SituationRepository


_AIRPORT_ID_RE = re.compile(r"^AP([0-9]+)$")
_SITUATION_ID_RE = re.compile(r"^ST([0-9]+)$")
_AIRPORT_REFERENCE_TABLES = (
    "airports",
    "runways",
    "airport_operational_profiles",
    "airport_aircraft_support",
    "airport_resource_stocks",
    "situation_airports",
    "situation_runways",
    "situation_aircraft_support",
    "situation_resource_stocks",
    "situation_resource_replenishments",
    "situation_damage_events",
)
_SITUATION_REFERENCE_TABLES = (
    "situations",
    "situation_airports",
    "situation_runways",
    "situation_aircraft_support",
    "situation_resource_stocks",
    "situation_resource_replenishments",
    "situation_damage_scenarios",
    "situation_damage_events",
    "situation_missions",
    "situation_mission_aircraft_requirements",
)


@dataclass(frozen=True)
class IdentifierMigrationReport:
    airports_migrated: int
    situations_migrated: int
    airport_reference_rows: int
    situation_reference_rows: int
    airport_next_value: int
    situation_next_value: int


def load_airport_id_map(path: str | Path) -> dict[str, str]:
    """Load and validate the one-time, human-auditable airport mapping authority."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError("airport ID mapping must be a non-empty JSON object")
    mapping = {str(old): str(new) for old, new in raw.items()}
    if any(not old.startswith("oa:") for old in mapping):
        raise ValueError("airport ID mapping keys must be OpenAirport oa:* IDs")
    if any(_AIRPORT_ID_RE.fullmatch(new) is None for new in mapping.values()):
        raise ValueError("airport ID mapping values must be APxxx IDs")
    if len(mapping) != len(set(mapping.values())):
        raise ValueError("airport ID mapping values must be unique")
    ordered = sorted(mapping.values(), key=lambda value: int(_AIRPORT_ID_RE.fullmatch(value).group(1)))
    expected = [f"AP{index:03d}" for index in range(1, len(ordered) + 1)]
    if ordered != expected:
        raise ValueError("airport ID mapping must contain the contiguous AP001..APn sequence")
    return mapping


def backup_database(db_path: str | Path, backup_dir: str | Path) -> Path:
    """Create a non-overwriting SQLite backup before an identifier migration."""
    source_path = Path(db_path)
    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_dir / f"{source_path.stem}_before_id_migration_{stamp}.sqlite3"
    suffix = 1
    while target.exists():
        target = target_dir / f"{source_path.stem}_before_id_migration_{stamp}_{suffix}.sqlite3"
        suffix += 1

    source = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    return target


def _replace_situation_ids(situation: Situation, *, airport_map: Mapping[str, str], situation_id: str) -> Situation:
    raw = situation.to_dict()
    raw["situation_id"] = situation_id
    for item in raw["airports"]:
        old_id = item["airport"]["airport_id"]
        item["airport"]["airport_id"] = airport_map.get(old_id, old_id)
        item["operational_profile"]["airport_id"] = airport_map.get(
            item["operational_profile"]["airport_id"],
            item["operational_profile"]["airport_id"],
        )
    for scenario in raw["damage_scenarios"]:
        for event in scenario["events"]:
            old_id = event["target"]["airport_id"]
            event["target"]["airport_id"] = airport_map.get(old_id, old_id)
    return Situation.from_mapping(raw)


def _active_airport_ids(conn: sqlite3.Connection) -> set[str]:
    ids: set[str] = set()
    for table in _AIRPORT_REFERENCE_TABLES:
        column = "airport_id"
        ids.update(row[0] for row in conn.execute(f"SELECT DISTINCT {column} FROM {table}") if row[0])
    return ids


def migrate_project_identifiers(
    db_path: str | Path,
    *,
    mapping_path: str | Path,
) -> IdentifierMigrationReport:
    """Migrate mutable Base Data and Situation IDs in one deferred-FK transaction.

    RunSnapshot payloads, ``runs.situation_id`` and audit history are intentionally
    excluded: they are immutable historical authorities and must remain byte-for-byte
    traceable to the Run that produced them.
    """
    mapping = load_airport_id_map(mapping_path)
    initialize_database(db_path)
    situation_repo = SituationRepository(db_path)

    with situation_repo.connect() as read_conn:
        situation_rows = read_conn.execute(
            "SELECT situation_id FROM situations ORDER BY COALESCE(created_at, ''), situation_id"
        ).fetchall()
        existing_situation_ids = {row[0] for row in read_conn.execute("SELECT situation_id FROM situations")}
        active_airport_ids = _active_airport_ids(read_conn)

    missing = sorted(old for old in active_airport_ids if old.startswith("oa:") and old not in mapping)
    if missing:
        raise ValueError(f"airport ID mapping is missing active IDs: {missing[:5]}")
    unsupported = sorted(
        value
        for value in active_airport_ids
        if not value.startswith("oa:") and _AIRPORT_ID_RE.fullmatch(value) is None
    )
    if unsupported:
        raise ValueError(f"active airport IDs are outside the oa:/AP authority: {unsupported[:5]}")

    existing_numbers = [
        int(match.group(1))
        for value in existing_situation_ids
        if (match := _SITUATION_ID_RE.fullmatch(value)) is not None
    ]
    next_situation_number = max(existing_numbers, default=0) + 1
    situation_map: dict[str, str] = {}
    transformed: dict[str, Situation] = {}
    for row in situation_rows:
        old_id = row[0]
        if _SITUATION_ID_RE.fullmatch(old_id):
            new_id = old_id
        else:
            while f"ST{next_situation_number:03d}" in existing_situation_ids or f"ST{next_situation_number:03d}" in situation_map.values():
                next_situation_number += 1
            new_id = f"ST{next_situation_number:03d}"
            next_situation_number += 1
        situation_map[old_id] = new_id
        current = situation_repo.get_situation(old_id)
        if current is None:
            raise RuntimeError(f"Situation disappeared during migration planning: {old_id}")
        if old_id != new_id or any(
            item.airport_id.startswith("oa:")
            for item in current.airports
        ):
            transformed[old_id] = _replace_situation_ids(
                current,
                airport_map=mapping,
                situation_id=new_id,
            )

    airport_updates = {
        old: new
        for old, new in mapping.items()
        if old in active_airport_ids
    }
    airport_next = max(
        [int(match.group(1)) for value in active_airport_ids if (match := _AIRPORT_ID_RE.fullmatch(value))],
        default=0,
    ) + 1
    airport_next = max(airport_next, len(mapping) + 1)
    situation_updates = {
        old: new
        for old, new in situation_map.items()
        if old != new
    }

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute("CREATE TEMP TABLE airport_id_migration(old_id TEXT PRIMARY KEY, new_id TEXT NOT NULL UNIQUE)")
        conn.executemany("INSERT INTO airport_id_migration VALUES (?, ?)", airport_updates.items())
        conn.execute("CREATE TEMP TABLE situation_id_migration(old_id TEXT PRIMARY KEY, new_id TEXT NOT NULL UNIQUE)")
        conn.executemany("INSERT INTO situation_id_migration VALUES (?, ?)", situation_updates.items())

        airport_reference_rows = 0
        for table in _AIRPORT_REFERENCE_TABLES:
            result = conn.execute(
                f"UPDATE {table} SET airport_id = (SELECT new_id FROM airport_id_migration WHERE old_id = {table}.airport_id) "
                f"WHERE airport_id IN (SELECT old_id FROM airport_id_migration)"
            )
            airport_reference_rows += result.rowcount

        situation_reference_rows = 0
        for table in _SITUATION_REFERENCE_TABLES:
            result = conn.execute(
                f"UPDATE {table} SET situation_id = (SELECT new_id FROM situation_id_migration WHERE old_id = {table}.situation_id) "
                f"WHERE situation_id IN (SELECT old_id FROM situation_id_migration)"
            )
            situation_reference_rows += result.rowcount

        for old_id, migrated in transformed.items():
            conn.execute(
                "UPDATE situations SET content_hash = ? WHERE situation_id = ?",
                (migrated.content_hash(), situation_map[old_id]),
            )

        conn.execute(
            "INSERT INTO identifier_sequences(namespace, next_value) VALUES ('airport', ?) "
            "ON CONFLICT(namespace) DO UPDATE SET next_value=MAX(identifier_sequences.next_value, excluded.next_value)",
            (airport_next,),
        )
        conn.execute(
            "INSERT INTO identifier_sequences(namespace, next_value) VALUES ('situation', ?) "
            "ON CONFLICT(namespace) DO UPDATE SET next_value=MAX(identifier_sequences.next_value, excluded.next_value)",
            (next_situation_number,),
        )
        errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if errors:
            raise RuntimeError(f"identifier migration left foreign-key errors: {errors[:3]}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return IdentifierMigrationReport(
        airports_migrated=len(airport_updates),
        situations_migrated=len(situation_updates),
        airport_reference_rows=airport_reference_rows,
        situation_reference_rows=situation_reference_rows,
        airport_next_value=airport_next,
        situation_next_value=next_situation_number,
    )


__all__ = [
    "IdentifierMigrationReport",
    "backup_database",
    "load_airport_id_map",
    "migrate_project_identifiers",
]
