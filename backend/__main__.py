from __future__ import annotations

import argparse
import getpass
import json
import sys

from backend.runtime import RuntimeStartupError, prepare_runtime, runtime_status, serve, validate_runtime_ready
from backend.settings import AppSettings, SettingsError


def _settings() -> AppSettings:
    return AppSettings.from_environment()


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
        prepared.user_repository.bootstrap_admin(login_name=login, password=password, user_id=settings.bootstrap_admin_user_id)
    print(json.dumps(runtime_status(settings), ensure_ascii=False, indent=2))
    return 0


def _check_command(settings: AppSettings) -> int:
    validate_runtime_ready(settings)
    print(json.dumps(runtime_status(settings), ensure_ascii=False, indent=2))
    return 0


def _serve_command(settings: AppSettings) -> int:
    serve(settings)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend", description="Airport group application runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    init_parser = sub.add_parser("init", help="initialize database and first administrator")
    init_parser.add_argument("--non-interactive", action="store_true")
    sub.add_parser("check", help="validate runtime paths/database and print non-secret status")
    sub.add_parser("serve", help="start the Waitress WSGI server")
    args = parser.parse_args(argv)
    try:
        settings = _settings()
        if args.command == "init":
            return _init_command(settings, non_interactive=bool(args.non_interactive))
        if args.command == "check":
            return _check_command(settings)
        if args.command == "serve":
            return _serve_command(settings)
        parser.error("unknown command")
    except (SettingsError, RuntimeStartupError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())