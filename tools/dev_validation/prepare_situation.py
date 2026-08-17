from __future__ import annotations

import argparse
import getpass
import os
import sys
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
from backend.runtime import build_application
from backend.settings import AppSettings, DEFAULT_DB_NAME

SITUATION_ID = "DEV-VALIDATION-01"

AIRPORT_IDS = (
    "oa:27221",  # Nanjing Lukou
    "oa:32713",  # Xuzhou Guanyin
    "oa:32048",  # Nantong Xingdong
    "oa:32420",  # Suzhou Guangfu
    "oa:35316",  # Yancheng Nanyang
    "oa:32684",  # Sunan Shuofang
)

AIRCRAFT_TYPES = (
    {
        "aircraft_type_id": "fighter",
        "name": "Fighter",
        "speed_kmh": 800.0,
        "max_range_km": 1500.0,
        "reserve_ratio": 0.2,
        "departure_capacity_occupancy_factor": 1.0,
        "arrival_capacity_occupancy_factor": 1.0,
    },
    {
        "aircraft_type_id": "bomber",
        "name": "Bomber",
        "speed_kmh": 800.0,
        "max_range_km": 2000.0,
        "reserve_ratio": 0.1,
        "departure_capacity_occupancy_factor": 1.3,
        "arrival_capacity_occupancy_factor": 1.3,
    },
    {
        "aircraft_type_id": "transport",
        "name": "Transport",
        "speed_kmh": 700.0,
        "max_range_km": 2500.0,
        "reserve_ratio": 0.1,
        "departure_capacity_occupancy_factor": 1.1,
        "arrival_capacity_occupancy_factor": 1.1,
    },
)

RESOURCE_IDS = ("FUEL-A", "MAT-1", "MAT-2", "MAT-3", "MUN-1", "MUN-2")

RESOURCE_TYPES = (
    {"resource_type_id": "FUEL-A", "name": "Fuel A", "category": "fuel", "unit": "t"},
    {"resource_type_id": "MAT-1", "name": "Material 1", "category": "material", "unit": "t"},
    {"resource_type_id": "MAT-2", "name": "Material 2", "category": "material", "unit": "t"},
    {"resource_type_id": "MAT-3", "name": "Material 3", "category": "material", "unit": "t"},
    {"resource_type_id": "MUN-1", "name": "Munition 1", "category": "munition", "unit": "t"},
    {"resource_type_id": "MUN-2", "name": "Munition 2", "category": "munition", "unit": "t"},
)

AIRCRAFT_RESOURCE_REQUIREMENTS = {
    "fighter": (
        {"aircraft_type_id": "fighter", "resource_type_id": "FUEL-A", "basis": "per_hour", "quantity": 1.0},
        {"aircraft_type_id": "fighter", "resource_type_id": "MAT-1", "basis": "per_sortie", "quantity": 1.0},
        {"aircraft_type_id": "fighter", "resource_type_id": "MAT-2", "basis": "per_sortie", "quantity": 1.0},
        {"aircraft_type_id": "fighter", "resource_type_id": "MAT-3", "basis": "per_sortie", "quantity": 1.0},
        {"aircraft_type_id": "fighter", "resource_type_id": "MUN-1", "basis": "per_sortie", "quantity": 2.0},
        {"aircraft_type_id": "fighter", "resource_type_id": "MUN-2", "basis": "per_sortie", "quantity": 1.0},
    ),
    "bomber": (
        {"aircraft_type_id": "bomber", "resource_type_id": "FUEL-A", "basis": "per_hour", "quantity": 1.8},
        {"aircraft_type_id": "bomber", "resource_type_id": "MAT-1", "basis": "per_sortie", "quantity": 3.0},
        {"aircraft_type_id": "bomber", "resource_type_id": "MAT-2", "basis": "per_sortie", "quantity": 2.0},
        {"aircraft_type_id": "bomber", "resource_type_id": "MUN-1", "basis": "per_sortie", "quantity": 10.0},
        {"aircraft_type_id": "bomber", "resource_type_id": "MUN-2", "basis": "per_sortie", "quantity": 4.0},
    ),
    "transport": (
        {"aircraft_type_id": "transport", "resource_type_id": "FUEL-A", "basis": "per_hour", "quantity": 1.5},
        {"aircraft_type_id": "transport", "resource_type_id": "MAT-1", "basis": "per_sortie", "quantity": 1.0},
        {"aircraft_type_id": "transport", "resource_type_id": "MAT-2", "basis": "per_sortie", "quantity": 1.0},
    ),
}

