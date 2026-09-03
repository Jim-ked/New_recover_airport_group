from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flask import Flask

from backend.web.flask_tiles import create_tile_blueprint, resolve_local_tile


SOURCE_ZOOM_CONTRACTS = {
    "world": ("tiles_world", 2, 7),
    "eastasia": ("tiles_eastasia", 8, 11),
    "china_east": ("tiles_china_east", 12, 13),
    "japan": ("tiles_japan", 12, 14),
    "korea": ("tiles_korea", 12, 14),
    "taiwan": ("tiles_taiwan", 12, 15),
}


class LocalTileResolutionTests(unittest.TestCase):
    def _tile(self, root: Path, source: str, relative: str, body: bytes = b"tile") -> Path:
        path = root / source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return path

    def _app(self, root: Path) -> Flask:
        app = Flask(__name__)
        app.register_blueprint(create_tile_blueprint(tile_root=root))
        return app

    def _get(self, client, url: str) -> tuple[int, bytes]:
        response = client.get(url)
        result = (response.status_code, response.get_data())
        response.close()
        return result

    def test_all_six_explicit_sources_map_to_their_own_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = {}
            for source, (folder, minimum, maximum) in SOURCE_ZOOM_CONTRACTS.items():
                for zoom in (minimum, maximum):
                    body = f"{source}:{zoom}".encode()
                    self._tile(root, folder, f"{zoom}/1/1.jpg", body)
                    expected[f"/tiles/{source}/{zoom}/1/1.jpg"] = body

            client = self._app(root).test_client()
            responses = {url: self._get(client, url) for url in expected}

        for url, body in expected.items():
            with self.subTest(url=url):
                self.assertEqual((200, body), responses[url])

    def test_unknown_source_returns_404(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self._app(Path(directory)).test_client()
            status, _ = self._get(client, "/tiles/unknown/8/1/1.jpg")

        self.assertEqual(404, status)

    def test_explicit_source_route_does_not_fall_back_to_another_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tile(root, "tiles_world", "8/1/1.jpg", b"world")
            status, _ = self._get(self._app(root).test_client(), "/tiles/eastasia/8/1/1.jpg")

        self.assertEqual(404, status)

    def test_taiwan_request_never_returns_same_xyz_from_china_east(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tile(root, "tiles_china_east", "12/3400/1700.jpg", b"china-east")
            self._tile(root, "tiles_taiwan", "12/3400/1700.jpg", b"taiwan")
            status, body = self._get(self._app(root).test_client(), "/tiles/taiwan/12/3400/1700.jpg")

        self.assertEqual(200, status)
        self.assertEqual(b"taiwan", body)

    def test_explicit_route_rejects_invalid_extension_and_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tile(root, "tiles_world", "7/128/1.jpg")
            client = self._app(root).test_client()

            invalid_extension, _ = self._get(client, "/tiles/world/7/1/1.exe")
            invalid_coordinate, _ = self._get(client, "/tiles/world/7/128/1.jpg")

        self.assertEqual(404, invalid_extension)
        self.assertEqual(404, invalid_coordinate)

    def test_legacy_route_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tile(root, "tiles_world", "7/101/55.jpg", b"legacy-world")
            status, body = self._get(self._app(root).test_client(), "/tiles/7/101/55.jpg")

        self.assertEqual(200, status)
        self.assertEqual(b"legacy-world", body)

    def test_legacy_resolution_prefers_real_regional_zoom_over_world(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regional = self._tile(root, "tiles_china_east", "12/3265/1643.jpg")
            self._tile(root, "tiles_world", "12/3265/1643.jpg")

            resolved = resolve_local_tile(root, z=12, x=3265, y=1643, ext="jpg")

        self.assertEqual((regional.parent, regional.name), resolved)

    def test_existing_tiles_world_configuration_can_serve_explicit_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tile(root, "tiles_taiwan", "15/1/1.jpg", b"taiwan")
            app = Flask(__name__)
            app.register_blueprint(create_tile_blueprint(tile_root=root / "tiles_world"))

            status, _ = self._get(app.test_client(), "/tiles/taiwan/15/1/1.jpg")

        self.assertEqual(200, status)

    def test_missing_tile_does_not_resolve_to_an_unrelated_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tile(root, "tiles_japan", "12/1/1.jpg")
            self.assertIsNone(resolve_local_tile(root, z=12, x=2, y=2, ext="jpg"))


if __name__ == "__main__":
    unittest.main()
