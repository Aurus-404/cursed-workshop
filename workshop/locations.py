"""Where the game keeps its profiles, saves and log on this PC.

Windows "Documents" is not one fixed place: OneDrive can move it (and break
its sync), the folder name is localized (Documents, Dokumente…) and the game
may keep writing to the other one. Under Wine/Proton (Linux, Steam Deck) the
game uses its own prefix, not this program's. Every lookup goes through here.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

GAME_APP_ID = '400750'
GAME_DIR = ('My Games', 'gates of hell')


def running_under_wine() -> bool:
    """Wine and Proton (Linux, Steam Deck) expose wine_get_version in ntdll."""
    if os.name != 'nt':
        return False
    try:
        import ctypes
        return hasattr(ctypes.WinDLL('ntdll'), 'wine_get_version')
    except Exception:
        return False


def documents_dir() -> Path:
    """Windows "Documents" as the shell sees it, wherever it was moved."""
    documents = Path.home() / 'Documents'
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                documents = Path(os.path.expandvars(winreg.QueryValueEx(key, 'Personal')[0]))
        except OSError:
            pass
    return documents


def _has_game(folder: Path) -> bool:
    try:
        return folder.joinpath(*GAME_DIR).is_dir()
    except OSError:
        return False


def windows_documents() -> list[Path]:
    """Documents folders that hold the game's data: the shell's, the profile's
    own and any folder in the home or a OneDrive root that has "My Games/gates
    of hell" (so a localized or moved Documents is found too)."""
    candidates = [documents_dir(), Path.home() / 'Documents']
    roots = [Path.home()] + [Path(os.environ[name]) for name in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial')
                             if os.environ.get(name)]
    try:
        roots += [child for child in Path.home().iterdir() if child.name.casefold().startswith('onedrive')]
    except OSError:
        pass
    for root in dict.fromkeys(roots):
        try:
            candidates += [child for child in root.iterdir() if child.is_dir() and _has_game(child)]
        except OSError:
            continue
    found, seen = [], set()
    for folder in candidates:
        key = os.path.normcase(os.path.realpath(folder))  # "Mis documentos" is a junction to Documents
        if key not in seen and _has_game(folder):
            seen.add(key)
            found.append(folder)
    return found


def unix_path(path: str) -> Path:
    """A Linux path as Wine/Proton sees it: the whole file system is drive Z:."""
    return Path('Z:' + path.replace('/', '\\'))


def unix_homes() -> list[str]:
    """Linux home folders (/home/deck…) seen from Wine/Proton."""
    if not running_under_wine():
        return []
    homes = [os.environ.get('HOME', '')]
    try:
        homes += ['/home/' + p.name for p in unix_path('/home').iterdir()]
    except OSError:
        pass
    return [home for home in dict.fromkeys(homes) if home.startswith('/')]


def proton_users(game_root=None) -> list[Path]:
    """Under Wine/Proton the game runs in its own prefix,
    <library>/steamapps/compatdata/400750/pfx/drive_c/users/steamuser, not in
    this program's. Looks in every Steam library it can see."""
    if not running_under_wine():
        return []
    steams = [os.environ.get('STEAM_COMPAT_CLIENT_INSTALL_PATH', '')]
    for home in unix_homes():
        steams += [home + '/.local/share/Steam', home + '/.steam/steam',
                   home + '/.var/app/com.valvesoftware.Steam/.local/share/Steam']
    libraries = []
    for steam in steams:
        if not steam.startswith('/'):
            continue
        root = unix_path(steam) / 'steamapps'
        libraries.append(root)
        try:
            text = (root / 'libraryfolders.vdf').read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        libraries += [unix_path(path) / 'steamapps' for path in re.findall(r'"path"\s+"(/[^"]+)"', text)]
    if game_root:
        # The library the game itself is installed in, as this program sees it.
        libraries += [parent for parent in Path(game_root).parents if parent.name.casefold() == 'steamapps'][:1]
    found = []
    for library in dict.fromkeys(libraries):
        user = library / 'compatdata' / GAME_APP_ID / 'pfx' / 'drive_c' / 'users' / 'steamuser'
        try:
            if user.is_dir() and user not in found:
                found.append(user)
        except OSError:
            continue
    return found


def _modified(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def config_dirs(game_root=None) -> list[Path]:
    """The game's data folders (profiles, saves, log; "CONFIG:" in game.log).
    Depending on the PC the game uses <Documents>/My Games/gates of hell or
    %LOCALAPPDATA%/digitalmindsoft/gates of hell; under Proton, either one
    inside its prefix. The folder whose game.log was written last, the one the
    game uses now, comes first."""
    candidates = [documents.joinpath(*GAME_DIR) for documents in windows_documents()]
    local = os.environ.get('LOCALAPPDATA')
    if local:
        candidates.append(Path(local) / 'digitalmindsoft' / 'gates of hell')
    for user in proton_users(game_root):
        candidates += [user / 'Documents' / 'My Games' / 'gates of hell',
                       user / 'AppData' / 'Local' / 'digitalmindsoft' / 'gates of hell']
    found, seen = [], set()
    for folder in candidates:
        key = os.path.normcase(os.path.realpath(folder))
        try:
            exists = folder.is_dir()
        except OSError:
            exists = False
        if exists and key not in seen:
            seen.add(key)
            found.append(folder)
    found.sort(key=lambda folder: _modified(folder / 'log' / 'game.log'), reverse=True)
    return found or [documents_dir().joinpath(*GAME_DIR)]


def game_log_path(game_root=None) -> Path:
    """The live game.log of the folder the game uses now."""
    return config_dirs(game_root)[0] / 'log' / 'game.log'
