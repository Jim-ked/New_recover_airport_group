from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from http.cookies import SimpleCookie
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, quote, urlencode, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.domain.damage import (
    AircraftDamageEffect,
    CapacityDamageEffect,
    DamageScenario,
    ResourceDamageEffect,
)
from backend.domain.mission import Mission
from backend.auth.principal import Principal
from backend.runtime import build_application
from backend.services.run_result_service import RunResultService
from backend.services.run_runtime_service import RunRuntimeService
from backend.services.run_service import RunService
from backend.services.run_snapshot_service import RunSnapshotService
from backend.services.run_worker import RunWorker, RunWorkerError
from backend.settings import AppSettings
from backend.storage.airport_repository import AirportRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.situation_repository import SituationRepository
from backend.web.results_api import ResultsApi


DEMO_SITUATION_NAME = "江苏机场群标准验证情境"
# The exact, business-readable description is the stable ownership marker. Name alone
# is never sufficient because a user may independently choose the same visible name.
DEMO_DESCRIPTION = "用于机场群受损条件下组群选择、任务调度和结果比较的标准开发验证情境。"
DEFAULT_DB_PATH = (PROJECT_ROOT / "runtime" / "db" / "airport_group.sqlite3").resolve()
MAPPING_PATH = PROJECT_ROOT / "resources" / "migrations" / "airport_id_map_20260818.json"

# Source IDs are migration lookup keys only. Every Situation, Run and business payload
# constructed below uses the resolved AP identity.
SOURCE_AIRPORT_IDS = {
    "nanjing": "oa:27221",
    "xuzhou": "oa:32713",
    "nantong": "oa:32048",
    "suzhou": "oa:32420",
    "yancheng": "oa:35316",
    "sunan": "oa:32684",
}
EXPECTED_AIRPORT_NAMES = {
    "nanjing": "Nanjing Lukou International Airport",
    "xuzhou": "Xuzhou Guanyin International Airport",
    "nantong": "Nantong Xingdong International Airport",
    "suzhou": "Suzhou Guangfu Airport",
    "yancheng": "Yancheng Nanyang International Airport",
    "sunan": "Sunan Shuofang International Airport",
}

AIRCRAFT_TYPES = (
    {
        "aircraft_type_id": "fighter", "name": "战斗机", "speed_kmh": 800.0,
        "max_range_km": 1500.0, "reserve_ratio": 0.2,
        "departure_capacity_occupancy_factor": 1.0,
        "arrival_capacity_occupancy_factor": 1.0,
    },
    {
        "aircraft_type_id": "bomber", "name": "轰炸机", "speed_kmh": 800.0,
        "max_range_km": 2000.0, "reserve_ratio": 0.1,
        "departure_capacity_occupancy_factor": 1.3,
        "arrival_capacity_occupancy_factor": 1.3,
    },
    {
        "aircraft_type_id": "transport", "name": "运输机", "speed_kmh": 700.0,
        "max_range_km": 2500.0, "reserve_ratio": 0.1,
        "departure_capacity_occupancy_factor": 1.1,
        "arrival_capacity_occupancy_factor": 1.1,
    },
)

RESOURCE_TYPES = (
    {"resource_type_id": "fuel", "name": "航空燃油", "category": "fuel", "unit": "t"},
    {"resource_type_id": "MAT-1", "name": "通用航材", "category": "material", "unit": "t"},
    {"resource_type_id": "MAT-2", "name": "动力航材", "category": "material", "unit": "t"},
    {"resource_type_id": "MAT-3", "name": "电子航材", "category": "material", "unit": "t"},
    {"resource_type_id": "MUN-1", "name": "通用航弹", "category": "munition", "unit": "t"},
    {"resource_type_id": "MUN-2", "name": "重型航弹", "category": "munition", "unit": "t"},
)
RESOURCE_IDS = tuple(row["resource_type_id"] for row in RESOURCE_TYPES)

# Keep the established project consumption relationship; the new dataset creates
# readable trends through differentiated stocks, replenishment and damage facts.
AIRCRAFT_RESOURCE_REQUIREMENTS = {
    "fighter": (
        ("fuel", "per_hour", 1.0), ("MAT-1", "per_sortie", 1.0),
        ("MAT-2", "per_sortie", 1.0), ("MAT-3", "per_sortie", 1.0),
        ("MUN-1", "per_sortie", 2.0), ("MUN-2", "per_sortie", 1.0),
    ),
    "bomber": (
        ("fuel", "per_hour", 1.8), ("MAT-1", "per_sortie", 3.0),
        ("MAT-2", "per_sortie", 2.0), ("MUN-1", "per_sortie", 10.0),
        ("MUN-2", "per_sortie", 4.0),
    ),
    "transport": (
        ("fuel", "per_hour", 1.5), ("MAT-1", "per_sortie", 1.0),
        ("MAT-2", "per_sortie", 1.0), ("MUN-1", "per_sortie", 0.0),
        ("MUN-2", "per_sortie", 0.0),
    ),
}

PROFILE_SPECS = {
    "nanjing": {
        "capacity": 12, "support": {"fighter": (18, 1), "bomber": (8, 2), "transport": (6, 3)},
        "stocks": {"fuel": (360, 30), "MAT-1": (180, 12), "MAT-2": (160, 10), "MAT-3": (130, 8), "MUN-1": (220, 15), "MUN-2": (140, 10)},
    },
    "nantong": {
        "capacity": 10, "support": {"fighter": (15, 1), "bomber": (6, 2), "transport": (4, 3)},
        "stocks": {"fuel": (300, 22), "MAT-1": (150, 10), "MAT-2": (135, 8), "MAT-3": (120, 7), "MUN-1": (190, 12), "MUN-2": (110, 8)},
    },
    "sunan": {
        "capacity": 9, "support": {"fighter": (14, 1), "bomber": (5, 2), "transport": (4, 4)},
        "stocks": {"fuel": (280, 20), "MAT-1": (160, 10), "MAT-2": (145, 8), "MAT-3": (115, 6), "MUN-1": (175, 10), "MUN-2": (105, 7)},
    },
    "yancheng": {
        "capacity": 8, "support": {"fighter": (12, 2), "bomber": (4, 3), "transport": (5, 3)},
        "stocks": {"fuel": (310, 18), "MAT-1": (145, 8), "MAT-2": (130, 7), "MAT-3": (120, 6), "MUN-1": (155, 10), "MUN-2": (100, 8)},
    },
    "xuzhou": {
        "capacity": 7, "support": {"fighter": (11, 2), "bomber": (3, 3), "transport": (3, 4)},
        "stocks": {"fuel": (260, 16), "MAT-1": (140, 8), "MAT-2": (125, 7), "MAT-3": (105, 5), "MUN-1": (145, 9), "MUN-2": (90, 6)},
    },
    "suzhou": {
        "capacity": 5, "support": {"fighter": (8, 2), "transport": (2, 5)},
        "stocks": {"fuel": (200, 12), "MAT-1": (110, 6), "MAT-2": (100, 5), "MAT-3": (90, 4), "MUN-1": (110, 6), "MUN-2": (65, 4)},
    },
}

REPLENISHMENTS = {
    "nanjing": (("fuel", 22, 24), ("MUN-1", 24, 12)),
    "nantong": (("MAT-1", 26, 8),),
    "sunan": (("fuel", 28, 18),),
    "yancheng": (("MUN-2", 30, 8),),
}

MISSIONS = (
    {
        "mission_id": "M001", "name": "东部任务区", "longitude": 121.45, "latitude": 32.55,
        "window_start_slot": 12, "window_end_slot": 26,
        "aircraft_requirements": [
            {"aircraft_type_id": "fighter", "required_sorties": 26, "tau_work_windows": 1},
            {"aircraft_type_id": "bomber", "required_sorties": 8, "tau_work_windows": 2},
            {"aircraft_type_id": "transport", "required_sorties": 5, "tau_work_windows": 1},
        ],
    },
    {
        "mission_id": "M002", "name": "中部任务区", "longitude": 119.65, "latitude": 32.05,
        "window_start_slot": 18, "window_end_slot": 34,
        "aircraft_requirements": [
            {"aircraft_type_id": "fighter", "required_sorties": 30, "tau_work_windows": 1},
            {"aircraft_type_id": "bomber", "required_sorties": 10, "tau_work_windows": 2},
            {"aircraft_type_id": "transport", "required_sorties": 7, "tau_work_windows": 1},
        ],
    },
    {
        "mission_id": "M003", "name": "北部任务区", "longitude": 118.75, "latitude": 33.45,
        "window_start_slot": 22, "window_end_slot": 38,
        "aircraft_requirements": [
            {"aircraft_type_id": "fighter", "required_sorties": 24, "tau_work_windows": 1},
            {"aircraft_type_id": "bomber", "required_sorties": 7, "tau_work_windows": 2},
            {"aircraft_type_id": "transport", "required_sorties": 6, "tau_work_windows": 1},
        ],
    },
)
SCENARIOS = ("DS-LOW", "DS-MEDIUM", "DS-HIGH")


