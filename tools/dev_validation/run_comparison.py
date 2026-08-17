from __future__ import annotations

import argparse
import getpass
import os
import sys
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.runtime import build_application
from backend.services.run_result_service import RunResultService
from backend.services.run_service import RunService
from backend.services.run_snapshot_service import RunSnapshotService
from backend.services.run_worker import RunWorker, RunWorkerError
from backend.settings import AppSettings, DEFAULT_DB_NAME
from backend.storage.airport_repository import AirportRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.situation_repository import SituationRepository

SITUATION_ID = "DEV-VALIDATION-01"
EXPECTED_AIRPORT_IDS = {
    "oa:27221",
    "oa:32713",
    "oa:32048",
    "oa:32420",
    "oa:35316",
    "oa:32684",
}
SCENARIOS = ("DS-LOW", "DS-MEDIUM", "DS-HIGH")


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
    def body(response: Any) -> Any:
        value = response.get_json(silent=True)
        return value if value is not None else response.get_data(as_text=True)

    def login(self, username: str, password: str) -> None:
        response = self.client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        if response.status_code != 200:
            raise ApiFailure("POST", "/api/auth/login", response.status_code, self.body(response))
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

    def request(self, method: str, path: str, *, body: Any = None, expected=(200,)) -> Any:
        headers = {}
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        response = self.client.open(path, method=method.upper(), json=body, headers=headers)
        if response.status_code not in expected:
            raise ApiFailure(method.upper(), path, response.status_code, self.body(response))
        return self.body(response)


def warn_interpreter() -> None:
    expected = PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if expected.is_file() and Path(sys.executable).resolve() != expected.resolve():
        print(
            f"[WARN] using {Path(sys.executable).resolve()} instead of project venv {expected.resolve()}"
        )


def assert_validation_db(path: Path) -> None:
    if path.name == DEFAULT_DB_NAME or "validation" not in path.name.lower():
        raise RuntimeError(
            f"refusing to run against non-validation database: {path}. Set AIRPORT_GROUP_DB_PATH to validation_work.sqlite3"
        )
    if not path.exists():
        raise FileNotFoundError(f"validation database not found: {path}")


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


def submit_and_execute(
    api: ValidationClient,
    worker: RunWorker,
    *,
    label: str,
    damage: str | None,
    cluster: bool,
) -> tuple[str, dict[str, Any]]:
    config = run_config(damage=damage, cluster=cluster)
    validation = api.request(
        "POST",
        "/api/runs/validate",
        body={"situation_id": SITUATION_ID, "run_config": config},
    )
    if not validation.get("can_submit", False):
        raise RuntimeError(f"{label} preflight failed: {validation}")
    expected_input_hash = validation.get("validated_input_hash")
    if not isinstance(expected_input_hash, str) or len(expected_input_hash) != 64:
        raise RuntimeError(f"{label} preflight returned invalid validated_input_hash")
    submit_body: dict[str, Any] = {
        "situation_id": SITUATION_ID,
        "run_config": config,
        "expected_input_hash": expected_input_hash,
    }
    record = api.request("POST", "/api/runs", body=submit_body, expected=(201,))
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError(f"{label} submit response has no run_id: {record}")
    if record.get("status") != "queued":
        raise RuntimeError(f"{label} submit did not create a queued Run: {record}")
    print(f"[RUN] {label}: {run_id}")

    try:
        terminal = worker.execute(run_id)
    except RunWorkerError as exc:
        raise RuntimeError(
            f"{label} worker could not claim/execute {run_id}: {exc}. "
            "Do not run a separate worker against validation_work.sqlite3 while this script is running."
        ) from exc
    if terminal.status != "succeeded":
        detail = api.request("GET", f"/api/runs/{quote(run_id, safe='')}")
        raise RuntimeError(f"{label} did not succeed: {detail}")

    run_path = f"/api/runs/{quote(run_id, safe='')}"
    detail = api.request("GET", run_path)
    solution = api.request("GET", f"{run_path}/solution")
    metrics = api.request("GET", f"{run_path}/metrics")
    snapshot_situation = api.request("GET", f"{run_path}/situation")
    api.request("GET", f"{run_path}/runtime")
    events = api.request("GET", f"{run_path}/events")

    if detail.get("run_id") != run_id or detail.get("status") != "succeeded":
        raise RuntimeError(f"{label} Run detail is inconsistent after success: {detail}")
    if solution.get("run_id") != run_id:
        raise RuntimeError(f"{label} Solution run_id mismatch")
    if metrics.get("run_id") != run_id:
        raise RuntimeError(f"{label} Metrics run_id mismatch")
    if snapshot_situation.get("situation_id") != SITUATION_ID:
        raise RuntimeError(f"{label} frozen Situation mismatch")
    if not any(row.get("event") == "run_succeeded" for row in events.get("events", [])):
        raise RuntimeError(f"{label} Run events do not contain run_succeeded")

    print(f"[OK] {label} succeeded")
    return run_id, {"detail": detail, "metrics": metrics}


