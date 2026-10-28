"""Extra MGs manned by a passenger standing on the vehicle.

The game's own deck MGs (the .50 cal of the Stuart, the M4A3 Sherman, the
M10/Wolverine/Achilles and the M26) are a mount on two turning bones, a
passenger place hung from the turning one, so the soldier turns with the gun,
and a {Weaponry} place whose gunner is that passenger. Whoever climbs on, a
soldier or a crewman, fires it; the crew keeps its own duties.

Everything is read from the stock game only (mounts, poses, weapons), so a
built vehicle never depends on a mod staying active. Names are fixed by the
mount's number: "Update vehicles" rebuilds the same places a saved campaign
may hold soldiers in.
"""
from __future__ import annotations

import math
import posixpath

from .pivots import pose_matrix, pivot_of, rebase_joint
from .syntax import block, quote
from .geometry import (parse_model, descendants, multiply, inverse, point, build_preview, attached_actor_frame)

# Climbing on and off any vehicle; a pose's own clip is kept only when it is one
# of these (a boat's or an aircraft's would look wrong on a tank).
BOARDING = ('board_HighArmor', 'board_MiddleArmor', 'board_lowarmor', 'board_body', 'board_tank_left', 'board_tank_right')
LEAVING = ('emit_tank_1', 'emit_tank_1_hold', 'emit_tank_2', 'emit_tank_2_hold', 'emit_body')


def _error(message):
    from .compiler import CompileError
    raise CompileError(message)


def _limits(node):
    limits = node.find('limits')
    try:
        return [float(limits.args[0]), float(limits.args[1])] if limits is not None and len(limits.args) >= 2 else None
    except ValueError:
        return None


def _machine_gun(root, place, guns):
    """A place firing a stock mountable MG, or an empty mountable slot (Stuart)."""
    weapons = [w.args[0].casefold() for w in place.find_all('weapon') if w.args]
    if weapons:
        return all(w in guns for w in weapons)
    return any(w.args == place.args and any(m.args == ['mountable'] for m in w.find_all('mask'))
               for e in root.find_all('extender') if e.args == ['inventory'] for w in e.find_all('weapon'))


