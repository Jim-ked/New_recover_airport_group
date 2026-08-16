from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, Union

from backend.domain.run import RunEvent, RunRecord
from backend.domain.run_config import RunConfig
from backend.domain.run_snapshot import ODDistance, RunSnapshot
from backend.services.run_snapshot_service import RunSnapshotService
from backend.storage.run_repository import RunRepository


class RunServiceError(RuntimeError):
    pass


class RunAccessError(RunServiceError):
    pass


class RunNotFoundError(RunServiceError):
    pass


class RunRetryNotAllowedError(RunServiceError):
    pass


class RunService:
    """Application boundary for Run creation, lifecycle, ownership and events.

    This class has no Flask dependency and accepts no file paths. A submitted Run is
    created from business IDs + canonical configuration and becomes snapshot-only before
    it enters the queue.
    """

    def __init__(self, *, snapshot_service: RunSnapshotService, run_repository: RunRepository) -> None:
        self.snapshot_service = snapshot_service
        self.runs = run_repository

    @staticmethod
    def _assert_access(record: RunRecord, actor_user_id: str, *, is_admin: bool) -> None:
        if not is_admin and record.owner_user_id != actor_user_id:
            raise RunAccessError("Run is not owned by actor")

    def submit(
        self,
        *,
        run_id: str,
        owner_user_id: str,
        situation_id: str,
        run_config: Union[Mapping[str, Any], RunConfig],
        od_distances: Sequence[ODDistance],
    ) -> RunRecord:
        snapshot = self.snapshot_service.build_snapshot(
            run_id=run_id,
            situation_id=situation_id,
            run_config=run_config,
            od_distances=od_distances,
        )
        return self.submit_snapshot(snapshot=snapshot, owner_user_id=owner_user_id)

    def submit_snapshot(self, *, snapshot: RunSnapshot, owner_user_id: str) -> RunRecord:
        if not isinstance(snapshot, RunSnapshot):
            raise TypeError("snapshot must be RunSnapshot")
        # The repository commits snapshot + queued RunRecord in one transaction. No Run
        # can point at a mutable Situation or a not-yet-persisted input closure.
        return self.runs.create_queued(snapshot=snapshot, owner_user_id=owner_user_id)

    def get(self, run_id: str, *, actor_user_id: str, is_admin: bool = False) -> RunRecord:
        record = self.runs.get(run_id)
        if record is None:
            raise RunNotFoundError(f"run not found: {run_id}")
        self._assert_access(record, actor_user_id, is_admin=is_admin)
        return record

    def list(
        self,
        *,
        actor_user_id: str,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunRecord]:
        return self.runs.list_for_owner(
            actor_user_id, statuses=statuses, limit=limit, offset=offset
        )

    def search_history(
        self,
        *,
        actor_user_id: str,
        statuses: Optional[Sequence[str]] = None,
        situation_id: Optional[str] = None,
        run_id_query: Optional[str] = None,
        task_id: Optional[str] = None,
        selected_airport_id: Optional[str] = None,
        damage_scenario_id: Optional[str] = None,
        no_damage: Optional[bool] = None,
        cluster_enabled: Optional[bool] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[RunRecord], int]:
        # The normal Run workbench is intentionally owner-scoped.  Admin access to an
        # individual foreign Run is a separate capability from browsing every user's
        # history and must not leak into this product surface accidentally.
        return self.runs.search_for_owner(
            actor_user_id,
            statuses=statuses,
            situation_id=situation_id,
            run_id_query=run_id_query,
            task_id=task_id,
            selected_airport_id=selected_airport_id,
            damage_scenario_id=damage_scenario_id,
            no_damage=no_damage,
            cluster_enabled=cluster_enabled,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
        )

    def retry_failed(
        self,
        source_run_id: str,
        *,
        new_run_id: str,
        actor_user_id: str,
        is_admin: bool = False,
    ) -> RunRecord:
        """Create a new queued Run from the source Run's immutable snapshot.

        This is deliberately not implemented by resubmitting ``situation_id + run_config``:
        the mutable Situation may have changed since the failed Run was created.
        """
        source = self.get(source_run_id, actor_user_id=actor_user_id, is_admin=is_admin)
        if source.status != "failed":
            raise RunRetryNotAllowedError("only failed Run can be retried")
        snapshot = self.snapshot_service.snapshots.get(source_run_id)
        if snapshot is None:
            raise RunServiceError(f"source Run snapshot is missing: {source_run_id}")
        cloned = snapshot.clone_for_run(new_run_id)
        # Preserve the original owner. An admin retrying another user's failed Run must not
        # silently transfer ownership of the immutable business input.
        return self.submit_snapshot(snapshot=cloned, owner_user_id=source.owner_user_id)

    def request_cancel(self, run_id: str, *, actor_user_id: str, is_admin: bool = False) -> RunRecord:
        record = self.get(run_id, actor_user_id=actor_user_id, is_admin=is_admin)
        return self.runs.request_cancel(record.run_id)

    def events(
        self,
        run_id: str,
        *,
        actor_user_id: str,
        is_admin: bool = False,
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[RunEvent]:
        self.get(run_id, actor_user_id=actor_user_id, is_admin=is_admin)
        return self.runs.list_events(run_id, after_seq=after_seq, limit=limit)

    # ---- Internal worker-facing lifecycle operations ----

    def claim_for_worker(self, run_id: str) -> RunRecord:
        return self.runs.claim_running(run_id)

    def worker_cancel_requested(self, run_id: str) -> bool:
        record = self.runs.get(run_id)
        if record is None:
            raise RunNotFoundError(f"run not found: {run_id}")
        return bool(record.cancel_requested)

    def mark_cancelled_by_worker(self, run_id: str) -> RunRecord:
        return self.runs.mark_cancelled(run_id)

    def mark_failed_by_worker(self, run_id: str, *, message: str, code: Optional[str] = None) -> RunRecord:
        return self.runs.mark_failed(run_id, message=message, code=code)

    def append_worker_event(
        self,
        run_id: str,
        *,
        level: str,
        stage: str,
        event: str,
        message: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> RunEvent:
        return self.runs.append_event(
            run_id,
            level=level,
            stage=stage,
            event=event,
            message=message,
            payload=payload,
        )


__all__ = [
    "RunService",
    "RunServiceError",
    "RunAccessError",
    "RunNotFoundError",
    "RunRetryNotAllowedError",
]
