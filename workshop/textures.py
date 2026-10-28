"""Diffuse textures for the 3D preview: .mtl -> .dds -> small PNG.

Materials name their diffuse map as "$/_vehicle/sdkfz222" ($ = texture/common)
or relative to the material's folder. DDS maps are DXT1/DXT3/DXT5 (some are
uncompressed); the preview decodes the first mipmap no larger than `limit`.
"""
from __future__ import annotations

import posixpath
import struct
import zlib

from .syntax import parse

TEXTURE_ROOT = "texture/common/"


class TextureError(ValueError):
    pass


def diffuse_path(resources, mtl_path: str) -> str | None:
    document = parse(resources.read(mtl_path))
    reference = next((node.args[0] for node in document.walk("diffuse") if node.args), None)
    if not reference:
        return None
    reference = reference.replace("\\", "/")
    if reference.startswith("$/"):
        candidates = [TEXTURE_ROOT + reference[2:]]
    elif reference.startswith("/"):
        candidates = [reference.lstrip("/")]
    else:
        candidates = [posixpath.normpath(posixpath.join(posixpath.dirname(mtl_path), reference)),
                      TEXTURE_ROOT + reference]
    for candidate in candidates:
        for path in ((candidate,) if candidate.lower().endswith(".dds") else (candidate + ".dds", candidate)):
            if resources.exists(path):
                return path
    return None


def _rgb565(value):
    r, g, b = (value >> 11) & 31, (value >> 5) & 63, value & 31
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)


def _colour_block(raw, offset, dxt1):
    c0, c1, bits = struct.unpack_from("<HHI", raw, offset)
    a, b = _rgb565(c0), _rgb565(c1)
    if c0 > c1 or not dxt1:
        palette = [a + (255,), b + (255,),
                   tuple((2*x + y) // 3 for x, y in zip(a, b)) + (255,),
                   tuple((x + 2*y) // 3 for x, y in zip(a, b)) + (255,)]
    else:
        palette = [a + (255,), b + (255,), tuple((x + y) // 2 for x, y in zip(a, b)) + (255,), (0, 0, 0, 0)]
    return [palette[(bits >> (2*i)) & 3] for i in range(16)]


def _alpha_dxt5(raw, offset):
    a0, a1 = raw[offset], raw[offset+1]
    bits = int.from_bytes(raw[offset+2:offset+8], "little")
    if a0 > a1:
        table = [a0, a1] + [((7-i)*a0 + i*a1) // 7 for i in range(1, 7)]
    else:
        table = [a0, a1] + [((5-i)*a0 + i*a1) // 5 for i in range(1, 5)] + [0, 255]
    return [table[(bits >> (3*i)) & 7] for i in range(16)]


def _alpha_dxt3(raw, offset):
    bits = int.from_bytes(raw[offset:offset+8], "little")
    return [((bits >> (4*i)) & 15) * 17 for i in range(16)]


def decode_dds(raw: bytes, limit: int = 512):
    """(width, height, RGBA bytes) of the first mip level not larger than limit."""
    if raw[:4] != b"DDS " or len(raw) < 128:
        raise TextureError("Not a DDS file")
    height, width, _, _, mips = struct.unpack_from("<IIIII", raw, 12)
    fourcc = raw[84:88]
    bit_count, r_mask, g_mask, b_mask, a_mask = struct.unpack_from("<IIIII", raw, 88)
    offset = 128 + (20 if fourcc == b"DX10" else 0)
    mips = max(1, mips)
    if fourcc in (b"DXT1", b"DXT3", b"DXT5"):
        block = 8 if fourcc == b"DXT1" else 16

        def size(w, h):
            return max(1, (w+3)//4) * max(1, (h+3)//4) * block
    elif fourcc == b"\0\0\0\0" and bit_count in (24, 32):
        def size(w, h):
            return w * h * (bit_count // 8)
    else:
        raise TextureError(f"Unsupported DDS format {fourcc!r}")
    w, h = width, height
    for _ in range(mips - 1):
        if max(w, h) <= limit:
            break
        offset += size(w, h)
        w, h = max(1, w // 2), max(1, h // 2)
    if offset + size(w, h) > len(raw):
        raise TextureError("Truncated DDS")
    pixels = bytearray(w * h * 4)
    if fourcc in (b"DXT1", b"DXT3", b"DXT5"):
        for by in range(0, h, 4):
            for bx in range(0, w, 4):
                if fourcc == b"DXT1":
                    colours = _colour_block(raw, offset, True)
                else:
                    alpha = _alpha_dxt5(raw, offset) if fourcc == b"DXT5" else _alpha_dxt3(raw, offset)
                    colours = [c[:3] + (a,) for c, a in zip(_colour_block(raw, offset + 8, False), alpha)]
                offset += block
                for i, colour in enumerate(colours):
                    x, y = bx + i % 4, by + i // 4
                    if x < w and y < h:
                        start = (y*w + x) * 4
                        pixels[start:start+4] = bytes(colour)
    else:
        step = bit_count // 8

        def channel(value, mask):
            if not mask:
                return 255
            shift = (mask & -mask).bit_length() - 1
            return ((value & mask) >> shift) * 255 // (mask >> shift)
        for i in range(w * h):
            value = int.from_bytes(raw[offset + i*step:offset + (i+1)*step], "little")
            pixels[i*4:i*4+4] = bytes((channel(value, r_mask), channel(value, g_mask), channel(value, b_mask),
                                       channel(value, a_mask) if a_mask else 255))
    return w, h, bytes(pixels)


def png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\0" + rgba[y*width*4:(y+1)*width*4] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def material_png(resources, mtl_path: str, limit: int = 512) -> bytes:
    path = diffuse_path(resources, mtl_path)
    if path is None:
        raise TextureError(f"No diffuse texture for {mtl_path}")
    return png(*decode_dds(resources.read(path), limit))
