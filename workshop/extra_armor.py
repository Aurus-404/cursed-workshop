"""Native passive armor fittings: visible geometry plus spatial protection volumes.

No blanket hull-thickness bonus. Every fitting retains source geometry/material/
thickness (including CastSteel). The native armor tag isolates it from hull damage.
"""
import hashlib
import json
import math
import re
from array import array
from collections import OrderedDict

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
        if not re.search(r'shield|sandbag|\("armor"|volume\s+"armor|wood_shield|"mantlet', text+'\n'+local, re.I): return []
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
    if vehicle:
        mantlet = _mantlet_candidate(resources, info, root, model, effective, contracts)
        if mantlet: rows.append(mantlet)
    return rows


def elevation_joint(root, model):
    """The joint that raises the main gun: the nearest one above it turning
    about a horizontal axis. Its shell is the gun's mantlet."""
    from .gun_assembly import _primary_place
    from .weapon_binding import weapon_link
    primary = _primary_place(root)
    place = next((p for w in root.find_all('Weaponry') for p in w.find_all('place') if p.args and p.args[0] == primary), None)
    if place is None: return None
    bones = model['bones']
    name = weapon_link(place, model)
    while name in bones:
        bone = bones[name]
        if bone['parent'] is not None and bone['node'].args[:1] == ['revolute'] and abs(bone['world'][2][2]) < 0.5:
            return name
        name = bone['parent']
    return None


def _mantlet_candidate(resources, info, root, model, effective, contracts):
    """The main gun's mantlet: the elevation joint's own shell and armor,
    without the barrel, breech or coaxial weapons hanging from it. It is
    fitted as a fixed piece, usually on the new vehicle's own gun elevation."""
    bones = model['bones']
    name = elevation_joint(root, model)
    if name is None or not bones[name]['assets'] or any(b['name'] == name for b in model.get('duplicates', [])): return None
    def internal(volume):  # breech and barrel volumes: hidden, not armor
        tags = {v for tag in volume.find_all('tags') for a in tag.args for v in a.split()}
        return 'no_target' in tags or re.search(r'contact\s+0', volume.render(), re.I)
    owned = [n for n, v in model['volumes'].items()
             if v['bone'] == name and n in contracts and n in effective and not internal(effective[n])]
    if not owned: return None
    vehicle = resources.label_for(info['definition_path'].rsplit('/', 1)[-1][:-4])
    item = {'id': info['model_path']+'#'+name+'@mantlet', 'label': vehicle+' · mantlet', 'kind': 'mantlet',
            'single': True, 'source': info.get('source_id', ''), 'definition_path': info['definition_path'],
            'model_path': info['model_path'], 'bone': name, 'volumes': owned,
            'contracts': {n: contracts[n].render() for n in owned},
            'materials': sorted({contracts[n].find('material').args[0] for n in owned}),
            'thickness': sorted({float(contracts[n].find('thickness').args[0]) for n in owned}),
            'cast_steel': any(contracts[n].find('CastSteel') is not None for n in owned)}
    try:
        # A shell that cannot be told from its breech or cradle stays out of
        # the list (still resolvable for a recipe that already uses it).
        item['clean'] = _shell_faces(resources, item, model)[1]
    except (KeyError, OSError, ValueError):
        item['clean'] = False
    return item


_NATIONS =('germany', 'ussr', 'eng', 'usa', 'japan', 'finland', 'fra', 'swe', 'ita', 'hun', 'pol', 'rom')
_WEIGHT = {'other': 0.05, 'wood': 0.2, 'sandbags': 0.3, 'tracks': 0.4}


def _category(item):
    """What the piece is for, from its kind and the modeler's part name."""
    if item['kind'] in ('mantlet', 'tracks', 'sandbags', 'wood'): return item['kind']
    part = (item.get('bone') or '').casefold()
    if re.search(r'lifering|basket|box|crate|external|rocket|towing|exha?ust|roller|magazine|ammo|door|grill|jerry|tool|antenna|light', part):
        return 'other'
    if re.search(r'shield_?(?:[lr]|left|right)\d*$|schur|skirt|side', part): return 'skirts'
    if re.search(r'shield', part): return 'shields'
    return 'plates'


