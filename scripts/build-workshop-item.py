"""Assemble the Steam Workshop item folder from a built desktop package.

Like other GoH tool items, the item is a mod folder that carries the program:

    <item>/mod.info                 visible in the game's mod list (no game content)
    <item>/resource/LEEME.txt       the game requires a resource folder
    <item>/CursedWorkshop/...      the packaged tool (CursedWorkshop.exe + _internal)
    <item>/LEEME.txt, README.txt    where to start
    <output>/preview.png            512x512 preview outside the uploaded content

    python scripts/build-workshop-item.py [dist/CursedWorkshop] [outputs/workshop_item]
"""
from __future__ import annotations

import shutil
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

MOD_INFO = """{mod
\t{name "Cursed Workshop (WIP) (External Tool)"}
\t{desc "External tool for Dynamic Conquest. Run CursedWorkshop.exe from the CursedWorkshop folder of this item. Enable the generated local mod Cursed Workshop - Built Vehicles last in Extras > Mods."}
\t{minGameVersion "any"}
\t{maxGameVersion "any"}
}
"""

START = {
    "LEEME.txt": """CURSED WORKSHOP — Gates of Hell (Conquest)

Versión en desarrollo (WIP) para Windows de 64 bits: puede tener errores; probá primero en una copia de tu campaña. Interfaz en español, inglés y chino simplificado.
Probá primero con una copia de tu campaña.

1. Cerrá el juego o dejalo en el menú principal, sin campaña cargada.
2. Abrí CursedWorkshop\\CursedWorkshop.exe (está en esta misma carpeta).
3. Elegí y abrí tu campaña, armá el vehículo y tocá "Construir vehículo".
4. En Extras > Mods, activá "Cursed Workshop - Built Vehicles" al final de la lista y aplicá.
5. Usá "Comprobar mod cargado" en la herramienta antes de abrir la campaña. Conservá sus backups.

No hace falta activar este ítem en la lista de mods: activá el mod local
"Cursed Workshop - Built Vehicles" (carpeta mods\\modular_workshop del juego).
Guía completa: GUIA_DE_USO.md
Soporte: Diagnóstico → copiar el informe como texto. No envía archivos automáticamente.
""",
    "README.txt": """CURSED WORKSHOP — Gates of Hell (Conquest)

Work-in-progress (WIP) Windows x64 release: expect bugs; try it on a campaign copy first. Spanish, English and Simplified Chinese interface.
Test with a copy of your campaign first.

1. Close the game or leave it in its main menu, with no campaign loaded.
2. Run CursedWorkshop\\CursedWorkshop.exe (in this same folder).
3. Select and open your campaign, assemble the vehicle and press "Build vehicle".
4. In Extras > Mods, enable "Cursed Workshop - Built Vehicles" last and apply the list.
5. Use "Check loaded mod" in the tool before opening the campaign. Keep its backups.

You do not need to enable this item in the mod list: enable the generated local mod
"Cursed Workshop - Built Vehicles" (the game's mods\\modular_workshop folder).
Full English guide: USER_GUIDE.md. Spanish guide: GUIA_DE_USO.md. Chinese guide: USER_GUIDE_ZH.md.
Support: Diagnostics → copy the report as text. No files are sent automatically.
""",
}


# 5x7 pixel glyphs for the preview title (no image library needed).
_GLYPHS = {
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
}


def _text_pixels(text, top, cell, size):
    """Pixels covered by centered text, one cell per glyph pixel."""
    width = len(text) * 6 * cell - cell
    left = (size - width) // 2
    covered = set()
    for index, letter in enumerate(text):
        for gy, line in enumerate(_GLYPHS[letter]):
            for gx, bit in enumerate(line):
                if bit == "1":
                    x0, y0 = left + (index * 6 + gx) * cell, top + gy * cell
                    covered.update((x, y) for x in range(x0, x0 + cell) for y in range(y0, y0 + cell))
    return covered, left, width


def preview_png(path: Path, size: int = 512) -> None:
    """Gold tank silhouette on dark slate (the app icon), the title and a WIP tag."""
    gold, dark, edge = (201, 163, 92), (24, 36, 44), (70, 92, 104)
    cream, red = (236, 226, 204), (178, 52, 44)
    shapes = [(8, 34, 56, 46), (14, 46, 50, 52), (20, 24, 40, 34), (40, 27, 58, 30)]  # hull, tracks, turret, barrel
    scale = size / 64
    title = _text_pixels("CURSED", 34, 7, size)[0] | _text_pixels("WORKSHOP", 96, 7, size)[0]
    tag, tag_left, tag_width = _text_pixels("WIP", 444, 6, size)
    pad = 12
    box = (tag_left - pad, 444 - pad, tag_left + tag_width + pad, 444 + 7 * 6 + pad)
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            px, py = (x + .5) / scale, (y + .5) / scale + 3  # silhouette moved down for the title
            if (x, y) in title:
                row += bytes(cream)
            elif (x, y) in tag:
                row += bytes(cream)
            elif box[0] <= x < box[2] and box[1] <= y < box[3]:
                row += bytes(red)
            elif any(a <= px < c and b <= py < d for a, b, c, d in shapes):
                row += bytes(gold)
            elif x < 1.2 * scale or x > size - 1.2 * scale or y < 1.2 * scale or y > size - 1.2 * scale:
                row += bytes(edge)
            else:
                row += bytes(dark)
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def build_item(dist, out):
    dist, out = Path(dist).resolve(), Path(out).resolve()
    if not (dist / "CursedWorkshop.exe").is_file():
        raise SystemExit(f"Build the package first: {dist / 'CursedWorkshop.exe'} not found")
    item = out / "CursedWorkshop_item"
    if item.exists():
        raise FileExistsError(f"Item already exists; choose a fresh output directory: {item}")
    if out.is_relative_to(dist) or dist.is_relative_to(out):
        raise ValueError("Build and output directories must be separate")
    (item / "resource").mkdir(parents=True)
    (item / "mod.info").write_text(MOD_INFO, encoding="utf-8")
    (item / "resource" / "LEEME.txt").write_text(START["LEEME.txt"], encoding="utf-8")
    for name, text in START.items():
        (item / name).write_text(text, encoding="utf-8")
    for name in ("GUIA_DE_USO.md", "USER_GUIDE.md", "USER_GUIDE_ZH.md", "COMPATIBILITY.md", "RELEASE_NOTES.md"):
        if (DOCS / name).is_file():
            shutil.copy2(DOCS / name, item / name)
    shutil.copytree(dist, item / "CursedWorkshop", ignore=shutil.ignore_patterns("*.map", "__pycache__"))
    # Project logo (docs/branding); generated silhouette as fallback.
    logo = DOCS / "branding" / "cursed_workshop_logo.png"
    if logo.is_file() and logo.stat().st_size < 1_000_000:
        shutil.copy2(logo, out / "preview.png")
    # The game's uploader (call_to_arms_workshop.exe) only accepts a JPEG preview.
    jpeg = logo.with_suffix(".jpg")
    if jpeg.is_file() and jpeg.stat().st_size < 1_000_000:
        shutil.copy2(jpeg, out / "preview.jpg")
    else:
        preview_png(out / "preview.png")
    print(f"Workshop item: {item}\nPreview image: {out / 'preview.png'} (upload it as the item's preview)")

    return item


def main(argv):
    dist = Path(argv[1]) if len(argv) > 1 else ROOT / "dist" / "CursedWorkshop"
    out = Path(argv[2]) if len(argv) > 2 else ROOT / "outputs" / "workshop_item"
    build_item(dist, out)


if __name__ == "__main__":
    main(sys.argv)
