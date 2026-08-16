from __future__ import annotations

from typing import Any, Callable, Optional

from backend.auth.principal import Principal
from backend.web.error_mapping import map_expected_error
from backend.web.http import error_body
from backend.web.run_api import RunApi


PrincipalResolver = Callable[[Any], Optional[Principal]]
MutationGuard = Callable[[Any, Principal], None]


def create_run_blueprint(
    *,
    api: RunApi,
    principal_resolver: PrincipalResolver,
    mutation_guard: Optional[MutationGuard] = None,
):
    """Create the Flask `/api/runs*` Blueprint.

    Flask is imported lazily so domain/service tests do not require the web runtime. The
    application must explicitly provide a principal resolver; this module never trusts a
    request header as an identity source. A project-level CSRF/mutation guard can be
    injected without coupling RunApi to Flask/session implementation.
    """

    try:
        from flask import Blueprint, current_app, jsonify, request
        from werkzeug.exceptions import BadRequest
    except ImportError as exc:  # pragma: no cover - exercised only in runtime packaging
        raise RuntimeError("Flask runtime dependency is required to bind Run API") from exc

    bp = Blueprint("runs_v1", __name__, url_prefix="/api")

    def principal_or_401():
        principal = principal_resolver(request)
        if principal is None:
            return None, (jsonify(error_body("AUTHENTICATION_REQUIRED", "Authentication is required")), 401)
        if not isinstance(principal, Principal):
            raise TypeError("principal_resolver must return Principal or None")
        return principal, None

    def json_body(*, required: bool):
        if not request.data and not required:
            return None
        try:
            value = request.get_json(force=False, silent=False)
        except BadRequest:
            raise
        if value is None and required:
            raise BadRequest("JSON request body is required")
        return value

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
            current_app.logger.exception("Unhandled Run API error")
            return jsonify(error_body("INTERNAL_ERROR", "Unexpected server error")), 500

    def guard_mutation(principal: Principal) -> None:
        if mutation_guard is not None:
            mutation_guard(request, principal)

    @bp.post("/runs/validate")
    def validate_run():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            guard_mutation(principal)
            return render(api.validate(json_body(required=True), principal=principal))
        return invoke(action)

    @bp.post("/runs")
    def submit_run():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            guard_mutation(principal)
            return render(api.submit(json_body(required=True), principal=principal))
        return invoke(action)

    @bp.get("/runs")
    def list_runs():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            statuses = request.args.getlist("status")
            return render(
                api.list(
                    principal=principal,
                    statuses=(statuses if statuses else None),
                    limit=request.args.get("limit"),
                    offset=request.args.get("offset"),
                    situation_id=request.args.get("situation_id"),
                    run_id_query=request.args.get("q"),
                    task_id=request.args.get("task_id"),
                    selected_airport_id=request.args.get("selected_airport_id"),
                    damage_scenario_id=request.args.get("damage_scenario_id"),
                    no_damage=request.args.get("no_damage"),
                    cluster_enabled=request.args.get("cluster_enabled"),
                    created_after=request.args.get("created_after"),
                    created_before=request.args.get("created_before"),
                )
            )
        return invoke(action)

    @bp.get("/runs/<run_id>")
    def run_detail(run_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            return render(api.detail(run_id, principal=principal))
        return invoke(action)

    @bp.get("/runs/<run_id>/events")
    def run_events(run_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            return render(
                api.events(
                    run_id,
                    principal=principal,
                    after_seq=request.args.get("after_seq"),
                    limit=request.args.get("limit"),
                )
            )
        return invoke(action)

    @bp.post("/runs/<run_id>/retry")
    def retry_run(run_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            guard_mutation(principal)
            return render(api.retry(run_id, principal=principal, raw_body=json_body(required=False)))
        return invoke(action)

    @bp.post("/runs/<run_id>/cancel")
    def cancel_run(run_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            guard_mutation(principal)
            return render(api.cancel(run_id, principal=principal, raw_body=json_body(required=False)))
        return invoke(action)


    @bp.get("/runs/<run_id>/situation")
    def run_situation(run_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            return render(api.situation(run_id, principal=principal))
        return invoke(action)

    @bp.get("/runs/<run_id>/runtime")
    def run_runtime(run_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            return render(api.runtime_projection(run_id, principal=principal))
        return invoke(action)

    @bp.get("/runs/<run_id>/solution")
    def run_solution(run_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            return render(api.solution(run_id, principal=principal))
        return invoke(action)

    @bp.get("/runs/<run_id>/metrics")
    def run_metrics(run_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            return render(api.metrics(run_id, principal=principal))
        return invoke(action)

    return bp


__all__ = ["create_run_blueprint", "PrincipalResolver", "MutationGuard"]
