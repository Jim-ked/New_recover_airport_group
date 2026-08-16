from __future__ import annotations

from typing import Any, Callable, Optional

from backend.auth.principal import Principal
from backend.web.error_mapping import map_expected_error
from backend.web.http import error_body
from backend.services.result_export_service import ResultExportService, ResultExportError
from backend.web.results_api import ResultsApi


PrincipalResolver = Callable[[Any], Optional[Principal]]


def create_results_blueprint(*, api: ResultsApi, principal_resolver: PrincipalResolver):
    try:
        from flask import Blueprint, current_app, jsonify, request
        from werkzeug.exceptions import BadRequest
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask runtime dependency is required to bind Results API") from exc

    bp = Blueprint("results_v1", __name__, url_prefix="/api")

    def principal_or_401():
        principal = principal_resolver(request)
        if principal is None:
            return None, (jsonify(error_body("AUTHENTICATION_REQUIRED", "Authentication is required")), 401)
        if not isinstance(principal, Principal):
            raise TypeError("principal_resolver must return Principal or None")
        return principal, None

    def render(response):
        return jsonify(dict(response.body)), response.status

    def invoke(fn):
        try:
            return fn()
        except BadRequest as exc:
            return jsonify(error_body("INVALID_JSON", str(exc))), 400
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return jsonify(dict(mapped.body)), mapped.status
            current_app.logger.exception("Unhandled Results API error")
            return jsonify(error_body("INTERNAL_ERROR", "Unexpected server error")), 500

    @bp.get("/results/comparable-runs")
    def comparable_runs():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            return render(api.comparable_runs(
                principal=principal,
                base_run_id=request.args.get("base_run_id"),
                mode=request.args.get("mode"),
            ))
        return invoke(action)


    @bp.get("/results/damage-candidates")
    def damage_candidates():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            return render(api.damage_candidates(principal=principal))
        return invoke(action)

    @bp.post("/results/damage-comparison")
    def damage_comparison():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            body = request.get_json(force=False, silent=False)
            return render(api.damage_comparison(body, principal=principal))
        return invoke(action)

    @bp.post("/results/scenario-comparison")
    def scenario_comparison():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            body = request.get_json(force=False, silent=False)
            return render(api.scenario_comparison(body, principal=principal))
        return invoke(action)

    @bp.post("/results/config-comparison")
    def configuration_comparison():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            body = request.get_json(force=False, silent=False)
            return render(api.configuration_comparison(body, principal=principal))
        return invoke(action)

    @bp.post("/results/export-data")
    def export_data():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            body = request.get_json(force=False, silent=False)
            return render(api.export_data(body, principal=principal))
        return invoke(action)


    @bp.post("/results/export-file")
    def export_file():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            body = request.get_json(force=False, silent=False)
            if not isinstance(body, dict):
                return jsonify(error_body("INVALID_BODY", "JSON object body is required")), 400
            fmt = str(body.get("format") or "").strip().lower()
            source_body = dict(body)
            source_body.pop("format", None)
            source = api.export_data(source_body, principal=principal)
            if source.status >= 400:
                return render(source)
            try:
                rendered = ResultExportService().render(source.body, fmt)
            except ResultExportError as exc:
                return jsonify(error_body("RESULT_EXPORT_INVALID", str(exc))), 400
            from flask import g, make_response
            g.audit_details = {
                "results_export": {
                    "format": fmt,
                    "kind": source.body.get("kind"),
                    "source_run_ids": source.body.get("source_run_ids"),
                }
            }
            response = make_response(rendered.content)
            response.headers["Content-Type"] = rendered.mimetype
            response.headers["Content-Disposition"] = f'attachment; filename="{rendered.filename}"'
            return response
        return invoke(action)



    return bp


__all__ = ["create_results_blueprint"]
