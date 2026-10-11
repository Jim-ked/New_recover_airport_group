from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Optional, Sequence

from backend.domain.assessment import (
    AssessmentAirportRisk,
    AssessmentResult,
    IndicatorAssessmentValue,
    RunIndicatorAssessment,
)
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

    def persist_run_capacity_results(
        self, results: Sequence[AssessmentResult], *, dry_run: bool = False,
    ) -> dict[str, Any]:
        """Insert one complete projection atomically; never overwrite derived facts."""
        if not results or len({row.airport_id for row in results}) != len(results):
            raise AssessmentRepositoryError("capacity results must contain unique airports")
        first = results[0]
        provenance = (first.source_run_id, first.snapshot_hash, first.metrics_hash)
        if not all(provenance) or any(
            (row.source_run_id, row.snapshot_hash, row.metrics_hash) != provenance
            or row.metric_kind != "capacity_availability_v1" for row in results
        ):
            raise AssessmentRepositoryError("capacity results require one complete Run provenance")
        expected = {row.id: row for row in results}
        if len(expected) != len(results):
            raise AssessmentRepositoryError("capacity result IDs must be unique")
        outcome = {"generated": 0, "skipped": 0, "conflict": 0, "failed": 0}
        with self.connect() as conn:
            conn.execute("BEGIN" if dry_run else "BEGIN IMMEDIATE")
            current = conn.execute(
                """SELECT r.status,r.snapshot_hash,s.snapshot_hash AS stored_snapshot_hash,rr.metrics_hash
                   FROM runs r JOIN run_input_snapshots s USING(run_id)
                   JOIN run_results rr USING(run_id) WHERE r.run_id=?""",
                (first.source_run_id,),
            ).fetchone()
            if current is None or current["status"] != "succeeded" or (
                current["snapshot_hash"], current["stored_snapshot_hash"], current["metrics_hash"]
            ) != (first.snapshot_hash, first.snapshot_hash, first.metrics_hash):
                return {**outcome, "status": "conflict", "conflict": len(results),
                        "reason": "canonical Run provenance changed before persistence"}
            ids = list(expected)
            existing = conn.execute(
                f"SELECT * FROM assessment_result WHERE id IN ({','.join('?' for _ in ids)}) "
                "OR (source_run_id=? AND metric_kind='capacity_availability_v1')",
                [*ids, first.source_run_id],
            ).fetchall()
            for raw in existing:
                saved = self._result(raw)
                candidate = expected.get(saved.id)
                if candidate is None or replace(saved, created_at=None) != replace(candidate, created_at=None):
                    return {**outcome, "status": "conflict", "conflict": len(results),
                            "reason": f"stored capacity facts/provenance conflict: {saved.id}"}
            saved_ids = {row["id"] for row in existing}
            missing = [row for row in results if row.id not in saved_ids]
            outcome["skipped"] = len(existing)
            if not missing:
                return {**outcome, "status": "skipped"}
            if dry_run:
                return {**outcome, "status": "eligible", "would_generate": len(missing)}
            for result in missing:
                values = asdict(result)
                values["curves"] = canonical_json({str(step): value for step, value in result.curves})
                values.pop("created_at")
                columns = list(values)
                conn.execute(
                    f"INSERT INTO assessment_result ({','.join(columns)}) "
                    f"VALUES ({','.join('?' for _ in columns)})",
                    [values[name] for name in columns],
                )
            return {**outcome, "status": "generated", "generated": len(missing)}

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

    def save_run_evaluation(self, evaluation: RunIndicatorAssessment) -> RunIndicatorAssessment:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO assessment_run_evaluation (
                       run_id,snapshot_hash,metrics_hash,indicator_set_id,indicator_set_version,
                       status,unavailable_reason,calculation_version,weight_source,weight_trace_json,
                       support_index,overall_membership_json,weighted_composite,risk_score,risk_level,
                       risk_status,risk_reason,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP),CURRENT_TIMESTAMP)
                   ON CONFLICT(run_id) DO UPDATE SET
                       snapshot_hash=excluded.snapshot_hash,metrics_hash=excluded.metrics_hash,
                       indicator_set_id=excluded.indicator_set_id,
                       indicator_set_version=excluded.indicator_set_version,status=excluded.status,
                       unavailable_reason=excluded.unavailable_reason,
                       calculation_version=excluded.calculation_version,
                       weight_source=excluded.weight_source,weight_trace_json=excluded.weight_trace_json,
                       support_index=excluded.support_index,
                       overall_membership_json=excluded.overall_membership_json,
                       weighted_composite=excluded.weighted_composite,risk_score=excluded.risk_score,
                       risk_level=excluded.risk_level,risk_status=excluded.risk_status,
                       risk_reason=excluded.risk_reason,updated_at=CURRENT_TIMESTAMP""",
                (
                    evaluation.run_id,
                    evaluation.snapshot_hash,
                    evaluation.metrics_hash,
                    evaluation.indicator_set_id,
                    evaluation.indicator_set_version,
                    evaluation.status,
                    evaluation.unavailable_reason,
                    evaluation.calculation_version,
                    evaluation.weight_source,
                    canonical_json(dict(evaluation.weight_trace)),
                    evaluation.support_index,
                    None if evaluation.overall_membership is None else canonical_json(dict(evaluation.overall_membership)),
                    evaluation.weighted_composite,
                    evaluation.risk_score,
                    evaluation.risk_level,
                    evaluation.risk_status,
                    evaluation.risk_reason,
                    evaluation.created_at,
                ),
            )
            conn.execute("DELETE FROM assessment_indicator_value WHERE run_id=?", (evaluation.run_id,))
            for item in evaluation.indicators:
                conn.execute(
                    """INSERT INTO assessment_indicator_value (
                           run_id,indicator_id,code,name,raw_value,normalized_value,weight,
                           weight_source,calculation_type,evidence_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        evaluation.run_id,
                        item.indicator_id,
                        item.code,
                        item.name,
                        item.raw_value,
                        item.normalized_value,
                        item.weight,
                        item.weight_source,
                        item.calculation_type,
                        canonical_json(dict(item.evidence)),
                    ),
                )
        return self.get_run_evaluation(evaluation.run_id) or evaluation

    def get_run_evaluation(self, run_id: str) -> Optional[RunIndicatorAssessment]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM assessment_run_evaluation WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            value_rows = conn.execute(
                """SELECT * FROM assessment_indicator_value WHERE run_id=?
                   ORDER BY indicator_id""",
                (run_id,),
            ).fetchall()
        return self._run_evaluation(row, value_rows)

    def list_run_evaluations(
        self,
        *,
        status: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[RunIndicatorAssessment], int]:
        if not 1 <= limit <= 500:
            raise AssessmentRepositoryError("limit must be in [1,500]")
        if offset < 0:
            raise AssessmentRepositoryError("offset must be nonnegative")
        if status is not None and status not in {"available", "unavailable"}:
            raise AssessmentRepositoryError("status must be available or unavailable")
        if owner_user_id is not None and (
            not isinstance(owner_user_id, str) or not owner_user_id.strip()
        ):
            raise AssessmentRepositoryError("owner_user_id must be nonblank or None")
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("evaluation.status=?")
            params.append(status)
        if owner_user_id is not None:
            clauses.append("run.owner_user_id=?")
            params.append(owner_user_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        source = (
            "FROM assessment_run_evaluation evaluation "
            "JOIN runs run ON run.run_id=evaluation.run_id"
        )
        with self.connect() as conn:
            total = int(conn.execute(
                f"SELECT COUNT(*) {source} {where}", params
            ).fetchone()[0])
            rows = conn.execute(
                f"SELECT evaluation.* {source} {where} "
                "ORDER BY evaluation.updated_at DESC,evaluation.run_id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            result: list[RunIndicatorAssessment] = []
            for row in rows:
                value_rows = conn.execute(
                    "SELECT * FROM assessment_indicator_value WHERE run_id=? ORDER BY indicator_id",
                    (row["run_id"],),
                ).fetchall()
                result.append(self._run_evaluation(row, value_rows))
        return result, total

    @staticmethod
    def _run_evaluation(
        row: sqlite3.Row, value_rows: list[sqlite3.Row]
    ) -> RunIndicatorAssessment:
        try:
            indicators = tuple(
                IndicatorAssessmentValue(
                    indicator_id=value["indicator_id"],
                    code=value["code"],
                    name=value["name"],
                    raw_value=float(value["raw_value"]),
                    normalized_value=float(value["normalized_value"]),
                    weight=float(value["weight"]),
                    weight_source=value["weight_source"],
                    calculation_type=value["calculation_type"],
                    evidence=json.loads(value["evidence_json"]),
                )
                for value in value_rows
            )
            return RunIndicatorAssessment(
                run_id=row["run_id"],
                snapshot_hash=row["snapshot_hash"],
                metrics_hash=row["metrics_hash"],
                indicator_set_id=row["indicator_set_id"],
                indicator_set_version=row["indicator_set_version"],
                indicators=indicators,
                status=row["status"],
                unavailable_reason=row["unavailable_reason"],
                calculation_version=row["calculation_version"],
                weight_source=row["weight_source"],
                weight_trace=json.loads(row["weight_trace_json"]),
                support_index=row["support_index"],
                overall_membership=(
                    None if row["overall_membership_json"] is None
                    else json.loads(row["overall_membership_json"])
                ),
                weighted_composite=row["weighted_composite"],
                risk_score=row["risk_score"],
                risk_level=row["risk_level"],
                risk_status=row["risk_status"],
                risk_reason=row["risk_reason"],
                created_at=row["created_at"],
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise AssessmentRepositoryError("stored run indicator assessment is invalid") from exc


__all__ = ["AssessmentRepository", "AssessmentRepositoryError"]
