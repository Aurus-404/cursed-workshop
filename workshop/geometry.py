"""GEM skeleton and EPLY geometry reader; no engine or original-mod code runs."""
from __future__ import annotations

import hashlib
import math
import re
import posixpath
import struct
from .syntax import Node, Document, parse, bone_name


class GeometryError(ValueError):
    pass


def identity():
    return [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def multiply(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def linear_determinant(matrix):
    """Handedness of the affine frame, without its translation."""
    a=matrix
    return (a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1])
            -a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])
            +a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0]))


def inverse(a):
    """General inverse: some shipped basis transforms are reflections, not rotations."""
    rows = [list(a[r]) + identity()[r] for r in range(4)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-12:
            raise GeometryError("Singular bone transformation")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        divisor = rows[col][col]
        rows[col] = [x / divisor for x in rows[col]]
        for r in range(4):
            if r != col:
                factor = rows[r][col]
                rows[r] = [rows[r][c] - factor * rows[col][c] for c in range(8)]
    return [row[4:] for row in rows]


def point(matrix, value):
    return [sum(matrix[r][c] * value[c] for c in range(3)) + matrix[r][3] for r in range(3)]


def attached_actor_frame(model, socket):
    """Attach the actor root to a socket, removing its standalone rest frame."""
    roots = [bone for bone in model['bones'].values() if bone['parent'] is None]
    if len(roots) != 1:
        raise GeometryError('No se pudo verificar el marco de enganche del arma.')
    return multiply(socket, inverse(roots[0]['world']))


def translation(value):
    result = identity()
    for i in range(3):
        result[i][3] = float(value[i])
    return result


def euler_matrix(rotation=(0, 0, 0), position=(0, 0, 0)):
    """X then Y then Z, degrees, column vectors; matches the browser XYZ controls."""
    if len(rotation) != 3 or len(position) != 3:
        raise GeometryError("Transformation requires three translation and rotation values")
    values = [float(x) for x in [*rotation, *position]]
    if not all(math.isfinite(x) for x in values):
        raise GeometryError("Transformation contains a non-finite value")
    x, y, z = [math.radians(v) for v in values[:3]]
    cx, cy, cz, sx, sy, sz = math.cos(x), math.cos(y), math.cos(z), math.sin(x), math.sin(y), math.sin(z)
    result = identity()
    result[0][:3] = [cz*cy, cz*sy*sx-sz*cx, cz*sy*cx+sz*sx]
    result[1][:3] = [sz*cy, sz*sy*sx+cz*cx, sz*sy*cx-cz*sx]
    result[2][:3] = [-sy, cy*sx, cy*cx]
    for i in range(3):
        result[i][3] = values[i+3]
    return result


def node_matrix(node: Node):
    result = identity()
    matrix = node.find("matrix34")
    orientation = node.find("orientation")
    position = node.find("position")
    try:
        if matrix:
            values = [float(x) for x in matrix.args]
            if len(values) != 12:
                raise GeometryError("Matrix34 must contain twelve values")
            for r in range(3):
                for c in range(3):
                    result[r][c] = values[c*3+r]
                result[r][3] = values[9+r]
        elif orientation:
            values = [float(x) for x in orientation.args]
            if len(values) != 9:
                raise GeometryError("Orientation must contain nine values")
            for r in range(3):
                for c in range(3):
                    result[r][c] = values[c*3+r]
        if position:
            if len(position.args) != 3:
                raise GeometryError("Position must contain three values")
            for r in range(3):
                result[r][3] = float(position.args[r])
    except ValueError as error:
        raise GeometryError(f"Invalid transform for bone {bone_name(node)!r}: {error}") from error
    if not all(math.isfinite(x) for row in result for x in row):
        raise GeometryError("Non-finite skeleton transformation")
    return result


def _view_assets(node):
    """Meshes a bone shows: direct {VolumeView}s plus the most detailed level
    of each {LODView} (a quarter of all vehicle meshes, e.g. Sd.Kfz 222 wheels)."""
    assets = [child.args[0] for child in node.find_all("VolumeView") if child.args]
    for lod in node.find_all("LODView"):
        first = next((child for child in lod.find_all("VolumeView") if child.args), None)
        if first is not None:
            assets.append(first.args[0])
    return assets


def _initially_visible(node):
    """Native initial draw state; keep hidden assets available to the compiler."""
    visibility = node.find("visibility")
    if visibility is not None and visibility.args:
        try:
            return float(visibility.args[0]) != 0
        except ValueError:
            pass
    return True


# Bones that carry a weapon: gun/gun_rot and their numbered copies on the
# auxiliary turrets of multi-turret tanks (T-35: gun1, gun_rot2), and machine guns,
# also when a name clash made the tool rename them (a Pz I turret on a Pz IV
# roof keeps "module_gun_rot"), so an added part stays adjustable once built.
_WEAPON_BONE = re.compile(r"(?:module_)*(?:gun(?:_rot)?_?\d*|mgun.*)$", re.IGNORECASE)


def parse_model(text: str | bytes) -> dict:
    document = parse(text)
    skeleton = document.find("skeleton")
    if skeleton is None:
        raise GeometryError("Model has no direct Skeleton block; resolve its model extension first")
    bones, duplicates = {}, []

    def visit(node, parent, parent_matrix, parent_visible):
        name = bone_name(node)
        if name is None:
            return
        matrix = node_matrix(node)
        world = multiply(parent_matrix, matrix)
        entry = {"name": name, "parent": parent, "node": node, "local": matrix, "world": world,
                 "assets": _view_assets(node), "visible": parent_visible and _initially_visible(node)}
        if name in bones:
            # Shipped models repeat locator names (ARL-44 has a "visor1" in the
            # turret and another in the hull). A name resolves to the first
            # one; the repeat stays in the file untouched, inside its subtree.
            duplicates.append(entry)
        else:
            bones[name] = entry
        for child in node.find_all("bone"):
            visit(child, name, world, entry["visible"])

    for node in skeleton.find_all("bone"):
        visit(node, None, identity(), _initially_visible(skeleton))
    volumes = {}
    for node in document.find_all("volume"):
        owner = node.find("bone")
        if node.args:
            volumes[node.args[0]] = {"node": node, "bone": owner.args[0] if owner and owner.args else None}
    mounts = [name for name, bone in bones.items()
              if len(bone["node"].args) > 1 and bone["node"].args[0].casefold() == "revolute"
              and any(_WEAPON_BONE.match(desc) for desc in descendants(bones, name))]
    anchor_name = "turret" if "turret" in bones else (mounts[0] if len(mounts) == 1 else None)
    return {"document": document, "skeleton": skeleton, "bones": bones, "volumes": volumes, "duplicates": duplicates,
            "mounts": mounts, "anchor_name": anchor_name,
            "anchor": point(bones[anchor_name]["world"], [0, 0, 0]) if anchor_name else [0, 0, 0]}


def descendants(bones, root):
    result = {root}
    changed = True
    while changed:
        changed = False
        for name, bone in bones.items():
            if bone["parent"] in result and name not in result:
                result.add(name)
                changed = True
    return result


def resolve_path(base_path, reference):
    reference = reference.replace("\\", "/")
    if reference.startswith("/"):
        return reference.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_path), reference))