# Values are adapted from the old A1-A6 development dataset; only AirportBase identity
# comes from the current canonical airport catalog.
AIRPORT_PROFILES = {
    "oa:27221": {"capacity": 8, "support": {"fighter": (16, 1), "bomber": (7, 2), "transport": (5, 3)}, "stock": (220.2, 130, 120, 90, 150, 120)},
    "oa:32713": {"capacity": 6, "support": {"fighter": (15, 1)}, "stock": (150.8, 150, 180, 85, 175, 190)},
    "oa:32048": {"capacity": 10, "support": {"fighter": (15, 1), "bomber": (6, 2)}, "stock": (170.4, 180, 160, 190, 196, 190)},
    "oa:32420": {"capacity": 4, "support": {"fighter": (8, 1)}, "stock": (130.5, 190, 120, 100, 90, 88)},
    "oa:35316": {"capacity": 12, "support": {"fighter": (17, 2), "bomber": (5, 3), "transport": (6, 4)}, "stock": (190.6, 185, 188, 196, 70, 60)},
    "oa:32684": {"capacity": 7, "support": {"fighter": (10, 1), "bomber": (4, 2), "transport": (2, 5)}, "stock": (170.8, 188, 166, 198, 110, 120)},
}

MISSIONS = (
    {
        "mission_id": "N1",
        "name": "任务一",
        "longitude": 120.939927,
        "latitude": 24.818282,
        "window_start_slot": 42,
        "window_end_slot": 56,
        "aircraft_requirements": [
            {"aircraft_type_id": "fighter", "required_sorties": 38, "tau_work_windows": 1},
            {"aircraft_type_id": "bomber", "required_sorties": 15, "tau_work_windows": 2},
            {"aircraft_type_id": "transport", "required_sorties": 8, "tau_work_windows": 1},
        ],
    },
    {
        "mission_id": "N2",
        "name": "任务二",
        "longitude": 130.521860,
        "latitude": 31.173962,
        "window_start_slot": 56,
        "window_end_slot": 68,
        "aircraft_requirements": [
            {"aircraft_type_id": "fighter", "required_sorties": 26, "tau_work_windows": 1},
            {"aircraft_type_id": "bomber", "required_sorties": 6, "tau_work_windows": 2},
            {"aircraft_type_id": "transport", "required_sorties": 5, "tau_work_windows": 1},
        ],
    },
    {
        "mission_id": "N3",
        "name": "任务三",
        "longitude": 121.551529,
        "latitude": 25.069622,
        "window_start_slot": 50,
        "window_end_slot": 65,
        "aircraft_requirements": [
            {"aircraft_type_id": "fighter", "required_sorties": 42, "tau_work_windows": 1},
            {"aircraft_type_id": "bomber", "required_sorties": 10, "tau_work_windows": 2},
            {"aircraft_type_id": "transport", "required_sorties": 6, "tau_work_windows": 1},
        ],
    },
)

