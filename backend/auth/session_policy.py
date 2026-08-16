from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from backend.auth.principal import Principal

DEFAULT_IDLE_TIMEOUT_SECONDS = 30 * 60
DEFAULT_ABSOLUTE_TIMEOUT_SECONDS = 8 * 60 * 60


@dataclass(frozen=True)
class SessionValidation:
    principal: Optional[Principal]
    reason: Optional[str]
    refresh_last_seen: bool = False

    @property
    def valid(self) -> bool:
        return self.principal is not None


def validate_session(
    session_user: Any,
    authority_user: Any,
    *,
    now: int,
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS,
    absolute_timeout_seconds: int = DEFAULT_ABSOLUTE_TIMEOUT_SECONDS,
) -> SessionValidation:
    """Validate a browser session against the current SQLite account authority.

    ``auth_revision`` makes password changes, role changes and disabling an account revoke
    every pre-existing session without maintaining a server-side session table.  Both idle
    and absolute timeouts are explicit so an offline deployment never has a permanently
    valid signed cookie merely because the cookie itself still verifies cryptographically.
    """
    if not isinstance(session_user, Mapping):
        return SessionValidation(None, "missing")
    if not isinstance(authority_user, Mapping):
        return SessionValidation(None, "account_missing")
    try:
        user_id = str(session_user["user_id"])
        auth_revision = int(session_user["auth_revision"])
        issued_at = int(session_user["issued_at"])
        last_seen_at = int(session_user["last_seen_at"])
        current_revision = int(authority_user["auth_revision"])
    except (KeyError, TypeError, ValueError):
        return SessionValidation(None, "malformed")
    if not user_id or user_id != authority_user.get("user_id"):
        return SessionValidation(None, "identity_changed")
    if bool(authority_user.get("is_disabled")):
        return SessionValidation(None, "account_disabled")
    if auth_revision != current_revision:
        return SessionValidation(None, "auth_revision_changed")
    if now < issued_at or now < last_seen_at:
        return SessionValidation(None, "clock_invalid")
    if absolute_timeout_seconds > 0 and now - issued_at > absolute_timeout_seconds:
        return SessionValidation(None, "absolute_timeout")
    if idle_timeout_seconds > 0 and now - last_seen_at > idle_timeout_seconds:
        return SessionValidation(None, "idle_timeout")
    role = str(authority_user.get("role") or "viewer")
    return SessionValidation(
        Principal(user_id=user_id, role=role, is_admin=(role == "admin")),
        None,
        refresh_last_seen=(now > last_seen_at),
    )


__all__ = [
    "SessionValidation",
    "validate_session",
    "DEFAULT_IDLE_TIMEOUT_SECONDS",
    "DEFAULT_ABSOLUTE_TIMEOUT_SECONDS",
]