_VALID_MESHES: set[bytes] = set()


def validate_mesh(raw: bytes) -> None:
    """read_mesh's checks without keeping the vertices, remembered by content.

    One build validates each mesh several times (copy, material patch, wrecks,
    final export check); the same bytes always give the same answer, so a mesh
    whose digest already passed is not decoded again. Changed bytes have a new
    digest and are checked in full.
    """
    digest = hashlib.blake2b(raw, digest_size=16).digest() if isinstance(raw, bytes) else None
    if digest is not None and digest in _VALID_MESHES:
        return
    read_mesh(raw)
    if digest is not None:
        if len(_VALID_MESHES) > 50_000:
            _VALID_MESHES.clear()
        _VALID_MESHES.add(digest)


def read_mesh(raw: bytes) -> dict:
    """Read validated EPLY vertex/index chunks (32/48-byte native layouts).

    EPLY stores a four-byte layout descriptor once after vertex count, followed
    by packed vertices. Each candidate is checked against the exact INDX boundary
    so a marker inside floating point payload cannot be mistaken for a chunk.
    """
    if not isinstance(raw, bytes) or not raw.startswith(b"EPLY"):
        raise GeometryError("Expected an EPLY binary mesh")
    positions, normals, uv, indices, submeshes = [], [], [], [], []
    invalid = unused = 0
    cursor = 4
    while True:
        offset = raw.find(b"VERT", cursor)
        if offset < 0:
            break
        cursor = offset + 4
        if offset + 12 > len(raw):
            continue
        count, stride, flags = struct.unpack_from("<IHH", raw, offset+4)
        if not 0 < count <= 2_000_000 or stride not in (32, 36, 40, 44, 48, 52, 56, 64):
            continue
        index_offset = offset + 12 + count * stride
        if raw[index_offset:index_offset+4] != b"INDX" or index_offset+8 > len(raw):
            continue
        index_count = struct.unpack_from("<I", raw, index_offset+4)[0]
        if index_count % 3 or index_count > 12_000_000 or index_offset+8+index_count*2 > len(raw):
            raise GeometryError("Truncated or non-triangular EPLY index chunk")
        base = len(positions)//3
        first_index = len(indices)
        # MESH stores the native D3D flexible vertex format. Blend weights and
        # packed bone indices precede normals on skinned wheels/tracks; stride
        # alone cannot distinguish them from rigid meshes with extra UV sets.
        normal_offset, uv_offset = 12, 24
        mesh_header = raw.rfind(b'MESH', 0, offset)
        if mesh_header >= 0:
            fvf = struct.unpack_from('<I', raw, mesh_header+4)[0]
            position_format = fvf & 0x400e
            if position_format in (2, 4, 6, 8, 10, 12, 14):
                blend_bytes = 0 if position_format == 2 else 4 if position_format == 4 else 2*(position_format-4)
                normal_offset = 12 + blend_bytes
                uv_offset = normal_offset + (12 if fvf & 0x10 else 0)
                uv_offset += sum(4 for flag in (0x20, 0x40, 0x80) if fvf & flag)
                if uv_offset+8 > stride or (fvf & 0x10 and normal_offset+12 > stride):
                    raise GeometryError('EPLY vertex layout exceeds its declared stride')
        fallback = None
        for vertex in range(count):
            data = struct.unpack_from("<8f", raw, offset+12+vertex*stride)
            # Shipped meshes carry NaN texture coordinates (T-20 dt29.ply) and
            # even NaN positions (Churchill) that the engine tolerates. The file
            # is copied untouched; for fitting/preview a broken position takes a
            # valid vertex of the same chunk so bounds and anchors stay right.
            position = data[:3]
            if all(math.isfinite(x) for x in position):
                fallback = fallback or position
            else:
                invalid += 1
                position = fallback or next((candidate[:3] for candidate in
                                             (struct.unpack_from("<3f", raw, offset+12+other*stride) for other in range(count))
                                             if all(math.isfinite(x) for x in candidate)), (0.0, 0.0, 0.0))
            positions.extend(position)
            start = offset+12+vertex*stride
            normal = struct.unpack_from('<3f', raw, start+normal_offset)
            texture_uv = struct.unpack_from('<2f', raw, start+uv_offset)
            normals.extend(x if math.isfinite(x) else 0.0 for x in normal)
            uv.extend(x if math.isfinite(x) else 0.0 for x in texture_uv)
        for index in struct.unpack_from(f"<{index_count}H", raw, index_offset+8):
            if index >= count:
                raise GeometryError("EPLY index references a missing vertex")
            indices.append(base+index)
        # Vertices no triangle uses are never drawn. Some ship as garbage
        # (MACE nbfz/pz2j body.ply: 1e22 coordinates) and would blow up bounds,
        # framing and fitting: they take the position of a drawn vertex.
        drawn = set(indices[first_index:])
        if drawn and len(drawn) < count:
            anchor = 3*min(drawn)
            for vertex in range(base, base+count):
                if vertex not in drawn:
                    positions[3*vertex:3*vertex+3] = positions[anchor:anchor+3]
                    unused += 1
        # Skinned wheels/tracks have a bone table between MESH's material and
        # VERT. Resolve the owning chunk instead of searching directly at VERT.
        references=mesh_materials(raw)
        material=next((name for at,name in reversed(references) if at<offset),None)
        submeshes.append({"material": material, "start": first_index, "count": index_count,
                          "vertex_count": count, "stride": stride, "flags": flags})
        cursor = index_offset+8+index_count*2
    if not positions:
        raise GeometryError("No supported, structurally valid EPLY vertex/index layout")
    bounds = bounds_of(positions)
    return {"positions": positions, "normals": normals, "uv": uv, "indices": indices,
            "submeshes": submeshes, "bounds": bounds, "vertex_count": len(positions)//3,
            "triangle_count": len(indices)//3, "invalid_vertices": invalid,
            "unused_vertices": unused}


