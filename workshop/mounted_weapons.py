"""Weapons anywhere on a vehicle, and swappable (mountable) weapon slots.

How the game does it (read from the stock definitions, 1.067):

- The Universal Carrier's front Bren is a *mountable slot*: the inventory
  declares {weapon "mgun" {mask "mountable"}}, its {Weaponry} place "mgun"
  has no {weapon} of its own, only {LinkBone "mgun"} and {LinkAnimation
  "off"}, and the box holds the infantry weapon bound to it:
  {item "bren_mk1" filled {user "mgun"}}. Whatever the crew puts in that slot
  in battle is the weapon (the Boys variant has a Boys there). The StuG's
  roof MG and the Tiger E's commander MG are the same contract.
- The slot hangs from two revolute bones (traverse, then elevation, a
  quarter turn about X) and a plain link bone under them, where the infantry
  weapon's own model is attached: its muzzle, sounds and flash travel with it.
- A weapon that cannot turn far enough turns the vehicle: {rotate} on the
  place plus a revolute model root with {limits 0 0} (the Carrier's hull
  Bren, every casemate; see compiler._apply_traverse_class).

So the editor can add a new weapon place on any bone (hull, turret, a wheel)
without a turret: new traverse/elevation/link bones, a mountable slot, the
chosen weapon and its ammunition in the box, worked by a crewman already
aboard ("who works each weapon"). It can also turn an existing weapon place
(the Sherman's bow .30, even the main gun's place) into a mountable slot.

Only weapons the game marks mountable can be put in a slot: infantry MGs,
AT rifles, rifles (commander_mg.weapon_catalog). A towed gun (PaK) or a
tripod MG crew weapon (Lafette) is a vehicle of its own, not an inventory
item; the turret/free-mount operations install those.

transform['mounted_weapons'] = {
  'added': [{parent, position, rotation, link:{position, rotation}, yaw:[min,max],
             pitch:[min,max], item, type:'hull'|'aa', rotate: bool, operator?}],
  'slots': [{place, item, link?:{position, rotation}}],
}
"""
from __future__ import annotations

import math
import re

from .pivots import pose_matrix, copy_mirror
from .syntax import block, quote

MAX_ADDED = 8
MAX_SLOTS = 16
TYPES = ('hull', 'aa')
_NAME = re.compile(r'[A-Za-z0-9_@.-]{1,80}')
# The elevation bone of every native slot: a quarter turn about X.
_ELEVATION = [[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]


def _error(message):
    from .compiler import CompileError
    raise CompileError(message)


def _vector(value, count, bound, message):
    if (not isinstance(value, (list, tuple)) or len(value) != count
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > bound
                   for v in value)):
        _error(message)
    return [float(v) for v in value]


def _pose(value, message):
    value = value or {}
    if not isinstance(value, dict):
        _error(message)
    pose = {'position': _vector(value.get('position', [0, 0, 0]), 3, 10000, message),
            'rotation': _vector(value.get('rotation', [0, 0, 0]), 3, 360, message)}
    copy_mirror(value, pose)
    return pose


def _weapon(index, item):
    from .commander_mg import weapon_catalog
    if not isinstance(item, str) or not _NAME.fullmatch(item):
        _error('Elegí un arma montable para el hueco.')
    weapon = next((w for w in weapon_catalog(index, item=item) if w['id'] == item), None)
    if weapon is None:
        _error('Elegí un arma montable disponible. Para armas DLC, abrí el juego con el DLC adquirido e '
               'instalado y volvé a leer los recursos.')
    return weapon


