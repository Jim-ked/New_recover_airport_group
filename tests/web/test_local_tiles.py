from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.web.flask_tiles import create_tile_blueprint, resolve_local_tile


class LocalTileResolutionTests(unittest.TestCase):
    def _tile(self, root: Path, source: str, relative: str) -> Path:
        path = root / source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"tile")
        return path

    def test_regional_tile_wins_over_world_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regional = self._tile(root, "tiles_china_east", "7/101/55.jpg")
            self._tile(root, "tiles_world", "7/101/55.jpg")

            resolved = resolve_local_tile(root, z=7, x=101, y=55, ext="jpg")

        self.assertEqual((regional.parent, regional.name), resolved)

    def test_world_tile_is_used_after_regional_sources_miss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world = self._tile(root, "tiles_world", "4/12/7.jpg")

            resolved = resolve_local_tile(root, z=4, x=12, y=7, ext="jpg")

        self.assertEqual((world.parent, world.name), resolved)

    def test_existing_tiles_world_configuration_can_serve_regional_tiles(self) -> None:
        from flask import Flask
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tile(root, "tiles_world", "4/12/7.jpg")
            self._tile(root, "tiles_china_east", "12/3265/1643.jpg")
            app = Flask(__name__)
            app.register_blueprint(create_tile_blueprint(tile_root=root / "tiles_world"))

            response = app.test_client().get("/tiles/12/3265/1643.jpg")
            response.close()

        self.assertEqual(200, response.status_code)

    def test_missing_tile_does_not_resolve_to_an_unrelated_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._tile(root, "tiles_japan", "12/1/1.jpg")
            self.assertIsNone(resolve_local_tile(root, z=12, x=2, y=2, ext="jpg"))


if __name__ == "__main__":
    unittest.main()