def reverse_mesh_winding(raw: bytes) -> bytes:
    """Reverse validated triangle indices only; preserve every vertex channel."""
    mesh=read_mesh(raw)
    output=bytearray(raw);cursor=4;chunks=0
    while (offset:=raw.find(b'VERT',cursor))>=0:
        cursor=offset+4
        if offset+12>len(raw):continue
        count,stride,_=struct.unpack_from('<IHH',raw,offset+4)
        if not 0<count<=2_000_000 or stride not in (32,36,40,44,48,52,56,64):continue
        end=offset+12+count*stride
        if raw[end:end+4]!=b'INDX' or end+8>len(raw):continue
        size=struct.unpack_from('<I',raw,end+4)[0]
        if size%3 or size>12_000_000 or end+8+size*2>len(raw):
            raise GeometryError('Truncated or non-triangular EPLY index chunk')
        for position in range(end+8,end+8+size*2,6):
            output[position+2:position+4],output[position+4:position+6]=raw[position+4:position+6],raw[position+2:position+4]
        cursor=end+8+size*2;chunks+=1
    if chunks!=len(mesh['submeshes']):raise GeometryError('Could not verify every EPLY index chunk')
    return bytes(output)


def mesh_materials(raw: bytes) -> list[tuple[int, str]]:
    """Material references of every EPLY MESH chunk as (length-byte offset, name).

    Layout: "MESH", five uint32 fields, a length-prefixed material path, then
    (for skinned meshes) a bone list. About 3% of shipped meshes (tactical
    number plates, many glass parts) carry only four fields, so the path's
    length byte sits at +20 instead of +24. A file may hold several MESH
    chunks, so looking only right before VERT misses all but one, and all
    skinned ones.
    """
    found, cursor = [], 4
    while True:
        offset = raw.find(b"MESH", cursor)
        if offset < 0 or offset + 21 > len(raw):
            return found
        cursor = offset + 4
        for at in (offset+24, offset+20):
            if at >= len(raw):
                continue
            length = raw[at]
            name = raw[at+1:at+1+length]
            if length and name.lower().endswith(b".mtl") and all(32 <= x < 127 for x in name):
                found.append((at, name.decode("ascii")))
                break


