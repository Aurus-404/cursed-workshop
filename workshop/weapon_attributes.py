"""Weapon attributes: each weapon of one vehicle gets its own copy, edited.

A weapon is a set/stuff file that inherits from others ({from "75mm_pak39"}
...{from "pattern gun"}) and fills fields through macros ("reload time"
writes {rechargeTime 7}, "fire rate" writes {recoveryTime (/ 60 850)}). The
game merges a later {parameters "ap" ...} into an earlier one and a later
field replaces an earlier one; the base game relies on it in a single file.

Editing a weapon of a vehicle writes a new file beside the original:

    {from "37mm_kwk38t" {rechargeTime 3} {parameters "aphe" {speed (* 0.75 800)}}}

and the vehicle's weapon place names that copy. Nothing else in the game
changes: other vehicles and the AI keep the native weapon. Only values the
weapon really has are offered; nothing is invented.
"""
from __future__ import annotations

import copy
import math
import posixpath
import re

from .syntax import Atom, Node, block, parse, quote

# Field: (lowest, highest). Bounds keep the game's formulas finite.
WEAPON_FIELDS = {
    'reload_time': (0.05, 120),     # seconds: shell, or whole magazine/belt
    'fire_rate': (1, 6000),         # rounds per minute inside a magazine
    'magazine': (1, 100000),        # rounds per magazine/belt
    'range': (1, 2000),             # game units, AI and player alike
    'burst_pause': (0, 60),         # seconds between bursts
    'burst_short': (1, 1000),       # rounds in a short burst
    'burst_long': (1, 1000),        # rounds in a long burst
    'dispersion': (0.05, 20),       # spread radius multiplier (1 = native)
}
SHELL_FIELDS = {
    'velocity': (1, 5000),          # muzzle velocity, the weapon file's own unit (m/s)
    'armor_damage': (0, 100000),
    'infantry_damage': (0, 1000000),
}
PENETRATION = (0, 5000)
AMMO = (0, 10000000)


class WeaponAttributeError(ValueError):
    pass


def _operands(node):
    return [item for item in node.items if isinstance(item, (Atom, Node))]


def evaluate(item):
    """A number or a prefix expression such as (* 0.75 (+ 1 2)); None if not one."""
    if isinstance(item, Atom):
        try:
            value = float(item.value)
        except ValueError:
            return None
        return value if math.isfinite(value) else None
    if not isinstance(item, Node) or item.opener != '(':
        return None
    parts = _operands(item)
    if len(parts) < 2 or not isinstance(parts[0], Atom) or parts[0].value not in '+-*/':
        return None
    values = [evaluate(part) for part in parts[1:]]
    if any(value is None for value in values):
        return None
    operator = parts[0].value
    result = values[0]
    if operator == '-' and len(values) == 1:
        return -result
    for value in values[1:]:
        if operator == '+':result += value
        elif operator == '-':result -= value
        elif operator == '*':result *= value
        elif value == 0:return None
        else:result /= value
    return result if math.isfinite(result) else None


def _scaled(node):
    """{speed (* 0.75 740)} -> (0.75, 740): the factor stays, 740 is edited.
    A plain number is (1, value); anything else (None, evaluated value)."""
    parts = _operands(node)[1:]
    if len(parts) != 1:
        return None, None
    value = parts[0]
    if isinstance(value, Atom):
        number = evaluate(value)
        return (1.0, number) if number is not None else (None, None)
    inner = _operands(value)
    if (len(inner) == 3 and isinstance(inner[0], Atom) and inner[0].value == '*'
            and all(isinstance(i, Atom) for i in inner[1:])):
        factor, number = evaluate(inner[1]), evaluate(inner[2])
        if factor is not None and number is not None:
            return factor, number
    return None, evaluate(value)


def _number_text(value):
    text = ('%.4f' % value).rstrip('0').rstrip('.')
    return text if text not in ('', '-0') else '0'


