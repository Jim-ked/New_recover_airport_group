from __future__ import annotations

from backend.auth.csrf import CsrfValidationError
from backend.auth.principal import PermissionDeniedError
from backend.domain.airport import AirportValidationError
from backend.domain.airport_operations import AirportOperationsValidationError
from backend.domain.catalog import CatalogValidationError
from backend.domain.damage import DamageValidationError
from backend.domain.indicator import IndicatorValidationError
from backend.domain.mission import MissionValidationError
from backend.domain.run_config import RunConfigValidationError
from backend.domain.situation import SituationValidationError
from backend.analysis.comparison import ComparisonError
from backend.domain.run_snapshot import RunSnapshotValidationError
from backend.services.od_distance_service import ODDistanceServiceError
from backend.services.run_result_service import (
    RunResultAccessError,
    RunResultNotFoundError,
    RunResultNotReadyError,
    RunResultServiceError,
)
from backend.services.run_service import RunAccessError, RunNotFoundError, RunRetryNotAllowedError
from backend.services.run_snapshot_service import RunSnapshotServiceError
from backend.services.run_submission_service import (
    RunSubmissionBlockedError,
    RunSubmissionSituationNotFoundError,
    RunSubmissionStaleValidationError,
)
from backend.storage.airport_repository import CatalogConflictError, CatalogReferenceError
from backend.storage.mission_repository import MissionConflictError, MissionReferenceError
from backend.storage.indicator_repository import (
    IndicatorConflictError, IndicatorNotFoundError, IndicatorProtectionError, IndicatorStateError,
)
from backend.storage.run_repository import RunConflictError, RunRepositoryError, RunTransitionError
from backend.storage.run_snapshot_repository import RunSnapshotConflictError
from backend.storage.situation_repository import SituationAccessError, SituationConflictError, SituationOwnershipError
from backend.web.http import ApiInputError, ApiResponse, error_body


def _field(exc: Exception):
    value = getattr(exc, "field", None)
    return value if isinstance(value, str) and value else None


def map_expected_error(exc: Exception) -> ApiResponse | None:
    if isinstance(exc, ApiInputError):
        return ApiResponse(error_body(exc.code, str(exc), field=exc.field), 400)

    if isinstance(exc, PermissionDeniedError):
        return ApiResponse(error_body("PERMISSION_DENIED", "Current user lacks required permission"), 403)

    if isinstance(exc, CsrfValidationError):
        return ApiResponse(error_body("CSRF_FAILED", "CSRF validation failed"), 403)

    if isinstance(exc, (RunAccessError, RunResultAccessError)):
        return ApiResponse(error_body("FORBIDDEN", "Run is not accessible to current user"), 403)

    if isinstance(exc, SituationAccessError):
        return ApiResponse(error_body("FORBIDDEN", "Situation is not accessible to current user"), 403)

    if isinstance(exc, (RunNotFoundError, RunResultNotFoundError)):
        return ApiResponse(error_body("RUN_NOT_FOUND", str(exc)), 404)

    if isinstance(exc, RunSubmissionSituationNotFoundError):
        return ApiResponse(error_body("SITUATION_NOT_FOUND", str(exc), field="situation_id"), 404)

    if isinstance(exc, RunSubmissionStaleValidationError):
        return ApiResponse(error_body("RUN_VALIDATION_STALE", str(exc)), 409)

    if isinstance(exc, RunSubmissionBlockedError):
        return ApiResponse(
            {
                "error": {
                    "code": "RUN_PREFLIGHT_FAILED",
                    "message": str(exc),
                    "validation": exc.report.to_dict(),
                }
            },
            422,
        )

    if isinstance(exc, (RunConflictError, RunTransitionError, RunSnapshotConflictError, RunRetryNotAllowedError)):
        return ApiResponse(error_body("RUN_STATE_CONFLICT", str(exc)), 409)

    if isinstance(exc, (CatalogConflictError, MissionConflictError)):
        return ApiResponse(error_body("CATALOG_STATE_CONFLICT", str(exc)), 409)

    if isinstance(exc, IndicatorConflictError):
        return ApiResponse(error_body("INDICATOR_STATE_CONFLICT", str(exc)), 409)

    if isinstance(exc, (IndicatorProtectionError, IndicatorStateError)):
        return ApiResponse(error_body("INDICATOR_OPERATION_BLOCKED", str(exc)), 409)

    if isinstance(exc, IndicatorNotFoundError):
        return ApiResponse(error_body("INDICATOR_NOT_FOUND", str(exc)), 404)

    if isinstance(exc, (CatalogReferenceError, MissionReferenceError)):
        return ApiResponse(error_body("CATALOG_IN_USE", str(exc)), 409)

    if isinstance(exc, SituationConflictError):
        return ApiResponse(error_body("SITUATION_STATE_CONFLICT", str(exc)), 409)

    if isinstance(exc, SituationOwnershipError):
        return ApiResponse(error_body("SITUATION_OWNER_INVALID", str(exc)), 422)

    if isinstance(exc, RunResultNotReadyError):
        return ApiResponse(error_body("RUN_RESULT_NOT_READY", str(exc)), 409)

    if isinstance(exc, ComparisonError):
        return ApiResponse(error_body("RUNS_NOT_COMPARABLE", str(exc)), 422)

    if isinstance(
        exc,
        (
            AirportValidationError, AirportOperationsValidationError, CatalogValidationError,
            DamageValidationError, MissionValidationError, SituationValidationError, IndicatorValidationError,
        ),
    ):
        return ApiResponse(error_body("VALIDATION_FAILED", str(exc), field=_field(exc)), 422)

    if isinstance(
        exc,
        (RunConfigValidationError, RunSnapshotValidationError, ODDistanceServiceError, RunSnapshotServiceError),
    ):
        return ApiResponse(error_body("RUN_VALIDATION_FAILED", str(exc), field=_field(exc)), 422)

    # A repository failure is an internal persistence error unless it is one of the
    # explicit conflict/transition classes handled above.
    if isinstance(exc, (RunRepositoryError, RunResultServiceError)):
        return ApiResponse(error_body("INTERNAL_ERROR", "Run service persistence/result invariant failed"), 500)

    return None


__all__ = ["map_expected_error"]