def _annotate(resources, rows):
    """Nation, purpose, usefulness and identical-piece groups for the list.

    Every piece stays resolvable (saved recipes keep their ids); the list shows
    one piece per group of identical geometry and armor, the most useful first.
    """
    # Thousands of pieces: holding every model and mesh until the end took over
    # a gigabyte. Pieces come sorted by vehicle, so the last few are the ones
    # asked for again; positions are kept packed (8 bytes each, not a float object).
    meshes, models = OrderedDict(), OrderedDict()
    def kept(cache, key, limit, make):
        if key in cache: cache.move_to_end(key)
        else:
            cache[key] = make()
            while len(cache) > limit: cache.popitem(last=False)
        return cache[key]
    def shape(item):
        model = kept(models, item['model_path'], 2, lambda: parse_model(resources.read(item['model_path'])))
        names = {item['bone']} if item.get('single') else (descendants(model['bones'], item['bone']) if item['bone'] else set(model['bones']))
        origin = inverse(model['bones'][item['bone']]['world']) if item['bone'] else identity()
        low, high, parts = None, None, []
        for name in sorted(names):
            bone = model['bones'][name]
            frame = multiply(origin, bone['world'])
            for ref in bone['assets']:
                path = resolve_path(item['model_path'], ref)
                def load(path=path):
                    data = resources.read(path)
                    return hashlib.sha1(data).hexdigest(), array('d', read_mesh(data)['positions'])
                digest, positions = kept(meshes, path, 64, load)
                parts.append(digest + ':' + ','.join('%.2f' % v for row in frame for v in row))
                for i in range(0, len(positions), 3):
                    p = point(frame, positions[i:i+3])
                    if low is None: low, high = list(p), list(p)
                    else:
                        for k in range(3):
                            if p[k] < low[k]: low[k] = p[k]
                            elif p[k] > high[k]: high[k] = p[k]
        return (None if low is None else [high[k] - low[k] for k in range(3)]), parts
    for item in rows:
        segment = item['definition_path'].split('/')[2].lstrip('+') if item['definition_path'].count('/') > 2 else ''
        item['nation'] = segment if segment in _NATIONS else 'other'
        # Test vehicles, ships, aircraft and trains are no tank armor (a
        # destroyer's funnels topped the list); their pieces stay resolvable.
        folders = item['definition_path'].casefold().split('/')
        item['listed'] = (segment != 'test' and not set(folders) & {'test', 'marine', 'airborne', 'train', 'trains', 'railway'}
                          and item.get('clean', True))
        item['category'] = _category(item)
        try:
            extent, parts = shape(item)
        except (KeyError, OSError, ValueError):
            extent, parts = None, [item['id']]
        if extent:
            size = sorted(extent)
            area = size[1] * size[2]
        else:
            area = 0.0
        item['score'] = round(max(item['thickness'] or [0]) * area * _WEIGHT.get(item['category'], 1.0), 3)
        item['group'] = hashlib.sha1(json.dumps([sorted(parts), sorted(item['contracts'].values()), item['category']]).encode()).hexdigest()[:16]
    groups = {}
    for item in rows:
        groups.setdefault(item['group'], []).append(item)
    for members in groups.values():
        listed = [m for m in members if m['listed']]
        lead = min(listed or members, key=lambda m: (m['source'] != 'vanilla', len(m['label']), m['label']))
        for m in members:
            m['primary'] = m is lead
            m['variants'] = len(listed) or 1
    return rows


def catalog(resources):
    from .catalog_cache import catalog_source
    resources = catalog_source(resources)
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
    resources._armor_catalog = _annotate(resources, rows)
    return rows