def _discover(index):
    """Stock MG mounts and every MG operator pose, in one pass.

    Mounts (their meshes are copied) come only from the stock game without
    DLC. Poses are the shared human animations of the stock registry; DLC
    vehicles may name them too, since an animation is no DLC content.
    """
    from .commander_mg import _stock, _blocked
    from .compiler import _load_entity, _crew_expansion, _expand_weapon_seats, _append_inherited_crew
    from .geometry import is_carried_weapon
    from .crew_model import _pose_registry
    base = _stock(index)
    blocked = _blocked(base)
    registry = {name.casefold(): name for name in _pose_registry(base)}
    guns = {w['entity'].casefold() for w in weapon_catalog(index)}
    from .commander_mg import support_catalog as commander_supports
    hatch = {s['id'] for s in commander_supports(index)}
    supports, poses, seen = [], {}, set()
    for key in sorted(index.entries):
        if not key.startswith('entity/-vehicle/') or not key.endswith('.def') or '/x/' in key or '/xx/' in key:
            continue
        if index.resolve(key).source_id != 'vanilla':
            continue
        template = posixpath.basename(key)[:-4]
        stock = template not in blocked and base.exists(key)
        source = base if stock else index
        try:
            info, _, root = _load_entity(source, template)
            carried = is_carried_weapon(info)
            if carried:
                _append_inherited_crew(source, root)
            _crew_expansion(source, root)
            weapon_places = [p for w in root.find_all('Weaponry') for p in w.find_all('place')
                             if p.args and p.find('gunner') is not None and len(p.find('gunner').args) == 1
                             and _machine_gun(root, p, guns)]
            if not weapon_places:
                continue
            operators = {p.find('gunner').args[0] for p in weapon_places}
            _expand_weapon_seats(source, root, operators)
            declared = {}
            for placer in root.find_all('placer'):
                for place in placer.find_all('place'):
                    if place.args:
                        values = declared.setdefault(place.args[0], {})
                        for node in place.children:
                            if node.head.casefold() in ('group', 'linkbone') and node.args:
                                values[node.head.casefold()] = node.args[0]
            boarders = list(root.find_all('boarder'))
            clips = {a.args[0]: a for b in boarders for a in b.find_all('anm') if a.args}
            model = parse_model(source.read(info['model_path']))
            bones = model['bones']
            for place in weapon_places:
                operator = place.find('gunner').args[0]
                anm = next((clips.get(link.find('anm').args[0]) for b in boarders for link in b.find_all('link')
                            if len(link.args) >= 2 and link.args[1] == operator and link.find('anm') is not None
                            and link.find('anm').args), None) or clips.get(operator)
                forward = anm.find('forward') if anm is not None else None
                end = forward.find('end') if forward is not None else None
                if end is None or not end.args or end.args[0].casefold() not in registry:
                    continue
                begin = forward.find('begin')
                reverse = anm.find('reverse')
                reverse_end = reverse.find('end') if reverse is not None else None
                pose = registry[end.args[0].casefold()]
                climb = next((c for c in BOARDING if begin is not None and begin.args and begin.args[0].casefold() == c.casefold()), BOARDING[0])
                leave = next((c for c in LEAVING if reverse_end is not None and reverse_end.args
                              and reverse_end.args[0].casefold() == c.casefold()), LEAVING[0])
                poses.setdefault(pose.casefold(), {'id': pose, 'label': pose, 'source': source.label_for(template),
                                                   'begin': climb, 'reverse': leave})
                seat = declared.get(operator, {})
                link = place.find('LinkBone') or place.find('linkbone')
                if not stock or link is None or not link.args or not seat.get('linkbone'):
                    continue
                ancestors, current = [], link.args[0]
                while current in bones:
                    if 'revolute' in bones[current]['node'].args[:-1]:
                        ancestors.append(current)
                    current = bones[current]['parent']
                if len(ancestors) < 2:
                    continue
                pitch, yaw = ancestors[:2]
                # A tripod's legs hang from its base, not from the turning
                # bone: a carried MG travels whole, a vehicle mount from its yaw.
                top = yaw
                while carried and bones[top]['parent'] is not None:
                    top = bones[top]['parent']
                selected = descendants(bones, top)
                # A commander's hatch MG rests folded and unfolds with the hatch.
                # A vehicle's main turret is no MG mount, even with an MG in it.
                if (seat['linkbone'] not in bones or template + ':' + place.args[0] in hatch
                        or (not carried and yaw.casefold() == 'turret')
                        or len(selected) > (80 if carried else 16)):
                    continue
                # The operator turns with the gun when he hangs from it (Stuart,
                # Sherman); otherwise he keeps his spot and only the gun turns.
                turns = seat['linkbone'] in descendants(bones, yaw)
                identity = (bones[top]['node'].render(), pose.casefold())
                if identity in seen:
                    continue
                seen.add(identity)
                supports.append({'id': template + ':' + place.args[0],
                                 'label': base.label_for(template) + ('' if carried else ' · ' + place.args[0]),
                                 'template': template, 'model_path': info['model_path'], 'root': top, 'yaw': yaw, 'pitch': pitch,
                                 'link': link.args[0], 'seat': seat['linkbone'], 'pose': pose, 'turns': turns,
                                 'seat_frame': multiply(inverse(bones[top]['world']), bones[seat['linkbone']]['world']),
                                 'yaw_limits': _limits(bones[yaw]['node']) or [-180, 180],
                                 'pitch_limits': _limits(bones[pitch]['node']) or [-10, 60],
                                 'bones': sorted(selected)})
        except (ValueError, KeyError, OSError):
            continue
    return supports, sorted(poses.values(), key=lambda p: p['id'].casefold())