@dataclass(frozen=True)
class WorkspaceInspection:
    database_path: Path
    airport_count: int
    situation_count: int
    run_count: int
    demo_situation_count: int
    demo_situation_id: str | None
    demo_owner_user_id: str | None
    demo_run_count: int
    demo_collision_count: int
    demo_collision_ids: tuple[str, ...]


class DemoCollisionError(RuntimeError):
    pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the standard Jiangsu demo Situation and its seven comparison Runs."
    )
    parser.add_argument(
        "--apply-default-db",
        action="store_true",
        help="allow writes to runtime/db/airport_group.sqlite3",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare-only", dest="mode", action="store_const", const="prepare")
    modes.add_argument("--run", dest="mode", action="store_const", const="run")
    modes.add_argument("--rebuild", dest="mode", action="store_const", const="rebuild")
    modes.add_argument(
        "--verify-existing",
        dest="mode",
        action="store_const",
        const="verify",
        help="read-only verification of the existing standard seven-Run workspace",
    )
    parser.set_defaults(mode="rebuild")
    return parser.parse_args(argv)


def _classify_demo_situations(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, tuple[str, ...]]:
    same_name = [row for row in rows if row.get("name") == DEMO_SITUATION_NAME]
    owned = [row for row in same_name if row.get("description") == DEMO_DESCRIPTION]
    collisions = tuple(
        sorted(
            str(row.get("situation_id") or "")
            for row in same_name
            if row.get("description") != DEMO_DESCRIPTION
        )
    )
    if len(owned) > 1:
        raise RuntimeError("multiple standard demo Situations exist; refusing to choose one")
    return (owned[0] if owned else None), collisions


def find_demo_situation(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    demo, collisions = _classify_demo_situations(rows)
    if collisions:
        raise DemoCollisionError(
            "same-name Situation lacks the standard demo ownership marker: "
            f"{list(collisions)}"
        )
    return demo


def inspect_workspace(db_path: str | Path) -> WorkspaceInspection:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"database not found: {path}")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        airports = int(connection.execute("SELECT COUNT(*) FROM airports").fetchone()[0])
        situations = int(connection.execute("SELECT COUNT(*) FROM situations").fetchone()[0])
        runs = int(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
        demo_rows = connection.execute(
            "SELECT situation_id, name, description, owner_user_id "
            "FROM situations WHERE name=? ORDER BY situation_id",
            (DEMO_SITUATION_NAME,),
        ).fetchall()
        demo, collision_ids = _classify_demo_situations([dict(row) for row in demo_rows])
        demo_id = None if demo is None else str(demo["situation_id"])
        demo_owner = None if demo is None else str(demo.get("owner_user_id") or "").strip() or None
        demo_runs = 0
        if demo_id is not None:
            demo_runs = int(
                connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE situation_id=?", (demo_id,)
                ).fetchone()[0]
            )
        return WorkspaceInspection(
            database_path=path,
            airport_count=airports,
            situation_count=situations,
            run_count=runs,
            demo_situation_count=1 if demo is not None else 0,
            demo_situation_id=demo_id,
            demo_owner_user_id=demo_owner,
            demo_run_count=demo_runs,
            demo_collision_count=len(collision_ids),
            demo_collision_ids=collision_ids,
        )
    finally:
        connection.close()


def require_demo_apply_safe(inspection: WorkspaceInspection) -> None:
    if inspection.demo_collision_count:
        raise DemoCollisionError(
            "refusing default-database writes while same-name non-demo Situations exist: "
            f"{list(inspection.demo_collision_ids)}"
        )


class ApiFailure(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: Any):
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"{method} {path} failed ({status}): {body}")


class DemoClient:
    def __init__(self, app: Any) -> None:
        self.client = app.test_client()
        self.csrf: str | None = None
        self.user_id: str | None = None

    @staticmethod
    def _body(response: Any) -> Any:
        value = response.get_json(silent=True)
        return value if value is not None else response.get_data(as_text=True)

    def login(self, username: str, password: str) -> None:
        response = self.client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        body = self._body(response)
        if response.status_code != 200:
            raise ApiFailure("POST", "/api/auth/login", response.status_code, body)
        self.user_id = str(body.get("user_id") or "").strip() or None
        cookie = self.client.get_cookie("csrftoken") if hasattr(self.client, "get_cookie") else None
        if cookie is not None:
            self.csrf = getattr(cookie, "value", None) or str(cookie)
        if not self.csrf:
            for raw in response.headers.getlist("Set-Cookie"):
                parsed = SimpleCookie(); parsed.load(raw)
                if "csrftoken" in parsed:
                    self.csrf = parsed["csrftoken"].value
                    break
        if not self.user_id or not self.csrf:
            raise RuntimeError("login succeeded without user_id/csrftoken")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        headers = {}
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        response = self.client.open(path, method=method.upper(), json=body, headers=headers)
        parsed = self._body(response)
        if response.status_code not in expected:
            raise ApiFailure(method.upper(), path, response.status_code, parsed)
        return parsed


class ReadOnlyResultsClient:
    """Route the tool's comparison checks through the formal Results API without Flask.

    This adapter deliberately exposes only read-only Results endpoints.  It lets
    ``--verify-existing`` validate the default database without application startup,
    authentication writes, schema initialization, or a Solver-capable Run API.
    """

    def __init__(self, api: ResultsApi, *, principal: Principal) -> None:
        self.api = api
        self.principal = principal

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        method = method.upper()
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        if method == "GET" and parsed.path == "/api/results/damage-candidates":
            response = self.api.damage_candidates(principal=self.principal)
        elif method == "GET" and parsed.path == "/api/results/comparable-runs":
            response = self.api.comparable_runs(
                principal=self.principal,
                base_run_id=(query.get("base_run_id") or [None])[0],
                mode=(query.get("mode") or [None])[0],
            )
        elif method == "POST" and parsed.path == "/api/results/damage-comparison":
            response = self.api.damage_comparison(body, principal=self.principal)
        elif method == "POST" and parsed.path == "/api/results/scenario-comparison":
            response = self.api.scenario_comparison(body, principal=self.principal)
        elif method == "POST" and parsed.path == "/api/results/config-comparison":
            response = self.api.configuration_comparison(body, principal=self.principal)
        else:
            raise RuntimeError(f"unsupported read-only Results request: {method} {path}")
        if response.status not in expected:
            raise ApiFailure(method, path, response.status, response.body)
        return response.body


def resolve_airport_ids() -> dict[str, str]:
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    resolved = {}
    for role, source_id in SOURCE_AIRPORT_IDS.items():
        airport_id = mapping.get(source_id)
        if not isinstance(airport_id, str) or not airport_id.startswith("AP"):
            raise RuntimeError(f"airport mapping is missing {role}: {source_id}")
        resolved[role] = airport_id
    if len(set(resolved.values())) != len(resolved):
        raise RuntimeError("resolved demo airport IDs are not unique")
    return resolved


def operational_profile(role: str, airport_ids: Mapping[str, str]) -> dict[str, Any]:
    spec = PROFILE_SPECS[role]
    return {
        "airport_id": airport_ids[role],
        "configuration_complete": True,
        "capacity_per_window": spec["capacity"],
        "support_level": "标准验证",
        "aircraft_support": [
            {
                "aircraft_type_id": aircraft_id,
                "initial_quantity": values[0],
                "tau_reset_windows": values[1],
            }
            for aircraft_id, values in spec["support"].items()
        ],
        "resource_stocks": [
            {
                "resource_type_id": resource_id,
                "initial_quantity": spec["stocks"][resource_id][0],
                "replenishment_capacity_per_window": spec["stocks"][resource_id][1],
            }
            for resource_id in RESOURCE_IDS
        ],
    }