def resolve_fitting(resources, identifier):
    """Validate an already selected fitting without rescanning every vehicle.

    The exact same extraction/protection rules serve both routes. A model may
    be shared with definitions in other folders; retain full discovery as a
    fallback for that uncommon layout. No asset supplied by a recipe is trusted.
    """
    if not isinstance(identifier,str):return None
    from .catalog_cache import catalog_source
    resources=catalog_source(resources)
    if hasattr(resources,'_armor_catalog'):
        if not hasattr(resources,'_armor_by_id'):
            resources._armor_by_id={item['id']:item for item in resources._armor_catalog}
        return resources._armor_by_id.get(identifier)
    cached=resources.__dict__.setdefault('_armor_items',{})
    if identifier in cached:return cached[identifier]
    if '#' not in identifier:return None
    model, _=identifier.rsplit('#',1);folder=model.rsplit('/',1)[0].casefold()
    # Ids keep the model's own spelling and folders are not always spelled alike
    # (US_M4A3E8_ThunderVII): a mismatch here read the whole game to open a vehicle.
    paths=[p for p in resources.entries if p.rsplit('/',1)[0].casefold()==folder]
    extensions={path.rsplit('/',1)[0]:[p for p in paths if p.endswith('.ext')] for path in paths}
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
    from . import custom_armor
    for row in value:
        made=isinstance(row,dict) and row.get('asset')==custom_armor.ASSET
        item=custom_armor.item(custom_armor.plate(row,ArmorError)) if made else resolve_fitting(resources,row.get('asset')) if isinstance(row,dict) else None
        if item is None:
            raise ArmorError('Elegí una pieza de blindaje nativa disponible.')
        parent = row.get('parent')
        if not isinstance(parent, str) or not parent or len(parent) > 160:
            raise ArmorError('Elegí un punto de fijación válido para el blindaje.')
        normalized = {'asset': row['asset'], 'parent': parent}
        if made:normalized['plate'] = item['plate']
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
        rows.append((normalized, item))
    return rows


