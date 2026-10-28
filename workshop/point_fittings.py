"""Accessories that are a single point on the vehicle plus the game's own data.

Towing hitch (ChuckWagon): native tractors carry the props "tractor" and
"tractor_light|medium|heavy" (the heaviest trailer they may tow) and a hook bone
"link2" facing backwards (194 tractors in the game and MACE). Nothing else.

Demolition charge (frenchies): the Goliath's weapon, "weapon goliath" filled with
"ammo goliath 60kg|100kg", fired by the driver from its sight bone; the round is
the Sturmtiger's explosion at four metres, so the vehicle blows up beside its
target. Only native definitions are used; the point is ours.

Both are idempotent: building again (Edit, Update vehicles) replaces the hitch
or charge this module wrote, and a vehicle without the accessory keeps its own.
"""
import math

from .geometry import parse_model
from .pivots import pose_matrix
from .syntax import block, quote, bone_name


class PointFittingError(ValueError):
    pass


TIERS = ('light', 'medium', 'heavy')
CHARGES = {'60kg': 'ammo goliath 60kg', '100kg': 'ammo goliath 100kg'}
HOOK = 'link2'
CHARGE_BONE = 'cw_charge'
CHARGE_PLACE = 'cw_explosive'
NATIVE_CHARGE = ('set/stuff/explosive/goliath.weapon', 'set/stuff/explosive/goliath.60kg.ammo',
                 'set/stuff/explosive/goliath.100kg.ammo')


def _point(value, key, choices, message):
    if not isinstance(value, dict) or value.get(key) not in choices:
        raise PointFittingError(message)
    if not isinstance(value.get('parent'), str) or not value['parent'] or len(value['parent']) > 128:
        raise PointFittingError(message)
    for field in ('position', 'rotation'):
        values = value.get(field, [0, 0, 0])
        if (not isinstance(values, list) or len(values) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 100000 for v in values)):
            raise PointFittingError('La posición u orientación del punto no es válida.')
    return value


def towing_configuration(value):
    return _point(value, 'tier', TIERS, 'Elegí el peso que puede remolcar y dónde va el enganche.')


def charge_configuration(value):
    return _point(value, 'charge', CHARGES, 'Elegí la carga explosiva y dónde va.')


def _remove_bones(model, name):
    """Drop every bone called `name` (a hook or charge written before)."""
    for parent in [model['skeleton'], *model['document'].walk('bone')]:
        for child in list(parent.find_all('bone')):
            if bone_name(child) == name:
                parent.remove(child)


def _add_point(model, spec, name):
    from .compiler import _set_matrix
    from .extra_armor import parents
    hosts = {row['name'] for row in parents(model)}
    if spec['parent'] not in hosts:
        raise PointFittingError('El punto de fijación ya no existe en este vehículo.')
    _remove_bones(model, name)
    host = next(node for node in model['document'].walk('bone') if bone_name(node) == spec['parent'])
    node = block('{bone ' + quote(name) + '}')
    _set_matrix(node, pose_matrix(spec))
    host.append(node)
    model.update(parse_model(model['document'].render()))


def apply_towing(root, model, value, diagnostics):
    """Make the vehicle a tractor of the chosen weight with its hook at the point."""
    if value is None:
        return None
    spec = towing_configuration(value)
    _add_point(model, spec, HOOK)
    for node in list(root.find_all('props')):
        kept = [v for v in node.args if not (v == 'tractor' or v.startswith('tractor_'))]
        if kept != node.args:
            root.remove(node)
            if kept:
                root.append(block('{props ' + ' '.join(quote(v) for v in kept) + '}'))
    root.append(block('{props "tractor" "tractor_' + spec['tier'] + '"}'))
    diagnostics.append({'code': 'towing_hitch', 'level': 'info',
                        'message': f"Tows {spec['tier']} trailers from the hook at {spec['parent']}; towing in battle requires acceptance."})
    return {key: spec[key] for key in ('tier', 'parent', 'position', 'rotation') if key in spec}


def _crew_roles(root):
    return [place.args[0] for placer in root.find_all('placer') for place in placer.find_all('place') if place.args]


def apply_charge(resources, root, model, value, diagnostics):
    """Add the Goliath's demolition charge, fired by the driver from the point."""
    if value is None:
        return None
    spec = charge_configuration(value)
    for path in NATIVE_CHARGE:
        try:
            resources.read(path)
        except (KeyError, OSError) as error:
            raise PointFittingError('No están disponibles los recursos nativos de la carga explosiva.') from error
    _add_point(model, spec, CHARGE_BONE)
    weaponry = root.find('Weaponry')
    if weaponry is None:
        weaponry = block('{Weaponry}')
        root.append(weaponry)
    for place in list(weaponry.find_all('place')):
        if place.args == [CHARGE_PLACE]:
            weaponry.remove(place)
    roles = _crew_roles(root)
    operator = 'driver' if 'driver' in roles else (roles[0] if roles else None)
    if operator is None:
        raise PointFittingError('El vehículo no tiene tripulantes que puedan detonar la carga.')
    weaponry.append(block('{place ' + quote(CHARGE_PLACE) + ' {weapon "weapon goliath" filling '
                          + quote(CHARGES[spec['charge']]) + ' 1} {foresight ' + quote(CHARGE_BONE) + '}'
                          + ' {gunner ' + quote(operator) + '} {charger ' + quote(operator) + '} {basic}}'))
    diagnostics.append({'code': 'demolition_charge', 'level': 'info',
                        'message': f"{spec['charge']} demolition charge fired by {operator}; detonation in battle requires acceptance."})
    return {key: spec[key] for key in ('charge', 'parent', 'position', 'rotation') if key in spec}
