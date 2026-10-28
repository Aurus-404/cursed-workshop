"""Read-only GEM virtual filesystem, with explicit mod order and lazy ZIP reads.

No supplied program is executed. PAKs are standard ZIP containers; entries stay
in their archives until requested. Later explicit mod roots override earlier
ones; loose resources override PAKs within each root. The installed engine's
mount order must still be checked during game acceptance, especially for DLC.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Iterable


class ResourceError(ValueError):
    pass


def normalize_path(path: str) -> str:
    """Normalize a game-absolute or resource-relative path, without traversal."""
    value = str(path).replace("\\", "/").strip()
    if "\x00" in value or ":" in value or value.startswith("//"):
        raise ResourceError(f"Unsafe virtual path: {path!r}")
    value = value.lstrip("/")
    parts = value.split("/")
    if ".." in parts:
        raise ResourceError(f"Parent traversal is not allowed: {path!r}")
    value = "/".join(part for part in parts if part and part != ".")
    if value.casefold().startswith("resource/"):
        value = value[len("resource/"):]
    if not value:
        raise ResourceError("Empty virtual path")
    return value


# Member lists of PAK archives, reused while the tool runs: the game's 64
# archives never change between the several index builds of one session
# (open, install, portraits, reopen). Keyed by path, size and mtime.
_PAK_MEMBERS: dict[tuple, list] = {}
_PAK_LOCK = threading.Lock()


def _pak_members(pak: Path, mount: str) -> list:
    """(virtual path or None if unsafe, member name, size, crc) of a PAK."""
    stat = pak.stat()
    key = (os.path.normcase(str(pak)), mount, stat.st_size, stat.st_mtime_ns)
    with _PAK_LOCK:
        cached = _PAK_MEMBERS.get(key)
    if cached is not None:
        return cached
    members = []
    with zipfile.ZipFile(pak) as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            try:
                # ZIP members are never extracted; still reject malicious names.
                if item.filename.startswith(("/", "\\")):
                    raise ResourceError("Absolute ZIP member")
                virtual = normalize_path(mount + item.filename)
            except ResourceError:
                virtual = None
            members.append((virtual, item.filename, item.file_size, item.CRC))
    with _PAK_LOCK:
        _PAK_MEMBERS[key] = members
    return members


def _loose_files(folder: Path, root: Path, warnings: list) -> list[Path]:
    """Files under folder without following links. Only a link can point
    outside the source root, so only links are resolved and checked (one
    realpath per file before)."""
    found = []
    for current, directories, files in os.walk(folder, followlinks=False):
        base = Path(current)
        for name in list(directories):
            path = base / name
            if path.is_symlink() or os.path.isjunction(path):
                directories.remove(name)
                if not path.resolve().is_relative_to(root):
                    warnings.append(f"Skipped loose file outside source root: {path}")
                else:
                    found.extend(item for item in path.rglob("*") if item.is_file()
                                 and item.resolve().is_relative_to(root))
        for name in files:
            path = base / name
            if path.is_symlink() and not path.resolve().is_relative_to(root):
                warnings.append(f"Skipped loose file outside source root: {path}")
                continue
            found.append(path)
    return found


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class ResourceEntry:
    path: str
    source_root: str
    origin: str
    source_id: str
    version: str
    size: int
    priority: int
    physical_path: str | None = None
    archive_path: str | None = None
    archive_member: str | None = None
    crc: int | None = None
    modified_ns: int | None = None

    def as_dict(self) -> dict:
        return {key: getattr(self, key) for key in self.__slots__}

    def __getitem__(self, key):
        return getattr(self, key)


def _mod_metadata(root: Path, game: bool = False) -> dict:
    info = root / "mod.info"
    text = decode_text(info.read_bytes()) if info.is_file() else ""
    name = re.search(r'\{\s*name\s+"([^"]+)"', text, re.I)
    desc = re.search(r'\{\s*desc\s+"([^"]+)"', text, re.I)
    version = re.search(r'\{\s*version\s+"?([^"}\s]+)', text, re.I)
    manifest = root.parent.parent / "appmanifest_400750.acf" if game else root.parent.parent.parent / "appworkshop_400750.acf"
    stamp = hashlib.sha256(info.read_bytes()).hexdigest()[:16] if info.is_file() else ""
    if game and manifest.is_file():
        match = re.search(r'"buildid"\s+"(\d+)"', decode_text(manifest.read_bytes()), re.I)
        if match:
            stamp = "build:" + match.group(1)
    if not game and manifest.is_file() and root.name.isdecimal():
        manifest_text = decode_text(manifest.read_bytes())
        section = re.search(r'"' + re.escape(root.name) + r'"\s*\{([^}]+)\}', manifest_text)
        if section:
            match = re.search(r'"manifest"\s+"(\d+)"', section.group(1))
            if match:
                stamp += ":workshop:" + match.group(1)
    return {"id": "vanilla" if game else root.name,
            "name": "Gates of Hell" if game else name.group(1) if name else root.name,
            "root": str(root), "description": desc.group(1) if desc else "",
            "version": version.group(1) if version else stamp,
            "require": re.findall(r'\{\s*require\s+"([^"]+)"', text, re.I)}


_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
_GAME_VERSIONS: dict[tuple, tuple | None] = {}


def _parse_game_version(text: str):
    """"1.067.0" / "1.067" -> (1, 67, 0); "any" or unreadable -> None (no limit)."""
    match = _VERSION.fullmatch(str(text).strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def game_version(game_root: str | Path):
    """Version of the installed game as (1, 67, 0), or None if it cannot be read.

    The executable's version resource reads 1.0.6.7 for v1.067.0; the last part
    comes from game.log's header when that header is the same release.
    """
    exe = Path(game_root) / "binaries" / "x64" / "call_to_arms.exe"
    try:
        key = (str(exe), exe.stat().st_mtime_ns)
    except OSError:
        return None
    if key in _GAME_VERSIONS:
        return _GAME_VERSIONS[key]
    version = None
    try:
        import ctypes
        from ctypes import wintypes
        api = ctypes.windll.version
        size = api.GetFileVersionInfoSizeW(str(exe), None)
        buffer = ctypes.create_string_buffer(size)
        if size and api.GetFileVersionInfoW(str(exe), 0, size, buffer):
            pointer, length = ctypes.c_void_p(), wintypes.UINT()
            if api.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
                ms, ls = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 4)).contents[2:4]
                version = (ms >> 16, (ms & 0xffff) * 100 + (ls >> 16) * 10 + (ls & 0xffff), 0)
    except (AttributeError, OSError, ValueError):
        version = None
    from .locations import game_log_path
    log = game_log_path()
    try:
        header = re.search(r"GATES OF HELL - v(\d+\.\d+\.\d+)", log.read_bytes()[:8192].decode("latin-1"))
        logged = _parse_game_version(header.group(1)) if header else None
        if logged and (version is None or logged[:2] == version[:2]):
            version = logged
    except OSError:
        pass
    _GAME_VERSIONS[key] = version
    return version


def disabled_reason(root: Path, version) -> str | None:
    """Why the game would switch this mod off, like it does at start ("Versión de
    juego no compatible"); None when it loads. Unknown versions never disable."""
    if version is None:
        return None
    try:
        text = decode_text((Path(root) / "mod.info").read_bytes())
    except OSError:
        return None
    low = re.search(r'\{\s*minGameVersion\s+"([^"]*)"', text, re.I)
    high = re.search(r'\{\s*maxGameVersion\s+"([^"]*)"', text, re.I)
    low = _parse_game_version(low.group(1)) if low else None
    high = _parse_game_version(high.group(1)) if high else None
    shown = ".".join(f"{part:03d}" if index == 1 else str(part) for index, part in enumerate(version))
    if low and version < low:
        return f"needs game {'.'.join(map(str, low))} or newer (game is {shown})"
    if high and version > high:
        return f"made for game up to {high[0]}.{high[1]:03d}.{high[2]} (game is {shown})"
    return None


def _pak_order(path: Path) -> tuple:
    # Expansion packages overlay base archives. Numbers are numeric, not lexical.
    name = path.stem.casefold()
    match = re.search(r"(?:^|_)dlc(\d+)", name)
    return (1, int(match.group(1)), str(path).casefold()) if match else (0, 0, str(path).casefold())


class ResourceIndex:
    """Effective resource index; mod_roots MUST be the campaign's active order."""

    def __init__(self, game_root: str | Path, mod_roots: Iterable[str | Path] = ()):
        self.game_root = Path(game_root).resolve()
        self.mod_roots = [Path(root).resolve() for root in mod_roots]
        self.entries: dict[str, ResourceEntry] = {}
        self.collisions: dict[str, list[ResourceEntry]] = {}
        self.sources: list[dict] = []
        self.warnings: list[str] = []
        self.disabled_mods: list[dict] = []
        self._archives: dict[str, zipfile.ZipFile] = {}
        self._units: list[dict] | None = None
        self._entity_by_id: dict[str, list[str]] = {}
        self._resolution_cache: dict[str, dict] = {}
        self._labels: dict[str, str] | None = None
        self._source_digests: dict[int, object] = {}
        self._lock = threading.RLock()
        self._base_lock = threading.Lock()  # the catalog warmup and the player may both ask for it
        self.built = False

    def close(self):
        registration=self.__dict__.pop('_registration_source_view',None)
        if registration is not None:registration.close()
        for view in self.__dict__.pop('_retired_mountable_views', []):
            view.close()
        view = self.__dict__.pop('_mountable_game_view', None)
        if view is not None:
            view.close()
        for context in self.__dict__.pop('_reopened_sources',{}).values():
            for view in reversed(context['views']):view.close()
            context['temp'].cleanup()
        self.__dict__.pop('_registration_graphs', None)
        self.__dict__.pop('_weapon_labels', None)
        self.__dict__.pop('_commander_weapons', None)
        for key in ('_armor_catalog','_armor_items','_armor_material_includes','_armor_defines','_armor_base_volumes','_support_catalog',
                    '_commander_weapon_items','_commander_support_items','_commander_supports','_rider_catalog',
                    '_dlc_log_stamp','_dlc_log_state','_mountable_state'):
            self.__dict__.pop(key,None)
        base = self.__dict__.pop('_base_game_view', None)
        if base is not None:
            base.close()
        for archive in self._archives.values():
            archive.close()
        self._archives.clear()

    def base_game(self):
        """Archive-only stock view, before DLC overlays and mods (no disk writes).

        Common archives also contain DLC items. Callers must additionally use
        set/registry/dlc.reg; archive provenance alone is not an entitlement.
        """
        with self._base_lock:
            return self._build_base_game()

    def _build_base_game(self):
        if '_base_game_view' not in self.__dict__:
            base = ResourceIndex(self.game_root)
            for key, effective in self.entries.items():
                candidates = self.collisions.get(key, [effective])
                stock = [entry for entry in candidates if entry.source_id == 'vanilla'
                         and entry.archive_path and not re.search(
                             r'(?:^|_)dlc\d+', Path(entry.archive_path).stem, re.I)]
                if stock:
                    base.entries[key] = stock[-1]
            for entry in base.entries.values():
                if entry.path.startswith('entity/') and entry.path.endswith('.def'):
                    base._entity_by_id.setdefault(PurePosixPath(entry.path).stem.casefold(), []).append(entry.path)
            base.sources = self.sources[:1]
            base.built = True
            self._base_game_view = base
        return self._base_game_view

    def dlc_availability(self):
        from .dlc import read_availability
        with self._base_lock:
            return read_availability(self)

    def mountable_game(self):
        """Official weapon resources, including DLC verified in the last game run.

        Mounts still use base_game(). Mods/loose overrides are never smuggled
        into the official weapon catalog. A changed scan invalidates memory and
        disk catalog identities, even if all installed PAKs stayed identical.
        """
        from .dlc import read_availability, dlc_archive, _path
        with self._base_lock:
            state = read_availability(self)
            if self.__dict__.get('_mountable_state') != state:
                old = self.__dict__.pop('_mountable_game_view', None)
                if old is not None:
                    # Another catalog reader may still hold the previous view.
                    self.__dict__.setdefault('_retired_mountable_views', []).append(old)
                for key in ('_commander_weapons', '_commander_weapon_items', '_rider_catalog'):
                    self.__dict__.pop(key, None)
                self._mountable_state = state
            if '_mountable_game_view' not in self.__dict__:
                view = ResourceIndex(self.game_root)
                for key, effective in self.entries.items():
                    candidates = self.collisions.get(key, [effective])
                    official = [e for e in candidates if e.source_id == 'vanilla' and e.archive_path
                                and (not dlc_archive(e.archive_path) or _path(e.archive_path) in state.archives)]
                    if official:
                        view.entries[key] = official[-1]
                for entry in view.entries.values():
                    if entry.path.startswith('entity/') and entry.path.endswith('.def'):
                        view._entity_by_id.setdefault(PurePosixPath(entry.path).stem.casefold(), []).append(entry.path)
                view.sources = self.sources[:1]
                view._available_dlc = frozenset(state.available)
                view.built = True
                self._mountable_game_view = view
            return self._mountable_game_view

    def registration_sources(self):
        """Read registrations from active inputs, never a previous generated copy.

        Keep generated entities for chained designs; only registration globals
        and their include trees fall back through collision history.
        """
        from .registration import is_own_mod
        if self.__dict__.get('_registration_authority'):return self
        if not any(is_own_mod(s['id']) for s in self.sources):return self
        with self._base_lock:
            if '_registration_source_view' not in self.__dict__:
                view=ResourceIndex(self.game_root,self.mod_roots)
                for key,effective in self.entries.items():
                    candidates=self.collisions.get(key,[effective])
                    registration=(key.startswith(('set/interaction_entity/','set/multiplayer/units/'))
                                  or key in ('set/registry/unit.reg','set/tp_control.set'))
                    if '/modular_workshop/' in key:registration=False
                    kept=[e for e in candidates if not registration or not is_own_mod(e.source_id)]
                    for entry in kept:view._put(entry)
                for entry in view.entries.values():
                    if entry.path.startswith('entity/') and entry.path.endswith('.def'):
                        view._entity_by_id.setdefault(PurePosixPath(entry.path).stem.casefold(),[]).append(entry.path)
                view.sources=list(self.sources);view.disabled_mods=list(self.disabled_mods);view.built=True
                view._registration_authority=True
                self._registration_source_view=view
            return self._registration_source_view

    def with_overlay(self, root: str | Path, *, exclude_roots=()):
        """Independent read-only view of this session plus freshly scanned output.

        Reuse immutable entry records, never open ZIP handles or resolved entity
        caches. Excluded sources are removed from collision history as well as
        effective entries, so an old workshop override cannot leak into a portrait.
        Publication still builds a fresh installation index for dependency checks.
        """
        from .cancellation import checkpoint
        if not self.built:
            raise ResourceError('Build the resource index before adding an overlay')
        root=Path(root).resolve()
        excluded={str(Path(p).resolve()).casefold() for p in (*exclude_roots,root)}
        if str(self.game_root).casefold() in excluded:
            raise ResourceError('An overlay cannot replace the game resource root')
        # Reuse the ordinary scanner, including archive order, path limits and
        # localization rules. Only this generated directory is enumerated.
        scanned=ResourceIndex(root).build()
        view=ResourceIndex(self.game_root,[p for p in self.mod_roots if str(p).casefold() not in excluded]+[root])
        try:
            with self._lock:
                view.sources=[dict(s) for s in self.sources if str(Path(s['root']).resolve()).casefold() not in excluded]
                for key,effective in self.entries.items():
                    checkpoint()
                    entries=[e for e in self.collisions.get(key,[effective]) if e.source_root.casefold() not in excluded]
                    if entries:
                        view.entries[key]=entries[-1]
                        if len(entries)>1:view.collisions[key]=list(entries)
                view.warnings=list(self.warnings)
            priority=max(s['priority'] for s in view.sources)+1
            source=_mod_metadata(root);source['priority']=priority
            source['fingerprint']=scanned.sources[0]['fingerprint']
            view.sources.append(source)
            for key,effective in scanned.entries.items():
                for entry in scanned.collisions.get(key,[effective]):
                    checkpoint()
                    view._put(replace(entry,source_id=source['id'],origin=source['name'],version=source['version'],priority=priority))
            for entry in view.entries.values():
                if entry.path.casefold().startswith('entity/') and entry.path.casefold().endswith('.def'):
                    view._entity_by_id.setdefault(PurePosixPath(entry.path).stem.casefold(),[]).append(entry.path)
            view.built=True
            return view
        except BaseException:
            view.close();raise
        finally:
            scanned.close()

    def _put(self, entry: ResourceEntry):
        digest = self._source_digests.setdefault(entry.priority, hashlib.sha256())
        stamp = entry.modified_ns if entry.physical_path else entry.crc
        digest.update(f"{entry.path.casefold()}\0{entry.size}\0{stamp}\n".encode("utf-8"))
        key = entry.path.casefold()
        old = self.entries.get(key)
        if old:
            self.collisions.setdefault(key, [old]).append(entry)
        self.entries[key] = entry

    def build(self) -> "ResourceIndex":
        with self._lock:
            self.close()
            self.entries.clear()
            self.collisions.clear()
            self.sources.clear()
            self.warnings.clear()
            self._resolution_cache.clear()
            self._entity_by_id.clear()
            self._units = None
            self._labels = None
            self._source_digests.clear()
            if not (self.game_root / "resource").is_dir():
                raise ResourceError(f"Game resource folder missing: {self.game_root}")
            roots = [self.game_root, *self.mod_roots]
            if len({str(root).casefold() for root in roots}) != len(roots):
                raise ResourceError("Duplicate source roots in active mod order")
            version = game_version(self.game_root)
            self.disabled_mods = []
            for priority, root in enumerate(roots):
                from .cancellation import checkpoint
                checkpoint()
                if not root.is_dir():
                    raise ResourceError(f"Active mod folder missing: {root}. Refresh its path in Settings or reinstall it through Steam.")
                reason = disabled_reason(root, version) if priority else None
                if reason:
                    # The game switches such mods off at start; reading their files
                    # would copy references to resources the game never mounts.
                    name = _mod_metadata(root)["name"]
                    self.disabled_mods.append({"root": str(root), "name": name, "reason": reason})
                    self.warnings.append(f"Mod {name} is disabled by the game: {reason}")
                    continue
                resource_root = root / "resource"
                if resource_root.exists() and not resource_root.is_dir():
                    raise ResourceError(f"Active mod resource path is not a folder: {resource_root}")
                if not resource_root.is_dir() and not (root / "mod.info").is_file():
                    raise ResourceError(f"Active mod resource folder missing and no mod.info found: {root}. Select the installed mod root in Settings.")
                source = _mod_metadata(root, priority == 0)
                source["priority"] = priority
                self.sources.append(source)
                if not resource_root.is_dir():
                    # A localization-only addon or external Workshop tool can
                    # have mod.info without game resources. Keep its identity,
                    # still index localizations, and never invent missing files.
                    self.warnings.append(f"Mod {source['name']} has no resource folder; metadata and any localizations are indexed: {root}")
                pak_paths = sorted(resource_root.rglob("*.pak"), key=_pak_order)
                for pak in pak_paths:
                    if not pak.resolve().is_relative_to(root):
                        self.warnings.append(f"Skipped archive outside source root: {pak}")
                        continue
                    mount = pak.parent.relative_to(resource_root).as_posix()
                    mount = "" if mount == "." else mount + "/"
                    try:
                        members = _pak_members(pak, mount)
                    except (zipfile.BadZipFile, OSError) as exc:
                        raise ResourceError(f"Unreadable PAK {pak}: {exc}") from exc
                    checkpoint()
                    pak_name = str(pak)
                    for virtual, member, size, crc in members:
                        if virtual is None:
                            self.warnings.append(f"Skipped unsafe member: {pak.name}:{member}")
                            continue
                        self._put(ResourceEntry(virtual, str(root), source["name"], source["id"],
                                                source["version"], size, priority,
                                                archive_path=pak_name, archive_member=member, crc=crc))
                for loose in sorted(_loose_files(resource_root, root, self.warnings), key=lambda item: str(item).casefold()):
                    checkpoint()
                    if loose.suffix.casefold() == ".pak":
                        continue
                    virtual = normalize_path(loose.relative_to(resource_root).as_posix())
                    loose_stat = loose.stat()
                    self._put(ResourceEntry(virtual, str(root), source["name"], source["id"], source["version"],
                                            loose_stat.st_size, priority, physical_path=str(loose), modified_ns=loose_stat.st_mtime_ns))
                localization_root = root / "localizations"
                if localization_root.is_dir():
                    for pak in sorted(localization_root.rglob("*.pak"), key=_pak_order):
                        if not pak.resolve().is_relative_to(root):
                            continue
                        with zipfile.ZipFile(pak) as archive:
                            for item in archive.infolist():
                                if item.is_dir():
                                    continue
                                try:
                                    virtual = normalize_path("localizations/" + item.filename)
                                except ResourceError:
                                    continue
                                self._put(ResourceEntry(virtual, str(root), source["name"], source["id"],
                                                        source["version"], item.file_size, priority,
                                                        archive_path=str(pak), archive_member=item.filename, crc=item.CRC))
                    for loose in localization_root.rglob("*"):
                        if loose.is_file() and loose.suffix.casefold() != ".pak" and loose.resolve().is_relative_to(root):
                            virtual = "localizations/" + loose.relative_to(localization_root).as_posix()
                            loose_stat = loose.stat()
                            self._put(ResourceEntry(virtual, str(root), source["name"], source["id"], source["version"],
                                                    loose_stat.st_size, priority, physical_path=str(loose), modified_ns=loose_stat.st_mtime_ns))
            for entry in self.entries.values():
                if entry.path.casefold().startswith("entity/") and entry.path.casefold().endswith(".def"):
                    template = PurePosixPath(entry.path).stem.casefold()
                    self._entity_by_id.setdefault(template, []).append(entry.path)
            for source in self.sources:
                source["fingerprint"] = self._source_digests.get(source["priority"], hashlib.sha256()).hexdigest()
            self.built = True
            self.warnings.append("Resource precedence: base PAKs, installed DLC PAKs, loose files, then explicit mods in input order. Confirm engine DLC/mount order in-game.")
            return self

    def resolve(self, path: str) -> ResourceEntry:
        normalized = normalize_path(path)
        try:
            return self.entries[normalized.casefold()]
        except KeyError:
            raise FileNotFoundError(f"Resource not found: {normalized}") from None

    def exists(self, path: str) -> bool:
        return normalize_path(path).casefold() in self.entries

    def read_bytes(self, path: str, *, max_bytes: int = 128 * 1024 * 1024) -> bytes:
        from .cancellation import checkpoint
        checkpoint()
        entry = self.resolve(path)
        if entry.size > max_bytes:
            raise ResourceError(f"Resource exceeds read limit ({max_bytes} bytes): {entry.path}")
        with self._lock:
            self._check_unchanged(entry)
            if entry.physical_path:
                with Path(entry.physical_path).open("rb") as file:
                    data = file.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ResourceError("Resource grew beyond the read limit")
                return data
            return self._archives[entry.archive_path].read(entry.archive_member)

    def verify_unchanged(self, path: str) -> None:
        """The change guards of read_bytes, without reading the content."""
        entry = self.resolve(path)
        with self._lock:
            self._check_unchanged(entry)

    def _check_unchanged(self, entry: ResourceEntry) -> None:
        if entry.physical_path:
            physical = Path(entry.physical_path)
            if not physical.resolve().is_relative_to(Path(entry.source_root)):
                raise ResourceError("Resource changed to point outside its source root")
            current_stat = physical.stat()
            if current_stat.st_size != entry.size or current_stat.st_mtime_ns != entry.modified_ns:
                raise ResourceError("Loose resource changed after indexing; rebuild the resource index")
            return
        archive = self._archives.get(entry.archive_path)
        if archive is None:
            archive = self._archives[entry.archive_path] = zipfile.ZipFile(entry.archive_path)
        current = archive.getinfo(entry.archive_member)
        if current.CRC != entry.crc or current.file_size != entry.size:
            raise ResourceError("Archive resource changed after indexing; rebuild the resource index")

    read = read_bytes

    def read_text(self, path: str, **kwargs) -> str:
        return decode_text(self.read_bytes(path, **kwargs))

    def resolve_reference(self, reference: str, from_path: str, extensions: tuple[str, ...] = ()) -> str:
        reference = reference.replace("\\", "/")
        candidates = []
        if reference.startswith("/") or reference.casefold().startswith(("resource/", "entity/", "properties/", "set/")):
            candidates.append(normalize_path(reference))
        else:
            # Engine includes legitimately use ../ between sibling folders.
            # Resolve them against an existing virtual source, with a root
            # boundary check; public arbitrary reads still reject traversal.
            parts = list(PurePosixPath(from_path).parent.parts)
            for part in reference.split("/"):
                if part == "..":
                    if not parts:
                        raise ResourceError(f"Reference escapes virtual root: {reference}")
                    parts.pop()
                elif part not in ("", "."):
                    parts.append(part)
            candidates.append(normalize_path("/".join(parts)))
            if ".." not in reference.split("/"):
                candidates.append(normalize_path(reference))
        for candidate in candidates:
            for suffix in ("", *extensions):
                if self.exists(candidate + suffix):
                    return self.resolve(candidate + suffix).path
        raise FileNotFoundError(f"Unresolved reference {reference!r} from {from_path}")

    def resolve_entity(self, template_id: str) -> dict:
        key = re.sub(r"\([^()]*\)$", "", str(template_id)).casefold()
        if key in self._resolution_cache:
            return dict(self._resolution_cache[key])
        paths = self._entity_by_id.get(key, [])
        if not paths and "/" in key:
            candidate = normalize_path(key)
            candidate += "" if candidate.endswith(".def") else ".def"
            if self.exists(candidate):
                paths = [self.resolve(candidate).path]
        if not paths:
            raise FileNotFoundError(f"Entity template not found: {template_id}")
        # Multiple same-ID paths are ambiguous engine names; never silently pick
        # one. Byte-identical copies (MACE ships some twice) are the same entity.
        if len(paths) > 1 and not self._identical_definitions(paths):
            raise ResourceError(f"Ambiguous entity ID {template_id}: {', '.join(paths)}")
        definition = paths[0]
        text = self.read_text(definition)
        from .syntax import parse
        doc = parse(text)
        from .syntax import entity_root
        root = entity_root(doc)
        if root is None:
            raise ResourceError(f"No game_entity in {definition}")
        root, local_files = self._inline_local_includes(root, definition)
        model = root.find("model")
        extension = root.find("extension")
        shared_mdl = extension.args[0] if extension and extension.args and extension.args[0].casefold().endswith(".mdl") else None
        model_ref = model.args[0] if model and model.args else shared_mdl or PurePosixPath(definition).stem
        # An inlined .inc names its model relative to its own folder
        # (bedford_oyd_boys includes ../bedford_oyd/bedford_oyd.inc).
        model_path = None
        for base in (definition, *local_files):
            try:
                model_path = self.resolve_reference(model_ref, base, (".mdl",))
                break
            except FileNotFoundError:
                continue
        ext_paths = []
        for include in root.walk("include"):
            if include.args and include.args[0].casefold().endswith(".ext"):
                try:
                    ext_paths.append(self.resolve_reference(include.args[0], definition))
                except FileNotFoundError:
                    pass
        own_ext = str(PurePosixPath(definition).with_suffix(".ext"))
        if self.exists(own_ext) and own_ext not in ext_paths:
            ext_paths.append(self.resolve(own_ext).path)
        target = root.find("targetclass")
        props = [value for node in root.find_all("props") for value in node.args]
        entry = self.resolve(definition)
        result = {"id": PurePosixPath(definition).stem, "label": self.label_for(key),
                  "kind": target.args[0] if target and target.args else self._classify(definition),
                  "props": list(dict.fromkeys(props)), "definition_path": definition,
                  "model_path": model_path, "extension_path": ext_paths[-1] if ext_paths else None,
                  "extension_paths": ext_paths, "origin": entry.origin, "source_id": entry.source_id,
                  "version": entry.version,
                  "files": [definition, *local_files, *([model_path] if model_path else []), *ext_paths],
                  "provenance": entry.as_dict()}
        self._resolution_cache[key] = result
        return dict(result)

    resolve_template = resolve_entity

    def _identical_definitions(self, paths) -> bool:
        """Same .def and same sibling model in every copy."""
        def signature(path):
            model = path[:-4] + ".mdl"
            return self.read(path), self.read(model) if self.exists(model) else b""
        try:
            first = signature(paths[0])
            return all(signature(path) == first for path in paths[1:])
        except (KeyError, OSError):
            return False

    def _inline_local_includes(self, root, definition: str, depth: int = 0):
        """A copy of root with relative (include "x.inc") blocks spliced in,
        so model/props/includes declared there (MG lafettes) are visible."""
        from .syntax import parse
        if depth > 8:
            raise ResourceError(f"Include nesting too deep in {definition}")
        root, files = root.clone(), []
        for node in list(root.children):
            if node.head.casefold() != "include" or not node.args:
                continue
            reference = node.args[0]
            if reference.startswith("/") or not reference.casefold().endswith((".inc", ".def")):
                continue
            try:
                linked = self.resolve_reference(reference, definition)
            except FileNotFoundError:
                continue
            inner, nested = self._inline_local_includes(parse(self.read_text(linked)), linked, depth+1)
            index = next(i for i, item in enumerate(root.items) if item is node)
            root.items[index:index+1] = inner.items
            files += [linked, *nested]
        return root, files

    def has_entity(self, template_id: str) -> bool:
        return template_id.casefold() in self._entity_by_id

    def label_for(self, template_id: str) -> str:
        if self._labels is None:
            self._labels = {}
            # Current game uses gettext POT, not legacy SDL translations. Labels
            # are optional display data; they never control entity identity.
            entries = sorted((entry for key, entry in self.entries.items()
                              if key.startswith("localizations/default/interface/text/desc/") and key.endswith(".pot")),
                             key=lambda entry: (entry.priority, entry.path.casefold()))
            for entry in entries:
                context = None
                for line in self.read_text(entry.path).splitlines():
                    line = line.strip()
                    if line.startswith('msgctxt "desc/entity/'):
                        context = line[len('msgctxt "desc/entity/'):-1].casefold()
                    elif context and line.startswith('msgid "'):
                        # Multi-line gettext names are rare; leave fallback IDs
                        # for empty fragments rather than displaying incomplete text.
                        value = line[len('msgid "'):-1].replace('\\"', '"')
                        if value:
                            self._labels[context] = value
                        context = None
        return self._labels.get(template_id.casefold(), template_id.replace("_", " "))

    def weapon_label(self, identifier):
        if not hasattr(self,'_weapon_labels'):
            labels={}
            entries=sorted((e for k,e in self.entries.items() if k.startswith('localizations/default/') and k.endswith('.pot') and '/desc/' in k),key=lambda e:(e.priority,e.path))
            for entry in entries:
                context=None
                for line in self.read_text(entry.path).splitlines():
                    line=line.strip()
                    if line.startswith('msgctxt "desc/weapon/'):
                        context=line[len('msgctxt "desc/weapon/'):-1].casefold()
                    elif context and line.startswith('msgid "'):
                        value=line[len('msgid "'):-1].replace('\\"','"')
                        if value:labels[context]=value
                        context=None
            self._weapon_labels=labels
        return self._weapon_labels.get(identifier.casefold(),identifier.replace('_',' '))

    @staticmethod
    def _classify(path: str) -> str:
        value = path.casefold()
        if any(token in value for token in ("-human/", "/human/")):
            return "infantry"
        for tokens, kind in ((("/airborne/", "/aircraft/"), "aircraft"),
                             (("/marine/", "/boat/"), "boat"),
                             (("/cannon/", "/cannons/", "/artillery/"), "artillery"),
                             (("/btr/", "/armoredcar/", "/armored_car/"), "armoredcar"),
                             (("/car/", "/truck/", "/moto/"), "vehicle"),
                             (("/tank", "/heavy/", "/medium/", "/light/"), "tank")):
            if any(token in value for token in tokens):
                return kind
        return "vehicle" if "-vehicle/" in value else "entity"

    def is_wreck(self, template: str) -> bool:
        """Destruction states (`panzer3e_x`, `panzer3e_xx`) of an existing entity:
        the engine swaps them in when the vehicle is destroyed; never a choice."""
        match = re.fullmatch(r"(.+?)_(x{1,2})", str(template).casefold())
        return bool(match) and match.group(1) in self._entity_by_id

    def list_units(self, search: str = "", kinds: Iterable[str] | None = None, limit: int | None = None) -> list[dict]:
        if self._units is None:
            self._units = []
            for template, paths in self._entity_by_id.items():
                if self.is_wreck(template):
                    continue
                for path in paths:
                    kind = self._classify(path)
                    if kind == "entity":
                        continue
                    entry = self.resolve(path)
                    self._units.append({"id": PurePosixPath(path).stem, "label": self.label_for(template),
                                        "kind": kind, "definition_path": path, "origin": entry.origin,
                                        "source_id": entry.source_id, "version": entry.version,
                                        "ambiguous": len(paths) > 1})
            for path, entry in self.entries.items():
                if path.startswith("set/breed/mp/") and path.endswith(".set"):
                    breed = entry.path[len("set/breed/"):-len(".set")]
                    self._units.append({"id": breed, "label": PurePosixPath(breed).name.replace("_", " "),
                                        "kind": "infantry", "breed_path": entry.path, "origin": entry.origin,
                                        "source_id": entry.source_id, "version": entry.version, "ambiguous": False})
            self._units.sort(key=lambda row: (row["kind"], row["id"].casefold()))
        query = search.casefold().strip()
        accepted = {kind.casefold() for kind in kinds} if kinds else None
        rows = [dict(row) for row in self._units if (not accepted or row["kind"].casefold() in accepted)
                and (not query or query in (row["id"] + " " + row["label"] + " " + row["origin"]).casefold())]
        return rows[:limit] if limit else rows

    def search(self, query: str, limit: int = 100) -> list[dict]:
        query = query.casefold()
        return [entry.as_dict() for path, entry in self.entries.items() if query in path][:limit]

    @property
    def stats(self) -> dict:
        return {"resources": len(self.entries), "entity_ids": len(self._entity_by_id),
                "colliding_paths": len(self.collisions), "sources": self.sources, "warnings": self.warnings}


def discover_installed_mods(game_root: str | Path) -> list[dict]:
    """Inventory is informational; returned roots are NOT automatically active."""
    game = Path(game_root).resolve()
    folders = [game / "mods", game.parent.parent / "workshop" / "content" / "400750"]
    output = []
    for folder in folders:
        if folder.is_dir():
            for root in sorted(folder.iterdir(), key=lambda path: path.name.casefold()):
                if root.is_dir() and (root / "mod.info").is_file():
                    output.append(_mod_metadata(root))
    return output


def roots_for_mod_ids(game_root: str | Path, mod_ids: Iterable[str]) -> list[str]:
    installed = discover_installed_mods(game_root)
    roots = []
    for mod_id in mod_ids:
        # Local saves sometimes use mod:<directory>; Workshop saves use numeric ID.
        identifier = re.sub(r":\d+$", "", str(mod_id)).removeprefix("mod:").removeprefix("mod_").casefold()
        matches = [mod for mod in installed if identifier == mod["id"].casefold()]
        if len(matches) != 1:
            raise ResourceError(f"Active save mod cannot be resolved uniquely: {mod_id}")
        roots.append(matches[0]["root"])
    return roots
