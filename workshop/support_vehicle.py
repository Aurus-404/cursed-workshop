"""Turretless support conversions using effective native supply contracts.

Cargo meshes are visual fittings, never extra actors, inventories or armor.
Supply capacity comes from a finite, existing native profile, not crate count.
"""
import hashlib
import json
import math
import re

from .pivots import pose_matrix, pivot_of
from .syntax import parse, block, quote, bone_name
from .geometry import (parse_model, descendants, multiply, inverse, identity,
                       euler_matrix, translation, point, build_preview, bounds_of)


class SupportError(ValueError):
    pass


def _fail():
    raise SupportError('La conversión de apoyo no es válida.')


def _finite(node, head):
    value = node.find(head)
    try:
        number = float(value.args[0])
        return number if math.isfinite(number) else None
    except (AttributeError, IndexError, ValueError):
        return None


def catalog(resources):
    """Discover finite native packages and passive cargo geometry, cached per stack."""
    if hasattr(resources, '_support_catalog'):
        return resources._support_catalog
    from .compiler import _include_defines, _load_entity
    from .registration import _expand_macros
    properties = 'properties/resupply.inc'
    definitions = _include_defines(resources, parse('(include "/'+properties+'")'))
    supply = {name: node for name, node in definitions.items()
              if any(e.args == ['supply_zone'] for e in node.walk('extender'))}
    packages, cargo = {}, []
    call_pattern = re.compile(r'\(\s*"?('+'|'.join(re.escape(n) for n in supply)+r')"?(?:\s|\))') if supply else None
    for path in sorted(resources.entries):
        if not path.startswith('entity/') or not path.endswith('.def') or re.search(r'/(?:x|xx)/|_(?:x|xx)\.def$', path):
            continue
        if '/workshop/' in path:
            continue  # built vehicles only copy native cargo (as the armour catalog skips them)
        if path.startswith('entity/-vehicle/') and call_pattern:
            text = resources.read_text(path)
            if call_pattern.search(text):
                try:
                    root = parse(text)
                    for call in root.walk():
                        if call.opener != '(' or call.head not in supply:
                            continue
                        expanded = _expand_macros(parse(call.render()), definitions)
                        zones = [n for n in expanded.walk('extender') if n.args == ['supply_zone']]
                        if len(zones) != 1:
                            continue
                        zone = zones[0]
                        capacity, radius = _finite(zone, 'limit'), _finite(zone, 'radius')
                        if capacity is None or not 0 < capacity <= 1_000_000 or radius is None or not 0 < radius <= 100:
                            continue  # no infinite or unresolved native profiles
                        kind = ('fuel' if zone.find('fuel') else 'engineer' if zone.find('mechanicResources')
                                else 'ammo' if any('ammo' in n.args for n in zone.walk('item')) else None)
                        if kind is None:
                            continue
                        signature = expanded.render()
                        identifier = hashlib.sha256(signature.encode()).hexdigest()[:16]
                        if identifier not in packages:
                            entry = resources.resolve(path)
                            template = path.rsplit('/', 1)[-1][:-4]
                            packages[identifier] = {'id': identifier, 'kind': kind,
                                'label': resources.label_for(template), 'template': template,
                                'source': entry.source_id, 'definition_path': path,
                                'capacity': capacity, 'radius': radius, 'macro': call.head,
                                'call': call.render(), 'body': signature}
                except (ValueError, KeyError, OSError):
                    continue
        # Resource families, never a vehicle whitelist. Keep selectable props passive.
        if not (re.search(r'entity/construction/_military/(boxes/|environment/barrels/)', path)
                or re.search(r'entity/inventory/.+/(?:repair_kit|toolbox|box_metal|barrel1|canister|jerrycan)/', path)):
            continue
        try:
            info = resources.resolve_entity(path)
            if not info.get('model_path'):
                continue
            model = parse_model(resources.read(info['model_path']))
            if len(model['bones']) > 64 or not any(b['assets'] for b in model['bones'].values()):
                continue
            cargo.append({'id': path, 'template': path, 'label': path.rsplit('/', 1)[-1][:-4],
                          'kind': 'fuel' if re.search('barrel|fuel|canister|jerry', path) else
                                  'engineer' if re.search('tool|repair|box_metal', path) else 'ammo',
                          'model_path': info['model_path'], 'source': info.get('source_id', '')})
        except (ValueError, KeyError, OSError):
            continue
    result = {'profiles': sorted(packages.values(), key=lambda p: (p['kind'], p['capacity'], p['label'])),
              'cargo': cargo, 'runtime_verified': False}
    resources._support_catalog = result
    return result


