"""Tk-independent operations. All server ownership stays in Windows lifecycle scripts."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import compiler, excel_reader, validator


class GuiServiceError(ValueError):
    """An actionable message suitable for the Chinese desktop UI."""


def launch_exit_message(code: int, stop_requested: bool) -> str | None:
    if code == 0 or stop_requested:
        return None
    return {3: '端口被其他程序占用，系统未启动。',
            6: '已有系统使用其他场景库，请先停止系统。'}.get(
                code, f'启动失败（退出码 {code}），请查看日志。')


def redact(text: str) -> str:
    values = [value for key, value in os.environ.items()
              if value and re.search(r'password|passwd|token|secret', key, re.I)]
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, '[已隐藏]')
    text = text.replace('hao123456', '[已隐藏]')
    return '\n'.join('[包含敏感字段，已隐藏]' if re.search(
        r'password|passwd|token|secret|密码|令牌|密钥', line, re.I) else line
        for line in str(text).splitlines())


class _SafeFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


@dataclass(frozen=True)
class SceneFile:
    path: Path
    created_at: datetime
    size: int


@dataclass(frozen=True)
class ExcelCheck:
    ok: bool
    issues: tuple
    counts: dict[str, int]


class GuiService:
    def __init__(self, project_root: str | Path | None = None):
        self.root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        self.scenes_dir = self.root / 'runtime/db/scenes'
        self.default_db = self.root / 'runtime/db/airport_group.sqlite3'
        self.scripts = self.root / 'deploy/windows'
        self.logger = logging.Logger('scenario_gui')
        log_dir = self.root / 'runtime/logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_dir / 'scenario_gui.log', maxBytes=2_000_000,
                                      backupCount=3, encoding='utf-8')
        handler.setFormatter(_SafeFormatter('%(asctime)s %(levelname)s %(message)s'))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def close(self):
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)

    def _excel(self, path: str | Path) -> Path:
        path = Path(path).resolve()
        if not path.is_file():
            raise GuiServiceError('Excel 文件不存在，请重新选择。')
        if path.suffix.lower() != '.xlsx':
            raise GuiServiceError('Excel 格式不支持，请选择 .xlsx 文件。')
        return path

    def validate_excel(self, path: str | Path) -> ExcelCheck:
        result = validator.validate(excel_reader.read_excel(self._excel(path)))
        counts = {}
        if result.ok:
            scene = result.scenario
            counts = {'airports': len(scene.situation.airports),
                      'aircraft_types': len(scene.aircraft_types),
                      'resources': len(scene.resource_types),
                      'missions': len(scene.situation.missions),
                      'damage_scenarios': len(scene.situation.damage_scenarios)}
        return ExcelCheck(result.ok, tuple(result.issues), counts)

    def list_scenes(self) -> list[SceneFile]:
        scenes = []
        for path in self.scenes_dir.glob('*.sqlite3'):
            if path.is_file() and not path.name.startswith('.'):
                stat = path.stat()
                scenes.append(SceneFile(path, datetime.fromtimestamp(stat.st_ctime), stat.st_size))
        return sorted(scenes, key=lambda item: (item.created_at, item.path.name), reverse=True)

    def build_scene(self, path: str | Path) -> tuple[Path, list[SceneFile]]:
        database = compiler.build(self._excel(path), output_dir=self.scenes_dir)
        return database, self.list_scenes()

    def _scene(self, path: str | Path) -> Path:
        path = Path(path).resolve()
        if path == self.default_db.resolve() or (path.exists() and self.default_db.exists()
                                                 and path.samefile(self.default_db)):
            raise GuiServiceError('默认数据库不能作为场景库启动。请使用“启动系统”。')
        if path.parent != self.scenes_dir.resolve() or path.suffix.lower() != '.sqlite3' or not path.is_file():
            raise GuiServiceError('请选择 runtime/db/scenes 中已有的场景数据库。')
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
                if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise GuiServiceError('场景数据库损坏，请重新生成场景库。')
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'users', 'situations', 'airports', 'mission_records'} <= tables:
                    raise GuiServiceError('该文件不是本系统的场景数据库，请重新生成。')
        except sqlite3.Error as exc:
            raise GuiServiceError('场景数据库无法读取或已损坏，请重新生成。') from exc
        return path

    def _powershell(self) -> str:
        return str(Path(os.environ.get('SystemRoot', r'C:\Windows')) /
                   'System32/WindowsPowerShell/v1.0/powershell.exe')

    def _popen(self, arguments: list[str]):
        return subprocess.Popen(arguments, cwd=self.root, env=os.environ.copy(),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace',
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def _command(self, name: str, *arguments: str):
        script = self.scripts / name
        if not script.is_file():
            raise GuiServiceError(f'启动脚本不存在：{name}。')
        values = [str(script), *arguments]
        # Batch expansion has additional metacharacters even when called from PowerShell.
        if any(re.search(r'[\x00-\x1f"%!?&|<>^]', value) for value in values):
            raise GuiServiceError('部署目录或文件名包含命令行特殊字符，请使用普通目录和文件名。')
        quoted = ' '.join("'" + value.replace("'", "''") + "'" for value in values)
        command = '& ' + quoted + '; exit $LASTEXITCODE'
        return self._popen([self._powershell(), '-NoLogo', '-NoProfile', '-ExecutionPolicy',
                            'Bypass', '-Command', command])

    def _idle(self):
        status = self.status()
        if status['port_occupied']:
            raise GuiServiceError('系统端口已被其他程序占用，请先释放端口。')
        if status['running'] or status.get('worker_pids'):
            raise GuiServiceError('系统已经启动或正在启动，请先停止系统再切换场景。')

    def start_default(self):
        self._idle()
        return self._command('start.cmd')

    def start_scene(self, path: str | Path):
        database = self._scene(path)
        self._idle()
        return self._command('start_scene.cmd', str(database))

    def stop(self):
        process = self._command('stop.cmd')
        self._complete(process, '停止系统失败，请查看日志。')

    def _complete(self, process, message: str):
        try:
            output, _ = process.communicate(timeout=25)
        except subprocess.TimeoutExpired as exc:
            # Only terminate this short-lived status/stop command, never Web/Worker.
            process.kill()
            process.communicate()
            raise GuiServiceError('状态或停止脚本响应超时，请稍后重试。') from exc
        if process.returncode:
            if output:
                self.logger.error(redact(output.strip()))
            raise GuiServiceError(message)
        return output

    def status(self) -> dict:
        script = self.scripts / 'web_lifecycle.ps1'
        if not script.is_file():
            raise GuiServiceError('系统状态脚本不存在。')
        process = self._popen([self._powershell(), '-NoLogo', '-NoProfile', '-ExecutionPolicy',
                               'Bypass', '-File', str(script), '-Action', 'status', '-StatusJson'])
        output = self._complete(process, '无法读取系统状态，请查看日志。')
        try:
            status = json.loads(output.lstrip('\ufeff').strip())
            if not isinstance(status, dict) or any(key not in status for key in
                                                 ('running', 'web_pids', 'worker_pids', 'port_occupied')):
                raise ValueError('invalid lifecycle status')
            if not all(isinstance(status[key], bool) for key in ('running', 'port_occupied')):
                raise ValueError('invalid lifecycle booleans')
            for key in ('web_pids', 'worker_pids'):
                if not isinstance(status[key], list) or any(type(pid) is not int or pid <= 0 for pid in status[key]):
                    raise ValueError('invalid lifecycle PIDs')
            return status
        except (ValueError, TypeError) as exc:
            raise GuiServiceError('系统状态返回格式不正确，请检查启动脚本版本。') from exc
