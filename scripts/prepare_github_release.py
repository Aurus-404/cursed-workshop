"""Test, audit and ZIP the canonical build for GitHub. Does not upload anything.

Use after npm run build and python scripts/build-package.py. The build must match
the current committed public sources. Reuses outputs/checks/github-release.
"""
import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

from release_utils import source_digest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('package_audit', ROOT / 'scripts/prepare_workshop_release.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def version():
    value = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / 'workshop/__init__.py').read_text()).group(1)
    if not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('Expected the real numeric application version')
    for name in ('package.json', 'package-lock.json'):
        if json.loads((ROOT / name).read_text())['version'] != value:
            raise ValueError(f'Version mismatch: {name}')
    import tomllib
    if tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version'] != value:
        raise ValueError('Version mismatch: pyproject.toml')
    return value


def checked(command, log):
    with log.open('w', encoding='utf8') as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                env={**os.environ, 'PYTHONUTF8': '1'})
    if result.returncode:
        raise RuntimeError(f'Validation failed; see {log.relative_to(ROOT)}')
    return log.read_text(encoding='utf8')


@contextmanager
def extracted_package(output):
    """Windows can briefly retain an image/antivirus handle after process exit."""
    folder = Path(tempfile.mkdtemp(prefix='github-extracted-', dir=output)).resolve()
    try:
        yield folder
    finally:
        if folder.parent != output.resolve() or not folder.name.startswith('github-extracted-') or folder.is_symlink():
            raise ValueError('Refusing cleanup outside the owned extraction directory')
        for attempt in range(20):
            try:
                shutil.rmtree(folder)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.5)


