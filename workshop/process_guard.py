"""Read-only game-process guard. Never terminates another process."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
from pathlib import Path
import os
import re
import subprocess

GAME_NAMES = {"call_to_arms.exe", "call_to_arms_ed.exe", "call_to_arms_server.exe", "call_to_arms_workshop.exe", "gatesofhell.exe"}

class GameRunningError(RuntimeError):
    pass

def running_game_processes(game_root: str | Path | None = None) -> list[dict]:
    if os.name != "nt":
        output = subprocess.run(["ps", "-A", "-o", "pid=,comm="], capture_output=True, text=True, check=True).stdout
        return [{"pid": int(line.split()[0]), "name": Path(line.split(maxsplit=1)[1]).name}
                for line in output.splitlines() if len(line.split()) >= 2 and Path(line.split(maxsplit=1)[1]).name.lower() in GAME_NAMES]
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),("cntUsage",wintypes.DWORD),("th32ProcessID",wintypes.DWORD),
                    ("th32DefaultHeapID",ctypes.c_size_t),("th32ModuleID",wintypes.DWORD),("cntThreads",wintypes.DWORD),
                    ("th32ParentProcessID",wintypes.DWORD),("pcPriClassBase",wintypes.LONG),("dwFlags",wintypes.DWORD),
                    ("szExeFile",wintypes.WCHAR*260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype=wintypes.BOOL
    kernel.Process32NextW.argtypes=kernel.Process32FirstW.argtypes
    kernel.Process32NextW.restype=wintypes.BOOL
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateToolhelp32Snapshot(2,0)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "No se pudo comprobar si el juego está cerrado")
    result=[]
    try:
        entry=PROCESSENTRY32W(); entry.dwSize=ctypes.sizeof(entry)
        success=kernel.Process32FirstW(handle,ctypes.byref(entry))
        while success:
            if entry.szExeFile.lower() in GAME_NAMES:
                path=None; process=kernel.OpenProcess(0x1000,False,entry.th32ProcessID)
                if process:
                    try:
                        buffer=ctypes.create_unicode_buffer(32768); size=wintypes.DWORD(len(buffer))
                        if kernel.QueryFullProcessImageNameW(process,0,buffer,ctypes.byref(size)):
                            path=buffer.value
                    finally: kernel.CloseHandle(process)
                # Unknown path stays blocked: do not guess that a process is unrelated.
                if not game_root or not path or Path(game_root).resolve() in Path(path).resolve().parents:
                    result.append({"pid":entry.th32ProcessID,"name":entry.szExeFile,"path":path})
            success=kernel.Process32NextW(handle,ctypes.byref(entry))
    finally: kernel.CloseHandle(handle)
    return result

def require_game_closed(game_root: str | Path | None = None) -> None:
    processes=running_game_processes(game_root)
    if processes:
        raise GameRunningError("Cerrá Gates of Hell y su editor antes de abrir o modificar una campaña: " + ", ".join(p['name'] for p in processes))


# Menu pages where no campaign is held in memory. Verified in game.log: after
# building there, Extras > Apply remounts every mod in-process and the new
# vehicle loads (docs/INVESTIGACION.md, route A). Anything else (the dynamic
# campaign page, a battle, loading) may write the save back: blocked.
SAFE_PAGES = {"page_main", "page_singleplayer", "page_extras", "page_credits"}
_PAGE = re.compile(r"GUI: Show page (\w+) with mods \[ ?\d+ (?:service )?unique\]")


def game_log_path() -> Path:
    """The live game.log, also with Documents moved to OneDrive or localized."""
    from .locations import game_log_path as located
    return located()


def menu_page(log: str | Path | None = None) -> str | None:
    """Where the running game is, from the tail of its live log: the last
    full-screen page, "battle" or "campaign"; None when the log says nothing."""
    path = Path(log) if log else game_log_path()
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 512 * 1024))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return None
    state = None
    for line in text.splitlines():
        match = _PAGE.search(line)
        if match:
            state = match.group(1)
        elif "Game: Create game" in line:
            state = "battle"
        elif "Loading Campaign:" in line:
            state = "campaign"
    return state


def game_state(game_root: str | Path | None = None) -> dict:
    """For the status bar: {"running", "page", "ready", "message"}; never raises."""
    try:
        return {**require_game_idle(game_root), "ready": True, "message": ""}
    except GameRunningError as error:
        return {"running": True, "page": menu_page(), "ready": False, "message": str(error)}
    except OSError as error:
        return {"running": None, "page": None, "ready": False, "message": str(error)}


def require_game_idle(game_root: str | Path | None = None, log: str | Path | None = None) -> dict:
    """Allow work while the game sits in a main-menu page; block the editor,
    the server, a loaded campaign or a battle. Returns {"running", "page"}."""
    processes = running_game_processes(game_root)
    if not processes:
        return {"running": False, "page": None}
    others = [p["name"] for p in processes if p["name"].lower() != "call_to_arms.exe"]
    if others:
        raise GameRunningError("Cerrá el editor o el servidor de Gates of Hell antes de modificar una campaña: " + ", ".join(others))
    page = menu_page(log)
    if page not in SAFE_PAGES:
        where = {"battle": "en una batalla", "campaign": "con una campaña cargada",
                 "page_dynamic_campaign": "con una campaña cargada", None: "cargando o en una pantalla desconocida"}.get(page, f"en la pantalla {page}")
        raise GameRunningError(f"El juego está {where}. Volvé al menú principal (o cerralo) y probá de nuevo: "
                               "con una campaña abierta el juego podría pisar los cambios.")
    return {"running": True, "page": page}
