"""Package local files, never Git exports or an installed development environment."""
from __future__ import annotations

import argparse
import email
import hashlib
import json
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def fingerprint(path):
    stat = path.stat()
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'sha256': digest, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build(root, assets, output):
    specification = json.loads((root / 'deploy/native/package_manifest.json').read_text(encoding='utf-8'))
    name = 'AirportGroupSystem_Native_V' + specification['version']
    stage, archive = output / name, output / (name + '.zip')
    if stage.exists() or archive.exists():
        raise FileExistsError('Refusing to overwrite an existing release: ' + str(stage))
    excluded = set(specification['excluded_directories'])
    def allowed(relative):
        return not (set(relative.parts) & excluded) and relative.suffix not in specification['excluded_suffixes']
    selected = {}
    for directory in specification['source_directories']:
        source = root / directory
        if not source.is_dir():
            raise FileNotFoundError(source)
        for path in source.rglob('*'):
            relative = path.relative_to(root)
            if path.is_file() and allowed(relative):
                selected[relative] = path
    for relative in specification['source_files']:
        selected[Path(relative)] = root / relative
    databases = [root / p for p in specification['database_files']] + sorted(root.glob(specification['scene_glob']))
    protected_before = {p.relative_to(root).as_posix(): fingerprint(p) for p in databases}
    for path in databases:
        selected[path.relative_to(root)] = path
    required_components = specification['python_components']
    for relative in ['python/python-3.14.0-amd64.exe', *['python/msi/' + c for c in required_components]]:
        selected[Path('offline') / relative] = assets / relative
    wheels = sorted((assets / 'wheels').glob('*.whl'))
    lock, dependencies = [], {}
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as data:
            metadata_files = [n for n in data.namelist() if n.endswith('.dist-info/METADATA')]
            if len(metadata_files) != 1:
                raise ValueError('Invalid wheel metadata: ' + wheel.name)
            metadata = email.message_from_bytes(data.read(metadata_files[0]))
        package, version = metadata['Name'], metadata['Version']
        normalized = package.lower().replace('-', '_')
        if normalized in dependencies:
            raise ValueError('Duplicate offline dependency: ' + package)
        dependencies[normalized] = version
        lock.append(package + '==' + version)
        selected[Path('offline/wheels') / wheel.name] = wheel
    for required in ('pip', 'flask', 'waitress', 'reportlab', 'numpy', 'pyscipopt', 'openpyxl', 'et_xmlfile'):
        if required not in dependencies:
            raise ValueError('Missing offline wheel: ' + required)
    if dependencies['openpyxl'] != '3.1.5' or dependencies['pyscipopt'] != '6.2.1':
        raise ValueError('Unverified runtime dependency version')
    for path in selected.values():
        if not path.is_file() or path.is_symlink():
            raise ValueError('Missing or linked input file: ' + str(path))
    output.mkdir(parents=True, exist_ok=True)
    stage.mkdir()
    records = []
    for relative, source in sorted(selected.items(), key=lambda item: item[0].as_posix()):
        before = fingerprint(source)
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if fingerprint(source) != before or fingerprint(target)['sha256'] != before['sha256']:
            raise RuntimeError('Input changed during packaging: ' + str(source))
        records.append({'path': relative.as_posix(), 'size': before['size'],
                        'source': 'working_tree' if source.is_relative_to(root) and not source.is_relative_to(assets) else 'offline_asset'})
    for directory in ('runtime/db/scenes', 'runtime/logs', 'runtime/runs', 'runtime/content', 'runtime/temp', 'resources/tiles'):
        (stage / directory).mkdir(parents=True, exist_ok=True)
    generated = {
        'setup_native.cmd': '@echo off\nsetlocal\n"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\\native\\setup_native.ps1" %*\nset "EXIT_CODE=%ERRORLEVEL%"\nendlocal & exit /b %EXIT_CODE%\n',
        'scenario_gui.cmd': '@echo off\nsetlocal\ncd /d "%~dp0"\nif not exist ".venv\\Scripts\\pythonw.exe" (echo Run setup_native.cmd first. & exit /b 2)\nstart "" ".venv\\Scripts\\pythonw.exe" -B -m tools.scenario_db_tool.gui\nendlocal\n',
        'offline/requirements-native.lock.txt': '\n'.join(sorted(lock)) + '\n',
        'README_Native.md': (root / 'deploy/native/README.md').read_text(encoding='utf-8'),
    }
    for relative, content in generated.items():
        target = stage / relative
        target.write_text(content, encoding='utf-8', newline='\n')
        info = fingerprint(target)
        records.append({'path': relative, 'size': info['size'], 'source': 'generated'})
    git = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True)
    provenance = {'version': specification['version'], 'python_version': specification['python_version'],
                  'built_at_utc': datetime.now(timezone.utc).isoformat(), 'source_root': str(root),
                  'git_head_reference_only': git.stdout.strip(), 'baseline': 'current local working tree, including uncommitted files',
                  'dependencies': dependencies, 'tiles': 'external, excluded'}
    write_json(stage / 'build_provenance.json', provenance)
    info = fingerprint(stage / 'build_provenance.json')
    records.append({'path': 'build_provenance.json', 'size': info['size'], 'source': 'generated'})
    write_json(stage / 'file_manifest.json', {'version': specification['version'], 'self_excluded': True,
                                           'files': sorted(records, key=lambda r: r['path'])})
    if {p.relative_to(root).as_posix(): fingerprint(p) for p in databases} != protected_before:
        raise RuntimeError('Source database fingerprint changed')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as data:
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                if not allowed(path.relative_to(stage)):
                    raise ValueError('Excluded directory leaked into package')
                data.write(path, name + '/' + path.relative_to(stage).as_posix())
    info = {'size': archive.stat().st_size}
    write_json(output / (name + '.release.json'), {'zip': archive.name, **info, 'file_count': len(records) + 1})
    print(json.dumps({'directory': str(stage), 'zip': str(archive), 'file_count': len(records) + 1, **info}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for option in ('root', 'assets', 'output'):
        parser.add_argument('--' + option, type=Path, required=True)
    args = parser.parse_args()
    build(args.root.resolve(), args.assets.resolve(), args.output.resolve())
