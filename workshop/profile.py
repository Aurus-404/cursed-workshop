"""Activate the workshop addon in the player's profile (options.set).

The game keeps the active mod list, in load order, in
profiles/<id>/options.set as {options ... {mods "mod_<workshop id>:0" "<local folder>:0"}}.
The tool edits it only while the game is closed, and keeps a backup.
"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shutil

from .registration import ADDON_MOD, is_own_mod
from .syntax import Atom, Trivia, block, parse, quote


class ProfileError(ValueError):
    pass


def profile_for_save(save_path: str | Path) -> Path | None:
    """profiles/<id>/campaign/<name>.sav -> profiles/<id>/options.set"""
    options = Path(save_path).resolve().parent.parent / "options.set"
    return options if options.is_file() else None


def active_mods(options_path: Path) -> list[str]:
    options = parse(options_path.read_text(encoding="utf-8-sig")).find("options")
    mods = options.find("mods") if options else None
    return mods.args if mods else []


def planned_mod_order(options_path: str | Path | None, required: list[str]) -> list[str]:
    """The mod list options.set will have after activation, which is the order
    the game mounts them in: mods already active keep their place, the
    campaign's missing ones follow in the campaign's order, the workshop addon
    goes last. Resources must be read in this same order, or the files the tool
    copies are not the ones the game uses (a crash: "define not found")."""
    before = []
    if options_path is not None and Path(options_path).is_file():
        options = parse(Path(options_path).read_bytes().decode("utf-8-sig")).find("options")
        mods = options.find("mods") if options is not None else None
        before = list(mods.args) if mods else []
    wanted = [mod for mod in before if not is_own_mod(mod)]
    for mod in required:
        if not is_own_mod(mod) and mod.casefold() not in {item.casefold() for item in wanted}:
            wanted.append(mod)
    return wanted + [ADDON_MOD]


def activate_addon(options_path: str | Path, required: list[str], backup_dir: str | Path) -> dict:
    """Ensure the campaign's mods are active, then the workshop addon last.

    Legacy per-campaign workshop addons are deactivated: they override the same
    registry files as the shared addon and would hide its vehicles.
    """
    options_path = Path(options_path)
    raw = options_path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    document = parse(text)
    options = document.find("options")
    if options is None:
        raise ProfileError("options.set has no options block")
    mods = options.find("mods")
    before = mods.args if mods else []
    wanted = planned_mod_order(options_path, required)
    if wanted == before:
        return {"changed": False, "mods": wanted, "backup": None}
    node = block("{mods}")
    node.items = [node.atoms[0]]
    for mod in wanted:
        node.items += [Trivia(newline + "\t\t"), Atom(mod, quote(mod), True)]
    node.items.append(Trivia(newline + "\t"))
    if mods is None:
        options.append(node)
    else:
        options.items = [node if item is mods else item for item in options.items]
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"options_{datetime.now().strftime('%Y%m%d_%H%M%S')}.set"
    shutil.copy2(options_path, backup)
    data = document.render().encode("utf-8")
    temporary = options_path.with_suffix(".mw-tmp")
    temporary.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)
    os.replace(temporary, options_path)
    return {"changed": True, "mods": wanted, "previous": before, "backup": str(backup),
            "deactivated": [mod for mod in before if is_own_mod(mod) and mod != ADDON_MOD]}
