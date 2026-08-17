from __future__ import annotations


def create_ui_blueprint():
    try:
        from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
    except ImportError as exc:  # pragma: no cover - runtime packaging concern
        raise RuntimeError("Flask runtime dependency is required to bind UI") from exc

    bp = Blueprint("ui_v1", __name__)

    def _safe_next(value: str | None) -> str:
        if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
            return url_for("ui_v1.situations_page")
        return value

    def _require_login():
        if session.get("user"):
            return None
        target = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
        return redirect(url_for("ui_v1.login_page", next=_safe_next(target)))

    @bp.get("/")
    def root():
        if not session.get("user"):
            return redirect(url_for("ui_v1.login_page"))
        return redirect(url_for("ui_v1.situations_page"))

    @bp.get("/login")
    def login_page():
        if session.get("user"):
            return redirect(_safe_next(request.args.get("next")))
        return render_template("pages/login.html", next_path=_safe_next(request.args.get("next")))

    @bp.get("/situations")
    def situations_page():
        denied = _require_login()
        if denied is not None:
            return denied
        return render_template(
            "pages/situations.html",
            session_user=session.get("user"),
            active_nav="situation",
            tile_template=str(current_app.config.get("GIS_TILE_TEMPLATE") or ""),
        )

    @bp.get("/base-data")
    def base_data_page():
        denied = _require_login()
        if denied is not None:
            return denied
        return render_template(
            "pages/base_data.html",
            session_user=session.get("user"),
            active_nav="situation",
            base_data_active=True,
        )

    @bp.get("/indicators")
    def indicators_page():
        denied = _require_login()
        if denied is not None:
            return denied
        return render_template(
            "pages/indicators.html",
            session_user=session.get("user"),
            active_nav="indicators",
        )

    @bp.get("/run")
    def run_page():
        denied = _require_login()
        if denied is not None:
            return denied
        return render_template("pages/run.html", session_user=session.get("user"), active_nav="run")

    @bp.get("/runs/<run_id>")
    def single_run_page(run_id: str):
        denied = _require_login()
        if denied is not None:
            return denied
        return render_template(
            "pages/single_run.html",
            session_user=session.get("user"),
            run_id=run_id,
            active_nav="run",
        )

    @bp.get("/runs/<run_id>/runtime")
    def runtime_page(run_id: str):
        denied = _require_login()
        if denied is not None:
            return denied
        return render_template(
            "pages/gis_runtime.html",
            session_user=session.get("user"),
            run_id=run_id,
            active_nav="run",
            tile_template=str(current_app.config.get("GIS_TILE_TEMPLATE") or ""),
        )

    @bp.get("/results")
    def results_page():
        denied = _require_login()
        if denied is not None:
            return denied
        return render_template(
            "pages/results.html",
            session_user=session.get("user"),
            active_nav="results",
        )

    @bp.get("/settings")
    def settings_page():
        denied = _require_login()
        if denied is not None:
            return denied
        return render_template(
            "pages/settings.html",
            session_user=session.get("user"),
            active_nav="settings",
        )

    return bp


__all__ = ["create_ui_blueprint"]
