from __future__ import annotations

import unittest

from backend.domain.mission import Mission, MissionValidationError


class MissionTests(unittest.TestCase):
    def test_half_open_window_requires_end_after_start(self) -> None:
        m = Mission.from_mapping({
            "mission_id": "M1", "name": "Task", "longitude": 120, "latitude": 30,
            "window_start_slot": 42, "window_end_slot": 56,
            "aircraft_requirements": [
                {"aircraft_type_id": "fighter", "required_sorties": 3, "tau_work_windows": 1}
            ],
        })
        self.assertEqual((42, 56), (m.window_start_slot, m.window_end_slot))
        with self.assertRaises(MissionValidationError):
            Mission.from_mapping({
                "mission_id": "M2", "name": "Bad", "longitude": 120, "latitude": 30,
                "window_start_slot": 5, "window_end_slot": 5, "aircraft_requirements": [],
            })

    def test_requirement_rows_are_sparse_and_unique(self) -> None:
        with self.assertRaises(MissionValidationError):
            Mission.from_mapping({
                "mission_id": "M1", "name": "Task", "longitude": 120, "latitude": 30,
                "window_start_slot": 1, "window_end_slot": 2,
                "aircraft_requirements": [
                    {"aircraft_type_id": "fighter", "required_sorties": 1, "tau_work_windows": 1},
                    {"aircraft_type_id": "fighter", "required_sorties": 2, "tau_work_windows": 1},
                ],
            })

    def test_requirement_row_requires_explicit_tau_work(self) -> None:
        with self.assertRaises(MissionValidationError):
            Mission.from_mapping({
                "mission_id": "M1", "name": "Task", "longitude": 120, "latitude": 30,
                "window_start_slot": 1, "window_end_slot": 2,
                "aircraft_requirements": [
                    {"aircraft_type_id": "fighter", "required_sorties": 1}
                ],
            })


if __name__ == "__main__":
    unittest.main()
