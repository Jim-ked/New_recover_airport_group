from __future__ import annotations

from backend.auth.principal import Principal
from backend.web.error_mapping import map_expected_error
from backend.web.http import ApiResponse


class AccountApi:
    """Read-only authenticated-session facts for permission-aware UI rendering."""

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
            return ApiResponse(
                {
                    "user_id": principal.user_id,
                    "role": principal.role,
                    "is_admin": principal.is_admin,
                    "permissions": sorted(principal.permissions or ()),
                },
                200,
            )

        return self._handle(action)


__all__ = ["AccountApi"]
