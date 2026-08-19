from __future__ import annotations

"""Application startup composition.

This is the only module that is allowed to connect deployment settings, schema migration,
first-user bootstrap, API dependency builders, Flask, logging and runtime processes.
"""

import logging
import threading
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

from backend.services.run_worker_runtime import build_run_worker_loop
from backend.services.run_worker_status import RunWorkerStatus
from backend.settings import AppSettings
from backend.storage.database import initialize_database
from backend.storage.airport_repository import AirportRepository
from backend.storage.user_repository import UserRepository
from backend.web.app import create_app
from backend.web.user_admin_api import UserAdminApi
from backend.web.composition import (
    build_account_api,
    build_audit_api,
    build_catalog_api,
    build_indicator_api,
    build_results_api,
    build_run_api,
    build_situation_api,
    build_user_repository,
)


class RuntimeStartupError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedRuntime:
    settings: AppSettings
    user_repository: UserRepository
    admin_bootstrapped: bool


def ensure_runtime_directories(settings: AppSettings) -> None:
    for path in settings.runtime_directories():
        path.mkdir(parents=True, exist_ok=True)


def configure_logging(settings: AppSettings) -> Path:
    ensure_runtime_directories(settings)
    log_path = settings.log_dir / "application.log"
    logger = logging.getLogger("airport_group")
    logger.setLevel(getattr(logging, settings.log_level))
    marker = str(log_path.resolve())
    if not any(getattr(handler, "_airport_group_log_path", None) == marker for handler in logger.handlers):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler._airport_group_log_path = marker  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return log_path


def prepare_runtime(
    settings: AppSettings,
    *,
    require_user: bool = True,
    bootstrap_if_configured: bool = True,
) -> PreparedRuntime:
    ensure_runtime_directories(settings)
    initialize_database(settings.db_path)
    users = build_user_repository(settings.db_path)
    admin_bootstrapped = False
    if users.count() == 0 and bootstrap_if_configured:
        if settings.bootstrap_admin_login and settings.bootstrap_admin_password:
            created = users.bootstrap_admin(
                login_name=settings.bootstrap_admin_login,
                password=settings.bootstrap_admin_password,
                user_id=settings.bootstrap_admin_user_id,
            )
            admin_bootstrapped = created is not None
    if require_user and users.count() == 0:
        raise RuntimeStartupError(
            "user authority is empty; run `python -m backend init` with explicit bootstrap admin credentials first"
        )
    return PreparedRuntime(settings=settings, user_repository=users, admin_bootstrapped=admin_bootstrapped)


def build_application(
    settings: AppSettings,
    *,
    app_factory: Callable[..., Any] = create_app,
) -> Any:
    settings.validate_web()
    prepared = prepare_runtime(settings, require_user=True, bootstrap_if_configured=True)
    configure_logging(settings)
    db_path = settings.db_path
    app = app_factory(
        run_api=build_run_api(db_path),
        results_api=build_results_api(db_path),
        situation_api=build_situation_api(db_path),
        catalog_api=build_catalog_api(db_path),
        indicator_api=build_indicator_api(db_path),
        account_api=build_account_api(db_path),
        audit_api=build_audit_api(db_path),
        user_admin_api=UserAdminApi(prepared.user_repository),
        user_repository=prepared.user_repository,
        secret_key=settings.secret_key,
        session_idle_timeout_seconds=settings.session_idle_timeout_seconds,
        session_absolute_timeout_seconds=settings.session_absolute_timeout_seconds,
        app_config=settings.flask_config(),
        tile_root=settings.tile_root,
        enable_ui=True,
    )
    # Keep deployment facts visible to diagnostics without exposing secrets.
    app.config["AIRPORT_GROUP_DB_PATH"] = str(settings.db_path)
    app.config["AIRPORT_GROUP_RUNTIME_ROOT"] = str(settings.runtime_root)
    app_logger = getattr(app, "logger", None)
    if app_logger is not None:
        app_logger.setLevel(getattr(logging, settings.log_level))
        for handler in logging.getLogger("airport_group").handlers:
            if handler not in app_logger.handlers:
                app_logger.addHandler(handler)
    return app


def validate_runtime_ready(settings: AppSettings) -> None:
    settings.validate_web()
    prepare_runtime(settings, require_user=True, bootstrap_if_configured=False)


def runtime_status(settings: AppSettings) -> dict[str, object]:
    ensure_runtime_directories(settings)
    db_exists_before = settings.db_path.exists()
    initialize_database(settings.db_path)
    users = UserRepository(settings.db_path)
    airports = AirportRepository(settings.db_path)
    return {
        "project_root": str(settings.project_root),
        "runtime_root": str(settings.runtime_root),
        "db_path": str(settings.db_path),
        "db_existed_before_check": db_exists_before,
        "user_count": users.count(),
        "airport_count": airports.count_airports(),
        "web_secret_configured": bool(settings.secret_key and len(settings.secret_key) >= 32),
        "host": settings.host,
        "port": settings.port,
        "server_threads": settings.server_threads,
        "session_cookie_secure": settings.session_cookie_secure,
        "gis_tile_configured": bool(settings.gis_tile_template),
        "tile_root": str(settings.tile_root),
        "run_worker_mode": "separate_process",
    }


def serve(settings: AppSettings) -> None:
    app = build_application(settings)
    try:
        from waitress import serve as waitress_serve
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeStartupError(
            "Waitress runtime dependency is required; install requirements.txt in the deployment environment"
        ) from exc
    logging.getLogger("airport_group").info(
        "starting server host=%s port=%s db=%s", settings.host, settings.port, settings.db_path
    )
    waitress_serve(app, host=settings.host, port=settings.port, threads=settings.server_threads)


def run_worker(
    settings: AppSettings,
    *,
    once: bool = False,
    poll_interval_s: float = 1.0,
) -> None:
    """Run the queue consumer as a process separate from Waitress."""
    prepare_runtime(settings, require_user=True, bootstrap_if_configured=False)
    configure_logging(settings)
    loop = build_run_worker_loop(settings.db_path, poll_interval_s=poll_interval_s)
    worker_status = RunWorkerStatus(settings.db_path)
    stop_heartbeat = threading.Event()
    def heartbeat_loop() -> None:
        while not stop_heartbeat.is_set():
            worker_status.heartbeat()
            stop_heartbeat.wait(2.0)
    heartbeat_thread = threading.Thread(target=heartbeat_loop, name="run-worker-heartbeat", daemon=True)
    heartbeat_thread.start()
    logger = logging.getLogger("airport_group.worker")
    logger.info(
        "starting Run worker db=%s mode=%s poll_interval_s=%.3f",
        settings.db_path,
        "once" if once else "continuous",
        float(poll_interval_s),
    )
    try:
        if once:
            record = loop.execute_next()
            if record is None:
                logger.info("Run worker --once found no claimable queued Run")
            else:
                logger.info("Run worker --once completed run_id=%s status=%s", record.run_id, record.status)
            return
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Run worker stopped by operator")
    finally:
        stop_heartbeat.set()
        worker_status.stopped()


__all__ = [
    "RuntimeStartupError",
    "PreparedRuntime",
    "ensure_runtime_directories",
    "configure_logging",
    "prepare_runtime",
    "build_application",
    "validate_runtime_ready",
    "runtime_status",
    "serve",
    "run_worker",
]
