"""Independent, structural assembly compiler for GEM vehicles.

Compilation writes an isolated mod directory. Source assets and campaigns are
read-only. Runtime acceptance is a separate gate recorded in the manifest.
"""
from __future__ import annotations

import hashlib
import json
import math
import posixpath
import re
import struct
from pathlib import Path
from . import __version__
from .pivots import pose_matrix, pivot_of, rebase_joint
from .syntax import Atom, Node, Document, Trivia, parse, block, quote, bone_name, entity_root
from .geometry import (GeometryError, FREE_MOUNT, whole_model_mount, donor_mount_name, free_mount_parent, deck_point, free_mount_spec, free_spot_parent, recipient_mount_name,
                       is_carried_weapon, donor_seat, free_anchor, seat_offset, mount_seat, parse_model, descendants, resolve_path, identity,
                       multiply, inverse, point, translation, euler_matrix, read_mesh, validate_mesh, mesh_materials, _points)


class CompileError(ValueError):
    pass


def _set_matrix(node, matrix):
    for child in list(node.children):
        if child.head.casefold() in ("position", "orientation", "matrix34"):
            node.remove(child)
    values = [matrix[r][c] for c in range(3) for r in range(3)] + [matrix[r][3] for r in range(3)]
    node.append(block("{Matrix34 " + " ".join(format(x, ".10g") for x in values) + "}"))


def _remove_bone(model, name):
    entry = model["bones"][name]
    parent = model["bones"][entry["parent"]]["node"] if entry["parent"] else model["skeleton"]
    parent.remove(entry["node"])


def _camera_sockets(resources, template, source, document, source_joint, target_joint,
                    mapping, diagnostics, *, external_parent=None):
    """Carry native optical frames without importing unrelated donor geometry."""
    from .registration import _native_camera_profile
    try:
        profile, provenance = _native_camera_profile(resources, template)
    except (KeyError, OSError, ValueError, AttributeError) as error:
        if not isinstance(error, KeyError):
            diagnostics.append({'code': 'camera_profile_unavailable', 'level': 'warning', 'message': str(error)})
        return {}, {}
    final = parse_model(document.render())
    aliases = {}
    if source_joint not in source['bones'] or target_joint not in final['bones']:
        return aliases, {}
    frame = multiply(final['bones'][target_joint]['world'], inverse(source['bones'][source_joint]['world']))
    parent = external_parent if external_parent in final['bones'] else target_joint
    source_names = {name.casefold(): name for name in source['bones']}
    refs = {n.args[0] for level in profile.find_all('level')[1:] for n in level.walk('linkBone') if n.args}
    for original in sorted(refs):
        source_name = source_names.get(original.casefold())
        emitted = mapping.get(source_name)
        if emitted in final['bones']:
            aliases[original] = emitted
            continue
        if source_name is None:
            continue
        serial = 0
        while 'cw_scope_'+str(serial) in final['bones']:
            serial += 1
        name = 'cw_scope_'+str(serial)
        socket = block('{bone '+quote(name)+'}')
        world = multiply(frame, source['bones'][source_name]['world'])
        _set_matrix(socket, multiply(inverse(final['bones'][parent]['world']), world))
        aliases[original] = name
        original_parent = next(n for n in document.walk('bone') if bone_name(n) == parent)
        original_parent.append(socket)
        final = parse_model(document.render())
        diagnostics.append({'code': 'camera_socket_preserved', 'level': 'info',
                            'bone': name, 'source_bone': original,
                            'message': 'Native aiming camera frame retained without donor hull geometry.'})
    return aliases, {row['path']: row['sha256'] for row in provenance}


def _parameters(node):
    values = {}
    for child in node.find_all("parameters"):
        for arg in child.args:
            for pair in arg.split(";"):
                key, separator, value = pair.partition("=")
                if separator:
                    values[key.casefold()] = value
    return values


def _module_ids(model, names):
    return set(names) | {value for name in names for key, value in _parameters(model["bones"][name]["node"]).items() if key == "id"}


def _component_ids(model, names):
    return {value for name in names for parameters in model['bones'][name]['node'].children
            if parameters.head.casefold() == 'parameters' for arg in parameters.args
            for pair in arg.split(';') for key, separator, value in [pair.partition('=')]
            if separator and key.strip().casefold() == 'id' and value}


def _rename_module_components(document, names, mapping):
    # Only imported bones own these IDs; never rewrite chassis/wheel parameters.
    for bone in document.walk('bone'):
        if bone_name(bone) not in names:
            continue
        for parameters in bone.children:
            if parameters.head.casefold() != 'parameters':
                continue
            for atom in parameters.atoms[1:]:
                atom.set(re.sub(r'(?i)((?:^|;)\s*id\s*=\s*)([^;]+)',
                                lambda m: m[1] + mapping.get(m[2], m[2]), atom.value))


def _copy_module_armor(resources, source, target, volumes, identifiers, mapping, bone_mapping, components):
    isolated = components & mapping.keys()
    contracts = None
    copied_contracts = set()
    for node in source.children:
        if not ((node.head.casefold() == 'volume' and node.args and node.args[0] in volumes | identifiers)
                or (node.opener == '(' and (node.head in identifiers or _values(node) & volumes))):
            continue
        if node.opener == '(' and node.head in isolated:
            # Native body(...) hard-codes body volume names. Copy the effective
            # per-volume contract, not a macro that would configure the hull.
            if contracts is None:
                from .extra_armor import _volumes
                contracts = _volumes(resources, source)
            if node.head in contracts:
                for name in sorted((isolated & contracts.keys()) - copied_contracts):
                    extra = _rename_volume(contracts[name], mapping)
                    _rename_bone_refs(extra, bone_mapping)
                    target.append(extra)
                    copied_contracts.add(name)
                continue
        extra = _rename_volume(node, mapping)
        _rename_bone_refs(extra, bone_mapping)
        target.append(extra)


def _allowed_damage_ids(resources, root, model, extensions=()):
    """Damage names belong to emitted model AND effective definition volumes.

    Native internals can share a logical component (fuel, engine...) declared
    through property macros instead of a same-named model volume. Read the
    existing volume authority, including positional macros; never admit names
    merely because they happened to occur in the old save.
    """
    # Assembly moves armor and component macros into a generated sibling EXT.
    # It is not in the source ResourceIndex yet: validate the emitted documents
    # together instead of silently losing the component part of that contract.
    if extensions:
        root = root.clone()
        for extension in extensions:
            for child in extension.children:
                root.append(child.clone())
    allowed=set(model['volumes'])|{v for b in model['bones'].values() for k,v in _parameters(b['node']).items() if k=='id'}
    allowed|={n.args[0] for n in root.walk('component') if n.args}
    from .extra_armor import _volumes
    for name,volume in _volumes(resources,root).items():
        if not name or '%' in name:continue
        allowed.add(name)
        for component in volume.find_all('component'):
            if component.args and component.args[0]!='tag' and '%' not in component.args[0]:
                allowed.add(component.args[0])
    return allowed


def _values(node):
    return {item.value for item in node.items if isinstance(item, Atom)} | {value for child in node.children for value in _values(child)}


def _volume_mapping(names, reserved, mapping=None):
    mapping = dict(mapping or {})
    for name in sorted(names & reserved):
        if name in mapping:
            continue
        candidate = "module_"+name
        counter = 1
        while candidate in names | reserved | set(mapping.values()):
            candidate = "module_"+name+"_"+str(counter)
            counter += 1
        mapping[name] = candidate
    return mapping


def _rename_volume(node, mapping):
    node = node.clone()
    if node.head.casefold() == "volume" and node.args and node.args[0] in mapping:
        node.atoms[1].set(mapping[node.args[0]])
    elif node.opener == "(":
        # Macro names and field names are instructions, not resource operands.
        # Named argument blocks such as volume(shield) are values, however:
        # their first atom must still follow the renamed volume.
        def operands(current,value=False):
            head=current.atoms[0] if current.atoms else None
            pending=None
            marker=current.atoms[1] if current.opener=='(' and current.args[:1]==['args'] else None
            definition=current.atoms[1] if current.head.casefold()=='define' and current.args else None
            for i,item in enumerate(current.items):
                if isinstance(item,Atom):
                    following=next((n for n in current.items[i+1:] if not isinstance(n,Trivia)),None)
                    key=current.opener=='(' and isinstance(following,Node) and following.opener=='('
                    if (value or item is not head) and item is not marker and item is not definition and not key and item.value in mapping:
                        item.set(mapping[item.value])
                    pending=item if item is not head else None
                elif isinstance(item,Node):
                    argument=current.opener=='(' and item.opener=='(' and pending is not None
                    operands(item,argument);pending=None
        operands(node)
    return node


def _implicit_foresight(original, place, model, transferred):
    """The sight a native place uses without declaring one, or None.

    A primary place named "gun" with no {foresight} aims through the model's
    "foresight1" (or "foresight" on rocket and mortar carriers): measured on
    every installed vehicle. Other places always declare theirs.
    """
    if original != "gun" or place.find("foresight") is not None:
        return None
    # Only a sight that travels with the module can be named.
    return next((name for name in ("foresight1", "foresight") if name in model["bones"] and name in transferred), None)


def _rename_bone_refs(node, mapping):
    for child in node.walk():
        if child.head.casefold() == "bone":
            name = bone_name(child)
            if name in mapping:
                child.atoms[-1].set(mapping[name])
        elif child.head.casefold() in ("linkbone", "foresight", "shooter", "gunner", "charger", "commander"):
            for atom in child.atoms[1:]:
                if atom.value in mapping:
                    atom.set(mapping[atom.value])


def _bind_renamed_volumes(resources, root, mapping):
    if not mapping:
        return
    registry = parse(resources.read("properties/vehicle.ext"))
    components = {}
    for call in registry.walk("make_component"):
        if len(call.args) >= 3 and call.args[0] == "args":
            components[call.args[1]] = call.args[2]
    holes = next((node for node in registry.find_all("define") if node.args and node.args[0] == "holes"), None)
    for original, emitted in mapping.items():
        volume = next((node for node in root.find_all("volume") if node.args and node.args[0] == emitted), None)
        if volume is None:
            if original not in components and not (re.fullmatch(r"hole\d*", original) and holes):
                # Plain geometry volume with no definition entry in the donor:
                # renaming it changes nothing about its behaviour.
                continue
            volume = block("{volume "+quote(emitted)+"}")
            root.append(volume)
        if original in components:
            if not volume.find("component"):
                volume.append(block("{component "+quote(components[original])+"}"))
            if not volume.find("tags"):
                volume.append(block("{tags "+quote(components[original])+"}"))
        elif re.fullmatch(r"hole\d*", original) and holes:
            template = holes.find("volume")
            if template is None:
                raise CompileError("Effective holes macro has no volume behavior")
            for child in template.children:
                if any(value.startswith("%") for value in _values(child)):
                    raise CompileError("Effective holes macro requires an explicit remapping profile")
                volume.append(child.clone())
        # Otherwise the donor's own volume entry (armor, flags) was copied under
        # the new name; macro operands follow it while macro names stay native.


def _selected_places(root, model, names):
    weaponry = root.find("Weaponry")
    result, unknown = [], []
    if weaponry is None:
        return result, unknown
    for place in weaponry.find_all("place"):
        references = []
        for head in ("linkbone", "bone", "foresight", "shooter"):
            references += [node.args[0] for node in place.find_all(head) if node.args]
        fallback = place.args[0] if place.args else ""
        if not references and fallback in model["bones"]:
            references = [fallback]
        owners = [name for name in references if name in model["bones"]]
        if owners:
            # A place belongs where the bone carrying it is (LinkBone first,
            # then bone/foresight/shooter): references may straddle turret and
            # hull, e.g. a second gun of a multi-turret tank (NbFz "gun2").
            if owners[0] in names:
                result.append(place)
        elif names >= set(model["bones"]) or (
                model["anchor_name"] in names and not any(
                    node.args and node.args[0].casefold() == "hull" for node in place.find_all("type"))):
            # A place without bone references is aimed by turning the model's
            # main turret (Katyusha "gun": its launcher sits on "turret"), so it
            # travels with a module holding that turret, or with the whole model
            # (Solothurn). Otherwise it is the vehicle's (Wanze "explosive").
            result.append(place)
    return result, unknown


_HULL_SYSTEM = re.compile(r"(engine|transmission|fuel|radiator|exhaust)", re.I)


def _hull_system(name):
    """Engine, gearbox and fuel always belong to the chassis, even when a
    donor's modeler parented them under its turret (e.g. Flakpanzer I)."""
    return bool(_HULL_SYSTEM.match(name or ""))


def _prune_hull_systems(node):
    """Remove geometry-free hull-system bones from a transplanted module.

    Returns the removed bone names. Bones that carry meshes or children stay
    (they are visible structure); only their component role is dropped.
    """
    removed = set()
    for child in list(node.children):
        if child.head.casefold() != "bone":
            continue
        name = bone_name(child)
        if _hull_system(name) and not child.find_all("bone") and not child.find_all("VolumeView") and not child.find("LODView"):
            node.remove(child)
            removed.add(name)
        else:
            removed |= _prune_hull_systems(child)
    return removed


def _place_model_extension(root, model_name, *extra):
    """Bind the model where the original .mdl extension was declared.

    The engine reads a definition top-down: {bone}, volume and place settings
    are resolved against the model loaded so far, so the extension (and the
    armor include that configures its volumes) must precede them.
    """
    nodes = [node for node in root.find_all("extension") if node.args and node.args[0].lower().endswith(".mdl")]
    new_nodes = []
    for node in (block("{extension "+quote(model_name)+"}"), *extra):
        new_nodes += [Trivia("\n\t"), node]
    new_nodes.append(Trivia("\n"))
    if nodes:
        index = next(i for i, item in enumerate(root.items) if item is nodes[0])
        for node in nodes[1:]:
            root.remove(node)
    else:
        # After the leading global property includes and props, before anything else.
        index = 0
        for i, item in enumerate(root.items):
            if isinstance(item, Node):
                if item.head.casefold() in ("include", "props") and (item.head.casefold() == "props" or
                                                                    (item.args and item.args[0].startswith("/"))):
                    index = i + 1
                else:
                    break
    root.items[index:index+1 if nodes else index] = new_nodes


def _expand_local(resources, document, path, seen=None):
    """Inline local extensions/includes, leaving global property macros intact."""
    seen = set() if seen is None else set(seen)
    if path in seen:
        raise CompileError(f"Extension cycle at {path}")
    seen.add(path)
    container = entity_root(document) or document
    for node in list(container.children):
        if node.head.casefold() not in ("extension", "include") or not node.args:
            continue
        reference = node.args[0]
        if reference.startswith("/") or reference.lower().endswith(".mdl"):
            continue
        if reference.lower().endswith((".ext", ".inc", ".def")):
            linked = resolve_path(path, reference)
            try:
                extra = parse(resources.read(linked))
            except (KeyError, OSError) as error:
                raise CompileError(f"Missing local extension {linked}") from error
            _expand_local(resources, extra, linked, seen)
            inner = entity_root(extra) or extra
            index = next(i for i, item in enumerate(container.items) if item is node)
            container.items[index:index+1] = inner.items
    return document


def _merge_weaponry(container):
    """One Weaponry block with one declaration per place, as the engine reads it.

    A shared file often declares a place's crew and aiming and each variant adds
    its concrete weapon in a second block (57mm_qf6_mk2.inc + 57mm_qf6_mk2_early:
    {place "gun" {weapon "57mm_qf6" ...}}). Reading only the first block lost the
    weapon: the gun travelled without it and could not fire. A field declared
    again by the later block (the variant) replaces the earlier one.
    """
    blocks = [node for node in container.children if node.head.casefold() == "weaponry"]
    if not blocks:
        return
    first, places = blocks[0], {}
    for block_node in blocks:
        for node in list(block_node.children):
            name = node.args[0] if node.head.casefold() == "place" and node.args else None
            if name is None:
                if block_node is not first and not any(child.render() == node.render() for child in first.children):
                    first.append(node.clone())
                continue
            target = places.get(name)
            if target is None:
                if block_node is not first:
                    node = node.clone()
                    first.append(node)
                places[name] = node
                continue
            for child in node.children:
                for old in [old for old in target.children if old.head.casefold() == child.head.casefold()]:
                    target.remove(old)
                target.append(child.clone())
            if block_node is first:
                first.remove(node)
        if block_node is not first:
            container.remove(block_node)


def _state_template(resources, template, tag):
    """The <template>_<tag> destruction state. When several files share that
    name (MACE ships chi_ha_x in x/chi_ha_x and x/chi_ha_early_x), the state is
    the one in the vehicle's own wreck folder, <class>/x/<vehicle folder>_<tag>:
    chi_ha lives in chi_ha_early, so its wreck is x/chi_ha_early_x/chi_ha_x.def."""
    name = f"{template}_{tag}"
    paths = getattr(resources, "_entity_by_id", {}).get(name.casefold(), [])
    if len(paths) > 1:
        try:
            parent, folder = resources.resolve_entity(template)["definition_path"].rsplit("/", 2)[:2]
        except (KeyError, OSError, ValueError):
            return name
        wreck = f"{parent}/x/{folder}_{tag}".casefold()
        for own in ([p for p in paths if p.rsplit("/", 1)[0].casefold() == wreck],
                    [p for p in paths if p.rsplit("/", 2)[-2].casefold() == f"{folder}_{tag}".casefold()]):
            if len(own) == 1:
                return own[0]
    return name


def _load_entity(resources, template):
    info = resources.resolve_template(template)
    document = parse(resources.read(info["definition_path"]))
    entity_root(document)
    root = _expand_local(resources, document, info["definition_path"])
    container = entity_root(root)
    if container is None:
        raise CompileError(f"{template} has no game_entity block")
    _merge_weaponry(container)
    return info, root, container


def _macro_arguments(call):
    """name(value) arguments as source text; empty or block values included
    (e.g. bone() or off({targetable 0}) in ("crew_human" ...))."""
    values, pending = {}, None
    for item in call.items:
        if isinstance(item, Atom):
            pending = item.value if item is not call.atoms[0] else None
        elif isinstance(item, Node) and item.opener == "(" and pending:
            values[pending.casefold()] = item.render()[1:-1].strip()
            pending = None
    return values


def _include_defines(resources, document, base="", seen=None, depth=0):
    """Macro definitions visible through a document's include chain, in order.

    Global includes are absolute ("/properties/tank.ext"); those files include
    their siblings relatively ("tank_unarmed.ext" -> "tank_crew.ext").
    """
    seen = set() if seen is None else seen
    definitions = {}
    if depth > 12:
        return definitions
    for node in document.walk():
        if node.head.casefold() != "include" or not node.args:
            continue
        reference = node.args[0]
        if not reference.startswith("/") and not base:
            continue  # local includes of the entity were already inlined
        path = resolve_path(base, reference)
        if path.casefold() in seen:
            continue
        seen.add(path.casefold())
        try:
            included = parse(resources.read(path))
        except (KeyError, OSError, ValueError):
            continue
        definitions.update(_include_defines(resources, included, path, seen, depth+1))
        definitions.update({item.args[0]: item for item in included.find_all("define") if item.args})
    return definitions


_ROLE_HEADS = ("gunner", "charger", "commander", "shooter")


