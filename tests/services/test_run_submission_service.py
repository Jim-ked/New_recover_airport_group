from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportAircraftSupport, AirportOperationalProfile, AirportResourceStock
from backend.domain.catalog import AircraftResourceRequirement, AircraftType, ResourceType
from backend.domain.mission import Mission, MissionAircraftRequirement
from backend.domain.run_config import RunConfig, RunConfigValidationError
from backend.domain.situation import Situation, SituationAirport
from backend.services.od_distance_service import ODDistanceService
from backend.services.run_service import RunService
from backend.services.run_snapshot_service import RunSnapshotService
from backend.services.run_submission_service import (
    RunSubmissionService,
    RunSubmissionSituationNotFoundError,
    RunSubmissionStaleValidationError,
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
            "resource_type_id": "fuel", "name": "Fuel", "category": "fuel", "unit": "t",
        }))
        self.airports.save_aircraft_resource_requirement(AircraftResourceRequirement.from_mapping({
            "aircraft_type_id": "fighter",
            "resource_type_id": "fuel",
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
            resource_stocks=(AirportResourceStock("fuel", 50),),
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
            "objective_profile_id": "research_objective_v1",
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
        self.assertEqual("research_objective_v1", result.run_config["objective_profile_id"])
        self.assertEqual(16.0, result.run_config["f3_time_reference_slots"])
        self.assertEqual(0.0, result.run_config["core_airport_reward_weight"])
        self.assertIsNone(self.runs.get("RUN-VALIDATION"))
        self.assertIsNone(self.snapshots.get("RUN-VALIDATION"))

    def test_submit_persists_exact_snapshot_without_client_od_payload(self):
        validation = self.service.validate(
            owner_user_id="U1", situation_id="S1", run_config=self.config
        )
        record = self.service.submit(
            run_id="RUN-1",
            owner_user_id="U1",
            situation_id="S1",
            run_config=self.config,
            expected_input_hash=validation.validated_input_hash,
        )
        self.assertEqual("queued", record.status)
        frozen = self.snapshots.get("RUN-1")
        self.assertIsNotNone(frozen)
        payload = frozen.to_dict()
        self.assertEqual(1, len(payload["od_distances"]))
        self.assertAlmostEqual(118.2362089185, payload["od_distances"][0]["distance_km"], places=6)
        self.assertEqual(record.snapshot_hash, frozen.content_hash)
        self.assertEqual(validation.run_config, payload["run_config"])

    def test_missing_situation_is_explicit_not_validation_guess(self):
        with self.assertRaises(RunSubmissionSituationNotFoundError):
            self.service.validate(owner_user_id="U1", situation_id="NOPE", run_config=self.config)

    def _write_f2_calibration(self, calibration, *, situation_id="S1", database=None):
        database = database or self.db
        path = database.with_name(database.name + ".f2-calibration.json")
        path.write_text(json.dumps({situation_id: calibration}), encoding="utf-8")
        return path

    def test_scene_f2_overrides_client_and_default_and_is_frozen(self):
        baseline = self.service.validate(owner_user_id="U1", situation_id="S1", run_config=self.config)
        calibration = {
            "f2_resource_reference_quantities": {"fuel": 20.0},
            "f2_resource_weights": {"fuel": 0.4},
        }
        path = self._write_f2_calibration(calibration)
        path.write_text(json.dumps({"S1": calibration}), encoding="utf-8-sig")
        client_config = {**self.config, "f2_resource_reference_quantities": {"fuel": 99.0},
                         "f2_resource_weights": {"fuel": 0.9}}
        validation = self.service.validate(owner_user_id="U1", situation_id="S1", run_config=client_config)
        for field, expected in calibration.items():
            self.assertEqual(expected, validation.run_config[field])
        for field in baseline.run_config.keys() - calibration.keys():
            self.assertEqual(baseline.run_config[field], validation.run_config[field])
        record = self.service.submit(run_id="RUN-SCENE", owner_user_id="U1", situation_id="S1",
                                     run_config=client_config, expected_input_hash=validation.validated_input_hash)
        frozen = self.snapshots.get(record.run_id)
        path.unlink()
        self.assertEqual(validation.run_config, frozen.to_dict()["run_config"])
        self.assertEqual(validation.run_config, frozen.clone_for_run("RUN-RETRY").to_dict()["run_config"])
        restored = self.service.validate(owner_user_id="U1", situation_id="S1", run_config=self.config)
        self.assertEqual(baseline.run_config, restored.run_config)

    def test_scene_f2_supports_rs_resource_ids_without_mapping(self):
        self.airports.save_resource_type(ResourceType("RS001", "Fuel litres", "fuel", "L"))
        self.airports.save_aircraft_resource_requirement(
            AircraftResourceRequirement("fighter", "RS001", "per_hour", 5000)
        )
        with self.assertRaisesRegex(RunConfigValidationError, "missing resource: RS001"):
            self.service.validate(owner_user_id="U1", situation_id="S1", run_config=self.config)
        calibration = {
            "f2_resource_reference_quantities": {"fuel": 20.0, "RS001": 10000.0},
            "f2_resource_weights": {"fuel": 0.4, "RS001": 0.6},
        }
        self._write_f2_calibration(calibration)
        result = self.service.validate(owner_user_id="U1", situation_id="S1", run_config=self.config)
        self.assertTrue(result.can_submit)
        for field, expected in calibration.items():
            self.assertEqual(expected, result.run_config[field])

    def test_scene_f2_change_requires_revalidation(self):
        calibration = {"f2_resource_reference_quantities": {"fuel": 20.0},
                       "f2_resource_weights": {"fuel": 0.4}}
        self._write_f2_calibration(calibration)
        validation = self.service.validate(owner_user_id="U1", situation_id="S1", run_config=self.config)
        calibration["f2_resource_reference_quantities"]["fuel"] = 21.0
        self._write_f2_calibration(calibration)
        with self.assertRaises(RunSubmissionStaleValidationError):
            self.service.submit(run_id="RUN-STALE", owner_user_id="U1", situation_id="S1",
                                run_config=self.config, expected_input_hash=validation.validated_input_hash)
        self.assertIsNone(self.runs.get("RUN-STALE"))
        self.assertIsNone(self.snapshots.get("RUN-STALE"))

    def test_scene_f2_is_isolated_by_database_and_situation(self):
        baseline = self.service.validate(owner_user_id="U1", situation_id="S1", run_config=self.config)
        calibration = {"f2_resource_reference_quantities": {"fuel": 20.0},
                       "f2_resource_weights": {"fuel": 0.4}}
        self._write_f2_calibration(calibration, situation_id="S2")
        self._write_f2_calibration(calibration, database=self.db.with_name("other.sqlite"))
        self._write_f2_calibration(calibration, database=self.db.with_suffix(".db"))
        actual = self.service.validate(owner_user_id="U1", situation_id="S1", run_config=self.config)
        self.assertEqual(baseline.run_config, actual.run_config)
        self.assertEqual(baseline.validated_input_hash, actual.validated_input_hash)

    def test_scene_f2_rejects_invalid_calibration_instead_of_falling_back(self):
        cases = [
            None,
            {},
            {"f2_resource_reference_quantities": {"fuel": 1}},
            {"f2_resource_reference_quantities": {}, "f2_resource_weights": {}},
            {"f2_resource_reference_quantities": {"fuel": 0}, "f2_resource_weights": {"fuel": 1}},
            {"f2_resource_reference_quantities": {"fuel": float("nan")}, "f2_resource_weights": {"fuel": 1}},
            {"f2_resource_reference_quantities": {"fuel": 1}, "f2_resource_weights": {"RS001": 1}},
            {"f2_resource_reference_quantities": {"RS001": 1}, "f2_resource_weights": {"RS001": 1}},
            {"f2_resource_reference_quantities": {"fuel": 1}, "f2_resource_weights": {"fuel": 1},
             "unmet_demand_penalty": 0},
        ]
        for calibration in cases:
            with self.subTest(calibration=calibration):
                self._write_f2_calibration(calibration)
                with self.assertRaises(RunConfigValidationError):
                    self.service.validate(owner_user_id="U1", situation_id="S1", run_config=self.config)
        path = self._write_f2_calibration(cases[-1])
        for content in ("{", "[]", "null"):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(RunConfigValidationError):
                    self.service.validate(owner_user_id="U1", situation_id="S1", run_config=self.config)

    def test_scene_f2_also_overrides_prebuilt_run_config(self):
        baseline = self.service.validate(owner_user_id="U1", situation_id="S1", run_config=self.config)
        prebuilt = RunConfig.from_mapping({**baseline.run_config, "f3_time_reference_slots": 32.0})
        calibration = {"f2_resource_reference_quantities": {"fuel": 20.0},
                       "f2_resource_weights": {"fuel": 0.4}}
        self._write_f2_calibration(calibration)
        result = self.service.validate(owner_user_id="U1", situation_id="S1",
                                       run_config=prebuilt)
        for field, expected in calibration.items():
            self.assertEqual(expected, result.run_config[field])
        for field in prebuilt.to_dict().keys() - calibration.keys():
            self.assertEqual(prebuilt.to_dict()[field], result.run_config[field])


if __name__ == "__main__":
    unittest.main()