def damage_scenarios(airport_ids: Mapping[str, str]) -> tuple[dict[str, Any], ...]:
    return (
        {
            "damage_scenario_id": "DS-LOW", "name": "轻度损毁", "category": "low",
            "events": [{
                "event_id": "LOW-CAP-SUNAN", "sequence": 0,
                "target": {"airport_id": airport_ids["sunan"], "target_type": "airport", "target_id": None},
                "damage_type": "capacity_damage", "start_slot": 16, "end_slot": 27,
                "effect": {"closed": False, "remaining_capacity_per_window": 5},
                "recovery_mode": "instant", "recovery_duration_slots": None,
            }],
        },
        {
            "damage_scenario_id": "DS-MEDIUM", "name": "中度损毁", "category": "medium",
            "events": [
                {
                    "event_id": "MED-AIRCRAFT-NANJING", "sequence": 0,
                    "target": {"airport_id": airport_ids["nanjing"], "target_type": "airport", "target_id": None},
                    "damage_type": "aircraft_damage", "start_slot": 18, "end_slot": 19,
                    "effect": {"aircraft_loss": {"fighter": 6, "bomber": 2}},
                    "recovery_mode": "none", "recovery_duration_slots": None,
                },
                {
                    "event_id": "MED-CAP-YANCHENG", "sequence": 1,
                    "target": {"airport_id": airport_ids["yancheng"], "target_type": "airport", "target_id": None},
                    "damage_type": "capacity_damage", "start_slot": 21, "end_slot": 34,
                    "effect": {"closed": False, "remaining_capacity_per_window": 4},
                    "recovery_mode": "instant", "recovery_duration_slots": None,
                },
            ],
        },
        {
            "damage_scenario_id": "DS-HIGH", "name": "重度损毁", "category": "high",
            "events": [
                {
                    "event_id": "HIGH-AIRCRAFT-NANTONG", "sequence": 0,
                    "target": {"airport_id": airport_ids["nantong"], "target_type": "airport", "target_id": None},
                    "damage_type": "aircraft_damage", "start_slot": 17, "end_slot": 18,
                    "effect": {"aircraft_loss": {"fighter": 7, "bomber": 3}},
                    "recovery_mode": "none", "recovery_duration_slots": None,
                },
                {
                    "event_id": "HIGH-CAP-NANJING", "sequence": 1,
                    "target": {"airport_id": airport_ids["nanjing"], "target_type": "airport", "target_id": None},
                    "damage_type": "capacity_damage", "start_slot": 20, "end_slot": 36,
                    "effect": {"closed": False, "remaining_capacity_per_window": 4},
                    "recovery_mode": "instant", "recovery_duration_slots": None,
                },
                {
                    "event_id": "HIGH-RESOURCE-YANCHENG", "sequence": 2,
                    "target": {"airport_id": airport_ids["yancheng"], "target_type": "airport", "target_id": None},
                    "damage_type": "resource_damage", "start_slot": 23, "end_slot": 38,
                    "effect": {"remaining_quantity": {"fuel": 85.0, "MUN-1": 55.0, "MUN-2": 35.0}},
                    "recovery_mode": "instant", "recovery_duration_slots": None,
                },
            ],
        },
    )


def validate_static_definition(airport_ids: Mapping[str, str]) -> None:
    if set(airport_ids) != set(PROFILE_SPECS):
        raise RuntimeError("airport roles and profile definitions differ")
    aircraft = [AircraftType.from_mapping(dict(row)) for row in AIRCRAFT_TYPES]
    resources = [ResourceType.from_mapping(dict(row)) for row in RESOURCE_TYPES]
    aircraft_ids = {row.aircraft_type_id for row in aircraft}
    resource_ids = {row.resource_type_id for row in resources}
    if resource_ids != set(RESOURCE_IDS):
        raise RuntimeError("resource definitions are inconsistent")
    for aircraft_id, rows in AIRCRAFT_RESOURCE_REQUIREMENTS.items():
        for resource_id, basis, quantity in rows:
            AircraftResourceRequirement.from_mapping({
                "aircraft_type_id": aircraft_id,
                "resource_type_id": resource_id,
                "basis": basis,
                "quantity": quantity,
            })
    if set(AIRCRAFT_RESOURCE_REQUIREMENTS) != aircraft_ids:
        raise RuntimeError("aircraft resource requirements do not cover the catalog")
    missions = [Mission.from_mapping(dict(row)) for row in MISSIONS]
    if len({row.mission_id for row in missions}) != 3:
        raise RuntimeError("demo must contain exactly three unique missions")
    profiles = {
        role: AirportOperationalProfile.from_mapping(operational_profile(role, airport_ids))
        for role in airport_ids
    }
    for role, rows in REPLENISHMENTS.items():
        profile = profiles[role]
        capacities = {
            row.resource_type_id: float(row.replenishment_capacity_per_window or 0)
            for row in profile.resource_stocks
        }
        for resource_id, _slot, quantity in rows:
            if quantity > capacities.get(resource_id, 0):
                raise RuntimeError(f"replenishment exceeds capacity: {role}/{resource_id}")
    mission_start = min(row.window_start_slot for row in missions)
    mission_end = max(row.window_end_slot for row in missions)
    for scenario in (DamageScenario.from_mapping(row) for row in damage_scenarios(airport_ids)):
        for event in scenario.events:
            if not (mission_start <= event.start_slot < event.end_slot <= mission_end):
                raise RuntimeError(f"damage event outside mission envelope: {event.event_id}")
            role = next(key for key, value in airport_ids.items() if value == event.target.airport_id)
            profile = profiles[role]
            if isinstance(event.effect, CapacityDamageEffect):
                if event.effect.remaining_capacity_per_window >= int(profile.capacity_per_window or 0):
                    raise RuntimeError(f"capacity damage is not effective: {event.event_id}")
            if isinstance(event.effect, AircraftDamageEffect):
                available = {row.aircraft_type_id: int(row.initial_quantity or 0) for row in profile.aircraft_support}
                if any(loss > available.get(kind, 0) for kind, loss in event.effect.aircraft_loss):
                    raise RuntimeError(f"aircraft loss exceeds inventory: {event.event_id}")
            if isinstance(event.effect, ResourceDamageEffect):
                available = {row.resource_type_id: float(row.initial_quantity or 0) for row in profile.resource_stocks}
                if any(value > available.get(resource_id, -1) for resource_id, value in event.effect.remaining_quantity):
                    raise RuntimeError(f"resource damage exceeds stock: {event.event_id}")


def credentials() -> tuple[str, str]:
    username = os.environ.get("AIRPORT_GROUP_VALIDATION_USERNAME", "").strip()
    password = os.environ.get("AIRPORT_GROUP_VALIDATION_PASSWORD", "")
    if not username or not password:
        raise RuntimeError(
            "set AIRPORT_GROUP_VALIDATION_USERNAME and AIRPORT_GROUP_VALIDATION_PASSWORD"
        )
    return username, password


def backup_default_database(db_path: Path) -> Path:
    target_dir = db_path.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_dir / f"airport_group_before_demo_situation_{stamp}.sqlite3"
    suffix = 1
    while target.exists():
        target = target_dir / f"airport_group_before_demo_situation_{stamp}_{suffix}.sqlite3"
        suffix += 1
    source = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()
    return target


def _without_name(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "name"}


def print_catalog_differences(db_path: Path) -> None:
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for desired in AIRCRAFT_TYPES:
            row = connection.execute(
                "SELECT name FROM aircraft_types WHERE aircraft_type_id=?",
                (desired["aircraft_type_id"],),
            ).fetchone()
            if row is None:
                print(f"[PLAN] create aircraft {desired['aircraft_type_id']} ({desired['name']})")
            elif row["name"] != desired["name"]:
                print(
                    f"[PLAN] aircraft display name {desired['aircraft_type_id']}: "
                    f"{row['name']} -> {desired['name']}"
                )
        for desired in RESOURCE_TYPES:
            row = connection.execute(
                "SELECT name FROM resource_types WHERE resource_type_id=?",
                (desired["resource_type_id"],),
            ).fetchone()
            if row is None:
                print(f"[PLAN] create resource {desired['resource_type_id']} ({desired['name']})")
            elif row["name"] != desired["name"]:
                print(
                    f"[PLAN] resource display name {desired['resource_type_id']}: "
                    f"{row['name']} -> {desired['name']}"
                )
        for mission in MISSIONS:
            row = connection.execute(
                "SELECT name FROM mission_records WHERE mission_id=?", (mission["mission_id"],)
            ).fetchone()
            if row is None:
                print(f"[PLAN] create mission {mission['mission_id']} ({mission['name']})")
            elif row["name"] != mission["name"]:
                print(
                    f"[BLOCK] mission ID {mission['mission_id']} already has name {row['name']}"
                )
    finally:
        connection.close()