def public_catalog(resources):
    value = catalog(resources)
    return {**value, 'profiles': [{k: v for k, v in p.items() if k not in ('body', 'call')}
                                for p in value['profiles']]}


def configuration(resources, spec):
    if not isinstance(spec, dict) or spec.get('schema') != 1:
        _fail()
    value = catalog(resources)
    profile = next((p for p in value['profiles'] if p['id'] == spec.get('profile')), None)
    if profile is None:
        raise SupportError('Elegí un perfil de suministro nativo disponible.')
    fittings = spec.get('cargo')
    if not isinstance(fittings, list) or not fittings:
        raise SupportError('Colocá al menos un accesorio de carga.')
    allowed = {p['id']: p for p in value['cargo']}
    normalized = []
    for row in fittings:
        if not isinstance(row, dict) or row.get('asset') not in allowed:
            raise SupportError('Elegí un accesorio de carga disponible.')
        pose = {}
        pivot_of(row)
        for key, bound in (('position', 10000), ('rotation', 360)):
            vector = row.get(key, [0, 0, 0])
            if (not isinstance(vector, list) or len(vector) != 3 or any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                    or abs(v) > bound for v in vector)):
                _fail()
            pose[key] = vector
        normalized.append({'asset': row['asset'], **pose})
    mount = spec.get('remove_mount', '')
    if not isinstance(mount, str) or len(mount) > 160:
        _fail()
    return profile, normalized, mount


def _crew_materialized(resources, root):
    """Materialize only crew-producing calls; do not expand armor/mobility macros."""
    from .crew_visibility import seat_declarations
    from .compiler import _include_defines
    places, boarders, _ = seat_declarations(resources, root)
    definitions = _include_defines(resources, root)
    # Match seat_declarations' effective macro lookup, including local wrappers.
    for path in ('properties/tank_crew.ext', 'properties/car_crew.ext', 'properties/cannon_crew.ext'):
        try:
            for node in parse(resources.read(path)).find_all('define'):
                if node.args: definitions.setdefault(node.args[0], node)
        except (KeyError, OSError):
            pass
    definitions.update({n.args[0]: n for n in root.find_all('define') if n.args})
    def emits(name, seen=()):
        if name in seen or name not in definitions:
            return False
        return any(n.head.casefold() in ('placer', 'boarder') or
                   (n.opener == '(' and emits(n.head, (*seen, name))) for n in definitions[name].children)
    for n in list(root.children):
        if n.head.casefold() in ('placer', 'boarder') or (n.opener == '(' and emits(n.head)):
            root.remove(n)
    placer = block('{placer}')
    for p in places:
        placer.append(p.clone())
    root.append(placer)
    for b in boarders:
        root.append(b.clone())