def _chain(resources, name):
    """[(path, expanded node)] from the root pattern to the weapon itself."""
    from .gun_assembly import _stuff_presets
    from .registration import _expand_macros, _stuff_definition
    chain, seen = [], set()
    path = _stuff_definition(resources, name)
    while True:
        if path in seen:
            raise WeaponAttributeError('The weapon inherits from itself.')
        seen.add(path)
        document = parse(resources.read(path))
        node = document.find('from')
        parent = None
        if node is not None and node.args:
            parent = node.args[0]
        else:
            node = next((n for n in document.children if n.opener == '{'), None)
        if node is None:
            raise WeaponAttributeError('Weapon definition without a body: ' + path)
        chain.append((path, _expand_macros(node, _stuff_presets(resources, path))))
        if parent is None:
            break
        pattern = parent.startswith('pattern ')
        path = _stuff_definition(resources, parent.removeprefix('pattern '), path, pattern)
    chain.reverse()
    return chain


def _key(node):
    return node.head.casefold()


def effective(resources, name):
    """{'path', 'top': {field: node}, 'shells': {shell: {field: node}}} as merged by the game."""
    chain = _chain(resources, name)
    top, shells = {}, {}
    for _, node in chain:
        for child in node.children:
            key = _key(child)
            if child.opener != '{':
                continue
            if key == 'parameters' and child.args:
                shell = shells.setdefault(child.args[0].casefold(), {})
                for field in child.children:
                    if field.opener != '{':
                        continue
                    if _key(field) == 'spreading':
                        table = field.find('radiusTable')
                        if table is not None:shell['radiustable'] = table
                    else:
                        shell[_key(field)] = field
            elif key == 'spreading':
                for field in child.children:
                    top['spreading.' + _key(field)] = field
            else:
                top[key] = child
    return {'path': chain[-1][0], 'top': top, 'shells': shells}


def _table(node):
    """[[distance, value]] of {projectileDamageTable {10 54} {30 (+ ...)}}; None if not numeric."""
    if node is None:
        return None
    rows = []
    for pair in node.children:
        parts = _operands(pair)
        if len(parts) != 2:
            return None
        distance, value = evaluate(parts[0]), evaluate(parts[1])
        if distance is None or value is None:
            return None
        rows.append([distance, value])
    return rows or None


def _range(top):
    node = top.get('range')
    if node is not None:
        values = [evaluate(part) for part in _operands(node)[1:]]
        if values and all(v is not None for v in values):
            return max(values)
    values = [evaluate(_operands(top[key])[1]) for key in ('aimrange', 'maxrange')
              if key in top and len(_operands(top[key])) > 1]
    values = [v for v in values if v is not None]
    return max(values) if values else None


def _shell_range(fields):
    node = fields.get('range')
    if node is None:
        return None
    values = [evaluate(part) for part in _operands(node)[1:]]
    return max(values) if values and all(v is not None for v in values) else None


def _item_tokens(name):
    tokens = name.split()
    container = bool(tokens) and tokens[0].casefold() == 'ammo'
    return (tokens[1:] if container else tokens), container


def _inventory_items(root):
    inventory = next((n for n in root.find_all('extender') if n.args and n.args[0].casefold() == 'inventory'), None)
    if inventory is None:
        return []
    return [item for item in inventory.walk('item') if item.args]


def _family(place, top):
    weapon = place.find('weapon')
    if weapon is not None and len(weapon.args) >= 3 and weapon.args[1] == 'filling':
        return weapon.args[2].split()[0]
    filling = top.get('filling')
    if filling is not None and filling.args:
        return _item_tokens(filling.args[0])[0][0] if _item_tokens(filling.args[0])[0] else None
    return None


def _places(root):
    for weaponry in root.find_all('Weaponry'):
        for place in weaponry.find_all('place'):
            weapon = place.find('weapon')
            if place.args and weapon is not None and weapon.args:
                yield place, weapon


