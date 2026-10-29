"""Stock inventory mounts, discovered from GEM data; never installs anything.

Only the existing commander operates a mount. Battle animation acceptance is
deliberately separate from the structural compiler checks below.
"""
from __future__ import annotations

import math
import posixpath
import re
from collections import defaultdict

from .pivots import pose_matrix, pivot_of, rebase_joint
from .syntax import block, parse, quote
from .geometry import (parse_model, descendants, multiply, inverse, identity,
                       euler_matrix, point, build_preview, resolve_path)


def _error(message):
    from .compiler import CompileError
    raise CompileError(message)


def _stock(index):
    return index.base_game()


def _blocked(base):
    root = parse(base.read('set/registry/dlc.reg')).find('dlc')
    if root is None:
        _error('No se pudo verificar el registro de DLC del juego base.')
    available = getattr(base, '_available_dlc', frozenset())
    return {node.head.casefold() for node in root.children if node.args and not node.children
            and not (set(' '.join(node.args).split()) & available)}


def weapon_catalog(index, *, item=None):
    """Official mountable weapons, including DLC available in the last game run."""
    base = index.mountable_game()
    cached = index.__dict__.get('_commander_weapons')
    if cached is not None:
        return [w for w in cached if item is None or w['id']==item]
    partial=index.__dict__.setdefault('_commander_weapon_items',{})
    if item is not None and not isinstance(item,str):return []
    if item in partial:return partial[item]
    blocked = _blocked(base)
    documents, patterns, items = {}, defaultdict(list), {}
    for key in base.entries:
        if not key.startswith('set/stuff/'):
            continue
        name = posixpath.basename(key)
        if '.' not in name or name.endswith('.pattern'):
            try:
                documents[key] = parse(base.read(key))
            except (ValueError, KeyError, OSError):
                continue  # a mod's unreadable item is not offered; the rest still are
            if name.endswith('.pattern'):
                patterns[name[:-8]].append(key)
            else:
                items[name] = key
    cache = {}

    def resolve(path, seen=()):
        if path in cache:
            return cache[path]
        if path in seen or len(seen) > 24:
            raise ValueError('Recursive stock item inheritance')
        doc = documents[path]
        tags, values, chain = set(), {}, []
        for node in doc.walk('from'):
            if not node.args:
                continue
            name = node.args[0]
            if name.startswith('pattern '):
                name = name[8:]
                choices = patterns.get(name, [])
                near = posixpath.join(posixpath.dirname(path), name + '.pattern')
                parent = near if near in choices else choices[0] if len(choices) == 1 else None
            else:
                parent = items.get(name)
            if not parent:
                raise ValueError('Unresolved stock item inheritance: ' + name)
            inherited, inherited_values, inherited_chain = resolve(parent, (*seen, path))
            tags.update(inherited)
            values.update(inherited_values)
            chain.extend(inherited_chain)
        for node in doc.walk('tag'):
            for tag in ' '.join(node.args).split():
                if tag.startswith('-'):
                    tags.discard(tag[1:])
                else:
                    tags.add(tag)
        for field in ('entity', 'filling'):
            nodes = list(doc.walk(field))
            if nodes:
                values[field] = nodes[-1].args
        result = tags, values, list(dict.fromkeys([*chain, path]))
        cache[path] = result
        return result

    rows = []
    for name, path in sorted(items.items()):
        if item is not None and name!=item:continue
        try:
            tags, values, chain = resolve(path)
        except ValueError:
            continue  # Unknown contracts are not selectable.
        entity = (values.get('entity') or [name])[0]
        if (not {'weapon', 'mountable'} <= tags or name in blocked or entity.casefold() in blocked
                or any(posixpath.basename(p).casefold() in blocked for p in chain)):
            continue
        try:
            info = base.resolve_template(entity)
        except (ValueError, KeyError, OSError):
            continue
        # An active mod can replace a stock weapon or any inherited pattern.
        # Do not silently offer a mod's version as an official weapon.
        dependencies = [*chain, info['definition_path'], info['model_path']]
        if any(index.read(p) != base.read(p) for p in dependencies):
            continue
        filling = values.get('filling') or []
        family = (filling[0].removeprefix('ammo ').split() if filling else [])
        ammunition, broader = [], []
        for ammo_path in base.entries:
            if not ammo_path.startswith('set/stuff/') or not ammo_path.endswith('.ammo'):
                continue
            tokens = posixpath.basename(ammo_path)[:-5].split('.')
            if not family:
                continue
            if tokens[:len(family)] != family:
                # MACE's Type 92 HMG fills "type_92_hmg_magazine clip" from
                # type_92_hmg_magazine.ammo: a file naming the family's start.
                if family[:len(tokens)] == tokens and index.read(ammo_path) == base.read(ammo_path):
                    broader.append((-len(tokens), tokens, ammo_path))
                continue
            if index.read(ammo_path) != base.read(ammo_path):
                continue
            ammo_doc = parse(base.read(ammo_path))
            ammo_entity = next(ammo_doc.walk('entity'), None)
            if ammo_entity and ammo_entity.args and ammo_entity.args[0].casefold() in blocked:
                continue
            ammunition.append((len(tokens), tokens, ammo_path))
        ammunition.sort()
        if not ammunition and broader:
            broader.sort()  # the most specific file first
            ammunition = [broader[0]]
        ammo_item = 'ammo ' + ' '.join(ammunition[0][1]) if ammunition else None
        if not ammo_item:
            continue
        rows.append({'id': name, 'label': base.label_for(entity), 'entity': entity,
                     'path': path, 'dependencies': chain, 'filling': filling,
                     'ammo_item': ammo_item, 'ammo_rounds': 3 * int(filling[1]),
                     'tags': sorted(tags)})
    # Effective-stack filtering belongs to this index, not the stock cache.
    with index._base_lock:
        if index.__dict__.get('_mountable_game_view') is base:
            if item is None:index._commander_weapons = rows
            else:partial[item]=rows
            return rows
    # A background reader must not republish an old list after a new game scan
    # revoked a DLC while it was resolving the catalog.
    return weapon_catalog(index, item=item)


