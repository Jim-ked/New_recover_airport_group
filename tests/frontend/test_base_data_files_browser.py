"""Real browser + Flask/catalog/database file roundtrips, with isolated data."""
import json
import time
from pathlib import Path
from threading import Thread

import pytest
from flask import Flask, session
from werkzeug.serving import make_server

from backend.auth.csrf import CsrfValidationError
from backend.auth.principal import Principal
from backend.security.sm4 import SM4FileCipher
from backend.storage.airport_repository import AirportRepository
from backend.storage.database import initialize_database
from backend.storage.mission_repository import MissionRepository
from backend.web.catalog_api import CatalogApi
from backend.web.flask_catalog import create_catalog_blueprint
from backend.web.flask_ui import create_ui_blueprint
from tests.frontend.test_base_data_validation_browser import browser

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def file_server(tmp_path):
    db = tmp_path / "files.sqlite"; initialize_database(db)
    api = CatalogApi(airport_repository=AirportRepository(db), mission_repository=MissionRepository(db),
                     file_cipher=SM4FileCipher(b"0123456789abcdef"))
    admin = Principal("FILE-TEST", is_admin=True)
    api.replace_base_data_json({"dataset": "resource_types", "items": [{
        "resource_type_id": "F1", "name": "燃油测试", "category": "fuel", "unit": "t"}]}, principal=admin)
    app = Flask("file-test", template_folder=str(ROOT / "frontend/templates"),
                static_folder=str(ROOT / "frontend/static"), static_url_path="/static")
    app.secret_key = "file-test-session-only"
    actor = {"viewer": False}

    def guard(request, principal):
        if request.headers.get("X-CSRF-Token") != "file-test-csrf":
            raise CsrfValidationError("missing CSRF token")

    app.register_blueprint(create_ui_blueprint())
    app.register_blueprint(create_catalog_blueprint(api=api,
        principal_resolver=lambda request: Principal("VIEWER", role="viewer") if actor["viewer"] else admin,
        mutation_guard=guard))

    @app.before_request
    def login():
        if actor.get("delay_export"):
            from flask import request
            if request.path == "/api/base-data/export":
                time.sleep(0.5)
        session["user"] = {"user_id": "FILE-TEST", "login_name": "file-test", "display_name": "文件测试员",
                           "role": "viewer" if actor["viewer"] else "admin"}

    @app.after_request
    def csrf(response):
        response.set_cookie("csrftoken", "file-test-csrf")
        return response

    @app.get("/api/me")
    def me():
        return {"user_id": "FILE-TEST", "display_name": "文件测试员",
                "role": "viewer" if actor["viewer"] else "admin",
                "permissions": ["catalog.read"] if actor["viewer"] else ["catalog.read", "catalog.write"]}

    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    yield f"http://127.0.0.1:{server.server_port}", api, actor
    server.shutdown(); thread.join(timeout=3)


def test_download_upload_four_formats_and_encrypted_xlsx(browser, file_server, tmp_path):
    url, api, actor = file_server
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "/base-data?tab=resource_types")
    page.locator("#baseDataCount").filter(has_text="1").wait_for()
    for fmt, encrypt in [("json", False), ("csv", False), ("xlsx", False), ("xml", False), ("xlsx", True)]:
        page.locator("#baseDataExportButton").click()
        page.locator("#exportFormat").select_option(fmt)
        page.locator("#exportEncrypted").set_checked(encrypt)
        with page.expect_download() as download_info:
            page.locator("#exportConfirm").click()
        download = download_info.value
        path = tmp_path / download.suggested_filename; download.save_as(path)
        if fmt == "xlsx" and not encrypt: assert path.read_bytes().startswith(b"PK")
        if encrypt: assert path.name.endswith(".xlsx.sm4")
        page.locator("#baseDataImportButton").click()
        page.locator("#importFormat").select_option(fmt)
        page.locator("#importEncrypted").set_checked(encrypt)
        page.locator("#importFile").set_input_files(path)
        page.locator("#importConfirm").click()
        page.locator("#baseDataImportModal").wait_for(state="hidden")
        assert api.airports.list_resource_types()[0].name == "燃油测试"
    page.locator("#baseDataImportButton").click()
    page.locator("#importFormat").select_option("xml")
    page.locator("#importFile").set_input_files({"name": "bad.xml", "mimeType": "application/xml", "buffer": b"<wrong/>"})
    page.locator("#importConfirm").click()
    page.locator("#importMessage").filter(has_text="XML").wait_for()
    assert api.airports.list_resource_types()[0].name == "燃油测试"
    assert not errors
    page.close()


def test_export_preserves_draft_and_viewer_permissions(browser, file_server):
    url, api, actor = file_server
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(url + "/base-data?tab=resource_types")
    page.locator("#baseDataAddButton").click()
    page.locator("#edResourceName").fill("未保存草稿")
    page.locator("#baseDataExportButton").click()
    page.locator("#exportCancel").click()
    assert page.locator("#edResourceName").input_value() == "未保存草稿"
    page.locator("#baseDataImportButton").click()
    page.locator("#baseDataConfirmCancel").click()
    assert page.locator("#edResourceName").input_value() == "未保存草稿"
    page.close()
    actor["viewer"] = True
    page = browser.new_page()
    page.goto(url + "/base-data?tab=resource_types")
    page.locator("#baseDataCount").filter(has_text="1").wait_for()
    assert page.locator("#baseDataImportButton").is_disabled()
    assert page.locator("#baseDataExportButton").is_enabled()
    page.close()


def test_export_failure_stops_download_and_unmount_cancels_request(browser, file_server, monkeypatch):
    url, api, actor = file_server
    page = browser.new_page()
    errors = []; downloads = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("download", lambda download: downloads.append(download))
    page.goto(url + "/base-data?tab=resource_types")
    page.locator("#baseDataCount").filter(has_text="1").wait_for()
    api.file_cipher = None; monkeypatch.delenv("AIRPORT_GROUP_SM4_KEY", raising=False)
    page.locator("#baseDataExportButton").click()
    page.locator("#exportEncrypted").check()
    page.locator("#exportConfirm").click()
    page.locator("#exportMessage").filter(has_text="SM4_KEY").wait_for()
    assert not downloads
    page.locator("#exportEncrypted").uncheck()
    actor["delay_export"] = True
    with page.expect_request("**/api/base-data/export"):
        page.locator("#exportConfirm").click()
    page.evaluate("async () => (await import('/static/js/modules/base-data.js')).unmount()")
    page.wait_for_timeout(1000)
    assert not downloads
    assert not errors
    page.close()
