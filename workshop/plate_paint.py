"""Worn dark steel for made-to-measure plates: an original, seamless texture.

Drawn from fixed pseudo-random noise, so every build gives the same bytes and
no game or third-party image is used. It repeats without a visible seam.
"""
import random
import struct
from functools import lru_cache

SIZE = 128


def _layer(rng, cells):
    """Smooth noise in 0..1 on a grid that wraps around (so the texture tiles)."""
    lattice = [[rng.random() for _ in range(cells)] for _ in range(cells)]
    step = SIZE/cells
    rows = []
    for y in range(SIZE):
        fy = y/step; y0 = int(fy); ty = fy-y0; ty = ty*ty*(3-2*ty)
        a, b = lattice[y0 % cells], lattice[(y0+1) % cells]
        row = []
        for x in range(SIZE):
            fx = x/step; x0 = int(fx); tx = fx-x0; tx = tx*tx*(3-2*tx)
            x1 = (x0+1) % cells; x0 %= cells
            top = a[x0]+(a[x1]-a[x0])*tx; bottom = b[x0]+(b[x1]-b[x0])*tx
            row.append(top+(bottom-top)*ty)
        rows.append(row)
    return rows


@lru_cache(maxsize=1)
def pixels():
    """SIZE x SIZE rows of (r, g, b): dark gray steel, cool and rusty patches, fine grain."""
    rng = random.Random(1943)
    shade = [_layer(rng, cells) for cells in (4, 8, 16, 32)]
    rust, cool, grain = _layer(rng, 4), _layer(rng, 8), _layer(rng, 64)
    result = []
    for y in range(SIZE):
        row = []
        for x in range(SIZE):
            light = (shade[0][y][x]*.22+shade[1][y][x]*.26+shade[2][y][x]*.30+shade[3][y][x]*.22)
            value = 26+light*62+(grain[y][x]-.5)*12
            warm = max(0.0, rust[y][x]-.56)*1.5   # brown rust where the paint wore off
            cold = max(0.0, cool[y][x]-.60)*.9   # bluish bare steel
            r = value*(1.03+.30*warm-.08*cold); g = value*(1+.06*warm-.01*cold); b = value*(.97-.20*warm+.12*cold)
            row.append(tuple(max(0, min(255, int(c+.5))) for c in (r, g, b)))
        result.append(row)
    return result


def _halve(rows):
    size = len(rows)//2
    return [[tuple((rows[2*y][2*x][c]+rows[2*y][2*x+1][c]+rows[2*y+1][2*x][c]+rows[2*y+1][2*x+1][c]+2)//4 for c in range(3))
             for x in range(size)] for y in range(size)]


def _565(color):
    return ((color[0]*31+127)//255 << 11) | ((color[1]*63+127)//255 << 5) | ((color[2]*31+127)//255)


def _888(value):
    return ((value >> 11)*255//31, ((value >> 5) & 63)*255//63, (value & 31)*255//31)


def _block(colors):
    """One DXT1 block of 16 colours: darkest and lightest as ends, two blends between."""
    key = lambda c: c[0]*2+c[1]*5+c[2]
    high, low = _565(max(colors, key=key)), _565(min(colors, key=key))
    if high == low:
        return struct.pack('<HHI', high, low, 0)
    if high < low:
        high, low = low, high
    a, b = _888(high), _888(low)
    palette = [a, b, tuple((2*a[i]+b[i])//3 for i in range(3)), tuple((a[i]+2*b[i])//3 for i in range(3))]
    bits = 0
    for i, color in enumerate(colors):
        best = min(range(4), key=lambda k: sum((color[j]-palette[k][j])**2 for j in range(3)))
        bits |= best << (2*i)
    return struct.pack('<HHI', high, low, bits)


@lru_cache(maxsize=1)
def texture_bytes():
    """DXT1 with every mip level down to 1x1, as the game's own vehicle textures."""
    rows, chunks, levels = pixels(), [], 0
    while True:
        size = len(rows); levels += 1
        for by in range(0, size, 4):
            for bx in range(0, size, 4):
                chunks.append(_block([rows[min(by+j, size-1)][min(bx+i, size-1)] for j in range(4) for i in range(4)]))
        if size == 1:
            break
        rows = _halve(rows)
    header = struct.pack('<7I', 124, 0xA1007, SIZE, SIZE, SIZE*SIZE//2, 0, levels)+bytes(44)
    header += struct.pack('<II4s5I', 32, 4, b'DXT1', 0, 0, 0, 0, 0)+struct.pack('<5I', 0x401008, 0, 0, 0, 0)
    return b'DDS '+header+b''.join(chunks)
