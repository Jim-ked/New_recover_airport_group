from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.auth.principal import Principal
from backend.domain.security import BackupRecord
from backend.services.run_indicator_assessment import RunIndicatorAssessmentService
from backend.services.security_service import SecurityService
from backend.storage.assessment_repository import AssessmentRepository
from backend.storage.audit_repository import AuditRepository
from backend.storage.database import initialize_database
from backend.storage.indicator_repository import IndicatorRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.security_repository import SecurityRepository
from backend.web.flask_security import create_security_blueprint
from backend.web.security_api import SecurityApi
from tools.dev_validation.rebuild_v5_experiment import load_definition


class AcceptanceError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expect(response: Any, status: int, label: str) -> Mapping[str, Any]:
    if response.status != status:
        raise AcceptanceError(
            f"{label} expected HTTP {status}, found {response.status}: {response.body!r}"
        )
    return dict(response.body)


def evaluate_v5_assessments(
    database: str | Path,
    manifest: str | Path,
) -> dict[str, Any]:
    database_path = Path(database).resolve()
    if not database_path.is_file():
        raise AcceptanceError(f"V5 database does not exist: {database_path}")
    definition = load_definition(manifest)
    initialize_database(database_path)
    repository = AssessmentRepository(database_path)
    service = RunIndicatorAssessmentService(
        run_repository=RunRepository(database_path),
        snapshot_repository=RunSnapshotRepository(database_path),
        indicator_repository=IndicatorRepository(database_path),
        assessment_repository=repository,
    )

    rows: list[dict[str, Any]] = []
    labels = {run.run_id: run.label for run in definition.runs}
    for run in definition.runs:
        result = service.evaluate(run.run_id)
        if result.status != "available":
            raise AcceptanceError(
                f"{run.label} formal assessment is unavailable: {result.unavailable_reason}"
            )
        rows.append({"label": run.label, **result.to_dict()})

    persisted, total = repository.list_run_evaluations(limit=100)
    value_count = sum(len(result.indicators) for result in persisted)
    if total != 11 or value_count != 44:
        raise AcceptanceError(
            f"V5 assessment persistence mismatch: evaluations={total}, values={value_count}"
        )
    if {result.run_id for result in persisted} != set(labels):
        raise AcceptanceError("persisted evaluation Run set differs from the frozen V5 manifest")

    return {
        "schema_version": "jiangsu-six-airport-v5-assessment.v1",
        "experiment_id": definition.experiment_id,
        "database": str(database_path),
        "database_sha256": _sha256(database_path),
        "indicator_set": {
            "id": rows[0]["indicator_set"]["id"],
            "version": rows[0]["indicator_set"]["version"],
            "calculation_version": rows[0]["calculation_version"],
            "weight_source": rows[0]["weight_source"],
        },
        "evaluation_count": total,
        "indicator_value_count": value_count,
        "runs": rows,
    }