def _place_roles(place, implicit_gunner=False):
    """Crew place names a weapon place uses.

    Values may list several places ({charger "charger charger2" minWorkers 1}
    on the Pak 36); a weapon place without {gunner} uses the "gunner" place.
    """
    roles = set()
    for node in place.children:
        if node.head.casefold() in _ROLE_HEADS and node.args:
            roles |= set(node.args[0].split())
    if implicit_gunner and not any(node.head.casefold() == "gunner" for node in place.children):
        roles.add("gunner")
    return roles


def _restore_independent_mount_capabilities(root, model):
    """Enable native articulation capabilities after adding a free mount.

    A support conversion can leave -turret behind. Count operating joints,
    not barrels: a coax shares its turret and operator with the main gun.
    Nothing from the donor's chassis class or mobility is transferred.
    """
    stations = set()
    bones = model['bones']
    for weaponry in root.find_all('Weaponry'):
        for place in weaponry.find_all('place'):
            gunner = place.find('gunner')
            if place.find('weapon') is None or gunner is None or not gunner.args:
                continue
            kind = place.find('type')
            if kind is not None and any(a.casefold() == 'coaxial' for a in kind.args):
                continue
            operators = frozenset(gunner.args[0].casefold().split())
            references = [n.args[0] for head in ('LinkBone', 'bone', 'foresight', 'shooter')
                          for n in place.find_all(head) if n.args]
            references += [place.args[0] if place.args else '', model.get('anchor_name')]
            owner = next((name for name in references if name in bones), None)
            joints, seen = [], set()
            while owner in bones and owner not in seen:
                seen.add(owner)
                bone = bones[owner]
                if bone['node'].args and bone['node'].args[0].casefold() == 'revolute':
                    joints.append(owner)
                owner = bone['parent']
            if joints and operators:
                # The nearest operating joint belongs to this weapon. Using
                # the outermost one would merge a roof mount into its parent
                # turret even though they have independent joints/operators.
                stations.add((joints[0], operators))
    capabilities = {'turret'} if stations else set()
    if any(a != b and operators.isdisjoint(other)
           for a, operators in stations for b, other in stations):
        capabilities.add('multi_turret')
    for name in sorted(capabilities):
        # Remove stale explicit negation and append after inherited properties.
        for props in root.find_all('props'):
            props.items = [item for item in props.items
                           if not (isinstance(item, Atom) and item.value in (name, '-'+name))]
            if not props.args and not props.children:
                root.remove(props)
        root.append(block('{props "'+name+'"}'))


def _explicit_weapon_operators(resources, root):
    """Carry crew defaults out of the donor's global property includes.

    cannon.ext declares Weaponry/gun/gunner separately from the entity's
    concrete weapon. The recipient need not include that property file.
    Resolve only operator fields: importing the whole cannon class would
    overwrite chassis physics, crew and interaction behavior.
    """
    defaults, props = {}, set()
    seen = set()

    def visit(doc, base=''):
        for include in doc.find_all('include'):
            if not include.args or (not base and not include.args[0].startswith('/')):
                continue
            path = resolve_path(base, include.args[0])
            if path in seen:
                continue
            seen.add(path)
            try:
                visit(parse(resources.read(path)), path)
            except (KeyError, OSError):
                continue
        for weaponry in doc.find_all('Weaponry'):
            for place in weaponry.find_all('place'):
                if not place.args:
                    continue
                fields = defaults.setdefault(place.args[0], {})
                fields.update({n.head.casefold(): n for n in place.children
                               if n.head.casefold() in _ROLE_HEADS})
        for node in doc.find_all('props'):
            for name in node.args:
                if name.startswith('-'):
                    props.discard(name[1:])
                else:
                    props.add(name)

    visit(root)
    for weaponry in root.find_all('Weaponry'):
        for place in weaponry.find_all('place'):
            if not place.args:
                continue
            for field, node in defaults.get(place.args[0], {}).items():
                if place.find(field) is None:
                    place.append(node.clone())
    return props


def _articulation_ancestors(root, model, mount):
    """Driven revolute ancestors are part of a carried weapon's aiming chain."""
    driven = _rotating_bones(root)
    current, result = model['bones'][mount]['parent'], []
    while current:
        bone = model['bones'][current]
        if current in driven and 'revolute' in bone['node'].args[:-1]:
            result.append(current)
        current = bone['parent']
    return list(reversed(result))


def _wrap_articulation(mounted, model, mount, ancestors, target_local):
    """Retain each yaw pivot without importing the donor carriage or wheels.

    Fold intervening fixed frames into the next joint. At rest the selected
    module has EXACTLY the requested transform, including reflected frames;
    motion can then use the original joint axes instead of losing outer yaw.
    """
    if not ancestors:
        return mounted
    chain = ancestors + [mount]
    relative = multiply(inverse(model['bones'][ancestors[0]]['world']), model['bones'][mount]['world'])
    nodes = []
    for number, name in enumerate(ancestors):
        node = block('{bone revolute ' + quote(name) + '}')
        matrix = (multiply(target_local, inverse(relative)) if number == 0 else
                  multiply(inverse(model['bones'][chain[number-1]]['world']), model['bones'][name]['world']))
        _set_matrix(node, matrix)
        if nodes:
            nodes[-1].append(node)
        nodes.append(node)
    _set_matrix(mounted, multiply(inverse(model['bones'][ancestors[-1]]['world']), model['bones'][mount]['world']))
    nodes[-1].append(mounted)
    return nodes[0]


def _own_poses(root):
    """The pose each place holds by the definition's own boarding clips."""
    poses = {}
    for boarder in root.find_all("boarder"):
        for anm in boarder.find_all("anm"):
            forward = anm.find("forward")
            end = forward.find("end") if forward is not None else None
            if anm.args and end is not None and end.args:
                poses.setdefault(anm.args[0], end.args[0])
    return poses


def _append_inherited_crew(resources, root):
    """Add the crew a carried weapon gets from its shared includes.

    The game reads those includes first and the weapon's own definition after
    them, so the weapon's own boarding clips win: the M45 quad's gunner sits on
    its seat ("seat_m45_gunner"), not in the tripod MG pose of cannon_mgun.ext.
    """
    own = {anm.args[0] for boarder in root.find_all("boarder") for anm in boarder.find_all("anm") if anm.args}
    for node in _inherited_crew(resources, root):
        if node.head.casefold() == "boarder":
            for anm in list(node.find_all("anm")):
                if anm.args and anm.args[0] in own:
                    node.remove(anm)
            if not node.children:
                continue
        root.append(node)


def _is_crew_macro(name):
    # Armored cars such as the M3A1 use the unsuffixed native crew wrapper.
    name = name.casefold()
    return name == 'crew' or name.startswith('crew_')


def _inherited_crew(resources, root, base="", seen=None, depth=0):
    """Crew blocks a definition receives from its global includes.

    Carried weapons (tripod MGs, AT rifles) get their crew from shared files
    such as properties/cannon_mgun.ext ("crew_carried_cannon"); once the
    weapon sits on another vehicle those files are no longer included.
    """
    seen = set() if seen is None else seen
    nodes = []
    if depth > 12:
        return nodes
    for node in root.children:
        if node.head.casefold() != "include" or not node.args:
            continue
        reference = node.args[0]
        if not reference.startswith("/") and not base:
            continue
        path = resolve_path(base, reference)
        if path.casefold() in seen:
            continue
        seen.add(path.casefold())
        try:
            included = parse(resources.read(path))
        except (KeyError, OSError, ValueError):
            continue
        nodes += _inherited_crew(resources, included, path, seen, depth+1)
        for child in included.children:
            if child.head.casefold() in ("placer", "boarder") or (child.opener == "(" and _is_crew_macro(child.head)):
                nodes.append(child.clone())
    return nodes


def _unique_name(name, taken):
    """A free weapon place name numbered like native ones (gun -> gun2,
    mgun3 -> mgun4), never an invented suffix."""
    base = re.sub(r"\d+$", "", name)
    counter = int(re.search(r"\d+$", name).group()) + 1 if re.search(r"\d+$", name) else 2
    while f"{base}{counter}" in taken:
        counter += 1
    return f"{base}{counter}"


def _crew_role_names(resources):
    """Verified operator place names from the active game's crew properties.

    Numbered roles (gunner2, charger2...) exist in the native macros. Arbitrary
    suffixes do not: crewbar labels and worker animation binding use these
    place identities. Passenger and cannon-pushing driver seats are excluded.
    """
    names = {"gunner", "charger", "commander"}
    for path in ("properties/tank_crew.ext", "properties/car_crew.ext", "properties/cannon_crew.ext"):
        try:
            document = parse(resources.read(path))
        except (KeyError, OSError):
            continue
        for node in document.walk():
            candidates = node.args[:1] if node.head.casefold() == "place" else []
            if node.opener == "(":
                argument = _macro_arguments(node).get("place")
                if argument:
                    candidates += [argument.strip('"')]
            names.update(name for name in candidates if re.fullmatch(r"(?:gunner|charger|commander)\d*", name))
    return names


def _allocate_crew_roles(resources, roles, taken):
    """Resolve collisions using unused native operator roles, without aliasing.

    Reserve all donor roles first so two workers can never be assigned the
    same seat. Prefer another numbered role of the same job, then an unused
    native commander/gunner/charger seat. Never take the chassis driver.
    """
    available = _crew_role_names(resources)
    reserved, mapping = set(taken) | set(roles), {}
    for role in sorted(roles & taken):
        family = re.sub(r"\d+$", "", role)
        candidates = sorted(available, key=lambda name: (
            re.sub(r"\d+$", "", name) != family,
            {"commander": 0, "gunner": 1, "charger": 2}.get(re.sub(r"\d+$", "", name), 3),
            int(re.search(r"\d+$", name).group()) if re.search(r"\d+$", name) else 0, name))
        replacement = next((name for name in candidates if name not in reserved), None)
        if replacement is None:
            raise CompileError(f"No unused native crew role for {role}; free mount needs a crew assignment profile")
        mapping[role] = replacement
        reserved.add(replacement)
    return mapping


def _rename_crew_and_places(root, roles, places):
    """Rename donor crew places and weapon places that the chassis keeps.

    A free-mounted weapon is added next to the chassis' own weapons; its
    "gunner" or "gun" must not replace (or be confused with) the chassis'.
    """
    if not roles and not places:
        return
    weaponry = root.find("Weaponry")
    weapon_places = {id(node) for node in (weaponry.children if weaponry else [])}
    doors = {node.args[0] for boarder in root.find_all("boarder")
             for node in boarder.find_all("door") if node.args}
    for node in root.walk():
        head = node.head.casefold()
        if head == "place" and node.args:
            mapping = places if id(node) in weapon_places else roles
            if node.args[0] in mapping:
                node.atoms[1].set(mapping[node.args[0]])
        elif head in _ROLE_HEADS and node.args and set(node.args[0].split()) & set(roles):
            node.atoms[1].set(" ".join(roles.get(token, token) for token in node.args[0].split()))
        elif head == "link" and len(node.args) >= 2:
            for index, atom in enumerate(node.atoms[1:3]):
                if atom.value in roles and (index or atom.value not in doors):
                    atom.set(roles[atom.value])
        elif head == "anm" and node.args and node.args[0] in roles:
            # Boarder animation keys AND link references identify a crew place;
            # forward/end clips are human resource names and stay untouched.
            node.atoms[1].set(roles[node.args[0]])
        elif head == "workers":
            # Cannon attack animations are positional arrays parallel to these
            # workers. Rename their seat identities, not their human clips.
            for atom in node.atoms[1:]:
                if set(atom.value.split()) & set(roles):
                    atom.set(" ".join(roles.get(token, token) for token in atom.value.split()))
        elif head == "user" and node.args and node.args[0] in places:
            node.atoms[1].set(places[node.args[0]])
        elif head == "weapon" and node.args and node.args[0] in places and node.find("mask"):
            node.atoms[1].set(places[node.args[0]])


def _rotating_bones(root):
    """Bones the definition turns (traverse/elevation): {bone "x" {limits ..}}."""
    return {bone_name(node) for node in root.find_all("bone")
            if any(child.head.casefold() in ("limits", "speed", "speed2") for child in node.children)}


def _exclude_crew(root, excluded):
    """Leave crew roles of the module behind (editor: "who rides along").

    Removes their seats, boarding clips and door links, and drops them from
    every weapon's operator lists. A weapon left without loader is reloaded by
    its own gunner, as native one-man weapons are ({charger "gunner"}).
    """
    if not excluded:
        return
    weaponry = root.find("Weaponry")
    for place in (weaponry.find_all("place") if weaponry else []):
        gunner_node = next((node for node in place.children if node.head.casefold() == "gunner" and node.args), None)
        gunners = gunner_node.args[0].split() if gunner_node else ["gunner"]
        for node in list(place.children):
            if node.head.casefold() not in _ROLE_HEADS or not node.args:
                continue
            kept = [token for token in node.args[0].split() if token not in excluded]
            if kept:
                node.atoms[1].set(" ".join(kept))
            elif node.head.casefold() == "charger":
                node.atoms[1].set(" ".join(token for token in gunners if token not in excluded) or "gunner")
            else:
                place.remove(node)
    for parent in [root, *root.walk()]:
        for node in list(parent.children):
            head = node.head.casefold()
            if head == "placer":
                for place in list(node.find_all("place")):
                    if place.args and place.args[0] in excluded:
                        node.remove(place)
                if not node.children:
                    parent.remove(node)
            elif head == "anm" and parent.head.casefold() == "boarder" and node.args and node.args[0] in excluded:
                parent.remove(node)
            elif head == "link" and len(node.args) >= 2 and node.args[1] in excluded:
                parent.remove(node)


def _mans_main_weapon(places, role):
    """Whether `role` aims an untyped place: the main armament. Typed places
    (hull, coaxial, aa…) are secondary weapons; a place without {gunner}
    is aimed by "gunner"."""
    for place in places:
        if any(node.head.casefold() == "type" for node in place.children):
            continue
        gunners = [node.args[0] for node in place.children if node.head.casefold() == "gunner" and node.args]
        if role in ({token for value in gunners for token in value.split()} if gunners else {"gunner"}):
            return True
    return False


def _aiming_roles(places):
    """Crew places that aim a weapon; a place without {gunner} uses "gunner"."""
    roles = set()
    for place in places:
        gunners = [node.args[0] for node in place.children if node.head.casefold() == "gunner" and node.args]
        roles |= {token for value in gunners for token in value.split()} if gunners else {"gunner"}
    return roles


def _static_crew(mounted, crew_bones, rotating, world_of, parent_inverse, footprint, margin=0.06, spacing=10.0,
                 overrides=None, body=6.0):
    """Detach crew seats from the turning part of a carried weapon.

    On the ground a Pak's loaders stand beside the gun and turn with it; on a
    narrow chassis that swings them out over the side. Seats under a turning
    bone move to the static chassis bone the weapon hangs from (`parent_inverse`
    maps final world space into it) and are pulled inside the chassis footprint
    (min/max X and Y, shrunk by `margin` of each extent, and at least `body`
    units so a seated or kneeling body does not hang over the edge). Seats the
    player placed in the editor (`overrides`: bone -> {"position", "yaw"} in
    final model space) are used as given. Returns the detached bone nodes,
    re-expressed in the chassis bone's frame, plus a report.
    """
    overrides = overrides or {}
    detached, report = [], []

    def walk(node, turning):
        for child in list(node.find_all("bone")):
            name = bone_name(child)
            below = turning or name in rotating
            if name in crew_bones and below:
                node.remove(child)
                detached.append(child)
            else:
                walk(child, below)

    walk(mounted, bone_name(mounted) in rotating)
    placed = []
    for node in detached:
        name = bone_name(node)
        world = [row[:] for row in world_of(name)]
        before = [world[i][3] for i in range(3)]
        manual = overrides.get(name)
        if manual:
            position = [float(value) for value in manual["position"]]
            if manual.get("yaw") is not None:
                rotation = euler_matrix((0, 0, float(manual["yaw"])))
                world = [[rotation[i][0], rotation[i][1], rotation[i][2], world[i][3]] for i in range(3)] + world[3:]
            for axis in range(3):
                world[axis][3] = position[axis]
            placed.append((world[0][3], world[1][3]))
        elif footprint:
            (low_x, low_y), (high_x, high_y) = footprint
            pad_x = min(max((high_x-low_x)*margin, body), (high_x-low_x)*0.45)
            pad_y = min(max((high_y-low_y)*margin, body), (high_y-low_y)*0.45)
            world[0][3] = min(max(world[0][3], low_x+pad_x), high_x-pad_x)
            world[1][3] = min(max(world[1][3], low_y+pad_y), high_y-pad_y)
            # Two loaders pulled onto the same edge would overlap: step the
            # later one towards the middle (10 units, about half a metre).
            centre_x = (low_x+high_x)/2
            for _ in range(4):
                if not any(math.dist(spot, (world[0][3], world[1][3])) < spacing for spot in placed):
                    break
                step = spacing if world[0][3] < centre_x else -spacing
                world[0][3] = min(max(world[0][3]+step, low_x+pad_x), high_x-pad_x)
            placed.append((world[0][3], world[1][3]))
        _set_matrix(node, multiply(parent_inverse, world))
        report.append({"bone": name, "from": [round(v, 3) for v in before],
                       "to": [round(world[i][3], 3) for i in range(3)], "manual": bool(manual)})
    return detached, report


def _crew_positions(spec, roles, excluded, role_mapping, link_bones, model, bone_mapping, module_frame):
    """Resolve preview place keys into final seat bones, independently of fixing crew.

    New recipes store module-preview coordinates (origin at the source mount).
    Recipes without a space retain their original final-model coordinates.
    """
    if not isinstance(spec, dict):
        raise CompileError("Invalid crew seat positions")
    result = {}
    for place, value in spec.items():
        if place in excluded:
            continue  # Keep the editor adjustment for when this worker is included again.
        role = role_mapping.get(place, place)
        if role not in roles or not isinstance(value, dict):
            raise CompileError(f"Unknown crew seat: {place}")
        position = value.get("position")
        space = value.get("space", "world")
        if (space not in ("world", "module") or not isinstance(position, (list, tuple))
                or len(position) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                           or not math.isfinite(v) for v in position)):
            raise CompileError(f"Invalid crew seat position: {place}")
        bone = link_bones.get(role, role if role in model["bones"] else None)
        if not bone or bone not in model["bones"]:
            raise CompileError(f"Crew seat has no visible bone: {place}")
        yaw = value.get("yaw")
        if yaw is not None and (isinstance(yaw, bool) or not isinstance(yaw, (int, float)) or not math.isfinite(yaw)):
            raise CompileError(f"Invalid crew seat orientation: {place}")
        final = {"position": point(module_frame, position) if space == "module" else list(position), "yaw": yaw}
        rotation = value.get('rotation')
        if rotation is not None:
            if (not isinstance(rotation, (list, tuple)) or len(rotation) != 3
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in rotation)):
                raise CompileError(f"Invalid crew seat rotation: {place}")
            # Relative adjustment around the seat origin, keeping the entire
            # native frame (including reflections), just like the editor group.
            adjustment = euler_matrix(rotation)
            frame = multiply(module_frame, adjustment) if space == 'module' else multiply(adjustment, module_frame)
            final['orientation'] = [row[:3] for row in multiply(frame, model['bones'][bone]['world'])[:3]]
        name = bone_mapping.get(bone, bone)
        if name in result and result[name] != final:
            raise CompileError(f"Conflicting positions for shared crew seat: {place}")
        result[name] = final
    return result