def _expand_visible(index, root):
    """Expose native visibility macros, then use the existing crew expander."""
    from .compiler import _include_defines, _macro_arguments, _crew_expansion
    definitions = _include_defines(index, root)
    for path in ('properties/tank_crew.ext', 'properties/car_crew.ext'):
        if index.exists(path):
            for node in parse(index.read(path)).find_all('define'):
                if node.args:
                    definitions.setdefault(node.args[0], node)
    for call in list(root.children):
        definition = definitions.get(call.head) if call.opener == '(' else None
        if definition is None or 'crew_open' not in definition.render():
            continue
        arguments = _macro_arguments(call)
        body = ''.join(item.render() for item in definition.items[definition.items.index(definition.atoms[1])+1:])
        body = re.sub(r'%(\w+)', lambda m: arguments.get(m.group(1).casefold(), ''), body)
        root.items[root.items.index(call):root.items.index(call)+1] = parse(body).items
    _crew_expansion(index, root)


def _commander_pose(root):
    links = [n for b in root.find_all('boarder') for n in b.find_all('link')
             if len(n.args) >= 2 and n.args[1] == 'commander' and n.find('anm')]
    names = {n.find('anm').args[0] for n in links}
    for b in root.find_all('boarder'):
        for n in b.find_all('anm'):
            if n.args and n.args[0] in names:
                forward = n.find('forward')
                end = forward.find('end') if forward else None
                if end and end.args:
                    return end.args[0]
    return None


