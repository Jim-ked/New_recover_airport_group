import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from backend.auth.csrf import CsrfValidationError
from backend.auth.principal import Principal
from backend.security.sm4 import SM4FileCipher
from backend.storage.airport_repository import AirportRepository
from backend.storage.database import initialize_database
from backend.storage.mission_repository import MissionRepository
from backend.web.catalog_api import CatalogApi
from backend.web.flask_catalog import create_catalog_blueprint


class BaseDataFilesApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "api.sqlite"
        initialize_database(self.db)
        self.airports = AirportRepository(self.db)
        self.missions = MissionRepository(self.db)
        self.cipher = SM4FileCipher(b"0123456789abcdef")
        self.api = CatalogApi(airport_repository=self.airports, mission_repository=self.missions,
                              file_cipher=self.cipher)
        self.admin = Principal("ADMIN", is_admin=True)
        self.viewer = Principal("VIEWER", role="viewer")
        self.actor = self.admin
        self.app = Flask(__name__)

        def guard(request, principal):
            if request.headers.get("X-CSRF-Token") != "test-token":
                raise CsrfValidationError("invalid CSRF token")

        self.app.register_blueprint(create_catalog_blueprint(api=self.api,
            principal_resolver=lambda request: self.actor, mutation_guard=guard))
        self.client = self.app.test_client()
        self.headers = {"X-CSRF-Token": "test-token"}
        self.api.replace_base_data_json({"dataset": "resource_types", "items": [{
            "resource_type_id": "F1", "name": "燃油", "category": "fuel", "unit": "t"}]}, principal=self.admin)

    def tearDown(self): self.temp.cleanup()

    def snapshot(self):
        with self.airports.connect() as conn: return "\n".join(conn.iterdump())

    def upload(self, data, fmt, encrypted=False, headers=None):
        return self.client.post("/api/base-data/import-multi", headers=self.headers if headers is None else headers,
            data={"dataset": "resource_types", "format": fmt, "encrypted": str(encrypted).lower(),
                  "file": (io.BytesIO(data), f"data.{fmt}" + (".sm4" if encrypted else ""))})

    def test_real_file_downloads_and_multipart_roundtrip(self):
        for fmt in ("json", "csv", "xlsx", "xml"):
            with self.subTest(fmt=fmt):
                response = self.client.post("/api/base-data/export", headers=self.headers,
                    json={"dataset": "resource_types", "format": fmt, "encrypt": False})
                self.assertEqual(200, response.status_code)
                self.assertIn(f"resource_types-current.{fmt}", response.headers["Content-Disposition"])
                if fmt == "xlsx": self.assertTrue(response.data.startswith(b"PK"))
                elif fmt == "json": self.assertEqual("燃油", response.json["items"][0]["name"])
                imported = self.upload(response.data, fmt)
                self.assertEqual(200, imported.status_code, imported.json)
                self.assertEqual(1, imported.json["total"])
                response.close()

    def test_encrypted_file_detection_and_failed_decryption(self):
        response = self.client.get("/api/base-data/export?dataset=resource_types&format=xlsx&encrypt=true")
        self.assertEqual(200, response.status_code)
        self.assertIn(".xlsx.sm4", response.headers["Content-Disposition"])
        self.assertEqual(200, self.upload(response.data, "xlsx", True).status_code)
        self.assertEqual(200, self.upload(response.data, "xlsx", False).status_code)
        before = self.snapshot()
        broken = response.data[:-1] + bytes([response.data[-1] ^ 1])
        self.assertEqual(400, self.upload(broken, "xlsx", True).status_code)
        self.assertEqual(before, self.snapshot())
        response.close()

    def test_permissions_authentication_csrf_and_bad_parameters(self):
        before = self.snapshot()
        self.assertEqual(403, self.upload(b"[]", "json", headers={}).status_code)
        self.actor = self.viewer
        self.assertEqual(403, self.upload(b"[]", "json").status_code)
        read = self.client.get("/api/base-data/export?dataset=resource_types&format=json")
        self.assertEqual(200, read.status_code); read.close()
        self.actor = None
        self.assertEqual(401, self.client.get("/api/base-data/export?dataset=resource_types&format=json").status_code)
        self.actor = self.admin
        for params in ({"dataset": "wrong", "format": "json"}, {"dataset": "airports", "format": "wrong"},
                       {"dataset": "airports", "format": "json", "encrypt": "maybe"}):
            response = self.client.post("/api/base-data/export", headers=self.headers, json=params)
            self.assertEqual(400, response.status_code)
        self.assertEqual(before, self.snapshot())

    def test_empty_invalid_schema_and_encryption_errors_leave_database_unchanged(self):
        before = self.snapshot()
        for data, fmt in ((b"", "csv"), (b"wrong\n", "csv"), (b"<wrong/>", "xml"),
                          (b"<records>", "xml"), (b"bad xlsx", "xlsx")):
            response = self.upload(data, fmt)
            self.assertEqual(400, response.status_code, response.json)
        self.assertEqual(400, self.upload(b"plaintext", "json", True).status_code)
        self.assertEqual(before, self.snapshot())

    def test_encryption_failure_never_returns_plaintext_attachment(self):
        self.api.file_cipher = None
        with patch.dict("os.environ", {}, clear=True):
            response = self.client.get("/api/base-data/export?dataset=resource_types&format=json&encrypt=true")
        self.assertEqual(400, response.status_code)
        self.assertNotIn("Content-Disposition", response.headers)
        self.assertNotIn("燃油", response.get_data(as_text=True))
