from __future__ import annotations

import os
import tempfile
import unittest

from backend.auth.principal import Principal
from backend.domain.situation import Situation
from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.mission import Mission, MissionAircraftRequirement
from backend.domain.catalog import AircraftType
from backend.storage.airport_repository import AirportRepository
from backend.storage.mission_repository import MissionRepository
from backend.storage.database import initialize_database
from backend.storage.situation_repository import SituationRepository
from backend.web.situation_api import SituationApi


class SituationApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "app.db")
        initialize_database(self.db)
        self.repo = SituationRepository(self.db)
        self.repo.save_situation(Situation.create(situation_id="S-U1", name="Owned U1"), owner_user_id="U1")
        self.repo.save_situation(Situation.create(situation_id="S-U2", name="Owned U2"), owner_user_id="U2")
        self.airports = AirportRepository(self.db)
        self.missions = MissionRepository(self.db)
        self.api = SituationApi(
            situation_repository=self.repo,
            airport_repository=self.airports,
            mission_repository=self.missions,
        )
        self.u1 = Principal("U1", role="viewer")
        self.u2 = Principal("U2", role="operator")
        self.admin = Principal("ADMIN", is_admin=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_is_owner_scoped_and_admin_can_see_all(self):
        u1 = self.api.list(principal=self.u1)
        self.assertEqual(200, u1.status)
        self.assertEqual(["S-U1"], [x["situation_id"] for x in u1.body["items"]])
        self.assertEqual("U1", u1.body["items"][0]["owner_user_id"])

        admin = self.api.list(principal=self.admin)
        self.assertEqual(200, admin.status)
        self.assertEqual({"S-U1", "S-U2"}, {x["situation_id"] for x in admin.body["items"]})

    def test_list_supports_search_and_total(self):
        response = self.api.list(principal=self.admin, query="Owned U1", limit="1", offset="0")
        self.assertEqual(200, response.status)
        self.assertEqual(1, response.body["total"])
        self.assertEqual("S-U1", response.body["items"][0]["situation_id"])

    def test_situation_ids_are_allocated_by_backend_for_new_working_copies(self):
        first = self.api.allocate_id(principal=self.u2)
        second = self.api.create(
            {"situation": {"name": "Auto ID", "description": None, "airports": [], "missions": [], "damage_scenarios": []}},
            principal=self.u2,
        )

        self.assertEqual(201, first.status)
        self.assertEqual("ST001", first.body["situation_id"])
        self.assertEqual(201, second.status)
        self.assertEqual("ST002", second.body["situation"]["situation_id"])

    def test_working_copy_copy_airport_and_mission_is_nonpersistent_backend_transform(self):
        airport = AirportBase.from_mapping({
            "airport_id": "A1", "airport_name": "Base A1", "facility_type": "medium_airport",
            "role": "joint", "longitude": 118.8, "latitude": 31.7,
            "scheduled_service": True, "runway_count": 0, "max_runway_length_m": None, "runways": [],
        })
        profile = AirportOperationalProfile(
            airport_id="A1", configuration_complete=True, capacity_per_window=8,
            support_level="L1", aircraft_support=(), resource_stocks=(),
        )
        self.airports.save_airport_bundle(airport=airport, operational_profile=profile, create_only=True)
        self.airports.create_aircraft_type_versioned(AircraftType("fighter", "Fighter"))
        mission = Mission(
            "M1", "Template M1", 120.0, 32.0, 2, 6,
            (MissionAircraftRequirement("fighter", 1, 1),),
        )
        self.missions.save_versioned(mission, create_only=True)

        operator = Principal("U1", role="operator")
        working = Situation.create(situation_id="LOCAL", name="Unsaved local")
        airport_preview = self.api.copy_airport_to_working_copy(
            {"situation": working.to_dict(), "airport_id": "A1"}, principal=operator
        )
        self.assertEqual(200, airport_preview.status)
        self.assertFalse(airport_preview.body["persisted"])
        self.assertEqual("A1", airport_preview.body["situation"]["airports"][0]["airport"]["airport_id"])
        self.assertIsNone(self.repo.get_situation("LOCAL"))

        mission_preview = self.api.copy_mission_to_working_copy(
            {"situation": airport_preview.body["situation"], "mission_id": "M1"}, principal=operator
        )
        self.assertEqual(200, mission_preview.status)
        self.assertEqual("M1", mission_preview.body["situation"]["missions"][0]["mission_id"])
        self.assertIsNone(self.repo.get_situation("LOCAL"))


    def test_working_copy_canonicalize_is_nonpersistent_and_uses_domain_validation(self):
        operator = Principal("U1", role="operator")
        working = Situation.create(situation_id="LOCAL-CANON", name="Canonical")
        ok = self.api.canonicalize_working_copy({"situation": working.to_dict()}, principal=operator)
        self.assertEqual(200, ok.status)
        self.assertFalse(ok.body["persisted"])
        self.assertEqual("canonicalize", ok.body["operation"])
        self.assertEqual(64, len(ok.body["working_copy_hash"]))
        self.assertIsNone(self.repo.get_situation("LOCAL-CANON"))

        invalid = working.to_dict()
        invalid["missions"] = [{
            "mission_id": "M-BAD", "name": "Bad", "longitude": None, "latitude": None,
            "window_start_slot": 0, "window_end_slot": 1, "aircraft_requirements": [],
        }]
        bad = self.api.canonicalize_working_copy({"situation": invalid}, principal=operator)
        self.assertEqual(422, bad.status)
        self.assertIn("error", bad.body)

    def test_detail_returns_canonical_saved_object_and_hash(self):
        response = self.api.detail("S-U1", principal=self.u1)
        self.assertEqual(200, response.status)
        self.assertEqual("S-U1", response.body["situation"]["situation_id"])
        self.assertEqual(64, len(response.body["content_hash"]))
        self.assertEqual("U1", response.body["owner_user_id"])

    def test_detail_does_not_leak_other_owner(self):
        response = self.api.detail("S-U2", principal=self.u1)
        self.assertEqual(403, response.status)
        self.assertEqual("FORBIDDEN", response.body["error"]["code"])

    def test_missing_situation_is_404(self):
        response = self.api.detail("NOPE", principal=self.u1)
        self.assertEqual(404, response.status)
        self.assertEqual("SITUATION_NOT_FOUND", response.body["error"]["code"])

    def test_explicit_permission_is_required(self):
        principal = Principal("U1", permissions=frozenset({"runs.read"}))
        response = self.api.list(principal=principal)
        self.assertEqual(403, response.status)
        self.assertEqual("PERMISSION_DENIED", response.body["error"]["code"])

    def test_operator_can_create_update_and_delete_with_content_hash_lock(self):
        operator = Principal("U1", role="operator")
        created_obj = Situation.create(situation_id="S-NEW", name="New")
        created = self.api.create({"situation": created_obj.to_dict()}, principal=operator)
        self.assertEqual(201, created.status)
        first_hash = created.body["content_hash"]

        changed = Situation.create(situation_id="S-NEW", name="Changed")
        updated = self.api.update(
            "S-NEW",
            {"situation": changed.to_dict(), "expected_content_hash": first_hash},
            principal=operator,
        )
        self.assertEqual(200, updated.status)
        self.assertNotEqual(first_hash, updated.body["content_hash"])

        stale = self.api.update(
            "S-NEW",
            {"situation": changed.to_dict(), "expected_content_hash": first_hash},
            principal=operator,
        )
        self.assertEqual(409, stale.status)
        self.assertEqual("SITUATION_STATE_CONFLICT", stale.body["error"]["code"])

        deleted = self.api.delete(
            "S-NEW", {"expected_content_hash": updated.body["content_hash"]}, principal=operator
        )
        self.assertEqual(200, deleted.status)
        self.assertTrue(deleted.body["deleted"])

    def test_viewer_cannot_mutate_situation(self):
        response = self.api.create(
            {"situation": Situation.create(situation_id="DENIED", name="Denied").to_dict()},
            principal=self.u1,
        )
        self.assertEqual(403, response.status)
        self.assertEqual("PERMISSION_DENIED", response.body["error"]["code"])

    def test_legacy_unowned_row_is_admin_only(self):
        # Simulate a row preserved by v012 migration without silently assigning it.
        with self.repo.connect() as conn:
            conn.execute(
                "INSERT INTO situations (situation_id,name,description,content_hash,owner_user_id,created_at,updated_at) "
                "VALUES ('LEGACY','Legacy',NULL,NULL,NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        user = self.api.list(principal=self.u1)
        self.assertNotIn("LEGACY", [x["situation_id"] for x in user.body["items"]])
        admin = self.api.list(principal=self.admin)
        self.assertIn("LEGACY", [x["situation_id"] for x in admin.body["items"]])


if __name__ == "__main__":
    unittest.main()
