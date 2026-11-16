"""Disk caches only: explicit roots, no links, no campaign or recovery data."""
import os
import stat
from pathlib import Path


ROOTS = ('cache/archives', 'cache/catalogs', 'cache/crew_models', 'texture_cache')


def _plain(path):
    info = path.lstat()
    # Windows junctions and other reparse points must not be traversed either.
    return not (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400)


def _inside(workspace, path):
    """Check every ancestor, including the shared cache parent."""
    try:
        relative = path.relative_to(workspace)
        current = workspace
        for part in relative.parts:
            current = current / part
            if not _plain(current):
                return False
        return path.resolve().is_relative_to(workspace)
    except (OSError, ValueError):
        return False


def maintain(workspace, *, clear=False):
    """Count/delete regular cache files; concurrent/locked files are retained.

    Cache consumers already tolerate missing files and regenerate them. Keeping
    directory roots avoids breaking concurrent atomic writers. Nothing in the
    current design or memory cache is invalidated by deleting a disk copy.
    """
    workspace = Path(workspace).resolve()
    result = {'bytes': 0, 'files': 0, 'removed_bytes': 0, 'removed_files': 0, 'skipped': 0}

    def visit(folder):
        if not _inside(workspace, folder):
            result['skipped'] += 1
            return
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    try:
                        if not _plain(path):
                            result['skipped'] += 1
                        elif entry.is_dir(follow_symlinks=False):
                            visit(path)
                        elif entry.is_file(follow_symlinks=False):
                            size = entry.stat(follow_symlinks=False).st_size
                            result['bytes'] += size
                            result['files'] += 1
                            if clear:
                                # Recheck parents just before deletion. Never resolve a
                                # link and then delete through it.
                                if not _inside(workspace, path):
                                    result['skipped'] += 1
                                    continue
                                path.unlink()
                                result['removed_bytes'] += size
                                result['removed_files'] += 1
                    except FileNotFoundError:
                        pass  # A cache writer/another cleanup won the race.
                    except OSError:
                        result['skipped'] += 1
        except FileNotFoundError:
            pass
        except OSError:
            result['skipped'] += 1

    for relative in ROOTS:
        folder = workspace / relative
        if folder.exists() or folder.is_symlink():
            visit(folder)
    return result
