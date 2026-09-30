from __future__ import annotations

from backend.auth.principal import Principal
from backend.web.assessment_results_api import AssessmentResultsApi
from backend.web.error_mapping import map_expected_error
from backend.web.flask_runs import PrincipalResolver
from backend.web.http import error_body


def create_assessment_results_blueprint(
    *,
    api: AssessmentResultsApi,
    principal_resolver: PrincipalResolver,
):
    try:
        from flask import Blueprint, current_app, jsonify, request
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask is required to bind assessment results routes") from exc

    blueprint = Blueprint(
        "assessment_results_v1", __name__, url_prefix="/api/results/assessment"
    )

    def invoke(call):
        principal = principal_resolver(request)
        if principal is None:
            return jsonify(error_body("AUTHENTICATION_REQUIRED", "Authentication is required")), 401
        if not isinstance(principal, Principal):
            raise TypeError("principal_resolver must return Principal or None")
        try:
            response = call(principal)
            return jsonify(dict(response.body)), response.status
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return jsonify(dict(mapped.body)), mapped.status
            current_app.logger.exception("Unhandled assessment results API error")
            return jsonify(error_body("INTERNAL_ERROR", "Unexpected server error")), 500

    @blueprint.get("/overview")
    def overview():
        return invoke(lambda principal: api.overview(principal=principal))

    @blueprint.get("/curves")
    def curves():
        return invoke(
            lambda principal: api.curves(
                principal=principal, airport_id=request.args.get("airport_id")
            )
        )

    @blueprint.get("/comparisons")
    def comparisons():
        return invoke(
            lambda principal: api.comparisons(
                principal=principal, limit=request.args.get("limit")
            )
        )

    @blueprint.get("/evaluations")
    def evaluations():
        return invoke(
            lambda principal: api.evaluations(
                principal=principal, limit=request.args.get("limit")
            )
        )

    return blueprint


__all__ = ["create_assessment_results_blueprint"]