def bounds_of(positions):
    if not positions:
        return {"min": [0, 0, 0], "max": [0, 0, 0]}
    return {"min": [min(positions[i::3]) for i in range(3)], "max": [max(positions[i::3]) for i in range(3)]}


_SEAT_IGNORED = ("antenna", "aerial", "fx", "emit", "cw_smoke_", "mw_armor_", "cmdmg_", "mw_cargo_")


def mount_seat(resources, model, model_path, anchor_name):
    """Height of the hull roof under a mount, in the mount bone's frame.

    Each module should rest on its new hull the way it rested on its original
    one: the ring radius comes from the mount's own shell mesh, and the seat is
    the highest hull vertex within that ring. Bone origins are not comparable
    between models (some sit on the ring, others deep inside the hull).
    Returns None when the mount has no shell mesh or nothing lies under it.
    """
    if not anchor_name or anchor_name not in model["bones"]:
        return None
    selected = descendants(model["bones"], anchor_name)
    adjustment = translation([-v for v in point(model["bones"][anchor_name]["world"], [0, 0, 0])])

    def vertices(bone):
        matrix = multiply(adjustment, bone["world"])
        for reference in bone["assets"]:
            try:
                mesh = read_mesh(resources.read(resolve_path(model_path, reference)))
            except (OSError, KeyError, ValueError):
                continue
            for i in range(0, len(mesh["positions"]), 3):
                yield point(matrix, mesh["positions"][i:i+3])

    shell = list(vertices(model["bones"][anchor_name]))
    if not shell:
        return None
    radius = min(max(abs(p[0]) for p in shell), max(abs(p[1]) for p in shell)) * 1.2
    seat = None
    for name, bone in model["bones"].items():
        if name in selected or name.casefold().startswith(_SEAT_IGNORED):
            continue
        for p in vertices(bone):
            if math.hypot(p[0], p[1]) < radius and (seat is None or p[2] > seat):
                seat = p[2]
    return seat


FREE_MOUNT = "@free"
# Spots a crew-served weapon can be added on besides the deck: the turret roof
# and hatches/cupolas, named this way across nations and mods.
_SPOT_PATTERN = re.compile(r"hatch|cupola|commander|lid|lyuk", re.IGNORECASE)


def replaceable_mounts(model):
    """A replacement must leave the structural chassis in place.

    Some vehicles expose a revolute skeleton root enclosing every weapon and
    the whole hull. That root is an animation frame, not a removable turret.
    """
    structural = free_mount_parent(model)
    candidates = list(model['mounts'])
    if model['anchor_name'] in model['bones'] and model['anchor_name'] not in candidates:
        candidates.append(model['anchor_name'])
    return [name for name in candidates if model['bones'][name]['parent'] is not None
            and structural not in descendants(model['bones'], name)]


def recipient_mount_name(model, requested=None):
    if requested:
        if (requested in model['bones'] and
                (model['bones'][requested]['parent'] is None or
                 free_mount_parent(model) in descendants(model['bones'], requested))):
            raise GeometryError('Ese punto incluye el chasis completo. Elegí un montaje libre para conservar el vehículo.')
        return requested
    return model['anchor_name'] if model['anchor_name'] in replaceable_mounts(model) else FREE_MOUNT


