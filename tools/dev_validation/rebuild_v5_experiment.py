from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.run_snapshot import RunSnapshot
from backend.auth.principal import Principal
from backend.domain.assessment import AssessmentResult
from backend.services.run_assessment_projection import project_run_capacity_assessment
from backend.services.run_result_service import RunResultService
from backend.services.run_service import RunService
from backend.services.run_snapshot_service import RunSnapshotService
from backend.storage.database import initialize_database
from backend.storage.assessment_repository import AssessmentRepository
from backend.storage.airport_repository import AirportRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.situation_repository import SituationRepository
from backend.services.run_worker import RunWorker
from backend.web.composition import build_results_api


class ExperimentDefinitionError(RuntimeError):
    """Raised when the frozen experiment definition or archive is inconsistent."""


@dataclass(frozen=True)
class RunDefinition:
    label: str
    run_id: str
    situation_id: str
    damage_scenario_id: str | None
    cluster_enabled: bool
    snapshot_hash: str
    archived_solution_hash: str
    archived_metrics_hash: str


@dataclass(frozen=True)
class ExperimentDefinition:
    schema_version: str
    experiment_id: str
    archive_sha256: str
    required_counts: Mapping[str, int]
    owner_user_id: str
    situations: tuple[str, ...]
    damage_scenarios: tuple[str, ...]
    runs: tuple[RunDefinition, ...]
    comparisons: Mapping[str, Any]


@dataclass(frozen=True)
class ArchiveBundle:
    snapshots: Mapping[str, RunSnapshot]
    solutions: Mapping[str, Mapping[str, Any]]
    metrics: Mapping[str, Mapping[str, Any]]


