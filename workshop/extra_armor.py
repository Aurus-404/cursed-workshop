"""Native passive armor fittings: visible geometry plus spatial protection volumes.

No blanket hull-thickness bonus. Every fitting retains source geometry/material/
thickness (including CastSteel). The native armor tag isolates it from hull damage.
"""
import hashlib
import math
import re

from .pivots import pose_matrix, pivot_of
from .syntax import parse, block, quote, bone_name
from .geometry import (parse_model, descendants, identity, inverse, multiply,
                       euler_matrix, translation, point, bounds_of, resolve_path,
                       read_mesh, free_mount_parent, free_mount_options, free_spot_parent)


class ArmorError(ValueError):
    pass


def _default_material(resources, root):
    """Follow native property inheritance instead of assigning vehicle steel blindly."""
    known = {'sand', 'wood', 'steel', 'stone', 'concrete', 'earth', 'glass', 'rubber', 'fabric'}
    cache = resources.__dict__.setdefault('_armor_material_includes', {})
    def visit(document, base='', chain=()):
        result = None
        for n in document.children:
            if n.head.casefold() == 'include' and n.args and (base or n.args[0].startswith('/')):
                path = resolve_path(base, n.args[0])
                if path in chain or len(chain) > 24: raise ArmorError('No se pudo resolver el blindaje nativo de esa pieza.')
                if path not in cache:
                    try: cache[path] = visit(parse(resources.read(path)), path, (*chain, path))
                    except (KeyError, OSError): cache[path] = None
                if cache[path] is not None: result = cache[path]
            elif n.head.casefold() == 'material' and n.args:
                result = n.args[0]
            elif n.head.casefold() == 'props':
                values = set(n.args) & known
                if values: result = next(iter(values)) if len(values) == 1 else ''
        return result
    return visit(root) or None


def parents(model):
    names = {free_mount_parent(model)}
    names.update(free_spot_parent(model, s[6:]) for s in free_mount_options(model) if s.startswith('@free:'))
    # Articulations need not be named turret/gun_rot in mod resources.
    names.update(n for n, b in model['bones'].items() if b['node'].args[:1] == ['revolute'])
    return [{'name': n, 'matrix': model['bones'][n]['world']} for n in sorted(names) if n in model['bones']]


def _volumes(resources, root):
    """Read volume-affecting macros only, including native positional args."""
    from .compiler import _include_defines, _macro_arguments
    def exists(path):
        if hasattr(resources,'exists'):return resources.exists(path)
        try:resources.read(path);return True
        except (KeyError,OSError):return False
    includes = tuple(n.args[0] for n in root.find_all('include') if n.args)
    cache = resources.__dict__.setdefault('_armor_defines', {})
    if includes not in cache:
        cache[includes] = _include_defines(resources, root)
    definitions = {**cache[includes], **{n.args[0]: n for n in root.find_all('define') if n.args}}
    if not definitions and exists('properties/vehicle.ext'):
        registry = parse(resources.read('properties/vehicle.ext'))
        definitions.update({n.args[0]: n for n in registry.find_all('define') if n.args})
    effects = {}
    def relevant(name, seen=()):
        if name in effects: return effects[name]
        if name in seen or name not in definitions: return False
        result = any(n.head.casefold() == 'volume' or (n.opener == '(' and relevant(n.head, (*seen, name)))
                     for n in definitions[name].children)
        effects[name] = result
        return result
    def expand(document, depth=0, stack=()):
        if depth > 24: raise ArmorError('No se pudo resolver el blindaje nativo de esa pieza.')
        for n in document.children:
            if n.head.casefold() == 'volume': yield n
            elif n.opener == '(' and relevant(n.head):
                if n.head in stack: raise ArmorError('No se pudo resolver el blindaje nativo de esa pieza.')
                definition = definitions[n.head]
                body = ''.join(v.render() for v in definition.items[definition.items.index(definition.atoms[1])+1:])
                values = _macro_arguments(n)
                if 'args' in n.args:
                    atoms = n.atoms[n.args.index('args')+2:]
                    values.update({str(i): a.render() for i, a in enumerate(atoms)})
                body = re.sub(r'%(\w+)', lambda m: values.get(m.group(1).casefold(), ''), body)
                yield from expand(parse(body), depth+1, (*stack, n.head))
    layers = []
    if exists('properties/vehicle.ext'):
        base_key = (includes, tuple(n.render() for n in root.find_all('define')))
        base_cache = resources.__dict__.setdefault('_armor_base_volumes', {})
        if base_key not in base_cache:
            base_cache[base_key] = list(expand(parse(resources.read('properties/vehicle.ext'))))
        layers.extend(base_cache[base_key])
    layers.extend(expand(root))
    result = {}
    for n in layers:
        if not n.args: continue
        target = result.setdefault(n.args[0], block('{volume '+quote(n.args[0])+'}'))
        for child in n.children:
            for previous in list(target.find_all(child.head)): target.remove(previous)
            target.append(child.clone())
    return result


