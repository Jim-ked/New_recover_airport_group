from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from backend.web.flask_runs import MutationGuard, PrincipalResolver, create_run_blueprint
from backend.web.flask_auth import (
    create_auth_blueprint,
    install_session_auth,
    session_csrf_mutation_guard,
    session_principal_resolver,
)
from backend.web.flask_results import create_results_blueprint
from backend.web.flask_situations import create_situation_blueprint
from backend.web.flask_catalog import create_catalog_blueprint
from backend.web.flask_indicators import create_indicator_blueprint
from backend.web.flask_ui import create_ui_blueprint
from backend.web.flask_account import create_account_blueprint
from backend.web.flask_audit import create_audit_blueprint, install_audit_hook
from backend.web.flask_tiles import create_tile_blueprint
from backend.web.flask_user_admin import create_user_admin_blueprint
from backend.web.run_api import RunApi
from backend.web.results_api import ResultsApi
from backend.web.situation_api import SituationApi
from backend.web.catalog_api import CatalogApi
from backend.web.indicator_api import IndicatorApi
from backend.web.account_api import AccountApi
from backend.web.audit_api import AuditApi
from backend.web.user_admin_api import UserAdminApi
from backend.storage.user_repository import UserRepository
from backend.auth.session_policy import DEFAULT_IDLE_TIMEOUT_SECONDS, DEFAULT_ABSOLUTE_TIMEOUT_SECONDS


def create_app(
    *,
    run_api: RunApi,
    principal_resolver: Optional[PrincipalResolver] = None,
    mutation_guard: Optional[MutationGuard] = None,
    secret_key: Optional[str] = None,
    results_api: Optional[ResultsApi] = None,
    situation_api: Optional[SituationApi] = None,
    catalog_api: Optional[CatalogApi] = None,
    indicator_api: Optional[IndicatorApi] = None,
    account_api: Optional[AccountApi] = None,
    audit_api: Optional[AuditApi] = None,
    user_admin_api: Optional[UserAdminApi] = None,
    user_repository: Optional[UserRepository] = None,
    session_idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS,
    session_absolute_timeout_seconds: int = DEFAULT_ABSOLUTE_TIMEOUT_SECONDS,
    enable_ui: bool = True,
    app_config: Optional[Mapping[str, Any]] = None,
    tile_root: Optional[str | Path] = None,
):
    """Create the complete Flask shell from already-constructed application APIs."""

    try:
        from flask import Flask
    except ImportError as exc:  # pragma: no cover - runtime packaging concern
        raise RuntimeError("Flask runtime dependency is required") from exc

    frontend_root = Path(__file__).resolve().parents[2] / "frontend"
    app = Flask(
        __name__,
        template_folder=str(frontend_root / "templates"),
        static_folder=str(frontend_root / "static"),
        static_url_path="/static",
    )
    app.json.ensure_ascii = False
    if app_config:
        app.config.from_mapping(dict(app_config))
    if principal_resolver is None:
        if not isinstance(secret_key, str) or not secret_key:
            raise RuntimeError("secret_key is required for default session authentication")
        app.secret_key = secret_key
        if user_repository is not None:
            install_session_auth(
                app,
                user_repository=user_repository,
                idle_timeout_seconds=session_idle_timeout_seconds,
                absolute_timeout_seconds=session_absolute_timeout_seconds,
            )
            app.register_blueprint(create_auth_blueprint(
                user_repository=user_repository,
                idle_timeout_seconds=session_idle_timeout_seconds,
                absolute_timeout_seconds=session_absolute_timeout_seconds,
            ))
        principal_resolver = session_principal_resolver
        if mutation_guard is None:
            mutation_guard = session_csrf_mutation_guard
    elif secret_key is not None:
        app.secret_key = secret_key

    app.register_blueprint(
        create_run_blueprint(
            api=run_api,
            principal_resolver=principal_resolver,
            mutation_guard=mutation_guard,
        )
    )
    if results_api is not None:
        app.register_blueprint(
            create_results_blueprint(api=results_api, principal_resolver=principal_resolver)
        )
    if situation_api is not None:
        app.register_blueprint(
            create_situation_blueprint(
                api=situation_api,
                principal_resolver=principal_resolver,
                mutation_guard=mutation_guard,
            )
        )
    if catalog_api is not None:
        app.register_blueprint(
            create_catalog_blueprint(
                api=catalog_api,
                principal_resolver=principal_resolver,
                mutation_guard=mutation_guard,
            )
        )
    if indicator_api is not None:
        app.register_blueprint(
            create_indicator_blueprint(
                api=indicator_api,
                principal_resolver=principal_resolver,
                mutation_guard=mutation_guard,
            )
        )
    if account_api is None:
        account_api = AccountApi()
    app.register_blueprint(
        create_account_blueprint(api=account_api, principal_resolver=principal_resolver)
    )
    if user_admin_api is None and user_repository is not None:
        user_admin_api = UserAdminApi(user_repository)
    if user_admin_api is not None:
        app.register_blueprint(
            create_user_admin_blueprint(
                api=user_admin_api,
                principal_resolver=principal_resolver,
                mutation_guard=mutation_guard,
            )
        )
    if audit_api is not None:
        app.register_blueprint(
            create_audit_blueprint(api=audit_api, principal_resolver=principal_resolver)
        )
        install_audit_hook(
            app, repository=audit_api.repository, principal_resolver=principal_resolver
        )
    if tile_root is not None:
        app.register_blueprint(create_tile_blueprint(tile_root=tile_root))
    if enable_ui:
        app.register_blueprint(create_ui_blueprint())
    return app


__all__ = ["create_app"]