def catalog(index):
    if not hasattr(index, '_rider_catalog'):
        supports, poses = _discover(index)
        index._rider_catalog = {'supports': supports, 'poses': poses}
    return index._rider_catalog


def support_catalog(index):
    return catalog(index)['supports']


def pose_catalog(index):
    return catalog(index)['poses']


def weapon_catalog(index):
    from .commander_mg import weapon_catalog as mountable
    return [w for w in mountable(index) if 'mgun' in w['tags']]


def _free(name, taken):
    from .compiler import _unique_name
    return name if name not in taken else _unique_name(name, taken)


def _vector(value, count, bound):
    if (not isinstance(value, (list, tuple)) or len(value) != count
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > bound
                   for v in value)):
        _error('La MG de pasajero necesita posición, orientación y límites válidos.')
    return [float(v) for v in value]


def configuration(index, rows):
    """[(support, pose, weapon, row)] for the recipe's rows, every value checked."""
    if rows is None:
        return []
    if not isinstance(rows, list):
        _error('La MG de pasajero no es válida.')
    supports = {s['id']: s for s in support_catalog(index)}
    poses = {p['id']: p for p in pose_catalog(index)}
    weapons = {w['id']: w for w in weapon_catalog(index)}
    result = []
    for row in rows:
        if not isinstance(row, dict):
            _error('La MG de pasajero no es válida.')
        support, pose, weapon = supports.get(row.get('support')), poses.get(row.get('pose')), weapons.get(row.get('item'))
        if support is None:
            _error('Elegí un soporte nativo disponible para la MG de pasajero.')
        if pose is None:
            _error('Elegí una postura del operador disponible.')
        if weapon is None:
            _error('Elegí una ametralladora disponible. Para armas DLC, abrí el juego con el DLC adquirido e instalado y volvé a leer los recursos.')
        parent = row.get('parent')
        if not isinstance(parent, str) or not parent or len(parent) > 160:
            _error('Elegí el hueso donde se fija la MG de pasajero.')
        yaw, pitch = _vector(row.get('yaw'), 2, 180), _vector(row.get('pitch'), 2, 90)
        if yaw[0] >= yaw[1] or pitch[0] >= pitch[1]:
            _error('El límite mínimo debe ser menor que el máximo.')
        normalized = {'support': support['id'], 'pose': pose['id'], 'item': weapon['id'], 'parent': parent,
                      'position': _vector(row.get('position'), 3, 10000), 'rotation': _vector(row.get('rotation'), 3, 360),
                      'yaw': yaw, 'pitch': pitch, 'seat': _vector(row.get('seat', [0, 0, 0]), 3, 200)}
        if 'pivot' in row:
            normalized['pivot'] = pivot_of(row)
        result.append((support, pose, weapon, normalized))
    return result


def _seat_frame(support, offset):
    """The operator's spot, in the mount's frame, moved by the player's offset."""
    local = [list(r) for r in support['seat_frame']]
    for axis in range(3):
        local[axis][3] += offset[axis]
    return local


