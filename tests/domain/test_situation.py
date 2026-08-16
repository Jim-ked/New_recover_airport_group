from __future__ import annotations

import unittest

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportAircraftSupport, AirportOperationalProfile, AirportResourceStock
from backend.domain.damage import DamageEvent, DamageScenario
from backend.domain.mission import Mission
from backend.domain.situation import ResourceReplenishment, Situation, SituationAirport


class SituationTests(unittest.TestCase):
    def _airport(self, airport_id="A1", name="A"):
        return AirportBase.from_mapping({
            "airport_id": airport_id, "airport_name": name, "facility_type": "small_airport", "role": "civil",
            "longitude": 120, "latitude": 30, "scheduled_service": False,
            "icao_code": None, "iata_code": None, "region": None, "municipality": None,
            "elevation_m": None, "runway_count": None, "max_runway_length_m": None, "runways": None,
        })

    def _profile(self, airport_id="A1", capacity=8):
        return AirportOperationalProfile(
            airport_id=airport_id, configuration_complete=True, capacity_per_window=capacity,
            aircraft_support=(AirportAircraftSupport("fighter", 3, 2),),
            resource_stocks=(AirportResourceStock("FUEL-1", 100, 0),),
        )

    def _capacity_event(self, event_id="D1", seq=0, airport_id="A1", start=2, end=5):
        return DamageEvent.from_mapping({
            "event_id": event_id, "sequence": seq,
            "target": {"airport_id": airport_id, "target_type": "airport", "target_id": None},
            "damage_type": "capacity_damage", "start_slot": start, "end_slot": end,
            "effect": {"closed": False, "remaining_capacity_per_window": 3},
            "recovery_mode": "average", "recovery_duration_slots": 2,
        })

    def test_situation_airport_requires_matching_id(self) -> None:
        with self.assertRaises(Exception):
            SituationAirport(self._airport(), self._profile("A2"))

    def test_with_airport_replaces_same_membership_not_duplicates(self) -> None:
        s = Situation.create(situation_id="S1", name="Situation")
        s = s.with_airport(SituationAirport(self._airport(name="old"), self._profile(capacity=8)))
        s = s.with_airport(SituationAirport(self._airport(name="new"), self._profile(capacity=9)))
        self.assertEqual(1, len(s.airports))
        self.assertEqual("new", s.airports[0].airport.airport_name)
        self.assertEqual(9, s.airports[0].operational_profile.capacity_per_window)

    def test_missions_are_owned_by_situation(self) -> None:
        mission = Mission.from_mapping({
            "mission_id": "M1", "name": "Task", "longitude": 120, "latitude": 30,
            "window_start_slot": 2, "window_end_slot": 4, "aircraft_requirements": [],
        })
        s = Situation.create(situation_id="S1", name="Situation").with_mission(mission)
        self.assertEqual(("M1",), tuple(m.mission_id for m in s.missions))

    def test_damage_scenario_overlap_allowed_sequence_unique_within_scenario(self) -> None:
        item = SituationAirport(self._airport(), self._profile())
        e1 = self._capacity_event("D1", 0, start=2, end=5)
        e2 = self._capacity_event("D2", 1, start=3, end=6)
        ds = DamageScenario("DS1", "Medium", "medium", (e1, e2))
        s = Situation.create(situation_id="S1", name="S").with_airport(item).with_damage_scenario(ds)
        self.assertEqual(["D1", "D2"], [e["event_id"] for e in s.to_dict()["damage_scenarios"][0]["events"]])
        with self.assertRaises(Exception):
            DamageScenario("DS2", "Bad", "custom", (e1, self._capacity_event("D3", 0, start=4, end=7)))

    def test_damage_target_must_belong_to_situation(self) -> None:
        ds = DamageScenario("DS1", "X", "custom", (self._capacity_event(airport_id="A2"),))
        with self.assertRaises(Exception):
            Situation.create(situation_id="S1", name="S").with_damage_scenario(ds)

    def test_damage_resource_and_aircraft_refs_must_exist_in_airport_profile(self) -> None:
        item = SituationAirport(self._airport(), self._profile())
        bad_resource = DamageScenario.from_mapping({
            "damage_scenario_id": "DS1", "name": "Bad", "category": "custom",
            "events": [{
                "event_id": "D1", "sequence": 0,
                "target": {"airport_id": "A1", "target_type": "airport", "target_id": None},
                "damage_type": "resource_damage", "start_slot": 1, "end_slot": 2,
                "effect": {"remaining_quantity": {"MAT-X": 0}},
                "recovery_mode": "instant", "recovery_duration_slots": None,
            }],
        })
        with self.assertRaises(Exception):
            Situation(situation_id="S1", name="S", airports=(item,), damage_scenarios=(bad_resource,))

    def test_cannot_remove_airport_still_referenced_by_damage(self) -> None:
        item = SituationAirport(self._airport(), self._profile())
        ds = DamageScenario("DS1", "X", "custom", (self._capacity_event(),))
        s = Situation.create(situation_id="S1", name="S").with_airport(item).with_damage_scenario(ds)
        with self.assertRaises(Exception):
            s.without_airport("A1")

    def test_content_hash_is_stable_across_member_order_and_changes_on_content(self) -> None:
        a1 = SituationAirport(self._airport(), self._profile())
        a2 = SituationAirport(self._airport("A2", "B"), self._profile("A2", 2))
        s1 = Situation(situation_id="S1", name="S", airports=(a1, a2))
        s2 = Situation(situation_id="S1", name="S", airports=(a2, a1))
        self.assertEqual(s1.content_hash(), s2.content_hash())
        self.assertNotEqual(s1.content_hash(), Situation(situation_id="S1", name="S2", airports=(a1, a2)).content_hash())


    def test_replenishment_schedule_is_situation_fact_and_cannot_exceed_capacity(self) -> None:
        profile = AirportOperationalProfile(
            airport_id="A1",
            configuration_complete=True,
            capacity_per_window=8,
            aircraft_support=(AirportAircraftSupport("fighter", 3, 2),),
            resource_stocks=(AirportResourceStock("FUEL-1", 100, 10),),
        )
        item = SituationAirport(
            self._airport(),
            profile,
            (ResourceReplenishment("FUEL-1", 3, 6),),
        )
        self.assertEqual(
            [{"resource_type_id": "FUEL-1", "slot": 3, "quantity": 6}],
            item.to_dict()["resource_replenishments"],
        )
        with self.assertRaises(Exception):
            SituationAirport(
                self._airport(),
                profile,
                (ResourceReplenishment("FUEL-1", 3, 11),),
            )

    def test_missing_replenishment_entry_means_zero_not_capacity_auto_fill(self) -> None:
        profile = AirportOperationalProfile(
            airport_id="A1",
            configuration_complete=True,
            capacity_per_window=8,
            aircraft_support=(AirportAircraftSupport("fighter", 3, 2),),
            resource_stocks=(AirportResourceStock("FUEL-1", 100, 10),),
        )
        item = SituationAirport(self._airport(), profile)
        self.assertEqual((), item.resource_replenishments)


if __name__ == "__main__":
    unittest.main()
