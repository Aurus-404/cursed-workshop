"""One zip a player can drop into Discord with everything a bug needs: the
tool's diagnostic, the game's whole game.log and latest crash logs, the active
mod lists, the tool's own logs and settings, the history of every vehicle built,
the latest builds in full, how each workshop vehicle is made now (recipe,
definition, model) and the campaign save. Only made when the player asks, never
sent anywhere.

A save holds no personal data (no Steam account or Windows name, only the
campaign the player named). The user's home folder is written as %USERPROFILE%
and the Steam account id and public name are blanked. Minidump memory images
are never included. The zip stays under Discord's 10 MB upload limit.
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path

GAME_LOG_LIMIT = 64 * 1024 * 1024
CRASH_LOGS = 5
BUILD_RESULTS = 3
SIZE_LIMIT = 9_500_000  # Discord's upload limit is 10 MB
FULL_SAVE_LIMIT = 6_000_000

README = """Cursed Workshop error report
Made by the tool when you pressed "Save error report". Nothing was sent anywhere.

diagnostic.txt  the tool's diagnostic (version, mods, recent errors, editor selection)
game.log        the game's whole log of its last run (the crash is usually at the end)
crashes/        the game's latest crash logs, if any
mods.txt        the mods each game profile has active, in load order
workshop.log    the tool's own log (workshop.log.1: the part before it)
settings.json   the tool's settings (game folder and mods it reads)
history/        every vehicle built, installed or updated with the tool
builds/         the latest builds in full (recipe, compilation and campaign plan)
vehicles/       how each Cursed Workshop vehicle is made now: recipe, definition, model
save/           the campaign save currently open in the tool

Your Windows user folder is shown as %USERPROFILE% and your Steam id and name are
hidden. Saves hold no personal data. Post this file in #bug-reports on the
Cursed Workshop Discord.
"""


def steam_names(text: str) -> set[str]:
    """The player's Steam public name, as the game writes it in its log."""
    return {name.strip() for name in re.findall(r"Steam Public Name:([^\r\n]*)", text)
            if name.strip() and name.strip() != "(hidden)"}


def redact(text: str, home: Path, names: set[str] = frozenset()) -> str:
    """Replace the user's home folder in any spelling (case, slashes) and the
    Steam account the game writes in its log: id, and public name wherever it
    appears (players joining, battle events)."""
    variants = {str(home), str(home).replace("\\", "/"), str(home).replace("\\", "\\\\")}
    for variant in sorted(variants, key=len, reverse=True):
        if variant:
            text = re.sub(re.escape(variant), "%USERPROFILE%", text, flags=re.IGNORECASE)
    text = re.sub(r"(Steam User ID:\s*)\S+", r"\1(hidden)", text)
    text = re.sub(r"(Steam Public Name:)[^\r\n]*", r"\1 (hidden)", text)
    text = re.sub(r"\b7656119\d{10}\b", "(steam id)", text)
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(r"(?<![\w])" + re.escape(name) + r"(?![\w])", "(player)", text)
    return text


def _read(path: Path, limit: int | None = None) -> str:
    raw = path.read_bytes()
    if limit is not None and len(raw) > limit:
        raw = b"[... start of the file omitted ...]\n" + raw[-limit:]
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _profile_mods(profiles: Path) -> str:
    rows = []
    for options in sorted(profiles.glob("*/options.set")):
        try:
            text = _read(options)
        except OSError:
            continue
        match = re.search(r"\{mods\b([^{}]*)\}", text)
        mods = re.findall(r'"([^"]+)"', match.group(1)) if match else []
        rows.append(f"Profile {options.parent.name}:")
        rows += [f"  {position}. {mod}" for position, mod in enumerate(mods, 1)] or ["  (no mods active)"]
    return "\n".join(rows) + "\n" if rows else "No game profiles found.\n"


def _light_save(save: Path) -> bytes:
    """The save without its battle maps: campaign, units and status, which is
    what the tool reads, for a save too big to travel whole."""
    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(save) as source, zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as light:
        for info in source.infolist():
            if info.filename in ("campaign.scn", "status"):
                light.writestr(info.filename, source.read(info))
    return buffer.getvalue()