def _magazine(top):
    filling = top.get('filling')
    if filling is None or len(filling.args) < 2:
        return None
    try:
        value = float(filling.args[1])
    except ValueError:
        return None
    return value if value >= 1 else None


def describe(resources, root):
    """The weapons of a compiled vehicle with their native values, one per place."""
    result, ammo_seen = [], {}
    for place, weapon in _places(root):
        name = weapon.args[0]
        try:
            info = effective(resources, name)
        except Exception:
            continue  # not a stuff weapon (vision devices, unresolvable): not editable
        top, shells = info['top'], info['shells']
        fields = {}
        if 'rechargetime' in top and (v := evaluate(_operands(top['rechargetime'])[1] if len(_operands(top['rechargetime'])) > 1 else None)) is not None:
            fields['reload_time'] = v
        if 'recoverytime' in top and len(_operands(top['recoverytime'])) > 1:
            v = evaluate(_operands(top['recoverytime'])[1])
            if v:
                fields['fire_rate'] = 60 / v
        if (v := _magazine(top)) is not None:
            fields['magazine'] = v
        if (v := _range(top)) is not None:
            fields['range'] = v
        timeout = top.get('firingtimeout')
        if timeout is not None and len(_operands(timeout)) > 1 and (v := evaluate(_operands(timeout)[1])) is not None:
            fields['burst_pause'] = v
        burst = top.get('burst')
        for kind in ('short', 'long'):
            node = burst.find(kind) if burst is not None else None
            if node is not None and len(_operands(node)) > 1 and (v := evaluate(_operands(node)[1])) is not None:
                fields['burst_' + kind] = v
        if any('radiustable' in s for s in shells.values()) or 'spreading.spreadradius' in top:
            fields['dispersion'] = 1.0
        family = _family(place, top)
        items = []
        for item in _inventory_items(root):
            tokens, container = _item_tokens(item.args[0])
            if family and tokens and tokens[0] == family:
                count = None
                if len(item.args) > 1:
                    try:count = float(item.args[1])
                    except ValueError:pass
                items.append((item.args[0], tokens, container, count))
        shell_names = []
        for _, tokens, container, _ in items:
            if not container and len(tokens) > 1 and tokens[1].casefold() not in shell_names:
                shell_names.append(tokens[1].casefold())
        sections = []
        for shell in shell_names or ['']:
            own = shells.get(shell, {}) if shell else {}
            section = {'key': shell, 'label': shell.upper() if shell else ''}
            for field, key in (('velocity', 'speed'), ('armor_damage', 'minimumdamagemodifier'), ('infantry_damage', 'healthdamage')):
                node = own.get(key) or top.get(key)
                if node is not None:
                    factor, value = _scaled(node)
                    if factor is not None and value is not None:
                        section[field] = value
            table = _table(own.get('projectiledamagetable') or (None if shell else top.get('projectiledamagetable')))
            if table:
                section['penetration'] = table
            if len(section) > 2:
                sections.append(section)
        ammo = [{'item': name, 'count': count} for name, _, _, count in items if count is not None]
        for row in ammo:
            ammo_seen.setdefault(row['item'], []).append(place.args[0])
        entry = {'place': place.args[0], 'weapon': name, 'type': (place.find('type').args[0] if place.find('type') and place.find('type').args else ''),
                 'fields': fields, 'shells': sections, 'ammo': ammo}
        if fields or sections or ammo:
            result.append(entry)
    for entry in result:
        for row in entry['ammo']:
            row['shared'] = [p for p in ammo_seen[row['item']] if p != entry['place']]
    return result


