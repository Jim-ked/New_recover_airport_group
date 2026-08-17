from __future__ import annotations

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_paths() -> tuple[Path, Path, Path]:
    root = project_root()
    db_dir = root / "runtime" / "db"
    return (
        db_dir / "airport_group.sqlite3",
        db_dir / "validation_base.sqlite3",
        db_dir / "validation_work.sqlite3",
    )


def _readonly_uri(path: Path) -> str:
    # Path.as_uri() produces a SQLite-compatible absolute file URI on Windows and POSIX.
    return f"{path.resolve().as_uri()}?mode=ro"


def _integrity_check(path: Path) -> None:
    # sqlite3.Connection.__exit__ commits/rolls back but does NOT close the connection.
    # Use closing() so Windows file handles are released before rename/delete operations.
    with closing(sqlite3.connect(str(path), timeout=5.0)) as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    if not row or str(row[0]).lower() != "ok":
        raise RuntimeError(f"SQLite integrity_check failed for {path}: {row}")


def _unlink_stale_temp(path: Path) -> None:
    if not path.exists():
        return
    try:
        path.unlink()
    except PermissionError as exc:
        raise RuntimeError(
            f"cannot remove stale temporary database because it is in use: {path}. "
            "Stop any process using the validation database and retry."
        ) from exc


def _cleanup_temp(path: Path) -> None:
    if not path.exists():
        return
    try:
        path.unlink()
    except OSError:
        # Never hide the real backup/replace exception with a cleanup exception.
        pass


def _backup(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise ValueError("source and target database paths must differ")
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    _unlink_stale_temp(temp)

    try:
        # Explicitly close both connections before integrity_check/os.replace. This is
        # required on Windows; the Connection context manager alone does not close them.
        with closing(sqlite3.connect(_readonly_uri(source), uri=True, timeout=5.0)) as src:
            with closing(sqlite3.connect(str(temp), timeout=5.0)) as dst:
                src.backup(dst)
                dst.commit()

        _integrity_check(temp)
        try:
            os.replace(temp, target)
        except PermissionError as exc:
            raise RuntimeError(
                f"cannot replace validation database because the target is in use: {target}. "
                "Stop the application/worker that is using this validation database and retry."
            ) from exc
        _integrity_check(target)
    finally:
        _cleanup_temp(temp)


def _args() -> argparse.Namespace:
    live_default, base_default, work_default = default_paths()
    parser = argparse.ArgumentParser(
        description="Create/reset the disposable validation SQLite database."
    )
    parser.add_argument(
        "--capture-base",
        action="store_true",
        help="capture a fresh validation_base.sqlite3 from --source-db before resetting work",
    )
    parser.add_argument("--source-db", type=Path, default=live_default)
    parser.add_argument("--base-db", type=Path, default=base_default)
    parser.add_argument("--work-db", type=Path, default=work_default)
    parser.add_argument(
        "--force-base",
        action="store_true",
        help="allow --capture-base to overwrite an existing validation base",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    source = args.source_db.resolve()
    base = args.base_db.resolve()
    work = args.work_db.resolve()

    if len({source, base, work}) != 3:
        raise RuntimeError("source, validation base and validation work paths must be distinct")
    if "validation" not in base.name.lower() or "validation" not in work.name.lower():
        raise RuntimeError("base/work database filenames must contain 'validation'")

    if args.capture_base:
        if base.exists() and not args.force_base:
            raise RuntimeError(
                f"validation base already exists: {base}; use --force-base only when you intentionally want to replace it"
            )
        print(f"[INFO] capture base: {source} -> {base}")
        _backup(source, base)
        print("[OK] validation base captured")

    if not base.is_file():
        raise FileNotFoundError(
            f"validation base not found: {base}; first run with --capture-base"
        )

    print(f"[INFO] reset work: {base} -> {work}")
    _backup(base, work)
    print("[OK] validation work database reset")
    print(f"[NEXT] set AIRPORT_GROUP_DB_PATH={work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
