from __future__ import annotations

import argparse
import getpass
import json
import sys

from backend.runtime import (
    RuntimeStartupError,
    prepare_runtime,
    run_worker,
    runtime_status,
    serve,
    validate_runtime_ready,
)
from backend.settings import AppSettings, SettingsError
from backend.services.airport_seed_service import AirportSeedError, bootstrap_airport_master
from backend.storage.airport_repository import AirportRepository
from backend.storage.identifier_migration import backup_database, migrate_project_identifiers


def _settings() -> AppSettings:
    return AppSettings.from_environment()


def _bootstrap_canonical_airports(settings: AppSettings) -> int:
    repository = AirportRepository(settings.db_path)
    repository.init_schema()
    existing = repository.count_airports()
    if existing:
        return existing
    seed_path = (
        settings.project_root
        / "resources"
        / "seed"
        / "airports_master_v1.json"
    )
    if not seed_path.is_file():
        raise RuntimeStartupError(
            f"canonical airport seed is missing: {seed_path}"
        )
    try:
        return bootstrap_airport_master(repository, seed_path)
    except AirportSeedError as exc:
        raise RuntimeStartupError(
            f"canonical airport seed bootstrap failed ({exc.field}): {exc}"
        ) from exc


def _init_command(settings: AppSettings, *, non_interactive: bool) -> int:
    prepared = prepare_runtime(settings, require_user=False, bootstrap_if_configured=True)
    if prepared.user_repository.count() == 0:
        if non_interactive or not sys.stdin.isatty():
            raise RuntimeStartupError(
                "no users exist and bootstrap admin credentials were not configured"
            )
        login = input("Initial admin login [admin]: ").strip() or "admin"
        password = getpass.getpass("Initial admin password: ")
        confirm = getpass.getpass("Confirm admin password: ")
        if password != confirm:
            raise RuntimeStartupError("admin password confirmation does not match")
        prepared.user_repository.bootstrap_admin(
            login_name=login,
            password=password,
            user_id=settings.bootstrap_admin_user_id,
        )
    _bootstrap_canonical_airports(settings)
    print(json.dumps(runtime_status(settings), ensure_ascii=False, indent=2))
    return 0


def _check_command(settings: AppSettings) -> int:
    validate_runtime_ready(settings)
    print(json.dumps(runtime_status(settings), ensure_ascii=False, indent=2))
    return 0


def _migrate_identifiers_command(settings: AppSettings) -> int:
    mapping_path = settings.project_root / "resources" / "migrations" / "airport_id_map_20260818.json"
    backup_path = backup_database(settings.db_path, settings.db_path.parent / "backups")
    report = migrate_project_identifiers(settings.db_path, mapping_path=mapping_path)
    print(json.dumps({"backup_path": str(backup_path), **report.__dict__}, ensure_ascii=False, indent=2))
    return 0


def _serve_command(settings: AppSettings) -> int:
    serve(settings)
    return 0


def _worker_command(
    settings: AppSettings,
    *,
    once: bool,
    poll_interval_s: float,
) -> int:
    run_worker(settings, once=once, poll_interval_s=poll_interval_s)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend", description="Airport group application runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    init_parser = sub.add_parser("init", help="initialize database and first administrator")
    init_parser.add_argument("--non-interactive", action="store_true")
    sub.add_parser("check", help="validate runtime paths/database and print non-secret status")
    sub.add_parser("migrate-identifiers", help="backup and migrate mutable airport/Situation identifiers")
    sub.add_parser("serve", help="start the Waitress WSGI server")
    worker_parser = sub.add_parser("worker", help="start the queued Run worker process")
    worker_parser.add_argument(
        "--once",
        action="store_true",
        help="execute at most one queued Run and exit",
    )
    worker_parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="idle queue polling interval in seconds (0.2..60, default 1.0)",
    )
    args = parser.parse_args(argv)
    try:
        settings = _settings()
        if args.command == "init":
            return _init_command(settings, non_interactive=bool(args.non_interactive))
        if args.command == "check":
            return _check_command(settings)
        if args.command == "migrate-identifiers":
            return _migrate_identifiers_command(settings)
        if args.command == "serve":
            return _serve_command(settings)
        if args.command == "worker":
            return _worker_command(
                settings,
                once=bool(args.once),
                poll_interval_s=float(args.poll_interval),
            )
        parser.error("unknown command")
    except (SettingsError, RuntimeStartupError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