def preview(index, body):
    """Mount, weapon and operator of one row, in the mount's own frame."""
    from .commander_mg import _stock
    support, pose, weapon, row = configuration(index, [body])[0]
    base = _stock(index)
    model = parse_model(base.read(support['model_path']))
    pivot = model['bones'][support['root']]['world']
    meshes = build_preview(base, support['template'], 'turret', support['root'])['meshes']
    pitched = descendants(model['bones'], support['pitch'])
    for mesh in meshes:
        mesh['rig_joint'] = 'pitch' if mesh.get('bone') in pitched else 'yaw'
    linear = inverse([r[:3] + [0] for r in pivot[:3]] + [[0, 0, 0, 1]])
    for mesh in meshes:
        mesh['positions'] = [v for i in range(0, len(mesh['positions']), 3) for v in point(linear, mesh['positions'][i:i+3])]
        mesh['normals'] = []
    weapon_source = index.mountable_game()
    gun = build_preview(weapon_source, weapon['entity'], 'full')
    attachment = attached_actor_frame(parse_model(weapon_source.read(gun['model_path'])),
                                      multiply(inverse(pivot), model['bones'][support['link']]['world']))
    for mesh in gun['meshes']:
        mesh['positions'] = [v for i in range(0, len(mesh['positions']), 3) for v in point(attachment, mesh['positions'][i:i+3])]
        mesh['normals'] = []
        mesh['rig_joint'] = 'pitch'
        meshes.append(mesh)
    seat = _seat_frame(support, row['seat'])
    return {'meshes': meshes, 'pitch_matrix': multiply(inverse(pivot), model['bones'][support['pitch']]['world']),
            'seats': [{'place': 'rider', 'group': 'passenger', 'bone': 'rider', 'position': point(seat, [0, 0, 0]),
                       'rotation': [r[:3] for r in seat[:3]], 'pose': pose['id']}],
            'yaw_limits': support['yaw_limits'], 'pitch_limits': support['pitch_limits'],
            'diagnostics': [], 'mounts': [], 'internal_crew': 0}