def validate(spec):
    if spec is None:
        return
    if not isinstance(spec, dict) or set(spec) - {'places', 'ammo'}:
        raise WeaponAttributeError('Los atributos de armas no son válidos.')
    def number(value, bounds, label):
        low, high = bounds
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise WeaponAttributeError(f'«{label}» tiene que estar entre {low:g} y {high:g}.')
    places = spec.get('places') or {}
    if not isinstance(places, dict) or len(places) > 32:
        raise WeaponAttributeError('Los atributos de armas no son válidos.')
    for place, row in places.items():
        if not isinstance(place, str) or not re.fullmatch(r'[A-Za-z0-9_@.-]{1,80}', place) or not isinstance(row, dict):
            raise WeaponAttributeError('Los atributos de armas no son válidos.')
        if set(row) - set(WEAPON_FIELDS) - {'shells'}:
            raise WeaponAttributeError('Los atributos de armas no son válidos.')
        for field, value in row.items():
            if field != 'shells':number(value, WEAPON_FIELDS[field], field)
        shells = row.get('shells') or {}
        if not isinstance(shells, dict) or len(shells) > 32:
            raise WeaponAttributeError('Los atributos de armas no son válidos.')
        for shell, values in shells.items():
            if not isinstance(shell, str) or not re.fullmatch(r'[a-z0-9_]{0,40}', shell) or not isinstance(values, dict):
                raise WeaponAttributeError('Los atributos de armas no son válidos.')
            if set(values) - set(SHELL_FIELDS) - {'penetration'}:
                raise WeaponAttributeError('Los atributos de armas no son válidos.')
            for field, value in values.items():
                if field == 'penetration':
                    if not isinstance(value, list) or not 0 < len(value) <= 32:
                        raise WeaponAttributeError('La tabla de penetración no es válida.')
                    for v in value:number(v, PENETRATION, 'penetración')
                else:
                    number(value, SHELL_FIELDS[field], field)
    ammo = spec.get('ammo') or {}
    if not isinstance(ammo, dict) or len(ammo) > 64:
        raise WeaponAttributeError('Los atributos de armas no son válidos.')
    for item, count in ammo.items():
        if not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9_ .-]{1,120}', item):
            raise WeaponAttributeError('Los atributos de armas no son válidos.')
        number(count, AMMO, 'munición')


def _shifted(rows, old, new):
    """Points at or past the old range move with it; the rest keep their distance."""
    if old is None or new is None or abs(new - old) < 1e-9:
        return [list(r) for r in rows]
    result = []
    for distance, value in rows:
        if distance >= old - 0.02:
            distance += new - old
        result.append([distance, value])
    # A shorter range drops native points beyond it, keeping the order valid.
    ordered = []
    for distance, value in result:
        if ordered and distance <= ordered[-1][0]:
            continue
        ordered.append([distance, value])
    return ordered


def _table_node(head, rows):
    return block('{' + head + ' ' + ' '.join('{%s %s}' % (_number_text(d), _number_text(v)) for d, v in rows) + '}')


def _scaled_node(head, node, value):
    factor, _ = _scaled(node) if node is not None else (1.0, None)
    if factor is None or abs(factor - 1) < 1e-12:
        return block('{%s %s}' % (head, _number_text(value)))
    return block('{%s (* %s %s)}' % (head, _number_text(factor), _number_text(value)))


def weapon_names(resources, name):
    """The weapon's shown name in each language the game ships: {"default":
    English, "es": Spanish}, read from desc/weapon/<name> in the .pot/.po
    tables. Later mods override earlier ones, as in the game."""
    cache = getattr(resources, '_weapon_names', None)
    if cache is None:
        cache = {}
        entries = getattr(resources, 'entries', None) or {}
        rows = sorted((e for k, e in entries.items() if k.startswith('localizations/') and '/desc/' in k
                       and k.endswith(('.pot', '.po'))), key=lambda e: (e.priority, e.path))
        for entry in rows:
            language = entry.path.split('/')[1]
            context = source = None
            for line in resources.read_text(entry.path).splitlines():
                line = line.strip()
                if line.startswith('msgctxt "desc/weapon/'):
                    context, source = line[len('msgctxt "desc/weapon/'):-1].casefold(), None
                elif context and line.startswith('msgid "'):
                    source = line[len('msgid "'):-1].replace('\\"', '"')
                elif context and line.startswith('msgstr "'):
                    text = line[len('msgstr "'):-1].replace('\\"', '"')
                    shown = source if language == 'default' else text
                    if shown:
                        cache.setdefault(context, {})[language] = shown
                    context = None
        try:
            resources._weapon_names = cache
        except AttributeError:
            pass
    return dict(cache.get(name.casefold(), {}))


