"""A real crew member for the 3D view, in the pose of each seat.

One generic soldier (the German tank crewman, ger_heer_43_pzwaffe_1) stands in
for every nation: the editor only needs the body to show where and how each
crew member sits. Its skinned meshes hang from the "skin" bone in the rest
pose of the soldier's own skeleton. A seat's pose is the clip named by the
vehicle's boarding animation ({anm "<place>" {forward {end "<pose>"}}}),
resolved like the engine does through properties/animation/human/*.inc:
{blend "<pose>" {pose "<file>" begin|end}} or {sequence "<file>"}.

Vertex layout of these skins (stride 56): position, weight of the first bone,
four 1-based bone indices into the MESH chunk's bone table (which indexes the
SKIN name list), normal, uv, tangent. Two bones per vertex: w and 1-w.
"""
from __future__ import annotations

import math
import posixpath
import re
import struct

from .compiler import _animation_map, _legacy_layout, _legacy_matrix, _quat_matrix
from .geometry import inverse, multiply, parse_model, resolve_path, attached_actor_frame, identity, point
from .syntax import parse

SOLDIER = "entity/humanskin/[germans]/ger_heer_43_panzer/ger_heer_43_pzwaffe_1.mdl"
# Clips are authored on the reference skeleton; uniform models re-declare its
# bones with their own local axes, so poses are evaluated on this one.
SKELETON = "entity/humanskin/human/human.mdl"
ANIMATIONS = "properties/animation/human/"
REGISTRIES = ("_reg_vehicle_crew.inc", "_reg_human_movement.inc")


class CrewModelError(ValueError):
    pass


def _pose_registry(resources) -> dict[str, tuple[str, str]]:
    """pose name -> (clip file, "begin" | "end")."""
    registry = {}
    for name in REGISTRIES:
        try:
            document = parse(resources.read(ANIMATIONS + name))
        except (KeyError, OSError):
            continue
        for node in document.walk():
            head = node.head.casefold()
            if head == "blend" and node.args:
                pose = node.find("pose")
                if pose is not None and pose.args:
                    registry.setdefault(node.args[0], (pose.args[0], (pose.args[1:] or ["begin"])[0]))
            elif head == "sequence" and node.args:
                registry.setdefault(node.args[0], (node.args[0], "begin"))
    return registry


def _clip_locals(raw: bytes, which: str) -> dict[str, list[list[float]]]:
    """Local bone transforms at the first or last key of a clip (sparse keys
    hold their last value)."""
    names, cursor, frame_count = _animation_map(raw)
    target = 0 if which == "begin" else frame_count - 1
    result = {}
    if raw[cursor:cursor + 4] == b"FRMN":
        _, frames = _legacy_layout(raw)
        for frame in sorted(frames, key=lambda f: f["index"]):
            if frame["index"] > target and result:
                break
            for track in frame["tracks"]:
                for tag, offset in track["chunks"]:
                    if tag == b"MATR":
                        result[track["name"]] = _legacy_matrix(raw, offset)
        return result
    keys = {}
    while cursor < len(raw):
        if raw[cursor:cursor + 4] != b"FRM2":
            raise CrewModelError("Unsupported animation frame")
        index, count = struct.unpack_from("<H", raw, cursor + 4)[0], raw[cursor + 6]
        cursor += 7
        for _ in range(count):
            bone, flags = raw[cursor], struct.unpack_from("<H", raw, cursor + 1)[0]
            cursor += 3
            position = struct.unpack_from("<3f", raw, cursor) if flags & 1 else None
            cursor += 12 if flags & 1 else 0
            rotation = struct.unpack_from("<3f", raw, cursor) if flags & 2 else None
            cursor += 12 if flags & 2 else 0
            if flags & 32:
                cursor += 4 + struct.unpack_from("<I", raw, cursor)[0]
            if bone < len(names) and (index <= target or names[bone] not in keys):
                previous = keys.get(names[bone], (None, None))
                keys[names[bone]] = (position or previous[0], rotation or previous[1])
    for name, (position, rotation) in keys.items():
        result[name] = (position, rotation)
    return result