DAMAGE_SCENARIOS = (
    {
        "damage_scenario_id": "DS-LOW",
        "name": "轻度损毁",
        "category": "low",
        "events": [
            {
                "event_id": "LOW-CAP-NKG",
                "sequence": 0,
                "target": {"airport_id": "oa:27221", "target_type": "airport", "target_id": None},
                "damage_type": "capacity_damage",
                "start_slot": 42,
                "end_slot": 56,
                "effect": {"closed": False, "remaining_capacity_per_window": 6},
                "recovery_mode": "instant",
                "recovery_duration_slots": None,
            }
        ],
    },
    {
        "damage_scenario_id": "DS-MEDIUM",
        "name": "中度损毁",
        "category": "medium",
        "events": [
            {
                "event_id": "MED-AIRCRAFT-NKG",
                "sequence": 0,
                "target": {"airport_id": "oa:27221", "target_type": "airport", "target_id": None},
                "damage_type": "aircraft_damage",
                "start_slot": 42,
                "end_slot": 43,
                "effect": {"aircraft_loss": {"fighter": 8, "bomber": 3}},
                "recovery_mode": "none",
                "recovery_duration_slots": None,
            },
            {
                "event_id": "MED-CAP-NTG",
                "sequence": 1,
                "target": {"airport_id": "oa:32048", "target_type": "airport", "target_id": None},
                "damage_type": "capacity_damage",
                "start_slot": 48,
                "end_slot": 61,
                "effect": {"closed": False, "remaining_capacity_per_window": 5},
                "recovery_mode": "instant",
                "recovery_duration_slots": None,
            },
        ],
    },
    {
        "damage_scenario_id": "DS-HIGH",
        "name": "重度损毁",
        "category": "high",
        "events": [
            {
                "event_id": "HIGH-AIRCRAFT-NKG",
                "sequence": 0,
                "target": {"airport_id": "oa:27221", "target_type": "airport", "target_id": None},
                "damage_type": "aircraft_damage",
                "start_slot": 42,
                "end_slot": 43,
                "effect": {"aircraft_loss": {"fighter": 12, "bomber": 5, "transport": 3}},
                "recovery_mode": "none",
                "recovery_duration_slots": None,
            },
            {
                "event_id": "HIGH-CAP-NTG",
                "sequence": 1,
                "target": {"airport_id": "oa:32048", "target_type": "airport", "target_id": None},
                "damage_type": "capacity_damage",
                "start_slot": 46,
                "end_slot": 64,
                "effect": {"closed": False, "remaining_capacity_per_window": 2},
                "recovery_mode": "instant",
                "recovery_duration_slots": None,
            },
            {
                "event_id": "HIGH-RESOURCE-YNZ",
                "sequence": 2,
                "target": {"airport_id": "oa:35316", "target_type": "airport", "target_id": None},
                "damage_type": "resource_damage",
                "start_slot": 48,
                "end_slot": 65,
                "effect": {"remaining_quantity": {"FUEL-A": 75.0, "MUN-1": 25.0, "MUN-2": 20.0}},
                "recovery_mode": "instant",
                "recovery_duration_slots": None,
            },
        ],
    },
)


class ApiFailure(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: Any):
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"{method} {path} failed ({status}): {body}")


class ValidationClient:
    def __init__(self, app: Any) -> None:
        self.client = app.test_client()
        self.csrf: str | None = None

    @staticmethod
    def _body(response: Any) -> Any:
        body = response.get_json(silent=True)
        return body if body is not None else response.get_data(as_text=True)

    def login(self, username: str, password: str) -> None:
        response = self.client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        if response.status_code != 200:
            raise ApiFailure("POST", "/api/auth/login", response.status_code, self._body(response))
        cookie = None
        if hasattr(self.client, "get_cookie"):
            cookie = self.client.get_cookie("csrftoken")
        if cookie is not None:
            self.csrf = getattr(cookie, "value", None) or str(cookie)
        if not self.csrf:
            for raw in response.headers.getlist("Set-Cookie"):
                parsed = SimpleCookie(); parsed.load(raw)
                if "csrftoken" in parsed:
                    self.csrf = parsed["csrftoken"].value
                    break
        if not self.csrf:
            raise ApiFailure("POST", "/api/auth/login", 200, "login succeeded but csrftoken was not issued")

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
        if response.status_code not in expected:
            raise ApiFailure(method.upper(), path, response.status_code, self._body(response))
        return self._body(response)