def _edit_complete_crew(resources, root, model, transform):
    """Apply final-role seat positions on a complete saved design.

    Use the same role, bone and coordinate validation as module placement.
    Existing native linear frames remain intact for animation retargeting.
    """
    if not transform.get('crew_seats') and not transform.get('crew_exclude'):
        return []
    from .crew_visibility import seat_declarations
    declarations, _, _ = seat_declarations(resources, root)
    roles=set();links={}
    for place in declarations:
        if not place.args: continue
        group=place.find('group')
        if group is not None and group.args==['crew']: roles.add(place.args[0])
        for link in (n for n in place.children if n.head.casefold()=='linkbone'):
            links[place.args[0]]=link.args[0] if link.args else ''
    weaponry=root.find('Weaponry');places=weaponry.find_all('place') if weaponry is not None else []
    roles |= set().union(set(),*(_place_roles(p,implicit_gunner=True) for p in places))
    excluded=set(transform.get('crew_exclude') or []) & roles
    if excluded & (_aiming_roles(places)|{'driver'}):
        raise CompileError('Cannot leave behind the driver or crew that aims a weapon')
    overrides=_crew_positions(transform.get('crew_seats') or {},roles,excluded,{},links,model,{},identity())
    report=_position_crew(model['document'],overrides)
    _exclude_crew(root,excluded)
    return report


def _position_crew(document, overrides):
    """Move seats in their existing parent frames without touching weapon bindings.

    Preserve the native linear frame, including reflection. Reparse after each
    parent seat change so nested seats still reach their requested rest position.
    Animation export subsequently retargets these changed local rest matrices.
    """
    if not overrides:
        return []
    model = parse_model(document.render())
    def depth(name):
        count = 0
        while model["bones"][name]["parent"]:
            name = model["bones"][name]["parent"]
            count += 1
        return count
    if any(name not in model["bones"] for name in overrides):
        raise CompileError("Manual crew seat is missing from assembled model")
    report = []
    for name in sorted(overrides, key=depth):
        bone = model["bones"][name]
        world = [row[:] for row in bone["world"]]
        value = overrides[name]
        if value.get("yaw") is not None:
            # Legacy world-yaw overrides, only emitted by older recipes.
            rotation = euler_matrix((0, 0, float(value["yaw"])))
            for r in range(3):
                world[r][:3] = rotation[r][:3]
        if value.get('orientation') is not None:
            for r in range(3):
                world[r][:3] = value['orientation'][r][:]
        for axis in range(3):
            world[axis][3] = value["position"][axis]
        parent = model["bones"][bone["parent"]]["world"] if bone["parent"] else identity()
        node = next(node for node in document.find("skeleton").walk("bone") if bone_name(node) == name)
        _set_matrix(node, multiply(inverse(parent), world))
        report.append({"bone": name, "parent": bone["parent"], "position": value["position"]})
        model = parse_model(document.render())
    return report


def _passenger_macros(resources, root):
    """Macro names whose body declares passenger places (e.g. "seat_human")."""
    definitions = _include_defines(resources, root)
    for path in ("properties/tank_crew.ext", "properties/car_crew.ext", "properties/cannon_crew.ext"):
        try:
            document = parse(resources.read(path))
        except (KeyError, OSError):
            continue
        for node in document.find_all("define"):
            if node.args:
                definitions.setdefault(node.args[0], node)
    definitions.update({node.args[0]: node for node in root.find_all("define") if node.args})
    def passenger(name,seen=()):
        if name in seen or name not in definitions:return False
        for node in definitions[name].walk():
            if node.head.casefold()=='group' and node.args==['passenger']:return True
            if node.opener=='(':
                if _macro_arguments(node).get('group','').strip('"').casefold()=='passenger':return True
                if passenger(node.head,(*seen,name)):return True
        return False
    return {name for name in definitions if passenger(name)}


def _insert_before_passengers(resources, root, nodes):
    """Place crew blocks before the first passenger seat of the definition."""
    if not nodes:
        return
    passenger_macros = _passenger_macros(resources, root)

    def passenger(item):
        if not isinstance(item, Node):
            return False
        if item.opener == "(" and item.head in passenger_macros:
            return True
        if item.opener=='(' and _macro_arguments(item).get('group','').strip('"').casefold()=='passenger':
            return True
        return item.head.casefold() == "placer" and any(
            group.args == ["passenger"] for place in item.find_all("place") for group in place.find_all("group"))

    index = next((i for i, item in enumerate(root.items) if passenger(item)), None)
    if index is None:
        for node in nodes:
            root.append(node)
        return
    inserted = []
    for node in nodes:
        inserted += [node, Trivia("\n\t")]
    root.items[index:index] = inserted


def _prioritize_crew(resources,root):
    """Board operational crew before native or added passenger places.

    Crew expansion can leave chassis seats after passenger macros too. Apply
    to the complete export, retaining seat order within each group and keeping
    boarding links, poses and per-placer fields unchanged.
    """
    passenger_macros=_passenger_macros(resources,root)
    if not any(
        (n.opener=='(' and (n.head in passenger_macros or _macro_arguments(n).get('group','').strip('"').casefold()=='passenger'))
        or (n.head.casefold()=='placer' and any(p.find('group') and p.find('group').args==['passenger'] for p in n.find_all('place')))
        for n in root.children):return
    crew=[];roles=set()
    for placer in list(root.find_all('placer')):
        places=placer.find_all('place')
        selected=[p for p in places if p.find('group') and p.find('group').args==['crew']]
        if not selected:continue
        roles.update(p.args[0] for p in selected if p.args)
        if len(selected)==len(places):
            root.remove(placer);crew.append(placer)
        else:
            copied=placer.clone()
            for place in list(copied.find_all('place')):
                if not(place.find('group') and place.find('group').args==['crew']):copied.remove(place)
            for place in selected:placer.remove(place)
            crew.append(copied)
    # Native passenger macros also declare boarding links. Put operational
    # links first as well, without copying doors or animation definitions.
    for boarder in list(root.find_all('boarder')):
        if boarder.args:continue
        links=[n for n in boarder.find_all('link') if len(n.args)>1 and n.args[1] in roles]
        if not links:continue
        moved=block('{boarder}')
        for link in links:boarder.remove(link);moved.append(link)
        crew.append(moved)
        if not boarder.children:root.remove(boarder)
    _insert_before_passengers(resources,root,crew)


def _crew_definitions(resources, root):
    # The entity's own include chain decides which crew_human applies (cars
    # and tanks define different ones); the classic files are a fallback.
    definitions = _include_defines(resources, root)
    for path in ("properties/tank_crew.ext", "properties/car_crew.ext", "properties/cannon_crew.ext"):
        try:
            document = parse(resources.read(path))
        except (KeyError, OSError):
            continue
        for node in document.find_all("define"):
            if node.args:
                definitions.setdefault(node.args[0], node)
    for node in root.find_all("define"):
        if node.args:
            definitions[node.args[0]] = node
    return definitions


def _crew_expansion(resources, root):
    definitions = _crew_definitions(resources, root)

    def expand(call, depth=0):
        if depth > 16:
            raise CompileError("Crew macro recursion exceeded")
        definition = definitions.get(call.head)
        if definition is None:
            return [call]
        arguments = _macro_arguments(call)
        body = "".join(item.render() for item in definition.items[definition.items.index(definition.atoms[1])+1:])
        # Like the engine: every %name is replaced textually, missing ones by nothing.
        body = re.sub(r"%(\w+)", lambda match: arguments.get(match.group(1).casefold(), ""), body)
        result = []
        for child in parse(body).children:
            if child.opener == "(" and _is_crew_macro(child.head):
                result.extend(expand(child, depth+1))
            else:
                result.append(child)
        return result

    for call in list(root.children):
        if call.opener == "(" and _is_crew_macro(call.head):
            index = next(i for i, item in enumerate(root.items) if item is call)
            nodes = expand(call)
            root.items[index:index+1] = [item for node in nodes for item in (Trivia("\n"), node)]
    # Definitions that were embedded in a crew extension must not redefine global macros.
    for node in list(root.find_all("define")):
        if node.args and _is_crew_macro(node.args[0]):
            root.remove(node)


def _expand_place_macros(resources, root, accept):
    """Write out the non-crew macros that declare places, when accept(nodes) says so.

    Returns the places written out. Only crew/crew_ macros are expanded for the
    transplant; others (("gunner_open" ...), ("seat_human" ...)) stay calls.
    """
    definitions = _crew_definitions(resources, root)
    expanded = set()
    for call in list(root.children):
        definition = definitions.get(call.head) if call.opener == "(" and not _is_crew_macro(call.head) else None
        if definition is None or "placer" not in definition.render():
            continue
        arguments = _macro_arguments(call)
        body = "".join(item.render() for item in definition.items[definition.items.index(definition.atoms[1])+1:])
        body = re.sub(r"%(\w+)", lambda match: arguments.get(match.group(1).casefold(), ""), body)
        nodes = parse(body).children
        places = {place.args[0] for node in nodes if node.head.casefold() == "placer"
                  for place in node.find_all("place") if place.args}
        if not places or not accept(nodes, places):
            continue
        index = next(i for i, item in enumerate(root.items) if item is call)
        root.items[index:index+1] = [item for node in nodes for item in (Trivia("\n"), node)]
        expanded |= places
    return expanded


def _expand_weapon_seats(resources, root, roles):
    """Write out other macros that declare the seat of a module weapon's operator.

    A turret-roof MG manned by whoever climbs on the deck is a passenger place
    from ("gunner_open" place(gunner2) bone(gunner2) ...) on Stuarts, Shermans,
    US tank destroyers and MACE's Chi-Ha. Only crew_ macros are expanded for
    the transplant, so that seat stayed behind and nobody could fire the MG.
    Returns the places written out this way.
    """
    return _expand_place_macros(resources, root, lambda nodes, places: places <= roles)


def _seat_bones(nodes):
    """(node, bone) for every place linkbone and visor bone in these nodes."""
    found = []
    for node in nodes:
        for item in node.walk():
            head = item.head.casefold()
            if head == "linkbone" and item.args:
                found.append(item)
            elif head == "visor":
                bone = item.find("bone")
                if bone is not None and bone.args:
                    found.append(bone)
    return found


def _drop_orphan_slots(root, source_places, diagnostics):
    """An inventory weapon slot whose Weaponry place left with the replaced part.

    The Stuart keeps its roof MG in a slot: {weapon "mgun" {mask "mountable"}}
    and {item "browning_m19a4" ... {user "mgun"}}. With the turret replaced by a
    searchlight the "mgun" place was gone but the slot and its gun stayed, and
    the game crashed spawning the vehicle (Vidarr). The slot goes; its gun stays
    as cargo. Returns the slots removed, for the saved inventory as well.
    """
    places = {p.args[0] for w in root.find_all("Weaponry") for p in w.find_all("place") if p.args}
    removed = []
    for inventory in (n for n in root.find_all("extender") if n.args == ["inventory"]):
        for slot in list(inventory.find_all("weapon")):
            if slot.args and slot.find("mask") is not None and slot.args[0] not in places and slot.args[0] in source_places:
                inventory.remove(slot)
                removed.append(slot.args[0])
        for item in inventory.walk("item"):
            for user in list(item.find_all("user")):
                if user.args and user.args[0] not in places and user.args[0] in source_places:
                    item.remove(user)
                    if user.args[0] not in removed:
                        removed.append(user.args[0])
    for name in removed:
        diagnostics.append({"code": "orphan_slot_removed", "level": "info",
                            "message": f"Weapon slot {name!r} lost its place with the replaced part; its gun stays as cargo"})
    return removed


def _restore_boarding_clips(resources, root, sources, diagnostics):
    """A boarding link whose clip was removed with the replaced part's crew.

    The Stuart's deck rider kept its place and links, but its clip ("gunner2")
    left with the turret crew: whoever climbs on asks for a clip that does not
    exist. The source vehicle's clip comes back; without one the link boards
    with the engine's default.
    """
    defined = {a.args[0] for b in root.find_all("boarder") for a in b.find_all("anm") if a.args}
    native = {}
    for source in sources:
        for boarder in source.find_all("boarder"):
            for anm in boarder.find_all("anm"):
                if anm.args:
                    native.setdefault(anm.args[0], anm)
    target = root.find("boarder")
    for boarder in list(root.find_all("boarder")):
        for link in boarder.find_all("link"):
            reference = link.find("anm")
            if reference is None or not reference.args or reference.args[0] in defined:
                continue
            name = reference.args[0]
            if name in native and target is not None:
                target.append(native[name].clone())
                defined.add(name)
                diagnostics.append({"code": "boarding_clip_restored", "level": "info",
                                    "message": f"Boarding clip {name!r} restored for a seat that stayed"})
            else:
                link.remove(reference)


def _rebase_lost_seats(resources, root, source_bones, final_bones, mount, diagnostics):
    """A seat of either vehicle that hung from a bone left out of the new one
    (the chassis' replaced turret, the donor's hull) keeps its place, now on
    the new mount.

    The Stuart's deck rider (("gunner_open" place(gunner2) bone(gunner2)
    visorbone(mgun_rot) ...)) stands on the turret: replaced by a searchlight,
    the place pointed at a bone that no longer exists and the game crashed
    spawning the vehicle. The place stays (a saved campaign may hold a soldier
    in it), like a visor that looked from a removed bone.
    """
    final = {name.casefold() for name in final_bones}
    lost = lambda name: name.casefold() not in final and name in source_bones
    _expand_place_macros(resources, root, lambda nodes, places: any(lost(n.args[0]) for n in _seat_bones(nodes)))
    for node in _seat_bones(root.children):
        if lost(node.args[0]):
            diagnostics.append({"code": "seat_rebased", "level": "info",
                                "message": f"Seat bone {node.args[0]!r} is not part of the new vehicle; the seat is now on {mount!r}"})
            node.atoms[1].set(mount)


def seat_markers(resources, template):
    """Every seat of a unit for the 3D view: [{"place", "group", "bone"}].

    Crew and passenger places come from crew macros, from macros such as
    ("passenger_12_human") that call ("passenger_human" place(..) bone(..))
    in turn, and for carried weapons from shared crew files. Every macro whose
    body declares a place is expanded textually, as the engine does.
    """
    info, _, root = _load_entity(resources, template)
    _crew_expansion(resources, root)
    own_poses = _own_poses(root)
    if is_carried_weapon(info):
        _append_inherited_crew(resources, root)
    from .crew_visibility import seat_declarations
    declarations, boarders, _ = seat_declarations(resources, root)
    weaponry = root.find("Weaponry")
    weapon_roles = set().union(*(_place_roles(place, implicit_gunner=True) for place in weaponry.find_all("place")))         if weaponry is not None and weaponry.find_all("place") else set()
    aiming = _aiming_roles(weaponry.find_all("place")) if weaponry is not None else set()
    # The pose each seat holds: its boarding clip's last step, by place name
    # or through a door link ({link "emit1" "<place>" {anm "<clip>"}}).
    clips = dict(own_poses)
    for boarder in boarders:
        for anm in boarder.find_all("anm"):
            forward = anm.find("forward")
            end = forward.find("end") if forward is not None else None
            if anm.args and end is not None and end.args:
                clips.setdefault(anm.args[0], end.args[0])
    links = {}
    for link in (n for b in boarders for n in b.find_all("link")):
        clip = link.find("anm")
        if len(link.args) >= 2 and clip is not None and clip.args and clip.args[0] in clips:
            links.setdefault(link.args[1], clips[clip.args[0]])
    # The engine merges repeated place declarations. Later visual wrappers
    # and empty closed-seat overrides must not be discarded by first-ID wins.
    effective = {}
    for place in declarations:
        if not place.args or not place.args[0]: continue
        values = effective.setdefault(place.args[0], {})
        for node in place.children:
            if node.head.casefold() in ('group', 'linkbone'):
                values[node.head.casefold()] = node.args[0] if node.args else ''
    # A place linked to another place is the same soldier elsewhere: a towed
    # gun's crew pushing it (driver1 -> gunner) or riding it (seat1 -> driver1).
    alternates = {link.args[0] for b in boarders for link in b.find_all("link")
                  if len(link.args) >= 2 and link.args[0] in effective and link.args[1] in effective}
    seats = []
    for role, values in effective.items():
        if role in alternates:
            continue
        seats.append({'place': role, 'group': values.get('group', 'crew').casefold() or 'crew',
                      'bone': values.get('linkbone', ''),
                      'weapon': role in weapon_roles or role in ('gunner', 'commander', 'charger'),
                      'aims': role in aiming, 'pose': clips.get(role) or links.get(role)})
    return seats


def _filter_crew(root, roles, keep):
    """Filter each place/link by explicit role, preserving all unrelated passengers."""
    for container in list(root.children):
        if container.head.casefold() not in ("placer", "boarder"):
            continue
        for node in list(container.children):
            relevant = None
            if node.head.casefold() == "place" and node.args:
                relevant = node.args[0]
            elif node.head.casefold() == "link" and len(node.args) > 1:
                relevant = node.args[1]
            elif node.head.casefold() == "anm" and node.args and node.args[0] in roles | {"driver", "co_driver", "gunner", "charger", "commander"}:
                relevant = node.args[0]
            if relevant is not None and ((relevant in roles) != keep):
                container.remove(node)
        if not container.children:
            root.remove(container)


def _cannon_workers(root):
    """Native carriage states use alternate places for the same people."""
    states = {}
    for controller in root.find_all('extender'):
        if controller.args != ['cannon']:
            continue
        for state in ('attack', 'move', 'trailing'):
            for node in controller.find_all(state):
                workers = node.find('workers')
                if workers is None:
                    continue
                names = []
                for value in workers.args:
                    if value.casefold() == 'minworkers':
                        break
                    names.extend(value.split())
                states.setdefault(state, set()).update(names)
    return states


def _crew_roles(root):
    roles = {p.args[0] for pl in root.find_all('placer') for p in pl.find_all('place')
             if p.args and p.find('group') and p.find('group').args == ['crew']}
    states = _cannon_workers(root)
    if states.get('attack'):
        roles -= (states.get('move', set()) | states.get('trailing', set())) - states['attack']
    return roles


def _validate_carriage_crew(root):
    states = _cannon_workers(root)
    active = states.get('attack', set())
    if not active or not states.get('move'):
        return
    places = {}
    for pl in root.find_all('placer'):
        for p in pl.find_all('place'):
            if p.args:
                places.setdefault(p.args[0], []).append(p)
    required = set().union(*states.values())
    if required - places.keys():
        raise CompileError('La tripulación de empuje/remolque tiene puestos incompletos: '
                           + ', '.join(sorted(required - places.keys())))
    # The cannon controller animates these actors. Closed tank seats mark them
    # inactive, even if the role names happen to match the old cannon crew.
    if any(not any((link := p.find('LinkBone')) is not None and link.args and link.args[0]
                   for p in places[name]) for name in required):
        raise CompileError('El chasis con tripulación de empuje necesita conservar sus puestos visibles.')
    graph = {}
    for boarder in root.find_all('boarder'):
        for link in boarder.find_all('link'):
            if len(link.args) >= 2 and all(n in required for n in link.args[:2]):
                a, b = link.args[:2]
                graph.setdefault(a, set()).add(b)
                graph.setdefault(b, set()).add(a)
    for name in required - active:
        seen, pending = set(), [name]
        while pending:
            item = pending.pop()
            if item not in seen:
                seen.add(item)
                pending.extend(graph.get(item, set()) - seen)
        if not seen & active:
            raise CompileError('Falta el enlace nativo entre el puesto de empuje/remolque y su tripulante: ' + name)


