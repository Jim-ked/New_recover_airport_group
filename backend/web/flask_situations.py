from __future__ import annotations

from backend.auth.principal import Principal
from backend.web.error_mapping import map_expected_error
from backend.web.http import error_body
from backend.web.situation_api import SituationApi
from backend.web.flask_runs import MutationGuard, PrincipalResolver


def create_situation_blueprint(
    *,
    api: SituationApi,
    principal_resolver: PrincipalResolver,
    mutation_guard: MutationGuard = None,
):
    try:
        from flask import Blueprint, current_app, jsonify, request
        from werkzeug.exceptions import BadRequest
    except ImportError as exc:  # pragma: no cover - runtime packaging concern
        raise RuntimeError("Flask runtime dependency is required to bind Situation API") from exc

    bp = Blueprint("situations_v1", __name__, url_prefix="/api")

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
            current_app.logger.exception("Unhandled Situation API error")
            return jsonify(error_body("INTERNAL_ERROR", "Unexpected server error")), 500

    def guard(principal: Principal) -> None:
        if mutation_guard is not None:
            mutation_guard(request, principal)

    @bp.get("/situations")
    def list_situations():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            return render(api.list(principal=principal, query=request.args.get("q"), limit=request.args.get("limit"), offset=request.args.get("offset")))
        return invoke(action)

    @bp.post("/situations")
    def create_situation():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            guard(principal)
            return render(api.create(request.get_json(force=False, silent=False), principal=principal))
        return invoke(action)

    @bp.post("/situations/working-copy/canonicalize")
    def canonicalize_working_copy():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            guard(principal)
            return render(api.canonicalize_working_copy(
                request.get_json(force=False, silent=False), principal=principal
            ))
        return invoke(action)

    @bp.post("/situations/working-copy/copy-airport")
    def copy_airport_to_working_copy():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            guard(principal)
            return render(api.copy_airport_to_working_copy(
                request.get_json(force=False, silent=False), principal=principal
            ))
        return invoke(action)

    @bp.post("/situations/working-copy/copy-mission")
    def copy_mission_to_working_copy():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            guard(principal)
            return render(api.copy_mission_to_working_copy(
                request.get_json(force=False, silent=False), principal=principal
            ))
        return invoke(action)

    @bp.get("/situations/<situation_id>")
    def situation_detail(situation_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            return render(api.detail(situation_id, principal=principal))
        return invoke(action)

    @bp.put("/situations/<situation_id>")
    def update_situation(situation_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            guard(principal)
            return render(api.update(situation_id, request.get_json(force=False, silent=False), principal=principal))
        return invoke(action)

    @bp.delete("/situations/<situation_id>")
    def delete_situation(situation_id: str):
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            guard(principal)
            return render(api.delete(situation_id, request.get_json(force=False, silent=False), principal=principal))
        return invoke(action)

    return bp


__all__ = ["create_situation_blueprint"]