def assert_validation_db(path: Path) -> None:
    if path.name == DEFAULT_DB_NAME or "validation" not in path.name.lower():
        raise RuntimeError(
            f"refusing to modify non-validation database: {path}. Set AIRPORT_GROUP_DB_PATH to validation_work.sqlite3"
        )
    if not path.exists():
        raise FileNotFoundError(
            f"validation database not found: {path}. Run reset_validation_db.py first"
        )


def credentials(args: argparse.Namespace, settings: AppSettings) -> tuple[str, str]:
    username = (
        args.username
        or os.environ.get("AIRPORT_GROUP_VALIDATION_USERNAME")
        or settings.bootstrap_admin_login
        or "admin"
    )
    password = (
        args.password
        or os.environ.get("AIRPORT_GROUP_VALIDATION_PASSWORD")
        or settings.bootstrap_admin_password
    )
    if not password:
        password = getpass.getpass(f"Password for {username}: ")
    return username, password


def resource_stock(rows: tuple[float, ...]) -> list[dict[str, Any]]:
    if len(rows) != len(RESOURCE_IDS):
        raise RuntimeError(
            f"airport stock must contain {len(RESOURCE_IDS)} quantities, got {len(rows)}"
        )
    return [
        {
            "resource_type_id": rid,
            "initial_quantity": quantity,
            "replenishment_capacity_per_window": 0.0,
        }
        for rid, quantity in zip(RESOURCE_IDS, rows)
    ]


def operational_profile(airport_id: str) -> dict[str, Any]:
    raw = AIRPORT_PROFILES[airport_id]
    return {
        "airport_id": airport_id,
        "configuration_complete": True,
        "capacity_per_window": raw["capacity"],
        "support_level": "validation",
        "aircraft_support": [
            {
                "aircraft_type_id": aircraft_id,
                "initial_quantity": values[0],
                "tau_reset_windows": values[1],
            }
            for aircraft_id, values in raw["support"].items()
        ],
        "resource_stocks": resource_stock(raw["stock"]),
    }




def warn_interpreter() -> None:
    expected = PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if expected.is_file() and Path(sys.executable).resolve() != expected.resolve():
        print(
            f"[WARN] using {Path(sys.executable).resolve()} instead of project venv {expected.resolve()}"
        )


