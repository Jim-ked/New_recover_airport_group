from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from backend.auth.passwords import hash_password, verify_password
from backend.auth.principal import normalize_role
from backend.storage.database import initialize_database

_LOGIN_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class UserRepositoryError(RuntimeError):
    pass


class UserNotFoundError(UserRepositoryError):
    pass


class UserConflictError(UserRepositoryError):
    pass


class AuthenticationFailedError(UserRepositoryError):
    pass


class AccountDisabledError(UserRepositoryError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __enter__(self):
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class UserRepository:
    """SQLite authority for local/offline application accounts.

    Only password hashes are persisted. ``auth_revision`` invalidates every existing
    session when password, role, or disabled state changes.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        initialize_database(self.db_path)

    @staticmethod
    def _validate_identity(user_id: str, login_name: str) -> None:
        if not isinstance(user_id, str) or not _ID_RE.fullmatch(user_id):
            raise ValueError("user_id must be a stable identifier")
        if not isinstance(login_name, str) or not _LOGIN_RE.fullmatch(login_name):
            raise ValueError("login_name contains unsupported characters")

    @staticmethod
    def _payload(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "user_id": row["user_id"],
            "login_name": row["login_name"],
            "display_name": row["display_name"],
            "role": row["role"],
            "is_disabled": bool(row["is_disabled"]),
            "auth_revision": int(row["auth_revision"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_login_at": row["last_login_at"],
        }

    def count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def get(self, user_id: str) -> Dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            raise UserNotFoundError(f"user not found: {user_id}")
        return self._payload(row)

    def get_by_login_name(self, login_name: str, *, include_password_hash: bool = False) -> Dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE login_name=?", (login_name,)).fetchone()
        if row is None:
            raise UserNotFoundError(f"user not found: {login_name}")
        item = self._payload(row)
        if include_password_hash:
            item["password_hash"] = row["password_hash"]
        return item

    def list_users(self) -> list[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY login_name").fetchall()
        return [self._payload(r) for r in rows]

    def create_user(
        self,
        *,
        user_id: str,
        login_name: str,
        password: str,
        role: str = "operator",
        display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._validate_identity(user_id, login_name)
        canonical_role = normalize_role(role)
        if canonical_role == "user":
            canonical_role = "operator"
        if canonical_role not in {"viewer", "operator", "admin"}:
            raise ValueError("role must be viewer, operator or admin")
        if display_name is not None and (not isinstance(display_name, str) or not display_name.strip()):
            raise ValueError("display_name must be nonblank or null")
        encoded = hash_password(password)
        try:
            with self.connect() as conn:
                conn.execute(
                    """INSERT INTO users
                       (user_id,login_name,display_name,password_hash,role,is_disabled,auth_revision)
                       VALUES (?,?,?,?,?,0,1)""",
                    (user_id, login_name, display_name, encoded, canonical_role),
                )
        except sqlite3.IntegrityError as exc:
            raise UserConflictError(f"user_id or login_name already exists: {login_name}") from exc
        return self.get(user_id)

    def authenticate(self, login_name: str, password: str) -> Dict[str, Any]:
        try:
            item = self.get_by_login_name(login_name, include_password_hash=True)
        except UserNotFoundError as exc:
            # Keep login failure externally indistinguishable for account enumeration.
            raise AuthenticationFailedError("invalid login name or password") from exc
        if item["is_disabled"]:
            raise AccountDisabledError("account is disabled")
        if not verify_password(password, item.pop("password_hash")):
            raise AuthenticationFailedError("invalid login name or password")
        with self.connect() as conn:
            conn.execute("UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE user_id=?", (item["user_id"],))
        return self.get(item["user_id"])

    def change_password(self, user_id: str, *, current_password: str, new_password: str) -> Dict[str, Any]:
        user = self.get(user_id)
        auth = self.get_by_login_name(user["login_name"], include_password_hash=True)
        if auth["is_disabled"]:
            raise AccountDisabledError("account is disabled")
        if not verify_password(current_password, auth["password_hash"]):
            raise AuthenticationFailedError("current password is incorrect")
        return self.set_password(user_id, new_password)

    def touch_last_login(self, user_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,))

    def set_password(self, user_id: str, password: str) -> Dict[str, Any]:
        encoded = hash_password(password)
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                raise UserNotFoundError(f"user not found: {user_id}")
            conn.execute(
                """UPDATE users SET password_hash=?,auth_revision=auth_revision+1,
                   updated_at=CURRENT_TIMESTAMP WHERE user_id=?""",
                (encoded, user_id),
            )
        return self.get(user_id)

    def set_disabled(self, user_id: str, disabled: bool) -> Dict[str, Any]:
        if not isinstance(disabled, bool):
            raise ValueError("disabled must be boolean")
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                raise UserNotFoundError(f"user not found: {user_id}")
            conn.execute(
                """UPDATE users SET is_disabled=?,auth_revision=auth_revision+1,
                   updated_at=CURRENT_TIMESTAMP WHERE user_id=?""",
                (int(disabled), user_id),
            )
        return self.get(user_id)

    def set_role(self, user_id: str, role: str) -> Dict[str, Any]:
        canonical_role = normalize_role(role)
        if canonical_role == "user":
            canonical_role = "operator"
        if canonical_role not in {"viewer", "operator", "admin"}:
            raise ValueError("role must be viewer, operator or admin")
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                raise UserNotFoundError(f"user not found: {user_id}")
            conn.execute(
                """UPDATE users SET role=?,auth_revision=auth_revision+1,
                   updated_at=CURRENT_TIMESTAMP WHERE user_id=?""",
                (canonical_role, user_id),
            )
        return self.get(user_id)

    def bootstrap_admin(self, *, login_name: str, password: str, user_id: str = "admin") -> Optional[Dict[str, Any]]:
        """Create the first admin only when the authority is empty.

        Deployment must supply the credentials explicitly. There is no built-in default
        password in source code.
        """
        if self.count() != 0:
            return None
        return self.create_user(
            user_id=user_id,
            login_name=login_name,
            password=password,
            role="admin",
            display_name="管理员",
        )


__all__ = [
    "UserRepository", "UserRepositoryError", "UserNotFoundError", "UserConflictError",
    "AuthenticationFailedError", "AccountDisabledError",
]
