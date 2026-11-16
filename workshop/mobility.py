"""Chassis mobility: the parameters of the vehicle's native mobility macro.

Every vehicle calls one in its Chassis block (("mobility_tank" speed(40)
reverse(8) traverse(30) weight(23.9) power(265) track(5) fuel(310) ...)). The
game derives normal and sprint speed, acceleration, turn time and radius from
them (properties/mobility.inc), so editing a parameter changes everything the
game computes from it, exactly as for a native vehicle with that value.
Weight stays native: it also stands for the physical mass the engine uses.
"""
import math

from .syntax import Atom, Node, Trivia, block

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


def normalize_gears(root, resources, document, diagnostics=None):
    """Keep the requested top speeds while removing unreachable lower gears.

    Native macros can put a fixed lower ratio before reverse/speed. At high
    speeds that terminal ratio can fall below (or equal) the fixed ratio.
    A local macro overrides only the affected gearbox fields, after the native
    mobility call. The native call stays editable and global resources stay intact.
    """
    from .compiler import _include_defines
    from .registration import _expand_macros
    from .weapon_attributes import evaluate
    chassis=root.find('Chassis')
    if chassis is None:return
    marker='cw_mobility_gears'
    for owner in (root, document):
        for node in list(owner.find_all('define')):
            if node.args[:1]==[marker]:owner.remove(node)
    for node in list(chassis.children):
        if node.opener=='(' and node.head==marker:chassis.remove(node)
    definitions=_include_defines(resources,root)
    definitions.update({n.args[0].casefold():n for owner in (document,root)
                        for n in owner.find_all('define') if n.args})
    expanded=_expand_macros(chassis,definitions)
    effective={}
    for locomotion in expanded.find_all('Locomotion'):
        for node in locomotion.children:
            if node.head.casefold() in ('gears','reargears'):effective[node.head.casefold()]=node
    repairs=[]
    for field,node in effective.items():
        values=[evaluate(v) for v in node.items[1:] if isinstance(v,(Atom,Node))]
        if not values or any(v is None or not math.isfinite(v) or v<=0 for v in values):
            raise ValueError('The chassis gear ratios must be finite positive numbers.')
        if all(b>a for a,b in zip(values,values[1:])):continue
        top=values[-1]
        valid=[]
        for ratio in sorted(values[:-1]):
            if ratio<top*(1-1e-6) and (not valid or ratio>valid[-1]*(1+1e-6)):valid.append(ratio)
        valid.append(top)
        repairs.append(block('{'+node.head+' '+' '.join(format(v,'.12g') for v in valid)+'}'))
    if not repairs:return
    definition=block('(define "'+marker+'" {Locomotion})')
    for node in repairs:definition.find('Locomotion').append(node)
    document.items[0:0]=[definition,Trivia('\n')]
    chassis.append(block('("'+marker+'")'))
    if diagnostics is not None:
        diagnostics.append({'code':'mobility_gears_normalized','level':'warning',
            'message':'Removed unreachable lower gear ratios; requested forward and reverse top speeds are preserved.'})


def apply(root, spec, diagnostics=None, *, resources=None, document=None):
    """Write the chosen values into the vehicle's own mobility call. Returns the
    parameters the vehicle has no native value for (left out, never invented)."""
    validate(spec)
    call = mobility_call(root)
    arguments = _arguments(call) if call is not None else {}
    skipped = []
    for name, value in sorted((spec or {}).items()):
        node = arguments.get(name)
        if node is None or _number(node) is None:
            skipped.append(name)
            continue
        node.items = [Atom('%g' % value, '%g' % value)]
    if skipped and diagnostics is not None:
        diagnostics.append({'code': 'mobility_skipped', 'level': 'warning', 'parameters': skipped,
                            'message': 'This chassis has no native value for: ' + ', '.join(skipped) + '; left unchanged.'})
    if resources is not None and document is not None:
        normalize_gears(root,resources,document,diagnostics)
    return skipped