def strip_turret(resources, root, model, mount, *, wreck=False):
    """Remove an explicitly owned subtree and its references, retaining hull weapons."""
    from .compiler import (_remove_bone, _module_ids, _values, _selected_places,
                           _place_roles, _filter_crew, _hull_system, _explicit_weapon_operators)
    from .gun_assembly import weapon_link
    _explicit_weapon_operators(resources, root)
    removed = set()
    if mount:
        if mount not in model['bones'] or not model['bones'][mount]['parent']:
            raise SupportError('No se puede separar esa torreta del chasis.')
        if not wreck:
            candidates = {n for n in model['mounts'] if model['bones'][n]['parent']}
            for w in root.find_all('Weaponry'):
                for place in w.find_all('place'):
                    name = weapon_link(place, model)
                    while name in model['bones']:
                        b = model['bones'][name]
                        if b['parent'] and b['node'].args[0].casefold() == 'revolute':
                            candidates.add(name)
                        name = b['parent']
            top_mounts = {n for n in candidates if not any(
                other != n and n in descendants(model['bones'], other) for other in candidates)}
            if mount not in top_mounts:
                raise SupportError('No se puede separar esa torreta del chasis.')
        removed = descendants(model['bones'], mount)
        if any(_hull_system(n) for n in removed):
            raise SupportError('La torreta comparte sistemas del chasis que no se pueden retirar.')
    _crew_materialized(resources, root)
    weapons = [p for w in root.find_all('Weaponry') for p in w.find_all('place')]
    selected = []
    for w in root.find_all('Weaponry'):
        # _selected_places historically reads the first block. Isolate each
        # block so later native/mod declarations have the same ownership rule.
        scope = block('{game_entity}')
        scope.append(w)
        owned, _ = _selected_places(scope, model, removed)
        for p in w.find_all('place'):
            link = weapon_link(p, model)
            if p in owned or link in removed:
                selected.append(p)
    kept = [p for p in weapons if p not in selected]
    # Defaults have been resolved above. Utility places (native smoke barrels,
    # for example) have no operator and must not keep a removed gunner alive.
    keep_roles = {'driver'} | set().union(set(), *(_place_roles(p, p.args == ['gun']) for p in kept))
    drop_roles = set().union(set(), *(_place_roles(p, True) for p in selected)) - keep_roles
    for pl in root.find_all('placer'):
        for seat in pl.find_all('place'):
            links = seat.find_all('LinkBone')
            if links and links[-1].args and links[-1].args[0] in removed and seat.args:
                if seat.args[0] in keep_roles:
                    raise SupportError('Un puesto conservado depende de la torreta que se quiere quitar.')
                drop_roles.add(seat.args[0])
            elif mount and seat.args and seat.find('group') and seat.find('group').args == ['crew']:
                role = seat.args[0]
                anchor = links[-1].args[0] if links and links[-1].args else role
                if role not in keep_roles and (anchor not in model['bones'] or anchor in removed):
                    # Closed turret seats often have no LinkBone at all. They
                    # must not survive merely because there is no visible actor.
                    drop_roles.add(role)
    _filter_crew(root, drop_roles, False)
    for w in root.find_all('Weaponry'):
        for p in list(w.find_all('place')):
            if p in selected:
                w.remove(p)
    retired = {p.args[0] for p in selected if p.args}
    for inv in root.find_all('extender'):
        if inv.args != ['inventory']:
            continue
        for slot in list(inv.find_all('weapon')):
            if slot.args and slot.args[0] in retired:
                inv.remove(slot)
        for item in inv.walk('item'):
            for user in list(item.find_all('user')):
                if user.args and user.args[0] in retired:
                    item.remove(user)
    volumes = {n for n, v in model['volumes'].items() if v['bone'] in removed}
    ids = _module_ids(model, removed) | volumes
    for n in list(root.children):
        if ((n.head.casefold() in ('volume', 'component') and n.args and n.args[0] in ids)
                or (n.head.casefold() == 'bone' and bone_name(n) in removed)
                or (n.opener == '(' and n.head != 'define' and (n.head in ids or _values(n) & ids))):
            root.remove(n)
    for sensor in root.find_all('sensor'):
        for visor in list(sensor.find_all('visor')):
            if _values(visor) & removed:
                sensor.remove(visor)
    for n in volumes:
        model['document'].remove(model['volumes'][n]['node'])
    if mount:
        _remove_bone(model, mount)
    root.append(block('{props "-turret"}'))
    if mount:
        from .compiler import _restore_independent_mount_capabilities
        _restore_independent_mount_capabilities(root, parse_model(model['document'].render()))
    from .crew_visibility import seat_declarations
    remaining, _, _ = seat_declarations(resources, root)
    if any(p.args and p.args[0] in drop_roles for p in remaining):
        raise SupportError('No se pudieron retirar todos los puestos de la torreta.')
    return {'removed_bones': sorted(removed), 'removed_places': [p.args[0] for p in selected],
            'removed_roles': sorted(drop_roles), 'removed_mount': mount}