def _posed_worlds(model, clip):
    """World matrices of every bone with the clip applied over the rest pose."""
    worlds = {}

    def local(name):
        rest = model["bones"][name]["local"]
        value = clip.get(name)
        if value is None:
            return rest
        if isinstance(value, list):  # legacy full matrix
            return value
        position, rotation = value
        matrix = [row[:] for row in rest]
        if rotation is not None:
            rotated = _quat_matrix(rotation)
            for r in range(3):
                matrix[r][:3] = rotated[r][:3]
        if position is not None:
            for r in range(3):
                matrix[r][3] = position[r]
        return matrix

    def world(name):
        if name not in worlds:
            parent = model["bones"][name]["parent"]
            worlds[name] = multiply(world(parent), local(name)) if parent else local(name)
        return worlds[name]

    for name in model["bones"]:
        world(name)
    return worlds


def _skin_meshes(resources, model):
    """(path, raw) of LOD0 skinned meshes hanging from the "skin" bone."""
    skin = model["bones"].get("skin")
    if skin is None:
        raise CrewModelError("Soldier model has no skin bone")
    paths = []
    for node in skin["node"].children:
        head = node.head.casefold()
        if head == "lodview":
            first = next((child for child in node.children if child.head.casefold() == "volumeview" and child.args), None)
            if first is not None:
                paths.append(first.args[0])
        elif head == "volumeview" and node.args:
            paths.append(node.args[0])
    for reference in paths:
        if "null" in reference.casefold():
            continue
        path = resolve_path(SOLDIER, reference)
        try:
            yield path, resources.read(path)
        except (KeyError, OSError):
            continue


