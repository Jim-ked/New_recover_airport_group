from __future__ import annotations
import tempfile, unittest
from pathlib import Path
from backend.domain.airport import AirportBase
from backend.storage.workspace_airport_repository import WorkspaceAirportRepository

class WorkspaceAirportBundleTruthTests(unittest.TestCase):
    def test_list_is_selectable_but_detail_preserves_missing_profile(self):
        with tempfile.TemporaryDirectory() as td:
            repo=WorkspaceAirportRepository(Path(td)/"app.db")
            repo.init_schema()
            repo.save_airport(AirportBase.from_mapping({
                "airport_id":"A1","airport_name":"Airport 1","facility_type":"small_airport",
                "role":"civil","icao_code":None,"iata_code":None,"region":"R1","municipality":"M1",
                "longitude":118.0,"latitude":34.0,"elevation_m":None,"scheduled_service":False,
                "runway_count":None,"max_runway_length_m":None,"runways":None
            }))
            rows,total=repo.list_airport_bundles(limit=20,offset=0)
            self.assertEqual(1,total)
            self.assertFalse(rows[0]["configuration_complete"])
            detail=repo.get_airport_bundle("A1")
            self.assertIsNone(detail["operational_profile"])
            synthetic=repo.get_operational_profile("A1")
            self.assertFalse(synthetic.configuration_complete)

if __name__=="__main__": unittest.main()
