from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.services.airport_seed_service import AirportSeedError, bootstrap_airport_master
from backend.storage.airport_repository import AirportRepository


ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "resources" / "seed" / "airports" / "cleaned" / "airports_master_v1.json"


class AirportSeedServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "app.db"
        self.repo = AirportRepository(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_explicit_bootstrap_loads_the_only_566_airport_master(self) -> None:
        count = bootstrap_airport_master(self.repo, MASTER)
        self.assertEqual(566, count)
        self.assertEqual(566, self.repo.count_airports())
        raw = json.loads(MASTER.read_text(encoding="utf-8"))
        self.assertEqual("WGS84", raw["coordinate_reference_system"])
        self.assertTrue(all("support_level" not in a and "default_config" not in a for a in raw["airports"]))

    def test_bootstrap_refuses_to_overwrite_existing_authority(self) -> None:
        bootstrap_airport_master(self.repo, MASTER)
        with self.assertRaises(AirportSeedError) as ctx:
            bootstrap_airport_master(self.repo, MASTER)
        self.assertEqual("airports", ctx.exception.field)
        self.assertEqual(566, self.repo.count_airports())

    def test_metadata_mismatch_fails_before_writing(self) -> None:
        broken = json.loads(MASTER.read_text(encoding="utf-8"))
        broken["count"] = 565
        path = Path(self.tmp.name) / "broken.json"
        path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(AirportSeedError) as ctx:
            bootstrap_airport_master(self.repo, path)
        self.assertEqual("count", ctx.exception.field)
        self.repo.init_schema()
        self.assertEqual(0, self.repo.count_airports())


if __name__ == "__main__":
    unittest.main()
