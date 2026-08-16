from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportAircraftSupport, AirportOperationalProfile, AirportResourceStock
from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.domain.mission import Mission, MissionAircraftRequirement
from backend.domain.situation import Situation, SituationAirport
from backend.services.od_distance_service import ODDistanceService
from backend.services.run_service import RunService
from backend.services.run_snapshot_service import RunSnapshotService
from backend.services.run_submission_service import (
    RunSubmissionService,
    RunSubmissionSituationNotFoundError,
    SolverProbeResult,
)
from backend.storage.airport_repository import AirportRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.storage.situation_repository import SituationRepository


class RunSubmissionServiceTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "app.sqlite"
        self.airports = AirportRepository(self.db)
        self.situations = SituationRepository(self.db)
        self.snapshots = RunSnapshotRepository(self.db)
        self.runs = RunRepository(self.db)
        self.airports.init_schema()

        self.airports.save_aircraft_type(AircraftType.from_mapping({
            "aircraft_type_id": "fighter",
            "name": "Fighter",
            "speed_kmh": 800,
            "max_range_km": 1200,
            "reserve_ratio": 0.2,
            "departure_capacity_occupancy_factor": 1.0,
            "arrival_capacity_occupancy_factor": 1.0,
        }))
        self.airports.save_resource_type(ResourceType.from_mapping({
            "resource_type_id": "FUEL-1", "name": "Fuel", "category": "fuel", "unit": "t",
        }))
        self.airports.save_aircraft_resource_requirement(AircraftResourceRequirement.from_mapping({
            "aircraft_type_id": "fighter",
            "resource_type_id": "FUEL-1",
            "basis": "per_hour",
            "quantity": 1.5,
        }))

        airport = AirportBase.from_mapping({
            "airport_id": "A1", "airport_name": "A1", "facility_type": "medium_airport",
            "role": "joint", "longitude": 118.8, "latitude": 31.7,
            "scheduled_service": True, "runway_count": 0, "max_runway_length_m": None, "runways": [],
        })
        profile = AirportOperationalProfile(
            airport_id="A1", configuration_complete=True, capacity_per_window=8,
            support_level="L1",
            aircraft_support=(AirportAircraftSupport("fighter", 3, 2),),
            resource_stocks=(AirportResourceStock("FUEL-1", 50, 0),),
        )
        situation = Situation(
            situation_id="S1", name="S1",
            airports=(SituationAirport(airport=airport, operational_profile=profile),),
            missions=(Mission(
                "M1", "M1", 120.0, 32.0, 4, 10,
                (MissionAircraftRequirement("fighter", 2, 1),),
            ),),
        )
        self.situations.save_situation(situation, owner_user_id="U1")

        snapshot_service = RunSnapshotService(
            airport_repository=self.airports,
            situation_repository=self.situations,
            snapshot_repository=self.snapshots,
        )
        run_service = RunService(snapshot_service=snapshot_service, run_repository=self.runs)
        self.service = RunSubmissionService(
            situation_repository=self.situations,
            snapshot_repository=self.snapshots,
            snapshot_service=snapshot_service,
            run_service=run_service,
            od_distance_service=ODDistanceService(),
            solver_probe=lambda: SolverProbeResult(True, "solver available in test"),
        )
        self.config = {
            "damage_scenario_id": None,
            "preference_mode": "sortie_max",
            "cluster_enabled": False,
            "cluster_size": None,
            "core_airports": [],
            "aircraft_type_weight": {"fighter": 1.0},
            "mip_time_limit_s": 120,
        }

    def tearDown(self):
        self._td.cleanup()

    def test_validate_is_side_effect_free_and_derives_od_internally(self):
        result = self.service.validate(owner_user_id="U1", situation_id="S1", run_config=self.config)
        self.assertEqual("S1", result.situation_id)
        self.assertEqual(1, result.od_pair_count)
        self.assertEqual(42, result.run_config["algorithm_seed"])
        self.assertIsNone(self.runs.get("RUN-VALIDATION"))
        self.assertIsNone(self.snapshots.get("RUN-VALIDATION"))

    def test_submit_persists_exact_snapshot_without_client_od_payload(self):
        record = self.service.submit(
            run_id="RUN-1", owner_user_id="U1", situation_id="S1", run_config=self.config
        )
        self.assertEqual("queued", record.status)
        frozen = self.snapshots.get("RUN-1")
        self.assertIsNotNone(frozen)
        payload = frozen.to_dict()
        self.assertEqual(1, len(payload["od_distances"]))
        self.assertAlmostEqual(118.2362089185, payload["od_distances"][0]["distance_km"], places=6)
        self.assertEqual(record.snapshot_hash, frozen.content_hash)

    def test_missing_situation_is_explicit_not_validation_guess(self):
        with self.assertRaises(RunSubmissionSituationNotFoundError):
            self.service.validate(owner_user_id="U1", situation_id="NOPE", run_config=self.config)


if __name__ == "__main__":
    unittest.main()