InventoryFacts = Dict[str, Dict[str, Dict[str, Dict[str, float]]]]


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentDefinitionError(f"{field} must be a nonblank string")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(raw: str, field: str) -> Dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExperimentDefinitionError(f"{field} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ExperimentDefinitionError(f"{field} must contain a JSON object")
    return value


def load_definition(path: str | Path) -> ExperimentDefinition:
    manifest_path = Path(path)
    data = _json_object(manifest_path.read_text(encoding="utf-8"), "manifest")
    if data.get("schema_version") != "jiangsu-six-airport-v5.v1":
        raise ExperimentDefinitionError("unsupported experiment manifest schema")
    archive = data.get("archive")
    if not isinstance(archive, Mapping):
        raise ExperimentDefinitionError("archive must be an object")
    counts = archive.get("required_counts")
    if not isinstance(counts, Mapping):
        raise ExperimentDefinitionError("archive.required_counts must be an object")

    raw_runs = data.get("runs")
    if not isinstance(raw_runs, list):
        raise ExperimentDefinitionError("runs must be an array")
    runs: list[RunDefinition] = []
    for index, raw in enumerate(raw_runs):
        if not isinstance(raw, Mapping):
            raise ExperimentDefinitionError(f"runs[{index}] must be an object")
        damage_id = raw.get("damage_scenario_id")
        if damage_id is not None:
            damage_id = _require_text(damage_id, f"runs[{index}].damage_scenario_id")
        cluster_enabled = raw.get("cluster_enabled")
        if not isinstance(cluster_enabled, bool):
            raise ExperimentDefinitionError(f"runs[{index}].cluster_enabled must be bool")
        runs.append(
            RunDefinition(
                label=_require_text(raw.get("label"), f"runs[{index}].label"),
                run_id=_require_text(raw.get("run_id"), f"runs[{index}].run_id"),
                situation_id=_require_text(
                    raw.get("situation_id"), f"runs[{index}].situation_id"
                ),
                damage_scenario_id=damage_id,
                cluster_enabled=cluster_enabled,
                snapshot_hash=_require_text(
                    raw.get("snapshot_hash"), f"runs[{index}].snapshot_hash"
                ),
                archived_solution_hash=_require_text(
                    raw.get("archived_solution_hash"),
                    f"runs[{index}].archived_solution_hash",
                ),
                archived_metrics_hash=_require_text(
                    raw.get("archived_metrics_hash"),
                    f"runs[{index}].archived_metrics_hash",
                ),
            )
        )

    labels = [run.label for run in runs]
    run_ids = [run.run_id for run in runs]
    if len(runs) != 11 or len(set(labels)) != len(labels) or len(set(run_ids)) != len(run_ids):
        raise ExperimentDefinitionError("V5 manifest must contain 11 uniquely labelled Runs")
    situations = tuple(data.get("situations") or ())
    scenarios = tuple(data.get("damage_scenarios") or ())
    if len(situations) != 2 or len(set(situations)) != 2:
        raise ExperimentDefinitionError("V5 manifest must contain two unique Situations")
    if len(scenarios) != 5 or len(set(scenarios)) != 5:
        raise ExperimentDefinitionError("V5 manifest must contain five unique damage scenarios")
    if any(run.situation_id not in situations for run in runs):
        raise ExperimentDefinitionError("Run references a Situation outside the frozen set")
    if any(
        run.damage_scenario_id is not None and run.damage_scenario_id not in scenarios
        for run in runs
    ):
        raise ExperimentDefinitionError("Run references a damage scenario outside the frozen set")

    return ExperimentDefinition(
        schema_version=data["schema_version"],
        experiment_id=_require_text(data.get("experiment_id"), "experiment_id"),
        archive_sha256=_require_text(archive.get("sha256"), "archive.sha256"),
        required_counts={str(key): int(value) for key, value in counts.items()},
        owner_user_id=_require_text(data.get("owner_user_id"), "owner_user_id"),
        situations=tuple(_require_text(value, "situations[]") for value in situations),
        damage_scenarios=tuple(
            _require_text(value, "damage_scenarios[]") for value in scenarios
        ),
        runs=tuple(runs),
        comparisons=dict(data.get("comparisons") or {}),
    )


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def verify_archive(
    path: str | Path,
    definition: ExperimentDefinition,
    *,
    verify_file_hash: bool = True,
) -> ArchiveBundle:
    archive_path = Path(path).resolve()
    if not archive_path.is_file():
        raise ExperimentDefinitionError(f"archive does not exist: {archive_path}")
    if verify_file_hash and _sha256_file(archive_path) != definition.archive_sha256:
        raise ExperimentDefinitionError("archive SHA256 does not match the frozen manifest")

    snapshots: dict[str, RunSnapshot] = {}
    solutions: dict[str, Mapping[str, Any]] = {}
    metrics: dict[str, Mapping[str, Any]] = {}
    with closing(_open_read_only(archive_path)) as connection:
        table_queries = {
            "airports": "SELECT COUNT(*) FROM airports",
            "situations": "SELECT COUNT(*) FROM situations",
            "situation_damage_scenarios": "SELECT COUNT(*) FROM situation_damage_scenarios",
            "runs": "SELECT COUNT(*) FROM runs",
            "run_input_snapshots": "SELECT COUNT(*) FROM run_input_snapshots",
            "run_results": "SELECT COUNT(*) FROM run_results",
        }
        for name, expected in definition.required_counts.items():
            query = table_queries.get(name)
            if query is None:
                raise ExperimentDefinitionError(f"unsupported archive count: {name}")
            actual = int(connection.execute(query).fetchone()[0])
            if actual != expected:
                raise ExperimentDefinitionError(
                    f"archive {name} count mismatch: expected {expected}, found {actual}"
                )

        for run in definition.runs:
            row = connection.execute(
                """
                SELECT r.owner_user_id, r.situation_id, r.status, r.snapshot_hash,
                       s.payload_json, rr.solution_hash, rr.metrics_hash,
                       rr.solution_json, rr.metrics_json
                FROM runs r
                JOIN run_input_snapshots s ON s.run_id = r.run_id
                JOIN run_results rr ON rr.run_id = r.run_id
                WHERE r.run_id = ?
                """,
                (run.run_id,),
            ).fetchone()
            if row is None:
                raise ExperimentDefinitionError(f"archive is missing Run {run.run_id}")
            actual = {
                "owner_user_id": row["owner_user_id"],
                "situation_id": row["situation_id"],
                "status": row["status"],
                "snapshot_hash": row["snapshot_hash"],
                "solution_hash": row["solution_hash"],
                "metrics_hash": row["metrics_hash"],
            }
            expected = {
                "owner_user_id": definition.owner_user_id,
                "situation_id": run.situation_id,
                "status": "succeeded",
                "snapshot_hash": run.snapshot_hash,
                "solution_hash": run.archived_solution_hash,
                "metrics_hash": run.archived_metrics_hash,
            }
            if actual != expected:
                raise ExperimentDefinitionError(
                    f"archive Run contract mismatch for {run.run_id}: {actual!r}"
                )
            snapshot = RunSnapshot(
                run_id=run.run_id,
                situation_id=run.situation_id,
                content_hash=run.snapshot_hash,
                payload_json=row["payload_json"],
            )
            config = snapshot.to_dict().get("run_config") or {}
            if config.get("damage_scenario_id") != run.damage_scenario_id:
                raise ExperimentDefinitionError(
                    f"archive damage configuration mismatch for {run.run_id}"
                )
            if config.get("cluster_enabled") is not run.cluster_enabled:
                raise ExperimentDefinitionError(
                    f"archive cluster configuration mismatch for {run.run_id}"
                )
            snapshots[run.run_id] = snapshot
            solutions[run.run_id] = _json_object(
                row["solution_json"], f"{run.run_id}.solution_json"
            )
            metrics[run.run_id] = _json_object(
                row["metrics_json"], f"{run.run_id}.metrics_json"
            )
    return ArchiveBundle(snapshots=snapshots, solutions=solutions, metrics=metrics)


def _frozen_nonnegative_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExperimentDefinitionError(f"{field} must be a finite nonnegative number")
    canonical = float(value)
    if not math.isfinite(canonical) or canonical < 0:
        raise ExperimentDefinitionError(f"{field} must be a finite nonnegative number")
    return canonical


def _snapshot_inventory_facts(snapshot: RunSnapshot) -> Dict[str, Dict[str, Dict[str, float]]]:
    payload = snapshot.to_dict()
    situation = payload.get("situation")
    if not isinstance(situation, Mapping):
        raise ExperimentDefinitionError(f"{snapshot.run_id}.situation must be an object")
    dynamic_inventory = payload.get("dynamic_inventory")
    if not isinstance(dynamic_inventory, Mapping):
        raise ExperimentDefinitionError(
            f"{snapshot.run_id}.dynamic_inventory must be an object"
        )
    frozen_storage = dynamic_inventory.get("storage_capacity")
    if not isinstance(frozen_storage, Mapping):
        raise ExperimentDefinitionError(
            f"{snapshot.run_id}.dynamic_inventory.storage_capacity must be an object"
        )

    facts: Dict[str, Dict[str, Dict[str, float]]] = {}
    for airport_index, item in enumerate(situation.get("airports") or []):
        if not isinstance(item, Mapping):
            raise ExperimentDefinitionError(
                f"{snapshot.run_id}.situation.airports[{airport_index}] must be an object"
            )
        airport = item.get("airport")
        profile = item.get("operational_profile")
        if not isinstance(airport, Mapping) or not isinstance(profile, Mapping):
            raise ExperimentDefinitionError(
                f"{snapshot.run_id}.situation.airports[{airport_index}] is incomplete"
            )
        airport_id = _require_text(
            airport.get("airport_id"),
            f"{snapshot.run_id}.situation.airports[{airport_index}].airport_id",
        )
        if airport_id in facts:
            raise ExperimentDefinitionError(
                f"{snapshot.run_id} has duplicate airport inventory facts: {airport_id}"
            )
        dynamic_rows = frozen_storage.get(airport_id)
        if not isinstance(dynamic_rows, Mapping):
            raise ExperimentDefinitionError(
                f"{snapshot.run_id}.dynamic_inventory.storage_capacity.{airport_id} is missing"
            )
        facts[airport_id] = {}
        for stock_index, stock in enumerate(profile.get("resource_stocks") or []):
            if not isinstance(stock, Mapping):
                raise ExperimentDefinitionError(
                    f"{snapshot.run_id}.{airport_id}.resource_stocks[{stock_index}] must be an object"
                )
            resource_id = _require_text(
                stock.get("resource_type_id"),
                f"{snapshot.run_id}.{airport_id}.resource_stocks[{stock_index}].resource_type_id",
            )
            if resource_id in facts[airport_id]:
                raise ExperimentDefinitionError(
                    f"{snapshot.run_id} has duplicate inventory facts: {airport_id}/{resource_id}"
                )
            initial = _frozen_nonnegative_number(
                stock.get("initial_quantity"),
                f"{snapshot.run_id}.{airport_id}.{resource_id}.initial_quantity",
            )
            replenishment = _frozen_nonnegative_number(
                stock.get("replenishment_capacity_per_window"),
                f"{snapshot.run_id}.{airport_id}.{resource_id}.replenishment_capacity_per_window",
            )
            storage = _frozen_nonnegative_number(
                stock.get("storage_capacity"),
                f"{snapshot.run_id}.{airport_id}.{resource_id}.storage_capacity",
            )
            if initial > storage + 1e-9:
                raise ExperimentDefinitionError(
                    f"{snapshot.run_id} initial inventory exceeds storage_capacity: "
                    f"{airport_id}/{resource_id}"
                )
            dynamic_storage = _frozen_nonnegative_number(
                dynamic_rows.get(resource_id),
                f"{snapshot.run_id}.dynamic_inventory.storage_capacity."
                f"{airport_id}.{resource_id}",
            )
            if abs(dynamic_storage - storage) > 1e-9:
                raise ExperimentDefinitionError(
                    f"{snapshot.run_id} storage_capacity sources disagree: "
                    f"{airport_id}/{resource_id}"
                )
            facts[airport_id][resource_id] = {
                "initial_quantity": initial,
                "replenishment_capacity_per_window": replenishment,
                "storage_capacity": storage,
            }
    if not facts:
        raise ExperimentDefinitionError(f"{snapshot.run_id} has no frozen inventory facts")
    return facts


def extract_frozen_inventory_facts(
    bundle: ArchiveBundle,
    definition: ExperimentDefinition,
) -> InventoryFacts:
    """Recover V5 base inventory only when every frozen Snapshot agrees."""

    by_situation: InventoryFacts = {}
    source_run: Dict[str, str] = {}
    for run in definition.runs:
        snapshot = bundle.snapshots.get(run.run_id)
        if snapshot is None:
            raise ExperimentDefinitionError(f"archive is missing Snapshot {run.run_id}")
        if snapshot.situation_id != run.situation_id:
            raise ExperimentDefinitionError(
                f"snapshot Situation mismatch for {run.run_id}"
            )
        facts = _snapshot_inventory_facts(snapshot)
        existing = by_situation.get(run.situation_id)
        if existing is not None and existing != facts:
            raise ExperimentDefinitionError(
                "inconsistent frozen inventory facts for Situation "
                f"{run.situation_id}: {source_run[run.situation_id]} vs {run.run_id}"
            )
        by_situation[run.situation_id] = facts
        source_run.setdefault(run.situation_id, run.run_id)
    missing = sorted(set(definition.situations) - set(by_situation))
    if missing:
        raise ExperimentDefinitionError(
            f"frozen inventory facts missing for Situations: {missing}"
        )
    return by_situation


def _restore_frozen_inventory_facts(
    database: Path,
    facts_by_situation: InventoryFacts,
) -> None:
    base_facts: Dict[str, Dict[str, Dict[str, float]]] = {}
    base_source: Dict[tuple[str, str], str] = {}
    for situation_id, airport_rows in facts_by_situation.items():
        for airport_id, resource_rows in airport_rows.items():
            base_airport = base_facts.setdefault(airport_id, {})
            for resource_id, facts in resource_rows.items():
                key = (airport_id, resource_id)
                existing = base_airport.get(resource_id)
                if existing is not None and existing != facts:
                    raise ExperimentDefinitionError(
                        "inconsistent frozen base-airport inventory facts across Situations: "
                        f"{airport_id}/{resource_id} "
                        f"({base_source[key]} vs {situation_id})"
                    )
                base_airport[resource_id] = facts
                base_source.setdefault(key, situation_id)

    with closing(sqlite3.connect(database)) as connection:
        actual_base = {
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT airport_id,resource_type_id FROM airport_resource_stocks"
            )
        }
        expected_base = {
            (airport_id, resource_id)
            for airport_id, resource_rows in base_facts.items()
            for resource_id in resource_rows
        }
        if actual_base != expected_base:
            raise ExperimentDefinitionError(
                "frozen base-airport inventory keys do not match destination database"
            )

        actual_situations = {
            (str(row[0]), str(row[1]), str(row[2]))
            for row in connection.execute(
                "SELECT situation_id,airport_id,resource_type_id "
                "FROM situation_resource_stocks"
            )
        }
        expected_situations = {
            (situation_id, airport_id, resource_id)
            for situation_id, airport_rows in facts_by_situation.items()
            for airport_id, resource_rows in airport_rows.items()
            for resource_id in resource_rows
        }
        if actual_situations != expected_situations:
            raise ExperimentDefinitionError(
                "frozen Situation inventory keys do not match destination database"
            )

        for airport_id, resource_rows in base_facts.items():
            for resource_id, facts in resource_rows.items():
                connection.execute(
                    "UPDATE airport_resource_stocks SET quantity=?, "
                    "replenishment_capacity_per_window=?, storage_capacity=? "
                    "WHERE airport_id=? AND resource_type_id=?",
                    (
                        facts["initial_quantity"],
                        facts["replenishment_capacity_per_window"],
                        facts["storage_capacity"],
                        airport_id,
                        resource_id,
                    ),
                )
        for situation_id, airport_rows in facts_by_situation.items():
            for airport_id, resource_rows in airport_rows.items():
                for resource_id, facts in resource_rows.items():
                    connection.execute(
                        "UPDATE situation_resource_stocks SET quantity=?, "
                        "replenishment_capacity_per_window=?, storage_capacity=? "
                        "WHERE situation_id=? AND airport_id=? AND resource_type_id=?",
                        (
                            facts["initial_quantity"],
                            facts["replenishment_capacity_per_window"],
                            facts["storage_capacity"],
                            situation_id,
                            airport_id,
                            resource_id,
                        ),
                    )
        connection.commit()