def _contract(volume, default_material=None):
    thickness = volume.find('thickness') if volume else None
    try:
        values = [float(v) for n in thickness.walk() for v in n.args]
        if not values or any(not math.isfinite(v) or not 0 < v <= 10000 for v in values): return None
    except (AttributeError, ValueError): return None
    material = volume.find('material')
    name = material.args[0] if material and material.args else default_material
    if not name: return None
    result = block('{volume "source" {tags "armor"}}')
    result.append(thickness.clone());result.append(block('{material '+quote(name)+'}'))
    cast = volume.find('CastSteel')
    if cast:
        try:
            if len(cast.args) != 1 or not 0 < float(cast.args[0]) <= 1: return None
        except ValueError: return None
        result.append(cast.clone())
    # No source body/fire/component tags: a bag penetration must not damage the hull.
    result.append(block('{able {NoWoundDecal}}'))
    return result


def _candidate(resources, info, root, model, selected, name, kind, contracts):
    bones = descendants(model['bones'], selected) if selected else set(model['bones'])
    if not bones or len(bones) > 64 or any(len(model['bones'][n]['node'].args) > 1 for n in bones): return None
    if any(b['name'] in bones for b in model.get('duplicates', [])): return None
    volume_names = [n for n, v in model['volumes'].items() if v['bone'] in bones]
    if not volume_names or any(n not in contracts for n in volume_names): return None
    if not any(model['bones'][n]['assets'] for n in bones): return None
    # No weapons, crew locators, mobility or other systems come with an armor fitting.
    if selected and any(re.search(r'gun|visor|shooter|engine|wheel|track[lr]$|driver|charger', n, re.I) for n in bones): return None
    identifier = info['model_path']+'#'+(selected or '@full')
    materials = sorted({contracts[n].find('material').args[0] for n in volume_names})
    thickness = sorted({float(contracts[n].find('thickness').args[0]) for n in volume_names})
    return {'id': identifier, 'label': name, 'kind': kind, 'source': info.get('source_id', ''),
            'definition_path': info['definition_path'], 'model_path': info['model_path'], 'bone': selected,
            'volumes': volume_names, 'contracts': {n: contracts[n].render() for n in volume_names},
            'materials': materials, 'thickness': thickness,
            'cast_steel': any(contracts[n].find('CastSteel') is not None for n in volume_names)}


def _definition_items(resources, path, extensions):
    from .compiler import _load_entity
    if re.search(r'/(?:x|xx)/|_(?:x|xx)\.def$', path): return []
    sand = 'entity/construction/_military/fortifications/sandbag' in path
    vehicle = path.startswith('entity/-vehicle/') and '/workshop/' not in path
    if not sand and not vehicle: return []
    rows=[]
    if vehicle:
        text=resources.read_text(path)
        local='\n'.join(resources.read_text(p) for p in extensions.get(path.rsplit('/',1)[0], []))
        if not re.search(r'shield|sandbag|\("armor"|volume\s+"armor|wood_shield', text+'\n'+local, re.I): return []
    info, _, root = _load_entity(resources, path)
    if not info.get('model_path'): return []
    model = parse_model(resources.read(info['model_path']))
    effective = _volumes(resources, root)
    default = _default_material(resources, root)
    contracts = {n: c for n, v in effective.items() if (c := _contract(v, default)) is not None}
    candidates = []
    if sand:
        candidates.append((None, path.rsplit('/', 1)[-1][:-4], 'sandbags'))
    else:
        for name, bone in model['bones'].items():
            owned = [n for n, v in model['volumes'].items() if v['bone'] == name]
            if not owned or not bone['assets'] or any(n not in contracts for n in owned): continue
            tags = {v for n in owned for tag in effective[n].find_all('tags') for a in tag.args for v in a.split()}
            materials = {contracts[n].find('material').args[0] for n in owned}
            if not tags & {'armor', 'shield', 'gun_shield'} and not materials & {'sand', 'wood'}: continue
            kind = ('tracks' if any(contracts[n].find('CastSteel') for n in owned) or re.search('spare|track|trak', name+' '.join(bone['assets']), re.I)
                    else 'sandbags' if 'sand' in materials else 'wood' if 'wood' in materials else 'plates')
            candidates.append((name, resources.label_for(path.rsplit('/', 1)[-1][:-4])+' · '+name, kind))
    for selected, label, kind in candidates:
        item = _candidate(resources, info, root, model, selected, label, kind, contracts)
        if item: rows.append(item)
    return rows


