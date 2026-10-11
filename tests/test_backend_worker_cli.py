from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import backend.__main__ as cli


class BackendWorkerCliTests(unittest.TestCase):
    def test_capacity_backfill_command_routes_without_initializing_runtime(self):
        from types import SimpleNamespace
        with mock.patch.object(cli, "_settings", return_value=SimpleNamespace(db_path="existing.sqlite3")), \
                mock.patch.object(cli, "backfill_capacity", return_value={"failed": 0, "conflicts": 0, "not_succeeded": []}) as call:
            status = cli.main(["assessment", "backfill-capacity", "--run-id", "R1", "--run-id", "R2", "--dry-run"])
        self.assertEqual(0, status)
        call.assert_called_once_with("existing.sqlite3", run_ids=["R1", "R2"], all_succeeded=False, dry_run=True)

    def test_capacity_backfill_requires_an_explicit_selection(self):
        with self.assertRaises(SystemExit):
            cli.main(["assessment", "backfill-capacity"])

    def test_worker_command_routes_to_runtime_worker(self):
        fake_settings = object()
        with mock.patch.object(cli, "_settings", return_value=fake_settings), mock.patch.object(cli, "run_worker") as call:
            status = cli.main(["worker", "--once", "--poll-interval", "0.5"])
        self.assertEqual(0, status)
        call.assert_called_once_with(fake_settings, once=True, poll_interval_s=0.5)


if __name__ == "__main__":
    unittest.main()
