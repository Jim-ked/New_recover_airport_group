from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.catalog import AircraftType, ResourceType
from backend.domain.damage import DamageEvent, DamageScenario
from backend.domain.mission import Mission
from backend.domain.situation import ResourceReplenishment, Situation, SituationAirport
from backend.storage.airport_repository import AirportRepository
from backend.storage.situation_repository import SituationConflictError, SituationRepository


class SituationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "app.db"
        self.airports = AirportRepository(self.db)
        self.airports.init_schema()
        self.repo = SituationRepository(self.db)
        self.repo.init_schema()
        self.airports.save_aircraft_type(AircraftType.from_mapping({
            "aircraft_type_id": "fighter", "name": "fighter", "speed_kmh": 800,
            "departure_capacity_occupancy_factor": 1,
            "arrival_capacity_occupancy_factor": 1,
        }))
        self.airports.save_resource_type(ResourceType.from_mapping({
            "resource_type_id": "MAT-1", "name": "MAT-1", "category": "material", "unit": "t",
        }))

    def tearDown(self):
        self.tmp.cleanup()

    def _item(self, *, name="Airport Old", capacity=8):
        ap = AirportBase.from_mapping({
            "airport_id": "A1", "airport_name": name, "facility_type": "small_airport", "role": "military",
            "longitude": 120, "latitude": 30, "scheduled_service": False,
            "icao_code": None, "iata_code": None, "region": "CN-32", "municipality": "X",
            "elevation_m": 10, "runway_count": 0, "max_runway_length_m": None, "runways": [],
        })
        op = AirportOperationalProfile.from_mapping({
            "airport_id": "A1", "configuration_complete": True, "capacity_per_window": capacity,
            "support_level": "L1",
            "aircraft_support": [
                {"aircraft_type_id": "fighter", "initial_quantity": 2, "tau_reset_windows": 2}
            ],
            "resource_stocks": [{"resource_type_id": "MAT-1", "initial_quantity": 7, "replenishment_capacity_per_window": 0}],
        })
        return SituationAirport(ap, op)

    def _mission(self):
        return Mission.from_mapping({
            "mission_id": "M1", "name": "Task", "longitude": 121, "latitude": 31,
            "window_start_slot": 10, "window_end_slot": 20,
            "aircraft_requirements": [
                {"aircraft_type_id": "fighter", "required_sorties": 3, "tau_work_windows": 1}
            ],
        })

    def _damage_scenario(self):
        return DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "Custom", "category": "custom",
            "events": [
                {
                    "event_id": "D2", "sequence": 2,
                    "target": {"airport_id": "A1", "target_type": "support_element", "target_id": "SUP-1"},
                    "damage_type": "navigation_delay", "start_slot": 4, "end_slot": 9,
                    "effect": {"departure_delay_slots": 2, "return_delay_slots": 1},
                    "recovery_mode": "instant", "recovery_duration_slots": None,
                },
                {
                    "event_id": "D1", "sequence": 1,
                    "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                    "damage_type": "resource_damage", "start_slot": 3, "end_slot": 8,
                    "effect": {"remaining_quantity": {"MAT-1": 3}},
                    "recovery_mode": "average", "recovery_duration_slots": 5,
                },
            ],
        })

    def test_whole_situation_round_trip(self):
        s = Situation.create(situation_id="S1", name="S").with_airport(self._item()).with_mission(self._mission())
        self.repo.save_situation(s, owner_user_id="u1")
        got = self.repo.get_situation("S1")
        self.assertEqual(s.to_dict(), got.to_dict())
        self.assertEqual((), got.airports[0].airport.runways)
        self.assertTrue(got.airports[0].operational_profile.supports_aircraft("fighter"))
        self.assertEqual(2, got.airports[0].operational_profile.aircraft_support[0].initial_quantity)

    def test_explicit_save_replaces_children_and_no_autosave(self):
        saved = Situation.create(situation_id="S1", name="S").with_airport(self._item()).with_mission(self._mission())
        self.repo.save_situation(saved, owner_user_id="u1")
        working = saved.without_mission("M1").with_airport(self._item(name="Working Changed", capacity=12))
        before_save = self.repo.get_situation("S1")
        self.assertEqual("Airport Old", before_save.airports[0].airport.airport_name)
        self.assertEqual(1, len(before_save.missions))
        self.repo.save_situation(working, owner_user_id="u1")
        after = self.repo.get_situation("S1")
        self.assertEqual("Working Changed", after.airports[0].airport.airport_name)
        self.assertEqual(12, after.airports[0].operational_profile.capacity_per_window)
        self.assertEqual(0, len(after.missions))

    def test_base_airport_edit_does_not_propagate_to_saved_situation_snapshot(self):
        base = self._item(name="Base A", capacity=8)
        self.airports.save_airport(base.airport)
        self.airports.save_operational_profile(base.operational_profile)
        self.repo.save_situation(Situation.create(situation_id="S1", name="S").with_airport(base), owner_user_id="u1")
        changed = self._item(name="Base A Changed", capacity=99)
        self.airports.save_airport(changed.airport)
        self.airports.save_operational_profile(changed.operational_profile)
        got = self.repo.get_situation("S1")
        self.assertEqual("Base A", got.airports[0].airport.airport_name)
        self.assertEqual(8, got.airports[0].operational_profile.capacity_per_window)

    def test_damage_scenario_round_trip_overlap_and_order(self):
        s = Situation.create(situation_id="S1", name="S").with_airport(self._item()).with_damage_scenario(self._damage_scenario())
        self.repo.save_situation(s, owner_user_id="u1")
        got = self.repo.get_situation("S1")
        self.assertEqual(["D1", "D2"], [e.event_id for e in got.damage_scenarios[0].events])
        self.assertEqual(s.to_dict(), got.to_dict())
        self.assertEqual(7, got.airports[0].operational_profile.resource_stocks[0].initial_quantity)

    def test_explicit_save_can_remove_damage_before_airport(self):
        s = Situation.create(situation_id="S1", name="S").with_airport(self._item()).with_damage_scenario(self._damage_scenario())
        self.repo.save_situation(s, owner_user_id="u1")
        cleaned = s.without_damage_scenario("DS1").without_airport("A1")
        self.repo.save_situation(cleaned, owner_user_id="u1")
        got = self.repo.get_situation("S1")
        self.assertEqual((), got.airports)
        self.assertEqual((), got.damage_scenarios)

    def test_save_returns_content_hash_and_rejects_stale_expected_hash(self):
        s = Situation.create(situation_id="S1", name="S").with_airport(self._item())
        h1 = self.repo.save_situation(s, owner_user_id="u1")
        self.assertEqual(h1, self.repo.get_content_hash("S1"))
        changed = Situation(
            situation_id="S1", name="S changed", description=s.description,
            airports=s.airports, missions=s.missions, damage_scenarios=s.damage_scenarios,
        )
        h2 = self.repo.save_situation(changed, owner_user_id="u1", expected_content_hash=h1)
        self.assertNotEqual(h1, h2)
        with self.assertRaises(SituationConflictError):
            self.repo.save_situation(s, owner_user_id="u1", expected_content_hash=h1)
        self.assertEqual(h2, self.repo.get_content_hash("S1"))


    def test_resource_replenishment_schedule_round_trip(self):
        base = self._item()
        profile = AirportOperationalProfile.from_mapping({
            **base.operational_profile.to_dict(),
            "resource_stocks": [{
                "resource_type_id": "MAT-1",
                "initial_quantity": 7,
                "replenishment_capacity_per_window": 3,
            }],
        })
        item = SituationAirport(
            base.airport,
            profile,
            (
                ResourceReplenishment("MAT-1", 10, 2),
                ResourceReplenishment("MAT-1", 12, 3),
            ),
        )
        s = Situation.create(situation_id="S1", name="S").with_airport(item)
        self.repo.save_situation(s, owner_user_id="u1")
        got = self.repo.get_situation("S1")
        self.assertEqual(3, got.airports[0].operational_profile.resource_stocks[0].replenishment_capacity_per_window)
        self.assertEqual(
            (
                ResourceReplenishment("MAT-1", 10, 2.0),
                ResourceReplenishment("MAT-1", 12, 3.0),
            ),
            got.airports[0].resource_replenishments,
        )

    def test_owner_metadata_is_stable_and_visible_queries_are_scoped(self):
        s1 = Situation.create(situation_id="OWN1", name="One")
        s2 = Situation.create(situation_id="OWN2", name="Two")
        self.repo.save_situation(s1, owner_user_id="U1")
        self.repo.save_situation(s2, owner_user_id="U2")

        self.assertEqual("U1", self.repo.get_metadata("OWN1")["owner_user_id"])
        self.assertEqual(["OWN1"], [x["situation_id"] for x in self.repo.list_visible(actor_user_id="U1")])
        self.assertEqual({"OWN1", "OWN2"}, {x["situation_id"] for x in self.repo.list_visible(actor_user_id="ADMIN", is_admin=True)})
        with self.assertRaises(Exception):
            self.repo.get_situation_for_actor("OWN2", actor_user_id="U1")

    def test_owner_cannot_be_silently_reassigned_by_save(self):
        s = Situation.create(situation_id="OWN", name="One")
        self.repo.save_situation(s, owner_user_id="U1")
        with self.assertRaises(Exception):
            self.repo.save_situation(s, owner_user_id="U2")


if __name__ == "__main__":
    unittest.main()