def build_report(destination: Path, game: Path, workspace: Path, diagnostic: str,
                 home: Path | None = None, now: datetime | None = None, addon: Path | None = None,
                 save: Path | None = None) -> dict:
    """Write the zip into destination and say what went into it. game is the
    game's data folder (profiles and log, "CONFIG:" in game.log); addon the
    installed workshop mod, whose recipes, definitions and models show how
    each vehicle is made; save the campaign the player is working on."""
    home = home or Path.home()
    now = now or datetime.now()
    destination.mkdir(parents=True, exist_ok=True)
    stem = f"Cursed Workshop report {now.strftime('%Y-%m-%d %H-%M-%S')}"
    target = destination / f"{stem}.zip"
    copy = 1
    while target.exists():
        copy += 1
        target = destination / f"{stem} ({copy}).zip"
    included, missing = [], []
    log = game / "log" / "game.log"
    try:
        names = steam_names(_read(log, GAME_LOG_LIMIT))
    except OSError:
        names = set()
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        def add(name, text):
            archive.writestr(name, redact(text, home, names))
            included.append(name)

        add("README.txt", README)
        add("diagnostic.txt", diagnostic or "The tool's diagnostic was not available.\n")
        try:
            add("game.log", _read(log, GAME_LOG_LIMIT))
        except OSError:
            missing.append("game.log")
        crashes = sorted((game / "log" / "minidumps").glob("*.mdmp.log"),
                         key=lambda path: path.stat().st_mtime, reverse=True)[:CRASH_LOGS]
        for crash in crashes:
            try:
                add(f"crashes/{crash.name}", _read(crash, 4 * 1024 * 1024))
            except OSError:
                continue
        add("mods.txt", _profile_mods(game / "profiles"))
        for name in ("workshop.log", "workshop.log.1"):
            try:
                add(name, _read(workspace / name, 8 * 1024 * 1024))
            except OSError:
                if name == "workshop.log":
                    missing.append(name)
        try:
            add("settings.json", _read(workspace / "settings.json", 1024 * 1024))
        except OSError:
            missing.append("settings.json")
        try:
            add("history/builds.jsonl", _read(workspace / "history" / "builds.jsonl", 4 * 1024 * 1024))
        except OSError:
            missing.append("history/builds.jsonl")
        builds = sorted((workspace / "operations").glob("*/result.json"), key=lambda path: path.stat().st_mtime,
                        reverse=True)[:BUILD_RESULTS]
        for result in builds:
            try:
                add(f"builds/{result.parent.name}.json", _read(result, 8 * 1024 * 1024))
            except OSError:
                continue
        vehicles = addon / "resource" / "entity" / "-vehicle" / "workshop" if addon else None
        for recipe in sorted(addon.glob("assembly_mw_*.json")) if addon and addon.is_dir() else []:
            identifier = recipe.stem[len("assembly_"):]
            try:
                add(f"vehicles/{recipe.name}", _read(recipe, 8 * 1024 * 1024))
            except OSError:
                continue
            for suffix in (".def", ".mdl", ".ext"):
                try:
                    add(f"vehicles/{identifier}{suffix}", _read(vehicles / identifier / f"{identifier}{suffix}", 4 * 1024 * 1024))
                except OSError:
                    continue
        if save is not None:
            try:
                raw = Path(save).read_bytes()
                room = SIZE_LIMIT - archive.fp.tell()
                if len(raw) > min(FULL_SAVE_LIMIT, room):
                    raw = _light_save(Path(save))
                    name = f"save/{Path(save).stem} (campaign only).sav"
                else:
                    name = f"save/{Path(save).name}"
                if len(raw) <= room:
                    archive.writestr(name, raw, compress_type=zipfile.ZIP_STORED)
                    included.append(name)
                else:
                    missing.append("save (too big)")
            except (OSError, zipfile.BadZipFile):
                missing.append("save")
        else:
            missing.append("save (no campaign opened yet)")
    return {"path": str(target), "size": target.stat().st_size, "included": included, "missing": missing}