def validate_static_definition() -> None:
    if len(AIRPORT_IDS) != len(set(AIRPORT_IDS)):
        raise RuntimeError("AIRPORT_IDS contains duplicates")
    if set(AIRPORT_PROFILES) != set(AIRPORT_IDS):
        raise RuntimeError("AIRPORT_PROFILES must cover exactly AIRPORT_IDS")

    aircraft = [AircraftType.from_mapping(dict(item)) for item in AIRCRAFT_TYPES]
    resources = [ResourceType.from_mapping(dict(item)) for item in RESOURCE_TYPES]
    aircraft_ids = {item.aircraft_type_id for item in aircraft}
    resource_ids = {item.resource_type_id for item in resources}
    if aircraft_ids != set(AIRCRAFT_RESOURCE_REQUIREMENTS):
        raise RuntimeError("aircraft requirement map must cover exactly the configured aircraft types")
    if resource_ids != set(RESOURCE_IDS):
        raise RuntimeError("RESOURCE_IDS and RESOURCE_TYPES differ")

    for aircraft_id, rows in AIRCRAFT_RESOURCE_REQUIREMENTS.items():
        for raw in rows:
            row = AircraftResourceRequirement.from_mapping(dict(raw))
            if row.aircraft_type_id != aircraft_id:
                raise RuntimeError(f"requirement aircraft mismatch: {aircraft_id}")
            if row.resource_type_id not in resource_ids:
                raise RuntimeError(f"unknown requirement resource: {row.resource_type_id}")

    missions = [Mission.from_mapping(dict(item)) for item in MISSIONS]
    if len({item.mission_id for item in missions}) != len(missions):
        raise RuntimeError("MISSIONS contains duplicate mission_id")
    for mission in missions:
        unknown = {row.aircraft_type_id for row in mission.aircraft_requirements} - aircraft_ids
        if unknown:
            raise RuntimeError(f"mission {mission.mission_id} references unknown aircraft: {sorted(unknown)}")

    profiles = {
        airport_id: AirportOperationalProfile.from_mapping(operational_profile(airport_id))
        for airport_id in AIRPORT_IDS
    }
    for airport_id, profile in profiles.items():
        supported = {row.aircraft_type_id for row in profile.aircraft_support}
        unknown = supported - aircraft_ids
        if unknown:
            raise RuntimeError(f"airport {airport_id} supports unknown aircraft: {sorted(unknown)}")
        stocks = {row.resource_type_id for row in profile.resource_stocks}
        if stocks != resource_ids:
            raise RuntimeError(f"airport {airport_id} resource stock does not cover the validation catalog")

    scenarios = [DamageScenario.from_mapping(dict(item)) for item in DAMAGE_SCENARIOS]
    if len({item.damage_scenario_id for item in scenarios}) != len(scenarios):
        raise RuntimeError("DAMAGE_SCENARIOS contains duplicate damage_scenario_id")

    mission_min = min(item.window_start_slot for item in missions)
    mission_end = max(item.window_end_slot for item in missions)
    for scenario in scenarios:
        for event in scenario.events:
            airport_id = event.target.airport_id
            if airport_id not in profiles:
                raise RuntimeError(
                    f"damage scenario {scenario.damage_scenario_id} targets airport outside validation set: {airport_id}"
                )
            # Keep every scenario inside the mission envelope so R0/R1/R2 get the same
            # algorithm horizon and remain discoverable by comparison-candidate APIs.
            if event.start_slot < mission_min or event.end_slot > mission_end:
                raise RuntimeError(
                    f"damage event {event.event_id} falls outside validation mission envelope "
                    f"[{mission_min},{mission_end})"
                )

            profile = profiles[airport_id]
            if isinstance(event.effect, CapacityDamageEffect) and event.target.target_type == "airport":
                if event.effect.remaining_capacity_per_window > int(profile.capacity_per_window or 0):
                    raise RuntimeError(f"damage event {event.event_id} increases airport capacity")
            elif isinstance(event.effect, AircraftDamageEffect):
                available = {
                    row.aircraft_type_id: int(row.initial_quantity or 0)
                    for row in profile.aircraft_support
                }
                for aircraft_type_id, loss in event.effect.aircraft_loss:
                    if loss > available.get(aircraft_type_id, 0):
                        raise RuntimeError(
                            f"damage event {event.event_id} loses more {aircraft_type_id} than available"
                        )
            elif isinstance(event.effect, ResourceDamageEffect):
                available = {
                    row.resource_type_id: float(row.initial_quantity or 0.0)
                    for row in profile.resource_stocks
                }
                for resource_type_id, remaining in event.effect.remaining_quantity:
                    if remaining > available.get(resource_type_id, -1.0) + 1e-9:
                        raise RuntimeError(
                            f"damage event {event.event_id} leaves more {resource_type_id} than baseline stock"
                        )

    print("[OK] validation constants pass current domain checks")


def upsert_resources(api: ValidationClient) -> None:
    current = api.request("GET", "/api/resource-types").get("items", [])
    by_id = {x["resource_type"]["resource_type_id"]: x for x in current}
    for item in RESOURCE_TYPES:
        rid = item["resource_type_id"]
        if rid in by_id:
            revision = by_id[rid]["metadata"]["revision"]
            api.request(
                "PUT", f"/api/resource-types/{quote(rid, safe='')}",
                body={"resource_type": item, "expected_revision": revision},
            )
        else:
            api.request("POST", "/api/resource-types", body={"resource_type": item}, expected=(201,))
        print(f"[OK] resource {rid}")


