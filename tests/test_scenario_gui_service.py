import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.scenario_db_tool.gui_service import GuiService, GuiServiceError, launch_exit_message, redact
from tools.scenario_db_tool.models import ValidationResult


class GuiServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scenes = self.root / 'runtime/db/scenes'
        self.scenes.mkdir(parents=True)
        self.default = self.root / 'runtime/db/airport_group.sqlite3'
        self.default.write_bytes(b'default database must not change')
        self.env_file = self.root / '.env.local.ps1'
        self.env_file.write_text('configuration must not change')
        scripts = self.root / 'deploy/windows'
        scripts.mkdir(parents=True)
        for name in ('start.cmd', 'start_scene.cmd', 'stop.cmd', 'web_lifecycle.ps1'):
            (scripts / name).write_text('placeholder')
        self.service = GuiService(self.root)
        self.addCleanup(self.service.close)
        self.excel = self.root / '场景.xlsx'
        self.excel.touch()

    def scene(self, name='SCN_TEST.sqlite3'):
        path = self.scenes / name
        with closing(sqlite3.connect(path)) as conn:
            for name in ('users', 'situations', 'airports', 'mission_records'):
                conn.execute(f'CREATE TABLE {name} (value TEXT)')
            conn.commit()
        return path

    def test_excel_validation_calls_existing_reader_and_validator(self):
        result = ValidationResult([], SimpleNamespace(
            aircraft_types=(1, 2), resource_types=(1,),
            situation=SimpleNamespace(airports=(1, 2, 3), missions=(1,), damage_scenarios=(1,))))
        with patch('tools.scenario_db_tool.gui_service.excel_reader.read_excel', return_value='cells') as reader, \
             patch('tools.scenario_db_tool.gui_service.validator.validate', return_value=result) as validator:
            actual = self.service.validate_excel(self.excel)
        reader.assert_called_once_with(self.excel)
        validator.assert_called_once_with('cells')
        self.assertEqual(actual.counts, {'airports': 3, 'aircraft_types': 2, 'resources': 1, 'missions': 1, 'damage_scenarios': 1})

    def test_missing_excel_and_wrong_format(self):
        for path in (self.root / 'missing.xlsx', self.default):
            with self.subTest(path=path), self.assertRaises(GuiServiceError):
                self.service.validate_excel(path)

    def test_build_uses_existing_compiler_and_refreshes_scene_list(self):
        path = self.scene()
        with patch('tools.scenario_db_tool.gui_service.compiler.build', return_value=path) as build:
            actual, scenes = self.service.build_scene(self.excel)
        build.assert_called_once_with(self.excel, output_dir=self.scenes)
        self.assertEqual(actual, path)
        self.assertEqual([item.path for item in scenes], [path])
        self.assertEqual(scenes[0].size, path.stat().st_size)

    def test_validation_failure_returns_existing_issues(self):
        result = ValidationResult(['invalid workbook'])
        with patch('tools.scenario_db_tool.gui_service.excel_reader.read_excel'), \
             patch('tools.scenario_db_tool.gui_service.validator.validate', return_value=result):
            actual = self.service.validate_excel(self.excel)
        self.assertFalse(actual.ok)
        self.assertEqual(actual.issues, ('invalid workbook',))
        self.assertEqual(actual.counts, {})

    def test_build_failure_does_not_refresh_or_modify_protected_files(self):
        before = self.default.read_bytes(), self.env_file.read_bytes()
        with patch('tools.scenario_db_tool.gui_service.compiler.build', side_effect=ValueError('build failed')), \
             patch.object(self.service, 'list_scenes') as listing:
            with self.assertRaisesRegex(ValueError, 'build failed'):
                self.service.build_scene(self.excel)
        listing.assert_not_called()
        self.assertEqual(before, (self.default.read_bytes(), self.env_file.read_bytes()))

    def test_default_start_calls_start_cmd_with_copied_environment(self):
        process = Mock()
        with patch.object(self.service, 'status', return_value={'running': False, 'port_occupied': False}), \
             patch('tools.scenario_db_tool.gui_service.subprocess.Popen', return_value=process) as popen:
            self.assertIs(self.service.start_default(), process)
        args, kwargs = popen.call_args
        self.assertIn('start.cmd', str(args))
        self.assertEqual(kwargs['cwd'], self.root)
        self.assertIsNot(kwargs['env'], os.environ)
        self.assertFalse(kwargs.get('shell', False))
        self.assertNotIn('-m backend', str(args))

    def test_scene_start_passes_absolute_database_to_script(self):
        path = self.scene('含空格的 场景.sqlite3')
        with patch.object(self.service, 'status', return_value={'running': False, 'port_occupied': False}), \
             patch('tools.scenario_db_tool.gui_service.subprocess.Popen') as popen:
            self.service.start_scene(path)
        arguments = str(popen.call_args.args)
        self.assertIn(str(path.resolve()).replace('\\', '\\\\'), arguments)
        self.assertIn('start_scene.cmd', arguments)

    def test_corrupt_database_and_default_database_rejected(self):
        corrupt = self.scenes / 'bad.sqlite3'
        corrupt.write_bytes(b'not a database')
        for path in (corrupt, self.default):
            with self.subTest(path=path), patch('tools.scenario_db_tool.gui_service.subprocess.Popen') as popen:
                with self.assertRaises(GuiServiceError):
                    self.service.start_scene(path)
                popen.assert_not_called()

    def test_protected_files_and_parent_environment_unchanged(self):
        path = self.scene()
        before = [(hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in (self.default, self.env_file)]
        environment = dict(os.environ)
        process = Mock(returncode=0)
        process.communicate.return_value = ('stopped', '')
        with patch.object(self.service, 'status', return_value={'running': False, 'port_occupied': False}), \
             patch('tools.scenario_db_tool.gui_service.subprocess.Popen', return_value=process), \
             patch('tools.scenario_db_tool.gui_service.compiler.build', return_value=path):
            self.service.build_scene(self.excel)
            self.service.start_default()
            self.service.start_scene(path)
            self.service.stop()
            self.service.list_scenes()
        self.assertEqual(before, [(hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in (self.default, self.env_file)])
        self.assertEqual(environment, dict(os.environ))

    def test_port_occupied_and_running_system_prevent_start(self):
        for status in ({'running': False, 'port_occupied': True}, {'running': True, 'port_occupied': False}):
            with patch.object(self.service, 'status', return_value=status), self.assertRaises(GuiServiceError):
                self.service.start_default()

    def test_status_calls_lifecycle_and_parses_json(self):
        process = Mock(returncode=0)
        payload = {'running': True, 'web_pids': [11], 'worker_pids': [22], 'port_occupied': False}
        process.communicate.return_value = (json.dumps(payload), '')
        with patch('tools.scenario_db_tool.gui_service.subprocess.Popen', return_value=process) as popen:
            self.assertEqual(self.service.status(), payload)
        self.assertIn('-StatusJson', str(popen.call_args.args))

    def test_stop_failure_is_reported(self):
        process = Mock(returncode=3)
        process.communicate.return_value = ('port occupied', '')
        with patch('tools.scenario_db_tool.gui_service.subprocess.Popen', return_value=process), self.assertRaises(GuiServiceError):
            self.service.stop()

    def test_requested_stop_is_not_reported_as_launch_failure(self):
        self.assertIsNone(launch_exit_message(4294967295, stop_requested=True))
        self.assertIsNone(launch_exit_message(0, stop_requested=False))
        self.assertIn('端口', launch_exit_message(3, stop_requested=False))
        self.assertIn('其他场景库', launch_exit_message(6, stop_requested=False))
        self.assertIn('启动失败', launch_exit_message(2, stop_requested=False))

    def test_log_redacts_credentials(self):
        text = redact('password=example token:abc secret_key="abc def" AIRPORT_GROUP_SECRET_KEY=hidden')
        self.assertNotIn('example', text)
        self.assertNotIn('abc', text)
        self.assertNotIn('hidden', text)

    def test_temporary_compiler_database_is_not_listed(self):
        self.scene('.SCN_TEST.unique.tmp.sqlite3')
        self.assertEqual(self.service.list_scenes(), [])

    def test_status_rejects_untrusted_pid_and_boolean_types(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'running': 'false', 'web_pids': ['11'],
                                                       'worker_pids': [], 'port_occupied': False}), '')
        with patch('tools.scenario_db_tool.gui_service.subprocess.Popen', return_value=process):
            with self.assertRaises(GuiServiceError):
                self.service.status()

    @unittest.skipUnless(os.name == 'nt', 'Windows lifecycle adapter')
    def test_lifecycle_stop_tolerates_exit_but_rejects_real_failure_and_pid_reuse(self):
        source = Path(__file__).resolve().parents[1] / 'deploy/windows/web_lifecycle.ps1'
        command = """$ast = [System.Management.Automation.Language.Parser]::ParseFile('%s', [ref]$null, [ref]$null)
$definition = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Stop-ProjectProcess'}, $true)
. ([scriptblock]::Create($definition.Extent.Text))
$record = [pscustomobject]@{ProcessId=123; CreationDate='original'}
function Test-ProjectWebProcess { return $true }
function Test-ProjectWorkerProcess { return $false }
function Stop-Process { throw 'simulated termination error' }
$script:reads = 0
function Get-ProcessRecord { $script:reads++; if ($script:reads -eq 1) { return $record }; return $null }
Stop-ProjectProcess $record
function Get-ProcessRecord { return $record }
$failed = $false
try { Stop-ProjectProcess $record } catch { $failed = $true }
if (-not $failed) { throw 'termination failure was swallowed' }
function Get-ProcessRecord { return [pscustomobject]@{ProcessId=123; CreationDate='reused'} }
function Stop-Process { throw 'reused PID must not be stopped' }
Stop-ProjectProcess $record
Write-Output 'PASS'
""" % str(source).replace("'", "''")
        process = self.service._popen([self.service._powershell(), '-NoProfile', '-Command', command])
        self.assertEqual(self.service._complete(process, 'cleanup test failed').strip(), 'PASS')

    @unittest.skipUnless(os.name == 'nt', 'Windows lifecycle adapter')
    def test_scene_parameter_overrides_local_default_without_writing_config(self):
        import shutil
        source = Path(__file__).resolve().parents[1] / 'deploy/windows'
        for name in ('web_lifecycle.ps1', 'load_environment.ps1'):
            shutil.copy2(source / name, self.root / 'deploy/windows' / name)
        self.env_file.write_text("$env:AIRPORT_GROUP_DB_PATH='ignored.sqlite3'\n$env:AIRPORT_GROUP_PORT='18993'\n")
        before = self.env_file.read_bytes()
        path = self.scene()
        state = self.root / 'runtime/logs/web_lifecycle_state.json'
        state.write_text(json.dumps({'db_path': 'stale.sqlite3', 'owner_pid': 2147483647,
                                     'owner_created_at': 'invalid'}))
        # -Action status never initializes a backend/database or starts a process.
        process = self.service._popen([self.service._powershell(), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                       '-File', str(self.root / 'deploy/windows/web_lifecycle.ps1'),
                                       '-Action', 'status', '-StatusJson', '-SceneDatabase', str(path)])
        status = json.loads(self.service._complete(process, 'status failed'))
        self.assertEqual(status['db_path'], str(path))
        self.assertEqual(self.env_file.read_bytes(), before)
        # The isolated root has no project processes; stop cleans stale metadata
        # without touching other roots or creating/opening its canonical database.
        process = self.service._popen([self.service._powershell(), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                       '-File', str(self.root / 'deploy/windows/web_lifecycle.ps1'),
                                       '-Action', 'stop'])
        self.service._complete(process, 'stop failed')
        self.assertFalse(state.exists())


if __name__ == '__main__':
    unittest.main()
