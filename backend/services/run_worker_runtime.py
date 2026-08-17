from __future__ import annotations

from pathlib import Path

from backend.services.run_result_service import RunResultService
from backend.services.run_service import RunService
from backend.services.run_snapshot_service import RunSnapshotService
from backend.services.run_worker import RunWorker
from backend.services.run_worker_loop import RunWorkerLoop
from backend.storage.airport_repository import AirportRepository
from backend.storage.run_queue_repository import RunQueueRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.situation_repository import SituationRepository


def build_run_worker_loop(
    db_path: str | Path,
    *,
    poll_interval_s: float = 1.0,
) -> RunWorkerLoop:
    """Compose the background worker against the same SQLite authority as the Web app."""
    airports = AirportRepository(db_path)
    situations = SituationRepository(db_path)
    snapshots = RunSnapshotRepository(db_path)
    runs = RunQueueRepository(db_path)

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
    worker = RunWorker(
        run_service=run_service,
        result_service=result_service,
        snapshot_repository=snapshots,
    )
    return RunWorkerLoop(
        queue_repository=runs,
        worker=worker,
        poll_interval_s=poll_interval_s,
    )


__all__ = ["build_run_worker_loop"]