_FILE_SUFFIXES = ('.ebm', '.dds', '.mtl', '.ply', '.anm', '.mdl', '.vol')


def _localize_files(resources, document, definition_path, assets):
    """Copy the files a service block names relative to its native vehicle.

    resupply.inc macros name files relative to the vehicle that calls them:
    {DecalView supply_zone {Sprite "supply_zone.ebm"}} is the zone circle in
    each supply truck's own folder. Copied into a generated vehicle the name
    pointed nowhere and the circle was not drawn; the file now travels with
    the vehicle, like every other asset, and keeps a relative reference.
    """
    for node in document.walk():
        for atom in node.atoms[1:]:
            value = atom.value
            if not value.casefold().endswith(_FILE_SUFFIXES) or value.startswith(('/', '$')) or ':' in value:
                continue
            try:
                path = resources.resolve_reference(value, definition_path)
                atom.set(assets.add(path, 'sz'))
            except (FileNotFoundError, KeyError, OSError, ValueError):
                continue


def apply_supply(resources, root, spec, sources, assets=None):
    profile, cargo, mount = configuration(resources, spec)
    # Override the original support service, if any. Cosmetic edits never stack zones.
    from .compiler import _include_defines, _macro_arguments
    defs = _include_defines(resources, root)
    defs.update(_include_defines(resources, parse('(include "/properties/resupply.inc")')))
    effects = {}
    def emits(name, seen=()):
        if name in effects: return effects[name]
        if name in seen or name not in defs: return False
        result = any((n.head == 'extender' and n.args == ['supply_zone']) or
                     (n.opener == '(' and emits(n.head, (*seen, name))) for n in defs[name].children)
        effects[name] = result
        return result
    def expand(document, depth=0, stack=()):
        if depth > 24: raise SupportError('No se pudo resolver el servicio de apoyo original.')
        for n in list(document.children):
            if n.opener != '(' or not emits(n.head): continue
            if n.head in stack: raise SupportError('No se pudo resolver el servicio de apoyo original.')
            definition = defs[n.head]
            body = ''.join(v.render() for v in definition.items[definition.items.index(definition.atoms[1])+1:])
            arguments = _macro_arguments(n)
            body = re.sub(r'%(\w+)', lambda m: arguments.get(m.group(1).casefold(), ''), body)
            expanded = parse(body)
            expand(expanded, depth+1, (*stack, n.head))
            index = document.items.index(n)
            document.items[index:index+1] = expanded.items
    expand(root)
    for n in list(root.children):
        if ((n.head == 'extender' and n.args == ['supply_zone']) or
                (n.head.casefold() == 'decalview' and n.args == ['supply_zone'])):
            root.remove(n)
    body = parse(profile['body'])
    if assets is not None:
        _localize_files(resources, body, profile['definition_path'], assets)
    for node in body.children:
        root.append(node.clone())
    for path in ('properties/resupply.inc', profile['definition_path']):
        sources[path] = hashlib.sha256(resources.read(path)).hexdigest()
    return {k: profile[k] for k in ('id', 'kind', 'template', 'capacity', 'radius', 'macro', 'source')}


def fitting_frame(resources, asset):
    data = build_preview(resources, asset, 'full')
    if not data['meshes'] or any(d.get('code') == 'mesh_unavailable' for d in data.get('diagnostics', [])):
        raise SupportError('No se pudo cargar el modelo del accesorio de carga.')
    positions = [v for mesh in data['meshes'] for v in mesh['positions']]
    bounds = bounds_of(positions)
    lo, hi = bounds['min'], bounds['max']
    return data, translation([-(lo[0]+hi[0])/2, -(lo[1]+hi[1])/2, -lo[2]])


