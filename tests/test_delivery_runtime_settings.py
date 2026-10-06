from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.settings import AppSettings


ROOT = Path(__file__).resolve().parents[1]


class DeliveryRuntimeSettingsTests(unittest.TestCase):
    def test_application_defaults_database_to_project_runtime(self):
        settings = AppSettings.from_environment({}, project_root=ROOT)

        self.assertEqual((ROOT / "runtime" / "db" / "airport_group.sqlite3").resolve(), settings.db_path)

    @unittest.skipUnless(os.name == "nt", "Windows startup contract")
    def test_windows_loader_database_and_port_configuration_priority(self):
        cases = (
            ("inherited port without local override", None, "1234"),
            (
                "explicit local port override",
                (
                    "$env:AIRPORT_GROUP_DB_PATH='runtime\\db\\local_override.sqlite3'\n"
                    "$env:AIRPORT_GROUP_PORT='4321'\n"
                ),
                "4321",
            ),
        )

        for case_name, local_environment, expected_port in cases:
            with self.subTest(case_name):
                lines, expected_database = self._run_windows_loader_in_isolated_project(
                    local_environment=local_environment
                )

                self.assertEqual(expected_database, lines[-2])
                self.assertEqual(expected_port, lines[-1])

    @staticmethod
    def _run_windows_loader_in_isolated_project(
        *, local_environment: str | None = None
    ) -> tuple[list[str], str]:
        source_loader = ROOT / "deploy" / "windows" / "load_environment.ps1"

        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            loader = project_root / "deploy" / "windows" / "load_environment.ps1"
            loader.parent.mkdir(parents=True)
            shutil.copy2(source_loader, loader)

            if local_environment is not None:
                (project_root / ".env.local.ps1").write_text(
                    local_environment,
                    encoding="utf-8",
                )

            command = (
                "$env:AIRPORT_GROUP_DB_PATH='runtime\\db\\validation_work.sqlite3';"
                "$env:AIRPORT_GROUP_PORT='1234';"
                f". '{loader}';"
                "Write-Output $env:AIRPORT_GROUP_DB_PATH;"
                "Write-Output $env:AIRPORT_GROUP_PORT"
            )
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )
            lines = [
                line.strip()
                for line in completed.stdout.splitlines()
                if line.strip()
            ]
            expected_database = str(
                (project_root / "runtime" / "db" / "airport_group.sqlite3").resolve()
            )

        return lines, expected_database


if __name__ == "__main__":
    unittest.main()
