from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DockerDeliveryContractTest(unittest.TestCase):
    def _read(self, relative: str) -> str:
        path = ROOT / relative
        self.assertTrue(path.is_file(), f"missing Docker delivery file: {relative}")
        return path.read_text(encoding="utf-8")

    def test_one_image_contains_only_program_and_fixed_resources(self) -> None:
        dockerfile = self._read("Dockerfile")

        self.assertIn("pip install --no-cache-dir -r requirements-solver.txt", dockerfile)
        for expected in (
            "COPY backend/ ./backend/",
            "COPY frontend/ ./frontend/",
            "COPY original_algorithm_overlay/ ./original_algorithm_overlay/",
            "COPY resources/seed/ ./resources/seed/",
            "COPY resources/migrations/ ./resources/migrations/",
        ):
            self.assertIn(expected, dockerfile)

        self.assertNotRegex(dockerfile, r"(?m)^COPY\s+\.\s")
        self.assertNotRegex(dockerfile, r"(?m)^COPY\s+runtime(?:/|\s)")
        self.assertNotRegex(dockerfile, r"(?m)^COPY\s+resources/tiles(?:/|\s)")
        self.assertNotIn("EXPOSE", dockerfile)

    def test_compose_has_only_web_and_worker_using_the_same_image(self) -> None:
        compose = self._read("compose.yaml")
        services = re.search(r"(?ms)^services:\s*\n(?P<body>.*?)(?=^[^\s#]|\Z)", compose)
        self.assertIsNotNone(services)
        body = services.group("body")
        names = re.findall(r"(?m)^  ([a-zA-Z0-9_-]+):\s*$", body)
        self.assertEqual(["web", "worker"], names)
        self.assertEqual(2, body.count("image: airport-group:delivery"))
        self.assertEqual(1, body.count("build:"))
        self.assertIn("command: [\"python\", \"-m\", \"backend\", \"serve\"]", body)
        self.assertIn("command: [\"python\", \"-m\", \"backend\", \"worker\"]", body)

    def test_runtime_is_writable_for_both_and_tiles_are_web_only_read_only(self) -> None:
        compose = self._read("compose.yaml")
        web, worker = compose.split("  worker:", maxsplit=1)

        runtime_mount = "./runtime:/app/runtime"
        tile_mount = "./resources/tiles:/app/resources/tiles:ro"
        self.assertEqual(1, web.count(runtime_mount))
        self.assertEqual(1, worker.count(runtime_mount))
        self.assertEqual(1, web.count(tile_mount))
        self.assertNotIn(tile_mount, worker)
        self.assertIn('"8080:8080"', web)
        self.assertNotIn("ports:", worker)
        self.assertIn("AIRPORT_GROUP_DB_PATH: /app/runtime/db/airport_group.sqlite3", web)
        self.assertIn("AIRPORT_GROUP_TILE_ROOT: /app/resources/tiles", web)

    def test_build_context_excludes_runtime_tiles_and_local_validation_assets(self) -> None:
        dockerignore = self._read(".dockerignore")
        patterns = {
            line.strip()
            for line in dockerignore.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        for expected in (
            "runtime/",
            "resources/tiles/",
            "resources/experiments/",
            "docs/audits/",
            "output/",
            "tests/",
            "tools/",
            ".env*",
            "*.sqlite3",
            "*.sqlite3-*",
            "*.pdf",
            "**/__pycache__/",
            "*.py[cod]",
        ):
            self.assertIn(expected, patterns)

        self.assertNotIn("frontend/static/", patterns)
        self.assertNotIn("original_algorithm_overlay/", patterns)
        self.assertNotIn("resources/seed/", patterns)
        self.assertNotIn("resources/migrations/", patterns)


if __name__ == "__main__":
    unittest.main()