def free_mount_spec(name):
    """"@free" -> (True, None); "@free:<bone>" -> (True, bone); else (False, None)."""
    if not name or not str(name).startswith(FREE_MOUNT):
        return False, None
    rest = str(name)[len(FREE_MOUNT):]
    return True, (rest[1:] if rest.startswith(":") and len(rest) > 1 else None)


def free_mount_options(model):
    """Free mounts a chassis offers: deck, turret roof, hatches and cupolas."""
    options = [FREE_MOUNT]
    if model["anchor_name"] in model["bones"]:
        options.append(f"{FREE_MOUNT}:{model['anchor_name']}")
    for name, bone in model["bones"].items():
        if _SPOT_PATTERN.search(name) and bone["assets"] and name != model["anchor_name"]:
            options.append(f"{FREE_MOUNT}:{name}")
    return options


def free_spot_parent(model, spot):
    """Bone a module added at `spot` hangs from: the turret itself (it turns
    with the turret); for a hatch, the part that carries it (the lid opens)."""
    if spot not in model["bones"]:
        raise GeometryError(f"Mount spot {spot!r} does not exist")
    if spot == model["anchor_name"]:
        return spot
    return model["bones"][spot]["parent"] or spot


def whole_model_mount(model):
    """Root bone of a model, used when a weapon has no pivot of its own
    (e.g. the Solothurn AT rifle): the whole model is the module."""
    roots = [name for name, bone in model["bones"].items() if bone["parent"] is None]
    return roots[0] if roots else None


def donor_mount_name(model):
    """A module's default mount: its turret, unless nothing visible hangs from
    it (MACE T91: the rocket tubes hang from the body and the turret is a bare
    aiming frame, so the turret alone showed and moved nothing). Then the whole
    model moves, as a weapon without a pivot of its own does."""
    anchor = model["anchor_name"]
    if anchor in model["bones"] and any(model["bones"][name]["assets"] for name in descendants(model["bones"], anchor)):
        return anchor
    return whole_model_mount(model)


def free_mount_parent(model):
    """Structural bone a free-mounted module hangs from."""
    if "body" in model["bones"]:
        return "body"
    for name, bone in model["bones"].items():
        if bone["assets"]:
            return name
    return whole_model_mount(model)


def _points(resources, model, model_path, names, frame=None):
    inverse_frame = inverse(frame) if frame is not None else identity()
    for name in names:
        bone = model["bones"][name]
        matrix = multiply(inverse_frame, bone["world"])
        for reference in bone["assets"]:
            try:
                mesh = read_mesh(resources.read(resolve_path(model_path, reference)))
            except (OSError, KeyError, ValueError):
                continue
            for i in range(0, len(mesh["positions"]), 3):
                yield point(matrix, mesh["positions"][i:i+3])


def module_floor(resources, model, model_path, mount):
    """Lowest point of a module in its mount frame (a gun's base/wheels)."""
    if not mount or mount not in model["bones"]:
        return None
    heights = [p[2] for p in _points(resources, model, model_path, descendants(model["bones"], mount),
                                      model["bones"][mount]["world"])]
    return min(heights) if heights else None


def deck_point(resources, model, model_path, parent=None):
    """Default free-mount position in world space: the top surface over the
    rear third of the vehicle, i.e. a truck bed or a tank's engine deck (the
    centre is the cab or the turret roof). All parts count, since a truck's
    bed is not always a child of its "body" bone; the user adjusts from here."""
    points = list(_points(resources, model, model_path, set(model["bones"])))
    if not points:
        origin = model["bones"][parent]["world"] if parent in model["bones"] else identity()
        return point(origin, [0, 0, 0])
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    cy = (min(ys) + max(ys)) / 2
    radius = max(1.0, 0.15 * min(max(xs) - min(xs), max(ys) - min(ys)))
    for fraction in (0.25, 0.35, 0.5):  # forward is +x; step forward if the rear is empty
        cx = min(xs) + fraction * (max(xs) - min(xs))
        near = [p[2] for p in points if math.hypot(p[0] - cx, p[1] - cy) <= radius]
        if near:
            return [cx, cy, max(near)]
    return [(min(xs) + max(xs)) / 2, cy, max(p[2] for p in points)]


