"""Extra crew: more soldiers in the vehicle than its native seats.

A crew seat in the game is a "crew" place hung from a bone ({LinkBone}), with
a boarding clip in the boarder and links: from a door to climb in, and to the
other crew places it may move to when someone falls. A new crewman copies all
of that from a native seat of the same job (or the closest one), on a bone of
its own on the chosen part of the vehicle.

What the extra crewman does:

- A loader ("charger") joins the weapon's {charger "a b"} list. The weapon
  still fires with the loaders it had ({minWorkers} keeps the old count), so a
  fallen extra loader never stops the gun. The engine reads one reload time per
  weapon ({rechargeTime}); it does not shorten it for more loaders, so the
  build gives that weapon its own copy with the reload shortened by
  `reload_bonus` percent per extra loader (see `reload_factors`).
- A gunner, commander or driver is a reserve: it copies the native seat's
  links, so it can take the place of whoever falls, as the game moves crew.

Names come from the job and the free numbers ("charger2", "commander2"):
the game's own crew bar captions apply, and "Update vehicles" rebuilds the
same places a saved campaign may hold soldiers in.
"""
from __future__ import annotations

import math
import re

from .geometry import parse_model
from .pivots import pose_matrix, pivot_of, copy_mirror
from .syntax import block, quote

ROLES = ('charger', 'gunner', 'commander', 'driver')
MAX_ADDED = 16
DEFAULT_RELOAD_BONUS = 15.0
RELOAD_BONUS = (0.0, 50.0)
# However many loaders, the weapon keeps at least this share of its reload.
FASTEST_RELOAD = 0.25


def _error(message):
    from .compiler import CompileError
    raise CompileError(message)


def _vector(value, count, bound, message):
    if (not isinstance(value, (list, tuple)) or len(value) != count
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > bound
                   for v in value)):
        _error(message)
    return [float(v) for v in value]


def configuration(spec):
    """{'added': [...], 'reload_bonus': %} with every value checked; None when empty."""
    if not spec:
        return None
    if not isinstance(spec, dict):
        _error('Los tripulantes extra no son válidos.')
    rows = spec.get('added') or []
    if not isinstance(rows, list) or len(rows) > MAX_ADDED:
        _error(f'Se pueden agregar hasta {MAX_ADDED} tripulantes.')
    bonus = spec.get('reload_bonus', DEFAULT_RELOAD_BONUS)
    if (isinstance(bonus, bool) or not isinstance(bonus, (int, float)) or not math.isfinite(bonus)
            or not RELOAD_BONUS[0] <= bonus <= RELOAD_BONUS[1]):
        _error('La mejora de recarga por cargador tiene que estar entre 0 y 50 %.')
    added = []
    for row in rows:
        if not isinstance(row, dict) or row.get('role') not in ROLES:
            _error('Elegí qué puesto ocupa el tripulante extra.')
        parent = row.get('parent')
        if not isinstance(parent, str) or not parent or len(parent) > 160:
            _error('Elegí la parte del vehículo donde va el tripulante.')
        item = {'role': row['role'], 'parent': parent,
                'position': _vector(row.get('position', [0, 0, 0]), 3, 10000, 'La posición del tripulante no es válida.'),
                'rotation': _vector(row.get('rotation', [0, 0, 0]), 3, 360, 'La orientación del tripulante no es válida.')}
        weapon = row.get('weapon')
        if weapon is not None:
            if row['role'] != 'charger' or not isinstance(weapon, str) or not re.fullmatch(r'[A-Za-z0-9_@.-]{1,80}', weapon):
                _error('El arma del cargador no es válida.')
            item['weapon'] = weapon
        if 'pivot' in row:
            item['pivot'] = pivot_of(row)
        copy_mirror(row, item)
        added.append(item)
    if not added:
        return None
    return {'added': added, 'reload_bonus': float(bonus)}


def reload_factors(contract):
    """{weapon place: reload multiplier} for the weapons that got extra loaders."""
    if not contract:
        return {}
    bonus = float(contract.get('reload_bonus', DEFAULT_RELOAD_BONUS)) / 100.0
    return {place: max(FASTEST_RELOAD, 1.0 - bonus * count)
            for place, count in sorted((contract.get('loaders') or {}).items())
            if count > 0 and bonus > 0}


def _family(name):
    return re.sub(r'\d+$', '', name)


def _crew_places(root):
    """{name: place node} of the explicit crew seats."""
    result = {}
    for placer in root.find_all('placer'):
        for place in placer.find_all('place'):
            group = place.find('group')
            if place.args and group is not None and group.args == ['crew']:
                result.setdefault(place.args[0], place)
    return result


def _reference(declared, role):
    """The native seat a new crewman copies: the same job, else the closest."""
    order = {}
    for name in (role, 'charger', 'gunner', 'commander', 'driver'):
        order.setdefault(name, len(order))
    candidates = sorted(declared, key=lambda name: (order.get(_family(name), len(order)),
                                                    int(re.search(r'\d+$', name).group()) if re.search(r'\d+$', name) else 0,
                                                    name))
    return candidates[0] if candidates else None


def _owner(place, bones):
    references = [n.args[0] for head in ('LinkBone', 'bone', 'foresight', 'shooter')
                  for n in place.find_all(head) if n.args]
    references.append(place.args[0])
    return next((name for name in references if name in bones), None)


def _distance(bones, name, ancestor):
    steps, seen = 0, set()
    while name and name not in seen:
        if name == ancestor:
            return steps
        seen.add(name)
        name = bones[name]['parent'] if name in bones else None
        steps += 1
    return None


