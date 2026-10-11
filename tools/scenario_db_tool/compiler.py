"""Build a fresh database through local migrations and domain repositories."""
from __future__ import annotations

import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

from backend.auth.passwords import verify_password
from backend.domain.airport import AirportBase
from backend.domain.airport_operations import AirportOperationalProfile
from backend.domain.catalog import AircraftType, ResourceType, AircraftResourceRequirement
from backend.domain.mission import Mission
from backend.domain.situation import Situation
from backend.storage import database
from backend.storage.airport_repository import AirportRepository
from backend.storage.mission_repository import MissionRepository
from backend.storage.situation_repository import SituationRepository
from backend.storage.user_repository import UserRepository

from .excel_reader import read_excel
from .models import CompiledScenario, ScenarioToolError, ValidationIssue
from .validator import validate

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / 'runtime/db/airport_group.sqlite3'
SCENES_DIR = PROJECT_ROOT / 'runtime/db/scenes'


def _verify(path: Path, expected: CompiledScenario) -> None:
    # Explicit read-only SQLite inspection, closed before final publication.
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        integrity = conn.execute('PRAGMA integrity_check').fetchall()
        if integrity != [('ok',)]:
            raise ValueError(f'integrity_check failed: {integrity}')
        foreign_keys = conn.execute('PRAGMA foreign_key_check').fetchall()
        if foreign_keys:
            raise ValueError(f'foreign_key_check failed: {foreign_keys}')
    repo = AirportRepository(path)
    def same_catalog(actual, wanted, factory, key):
        actual = [factory.from_mapping(item.to_dict()).to_dict() for item in actual]
        wanted = [item.to_dict() for item in wanted]
        if sorted(actual, key=key) != sorted(wanted, key=key):
            raise ValueError(f'{factory.__name__} repository roundtrip differs from input')
    same_catalog(repo.list_aircraft_types(), expected.aircraft_types, AircraftType, lambda x: x['aircraft_type_id'])
    same_catalog(repo.list_resource_types(), expected.resource_types, ResourceType, lambda x: x['resource_type_id'])
    same_catalog(repo.list_aircraft_resource_requirements(), expected.requirements,
                 AircraftResourceRequirement, lambda x: (x['aircraft_type_id'], x['resource_type_id']))
    airports = repo.list_airports()
    if len(airports) != len(expected.situation.airports):
        raise ValueError('airport count differs from input')
    for item in expected.situation.airports:
        base = AirportBase.from_mapping(repo.get_airport(item.airport_id).to_dict())
        profile = AirportOperationalProfile.from_mapping(repo.get_operational_profile(item.airport_id).to_dict())
        if base != item.airport or profile != item.operational_profile:
            raise ValueError(f'airport/profile roundtrip differs: {item.airport_id}')
    missions = MissionRepository(path).list()
    same_catalog(missions, expected.situation.missions, Mission, lambda x: x['mission_id'])
    situation = SituationRepository(path).get_situation(expected.situation.situation_id)
    if situation is None:
        raise ValueError('generated Situation is missing')
    restored = Situation.from_mapping(situation.to_dict(), derive_configuration=False)
    if restored.content_hash() != expected.situation.content_hash():
        raise ValueError('Situation repository/domain roundtrip differs from input')
    # authenticate() updates last_login_at. Verification uses the same password
    # verifier without adding writes after the integrity checks.
    user = UserRepository(path).get_by_login_name('admin', include_password_hash=True)
    if user['role'] != 'admin' or not verify_password('hao123456', user['password_hash']):
        raise ValueError('bootstrap admin verification failed')


def _rename_exclusive(source: Path, destination: Path) -> None:
    if os.name == 'nt':
        os.rename(source, destination)  # Windows never replaces an existing destination.
        return
    # POSIX os.rename overwrites. Use the platform's atomic no-replace rename.
    import ctypes
    import sys
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
    elif sys.platform == 'darwin' and hasattr(libc, 'renamex_np'):
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(source), os.fsencode(destination), 4)  # RENAME_EXCL
    else:
        raise OSError('This platform does not provide atomic no-replace rename')
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def _filename_stem(scene_id: str) -> str:
    # Stable domain IDs may contain ':'; filenames must not become Windows ADS paths.
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', scene_id)
    return safe if safe.startswith('SCN_') else 'SCN_' + safe


def _publish(temp: Path, output_dir: Path, scene_id: str) -> Path:
    """Never overwrite, including competing builds. Publish only a verified database."""
    stem = _filename_stem(scene_id)
    while True:
        suffix = '' if not (output_dir / f'{stem}.sqlite3').exists() else (
            '_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:12])
        final = output_dir / f'{stem}{suffix}.sqlite3'
        try:
            _rename_exclusive(temp, final)
            return final
        except FileExistsError:
            # The destination appeared after our existence check; try a fresh name.
            continue


def build(excel_path: str | Path, *, output_dir: str | Path | None = None) -> Path:
    result = validate(read_excel(excel_path))
    if not result.ok:
        raise ScenarioToolError(result.issues)
    scene = result.scenario
    destination = Path(output_dir) if output_dir is not None else SCENES_DIR
    destination = destination.resolve()
    # Only scene filenames are generated; no API accepts a target database path.
    if destination == DEFAULT_DB.resolve() or destination.is_file():
        raise ScenarioToolError([ValidationIssue('<build>', 0, 'output_dir', '输出必须为独立场景目录')])
    temp = None
    try:
        destination.mkdir(parents=True, exist_ok=True)
        # Reserve a unique file in the final directory; migrations start from empty.
        temp = destination / f'.{_filename_stem(scene.situation.situation_id)}.{uuid.uuid4().hex}.tmp.sqlite3'
        if temp.resolve() == DEFAULT_DB.resolve():
            raise ValueError('refusing to write default database')
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        database.initialize_database(temp)
        admin = UserRepository(temp).bootstrap_admin(login_name='admin', password='hao123456')
        if admin is None:
            raise ValueError('fresh database unexpectedly contains users')
        repo = AirportRepository(temp)
        for item in scene.aircraft_types:
            repo.save_aircraft_type(item)
        for item in scene.resource_types:
            repo.save_resource_type(item)
        for item in scene.requirements:
            repo.save_aircraft_resource_requirement(item)
        for item in scene.situation.airports:
            repo.save_airport(item.airport)
            repo.save_operational_profile(item.operational_profile)
        mission_repo = MissionRepository(temp)
        for item in scene.situation.missions:
            mission_repo.save(item)
        SituationRepository(temp).save_situation(scene.situation, owner_user_id='admin')
        _verify(temp, scene)
        # Windows FlushFileBuffers requires a writable file descriptor.
        with temp.open('rb+') as handle:
            os.fsync(handle.fileno())
        return _publish(temp, destination, scene.situation.situation_id)
    except ScenarioToolError:
        raise
    except Exception as exc:
        raise ScenarioToolError([ValidationIssue('<build>', 0, 'database', str(exc))]) from exc
    finally:
        if temp is not None:
            for suffix in ('', '-journal', '-wal', '-shm'):
                Path(str(temp) + suffix).unlink(missing_ok=True)
