from __future__ import annotations

from typing import Optional

from backend.domain.run import RunRecord
from backend.storage.run_repository import RunRepository


class RunQueueRepository(RunRepository):
    """RunRepository extension used only by the background queue consumer.

    Normal user-facing history remains owner-scoped.  The worker needs a system-level
    view of queued Runs because it executes jobs for every authenticated owner.
    """

    def next_queued(self) -> Optional[RunRecord]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT r.*,
                       rr.solution_hash AS solution_hash,
                       rr.metrics_hash AS metrics_hash
                FROM runs r
                LEFT JOIN run_results rr ON rr.run_id = r.run_id
                WHERE r.status = 'queued' AND r.cancel_requested = 0
                ORDER BY r.created_at ASC, r.run_id ASC
                LIMIT 1
                """
            ).fetchone()
        return None if row is None else self._record_from_row(row)


__all__ = ["RunQueueRepository"]