def _loaded_weapon(root, bones, parent, wanted=None):
    """The weapon place an extra loader serves: the one asked for, or the
    weapon closest to the part the loader sits on (the main gun first)."""
    places = [p for w in root.find_all('Weaponry') for p in w.find_all('place')
              if p.args and p.find('weapon') is not None]
    if wanted is not None:
        found = next((p for p in places if p.args[0] == wanted), None)
        if found is None:
            _error(f'El vehículo ya no tiene el arma {wanted} para el cargador extra.')
        return found
    ranked = []
    for index, place in enumerate(places):
        owner = _owner(place, bones)
        distance = _distance(bones, owner, parent) if owner else None
        if distance is None:
            continue
        typed = place.find('type') is not None
        ranked.append((distance, typed, index, place))
    if not ranked:
        _error('La parte elegida no tiene un arma que el cargador extra pueda cargar.')
    return min(ranked, key=lambda row: row[:3])[3]


def _add_loader(place, name, declared):
    """Put `name` on the weapon's loader list; the weapon still fires with the
    loaders it had before (minWorkers)."""
    charger = next((n for n in place.children if n.head.casefold() == 'charger' and n.args), None)
    gunner = next((n for n in place.children if n.head.casefold() == 'gunner' and n.args), None)
    if charger is not None:
        names = charger.args[0].split()
        minimum = None
        if 'minWorkers' in charger.args:
            try:
                minimum = int(float(charger.args[charger.args.index('minWorkers') + 1]))
            except (IndexError, ValueError):
                minimum = None
    else:
        # An unnamed loader is the crew's "charger", else the gunner loads.
        names = ['charger'] if 'charger' in declared else (gunner.args[0].split() if gunner is not None else ['gunner'])
        minimum = None
    if minimum is None:
        minimum = len(names)
    names = [*names, name]
    node = block('{charger %s minWorkers %d}' % (quote(' '.join(names)), max(1, min(minimum, len(names)))))
    if charger is not None:
        place.items[place.items.index(charger)] = node
    else:
        place.append(node)


def apply(index, root, model, spec, assets, diagnostics):
    """Add the extra crew to the final vehicle; None when there is nothing to do."""
    from .compiler import _set_matrix, _unique_name, _crew_expansion, _expand_place_macros
    configured = configuration(spec)
    if configured is None:
        return None
    _crew_expansion(index, root)
    crewish = re.compile(r'(?:%s)\d*' % '|'.join(ROLES))
    _expand_place_macros(index, root, lambda nodes, places: any(crewish.fullmatch(name) for name in places))
    declared = _crew_places(root)
    if not declared:
        _error('El vehículo no tiene un puesto de tripulante que copiar para el tripulante extra.')
    final = parse_model(model['document'].render())
    bones = final['bones']
    boarders = list(root.find_all('boarder'))
    if not boarders:
        _error('El vehículo no tiene cómo subir a su tripulación.')
    taken = (set(bones) | {p.args[0] for pl in root.find_all('placer') for p in pl.find_all('place') if p.args}
             | {a.args[0] for b in boarders for a in b.find_all('anm') if a.args})
    result = {'added': [], 'loaders': {}, 'reload_bonus': configured['reload_bonus']}
    for number, row in enumerate(configured['added'], 1):
        if row['parent'] not in bones:
            _error('La parte elegida para el tripulante extra no existe en el vehículo.')
        role = row['role']
        reference = _reference(declared, role)
        name = role if role not in taken else _unique_name(role, taken)
        bone = f'mw_crew{number}_pos'
        if bone in taken:
            bone = _unique_name(bone, taken)
        taken |= {name, bone}
        # The seat, on its own bone of the chosen part.
        node = block('{bone %s}' % quote(bone))
        _set_matrix(node, pose_matrix(row))
        host = next(n for n in model['document'].walk('bone') if n.args and n.args[-1] == row['parent'])
        host.append(node)
        seat = declared[reference].clone()
        seat.atoms[1].set(name)
        for link in [n for n in seat.children if n.head.casefold() == 'linkbone']:
            seat.remove(link)
        seat.append(block('{LinkBone %s}' % quote(bone)))
        placer = block('{placer}')
        placer.append(seat)
        root.append(placer)
        # Boarding: its own copy of the reference clip and every link the
        # reference has (doors in and out, seats to move to).
        boarded = False
        for boarder in boarders:
            for clip in [a for a in boarder.find_all('anm') if a.args and a.args[0] == reference]:
                copy = clip.clone()
                copy.atoms[1].set(name)
                boarder.append(copy)
            for link in list(boarder.find_all('link')):
                if len(link.args) < 2 or reference not in link.args[:2] or link.args[0] == link.args[1]:
                    continue
                copy = link.clone()
                for atom in copy.atoms[1:3]:
                    if atom.value == reference:
                        atom.set(name)
                reference_clip = copy.find('anm')
                if reference_clip is not None and reference_clip.args == [reference]:
                    reference_clip.atoms[1].set(name)
                boarder.append(copy)
                boarded = True
        if not boarded:
            _error(f'El puesto {reference} no dice cómo sube su tripulante: no se puede copiar para el tripulante extra.')
        entry = {'place': name, 'bone': bone, 'role': role, 'parent': row['parent'], 'reference': reference}
        if role == 'charger':
            weapon = _loaded_weapon(root, bones, row['parent'], row.get('weapon'))
            _add_loader(weapon, name, declared)
            entry['weapon'] = weapon.args[0]
            result['loaders'][weapon.args[0]] = result['loaders'].get(weapon.args[0], 0) + 1
        result['added'].append(entry)
    diagnostics.append({'code': 'extra_crew_runtime_gate', 'level': 'warning',
                        'message': 'Tripulantes extra: subida, reemplazo de caídos y recarga pendientes de prueba en batalla.'})
    return result