def support_catalog(index, *, template=None):
    """Find native single-commander AA pivots, poses and deployment clips."""
    from .compiler import _load_entity, _crew_expansion, _animation_map
    base = _stock(index)
    if hasattr(base, '_commander_supports'):
        return [s for s in base._commander_supports if template is None or s['template']==template]
    partial=base.__dict__.setdefault('_commander_support_items',{})
    if template is not None and not isinstance(template,str):return []
    if template in partial:return partial[template]
    blocked, rows = _blocked(base), []
    paths=sorted(base.entries) if template is None else []
    if template is not None:
        if not template or len(template)>256:return []
        try:paths=[base.resolve_template(template)['definition_path']]
        except (ValueError,KeyError,OSError):return []
    for key in paths:
        if not key.startswith('entity/-vehicle/') or not key.endswith('.def'):
            continue
        if b'mountable' not in base.read(key):
            continue
        template = posixpath.basename(key)[:-4]
        if template in blocked:
            continue
        try:
            info, _, root = _load_entity(base, template)
            _expand_visible(base, root)
            model = parse_model(base.read(info['model_path']))
            pose = _commander_pose(root)
            seats = [p for pl in root.find_all('placer') for p in pl.find_all('place')
                     if p.args == ['commander'] and p.find('linkbone')]
            for w in root.find_all('Weaponry'):
                for p in w.find_all('place'):
                    if (not p.find('type') or p.find('type').args != ['aa'] or p.find('weapon')
                            or not p.find('gunner') or p.find('gunner').args != ['commander']
                            or not p.find('charger') or p.find('charger').args != ['commander']
                            or not p.find('LinkBone') or not pose):
                        continue
                    link = p.find('LinkBone').args[0]
                    ancestors, current = [], link
                    while current in model['bones']:
                        node = model['bones'][current]
                        if 'revolute' in node['node'].args[:-1]:
                            ancestors.append(current)
                        current = node['parent']
                    if len(ancestors) < 2:
                        continue
                    pitch, yaw = ancestors[:2]
                    selected = descendants(model['bones'], yaw)
                    seat = next((s.find('linkbone').args[0] for s in seats
                                 if s.find('linkbone').args[0] in selected), None)
                    if not seat:
                        continue
                    clips = []
                    for seq in model['document'].walk('sequence'):
                        if not seq.args:
                            continue
                        file = seq.find('file')
                        path = resolve_path(info['model_path'], file.args[0] if file else seq.args[0] + '.anm')
                        if base.exists(path):
                            names, _, frames = _animation_map(base.read(path))
                            if frames and set(names) & {yaw, pitch}:
                                clips.append(path)
                    if len(clips) != 1:
                        continue
                    rows.append({'id': template + ':' + p.args[0], 'label': base.label_for(template), 'template': template,
                                 'model_path': info['model_path'], 'yaw': yaw, 'pitch': pitch,
                                 'link': link, 'seat': seat, 'pose': pose, 'clip': clips[0],
                                 'place': p.render(), 'bones': sorted(selected)})
        except (ValueError, KeyError, OSError):
            continue
    if template is None:base._commander_supports = rows
    else:partial[template]=rows
    return rows


def _values(value, count, low, high):
    if not isinstance(value, (list, tuple)) or len(value) != count:
        _error('La montura necesita posición, orientación y límites válidos.')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           or not low <= v <= high for v in value):
        _error('La montura necesita posición, orientación y límites válidos.')
    return list(value)


def configuration(index, spec):
    if not isinstance(spec, dict) or spec.get('operator', 'commander') != 'commander':
        _error('La montura solo puede asignarse al comandante.')
    support_id=spec.get('support')
    supports = support_catalog(index,template=support_id.rsplit(':',1)[0] if isinstance(support_id,str) else '')
    support = next((s for s in supports if s['id'] == spec.get('support')), None)
    if support is None:
        _error('Elegí una montura nativa disponible.')
    weapon = next((w for w in weapon_catalog(index,item=spec.get('item') or '') if w['id'] == spec.get('item')), None)
    if weapon is None:
        _error('Elegí un arma montable disponible. Para armas DLC, abrí el juego con el DLC adquirido e instalado y volvé a leer los recursos.')
    position = _values(spec.get('position'), 3, -10000, 10000)
    rotation = _values(spec.get('rotation'), 3, -360, 360)
    yaw = _values(spec.get('yaw'), 2, -180, 180)
    pitch = _values(spec.get('pitch'), 2, -90, 90)
    if yaw[0] >= yaw[1] or pitch[0] >= pitch[1]:
        _error('El límite mínimo debe ser menor que el máximo.')
    parent = spec.get('parent')
    if not isinstance(parent, str) or len(parent) > 160:
        _error('Elegí el hueso donde se fija la montura.')
    return support, weapon, dict(spec, operator='commander', parent=parent,
                                 position=position, rotation=rotation, yaw=yaw, pitch=pitch, **({'pivot':pivot_of(spec)} if 'pivot' in spec else {}))


