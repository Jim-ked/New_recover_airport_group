from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from backend.domain.assessment import AssessmentAirportRisk, AssessmentComparison, AssessmentResult
from backend.storage.run_repository import canonical_json


class AssessmentRepositoryError(RuntimeError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class AssessmentRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def save_result(self, result: AssessmentResult) -> AssessmentResult:
        curves = canonical_json({str(step): support for step, support in result.curves})
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO assessment_result (
                    id, airport_id, scenario_type, curves, decline_rate, recovery_slope,
                    task_threshold, initial_capacity, metric_kind, matrix_value,
                    source_run_id, snapshot_hash, metrics_hash, effective_windows,
                    unavailable_windows, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
                ON CONFLICT(id) DO UPDATE SET
                    airport_id=excluded.airport_id,
                    scenario_type=excluded.scenario_type,
                    curves=excluded.curves,
                    decline_rate=excluded.decline_rate,
                    recovery_slope=excluded.recovery_slope,
                    task_threshold=excluded.task_threshold,
                    initial_capacity=excluded.initial_capacity,
                    metric_kind=excluded.metric_kind,
                    matrix_value=excluded.matrix_value,
                    source_run_id=excluded.source_run_id,
                    snapshot_hash=excluded.snapshot_hash,
                    metrics_hash=excluded.metrics_hash,
                    effective_windows=excluded.effective_windows,
                    unavailable_windows=excluded.unavailable_windows,
                    created_at=excluded.created_at
                """,
                (
                    result.id,
                    result.airport_id,
                    result.scenario_type,
                    curves,
                    result.decline_rate,
                    result.recovery_slope,
                    result.task_threshold,
                    result.initial_capacity,
                    result.metric_kind,
                    result.matrix_value,
                    result.source_run_id,
                    result.snapshot_hash,
                    result.metrics_hash,
                    result.effective_windows,
                    result.unavailable_windows,
                    result.created_at,
                ),
            )
        return self.get_result(result.id) or result

    def get_result(self, result_id: str) -> Optional[AssessmentResult]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM assessment_result WHERE id=?", (result_id,)).fetchone()
        return self._result(row) if row is not None else None

    def list_results(
        self,
        *,
        airport_id: Optional[str] = None,
        scenario_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AssessmentResult], int]:
        if not 1 <= limit <= 500:
            raise AssessmentRepositoryError("limit must be in [1,500]")
        if offset < 0:
            raise AssessmentRepositoryError("offset must be nonnegative")
        clauses: list[str] = []
        params: list[Any] = []
        if airport_id is not None:
            clauses.append("airport_id=?")
            params.append(airport_id)
        if scenario_type is not None:
            clauses.append("scenario_type=?")
            params.append(scenario_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM assessment_result {where}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT * FROM assessment_result {where} "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._result(row) for row in rows], total

    @staticmethod
    def _result(row: sqlite3.Row) -> AssessmentResult:
        try:
            curves = json.loads(row["curves"])
            return AssessmentResult.from_mapping(
                {
                    "id": row["id"],
                    "airport_id": row["airport_id"],
                    "scenario_type": row["scenario_type"],
                    "curves": curves,
                    "decline_rate": row["decline_rate"],
                    "recovery_slope": row["recovery_slope"],
                    "task_threshold": row["task_threshold"],
                    "initial_capacity": row["initial_capacity"],
                    "metric_kind": row["metric_kind"],
                    "matrix_value": row["matrix_value"],
                    "source_run_id": row["source_run_id"],
                    "snapshot_hash": row["snapshot_hash"],
                    "metrics_hash": row["metrics_hash"],
                    "effective_windows": row["effective_windows"],
                    "unavailable_windows": row["unavailable_windows"],
                    "created_at": row["created_at"],
                }
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise AssessmentRepositoryError("stored assessment result is invalid") from exc

    def upsert_risk(self, risk: AssessmentAirportRisk) -> AssessmentAirportRisk:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO assessment_airport_risk (airport_id, risk_level, risk_score, updated_at)
                VALUES (?, ?, ?, COALESCE(?, datetime('now')))
                ON CONFLICT(airport_id) DO UPDATE SET
                    risk_level=excluded.risk_level,
                    risk_score=excluded.risk_score,
                    updated_at=excluded.updated_at
                """,
                (risk.airport_id, risk.risk_level, risk.risk_score, risk.updated_at),
            )
        return self.get_risk(risk.airport_id) or risk

    def get_risk(self, airport_id: str) -> Optional[AssessmentAirportRisk]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM assessment_airport_risk WHERE airport_id=?", (airport_id,)
            ).fetchone()
        if row is None:
            return None
        return AssessmentAirportRisk.from_mapping(dict(row))

    def list_risks(self) -> list[AssessmentAirportRisk]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM assessment_airport_risk ORDER BY airport_id").fetchall()
        return [AssessmentAirportRisk.from_mapping(dict(row)) for row in rows]

    def save_comparison(self, comparison: AssessmentComparison) -> AssessmentComparison:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO assessment_comparison (
                    id, base_airport_id, base_scenario_type, compared_airport_id,
                    compared_scenario_type, conclusion, log, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
                ON CONFLICT(id) DO UPDATE SET
                    base_airport_id=excluded.base_airport_id,
                    base_scenario_type=excluded.base_scenario_type,
                    compared_airport_id=excluded.compared_airport_id,
                    compared_scenario_type=excluded.compared_scenario_type,
                    conclusion=excluded.conclusion,
                    log=excluded.log,
                    created_at=excluded.created_at
                """,
                (
                    comparison.id,
                    comparison.base_airport_id,
                    comparison.base_scenario_type,
                    comparison.compared_airport_id,
                    comparison.compared_scenario_type,
                    comparison.conclusion,
                    comparison.log,
                    comparison.created_at,
                ),
            )
        return comparison

    def list_comparisons(self, *, limit: int = 50, offset: int = 0) -> tuple[list[AssessmentComparison], int]:
        if not 1 <= limit <= 500:
            raise AssessmentRepositoryError("limit must be in [1,500]")
        if offset < 0:
            raise AssessmentRepositoryError("offset must be nonnegative")
        with self.connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM assessment_comparison").fetchone()[0])
            rows = conn.execute(
                "SELECT * FROM assessment_comparison ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [AssessmentComparison.from_mapping(dict(row)) for row in rows], total


__all__ = ["AssessmentRepository", "AssessmentRepositoryError"]