def _restore_carriage_crew(root, source, model, original, removed, old_mount, new_mount, adjustment, diagnostics):
    """A manually moved carriage owns its workers, transitions and open poses.

    A replacement tank turret contributes weapons, not inactive tank actors.
    Retain removed crew/entry frames as bare sockets, without the old weapon.
    """
    places = [p for w in root.find_all('Weaponry') for p in w.find_all('place')]
    operators = set().union(set(), *(_place_roles(p, implicit_gunner=True) for p in places))
    attack = sorted(_cannon_workers(source).get('attack', set()) & _crew_roles(source))
    if operators - _crew_roles(source) and attack:
        # The carriage's own operators work the new weapons, as the gun and
        # commander of a searchlight or flak carriage do (Lazarus: a Pz.IV
        # turret's "charger" on the 150 cm searchlight). Tank seats the
        # carriage lacks would be crew who can never board it.
        def operator(role):
            if role.startswith('gunner'):
                return 'gunner' if 'gunner' in attack else attack[0]
            return next((r for r in attack if r != 'gunner'), attack[0])
        moved = {}
        for place in places:
            if 'gunner' in operators - _crew_roles(source) and place.find('gunner') is None:
                place.append(block('{gunner ' + quote(operator('gunner')) + '}'))
                moved['gunner'] = operator('gunner')
            for node in place.children:
                if node.head.casefold() in ('gunner', 'charger') and node.args and node.args[0] not in _crew_roles(source):
                    moved[node.args[0]] = operator(node.args[0])
                    node.atoms[1].set(moved[node.args[0]])
        diagnostics.append({'code': 'carriage_operators_reassigned', 'level': 'info', 'roles': moved,
                            'message': 'Weapon roles the carriage has no seat for were given to its own operators: '
                                       + ', '.join(f'{a} -> {b}' for a, b in sorted(moved.items()))})
        operators = set().union(set(), *(_place_roles(p, implicit_gunner=True) for p in places))
    if operators - _crew_roles(source):
        raise CompileError('La torreta requiere puestos que no tiene la tripulación del chasis: '
                           + ', '.join(sorted(operators - _crew_roles(source))))
    for node in list(root.children):
        if node.head.casefold() in ('placer', 'boarder'):
            root.remove(node)
    crew = [n.clone() for n in source.children if n.head.casefold() in ('placer', 'boarder')]
    current = parse_model(model['document'].render())
    refs = {n.args[0] for container in crew for n in container.walk()
            if n.head.casefold() in ('linkbone', 'door') and n.args and n.args[0]}
    aliases = {}
    for name in sorted(refs):
        if name in current['bones'] and name not in removed:
            continue
        if name not in original['bones']:
            raise CompileError('El puesto nativo del chasis refiere a un anclaje ausente: ' + name)
        serial = 0
        while 'cw_crew_' + str(serial) in current['bones']:
            serial += 1
        emitted = 'cw_crew_' + str(serial)
        aliases[name] = emitted
        parent = original['bones'][name]['parent']
        while parent and (parent in removed or parent not in current['bones']):
            if parent == old_mount:
                parent = new_mount
                break
            parent = original['bones'][parent]['parent']
        world = multiply(adjustment, original['bones'][name]['world'])
        local = multiply(inverse(current['bones'][parent]['world']), world) if parent else world
        socket = block('{bone ' + quote(emitted) + '}')
        _set_matrix(socket, local)
        container = next(n for n in model['document'].walk('bone') if bone_name(n) == parent) if parent else model['skeleton']
        container.append(socket)
        current = parse_model(model['document'].render())
    doors = {d.args[0] for container in crew if container.head.casefold() == 'boarder'
             for d in container.find_all('door') if d.args}
    for container in crew:
        for n in container.walk():
            head = n.head.casefold()
            is_frame = head in ('linkbone', 'door') or (head == 'link' and n.args and n.args[0] in doors)
            # A place-to-place transition names actor roles, not model bones.
            if is_frame and n.args and n.args[0] in aliases:
                n.atoms[1].set(aliases[n.args[0]])
        root.append(container)
    _validate_carriage_crew(root)
    diagnostics.append({'code': 'carriage_crew_preserved', 'level': 'info',
                        'message': 'Native carriage workers and push/tow transitions retained; alternate places are not extra crew.'})


def _patch_material(raw, mapping):
    """Only rewrite structurally validated, length-prefixed EPLY material names."""
    validate_mesh(raw)
    replacements = []
    for offset, material in mesh_materials(raw):
        if material not in mapping:
            continue
        old, new = material.encode("ascii"), mapping[material].encode("ascii")
        if len(new) > 31:
            raise CompileError("Generated EPLY material path exceeds the verified 31-byte engine convention")
        replacements.append((offset, offset+1+len(old), bytes([len(new)])+new))
    for start, end, value in sorted(set(replacements), reverse=True):
        raw = raw[:start]+value+raw[end:]
    # Validation after resizing detects accidental corruption.
    validate_mesh(raw)
    return raw


def _animation_map(raw):
    if len(raw) < 24 or raw[:4] != b"EANM" or raw[8:12] != b"FRMS" or raw[16:20] != b"BMAP":
        raise CompileError("Unsupported animation layout")
    frame_count, bone_count = struct.unpack_from("<I", raw, 12)[0], struct.unpack_from("<I", raw, 20)[0]
    if bone_count > 256 or frame_count > 1_000_000:
        raise CompileError("Animation layout exceeds safe bounds")
    cursor, names = 24, []
    for _ in range(bone_count):
        if cursor+4 > len(raw):
            raise CompileError("Truncated EANM bone map")
        length = struct.unpack_from("<I", raw, cursor)[0]
        cursor += 4
        if length > 255 or cursor+length > len(raw):
            raise CompileError("Invalid EANM bone name")
        names.append(raw[cursor:cursor+length].decode("ascii"))
        cursor += length
    return names, cursor, frame_count


def _animation_is_empty(raw):
    try:
        return not _animation_map(raw)[0]
    except CompileError:
        return False


_LEGACY_CHUNKS = {b"FRMN": 4, b"BONE": 4, b"MATR": 48, b"VISI": 4}


def _is_legacy_animation(raw):
    names, cursor, _ = _animation_map(raw)
    return raw[cursor:cursor+4] == b"FRMN"


def _legacy_layout(raw):
    """Older clips (header 0000 0400, e.g. fra/heavy/d2/engine_b.anm): tagged
    chunks FRMN <frame>, BONE <index>, then MATR (3x4 local matrix laid out
    like .mdl Matrix34: rotation columns, then translation) and/or VISI."""
    names, cursor, frame_count = _animation_map(raw)
    frames, track = [], None
    while cursor < len(raw):
        tag = raw[cursor:cursor+4]
        size = _LEGACY_CHUNKS.get(tag)
        if size is None or cursor+4+size > len(raw):
            raise CompileError("Unsupported legacy EANM chunk")
        value = cursor+4
        if tag == b"FRMN":
            index = struct.unpack_from("<I", raw, value)[0]
            if index >= frame_count:
                raise CompileError("EANM frame lies outside its timeline")
            frames.append({"index": index, "tracks": []})
            track = None
        elif tag == b"BONE":
            bone = struct.unpack_from("<I", raw, value)[0]
            if not frames or bone >= len(names):
                raise CompileError("Unsupported legacy EANM bone record")
            track = {"name": names[bone], "chunks": []}
            frames[-1]["tracks"].append(track)
        else:
            if track is None:
                raise CompileError("Legacy EANM data before its bone record")
            track["chunks"].append((tag, value))
        cursor = value+size
    return names, frames


def _legacy_matrix(raw, offset):
    values = struct.unpack_from("<12f", raw, offset)
    result = identity()
    for r in range(3):
        for c in range(3):
            result[r][c] = values[c*3+r]
        result[r][3] = values[9+r]
    return result


def _pack_legacy_matrix(matrix):
    return struct.pack("<12f", *[matrix[r][c] for c in range(3) for r in range(3)], *[matrix[r][3] for r in range(3)])


def _retarget_legacy_animation(raw, corrections, name_mapping, retained, indices, dropped):
    names, frames = _legacy_layout(raw)
    header = bytearray(raw[:24])
    struct.pack_into("<I", header, 20, len(retained))
    output = bytes(header)+b"".join(struct.pack("<I", len(name_mapping.get(name, name)))+name_mapping.get(name, name).encode("ascii")
                                    for name in retained)
    for frame in frames:
        written = b""
        for track in (track for track in frame["tracks"] if track["name"] in indices):
            correction = corrections[track["name"]]
            chunks = b""
            for tag, offset in track["chunks"]:
                if tag == b"MATR":
                    rotated = any(abs(correction[r][c]-(1 if r == c else 0)) > 1e-9 for r in range(3) for c in range(3))
                    if rotated:
                        # The column/row order of MATR rotations is unverified in game;
                        # translation-only corrections are order-independent. A rotated
                        # bone keeps its rest pose in this clip instead.
                        dropped.add(track["name"])
                        continue
                    chunks += b"MATR"+_pack_legacy_matrix(multiply(correction, _legacy_matrix(raw, offset)))
                else:
                    chunks += tag+raw[offset:offset+_LEGACY_CHUNKS[tag]]
            if chunks:
                written += b"BONE"+struct.pack("<I", indices[track["name"]])+chunks
        if written:
            output += b"FRMN"+struct.pack("<I", frame["index"])+written
    _legacy_layout(output)
    return output


def _animation_layout(raw):
    names, cursor, frame_count = _animation_map(raw)
    if raw[cursor:cursor+4] == b"FRMN":
        # Same track shape as FRM2: (bone, translation offset, None, 0, None).
        names, frames = _legacy_layout(raw)
        return names, [{"index": frame["index"],
                        "tracks": [(track["name"],
                                    next((offset+36 for tag, offset in track["chunks"] if tag == b"MATR"), None),
                                    None, 0, None) for track in frame["tracks"]]}
                       for frame in frames]
    frames = []
    # FRMS is the timeline length; sparse clips omit intermediate FRM2 records.
    while cursor < len(raw):
        if raw[cursor:cursor+4] != b"FRM2" or cursor+7 > len(raw):
            raise CompileError("Unsupported EANM frame framing")
        count = raw[cursor+6]
        frame_index = struct.unpack_from("<H", raw, cursor+4)[0]
        if frame_index >= frame_count:
            raise CompileError("EANM frame lies outside its timeline")
        cursor += 7
        entries = []
        for index in range(count):
            if cursor+3 > len(raw):
                raise CompileError("Truncated EANM track header")
            bone_index = raw[cursor]
            cursor += 1
            flags = struct.unpack_from("<H", raw, cursor)[0]
            cursor += 2
            # 1 position, 2 rotation; 4, 8 and 16 carry no payload; 32 is a
            # length-prefixed mesh-deformation block (wreck clips such as
            # panzerjager1_x/engine_b.anm, propellers), kept byte-for-byte.
            if bone_index >= len(names) or flags & ~63:
                raise CompileError("Unsupported EANM flags or bone index")
            position = cursor if flags & 1 else None
            cursor += 12 if flags & 1 else 0
            rotation = cursor if flags & 2 else None
            cursor += 12 if flags & 2 else 0
            deformation = None
            if flags & 32:
                if cursor+4 > len(raw):
                    raise CompileError("Truncated EANM deformation block")
                size = struct.unpack_from("<I", raw, cursor)[0]
                deformation = (cursor, 4+size)
                cursor += 4+size
            if cursor > len(raw):
                raise CompileError("Truncated EANM frame")
            entries.append((names[bone_index], position, rotation, flags, deformation))
        frames.append({"index": frame_index, "tracks": entries})
    if cursor != len(raw):
        raise CompileError("EANM contains unsupported trailing chunks")
    return names, frames


def _animation_tracks(raw):
    names, frames = _animation_layout(raw)
    return names, [frame["tracks"] for frame in frames]


def _quat_matrix(xyz):
    x, y, z = xyz
    squared = x*x+y*y+z*z
    if not math.isfinite(squared) or squared > 1.0001:
        raise CompileError("Unsupported EANM rotation: quaternion vector exceeds unit norm")
    w = math.sqrt(max(0, 1-squared))
    result = identity()
    result[0][:3] = [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)]
    result[1][:3] = [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)]
    result[2][:3] = [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]
    return result


def _matrix_quat_vector(matrix):
    # The native format stores quaternion XYZ; choose the canonical nonnegative W.
    trace = sum(matrix[i][i] for i in range(3))
    if trace > 0:
        s = math.sqrt(trace+1)*2
        w, x, y, z = s/4, (matrix[2][1]-matrix[1][2])/s, (matrix[0][2]-matrix[2][0])/s, (matrix[1][0]-matrix[0][1])/s
    else:
        axis = max(range(3), key=lambda i: matrix[i][i])
        j, k = (axis+1)%3, (axis+2)%3
        s = math.sqrt(max(1e-12,1+matrix[axis][axis]-matrix[j][j]-matrix[k][k]))*2
        vector = [0.0,0.0,0.0]
        vector[axis], vector[j], vector[k] = s/4, (matrix[j][axis]+matrix[axis][j])/s, (matrix[k][axis]+matrix[axis][k])/s
        w = (matrix[k][j]-matrix[j][k])/s
        x, y, z = vector
    norm = math.sqrt(w*w+x*x+y*y+z*z)
    sign = -1 if w < 0 else 1
    return [sign*x/norm,sign*y/norm,sign*z/norm]


def _is_identity(matrix):
    return all(abs(matrix[r][c]-(1 if r == c else 0)) < 1e-10 for r in range(4) for c in range(4))


def _retarget_animation(raw, corrections, name_mapping=None, dropped=None):
    """Re-express a clip in the assembled skeleton. Tracks that cannot be moved
    faithfully (a mesh deformation on a moved bone, a legacy rotation) are left
    out and their bone names added to `dropped`; the rest of the clip plays."""
    name_mapping = name_mapping or {}
    dropped = set() if dropped is None else dropped
    names, _, _ = _animation_map(raw)
    unchanged = all(name in corrections and _is_identity(corrections[name]) for name in names)
    if unchanged and all(name_mapping.get(name,name) == name for name in names):
        # Opaque mesh-deformation channels can pass through byte-for-byte only
        # when every source bone remains and no local rest transform changes.
        return raw
    retained = [name for name in names if name in corrections]
    indices = {name:index for index,name in enumerate(retained)}
    if _is_legacy_animation(raw):
        return _retarget_legacy_animation(raw, corrections, name_mapping, retained, indices, dropped)
    names, frames = _animation_layout(raw)
    header = bytearray(raw[:24])
    struct.pack_into("<I",header,20,len(retained))
    output = bytes(header)+b"".join(struct.pack("<I",len(name_mapping.get(name,name)))+name_mapping.get(name,name).encode("ascii") for name in retained)
    for frame in frames:
        entries = [entry for entry in frame["tracks"] if entry[0] in indices]
        for entry in [entry for entry in entries if entry[4] is not None and not _is_identity(corrections[entry[0]])]:
            dropped.add(entry[0])
            entries.remove(entry)
        if not entries:
            continue
        output += b"FRM2"+struct.pack("<HB",frame["index"],len(entries))
        for name, position, rotation, flags, deformation in entries:
            correction = corrections[name]
            output += struct.pack("<BH",indices[name],flags)
            if position is not None:
                moved = point(correction,struct.unpack_from("<3f",raw,position))
                output += struct.pack("<3f",*moved)
            if rotation is not None:
                rotated = multiply(correction,_quat_matrix(struct.unpack_from("<3f",raw,rotation)))
                output += struct.pack("<3f",*_matrix_quat_vector(rotated))
            if deformation is not None:
                output += raw[deformation[0]:deformation[0]+deformation[1]]
    _animation_layout(output)
    return output