def operator_weapon_problem(root):
    """A second aiming duty conflicts; loading alone does not prove that conflict."""
    for weaponry in root.find_all('Weaponry'):
        for place in weaponry.find_all('place'):
            typ=place.find('type')
            if typ and typ.args==['commander']:continue
            gunner=place.find('gunner')
            if gunner and 'commander' in ' '.join(gunner.args).split():
                return 'El comandante ya apunta otra arma del vehículo. No puede apuntar dos monturas independientes.'
    return ''


def operator_loader_warning(root):
    """Disclose the loading duty change before adding the exterior mount."""
    for weaponry in root.find_all('Weaponry'):
        for place in weaponry.find_all('place'):
            charger,gunner=place.find('charger'),place.find('gunner')
            if (charger and 'commander' in ' '.join(charger.args).split()
                    and not (gunner and 'commander' in ' '.join(gunner.args).split())):
                return ('El comandante también recarga el armamento del vehículo. '
                        'Con la MG montada, el artillero de cada arma también se encargará de su recarga, con la escotilla abierta o cerrada.')
    return ''


def _delegate_loaders(root):
    """Keep the sealed exterior commander out of internal weapons' work lists.

    Native T-70/SDKfz222a use one gunner for both aiming and loading. Transfer
    only the commander's loader entry, preserving other loaders and minWorkers.
    Plan every change first so unsupported crews cannot leave partial edits.
    """
    crew={p.args[0] for pl in root.find_all('placer') for p in pl.find_all('place')
          if p.args and p.find('group') and p.find('group').args==['crew']}
    changes=[]
    for weaponry in root.find_all('Weaponry'):
        for place in weaponry.find_all('place'):
            charger,gunner=place.find('charger'),place.find('gunner')
            if not charger or not charger.args or 'commander' not in charger.args[0].split():continue
            if place.find('type') and place.find('type').args==['commander']:continue
            aiming=gunner.args[0].split() if gunner and gunner.args else (['gunner'] if gunner is None else [])
            if 'commander' in aiming:continue  # the exterior MG keeps its own operator
            if len(aiming)!=1 or aiming[0] not in crew:
                _error('No se pudo delegar la recarga: el arma necesita un artillero propio único y válido.')
            previous=charger.args[0]
            current=list(dict.fromkeys(aiming[0] if name=='commander' else name for name in previous.split()))
            if 'minWorkers' in charger.args:
                try:minimum=int(charger.args[charger.args.index('minWorkers')+1])
                except (IndexError,ValueError):minimum=len(current)+1
                if minimum>len(current):
                    _error('No se pudo delegar la recarga sin reducir la cantidad de cargadores requerida.')
            changes.append((charger,{'place':place.args[0],'from':previous,'to':' '.join(current)}))
    for charger,change in changes:charger.atoms[1].set(change['to'])
    return [change for _,change in changes]


