from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "deploy" / "launcher" / "AirportGroupLauncher.cs"
BUILD_SCRIPT = ROOT / "deploy" / "launcher" / "build.ps1"
LAUNCHER = ROOT / "AirportGroupLauncher.exe"


class WindowsLauncherContractTests(unittest.TestCase):
    def test_launcher_source_has_only_the_required_ui_and_relative_paths(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")

        self.assertIn("机场群顽存能力模拟仿真验证系统", source)
        self.assertIn('Text = "打开系统"', source)
        self.assertIn('Text = "关闭系统"', source)
        self.assertIn('Text = "退出"', source)
        self.assertIn("Application.ExecutablePath", source)
        self.assertIn('Path.Combine(_deploymentRoot, "compose.yaml")', source)
        self.assertNotIn("D:\\\\Data", source)
        self.assertNotIn("C:\\\\Users", source)

    def test_launcher_uses_existing_health_and_exact_safe_compose_commands(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")

        self.assertIn('SystemUrl = "http://127.0.0.1:8080"', source)
        self.assertIn('HealthUrl = SystemUrl + "/login"', source)
        self.assertIn('RunDockerAsync("compose up -d"', source)
        self.assertIn('RunDockerAsync("compose down"', source)
        self.assertNotIn("compose down -v", source)
        self.assertIn('RunDockerAsync("compose version"', source)
        self.assertIn('RunDockerAsync("info --format', source)
        self.assertIn("未检测到可用的 Docker 运行环境，请先启动 Docker Desktop。", source)
        self.assertIn("for (int attempt = 0; attempt < 60; attempt++)", source)

    def test_launcher_hides_docker_processes_and_opens_default_browser(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")

        self.assertIn("CreateNoWindow = true", source)
        self.assertIn("UseShellExecute = false", source)
        self.assertIn("UseShellExecute = true", source)
        self.assertIn("FileName = SystemUrl", source)
        self.assertIn("WorkingDirectory = _deploymentRoot", source)

    def test_launcher_exit_never_runs_compose_down(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")

        exit_handler = source.split("private void ExitButtonClick", 1)[1].split("}", 1)[0]
        self.assertIn("Close();", exit_handler)
        self.assertNotIn("RunDockerAsync", exit_handler)

    def test_build_uses_windows_framework_compiler_without_packages(self) -> None:
        build = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("Framework64\\v4.0.30319\\csc.exe", build)
        self.assertIn("/target:winexe", build)
        self.assertIn("System.Windows.Forms.dll", build)
        self.assertIn("AirportGroupLauncher.exe", build)
        self.assertNotIn("nuget", build.lower())

    def test_compiled_launcher_exists(self) -> None:
        self.assertTrue(LAUNCHER.is_file())
        self.assertGreater(LAUNCHER.stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
