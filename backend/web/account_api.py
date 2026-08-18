from __future__ import annotations

from backend.auth.principal import Principal
from backend.storage.user_repository import UserRepository
from backend.web.error_mapping import map_expected_error
from backend.web.http import ApiResponse


class AccountApi:
    """Read-only authenticated-session facts for permission-aware UI rendering."""

    def __init__(self, user_repository: UserRepository | None = None) -> None:
        self.user_repository = user_repository

    @staticmethod
    def _handle(call) -> ApiResponse:
        try:
            return call()
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return mapped
            raise

    def current(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            # Authentication has already happened at the adapter boundary.  Returning the
            # server's effective permissions lets the UI avoid presenting impossible
            # actions while backend authorization remains the final authority.
            body = {
                "user_id": principal.user_id,
                "role": principal.role,
                "is_admin": principal.is_admin,
                "permissions": sorted(principal.permissions or ()),
            }
            if self.user_repository is not None:
                profile = self.user_repository.get(principal.user_id)
                body.update({
                    "login_name": profile.get("login_name"),
                    "display_name": profile.get("display_name"),
                    "created_at": profile.get("created_at"),
                    "last_login_at": profile.get("last_login_at"),
                })
            return ApiResponse(body, 200)

        return self._handle(action)


__all__ = ["AccountApi"]