class _Assets:
    def __init__(self, resources, target, sources, diagnostics):
        self.resources, self.target, self.sources, self.diagnostics = resources, target, sources, diagnostics
        self.names, self.files = {}, {}
        self.animation_context = {}
        self.animation_names = {}
        self.mesh_flips = {}
        # Materials a source mesh already lacks (e.g. MACE char_2c_xx/fan1_xx.ply):
        # the reference is kept as-is, so the part looks exactly like the original.
        self.missing_materials = set()
        # Volume bindings a source model already gets wrong (lorraine_37l_xx binds
        # "gun" to "gun1"; the bone is "gun1_xx"): accepted as in the original.
        self.missing_bones = set()

    def note_source(self, model):
        self.missing_bones |= {value["bone"] for value in model["volumes"].values()
                               if value["bone"] and value["bone"] not in model["bones"]}

    def preserve_mesh_faces(self, owner, relative_frame):
        """A reflected attachment must retain the source instance's visible side."""
        from .geometry import linear_determinant
        determinant=linear_determinant(relative_frame)
        if not math.isfinite(determinant) or abs(determinant)<1e-10:
            raise CompileError('Cannot preserve mesh faces through a singular fitting frame')
        self.mesh_flips[owner]=determinant<0

    def context(self, owner, source_model, final_model, bone_mapping=None, exclude=()):
        """exclude: source bones that were removed even if a same-named bone
        from the other model now exists (e.g. both turrets have "hatch1")."""
        bone_mapping = bone_mapping or {}
        self.animation_names[owner] = bone_mapping
        self.animation_context[owner] = {
            name: multiply(final_model["bones"][bone_mapping.get(name,name)]["local"], inverse(bone["local"]))
            for name,bone in source_model["bones"].items()
            if name not in exclude and bone_mapping.get(name,name) in final_model["bones"]}

    def prune(self, document):
        """Drop sequences whose animation lost every track: the engine aborts
        on empty animation frames (e.g. the removed gun's recoil clip)."""
        empty = set()
        for name, raw in self.files.items():
            if name.endswith(".anm"):
                try:
                    if not _animation_map(raw)[0]:
                        empty.add(name)
                except CompileError:
                    pass
        if not empty:
            return []
        dropped = []
        for animation in list(document.walk("animation")):
            for sequence in list(animation.find_all("sequence")):
                if any(file.args and file.args[0] in empty for file in sequence.walk("file")):
                    animation.remove(sequence)
                    dropped.append(sequence.args[0] if sequence.args else "?")
        for parent in [document, *document.walk()]:
            for animation in list(parent.find_all("animation")):
                if not animation.children:
                    parent.remove(animation)
        return dropped

    def add(self, path, owner, *, animation_delta=None):
        key = (path, owner)
        if key in self.names:
            return self.names[key]
        suffix = posixpath.splitext(path)[1].lower()
        digest = hashlib.sha256(path.encode()).hexdigest()[:10]
        name = f"{owner}_{digest}{suffix}"
        try:
            raw = self.resources.read(path)
        except (KeyError, OSError):
            raise  # not cached: a later reference must fail (or be skipped) the same way
        self.names[key] = name
        self.sources[path] = hashlib.sha256(raw).hexdigest()
        if suffix == ".ply":
            if self.mesh_flips.get(owner):
                from .geometry import reverse_mesh_winding
                raw=reverse_mesh_winding(raw)
            materials = sorted({name for _, name in mesh_materials(raw)})
            mapping = {}
            for material in materials:
                try:
                    mapping[material] = self.add(resolve_path(path, material), owner)
                except (KeyError, OSError):
                    self.missing_materials.add(material)
                    self.diagnostics.append({"code": "source_missing_material", "level": "warning", "path": path,
                                             "message": f"Source mesh already lacks material {material}; kept as in the original"})
            raw = _patch_material(raw, mapping)
        elif suffix in (".mtl", ".mdl", ".ext", ".def"):
            doc = parse(raw)
            self.rewrite(doc, path, owner)
            raw = doc.render().encode("utf-8")
        elif suffix == ".anm" and owner in self.animation_context:
            dropped = set()
            try:
                raw = _retarget_animation(raw, self.animation_context[owner], self.animation_names[owner], dropped)
            except CompileError as error:
                raise CompileError(f"Animation {path}: {error}") from error
            if dropped:
                self.diagnostics.append({"code": "animation_tracks_dropped", "level": "warning", "path": path,
                                         "message": "Tracks that cannot follow the moved bones were left out: "+", ".join(sorted(dropped))})
            try:
                _animation_layout(raw)
            except CompileError:
                self.diagnostics.append({"code": "unchanged_deformation_animation", "level": "info", "path": path,
                                         "message": "Native deformation channels preserved byte-for-byte; all local bone transforms unchanged"})
        elif suffix == ".anm" and animation_delta:
            changed = bytearray(raw)
            _, frames = _animation_tracks(raw)
            for entries in frames:
                for bone, offset, rotation, flags, _ in entries:
                    if bone in animation_delta:
                        delta = animation_delta[bone]
                        if offset is not None:
                            vector = struct.unpack_from("<3f", raw, offset)
                            struct.pack_into("<3f", changed, offset, *(vector[i]+delta[i] for i in range(3)))
            raw = bytes(changed)
        self.files[name] = raw
        return name

    def rewrite(self, doc, path, owner, animation_delta=None):
        # A sequence without {file} plays "<name>.anm" from its model's folder,
        # wherever it is declared (bone animations included: the MACE triple
        # Polsten's fire00-02). The workshop model lives elsewhere, so name the
        # source clip explicitly; left implicit, the game stopped loading the
        # campaign with "Resource not found .../fire00.anm".
        if path.lower().endswith(".mdl"):
            for sequence in list(doc.walk("sequence")):
                if sequence.args and sequence.find("file") is None:
                    try:
                        self.resources.read(resolve_path(path, sequence.args[0] + ".anm"))
                    except (KeyError, OSError):
                        continue
                    sequence.append(block("{file " + quote(sequence.args[0] + ".anm") + "}"))
        for node in doc.walk():
            for atom in node.atoms:
                suffix = posixpath.splitext(atom.value)[1].lower()
                if suffix not in (".ply", ".vol", ".anm", ".mtl", ".dds", ".tga", ".png") or atom.value.startswith("/"):
                    continue
                source = resolve_path(path, atom.value)
                try:
                    name = self.add(source, owner, animation_delta=animation_delta)
                except (KeyError, OSError):
                    # Texture names may be global resources, resolved by the active resource stack.
                    if suffix in (".dds", ".tga", ".png"):
                        self.diagnostics.append({"code": "global_texture", "level": "info", "path": source})
                        continue
                    raise CompileError(f"Missing required asset {source}")
                atom.set(name)


def _compile_wreck(resources, recipient_id, donor_id, recipient_mount, donor_mount,
                   intact_donor_model, local_mount, hull_transform, tag, new_id,
                   assets, roles, sources, volume_mapping, free_parent=None):
    """Compose the actual damaged source models; never substitute intact meshes.

    free_parent: the module hangs from this chassis bone (no ring removed).
    A donor whose destroyed state has no such piece (artillery wrecks are
    debris) leaves the chassis wreck without the weapon, as if blown off.
    """
    receiver, receiver_doc, receiver_root = _load_entity(resources, _state_template(resources, recipient_id, tag))
    model = parse_model(resources.read(receiver["model_path"]))
    assets.note_source(model)
    try:
        donor, donor_doc, donor_root = _load_entity(resources, _state_template(resources, donor_id, tag))
        donor_model = parse_model(resources.read(donor["model_path"]))
        assets.note_source(donor_model)
    except (KeyError, OSError, ValueError):
        donor = donor_root = None
        donor_model = {"bones": {}, "volumes": {}}
    blown_off = False
    if free_parent:
        receiver_names = set()
        parent = free_parent
        if free_parent not in model["bones"]:
            # The piece it stood on (a turret, a hatch ring) is not in this
            # wreck: the added weapon was blown off with it.
            parent, blown_off = free_mount_parent(model), True
            if parent not in model["bones"]:
                raise CompileError(f"Destruction state {tag} lacks a structural chassis bone")
    elif recipient_mount not in model["bones"]:
        # The chassis wreck has lost the mount (Hetzer _xx): the module went
        # with it, so the wreck is the chassis' own, without the new weapon.
        receiver_names, blown_off = set(), True
        parent = free_mount_parent(model)
    else:
        receiver_names = descendants(model["bones"], recipient_mount)
        parent = model["bones"][recipient_mount]["parent"]
    has_donor = not blown_off and donor_mount in donor_model["bones"] and donor_mount in intact_donor_model["bones"]
    donor_names = descendants(donor_model["bones"], donor_mount) if has_donor else set()
    bone_mapping = _volume_mapping(donor_names, set(model["bones"])-receiver_names-roles)
    receiver_parent = model["bones"][parent]["node"] if parent else model["skeleton"]
    copied = None
    if has_donor:
        copied = donor_model["bones"][donor_mount]["node"].clone()
        donor_names -= _prune_hull_systems(copied)
        _rename_bone_refs(copied, bone_mapping)
        # Keep the donor damage displacement relative to its intact rest posture.
        rest_correction = multiply(local_mount, inverse(intact_donor_model["bones"][donor_mount]["local"]))
        wreck_local = multiply(rest_correction, donor_model["bones"][donor_mount]["local"])
        _set_matrix(copied, wreck_local)
    if not free_parent and recipient_mount in model["bones"]:
        _remove_bone(model, recipient_mount)
    for role in sorted(roles):
        if role in model["bones"] and role not in receiver_names:
            _remove_bone(model, role)
    if not _is_identity(hull_transform):
        for root_bone in model["skeleton"].find_all("bone"):
            _set_matrix(root_bone, multiply(hull_transform, model["bones"][bone_name(root_bone)]["local"]))
    removed = {name for name,value in model["volumes"].items() if value["bone"] in receiver_names}
    module = {name for name,value in donor_model["volumes"].items() if value["bone"] in donor_names and not _hull_system(name)}
    components = _component_ids(donor_model, donor_names)
    reserved = _component_ids(model, set(model['bones']) - receiver_names)
    volume_mapping = _volume_mapping(module | components, (set(model['volumes']) - removed) | reserved, volume_mapping)
    if copied is not None:
        _rename_module_components(copied, {bone_mapping.get(n, n) for n in donor_names}, volume_mapping)
    for name in sorted(removed):
        model["document"].remove(model["volumes"][name]["node"])
    donor_nodes = []
    for name in sorted(module):
        node = _rename_volume(donor_model["volumes"][name]["node"], volume_mapping)
        _rename_bone_refs(node, bone_mapping)
        donor_nodes.append(node)
        model["document"].append(node)
    receiver_ids = _module_ids(model, receiver_names)
    donor_ids = {identifier for identifier in _module_ids(donor_model, donor_names) if not _hull_system(identifier)}
    for node in list(receiver_root.children):
        if node.head.casefold() == "volume" and node.args and node.args[0] in removed | receiver_ids:
            receiver_root.remove(node)
        elif node.opener == "(" and (node.head in receiver_ids or _values(node) & removed):
            receiver_root.remove(node)
    if donor_root is not None:
        _copy_module_armor(resources, donor_root, receiver_root, module, donor_ids, volume_mapping, bone_mapping, components)
    _bind_renamed_volumes(resources, receiver_root, {name:value for name,value in volume_mapping.items() if name in module | components})
    wreck_id = new_id+"_"+tag
    _place_model_extension(receiver_root, wreck_id+".mdl")
    if copied is not None:
        delta = {donor_mount: [wreck_local[r][3]-donor_model["bones"][donor_mount]["local"][r][3] for r in range(3)]}
        receiver_parent.append(copied)
    final_wreck = parse_model(model["document"].render())
    if copied is not None:
        assets.context("d"+tag, donor_model, final_wreck, bone_mapping)
    assets.context("h"+tag, model, final_wreck, exclude=receiver_names)
    if copied is not None:
        receiver_parent.remove(copied)
        assets.rewrite(copied, donor["model_path"], "d"+tag, animation_delta=delta)
        for node in donor_nodes:
            assets.rewrite(node, donor["model_path"], "d"+tag)
    for node in model["document"].children:
        if node not in donor_nodes:
            assets.rewrite(node, receiver["model_path"], "h"+tag)
    if copied is not None:
        receiver_parent.append(copied)
    # Put states beside intact model: entity identity, not folder spelling, resolves them.
    assets.prune(model["document"])
    assets.files[wreck_id+".mdl"] = model["document"].render().encode("utf-8")
    assets.files[wreck_id+".def"] = receiver_doc.render().encode("utf-8")
    for info in (receiver, donor):
        for path in ((info["definition_path"], info["model_path"]) if info else ()):
            sources[path] = hashlib.sha256(resources.read(path)).hexdigest()
    return {"id": wreck_id, "state": tag, "recipient_source": receiver["id"],
            "donor_source": donor["id"] if donor else None, "weapon_in_wreck": copied is not None, "model_file": wreck_id+".mdl", "definition_file": wreck_id+".def"}


def validate_linkbone_strings(raw,name):
    """The same engine string contract for exports and returning stored units."""
    document=parse(raw)
    inside_define={id(item) for define in document.walk('define') for item in define.walk()}
    for node in document.walk('LinkBone'):
        if not node.args and id(node) not in inside_define:
            raise CompileError('No se pudo validar un puesto de tripulación: LinkBone necesita un texto, '
                               'aunque esté vacío. Se evitó un cierre del juego (String expected!).\n'+name)


def _validate_export(files, source_missing=frozenset(), source_missing_bones=frozenset()):
    """Validate the complete in-memory export before a single file is written."""
    models = 0
    references = 0
    for name, raw in files.items():
        suffix = posixpath.splitext(name)[1].casefold()
        if suffix == ".ply":
            validate_mesh(raw)
            # Unresolved names render the engine's "1M" ruler placeholder texture.
            for _, material in mesh_materials(raw):
                if not material.startswith("/") and material not in files and material not in source_missing:
                    raise CompileError(f"Mesh {name} references missing material {material}")
        elif suffix in (".mdl", ".def", ".ext", ".mtl"):
            document = parse(raw)
            model = parse_model(raw) if suffix == ".mdl" else None
            models += model is not None
            if model:
                for volume, value in model["volumes"].items():
                    if value["bone"] and value["bone"] not in model["bones"] and value["bone"] not in source_missing_bones:
                        raise CompileError(f"Export {name} volume {volume} binds missing bone {value['bone']}")
            validate_linkbone_strings(raw,name)
            defines = [node for node in document.walk("define")]
            inside_define = {id(item) for define in defines for item in define.walk()}
            for node in document.walk():
                for atom in node.atoms:
                    ref = atom.value
                    if ref.startswith("%") and id(node) not in inside_define:
                        # The engine aborts reading a definition with a leftover macro parameter.
                        raise CompileError(f"Export {name} keeps unsubstituted macro parameter {ref}")
                    if ref.startswith(("/", "$", "%")):
                        continue
                    if posixpath.splitext(ref)[1].casefold() in (".mdl", ".ply", ".vol", ".anm", ".mtl", ".ext"):
                        if ref not in files:
                            raise CompileError(f"Export {name} has missing local dependency {ref}")
                        references += 1
                        if model and ref.endswith(".anm"):
                            names, _, _ = _animation_map(files[ref])
                            if not names:
                                raise CompileError(f"Export {name} references empty animation {ref} (engine abort)")
                            unknown = set(names)-set(model["bones"])
                            if unknown:
                                raise CompileError(f"Animation {ref} targets missing bones: {', '.join(sorted(unknown))}")
    _validate_bone_blocks(files)
    return {"models": models, "local_references": references, "files": len(files), "passed": True}


def _validate_bone_blocks(files):
    """A {bone "x" ...} block of a definition must name a bone of its model.

    The engine stops with "Bone x not found" (the game closes) otherwise; it has
    happened with a block left behind for a removed turret part. Case does not
    matter to the engine (base-game "Basis" vs "basis").
    """
    for name, raw in files.items():
        if not name.casefold().endswith(".def"):
            continue
        root = entity_root(parse(raw))
        if root is None:
            continue
        model_name = next((atom.value for node in root.walk() if node.head.casefold() in ("extension", "model")
                           for atom in node.atoms[1:] if atom.value.casefold().endswith(".mdl")), None)
        if model_name not in files:
            continue  # a shared base-game model: not produced here
        bones = {bone.casefold() for bone in parse_model(files[model_name])["bones"]}
        for node in root.children:
            if node.head.casefold() == "bone" and bone_name(node).casefold() not in bones:
                raise CompileError(f"Export {name} declares bone {bone_name(node)!r} that its model lacks "
                                   "(the game would close with 'Bone not found')")


