import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.catalog import AircraftType, ResourceType, AircraftResourceRequirement
from backend.domain.mission import Mission
from backend.security.sm4 import SM4FileCipher
from backend.services.base_data_import_service import BaseDataImportError, BaseDataImportService
from backend.services.base_data_export_service import BaseDataExportService
from backend.storage.airport_repository import AirportRepository
from backend.storage.database import initialize_database
from backend.storage.mission_repository import MissionRepository


class BaseDataMultiFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "catalog.sqlite"
        initialize_database(self.db)
        self.airports = AirportRepository(self.db)
        self.missions = MissionRepository(self.db)
        self.importer = BaseDataImportService(airport_repository=self.airports, mission_repository=self.missions)
        self.exporter = BaseDataExportService(airport_repository=self.airports, mission_repository=self.missions)
        self.cipher = SM4FileCipher(b"0123456789abcdef")
        self.airports.replace_aircraft_types_current([AircraftType.from_mapping({
            "aircraft_type_id": "AC001", "name": "机型一", "speed_kmh": 800,
            "max_range_km": 1200, "reserve_ratio": 0.2,
            "departure_capacity_occupancy_factor": 1, "arrival_capacity_occupancy_factor": 1})])
        self.airports.replace_resource_types_current([ResourceType.from_mapping({
            "resource_type_id": "F001", "name": "燃油", "category": "fuel", "unit": "t"})])
        self.airports.replace_aircraft_resource_requirements_current([AircraftResourceRequirement.from_mapping({
            "aircraft_type_id": "AC001", "resource_type_id": "F001", "basis": "per_hour", "quantity": 1.5})])
        airport = AirportBase.from_mapping({
            "airport_id": "AP001", "airport_name": "机场<&一", "facility_type": "medium_airport",
            "role": "joint", "longitude": 110, "latitude": 30, "scheduled_service": False,
            "parking_stand_count": 3, "runway_count": 1, "max_runway_length_m": 2400,
            "runways": [{"runway_id": "RW1", "length_m": 2400, "width_m": 45, "lighted": True}]})
        profile = AirportOperationalProfile.from_mapping({
            "airport_id": "AP001", "capacity_per_window": 4, "support_level": "legacy-L2",
            "aircraft_support": [{"aircraft_type_id": "AC001", "initial_quantity": 5, "tau_reset_windows": 1}],
            "resource_stocks": [{"resource_type_id": "F001", "initial_quantity": 100,
                                 "replenishment_capacity_per_window": 10, "storage_capacity": 200}]},
            derive_configuration=True)
        self.airports.replace_airport_bundles_current([(airport, profile)])
        self.missions.replace_current([Mission.from_mapping({
            "mission_id": "M001", "name": "任务一", "longitude": 112, "latitude": 31,
            "window_start_slot": 1, "window_end_slot": 8,
            "aircraft_requirements": [{"aircraft_type_id": "AC001", "required_sorties": 2, "tau_work_windows": 1}]})])

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        with self.airports.connect() as conn:
            return "\n".join(conn.iterdump())

    def test_all_five_datasets_roundtrip_all_formats(self):
        for dataset in ("airports", "missions", "aircraft_types", "resource_types", "aircraft_resource_requirements"):
            original = json.loads(self.exporter.export_dataset_records(dataset, format="json").content)
            for fmt in ("json", "csv", "xlsx", "xml"):
                with self.subTest(dataset=dataset, fmt=fmt):
                    file = self.exporter.export_dataset_records(dataset, format=fmt)
                    self.assertTrue(file.filename.endswith("." + fmt))
                    self.assertIsInstance(file.content, bytes)
                    result = self.importer.replace_multi_format(dataset, file.content, format=fmt)
                    self.assertEqual(1, result.total)
                    restored = json.loads(self.exporter.export_dataset_records(dataset, format="json").content)
                    self.assertEqual(original["items"], restored["items"])

    def test_encrypted_xlsx_roundtrip_and_wrong_key_keeps_database(self):
        expected = json.loads(self.exporter.export_dataset_records("airports", format="json").content)["items"]
        file = self.exporter.export_dataset_records("airports", format="xlsx", encrypt=True, cipher=self.cipher)
        self.assertTrue(file.filename.endswith(".xlsx.sm4"))
        self.assertTrue(self.cipher.is_encrypted(file.content))
        self.assertTrue(self.cipher.decrypt_file(file.content).startswith(b"PK"))
        before = self.snapshot()
        with self.assertRaises(BaseDataImportError):
            self.importer.replace_multi_format("airports", file.content, format="xlsx", encrypted=True,
                                               cipher=SM4FileCipher(b"fedcba9876543210"))
        self.assertEqual(before, self.snapshot())
        self.importer.replace_multi_format("airports", file.content, format="xlsx", encrypted=True, cipher=self.cipher)
        self.assertEqual(expected, json.loads(self.exporter.export_dataset_records("airports", format="json").content)["items"])

    def test_bad_files_never_modify_any_table(self):
        workbook = Workbook(); workbook.active.append(["wrong"]); workbook.active.append(["value"])
        stream = io.BytesIO(); workbook.save(stream); workbook.close()
        cases = [(b"", "csv"), (b"wrong\nvalue\n", "csv"), (b"wrong\n", "csv"),
                 (b"<wrong/>", "xml"), (b"<records/>", "xml"), (b"<records>", "xml"),
                 (stream.getvalue(), "xlsx"), (b"[]", "json"),
                 (b'{"dataset":"missions","items":[{}]}', "json"),
                 (b"resource_type_id,name,category,unit\nF001,ok,fuel,t\nF002,bad,invalid,t\n", "csv")]
        for content, fmt in cases:
            with self.subTest(fmt=fmt, content=content[:35]):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.importer.replace_multi_format("resource_types", content, format=fmt)
                self.assertEqual(before, self.snapshot())

    def test_unknown_json_airport_bundle_field_does_not_write(self):
        payload = json.loads(self.exporter.export_dataset_records("airports", format="json").content)
        payload["items"][0]["mistyped_profile"] = {}
        before = self.snapshot()
        with self.assertRaisesRegex(BaseDataImportError, "unknown fields"):
            self.importer.replace_multi_format("airports", json.dumps(payload).encode(), format="json")
        self.assertEqual(before, self.snapshot())

    def test_export_uses_one_snapshot_during_concurrent_changes(self):
        with self.airports.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
        old_profile = self.airports.get_operational_profile("AP001").to_dict()
        read_airports = AirportRepository.list_airports

        def change_profile_after_airports_read(repository):
            records = read_airports(repository)
            self.airports.save_operational_profile(AirportOperationalProfile.from_mapping(
                {**old_profile, "capacity_per_window": 99}, derive_configuration=True))
            return records

        with patch.object(AirportRepository, "list_airports", change_profile_after_airports_read):
            exported = json.loads(self.exporter.export_dataset_records("airports", format="json").content)
        self.assertEqual(old_profile, exported["items"][0]["operational_profile"])
        self.assertEqual(99, self.airports.get_operational_profile("AP001").capacity_per_window)

        read_mission = MissionRepository.get

        def remove_mission_after_id_list_read(repository, mission_id):
            self.missions.delete(mission_id)
            return read_mission(repository, mission_id)

        with patch.object(MissionRepository, "get", remove_mission_after_id_list_read):
            exported = json.loads(self.exporter.export_dataset_records("missions", format="json").content)
        self.assertEqual("M001", exported["items"][0]["mission_id"])
        self.assertEqual([], self.missions.list())

    def test_legacy_csv_runways_bom_and_wrong_header_are_safe(self):
        file = self.exporter.export_dataset_records("airports", format="csv")
        self.importer.replace_csv("airports", file.content.decode("utf-8"))
        self.assertEqual(3, self.airports.get_airport("AP001").parking_stand_count)
        self.assertEqual(1, self.airports.get_airport("AP001").runway_count)
        before = self.snapshot()
        with self.assertRaises(BaseDataImportError): self.importer.replace_csv("airports", "wrong_header\n")
        self.assertEqual(before, self.snapshot())

    def test_legacy_explicit_json_empty_replacement_remains_supported(self):
        self.assertEqual(1, self.importer.replace_json("missions", []).deleted)

    def test_null_optional_fields_and_unknown_vs_empty_runways_roundtrip(self):
        aircraft = AircraftType.from_mapping({"aircraft_type_id": "AC002", "name": "未配置机型"})
        self.airports.save_aircraft_type(aircraft)
        base = self.airports.get_airport("AP001").to_dict()
        for known in (False, True):
            airport = AirportBase.from_mapping({**base, "airport_id": "AP002", "parking_stand_count": None,
                "runways": [] if known else None, "runway_count": 0 if known else None, "max_runway_length_m": None})
            self.airports.replace_airport_bundles_current([(airport, None)])
            for fmt in ("json", "csv", "xlsx", "xml"):
                file = self.exporter.export_dataset_records("airports", format=fmt)
                self.importer.replace_multi_format("airports", file.content, format=fmt)
                restored = self.airports.get_airport("AP002")
                self.assertEqual(0 if known else None, restored.runway_count)
                self.assertIsNone(restored.parking_stand_count)
                self.assertIsNone(self.airports.get_airport_bundle("AP002")["operational_profile"])
        for fmt in ("json", "csv", "xlsx", "xml"):
            file = self.exporter.export_dataset_records("aircraft_types", format=fmt)
            self.importer.replace_multi_format("aircraft_types", file.content, format=fmt)
            self.assertEqual(aircraft.to_dict(), self.airports.get_aircraft_type("AC002").to_dict())

    def test_relationship_failure_rolls_back_valid_rows(self):
        before = self.snapshot()
        content = b'aircraft_type_id,resource_type_id,basis,quantity\nAC001,F001,per_hour,2\nAC001,MISSING,per_hour,1\n'
        with self.assertRaises(KeyError):
            self.importer.replace_multi_format("aircraft_resource_requirements", content, format="csv")
        self.assertEqual(before, self.snapshot())

    def test_master_document_and_legacy_export_wrapper_remain_compatible(self):
        profile = self.airports.get_operational_profile("AP001").to_dict()
        airport = self.airports.get_airport("AP001").to_dict()
        master = {"schema": "airport_master_v1", "generated_date": "2026-10-10",
                  "coordinate_reference_system": "WGS84", "count": 1,
                  "airports": [{**airport, "airport_name": "主数据更新"}]}
        self.importer.replace_multi_format("airports", json.dumps(master).encode(), format="json")
        self.assertEqual(profile, self.airports.get_operational_profile("AP001").to_dict())
        self.assertEqual("主数据更新", self.airports.get_airport("AP001").airport_name)
        legacy = {"dataset": "airports", "exported_at": "2026-10-10T00:00:00Z",
                  "items": [{"airport": airport, "operational_profile": profile}]}
        self.importer.replace_multi_format("airports", json.dumps(legacy).encode(), format="json")
        self.assertEqual(profile, self.airports.get_operational_profile("AP001").to_dict())

    def test_file_encryption_wraps_each_format_and_empty_exports_cannot_clear(self):
        for fmt in ("json", "csv", "xlsx", "xml"):
            file = self.exporter.export_dataset_records("missions", format=fmt, encrypt=True, cipher=self.cipher)
            self.importer.replace_multi_format("missions", file.content, format=fmt, cipher=self.cipher)
            self.assertEqual("任务一", self.missions.get("M001").name)
        self.importer.replace_json("missions", [])
        empty_files = [self.exporter.export_dataset_records("missions", format=fmt) for fmt in ("json", "csv", "xlsx", "xml")]
        self.missions.replace_current([Mission.from_mapping({"mission_id": "M2", "name": "保留任务",
            "longitude": 110, "latitude": 30, "window_start_slot": 0, "window_end_slot": 1})])
        for file in empty_files:
            before = self.snapshot()
            with self.assertRaises(BaseDataImportError):
                self.importer.replace_multi_format("missions", file.content, format=file.format)
            self.assertEqual(before, self.snapshot())