def prepare_experiment_database(
    *,
    archive_path: str | Path,
    destination: str | Path,
    definition: ExperimentDefinition,
) -> Path:
    source = Path(archive_path).resolve()
    target = Path(destination).resolve()
    if target.exists():
        raise ExperimentDefinitionError(f"refusing to overwrite experiment database: {target}")
    bundle = verify_archive(source, definition)
    inventory_facts = extract_frozen_inventory_facts(bundle, definition)
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(_open_read_only(source)) as source_connection:
        with closing(sqlite3.connect(target)) as destination_connection:
            source_connection.backup(destination_connection)
    initialize_database(target)
    _restore_frozen_inventory_facts(target, inventory_facts)
    with closing(sqlite3.connect(target)) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        raise ExperimentDefinitionError(f"experiment database quick_check failed: {result}")
    return target


def queue_experiment_runs(
    database: str | Path,
    definition: ExperimentDefinition,
) -> Mapping[str, RunSnapshot]:
    database_path = Path(database).resolve()
    bundle = verify_archive(database_path, definition, verify_file_hash=False)
    repository = RunRepository(database_path)
    for situation_id in definition.situations:
        run_ids = [run.run_id for run in definition.runs if run.situation_id == situation_id]
        repository.delete_terminal_batch(
            run_ids=run_ids,
            owner_user_id=definition.owner_user_id,
            situation_id=situation_id,
        )
    for run in definition.runs:
        repository.create_queued(
            snapshot=bundle.snapshots[run.run_id],
            owner_user_id=definition.owner_user_id,
        )
    return bundle.snapshots