def free_anchor(resources, hull_model, hull_path, donor_model, donor_path, donor_mount, spot=None):
    """Where a free-mounted module's mount goes, in the chassis' world space.

    Horizontal position: the rear-deck point, or the chosen spot (turret roof,
    hatch). Height: the highest part of the chassis under the module's whole
    footprint, so a long gun on a small car rests on the roof instead of
    sinking into the cabin, and an MG on a cupola sits on top of it.
    """
    if spot in hull_model["bones"]:
        deck = point(hull_model["bones"][spot]["world"], [0, 0, 0])
    else:
        deck = deck_point(resources, hull_model, hull_path)
    if not donor_mount or donor_mount not in donor_model["bones"]:
        return deck
    footprint = list(_points(resources, donor_model, donor_path, descendants(donor_model["bones"], donor_mount),
                             donor_model["bones"][donor_mount]["world"]))
    if not footprint:
        return deck
    x0, x1 = min(p[0] for p in footprint), max(p[0] for p in footprint)
    y0, y1 = min(p[1] for p in footprint), max(p[1] for p in footprint)
    under = [p[2] for p in _points(resources, hull_model, hull_path, set(hull_model["bones"]))
             if deck[0] + x0 <= p[0] <= deck[0] + x1 and deck[1] + y0 <= p[1] <= deck[1] + y1]
    return [deck[0], deck[1], max(under) if under else deck[2]]


def is_carried_weapon(entity) -> bool:
    """Towed guns, tripod MGs, mortars, AT rifles: artillery-class entities.

    The entity kind comes from its targetclass when present ("cannon_bullet"
    for the .50 tripod), so the cannon folders and props are checked too.
    """
    entity = entity or {}
    kind = str(entity.get("kind") or "").casefold()
    path = str(entity.get("definition_path") or "").casefold()
    props = {str(value).casefold() for value in entity.get("props") or []}
    return (kind == "artillery" or kind.startswith("cannon") or any(token in path for token in ("/cannon/", "/cannons/", "/artillery/"))
            or bool(props & {"cannon", "carried", "-carried", "cannon_small", "mg_stan"}))


def donor_seat(resources, model, model_path, mount, carried):
    """Height that must rest on the chassis: a turret's own roof line, or the
    base of a carried weapon (which has no hull roof of its own)."""
    if carried:
        return module_floor(resources, model, model_path, mount)
    return mount_seat(resources, model, model_path, mount)


def seat_offset(resources, recipient_model, recipient_path, recipient_mount,
                donor_model, donor_path, donor_mount):
    """Z translation placing the donor module on the recipient ring, or 0."""
    recipient_seat = mount_seat(resources, recipient_model, recipient_path, recipient_mount)
    donor_seat = mount_seat(resources, donor_model, donor_path, donor_mount)
    if recipient_seat is None or donor_seat is None:
        return 0.0
    return round(recipient_seat - donor_seat, 3)


def inventory_weapon_meshes(resources, template_id, model):
    """Render native inventory-mounted actors; their mesh is not in the vehicle MDL.

    This reads the definition's initial inventory, not a soldier or a campaign
    inventory. It never creates a fixed Weaponry weapon or changes ammunition.
    """
    from .compiler import _load_entity
    from .registration import _stuff_definition
    _,_,root=_load_entity(resources,template_id)
    slots={};items={};meshes=[];warnings=[]
    for inv in root.find_all('extender'):
        if inv.args!=['inventory']:continue
        for slot in inv.find_all('weapon'):
            if slot.args and slot.find('mask') and 'mountable' in slot.find('mask').args:slots[slot.args[0]]=True
        for item in inv.walk('item'):
            for user in item.find_all('user'):
                if user.args and item.args:items[user.args[0]]=item.args[0]
    bones={name.casefold():name for name in model['bones']}
    for weaponry in root.find_all('Weaponry'):
        for place in weaponry.find_all('place'):
            if not place.args or place.args[0] not in slots or place.find('weapon'):continue
            slot=place.args[0];link=place.find('LinkBone');item=items.get(slot)
            if not item or not link or not link.args:continue
            try:
                host=bones[link.args[0].casefold()]
                if not model['bones'][host].get('visible', True):continue
                def actor(path,seen=()):
                    if path in seen or len(seen)>24:raise GeometryError('Mounted weapon inheritance cycle')
                    doc=parse(resources.read(path));entity=None
                    parent=doc.find('from')
                    if parent and parent.args and not parent.find('entity'):
                        name=parent.args[0];pattern=name.startswith('pattern ')
                        entity=actor(_stuff_definition(resources,name.removeprefix('pattern '),path,pattern),(*seen,path))
                    for node in doc.walk('entity'):
                        if node.args:entity=node.args[0]
                    return entity
                entity=actor(_stuff_definition(resources,item))
                if not entity:raise GeometryError('Mounted weapon has no resolved actor')
                data=build_preview(resources,entity,'full')
                actor_model=parse_model(resources.read(data['model_path']))
                frame=attached_actor_frame(actor_model,model['bones'][host]['world'])
                if not data['meshes'] or any(d.get('code')=='mesh_unavailable' for d in data['diagnostics']):
                    raise GeometryError('Mounted weapon geometry is incomplete')
                for mesh in data['meshes']:
                    meshes.append({**mesh,'bone':host,'inventory_slot':slot,'attached_weapon':True,
                        'positions':[v for i in range(0,len(mesh['positions']),3) for v in point(frame,mesh['positions'][i:i+3])],
                        'normals':[]})
            except (ValueError,KeyError,OSError) as error:
                warnings.append({'code':'mounted_weapon_unavailable','message':str(error),'slot':slot})
    return meshes,warnings