def apply(index, root, model, rows, assets, diagnostics):
    """Add every passenger MG to the final assembly; None when there are none."""
    from .compiler import _Assets, _set_matrix, _rename_bone_refs
    from .commander_mg import _stock
    rows = configuration(index, rows)
    if not rows:
        return None
    base = _stock(index)
    final = parse_model(model['document'].render())
    doors = list(dict.fromkeys(d.args[0] for b in root.find_all('boarder') for d in b.find_all('door')
                               if d.args and d.args[0] in final['bones']))
    if not doors:
        _error('El vehículo no tiene puertas para que alguien suba a la MG de pasajero.')
    boarder = root.find('boarder')
    if boarder is None:
        boarder = block('{boarder}')
        root.append(boarder)
    inventory = next((n for n in root.find_all('extender') if n.args == ['inventory']), None)
    if inventory is None:
        inventory = block('{extender "inventory" {box}}')
        root.append(inventory)
    box = inventory.find('box')
    if box is None:
        box = block('{box}')
        inventory.append(box)
    weaponry = root.find('Weaponry')
    if weaponry is None:
        weaponry = block('{Weaponry}')
        root.append(weaponry)
    from .compiler import _include_defines
    if 'abm_mgun' not in _include_defines(index, root):
        root.append(block('(include "/properties/abm.inc")'))
    mounts = []
    for number, (support, pose, weapon, row) in enumerate(rows, 1):
        if row['parent'] not in final['bones']:
            _error('El hueso elegido para la MG de pasajero no existe en el ensamblaje final.')
        native = parse_model(base.read(support['model_path']))
        taken = set(parse_model(model['document'].render())['bones'])
        mapping = {}
        for name in support['bones']:
            mapping[name] = _free(f'rider{number}_{name}', taken | set(mapping.values()))
        copied = native['bones'][support['root']]['node'].clone()
        _rename_bone_refs(copied, mapping)
        _set_matrix(copied, pose_matrix(row))
        for name, limits in ((support['yaw'], row['yaw']), (support['pitch'], row['pitch'])):
            bone = next(n for n in copied.walk('bone') if n.args[-1] == mapping[name])
            for old in list(bone.find_all('limits')):
                bone.remove(old)
            bone.append(block('{limits %s %s}' % tuple(limits)))
        if support['turns'] and any(row['seat']):
            seat = next(n for n in copied.walk('bone') if n.args[-1] == mapping[support['seat']])
            parent = native['bones'][support['seat']]['parent']
            local = multiply(inverse(native['bones'][parent]['world']), native['bones'][support['seat']]['world'])
            # The offset is given in the mount's frame; the seat may hang from a sub-bone.
            frame = multiply(inverse(native['bones'][support['root']]['world']), native['bones'][parent]['world'])
            shift = point(inverse([r[:3] + [0] for r in frame[:3]] + [[0, 0, 0, 1]]), row['seat'])
            for axis in range(3):
                local[axis][3] += shift[axis]
            _set_matrix(seat, local)
        host = next(n for n in model['document'].walk('bone') if n.args[-1] == row['parent'])
        host.append(copied)
        if support['turns']:
            seat_bone = mapping[support['seat']]
        else:
            # A fixed operator spot beside the mount, where the native one stood.
            seat_bone = _free(f'rider{number}_seat', taken | set(mapping.values()))
            spot = block('{bone %s}' % quote(seat_bone))
            frame = [list(r) for r in support['seat_frame']]
            for axis in range(3):
                frame[axis][3] += row['seat'][axis]
            _set_matrix(spot, multiply(pose_matrix(row), frame))
            host.append(spot)
        if any(pivot_of(row)):
            frame = pose_matrix(row)
            rebase_joint(model['document'], mapping[support['root']], point(inverse(frame), point(frame, pivot_of(row))))
        merged = parse_model(model['document'].render())
        extra = _Assets(base, assets.target, assets.sources, diagnostics)
        extra.context(f'rd{number}', native, merged, mapping, exclude=set(native['bones']) - set(support['bones']))
        extra.rewrite(copied, support['model_path'], f'rd{number}')
        assets.files.update(extra.files)
        assets.missing_materials.update(extra.missing_materials)
        assets.missing_bones.update(extra.missing_bones)
        place, slot = f'mw_rider{number}', f'mgun_rider{number}'
        visor, clip = f'mw_rider{number}_top', f'mw_rider{number}'
        root.append(block('{placer {place %s {group "passenger"} {linkbone %s} {visor %s}}}'
                          % (quote(place), quote(seat_bone), quote(visor))))
        root.append(block('{sensor {visor %s {vision "human"} {bone %s}}}' % (quote(visor), quote(mapping[support['link']]))))
        boarder.append(block('{anm %s {forward {begin %s} {end %s}} {reverse {end %s}}}'
                             % (quote(clip), quote(pose['begin']), quote(pose['id']), quote(pose['reverse']))))
        for door in doors[:2]:
            boarder.append(block('{link %s %s {anm %s} {forward putoff} {reverse puton}}' % (quote(door), quote(place), quote(clip))))
        inventory.append(block('{weapon %s {mask "mountable"}}' % quote(slot)))
        initial = block('{item %s filled {user %s}}' % (quote(weapon['id']), quote(slot)))
        ammo = block('{item %s %s}' % (quote(weapon['ammo_item']), weapon['ammo_rounds']))
        box.append(initial)
        box.append(ammo)
        # The game's own passenger MG place (Stuart, M10), whatever the mount
        # came from: never the vehicle's main weapon, and no towed gun's
        # {Rotate}, which would turn the whole vehicle towards a target.
        mount = block('{place %s {type "aa"} {gunner %s} {charger %s} {LinkBone %s} {LinkAnimation "off"} '
                      '("abm_mgun") {basic} {ai_in_manual}}'
                      % (quote(slot), quote(place), quote(place), quote(mapping[support['link']])))
        weaponry.append(mount)
        for name, speed in ((support['yaw'], 180), (support['pitch'], 90)):
            root.append(block('{bone %s {speed2 %s}}' % (quote(mapping[name]), speed)))
        mounts.append({'place': place, 'slot': slot, 'item': weapon['id'], 'support': support['id'], 'pose': pose['id'],
                       'parent': row['parent'], 'bones': {**mapping, '@seat': seat_bone}, 'root': mapping[support['root']],
                       'initial_item': initial.render(), 'ammo_item': ammo.render()})
    diagnostics.append({'code': 'rider_mg_runtime_gate', 'level': 'warning',
                        'message': 'MG de pasajero: la usa quien suba al vehículo. Postura e IA pendientes de prueba en batalla.'})
    return {'mounts': mounts, 'runtime_verified': False}