def fitting(resources, item):
    model = parse_model(resources.read(item['model_path']))
    # A mantlet is its elevation joint's own shell: never the barrel below it.
    names = {item['bone']} if item.get('single') else (descendants(model['bones'], item['bone']) if item['bone'] else set(model['bones']))
    meshes = []
    origin = inverse(model['bones'][item['bone']]['world']) if item['bone'] else identity()
    shells = _shell_faces(resources, item, model)[0]
    for name in model['bones']:
        if name not in names: continue
        bone = model['bones'][name];frame = multiply(origin, bone['world'])
        for ref in bone['assets']:
            path = resolve_path(item['model_path'], ref);mesh = read_mesh(resources.read(path))
            for sub in mesh['submeshes']: sub['mtl'] = resolve_path(path, sub['material']) if sub['material'] else None
            positions = [v for i in range(0, len(mesh['positions']), 3) for v in point(frame, mesh['positions'][i:i+3])]
            indices = mesh['indices']
            if path in shells:  # the breech sharing the shell's mesh is hidden, as in the export
                keep = shells[path]
                indices = [index for t in range(len(indices)//3)
                           for index in (indices[3*t:3*t+3] if keep[t] else [indices[3*t]]*3)]
            meshes.append({**mesh, 'positions': positions, 'indices': indices, 'normals': [], 'bone': name, 'path': path})
    if not meshes: raise ArmorError('No se pudo cargar el modelo del blindaje.')
    drawn = {(m['path'], i) for m in meshes for t in range(len(m['indices'])//3)
             if len(set(m['indices'][3*t:3*t+3])) == 3 for i in m['indices'][3*t:3*t+3]}
    box = bounds_of([v for m in meshes for i in range(len(m['positions'])//3) if (m['path'], i) in drawn
                     for v in m['positions'][3*i:3*i+3]] or [v for m in meshes for v in m['positions']])
    lo, hi = box['min'], box['max']
    center = translation([-(lo[0]+hi[0])/2, -(lo[1]+hi[1])/2, -(lo[2]+hi[2])/2])
    for mesh in meshes:
        mesh['positions'] = [v for i in range(0, len(mesh['positions']), 3) for v in point(center, mesh['positions'][i:i+3])]
    model['shell_faces'] = shells
    return model, names, multiply(center, origin), meshes


def preview(resources, identifier):
    from . import custom_armor
    if identifier == custom_armor.ASSET:return custom_armor.preview()
    item = resolve_fitting(resources,identifier)
    if item is None: raise ArmorError('Elegí una pieza de blindaje nativa disponible.')
    model, _, _, meshes = fitting(resources, item)
    # The piece is drawn in its own bone's axes, which modelers often turn
    # (a gun_rot swaps them). This is how that bone sat on its own vehicle, so
    # a new piece can start upright, as it was there, on any attachment bone.
    native = model['bones'][item['bone']]['world'] if item['bone'] else identity()
    return {'meshes': meshes, 'item': {k: v for k, v in item.items() if k != 'contracts'},
            'native_frame': native, 'preview_valid': True, 'runtime_verified': False}


def _shell_faces(resources, item, model):
    """{mesh path: keep(triangle)} for a mantlet whose shell shares one mesh with
    the breech, cradle or barrel sleeve (IS-4 gun_rot.ply, Pz.Bfw VI P). The
    modeler's separate pieces are merged into one file: they are split into
    connected pieces (shared vertices or positions), and each whole piece stays
    when most of it lies in the mantlet's armor volumes (with a margin: hitboxes
    are simplified) and its centre in no internal one (breech, barrel). Cutting
    single triangles by volume left jagged plates and stray slivers.
    Returns ({path: keep}, clean): clean is False when a sizeable piece is half
    in and half out, so the shell cannot be told from the rest."""
    if not item.get('single'): return {}, True, None
    from .armor_view import read_volume_mesh, _box, _cylinder
    from .geometry import node_matrix
    bone = item['bone']
    def frame(name):
        node = model['volumes'][name]['node']
        shape = next(n for n in node.children if n.head.casefold() in ('polyhedron', 'box', 'cylinder'))
        kind = shape.head.casefold()
        if kind == 'polyhedron':
            verts = read_volume_mesh(resources.read(resolve_path(item['model_path'], shape.args[0])))[0]
        elif kind == 'box':
            verts = _box([float(v) for v in shape.args[:3]])[0]
        else:
            verts = _cylinder(float(shape.args[0]), float(shape.args[1]))[0]
        return inverse(node_matrix(node)), [min(v[k] for v in verts) for k in range(3)], [max(v[k] for v in verts) for k in range(3)]
    def boxes(names):
        found = []
        for name in names:
            try: found.append(frame(name))
            except (StopIteration, KeyError, OSError, ValueError, IndexError): pass
        return found
    armor = boxes([n for n in item['volumes'] if n in model['volumes']])
    internal = boxes([n for n, v in model['volumes'].items() if v['bone'] == bone and n not in item['volumes']])
    if not armor: return {}, True, None
    def inside(found, p, grow):
        # The margin follows the volume's largest size: a mantlet hitbox is a
        # thin plate while its shell (Tiger sleeve) reaches well beyond it.
        for to_local, low, high in found:
            q = point(to_local, p)
            pad = grow*max(high[k]-low[k] for k in range(3)) + (0.5 if grow else 0)
            if all(low[k] - pad <= q[k] <= high[k] + pad for k in range(3)):
                return True
        return False
    result, clean, kept_total, all_total, kept_area = {}, True, 0, 0, 0.0
    # The armor's own face: what a real shell must roughly cover (a lone collar
    # ring or a few bolts left behind by a cradle do not).
    face = sum(sorted(high[k]-low[k] for k in range(3))[1]*sorted(high[k]-low[k] for k in range(3))[2] for _, low, high in armor)
    for ref in model['bones'][bone]['assets']:
        path = resolve_path(item['model_path'], ref)
        try: mesh = read_mesh(resources.read(path))
        except (KeyError, OSError, ValueError): continue
        positions, indices = mesh['positions'], mesh['indices']
        count = len(positions)//3
        # Union-find over vertices: shared index or the same position (UV seams
        # duplicate vertices inside one modelled piece).
        parent = list(range(count))
        def find(v):
            while parent[v] != v:
                parent[v] = parent[parent[v]]; v = parent[v]
            return v
        def union(a, b):
            a, b = find(a), find(b)
            if a != b: parent[b] = a
        seen = {}
        for v in range(count):
            key = tuple(round(x, 3) for x in positions[3*v:3*v+3])
            if key in seen: union(seen[key], v)
            else: seen[key] = v
        for t in range(len(indices)//3):
            a, b, c = indices[3*t:3*t+3]; union(a, b); union(b, c)
        pieces = {}
        for t in range(len(indices)//3):
            pieces.setdefault(find(indices[3*t]), []).append(t)
        triangles = len(indices)//3
        keep = [False]*triangles
        for members in pieces.values():
            vertices = {i for t in members for i in indices[3*t:3*t+3]}
            points = [positions[3*v:3*v+3] for v in vertices]
            share = sum(inside(armor, p, 0.25) for p in points)/len(points)
            # Mostly inside the breech or barrel volumes: that piece. A barrel
            # cylinder runs through the mantlet's centre, so the centre alone
            # proves nothing.
            inner = sum(inside(internal, p, 0) for p in points)/len(points)
            # A small loose piece (coaxial MG, sight rod, barrel stub, cradle
            # bracket) must sit on the hitbox itself; only a large piece (the
            # Tiger's sleeve) may reach out through the wide margin.
            tight = sum(inside(armor, p, 0.05) for p in points)/len(points)
            size = sorted(max(p[k] for p in points) - min(p[k] for p in points) for k in range(3))
            rod = size[2] >= 4*max(size[1], 1e-6)  # a coaxial or sight barrel poking out
            if inner >= 0.5 or (len(members) < 0.2*triangles and (tight < 0.3 or rod)):
                chosen = []
            elif share >= 0.6:
                chosen = members
            elif share > 0.2:
                # One piece holds shell and breech (Sherman, Panther): only
                # triangles wholly in an internal volume go. That cut runs
                # inside the turret, never across the mantlet's face.
                chosen = [t for t in members if not all(inside(internal, positions[3*i:3*i+3], 0) for i in indices[3*t:3*t+3])]
            else:
                chosen = []  # outside the armor: sleeve, cradle, stray slivers
            for t in chosen: keep[t] = True
            if chosen:
                for t in chosen:
                    a, b, c = (positions[3*i:3*i+3] for i in indices[3*t:3*t+3])
                    u, w = [b[k]-a[k] for k in range(3)], [c[k]-a[k] for k in range(3)]
                    cross = (u[1]*w[2]-u[2]*w[1], u[2]*w[0]-u[0]*w[2], u[0]*w[1]-u[1]*w[0])
                    kept_area += math.sqrt(sum(x*x for x in cross))/2
        kept_total += sum(keep); all_total += triangles
        if not all(keep):
            result[path] = keep
    if all_total and (kept_total == 0 or (face > 0 and kept_area < SHELL_COVERAGE*face)):
        clean = False
    return result, clean, (kept_area/face if face else None)


SHELL_COVERAGE = 0.5  # kept surface / armor face: measured on the catalog's mantlets


def _static_piece(node, names):
    """A fitting is fixed armor: no joint, and only the bones it is made of
    (a mantlet's elevation joint loses its barrel and its revolute axis)."""
    for bone in list(node.walk('bone')):
        for child in list(bone.children):
            if child.head.casefold() == 'bone' and bone_name(child) not in names:
                bone.remove(child)
        if len(bone.atoms) > 2 and bone.atoms[1].value.casefold() == 'revolute':
            bone.items.remove(bone.atoms[1])


def apply(resources, root, model, value, assets, diagnostics):
    if value is None: return None
    from .compiler import _set_matrix, _rename_bone_refs
    from . import custom_armor
    from .geometry import linear_determinant
    rows = configuration(resources, value)
    final = parse_model(model['document'].render());model.update(final)
    hosts = {p['name'] for p in parents(final)}
    result = []
    reserved = set(final['bones']) | set(final['volumes'])
    claimed = {row['serial'] for row,_ in rows if 'serial' in row}
    used = set()
    fittings = {}
    for i, (row, item) in enumerate(rows):
        if row['parent'] not in hosts:
            raise ArmorError('El punto de fijación del blindaje ya no existe en este vehículo.')
        if not item.get('custom'):
            key = (item['model_path'], item['bone'])
            if key not in fittings:
                fittings[key] = fitting(resources, item)[:3]
            source, names, centered = fittings[key]
        serial = row.get('serial')
        if serial is None or serial in used or any(n.startswith('mw_armor_'+str(serial)+'_') for n in reserved):
            serial = 0
            while serial in claimed or serial in used or any(n.startswith('mw_armor_'+str(serial)+'_') for n in reserved):serial += 1
        used.add(serial)
        prefix = 'mw_armor_'+str(serial)+'_'
        if item.get('custom'):
            # Our own mesh: like the smoke tubes, its visible side follows the host's parity.
            host = final['bones'][row['parent']]
            try:
                bones, made = custom_armor.attach(root, final, host['node'], prefix, pose_matrix(row), item['plate'], assets.files,
                                                  linear_determinant(host['world']) > 0, _set_matrix)
            except ValueError as error:
                raise ArmorError(str(error)) from error
            result.append({**row, 'bones': bones, 'volumes': made, 'materials': item['materials'], 'thickness': item['thickness'],
                           'cast_steel': False, 'passive': True, 'custom': True,
                           'contract_sha256': hashlib.sha256(custom_armor.contract(item['plate']).encode()).hexdigest()})
            continue
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
            _static_piece(node, names)
            _set_matrix(node, multiply(centered, original['world']))
            _rename_bone_refs(node, mapping)
            for path, keep in source.get('shell_faces', {}).items():
                assets.mesh_filters[(path, owner)] = keep
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
