from __future__ import annotations

import unittest

from backend.domain.run import RunEvent, RunRecord, RunValidationError


HASH = "a" * 64


class RunDomainTests(unittest.TestCase):
    def test_external_statuses_are_exactly_five(self):
        base = dict(
            run_id="R1", owner_user_id="U1", situation_id="S1", snapshot_hash=HASH,
            cancel_requested=False, created_at="2026-08-16T00:00:00Z",
        )
        for status in ("queued", "running", "cancelled"):
            RunRecord(status=status, **base)
        RunRecord(status="failed", failure_message="boom", **base)
        RunRecord(status="succeeded", solution_hash=HASH, metrics_hash=HASH, **base)
        with self.assertRaisesRegex(RunValidationError, "status"):
            RunRecord(status="postprocessing", **base)

    def test_non_success_run_cannot_claim_canonical_result_hashes(self):
        with self.assertRaisesRegex(RunValidationError, "canonical Solution/Metrics"):
            RunRecord(
                run_id="R1", owner_user_id="U1", situation_id="S1", snapshot_hash=HASH,
                status="running", cancel_requested=False, created_at="x",
                solution_hash=HASH, metrics_hash=HASH,
            )

    def test_run_event_requires_frozen_stage_and_structured_payload(self):
        event = RunEvent(
            run_id="R1", seq=1, level="INFO", stage="exact_optimization",
            event="stage_start", message="开始精确优化", payload={"worker": "W1"},
            created_at="2026-08-16T00:00:00Z",
        )
        self.assertEqual("exact_optimization", event.to_dict()["stage"])
        with self.assertRaisesRegex(RunValidationError, "stage"):
            RunEvent(
                run_id="R1", seq=1, level="INFO", stage="solve",
                event="stage_start", message="x", payload={}, created_at="x",
            )


if __name__ == "__main__":
    unittest.main()
