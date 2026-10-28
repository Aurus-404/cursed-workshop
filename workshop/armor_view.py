"""Armour volumes for the 3D view: shape, face thickness and material (read-only).

A model volume is a box, a cylinder or a ".vol" polyhedron bound to a bone. The
definition gives its thickness: a default value plus optional faces. ".vol"
files label every triangle with the face it belongs to (SIDE chunk), the way the
game picks the face thickness; measured on native hulls: 1 front, 2 top,
3 left, 4 right, 5 rear, 6 bottom. Boxes use their local axes the same way.
"""
from __future__ import annotations

import math
import struct

from .geometry import identity, multiply, node_matrix, point, resolve_path

FACES = {1: "front", 2: "top", 3: "left", 4: "right", 5: "rear", 6: "bottom"}
# Box faces by local outward axis, in the polyhedron convention above.
BOX_FACES = [((1, 0, 0), 1), ((-1, 0, 0), 5), ((0, 1, 0), 3), ((0, -1, 0), 4), ((0, 0, 1), 2), ((0, 0, -1), 6)]


def read_volume_mesh(raw: bytes):
    """EVLM: VERT (n x 3 float), INDX (n x u16), SIDE (one byte per triangle)."""
    if raw[:4] != b"EVLM":
        raise ValueError("not a volume mesh")
    i, verts, indices, sides = 4, [], [], b""
    while i + 8 <= len(raw):
        tag, count = raw[i:i+4], struct.unpack("<I", raw[i+4:i+8])[0]
        if tag == b"VERT":
            verts = [struct.unpack("<3f", raw[i+8+k*12:i+20+k*12]) for k in range(count)]
            i += 8 + count*12
        elif tag == b"INDX":
            indices = list(struct.unpack("<%dH" % count, raw[i+8:i+8+count*2]))
            i += 8 + count*2
        elif tag == b"SIDE":
            sides = raw[i+8:i+8+count]
            i += 8 + count
        else:
            break
    if len(indices) % 3 or any(v >= len(verts) for v in indices):
        raise ValueError("broken volume mesh")
    faces = [sides[t] if t < len(sides) else 0 for t in range(len(indices)//3)]
    return verts, indices, faces


def _box(sizes):
    hx, hy, hz = (s/2 for s in sizes)
    verts, indices, faces = [], [], []
    for (nx, ny, nz), code in BOX_FACES:
        if nx:
            corners = [(nx*hx, -hy, -hz), (nx*hx, hy, -hz), (nx*hx, hy, hz), (nx*hx, -hy, hz)]
        elif ny:
            corners = [(-hx, ny*hy, -hz), (hx, ny*hy, -hz), (hx, ny*hy, hz), (-hx, ny*hy, hz)]
        else:
            corners = [(-hx, -hy, nz*hz), (hx, -hy, nz*hz), (hx, hy, nz*hz), (-hx, hy, nz*hz)]
        base = len(verts)
        verts.extend(corners)
        indices.extend([base, base+1, base+2, base, base+2, base+3])
        faces.extend([code, code])
    return verts, indices, faces


def _cylinder(radius, height, segments=16):
    """Axis along local Z, centred; its faces use the default thickness."""
    verts, indices, faces = [], [], []
    for k in range(segments):
        a = 2*math.pi*k/segments
        verts.append((radius*math.cos(a), radius*math.sin(a), -height/2))
        verts.append((radius*math.cos(a), radius*math.sin(a), height/2))
    bottom, top = len(verts), len(verts)+1
    verts.extend([(0, 0, -height/2), (0, 0, height/2)])
    for k in range(segments):
        a0, a1, b0, b1 = 2*k, 2*k+1, (2*k+2) % (2*segments), (2*k+3) % (2*segments)
        indices.extend([a0, b0, b1, a0, b1, a1, bottom, b0, a0, top, a1, b1])
        faces.extend([0, 0, 0, 0])
    return verts, indices, faces


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _thickness(volume):
    node = volume.find("thickness") if volume is not None else None
    if node is None or not node.args:
        return None
    result = {"default": _number(node.args[0])}
    for child in node.children:
        if child.head.casefold() in FACES.values() and child.args:
            result[child.head.casefold()] = _number(child.args[0])
    return result if result["default"] is not None else None


def armor_volumes(resources, root, model, path, keep, adjustment=None):
    """Every armour volume whose bone `keep(bone)` accepts, in preview space."""
    from .extra_armor import _volumes, _default_material
    adjustment = adjustment or identity()
    declared = _volumes(resources, root)
    try:
        default_material = _default_material(resources, root)
    except Exception:
        default_material = None
    result = []
    for name, volume in model["volumes"].items():
        bone = volume["bone"]
        if bone not in model["bones"] or not keep(bone):
            continue
        definition = declared.get(name)
        thickness = _thickness(definition)
        if thickness is None:
            continue
        node = volume["node"]
        try:
            shape = next(n for n in node.children if n.head.casefold() in ("polyhedron", "box", "cylinder"))
            kind = shape.head.casefold()
            if kind == "polyhedron":
                verts, indices, faces = read_volume_mesh(resources.read(resolve_path(path, shape.args[0])))
            elif kind == "box":
                verts, indices, faces = _box([float(v) for v in shape.args[:3]])
            else:
                verts, indices, faces = _cylinder(float(shape.args[0]), float(shape.args[1]))
        except (StopIteration, KeyError, OSError, ValueError, IndexError):
            continue
        matrix = multiply(adjustment, multiply(model["bones"][bone]["world"], node_matrix(node)))
        positions = [v for vertex in verts for v in point(matrix, list(vertex))]
        material = definition.find("material")
        cast = definition.find("CastSteel")
        result.append({
            "name": name, "bone": bone, "thickness": thickness,
            "material": material.args[0] if material is not None and material.args else default_material or "steel",
            "cast": _number(cast.args[0]) if cast is not None and cast.args else None,
            "high_hardness": definition.find("HighHardness") is not None,
            "component": next((c.args[0] for c in definition.find_all("component") if c.args), None),
            "positions": positions, "indices": indices, "faces": faces,
        })
    return result