def _build_worker(database: Path) -> RunWorker:
    airports = AirportRepository(database)
    situations = SituationRepository(database)
    snapshots = RunSnapshotRepository(database)
    runs = RunRepository(database)
    snapshot_service = RunSnapshotService(
        airport_repository=airports,
        situation_repository=situations,
        snapshot_repository=snapshots,
    )
    return RunWorker(
        run_service=RunService(
            snapshot_service=snapshot_service,
            run_repository=runs,
        ),
        result_service=RunResultService(
            run_repository=runs,
            snapshot_repository=snapshots,
        ),
        snapshot_repository=snapshots,
    )


def execute_experiment_batch(
    database: str | Path,
    definition: ExperimentDefinition,
) -> Dict[str, Any]:
    database_path = Path(database).resolve()
    repository = RunRepository(database_path)
    worker = _build_worker(database_path)
    rows: list[Dict[str, Any]] = []
    for ordinal, run in enumerate(definition.runs, start=1):
        print(f"[{ordinal:02d}/{len(definition.runs):02d}] solving {run.label} {run.run_id}", flush=True)
        record = worker.execute(run.run_id)
        if record.status != "succeeded":
            raise ExperimentDefinitionError(
                f"Run {run.run_id} did not succeed: {record.failure_code} {record.failure_message}"
            )
        result = repository.get_result_payloads(run.run_id)
        if result is None:
            raise ExperimentDefinitionError(f"Run {run.run_id} has no persisted result")
        solution, metrics = result
        if record.solution_hash != run.archived_solution_hash:
            raise ExperimentDefinitionError(
                f"Run {run.run_id} solution drift: "
                f"expected {run.archived_solution_hash}, found {record.solution_hash}"
            )
        summary = metrics.get("summary") or {}
        technical = metrics.get("technical") or {}
        row = {
            "label": run.label,
            "run_id": run.run_id,
            "snapshot_hash": record.snapshot_hash,
            "solution_hash": record.solution_hash,
            "solution_matches_archive": True,
            "metrics_hash": record.metrics_hash,
            "archived_metrics_hash": run.archived_metrics_hash,
            "metrics_matches_archive": record.metrics_hash == run.archived_metrics_hash,
            "summary": summary,
            "technical": technical,
        }
        rows.append(row)
        print(
            f"    succeeded solution={record.solution_hash[:12]} "
            f"metrics={str(record.metrics_hash)[:12]}",
            flush=True,
        )
    return {
        "schema_version": "jiangsu-six-airport-v5-result-index.v1",
        "experiment_id": definition.experiment_id,
        "source_archive_sha256": definition.archive_sha256,
        "runs": rows,
    }