def build_preview(resources, template_id, part="full", mount_name=None, seats=None, *, include_inventory=False) -> dict:
    """seats: [{"place","group","bone"}] (compiler.seat_markers) placed like the meshes."""
    if part not in ("full", "hull", "turret"):
        raise GeometryError("Preview part must be full, hull or turret")
    entity = resources.resolve_template(template_id)
    path = entity["model_path"]
    if not path:
        raise GeometryError(f"No resolved model for {template_id}")
    model = parse_model(resources.read(path))
    carried = is_carried_weapon(entity)
    if part == 'hull':
        mount_name = recipient_mount_name(model, mount_name)
    free_spot = free_mount_spec(mount_name)
    free = part == "hull" and (free_spot[0] or (not mount_name and not model["anchor_name"]))
    options = free_mount_options(model) if part == "hull" else []
    if mount_name and not free_spot[0]:
        if mount_name not in model["bones"]:
            raise GeometryError(f"Mount bone {mount_name!r} does not exist")
        model["anchor_name"] = mount_name
        model["anchor"] = point(model["bones"][mount_name]["world"], [0, 0, 0])
    elif part == "turret" and donor_mount_name(model) != model["anchor_name"]:
        # A weapon without its own pivot (Solothurn), or whose pivot carries
        # nothing visible (MACE T91), moves as a whole model.
        model["anchor_name"] = whole_model_mount(model)
        model["anchor"] = point(model["bones"][model["anchor_name"]]["world"], [0, 0, 0]) if model["anchor_name"] else [0, 0, 0]
        carried = True
    if free:
        # No ring on this chassis (or a free mount chosen): nothing is removed
        # and the module rests on the deck point the compiler also uses.
        spot = free_spot[1]
        model["anchor"] = (point(model["bones"][spot]["world"], [0, 0, 0]) if spot in model["bones"]
                           else deck_point(resources, model, path, free_mount_parent(model)))
        model["anchor_name"] = mount_name if free_spot[0] else FREE_MOUNT
    selected = descendants(model["bones"], model["anchor_name"]) if model["anchor_name"] in model["bones"] else set()
    meshes, warnings = [], []
    adjustment = translation([-v for v in model["anchor"]]) if part == "turret" else identity()
    for name, bone in model["bones"].items():
        if (part == "turret" and name not in selected) or (part == "hull" and name in selected):
            continue
        if not bone["visible"]:
            continue
        matrix = multiply(adjustment, bone["world"])
        for reference in bone["assets"]:
            virtual = resolve_path(path, reference)
            try:
                mesh = read_mesh(resources.read(virtual))
            except (OSError, KeyError, ValueError) as error:
                warnings.append({"code": "mesh_unavailable", "path": virtual, "message": str(error)})
                continue
            transformed, normal_values = [], []
            for i in range(0, len(mesh["positions"]), 3):
                transformed.extend(point(matrix, mesh["positions"][i:i+3]))
            normal_matrix = inverse(matrix)
            for i in range(0, len(mesh["normals"]), 3):
                n = mesh["normals"][i:i+3]
                moved = [sum(normal_matrix[c][r]*n[c] for c in range(3)) for r in range(3)]
                scale = math.sqrt(sum(x*x for x in moved)) or 1
                normal_values.extend(x/scale for x in moved)
            for submesh in mesh["submeshes"]:
                submesh["mtl"] = resolve_path(virtual, submesh["material"]) if submesh["material"] else None
            meshes.append({"name": name, "bone": name, "path": virtual, "positions": transformed,
                           "normals": normal_values, "uv": mesh["uv"], "indices": mesh["indices"],
                           "color": "#d2a150" if name in selected else "#7696a8", "submeshes": mesh["submeshes"]})
    module_roles = None
    if part == "turret" and seats:
        # Match the compiler's selected module, not every role in the donor.
        # A commander's native seat can belong to the carriage being discarded.
        from .compiler import _load_entity, _explicit_weapon_operators, _selected_places, _place_roles
        _, _, crew_root = _load_entity(resources, template_id)
        _explicit_weapon_operators(resources, crew_root)
        module_places, _ = _selected_places(crew_root, model, selected)
        module_roles = set().union(set(), *(_place_roles(p, implicit_gunner=True) for p in module_places))
        module_roles |= selected & {"gunner", "commander", "charger"}
        module_roles.discard("driver")
    seat_points, internal = [], 0
    for seat in seats or []:
        if module_roles is not None:
            seat = {**seat, "weapon": seat["place"] in module_roles}
        bone = model["bones"].get(seat["bone"]) if seat["bone"] else None
        if bone is None:
            internal += seat["group"] == "crew"  # tank crews sit inside, without a seat bone
            continue
        # A weapon's crew travels with the module even when its seat bone hangs
        # elsewhere in the source model (a Pak's loaders stand beside the gun).
        in_module = seat["bone"] in selected or (seat.get("weapon") and part == "turret")
        if (part == "turret" and not in_module) or (part == "hull" and seat["bone"] in selected):
            continue
        moving=False;cursor=seat['bone']
        while cursor in model['bones'] and model['bones'][cursor]['parent']:
            current=model['bones'][cursor]
            moving |= 'revolute' in current['node'].args[:-1]
            cursor=current['parent']
        seat={**seat,'fixed':not moving}
        world = multiply(adjustment, bone["world"])
        seat_points.append({**seat, "position": point(world, [0, 0, 0]),
                            "rotation": [[round(world[r][c], 6) for c in range(3)] for r in range(3)]})
    if include_inventory:
        attached,errors=inventory_weapon_meshes(resources,template_id,model)
        for mesh in attached:
            host=mesh['bone']
            if (part=='turret' and host not in selected) or (part=='hull' and host in selected):continue
            mesh['color']='#d2a150' if host in selected else '#7696a8'
            mesh['positions']=[v for i in range(0,len(mesh['positions']),3) for v in point(adjustment,mesh['positions'][i:i+3])]
            meshes.append(mesh)
        warnings.extend(errors)
    positions = [value for mesh in meshes for value in mesh["positions"]]
    if part == "turret" and not selected:
        warnings.append({"code": "no_mount", "message": "No recognized revolute weapon mounting root"})
    if part == "full":
        seat = None
    elif free:
        seat = 0.0
    elif part == "turret":
        seat = donor_seat(resources, model, path, model["anchor_name"], carried)
    else:
        seat = mount_seat(resources, model, path, model["anchor_name"])
    joints = []
    try:
        from .compiler import _load_entity, _explicit_weapon_operators, _articulation_ancestors, _include_defines
        from .articulation import weapon_joints
        _, _, root = _load_entity(resources, template_id)
        _explicit_weapon_operators(resources, root)
        visible = set(model['bones']) - selected if part == 'hull' else selected if part == 'turret' else set(model['bones'])
        if part == 'turret' and carried:
            visible |= set(_articulation_ancestors(root, model, model['anchor_name']))
        # Traverse macros ("turret_heavy") come from the vehicle's includes.
        defines = {k.casefold(): v for k, v in _include_defines(resources, root).items()}
        joints = weapon_joints(model, root, adjustment, visible, defines=defines)
    except (ValueError, KeyError, OSError) as error:
        warnings.append({'code': 'joints_unavailable', 'message': str(error)})
    # Armour volumes for View > Armour: the same parts and space as the meshes.
    # A view aid only: it never blocks the preview.
    armor = []
    try:
        from .armor_view import armor_volumes
        shown = (lambda bone: bone in selected) if part == "turret" else (lambda bone: bone not in selected) if part == "hull" else (lambda bone: True)
        armor = armor_volumes(resources, root, model, path, shown, adjustment)
    except Exception as error:
        warnings.append({'code': 'armor_unavailable', 'message': str(error)})
    return {"template_id": template_id, "part": part, "model_path": path, "meshes": meshes, "joints": joints,
            "anchor": model["anchor"], "anchor_name": model["anchor_name"], "mounts": (replaceable_mounts(model) if part == 'hull' else model["mounts"]) + options,
            "seat": seat, "carried": bool(part == "turret" and carried),
            "seats": seat_points, "internal_crew": internal,
            "bounds": bounds_of(positions), "diagnostics": warnings,
            "armor": armor,
            "bones": [{"name": name, "parent": bone["parent"], "position": point(bone["world"], [0, 0, 0])}
                      for name, bone in model["bones"].items()],
            "stats": {"meshes": len(meshes), "vertices": len(positions)//3,
                      "triangles": sum(len(mesh["indices"])//3 for mesh in meshes)}}