def configuration(spec, index=None):
    """The checked spec, None when empty; with `index`, each weapon is resolved too."""
    if not spec:
        return None
    if not isinstance(spec, dict):
        _error('Las armas montadas no son válidas.')
    added, slots = spec.get('added') or [], spec.get('slots') or []
    if not isinstance(added, list) or len(added) > MAX_ADDED:
        _error(f'Se pueden agregar hasta {MAX_ADDED} armas sin torreta.')
    if not isinstance(slots, list) or len(slots) > MAX_SLOTS:
        _error('Los huecos de armas montables no son válidos.')
    rows = []
    for row in added:
        if not isinstance(row, dict):
            _error('El arma montada no es válida.')
        parent = row.get('parent')
        if not isinstance(parent, str) or not parent or len(parent) > 160:
            _error('Elegí la parte del vehículo donde va el arma.')
        kind = row.get('type', 'hull')
        if kind not in TYPES:
            _error('El tipo del arma montada no es válido.')
        if not isinstance(row.get('rotate', True), bool):
            _error('El giro del vehículo del arma montada no es válido.')
        yaw = _vector(row.get('yaw', [-15, 15]), 2, 180, 'Los límites de giro del arma no son válidos.')
        pitch = _vector(row.get('pitch', [-10, 20]), 2, 90, 'Los límites de elevación del arma no son válidos.')
        if yaw[0] >= yaw[1] or pitch[0] >= pitch[1]:
            _error('El límite mínimo debe ser menor que el máximo.')
        item = {'parent': parent, 'type': kind, 'rotate': row.get('rotate', True), 'yaw': yaw, 'pitch': pitch,
                'position': _vector(row.get('position', [0, 0, 0]), 3, 10000, 'La posición del arma no es válida.'),
                'rotation': _vector(row.get('rotation', [0, 0, 0]), 3, 360, 'La orientación del arma no es válida.'),
                'link': _pose(row.get('link'), 'El ajuste del arma en su soporte no es válido.'),
                'item': row.get('item')}
        copy_mirror(row, item)
        operator = row.get('operator')
        if operator is not None:
            if not isinstance(operator, str) or not _NAME.fullmatch(operator):
                _error('El tripulante que maneja el arma no es válido.')
            item['operator'] = operator
        if index is not None:
            item['weapon'] = _weapon(index, item['item'])
        elif not isinstance(item['item'], str) or not _NAME.fullmatch(item['item']):
            _error('Elegí un arma montable para el hueco.')
        rows.append(item)
    converted, seen = [], set()
    for row in slots:
        if not isinstance(row, dict) or not isinstance(row.get('place'), str) or not _NAME.fullmatch(row['place']):
            _error('Elegí qué arma del vehículo se vuelve un hueco montable.')
        if row['place'] in seen:
            _error('Cada arma del vehículo se vuelve un hueco una sola vez.')
        seen.add(row['place'])
        item = {'place': row['place'], 'item': row.get('item'),
                'link': _pose(row.get('link'), 'El ajuste del arma en su soporte no es válido.')}
        if index is not None:
            item['weapon'] = _weapon(index, item['item'])
        elif not isinstance(item['item'], str) or not _NAME.fullmatch(item['item']):
            _error('Elegí un arma montable para el hueco.')
        converted.append(item)
    if not rows and not converted:
        return None
    return {'added': rows, 'slots': converted}


def _mountable(root):
    """Names of the inventory's mountable slots."""
    return {w.args[0] for e in root.find_all('extender') if e.args == ['inventory']
            for w in e.find_all('weapon') if w.args and any(m.args == ['mountable'] for m in w.find_all('mask'))}


def _aim_joint(model, bone):
    """The nearest revolute bone carrying `bone`: the elevation the weapon aims with."""
    while bone in model['bones']:
        if 'revolute' in model['bones'][bone]['node'].args[:-1]:
            return bone
        bone = model['bones'][bone]['parent']
    return None


def slot_candidates(root, model):
    """Weapon places that can become (or already are) mountable slots.

    [{place, type, weapon, mountable, host, link}] where host is the joint the
    infantry weapon would hang from and link the bone it attaches to.
    """
    from .weapon_binding import weapon_link
    slots = _mountable(root)
    rows = []
    for weaponry in root.find_all('Weaponry'):
        for place in weaponry.find_all('place'):
            if not place.args:
                continue
            kind = place.find('type')
            kind = kind.args[0] if kind is not None and kind.args else ''
            if kind == 'commander':
                continue
            weapon = place.find('weapon')
            native = place.args[0] in slots and weapon is None
            if weapon is None and not native:
                continue
            link = weapon_link(place, model)
            host = _aim_joint(model, link) if link else None
            if host is None:
                continue
            rows.append({'place': place.args[0], 'type': kind, 'weapon': weapon.args[0] if weapon is not None and weapon.args else '',
                         'mountable': native, 'host': host, 'link': link})
    return rows


def _crew(root):
    from .extra_crew import _crew_places
    return list(_crew_places(root))


def _default_operator(root, aboard):
    """A crewman who aims nothing yet, else whoever has the least to do; the driver last."""
    from .weapon_operators import weapon_places, _rank
    aiming = {}
    for place in weapon_places(root):
        gunner = place.find('gunner')
        name = gunner.args[0].split()[0] if gunner is not None and gunner.args and gunner.args[0].split() else 'gunner'
        aiming[name] = aiming.get(name, 0) + 1
    candidates = sorted(aboard, key=lambda name: (name == 'driver', aiming.get(name, 0), _rank(name)))
    return candidates[0] if candidates else None