def execute_security_acceptance(
    database: str | Path,
    runtime_root: str | Path,
) -> dict[str, Any]:
    database_path = Path(database).resolve()
    root = Path(runtime_root).resolve()
    backup_root = root / "security" / "backups"
    report_root = root / "security" / "reports"
    repository = SecurityRepository(database_path)
    audit_repository = AuditRepository(database_path)
    service = SecurityService(
        repository,
        db_path=database_path,
        backup_root=backup_root,
        report_root=report_root,
        audit_repository=audit_repository,
    )
    api = SecurityApi(service)
    admin = Principal("v5-acceptance-admin", role="admin")

    audit = audit_repository.append(
        actor_user_id=admin.user_id,
        actor_role=admin.role,
        action="V5 assessment acceptance",
        resource_type="assessment",
        resource_id="jiangsu-six-airport-v5",
        request_method="POST",
        request_path="/internal/acceptance/v5-assessment",
        source_address="127.0.0.1",
        response_status=200,
        outcome="success",
        details={"scope": "isolated-v5-database"},
    )
    alert = _expect(
        api.raise_alert(
            {
                "kind": "validation",
                "message": "V5验收：非法备份参数与恢复输入拒绝测试",
                "severity": "warning",
            },
            principal=admin,
        ),
        201,
        "validation alert",
    )
    backup = _expect(
        api.create_backup({"kind": "full", "encrypted": False}, principal=admin),
        201,
        "full backup",
    )
    backup_path = backup_root / str(backup["path"])
    if backup.get("encrypted") is not False or backup_path.read_bytes()[:16] != b"SQLite format 3\x00":
        raise AcceptanceError("full backup is not an honest unencrypted SQLite file")

    csv_report = _expect(
        api.generate_audit_report({"format": "csv"}, principal=admin),
        201,
        "CSV audit report",
    )
    pdf_report = _expect(
        api.generate_audit_report({"format": "pdf"}, principal=admin),
        201,
        "PDF audit report",
    )
    csv_bytes, csv_type, _ = service.read_audit_report(str(csv_report["id"]))
    pdf_bytes, pdf_type, _ = service.read_audit_report(str(pdf_report["id"]))
    if not csv_bytes.startswith(b"\xef\xbb\xbf") or not csv_type.startswith("text/csv"):
        raise AcceptanceError("CSV audit report content is invalid")
    if not pdf_bytes.startswith(b"%PDF-") or pdf_type != "application/pdf":
        raise AcceptanceError("PDF audit report content is invalid")

    encrypted = api.create_backup({"kind": "full", "encrypted": True}, principal=admin)
    incremental = api.create_backup(
        {"kind": "incremental", "encrypted": False}, principal=admin
    )
    _expect(encrypted, 422, "encrypted backup rejection")
    _expect(incremental, 422, "incremental backup rejection")

    unsafe_id = f"unsafe-{uuid.uuid4()}"
    repository.save_backup(
        BackupRecord(id=unsafe_id, kind="full", path="../outside.sqlite3", encrypted=False)
    )
    unsafe = api.restore_backup(unsafe_id, principal=admin)
    _expect(unsafe, 422, "outside-path restore rejection")

    backup_root.mkdir(parents=True, exist_ok=True)
    invalid_id = f"invalid-{uuid.uuid4()}"
    invalid_name = f"invalid_{invalid_id}.sqlite3"
    (backup_root / invalid_name).write_bytes(b"not a SQLite database")
    repository.save_backup(
        BackupRecord(id=invalid_id, kind="full", path=invalid_name, encrypted=False)
    )
    invalid = api.restore_backup(invalid_id, principal=admin)
    _expect(invalid, 422, "invalid-SQLite restore rejection")

    # Negative fixtures prove rejection but are not valid backup inventory. Remove only
    # these two records/files so the acceptance workspace continues to show honest assets.
    repository.delete_backup(unsafe_id)
    service.delete_backup(invalid_id)

    try:
        from flask import Flask
    except ImportError as exc:  # pragma: no cover
        raise AcceptanceError("Flask is required for unauthenticated acceptance") from exc
    app = Flask(__name__)
    app.register_blueprint(
        create_security_blueprint(api=api, principal_resolver=lambda _request: None)
    )
    unauthenticated = app.test_client().get("/api/security/backups")
    if unauthenticated.status_code != 401:
        raise AcceptanceError(
            f"unauthenticated request expected HTTP 401, found {unauthenticated.status_code}"
        )

    return {
        "runtime_root": str(root),
        "positive": {
            "full_backup": {
                "id": backup["id"],
                "path": str(backup_path),
                "encrypted": backup["encrypted"],
                "sha256": _sha256(backup_path),
            },
            "csv_report": {"id": csv_report["id"], "path": str(report_root / csv_report["report_path"])},
            "pdf_report": {"id": pdf_report["id"], "path": str(report_root / pdf_report["report_path"])},
            "audit_event": audit.to_dict(),
            "validation_alert": alert,
        },
        "negative": {
            "unauthenticated": {"status": 401, "result": "PASS"},
            "encrypted_true": {"status": encrypted.status, "result": "PASS"},
            "incremental": {"status": incremental.status, "result": "PASS"},
            "outside_path": {"status": unsafe.status, "result": "PASS"},
            "invalid_sqlite": {"status": invalid.status, "result": "PASS"},
        },
    }


def complete_acceptance(
    database: str | Path,
    manifest: str | Path,
    runtime_root: str | Path,
) -> dict[str, Any]:
    return {
        "schema_version": "v5-assessment-security-acceptance.v1",
        "assessment": evaluate_v5_assessments(database, manifest),
        "security": execute_security_acceptance(database, runtime_root),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Complete V5 indicator assessment and isolated security acceptance"
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = complete_acceptance(
        args.database,
        args.manifest,
        args.runtime_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output.resolve()),
        "evaluations": result["assessment"]["evaluation_count"],
        "indicator_values": result["assessment"]["indicator_value_count"],
        "security_negative_passed": sum(
            item["result"] == "PASS" for item in result["security"]["negative"].values()
        ),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
