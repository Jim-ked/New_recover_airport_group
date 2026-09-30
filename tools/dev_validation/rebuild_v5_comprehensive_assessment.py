from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.run_indicator_assessment import RunIndicatorAssessmentService
from backend.storage.assessment_repository import AssessmentRepository
from backend.storage.database import initialize_database
from backend.storage.indicator_repository import IndicatorRepository
from backend.storage.migrations.v024_indicator_assessment import FORMAL_SET_ID
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from tools.dev_validation.rebuild_v5_experiment import load_definition


class ComprehensiveAssessmentRebuildError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_database(database: Path, backup_dir: Path) -> tuple[Path, str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_dir / (
        f"pre-comprehensive-assessment-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    )
    with closing(sqlite3.connect(database)) as source, closing(
        sqlite3.connect(destination)
    ) as target:
        source.backup(target)
        quick_check = target.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or quick_check[0] != "ok":
        raise ComprehensiveAssessmentRebuildError("pre-assessment backup failed integrity check")
    return destination, _sha256(destination)


def _formal_state(database: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        run_rows = connection.execute(
            """SELECT run.run_id,run.status,result.solution_hash,result.metrics_hash
               FROM runs run LEFT JOIN run_results result ON result.run_id=run.run_id
               ORDER BY run.run_id"""
        ).fetchall()
        return {
            "runs": [dict(row) for row in run_rows],
            "capacity_results": int(
                connection.execute("SELECT COUNT(*) FROM assessment_result").fetchone()[0]
            ),
            "comparisons": int(
                connection.execute("SELECT COUNT(*) FROM assessment_comparison").fetchone()[0]
            ),
        }


def _assert_formal_state_preserved(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    if before != after:
        raise ComprehensiveAssessmentRebuildError(
            "Run, Solution, Metrics, capacity assessment, or comparison facts changed"
        )


def rebuild_v5_comprehensive_assessments(
    *,
    database: str | Path,
    manifest: str | Path,
    backup_dir: str | Path,
) -> dict[str, Any]:
    database_path = Path(database).resolve()
    if not database_path.is_file():
        raise ComprehensiveAssessmentRebuildError(f"V5 database does not exist: {database_path}")
    definition = load_definition(manifest)
    before = _formal_state(database_path)
    expected_run_ids = {run.run_id for run in definition.runs}
    actual_run_ids = {str(row["run_id"]) for row in before["runs"]}
    if actual_run_ids != expected_run_ids:
        raise ComprehensiveAssessmentRebuildError(
            "database Run set differs from the frozen V5 manifest"
        )
    if any(row["status"] != "succeeded" for row in before["runs"]):
        raise ComprehensiveAssessmentRebuildError("every frozen V5 Run must be succeeded")
    if any(not row["solution_hash"] or not row["metrics_hash"] for row in before["runs"]):
        raise ComprehensiveAssessmentRebuildError(
            "every frozen V5 Run must retain Solution and Metrics hashes"
        )

    source_sha256 = _sha256(database_path)
    backup_path, backup_sha256 = _backup_database(
        database_path, Path(backup_dir).resolve()
    )
    initialize_database(database_path)

    indicator_repository = IndicatorRepository(database_path)
    weights = indicator_repository.published_weights(FORMAL_SET_ID)
    trace = weights.get("trace") if isinstance(weights.get("trace"), Mapping) else {}
    if weights.get("status") != "available":
        raise ComprehensiveAssessmentRebuildError(
            "formal indicator weights are unavailable; no fallback weights were generated"
        )
    if weights.get("weight_source") not in {"expert", "ahp"}:
        raise ComprehensiveAssessmentRebuildError("formal weight source is not traceable")
    if weights.get("weight_source") == "ahp" and trace.get("consistent") is not True:
        raise ComprehensiveAssessmentRebuildError("formal AHP consistency validation did not pass")
    if not isinstance(trace.get("config_hash"), str) or len(trace["config_hash"]) != 64:
        raise ComprehensiveAssessmentRebuildError("formal weight configuration hash is missing")

    assessment_repository = AssessmentRepository(database_path)
    service = RunIndicatorAssessmentService(
        run_repository=RunRepository(database_path),
        snapshot_repository=RunSnapshotRepository(database_path),
        indicator_repository=indicator_repository,
        assessment_repository=assessment_repository,
    )
    rows: list[dict[str, Any]] = []
    for run in definition.runs:
        evaluation = service.evaluate(run.run_id)
        rows.append(
            {
                "label": run.label,
                "run_id": run.run_id,
                "indicator_eligibility": evaluation.status == "available",
                "weight_availability": weights["status"],
                "status": evaluation.status,
                "composite_value": evaluation.support_index,
                "reason": evaluation.unavailable_reason,
                "snapshot_hash": evaluation.snapshot_hash,
                "metrics_hash": evaluation.metrics_hash,
                "indicator_count": len(evaluation.indicators),
                "calculation_version": evaluation.calculation_version,
            }
        )

    persisted, total = assessment_repository.list_run_evaluations(limit=100)
    persisted_run_ids = {evaluation.run_id for evaluation in persisted}
    if persisted_run_ids != expected_run_ids or total != len(expected_run_ids):
        raise ComprehensiveAssessmentRebuildError(
            "persisted comprehensive evaluation set differs from the frozen V5 manifest"
        )
    indicator_value_count = sum(len(evaluation.indicators) for evaluation in persisted)
    after = _formal_state(database_path)
    _assert_formal_state_preserved(before, after)

    return {
        "schema_version": "jiangsu-six-airport-v5-comprehensive-assessment.v1",
        "experiment_id": definition.experiment_id,
        "database": str(database_path),
        "database_sha256_before": source_sha256,
        "database_sha256_after": _sha256(database_path),
        "backup": {"path": str(backup_path), "sha256": backup_sha256},
        "indicator_set": {
            "id": weights["indicator_set_id"],
            "version": weights["version"],
        },
        "weight_provenance": {
            "method": weights["weight_source"],
            "revision": trace["revision"],
            "config_hash": trace["config_hash"],
            "consistency_ratio": trace.get("consistency_ratio"),
            "calculation_rule": weights["calculation_rule"],
        },
        "evaluation_count": total,
        "indicator_value_count": indicator_value_count,
        "preserved": {
            "run_count": len(after["runs"]),
            "capacity_results": after["capacity_results"],
            "comparisons": after["comparisons"],
            "runs": after["runs"],
        },
        "runs": rows,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild formal comprehensive assessments for the repaired V5 database"
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = rebuild_v5_comprehensive_assessments(
        database=args.database,
        manifest=args.manifest,
        backup_dir=args.backup_dir,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "evaluations": result["evaluation_count"],
                "indicator_values": result["indicator_value_count"],
                "available": sum(row["status"] == "available" for row in result["runs"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ComprehensiveAssessmentRebuildError",
    "rebuild_v5_comprehensive_assessments",
]