def _lock_root(resources, root, model):
    """The root a {rotate} weapon needs: revolute, {limits 0 0} (stock casemates)."""
    from .compiler import _effective_props
    from .syntax import Atom, Trivia, bone_name
    if _effective_props(resources, root) & {'cannon', 'airborne'}:
        return False  # a towed gun swings on its free root; an aircraft cannot turn on the spot
    roots = [name for name, bone in model['bones'].items() if bone['parent'] is None]
    if len(roots) != 1:
        return False
    node = next(n for n in model['document'].walk('bone') if n.args and n.args[-1] == roots[0])
    if len(node.args) == 1:
        name_atom = node.atoms[-1]
        at = next(i for i, item in enumerate(node.items) if item is name_atom)
        node.items[at:at] = [Atom('revolute', 'revolute'), Trivia(' ')]
    if node.args[0].casefold() == 'revolute':
        limits = node.find('limits')
        if limits is None or [float(v) for v in limits.args[:2]] != [0.0, 0.0]:
            for old in node.find_all('limits'):
                node.remove(old)
            node.append(block('{limits 0 0}'))
        for definition in root.find_all('bone'):
            if bone_name(definition) == roots[0]:
                for old in definition.find_all('limits'):
                    definition.remove(old)
                definition.append(block('{limits 0 0}'))
    return True


def _free(name, taken):
    """`name`, or `name_2`, `name_3`...: a bone name nothing else uses (reserved in `taken`)."""
    candidate, number = name, 1
    while candidate in taken:
        number += 1
        candidate = f'{name}_{number}'
    taken.add(candidate)
    return candidate


def apply(index, root, model, spec, diagnostics, transform=None):
    """Add the mounted weapons and slots to the final vehicle; None when none."""
    from .compiler import _set_matrix, _crew_expansion, _include_defines
    from .geometry import parse_model
    from .weapon_binding import mountable_slot, isolate_aiming
    configured = configuration(spec, index)
    if configured is None:
        return None
    transform = transform or {}
    _crew_expansion(index, root)
    final = parse_model(model['document'].render())
    bones = final['bones']
    crew = _crew(root)
    excluded = set(transform.get('crew_exclude') or [])
    aboard = [name for name in crew if name not in excluded]
    chosen = transform.get('weapon_operators') or {}
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
    if 'abm_mgun' not in _include_defines(index, root):
        root.append(block('(include "/properties/abm.inc")'))
    taken = set(bones)
    mounts, skipped = [], []

    def stock(weapon, slot):
        initial = block('{item %s filled {user %s}}' % (quote(weapon['id']), quote(slot)))
        ammo = block('{item %s %s}' % (quote(weapon['ammo_item']), weapon['ammo_rounds']))
        box.append(initial)
        box.append(ammo)
        return initial.render(), ammo.render()

    def host_node(name):
        return next(n for n in model['document'].walk('bone') if n.args and n.args[-1] == name)

    # Existing places first: their names are the vehicle's own.
    candidates = {row['place']: row for row in slot_candidates(root, final)}
    places = {p.args[0]: p for w in root.find_all('Weaponry') for p in w.find_all('place') if p.args}
    for row in configured['slots']:
        found, place = candidates.get(row['place']), places.get(row['place'])
        if found is None or place is None:
            skipped.append(row['place'])  # replaced or removed since it was chosen
            continue
        slot, weapon = row['place'], row['weapon']
        if found['mountable']:
            # A native slot (Carrier, StuG): only its starting weapon changes.
            # The weapon it held stays in the box as cargo, as in battle.
            for item in box.find_all('item'):
                for user in list(item.find_all('user')):
                    if user.args == [slot]:
                        item.remove(user)
            link = found['link']
        else:
            link = _free('mw_slot_' + slot, taken)
            node = block('{bone %s}' % quote(link))
            _set_matrix(node, pose_matrix(row['link']))
            host_node(found['host']).append(node)
            for head in ('weapon', 'LinkBone', 'LinkAnimation', 'shooter', 'foresight', 'Barrels', 'RestoreIKAfterAim'):
                for old in [n for n in place.children if n.head.casefold() == head.casefold()]:
                    place.remove(old)
            # A native slot aims like an MG ("abm_mgun"), whatever the place held:
            # a cannon's zeroing and dispersion do not belong to an MG 42.
            for old in [n for n in place.children if n.opener == '(' and n.head.casefold().startswith('abm_')]:
                place.remove(old)
            place.append(block('{LinkBone %s}' % quote(link)))
            place.append(block('{LinkAnimation "off"}'))
            place.append(block('("abm_mgun")'))
            inventory.append(block('{weapon %s {mask "mountable"}}' % quote(slot)))
        initial, ammo = stock(weapon, slot)
        mounts.append({'slot': slot, 'place': slot, 'item': weapon['id'], 'converted': not found['mountable'],
                       'native_slot': found['mountable'], 'weapon': found['weapon'], 'host': found['host'],
                       'bones': {'@link': link}, 'initial_item': initial, 'ammo_item': ammo})
    rotating = False
    for number, row in enumerate(configured['added'], 1):
        if row['parent'] not in bones:
            _error('La parte elegida para el arma montada no existe en el vehículo.')
        slot = mountable_slot(root)
        # Its own tool names its crewman; the Crew list does not offer it.
        operator = row.get('operator') or chosen.get(slot)
        if operator is not None and operator not in aboard:
            _error(f'El tripulante {operator} que maneja el arma montada no está en el vehículo.')
        operator = operator or _default_operator(root, aboard)
        if operator is None:
            _error('El vehículo no tiene un tripulante que pueda manejar el arma montada.')
        names = {key: _free(f'mw_weapon{number}_{key}', taken) for key in ('yaw', 'pitch', 'link')}
        yaw = block('{bone revolute %s {limits %s %s}}' % (quote(names['yaw']), *row['yaw']))
        _set_matrix(yaw, pose_matrix(row))
        pitch = block('{bone revolute %s {limits %s %s}}' % (quote(names['pitch']), *row['pitch']))
        _set_matrix(pitch, _ELEVATION)
        link = block('{bone %s}' % quote(names['link']))
        _set_matrix(link, pose_matrix(row['link']))
        pitch.append(link)
        yaw.append(pitch)
        isolate_aiming(yaw, names['yaw'])
        host_node(row['parent']).append(yaw)
        inventory.append(block('{weapon %s {mask "mountable"}}' % quote(slot)))
        initial, ammo = stock(row['weapon'], slot)
        text = ('{place %s {type %s} {LinkBone %s} {LinkAnimation "off"} {gunner %s} {charger %s} ("abm_mgun")%s {basic}%s}'
                % (quote(slot), quote(row['type']), quote(names['link']), quote(operator), quote(operator),
                   ' {rotate}' if row['rotate'] else '', ' {ai_in_manual}' if row['type'] == 'aa' else ''))
        weaponry.append(block(text))
        for name, speed in ((names['yaw'], 60), (names['pitch'], 40)):
            root.append(block('{bone %s {speed2 %s}}' % (quote(name), speed)))
        rotating |= row['rotate']
        mounts.append({'slot': slot, 'place': slot, 'item': row['weapon']['id'], 'added': True, 'parent': row['parent'],
                       'operator': operator, 'type': row['type'], 'rotate': row['rotate'],
                       'bones': {'@yaw': names['yaw'], '@pitch': names['pitch'], '@link': names['link']},
                       'root': names['yaw'], 'initial_item': initial, 'ammo_item': ammo})
    locked = _lock_root(index, root, model) if rotating else False
    from .inventory_space import make_room
    make_room(index, root, [m[key] for m in mounts for key in ('initial_item', 'ammo_item')])
    if skipped:
        diagnostics.append({'code': 'mounted_weapon_slot_skipped', 'level': 'warning',
                            'message': 'El vehículo ya no tiene estas armas para volverlas huecos montables: ' + ', '.join(skipped)})
    diagnostics.append({'code': 'mounted_weapons_runtime_gate', 'level': 'warning',
                        'message': 'Armas montadas: posición del arma, cambio de arma en el hueco, giro del vehículo e IA '
                                   'pendientes de prueba en batalla.'})
    return {'mounts': mounts, 'skipped': skipped, 'hull_locked': locked, 'runtime_verified': False}