def apply_mount(index, root, model, spec, assets, diagnostics):
    """Compose into the final assembly, after its original assets were rewritten."""
    from .compiler import (_Assets, _set_matrix, _rename_bone_refs, _unique_name, _explicit_weapon_operators,
                           _crew_expansion, _animation_map)
    support, weapon, spec = configuration(index, spec)
    base = _stock(index)
    _expand_visible(index, root)
    _explicit_weapon_operators(index, root)
    places = [p for pl in root.find_all('placer') for p in pl.find_all('place') if p.args == ['commander']]
    if not any(p.find('group') and p.find('group').args == ['crew'] for p in places):
        _error('Este ensamblaje no tiene un puesto de comandante. No se agregan tripulantes.')
    problem=operator_weapon_problem(root)
    if problem:_error(problem)
    loader_warning=operator_loader_warning(root)
    loader_transfers=_delegate_loaders(root)
    final = parse_model(model['document'].render())
    if spec['parent'] not in final['bones']:
        _error('El hueso elegido para la montura no existe en el ensamblaje final.')
    native = parse_model(base.read(support['model_path']))
    taken = set(final['bones'])
    mapping = {}
    for name in support['bones']:
        mapping[name] = _unique_name('cmdmg_' + name, taken | set(mapping.values()))
    copied = native['bones'][support['yaw']]['node'].clone()
    _rename_bone_refs(copied, mapping)
    _set_matrix(copied, pose_matrix(spec))
    for name, limits in ((support['yaw'], spec['yaw']), (support['pitch'], spec['pitch'])):
        bone = next(n for n in copied.walk('bone') if n.args[-1] == mapping[name])
        old = bone.find('limits')
        if old:
            bone.remove(old)
        bone.append(block('{limits %s %s}' % tuple(limits)))
    parent = final['bones'][spec['parent']]['node']
    parent.append(copied)
    # Work on the caller's model, not the parsed validation copy.
    original_parent = next(n for n in model['document'].walk('bone') if n.args[-1] == spec['parent'])
    original_parent.append(copied)
    pivot=pivot_of(spec)
    if any(pivot):
        frame=pose_matrix(spec);centre=point(frame,pivot)
        from .geometry import inverse
        rebase_joint(model['document'],mapping[support['yaw']],point(inverse(frame),centre))
    merged = parse_model(model['document'].render())
    extra = _Assets(base, assets.target, assets.sources, diagnostics)
    extra.context('cmd', native, merged, mapping, exclude=set(native['bones']) - set(support['bones']))
    extra.rewrite(copied, support['model_path'], 'cmd')
    clip = extra.add(support['clip'], 'cmd')
    if not _animation_map(extra.files[clip])[0]:
        _error('La animación de la montura quedó vacía.')
    sequence = _unique_name('cmdmg_pose', {n.args[0] for n in model['document'].walk('sequence') if n.args})
    animation = model['skeleton'].find('animation')
    if animation is None:
        animation = block('{animation}')
        model['skeleton'].append(animation)
    animation.append(block('{sequence %s {file %s} {speed 0.3} {resume}}' % (quote(sequence), quote(clip))))
    assets.files.update(extra.files)
    assets.missing_materials.update(extra.missing_materials)
    assets.missing_bones.update(extra.missing_bones)
    visor = _unique_name('cmdmg_top', {n.args[0] for n in root.walk('visor') if n.args})
    for p in places:
        for field in ('LinkBone', 'visor', 'sealable'):
            for old in p.find_all(field):
                p.remove(old)
        p.append(block('{LinkBone %s}' % quote(mapping[support['seat']])))
        p.append(block('{sealable}'))
        p.append(block('{visor %s}' % quote(visor)))
    root.append(block('{sensor {visor %s {vision "human"} {bone %s}}}' % (quote(visor), quote(mapping[support['link']]))))
    # Stock tank-main's "hatch" handler updates the deployment clip on spawn
    # and crew occupancy changes. Open vehicles may not inherit this property.
    for props in root.find_all('props'):
        props.items=[item for item in props.items if not (hasattr(item,'value') and item.value=='-hatch')]
    root.append(block('{props "hatch"}'))
    # Preserve chassis boarding routes, clone only the commander's clips so
    # passengers using the same route retain their own pose.
    links = [n for b in root.find_all('boarder') for n in b.find_all('link')
             if len(n.args) >= 2 and n.args[1] == 'commander']
    boarder = root.find('boarder')
    if boarder is None or not links:
        _error('No se pudo verificar la entrada y salida del comandante en este vehículo.')
    existing = {n.args[0]: n for b in root.find_all('boarder') for n in b.find_all('anm') if n.args}
    for link in links:
        reference = link.find('anm')
        old = existing.get(reference.args[0]) if reference and reference.args else None
        name = _unique_name('cmdmg_board', set(existing))
        clip_node = old.clone() if old else block('{anm %s {forward} {reverse {end "emit_tank_1_hold"}}}' % quote(name))
        clip_node.atoms[1].set(name)
        forward = clip_node.find('forward')
        if forward is None:
            forward = block('{forward}')
            clip_node.append(forward)
        for end in forward.find_all('end'):
            forward.remove(end)
        forward.append(block('{end %s}' % quote(support['pose'])))
        boarder.append(clip_node)
        existing[name] = clip_node
        if reference:
            reference.atoms[1].set(name)
        else:
            link.append(block('{anm %s}' % quote(name)))
    inventory = next((n for n in root.find_all('extender') if n.args == ['inventory']), None)
    if inventory is None:
        inventory = block('{extender "inventory" {box}}')
        root.append(inventory)
    names = {n.args[0] for n in root.walk('place') if n.args} | {n.args[0] for n in inventory.find_all('weapon') if n.args}
    names.add('mgun')  # Generic inherited slot in properties/vehicle.ext.
    slot = _unique_name('mgun_cmd', names)
    inventory.append(block('{weapon %s {mask "mountable"}}' % quote(slot)))
    box = inventory.find('box')
    if box is None:
        box = block('{box}')
        inventory.append(box)
    initial = block('{item %s filled {user %s}}' % (quote(weapon['id']), quote(slot)))
    box.append(initial)
    ammo = block('{item %s %s}' % (quote(weapon['ammo_item']), weapon['ammo_rounds']))
    box.append(ammo)
    weaponry = root.find('Weaponry')
    if weaponry is None:
        weaponry = block('{Weaponry}')
        root.append(weaponry)
    removed_places = []
    for old in list(weaponry.find_all('place')):
        if old.find('type') and old.find('type').args == ['commander'] and old.find('gunner') and old.find('gunner').args == ['commander']:
            removed_places.append(old.args[0])
            weaponry.remove(old)
    place = block(support['place'])
    place.atoms[1].set(slot)
    _rename_bone_refs(place, mapping)
    weaponry.append(place)
    for name, speed in ((support['yaw'], 180), (support['pitch'], 90)):
        root.append(block('{bone %s {speed2 %s}}' % (quote(mapping[name]), speed)))
    diagnostics.append({'code': 'commander_mg_runtime_gate', 'level': 'warning',
                        'message': 'Montura de comandante experimental: animación, cambio de arma e IA pendientes de prueba en batalla.'})
    if loader_warning:
        diagnostics.append({'code':'commander_mg_loader_handoff','level':'warning','message':loader_warning})
    return {'operator': 'commander', 'slot': slot, 'item': weapon['id'], 'sequence': sequence,
            'support': support['id'], 'parent': spec['parent'], 'bones': mapping,
            'crew_slots_added': 0, 'initial_item': initial.render(), 'ammo_item': ammo.render(),
            'removed_weapon_places': removed_places, 'loader_transfers': loader_transfers, 'runtime_verified': False}


