"""Portable native multibarrel effects, owned by a weapon rather than a chassis.

The engine's shot name (00, 01, ...) selects the native barrel event. Preserve
that dispatch and its actual add_view sockets; a generic muzzle at foresight14
is the aiming centre between the barrels, not any of their muzzles.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

from .syntax import Atom, Trivia, Node, block, parse, quote
from .weapon_binding import barrel_bones, weapon_link


def _recipe(resources, template, model_path=None):
    if not hasattr(resources, 'resolve'):
        return None
    if model_path is None:
        model_path = resources.resolve_template(template)['model_path']
    source = resources.resolve(model_path).source_root
    path = Path(source) / ('assembly_' + template + '.json')
    return json.loads(path.read_text(encoding='utf8')).get('metadata') if path.is_file() else None


def interaction_source(resources, template, seen=()):
    """Resolve private design/replay overlays to a persistent sound authority."""
    if template in seen:
        raise ValueError('Cyclic interaction provenance: ' + template)
    metadata = _recipe(resources, template)
    if metadata is None:
        return template
    authority = metadata.get('interaction_donor')
    if not authority:
        transform = metadata.get('transform') or {}
        draft = transform.get('assembly_draft')
        replacement = None
        if draft:
            from .draft import design_turrets
            replacement = next((r for r in design_turrets(draft) if not r.get('native') and (r.get('recipient_mount') or 'turret') == 'turret'), None)
        if draft:
            authority = (replacement or {}).get('donor_template') or metadata.get('recipient')
        elif metadata.get('recipient_mount') == '@full' or str(metadata.get('recipient_mount','')).startswith('@free'):
            authority = metadata.get('recipient')
        else:
            authority = metadata.get('donor')
    if isinstance(authority, dict): authority = authority.get('id')
    return interaction_source(resources, authority, (*seen,template)) if authority and authority != template else template


def _expand(scope, defines, stack=()):
    """Expand native macros with lexical defines and positional/named args."""
    from .registration import _macro_arguments
    local = dict(defines)
    for node in scope.children:
        if node.head.casefold() == 'define' and node.args:
            local[node.args[0].casefold()] = node
    result = scope.clone()
    result.items = []
    for item in scope.items:
        if not isinstance(item, Node):
            result.items.append(copy.deepcopy(item))
            continue
        if item.head.casefold() == 'define':
            continue
        macro = local.get(item.head.casefold()) if item.opener == '(' else None
        if macro is None:
            result.items.append(_expand(item, local, stack))
            continue
        name = item.head.casefold()
        if name in stack or len(stack) > 24:
            raise ValueError('Cyclic weapon effect macro: ' + name)
        args = _macro_arguments(item)
        if 'args' in item.args:
            args.update({str(i): a.render() for i, a in enumerate(item.atoms[item.args.index('args')+2:])})
        body = ''.join(p.render() for p in macro.items[macro.items.index(macro.atoms[1])+1:])
        body = re.sub(r'%(\w+)', lambda m: args.get(m[1].casefold(), m[0]), body)
        result.items.extend(_expand(parse(body), local, (*stack, name)).items)
    return result


def capture(resources, template):
    """Read generated contracts from their authoritative overlay, else native data.

    The answer only depends on the indexed files, so it is kept per index:
    every preview of the same design asked for it again."""
    kept = getattr(resources, '__dict__', {}).setdefault('_weapon_effects', {}) if hasattr(resources, '__dict__') else {}
    key = (template, len(getattr(resources, 'entries', ())))
    if key not in kept:
        kept[key] = _capture(resources, template)
    return copy.deepcopy(kept[key])


def _capture(resources, template):
    from .compiler import _load_entity, _explicit_weapon_operators, _include_defines, _implicit_foresight
    from .geometry import parse_model, editable_mounts, descendants
    from .registration import _load_include_graph, _interaction_defines
    info, _, root = _load_entity(resources, template)
    metadata = _recipe(resources, template, info['model_path'])
    if metadata is not None and 'weapon_effects' in metadata:
        return copy.deepcopy(metadata['weapon_effects'])
    model = parse_model(resources.read(info['model_path']))
    places = [p for w in root.find_all('Weaponry') for p in w.find_all('place')
              if p.args and p.find('weapon') is not None]
    if not places:
        return []
    if not hasattr(resources,'resolve') or not hasattr(resources,'read_text'):
        return []  # Minimal compiler adapters do not expose the native include graph.
    try:
        records, _ = _load_include_graph(resources, 'set/interaction_entity/entity.set')
    except (KeyError, FileNotFoundError):
        return []  # Minimal resources can rely entirely on stuff/engine effects.
    defines = _interaction_defines(records)
    props = {p.casefold() for p in _explicit_weapon_operators(resources, root)}
    tags = props | {template.casefold()}
    candidates = [n for _, n in records if n.head and n.head.casefold() != 'define'
                  and set(n.head.casefold().split()) <= tags]
    # Interaction heads are tag conjunctions: "tank muzzle_75mm" must not
    # apply to every tank, nor replace a more specific entity's barrel loop.
    candidates.sort(key=lambda n:(template.casefold() in n.head.casefold().split(), len(n.head.split())))
    paths = {id(n):p for p,n in records}
    for node in candidates:
        defines.update({k.casefold():v for k,v in _include_defines(resources, node, paths[id(node)]).items()})
    # Native class inheritance is listed in each record head ("foo tank").
    inherited = []
    seen = set()
    def visit(node):
        for parent in node.head.split()[1:]:
            for _, ancestor in records:
                if ancestor.head.casefold() == parent.casefold() and id(ancestor) not in seen:
                    seen.add(id(ancestor)); visit(ancestor); inherited.append(ancestor)
    for node in candidates:
        visit(node)
    events, applied = {}, set()
    for node in [*inherited, *candidates]:
        if id(node) in applied:
            continue
        applied.add(id(node))
        for event in _expand(node, defines).find_all('on'):
            key = tuple(a for a in event.args if a.casefold() != 'overload')
            if key in events and 'overload' not in event.args:
                previous = events[key].clone()
                for child in event.children:
                    previous.append(child.clone())
                event = previous
            events[key] = event
    result = []
    modules = set(editable_mounts(model, root))
    for place in places:
        slot = place.args[0]
        owner = weapon_link(place, model)
        while owner in model['bones'] and owner not in modules:
            owner = model['bones'][owner]['parent']
        owned = descendants(model['bones'], owner) if owner in model['bones'] else set(model['bones'])
        fire = events.get(('fire', slot))
        if fire is None:
            continue  # No entity effect override to transfer; native stuff owns it.
        helpers, pending = {}, [fire]
        while pending:
            event = pending.pop()
            for call in event.walk('call'):
                key = tuple(call.args[:1])
                if key in events and key not in helpers and key not in (('fire', slot), ('fire',)):
                    helpers[key] = events[key]; pending.append(events[key])
        scripts = [fire, *helpers.values()]
        views = {n.args[1] for event in scripts for n in event.walk('view') if len(n.args) >= 2}
        setup = {}
        bones = {b.casefold():b for b in model['bones']}
        for event in events.values():
            for view in event.walk('add_view'):
                if (len(view.args) >= 3 and view.args[1] in views and view.args[2].casefold() in bones
                        and bones[view.args[2].casefold()] in owned):
                    view = view.clone(); view.atoms[3].set(bones[view.args[2].casefold()])
                    # A native label may intentionally bind several sockets
                    # (shell ejection on Flak guns); keep every binding.
                    setup[tuple(view.args)] = view
        shooters = barrel_bones(place, model)
        if not shooters:
            # A single-barrel gun's native effect usually binds its foresight,
            # not its recoil bone. Its slot can be renamed just like a battery's.
            from .weapon_binding import reference_bones
            for head in ('shooter','foresight'):
                reference=place.find(head)
                if reference and reference.args:
                    shooters=reference_bones(place,model,head,reference.args[0])
                    if shooters:break
            if not shooters:
                sight=_implicit_foresight(slot,place,model,set(model['bones']))
                if sight:shooters=[sight]
        if not any(v.args[2] in shooters for v in setup.values()):
            continue  # Rockets/stuff without native per-barrel views keep their own dispatch.
        muzzle = [v for v in setup.values() if v.args[2] in shooters]
        projected = False
        # Some native definitions declare several barrels but use one generic
        # emitter on every shot. Project that native effect onto the complete
        # shooter family, using GEM's own 00/01/... shot dispatch. Preserve
        # sources that already author their own per-barrel conditional logic.
        if (len({v.args[2] for v in muzzle}) == 1 and len(shooters) > 1
                and not any('name' in n.args for n in fire.walk('if'))):
            projected = True
            for view in muzzle: setup.pop(tuple(view.args))
            dispatch = block('{if}')
            for i, shooter in enumerate(shooters):
                local = {v.args[1]:v.args[1] + f'_barrel{i:02}' for v in muzzle}
                for view in muzzle:
                    clone = view.clone(); clone.atoms[2].set(local[view.args[1]]); clone.atoms[3].set(shooter)
                    setup[tuple(clone.args)] = clone
                dispatch.items.extend(parse(' ' + ('else ' if i else '') + 'name ' + quote(f'{i:02}')).items)
                for child in fire.children:
                    clone = child.clone()
                    for command in clone.walk('view'):
                        if len(command.args) > 1 and command.args[1] in local:
                            command.atoms[2].set(local[command.args[1]])
                    dispatch.append(clone)
            fire = block('{on fire ' + quote(slot) + ' overload}')
            fire.append(dispatch); scripts[0] = fire
        views = {n.args[1] for event in scripts for n in event.walk('view') if len(n.args) >= 2}
        missing = views - {v.args[1] for v in setup.values()}
        sounds = {n.args[0] for event in scripts for n in event.walk('play_sound') if n.args}
        links = {}
        for event in events.values():
            for link in event.walk('link_sound'):
                if link.args and link.args[0] in sounds:
                    links[link.args[0]] = link
        result.append({'place': slot, 'shooters': shooters, 'optional_views':sorted(missing), 'projected_barrels':projected,
                       'setup': [n.render() for n in [*setup.values(), *links.values()]],
                       'events': [n.render() for n in scripts], 'origin': template})
    return result


_NUMERIC = re.compile(r'[-+]?[0-9.]')
_BROKEN_DELAY = re.compile(r'(\{delay\s+\S+\s+)cw_[0-9a-f]{10}_(?=[-+]?[0-9.])')


def transplant(contracts, places, mapping=None, sequences=None, namespace='', *, owned=None):
    """Filter by selected places and remap all local effect identities together."""
    mapping, sequences = mapping or {}, sequences or {}
    result = []
    for original in contracts:
        if original['place'] not in places:
            continue
        row = copy.deepcopy(original)
        row['place'] = places[original['place']]
        row['shooters'] = [mapping.get(n, n) for n in row['shooters']]
        if owned is not None:
            kept = []
            for text in row['setup']:
                node = block(text)
                if node.head.casefold() == 'add_view' and node.args[2] not in owned:
                    row.setdefault('optional_views', []).append(node.args[1])
                else:
                    kept.append(text)
            row['setup'] = kept
        nodes = [block(s) for s in [*row['setup'], *row['events']]]
        names, variables, tags = set(), set(), set()
        for node in nodes:
            for child in node.walk():
                head, args = child.head.casefold(), child.args
                if head == 'add_view' and len(args) >= 2: names.add(args[1])
                if head == 'view' and len(args) >= 2: names.add(args[1])
                if head in ('link_sound', 'set') and args: names.add(args[0])
                if head == 'set' and args: variables.add(args[0])
                if head == 'tags' and len(args) > 1: tags.update(args[1].split()); names.update(tags)
                if head == 'on' and args[:1] != ['fire'] and args: names.add(args[0])
                # {delay 4 3 ...}: a second number, not a name (167 in the game).
                if head == 'delay' and len(args) > 1 and not _NUMERIC.match(args[1]): names.add(args[1])
        prefix = 'cw_' + hashlib.sha256((namespace + '/' + row['place']).encode()).hexdigest()[:10] + '_'
        rename = {n: prefix+n for n in names} if namespace else {}
        for node in nodes:
            for child in node.walk():
                head, args = child.head.casefold(), child.args
                def change(index, table):
                    if len(args) > index:
                        child.atoms[index+1].set(table.get(args[index], args[index]))
                # Identifiers are typed. A delay named "fire" is independent
                # of the engine's {on fire}, a clip "fire" or an FX resource.
                if head == 'add_view':
                    change(1, rename); change(2, mapping)
                elif head == 'view': change(1, rename)
                elif head in ('link_sound','play_sound','stop_sound','start_sound','unlink_sound','kill_delay','set'):
                    change(0, rename)
                elif head == 'delay': change(1, rename)
                elif head == 'on' and args[:1] != ['fire']: change(0, rename)
                elif head in ('call','call_synced','call_synced_local'):
                    # {with_effector {call fire}} is a native stuff callback.
                    if args[:1] != ['fire']: change(0, rename)
                elif head.startswith('ani_'): change(0, sequences)
                elif head == 'tags' and len(args) > 1:
                    child.atoms[2].set(' '.join(rename.get(n,n) for n in args[1].split()))
                elif head == 'if':
                    for i,arg in enumerate(args):
                        previous = args[i-1].casefold() if i else ''
                        if (arg in tags and previous in ('tagged','not_tagged')) or (arg in variables and previous in ('','not','else')):
                            change(i, rename)
                elif head in ('bone','ik_enable'):
                    for i in range(len(args)): change(i, mapping)
                elif head == 'spawn' and len(args)>1:
                    change(1, mapping)
            if node.head.casefold() == 'on' and node.args[:1] == ['fire']:
                node.atoms[2].set(row['place'])
                if 'overload' not in [a.casefold() for a in node.args]:
                    at = node.items.index(node.atoms[-1]) + 1
                    node.items[at:at] = [Trivia(' '), Atom('overload','overload')]
        row['setup'] = [n.render() for n in nodes[:len(row['setup'])]]
        row['events'] = [n.render() for n in nodes[len(row['setup']):]]
        result.append(row)
    return result


def install(record, contracts, resources=None):
    """Install each weapon's own native dispatch; preserve chassis spawn/hatches."""
    if not contracts:
        return record
    if record is None:
        raise ValueError('Weapon effects require an entity interaction record')
    if resources is not None:
        from .registration import _load_include_graph, _interaction_events, _interaction_defines
        records, _ = _load_include_graph(resources, 'set/interaction_entity/entity.set')
        defines = _interaction_defines(records)
        record = _interaction_events(record, defines)
    setup = []
    for row in contracts:
        setup.extend(row['setup'])
        for text in row['events']:
            # Vehicles built before this fix prefixed a delay's second number
            # (cw_<hash>_0.5); the game cannot read that and crashes loading.
            text = _BROKEN_DELAY.sub(r'\1', text)
            event = block(text)
            key = [a for a in event.args if a.casefold() != 'overload']
            for old in list(record.find_all('on')):
                if [a for a in old.args if a.casefold() != 'overload'] == key:
                    record.remove(old)
            record.append(event)
    spawn = next((e for e in record.find_all('on') if e.args[:1] == ['spawn']), None)
    if spawn is None:
        spawn = block('{on spawn {inherited}}'); record.append(spawn)
    for text in dict.fromkeys(setup):
        node = block(text)
        if not any(n.head.casefold() == node.head.casefold() and n.args == node.args for n in spawn.walk()):
            spawn.append(node)
    return record
