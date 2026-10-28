"""Battle icons (unit_icon 144x72, unit_icon_small 112x48) of an assembled vehicle.

The game looks them up by squad name as interface/scene/<kind>/<name>_00..03.tga:
normal, hover, pressed and disabled. The tool renders the assembled model with
a transparent background (the UI sends raw RGBA); here it is laid over a plain
dark background in the four native states, with the native chassis emblem
cropped at its original left-hand position and its frame alpha preserved.
Texture dimensions include transparent padding, not extra button space.
"""
from __future__ import annotations

import base64
import struct

ICON_KINDS = {"unit_icon": (144, 72), "unit_icon_small": (112, 48)}  # width, height
ICON_STRIPS = {"unit_icon": 28, "unit_icon_small": 24}
# Native small icons have a 96-pixel plate inside the 112-pixel texture.
# Prefer the source alpha below; these bounds are only for missing artwork.
ICON_FALLBACK_BOUNDS = {"unit_icon": (0, 144), "unit_icon_small": (8, 104)}


class IconError(ValueError):
    pass


def read_tga(raw: bytes) -> tuple[int, int, bytearray]:
    """Uncompressed (2) or RLE (10) true-colour TGA, 24/32 bit -> (w, h, RGBA rows top-down)."""
    if len(raw) < 18:
        raise IconError("TGA too short")
    id_length, _, kind = raw[0], raw[1], raw[2]
    width, height, depth, descriptor = struct.unpack_from("<HHBB", raw, 12)
    if kind not in (2, 10) or depth not in (24, 32) or not 0 < width <= 4096 or not 0 < height <= 4096:
        raise IconError("Unsupported TGA")
    size, data, cursor = depth // 8, bytearray(), 18 + id_length
    total = width * height * size
    if kind == 2:
        data = bytearray(raw[cursor:cursor + total])
    else:
        while len(data) < total:
            if cursor >= len(raw):
                raise IconError("Truncated RLE TGA")
            head = raw[cursor]; cursor += 1; count = (head & 0x7F) + 1
            if head & 0x80:
                data += raw[cursor:cursor + size] * count; cursor += size
            else:
                data += raw[cursor:cursor + size * count]; cursor += size * count
    if len(data) < total:
        raise IconError("Truncated TGA")
    top_down = bool(descriptor & 0x20)
    out = bytearray(width * height * 4)
    for y in range(height):
        source = y if top_down else height - 1 - y
        for x in range(width):
            s = (source * width + x) * size
            b, g, r = data[s], data[s + 1], data[s + 2]
            a = data[s + 3] if size == 4 else 255
            o = (y * width + x) * 4
            out[o:o + 4] = bytes((r, g, b, a))
    return width, height, out


def write_tga(width: int, height: int, rgba: bytes) -> bytes:
    """32-bit uncompressed, top-left origin (descriptor 0x28), as BGRA."""
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, width, height, 32, 0x28)
    body = bytearray(len(rgba))
    body[0::4], body[1::4], body[2::4], body[3::4] = rgba[2::4], rgba[1::4], rgba[0::4], rgba[3::4]
    return header + bytes(body)


def decode_render(value, width: int, height: int) -> bytes:
    """The UI's render layer: 'rgba:' + base64 of exactly width*height*4 bytes."""
    prefix = "rgba:"
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) > 200_000:
        raise IconError("Icono inválido.")
    try:
        raw = base64.b64decode(value[len(prefix):], validate=True)
    except ValueError as error:
        raise IconError("Icono inválido.") from error
    if len(raw) != width * height * 4:
        raise IconError("Icono inválido.")
    return raw


def _variant(r: float, g: float, b: float, variant: int) -> tuple[float, float, float]:
    # Measured on native icons: hover ~+12 %, pressed ~+25 %, disabled grey ~0.6.
    if variant == 1:
        return r * 1.12, g * 1.12, b * 1.12
    if variant == 2:
        return r * 1.25, g * 1.25, b * 1.25
    if variant == 3:
        grey = (0.3 * r + 0.59 * g + 0.11 * b) * 0.58
        return grey, grey, grey
    return r, g, b


def _background(width: int, height: int) -> bytearray:
    """Plain dark plate like the native icons' background, slightly lighter
    in the middle so the vehicle stands out."""
    plate = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            dx, dy = (x - width / 2) / width, (y - height / 2) / height
            light = max(0.0, 1 - 2.2 * (dx * dx + dy * dy) ** 0.5)
            o = (y * width + x) * 4
            plate[o:o + 4] = bytes((int(30 + 18 * light), int(29 + 16 * light), int(27 + 14 * light), 255))
    return plate


def chassis_strips(resources, templates, kind):
    """Read native plates only as a source for a narrow left crop."""
    width, height = ICON_KINDS[kind]
    for template in templates:
        plates = []
        for variant in range(4):
            try:
                w, h, rgba = read_tga(resources.read_bytes(f'interface/scene/{kind}/{template}_{variant:02d}.tga'))
                plates.append(rgba if (w, h) == (width, height) else None)
            except (IconError, KeyError, OSError, ValueError):
                plates.append(None)
        if any(plate is not None for plate in plates):
            return plates
    return [None] * 4


def compose(kind: str, render: bytes, strips=None) -> list[bytes]:
    """Four TGAs: unshifted model, cropped emblem, native transparent frame."""
    width, height = ICON_KINDS[kind]
    strip = ICON_STRIPS[kind]
    out = []
    for variant in range(4):
        plate = _background(width, height)
        for i in range(0, len(plate), 4):
            alpha = render[i + 3] / 255
            if not alpha:
                continue
            r, g, b = _variant(render[i], render[i + 1], render[i + 2], variant)
            for c, value in enumerate((r, g, b)):
                plate[i + c] = max(0, min(255, round(value * alpha + plate[i + c] * (1 - alpha))))
        if variant == 3:  # disabled: the whole plate dims, as natively
            for i in range(0, len(plate), 4):
                plate[i:i + 3] = bytes(int(v * 0.62) for v in plate[i:i + 3])
        # Copy last: antialiasing or an old client cannot cover the strip.
        # State-specific native colors and alpha are kept byte for byte.
        base = strips[variant] if strips and variant < len(strips) else None
        if base is None and strips:
            base = next((p for p in strips if p is not None), None)
        # Alpha is the button's footprint, including transparent right padding.
        # Never copy the native vehicle's RGB outside the emblem crop.
        left, right = ICON_FALLBACK_BOUNDS[kind]
        for y in range(height):
            for x in range(width):
                i = (y * width + x) * 4
                plate[i + 3] = base[i + 3] if base is not None else (255 if left <= x < right else 0)
        for y in range(height):
            start = y * width * 4
            if base is not None:
                plate[start:start+strip*4] = base[start:start+strip*4]
            else:
                for x in range(strip):
                    i=start+x*4
                    plate[i:i+3]=bytes((49,48,45))
        out.append(write_tga(width, height, bytes(plate)))
    return out
