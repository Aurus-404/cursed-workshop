"""Catalogs that read the whole game (armor, supply, commander weapons) kept on disk.

Each one is saved with the exact stack it was read from (every source's id,
version and content fingerprint) and the tool's own code. Same mods and same
tool: the list loads at once. Any change: it is read again, once. A file that
is missing, damaged or from another stack is simply ignored.
"""
import hashlib
import json
import logging
import os
import threading
from pathlib import Path

from . import __version__

SCHEMA = 2


def _armor(index):
    from .extra_armor import catalog
    return catalog(index)


def _support(index):
    from .support_vehicle import catalog
    return catalog(index)


def _commander_weapons(index):
    from .commander_mg import weapon_catalog
    return weapon_catalog(index)


def _rider(index):
    from .rider_mg import catalog
    return catalog(index)


def _passengers(index):
    from .passenger_seats import pose_catalog
    return pose_catalog(index)


def _commander_supports(index):
    from .commander_mg import support_catalog
    return support_catalog(index)


# name: (view the catalog is stored on, attribute the reader caches it in, reader)
CATALOGS = {
    'armor': (lambda index: index, '_armor_catalog', _armor),
    'support': (lambda index: index, '_support_catalog', _support),
    'commander_weapons': (lambda index: index, '_commander_weapons', _commander_weapons),
    'rider_mgs': (lambda index: index, '_rider_catalog', _rider),
    'passenger_poses': (lambda index: index, '_passenger_poses', _passengers),
    # Commander supports only come from the stock game, whatever mods are on.
    'commander_supports': (lambda index: index.base_game(), '_commander_supports', _commander_supports),
}

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_CODE = None
_WAITING = 0  # threads blocked on a catalog another thread is reading


def awaited():
    """True while someone waits for a catalog that is being read: the reader must not pause."""
    return _WAITING > 0


def catalog_source(index):
    """Native catalogs belong to the source snapshot, not a compiled preview."""
    return index.catalog_source() if hasattr(index, 'catalog_source') else index


def code_identity():
    """Backend identity; documentation/UI rebuilds do not change native catalogs."""
    global _CODE
    if _CODE is None:
        from .build_info import read_build_info, source_code_sha256
        metadata = read_build_info()
        packaged = metadata.get('catalog_code_sha256') or metadata.get('source_sha256')
        if packaged:
            _CODE = packaged
        else:
            _CODE = source_code_sha256(Path(__file__).parent)
    return _CODE


def stack_key(view):
    view = catalog_source(view)
    # The tool's own built vehicles are not catalog material (readers skip
    # them) and change with every build: keying on them read the whole game
    # again after each build, minutes during which previews waited.
    from .registration import is_own_mod
    sources = [[s.get('id'), s.get('version'), s.get('fingerprint'), os.path.normcase(str(s.get('root', '')))]
               for s in view.sources if not is_own_mod(str(s.get('id') or ''))]
    availability = view.dlc_availability().key() if hasattr(view, 'dlc_availability') else None
    return hashlib.sha256(json.dumps([SCHEMA, __version__, code_identity(), sources, availability]).encode()).hexdigest()


def _lock(view, name):
    with _LOCKS_GUARD:
        locks = view.__dict__.setdefault('_catalog_locks', {})
        return locks.setdefault(name, threading.Lock())


STACKS_KEPT = 4


def _prune(folder, name, current):
    """Keep the stacks used last; drop older ones and the single-file layout."""
    saved = sorted(folder.glob(f'{name}-*.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for path in [p for p in saved if p != current][STACKS_KEPT - 1:] + [folder / f'{name}.json']:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def load(index, name, folder):
    """The catalog for this index: from memory, from disk, or read now and saved."""
    index = catalog_source(index)
    if name in ('commander_weapons', 'rider_mgs'):
        index.mountable_game()
    pick, attribute, reader = CATALOGS[name]
    view = pick(index)
    lock = _lock(view, name)
    if not lock.acquire(blocking=False):
        global _WAITING
        with _LOCKS_GUARD:
            _WAITING += 1
        try:
            lock.acquire()
        finally:
            with _LOCKS_GUARD:
                _WAITING -= 1
    try:
        if attribute not in view.__dict__ and folder is not None:
            key = stack_key(view)
            # One file per stack: switching between campaigns with different
            # mods (with and without MACE) never reads the whole game again.
            path = Path(folder) / f'{name}-{key[:24]}.json'
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(saved, dict) and saved.get('key') == key:
                    setattr(view, attribute, saved['value'])
                    os.utime(path)  # recently used: kept when older stacks are dropped
            except (OSError, ValueError):
                pass
            if attribute not in view.__dict__:
                value = reader(index)
                try:
                    # A game restart can change DLC ownership while a cold
                    # catalog is being read. Never publish that result under
                    # the previous availability's disk-cache key.
                    if stack_key(view) != key:
                        return reader(index)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix('.tmp')
                    temporary.write_text(json.dumps({'key': key, 'value': value}), encoding='utf-8')
                    os.replace(temporary, path)
                    _prune(Path(folder), name, path)
                except OSError as error:
                    logging.warning('Catalog %s not saved: %s', name, error)
        return reader(index)
    finally:
        lock.release()