def add_interactions(record, contract):
    """Each slot forwards its fire event to the weapon in it (as rider MGs)."""
    from .commander_mg import add_mountable_fire
    for mount in (contract or {}).get('mounts') or []:
        add_mountable_fire(record, mount['slot'])


def preview(index, body):
    """The weapon's meshes in its link bone's frame, for the editor."""
    from .geometry import build_preview, parse_model, attached_actor_frame, identity
    weapon = _weapon(index, (body or {}).get('item'))
    source = index.mountable_game()
    gun = build_preview(source, weapon['entity'], 'full')
    actor = attached_actor_frame(parse_model(source.read(gun['model_path'])), identity())
    for mesh in gun['meshes']:
        mesh['component'] = 'gun'
        mesh['rig_joint'] = 'pitch'
    return {'meshes': gun['meshes'], 'actor_frame': actor, 'item': weapon['id'], 'pitch_matrix': _ELEVATION}


def catalog(index):
    """Mountable MGs and AT rifles, MGs first: what the slot editor offers."""
    from .commander_mg import weapon_catalog
    rows = []
    for weapon in weapon_catalog(index):
        tags = set(weapon['tags'])
        kind = 'at_rifle' if 'at_rifle' in tags else 'mgun' if 'mgun' in tags else None
        if kind:
            rows.append({'id': weapon['id'], 'label': weapon['label'], 'kind': kind})
    rows.sort(key=lambda w: (w['kind'] != 'mgun', w['label'].casefold(), w['id']))
    return rows