def prepare():
    value = version()
    dist = ROOT / 'dist/CursedWorkshop'
    output = ROOT / 'outputs/checks/github-release'
    output.mkdir(parents=True, exist_ok=True)
    audit.validate_distribution(dist)
    info = json.loads((dist / '_internal/workshop/build_info.json').read_text())
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    current = source_digest(ROOT)
    if info.get('source_digest_format') != 'paths-lengths-crlf-normalized-v1':
        raise ValueError('Rebuild with the current source identity format')
    if info.get('source_sha256') != current or source_digest(ROOT, revision) != current:
        raise ValueError('Build, working source and committed release source must match')
    if info.get('revision') != revision or info.get('dirty_source') is not False:
        raise ValueError('Build must record this clean public source commit')
    notes = ROOT / f'docs/releases/{value}.md'
    if not notes.is_file():
        raise ValueError('Missing release notes for this application version')

    print('Running Python tests...', flush=True)
    python_log = checked([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], output / 'python-tests.log')
    count = re.search(r'Ran (\d+) tests?', python_log)
    if not count or not re.search(r'^OK(?:\s|$)', python_log, re.MULTILINE):
        raise ValueError('Missing successful Python test summary')
    skipped = re.search(r'^OK \(skipped=(\d+)\)', python_log, re.MULTILINE)
    python_skipped = int(skipped.group(1)) if skipped else 0
    js = sorted((ROOT / 'scripts').glob('test-*.mjs'))
    print(f'Running {len(js)} frontend test scripts...', flush=True)
    for path in js:
        checked(['node', str(path)], output / (path.stem + '.log'))

    archive = output / f'CursedWorkshop-{value}-win64.zip'
    with tempfile.TemporaryDirectory(prefix='github-package-', dir=output) as folder:
        staging = Path(folder)
        audit.collect_notices(staging)
        instructions = (
            f'CURSED WORKSHOP {value} - Windows x64 (WIP)\n\n'
            'Extract the entire ZIP. Run CursedWorkshop\\CursedWorkshop.exe.\n'
            'Keep _internal beside the executable. Python/Node.js are not required.\n'
            'If the desktop window does not open, install Microsoft Edge WebView2 Runtime\n'
            'or run CursedWorkshop.exe --browser. Select your game folder in Settings.\n'
            'The former Steam Workshop item is not required.\n'
            'Close the game or leave it at the main menu before editing a campaign.\n'
            'Enable Cursed Workshop - Built Vehicles LAST in Extras > Mods; apply.\n'
            'Try a campaign copy first and keep backups. Read USER_GUIDE.md.\n'
            'Data/backups: %LOCALAPPDATA%\\ModularWorkshop.\n'
            'Source, updates, issues: https://github.com/Aurus-404/cursed-workshop\n'
        )
        (staging / 'README.txt').write_text(instructions, encoding='utf8')
        for source, name in ((ROOT / 'docs/USER_GUIDE.md', 'USER_GUIDE.md'),
                             (ROOT / 'docs/DISTRIBUTION.md', 'DISTRIBUTION.md'),
                             (notes, 'RELEASE_NOTES.md')):
            (staging / name).write_bytes(source.read_bytes())
        files = [(p, 'CursedWorkshop/' + p.relative_to(dist).as_posix()) for p in dist.rglob('*')
                 if p.is_file() and p.suffix != '.map']
        files += [(p, p.relative_to(staging).as_posix()) for p in staging.rglob('*') if p.is_file()]
        manifest = {'version': value, 'revision': revision, 'source_sha256': current,
                    'source_digest_format': info['source_digest_format'],
                    'python_tests_run': int(count.group(1)), 'python_tests_skipped': python_skipped,
                    'python_tests': int(count.group(1)) - python_skipped, 'frontend_test_scripts': len(js),
                    'manual_battle_acceptance': 'NOT TESTED',
                    'files': {name: {'bytes': p.stat().st_size, 'sha256': audit.digest(p)} for p, name in sorted(files)}}
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path, name in files:
                bundle.write(path, name)
            bundle.writestr('release-manifest.json', json.dumps(manifest, indent=2))

    print('Extracting and checking the downloadable ZIP...', flush=True)
    with extracted_package(output) as extracted:
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip() is not None:
                raise ValueError('ZIP CRC verification failed')
            bundle.extractall(extracted)
        inspected = audit.audit_item(extracted)
        if set(inspected) != set(manifest['files']) | {'release-manifest.json'}:
            raise ValueError('Unexpected file set after extraction')
        for name, expected in manifest['files'].items():
            if inspected[name] != expected:
                raise ValueError(f'Extracted file differs: {name}')
        # The API exercise creates only synthetic game/save resources. It launches
        # our extracted executable from its own folder, never from the private checkout.
        # A fresh fixture folder preserves evidence from any earlier failed run.
        smoke = Path(tempfile.mkdtemp(prefix='github-smoke-', dir=ROOT / 'outputs/checks'))
        smoke.rmdir()  # the fixture runner requires a new destination
        print('Launching the extracted program with synthetic resources...', flush=True)
        checked([sys.executable, '-X', 'utf8', 'scripts/dev/smoke_gun_editor.py',
                 '--exe', str(extracted / 'CursedWorkshop/CursedWorkshop.exe'), '--out', str(smoke),
                 '--crew-visibility', '--support-vehicle', '--extra-armor', '--smoke-launchers', '--corrections'],
                output / 'packaged-smoke.log')
        smoke_result = json.loads((smoke / 'report.json').read_text())
        if not smoke_result.get('ok') or not smoke_result.get('own_process_stopped'):
            raise ValueError('Packaged API verification did not complete')
    if source_digest(ROOT) != current:
        raise ValueError('Application source changed during validation; rebuild')
    manifest['verification'] = {'zip_crc': 'PASS', 'extracted_file_hashes': 'PASS',
                                'package_content_audit': 'PASS', 'packaged_synthetic_api': smoke_result}
    (output / 'release-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf8')
    checksum = audit.digest(archive)
    (output / 'SHA256SUMS.txt').write_text(f'{checksum}  {archive.name}\n', encoding='utf8')
    # Test evidence, file inventory and hashes stay in local verification outputs.
    # Public release notes are written for players.
    (output / 'release-notes.md').write_text(notes.read_text(encoding='utf8'), encoding='utf8')
    print(json.dumps({'zip': str(archive), 'sha256': checksum,
                      'python_tests': manifest['python_tests'], 'frontend_test_scripts': len(js)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    prepare()
