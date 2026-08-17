from __future__ import annotations

import uuid
from typing import Any, Mapping, Optional

from backend.auth.passwords import PasswordValidationError
from backend.auth.principal import Principal
from backend.storage.user_repository import (
    UserConflictError,
    UserNotFoundError,
    UserRepository,
)
from backend.web.error_mapping import map_expected_error
from backend.web.http import (
    ApiInputError,
    ApiResponse,
    error_body,
    reject_unknown,
    require_object,
    required_nonblank_string,
)


_ALLOWED_ROLES = ("viewer", "operator", "admin")


class UserAdminApi:
    """Small administrator API over the existing local UserRepository."""

    def __init__(self, repository: UserRepository):
        self.repository = repository

    @staticmethod
    def _public_user(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "user_id": item.get("user_id"),
            "login_name": item.get("login_name"),
            "display_name": item.get("display_name"),
            "role": item.get("role"),
            "is_disabled": bool(item.get("is_disabled")),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "last_login_at": item.get("last_login_at"),
        }

    @staticmethod
    def _handle(call) -> ApiResponse:
        try:
            return call()
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return mapped
            if isinstance(exc, UserNotFoundError):
                return ApiResponse(error_body("USER_NOT_FOUND", str(exc)), 404)
            if isinstance(exc, UserConflictError):
                return ApiResponse(error_body("USER_STATE_CONFLICT", str(exc)), 409)
            if isinstance(exc, PasswordValidationError):
                return ApiResponse(error_body("USER_VALIDATION_FAILED", str(exc), field="password"), 422)
            if isinstance(exc, ValueError):
                return ApiResponse(error_body("USER_VALIDATION_FAILED", str(exc)), 422)
            raise

    @staticmethod
    def _require_admin(principal: Principal) -> None:
        principal.require_permission("users.admin")

    def list(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            self._require_admin(principal)
            return ApiResponse(
                {
                    "users": [self._public_user(row) for row in self.repository.list_users()],
                    "roles": list(_ALLOWED_ROLES),
                },
                200,
            )
        return self._handle(action)

    def create(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            self._require_admin(principal)
            body = require_object(raw_body)
            reject_unknown(body, {"user_id", "login_name", "display_name", "role", "password"})
            login_name = required_nonblank_string(body, "login_name").strip()
            password = required_nonblank_string(body, "password")
            role = str(body.get("role") or "operator").strip().lower()
            if role not in _ALLOWED_ROLES:
                raise ApiInputError("role must be viewer, operator or admin", field="role")
            display_name: Optional[str] = body.get("display_name")
            if display_name is not None:
                if not isinstance(display_name, str):
                    raise ApiInputError("display_name must be a string or null", field="display_name")
                display_name = display_name.strip() or None
            raw_user_id = body.get("user_id")
            if raw_user_id is None:
                user_id = f"USR-{uuid.uuid4().hex[:12]}"
            else:
                if not isinstance(raw_user_id, str) or not raw_user_id.strip():
                    raise ApiInputError("user_id must be a nonblank string when supplied", field="user_id")
                user_id = raw_user_id.strip()
            created = self.repository.create_user(
                user_id=user_id,
                login_name=login_name,
                password=password,
                role=role,
                display_name=display_name,
            )
            return ApiResponse({"user": self._public_user(created)}, 201)
        return self._handle(action)

    def set_role(self, user_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            self._require_admin(principal)
            body = require_object(raw_body)
            reject_unknown(body, {"role"})
            role = required_nonblank_string(body, "role").strip().lower()
            if role not in _ALLOWED_ROLES:
                raise ApiInputError("role must be viewer, operator or admin", field="role")
            if user_id == principal.user_id and role != principal.role:
                raise ApiInputError(
                    "Current administrator cannot change own role from Settings",
                    field="role",
                    code="SELF_ADMIN_CHANGE_BLOCKED",
                )
            row = self.repository.set_role(user_id, role)
            return ApiResponse({"user": self._public_user(row)}, 200)
        return self._handle(action)

    def set_disabled(self, user_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            self._require_admin(principal)
            body = require_object(raw_body)
            reject_unknown(body, {"disabled"})
            disabled = body.get("disabled")
            if not isinstance(disabled, bool):
                raise ApiInputError("disabled must be boolean", field="disabled")
            if user_id == principal.user_id and disabled:
                raise ApiInputError(
                    "Current administrator cannot disable own account from Settings",
                    field="disabled",
                    code="SELF_ADMIN_CHANGE_BLOCKED",
                )
            row = self.repository.set_disabled(user_id, disabled)
            return ApiResponse({"user": self._public_user(row)}, 200)
        return self._handle(action)

    def reset_password(self, user_id: str, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            self._require_admin(principal)
            if user_id == principal.user_id:
                raise ApiInputError(
                    "Use the normal change-password flow for the current account",
                    field="user_id",
                    code="SELF_ADMIN_CHANGE_BLOCKED",
                )
            body = require_object(raw_body)
            reject_unknown(body, {"new_password"})
            new_password = required_nonblank_string(body, "new_password")
            row = self.repository.set_password(user_id, new_password)
            return ApiResponse({"user": self._public_user(row)}, 200)
        return self._handle(action)


__all__ = ["UserAdminApi"]