def add_cargo(resources, model, spec, assets):
    _, cargo, _ = configuration(resources, spec)
    from .compiler import _set_matrix, _rename_bone_refs
    final = parse_model(model['document'].render())
    model.update(final)
    # Structural root, not a discarded ring or rotating weapon.
    parent = next((n for n in final['bones'] if n.casefold() == 'body'), None)
    if parent is None:
        parent = next(n for n, b in final['bones'].items() if b['parent'] is None)
    host = final['bones'][parent]
    for i, row in enumerate(cargo):
        info = resources.resolve_entity(row['asset'])
        source = parse_model(resources.read(info['model_path']))
        _, center = fitting_frame(resources, row['asset'])
        frame = multiply(pose_matrix(row), center)
        wrapper = block('{bone '+quote('mw_cargo_'+str(i))+'}')
        _set_matrix(wrapper, multiply(inverse(host['world']), frame))
        mapping = {n: 'mw_cargo_'+str(i)+'_'+str(j) for j, n in enumerate(source['bones'])}
        for bone in source['skeleton'].find_all('bone'):
            cloned = bone.clone()
            _rename_bone_refs(cloned, mapping)
            # These are passive fittings. Source animation/volumes/actors stay out.
            for node in list(cloned.walk()):
                for animation in list(node.find_all('animation')):
                    node.remove(animation)
            assets.rewrite(cloned, info['model_path'], 'cargo'+str(i))
            wrapper.append(cloned)
        host['node'].append(wrapper)
        assets.sources[info['model_path']] = hashlib.sha256(resources.read(info['model_path'])).hexdigest()


def preview(resources, template, spec):
    _, cargo, mount = configuration(resources, spec)
    from .compiler import _load_entity, _crew_expansion, seat_markers
    info, _, root = _load_entity(resources, template)
    model = parse_model(resources.read(info['model_path']))
    mount = mount or model['anchor_name'] or ''
    _crew_expansion(resources, root)
    removed = strip_turret(resources, root, model, mount)
    hull = build_preview(resources, template, 'full', seats=seat_markers(resources, template))
    hull['meshes'] = [m for m in hull['meshes'] if m['bone'] not in removed['removed_bones']]
    hull['seats'] = [s for s in hull.get('seats', []) if s['place'] not in removed['removed_roles']]
    markers = [s for s in seat_markers(resources, template) if s['place'] not in removed['removed_roles']]
    hull['internal_crew'] = sum(s.get('group') == 'crew' and not s.get('bone') for s in markers)
    positions = [v for mesh in hull['meshes'] for v in mesh['positions']]
    hull['bounds'] = bounds_of(positions)
    hull['stats'] = {'meshes': len(hull['meshes']), 'triangles': sum(len(m['indices'])//3 for m in hull['meshes'])}
    hull['bones'] = [b for b in hull.get('bones', []) if b['name'] not in removed['removed_bones']]
    hull.update(anchor=[0, 0, 0], anchor_name='@full', mounts=[], joints=[], carried=False, seat=0)
    fittings = []
    for i, row in enumerate(cargo):
        data, center = fitting_frame(resources, row['asset'])
        frame = multiply(pose_matrix(row), center)
        for mesh in data['meshes']:
            mesh = dict(mesh, cargo_index=i, normals=[])
            mesh['positions'] = [v for j in range(0, len(mesh['positions']), 3)
                                 for v in point(frame, mesh['positions'][j:j+3])]
            fittings.append(mesh)
    return {'hull': hull, 'cargo': {'meshes': fittings, 'seats': [], 'mounts': [], 'joints': [],
            'diagnostics': [], 'anchor': [0, 0, 0], 'seat': 0, 'anchor_name': '@full'},
            'removed': removed, 'compiled': False, 'preview_valid': True, 'runtime_verified': False}