def catalog(resources):
    if hasattr(resources, '_armor_catalog'): return resources._armor_catalog
    rows, seen = [], set()
    extensions = {}
    for path in resources.entries:
        if path.startswith('entity/-vehicle/') and path.endswith('.ext'):
            extensions.setdefault(path.rsplit('/', 1)[0], []).append(path)
    from .cancellation import checkpoint, report_progress
    files=[p for p in sorted(resources.entries) if p.endswith('.def')]
    for ordinal,path in enumerate(files):
        checkpoint()
        if ordinal%20==0:report_progress(ordinal,len(files))
        try:
            for item in _definition_items(resources,path,extensions):
                if item['id'] not in seen:rows.append(item);seen.add(item['id'])
        except (ValueError, KeyError, OSError): continue
    checkpoint();report_progress(len(files),len(files))
    resources._armor_catalog = rows
    return rows


def resolve_fitting(resources, identifier):
    """Validate an already selected fitting without rescanning every vehicle.

    The exact same extraction/protection rules serve both routes. A model may
    be shared with definitions in other folders; retain full discovery as a
    fallback for that uncommon layout. No asset supplied by a recipe is trusted.
    """
    if not isinstance(identifier,str):return None
    if hasattr(resources,'_armor_catalog'):
        return next((p for p in resources._armor_catalog if p['id']==identifier),None)
    cached=resources.__dict__.setdefault('_armor_items',{})
    if identifier in cached:return cached[identifier]
    if '#' not in identifier:return None
    model, _=identifier.rsplit('#',1);folder=model.rsplit('/',1)[0]
    paths=[p for p in resources.entries if p.rsplit('/',1)[0]==folder]
    extensions={folder:[p for p in paths if p.endswith('.ext')]}
    for path in sorted(p for p in paths if p.endswith('.def')):
        try:
            for item in _definition_items(resources,path,extensions):
                cached[item['id']]=item
        except (ValueError,KeyError,OSError):continue
    if identifier not in cached:
        for item in catalog(resources):cached[item['id']]=item
    return cached.get(identifier)


def public_catalog(resources):
    return {'items': [{k: v for k, v in p.items() if k != 'contracts'} for p in catalog(resources)], 'runtime_verified': False}


def configuration(resources, value):
    if not isinstance(value, list):
        raise ArmorError('Elegí una pieza de blindaje nativa disponible.')
    rows = []
    for row in value:
        if not isinstance(row, dict) or resolve_fitting(resources,row.get('asset')) is None:
            raise ArmorError('Elegí una pieza de blindaje nativa disponible.')
        parent = row.get('parent')
        if not isinstance(parent, str) or not parent or len(parent) > 160:
            raise ArmorError('Elegí un punto de fijación válido para el blindaje.')
        normalized = {'asset': row['asset'], 'parent': parent}
        if 'serial' in row:
            if type(row['serial']) is not int or not 0 <= row['serial'] <= 100000:
                raise ArmorError('Elegí una pieza de blindaje nativa disponible.')
            normalized['serial'] = row['serial']
        pivot_of(row)
        if 'pivot' in row:normalized['pivot'] = list(row['pivot'])
        for key, bound in (('position', 10000), ('rotation', 360)):
            vector = row.get(key, [0, 0, 0])
            if not isinstance(vector, list) or len(vector) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > bound for v in vector):
                raise ArmorError('La posición o el giro del blindaje no es válido.')
            normalized[key] = vector
        rows.append((normalized, resolve_fitting(resources,row['asset'])))
    return rows