def upsert_aircraft(api: ValidationClient) -> dict[str, int]:
    current = api.request("GET", "/api/aircraft-types").get("items", [])
    by_id = {x["aircraft_type"]["aircraft_type_id"]: x for x in current}
    revisions: dict[str, int] = {}
    for item in AIRCRAFT_TYPES:
        aid = item["aircraft_type_id"]
        if aid in by_id:
            revision = by_id[aid]["metadata"]["revision"]
            result = api.request(
                "PUT", f"/api/aircraft-types/{quote(aid, safe='')}",
                body={"aircraft_type": item, "expected_revision": revision},
            )
        else:
            result = api.request("POST", "/api/aircraft-types", body={"aircraft_type": item}, expected=(201,))
        revisions[aid] = int(result["metadata"]["revision"])
        print(f"[OK] aircraft {aid}")
    return revisions


def replace_requirements(api: ValidationClient, revisions: dict[str, int]) -> None:
    for aid, rows in AIRCRAFT_RESOURCE_REQUIREMENTS.items():
        result = api.request(
            "PUT",
            f"/api/aircraft-types/{quote(aid, safe='')}/resource-requirements",
            body={"requirements": list(rows), "expected_revision": revisions[aid]},
        )
        revisions[aid] = int(result["metadata"]["revision"])
        print(f"[OK] requirements {aid}")


def validate_global_aircraft_catalog(api: ValidationClient) -> None:
    # RunSnapshot currently freezes the whole aircraft catalog, and snapshot_adapter
    # materializes numeric flight parameters for every frozen aircraft type. Catch an
    # unrelated incomplete global row now instead of failing deep inside RunWorker later.
    current = api.request("GET", "/api/aircraft-types").get("items", [])
    required_numeric = (
        "speed_kmh",
        "max_range_km",
        "reserve_ratio",
        "departure_capacity_occupancy_factor",
        "arrival_capacity_occupancy_factor",
    )
    incomplete = []
    for row in current:
        item = row.get("aircraft_type") or {}
        missing = [field for field in required_numeric if item.get(field) is None]
        if missing:
            incomplete.append((item.get("aircraft_type_id"), missing))
    if incomplete:
        detail = "; ".join(f"{aircraft_id}: {','.join(fields)}" for aircraft_id, fields in incomplete)
        raise RuntimeError(
            "global aircraft catalog contains incomplete rows that the current RunSnapshot adapter "
            f"cannot materialize: {detail}"
        )
    print(f"[OK] global aircraft catalog is algorithm-materializable ({len(current)} type(s))")


def update_airport_profiles(api: ValidationClient) -> None:
    for airport_id in AIRPORT_IDS:
        path = f"/api/airports/{quote(airport_id, safe='')}"
        detail = api.request("GET", path)
        revision = detail["metadata"]["revision"]
        api.request(
            "PUT",
            path,
            body={
                "airport": detail["airport"],
                "operational_profile": operational_profile(airport_id),
                "expected_revision": revision,
            },
        )
        print(f"[OK] airport profile {airport_id}")


def upsert_missions(api: ValidationClient) -> None:
    for mission in MISSIONS:
        mid = mission["mission_id"]
        path = f"/api/missions/{quote(mid, safe='')}"
        try:
            detail = api.request("GET", path)
        except ApiFailure as exc:
            if exc.status != 404:
                raise
            detail = None
        if detail is None:
            api.request("POST", "/api/missions", body={"mission": mission}, expected=(201,))
        else:
            api.request(
                "PUT",
                path,
                body={"mission": mission, "expected_revision": detail["metadata"]["revision"]},
            )
        print(f"[OK] mission {mid}")


def run_config(*, damage: str | None, cluster: bool) -> dict[str, Any]:
    return {
        "damage_scenario_id": damage,
        "preference_mode": "sortie_max",
        "cluster_enabled": cluster,
        "cluster_size": 4 if cluster else None,
        "core_airports": ["oa:27221"] if cluster else [],
        "aircraft_type_weight": {"transport": 0.9},
        "mip_time_limit_s": 120.0,
        "algorithm_seed": 42,
    }


