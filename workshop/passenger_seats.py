"""Passenger seats: add as many as the player wants, move or re-pose the vehicle's own.

A game passenger seat is ("seat_human" place(seat1) bone(seat01) door(emit3)
animation(armor1)): a "passenger" place hung from a bone, a door to climb on
through and a boarding clip that ends in the pose (properties/tank_crew.ext).
New seats are written the same way, on a bone of their own. Poses come from
the stock game's registry only, so a vehicle never needs a mod or a DLC to
load. Names are fixed by the seat's number: "Update vehicles" rebuilds the
same places a saved campaign may hold soldiers in.
"""
from __future__ import annotations

import math
import posixpath

from .pivots import pose_matrix, pivot_of
from .syntax import block, quote
from .geometry import parse_model, multiply, inverse

# Climbing on and off any vehicle; a pose's own clip is kept only when it is one
# of these (a boat's or an aircraft's would look wrong on a tank).
BOARDING = ('board_HighArmor', 'board_MiddleArmor', 'board_lowarmor', 'board_body', 'board_tank_left',
            'board_tank_right', 'board_bigcar')
LEAVING = ('emit_tank_1', 'emit_tank_1_hold', 'emit_tank_2', 'emit_tank_2_hold', 'emit_body')


def _error(message):
    from .compiler import CompileError
    raise CompileError(message)


def _discover(index):
    """Every pose a stock vehicle gives its passengers, with its boarding clips."""
    from .commander_mg import _stock
    from .compiler import _load_entity, _crew_expansion, _expand_place_macros
    from .crew_model import _pose_registry
    registry = {name.casefold(): name for name in _pose_registry(_stock(index))}
    poses = {}
    for key in sorted(index.entries):
        if not key.startswith('entity/-vehicle/') or not key.endswith('.def') or '/x/' in key or '/xx/' in key:
            continue
        if index.resolve(key).source_id != 'vanilla':
            continue
        template = posixpath.basename(key)[:-4]
        try:
            _, _, root = _load_entity(index, template)
            _crew_expansion(index, root)
            _expand_place_macros(index, root, lambda nodes, places: True)
        except (ValueError, KeyError, OSError):
            continue
        passengers = {p.args[0] for placer in root.find_all('placer') for p in placer.find_all('place')
                      if p.args and p.find('group') is not None and p.find('group').args == ['passenger']}
        clips = {a.args[0]: a for b in root.find_all('boarder') for a in b.find_all('anm') if a.args}
        for boarder in root.find_all('boarder'):
            for link in boarder.find_all('link'):
                reference = link.find('anm')
                if len(link.args) < 2 or link.args[1] not in passengers or reference is None or not reference.args:
                    continue
                anm = clips.get(reference.args[0])
                forward = anm.find('forward') if anm is not None else None
                end = forward.find('end') if forward is not None else None
                if end is None or not end.args or end.args[0].casefold() not in registry:
                    continue
                pose = registry[end.args[0].casefold()]
                if pose.casefold() in poses:
                    continue
                begin = forward.find('begin')
                reverse = anm.find('reverse')
                leave = reverse.find('end') if reverse is not None else None
                poses[pose.casefold()] = {
                    'id': pose, 'source': index.label_for(template),
                    'begin': next((c for c in BOARDING if begin is not None and begin.args
                                   and begin.args[0].casefold() == c.casefold()), BOARDING[0]),
                    'reverse': next((c for c in LEAVING if leave is not None and leave.args
                                     and leave.args[0].casefold() == c.casefold()), LEAVING[0])}
    return sorted(poses.values(), key=lambda p: p['id'].casefold())


def pose_catalog(index):
    if not hasattr(index, '_passenger_poses'):
        index._passenger_poses = _discover(index)
    return index._passenger_poses


def _vector(value, count, bound, message):
    if (not isinstance(value, (list, tuple)) or len(value) != count
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > bound
                   for v in value)):
        _error(message)
    return [float(v) for v in value]


def configuration(index, spec):
    """{'added': [...], 'native': {place: {...}}} with every value checked; None when empty."""
    if not spec:
        return None
    if not isinstance(spec, dict):
        _error('Los asientos de pasajeros no son válidos.')
    poses = {p['id']: p for p in pose_catalog(index)}
    added, native = [], {}
    rows = spec.get('added') or []
    if not isinstance(rows, list):
        _error('Los asientos de pasajeros no son válidos.')
    for row in rows:
        if not isinstance(row, dict) or row.get('pose') not in poses:
            _error('Elegí una pose de pasajero disponible.')
        parent = row.get('parent')
        if not isinstance(parent, str) or not parent or len(parent) > 160:
            _error('Elegí la parte del vehículo donde va el asiento.')
        item = {'pose': row['pose'], 'parent': parent,
                'position': _vector(row.get('position'), 3, 10000, 'La posición del asiento no es válida.'),
                'rotation': _vector(row.get('rotation'), 3, 360, 'La orientación del asiento no es válida.')}
        if 'pivot' in row:
            item['pivot'] = pivot_of(row)
        added.append(item)
    changes = spec.get('native') or {}
    if not isinstance(changes, dict):
        _error('Los asientos de pasajeros no son válidos.')
    for place, change in changes.items():
        if not isinstance(place, str) or not place or not isinstance(change, dict):
            _error('Los asientos de pasajeros no son válidos.')
        item = {}
        if change.get('pose') is not None:
            if change['pose'] not in poses:
                _error('Elegí una pose de pasajero disponible.')
            item['pose'] = change['pose']
        if change.get('offset') is not None:
            item['offset'] = _vector(change['offset'], 3, 10000, 'La posición del asiento no es válida.')
        if item:
            native[place] = item
    if not added and not native:
        return None
    return {'added': added, 'native': native}, poses


