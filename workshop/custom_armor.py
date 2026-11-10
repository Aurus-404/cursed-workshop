"""A plate of the player's own size and thickness, placed like any armor fitting.

The mesh and its worn-steel paint are ours; protection is the game's own: a box volume
with a thickness and the native steel material, tagged as armor so a
penetration never reaches the hull behind it. The plate is as deep as it is
thick, so what is seen is what protects.
"""
import hashlib
import math
import struct

from .syntax import block, quote

ASSET = 'custom:plate'
UNIT = 50.0  # millimetres per model unit (native vehicles: about 20 units per metre)
PAINT = 'cw_plate_steel'
TILE = 1000/UNIT  # model units the paint covers before repeating
LIMITS = {'width': (50, 8000), 'height': (50, 8000), 'thickness': (1, 500)}  # mm
LABEL = 'Placa a medida'


def plate(row, error=ValueError):
    """The validated {'width','height','thickness'} of a row, in millimetres."""
    value = row.get('plate') if isinstance(row, dict) else None
    if not isinstance(value, dict) or set(value) != set(LIMITS):
        raise error('La placa a medida necesita ancho, alto y grosor.')
    for key, (low, high) in LIMITS.items():
        v = value[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
            raise error('El tamaño o el grosor de la placa no es válido.')
    return {key: round(float(value[key]), 1) for key in LIMITS}


def sizes(value):
    """Box sizes in model units: thickness along local X (its front and rear
    faces), width along Y, height along Z; centred on its bone."""
    return [value['thickness']/UNIT, value['width']/UNIT, value['height']/UNIT]


def mesh_name(value, flipped=False):
    digest = hashlib.sha1(repr(sorted(value.items())).encode()).hexdigest()[:10]
    return 'cw_plate_'+digest+('_f' if flipped else '')+'.ply'


def mesh_bytes(value):
    """Six flat faces in the stride-48 layout of shipped meshes (position,
    normal, uv, tangent + handedness), clockwise front faces as the game expects."""
    hx, hy, hz = (s/2 for s in sizes(value))
    corners = [[hx if i & 1 else -hx, hy if i & 2 else -hy, hz if i & 4 else -hz] for i in range(8)]
    vertices, indices = [], []
    for face, normal in (((0, 2, 3, 1), (0, 0, -1)), ((4, 5, 7, 6), (0, 0, 1)), ((0, 1, 5, 4), (0, -1, 0)),
                         ((2, 6, 7, 3), (0, 1, 0)), ((0, 4, 6, 2), (-1, 0, 0)), ((1, 3, 7, 5), (1, 0, 0))):
        tangent = [0.0, 1.0, 0.0] if normal[0] else [1.0, 0.0, 0.0]
        base = len(vertices)
        for i, corner in enumerate(face):
            # The paint repeats once per metre, whatever the plate's size.
            u, v = [corners[corner][axis]/TILE for axis in range(3) if not normal[axis]]
            vertices.append([*corners[corner], *map(float, normal), u, v, *tangent, 1.0])
        indices.extend((base, base+2, base+1, base, base+3, base+2))
    bounds = [-hx, -hy, -hz, hx, hy, hz]
    mtl = (PAINT+'.mtl').encode('ascii')
    return (b'EPLYBNDS'+struct.pack('<6f', *bounds)+b'MESH'+struct.pack('<5I', 0x112, 0, len(indices)//3, 0x704, 0x19e6e6e6)
            + bytes([len(mtl)])+mtl+b'VERT'+struct.pack('<IHH', len(vertices), 48, 7)
            + b''.join(struct.pack('<12f', *v) for v in vertices)+b'INDX'+struct.pack('<I', len(indices))
            + struct.pack('<'+str(len(indices))+'H', *indices))


def paint_files():
    from .plate_paint import texture_bytes
    return {PAINT+'.dds': texture_bytes(),
            PAINT+'.mtl': ('{material bump {diffuse "'+PAINT+'.dds"}{color "230 230 230 25"}{blend none}}').encode()}


def preview():
    """What the editor needs to draw a plate as the game will: its paint and how far it reaches."""
    from .plate_paint import texture_bytes
    from .textures import decode_dds
    width, height, pixels = decode_dds(texture_bytes())
    return {'texture': {'width': width, 'height': height, 'rgba': list(pixels)}, 'tile': TILE,
            'limits': {key: list(value) for key, value in LIMITS.items()}, 'preview_valid': True, 'runtime_verified': False}


def contract(value):
    thickness = value['thickness']
    text = str(int(thickness)) if thickness == int(thickness) else str(thickness)
    return '{volume "source" {tags "armor"}{thickness '+text+'}{material "steel"}{able {NoWoundDecal}}}'


def item(value):
    """The catalog-shaped description every placed fitting carries."""
    return {'id': ASSET, 'custom': True, 'label': LABEL, 'kind': 'plates', 'plate': value,
            'materials': ['steel'], 'thickness': [value['thickness']], 'cast_steel': False}


def attach(root, final, host, prefix, pose, value, files, flipped, set_matrix):
    """Add the plate's bone, mesh, box volume and armor contract; return its names."""
    name = mesh_name(value, flipped)
    from .geometry import reverse_mesh_winding
    raw = mesh_bytes(value)
    raw = reverse_mesh_winding(raw) if flipped else raw
    for path, data in {name: raw, **paint_files()}.items():
        if files.get(path, data) != data:
            raise ValueError('Los recursos de la placa chocan con archivos existentes.')
        files[path] = data
    bone = block('{bone '+quote(prefix+'root')+' {VolumeView '+quote(name)+'}}')
    set_matrix(bone, pose)
    host.append(bone)
    volume = prefix+'v0'
    final['document'].append(block('{volume '+quote(volume)+' {box '+' '.join('%.4f' % s for s in sizes(value))+'} {bone '+quote(prefix+'root')+'}}'))
    behavior = block(contract(value)); behavior.atoms[1].set(volume); root.append(behavior)
    return [prefix+'root'], {'plate': volume}