def validate_run_matrix(api: ValidationClient) -> None:
    cases = [("R0", None, False)]
    for scenario in ("DS-LOW", "DS-MEDIUM", "DS-HIGH"):
        cases.append((f"{scenario}-R1", scenario, False))
        cases.append((f"{scenario}-R2", scenario, True))
    for label, damage, cluster in cases:
        validation = api.request(
            "POST",
            "/api/runs/validate",
            body={
                "situation_id": SITUATION_ID,
                "run_config": run_config(damage=damage, cluster=cluster),
            },
        )
        if not validation.get("can_submit", False):
            raise RuntimeError(f"{label} preflight failed: {validation}")
        fingerprint = validation.get("validated_input_hash")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise RuntimeError(f"{label} preflight returned invalid validated_input_hash")
        print(f"[OK] preflight {label}")


def build_situation(api: ValidationClient) -> dict[str, Any]:
    try:
        api.request("GET", f"/api/situations/{quote(SITUATION_ID, safe='')}")
    except ApiFailure as exc:
        if exc.status != 404:
            raise
    else:
        raise RuntimeError(
            f"{SITUATION_ID} already exists. Reset validation_work.sqlite3 before preparing again."
        )

    working: dict[str, Any] = {
        "situation_id": SITUATION_ID,
        "name": "开发验证情境（江苏六机场）",
        "description": "开发验证专用；江苏六机场 + N1/N2/N3 + 轻/中/重损毁。",
        "airports": [],
        "missions": [],
        "damage_scenarios": [],
    }

    for airport_id in AIRPORT_IDS:
        result = api.request(
            "POST",
            "/api/situations/working-copy/copy-airport",
            body={"situation": working, "airport_id": airport_id},
        )
        working = result["situation"]
        print(f"[OK] Situation add airport {airport_id}")

    for mission in MISSIONS:
        mid = mission["mission_id"]
        result = api.request(
            "POST",
            "/api/situations/working-copy/copy-mission",
            body={"situation": working, "mission_id": mid},
        )
        working = result["situation"]
        print(f"[OK] Situation add mission {mid}")

    working["damage_scenarios"] = list(DAMAGE_SCENARIOS)
    normalized = api.request(
        "POST",
        "/api/situations/working-copy/canonicalize",
        body={"situation": working},
    )
    working = normalized["situation"]
    if {row["airport"]["airport_id"] for row in working["airports"]} != set(AIRPORT_IDS):
        raise RuntimeError("canonicalized Situation airport set differs from validation definition")
    if {row["mission_id"] for row in working["missions"]} != {row["mission_id"] for row in MISSIONS}:
        raise RuntimeError("canonicalized Situation mission set differs from validation definition")
    if {row["damage_scenario_id"] for row in working["damage_scenarios"]} != {
        row["damage_scenario_id"] for row in DAMAGE_SCENARIOS
    }:
        raise RuntimeError("canonicalized Situation damage set differs from validation definition")
    print("[OK] damage scenarios canonicalized")

    created = api.request(
        "POST", "/api/situations", body={"situation": working}, expected=(201,)
    )
    detail = api.request("GET", f"/api/situations/{quote(SITUATION_ID, safe='')}")
    if detail["content_hash"] != created["content_hash"]:
        raise RuntimeError("saved Situation content hash changed on immediate read-back")

    validate_run_matrix(api)
    return detail


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare the fixed development validation Situation.")
    parser.add_argument("--username")
    parser.add_argument("--password")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    warn_interpreter()
    validate_static_definition()
    settings = AppSettings.from_environment()
    assert_validation_db(settings.db_path)
    username, password = credentials(args, settings)

    app = build_application(settings)
    api = ValidationClient(app)
    api.login(username, password)
    print(f"[OK] login {username}")

    upsert_resources(api)
    revisions = upsert_aircraft(api)
    replace_requirements(api, revisions)
    validate_global_aircraft_catalog(api)
    update_airport_profiles(api)
    upsert_missions(api)
    detail = build_situation(api)

    situation = detail["situation"]
    print("\n=== VALIDATION SITUATION READY ===")
    print(f"Situation: {SITUATION_ID}")
    print(f"Airports: {len(situation['airports'])}")
    print(f"Missions: {len(situation['missions'])}")
    print(f"Damage scenarios: {len(situation['damage_scenarios'])}")
    print(f"Content hash: {detail['content_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
