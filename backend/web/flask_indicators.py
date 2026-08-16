from __future__ import annotations

from typing import Optional

from backend.auth.principal import Principal
from backend.web.error_mapping import map_expected_error
from backend.web.flask_runs import MutationGuard, PrincipalResolver
from backend.web.http import error_body
from backend.web.indicator_api import IndicatorApi


def create_indicator_blueprint(
    *, api: IndicatorApi, principal_resolver: PrincipalResolver,
    mutation_guard: Optional[MutationGuard] = None,
):
    try:
        from flask import Blueprint, current_app, jsonify, request
        from werkzeug.exceptions import BadRequest
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask runtime dependency is required to bind Indicator API") from exc

    bp = Blueprint("indicators_v1", __name__, url_prefix="/api")

    def principal_or_401():
        principal = principal_resolver(request)
        if principal is None:
            return None, (jsonify(error_body("AUTHENTICATION_REQUIRED", "Authentication is required")), 401)
        if not isinstance(principal, Principal):
            raise TypeError("principal_resolver must return Principal or None")
        return principal, None

    def body(required=True):
        if not request.data and not required:
            return None
        try:
            value = request.get_json(force=False, silent=False)
        except BadRequest:
            raise
        if value is None and required:
            raise BadRequest("JSON request body is required")
        return value

    def render(response): return jsonify(dict(response.body)), response.status
    def guard(p):
        if mutation_guard is not None: mutation_guard(request, p)

    def invoke(fn):
        try: return fn()
        except BadRequest as exc: return jsonify(error_body("INVALID_JSON", str(exc))), 400
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None: return jsonify(dict(mapped.body)), mapped.status
            current_app.logger.exception("Unhandled Indicator API error")
            return jsonify(error_body("INTERNAL_ERROR", "Unexpected server error")), 500

    def read(fn):
        def action():
            p, denied = principal_or_401()
            if denied is not None: return denied
            return render(fn(p))
        return invoke(action)

    def mutate(fn):
        def action():
            p, denied = principal_or_401()
            if denied is not None: return denied
            guard(p)
            return render(fn(p))
        return invoke(action)

    @bp.get("/indicators")
    def indicator_tree(): return read(lambda p: api.tree(principal=p, indicator_set_id=request.args.get("indicator_set_id")))
    @bp.post("/indicators")
    def create_indicator(): return mutate(lambda p: api.create_node(body(), principal=p))
    @bp.put("/indicators/<indicator_id>")
    def update_indicator(indicator_id): return mutate(lambda p: api.update_node(indicator_id, body(), principal=p))
    @bp.delete("/indicators/<indicator_id>")
    def delete_indicator(indicator_id): return mutate(lambda p: api.delete_node(indicator_id, body(), principal=p))

    @bp.get("/indicator-sets")
    def sets(): return read(lambda p: api.list_sets(principal=p))
    @bp.post("/indicator-sets/drafts")
    def draft(): return mutate(lambda p: api.create_draft(body(), principal=p))
    @bp.post("/indicator-sets/<set_id>/publish")
    def publish(set_id): return mutate(lambda p: api.publish(set_id, body(), principal=p))

    @bp.get("/experts")
    def experts(): return read(lambda p: api.list_experts(principal=p))
    @bp.post("/experts")
    def create_expert(): return mutate(lambda p: api.create_expert(body(), principal=p))
    @bp.put("/experts/<expert_id>")
    def update_expert(expert_id): return mutate(lambda p: api.update_expert(expert_id, body(), principal=p))
    @bp.delete("/experts/<expert_id>")
    def delete_expert(expert_id): return mutate(lambda p: api.delete_expert(expert_id, body(), principal=p))

    @bp.get("/expert-scores/<expert_id>")
    def scores(expert_id): return read(lambda p: api.get_score_sheet(expert_id, principal=p, indicator_set_id=request.args.get("indicator_set_id") or ""))
    @bp.put("/expert-scores/<expert_id>")
    def put_scores(expert_id): return mutate(lambda p: api.put_score_sheet(expert_id, body(), principal=p))
    @bp.get("/indicator-weights")
    def weights(): return read(lambda p: api.weights(principal=p, indicator_set_id=request.args.get("indicator_set_id")))
    return bp


__all__ = ["create_indicator_blueprint"]
