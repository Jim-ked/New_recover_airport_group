from __future__ import annotations

from typing import Any, Optional

from backend.auth.principal import Principal
from backend.storage.audit_repository import AuditRepository
from backend.web.error_mapping import map_expected_error
from backend.web.http import ApiInputError, ApiResponse, parse_nonnegative_int, parse_positive_int


def _optional_text(raw: Any, field: str) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ApiInputError(f"{field} must be a nonblank string", field=field)
    return raw.strip()


class AuditApi:
    def __init__(self, *, repository: AuditRepository) -> None:
        self.repository = repository

    @staticmethod
    def _handle(call) -> ApiResponse:
        try:
            return call()
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return mapped
            raise

    def list(
        self,
        *,
        principal: Principal,
        actor_user_id: Any = None,
        q: Any = None,
        resource_type: Any = None,
        resource_id: Any = None,
        outcome: Any = None,
        created_after: Any = None,
        created_before: Any = None,
        limit: Any = None,
        offset: Any = None,
    ) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("audit.read")
            parsed_outcome = _optional_text(outcome, "outcome")
            if parsed_outcome is not None and parsed_outcome not in {"success", "denied", "error"}:
                raise ApiInputError("outcome must be success, denied or error", field="outcome")
            parsed_limit = parse_positive_int(limit, field="limit", default=100, maximum=500)
            parsed_offset = parse_nonnegative_int(offset, field="offset", default=0, maximum=2_147_483_647)
            items, total = self.repository.query(
                actor_user_id=_optional_text(actor_user_id, "actor_user_id"),
                action_query=_optional_text(q, "q"),
                resource_type=_optional_text(resource_type, "resource_type"),
                resource_id=_optional_text(resource_id, "resource_id"),
                outcome=parsed_outcome,
                created_after=_optional_text(created_after, "created_after"),
                created_before=_optional_text(created_before, "created_before"),
                limit=parsed_limit,
                offset=parsed_offset,
            )
            return ApiResponse(
                {
                    "items": [item.to_dict() for item in items],
                    "total": total,
                    "limit": parsed_limit,
                    "offset": parsed_offset,
                },
                200,
            )

        return self._handle(action)


__all__ = ["AuditApi"]
