from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from backend.domain.run import RunRecord
from backend.services.run_worker import RunWorker, RunWorkerError
from backend.storage.run_queue_repository import RunQueueRepository


SleepFn = Callable[[float], None]


class RunWorkerLoop:
    """Small single-process queue loop around the existing synchronous RunWorker.

    Queue selection and execution stay separate: ``next_queued`` is only discovery;
    ``RunWorker.execute`` still performs the atomic queued->running claim.  This keeps a
    second accidentally started worker from executing the same Run twice.
    """

    def __init__(
        self,
        *,
        queue_repository: RunQueueRepository,
        worker: RunWorker,
        poll_interval_s: float = 1.0,
        sleep_fn: SleepFn = time.sleep,
    ) -> None:
        try:
            interval = float(poll_interval_s)
        except (TypeError, ValueError) as exc:
            raise ValueError("poll_interval_s must be numeric") from exc
        if not (0.2 <= interval <= 60.0):
            raise ValueError("poll_interval_s must be in [0.2, 60.0]")
        self.queue = queue_repository
        self.worker = worker
        self.poll_interval_s = interval
        self.sleep = sleep_fn
        self.logger = logging.getLogger("airport_group.worker")

    def execute_next(self) -> Optional[RunRecord]:
        record = self.queue.next_queued()
        if record is None:
            return None
        try:
            return self.worker.execute(record.run_id)
        except RunWorkerError as exc:
            # The only expected outer error is an atomic-claim race (for example if a
            # second worker process was started). The winning worker owns the Run; this
            # process simply continues polling instead of crashing the queue service.
            self.logger.warning("queued Run could not be claimed/executed run_id=%s error=%s", record.run_id, exc)
            return None

    def run_forever(self) -> None:
        while True:
            record = self.execute_next()
            if record is None:
                self.sleep(self.poll_interval_s)


__all__ = ["RunWorkerLoop"]
