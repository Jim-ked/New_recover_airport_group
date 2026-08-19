from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from backend.auth.principal import Principal
from backend.domain.run import RUN_STATUSES
from backend.services.run_result_service import RunResultService
from backend.services.run_service import RunService
from backend.services.run_runtime_service import RunRuntimeService
from backend.services.run_submission_service import RunSubmissionService
from backend.services.run_worker_status import RunWorkerStatus
from backend.web.error_mapping import map_expected_error
from backend.web.http import (
    ApiInputError,
    ApiResponse,
    parse_nonnegative_int,
    parse_positive_int,
    reject_unknown,
    require_object,
    required_nonblank_string,
)


RunIdFactory = Callable[[], str]


def _optional_nonblank(raw: Any, *, field: str) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ApiInputError(f"{field} must be a nonblank string", field=field)
    return raw.strip()


def _optional_bool(raw: Any, *, field: str) -> Optional[bool]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"true", "1"}:
            return True
        if value in {"false", "0"}:
            return False
    raise ApiInputError(f"{field} must be true or false", field=field)


def _optional_utc_timestamp(raw: Any, *, field: str) -> Optional[str]:
    value = _optional_nonblank(raw, field=field)
    if value is None:
        return None
    if "T" not in value and " " not in value:
        raise ApiInputError(
            f"{field} must include date and time in ISO-8601 form", field=field
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiInputError(f"{field} must be an ISO-8601 timestamp", field=field) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def default_run_id_factory() -> str:
    return f"RUN-{uuid.uuid4().hex}"


class RunApi:
    """Framework-neutral HTTP contract adapter for `/api/runs*`.

    It parses external request shapes, delegates to application services, and converts
    expected business failures into the frozen error envelope. It never reads SQLite,
    invokes the algorithm, derives Metrics, or touches result files directly.
    """

    def __init__(
        self,
        *,
        submission_service: RunSubmissionService,
        run_service: RunService,
        result_service: RunResultService,
        runtime_service: RunRuntimeService,
        worker_status: RunWorkerStatus | None = None,
        run_id_factory: RunIdFactory = default_run_id_factory,
    ) -> None:
        self.submissions = submission_service
        self.runs = run_service
        self.results = result_service
        self.runtime = runtime_service
        self.run_id_factory = run_id_factory
        self.worker_status_store = worker_status

    @staticmethod
    def _handle(call) -> ApiResponse:
        try:
            return call()
        except Exception as exc:
            mapped = map_expected_error(exc)
            if mapped is not None:
                return mapped
            raise

    @staticmethod
    def _submission_body(
        raw: Any, *, allow_expected_input_hash: bool = False
    ) -> tuple[str, Mapping[str, Any], Optional[str]]:
        body = require_object(raw)
        allowed = {"situation_id", "run_config"}
        if allow_expected_input_hash:
            allowed.add("expected_input_hash")
        reject_unknown(body, allowed)
        situation_id = required_nonblank_string(body, "situation_id")
        run_config = body.get("run_config")
        if not isinstance(run_config, Mapping):
            raise ApiInputError("run_config must be a JSON object", field="run_config")
        expected = None
        if allow_expected_input_hash and body.get("expected_input_hash") is not None:
            expected = required_nonblank_string(body, "expected_input_hash")
            if len(expected) != 64:
                raise ApiInputError("expected_input_hash must contain 64 characters", field="expected_input_hash")
        return situation_id, run_config, expected

    def validate(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.execute")
            situation_id, run_config, _ = self._submission_body(raw_body)
            result = self.submissions.validate(
                owner_user_id=principal.user_id,
                is_admin=principal.is_admin,
                situation_id=situation_id,
                run_config=run_config,
            )
            return ApiResponse(result.to_dict(), 200)

        return self._handle(action)

    def submit(self, raw_body: Any, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.execute")
            situation_id, run_config, expected_input_hash = self._submission_body(
                raw_body, allow_expected_input_hash=True
            )
            run_id = self.run_id_factory()
            record = self.submissions.submit(
                run_id=run_id,
                owner_user_id=principal.user_id,
                is_admin=principal.is_admin,
                situation_id=situation_id,
                run_config=run_config,
                expected_input_hash=expected_input_hash,
            )
            return ApiResponse(record.to_dict(), 201)

        return self._handle(action)

    def list(
        self,
        *,
        principal: Principal,
        statuses: Optional[Sequence[str]] = None,
        limit: Any = None,
        offset: Any = None,
        situation_id: Any = None,
        run_id_query: Any = None,
        task_id: Any = None,
        selected_airport_id: Any = None,
        damage_scenario_id: Any = None,
        no_damage: Any = None,
        cluster_enabled: Any = None,
        created_after: Any = None,
        created_before: Any = None,
    ) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.read")
            parsed_statuses: Optional[list[str]] = None
            if statuses is not None:
                if isinstance(statuses, (str, bytes)):
                    candidate = [str(statuses)]
                else:
                    candidate = [str(x) for x in statuses]
                if not candidate:
                    candidate = []
                unknown = sorted(set(candidate) - set(RUN_STATUSES))
                if unknown:
                    raise ApiInputError(
                        f"status must be one of {list(RUN_STATUSES)}",
                        field="status",
                    )
                parsed_statuses = candidate
            parsed_limit = parse_positive_int(limit, field="limit", default=100, maximum=500)
            parsed_offset = parse_nonnegative_int(offset, field="offset", default=0, maximum=2_147_483_647)
            parsed_situation = _optional_nonblank(situation_id, field="situation_id")
            parsed_query = _optional_nonblank(run_id_query, field="q")
            parsed_task = _optional_nonblank(task_id, field="task_id")
            parsed_airport = _optional_nonblank(selected_airport_id, field="selected_airport_id")
            parsed_damage = _optional_nonblank(damage_scenario_id, field="damage_scenario_id")
            parsed_no_damage = _optional_bool(no_damage, field="no_damage")
            parsed_cluster = _optional_bool(cluster_enabled, field="cluster_enabled")
            parsed_after = _optional_utc_timestamp(created_after, field="created_after")
            parsed_before = _optional_utc_timestamp(created_before, field="created_before")
            if parsed_after and parsed_before and parsed_after > parsed_before:
                raise ApiInputError("created_after cannot be later than created_before", field="created_after")
            if parsed_no_damage is True and parsed_damage is not None:
                raise ApiInputError(
                    "no_damage and damage_scenario_id cannot both be set", field="no_damage"
                )
            records, total = self.runs.search_history(
                actor_user_id=principal.user_id,
                statuses=parsed_statuses,
                situation_id=parsed_situation,
                run_id_query=parsed_query,
                task_id=parsed_task,
                selected_airport_id=parsed_airport,
                damage_scenario_id=parsed_damage,
                no_damage=parsed_no_damage,
                cluster_enabled=parsed_cluster,
                created_after=parsed_after,
                created_before=parsed_before,
                limit=parsed_limit,
                offset=parsed_offset,
            )
            items = [
                self.results.get_run_detail(
                    record.run_id,
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                )
                for record in records
            ]
            return ApiResponse(
                {
                    "items": items,
                    "total": total,
                    "limit": parsed_limit,
                    "offset": parsed_offset,
                },
                200,
            )

        return self._handle(action)

    def worker_status(self, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.read")
            if self.worker_status_store is None:
                return ApiResponse({"connected": False, "reason": "status_unconfigured"}, 200)
            return ApiResponse(self.worker_status_store.read(), 200)
        return self._handle(action)

    def detail(self, run_id: str, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.read")
            return ApiResponse(
                self.results.get_run_detail(
                    run_id,
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)

    def events(
        self,
        run_id: str,
        *,
        principal: Principal,
        after_seq: Any = None,
        limit: Any = None,
    ) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.read")
            parsed_after = parse_nonnegative_int(
                after_seq, field="after_seq", default=0, maximum=2_147_483_647
            )
            parsed_limit = parse_positive_int(limit, field="limit", default=200, maximum=1000)
            events = self.runs.events(
                run_id,
                actor_user_id=principal.user_id,
                is_admin=principal.is_admin,
                after_seq=parsed_after,
                limit=parsed_limit,
            )
            next_after_seq = events[-1].seq if events else parsed_after
            return ApiResponse(
                {
                    "run_id": run_id,
                    "events": [event.to_dict() for event in events],
                    "after_seq": parsed_after,
                    "next_after_seq": next_after_seq,
                },
                200,
            )

        return self._handle(action)

    def retry(self, run_id: str, *, principal: Principal, raw_body: Any = None) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.execute")
            if raw_body not in (None, {}):
                body = require_object(raw_body)
                reject_unknown(body, set())
            record = self.runs.retry_failed(
                run_id,
                new_run_id=self.run_id_factory(),
                actor_user_id=principal.user_id,
                is_admin=principal.is_admin,
            )
            return ApiResponse(record.to_dict(), 201)
        return self._handle(action)

    def cancel(self, run_id: str, *, principal: Principal, raw_body: Any = None) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.execute")
            if raw_body not in (None, {}):
                body = require_object(raw_body)
                reject_unknown(body, set())
            record = self.runs.request_cancel(
                run_id,
                actor_user_id=principal.user_id,
                is_admin=principal.is_admin,
            )
            return ApiResponse(record.to_dict(), 200)

        return self._handle(action)


    def situation(self, run_id: str, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.read")
            return ApiResponse(
                self.results.get_snapshot_situation(
                    run_id,
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)

    def runtime_projection(self, run_id: str, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.read")
            return ApiResponse(
                self.runtime.get_runtime(
                    run_id,
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)


    def solution(self, run_id: str, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.read")
            return ApiResponse(
                self.results.get_solution(
                    run_id,
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)

    def metrics(self, run_id: str, *, principal: Principal) -> ApiResponse:
        def action() -> ApiResponse:
            principal.require_permission("runs.read")
            return ApiResponse(
                self.results.get_metrics(
                    run_id,
                    actor_user_id=principal.user_id,
                    is_admin=principal.is_admin,
                ),
                200,
            )
        return self._handle(action)


__all__ = ["RunApi", "RunIdFactory", "default_run_id_factory"]
