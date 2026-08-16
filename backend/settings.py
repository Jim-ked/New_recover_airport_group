from __future__ import annotations

"""Runtime/deployment settings for the application host.

This module is intentionally limited to operational concerns.  Business/algorithm
parameters do not belong here.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from backend.auth.session_policy import (
    DEFAULT_ABSOLUTE_TIMEOUT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
)


ENV_PREFIX = "AIRPORT_GROUP_"
DEFAULT_DB_NAME = "airport_group.sqlite3"


class SettingsError(ValueError):
    pass


def _bool(value: object, *, default: bool = False, name: str) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be true/false")


def _int(value: object, *, default: int, name: str, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        result = default
    else:
        try:
            result = int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{name} must be an integer") from exc
    if result < minimum or result > maximum:
        raise SettingsError(f"{name} must be between {minimum} and {maximum}")
    return result


def _path(value: object, *, default: Path, root: Path) -> Path:
    if value is None or str(value).strip() == "":
        result = default
    else:
        result = Path(str(value).strip()).expanduser()
    if not result.is_absolute():
        result = root / result
    return result.resolve()


def _optional_nonblank(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class AppSettings:
    project_root: Path
    runtime_root: Path
    db_path: Path
    content_dir: Path
    run_dir: Path
    log_dir: Path
    temp_dir: Path
    tile_root: Path
    secret_key: Optional[str]
    host: str
    port: int
    debug: bool
    session_cookie_secure: bool
    session_idle_timeout_seconds: int
    session_absolute_timeout_seconds: int
    max_content_length_bytes: int
    gis_tile_template: str
    bootstrap_admin_login: Optional[str]
    bootstrap_admin_password: Optional[str]
    bootstrap_admin_user_id: str
    log_level: str
    server_threads: int

    @classmethod
    def from_environment(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        *,
        project_root: str | Path | None = None,
    ) -> "AppSettings":
        env: Mapping[str, str] = os.environ if environ is None else environ
        root = Path(project_root or Path(__file__).resolve().parents[1]).expanduser().resolve()
        runtime_root = _path(
            env.get(f"{ENV_PREFIX}RUNTIME_ROOT"),
            default=root / "runtime",
            root=root,
        )
        db_path = _path(
            env.get(f"{ENV_PREFIX}DB_PATH"),
            default=runtime_root / "db" / DEFAULT_DB_NAME,
            root=root,
        )
        content_dir = _path(
            env.get(f"{ENV_PREFIX}CONTENT_DIR"),
            default=runtime_root / "content",
            root=root,
        )
        run_dir = _path(
            env.get(f"{ENV_PREFIX}RUN_DIR"),
            default=runtime_root / "runs",
            root=root,
        )
        log_dir = _path(
            env.get(f"{ENV_PREFIX}LOG_DIR"),
            default=runtime_root / "logs",
            root=root,
        )
        temp_dir = _path(
            env.get(f"{ENV_PREFIX}TEMP_DIR"),
            default=runtime_root / "temp",
            root=root,
        )
        tile_root = _path(
            env.get(f"{ENV_PREFIX}TILE_ROOT"),
            default=root / "resources" / "tiles",
            root=root,
        )
        host = str(env.get(f"{ENV_PREFIX}HOST", "127.0.0.1")).strip()
        if not host:
            raise SettingsError(f"{ENV_PREFIX}HOST must be nonblank")
        port = _int(
            env.get(f"{ENV_PREFIX}PORT"),
            default=8080,
            name=f"{ENV_PREFIX}PORT",
            minimum=1,
            maximum=65535,
        )
        idle = _int(
            env.get(f"{ENV_PREFIX}SESSION_IDLE_SECONDS"),
            default=DEFAULT_IDLE_TIMEOUT_SECONDS,
            name=f"{ENV_PREFIX}SESSION_IDLE_SECONDS",
            minimum=0,
            maximum=7 * 24 * 60 * 60,
        )
        absolute = _int(
            env.get(f"{ENV_PREFIX}SESSION_ABSOLUTE_SECONDS"),
            default=DEFAULT_ABSOLUTE_TIMEOUT_SECONDS,
            name=f"{ENV_PREFIX}SESSION_ABSOLUTE_SECONDS",
            minimum=0,
            maximum=30 * 24 * 60 * 60,
        )
        if idle and absolute and idle > absolute:
            raise SettingsError("session idle timeout cannot exceed absolute timeout")
        max_content = _int(
            env.get(f"{ENV_PREFIX}MAX_CONTENT_LENGTH_BYTES"),
            default=50 * 1024 * 1024,
            name=f"{ENV_PREFIX}MAX_CONTENT_LENGTH_BYTES",
            minimum=1024,
            maximum=1024 * 1024 * 1024,
        )
        login = _optional_nonblank(env.get(f"{ENV_PREFIX}BOOTSTRAP_ADMIN_LOGIN"))
        password = _optional_nonblank(env.get(f"{ENV_PREFIX}BOOTSTRAP_ADMIN_PASSWORD"))
        if (login is None) != (password is None):
            raise SettingsError(
                f"{ENV_PREFIX}BOOTSTRAP_ADMIN_LOGIN and {ENV_PREFIX}BOOTSTRAP_ADMIN_PASSWORD must be provided together"
            )
        user_id = str(env.get(f"{ENV_PREFIX}BOOTSTRAP_ADMIN_USER_ID", "admin")).strip()
        if not user_id:
            raise SettingsError(f"{ENV_PREFIX}BOOTSTRAP_ADMIN_USER_ID must be nonblank")
        log_level = str(env.get(f"{ENV_PREFIX}LOG_LEVEL", "INFO")).strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise SettingsError(f"{ENV_PREFIX}LOG_LEVEL is invalid")
        server_threads = _int(
            env.get(f"{ENV_PREFIX}SERVER_THREADS"),
            default=8,
            name=f"{ENV_PREFIX}SERVER_THREADS",
            minimum=1,
            maximum=64,
        )
        return cls(
            project_root=root,
            runtime_root=runtime_root,
            db_path=db_path,
            content_dir=content_dir,
            run_dir=run_dir,
            log_dir=log_dir,
            temp_dir=temp_dir,
            tile_root=tile_root,
            secret_key=_optional_nonblank(env.get(f"{ENV_PREFIX}SECRET_KEY")),
            host=host,
            port=port,
            debug=_bool(env.get(f"{ENV_PREFIX}DEBUG"), default=False, name=f"{ENV_PREFIX}DEBUG"),
            session_cookie_secure=_bool(
                env.get(f"{ENV_PREFIX}SESSION_COOKIE_SECURE"),
                default=False,
                name=f"{ENV_PREFIX}SESSION_COOKIE_SECURE",
            ),
            session_idle_timeout_seconds=idle,
            session_absolute_timeout_seconds=absolute,
            max_content_length_bytes=max_content,
            gis_tile_template=str(env.get(f"{ENV_PREFIX}GIS_TILE_TEMPLATE", "")).strip(),
            bootstrap_admin_login=login,
            bootstrap_admin_password=password,
            bootstrap_admin_user_id=user_id,
            log_level=log_level,
            server_threads=server_threads,
        )

    def validate_web(self) -> None:
        if not isinstance(self.secret_key, str) or len(self.secret_key) < 32:
            raise SettingsError(
                f"{ENV_PREFIX}SECRET_KEY must be explicitly configured with at least 32 characters"
            )
        if self.debug and self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise SettingsError("debug mode may only bind to a loopback host")

    def runtime_directories(self) -> tuple[Path, ...]:
        return (
            self.db_path.parent,
            self.content_dir,
            self.run_dir,
            self.log_dir,
            self.temp_dir,
            self.tile_root,
        )

    def flask_config(self) -> dict[str, object]:
        # Session validity is enforced by backend.auth.session_policy.  Flask's cookie
        # lifetime is kept at the same absolute boundary so stale cookies are not retained
        # longer than the server-side policy permits.
        return {
            "SESSION_COOKIE_HTTPONLY": True,
            "SESSION_COOKIE_SAMESITE": "Lax",
            "SESSION_COOKIE_SECURE": self.session_cookie_secure,
            "PERMANENT_SESSION_LIFETIME": self.session_absolute_timeout_seconds,
            "MAX_CONTENT_LENGTH": self.max_content_length_bytes,
            "GIS_TILE_TEMPLATE": self.gis_tile_template,
            "JSON_SORT_KEYS": False,
        }


__all__ = ["AppSettings", "SettingsError", "ENV_PREFIX", "DEFAULT_DB_NAME"]