def prepare_catalogs(api: DemoClient) -> None:
    current_resources = api.request("GET", "/api/resource-types").get("items", [])
    resources_by_id = {row["resource_type"]["resource_type_id"]: row for row in current_resources}
    for desired in RESOURCE_TYPES:
        resource_id = desired["resource_type_id"]
        current = resources_by_id.get(resource_id)
        if current is None:
            api.request(
                "POST", "/api/resource-types", body={"resource_type": desired}, expected=(201,)
            )
        else:
            raw = current["resource_type"]
            if _without_name(raw) != _without_name(desired):
                raise RuntimeError(
                    f"resource {resource_id} has non-display differences; refusing to overwrite"
                )
            if raw != desired:
                print(f"[DIFF] resource {resource_id} display name: {raw['name']} -> {desired['name']}")
                api.request(
                    "PUT", f"/api/resource-types/{quote(resource_id, safe='')}",
                    body={
                        "resource_type": desired,
                        "expected_revision": current["metadata"]["revision"],
                    },
                )

    current_aircraft = api.request("GET", "/api/aircraft-types").get("items", [])
    aircraft_by_id = {row["aircraft_type"]["aircraft_type_id"]: row for row in current_aircraft}
    revisions: dict[str, int] = {}
    for desired in AIRCRAFT_TYPES:
        aircraft_id = desired["aircraft_type_id"]
        current = aircraft_by_id.get(aircraft_id)
        if current is None:
            result = api.request(
                "POST", "/api/aircraft-types", body={"aircraft_type": desired}, expected=(201,)
            )
        else:
            raw = current["aircraft_type"]
            if _without_name(raw) != _without_name(desired):
                raise RuntimeError(
                    f"aircraft {aircraft_id} has non-display differences; refusing to overwrite"
                )
            result = current
            if raw != desired:
                print(f"[DIFF] aircraft {aircraft_id} display name: {raw['name']} -> {desired['name']}")
                result = api.request(
                    "PUT", f"/api/aircraft-types/{quote(aircraft_id, safe='')}",
                    body={
                        "aircraft_type": desired,
                        "expected_revision": current["metadata"]["revision"],
                    },
                )
        revisions[aircraft_id] = int(result["metadata"]["revision"])

    existing_requirements = api.request(
        "GET", "/api/aircraft-resource-requirements"
    ).get("items", [])
    current_by_aircraft: dict[str, list[dict[str, Any]]] = {}
    for row in existing_requirements:
        current_by_aircraft.setdefault(row["aircraft_type_id"], []).append(row)
    for aircraft_id, definitions in AIRCRAFT_RESOURCE_REQUIREMENTS.items():
        desired = [
            {
                "aircraft_type_id": aircraft_id,
                "resource_type_id": resource_id,
                "basis": basis,
                "quantity": float(quantity),
            }
            for resource_id, basis, quantity in definitions
        ]
        key = lambda row: (row["resource_type_id"], row["basis"], float(row["quantity"]))
        current = current_by_aircraft.get(aircraft_id, [])
        if current and sorted(current, key=key) != sorted(desired, key=key):
            raise RuntimeError(
                f"aircraft requirements for {aircraft_id} differ from the established project defaults"
            )
        if not current:
            result = api.request(
                "PUT",
                f"/api/aircraft-types/{quote(aircraft_id, safe='')}/resource-requirements",
                body={"requirements": desired, "expected_revision": revisions[aircraft_id]},
            )
            revisions[aircraft_id] = int(result["metadata"]["revision"])


def verify_airport_authority(api: DemoClient, airport_ids: Mapping[str, str]) -> None:
    for role, airport_id in airport_ids.items():
        detail = api.request("GET", f"/api/airports/{quote(airport_id, safe='')}")
        actual_name = detail["airport"]["airport_name"]
        if actual_name != EXPECTED_AIRPORT_NAMES[role]:
            raise RuntimeError(
                f"airport mapping/name mismatch for {role}: {airport_id} is {actual_name}"
            )


def update_airport_profiles(api: DemoClient, airport_ids: Mapping[str, str]) -> None:
    for role, airport_id in airport_ids.items():
        path = f"/api/airports/{quote(airport_id, safe='')}"
        detail = api.request("GET", path)
        api.request(
            "PUT",
            path,
            body={
                "airport": detail["airport"],
                "operational_profile": operational_profile(role, airport_ids),
                "expected_revision": detail["metadata"]["revision"],
            },
        )
        print(f"[OK] profile {airport_id} {EXPECTED_AIRPORT_NAMES[role]}")


