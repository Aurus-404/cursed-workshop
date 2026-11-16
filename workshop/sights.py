"""Invisible native third-person aiming sockets, attached to real model bones."""
import copy
import hashlib
import json
import math
from pathlib import Path

from .geometry import parse_model
from .pivots import pose_matrix
from .syntax import block, bone_name, quote


def configuration(value):
    if not isinstance(value, dict) or not isinstance(value.get('parent'), str) or not value['parent'] or len(value['parent']) > 160:
        raise ValueError('Elegí la pieza que acompaña a la mira.')
    for field in ('position','rotation'):
        vector=value.get(field,[0,0,0])
        if not isinstance(vector,list) or len(vector)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>100000 for v in vector):
            raise ValueError('La posición y orientación de la mira no son válidas.')
    pose_matrix(value)
    return copy.deepcopy(value)


def apply(result, value):
    from .compiler import _set_matrix
    value = configuration(value)
    output = Path(result['output_dir'])
    path = output / 'resource' / result['model_path']
    model = parse_model(path.read_bytes())
    if value['parent'] not in model['bones']:
        raise ValueError('La pieza que acompaña a la mira ya no existe.')
    document = model['document']
    parent = next(n for n in document.find('skeleton').walk('bone') if bone_name(n) == value['parent'])
    serial = 0
    while 'cw_sight_' + str(serial) in model['bones']:
        serial += 1
    name = 'cw_sight_' + str(serial)
    socket = block('{bone ' + quote(name) + '}')
    _set_matrix(socket, pose_matrix(value))
    parent.append(socket)
    raw = document.render().encode('utf8')
    path.write_bytes(raw)
    metadata = result['metadata']
    metadata.setdefault('file_hashes', {})[path.name] = hashlib.sha256(raw).hexdigest()
    metadata['sight'] = dict(value, bone=name)
    metadata.setdefault('transform', {})['sight'] = value
    (output / f"assembly_{result['id']}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf8')
    return result


def camera_override(camera, sight):
    """Keep native zoom, lens and controls; only aiming levels use the new socket."""
    levels = camera.find_all('level')[1:]
    if not levels:
        raise ValueError('Este vehículo no tiene una cámara de apuntado nativa.')
    for level in levels:
        for node in list(level.children):
            if node.head.casefold() in ('linkbone', 'offset', 'faroffset', 'nearoffset', 'distance', 'distanceset', 'delta'):
                level.remove(node)
        for text in ('{linkBone '+quote(sight['bone'])+'}', '{offset 0 0 0}', '{distance 0}', '{distanceSet 0}', '{delta 0}'):
            level.append(block(text))
    return camera


def initial(resources, metadata, model):
    from .registration import _native_camera_profile, RegistrationError
    from .resources import ResourceError
    try:
        camera, _ = _native_camera_profile(resources, metadata.get('gun_camera_donor') or metadata.get('donor') or metadata.get('recipient', ''))
    except (ResourceError, RegistrationError, FileNotFoundError, KeyError):
        return None
    levels = camera.find_all('level')
    if len(levels) < 2:
        return None
    level = levels[1]
    link = level.find('linkBone')
    name = link.args[0] if link is not None and link.args else 'basis'
    mapping = {**metadata.get('bone_mapping', {}), **metadata.get('bone_map', {}), **metadata.get('camera_bone_mapping', {})}
    name = mapping.get(name, name)
    if name not in model['bones']:
        name = next(iter(model['bones']))
    offset = level.find('offset')
    position = [float(v) for v in offset.args] if offset is not None else [0, 0, 0]
    if len(position) != 3:
        position = [0, 0, 0]
    return {'parent': name, 'position': position, 'rotation': [0, 0, 0]}