def copy_name(new_id, place):
    return new_id + '_' + re.sub(r'[^A-Za-z0-9_]', '_', place).lower()


def _copy_text(resources, name, row):
    """The weapon copy: {from "name" ...only the changed values...}."""
    info = effective(resources, name)
    top, shells = info['top'], info['shells']
    body = []
    if 'reload_time' in row:
        body.append(block('{rechargeTime %s}' % _number_text(row['reload_time'])))
    if 'fire_rate' in row:
        body.append(block('{recoveryTime %s}' % _number_text(60 / row['fire_rate'])))
    if 'magazine' in row:
        filling = top.get('filling')
        if filling is None or not filling.args:
            raise WeaponAttributeError(f'{name} has no magazine to resize.')
        body.append(block('{filling %s %d}' % (quote(filling.args[0]), round(row['magazine']))))
    old_range = _range(top)
    new_range = row.get('range', old_range)
    if 'range' in row:
        body.append(block('{range %s %s}' % (_number_text(new_range), _number_text(new_range))))
    if 'burst_pause' in row:
        timeout = top.get('firingtimeout')
        parts = [evaluate(p) for p in _operands(timeout)[1:]] if timeout is not None else []
        old = parts[0] if parts and parts[0] else None
        spread = parts[1] * row['burst_pause'] / old if old and len(parts) > 1 and parts[1] is not None else 0
        body.append(block('{FiringTimeout %s %s}' % (_number_text(row['burst_pause']), _number_text(spread))))
    if 'burst_short' in row or 'burst_long' in row:
        burst = top.get('burst')
        if burst is None:
            raise WeaponAttributeError(f'{name} fires no bursts.')
        edited = burst.clone()
        for kind in ('short', 'long'):
            node = edited.find(kind)
            if 'burst_' + kind in row and node is not None:
                parts = _operands(node)
                if len(parts) > 1 and isinstance(parts[1], Atom):
                    parts[1].set(str(round(row['burst_' + kind])))
        body.append(edited)
    dispersion = row.get('dispersion', 1.0)
    if abs(dispersion - 1) > 1e-9 and 'spreading.spreadradius' in top:
        radius = evaluate(_operands(top['spreading.spreadradius'])[1])
        if radius is not None:
            body.append(block('{spreading {SpreadRadius %s}}' % _number_text(radius * dispersion)))
    edits = row.get('shells') or {}
    top_shell = edits.get('')
    if top_shell:
        for field, key in (('velocity', 'speed'), ('armor_damage', 'minimumDamageModifier'), ('infantry_damage', 'healthDamage')):
            if field in top_shell:
                body.append(_scaled_node(key, top.get(key.casefold()), top_shell[field]))
        if 'penetration' in top_shell:
            rows = _table(top.get('projectiledamagetable'))
            if not rows or len(rows) != len(top_shell['penetration']):
                raise WeaponAttributeError(f'{name}: the penetration table changed; open the weapon again.')
            body.append(_table_node('projectileDamageTable', [[d, v] for (d, _), v in zip(rows, top_shell['penetration'])]))
    names = set(shells) | {s for s in edits if s}
    for shell in sorted(names):
        own = shells.get(shell, {})
        values = edits.get(shell) or {}
        inner = []
        old = _shell_range(own)
        if old is not None and old <= 0:
            old = None  # an ammunition the weapon never fires (range 0): left native
            if not values:continue
        new = old * new_range / old_range if old is not None and old_range and 'range' in row else old
        if old is not None and 'range' in row:
            inner.append(block('{range %s %s}' % (_number_text(new), _number_text(new))))
        for field, key in (('velocity', 'speed'), ('armor_damage', 'minimumDamageModifier'), ('infantry_damage', 'healthDamage')):
            if field in values:
                inner.append(_scaled_node(key, own.get(key.casefold()) or top.get(key.casefold()), values[field]))
        rows = _table(own.get('projectiledamagetable'))
        if rows and any(later[0] <= earlier[0] for earlier, later in zip(rows, rows[1:])):
            rows = None  # not a distance table the game can read in order: left native
        if 'penetration' in values:
            if not rows or len(rows) != len(values['penetration']):
                raise WeaponAttributeError(f'{name}: the penetration table changed; open the weapon again.')
            rows = [[d, v] for (d, _), v in zip(rows, values['penetration'])]
        if rows and ('penetration' in values or 'range' in row):
            inner.append(_table_node('projectileDamageTable', _shifted(rows, old if old is not None else old_range, new if new is not None else new_range)))
        radius = _table(own.get('radiustable'))
        if radius and ('range' in row or abs(dispersion - 1) > 1e-9):
            radius = _shifted(radius, old if old is not None else old_range, new if new is not None else new_range)
            radius = [[d, v * dispersion] for d, v in radius]
            inner.append(block('{spreading ' + _table_node('radiusTable', radius).render() + '}'))
        if inner:
            parameters = block('{parameters %s}' % quote(shell))
            for node in inner:parameters.append(node)
            body.append(parameters)
    text = '{from ' + quote(name) + '\n' + '\n'.join('\t' + node.render() for node in body) + '\n}\n'
    parse(text)  # never write what the game could not read
    return info['path'], text


