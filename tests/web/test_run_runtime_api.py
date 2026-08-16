from __future__ import annotations

import unittest

from backend.auth.principal import Principal
from backend.web.run_api import RunApi


class _Runtime:
    def get_runtime(self, run_id, *, actor_user_id, is_admin=False):
        return {"schema_version": "runtime.v1", "run_id": run_id, "actor": actor_user_id, "is_admin": is_admin}


class RunRuntimeApiTests(unittest.TestCase):
    def setUp(self):
        self.api = RunApi(
            submission_service=None, run_service=None, result_service=None,
            runtime_service=_Runtime(), run_id_factory=lambda: "R",
        )

    def test_runtime_projection_requires_runs_read_and_preserves_actor(self):
        viewer = Principal("U1", role="viewer")
        response = self.api.runtime_projection("R1", principal=viewer)
        self.assertEqual(200, response.status)
        self.assertEqual("runtime.v1", response.body["schema_version"])
        self.assertEqual("U1", response.body["actor"])

        denied = self.api.runtime_projection(
            "R1", principal=Principal("U1", permissions=frozenset({"runs.execute"}))
        )
        self.assertEqual(403, denied.status)
        self.assertEqual("PERMISSION_DENIED", denied.body["error"]["code"])


if __name__ == '__main__':
    unittest.main()
