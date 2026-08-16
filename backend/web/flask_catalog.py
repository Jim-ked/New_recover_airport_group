from __future__ import annotations

from backend.auth.principal import Principal
from backend.web.catalog_api import CatalogApi
from backend.web.error_mapping import map_expected_error
from backend.web.flask_runs import MutationGuard, PrincipalResolver
from backend.web.http import error_body


def create_catalog_blueprint(
    *,
    api: CatalogApi,
    principal_resolver: PrincipalResolver,
    mutation_guard: MutationGuard = None,
):
    try:
        from flask import Blueprint, current_app, jsonify, request
        from werkzeug.exceptions import BadRequest
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Flask runtime dependency is required to bind Catalog API") from exc

    bp = Blueprint("catalog_v1", __name__, url_prefix="/api")

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
            current_app.logger.exception("Unhandled Catalog API error")
            return jsonify(error_body("INTERNAL_ERROR", "Unexpected server error")), 500

    def read(fn):
        def action(*args, **kwargs):
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            return render(fn(*args, principal=principal, **kwargs))
        return action

    def mutation(fn):
        def action(*args, **kwargs):
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            if mutation_guard is not None:
                mutation_guard(request, principal)
            return render(fn(*args, request.get_json(force=False, silent=False), principal=principal, **kwargs))
        return action


    @bp.post("/base-data/import")
    def replace_base_data():
        def action():
            principal, denied = principal_or_401()
            if denied is not None:
                return denied
            if mutation_guard is not None:
                mutation_guard(request, principal)
            if request.mimetype == "text/csv":
                response = api.replace_base_data_csv(
                    request.args.get("dataset"), request.get_data(as_text=True), principal=principal
                )
            else:
                response = api.replace_base_data_json(
                    request.get_json(force=False, silent=False), principal=principal
                )
            # Keep one append-only behavior log entry for the request.  The global audit
            # hook reads this safe summary; it does not store the uploaded business rows.
            try:
                from flask import g
                if response.status < 400:
                    g.audit_details = {
                        "base_data_replace": {
                            key: response.body.get(key)
                            for key in ("dataset","source_format","mode","version_history","added","updated","deleted","total")
                        }
                    }
            except Exception:
                pass
            return render(response)
        return invoke(action)

    @bp.get("/airports")
    def list_airports():
        return invoke(lambda: read(api.list_airports)(
            query=request.args.get("q"),
            roles=request.args.getlist("role") or None,
            regions=request.args.getlist("region") or None,
            limit=request.args.get("limit"),
            offset=request.args.get("offset"),
        ))

    @bp.post("/airports")
    def create_airport():
        return invoke(lambda: mutation(api.create_airport)())

    @bp.get("/airports/<airport_id>")
    def airport_detail(airport_id: str):
        return invoke(lambda: read(api.airport_detail)(airport_id))

    @bp.put("/airports/<airport_id>")
    def update_airport(airport_id: str):
        return invoke(lambda: mutation(api.update_airport)(airport_id))

    @bp.delete("/airports/<airport_id>")
    def delete_airport(airport_id: str):
        return invoke(lambda: mutation(api.delete_airport)(airport_id))

    @bp.get("/missions")
    def list_missions():
        return invoke(lambda: read(api.list_missions)(
            query=request.args.get("q"), limit=request.args.get("limit"), offset=request.args.get("offset")
        ))

    @bp.post("/missions")
    def create_mission():
        return invoke(lambda: mutation(api.create_mission)())

    @bp.get("/missions/history")
    def mission_history():
        return invoke(lambda: read(api.mission_history)(limit=request.args.get("limit")))

    @bp.get("/missions/<mission_id>")
    def mission_detail(mission_id: str):
        return invoke(lambda: read(api.mission_detail)(mission_id))

    @bp.put("/missions/<mission_id>")
    def update_mission(mission_id: str):
        return invoke(lambda: mutation(api.update_mission)(mission_id))

    @bp.delete("/missions/<mission_id>")
    def delete_mission(mission_id: str):
        return invoke(lambda: mutation(api.delete_mission)(mission_id))

    @bp.get("/aircraft-types")
    def list_aircraft_types():
        return invoke(lambda: read(api.list_aircraft_types)())

    @bp.post("/aircraft-types")
    def create_aircraft_type():
        return invoke(lambda: mutation(api.create_aircraft_type)())

    @bp.put("/aircraft-types/<aircraft_type_id>")
    def update_aircraft_type(aircraft_type_id: str):
        return invoke(lambda: mutation(api.update_aircraft_type)(aircraft_type_id))

    @bp.delete("/aircraft-types/<aircraft_type_id>")
    def delete_aircraft_type(aircraft_type_id: str):
        return invoke(lambda: mutation(api.delete_aircraft_type)(aircraft_type_id))

    @bp.put("/aircraft-types/<aircraft_type_id>/resource-requirements")
    def replace_aircraft_requirements(aircraft_type_id: str):
        return invoke(lambda: mutation(api.replace_aircraft_resource_requirements)(aircraft_type_id))

    @bp.get("/resource-types")
    def list_resource_types():
        return invoke(lambda: read(api.list_resource_types)())

    @bp.post("/resource-types")
    def create_resource_type():
        return invoke(lambda: mutation(api.create_resource_type)())

    @bp.put("/resource-types/<resource_type_id>")
    def update_resource_type(resource_type_id: str):
        return invoke(lambda: mutation(api.update_resource_type)(resource_type_id))

    @bp.delete("/resource-types/<resource_type_id>")
    def delete_resource_type(resource_type_id: str):
        return invoke(lambda: mutation(api.delete_resource_type)(resource_type_id))

    @bp.get("/aircraft-resource-requirements")
    def list_requirements():
        return invoke(lambda: read(api.list_aircraft_resource_requirements)())

    return bp


__all__ = ["create_catalog_blueprint"]
