from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from backend.services.run_assessment_projection import prepare_run_capacity_results, RunAssessmentProjectionError
from backend.storage.assessment_repository import AssessmentRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository


class RunCapacityAssessmentService:
    """Generate descriptive capacity artifacts from verified, immutable Run facts."""

    def __init__(self, *, run_repository: RunRepository,
                 snapshot_repository: RunSnapshotRepository,
                 assessment_repository: AssessmentRepository):
        self.runs = run_repository
        self.snapshots = snapshot_repository
        self.assessments = assessment_repository

    def generate_capacity_assessment(self, run_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        result = {"run_id": run_id, "result_count": 0, "generated": 0, "skipped": 0,
                  "conflict": 0, "failed": 0, "provenance": None, "dry_run": dry_run}
        try:
            rows = prepare_run_capacity_results(
                run_id=run_id, run_repository=self.runs, snapshot_repository=self.snapshots,
            )
            result.update(result_count=len(rows), provenance={
                "source_run_id": run_id, "snapshot_hash": rows[0].snapshot_hash,
                "metrics_hash": rows[0].metrics_hash,
            })
            result.update(self.assessments.persist_run_capacity_results(rows, dry_run=dry_run))
        except (RunAssessmentProjectionError, ValueError) as exc:
            result.update(status="unavailable", reason=str(exc), failed=1)
        except Exception as exc:
            logging.getLogger("airport_group.assessment").exception("capacity projection failed run_id=%s", run_id)
            result.update(status="failed", reason=str(exc), failed=1)
        return result


def backfill_capacity(
    db_path: str | Path, *, run_ids: list[str] | None = None,
    all_succeeded: bool = False, dry_run: bool = False,
) -> dict[str, Any]:
    """Maintenance entry point; never initializes schema or reruns optimization."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise ValueError(f"database does not exist: {path}")
    if bool(run_ids) == all_succeeded:
        raise ValueError("choose run_ids or all_succeeded")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        succeeded = {row[0] for row in conn.execute("SELECT run_id FROM runs WHERE status='succeeded'")}
    finally:
        conn.close()
    selected = sorted(succeeded if all_succeeded else succeeded.intersection(run_ids))
    report = {"dry_run": dry_run, "scanned": len(selected), "eligible": 0,
              "generated_runs": 0, "generated_rows": 0, "skipped": 0,
              "conflicts": 0, "failed": 0, "would_generate_rows": 0,
              "not_succeeded": sorted(set(run_ids or []) - succeeded), "items": []}
    service = RunCapacityAssessmentService(
        run_repository=RunRepository(path), snapshot_repository=RunSnapshotRepository(path),
        assessment_repository=AssessmentRepository(path),
    )
    for run_id in selected:
        item = service.generate_capacity_assessment(run_id, dry_run=dry_run)
        report["items"].append(item)
        if item["result_count"]:
            report["eligible"] += 1
        status = item["status"]
        if status == "generated":
            report["generated_runs"] += 1
            report["generated_rows"] += item["generated"]
        elif status == "skipped":
            report["skipped"] += 1
        elif status == "conflict":
            report["conflicts"] += 1
        elif status in {"failed", "unavailable"}:
            report["failed"] += 1
        report["would_generate_rows"] += item.get("would_generate", 0)
    return report
