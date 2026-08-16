from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, Mapping, Optional


class PrincipalValidationError(ValueError):
    pass


class PermissionDeniedError(PermissionError):
    def __init__(self, permission: str):
        super().__init__(f"permission required: {permission}")
        self.permission = permission


ROLE_RANK = {
    "viewer": 1,
    "operator": 2,
    "admin": 3,
    # legacy role retained only at the session adapter boundary
    "user": 2,
}

VIEWER_PERMISSIONS = frozenset({
    "catalog.read",
    "situations.read",
    "indicators.read",
    "runs.read",
    "results.read",
})
OPERATOR_PERMISSIONS = VIEWER_PERMISSIONS | frozenset({
    "situations.write",
    "indicators.score",
    "runs.execute",
})
ADMIN_PERMISSIONS = OPERATOR_PERMISSIONS | frozenset({
    "catalog.write",
    "indicators.write",
    "experts.manage",
    "users.admin",
    "results.export",
    "audit.read",
})
ROLE_PERMISSIONS = {
    "viewer": VIEWER_PERMISSIONS,
    "operator": OPERATOR_PERMISSIONS,
    "admin": ADMIN_PERMISSIONS,
    "user": OPERATOR_PERMISSIONS,
}


def normalize_role(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "viewer"
    role = value.strip().lower()
    return role if role in ROLE_RANK else "viewer"


def permissions_for_role(role: Any) -> FrozenSet[str]:
    return frozenset(ROLE_PERMISSIONS.get(normalize_role(role), VIEWER_PERMISSIONS))


@dataclass(frozen=True)
class Principal:
    """Authenticated actor passed from auth/session into application API handlers.

    The object carries only stable authorization facts.  Flask session/cookie details are
    converted at the adapter boundary and never leak into services or analysis code.
    """

    user_id: str
    is_admin: bool = False
    role: str = "operator"
    permissions: Optional[FrozenSet[str]] = None

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise PrincipalValidationError("user_id must be a nonblank string")
        if not isinstance(self.is_admin, bool):
            raise PrincipalValidationError("is_admin must be bool")
        role = "admin" if self.is_admin else normalize_role(self.role)
        is_admin = role == "admin"
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "is_admin", is_admin)
        if self.permissions is None:
            perms = permissions_for_role(role)
        else:
            try:
                perms = frozenset(str(x) for x in self.permissions)
            except TypeError as exc:
                raise PrincipalValidationError("permissions must be iterable") from exc
            if any(not x for x in perms):
                raise PrincipalValidationError("permissions cannot contain blank values")
        object.__setattr__(self, "permissions", perms)

    def has_permission(self, permission: str) -> bool:
        return bool(self.is_admin or permission in (self.permissions or ()))

    def require_permission(self, permission: str) -> None:
        if not self.has_permission(permission):
            raise PermissionDeniedError(permission)


def principal_from_session_user(user: Any) -> Optional[Principal]:
    """Convert the existing canonical Flask session user shape into a Principal.

    Expected session facts are deliberately small: `user_id` and `role`.  Display name,
    login timestamp and other UI/session metadata are ignored by the business API.
    """
    if user is None:
        return None
    if not isinstance(user, Mapping):
        raise PrincipalValidationError("session user must be a mapping")
    user_id = user.get("user_id")
    if not isinstance(user_id, str) or not user_id.strip():
        raise PrincipalValidationError("session user_id must be a nonblank string")
    role = normalize_role(user.get("role"))
    return Principal(user_id=user_id, role=role, is_admin=(role == "admin"))


__all__ = [
    "Principal",
    "PrincipalValidationError",
    "PermissionDeniedError",
    "ROLE_RANK",
    "ROLE_PERMISSIONS",
    "permissions_for_role",
    "normalize_role",
    "principal_from_session_user",
]
