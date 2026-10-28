"""Build the independent Windows desktop launcher from the current sources."""
from pathlib import Path
from datetime import datetime,timezone
import json
import os
import subprocess
import struct
import sys
from release_utils import source_digest

ROOT=Path(__file__).resolve().parents[1]
if len(sys.argv)>1:
    raise SystemExit("Build without arguments. The only local package is dist/CursedWorkshop/CursedWorkshop.exe.")
# Fail before PyInstaller can clean the canonical folder if Windows has the EXE open.
exe = ROOT/'dist/CursedWorkshop/CursedWorkshop.exe'
if os.name == 'nt' and exe.exists():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(exe), 0xC0000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise SystemExit('The canonical executable is open or not writable. Close CursedWorkshop.exe and retry; it has been preserved.')
    kernel.CloseHandle(handle)
sys.path.insert(0,str(ROOT/'.build-deps'))
from PyInstaller.__main__ import run


def tank_icon(path: Path) -> Path:
    """Write a small multi-size .ico: a gold tank silhouette on dark slate."""
    def pixels(size):
        s = size / 64
        gold, dark, edge = (201, 163, 92, 255), (24, 36, 44, 255), (70, 92, 104, 255)
        shapes = [(8, 34, 56, 46), (14, 46, 50, 52), (20, 24, 40, 34), (40, 27, 58, 30)]  # hull, tracks, turret, barrel
        rows = []
        for y in range(size):
            row = []
            for x in range(size):
                px, py = (x + .5) / s, (y + .5) / s
                corner = min(px, 64 - px) < 6 and min(py, 64 - py) < 6 and (min(px, 64 - px) - 6) ** 2 + (min(py, 64 - py) - 6) ** 2 > 36
                if corner:
                    row.append((0, 0, 0, 0))
                elif any(a <= px < c and b <= py < d for a, b, c, d in shapes):
                    row.append(gold)
                elif px < 2 or px > 62 or py < 2 or py > 62:
                    row.append(edge)
                else:
                    row.append(dark)
            rows.append(row)
        return rows

    images = []
    for size in (16, 32, 48, 64):
        rows = pixels(size)
        color = b"".join(bytes((b, g, r, a)) for row in reversed(rows) for r, g, b, a in row)
        stride = ((size + 31) // 32) * 4
        mask = b"".join(bytes(stride) for _ in range(size))  # alpha channel carries transparency
        header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(color) + len(mask), 0, 0, 0, 0)
        images.append((size, header + color + mask))
    offset = 6 + 16 * len(images)
    directory, data = b"", b""
    for size, image in images:
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset + len(data))
        data += image
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + directory + data)
    return path


icon = tank_icon(ROOT/'build'/'modular_workshop.ico')
info={'source_sha256':source_digest(ROOT),'source_digest_format':'paths-lengths-crlf-normalized-v1',
      'built_utc':datetime.now(timezone.utc).isoformat(timespec='seconds')}
try:
    info['revision']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip()
    info['dirty_source']=bool(subprocess.check_output(['git','status','--porcelain','--','launch.py','workshop','frontend'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip())
except (OSError,subprocess.CalledProcessError):pass
build_info=ROOT/'build'/'build_info.json';build_info.write_text(json.dumps(info,indent=2),encoding='utf8')
# A locked executable must be closed, not replaced with another per-case build.
dist = ROOT/'dist'
run(['--noconfirm','--onedir','--windowed','--name','CursedWorkshop','--icon',str(icon),
     '--paths',str(ROOT/'.build-deps'),
     '--add-data',str(ROOT/'workshop'/'web')+';workshop/web',
     '--add-data',str(build_info)+';workshop',
     '--distpath',str(dist),'--workpath',str(ROOT/'build'/'packaging'),
     '--specpath',str(ROOT/'build'),str(ROOT/'launch.py')])