def _clip(name, pose):
    return block('{anm %s {forward {begin %s} {end %s}} {reverse {end %s}}}'
                 % (quote(name), quote(pose['begin']), quote(pose['id']), quote(pose['reverse'])))


def _free(name, taken):
    from .compiler import _unique_name
    return name if name not in taken else _unique_name(name, taken)


def apply(index, root, model, spec, assets, diagnostics):
    """Add and adjust passenger seats on the final assembly; None when there is nothing to do."""
    from .compiler import _set_matrix, _expand_place_macros
    configured = configuration(index, spec)
    if configured is None:
        return None
    spec, poses = configured
    final = parse_model(model['document'].render())
    boarder = root.find('boarder')
    if boarder is None:
        boarder = block('{boarder}')
        root.append(boarder)
    result = {'added': [], 'native': sorted(spec['native'])}
    if spec['native']:
        # A seat the vehicle already has: written out of its macro to change it.
        _expand_place_macros(index, root, lambda nodes, places: bool(places & set(spec['native'])))
        declared = {}
        for placer in root.find_all('placer'):
            for place in placer.find_all('place'):
                if place.args and place.args[0] in spec['native']:
                    group = place.find('group')
                    link = next((n for n in place.children if n.head.casefold() == 'linkbone' and n.args), None)
                    if group is not None and group.args == ['passenger'] and link is not None:
                        declared[place.args[0]] = link.args[0]
        for name, change in spec['native'].items():
            if name not in declared:
                _error(f'El vehículo ya no tiene el asiento {name}.')
            if 'pose' in change:
                clip = _free(f'mw_pose_{name}', {a.args[0] for b in root.find_all('boarder') for a in b.find_all('anm') if a.args})
                boarder.append(_clip(clip, poses[change['pose']]))
                for link in (l for b in root.find_all('boarder') for l in b.find_all('link')):
                    if len(link.args) >= 2 and link.args[1] == name:
                        reference = link.find('anm')
                        if reference is None:
                            link.append(block('{anm %s}' % quote(clip)))
                        else:
                            reference.atoms[1].set(clip)
            if 'offset' in change and any(change['offset']):
                bone = declared[name]
                if bone not in final['bones']:
                    _error(f'El asiento {name} no tiene un lugar en el modelo.')
                current = parse_model(model['document'].render())['bones']
                parent = current[bone]['parent']
                world = [list(r) for r in current[bone]['world']]
                for axis in range(3):
                    world[axis][3] += change['offset'][axis]
                local = multiply(inverse(current[parent]['world']), world) if parent else world
                node = next(n for n in model['document'].walk('bone') if n.args and n.args[-1] == bone)
                _set_matrix(node, local)
    if spec['added']:
        doors = list(dict.fromkeys(d.args[0] for b in root.find_all('boarder') for d in b.find_all('door')
                                   if d.args and d.args[0] in final['bones']))
        if not doors:
            _error('El vehículo no tiene puertas para que suban los pasajeros.')
        taken = set(final['bones']) | {p.args[0] for pl in root.find_all('placer') for p in pl.find_all('place') if p.args}
        for number, row in enumerate(spec['added'], 1):
            if row['parent'] not in final['bones']:
                _error('La parte elegida para el asiento no existe en el vehículo.')
            place = _free(f'mw_seat{number}', taken)
            bone = _free(f'mw_seat{number}_pos', taken | {place})
            taken |= {place, bone}
            node = block('{bone %s}' % quote(bone))
            _set_matrix(node, pose_matrix(row))
            host = next(n for n in model['document'].walk('bone') if n.args and n.args[-1] == row['parent'])
            host.append(node)
            root.append(block('{placer {place %s {group "passenger"} {LinkBone %s}}}' % (quote(place), quote(bone))))
            boarder.append(_clip(place, poses[row['pose']]))
            for door in doors[:2]:
                boarder.append(block('{link %s %s {anm %s} {forward putoff} {reverse puton}}' % (quote(door), quote(place), quote(place))))
            result['added'].append({'place': place, 'bone': bone, 'pose': row['pose'], 'parent': row['parent']})
    if result['added'] or result['native']:
        diagnostics.append({'code': 'passenger_seats_runtime_gate', 'level': 'warning',
                            'message': 'Asientos de pasajeros: subida, poses e IA pendientes de prueba en batalla.'})
    return result


def listing(root, model, display=None):
    """The final vehicle's passenger seats for the editor: place, pose, position."""
    from .geometry import point, identity
    frame = display or identity()
    clips = {a.args[0]: a for b in root.find_all('boarder') for a in b.find_all('anm') if a.args}
    poses = {}
    for link in (l for b in root.find_all('boarder') for l in b.find_all('link')):
        reference = link.find('anm')
        if len(link.args) >= 2 and reference is not None and reference.args and reference.args[0] in clips:
            forward = clips[reference.args[0]].find('forward')
            end = forward.find('end') if forward is not None else None
            if end is not None and end.args:
                poses.setdefault(link.args[1], end.args[0])
    seats = []
    for placer in root.find_all('placer'):
        for place in placer.find_all('place'):
            group = place.find('group')
            link = next((n for n in place.children if n.head.casefold() == 'linkbone' and n.args), None)
            if (not place.args or place.args[0].startswith('mw_rider') or group is None or group.args != ['passenger']
                    or link is None or link.args[0] not in model['bones']):
                continue
            seats.append({'place': place.args[0], 'pose': poses.get(place.args[0]), 'added': place.args[0].startswith('mw_seat'),
                          'position': point(frame, point(model['bones'][link.args[0]]['world'], [0, 0, 0]))})
    return seats