def _comparison_fact(body: Mapping[str, Any]) -> Mapping[str, Any]:
    focus = body.get("focus_comparison")
    if not isinstance(focus, Mapping):
        raise ExperimentDefinitionError("comparison response has no focus_comparison")
    if focus.get("eligible") is not True:
        raise ExperimentDefinitionError(
            f"strict comparison is not eligible: {focus.get('reasons')!r}"
        )
    return focus


def _comparison_conclusion(focus: Mapping[str, Any]) -> str:
    values = focus.get("values") if isinstance(focus.get("values"), Mapping) else {}
    objective = (
        focus.get("objective_components")
        if isinstance(focus.get("objective_components"), Mapping) else {}
    )
    fulfilled = values.get("fulfilled_sorties_total") or {}
    unmet = values.get("unmet_sorties_total") or {}
    scheduled = values.get("scheduled_sorties_total") or {}
    j_value = objective.get("J") or {}
    parts = [
        f"履行 {fulfilled.get('baseline', '未记录')}→{fulfilled.get('current', '未记录')}",
        f"缺口 {unmet.get('baseline', '未记录')}→{unmet.get('current', '未记录')}",
        f"出动 {scheduled.get('baseline', '未记录')}→{scheduled.get('current', '未记录')}",
    ]
    if j_value.get("recorded") is True and j_value.get("definition_comparable") is True:
        parts.append(f"J差值 {float(j_value.get('delta', 0.0)):.6f}")
    else:
        parts.append("J不可直接比较")
    return "；".join(parts)


