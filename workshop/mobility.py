"""Chassis mobility: the parameters of the vehicle's native mobility macro.

Every vehicle calls one in its Chassis block (("mobility_tank" speed(40)
reverse(8) traverse(30) weight(23.9) power(265) track(5) fuel(310) ...)). The
game derives normal and sprint speed, acceleration, turn time and radius from
them (properties/mobility.inc), so editing a parameter changes everything the
game computes from it, exactly as for a native vehicle with that value.
Weight stays native: it also stands for the physical mass the engine uses.
"""
import math

from .syntax import Atom, Node

# Parameter: (lowest, highest). Bounds keep the game's formulas finite.
EDITABLE = {
    'speed': (1, 200),            # km/h on road
    'reverse': (1, 100),          # km/h
    'traverse': (1, 360),         # tracked: degrees per second turning in place
    'speedatmaxturn': (1, 200),   # wheeled: km/h in a tight turn
    'power': (1, 5000),           # hp
    'track': (0.1, 10),           # tracked: track performance, 10 = ideal
    'fuel': (1, 10000),           # litres
    'range': (1, 10000),          # km
}


def mobility_call(root):
    """The Chassis block's mobility macro call, or None (inherited, none)."""
    chassis = root.find('Chassis') if root is not None else None
    if chassis is None:
        return None
    return next((n for n in chassis.children if n.opener == '(' and n.head.casefold().startswith('mobility')), None)


def _arguments(call):
    """name -> the Node holding its value (speed(40): Atom speed, Node (40))."""
    found, items = {}, call.items
    for index, item in enumerate(items[:-1]):
        following = items[index + 1]
        if isinstance(item, Atom) and not item.quoted and isinstance(following, Node) and following.opener == '(':
            found.setdefault(item.value.casefold(), following)
    return found


def _number(node):
    values = [item.value for item in node.items if isinstance(item, Atom)]
    if len(values) != 1:
        return None
    try:
        value = float(values[0])
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def read(root):
    """{'macro': name, 'values': {parameter: number}} of the editable ones."""
    call = mobility_call(root)
    if call is None:
        return None
    values = {}
    for name, node in _arguments(call).items():
        if name in EDITABLE and (value := _number(node)) is not None:
            values[name] = value
    return {'macro': call.head, 'values': values} if values else None


def validate(spec):
    if spec is None:
        return
    if not isinstance(spec, dict) or set(spec) - set(EDITABLE):
        raise ValueError('Los valores de movilidad no son válidos.')
    for name, value in spec.items():
        low, high = EDITABLE[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'La movilidad «{name}» tiene que estar entre {low:g} y {high:g}.')


def apply(root, spec, diagnostics=None):
    """Write the chosen values into the vehicle's own mobility call. Returns the
    parameters the vehicle has no native value for (left out, never invented)."""
    validate(spec)
    if not spec:
        return []
    call = mobility_call(root)
    arguments = _arguments(call) if call is not None else {}
    skipped = []
    for name, value in sorted(spec.items()):
        node = arguments.get(name)
        if node is None or _number(node) is None:
            skipped.append(name)
            continue
        node.items = [Atom('%g' % value, '%g' % value)]
    if skipped and diagnostics is not None:
        diagnostics.append({'code': 'mobility_skipped', 'level': 'warning', 'parameters': skipped,
                            'message': 'This chassis has no native value for: ' + ', '.join(skipped) + '; left unchanged.'})
    return skipped
