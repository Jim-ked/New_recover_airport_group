from __future__ import annotations

from pathlib import Path

from backend.services.od_distance_service import ODDistanceService
from backend.services.run_result_service import RunResultService
from backend.services.run_runtime_service import RunRuntimeService
from backend.services.run_service import RunService
from backend.services.run_snapshot_service import RunSnapshotService
from backend.services.run_submission_service import (
    RunSubmissionService,
    SolverProbe,
    default_solver_probe,
)
from backend.storage.workspace_airport_repository import WorkspaceAirportRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.situation_repository import SituationRepository
from backend.storage.mission_repository import MissionRepository
from backend.storage.indicator_repository import IndicatorRepository
from backend.storage.audit_repository import AuditRepository
from backend.storage.user_repository import UserRepository
from backend.web.run_api import RunApi, RunIdFactory, default_run_id_factory
from backend.web.results_api import ResultsApi
from backend.web.situation_api import SituationApi
from backend.web.catalog_api import CatalogApi
from backend.web.indicator_api import IndicatorApi
from backend.web.account_api import AccountApi
from backend.web.audit_api import AuditApi


def build_run_api(
    db_path: str | Path,
    *,
    run_id_factory: RunIdFactory = default_run_id_factory,
    solver_probe: SolverProbe = default_solver_probe,
) -> RunApi:
    airports = WorkspaceAirportRepository(db_path)
    situations = SituationRepository(db_path)
    snapshots = RunSnapshotRepository(db_path)
    runs = RunRepository(db_path)

    snapshot_service = RunSnapshotService(
        airport_repository=airports,
        situation_repository=situations,
        snapshot_repository=snapshots,
    )
    run_service = RunService(snapshot_service=snapshot_service, run_repository=runs)
    result_service = RunResultService(
        run_repository=runs,
        snapshot_repository=snapshots,
    )
    runtime_service = RunRuntimeService(result_service=result_service)
    submission_service = RunSubmissionService(
        situation_repository=situations,
        snapshot_repository=snapshots,
        snapshot_service=snapshot_service,
        run_service=run_service,
        od_distance_service=ODDistanceService(),
        solver_probe=solver_probe,
    )
    return RunApi(
        submission_service=submission_service,
        run_service=run_service,
        result_service=result_service,
        runtime_service=runtime_service,
        run_id_factory=run_id_factory,
    )


def build_results_api(db_path: str | Path) -> ResultsApi:
    snapshots = RunSnapshotRepository(db_path)
    runs = RunRepository(db_path)
    result_service = RunResultService(
        run_repository=runs,
        snapshot_repository=snapshots,
    )
    return ResultsApi(result_service=result_service)


def build_situation_api(db_path: str | Path) -> SituationApi:
    return SituationApi(
        situation_repository=SituationRepository(db_path),
        airport_repository=WorkspaceAirportRepository(db_path),
        mission_repository=MissionRepository(db_path),
    )


def build_catalog_api(db_path: str | Path) -> CatalogApi:
    return CatalogApi(
        airport_repository=WorkspaceAirportRepository(db_path),
        mission_repository=MissionRepository(db_path),
        run_repository=RunRepository(db_path),
        snapshot_repository=RunSnapshotRepository(db_path),
    )


def build_indicator_api(db_path: str | Path) -> IndicatorApi:
    return IndicatorApi(repository=IndicatorRepository(db_path))


def build_account_api(db_path: str | Path | None = None) -> AccountApi:
    repository = UserRepository(db_path) if db_path is not None else None
    return AccountApi(user_repository=repository)


def build_user_repository(db_path: str | Path) -> UserRepository:
    return UserRepository(db_path)


def build_audit_api(db_path: str | Path) -> AuditApi:
    return AuditApi(repository=AuditRepository(db_path))


__all__ = [
    "build_run_api", "build_results_api", "build_situation_api",
    "build_catalog_api", "build_indicator_api", "build_account_api", "build_audit_api",
    "build_user_repository",
]
