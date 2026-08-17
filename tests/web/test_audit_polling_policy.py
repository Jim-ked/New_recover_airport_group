from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.web.flask_audit import should_skip_polling_audit


class AuditPollingPolicyTests(unittest.TestCase):
    def test_successful_run_polling_reads_are_skipped(self):
        self.assertTrue(should_skip_polling_audit(method="GET", path="/api/runs", status=200))
        self.assertTrue(should_skip_polling_audit(method="GET", path="/api/runs/R1", status=200))
        self.assertTrue(
            should_skip_polling_audit(method="GET", path="/api/runs/R1/events", status=200)
        )

    def test_mutations_errors_and_result_reads_remain_auditable(self):
        self.assertFalse(should_skip_polling_audit(method="POST", path="/api/runs", status=201))
        self.assertFalse(
            should_skip_polling_audit(method="GET", path="/api/runs/R1/events", status=403)
        )
        self.assertFalse(
            should_skip_polling_audit(method="GET", path="/api/runs/R1/metrics", status=200)
        )
        self.assertFalse(
            should_skip_polling_audit(method="GET", path="/api/results", status=200)
        )


if __name__ == "__main__":
    unittest.main()