def validate_experiment_comparisons(
    database: str | Path,
    definition: ExperimentDefinition,
    *,
    persist_records: bool = True,
) -> Dict[str, Any]:
    """Validate strict research pairs; persist_records writes an isolated JSON artifact only."""
    api = build_results_api(database)
    principal = Principal(definition.owner_user_id, is_admin=True)
    run_ids = {run.label: run.run_id for run in definition.runs}
    checks: list[Dict[str, Any]] = []
    records: dict[tuple[str, str], tuple[str, Mapping[str, Any]]] = {}

    for labels in definition.comparisons.get("damage_and_cluster", []):
        response = api.damage_comparison(
            {
                "r0_run_id": run_ids[labels[0]],
                "r1_run_id": run_ids[labels[1]],
                "r2_run_id": run_ids[labels[2]],
            },
            principal=principal,
        )
        if response.status != 200:
            raise ExperimentDefinitionError(f"damage comparison failed: {response.body!r}")
        comparison_map = response.body.get("comparisons") or {}
        relations = (
            (labels[0], labels[1], comparison_map.get("damage")),
            (labels[1], labels[2], comparison_map.get("cluster")),
        )
        for base, current, focus in relations:
            if not isinstance(focus, Mapping) or focus.get("eligible") is not True:
                raise ExperimentDefinitionError(
                    f"strict damage comparison {base}->{current} is not eligible"
                )
            records[(base, current)] = ("damage_and_cluster", focus)
        checks.append({"mode": "damage_and_cluster", "labels": list(labels), "eligible": True})

    multi_labels = list(definition.comparisons.get("multi_damage", []))
    for current in multi_labels[1:]:
        response = api.scenario_comparison(
            {
                "run_ids": [run_ids[label] for label in multi_labels],
                "baseline_run_id": run_ids[multi_labels[0]],
                "comparison_run_id": run_ids[current],
            },
            principal=principal,
        )
        if response.status != 200:
            raise ExperimentDefinitionError(f"multi-damage comparison failed: {response.body!r}")
        focus = _comparison_fact(response.body)
        records[(multi_labels[0], current)] = ("multi_damage", focus)
    checks.append({"mode": "multi_damage", "labels": multi_labels, "eligible": True})

    for labels in definition.comparisons.get("configuration", []):
        response = api.configuration_comparison(
            {
                "run_ids": [run_ids[label] for label in labels],
                "baseline_run_id": run_ids[labels[0]],
                "comparison_run_id": run_ids[labels[1]],
            },
            principal=principal,
        )
        if response.status != 200:
            raise ExperimentDefinitionError(f"configuration comparison failed: {response.body!r}")
        focus = _comparison_fact(response.body)
        records[(labels[0], labels[1])] = ("configuration", focus)
        checks.append({"mode": "configuration", "labels": list(labels), "eligible": True})

    # Preserve the historical label-pair projection: 12 candidates collapsed to 9
    # because mode was absent from its identity. This is research evidence only.
    evidence = [{"base_label":base, "compared_label":current, "mode":mode,
                 "focus_comparison":focus} for (base,current),(mode,focus) in sorted(records.items())]
    artifact = Path(database).with_suffix(".comparison-research-evidence.json")
    if persist_records:
        artifact.write_text(json.dumps({"schema_version":"v5-research-comparisons.v1",
            "historical_identity":"ordered label pair, excluding mode; later modes replaced earlier evidence",
            "historical_candidate_count":12, "historical_unique_pair_count":len(records),
            "records":evidence}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"checks":checks, "persisted_records":0, "research_evidence_count":len(records),
            "research_artifact":str(artifact) if persist_records else None}



def project_experiment_assessment(
    database: str | Path,
    definition: ExperimentDefinition,
) -> Dict[str, Any]:
    run_repository = RunRepository(database)
    snapshot_repository = RunSnapshotRepository(database)
    assessment_repository = AssessmentRepository(database)
    rows: list[AssessmentResult] = []
    for run in definition.runs:
        rows.extend(
            project_run_capacity_assessment(
                run_id=run.run_id,
                scenario_label=run.label,
                result_id_prefix="v5cap",
                run_repository=run_repository,
                snapshot_repository=snapshot_repository,
                assessment_repository=assessment_repository,
            )
        )
    return {
        "metric_kind": "capacity_availability_v1",
        "matrix_metric": "运行期容量占用率=有效时间窗使用量之和/同期可用容量之和",
        "curve_metric": "逐窗起降容量可用比例=本窗可用容量/冻结基准容量",
        "result_count": len(rows),
        "risk": {
            "status": "unavailable",
            "reason": "未配置独立风险评估模型或正式专家评分",
        },
    }


def write_result_index(path: str | Path, result_index: Mapping[str, Any]) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result_index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify and prepare the frozen Jiangsu six-airport V5 experiment"
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--result-index", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    definition = load_definition(args.manifest)
    bundle = verify_archive(args.archive, definition)
    print(
        json.dumps(
            {
                "experiment_id": definition.experiment_id,
                "archive_sha256": definition.archive_sha256,
                "verified_runs": len(bundle.snapshots),
            },
            ensure_ascii=False,
        )
    )
    if args.verify_only:
        return 0
    if args.database is None:
        raise ExperimentDefinitionError("--database is required unless --verify-only is used")
    prepared = prepare_experiment_database(
        archive_path=args.archive,
        destination=args.database,
        definition=definition,
    )
    queue_experiment_runs(prepared, definition)
    print(json.dumps({"prepared_database": str(prepared), "queued_runs": 11}, ensure_ascii=False))
    if args.execute:
        result_index = execute_experiment_batch(prepared, definition)
        result_index["comparisons"] = validate_experiment_comparisons(
            prepared, definition
        )
        result_index["assessment"] = project_experiment_assessment(
            prepared, definition
        )
        if args.result_index is None:
            raise ExperimentDefinitionError("--result-index is required with --execute")
        written = write_result_index(args.result_index, result_index)
        print(json.dumps({"result_index": str(written)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