def _compile_vehicle_edit(resources, template, transform, output_dir, new_id):
    """Keep a complete vehicle when there is no interchangeable turret.

    This is also the standalone commander-mount path: no donor, no additional
    crew, and no synthetic turret required on trucks/open vehicles.
    """
    info, document, root = _load_entity(resources, template)
    model = parse_model(resources.read(info['model_path']))
    source_model = parse_model(resources.read(info['model_path']))
    _crew_expansion(resources, root)
    if transform.get('module_poses') is not None:
        from .modular_assembly import apply_native_poses
        apply_native_poses(model,transform['module_poses'],root=root)
        model=parse_model(model['document'].render())
    support_contract = None
    removal_contract = None
    if transform.get('remove_module') is not None:
        from .modular_assembly import removal_mount
        from .support_vehicle import strip_turret
        removal_contract = strip_turret(resources, root, model, removal_mount(transform['remove_module']))
        model = parse_model(model['document'].render())
    if transform.get('support_vehicle') is not None:
        from .support_vehicle import configuration, strip_turret
        _, _, mount = configuration(resources, transform['support_vehicle'])
        mount = mount or model['anchor_name'] or ''
        transform = {**transform, 'support_vehicle': {**transform['support_vehicle'], 'remove_mount': mount}}
        support_contract = strip_turret(resources, root, model, mount)
        model = parse_model(model['document'].render())
    values = transform.get('hull') or {}
    adjustment = pose_matrix(values)
    # Finalizing a design also enters this path without a chassis adjustment.
    # Keep native root declarations/order in that case; do not normalize
    # Orientation/Position into Matrix34 without a requested movement.
    if not _is_identity(adjustment):
        for bone in model['skeleton'].find_all('bone'):
            name = bone_name(bone)
            _set_matrix(bone, multiply(adjustment,model['bones'][name]['local']))
    sources, diagnostics = {}, []
    assets = _Assets(resources, output_dir/'resource/entity/-vehicle/workshop'/new_id, sources, diagnostics)

    def copy_state(owner, doc, entity, mdl, original):
        assets.note_source(original)
        assets.context(owner,original,parse_model(mdl['document'].render()))
        assets.rewrite(mdl['document'],entity['model_path'],owner)
        for seq in mdl['document'].walk('sequence'):
            if not seq.args or seq.find('file'): continue
            path=resolve_path(entity['model_path'],seq.args[0]+'.anm')
            if resources.exists(path):
                seq.append(block('{file '+quote(assets.add(path,owner))+'}'))
        assets.rewrite(doc,entity['definition_path'],owner)
        assets.prune(mdl['document'])
        for path in (entity['model_path'],entity['definition_path']):
            sources[path]=hashlib.sha256(resources.read(path)).hexdigest()

    manual_crew = _edit_complete_crew(resources, root, model, transform)
    copy_state('h',document,info,model,source_model)
    if support_contract is not None:
        from .support_vehicle import apply_supply, add_cargo
        support_contract['profile'] = apply_supply(resources, root, transform['support_vehicle'], sources, assets)
        add_cargo(resources, model, transform['support_vehicle'], assets)
        diagnostics.append({'code': 'support_runtime_gate', 'level': 'warning',
                            'message': 'Native support service; battle supply, damage and persistence acceptance pending.'})
    if transform.get('joint_limits') is not None:
        from .articulation import apply_limits, skipped_limits_diagnostic
        model=parse_model(model['document'].render())
        skipped=apply_limits(model,root,transform['joint_limits'],{'hull':{n:n for n in model['bones']},'turret':{}})
        if skipped:diagnostics.append(skipped_limits_diagnostic(skipped))
    contract = None
    if transform.get('commander_mg') is not None:
        from .commander_mg import apply_mount
        contract=apply_mount(resources,root,model,transform['commander_mg'],assets,diagnostics)
    from .editable_recipe import fitting_parents
    fittings = fitting_parents(transform, contract)
    from .smoke_launchers import apply as apply_smoke
    smoke_launchers = apply_smoke(resources, root, model, fittings.get('smoke_launchers'), assets, diagnostics)
    from .point_fittings import apply_towing, apply_charge
    towing = apply_towing(root, model, transform.get('towing'), diagnostics)
    charge = apply_charge(resources, root, model, transform.get('charge'), diagnostics)
    from .rider_mg import apply as apply_riders
    rider_mgs = apply_riders(resources, root, model, fittings.get('rider_mgs'), assets, diagnostics)
    from .passenger_seats import apply as apply_passengers
    passenger_seats = apply_passengers(resources, root, model, transform.get('passenger_seats'), assets, diagnostics)
    from .extra_armor import apply as apply_extra_armor
    armor_fittings = apply_extra_armor(resources, root, model, fittings.get('extra_armor'), assets, diagnostics)
    from .crew_attachment import apply_crew_fixed
    crew_attachment=apply_crew_fixed(resources,root,model,transform.get('crew_fixed'))
    from .crew_visibility import apply_visibility
    crew_visibility = apply_visibility(root, transform.get('crew_hidden'), diagnostics=diagnostics, resources=resources)
    _place_model_extension(root,new_id+'.mdl')
    wrecks=[]
    for tag in ('x','xx'):
        try: state,doc,state_root=_load_entity(resources,_state_template(resources,template,tag))
        except (KeyError,OSError): continue
        state_model=parse_model(resources.read(state['model_path']))
        original_state = parse_model(resources.read(state['model_path']))
        if transform.get('module_poses') is not None:
            from .modular_assembly import apply_native_poses
            poses={n if n in state_model['bones'] else n+'_'+tag:p for n,p in transform['module_poses'].items()}
            apply_native_poses(state_model,poses,wreck=True)
            state_model=parse_model(state_model['document'].render())
        if support_contract is not None or removal_contract is not None:
            from .support_vehicle import strip_turret
            mount = (support_contract or removal_contract)['removed_mount']
            if mount and mount not in state_model['bones'] and mount+'_'+tag in state_model['bones']:
                mount += '_'+tag
            # A destroyed source can already have lost the selected turret.
            if mount in state_model['bones']:
                strip_turret(resources, state_root, state_model, mount, wreck=True)
            state_model = parse_model(state_model['document'].render())
        if not _is_identity(adjustment):
            for bone in state_model['skeleton'].find_all('bone'):
                _set_matrix(bone,multiply(adjustment,state_model['bones'][bone_name(bone)]['local']))
        copy_state('h'+tag,doc,state,state_model,original_state if support_contract is not None or removal_contract is not None or transform.get('module_poses') is not None else state_model)
        _place_model_extension(state_root,new_id+'_'+tag+'.mdl')
        assets.files[new_id+'_'+tag+'.mdl']=state_model['document'].render().encode('utf8')
        assets.files[new_id+'_'+tag+'.def']=doc.render().encode('utf8')
        wrecks.append({'state':tag,'module_lost':bool(contract),'native_chassis_preserved':True})
    final=parse_model(model['document'].render())
    _validate_carriage_crew(root)
    crew = _crew_roles(root)
    if not crew: raise CompileError('No verified crew capacity; this vehicle requires an explicit crew adapter')
    allowed=_allowed_damage_ids(resources,root,final)
    prepared={name:raw for name,raw in assets.files.items() if not(name.endswith('.anm') and _animation_is_empty(raw))}
    prepared[new_id+'.mdl']=model['document'].render().encode('utf8')
    _prioritize_crew(resources,root)
    prepared[new_id+'.def']=document.render().encode('utf8')
    validation=_validate_export(prepared,frozenset(assets.missing_materials),frozenset(assets.missing_bones))
    from .weapon_state import weapon_places
    profile={'weaponry_policy':'recipient','inventory_policy':'recipient','allowed_damage_ids':sorted(allowed),
             'weapon_places':weapon_places(root)}
    metadata={'schema_version':1,'compiler':'independent-gem-assembly','id':new_id,'recipient':template,'donor':template,
              'transform':transform,'crew_roles':sorted(crew),'crew_slots':len(crew),'source_hashes':sources,
              'crew_visibility':crew_visibility,'crew_attachment':crew_attachment,'manual_crew':manual_crew,
              'recipient_mount':'@full','donor_mount':'@full','bone_mapping':{},'bone_map':{},
              'state_profile':profile,'wrecks':wrecks,'validation':{'structural':'passed','dependencies':validation,
                'game_runtime':'pending','campaign_acceptance':'pending'}}
    if contract:
        metadata['commander_mg']=contract
        profile['commander_mg']=contract
    if support_contract is not None:
        metadata['operation'] = 'support_vehicle'
        metadata['support_vehicle'] = support_contract
        profile['weaponry_policy'] = 'reset'
        profile['retired_inventory_users'] = support_contract['removed_places']
    if removal_contract is not None:
        metadata['operation'] = 'remove_module'
        metadata['removed_module'] = removal_contract
        profile['retired_inventory_users'] = removal_contract['removed_places']
        diagnostics.append({'code': 'module_removal_runtime_gate', 'level': 'warning',
                            'message': 'Module removed structurally; battle and save/load acceptance pending.'})
    if smoke_launchers:
        metadata['smoke_launchers'] = smoke_launchers
        profile['smoke_launchers'] = smoke_launchers
        if profile['weaponry_policy'] == 'recipient': profile['weaponry_policy'] = 'reset'
    if towing:metadata['towing'] = towing
    if charge:metadata['charge'] = charge
    if rider_mgs:
        metadata['rider_mgs'] = rider_mgs
        profile['rider_mgs'] = rider_mgs
        if profile['weaponry_policy'] == 'recipient': profile['weaponry_policy'] = 'reset'
    if passenger_seats:
        metadata['passenger_seats'] = passenger_seats
    if armor_fittings is not None:
        metadata['extra_armor'] = armor_fittings
        # Full-vehicle editing preserves Weaponry state through plan_edit.
        # The campaign contract has never accepted a literal 'recipient' policy.
        if profile['weaponry_policy'] == 'recipient': profile['weaponry_policy'] = 'reset'
    for name,raw in list(prepared.items()):
        if name.endswith(('.def','.mdl','.ext','.mtl')):
            prepared[name]=raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
    metadata['file_hashes']={name:hashlib.sha256(raw).hexdigest() for name,raw in prepared.items()}
    assets.target.mkdir(parents=True)
    for name,raw in prepared.items(): (assets.target/name).write_bytes(raw)
    result={'id':new_id,'output_dir':str(output_dir),'entity_path':'-vehicle/workshop/'+new_id,
            'definition_path':'entity/-vehicle/workshop/'+new_id+'/'+new_id+'.def',
            'model_path':'entity/-vehicle/workshop/'+new_id+'/'+new_id+'.mdl',
            'files':[(p.relative_to(output_dir)).as_posix() for p in assets.target.rglob('*') if p.is_file()],
            'metadata':metadata,'diagnostics':diagnostics,'ready_for_campaign':True,'runtime_verified':False,
            'tool_version':__version__}
    (output_dir/('assembly_'+new_id+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return result


def _merge_sensors(recipient_root, donor_root, recipient_model, bone_mapping, donor_mount, diagnostics):
    """Vision of the assembled vehicle.

    The module's sensors travel with their bones renamed like the module's
    bones. A chassis visor that looked from a bone removed with the old turret
    is replaced by the module's visor of the same name, or else looks from the
    new mount (which turns like the turret did). A visor bone that does not
    exist can blind the vehicle or, worse, stop the game.
    """
    final_bones = {name.casefold() for name in parse_model(recipient_model["document"].render())["bones"]}
    donor_sensors = []
    for sensor in donor_root.find_all("sensor"):
        sensor = sensor.clone()
        _rename_bone_refs(sensor, bone_mapping)
        donor_sensors.append(sensor)
    donor_visors = {visor.args[0] for sensor in donor_sensors for visor in sensor.find_all("visor") if visor.args}
    mount = bone_mapping.get(donor_mount, donor_mount)
    for sensor in list(recipient_root.find_all("sensor")):
        for visor in list(sensor.find_all("visor")):
            bone = visor.find("bone")
            if bone is None or not bone.args or bone.args[0].casefold() in final_bones:
                continue
            if visor.args and visor.args[0] in donor_visors:
                sensor.remove(visor)
                action = "replaced by the module's visor"
            else:
                bone.atoms[1].set(mount)
                action = f"now looks from {mount!r}"
            diagnostics.append({"code": "visor_rebased", "level": "info",
                                "message": f"Chassis visor {visor.args[0] if visor.args else '?'!r} looked from removed bone "
                                           f"{bone.args[0]!r}; {action}"})
        if not sensor.children:
            recipient_root.remove(sensor)
    for sensor in donor_sensors:
        # Visors on donor parts that did not travel (its hull, other turrets).
        for visor in sensor.find_all("visor"):
            bone = visor.find("bone")
            if bone is not None and bone.args and bone.args[0].casefold() not in final_bones:
                diagnostics.append({"code": "visor_rebased", "level": "info",
                                    "message": f"Module visor {visor.args[0] if visor.args else '?'!r} looked from "
                                               f"{bone.args[0]!r}, which did not travel; now looks from {mount!r}"})
                bone.atoms[1].set(mount)
        recipient_root.append(sensor)


def compile_assembly(resources, recipient_template, donor_template, transform, output_dir, new_id):
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", new_id):
        raise CompileError("Entity ID must be 3-80 lower-case ASCII letters, digits or underscores")
    output_dir = Path(output_dir).resolve()
    for source in getattr(resources, "sources", []):
        root = Path(source["root"]).resolve()
        if output_dir == root or output_dir.is_relative_to(root):
            raise CompileError("Compilation staging must stay outside game and active resource roots")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise CompileError("Output directory must be new or empty; existing mod content is preserved")
    transform = transform or {}
    if transform.get('source_recipe'):
        from .editable_recipe import compile_reopened
        return compile_reopened(resources,recipient_template,donor_template,transform,output_dir,new_id)
    if transform.get('assembly_draft') is not None:
        from .draft import compile_draft
        return compile_draft(resources,recipient_template,donor_template,transform,output_dir,new_id)
    if transform.get('remove_module') is not None:
        if recipient_template != donor_template or any(transform.get(k) is not None for k in
                ('support_vehicle', 'gun_swap', 'gun_swaps', 'assembly_scope')):
            raise CompileError('Retirar una torreta usa un solo vehículo y no requiere un donante.')
        return _compile_vehicle_edit(resources,recipient_template,transform,output_dir,new_id)
    if transform.get('support_vehicle') is not None:
        if recipient_template != donor_template or transform.get('gun_swap') or transform.get('gun_swaps') or transform.get('commander_mg'):
            raise CompileError('La conversión de apoyo usa un solo vehículo y retira su torreta.')
        return _compile_vehicle_edit(resources,recipient_template,transform,output_dir,new_id)
    if transform.get('gun_swaps') is not None:
        from .gun_assembly import compile_guns
        return compile_guns(resources,recipient_template,donor_template,transform,output_dir,new_id)
    if transform.get('gun_swap') is not None:
        from .gun_assembly import compile_gun
        return compile_gun(resources,recipient_template,donor_template,transform,output_dir,new_id)
    if transform.get('commander_only'):
        if recipient_template != donor_template:
            raise CompileError('La edición completa requiere el mismo vehículo como origen.')
        return _compile_vehicle_edit(resources,recipient_template,transform,output_dir,new_id)
    recipient, recipient_doc, recipient_root = _load_entity(resources, recipient_template)
    donor, donor_doc, donor_root = _load_entity(resources, donor_template)
    _explicit_weapon_operators(resources, recipient_root)
    donor_effective_props = _explicit_weapon_operators(resources, donor_root)
    recipient_model = parse_model(resources.read(recipient["model_path"]))
    donor_model = parse_model(resources.read(donor["model_path"]))
    assembly_contract = None
    if transform.get('assembly_scope') is not None:
        if transform['assembly_scope'] != 'complete':
            raise CompileError('El alcance del conjunto de armamento no es válido.')
        from .modular_assembly import complete_contract
        assembly_contract = complete_contract(donor, donor_root, donor_model)
        transform = {**transform, 'donor_mount': assembly_contract['root']}
    # A chassis without a turret ring (trucks, half-tracks) or an explicit
    # free mount keeps all its parts; the module hangs from a structural bone.
    try:
        recipient_mount = recipient_mount_name(recipient_model, transform.get("recipient_mount"))
    except GeometryError as error:
        raise CompileError(str(error)) from error
    free, spot = free_mount_spec(recipient_mount)
    if spot and spot not in recipient_model["bones"]:
        raise CompileError(f"Selected mount spot {spot} does not exist on the chassis")
    free_parent = (transform.get("mount_parent") or (free_spot_parent(recipient_model, spot) if spot
                                                     else free_mount_parent(recipient_model))) if free else None
    # Weapons without a pivot of their own (Solothurn) move as a whole model.
    donor_mount = transform.get("donor_mount") or donor_mount_name(donor_model)
    carried = is_carried_weapon(donor) or donor_mount == whole_model_mount(donor_model)
    if (not free and recipient_mount not in recipient_model["bones"]) or donor_mount not in donor_model["bones"] \
            or (free and free_parent not in recipient_model["bones"]):
        raise CompileError("Both source models require a recognized or explicitly selected weapon mount")
    recipient_names = set() if free else descendants(recipient_model["bones"], recipient_mount)
    donor_names = descendants(donor_model["bones"], donor_mount)
    if free:
        recipient_places, recipient_unknown = [], []
    else:
        recipient_places, recipient_unknown = _selected_places(recipient_root, recipient_model, recipient_names)
    donor_places, donor_unknown = _selected_places(donor_root, donor_model, donor_names)
    donor_place_bones = {id(p):p.args[0] for p in donor_places if p.args and p.args[0] in donor_model['bones']}
    donor_place_names = {id(p):p.args[0] for p in donor_places if p.args}
    if recipient_unknown or donor_unknown:
        unknown = [node.args[0] if node.args else "?" for node in recipient_unknown+donor_unknown]
        raise CompileError("Weapon ownership requires a profile for places: "+", ".join(unknown))
    if not donor_places:
        raise CompileError("Selected donor module has no resolved weapon place")
    recipient_weaponry = recipient_root.find("Weaponry")
    donor_weaponry = donor_root.find("Weaponry")
    donor_complete = (not free and len(recipient_places) == len(recipient_weaponry.find_all("place") if recipient_weaponry else [])
                      and len(donor_places) == len(donor_weaponry.find_all("place") if donor_weaponry else []))
    diagnostics = []
    roles = set().union(*(_place_roles(place, implicit_gunner=True) for place in donor_places)) if donor_places else set()
    roles |= {name for name in donor_names if name in ("gunner", "commander", "charger")}
    roles.discard("driver")
    # Editor: module crew the player leaves behind (never whoever aims a weapon).
    excluded = {str(name) for name in (transform.get("crew_exclude") or [])} & roles
    if excluded & _aiming_roles(donor_places):
        raise CompileError("Cannot leave behind the crew that aims the module's weapon: "
                           + ", ".join(sorted(excluded & _aiming_roles(donor_places))))
    roles -= excluded
    previous_roles = set().union(*(_place_roles(place) for place in recipient_places)) if recipient_places else set()
    previous_roles |= {name for name in recipient_names if name in ("gunner", "commander", "charger")}
    if recipient_mount == "turret":
        previous_roles |= set(recipient_model["bones"]) & {"gunner", "commander", "charger"}
    if free:
        previous_roles = set()
    retained_roles = set().union(set(), *(_place_roles(place) for place in (recipient_weaponry.find_all("place") if recipient_weaponry else [])
                                          if not any(place is selected for selected in recipient_places)))
    _crew_expansion(resources, recipient_root)
    if not free:
        # A vehicle has one commander. When the module brings its own, a chassis
        # commander who also works a weapon that stays (the Befehls Tiger's,
        # with its hull flare pistol) leaves with the replaced turret, unless
        # its seat bone verifiably stays on the chassis: the module's commander
        # takes over the chassis weapon. Gunners and loaders keep their kept
        # weapons (a T-26's right-turret DT), as each turret has its own.
        stays = set()
        for placer in recipient_root.find_all("placer"):
            for place in placer.find_all("place"):
                link = place.find("linkbone")
                if (place.args and link is not None and link.args and link.args[0] in recipient_model["bones"]
                        and link.args[0] not in recipient_names):
                    stays.add(place.args[0])
        for role in (retained_roles & roles & {"commander"}) - stays:
            previous_roles.add(role);retained_roles.discard(role)
    previous_roles -= retained_roles | {"driver"}
    replaced_roles = previous_roles | roles
    carriage_source = None
    if not free and _cannon_workers(recipient_root).get('move') and _cannon_workers(recipient_root).get('attack'):
        _expand_place_macros(resources, recipient_root, lambda nodes, places: True)
        _validate_carriage_crew(recipient_root)
        carriage_source = recipient_root.clone()
    visibility_origins = {p.args[0]: {'part': 'hull', 'source_place': p.args[0]}
                          for pl in recipient_root.find_all('placer') for p in pl.find_all('place') if p.args}
    if carried:
        _append_inherited_crew(resources, donor_root)
    _crew_expansion(resources, donor_root)
    riders = _expand_weapon_seats(resources, donor_root, roles)
    donor_visibility_roles = {p.args[0] for pl in donor_root.find_all('placer')
                              for p in pl.find_all('place') if p.args}
    if carried:
        # The engine merges repeated declarations of a place: the weapon's own
        # {visor} plus the shared file's {group}{LinkBone}. Merge them into the
        # first one; for a property set twice the first (the weapon's) wins.
        first = {}
        for placer in list(donor_root.find_all("placer")):
            for place in list(placer.find_all("place")):
                if not place.args:
                    continue
                if place.args[0] not in first:
                    first[place.args[0]] = place
                    continue
                target = first[place.args[0]]
                present = {child.head.casefold() for child in target.children}
                for child in place.children:
                    if child.head.casefold() not in present:
                        target.append(child.clone())
                placer.remove(place)
            if not placer.children:
                donor_root.remove(placer)
        # A field gun's crew only "enter" their pose: on the ground they just
        # walk away. On a vehicle the engine dismounts crew (fire, abandon,
        # unload) through each seat's reverse clip; without one they stay
        # seated on a burning hull. Native vehicle seats use emit_* clips
        # (M5 halftrack gunner: emit_tank_1_hold); give the gun crew that one.
        for boarder in donor_root.find_all("boarder"):
            for anm in boarder.find_all("anm"):
                if (anm.args and anm.args[0] in roles and anm.find("forward") is not None
                        and anm.find("reverse") is None):
                    anm.append(block('{reverse {end "emit_tank_1_hold" 1}}'))
    _exclude_crew(donor_root, excluded)
    crew_rename = {}
    # Chassis weapons that stay (all of them on a free mount; on a turret swap
    # the ones outside the replaced turret, e.g. the T-26 M1931's right-turret
    # DT) keep their crew seats; the module's crew must not take them over.
    chassis_weapons = [place for place in (recipient_weaponry.find_all("place") if recipient_weaponry else [])
                       if not any(place is selected for selected in recipient_places)]
    chassis_crew = {place.args[0] for placer in recipient_root.find_all("placer")
                    for place in placer.find_all("place") if place.args} - previous_roles
    chassis_anm_names = {anm.args[0] for boarder in recipient_root.find_all("boarder")
                         for anm in boarder.find_all("anm") if anm.args} - previous_roles
    if not free:
        chassis_crew &= retained_roles | {"driver"}
        chassis_anm_names &= retained_roles
    if free or roles & chassis_crew:
        chassis_places = {place.args[0] for place in chassis_weapons if place.args}
        role_map = _allocate_crew_roles(resources, roles, chassis_crew | chassis_anm_names)
        place_map = {place.args[0]: _unique_name(place.args[0], chassis_places | {p.args[0] for p in donor_places if p.args})
                     for place in donor_places if place.args and place.args[0] in chassis_places}
        if ("gunner" in role_map and _mans_main_weapon(donor_places, "gunner")
                and not _mans_main_weapon(chassis_weapons, "gunner")
                and "gunner" not in set(recipient_model["bones"]) - recipient_names):  # a seat bone that stays
            # The game's direct control and weapon keys follow "gunner": it is
            # the main armament's operator in every native vehicle. When the
            # module brings the main gun and the chassis "gunner" only works
            # secondary weapons (T-20's hull DT), the chassis seat takes the
            # free numbered role instead, and the module's gun keeps "gunner".
            chassis_map = {"gunner": role_map.pop("gunner")}
            for place in chassis_weapons:
                if not any(node.head.casefold() == "gunner" for node in place.children):
                    place.append(block('{gunner "gunner"}'))
            _rename_crew_and_places(recipient_root, chassis_map, {})
            for original, replacement in chassis_map.items():
                if original in visibility_origins:
                    visibility_origins[replacement] = visibility_origins.pop(original)
            retained_roles = {chassis_map.get(role, role) for role in retained_roles}
            diagnostics.append({"code": "main_gunner_to_module", "level": "info",
                                "message": f"Chassis crew role 'gunner' (secondary weapons only) moved to {chassis_map['gunner']!r}; the module's main gun keeps 'gunner'",
                                "original": "gunner", "replacement": chassis_map["gunner"]})
        if "gunner" in role_map:
            # A weapon without {gunner} uses the "gunner" place, which now
            # belongs to the chassis (T-20 DT); name the renamed one explicitly.
            for place in donor_places:
                if not any(node.head.casefold() == "gunner" for node in place.children):
                    place.append(block('{gunner "gunner"}'))
        _rename_crew_and_places(donor_root, role_map, place_map)
        crew_rename = dict(role_map)
        for original, replacement in role_map.items():
            diagnostics.append({"code": "crew_role_reassigned", "level": "info",
                                "message": f"Donor crew role {original!r} assigned to native role {replacement!r}",
                                "original": original, "replacement": replacement})
        roles = {role_map.get(role, role) for role in roles}
        replaced_roles = set(roles) if free else previous_roles | roles
    donor_link_bones = {place.args[0]: place.find("LinkBone").args[0]
                        for placer in donor_root.find_all("placer") for place in placer.find_all("place")
                        if place.args and place.find("LinkBone") and place.find("LinkBone").args
                        and place.find("LinkBone").args[0]}  # LinkBone "" = internal place
    hull_values, turret_values = transform.get("hull", {}), transform.get("turret", {})
    hull_transform = pose_matrix(hull_values)
    turret_transform = pose_matrix(turret_values)
    if free:
        anchor = transform.get("mount") or free_anchor(resources, recipient_model, recipient["model_path"],
                                                     donor_model, donor["model_path"], donor_mount, spot)
    else:
        anchor = transform.get("mount", point(recipient_model["bones"][recipient_mount]["world"], [0, 0, 0]))
    source_anchor = point(donor_model["bones"][donor_mount]["world"], [0, 0, 0])
    # User offsets are relative to the automatic seat (module resting on the
    # recipient ring or deck), matching the editor's zero position.
    seat = 0.0
    if transform.get("auto_seat", True):
        if free or carried:
            recipient_ref = 0.0 if free else mount_seat(resources, recipient_model, recipient["model_path"], recipient_mount)
            donor_ref = donor_seat(resources, donor_model, donor["model_path"], donor_mount, carried)
            if recipient_ref is not None and donor_ref is not None:
                seat = round(recipient_ref - donor_ref, 3)
        else:
            seat = seat_offset(resources, recipient_model, recipient["model_path"], recipient_mount,
                               donor_model, donor["model_path"], donor_mount)
    donor_adjustment = multiply(multiply(multiply(translation(anchor), translation([0, 0, seat])), turret_transform),
                                translation([-x for x in source_anchor]))
    parent_name = free_parent if free else recipient_model["bones"][recipient_mount]["parent"]
    parent_world = recipient_model["bones"][parent_name]["world"] if parent_name else identity()
    new_mount_world = multiply(donor_adjustment, donor_model["bones"][donor_mount]["world"])
    local_mount = multiply(inverse(multiply(hull_transform, parent_world)), new_mount_world)
    mounted = donor_model["bones"][donor_mount]["node"].clone()
    donor_names -= _prune_hull_systems(mounted)
    _set_matrix(mounted, local_mount)
    recipient_parent = recipient_model["bones"][parent_name]["node"] if parent_name else recipient_model["skeleton"]
    if not free:
        _remove_bone(recipient_model, recipient_mount)
    removed_role_bones = set()
    for role in sorted(replaced_roles):
        if not free and role in recipient_model["bones"] and role not in recipient_names:
            _remove_bone(recipient_model, role)
            removed_role_bones.add(role)
        if role in roles and role not in donor_names:
            # Visible crew sit on a bone (the place's LinkBone, usually named
            # after the role); tank crews are mostly internal places without
            # one, which need no geometry and travel as plain crew places.
            anchor = donor_link_bones.get(role, role if role in donor_model["bones"] else None)
            if anchor is None:
                continue
            if anchor not in donor_model["bones"]:
                raise CompileError(f"Donor crew place {role} links missing bone {anchor}")
            if anchor in donor_names:
                continue
            extra = donor_model["bones"][anchor]["node"].clone()
            _set_matrix(extra, multiply(inverse(donor_model["bones"][donor_mount]["world"]), donor_model["bones"][anchor]["world"]))
            mounted.append(extra)
            donor_names.add(anchor)
    if not _is_identity(hull_transform):
        for root_bone in recipient_model["skeleton"].find_all("bone"):
            name = bone_name(root_bone)
            _set_matrix(root_bone, multiply(hull_transform, recipient_model["bones"][name]["local"]))
    # Donor parts named like parts the chassis keeps (fx points, details,
    # foresights...) would duplicate bones; rename the donor's side everywhere.
    kept_bones = set(recipient_model["bones"]) - recipient_names - removed_role_bones
    kept_slots = {p.args[0] for w in recipient_root.find_all('Weaponry') for p in w.find_all('place')
                   if p.args and p not in recipient_places}
    articulation = _articulation_ancestors(donor_root, donor_model, donor_mount) if carried else []
    mounted = _wrap_articulation(mounted, donor_model, donor_mount, articulation, local_mount)
    donor_geometry_names = set(donor_names)
    donor_names.update(articulation)
    if articulation:
        diagnostics.append({'code': 'aiming_ancestors_preserved', 'level': 'info',
                            'bones': articulation, 'message': 'Driven parent pivots retained without donor carriage geometry; battle aiming acceptance pending.'})
    bone_mapping = {name: value for name, value in _volume_mapping(donor_names, kept_bones | kept_slots).items()
                    if name not in roles | {"driver"}}
    _rename_bone_refs(mounted, bone_mapping)
    overrides = _crew_positions(
        transform.get("crew_seats") or {}, roles, excluded, crew_rename, donor_link_bones,
        donor_model, bone_mapping, multiply(donor_adjustment, translation(source_anchor)))
    static_crew = []
    if carried and transform.get("crew_static", True):
        # Loaders/commanders of a carried gun stay on the chassis instead of
        # swinging with the gun; whoever aims it stays on the gun.
        seat_bones = {bone_mapping.get(bone, bone) for bone in
                      (donor_link_bones.get(role, role) for role in roles - _aiming_roles(donor_places))}
        seat_bones -= {bone_mapping.get(bone, bone) for bone in
                       (donor_link_bones.get(role, role) for role in _aiming_roles(donor_places))}
        reverse_mapping = {value: key for key, value in bone_mapping.items()}
        kept_hull = [name for name in recipient_model["bones"] if name not in recipient_names | removed_role_bones]
        points = list(_points(resources, recipient_model, recipient["model_path"], kept_hull))
        footprint = None
        if points:
            moved_points = [point(hull_transform, value) for value in points]
            footprint = ((min(p[0] for p in moved_points), min(p[1] for p in moved_points)),
                         (max(p[0] for p in moved_points), max(p[1] for p in moved_points)))
        detached, static_crew = _static_crew(
            mounted, seat_bones, _rotating_bones(donor_root),
            lambda name: multiply(donor_adjustment, donor_model["bones"][reverse_mapping.get(name, name)]["world"]),
            inverse(multiply(hull_transform, parent_world)), footprint, overrides=overrides)
        for node in detached:
            recipient_parent.append(node)
    recipient_parent.append(mounted)
    pivot=pivot_of(turret_values)
    if any(pivot):
        mount_name=bone_mapping.get(donor_mount,donor_mount)
        final_frame=parse_model(recipient_model['document'].render())['bones'][mount_name]['world']
        centre=point(multiply(donor_adjustment,translation(source_anchor)),pivot)
        rebase_joint(recipient_model['document'],mount_name,point(inverse(final_frame),centre))
    camera_mapping, camera_sources = _camera_sockets(
        resources, donor_template, donor_model, recipient_model['document'], donor_mount,
        bone_mapping.get(donor_mount, donor_mount),
        {n: bone_mapping.get(n, n) for n in donor_names}, diagnostics)
    manual_crew = _position_crew(recipient_model["document"], overrides) if carriage_source is None else []
    original_ids = _module_ids(recipient_model, recipient_names)
    donor_ids = {identifier for identifier in _module_ids(donor_model, donor_geometry_names) if not _hull_system(identifier)}
    removed_volumes = {name for name, value in recipient_model["volumes"].items() if value["bone"] in recipient_names}
    donor_volumes = {name for name, value in donor_model["volumes"].items()
                     if value["bone"] in donor_geometry_names and not _hull_system(name)}
    for name in sorted(removed_volumes):
        recipient_model["document"].remove(recipient_model["volumes"][name]["node"])
    kept_volumes = set(recipient_model["volumes"])-removed_volumes
    components = _component_ids(donor_model, donor_geometry_names)
    reserved = _component_ids(recipient_model, set(recipient_model['bones']) - recipient_names)
    volume_mapping = _volume_mapping(donor_volumes | components, kept_volumes | reserved)
    _rename_module_components(recipient_model['document'], {bone_mapping.get(n, n) for n in donor_geometry_names}, volume_mapping)
    for name in sorted(donor_volumes):
        volume = _rename_volume(donor_model["volumes"][name]["node"], volume_mapping)
        _rename_bone_refs(volume, bone_mapping)
        recipient_model["document"].append(volume)
    # Armor remains independently owned by each volume/component.
    for node in list(recipient_root.children):
        if node.head.casefold() == "volume" and node.args and node.args[0] in removed_volumes:
            recipient_root.remove(node)
        elif node.opener == "(" and (node.head in original_ids or _values(node) & removed_volumes):
            recipient_root.remove(node)
    _copy_module_armor(resources, donor_root, recipient_root, donor_volumes, donor_ids, volume_mapping, bone_mapping, components)
    _bind_renamed_volumes(resources, recipient_root, volume_mapping)
    # Weaponry uses explicit mount ownership; additional chassis weapons stay in place.
    weaponry = recipient_root.find("Weaponry")
    if weaponry is None:
        weaponry = block("{Weaponry}")
        recipient_root.append(weaponry)
    for place in recipient_places:
        weaponry.remove(place)
    kept = next((item for item in weaponry.items if isinstance(item, Node) and item.head.casefold() == "place"), None)
    for place in donor_places:
        fallback = donor_place_bones.get(id(place))
        sight = _implicit_foresight(donor_place_names.get(id(place)), place, donor_model, donor_names)
        place = place.clone()
        if (fallback in bone_mapping or (fallback and place.args[0] != fallback)) and place.find('LinkBone') is None:
            # Native places implicitly use the bone of the same name. If the
            # bone or place was renamed to avoid a collision, bind it explicitly.
            place.append(block('{LinkBone '+quote(fallback)+'}'))
        if sight:
            # Same for the sight: renamed or added behind the chassis gun, the
            # donor gun would aim through the chassis gun's sight instead.
            place.append(block('{foresight '+quote(sight)+'}'))
        _rename_bone_refs(place, bone_mapping)
        if free and place.find('type') is None and place.find('basic') is None:
            # The implicit primary slot works while first in native Weaponry.
            # Added behind the chassis primary it needs the same explicit
            # selectable marker as the Nb.Fz's native second cannon (gun2).
            # Keep its weapon class, operators, ammunition and aiming intact.
            place.append(block('{basic}'))
        if free and place.find('ai_in_manual') is None:
            # Native independently crewed auxiliary guns (T-35) retain AI
            # control even when another place is the selected primary weapon.
            place.append(block('{ai_in_manual}'))
        if not free and place.find("type") is None and kept is not None:
            # A new turret's gun is the vehicle's main gun. Direct control and
            # the weapon bar offer only the first untyped place: after a kept
            # hull gun (Char B1/B2 "gun1") the turret gun fired for the AI
            # but the player could never pick it.
            at = next(i for i, item in enumerate(weaponry.items) if item is kept)
            weaponry.items[at:at] = [place, Trivia("\n")]
        else:
            weaponry.append(place)
    for node in list(recipient_root.find_all("bone")):
        if bone_name(node) in recipient_names:
            recipient_root.remove(node)
    for node in donor_root.find_all("bone"):
        if bone_name(node) in donor_names:
            node = node.clone()
            _rename_bone_refs(node, bone_mapping)
            recipient_root.append(node)
    # Preserve hull class/mobility; transfer weapon, vision and exposure properties.
    hull_props, donor_props = recipient_root.find("props"), donor_root.find("props")
    property_prefixes = ("muzzle_", "vision_", "nvw", "nwg")
    exposure = {"open_top", "with_gun", "sq_autocannon", "-hatch"}
    if hull_props and donor_props:
        for atom in list(hull_props.atoms[1:]):
            if atom.value.startswith(property_prefixes) or atom.value in exposure:
                hull_props.items.remove(atom)
        for atom in donor_props.atoms[1:]:
            if atom.value.startswith(property_prefixes) or atom.value in exposure:
                hull_props.items.extend((Trivia(" "), atom.__class__(atom.value, atom.raw, atom.quoted)))
    if 'with_gun' in donor_effective_props and any(p.find('weapon') and p.find('type') is None for p in donor_places):
        # A cannon inherits this capability from cannon.ext; an unarmed chassis
        # does not. Copy the capability, never the donor's cannon class/physics.
        for props in recipient_root.find_all('props'):
            props.items=[item for item in props.items if not (isinstance(item,Atom) and item.value=='-with_gun')]
        recipient_root.append(block('{props "with_gun"}'))
    # Inventory membership is tied to filling/user names, not calibre heuristics.
    replaced_families = {node.args[2].split()[0] for place in recipient_places for node in place.find_all("weapon")
                         if len(node.args) >= 3 and node.args[1] == "filling"}
    # Ammunition a kept weapon still loads stays, even when it shares the
    # generic first token ("ammo mgun_rus dt") with a replaced weapon's.

    def ammo_key(name):
        tokens = name.split()
        return " ".join(tokens[:-1]) if len(tokens) > 1 else name
    kept_ammo = {ammo_key(node.args[2]) for place in chassis_weapons for node in place.find_all("weapon")
                 if len(node.args) >= 3 and node.args[1] == "filling"}
    module_users = {place.args[0] for place in donor_places if place.args}
    for extender in list(recipient_root.find_all("extender")):
        if extender.args and extender.args[0] == "inventory":
            if donor_complete:
                recipient_root.remove(extender)
                continue
            box_node = extender.find("box")
            if box_node:
                for item in list(box_node.find_all("item")):
                    if item.args and ((item.args[0].split()[0] in replaced_families
                                       and ammo_key(item.args[0]) not in kept_ammo) or any(
                            user.args and user.args[0] in module_users for user in item.find_all("user"))):
                        box_node.remove(item)
    target_inventory = next((node for node in recipient_root.find_all("extender") if node.args and node.args[0] == "inventory"), None)
    for source_inventory in donor_root.find_all("extender"):
        if not source_inventory.args or source_inventory.args[0] != "inventory":
            continue
        if target_inventory is None:
            target_inventory = block('{extender "inventory" {box}}')
            recipient_root.append(target_inventory)
        target_box = target_inventory.find("box")
        if target_box is None:
            target_box = block("{box}")
            target_inventory.append(target_box)
        existing = {tuple(item.args) for item in target_box.find_all("item")}
        source_box = source_inventory.find("box")
        if source_box and source_box.find_all("item"):
            # The stowage comes with the module: the box holds at least what
            # the donor's did. A searchlight's {size 4 2} kept only one shell
            # of a Pz.IV turret's load (Lazarus); a box without size is unbounded.
            limits = [box.find("size") for box in (target_box, source_box)]
            if limits[0] is not None and limits[1] is None:
                target_box.remove(limits[0])
            elif limits[0] is not None:
                try:
                    widest = [str(max(float(a), float(b))).removesuffix(".0") for a, b in zip(limits[0].args, limits[1].args)]
                    for atom, value in zip(limits[0].atoms[1:], widest):
                        atom.set(value)
                except ValueError:
                    target_box.remove(limits[0])
        if source_box:
            for item in source_box.find_all("item"):
                if tuple(item.args) not in existing:
                    target_box.append(item.clone())
                    existing.add(tuple(item.args))
        for node in source_inventory.find_all("weapon"):
            target_inventory.append(node.clone())
    _filter_crew(recipient_root, replaced_roles, False)
    donor_crew = donor_root.clone()
    _filter_crew(donor_crew, roles, True)
    # Boarding animations belong to the hull being climbed. Donor definitions
    # with the same name (e.g. an armored car's "left" door entry) would
    # override the chassis crew's and leave them unable to board, so the
    # chassis keeps its own; donor-only ones lose {base} door clips, since the
    # donor's doors stay on the donor hull.
    chassis_anims = {anm.args[0].casefold() for boarder in recipient_root.find_all("boarder")
                     for anm in boarder.find_all("anm") if anm.args}
    # The engine seats crew in declaration order: module crew must come before
    # the chassis' passenger seats, or soldiers fill the deck seats first and
    # the gunner/commander places stay empty (seen on BT-2 + Flakpanzer I).
    crew_nodes = []
    rider_links = [link for boarder in donor_crew.find_all("boarder") for link in boarder.find_all("link")
                   if len(link.args) > 1 and link.args[1] in riders]
    if rider_links:
        # The rider climbs on through the donor hull's doors; one the chassis
        # lacks becomes one of the chassis' own doors, never a missing bone.
        bones = {name.casefold() for name in parse_model(recipient_model["document"].render())["bones"]}
        own_doors = [door.args[0] for boarder in recipient_root.find_all("boarder") for door in boarder.find_all("door")
                     if door.args and door.args[0].casefold() in bones]
        missing = set()
        for link in rider_links:
            door = link.args[0]
            if door.casefold() in bones:
                continue
            missing.add(door)
            if own_doors:
                link.atoms[1].set(own_doors[0])
                diagnostics.append({"code": "rider_door_rebased", "level": "info",
                                    "message": f"Rider door {door!r} is not on the chassis; boards through {own_doors[0]!r}"})
        used = {link.args[0] for boarder in donor_crew.find_all("boarder") for link in boarder.find_all("link") if link.args}
        for boarder in donor_crew.find_all("boarder"):
            for door in list(boarder.find_all("door")):
                if door.args and door.args[0] in missing and door.args[0] not in used:
                    boarder.remove(door)
        for boarder in donor_crew.find_all("boarder"):
            for link in list(boarder.find_all("link")):
                if link in rider_links and link.args[0].casefold() not in bones:
                    boarder.remove(link)  # no door at all on the chassis
    # Shared clip names ("left", "right") climb the chassis but end in the
    # pose of a seat: module crew sits in the module, so they climb like the
    # chassis crew and end in the module's pose (a Tiger I commander at its AA
    # MG, not the Befehls Tiger's at its binoculars).
    native_anms = {anm.args[0].casefold(): anm for boarder in recipient_root.find_all("boarder")
                   for anm in boarder.find_all("anm") if anm.args}
    def end_pose(anm):
        forward = anm.find("forward")
        end = forward.find("end") if forward is not None else None
        return end if end is not None and end.args else None
    for boarder in donor_crew.find_all("boarder"):
        for anm in list(boarder.find_all("anm")):
            native = native_anms.get(anm.args[0].casefold()) if anm.args else None
            pose = end_pose(anm) if native is not None else None
            if pose is None or (end_pose(native) is not None and end_pose(native).args[0] == pose.args[0]):
                continue
            users = [link for other in donor_crew.find_all("boarder") for link in other.find_all("link")
                     if len(link.args) > 1 and link.args[1] in roles and link not in rider_links
                     and (clip := link.find("anm")) is not None and clip.args == anm.args]
            if not users:
                continue
            own = native.clone()
            own.atoms[1].set("mw_crew_" + anm.args[0])
            forward = own.find("forward")
            if forward is None:
                forward = block("{forward}");own.append(forward)
            for old in forward.find_all("end"):
                forward.remove(old)
            forward.append(pose.clone())
            boarder.append(own)
            for link in users:
                link.find("anm").atoms[1].set("mw_crew_" + anm.args[0])
    for node in donor_crew.children:
        if node.head.casefold() == "placer":
            node = node.clone()
            _rename_bone_refs(node, bone_mapping)
            crew_nodes.append(node)
        elif node.head.casefold() == "boarder":
            boarder = node.clone()
            # A towed gun's links to its push/ride places ({link "driver1"
            # "gunner"}) stay: saved campaigns keep crew in them, and a vehicle
            # rebuilt without them crashed the game on load (Vidarr's Stuart +
            # 150cm searchlight).
            # A deck rider's clip ends in the pose at its MG: under the
            # chassis' clip of the same name it would sit in a hatch instead.
            for anm in boarder.find_all("anm"):
                if not anm.args or anm.args[0].casefold() not in chassis_anims:
                    continue
                users = [link for link in rider_links if (clip := link.find("anm")) is not None and clip.args == anm.args]
                if users:
                    own = anm.clone()
                    own.atoms[1].set("mw_" + anm.args[0])
                    boarder.append(own)
                    for link in users:
                        link.find("anm").atoms[1].set("mw_" + anm.args[0])
            for anm in boarder.find_all("anm"):
                if anm.args and anm.args[0].casefold() in chassis_anims:
                    boarder.remove(anm)
                    diagnostics.append({"code": "boarding_animation_kept", "level": "info",
                                        "message": f"Chassis boarding animation {anm.args[0]!r} kept over donor's"})
                else:
                    for step in anm.children:
                        for base in step.find_all("base"):
                            step.remove(base)
            if boarder.children:
                crew_nodes.append(boarder)
    _insert_before_passengers(resources, recipient_root, crew_nodes)
    if carriage_source is not None:
        _restore_carriage_crew(recipient_root, carriage_source, recipient_model,
                               parse_model(resources.read(recipient['model_path'])),
                               recipient_names | removed_role_bones, recipient_mount,
                               bone_mapping.get(donor_mount, donor_mount), hull_transform, diagnostics)
        manual_crew = _edit_complete_crew(resources, recipient_root,
                                         parse_model(recipient_model['document'].render()), transform)
    _merge_sensors(recipient_root, donor_root, recipient_model, bone_mapping, donor_mount, diagnostics)
    # All armor was structurally merged into DEF; export it also as independent EXT.
    armor = Document()
    for node in list(recipient_root.children):
        # A macro that declares a seat stays where it is, even when an argument
        # is named like a volume (Cromwell "seat_human ... animation(armor1)"):
        # moved into the EXT, deck seats came before the crew and a vehicle
        # bought in the shop boarded its tankmen on the deck (Potato Emperor).
        if node.opener == "(" and (_macro_arguments(node).get("place") or _is_crew_macro(node.head)):
            continue
        if node.head.casefold() == "volume" or (node.opener == "(" and (node.head in original_ids | donor_ids or _values(node) & (kept_volumes | donor_volumes))):
            armor.append(node.clone())
            recipient_root.remove(node)
    _place_model_extension(recipient_root, new_id+".mdl", block("(include "+quote(new_id+".ext")+")"))
    sources = {}
    sources.update(camera_sources)
    for info in (recipient, donor):
        for source_path in sorted(set([info["definition_path"], info["model_path"], *info.get("extension_paths", [])])):
            try:
                sources[source_path] = hashlib.sha256(resources.read(source_path)).hexdigest()
            except (OSError, KeyError):
                pass
    asset_target = output_dir / "resource" / "entity" / "-vehicle" / "workshop" / new_id
    assets = _Assets(resources, asset_target, sources, diagnostics)
    assets.note_source(recipient_model)
    assets.note_source(donor_model)
    pre_export_model = parse_model(recipient_model["document"].render())
    assets.context("d", donor_model, pre_export_model, bone_mapping)
    assets.context("h", recipient_model, pre_export_model, exclude=recipient_names)
    # Name and animation origins are tracked separately for each subtree.
    emitted_donor_volumes = {volume_mapping.get(name,name) for name in donor_volumes}
    donor_volume_nodes = [node for node in recipient_model["document"].find_all("volume") if node.args and node.args[0] in emitted_donor_volumes]
    donor_delta = {donor_mount: [local_mount[r][3]-donor_model["bones"][donor_mount]["local"][r][3] for r in range(3)]}
    rotation_changed = any(abs(local_mount[r][c]-donor_model["bones"][donor_mount]["local"][r][c]) > 1e-5 for r in range(3) for c in range(3))
    if rotation_changed:
        diagnostics.append({"code": "animation_rotation_retarget", "level": "info", "message": "Native quaternion rotation tracks retargeted to the new full rest transformation"})
    assets.rewrite(mounted, donor["model_path"], "d", animation_delta=donor_delta)
    for node in donor_volume_nodes:
        assets.rewrite(node, donor["model_path"], "d")
    for node in recipient_model["document"].children:
        if node in donor_volume_nodes:
            continue
        # Avoid traversing donor subtree twice during chassis asset remapping.
        if node is recipient_model["skeleton"]:
            recipient_parent.remove(mounted)
            assets.rewrite(node, recipient["model_path"], "h")
            recipient_parent.append(mounted)
        else:
            assets.rewrite(node, recipient["model_path"], "h")
    # Explicitly bind implicit global sequences to each origin's named animation.
    animation = recipient_model["skeleton"].find("animation")
    donor_animation = donor_model["skeleton"].find("animation")
    if animation is None:
        animation = block("{animation}")
        recipient_model["skeleton"].append(animation)
    # The engine plays a weapon's clips by its place number: "gun" fires
    # "fire", "gun1" "fire1", "gun2" "fire2" (T-35, KV-4, M3 Lee). A donor gun
    # renamed to stay apart from the chassis guns takes the clips of its new
    # number, and the chassis keeps the clips of the guns it still has
    # (TheOppsAreOntoMe: a T30 turret added to a KV-1 recoiled with the 76 mm).
    def place_number(name):
        match = re.fullmatch(r"gun(\d*)", name or "")
        return match.group(1) if match else None
    clip_rename = {}
    for place in donor_places:
        before, after = place_number(donor_place_names.get(id(place))), place_number(place.args[0] if place.args else None)
        if before is not None and after is not None:
            for clip in ("fire", "aim"):
                clip_rename[clip + before] = clip + after
    # Hatches: the donor's "open" replaces the chassis' only with its main
    # turret; an added or auxiliary turret leaves the chassis hatches alone.
    replaces_hatches = not free and recipient_mount == recipient_model["anchor_name"]
    chassis_sequences = {node.args[0] for node in animation.find_all("sequence") if node.args}
    donor_sequence_names = {node.args[0] for node in donor_animation.find_all("sequence") if node.args and node.args[0].startswith(("fire", "aim", "open"))
                            and (replaces_hatches or not node.args[0].startswith("open") or "open" not in chassis_sequences)} if donor_animation else set()
    emitted_names = {name: clip_rename.get(name, name) for name in donor_sequence_names}
    for node in list(animation.find_all("sequence")):
        if node.args and node.args[0] in emitted_names.values():
            animation.remove(node)
    if donor_animation:
        for sequence in donor_animation.find_all("sequence"):
            if sequence.args and sequence.args[0] in donor_sequence_names:
                clone = sequence.clone()
                if emitted_names[sequence.args[0]] != sequence.args[0]:
                    if not clone.find("file"):
                        # Implicit clips are named after the sequence: bind the donor's own file.
                        path = resolve_path(donor["model_path"], sequence.args[0] + ".anm")
                        try:
                            resources.read(path)
                        except (KeyError, OSError):
                            diagnostics.append({"code": "implicit_animation_absent", "level": "info", "path": path})
                            continue
                        clone.append(block("{file " + quote(sequence.args[0] + ".anm") + "}"))
                    clone.atoms[1].set(emitted_names[sequence.args[0]])
                # Generated vehicles bind sequences to explicit files; copy them too.
                assets.rewrite(clone, donor["model_path"], "d", animation_delta=donor_delta)
                animation.append(clone)
    for sequence in animation.find_all("sequence"):
        if not sequence.args or sequence.find("file"):
            continue
        source_info = donor if sequence.args[0] in emitted_names.values() else recipient
        owner = "d" if source_info is donor else "h"
        path = resolve_path(source_info["model_path"], sequence.args[0]+".anm")
        try:
            name = assets.add(path, owner, animation_delta=donor_delta if owner == "d" else None)
        except (KeyError, OSError):
            diagnostics.append({"code": "implicit_animation_absent", "level": "info", "path": path})
            continue
        sequence.append(block("{file "+quote(name)+"}"))
    # Physics: total mass is an authority of the chassis unless module mass is known.
    mass_node = recipient_root.find("mass")
    mass = float(mass_node.args[0]) if mass_node and mass_node.args else None
    chassis = recipient_root.find("chassis")
    physics = {"mass": mass, "mass_policy": "chassis_declared", "mobility_authority": recipient_template,
               "movement": chassis.render() if chassis else None,
               "module_mass": None, "module_mass_measured": False}
    diagnostics.append({"code": "mass_requires_component_data", "level": "warning", "message": "Source assets declare whole-vehicle mass, not separate turret mass; chassis mass retained explicitly"})
    lifecycle_required = any(node.args and node.args[0].startswith("/properties/") and node.args[0].endswith(".ext")
                             for node in recipient_root.find_all("include"))
    wrecks = []
    for tag in ("x", "xx"):
        try:
            resources.resolve_template(_state_template(resources, recipient_template, tag))
        except (KeyError, OSError):
            # The chassis has no such state (MACE m2a1_medium ships only _xx):
            # the new vehicle lacks it too, exactly like the original.
            diagnostics.append({"code": "destruction_state_absent", "level": "info", "state": tag,
                                "message": f"The chassis has no {tag} destruction state; none is created"})
            continue
        try:
            wrecks.append(_compile_wreck(resources, recipient_template, donor_template,
                                        recipient_mount, donor_mount, donor_model,
                                        local_mount, hull_transform, tag, new_id,
                                        assets, set() if free else replaced_roles, sources, volume_mapping,
                                        free_parent=free_parent))
        except (KeyError, OSError) as error:
            if lifecycle_required:
                raise CompileError(f"Missing required destruction source {tag}: {error}") from error
            diagnostics.append({"code": "destruction_contract_absent", "level": "info", "state": tag,
                                "message": "Sources have no declared global vehicle lifecycle; no destruction state synthesized"})
    if transform.get('joint_limits') is not None:
        from .articulation import apply_limits, skipped_limits_diagnostic
        recipient_model = parse_model(recipient_model['document'].render())
        skipped = apply_limits(recipient_model,recipient_root,transform['joint_limits'],
                               {'hull':{n:n for n in kept_bones},'turret':{n:bone_mapping.get(n,n) for n in donor_names}})
        if skipped:
            diagnostics.append(skipped_limits_diagnostic(skipped))
    commander_mg = None
    if transform.get('commander_mg') is not None:
        from .commander_mg import apply_mount
        commander_mg = apply_mount(resources, recipient_root, recipient_model,
                                   transform['commander_mg'], assets, diagnostics)
    for original in donor_visibility_roles if carriage_source is None else ():
        role = crew_rename.get(original, original)
        if role in roles:
            visibility_origins[role] = {'part': 'turret', 'source_place': original}
    from .editable_recipe import fitting_parents
    fittings = fitting_parents(transform, commander_mg)
    from .extra_armor import apply as apply_extra_armor
    armor_fittings = apply_extra_armor(resources, recipient_root, recipient_model, fittings.get('extra_armor'), assets, diagnostics)
    from .smoke_launchers import apply as apply_smoke
    smoke_launchers = apply_smoke(resources, recipient_root, recipient_model, fittings.get('smoke_launchers'), assets, diagnostics)
    from .point_fittings import apply_towing, apply_charge
    towing = apply_towing(recipient_root, recipient_model, transform.get('towing'), diagnostics)
    charge = apply_charge(resources, recipient_root, recipient_model, transform.get('charge'), diagnostics)
    from .rider_mg import apply as apply_riders
    rider_mgs = apply_riders(resources, recipient_root, recipient_model, fittings.get('rider_mgs'), assets, diagnostics)
    from .passenger_seats import apply as apply_passengers
    passenger_seats = apply_passengers(resources, recipient_root, recipient_model, transform.get('passenger_seats'), assets, diagnostics)
    from .crew_attachment import apply_crew_fixed
    crew_attachment=apply_crew_fixed(resources,recipient_root,recipient_model,transform.get('crew_fixed'))
    from .crew_visibility import apply_visibility
    crew_visibility = apply_visibility(recipient_root, transform.get('crew_hidden'), visibility_origins, diagnostics, resources=resources)
    final_model = parse_model(recipient_model["document"].render())
    if free:
        _restore_independent_mount_capabilities(recipient_root, final_model)
    allowed_ids = _allowed_damage_ids(resources,recipient_root,final_model,(armor,))
    recipient_source = _load_entity(resources, recipient_template)[2]
    orphan_slots = _drop_orphan_slots(recipient_root, {p.args[0] for w in recipient_source.find_all("Weaponry")
                                                       for p in w.find_all("place") if p.args}, diagnostics)
    # Seats of either vehicle on bones that did not make it into the new one.
    _rebase_lost_seats(resources, recipient_root,
                       set(parse_model(resources.read(recipient["model_path"]))["bones"]) | set(donor_model["bones"]),
                       final_model["bones"], bone_mapping.get(donor_mount, donor_mount), diagnostics)
    _crew_expansion(resources, recipient_source)
    _expand_place_macros(resources, recipient_source, lambda nodes, places: True)
    _restore_boarding_clips(resources, recipient_root, [recipient_source], diagnostics)
    _validate_carriage_crew(recipient_root)
    final_roles = _crew_roles(recipient_root)
    if carriage_source is None:
        final_roles |= roles | retained_roles
    if "driver" in final_model["bones"]:
        final_roles.add("driver")
    if not final_roles:
        raise CompileError("No verified crew capacity; this vehicle requires an explicit crew adapter")
    for sequence in assets.prune(recipient_model["document"]):
        diagnostics.append({"code": "empty_animation_dropped", "level": "info", "sequence": sequence,
                            "message": f"Sequence {sequence} only animated removed parts; dropped"})
    prepared = {name: raw for name, raw in assets.files.items()
                if not (name.endswith(".anm") and _animation_is_empty(raw))}
    prepared[new_id+".mdl"] = recipient_model["document"].render().encode("utf-8")
    _prioritize_crew(resources,recipient_root)
    prepared[new_id+".def"] = recipient_doc.render().encode("utf-8")
    prepared[new_id+".ext"] = armor.render().encode("utf-8")
    dependency_validation = _validate_export(prepared, frozenset(assets.missing_materials), frozenset(assets.missing_bones))
    metadata = {"schema_version": 1, "compiler": "independent-gem-assembly", "id": new_id,
                "recipient": recipient_template, "donor": donor_template, "transform": transform, "seat_offset": seat, "static_crew": static_crew,
                "manual_crew": manual_crew, "camera_bone_mapping": camera_mapping,
                "crew_visibility": crew_visibility, "crew_attachment": crew_attachment,
                "recipient_mount": recipient_mount, "donor_mount": donor_mount,
                "crew_roles": sorted(final_roles), "crew_slots": len(final_roles), "physics": physics,
                "wrecks": wrecks,
                "source_hashes": sources, "bone_mapping": {name: bone_mapping.get(name, name) for name in sorted(donor_names)},
                "bone_map": {name: name for name in sorted(donor_names)},
                "state_profile": {"removed_module_bones": sorted(recipient_names), "removed_module_volumes": sorted(removed_volumes),
                                  "donor_module_bones": sorted(donor_names), "donor_module_volumes": sorted(donor_volumes),
                                  "recipient_module_names": sorted(original_ids | removed_volumes),
                                  "donor_module_names": sorted(donor_ids | donor_volumes),
                                  "donor_name_map": {name: volume_mapping.get(name,name) for name in sorted(donor_ids | donor_volumes)},
                                  "weaponry_policy": "donor_complete" if donor_complete else "reset",
                                  "inventory_policy": "donor" if donor_complete else "reset",
                                  "allowed_damage_ids": sorted(allowed_ids), "damage_id_mapping": volume_mapping,
                                  "hull_authority": recipient_template, "turret_authority": donor_template},
                "validation": {"structural": "passed", "game_runtime": "pending", "campaign_acceptance": "pending"}}
    metadata["validation"]["dependencies"] = dependency_validation
    if assembly_contract is not None:
        metadata['weapon_assembly'] = assembly_contract
        diagnostics.append({'code': 'complete_assembly_runtime_gate', 'level': 'warning',
                            'message': 'Complete native artillery; carriage geometry is passive. Battle acceptance pending.'})
    from .weapon_state import weapon_places
    metadata['state_profile']['weapon_places'] = weapon_places(recipient_root)
    if orphan_slots:
        metadata['state_profile']['retired_inventory_users'] = sorted(orphan_slots)
    if smoke_launchers:
        metadata['smoke_launchers'] = smoke_launchers
        metadata['state_profile']['smoke_launchers'] = smoke_launchers
    if towing:metadata['towing'] = towing
    if charge:metadata['charge'] = charge
    if rider_mgs:
        metadata['rider_mgs'] = rider_mgs
        metadata['state_profile']['rider_mgs'] = rider_mgs
    if passenger_seats:
        metadata['passenger_seats'] = passenger_seats
    if armor_fittings is not None: metadata['extra_armor'] = armor_fittings
    if commander_mg:
        metadata['commander_mg'] = commander_mg
        metadata['state_profile']['commander_mg'] = commander_mg
    # Sources mix vanilla CRLF text with inserted LF blocks; ship one style.
    for name, raw in prepared.items():
        if name.lower().endswith((".def", ".mdl", ".ext", ".mtl")):
            prepared[name] = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    metadata["file_hashes"] = {name: hashlib.sha256(raw).hexdigest() for name,raw in prepared.items()}
    asset_target.mkdir(parents=True)
    for name, raw in prepared.items():
        (asset_target/name).write_bytes(raw)
    files = [str(path.relative_to(output_dir)).replace("\\", "/") for path in asset_target.rglob("*") if path.is_file()]
    result = {"id": new_id, "output_dir": str(output_dir), "entity_path": "-vehicle/workshop/"+new_id,
              "definition_path": "entity/-vehicle/workshop/"+new_id+"/"+new_id+".def",
              "model_path": "entity/-vehicle/workshop/"+new_id+"/"+new_id+".mdl", "files": files,
              "metadata": metadata, "diagnostics": diagnostics, "ready_for_campaign": True,
              "runtime_verified": False, "tool_version": __version__}
    (output_dir/f"assembly_{new_id}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