def apply(resources, root, spec, new_id, diagnostics=None):
    """Point each edited place at its own weapon copy and set the vehicle's
    ammunition. Returns ({addon path: text}, places given a copy, ammo totals)."""
    validate(spec)
    files, renamed, labels = {}, [], []
    if not spec:
        return files, renamed, {}
    places = {place.args[0]: weapon for place, weapon in _places(root)}
    for place, row in sorted((spec.get('places') or {}).items()):
        if not row:
            continue
        weapon = places.get(place)
        if weapon is None:
            if diagnostics is not None:
                diagnostics.append({'code': 'weapon_attributes_skipped', 'level': 'warning',
                                    'message': f'The vehicle has no weapon place {place} any more; its weapon attributes were left out.'})
            continue
        source, text = _copy_text(resources, weapon.args[0], row)
        name = copy_name(new_id, place)
        files['resource/' + posixpath.dirname(source) + '/' + name] = text
        # The copy shows the original's name, not desc/weapon/<copy>.
        shown = weapon_names(resources, weapon.args[0])
        if shown.get('default'):
            labels.append((name, shown['default'], shown.get('es') or shown['default']))
        weapon.atoms[1].set(name)
        renamed.append(place)
    if labels:
        from .registration import _pot
        table = 'localizations/{}/interface/text/desc/modular_workshop_' + new_id + '_weapons.{}'
        rows = [('desc/weapon/' + name, english, spanish) for name, english, spanish in labels]
        files[table.format('default', 'pot')] = _pot([row[:2] for row in rows])
        files[table.format('es', 'po')] = _pot(rows, 'es')
    totals = {}
    for item in _inventory_items(root):
        count = (spec.get('ammo') or {}).get(item.args[0])
        if count is None:
            continue
        atoms = item.atoms
        if len(atoms) < 3 or evaluate(atoms[2]) is None:
            continue  # only stacks the vehicle already counts
        atoms[2].set(str(round(count)))
        totals[item.args[0]] = round(count)
    return files, renamed, totals


def save_item_key(item):
    """("bulletger_37", "aphe") for both {item "bulletger_37 aphe" 35} and the
    save's {item "bulletger_37" "aphe" "ammo" 117 {cell 0 3}}."""
    words = [atom.value for atom in item.atoms[1:] if atom.quoted]
    words = [w for part in words for w in part.split()]
    return tuple(w for w in words if w.casefold() != 'ammo')
