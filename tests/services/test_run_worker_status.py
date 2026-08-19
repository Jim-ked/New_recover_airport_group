from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.services.run_worker_status import RunWorkerStatus


class RunWorkerStatusTests(unittest.TestCase):
    def test_heartbeat_reports_connected_for_the_same_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "runtime" / "db" / "app.sqlite3"
            status = RunWorkerStatus(db)
            status.heartbeat(current_run_id="RUN-1")

            payload = status.read()

        self.assertTrue(payload["connected"])
        self.assertEqual("RUN-1", payload["current_run_id"])

    def test_missing_heartbeat_is_explicitly_disconnected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            status = RunWorkerStatus(Path(directory) / "runtime" / "db" / "app.sqlite3")
            self.assertEqual(
                {"connected": False, "reason": "heartbeat_missing"},
                status.read(),
            )


if __name__ == "__main__":
    unittest.main()
