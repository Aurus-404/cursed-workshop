"""Audit an explicit public source set. Optionally commit it in separate Git metadata.

The private checkout, its index and history are preserved. This creates no second
application checkout or distribution. No network or publication happens here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = ('.gitignore', '.gitattributes', 'README.md', 'launch.py',
               'package.json', 'package-lock.json', 'pyproject.toml', 'requirements-build.txt')
PUBLIC_DOCS = ('USER_GUIDE.md', 'PUBLISHING_GITHUB.md', 'DISTRIBUTION.md')
SCRIPT_NAMES = ('build-package.py', 'build-ui.mjs', 'build-workshop-item.py',
                'prepare_workshop_release.py', 'prepare_github_source.py',
                'prepare_github_release.py', 'release_utils.py',
                'compat_report.py', 'compat_sweep.py', 'repair-weapon-state.py')


def public_paths(root=ROOT):
    root = Path(root)
    files = [root / p for p in PUBLIC_ROOT]
    files += [root / 'docs' / p for p in PUBLIC_DOCS]
    files += list((root / 'docs/releases').glob('*.md'))
    files += list((root / 'docs/third_party').glob('*'))
    files += list((root / 'frontend').glob('*.js'))
    files += [root / 'frontend/index.html', root / 'frontend/style.css']
    files += list((root / 'workshop').glob('*.py'))
    files += [p for p in (root / 'workshop/web').rglob('*') if p.is_file() and p.suffix != '.map']
    files += list((root / 'tests').glob('*.py'))
    files += [root / 'scripts' / p for p in SCRIPT_NAMES]
    files += list((root / 'scripts').glob('test-*.mjs'))
    files += [root / 'scripts/dev/smoke_gun_editor.py']
    return sorted(set(files))


SECRET_PATTERNS = (
    r'gh[pousr]_[A-Za-z0-9]{30,}', r'github_pat_[A-Za-z0-9_]{40,}',
    r'sk-(?:proj-)?[A-Za-z0-9_-]{32,}', r'AKIA[0-9A-Z]{16}',
    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    r'https://(?:discord(?:app)?\.com/api/webhooks|hooks\.slack\.com/services)/\S+',
    r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
    r'profiles[^\n]{0,40}[\x22\x27][0-9]{8,}[\x22\x27]',
)


def audit(root=ROOT):
    inventory, findings = {}, []
    for path in public_paths(root):
        name = path.relative_to(root).as_posix()
        if not path.is_file() or path.is_symlink() or path.is_junction():
            raise ValueError(f'Missing or linked public source: {name}')
        if path.suffix.lower() not in {'.py', '.js', '.mjs', '.html', '.css', '.md', '.txt', '.json', '.toml', ''}:
            raise ValueError(f'Unexpected public source type: {name}')
        raw = path.read_bytes()
        value = raw.decode('utf-8')
        for pattern in SECRET_PATTERNS:
            for match in re.finditer(pattern, value):
                # Vendored license contacts are public copyright notices.
                if '@' in match.group() and (name.startswith('docs/third_party/') or name == 'workshop/web/assets/app.js'):
                    continue
                findings.append({'path': name, 'line': value.count('\n', 0, match.start()) + 1,
                                 'kind': 'credential or contact candidate; review required'})
        inventory[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    if findings:
        raise ValueError(json.dumps(findings, indent=2))
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', action='store_true')
    args = parser.parse_args()
    inventory = audit()
    output = ROOT / 'outputs/checks'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'github-source-audit.json').write_text(json.dumps(inventory, indent=2), encoding='utf8')
    print(f'Public source audit: {len(inventory)} files; no credential/contact candidates.')
    if args.commit:
        metadata = output / 'github-public.git'
        if not metadata.exists():
            subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(metadata)], check=True)
        command = ['git', '--git-dir=' + str(metadata), '--work-tree=' + str(ROOT)]
        email = subprocess.check_output(command + ['config', 'user.email'], text=True).strip()
        if not email.endswith('@users.noreply.github.com'):
            raise ValueError('Configure a GitHub noreply user.email in the public Git metadata before committing')
        subprocess.run(command + ['config', 'core.autocrlf', 'false'], check=True)
        subprocess.run(command + ['read-tree', '--empty'], check=True)
        subprocess.run(command + ['add', '--', *inventory], cwd=ROOT, check=True)
        subprocess.run(command + ['diff', '--cached', '--check'], cwd=ROOT, check=True)
        version = json.loads((ROOT / 'package.json').read_text())['version']
        subprocess.run(command + ['commit', '-m', f'Cursed Workshop {version}: official GitHub distribution'], cwd=ROOT, check=True)
        print('Public Git metadata: ' + str(metadata))


if __name__ == '__main__':
    main()