def add_interactions(record, contract):
    """Keep native hatch handlers and append only the support deployment clip."""
    if contract.get('slot'):add_mountable_fire(record,contract['slot'])
    for event, direction in (('commander_hatch_open', '+1.0'), ('commander_hatch_close', '-1.0')):
        handlers = [h for h in record.find_all('on') if h.args and h.args[0] == event]
        if len(handlers) > 1:
            _error('La escotilla del comandante tiene eventos ambiguos.')
        handler = handlers[0] if handlers else block('{on %s overload}' % quote(event))
        if not handlers:
            record.append(handler)
        for play in list(handler.find_all('ani_play')):
            if play.args and play.args[0].startswith('cmdmg_pose'):
                handler.remove(play)
        handler.append(block('{ani_play %s %s resume}' % (quote(contract['sequence']), direction)))


def add_mountable_fire(record,slot):
    """Native vehicle-main dispatch for inventory-mounted weapons.

    Generated slots are outside vehicle-main's numbered MG events. Forward
    their effector event to the stuff weapon, which owns its muzzle flash,
    recoil and sounds. Never guess a vehicle flash bone or duplicate the call.
    """
    handlers=[n for n in record.find_all('on') if n.args[:2]==['fire',slot]]
    if len(handlers)>1:_error('La montura tiene eventos de disparo ambiguos.')
    handler=handlers[0] if handlers else block('{on fire '+quote(slot)+'}')
    if not handlers:record.append(handler)
    if any(n.args==['fire'] for n in handler.walk('call')):return
    handler.append(block('{if effector "stuff weapon" {with_effector {call "fire"}}}'))