def _read_skinned(raw: bytes):
    """Parts of one skinned EPLY, one per MESH chunk: material, triangles and
    their vertices (position, normal, uv, [(bone, weight)]).

    MESH: flags, first triangle, triangle count, a constant, the material
    name, then its bone table. A vertex names two table slots (0-based) and
    the weight of the first; a table value v is SKIN name v-1 (0 = root).
    Several MESH chunks may share one VERT/INDX pair.
    """
    start = raw.find(b"SKIN")
    if start < 0:
        raise CrewModelError("Mesh is not skinned")
    count, cursor, names = struct.unpack_from("<I", raw, start + 4)[0], start + 8, []
    for _ in range(count):
        length = raw[cursor]
        names.append(raw[cursor + 1:cursor + 1 + length].decode("ascii", "replace"))
        cursor += 1 + length
    parts = []
    while True:
        vert = raw.find(b"VERT", cursor)
        if vert < 0:
            break
        meshes, probe = [], cursor
        while True:
            mesh = raw.find(b"MESH", probe, vert)
            if mesh < 0:
                break
            _, first, triangles, _ = struct.unpack_from("<4I", raw, mesh + 4)
            length = raw[mesh + 20]
            material = raw[mesh + 21:mesh + 21 + length].decode("ascii", "replace")
            table_at = mesh + 21 + length
            table = list(raw[table_at + 1:table_at + 1 + raw[table_at]])
            meshes.append((first, triangles, material if material.lower().endswith(".mtl") else None, table))
            probe = table_at + 1 + raw[table_at]
        vertices, stride, _ = struct.unpack_from("<IHH", raw, vert + 4)
        if stride < 40:
            raise CrewModelError("Unsupported skinned vertex layout")
        base = vert + 12
        index_at = base + vertices * stride
        if raw[index_at:index_at + 4] != b"INDX":
            raise CrewModelError("Skinned mesh without index chunk")
        total = struct.unpack_from("<I", raw, index_at + 4)[0]
        indices = struct.unpack_from(f"<{total}H", raw, index_at + 8)
        if not meshes:
            meshes = [(0, total // 3, None, list(range(1, len(names) + 1)))]
        normal_at, uv_at = (20, 32) if stride >= 56 else (20, 32)
        for first, triangles, material, table in meshes:
            remap, rows, out = {}, [], []
            for index in indices[first * 3:(first + triangles) * 3]:
                if index >= vertices:
                    raise CrewModelError("Skinned index out of range")
                if index not in remap:
                    o = base + index * stride
                    x, y, z, weight = struct.unpack_from("<4f", raw, o)
                    slots = raw[o + 16:o + 20]
                    bones = []
                    for slot, share in ((slots[0], weight), (slots[1], 1 - weight)):
                        if share <= 1e-4 or slot >= len(table):
                            continue
                        value = table[slot]
                        bones.append((names[value - 1] if 0 < value <= len(names) else None, share))
                    remap[index] = len(rows)
                    rows.append(((x, y, z), struct.unpack_from("<3f", raw, o + normal_at),
                                 struct.unpack_from("<2f", raw, o + uv_at), bones))
                out.append(remap[index])
            parts.append({"material": material, "vertices": rows, "indices": out})
        cursor = index_at + 8 + total * 2
    return parts


def pose_for(resources, pose: str | None):
    """(clip path, which key) for a pose name, or None for the rest pose."""
    if not pose:
        return None
    entry = _pose_registry(resources).get(pose, (pose, "begin"))
    path = ANIMATIONS + entry[0] + ".anm"
    return (path, entry[1]) if resources.exists(path) else None


def posed_soldier(resources, pose: str | None = None) -> dict:
    """Meshes of the generic soldier in a seat pose, in the soldier's own
    space (its root sits on the seat bone, as in the game)."""
    model = parse_model(resources.read(SOLDIER))
    clip = {}
    source = pose_for(resources, pose)
    if source:
        try:
            clip = _clip_locals(resources.read(source[0]), source[1])
        except (ValueError, struct.error, KeyError, OSError):
            clip = {}
    skin = model["bones"]["skin"]["world"]
    try:
        skeleton = parse_model(resources.read(SKELETON))
    except (KeyError, OSError):
        skeleton = model
    posed = _posed_worlds(skeleton, clip)
    # World-space deformation per bone: independent of each model's local axes.
    deform = skin_deformations(model, posed) if clip else {}
    # Boarding attaches the actor root to the seat, replacing its standalone
    # basis. Skinning above still needs that basis; remove it only afterwards.
    attachment = attached_actor_frame(model, identity())
    # Pose clips can offset the pelvis far from the boarding socket. Keep that
    # authored placement separate from the correction of mirrored actor axes.
    body = posed.get('body') if clip else (model['bones'].get('body') or {}).get('world')
    pose_origin = point(attachment, point(body,[0,0,0])) if body is not None else [0,0,0]
    meshes = []
    for path, raw in _skin_meshes(resources, model):
        try:
            chunks = _read_skinned(raw)
        except (CrewModelError, struct.error, IndexError):
            continue
        for chunk in chunks:
            positions, normals, uvs = [], [], []
            for position, normal, uv, bones in chunk["vertices"]:
                base = [sum(skin[r][c] * position[c] for c in range(3)) + skin[r][3] for r in range(3)]
                n0 = [sum(skin[r][c] * normal[c] for c in range(3)) for r in range(3)]
                if not bones:
                    bones = [("skin", 1.0)]
                out, nout = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
                for name, share in bones:
                    matrix = deform.get(name) if name else deform.get("basis")
                    if matrix is None:
                        matrix = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]
                    for r in range(3):
                        out[r] += share * (sum(matrix[r][c] * base[c] for c in range(3)) + matrix[r][3])
                        nout[r] += share * sum(matrix[r][c] * n0[c] for c in range(3))
                out = [sum(attachment[r][c] * out[c] for c in range(3)) + attachment[r][3] for r in range(3)]
                nout = [sum(attachment[r][c] * nout[c] for c in range(3)) for r in range(3)]
                length = math.sqrt(sum(v * v for v in nout)) or 1.0
                positions.extend(round(v, 4) for v in out)
                normals.extend(round(v / length, 4) for v in nout)
                uvs.extend(round(v, 5) if math.isfinite(v) else 0.0 for v in uv)
            mtl = skin_material(resources, SOLDIER, path, chunk["material"])
            # Native skinned EPLY uses clockwise faces. The actor basis was
            # removed above; Three now applies the seat frame itself (including
            # its reflection), so it also handles that frame's front-face flip.
            # Convert local faces once, otherwise skin normals point away from
            # the visible face and double-sided textures appear inside out.
            triangles=chunk['indices']
            indices=[v for i in range(0,len(triangles),3) for v in (triangles[i],triangles[i+2],triangles[i+1])]
            meshes.append({"positions": positions, "normals": normals, "uv": uvs, "indices": indices,
                           "submeshes": [{"start": 0, "count": len(chunk["indices"]), "mtl": mtl}]})
    if not meshes:
        raise CrewModelError("The soldier model could not be read")
    return {"pose": pose, "clip": source[0] if source else None, "meshes": meshes,
            "pose_origin": [round(v,6) for v in pose_origin]}


def skin_deformations(model, posed):
    """Skin vertices were bound to this uniform, not the reference clip rig."""
    return {name: multiply(posed[name], inverse(bone['world']))
            for name, bone in model['bones'].items() if name in posed}


def skin_material(resources, model_path, mesh_path, material):
    if not material:
        return None
    # Shared clothing meshes use the selected uniform's local material overrides.
    for candidate in (resolve_path(model_path, material), resolve_path(mesh_path, material)):
        if resources.exists(candidate):
            return candidate
    return None