def _canonical_mission_value(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = Mission.from_mapping(raw).to_dict()
    value["aircraft_requirements"] = sorted(
        value["aircraft_requirements"],
        key=lambda row: row["aircraft_type_id"],
    )
    return value


def _mission_collision_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = _canonical_mission_value(raw)
    return {
        "mission_id": value["mission_id"],
        "name": value["name"],
        "longitude": value["longitude"],
        "latitude": value["latitude"],
        "window": [value["window_start_slot"], value["window_end_slot"]],
        "aircraft_requirements": value["aircraft_requirements"],
    }


def _legacy_demo_mission_is_proven(
    existing: Mapping[str, Any],
    *,
    demo_detail: Mapping[str, Any] | None,
    owner_user_id: str | None,
) -> bool:
    if not demo_detail or demo_detail.get("owner_user_id") != owner_user_id:
        return False
    situation = demo_detail.get("situation") or {}
    if (
        situation.get("name") != DEMO_SITUATION_NAME
        or situation.get("description") != DEMO_DESCRIPTION
    ):
        return False
    mission_id = existing.get("mission_id")
    frozen = next(
        (
            row
            for row in situation.get("missions") or []
            if row.get("mission_id") == mission_id
        ),
        None,
    )
    return frozen is not None and _canonical_mission_value(frozen) == _canonical_mission_value(existing)


def prepare_missions(
    api: DemoClient,
    *,
    rebuild: bool,
    demo_detail: Mapping[str, Any] | None,
) -> None:
    for desired in MISSIONS:
        mission_id = desired["mission_id"]
        path = f"/api/missions/{quote(mission_id, safe='')}"
        try:
            current = api.request("GET", path)
        except ApiFailure as exc:
            if exc.status != 404:
                raise
            current = None
        if current is None:
            api.request("POST", "/api/missions", body={"mission": desired}, expected=(201,))
            print(f"[OK] mission created {mission_id} {desired['name']}")
            continue
        existing = current["mission"]
        if _canonical_mission_value(existing) == _canonical_mission_value(desired):
            print(f"[OK] mission reused {mission_id} {desired['name']}")
            continue
        if rebuild and _legacy_demo_mission_is_proven(
            existing,
            demo_detail=demo_detail,
            owner_user_id=api.user_id,
        ):
            api.request(
                "PUT",
                path,
                body={
                    "mission": desired,
                    "expected_revision": current["metadata"]["revision"],
                },
            )
            print(f"[OK] legacy Demo mission updated {mission_id} {desired['name']}")
            continue
        existing_summary = json.dumps(
            _mission_collision_summary(existing), ensure_ascii=False, sort_keys=True
        )
        desired_summary = json.dumps(
            _mission_collision_summary(desired), ensure_ascii=False, sort_keys=True
        )
        raise RuntimeError(
            f"mission {mission_id} collision:\n"
            f"existing = {existing_summary}\n"
            f"desired  = {desired_summary}"
        )


def find_visible_demo(api: DemoClient) -> Mapping[str, Any] | None:
    result = api.request(
        "GET", f"/api/situations?{urlencode({'q': DEMO_SITUATION_NAME, 'limit': 500})}"
    )
    demo = find_demo_situation(result.get("items", []))
    if demo is not None and demo.get("owner_user_id") != api.user_id:
        raise RuntimeError(
            "standard demo Situation belongs to another user; refusing to modify it"
        )
    return demo


def _apply_replenishments(working: dict[str, Any], airport_ids: Mapping[str, str]) -> None:
    role_by_id = {airport_id: role for role, airport_id in airport_ids.items()}
    for row in working["airports"]:
        role = role_by_id[row["airport"]["airport_id"]]
        row["resource_replenishments"] = [
            {"resource_type_id": resource_id, "slot": slot, "quantity": quantity}
            for resource_id, slot, quantity in REPLENISHMENTS.get(role, ())
        ]


def prepare_situation(api: DemoClient, airport_ids: Mapping[str, str]) -> dict[str, Any]:
    existing = find_visible_demo(api)
    if existing is None:
        allocated = api.request("POST", "/api/situations/allocate-id", body={}, expected=(201,))
        situation_id = allocated["situation_id"]
        existing_detail = None
    else:
        situation_id = existing["situation_id"]
        existing_detail = api.request(
            "GET", f"/api/situations/{quote(situation_id, safe='')}"
        )

    working: dict[str, Any] = {
        "situation_id": situation_id,
        "name": DEMO_SITUATION_NAME,
        "description": DEMO_DESCRIPTION,
        "airports": [],
        "missions": [],
        "damage_scenarios": [],
    }
    for airport_id in airport_ids.values():
        result = api.request(
            "POST",
            "/api/situations/working-copy/copy-airport",
            body={"situation": working, "airport_id": airport_id},
        )
        working = result["situation"]
    _apply_replenishments(working, airport_ids)
    for mission in MISSIONS:
        result = api.request(
            "POST",
            "/api/situations/working-copy/copy-mission",
            body={"situation": working, "mission_id": mission["mission_id"]},
        )
        working = result["situation"]
    working["damage_scenarios"] = list(damage_scenarios(airport_ids))
    working = api.request(
        "POST", "/api/situations/working-copy/canonicalize", body={"situation": working}
    )["situation"]

    if existing_detail is None:
        saved = api.request(
            "POST", "/api/situations", body={"situation": working}, expected=(201,)
        )
        operation = "created"
    else:
        saved = api.request(
            "PUT",
            f"/api/situations/{quote(situation_id, safe='')}",
            body={
                "situation": working,
                "expected_content_hash": existing_detail["content_hash"],
            },
        )
        operation = "updated"
    detail = api.request("GET", f"/api/situations/{quote(situation_id, safe='')}")
    if detail["content_hash"] != saved["content_hash"]:
        raise RuntimeError("Situation content hash changed on immediate read-back")
    payload = detail["situation"]
    if len(payload["airports"]) != 6 or len(payload["missions"]) != 3 or len(payload["damage_scenarios"]) != 3:
        raise RuntimeError("saved standard Situation has an unexpected aggregate size")
    serialized = json.dumps(payload, ensure_ascii=False)
    if "oa:" in serialized or not situation_id.startswith("ST"):
        raise RuntimeError("saved standard Situation violates AP/ST identity authority")
    if {row["airport"]["airport_id"] for row in payload["airports"]} != set(airport_ids.values()):
        raise RuntimeError("saved standard Situation has an unexpected airport set")
    print(f"[OK] Situation {operation}: {situation_id} {DEMO_SITUATION_NAME}")
    return detail


def build_worker(db_path: Path) -> RunWorker:
    airports = AirportRepository(db_path)
    situations = SituationRepository(db_path)
    snapshots = RunSnapshotRepository(db_path)
    runs = RunRepository(db_path)
    snapshot_service = RunSnapshotService(
        airport_repository=airports,
        situation_repository=situations,
        snapshot_repository=snapshots,
    )
    run_service = RunService(snapshot_service=snapshot_service, run_repository=runs)
    result_service = RunResultService(run_repository=runs, snapshot_repository=snapshots)
    return RunWorker(
        run_service=run_service,
        result_service=result_service,
        snapshot_repository=snapshots,
    )


def run_config(
    *, damage_scenario_id: str | None, cluster_enabled: bool, core_airport_id: str
) -> dict[str, Any]:
    return {
        "damage_scenario_id": damage_scenario_id,
        "preference_mode": "sortie_max",
        "cluster_enabled": cluster_enabled,
        "cluster_size": 4 if cluster_enabled else None,
        "core_airports": [core_airport_id] if cluster_enabled else [],
        "aircraft_type_weight": {"fighter": 1.0, "bomber": 1.0, "transport": 0.9},
        "mip_time_limit_s": 120.0,
        "algorithm_seed": 42,
    }


def run_matrix() -> tuple[tuple[str, str | None, bool], ...]:
    rows: list[tuple[str, str | None, bool]] = [("R0", None, False)]
    for scenario in SCENARIOS:
        label = scenario.removeprefix("DS-")
        rows.extend(((f"{label}-R1", scenario, False), (f"{label}-R2", scenario, True)))
    return tuple(rows)


def identify_standard_run_roles(
    rows: Sequence[Mapping[str, Any]],
    *,
    situation_id: str,
    owner_user_id: str,
    core_airport_id: str,
) -> dict[str, str]:
    """Identify the canonical seven Runs from frozen config facts, never chronology."""
    expected_roles = {label for label, _damage, _cluster in run_matrix()}
    if len(rows) != len(expected_roles):
        raise RuntimeError(
            f"standard demo requires exactly {len(expected_roles)} Runs; found {len(rows)}"
        )

    roles: dict[str, str] = {}
    for row in rows:
        run_id = str(row.get("run_id") or "").strip()
        if not run_id:
            raise RuntimeError("standard demo contains a Run without run_id")
        if row.get("status") != "succeeded":
            raise RuntimeError(f"standard demo Run {run_id} is not succeeded")
        if row.get("situation_id") != situation_id:
            raise RuntimeError(f"standard demo Run {run_id} belongs to another Situation")
        if row.get("owner_user_id") != owner_user_id:
            raise RuntimeError(f"standard demo Run {run_id} belongs to another owner")

        snapshot = row.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise RuntimeError(f"standard demo Run {run_id} is missing its frozen Snapshot")
        if snapshot.get("run_id") != run_id:
            raise RuntimeError(f"standard demo Run {run_id} has a mismatched Snapshot run_id")
        snapshot_situation = snapshot.get("situation") or {}
        if not isinstance(snapshot_situation, Mapping) or snapshot_situation.get("situation_id") != situation_id:
            raise RuntimeError(f"standard demo Run {run_id} has a mismatched Snapshot Situation")

        config = snapshot.get("run_config")
        if not isinstance(config, Mapping):
            raise RuntimeError(f"standard demo Run {run_id} is missing frozen run_config")
        damage_id = config.get("damage_scenario_id")
        cluster_enabled = config.get("cluster_enabled")
        if not isinstance(cluster_enabled, bool):
            raise RuntimeError(f"standard demo Run {run_id} has invalid cluster_enabled")
        if damage_id is None and not cluster_enabled:
            label = "R0"
        elif damage_id in SCENARIOS:
            label = f"{str(damage_id).removeprefix('DS-')}-R{2 if cluster_enabled else 1}"
        else:
            raise RuntimeError(
                f"standard demo Run {run_id} has an unrecognized role config: "
                f"damage_scenario_id={damage_id!r}, cluster_enabled={cluster_enabled!r}"
            )

        expected_config = run_config(
            damage_scenario_id=damage_id,
            cluster_enabled=cluster_enabled,
            core_airport_id=core_airport_id,
        )
        mismatched = {
            key: {"expected": value, "actual": config.get(key)}
            for key, value in expected_config.items()
            if config.get(key) != value
        }
        if mismatched:
            raise RuntimeError(
                f"standard demo Run {run_id} ({label}) run_config mismatch: {mismatched}"
            )
        if label in roles:
            raise RuntimeError(
                f"standard demo role {label} is duplicated by {roles[label]} and {run_id}"
            )
        roles[label] = run_id

    missing = sorted(expected_roles - set(roles))
    if missing:
        raise RuntimeError(f"standard demo Run roles are missing: {missing}")
    return {label: roles[label] for label, _damage, _cluster in run_matrix()}


def validate_run_matrix(
    api: DemoClient, *, situation_id: str, core_airport_id: str
) -> None:
    for label, damage_id, cluster in run_matrix():
        validation = api.request(
            "POST",
            "/api/runs/validate",
            body={
                "situation_id": situation_id,
                "run_config": run_config(
                    damage_scenario_id=damage_id,
                    cluster_enabled=cluster,
                    core_airport_id=core_airport_id,
                ),
            },
        )
        if not validation.get("can_submit"):
            raise RuntimeError(f"{label} preflight failed: {validation}")
        fingerprint = validation.get("validated_input_hash")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise RuntimeError(f"{label} preflight returned an invalid input hash")
    print("[OK] all seven Run configurations pass submission preflight")


def clean_demo_runs(
    db_path: Path,
    *,
    owner_user_id: str,
    situation_id: str,
) -> int:
    repository = RunRepository(db_path)
    rows, total = repository.search_for_owner(
        owner_user_id,
        situation_id=situation_id,
        limit=500,
        offset=0,
    )
    if total > 500:
        raise RuntimeError("standard demo Situation has more than 500 Runs; refusing bulk cleanup")
    active = [row.run_id for row in rows if row.status in {"queued", "running"}]
    if active:
        raise RuntimeError(f"standard demo Situation has active Runs: {active}")
    if not rows:
        return 0
    return repository.delete_terminal_batch(
        run_ids=[row.run_id for row in rows],
        owner_user_id=owner_user_id,
        situation_id=situation_id,
    )


def _sum_metric_rows(rows: Mapping[str, Any], field: str) -> int:
    return sum(int((row or {}).get(field) or 0) for row in rows.values())


def assert_run_integrity(
    *,
    label: str,
    run_id: str,
    situation_id: str,
    detail: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    solution: Mapping[str, Any],
    metrics: Mapping[str, Any],
    runtime: Mapping[str, Any],
    events: Mapping[str, Any],
) -> None:
    if detail.get("run_id") != run_id or detail.get("status") != "succeeded":
        raise RuntimeError(f"{label} detail/status mismatch")
    for name, payload in (("snapshot", snapshot), ("solution", solution), ("metrics", metrics), ("runtime", runtime)):
        if name != "snapshot" and payload.get("run_id") != run_id:
            raise RuntimeError(f"{label} {name} run_id mismatch")
    if snapshot.get("run_id") != run_id:
        raise RuntimeError(
            f"{label} frozen Snapshot run_id mismatch: "
            f"expected={run_id!r}, actual={snapshot.get('run_id')!r}"
        )
    snapshot_situation = snapshot.get("situation") or {}
    if snapshot_situation.get("situation_id") != situation_id:
        raise RuntimeError(
            f"{label} frozen Snapshot situation_id mismatch: "
            f"expected={situation_id!r}, "
            f"actual={snapshot_situation.get('situation_id')!r}"
        )
    if not any(row.get("event") == "run_succeeded" for row in events.get("events", [])):
        raise RuntimeError(f"{label} is missing run_succeeded event")

    summary = metrics.get("summary") or {}
    scheduled = int(summary.get("scheduled_sorties_total") or 0)
    returned = int(summary.get("returned_sorties_total") or 0)
    if scheduled != returned:
        raise RuntimeError(f"{label} scheduled/returned conservation failed")
    if _sum_metric_rows(metrics.get("airports") or {}, "departures_total") != scheduled:
        raise RuntimeError(f"{label} airport departure conservation failed")
    if _sum_metric_rows(metrics.get("tasks") or {}, "scheduled_total") != scheduled:
        raise RuntimeError(f"{label} task scheduled conservation failed")
    if _sum_metric_rows(metrics.get("aircraft") or {}, "scheduled_total") != scheduled:
        raise RuntimeError(f"{label} aircraft scheduled conservation failed")
    if sum(int(row.get("departures_total") or 0) for row in runtime.get("frames", [])) != scheduled:
        raise RuntimeError(f"{label} Runtime departure conservation failed")
    if sum(int(row.get("returns_total") or 0) for row in runtime.get("frames", [])) != returned:
        raise RuntimeError(f"{label} Runtime return conservation failed")
    if sum(int(row.get("sorties") or 0) for row in solution.get("sortie_chains", [])) != scheduled:
        raise RuntimeError(f"{label} Solution chain conservation failed")

    serialized = json.dumps(
        {
            "snapshot": snapshot,
            "solution": solution,
            "metrics": metrics,
            "runtime": runtime,
        },
        ensure_ascii=False,
    )
    if "oa:" in serialized:
        raise RuntimeError(f"{label} contains a legacy oa: identity")
    if situation_id not in serialized or not situation_id.startswith("ST"):
        raise RuntimeError(f"{label} does not preserve the ST Situation identity")


def submit_and_execute(
    api: DemoClient,
    worker: RunWorker,
    snapshot_repository: RunSnapshotRepository,
    *,
    label: str,
    situation_id: str,
    damage_scenario_id: str | None,
    cluster_enabled: bool,
    core_airport_id: str,
) -> tuple[str, dict[str, Any]]:
    config = run_config(
        damage_scenario_id=damage_scenario_id,
        cluster_enabled=cluster_enabled,
        core_airport_id=core_airport_id,
    )
    validation = api.request(
        "POST",
        "/api/runs/validate",
        body={"situation_id": situation_id, "run_config": config},
    )
    if not validation.get("can_submit"):
        raise RuntimeError(f"{label} preflight failed: {validation}")
    record = api.request(
        "POST",
        "/api/runs",
        body={
            "situation_id": situation_id,
            "run_config": config,
            "expected_input_hash": validation["validated_input_hash"],
        },
        expected=(201,),
    )
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or record.get("status") != "queued":
        raise RuntimeError(f"{label} submission did not create a queued Run")
    print(f"[RUN] {label}: {run_id}", flush=True)
    try:
        terminal = worker.execute(run_id)
    except RunWorkerError as exc:
        raise RuntimeError(f"{label} worker execution failed: {exc}") from exc
    if terminal.status != "succeeded":
        raise RuntimeError(f"{label} ended with status {terminal.status}")

    path = f"/api/runs/{quote(run_id, safe='')}"
    detail = api.request("GET", path)
    solution = api.request("GET", f"{path}/solution")
    metrics = api.request("GET", f"{path}/metrics")
    frozen_situation = api.request("GET", f"{path}/situation")
    runtime = api.request("GET", f"{path}/runtime")
    events = api.request("GET", f"{path}/events")
    snapshot = snapshot_repository.get(run_id)
    if snapshot is None:
        raise RuntimeError(f"{label} snapshot disappeared")
    snapshot_payload = snapshot.to_dict()
    if frozen_situation.get("situation_id") != situation_id:
        raise RuntimeError(f"{label} frozen Situation mismatch")
    assert_run_integrity(
        label=label,
        run_id=run_id,
        situation_id=situation_id,
        detail=detail,
        snapshot=snapshot_payload,
        solution=solution,
        metrics=metrics,
        runtime=runtime,
        events=events,
    )
    print(f"[OK] {label} succeeded and passed conservation/identity checks", flush=True)
    return run_id, {
        "detail": detail,
        "snapshot": snapshot_payload,
        "solution": solution,
        "metrics": metrics,
        "runtime": runtime,
        "events": events,
    }


def _require_comparison_payload(
    label: str,
    payload: Mapping[str, Any],
    *,
    mode: str,
) -> None:
    required = [
        "run_summaries", "timeline", "airports", "tasks", "aircraft", "resources",
        "scheme", "labels",
    ]
    if mode in {"damage", "multi_scenario"}:
        required.append("difference_overview")
    elif mode == "configuration":
        # Configuration is baseline-relative by contract. Results consumes these
        # deltas directly; it does not consume Damage/Multi extrema.
        required.extend(("baseline_run_id", "configurations", "summary_deltas_vs_baseline"))
    else:
        raise ValueError(f"unknown Comparison mode: {mode}")
    missing = [key for key in required if key not in payload or payload[key] is None]
    if missing:
        raise RuntimeError(f"{label} Comparison payload is missing: {missing}")
    if not payload["run_summaries"] or not payload["timeline"] or not payload["labels"]:
        raise RuntimeError(f"{label} Comparison payload lacks core display facts")


def verify_comparisons(
    api: DemoClient | ReadOnlyResultsClient,
    run_ids: Mapping[str, str],
) -> dict[str, Any]:
    candidates = api.request("GET", "/api/results/damage-candidates")
    triples = {
        (row.get("r0_run_id"), row.get("r1_run_id"), row.get("r2_run_id"))
        for row in candidates.get("items", [])
    }
    outputs: dict[str, Any] = {"damage": {}, "configuration": {}}
    for scenario in ("LOW", "MEDIUM", "HIGH"):
        expected = (run_ids["R0"], run_ids[f"{scenario}-R1"], run_ids[f"{scenario}-R2"])
        if expected not in triples:
            raise RuntimeError(f"damage candidate discovery is missing {scenario}")
        payload = api.request(
            "POST",
            "/api/results/damage-comparison",
            body={"r0_run_id": expected[0], "r1_run_id": expected[1], "r2_run_id": expected[2]},
        )
        _require_comparison_payload(f"{scenario} damage", payload, mode="damage")
        outputs["damage"][scenario] = payload
        config_payload = api.request(
            "POST",
            "/api/results/config-comparison",
            body={"run_ids": [expected[1], expected[2]], "baseline_run_id": expected[1]},
        )
        _require_comparison_payload(
            f"{scenario} configuration", config_payload, mode="configuration"
        )
        print(
            f"[INFO] {scenario} configuration payload keys: "
            f"{sorted(config_payload.keys())}"
        )
        outputs["configuration"][scenario] = config_payload

    scenario_ids = [run_ids["R0"], run_ids["LOW-R1"], run_ids["MEDIUM-R1"], run_ids["HIGH-R1"]]
    multi = api.request(
        "POST", "/api/results/scenario-comparison", body={"run_ids": scenario_ids}
    )
    _require_comparison_payload("multi-scenario", multi, mode="multi_scenario")
    outputs["multi_scenario"] = multi

    comparable = api.request(
        "GET",
        f"/api/results/comparable-runs?{urlencode({'base_run_id': run_ids['R0'], 'mode': 'multi_scenario'})}",
    )
    comparable_ids = {row.get("run_id") for row in comparable.get("items", [])}
    if not set(scenario_ids[1:]).issubset(comparable_ids):
        raise RuntimeError("multi-scenario candidate discovery is incomplete")
    for scenario in ("LOW", "MEDIUM", "HIGH"):
        result = api.request(
            "GET",
            f"/api/results/comparable-runs?{urlencode({'base_run_id': run_ids[f'{scenario}-R1'], 'mode': 'configuration'})}",
        )
        if run_ids[f"{scenario}-R2"] not in {row.get("run_id") for row in result.get("items", [])}:
            raise RuntimeError(f"configuration candidate discovery is missing {scenario}-R2")
    print("[OK] three Damage, one multi-scenario and three configuration Comparisons")
    return outputs


def _resource_min(metrics: Mapping[str, Any], category: str) -> float | None:
    value = (metrics.get("resources") or {}).get("category_min_remaining_ratio", {}).get(category)
    if value is None:
        return None
    if isinstance(value, Mapping):
        value = value.get("ratio")
    return None if value is None else float(value)


def result_row(bundle: Mapping[str, Any]) -> dict[str, Any]:
    metrics = bundle["metrics"]
    summary = metrics.get("summary") or {}
    peak = summary.get("peak_departure_slot") or {}
    max_airport = summary.get("max_airport_departure") or {}
    collaboration = metrics.get("collaboration") or {}
    return {
        "required": int(summary.get("required_sorties_total") or 0),
        "scheduled": int(summary.get("scheduled_sorties_total") or 0),
        "participating": int(summary.get("participating_airport_count") or 0),
        "peak": int(peak.get("sorties") or 0),
        "peak_window": peak.get("window"),
        "max_share": max_airport.get("share"),
        "fuel": _resource_min(metrics, "fuel"),
        "material": _resource_min(metrics, "material"),
        "munition": _resource_min(metrics, "munition"),
        "hhi": collaboration.get("departure_hhi"),
        "cross_return": collaboration.get("cross_return_ratio"),
    }


def _fmt_ratio(value: Any) -> str:
    return "—" if value is None else f"{float(value):.1%}"


def print_result_table(bundles: Mapping[str, Mapping[str, Any]]) -> None:
    print("\n=== STANDARD 7-RUN RESULTS ===")
    print(
        f"{'Role':12s} {'Req':>5s} {'Sched':>6s} {'Air':>3s} {'Peak':>4s} {'T':>4s} "
        f"{'MaxShare':>8s} {'Fuel':>7s} {'Mat':>7s} {'Mun':>7s} {'HHI':>7s} {'Cross':>7s}"
    )
    for label, bundle in bundles.items():
        row = result_row(bundle)
        print(
            f"{label:12s} {row['required']:5d} {row['scheduled']:6d} {row['participating']:3d} "
            f"{row['peak']:4d} {str(row['peak_window']):>4s} {_fmt_ratio(row['max_share']):>8s} "
            f"{_fmt_ratio(row['fuel']):>7s} {_fmt_ratio(row['material']):>7s} "
            f"{_fmt_ratio(row['munition']):>7s} {_fmt_ratio(row['hhi']):>7s} "
            f"{_fmt_ratio(row['cross_return']):>7s}"
        )


def quality_warnings(bundles: Mapping[str, Mapping[str, Any]]) -> list[str]:
    rows = {label: result_row(bundle) for label, bundle in bundles.items()}
    warnings: list[str] = []
    if len({row["scheduled"] for row in rows.values()}) == 1:
        warnings.append("all seven Runs have identical scheduled totals")
    if rows["HIGH-R1"]["scheduled"] == 0:
        warnings.append("HIGH-R1 has zero scheduled sorties")
    resource_signatures = {
        (row["fuel"], row["material"], row["munition"])
        for row in rows.values()
    }
    if len(resource_signatures) == 1:
        warnings.append("all Runs have identical resource minima")
    if all(
        value is None or value >= 0.999999
        for row in rows.values()
        for value in (row["fuel"], row["material"], row["munition"])
    ):
        warnings.append("resource minima remain at 100%; resource pressure is insufficient")
    if len({round(float(row["max_share"] or 0), 8) for row in rows.values()}) == 1:
        warnings.append("all Runs have identical maximum airport departure share")
    return warnings


def choose_representative_run(bundles: Mapping[str, Mapping[str, Any]]) -> str:
    candidates = ("LOW-R2", "MEDIUM-R2", "HIGH-R2")
    def score(label: str) -> tuple[float, int, int]:
        row = result_row(bundles[label])
        minima = [value for value in (row["fuel"], row["material"], row["munition"]) if value is not None]
        pressure = 1.0 - min(minima) if minima else 0.0
        return pressure, row["participating"], row["scheduled"]
    return max(candidates, key=score)


def execute_run_batch(
    api: DemoClient,
    *,
    db_path: Path,
    situation_id: str,
    core_airport_id: str,
) -> tuple[dict[str, str], dict[str, dict[str, Any]], dict[str, Any]]:
    validate_run_matrix(api, situation_id=situation_id, core_airport_id=core_airport_id)
    worker = build_worker(db_path)
    snapshots = RunSnapshotRepository(db_path)
    run_ids: dict[str, str] = {}
    bundles: dict[str, dict[str, Any]] = {}
    for label, damage_id, cluster in run_matrix():
        run_id, bundle = submit_and_execute(
            api,
            worker,
            snapshots,
            label=label,
            situation_id=situation_id,
            damage_scenario_id=damage_id,
            cluster_enabled=cluster,
            core_airport_id=core_airport_id,
        )
        run_ids[label] = run_id
        bundles[label] = bundle

    axes = {
        (
            bundle["metrics"]["time_axis"]["slot_minutes"],
            tuple(bundle["metrics"]["time_axis"]["windows"]),
        )
        for bundle in bundles.values()
    }
    if len(axes) != 1:
        raise RuntimeError("seven Runs do not share one canonical time axis")
    comparisons = verify_comparisons(api, run_ids)
    print_result_table(bundles)
    warnings = quality_warnings(bundles)
    if warnings:
        for warning in warnings:
            print(f"[WARN] {warning}")
    else:
        print("[OK] dataset shows differentiated scheduling/resource/airport behavior")
    representative = choose_representative_run(bundles)
    print(f"[INFO] representative Single Run / Runtime sample: {representative} {run_ids[representative]}")
    return run_ids, bundles, comparisons


def verify_existing_workspace(
    db_path: Path,
    *,
    inspection: WorkspaceInspection,
    core_airport_id: str,
) -> tuple[dict[str, str], dict[str, dict[str, Any]], dict[str, Any]]:
    """Read and verify the existing canonical seven Runs without any write-capable flow."""
    require_demo_apply_safe(inspection)
    situation_id = inspection.demo_situation_id
    owner_user_id = inspection.demo_owner_user_id
    if situation_id is None:
        raise RuntimeError("standard demo Situation does not exist")
    if not owner_user_id:
        raise RuntimeError("standard demo Situation has no owner; refusing to claim its Runs")
    expected_count = len(run_matrix())
    if inspection.demo_run_count != expected_count:
        raise RuntimeError(
            f"standard demo Situation {situation_id} must have exactly {expected_count} Runs; "
            f"found {inspection.demo_run_count}"
        )

    run_repository = RunRepository(db_path)
    snapshot_repository = RunSnapshotRepository(db_path)
    records, total = run_repository.search_for_owner(
        owner_user_id,
        situation_id=situation_id,
        limit=500,
        offset=0,
    )
    if total != expected_count:
        raise RuntimeError(
            f"standard demo Situation {situation_id} has {inspection.demo_run_count} total Runs "
            f"but only {total} owned by {owner_user_id!r}"
        )

    snapshots: dict[str, Any] = {}
    role_rows: list[dict[str, Any]] = []
    for record in records:
        snapshot = snapshot_repository.get(record.run_id)
        snapshot_payload = None if snapshot is None else snapshot.to_dict()
        snapshots[record.run_id] = snapshot
        role_rows.append({
            "run_id": record.run_id,
            "status": record.status,
            "situation_id": record.situation_id,
            "owner_user_id": record.owner_user_id,
            "snapshot": snapshot_payload,
        })
    run_ids = identify_standard_run_roles(
        role_rows,
        situation_id=situation_id,
        owner_user_id=owner_user_id,
        core_airport_id=core_airport_id,
    )
    print("\n=== EXISTING RUN ROLES (from frozen run_config) ===")
    for label, run_id in run_ids.items():
        print(f"{label:12s} {run_id}")

    result_service = RunResultService(
        run_repository=run_repository,
        snapshot_repository=snapshot_repository,
    )
    runtime_service = RunRuntimeService(result_service=result_service)
    bundles: dict[str, dict[str, Any]] = {}
    for label, run_id in run_ids.items():
        snapshot = snapshots[run_id]
        if snapshot is None:
            raise RuntimeError(f"{label} is missing its frozen Snapshot")
        stored = result_service.get_single_run(
            run_id,
            actor_user_id=owner_user_id,
            is_admin=False,
        )
        detail = result_service.get_run_detail(
            run_id,
            actor_user_id=owner_user_id,
            is_admin=False,
        )
        runtime = runtime_service.get_runtime(
            run_id,
            actor_user_id=owner_user_id,
            is_admin=False,
        )
        events = {
            "run_id": run_id,
            "events": [
                event.to_dict()
                for event in run_repository.list_events(run_id, after_seq=0, limit=1000)
            ],
        }
        bundle = {
            "detail": detail,
            "snapshot": snapshot.to_dict(),
            "solution": stored["solution"],
            "metrics": stored["metrics"],
            "runtime": runtime,
            "events": events,
        }
        assert_run_integrity(
            label=label,
            run_id=run_id,
            situation_id=situation_id,
            **bundle,
        )
        bundles[label] = bundle
        print(f"[OK] {label} passed stored Run integrity checks")

    axes = {
        (
            bundle["metrics"]["time_axis"]["slot_minutes"],
            tuple(bundle["metrics"]["time_axis"]["windows"]),
        )
        for bundle in bundles.values()
    }
    if len(axes) != 1:
        raise RuntimeError("seven existing Runs do not share one canonical time axis")

    principal = Principal(owner_user_id, role="viewer")
    comparison_client = ReadOnlyResultsClient(
        ResultsApi(result_service=result_service),
        principal=principal,
    )
    comparisons = verify_comparisons(comparison_client, run_ids)
    print_result_table(bundles)
    warnings = quality_warnings(bundles)
    if warnings:
        for warning in warnings:
            print(f"[WARN] {warning}")
    else:
        print("[OK] dataset shows differentiated scheduling/resource/airport behavior")
    representative = choose_representative_run(bundles)
    print(f"[INFO] representative Single Run / Runtime sample: {representative} {run_ids[representative]}")
    print("[OK] existing standard demo verification completed without writes or Solver execution")
    return run_ids, bundles, comparisons


def _verify_default_path(settings: AppSettings) -> None:
    if settings.db_path.resolve() != DEFAULT_DB_PATH:
        raise RuntimeError(
            f"this tool only operates on the default database {DEFAULT_DB_PATH}; got {settings.db_path}"
        )


def print_plan(inspection: WorkspaceInspection, *, mode: str) -> None:
    print("=== STANDARD DEMO WORKSPACE PLAN ===")
    print(f"Database: {inspection.database_path}")
    print(f"Mode: {mode}")
    print(f"Airports: {inspection.airport_count}")
    print(f"Situations: {inspection.situation_count}")
    print(f"Runs: {inspection.run_count}")
    print(f"Existing demo Situation: {inspection.demo_situation_id or 'none'}")
    print(f"Existing demo owner: {inspection.demo_owner_user_id or 'none'}")
    print(f"Existing demo Runs: {inspection.demo_run_count}")
    if inspection.demo_collision_count:
        print(
            "[BLOCK] same-name non-demo Situation collision(s): "
            f"{', '.join(inspection.demo_collision_ids)}"
        )
    print_catalog_differences(inspection.database_path)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = AppSettings.from_environment()
    _verify_default_path(settings)
    airport_ids = resolve_airport_ids()
    validate_static_definition(airport_ids)
    inspection = inspect_workspace(settings.db_path)
    print_plan(inspection, mode=args.mode)
    if args.mode == "verify":
        verify_existing_workspace(
            settings.db_path,
            inspection=inspection,
            core_airport_id=airport_ids["nanjing"],
        )
        return 0
    if not args.apply_default_db:
        print("\n[DRY RUN] no database writes were performed; add --apply-default-db to execute")
        return 0

    require_demo_apply_safe(inspection)

    username, password = credentials()
    # Validate all environment-only prerequisites before creating a backup. Application
    # composition stays after the backup because build_application initializes schema
    # and may bootstrap a configured admin on a fresh authority.
    settings.validate_web()
    backup = backup_default_database(settings.db_path)
    print(f"[BACKUP] {backup}")
    app = build_application(settings)
    api = DemoClient(app)
    api.login(username, password)
    print(f"[OK] authenticated as {username} ({api.user_id})")

    existing = find_visible_demo(api)
    existing_demo_detail = None
    if existing is not None:
        existing_demo_detail = api.request(
            "GET", f"/api/situations/{quote(str(existing['situation_id']), safe='')}"
        )
    if args.mode == "run" and existing is None:
        raise RuntimeError("standard demo Situation does not exist; run --prepare-only first")
    if existing is not None and args.mode in {"run", "rebuild"}:
        situation_id = str(existing["situation_id"])
        repository = RunRepository(settings.db_path)
        _rows, existing_total = repository.search_for_owner(
            str(api.user_id), situation_id=situation_id, limit=500, offset=0
        )
        if args.mode == "run" and existing_total:
            raise RuntimeError(
                f"standard demo Situation already has {existing_total} Run(s); use --rebuild"
            )
        if args.mode == "rebuild":
            removed = clean_demo_runs(
                settings.db_path,
                owner_user_id=str(api.user_id),
                situation_id=situation_id,
            )
            print(f"[CLEAN] removed {removed} terminal standard-demo Run(s)")

    if args.mode in {"prepare", "rebuild"}:
        prepare_catalogs(api)
        verify_airport_authority(api, airport_ids)
        update_airport_profiles(api, airport_ids)
        prepare_missions(
            api,
            rebuild=args.mode == "rebuild",
            demo_detail=existing_demo_detail,
        )
        situation_detail = prepare_situation(api, airport_ids)
    else:
        assert existing is not None
        situation_detail = api.request(
            "GET", f"/api/situations/{quote(str(existing['situation_id']), safe='')}"
        )
    situation_id = str(situation_detail["situation"]["situation_id"])
    if args.mode == "prepare":
        print(f"\n[OK] standard demo Situation ready: {situation_id}; no Runs submitted")
        print(f"[BACKUP] {backup}")
        return 0

    run_ids, _bundles, _comparisons = execute_run_batch(
        api,
        db_path=settings.db_path,
        situation_id=situation_id,
        core_airport_id=airport_ids["nanjing"],
    )
    print("\n=== RUN IDS ===")
    for label, run_id in run_ids.items():
        print(f"{label:12s} {run_id}")
    print(f"[BACKUP] {backup}")
    print("[OK] default database standard demo workspace completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
