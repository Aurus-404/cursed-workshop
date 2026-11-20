"""Who aims and fires each weapon (Crew tool, "Operated by").

A weapon place names its operator: {gunner "gunner"} aims and fires it and
{charger "charger"} reloads it. One crewman can work several weapons, as
natively: a Pz IV's gunner fires the gun and the coaxial MG, a T-34's
loader the rear turret DT. Giving a weapon to someone already aboard lets
the player leave its own seat behind: a Wanze launcher put on a turret is
worked by the turret's gunner, with no extra soldier sitting on the roof.

A weapon with no traverse of its own turns with the joint that carries it
(the turret), like a coaxial MG: worked by the turret's gunner, aiming it
turns the turret instead of two crewmen pulling the turret their own way.

transform['weapon_operators'] = {weapon place: crew place}. It names the
finished vehicle's places, as crew_exclude and crew_seats do.
"""
from __future__ import annotations

import re

from .syntax import block, quote

MAX_ENTRIES = 32
_NAME = re.compile(r'[A-Za-z0-9_@.-]{1,80}')


def _error(message):
    from .compiler import CompileError
    raise CompileError(message)


def configuration(spec):
    """{place: crew place} with every name checked; None when empty."""
    if not spec:
        return None
    if not isinstance(spec, dict) or len(spec) > MAX_ENTRIES:
        _error('La lista de quién maneja cada arma no es válida.')
    result = {}
    for place, role in spec.items():
        if not isinstance(place, str) or not _NAME.fullmatch(place) or not isinstance(role, str) or not _NAME.fullmatch(role):
            _error('La lista de quién maneja cada arma no es válida.')
        result[place] = role
    return result or None


def _operators(place, head):
    node = next((n for n in place.children if n.head.casefold() == head and n.args), None)
    return node, (node.args[0].split() if node is not None else [])


def _mountable_slots(root):
    """Inventory slots that take an infantry weapon ({mask "mountable"})."""
    return {w.args[0] for e in root.find_all('extender') if e.args == ['inventory']
            for w in e.find_all('weapon') if w.args and any(m.args == ['mountable'] for m in w.find_all('mask'))}


def _slot_item(root, slot):
    """The weapon the box puts in a mountable slot, '' when it starts empty."""
    for e in root.find_all('extender'):
        if e.args == ['inventory']:
            for item in e.walk('item'):
                if item.args and any(u.args == [slot] for u in item.find_all('user')):
                    return item.args[0]
    return ''


def weapon_places(root):
    """Weapon places a crewman works (not vision devices), with the mountable
    slots whose weapon comes from the inventory (the Carrier's front Bren)."""
    result = []
    slots = _mountable_slots(root)
    for weaponry in root.find_all('Weaponry'):
        for place in weaponry.find_all('place'):
            kind = place.find('type')
            if not place.args or (place.find('weapon') is None and place.args[0] not in slots):
                continue
            if kind is not None and kind.args == ['commander']:
                continue
            result.append(place)
    return result


def listing(root):
    """[{place, weapon, type, gunner, charger}] for the Crew tool."""
    rows = []
    for place in weapon_places(root):
        _, gunners = _operators(place, 'gunner')
        _, chargers = _operators(place, 'charger')
        kind = place.find('type')
        weapon = place.find('weapon')
        row = {'place': place.args[0], 'weapon': (weapon.args[0] if weapon.args else '') if weapon is not None
               else _slot_item(root, place.args[0]),
               'type': kind.args[0] if kind is not None and kind.args else '',
               'gunner': gunners[0] if gunners else 'gunner', 'chargers': chargers}
        if weapon is None:
            row['mountable'] = True
        rows.append(row)
    return rows


def apply(root, spec, roles, excluded=()):
    """Give each listed weapon to its crewman. Returns the places changed and
    the ones skipped because the vehicle no longer has them. A weapon given to
    someone left in reserve (excluded) is left to cover()."""
    spec = configuration(spec)
    if not spec:
        return [], []
    places = {place.args[0]: place for place in weapon_places(root)}
    changed, skipped = [], []
    for name, role in sorted(spec.items()):
        place = places.get(name)
        if place is None:
            skipped.append(name)  # a weapon replaced or removed since it was assigned
            continue
        if role not in roles:
            _error(f'El tripulante {role} que maneja {name} no está en el vehículo.')
        if role in excluded:
            continue
        if _give(place, role, set(roles) - set(excluded)):
            changed.append(name)
    return changed, skipped


_ORDER = {'gunner': 0, 'commander': 1, 'charger': 2}


def _rank(name):
    family = re.sub(r'\d+$', '', name)
    number = re.search(r'\d+$', name)
    return (_ORDER.get(family, 3), int(number.group()) if number else 0, name)


def cover(root, excluded, roles, primary=None):
    """Weapons whose operator stays in reserve go to someone still aboard:
    one of the weapon's own loaders, else whoever works the main weapon, else
    a crewman already working a weapon, else anyone of the crew, the driver
    last. Returns {weapon place: new operator}."""
    excluded = set(excluded)
    aboard = set(roles) - excluded
    if not excluded:
        return {}
    places = weapon_places(root)
    gunner_of = lambda place: (_operators(place, 'gunner')[1] or ['gunner'])[0]
    main = next((p for p in places if p.args[0] == primary), None)
    result = {}
    for place in places:
        if gunner_of(place) not in excluded:
            continue
        aiming = sorted({gunner_of(p) for p in places if p is not place} & aboard, key=_rank)
        candidates = [*sorted((set(_operators(place, 'charger')[1]) & aboard) - {'driver'}, key=_rank),
                      *([gunner_of(main)] if main is not None and main is not place and gunner_of(main) in aboard else []),
                      *aiming, *sorted(aboard - {'driver'}, key=_rank), *(['driver'] if 'driver' in aboard else [])]
        if not candidates:
            _error(f'No queda ningún tripulante para manejar {place.args[0]}.')
        _give(place, candidates[0], aboard)
        result[place.args[0]] = candidates[0]
    return result


def _give(place, role, roles):
    """Make `role` aim and fire the weapon (roles: who is aboard); True when it changed."""
    gunner, previous = _operators(place, 'gunner')
    previous = previous or ['gunner']
    if previous == [role]:
        return False
    node = block('{gunner %s}' % quote(role))
    if gunner is not None:
        place.items[place.items.index(gunner)] = node
    else:
        place.append(node)
    # Whoever reloaded it still does; the old operator's share goes to the new one.
    charger, loaders = _operators(place, 'charger')
    if charger is not None:
        names = list(dict.fromkeys(role if name in previous else name for name in loaders))
        args = charger.args
        text = '{charger %s' % quote(' '.join(names))
        if 'minWorkers' in args:
            try:
                minimum = int(float(args[args.index('minWorkers') + 1]))
            except (IndexError, ValueError):
                minimum = len(names)
            text += ' minWorkers %d' % max(1, min(minimum, len(names)))
        place.items[place.items.index(charger)] = block(text + '}')
    elif 'charger' not in roles:
        # No loader named and no "charger" aboard: its operator reloads it.
        place.append(block('{charger %s}' % quote(role)))
    return True