def fitting(resources, item):
    model = parse_model(resources.read(item['model_path']))
    names = descendants(model['bones'], item['bone']) if item['bone'] else set(model['bones'])
    meshes = []
    origin = inverse(model['bones'][item['bone']]['world']) if item['bone'] else identity()
    for name in model['bones']:
        if name not in names: continue
        bone = model['bones'][name];frame = multiply(origin, bone['world'])
        for ref in bone['assets']:
            path = resolve_path(item['model_path'], ref);mesh = read_mesh(resources.read(path))
            for sub in mesh['submeshes']: sub['mtl'] = resolve_path(path, sub['material']) if sub['material'] else None
            positions = [v for i in range(0, len(mesh['positions']), 3) for v in point(frame, mesh['positions'][i:i+3])]
            meshes.append({**mesh, 'positions': positions, 'normals': [], 'bone': name, 'path': path})
    if not meshes: raise ArmorError('No se pudo cargar el modelo del blindaje.')
    box = bounds_of([v for m in meshes for v in m['positions']]);lo, hi = box['min'], box['max']
    center = translation([-(lo[0]+hi[0])/2, -(lo[1]+hi[1])/2, -(lo[2]+hi[2])/2])
    for mesh in meshes:
        mesh['positions'] = [v for i in range(0, len(mesh['positions']), 3) for v in point(center, mesh['positions'][i:i+3])]
    return model, names, multiply(center, origin), meshes


def preview(resources, identifier):
    item = resolve_fitting(resources,identifier)
    if item is None: raise ArmorError('Elegí una pieza de blindaje nativa disponible.')
    _, _, _, meshes = fitting(resources, item)
    return {'meshes': meshes, 'item': {k: v for k, v in item.items() if k != 'contracts'},
            'preview_valid': True, 'runtime_verified': False}


def apply(resources, root, model, value, assets, diagnostics):
    if value is None: return None
    from .compiler import _set_matrix, _rename_bone_refs
    rows = configuration(resources, value)
    final = parse_model(model['document'].render());model.update(final)
    hosts = {p['name'] for p in parents(final)}
    result = []
    reserved = set(final['bones']) | set(final['volumes'])
    claimed = {row['serial'] for row,_ in rows if 'serial' in row}
    used = set()
    for i, (row, item) in enumerate(rows):
        if row['parent'] not in hosts:
            raise ArmorError('El punto de fijación del blindaje ya no existe en este vehículo.')
        source, names, centered, _ = fitting(resources, item)
        serial = row.get('serial')
        if serial is None or serial in used or any(n.startswith('mw_armor_'+str(serial)+'_') for n in reserved):
            serial = 0
            while serial in claimed or serial in used or any(n.startswith('mw_armor_'+str(serial)+'_') for n in reserved):serial += 1
        used.add(serial)
        prefix = 'mw_armor_'+str(serial)+'_'
        mapping = {n: prefix+'b'+str(j) for j, n in enumerate(sorted(names))}
        volumes = {n: prefix+'v'+str(j) for j, n in enumerate(item['volumes'])}
        wrapper = block('{bone '+quote(prefix+'root')+'}')
        _set_matrix(wrapper, pose_matrix(row))
        owner='armor'+str(i)
        relative=multiply(final['bones'][row['parent']]['world'],multiply(pose_matrix(row),centered))
        assets.preserve_mesh_faces(owner,relative)
        roots = [n for n in names if source['bones'][n]['parent'] not in names]
        for n in sorted(roots):
            original = source['bones'][n];node = original['node'].clone()
            _set_matrix(node, multiply(centered, original['world']))
            _rename_bone_refs(node, mapping)
            for child in list(node.walk()):
                for p in list(child.children):
                    if p.head.casefold() in ('parameters', 'animation', 'limits', 'speed', 'speed2'): child.remove(p)
            assets.rewrite(node, item['model_path'], owner);wrapper.append(node)
        final['bones'][row['parent']]['node'].append(wrapper)
        for n in item['volumes']:
            node = source['volumes'][n]['node'].clone();node.atoms[1].set(volumes[n])
            _rename_bone_refs(node, mapping);assets.rewrite(node, item['model_path'], 'armor'+str(i))
            final['document'].append(node)
            behavior = block(item['contracts'][n]);behavior.atoms[1].set(volumes[n]);root.append(behavior)
        for path in (item['model_path'], item['definition_path'], 'properties/vehicle.ext'):
            if resources.exists(path):assets.sources[path] = hashlib.sha256(resources.read(path)).hexdigest()
        result.append({**row, 'bones': [prefix+'root',*mapping.values()], 'volumes': volumes, 'materials': item['materials'], 'thickness': item['thickness'],
                       'cast_steel': item['cast_steel'], 'passive': True,
                       'contract_sha256': hashlib.sha256(str(sorted(item['contracts'].items())).encode()).hexdigest()})
    if rows: diagnostics.append({'code': 'extra_armor_runtime_gate', 'level': 'warning',
        'message': 'Native spatial armor volumes exported; penetration, damage and persistence require battle acceptance.'})
    return result