def assert_clean_situation(api: ValidationClient) -> dict[str, Any]:
    situation = api.request("GET", f"/api/situations/{quote(SITUATION_ID, safe='')}")
    payload = situation.get("situation") or {}
    airport_ids = {row["airport"]["airport_id"] for row in payload.get("airports", [])}
    mission_ids = {row["mission_id"] for row in payload.get("missions", [])}
    damage_ids = {row["damage_scenario_id"] for row in payload.get("damage_scenarios", [])}
    if airport_ids != EXPECTED_AIRPORT_IDS:
        raise RuntimeError(f"unexpected validation airport set: {sorted(airport_ids)}")
    if mission_ids != {"N1", "N2", "N3"}:
        raise RuntimeError(f"unexpected validation mission set: {sorted(mission_ids)}")
    if damage_ids != set(SCENARIOS):
        raise RuntimeError(f"unexpected validation damage set: {sorted(damage_ids)}")

    query = urlencode({"situation_id": SITUATION_ID, "limit": 500})
    history = api.request("GET", f"/api/runs?{query}")
    if int(history.get("total") or 0) != 0:
        raise RuntimeError(
            f"validation Situation already has {history.get('total')} Run(s). "
            "Reset validation_work.sqlite3 before starting a new 7-run batch."
        )
    return situation


def assert_common_time_axis(summaries: dict[str, dict[str, Any]]) -> None:
    base = summaries["R0"]["metrics"].get("time_axis") or {}
    signature = (base.get("slot_minutes"), tuple(base.get("windows") or []))
    if not signature[1]:
        raise RuntimeError("R0 Metrics time axis is empty")
    for label, bundle in summaries.items():
        axis = bundle["metrics"].get("time_axis") or {}
        other = (axis.get("slot_minutes"), tuple(axis.get("windows") or []))
        if other != signature:
            raise RuntimeError(
                f"Metrics time axis differs for {label}; comparison candidate discovery would be incomplete"
            )
    print("[OK] all 7 Runs share one Metrics time axis")


def assert_comparison_candidates(api: ValidationClient, run_ids: dict[str, str]) -> None:
    damage_candidates = api.request("GET", "/api/results/damage-candidates")
    triples = {
        (row.get("r0_run_id"), row.get("r1_run_id"), row.get("r2_run_id"))
        for row in damage_candidates.get("items", [])
    }
    for scenario in SCENARIOS:
        expected = (
            run_ids["R0"],
            run_ids[f"{scenario}-R1"],
            run_ids[f"{scenario}-R2"],
        )
        if expected not in triples:
            raise RuntimeError(f"damage candidate discovery did not expose {scenario} R0/R1/R2")
    print("[OK] damage candidate discovery exposes all three R0/R1/R2 triples")

    scenario_query = urlencode({
        "base_run_id": run_ids["R0"],
        "mode": "multi_scenario",
    })
    comparable = api.request("GET", f"/api/results/comparable-runs?{scenario_query}")
    comparable_ids = {row.get("run_id") for row in comparable.get("items", [])}
    expected_scenarios = {run_ids[f"{scenario}-R1"] for scenario in SCENARIOS}
    if not expected_scenarios.issubset(comparable_ids):
        raise RuntimeError("multi-scenario comparable-run discovery is missing expected R1 Runs")
    print("[OK] multi-scenario comparable-run discovery")

    for scenario in SCENARIOS:
        query = urlencode({
            "base_run_id": run_ids[f"{scenario}-R1"],
            "mode": "configuration",
        })
        comparable = api.request("GET", f"/api/results/comparable-runs?{query}")
        comparable_ids = {row.get("run_id") for row in comparable.get("items", [])}
        if run_ids[f"{scenario}-R2"] not in comparable_ids:
            raise RuntimeError(f"configuration comparable-run discovery is missing {scenario}-R2")
    print("[OK] configuration comparable-run discovery")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the fixed R0/R1/R2 validation comparison set.")
    parser.add_argument("--username")
    parser.add_argument("--password")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    warn_interpreter()
    settings = AppSettings.from_environment()
    assert_validation_db(settings.db_path)
    username, password = credentials(args, settings)

    app = build_application(settings)
    api = ValidationClient(app)
    api.login(username, password)
    print(f"[OK] login {username}")

    assert_clean_situation(api)

    worker = build_worker(settings.db_path)
    run_ids: dict[str, str] = {}
    summaries: dict[str, dict[str, Any]] = {}

    run_ids["R0"], summaries["R0"] = submit_and_execute(
        api, worker, label="R0", damage=None, cluster=False
    )
    for scenario in SCENARIOS:
        r1_label = f"{scenario}-R1"
        r2_label = f"{scenario}-R2"
        run_ids[r1_label], summaries[r1_label] = submit_and_execute(
            api, worker, label=r1_label, damage=scenario, cluster=False
        )
        run_ids[r2_label], summaries[r2_label] = submit_and_execute(
            api, worker, label=r2_label, damage=scenario, cluster=True
        )

    assert_common_time_axis(summaries)

    print("\n=== COMPARISON API CHECKS ===")
    for scenario in SCENARIOS:
        api.request(
            "POST",
            "/api/results/damage-comparison",
            body={
                "r0_run_id": run_ids["R0"],
                "r1_run_id": run_ids[f"{scenario}-R1"],
                "r2_run_id": run_ids[f"{scenario}-R2"],
            },
        )
        print(f"[OK] damage comparison {scenario}")

        api.request(
            "POST",
            "/api/results/config-comparison",
            body={
                "run_ids": [run_ids[f"{scenario}-R1"], run_ids[f"{scenario}-R2"]],
                "baseline_run_id": run_ids[f"{scenario}-R1"],
            },
        )
        print(f"[OK] configuration comparison {scenario}")

    scenario_run_ids = [run_ids["R0"]] + [run_ids[f"{s}-R1"] for s in SCENARIOS]
    api.request(
        "POST", "/api/results/scenario-comparison", body={"run_ids": scenario_run_ids}
    )
    print("[OK] multi-scenario comparison")
    assert_comparison_candidates(api, run_ids)

    print("\n=== RUN IDS ===")
    for label, run_id in run_ids.items():
        print(f"{label:16s} {run_id}")
    print("\n[OK] 7-run validation combination completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
