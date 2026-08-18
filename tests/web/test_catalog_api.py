from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.auth.principal import Principal
from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.situation import Situation, SituationAirport
from backend.storage.airport_repository import AirportRepository
from backend.storage.mission_repository import MissionRepository
from backend.storage.run_repository import RunRepository
from backend.storage.run_snapshot_repository import RunSnapshotRepository
from backend.web.catalog_api import CatalogApi
from tests.algorithm.test_snapshot_adapter import make_snapshot


class CatalogApiTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = Path(self.td.name) / "app.sqlite"
        self.airports = AirportRepository(self.db)
        self.airports.init_schema()
        self.missions = MissionRepository(self.db)
        self.runs = RunRepository(self.db)
        self.snapshots = RunSnapshotRepository(self.db)
        self.api = CatalogApi(
            airport_repository=self.airports, mission_repository=self.missions,
            run_repository=self.runs, snapshot_repository=self.snapshots,
        )
        self.viewer = Principal("U1", role="viewer")
        self.admin = Principal("ADMIN", is_admin=True)
        self.airport = AirportBase.from_mapping({
            "airport_id":"A1","airport_name":"Airport One","facility_type":"medium_airport",
            "role":"joint","icao_code":None,"iata_code":None,"region":"East","municipality":"City",
            "longitude":110.0,"latitude":30.0,"elevation_m":None,"scheduled_service":True,
            "runway_count":0,"max_runway_length_m":None,"runways":[],
        })
        self.profile = AirportOperationalProfile.from_mapping({
            "airport_id":"A1","configuration_complete":False,"capacity_per_window":None,
            "support_level":None,"aircraft_support":[],"resource_stocks":[],
        })

    def tearDown(self): self.td.cleanup()

    def test_airport_crud_is_revisioned_searchable_and_viewer_read_only(self):
        denied = self.api.create_airport({
            "airport": self.airport.to_dict(), "operational_profile": self.profile.to_dict()
        }, principal=self.viewer)
        self.assertEqual(403, denied.status)

        created = self.api.create_airport({
            "airport": self.airport.to_dict(), "operational_profile": self.profile.to_dict()
        }, principal=self.admin)
        self.assertEqual(201, created.status)
        self.assertEqual(1, created.body["metadata"]["revision"])
        listed = self.api.list_airports(principal=self.viewer, query="One", roles=["joint"], regions=["East"])
        self.assertEqual(1, listed.body["total"])

        changed = {**self.airport.to_dict(), "airport_name": "Airport One Changed"}
        updated = self.api.update_airport("A1", {
            "airport": changed, "operational_profile": self.profile.to_dict(), "expected_revision": 1
        }, principal=self.admin)
        self.assertEqual(2, updated.body["metadata"]["revision"])
        stale = self.api.update_airport("A1", {
            "airport": changed, "operational_profile": self.profile.to_dict(), "expected_revision": 1
        }, principal=self.admin)
        self.assertEqual(409, stale.status)
        self.assertEqual("CATALOG_STATE_CONFLICT", stale.body["error"]["code"])

    def test_new_airport_id_is_allocated_when_client_omits_technical_id(self):
        airport = self.airport.to_dict()
        profile = self.profile.to_dict()
        airport.pop("airport_id")
        profile.pop("airport_id")

        created = self.api.create_airport(
            {"airport": airport, "operational_profile": profile},
            principal=self.admin,
        )

        self.assertEqual(201, created.status)
        self.assertEqual("AP001", created.body["airport"]["airport_id"])
        self.assertEqual("AP001", created.body["operational_profile"]["airport_id"])

    def test_airport_delete_is_blocked_while_current_situation_references_it(self):
        self.api.create_airport({
            "airport": self.airport.to_dict(), "operational_profile": self.profile.to_dict()
        }, principal=self.admin)
        from backend.storage.situation_repository import SituationRepository
        situations = SituationRepository(self.db)
        situations.save_situation(Situation(
            situation_id="S1", name="S1",
            airports=(SituationAirport(airport=self.airport, operational_profile=self.profile),), missions=(),
        ), owner_user_id="U1")
        blocked = self.api.delete_airport("A1", {"expected_revision":1}, principal=self.admin)
        self.assertEqual(409, blocked.status)
        self.assertEqual("CATALOG_IN_USE", blocked.body["error"]["code"])

    def test_aircraft_resource_catalog_is_revisioned_as_one_editable_bundle(self):
        ac = {
            "aircraft_type_id":"fighter-x","name":"Fighter X","speed_kmh":800,
            "max_range_km":1200,"reserve_ratio":0.2,
            "departure_capacity_occupancy_factor":1.0,"arrival_capacity_occupancy_factor":1.0,
        }
        created_ac = self.api.create_aircraft_type({"aircraft_type": ac}, principal=self.admin)
        self.assertEqual(201, created_ac.status)
        self.assertEqual(1, created_ac.body["metadata"]["revision"])
        res = {"resource_type_id":"FUEL-X","name":"Fuel X","category":"fuel","unit":"t"}
        created_res = self.api.create_resource_type({"resource_type": res}, principal=self.admin)
        self.assertEqual(201, created_res.status)

        linked = self.api.replace_aircraft_resource_requirements("fighter-x", {
            "expected_revision":1,
            "requirements":[{
                "aircraft_type_id":"fighter-x","resource_type_id":"FUEL-X",
                "basis":"per_hour","quantity":1.5,
            }],
        }, principal=self.admin)
        self.assertEqual(200, linked.status)
        self.assertEqual(2, linked.body["metadata"]["revision"])
        self.assertEqual(1, len(linked.body["requirements"]))

        blocked_resource_delete = self.api.delete_resource_type(
            "FUEL-X", {"expected_revision":1}, principal=self.admin
        )
        self.assertEqual(409, blocked_resource_delete.status)
        self.assertEqual("CATALOG_IN_USE", blocked_resource_delete.body["error"]["code"])

    def test_bulk_airport_replace_keeps_one_current_catalog_and_situation_snapshot(self):
        first = self.api.replace_base_data_json({
            "dataset": "airports",
            "items": [{"airport": self.airport.to_dict(), "operational_profile": self.profile.to_dict()}],
        }, principal=self.admin)
        self.assertEqual(200, first.status)
        self.assertEqual("replace_current_state", first.body["mode"])
        self.assertFalse(first.body["version_history"])
        self.assertEqual(1, first.body["added"])

        from backend.storage.situation_repository import SituationRepository
        situations = SituationRepository(self.db)
        situations.save_situation(Situation(
            situation_id="S-BULK", name="Bulk snapshot",
            airports=(SituationAirport(airport=self.airport, operational_profile=self.profile),), missions=(),
        ), owner_user_id="U1")

        a2 = {**self.airport.to_dict(), "airport_id": "A2", "airport_name": "Airport Two"}
        p2 = {**self.profile.to_dict(), "airport_id": "A2"}
        second = self.api.replace_base_data_json({
            "dataset": "airports", "items": [{"airport": a2, "operational_profile": p2}],
        }, principal=self.admin)
        self.assertEqual(200, second.status)
        self.assertEqual({"added": 1, "updated": 0, "deleted": 1, "total": 1},
                         {k: second.body[k] for k in ("added","updated","deleted","total")})
        listed = self.api.list_airports(principal=self.viewer)
        self.assertEqual(["A2"], [x["airport_id"] for x in listed.body["items"]])
        frozen = situations.get_situation_for_actor("S-BULK", actor_user_id="U1", is_admin=False)
        self.assertEqual("A1", frozen.airports[0].airport.airport_id)

    def test_csv_replace_is_dataset_specific_and_validated_before_write(self):
        csv_text = (
            "resource_type_id,name,category,unit\n"
            "FUEL-1,Jet Fuel,fuel,t\n"
            "MAT-1,Material,material,kg\n"
        )
        replaced = self.api.replace_base_data_csv("resource_types", csv_text, principal=self.admin)
        self.assertEqual(200, replaced.status)
        self.assertEqual("csv", replaced.body["source_format"])
        self.assertEqual(2, replaced.body["total"])
        invalid = self.api.replace_base_data_csv(
            "resource_types", "resource_type_id,name,category,unit\nBAD,Bad,unknown,kg\n", principal=self.admin
        )
        self.assertEqual(422, invalid.status)
        current = self.api.list_resource_types(principal=self.viewer)
        self.assertEqual({"FUEL-1", "MAT-1"}, {x["resource_type"]["resource_type_id"] for x in current.body["items"]})

    def test_mission_history_reads_immutable_terminal_run_snapshots(self):
        snap = make_snapshot(run_id="RHIST")
        self.runs.create_queued(snapshot=snap, owner_user_id="U1")
        self.runs.claim_running("RHIST")
        self.runs.mark_failed("RHIST", message="failed")
        history = self.api.mission_history(principal=self.viewer)
        self.assertEqual(200, history.status)
        self.assertGreaterEqual(len(history.body["items"]), 1)
        self.assertEqual("RHIST", history.body["items"][0]["source_run_id"])
        self.assertIn("mission", history.body["items"][0])


if __name__ == "__main__": unittest.main()
