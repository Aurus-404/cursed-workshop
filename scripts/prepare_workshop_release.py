"""Prepare and audit a private Workshop candidate. Does not log in or upload.

python scripts/prepare_workshop_release.py
Use the canonical dist/CursedWorkshop and temporary outputs/workshop_publish.
After upload, retain the publication record and remove the staging directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import re
import runpy
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DOC_NAMES = ("GUIA_DE_USO.md", "USER_GUIDE.md", "USER_GUIDE_ZH.md", "COMPATIBILITY.md", "RELEASE_NOTES.md")
BLOCKED_SUFFIXES = {".sav", ".pak", ".rar", ".log", ".dmp", ".map", ".mdl", ".ply", ".anm", ".dds",
                    ".pem", ".key", ".pfx", ".p12", ".sqlite", ".db", ".bak", ".7z"}
BLOCKED_NAMES = {"settings.json", "depot.json", "options.set", "keys.set", "game_progress.set", ".env"}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit_item(item):
    """Reject personal/generated game files and links; inventory only plain files."""
    item = Path(item)
    if item.is_symlink() or item.is_junction():
        raise ValueError("Linked item directories cannot be published")
    item = item.resolve()
    files = {}
    for path in sorted(item.rglob("*")):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Linked content cannot be published: {path.name}")
        if not path.is_file():
            continue
        relative = path.relative_to(item).as_posix()
        if not path.resolve().is_relative_to(item):
            raise ValueError("Content leaves the item directory")
        if path.suffix.lower() in BLOCKED_SUFFIXES or path.name.lower() in BLOCKED_NAMES:
            raise ValueError(f"Development, save or game content in package: {relative}")
        if any(part.lower() in {".git", "outputs", "operations", "node_modules", "__pycache__"} for part in path.relative_to(item).parts):
            raise ValueError(f"Development/workspace folder in package: {relative}")
        if path.suffix.lower() == ".zip" and relative != "CursedWorkshop/_internal/base_library.zip":
            raise ValueError(f"Unexpected archive in package: {relative}")
        files[relative] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    return files


def validate_distribution(dist, root=ROOT):
    dist = Path(dist)
    if dist.is_symlink() or dist.is_junction():
        raise ValueError("Linked build directories cannot be published")
    dist = dist.resolve()
    exe = dist / "CursedWorkshop.exe"
    if not exe.is_file():
        raise ValueError("Missing CursedWorkshop.exe")
    raw = exe.read_bytes()
    if len(raw) < 64 or raw[:2] != b"MZ":
        raise ValueError("Launcher is not a Windows executable")
    offset = struct.unpack_from("<I", raw, 60)[0]
    if offset + 6 > len(raw) or raw[offset:offset+4] != b"PE\0\0" or struct.unpack_from("<H", raw, offset+4)[0] != 0x8664:
        raise ValueError("Launcher is not Windows x64")
    for name in ("index.html", "assets/app.js", "assets/style.css"):
        packaged = dist / "_internal/workshop/web" / name
        built = root / "workshop/web" / name
        if not packaged.is_file() or not built.is_file() or digest(packaged) != digest(built):
            raise ValueError(f"Stale or missing packaged UI: {name}")
    # Reuse the content rules before copying. The source build's debug map is intentionally omitted.
    for path in dist.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Linked build files cannot be published")
        if path.is_file() and (path.name.lower() in BLOCKED_NAMES or path.suffix.lower() in BLOCKED_SUFFIXES - {".map"}):
            raise ValueError(f"Unexpected data in build: {path.name}")


def collect_notices(item, root=ROOT, python_root=None):
    destination = item / "THIRD_PARTY_LICENSES"
    destination.mkdir()
    python_root = Path(python_root or sys.base_prefix)
    sources = [(python_root / "LICENSE.txt", "Python-LICENSE.txt"),
               (root / "node_modules/three/LICENSE", "three-LICENSE.txt")]
    for path in (root / ".build-deps").rglob("*"):
        if path.is_file() and ".dist-info" in str(path.parent) and any(word in path.name.lower() for word in ("license", "copying", "notice")):
            sources.append((path, "python-packages/" + path.relative_to(root / ".build-deps").as_posix()))
    for path in sorted((root / "docs/third_party").glob("*.txt")):
        sources.append((path, path.name))
    required = {"proxy_tools-LICENSE.txt", "WebView2-LICENSE.txt", "WebView2-NOTICE.txt", "OpenSSL-3.0.16-LICENSE.txt"}
    if not required.issubset({target for _, target in sources}):
        raise ValueError("Missing supplemental runtime notices")
    packages = []
    for info in sorted((root / ".build-deps").glob("*.dist-info")):
        metadata = Parser().parsestr((info / "METADATA").read_text(encoding="utf8"))
        packages.append({"name": metadata["Name"], "version": metadata["Version"]})
    if not {"pywebview", "pythonnet", "clr_loader", "cffi", "pyinstaller"}.issubset({p["name"].lower().replace("-", "_") for p in packages}):
        raise ValueError("Incomplete build dependency inventory")
    for source, target in sources:
        if not source.is_file():
            raise ValueError(f"Required notice missing: {source.name}")
        output = destination / target
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)
    index = {"python": sys.version.split()[0], "build_environment_packages": packages,
             "note": "Includes runtime and build-tool notices. Not every listed build package is loaded at runtime.",
             "licenses": sorted(target for _, target in sources)}
    (destination / "inventory.json").write_text(json.dumps(index, indent=2), encoding="utf8")
    (item / "THIRD_PARTY_NOTICES.txt").write_text(
        "Cursed Workshop uses Python, three.js, pywebview, Python.NET and associated runtime components.\n"
        "Included original license texts and build-environment versions: THIRD_PARTY_LICENSES/.\n"
        "Python's notice also covers its listed bundled components. Tcl/Tk notices remain with the runtime.\n"
        "WebView2 SDK binaries: 1.0.3856.49; the separate Edge WebView2 Runtime is not bundled.\n"
        "These notices describe dependencies; they do not grant a license to the Cursed Workshop project.\n"
        "Game/mod resources are read from the player's installation and are not part of this distribution.\n",
        encoding="utf8")


# Public work-in-progress release, authorized by the author on 19/10/2026.
VERSION = "0.8.10"
VISIBILITY = "0"  # Steam: 0 public, 1 friends only, 2 private
# The published item (19/10/2026): every new release updates it, so subscribers
# get the new version. Pass --publishedfileid 0 only to create a separate item.
PUBLISHED_FILE_ID = "3823214707"


def upload_vdf(item, preview, publishedfileid="0"):
    if not re.fullmatch(r"\d+", str(publishedfileid)):
        raise ValueError("Workshop id must be numeric")
    values = {"appid": "400750", "publishedfileid": str(publishedfileid),
              "contentfolder": Path(item).resolve().as_posix(), "previewfile": Path(preview).resolve().as_posix(),
              "visibility": VISIBILITY, "title": "Cursed Workshop [WIP]",
              "description": "WORK IN PROGRESS. Windows x64 vehicle workshop for Dynamic Conquest. English/Spanish/Simplified Chinese UI. Run CursedWorkshop/CursedWorkshop.exe. Read USER_GUIDE.md or GUIA_DE_USO.md. Test on a campaign copy first.",
              "changenote": VERSION + ": stability update - fixes game crashes and blocked builds"}
    def quote(value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("Control characters are not valid in upload metadata")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return '"workshopitem"\n{\n' + "".join(f"    {quote(k)} {quote(v)}\n" for k, v in values.items()) + "}\n"


def prepare_release(dist, out, publishedfileid="0"):
    if Path(dist).is_symlink() or Path(dist).is_junction():
        raise ValueError("Linked build directories cannot be published")
    dist, out = Path(dist).resolve(), Path(out).resolve()
    if out.exists():
        raise FileExistsError("Choose a fresh release folder; existing releases and Workshop ids are preserved")
    if out.is_relative_to(dist) or dist.is_relative_to(out):
        raise ValueError("Build and release directories must be separate")
    validate_distribution(dist)
    upload_vdf(out / "CursedWorkshop_item", out / "preview.png", publishedfileid)
    for name in DOC_NAMES:
        if not (ROOT / "docs" / name).is_file():
            raise ValueError(f"Missing public guide: {name}")
    builder = runpy.run_path(str(ROOT / "scripts/build-workshop-item.py"))
    item = builder["build_item"](dist, out)
    collect_notices(item)
    preview = out / "preview.png"
    if not 16 < preview.stat().st_size < 1_000_000 or preview.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Workshop preview must be a PNG smaller than 1 MB")
    for language in ("es", "en", "zh-CN"):
        shutil.copy2(ROOT / f"docs/steam_workshop_{language}.bbcode.txt", out / f"description_{language}.bbcode.txt")
    (out / "upload.vdf").write_text(upload_vdf(item, preview, publishedfileid), encoding="utf8")
    shutil.copy2(ROOT / "docs/PUBLICAR_WORKSHOP.md", out / "PUBLICAR_WORKSHOP.md")
    files = audit_item(item)
    manifest = {"schema": 1, "version": VERSION, "channel": "wip",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "preparation_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "preparation_worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
                "appid": 400750, "visibility": {"0": "public", "1": "friends", "2": "private"}[VISIBILITY], "uploaded": False,
                "manual_acceptance": "pending", "files": files,
                "total_bytes": sum(v["bytes"] for v in files.values()), "file_count": len(files)}
    (out / "release-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    (out / "SHA256SUMS.txt").write_text("".join(f"{v['sha256']}  CursedWorkshop_item/{k}\n" for k, v in files.items()), encoding="utf8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "files"}, indent=2))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist/CursedWorkshop")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs/workshop_publish")
    parser.add_argument("--publishedfileid", default=PUBLISHED_FILE_ID)
    args = parser.parse_args()
    prepare_release(args.dist, args.out, args.publishedfileid)


if __name__ == "__main__":
    main()
