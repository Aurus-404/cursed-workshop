"""Recoverable publication of a generated addon and a separate save copy.

Each replacement uses os.replace on the same directory/volume. A journal records
intent before each replacement, and rollback refuses to erase externally changed
outputs. Original saves are read, hashed and archived, never replaced.
"""
from __future__ import annotations

import csv
import errno
import hashlib
from contextlib import nullcontext
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .campaign import CampaignSnapshot, apply_plan, build_save_copy, load_campaign
from .process_guard import SAFE_PAGES, menu_page


def long_path(path) -> Path:
    """The Windows extended-length form of a path for disk operations.

    Without it Windows hides every file past 260 characters (a long user name
    plus nested operation folders): walks skip them and copies lose them.
    Comparisons keep using the ordinary form.
    """
    text = os.fspath(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return Path(path)
    text = os.path.abspath(text)
    return Path("\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text)


class TransactionError(RuntimeError):
    pass


GAME_PROCESSES = {"call_to_arms.exe", "call_to_arms_ed.exe", "call_to_arms_server.exe",
                  "call_to_arms_workshop.exe", "gatesofhell.exe"}
OWNERSHIP_FILE = "workshop-ownership.json"


def running_game_processes() -> list[str]:
    """Read process names without controlling or closing any process."""
    if os.name == "nt":
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tasklist.exe"
        try:
            result = subprocess.run([str(executable), "/FO", "CSV", "/NH"], check=True,
                                    capture_output=True, text=True, timeout=15,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            raise TransactionError("Cannot establish that the game is closed") from exc
        names = [row[0].casefold() for row in csv.reader(io.StringIO(result.stdout)) if row]
    else:
        # Development/tests on POSIX; /proc inspection remains read-only.
        proc = Path("/proc")
        if not proc.exists():
            raise TransactionError("Process inspection is unavailable on this platform")
        names = []
        for item in proc.iterdir():
            if item.name.isdecimal():
                try:
                    names.append((item / "comm").read_text().strip().casefold())
                except OSError:
                    continue
    return sorted(set(names) & GAME_PROCESSES)


def assert_game_closed() -> None:
    """The game may stay open in a main-menu page (no campaign in memory);
    the Editor, the server, a loaded campaign or a battle block every step."""
    running = running_game_processes()
    if not running:
        return
    if running == ["call_to_arms.exe"] and menu_page() in SAFE_PAGES:
        return
    raise TransactionError("Close the game (or return it to the main menu) and its Editor before using the workshop: "
                           + ", ".join(running))


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# Digest cache keyed by file identity and metadata, like git's index: the
# same 4,000-file addon is verified several times per build (prepare,
# portrait, install, publication). A file whose size, modification time,
# change time and file id are unchanged is not read again. Entries are only
# trusted when the file was last modified more than _RACY_SECONDS before it
# was hashed, so a rewrite within the timestamp resolution is never missed.
_RACY_SECONDS = 2.0
_HASH_CACHE: dict[str, tuple[tuple, str]] = {}
_HASH_CACHE_LOCK = threading.Lock()


def _stat_key(stat) -> tuple:
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, stat.st_dev)


def _file_hash(path: Path, *, durable=False) -> str:
    before = os.stat(path)
    # File identity, not its path: staged folders are moved (os.replace) between
    # verifications and keep their files; a path key missed every time.
    key = (before.st_dev, before.st_ino) if before.st_ino else os.path.normcase(os.path.abspath(path))
    if not durable:
        with _HASH_CACHE_LOCK:
            cached = _HASH_CACHE.get(key)
        if cached is not None and cached[0] == _stat_key(before):
            return cached[1]
    digest = hashlib.sha256()
    with path.open("r+b" if durable else "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
        if durable:
            file.flush()
            os.fsync(file.fileno())
    value = digest.hexdigest()
    after = os.stat(path)
    if _stat_key(after) == _stat_key(before) and time.time() - after.st_mtime > _RACY_SECONDS:
        with _HASH_CACHE_LOCK:
            if len(_HASH_CACHE) > 200_000:
                _HASH_CACHE.clear()
            _HASH_CACHE[key] = (_stat_key(after), value)
    return value


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


_SYSTEM_FOLDERS = None


def _system_folder(item: Path) -> bool:
    """The user's own folders (home, Documents, AppData, Steam's Proton prefix,
    the Linux home under Wine) and everything above them may be links: a moved
    profile, a Documents junction, /home on SteamOS. That layout is the
    system's, not something to guard; links below these folders still are."""
    global _SYSTEM_FOLDERS
    if _SYSTEM_FOLDERS is None:
        from .locations import documents_dir, proton_users, unix_homes, unix_path
        folders = [Path.home(), documents_dir()]
        folders += [Path(os.environ[name]) for name in ("USERPROFILE", "LOCALAPPDATA", "APPDATA") if os.environ.get(name)]
        folders += [unix_path(home) for home in unix_homes()] + proton_users()
        _SYSTEM_FOLDERS = tuple({os.path.normcase(str(folder)).rstrip("\\/") for folder in folders})
    key = os.path.normcase(str(item)).rstrip("\\/")
    return any(folder == key or folder.startswith(key + os.sep) for folder in _SYSTEM_FOLDERS)


def _no_symlink(path: Path) -> None:
    for item in (path, *path.parents):
        if _system_folder(item):
            break
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise TransactionError(f"Symlink/junction destinations are unsupported: {item}")


def _write_file(path: Path, data: bytes, *, exclusive: bool = True, durable: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _no_symlink(path)
    with path.open("xb" if exclusive else "wb") as file:
        file.write(data)
        file.flush()
        if durable:os.fsync(file.fileno())


def replace_file_verified(target: Path, expected_hash: str, data: bytes, backup: Path) -> None:
    """Replace one file the tool has read (a save it repairs): the original is
    copied to backup and verified first, the target must still be the file that
    was read, and the new content is written beside it and swapped in at once."""
    target = Path(target)
    _no_symlink(target)
    original = target.read_bytes()
    if _sha(original) != expected_hash:
        raise TransactionError("The save changed after it was read; open it again")
    _write_file(backup, original)
    if _file_hash(backup, durable=True) != expected_hash:
        raise TransactionError("Save backup failed hash validation")
    temp = target.with_name(f".{target.name}.mw-repair-{uuid.uuid4().hex}")
    try:
        _write_file(temp, data)
        if _file_hash(temp, durable=True) != _sha(data):
            raise TransactionError("Repaired save failed hash validation")
        if _file_hash(target) != expected_hash:
            raise TransactionError("The save changed while it was being repaired")
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()


def _atomic_json(path: Path, data: dict) -> None:
    _no_symlink(path)
    raw = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    handle, temp = tempfile.mkstemp(prefix="." + path.name + "-", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _write_addon_files(root: Path, names, read, *, defer_sync=False, reuse=None) -> set:
    """Write independent staged files durably, then join before verification.

    Limit concurrency and retain each file's exclusive open, path checks and
    fsync. Publication alone may defer fsync to its subsequent full durable
    hash verification, before any live directory is replaced. Executor shutdown
    also joins in-flight writes on failure, before
    publication recovery is allowed to inspect or remove the staging tree.
    Returns the names that were hard-linked instead of written.
    """
    _no_symlink(root)
    root = long_path(root)
    boundary = root.resolve()
    names = list(names)
    targets = []
    for name in names:
        target = root.joinpath(*PurePosixPath(name).parts)
        if not _within(target.resolve(), boundary):
            raise TransactionError("Addon path escapes its staging root")
        targets.append((name, target))
    # Each directory is created and checked once (not every parent of every
    # file); files are created exclusively, so none can be a planted link.
    folders = sorted({target.parent for _, target in targets}, key=lambda folder: len(folder.parts))
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
        for item in (folder, *folder.parents):
            if item == root or not _within(item, root):
                break
            if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                raise TransactionError(f"Symlink/junction destinations are unsupported: {item}")
    reuse = reuse or {}
    linked = set()

    def write(member):
        name, target = member
        source = reuse.get(name)
        if source is not None:
            # Unchanged file of the verified installed addon: a hard link costs
            # no copy, no space and no flush. The caller verifies every digest
            # of the finished tree before anything is published.
            try:
                os.link(source, target)
                linked.add(name)
                return
            except OSError:
                pass
        with target.open("xb") as file:
            file.write(read(name))
            file.flush()
            if not defer_sync:
                os.fsync(file.fileno())

    if len(targets) < 2:
        for member in targets:
            write(member)
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(targets)), thread_name_prefix="cw-copy") as pool:
            for _ in pool.map(write, targets):
                pass
    return linked


def _tree_hashes(path: Path, *, durable=False, settled=frozenset()) -> dict[str, str]:
    """Digest of every file. durable: flush each to disk while reading it, except
    the names in settled (hard links to installed files, already on disk)."""
    _no_symlink(path)
    if not path.is_dir():
        raise TransactionError(f"Addon directory is missing: {path}")
    result, seen = {}, set()
    walk = long_path(path)
    items=sorted(walk.rglob('*'))
    def inspect(item):
        # The root and its parents were checked above; every directory below
        # the root is itself one of the walked items, so checking each item on
        # its own covers all of its ancestors (12 lstat calls per file before).
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise TransactionError(f"Symlink/junction destinations are unsupported: {item}")
        if item.is_file():
            key = item.relative_to(walk).as_posix()
            return key,(_file_hash(item,durable=True) if durable and key not in settled else _file_hash(item))
        if not item.is_dir():
            raise TransactionError(f"Addon file cannot be read: {item}")
        return None
    def collect(values):
        for entry in values:
            if entry is None:continue
            key,digest=entry
            folded = key.casefold()
            if folded in seen:
                raise TransactionError("Case-insensitive output path collision")
            seen.add(folded)
            result[key] = digest
    if len(items)<16:
        collect(map(inspect,items))
    else:
        # Every file keeps its link checks and full digest. Join all readers on
        # failure before rollback can replace/remove any of these directories.
        with ThreadPoolExecutor(max_workers=4,thread_name_prefix='cw-verify') as pool:
            collect(pool.map(inspect,items))
    return result


def _event(journal: dict, state: str, message: str) -> None:
    journal["state"] = state
    journal["events"].append({"time_utc": datetime.now(timezone.utc).isoformat(), "message": message})


def _read_journal(path: str | Path) -> tuple[Path, dict]:
    path = Path(path).resolve()
    _no_symlink(path)
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise TransactionError("Cannot read transaction journal") from exc
    op = Path(journal.get("operation_dir", "")).resolve()
    workspace = Path(journal.get("workspace", "")).resolve()
    if journal.get("schema_version") != 1 or path != op / "journal.json" or not _within(op, workspace):
        raise TransactionError("Invalid transaction journal identity or containment")
    # Every internal artifact has a fixed location; a edited journal cannot point
    # rollback at arbitrary source files.
    if Path(journal.get("staged_addon", "")).resolve() != op / "stage" / "addon":
        raise TransactionError("Invalid staged addon path")
    if not _within(Path(journal.get("staged_save", "")).resolve(), op / "stage" / "save"):
        raise TransactionError("Invalid staged save path")
    return path, journal


def _validate_source(journal: dict) -> None:
    path = Path(journal["source_path"])
    try:
        actual = _file_hash(path)
    except OSError as exc:
        raise TransactionError("The original save is no longer available for comparison") from exc
    if actual != journal["source_hash"]:
        raise TransactionError("Original save changed after planning; reload it before preparing or applying")


def prepare_transaction(workspace: str | Path, snapshot: CampaignSnapshot, plan: dict,
                        addon_files: dict[str, bytes] | None = None, *, addon_directory: str | Path | None = None) -> dict:
    """Write a reviewable bundle in workspace, without modifying game or saves."""
    assert_game_closed()
    workspace = Path(workspace).resolve()
    _no_symlink(workspace)
    directory=None;directory_hashes=None
    if addon_directory is not None:
        if addon_files is not None:raise TransactionError('Choose addon files or a prepared directory, not both')
        directory=Path(addon_directory)
        _no_symlink(directory)
        directory=directory.resolve()
        expected=plan['addon_mod'].split(':')[0]
        if directory.parent!=workspace or directory.name!=expected:
            raise TransactionError('Prepared addon directory must be the named child of this operation workspace')
        directory_hashes=_tree_hashes(directory,durable=True)
        # Reuse the same strict member-name validation, without loading all
        # previous variants into RAM or writing a second complete staging copy.
        addon_files=dict.fromkeys(directory_hashes,b'')
    if _file_hash(snapshot.source_path) != snapshot.source_hash:
        raise TransactionError("Original save changed after planning; reload it")
    if not addon_files or "mod.info" not in addon_files:
        raise TransactionError("Addon needs mod.info and emitted resources before a save can be prepared")
    paths, seen = {}, set()
    for name, payload in addon_files.items():
        normalized = PurePosixPath(name.replace("\\", "/"))
        if normalized.is_absolute() or not normalized.parts or any(p in ("", ".", "..") or ":" in p
                                                                     for p in normalized.parts):
            raise TransactionError(f"Unsafe addon path: {name}")
        reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                    *(f"lpt{i}" for i in range(1, 10))}
        if any(p.rstrip(" .") != p or any(c in p for c in '<>:"|?*')
               or p.split(".")[0].casefold() in reserved for p in normalized.parts):
            raise TransactionError(f"Unsupported Windows addon filename: {name}")
        key = normalized.as_posix()
        if key.casefold() in seen or key.casefold() == OWNERSHIP_FILE:
            raise TransactionError(f"Duplicate or reserved addon path: {name}")
        if not isinstance(payload, bytes):
            raise TransactionError(f"Addon member {name} must be bytes")
        seen.add(key.casefold())
        paths[key] = payload
    replacements = apply_plan(snapshot, plan)
    save_bytes = build_save_copy(snapshot, replacements)
    transaction_id = uuid.uuid4().hex
    op = workspace / "operations" / transaction_id
    op.mkdir(parents=True, exist_ok=False)
    stage_addon = op / "stage" / "addon"
    stage_save = op / "stage" / "save" / (snapshot.source_path.stem + "_assembled.sav")
    hashes = directory_hashes if directory is not None else {name: _sha(data) for name, data in paths.items()}
    if directory is not None:
        # Both resolved targets were checked within this one operation workspace.
        if not _within(directory,workspace) or not _within(stage_addon.resolve(),workspace):
            raise TransactionError('Prepared addon move escapes its operation workspace')
        stage_addon.parent.mkdir(parents=True,exist_ok=True)
        os.replace(long_path(directory),long_path(stage_addon))
    else:
        _write_addon_files(stage_addon, paths, paths.__getitem__)
    _write_file(stage_save, save_bytes)
    _write_file(op / "backup" / "original.sav", snapshot._zip_bytes)
    _write_file(op / "plan.json", json.dumps(plan, indent=2, ensure_ascii=False).encode("utf-8"))
    journal = {"schema_version": 1, "transaction_id": transaction_id, "state": "prepared",
               "workspace": str(workspace), "operation_dir": str(op),
               "source_path": str(snapshot.source_path), "source_hash": snapshot.source_hash,
               "addon_mod": plan["addon_mod"], "plan_id": plan["plan_id"],
               "staged_addon": str(stage_addon), "staged_save": str(stage_save),
               "addon_hashes": hashes, "save_hash": _sha(save_bytes), "events": [],
               "publication": None}
    _event(journal, "prepared", "Addon and independent save copy validated; original untouched")
    _atomic_json(op / "journal.json", journal)
    verify_transaction(op / "journal.json")
    return {"transaction_id": transaction_id, "state": "prepared", "journal_path": str(op / "journal.json"),
            "addon_path": str(stage_addon), "save_path": str(stage_save), "backup_path": str(op / "backup" / "original.sav"),
            "plan_path": str(op / "plan.json"), "source_hash": snapshot.source_hash, "save_hash": journal["save_hash"]}


def verify_transaction(journal_path: str | Path) -> dict:
    path, journal = _read_journal(journal_path)
    _validate_source(journal)
    if _tree_hashes(Path(journal["staged_addon"])) != journal["addon_hashes"]:
        raise TransactionError("Staged addon changed after validation")
    if _file_hash(Path(journal["staged_save"])) != journal["save_hash"]:
        raise TransactionError("Staged save changed after validation")
    backup = Path(journal["operation_dir"]) / "backup" / "original.sav"
    if _file_hash(backup) != journal["source_hash"]:
        raise TransactionError("Original backup failed hash validation")
    plan = json.loads((Path(journal["operation_dir"]) / "plan.json").read_text(encoding="utf-8"))
    if plan.get("plan_id") != journal["plan_id"]:
        raise TransactionError("Plan identity does not match journal")
    after = load_campaign(journal["staged_save"])
    present = {u["eid"] for u in after.units}
    if plan.get('operation')=='store_one':
        if set(plan['stored_eids'])&present:
            raise TransactionError('Staged campaign still holds stored vehicle or crew')
    elif plan.get('operation')=='restore_stored':
        if not set(plan['restored_eids'])<=present:
            raise TransactionError('Staged campaign lacks restored vehicle or crew')
    elif plan.get("operation") == "remove_one":
        # A deletion leaves no new vehicle: the deleted one is simply gone.
        if plan["recipient_eid"] in present:
            raise TransactionError("Staged campaign still holds the deleted vehicle")
    elif plan["new_eid"] not in present or any(e in present for e in plan["consumed_eids"]):
        raise TransactionError("Staged campaign does not match the two-into-one operation")
    return {"valid": True, "state": journal["state"], "transaction_id": journal["transaction_id"],
            "source_hash": journal["source_hash"], "save_hash": journal["save_hash"],
            "addon_hashes": dict(journal["addon_hashes"]),
            "addon_files": len(journal["addon_hashes"]), "journal_path": str(path)}


def _owned_addon(path: Path, addon_mod: str) -> dict[str, str]:
    try:
        marker = json.loads((path / OWNERSHIP_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TransactionError("Existing addon is not owned by this workshop; choose another destination") from exc
    actual = _tree_hashes(path)
    expected = marker.get("files", {})
    members = {name: digest for name, digest in actual.items() if name != OWNERSHIP_FILE}
    if marker.get("addon_mod") != addon_mod or members != expected:
        raise TransactionError("Existing addon changed externally or belongs to another addon")
    return actual  # Return the snapshot actually validated, without a second scan.


def commit_transaction(journal_path: str | Path, addon_destination: str | Path | None = None,
                       save_destination: str | Path | None = None, *, allow_install: bool = False,
                       in_place: bool = False, phase=None) -> dict:
    """Publish reviewed outputs; installing outside workspace is explicit.

    Without in_place, the original save and existing save files are never
    overwritten. With in_place, the opened save itself is updated (the player
    keeps one campaign): only that exact file, only if it is byte-identical to
    the planned source, and the verified original stays in backup/ for rollback.
    """
    assert_game_closed()
    phase=phase or (lambda label:nullcontext())
    path, journal = _read_journal(journal_path)
    if journal["state"] == "committed":
        publication = journal["publication"]
        if _tree_hashes(Path(publication["addon_destination"])) != publication["installed_addon_hashes"]:
            raise TransactionError("Previously published addon changed externally")
        if _file_hash(Path(publication["save_destination"])) != journal["save_hash"]:
            raise TransactionError("Previously published save copy changed externally")
        return {"state": "committed", **journal["publication"]}
    if journal["state"] != "prepared":
        raise TransactionError("Only a prepared transaction can be published; recover any interrupted operation first")
    with phase('Install: verify prepared transaction'):
        verify_transaction(path)
    op, workspace = Path(journal["operation_dir"]), Path(journal["workspace"])
    folder_name = journal["addon_mod"].split(":")[0]
    addon = Path(addon_destination).resolve() if addon_destination else op / "export" / folder_name
    save = Path(save_destination).resolve() if save_destination else op / "export" / Path(journal["staged_save"]).name
    source = Path(journal["source_path"]).resolve()
    if in_place and save != source:
        raise TransactionError("In-place publication may only update the opened save")
    for destination in (addon, save):
        _no_symlink(destination)
        if destination == source and not (in_place and destination is save):
            raise TransactionError("The original save can never be a publication destination")
        if not allow_install and not _within(destination, workspace):
            raise TransactionError("Installing outside the workspace requires allow_install=True")
        if _within(destination, op / "stage") or _within(destination, op / "backup"):
            raise TransactionError("Publication cannot overwrite staged files or backup")
    if _within(save, addon) or _within(addon, save):
        raise TransactionError("Save and addon destinations must be separate")
    if addon.name.casefold() != folder_name.casefold():
        raise TransactionError(f"Addon folder must be {folder_name!r} to match the save requirement")
    if save.exists() and not in_place:
        raise TransactionError("Save-copy destination already exists; choose a new filename")
    with phase('Install: verify previous addon'):
        previous = _owned_addon(addon, journal["addon_mod"]) if addon.exists() else None
    addon.parent.mkdir(parents=True, exist_ok=True)
    save.parent.mkdir(parents=True, exist_ok=True)
    temp_addon = addon.with_name(f".{addon.name}.mw-stage-{journal['transaction_id']}")
    backup_addon = addon.with_name(f".{addon.name}.mw-backup-{journal['transaction_id']}")
    temp_save = save.with_name(f".{save.name}.mw-stage-{journal['transaction_id']}")
    if any(p.exists() for p in (temp_addon, backup_addon, temp_save)):
        raise TransactionError("Publication scratch paths already exist")
    publication = {"addon_destination": str(addon), "save_destination": str(save),
                   "temp_addon": str(temp_addon), "backup_addon": str(backup_addon), "temp_save": str(temp_save),
                   "previous_addon_hashes": previous, "installed_addon_hashes": None,
                   "allow_install": bool(allow_install), "in_place": bool(in_place),
                   "addon_intent": False, "save_intent": False}
    journal["publication"] = publication
    _event(journal, "committing", "Publication intent recorded; source remains read-only")
    _atomic_json(path, journal)
    try:
        temp_addon.mkdir(exist_ok=False)
        staged_root = Path(journal["staged_addon"])
        with phase('Install: copy new addon'):
            # Files identical to the installed addon (usually all but the new
            # vehicle) are hard-linked from it instead of copied.
            reuse = {name: addon.joinpath(*PurePosixPath(name).parts)
                     for name, digest in journal["addon_hashes"].items()
                     if previous and previous.get(name) == digest}
            linked = _write_addon_files(temp_addon, journal["addon_hashes"],
                                        lambda name: staged_root.joinpath(*PurePosixPath(name).parts).read_bytes(),
                                        defer_sync=True, reuse=reuse)
        marker = {"schema_version": 1, "transaction_id": journal["transaction_id"],
                  "addon_mod": journal["addon_mod"], "files": journal["addon_hashes"]}
        marker_bytes = json.dumps(marker, sort_keys=True).encode("utf-8")
        _write_file(temp_addon / OWNERSHIP_FILE, marker_bytes)
        expected_hashes = {**journal["addon_hashes"], OWNERSHIP_FILE: _sha(marker_bytes)}
        with phase('Install: verify publication copy'):
            # All copied files are flushed to disk, read and compared before
            # publishing. Grouping writes first avoids thousands of competing
            # small synchronous write/flush cycles on Windows.
            # Hard links are the installed files themselves: their digest is
            # still computed (from the cache when unchanged) but not flushed again.
            copied_hashes = _tree_hashes(temp_addon,durable=True,settled=linked)
        if copied_hashes != expected_hashes:
            raise TransactionError("Addon publication copy failed hash validation")
        publication["installed_addon_hashes"] = copied_hashes
        _write_file(temp_save, Path(journal["staged_save"]).read_bytes())
        if _file_hash(temp_save) != journal["save_hash"]:
            raise TransactionError("Save publication copy failed hash validation")
        # Recheck the live source and game immediately before the first change.
        assert_game_closed()
        _validate_source(journal)
        publication["addon_intent"] = True
        _atomic_json(path, journal)
        if addon.exists():
            with phase('Install: recheck previous addon'):
                if _tree_hashes(addon) != previous:
                    raise TransactionError("Existing addon changed during preparation")
            os.replace(addon, backup_addon)
        os.replace(temp_addon, addon)
        _event(journal, "committing", "Addon published; save copy still staged")
        _atomic_json(path, journal)
        assert_game_closed()
        _validate_source(journal)
        if save.exists() and not in_place:
            raise TransactionError("Save destination appeared during publication")
        publication["save_intent"] = True
        _atomic_json(path, journal)
        os.replace(temp_save, save)
        _event(journal, "committed", "Addon and independent save copy published and hashed")
        _atomic_json(path, journal)
        with phase('Install: archive previous addon'):
            _archive_previous_addon(path, journal, backup_addon, op)
        return {"state": "committed", **publication}
    except Exception as exc:
        _event(journal, "failed", f"Publication failed: {type(exc).__name__}: {exc}")
        _atomic_json(path, journal)
        try:
            rollback_transaction(path)
        except Exception as rollback_error:
            raise TransactionError(f"Publication failed; recovery required at {path}: {rollback_error}") from exc
        raise TransactionError(f"Publication failed and was rolled back: {exc}") from exc


def _archive_previous_addon(path: Path, journal: dict, backup_addon: Path, op: Path) -> None:
    """Move the replaced addon out of the game's mods folder after success.

    On one volume rename the verified backup instead of copying every file.
    Across volumes copy and verify before removal. Record the exact archive
    destination first so an interruption after rename remains recoverable.
    """
    publication = journal["publication"]
    if not backup_addon.exists():
        return
    archive = op / "previous_addon"
    try:
        _no_symlink(archive);_no_symlink(backup_addon)
        if archive.resolve()!=op.resolve()/'previous_addon' or backup_addon.resolve()!=Path(publication['backup_addon']).resolve():
            raise TransactionError('Previous addon archive paths failed validation')
        if archive.exists():raise TransactionError('Previous addon archive already exists')
        expected=publication['previous_addon_hashes']
        if _tree_hashes(backup_addon)!=expected:
            raise TransactionError('Previous addon backup changed before archiving')
        publication['archived_backup']=str(archive)
        _atomic_json(path,journal)
        try:
            os.replace(long_path(backup_addon),long_path(archive))
            publication['archive_method']='rename'
        except OSError as error:
            if error.errno!=errno.EXDEV and getattr(error,'winerror',None)!=17:raise
            shutil.copytree(long_path(backup_addon),long_path(archive))
            publication['archive_method']='copy'
        if _tree_hashes(archive) != expected:
            raise TransactionError("Archived previous addon failed hash validation")
        if backup_addon.exists():
            if _tree_hashes(backup_addon)!=expected:
                raise TransactionError('Previous addon backup changed while copying')
            shutil.rmtree(long_path(backup_addon))
        _event(journal, "committed", "Previous addon archived outside the game folder")
    except (OSError, TransactionError) as error:
        _event(journal, "committed", f"Previous addon backup left in place: {error}")
    _atomic_json(path, journal)


def rollback_transaction(journal_path: str | Path) -> dict:
    """Undo only this operation's verified outputs; retain staged files/backups."""
    assert_game_closed()
    path, journal = _read_journal(journal_path)
    if journal["state"] == "rolled_back":
        return {"state": "rolled_back", "journal_path": str(path)}
    publication = journal.get("publication")
    if publication:
        workspace, op = Path(journal["workspace"]), Path(journal["operation_dir"])
        addon, save = Path(publication["addon_destination"]).resolve(), Path(publication["save_destination"]).resolve()
        folder = journal["addon_mod"].split(":")[0]
        expected_backup = addon.with_name(f".{addon.name}.mw-backup-{journal['transaction_id']}")
        expected_temp_addon = addon.with_name(f".{addon.name}.mw-stage-{journal['transaction_id']}")
        expected_temp_save = save.with_name(f".{save.name}.mw-stage-{journal['transaction_id']}")
        if addon.name.casefold() != folder.casefold() or Path(publication["backup_addon"]) != expected_backup:
            raise TransactionError("Journal publication paths failed validation")
        if Path(publication["temp_addon"]) != expected_temp_addon or Path(publication["temp_save"]) != expected_temp_save:
            raise TransactionError("Journal scratch paths failed validation")
        in_place = bool(publication.get("in_place"))
        original = op / "backup" / "original.sav"
        if (save == Path(journal["source_path"]).resolve() and not in_place) or in_place and save != Path(journal["source_path"]).resolve()                 or _within(save, op / "backup") or _within(addon, op / "backup"):
            raise TransactionError("Rollback cannot touch source or original backup")
        if in_place and _file_hash(original) != journal["source_hash"]:
            raise TransactionError("Original save backup failed validation; rollback refused")
        if not publication["allow_install"] and (not _within(addon, workspace) or not _within(save, workspace)):
            raise TransactionError("Rollback destinations escape the approved workspace")
        for destination in (addon, save, expected_backup, expected_temp_addon, expected_temp_save):
            _no_symlink(destination)
        # Verify all outputs before touching any, so an external edit never leads
        # to a partial destructive rollback.
        if in_place:
            if save.exists() and _file_hash(save) not in (journal["save_hash"], journal["source_hash"]):
                raise TransactionError("Campaign save changed after publication; rollback refused")
        elif save.exists() and (not publication["save_intent"] or _file_hash(save) != journal["save_hash"]):
            raise TransactionError("Published save copy changed externally; rollback refused")
        actual_addon = _tree_hashes(addon) if addon.exists() else None
        new_hashes, old_hashes = publication["installed_addon_hashes"], publication["previous_addon_hashes"]
        if actual_addon is not None and actual_addon not in (new_hashes, old_hashes):
            raise TransactionError("Published addon changed externally; rollback refused")
        if expected_backup.exists() and _tree_hashes(expected_backup) != old_hashes:
            raise TransactionError("Previous addon backup changed externally; rollback refused")
        archived_backup = Path(publication['archived_backup']) if publication.get('archived_backup') else None
        if archived_backup is not None:
            if archived_backup.resolve()!=op.resolve()/'previous_addon':
                raise TransactionError('Archived previous addon path failed validation')
            _no_symlink(archived_backup)
            # An interrupted cross-volume copy may leave a partial archive.
            # If the original verified backup still exists, restore that one
            # and leave the unused partial copy untouched for inspection.
            if not expected_backup.exists() and archived_backup.exists() and _tree_hashes(archived_backup)!=old_hashes:
                raise TransactionError('Archived previous addon changed; rollback refused')
        if old_hashes is not None and actual_addon==new_hashes and not expected_backup.exists() and not (archived_backup and archived_backup.is_dir()):
            raise TransactionError('Previous addon backup is missing; rollback refused')
        if expected_temp_save.exists() and _file_hash(expected_temp_save) != journal["save_hash"]:
            raise TransactionError("Temporary save copy changed externally; rollback refused")
        if expected_temp_addon.exists():
            temporary_hashes = _tree_hashes(expected_temp_addon)
            if temporary_hashes not in (journal["addon_hashes"], new_hashes):
                raise TransactionError("Temporary addon changed externally; rollback refused")
        _event(journal, "rolling_back", "Verified ownership and hashes before recovery")
        _atomic_json(path, journal)
        if in_place:
            if save.exists() and _file_hash(save) == journal["save_hash"]:
                archived = op / "rollback" / "published.sav"
                if not archived.exists():
                    _write_file(archived, save.read_bytes())
                restore = save.with_name(f".{save.name}.mw-restore-{journal['transaction_id']}")
                _write_file(restore, original.read_bytes(), exclusive=False)
                os.replace(restore, save)
        elif save.exists():
            # Keep the generated copy in the operation archive, never silently
            # discard an artifact that could help diagnose game-load behavior.
            archived = op / "rollback" / "published.sav"
            if not archived.exists():
                _write_file(archived, save.read_bytes())
            save.unlink()
        if actual_addon == new_hashes and actual_addon is not None:
            # Absolute target was verified against the stored exact destination,
            # ownership marker, hash manifest, and symlink-free ancestry above.
            shutil.rmtree(long_path(addon))
        if expected_backup.exists():
            if addon.exists():
                raise TransactionError("Cannot restore addon while destination exists")
            os.replace(long_path(expected_backup), long_path(addon))
        elif archived_backup and archived_backup.is_dir() and _within(archived_backup, op):
            if _tree_hashes(archived_backup) != old_hashes:
                raise TransactionError("Archived previous addon changed; rollback refused")
            if addon.exists():
                raise TransactionError("Cannot restore addon while destination exists")
            shutil.copytree(long_path(archived_backup), long_path(addon))
        if expected_temp_save.exists():
            expected_temp_save.unlink()
        if expected_temp_addon.exists():
            shutil.rmtree(long_path(expected_temp_addon))
    _event(journal, "rolled_back", "This operation's publication undone; original save remains untouched")
    _atomic_json(path, journal)
    return {"state": "rolled_back", "journal_path": str(path), "source_hash": journal["source_hash"]}


# Bulky copies an operation no longer needs once newer ones exist: the staged
# addon (identical to what was installed), the archived previous addon and
# compile/staging scratch. Only the newest installation can be rolled back
# (an older rollback is refused once the addon changed again), so these are
# kept for the most recent operations only. Journals, plans and the backup of
# the original save stay forever (about half a megabyte each).
_PRUNABLE = ("stage/addon", "previous_addon", "compile", "modular_workshop")
_SETTLED = {"committed", "rolled_back", "failed", "prepared"}


def prune_operations(workspace: str | Path, keep: int = 3) -> dict:
    root = Path(workspace).resolve() / "operations"
    if not root.is_dir():
        return {"pruned": [], "freed_bytes": 0}
    _no_symlink(root)
    def bulky(item):
        return (item.is_dir() and not item.is_symlink()
                and (any((item / name).is_dir() for name in _PRUNABLE)
                     or any(item.glob("operations/*/previous_addon")) or any(item.glob("operations/*/stage/addon"))))
    heavy = sorted((item for item in root.iterdir() if bulky(item)), key=lambda item: item.name)
    pruned, freed = [], 0
    for operation in heavy[:-keep] if keep > 0 else heavy:
        states = set()
        for journal in operation.glob("operations/*/journal.json"):
            try:
                states.add(json.loads(journal.read_text(encoding="utf-8")).get("state"))
            except (OSError, ValueError):
                states.add(None)
        if states - _SETTLED:
            continue  # an interrupted operation keeps everything for recovery
        targets = [operation / name for name in _PRUNABLE]
        targets += [Path(path) for pattern in ("operations/*/stage/addon", "operations/*/previous_addon")
                    for path in operation.glob(pattern)]
        removed = []
        for target in targets:
            if not target.is_dir() or target.is_symlink() or not _within(target.resolve(), root):
                continue
            size = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
            shutil.rmtree(long_path(target))
            freed += size
            removed.append(target.relative_to(operation).as_posix())
        if removed:
            note = operation / "pruned.json"
            previous = json.loads(note.read_text(encoding="utf-8")) if note.is_file() else []
            previous.append({"time_utc": datetime.now(timezone.utc).isoformat(), "removed": removed})
            note.write_text(json.dumps(previous, indent=2), encoding="utf-8")
            pruned.append(operation.name)
    return {"pruned": pruned, "freed_bytes": freed}
