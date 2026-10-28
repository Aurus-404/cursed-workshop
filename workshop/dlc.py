"""Read DLC availability reported by the game's last run; never infer ownership
from installed archives, Steam depot manifests or the contents of dlc.reg.
"""
from dataclasses import dataclass
from pathlib import Path
import os
import re

from .locations import game_log_path, running_under_wine

_SCAN = re.compile(r'Steam DLC Scan:\s*(\d+) total\s*$')
_DLC = re.compile(r'Found DLC (\d+) ".*" \(state=\d+, owned=([01]), installed=([01])\)\s*$')
_MOUNT = re.compile(r'Mount package (.+\.pak) with \d+ entries\s*$', re.I)
_DLC_PAK = re.compile(r'(?:^|_)dlc\d+', re.I)


def dlc_archive(path):
    return bool(_DLC_PAK.search(Path(str(path).replace('\\', '/')).stem))


def _path(value):
    value = str(value).replace('\\', '/').rstrip('/').casefold()
    if value.startswith('z:/') and (os.name != 'nt' or running_under_wine()):
        value = value[2:]
    return value


@dataclass(frozen=True)
class Availability:
    available: tuple[str, ...] = ()
    archives: tuple[str, ...] = ()
    verified: bool = False

    def public(self):
        return {'status': 'last_game_run' if self.verified else 'unknown',
                'available': list(self.available)}

    def key(self):
        return [self.verified, self.available, self.archives]


def parse_scan(text, game_root):
    """Only a complete scan followed by mounts of this installation is evidence.

    Registry entries with several app IDs are alternatives (e.g. Bren Mk2).
    A partial/new scan supersedes an older complete one; unknown stays closed.
    """
    expected, rows, archives, matched, invalid = None, {}, set(), False, False
    root = _path(Path(game_root) / 'resource') + '/'
    for line in text.splitlines() if isinstance(text, str) else text:
        if not any(marker in line for marker in ('Steam DLC Scan:', 'Found DLC ', 'Mount package ')):
            continue
        scan = _SCAN.search(line)
        if scan:
            expected = int(scan[1]); rows = {}; archives = set(); matched = invalid = False
            continue
        if expected is None:
            continue
        row = _DLC.search(line)
        if row:
            if row[1] in rows:
                invalid = True
            rows[row[1]] = row[2] == row[3] == '1'
        mount = _MOUNT.search(line)
        if mount:
            path = _path(mount[1])
            if path.startswith(root):
                matched = True
                if dlc_archive(path):
                    archives.add(path)
    if expected is None or len(rows) != expected or invalid or not matched:
        return Availability()
    return Availability(tuple(sorted(k for k, v in rows.items() if v)), tuple(sorted(archives)), True)


def read_availability(index):
    """Cache log parsing, but notice a new scan without restarting the tool."""
    path = game_log_path(index.game_root)
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
        if getattr(index, '_dlc_log_stamp', None) == stamp:
            return index._dlc_log_state
        with path.open(encoding='utf-8', errors='replace') as handle:
            state = parse_scan(handle, index.game_root)
    except OSError:
        stamp, state = None, Availability()
    index._dlc_log_stamp, index._dlc_log_state = stamp, state
    return state
