"""Stable source identity shared by the desktop build and GitHub packager."""
import hashlib
from pathlib import Path
import struct
import subprocess


def runtime_members(root):
    root = Path(root)
    return sorted([root / 'launch.py', *(root / 'workshop').glob('*.py'),
                   *(p for p in (root / 'workshop/web').rglob('*') if p.is_file() and p.suffix != '.map')])


def source_digest(root, revision=None):
    """Hash paths and bytes; normalize CRLF so Windows checkouts retain identity."""
    root = Path(root)
    result = hashlib.sha256()
    for path in runtime_members(root):
        name = path.relative_to(root).as_posix()
        raw = (subprocess.check_output(['git', 'show', f'{revision}:{name}'], cwd=root)
               if revision else path.read_bytes())
        if path.suffix in {'.py', '.js', '.css', '.html', '.json', '.txt'}:
            raw = raw.replace(b'\r\n', b'\n')
        result.update(name.encode() + b'\0' + struct.pack('<Q', len(raw)) + raw)
    return result.hexdigest()
