"""Register original generated variants without replacing active-mod authorities.

Globals are merged from the EFFECTIVE virtual filesystem. The add-on's manifest
records source hashes and order; a different active stack requires regeneration.
This module writes only its output staging directory, never an installation.
"""
from __future__ import annotations

import hashlib
import copy
import json
import re
from pathlib import Path, PurePosixPath

from . import __version__
from .resources import ResourceIndex, ResourceError, normalize_path
from .syntax import Atom, Node, block, entity_root, parse, quote, bone_name
from .addon_readiness import ADDON_NAME


class RegistrationError(ValueError):
    pass


# Every generated vehicle of every campaign lives in this one local mod, so the
# player activates (or the tool activates) a single mod once.
ADDON_ID = "modular_workshop"
ADDON_MOD = ADDON_ID + ":0"
STACK_FILE = "workshop_stack.json"
# Files rebuilt from the active mod stack; everything else (models, manifests,
# texts) is independent of which mods are active.
GLOBAL_FILES = ("mod.info", "resource/set/registry/unit.reg", "resource/set/tp_control.set",
                "resource/set/interaction_entity/entity.set", "resource/set/multiplayer/units/roster_conquest.set")
NAMESPACE_DIRS = ("resource/set/interaction_entity/modular_workshop", "resource/set/multiplayer/units/modular_workshop")
RESERVES = "modular_workshop/_reserves"  # shared reserve squads (roster set + desc pot)


def is_own_mod(identifier: str) -> bool:
    """The shared addon or a legacy per-campaign one (modular_workshop_<hash>)."""
    name = str(identifier).rsplit(":", 1)[0].removeprefix("mod_").casefold()
    return name == ADDON_ID or name.startswith(ADDON_ID + "_")


def stack_signature(index: ResourceIndex) -> list[dict]:
    """The game and mods that generated globals were derived from (own addon excluded)."""
    return [{"id": source["id"], "version": source.get("version"), "fingerprint": source.get("fingerprint")}
            for source in index.sources if not is_own_mod(source["id"])]


def recorded_stack(addon: Path) -> list[dict] | None:
    path = Path(addon) / STACK_FILE
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8")).get("stack")
    manifests = sorted(Path(addon).glob("workshop_manifest_*.json"))
    if not manifests:
        return None
    sources = json.loads(manifests[0].read_text(encoding="utf-8")).get("active_sources", [])
    return [{"id": source["id"], "version": source.get("version"), "fingerprint": source.get("fingerprint")}
            for source in sources if not is_own_mod(source["id"])]


def write_stack(addon: Path, index: ResourceIndex, skipped: list | None = None) -> None:
    (Path(addon) / STACK_FILE).write_text(json.dumps({"schema": 1, "stack": stack_signature(index),
                                                      "skipped": skipped or []}, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")


def set_campaign(addon: str | Path, identifier: str, campaign: str) -> None:
    path = Path(addon) / ("workshop_manifest_" + identifier + ".json")
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data["campaign"] = campaign
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def describe_addon(addon: str | Path) -> str:
    """mod.info description listing every vehicle and its campaign.

    The game's mod list then shows what this one mod contains, instead of a
    separate mod per save (those would override each other's registries).
    """
    addon = Path(addon)
    vehicles, required = [], []
    for path in sorted(addon.glob("workshop_manifest_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        name = data.get("display_name") or data.get("id")
        vehicles.append(f"{name} [{data['campaign']}]" if data.get("campaign") else name)
        for source in data.get("active_sources", [])[1:]:
            if not is_own_mod(source["id"]) and source["name"] not in required:
                required.append(source["name"])
    shown = vehicles[:15] + ([f"y {len(vehicles) - 15} más"] if len(vehicles) > 15 else [])
    description = ("Vehículos: " + "; ".join(shown) + "." if vehicles else "Sin vehículos todavía.")
    if required:
        description += " Cargar después de: " + ", ".join(required) + "."
    description += f" Cursed Workshop {__version__}."
    info = "{mod\n" + "\n".join([f'\t{{name {quote(ADDON_NAME)}}}', f'\t{{desc {quote(description)}}}',
                                  '\t{minGameVersion "any"}', '\t{maxGameVersion "any"}']) + "\n}\n"
    _write(addon.resolve(), "mod.info", info)
    return description


def rebuild_registrations(index: ResourceIndex, addon: str | Path, *, identifiers=None) -> dict:
    """Re-register stored variants against the CURRENT mod stack.

    With identifiers, refresh only those variants and preserve other variants'
    records and namespace files while rebasing shared globals on current inputs.

    `index` must not contain the addon itself. Models are reused; unit/roster/
    sound/camera registrations are derived again because they copy files that
    belong to the active mods (MACE's roster is not vanilla's). Variants whose
    sources do not exist in this stack are reported and left unregistered.
    """
    addon = Path(addon).resolve()
    if any(is_own_mod(source["id"]) for source in index.sources):
        raise RegistrationError("Rebuild needs an index without the workshop addon")
    selected=set(identifiers) if identifiers is not None else None
    preserved={}
    if selected is not None:
        # Globals are shared, but other campaigns' own registrations stay intact.
        for relative,head in (("resource/set/registry/unit.reg","units"),
                              ("resource/set/tp_control.set","third_person_control"),
                              ("resource/set/interaction_entity/entity.set","entities"),
                              ("resource/set/multiplayer/units/roster_conquest.set","units")):
            path=addon/relative
            if not path.is_file():continue
            scope=parse(path.read_text(encoding="utf-8-sig"),lenient=True).find(head)
            if head=="third_person_control" and scope is not None:scope=scope.find("vehicle")
            nodes=[]
            for node in scope.children if scope is not None else []:
                own=node.head.casefold().split(" ")[0]
                reference=node.args[0] if node.head.casefold()=="include" and node.args else ""
                included=PurePosixPath(reference).stem
                if ((own.startswith("mw_") and own not in selected)
                        or (reference.startswith("modular_workshop/") and included not in selected)):
                    nodes.append(node.clone())
            preserved[relative]=(head,nodes)
    for relative in GLOBAL_FILES:
        (addon / relative).unlink(missing_ok=True)
    for relative,(head,nodes) in preserved.items():
        document,scope=_load_global(index,addon,relative.removeprefix("resource/"),head)
        if head=="third_person_control":scope=scope.find("vehicle")
        if scope is None:raise RegistrationError(f"Missing registration scope in {relative}")
        for node in nodes:scope.append(node)
        _write(addon,relative,document.render())
    for relative in NAMESPACE_DIRS:
        for path in sorted((addon / relative).glob("*")) if (addon / relative).is_dir() else []:
            if selected is None or path.stem in selected:path.unlink()
    registered, skipped = [], []
    # A variant built from another workshop vehicle names it as recipient or
    # donor, but this index has no workshop entities. Register it against the
    # native parts it is made of: a workshop chassis contributes its own chassis,
    # a workshop module its own donor's module.
    recipes = _recipes(addon)

    def native(template, role):
        return _native_template(recipes, template, role)

    for manifest_path in sorted(addon.glob("workshop_manifest_*.json")):
        if selected is not None and manifest_path.stem.removeprefix("workshop_manifest_") not in selected:continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        identifier = manifest["id"]
        if selected is not None and identifier not in selected:continue
        assembly_path = addon / f"assembly_{identifier}.json"
        if not assembly_path.is_file() and (addon / "assembly.json").is_file():
            assembly_path = addon / "assembly.json"
        try:
            assembly = json.loads(assembly_path.read_text(encoding="utf-8"))
            if assembly.get("id") != identifier:
                raise RegistrationError("stored assembly does not match the manifest")
            metadata = dict(assembly["metadata"])
            metadata["recipient"] = native(metadata.get("recipient"), "recipient")
            metadata["donor"] = native(metadata.get("donor"), "donor")
            for key in ('gun_camera_donor','draft_turret_donor'):
                if metadata.get(key):metadata[key]=native(metadata[key],'donor')
            if metadata.get('gun_assemblies'):
                import copy
                metadata['gun_assemblies']=copy.deepcopy(metadata['gun_assemblies'])
                for contract in metadata['gun_assemblies']:
                    if contract['donor'].get('template'):contract['donor']['template']=native(contract['donor']['template'],'donor')
            result = {"metadata": metadata,
                      "campaign_plan": {"reserve_squads": manifest.get("reserve_squads", [])}}
            register_variant(index, result, addon, identifier, manifest.get("display_name") or identifier,
                             shop=manifest.get("shop"))
            registered.append(identifier)
        except (RegistrationError, ResourceError, FileNotFoundError, KeyError, ValueError) as error:
            skipped.append({"id": identifier, "reason": str(error)})
    drop_missing_own_includes(addon)
    write_stack(addon, index, skipped)
    return {"registered": registered, "skipped": skipped}


def _recipes(addon) -> dict:
    """Stored assembly metadata of every workshop vehicle in an addon, by id."""
    recipes = {}
    for path in Path(addon).glob("assembly_*.json"):
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            recipes[stored["id"]] = stored["metadata"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return recipes


def _native_template(recipes, template, role):
    """The game (or mod) vehicle a workshop vehicle's chassis ("recipient")
    or module ("donor") ultimately comes from, following chained builds."""
    seen = set()
    while template in recipes and template not in seen:
        seen.add(template)
        template = _source_id(recipes[template].get(role)) or template
    return template


def _assembly_recipes(index, output):
    """The dry run and publication resolve the same owned source recipes."""
    recipes = {}
    for source in index.sources:
        if source.get('id') == ADDON_ID:
            recipes.update(_recipes(source['root']))
    recipes.update(_recipes(output))
    return recipes


def _chassis_ancestor(recipes, template):
    seen = set()
    while template in recipes:
        if template in seen:
            raise RegistrationError('Cyclic chassis assembly recipe: ' + template)
        seen.add(template)
        parent = _source_id(recipes[template].get('recipient'))
        if not isinstance(parent, str) or not parent:
            raise RegistrationError('Missing chassis provenance in assembly recipe: ' + template)
        template = parent
    if template.startswith('mw_'):
        raise RegistrationError('Missing chassis assembly recipe: ' + template)
    return template


def validate_dependencies(index: ResourceIndex, registration_metadata: dict) -> dict:
    """Compare a FRESH resource index with the compiled add-on's source stack.

    The caller should rebuild outside this function immediately before install.
    The previous version of the generated add-on is excluded, because replacing
    that owned output is the intended operation. Every other source must match.
    """
    own_id = str(registration_metadata.get("mod_id", "")).casefold()
    expected = [source for source in registration_metadata["active_sources"] if source["id"].casefold() != own_id]
    actual = [source for source in index.sources if source["id"].casefold() != own_id]
    if [source["id"] for source in expected] != [source["id"] for source in actual]:
        raise RegistrationError("Active source order changed after compilation; rebuild the variant")
    for previous, current in zip(expected, actual):
        if previous.get("fingerprint") != current.get("fingerprint") or previous.get("version") != current.get("version"):
            raise RegistrationError(f"Source changed after compilation: {previous['name']}; rebuild the variant")
    return {"valid": True, "verified_sources": [source["id"] for source in actual],
            "generated_addon_excluded": bool(own_id)}


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,96}", value):
        raise RegistrationError(f"Invalid generated unit ID: {value!r}")
    return value


def _write(output: Path, path: str, text: str | bytes):
    # Physical mod-relative paths retain the resource/ directory; virtual
    # normalize_path deliberately strips that prefix for indexed lookups.
    relative = normalize_path("output/" + path)[len("output/"):]
    target = (output / relative).resolve()
    if not target.is_relative_to(output):
        raise RegistrationError("Output path escapes staging directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        target.write_bytes(text)
    else:
        target.write_text(text, encoding="utf-8", newline="\n")


def _load_global(index, output, virtual, expected_head):
    original=index
    index=index.registration_sources()
    target = output / "resource" / virtual
    text = target.read_text(encoding="utf-8-sig") if target.is_file() else index.read_text(virtual)
    document = parse(text, lenient=True)  # mod global files may carry typos the game accepts
    scope = document.find(expected_head)
    if scope is None:
        raise RegistrationError(f"Expected {expected_head} root in effective {virtual}")
    if not target.is_file() and original is not index and original.exists(virtual):
        # Chained designs still need their own explicit view/camera rows. Keep
        # only generated IDs, never the old mod's globals or foreign includes.
        previous=parse(original.read_text(virtual),lenient=True).find(expected_head)
        destination=scope.find('vehicle') if expected_head=='third_person_control' else scope
        previous=previous.find('vehicle') if previous and expected_head=='third_person_control' else previous
        if previous and destination:
            for node in previous.children:
                if node.head.casefold().split(' ')[0].startswith('mw_') and not destination.find(node.head):
                    destination.append(node.clone())
    return document, scope


def _ensure_include(scope, reference):
    if not any(node.args and node.args[0].casefold() == reference.casefold() for node in scope.find_all("include")):
        scope.append(block("(include " + quote(reference) + ")"))


def _load_include_graph(index, start, *, generated=False):
    """Load only included files, in engine source order; do not scan unused mods."""
    from .cancellation import checkpoint
    original=index
    if not generated and hasattr(index,'registration_sources'):index=index.registration_sources()
    checkpoint()
    cache = index.__dict__.setdefault('_registration_graphs', {})
    cache_key=(start,'stored') if original is not index else start
    if cache_key in cache:
        records, provenance = cache[cache_key]
        # Reads enforce the resource index's loose-file change guards.
        for source in provenance:
            index.verify_unchanged(source['path'])
        return records, provenance
    seen = set()
    records = []
    provenance = []

    def visit(path):
        checkpoint()
        if path.casefold() in seen:
            return
        seen.add(path.casefold())
        text = index.read_text(path)
        provenance.append({"path": path, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                           "source": index.resolve(path).as_dict()})
        document = parse(text, lenient=True)
        for node in document.children:
            if node.head.casefold() in ("entities", "units", "third_person_control"):
                children = node.children
            else:
                children = [node]
            for child in children:
                if child.head.casefold() == "include" and child.args:
                    visit(index.resolve_reference(child.args[0], path))
                else:
                    records.append((path, child))

    visit(start)
    if original is not index and original.exists(start):
        # Namespace-owned variant records can be reused without replaying the
        # obsolete external include list that surrounded them in the old addon.
        document=parse(original.read_text(start),lenient=True)
        scope=document.find('entities') or document.find('units')
        for include in scope.find_all('include') if scope else []:
            if include.args and include.args[0].replace('\\','/').startswith('modular_workshop/'):
                path=normalize_path(str(PurePosixPath(start).parent/include.args[0]))
                if index.exists(path):visit(path)
    cache[cache_key] = (records, provenance)
    return records, provenance


def drop_missing_own_includes(output) -> list[str]:
    """Remove includes of the addon's own files that are not in the addon.

    A vehicle that cannot be registered against the current mods is skipped
    and its include file removed, while the global file it came from can still
    list it; the game then closes at start ("File not found"). Only references
    into the workshop's own folders are touched.
    """
    removed = []
    for relative in GLOBAL_FILES:
        path = Path(output) / relative
        if not relative.endswith((".set", ".reg")) or not path.is_file():
            continue
        document = parse(path.read_text(encoding="utf-8-sig"), lenient=True)
        base = PurePosixPath(relative).parent
        changed = False
        for parent in [document, *document.walk()]:
            for node in list(parent.children):
                if node.head.casefold() != "include" or not node.args:
                    continue
                target = node.args[0].replace(chr(92), "/")
                if not target.casefold().startswith("modular_workshop/"):
                    continue
                if not (Path(output) / base / target).is_file():
                    parent.remove(node)
                    removed.append(target)
                    changed = True
        if changed:
            path.write_text(document.render(), encoding="utf-8")
    return removed


def _source_id(value):
    if isinstance(value, dict):
        return value.get("id") or value.get("template")
    return value


def _roster_side(node):
    return next((_macro_arguments(call).get("side") for call in node.walk()
                 if call.opener == "(" and _macro_arguments(call).get("side")), None)


def _roster_entries(index, template):
    """The unit's effective Conquest roster entries, one per army.

    A unit sold to several armies has one entry each (the base game's
    conquest_blank; a captured Matilda I for the Axis in Methodical Conquest
    Expanded). Within one army the last loaded entry is the effective one.
    """
    records, provenance = _load_include_graph(index, "set/multiplayer/units/roster_conquest.set")
    found = {}
    for path, node in records:
        if node.head.casefold() == template.casefold():
            found.pop(_roster_side(node), None)
            found[_roster_side(node)] = (path, node.clone())
    if not found:
        raise RegistrationError(f"Need one effective Conquest roster entry for {template}, found 0")
    return list(found.values()), provenance


def _roster_entry(index, template):
    entries, provenance = _roster_entries(index, template)
    path, node = entries[-1]
    return path, node, provenance


def _expand_sound(node, defines, stack=()):
    """Expand no-parameter sound macros only; never evaluate Lua/game scripts."""
    output = []
    for child in node.children:
        macro = defines.get(child.head.casefold()) if child.opener == "(" else None
        if macro and child.head.casefold().endswith("_sound"):
            if child.head.casefold() in stack:
                raise RegistrationError("Cyclic sound macro")
            if any("%" in atom.value for item in macro.walk() for atom in item.atoms):
                raise RegistrationError(f"Parameterized sound macro needs adapter: {child.head}")
            output.extend(_expand_sound(macro, defines, (*stack, child.head.casefold())))
        else:
            output.append(child.clone())
    return output


def _interaction_events(node, defines, stack=()):
    """Expose top-level event macros without expanding their runtime bodies.

    Nested native defines keep their own positional parameters. Expanding the
    whole event recursively would erase those parameters or rewrite effects.
    """
    result=node.clone();result.items=[]
    for item in node.items:
        macro=defines.get(item.head.casefold()) if isinstance(item,Node) and item.opener=='(' else None
        if macro is None:
            result.items.append(item.clone() if isinstance(item,Node) else copy.deepcopy(item));continue
        name=item.head.casefold()
        if name in stack or len(stack)>16:raise RegistrationError('Cyclic interaction event macro')
        arguments=_macro_arguments(item)
        if 'args' in item.args:
            arguments.update({str(i):a.render() for i,a in enumerate(item.atoms[item.args.index('args')+2:])})
        body=''.join(part.render() for part in macro.items[macro.items.index(macro.atoms[1])+1:])
        body=re.sub(r'%(\w+)',lambda m:arguments.get(m.group(1).casefold(),m.group(0)),body)
        expanded=_interaction_events(parse(body),defines,(*stack,name))
        if expanded.find_all('on'):result.items.extend(expanded.items)
        else:result.items.append(item.clone())
    return result


def _interaction_key(node):
    # An event is its name and every qualifier: {on ik begin "gun_rot"} and
    # {on ik end "turret"} are different events (MACE ZSU-37 declares four ik
    # events), as are {on fire "gun"} and {on fire "mgun"}. "overload" only
    # says how the event replaces an inherited one.
    if node.head.casefold()!='on' or not node.args:return None
    return tuple(arg for arg in node.args if arg.casefold()!='overload')


def _validate_interaction_events(index,record):
    records,_=_load_include_graph(index,'set/interaction_entity/entity.set')
    defines={n.args[0].casefold():n for _,n in records if n.head.casefold()=='define' and n.args}
    seen=set()
    for event in _interaction_events(record,defines).find_all('on'):
        key=_interaction_key(event)
        if key in seen:raise RegistrationError('Duplicate generated interaction event: '+' '.join(key))
        seen.add(key)


def _stuff_definition(index, identifier, from_path=None, pattern=False):
    suffixes = (".pattern",) if pattern else ("", ".weapon")
    # The game also names a weapon by its kind: {weapon "weapon 82mm_bm8_24"} (BM-8-24, Goliath).
    if not pattern and identifier.casefold().startswith("weapon "):
        identifier = identifier[len("weapon "):]
    if from_path:
        try:
            return index.resolve_reference(identifier, from_path, suffixes)
        except FileNotFoundError:
            pass
    matches = [entry.path for key, entry in index.entries.items()
               if key.startswith("set/stuff/") and any(PurePosixPath(key).name == identifier.casefold() + suffix
                                                       for suffix in suffixes)]
    if len(matches) != 1:
        raise RegistrationError(f"Native weapon definition must resolve uniquely: {identifier}")
    return matches[0]


def _stuff_sound_profile(index, path, visited=()):
    if path.casefold() in visited:
        raise RegistrationError("Cyclic native weapon inheritance")
    text = index.read_text(path)
    document = parse(text)
    definition = document.find("from")
    inherits = definition is not None
    if definition is None:
        # Base patterns such as bullet.pattern use their own root block
        # ({weapon_bullet ...}) and end the inheritance chain.
        definition = next((node for node in document.children if node.opener == "{"), None)
        if definition is None:
            raise RegistrationError(f"Native weapon definition has no root block: {path}")
    sounds, provenance = {}, []
    if inherits and definition.args:
        parent = definition.args[0]
        is_pattern = parent.startswith("pattern ")
        identifier = parent[len("pattern "):] if is_pattern else parent
        # Parent definitions can supply fire sound without a vehicle animation
        # event. Resolve inherited values; never infer a handler from the ID.
        try:
            parent_path = _stuff_definition(index, identifier, path, is_pattern)
        except RegistrationError:
            parent_path = None
        if parent_path:
            sounds, provenance = _stuff_sound_profile(index, parent_path, (*visited, path.casefold()))
    for field in ("fireSound", "fireSoundclose", "reloadSound"):
        value = definition.find(field)
        if value and value.args:
            if "%" in value.args[0]:
                raise RegistrationError(f"Unresolved native weapon sound parameter in {path}")
            sounds[field] = value.args[0]
    provenance.append({"path": path, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                       "source": index.resolve(path).as_dict()})
    return sounds, provenance


def _mountable_sound_authority(index, donor, module_bones=None):
    entity = index.resolve_entity(donor)
    root = entity_root(parse(index.read_text(entity["definition_path"])))
    weaponry = root.find("Weaponry")
    inventory = next((node for node in root.find_all("extender")
                      if node.args and node.args[0].casefold() == "inventory"), None)
    if weaponry is None or inventory is None:
        raise RegistrationError("Donor has no vehicle sound event or verified mountable inventory")
    authority = []
    for place in weaponry.find_all("place"):
        if not place.args:
            continue
        slot = place.args[0]
        link = place.find("LinkBone")
        if module_bones and link and link.args and link.args[0] not in module_bones:
            continue
        mask = next((weapon.find("mask") for weapon in inventory.find_all("weapon")
                     if weapon.args and weapon.args[0].casefold() == slot.casefold()), None)
        if mask is None or "mountable" not in [value.casefold() for value in mask.args]:
            raise RegistrationError(f"Donor weapon place {slot} needs a verified sound adapter")
        animation = place.find("LinkAnimation")
        if animation is None or not animation.args or animation.args[0].casefold() != "off":
            raise RegistrationError(f"Native mountable {slot} requires LinkAnimation off or an explicit vehicle sound event")
        items = [item for item in inventory.walk("item")
                 if item.args and item.find("user") and item.find("user").args
                 and item.find("user").args[0].casefold() == slot.casefold()]
        if len(items) != 1:
            raise RegistrationError(f"Mountable place {slot} must have one bound inventory weapon")
        path = _stuff_definition(index, items[0].args[0])
        sounds, sources = _stuff_sound_profile(index, path)
        if not sounds.get("fireSound"):
            raise RegistrationError(f"No verified native fireSound for mountable weapon {items[0].args[0]}")
        authority.append({"place": slot, "weapon": items[0].args[0], "definition_path": path,
                          "playback": "native_stuff", "link_animation": "off", "sounds": sounds,
                          "source_hashes": sources})
    if not authority:
        raise RegistrationError("No selected mountable donor weapon with verified native sound")
    return authority


def _entity_sound_record(index, recipient, donor, new_id, metadata=None):
    if (metadata or {}).get('operation')=='gun_swap':
        return _gun_entity_sound_record(index,recipient,donor,new_id,metadata)
    records, provenance = _load_include_graph(index, "set/interaction_entity/entity.set")
    defines = {node.args[0].casefold(): node for _, node in records if node.head.casefold() == "define" and node.args}

    def record(template, optional=False):
        found = [(path, node) for path, node in records if node.head.split(" ")[0].casefold() == template.casefold()]
        if optional and not found:
            return None, None
        if not found:
            raise RegistrationError(f"Need one interaction record for {template}, found 0")
        if len(found) == 1:
            return found[0]
        # Mods extend a vanilla record by declaring it again (MACE adds AI tags
        # to "183mm_bl_7_2_mk6" in cannon_mace.inc); the game merges them in
        # include order, a repeated {on "event"} replacing the earlier one.
        # Events usually come from sound macros, so merge the expanded events.
        merged = found[0][1].clone()
        merged.items = [item for item in merged.items if not isinstance(item, Node)]
        for child in _expand_sound(_interaction_events(found[0][1],defines), defines):
            merged.append(child)
        for _, node in found[1:]:
            for child in _expand_sound(_interaction_events(node,defines), defines):
                event = _interaction_key(child)
                previous = next((item for item in merged.children if event and _interaction_key(item)==event), None)
                if previous is not None:
                    merged.items[merged.items.index(previous)] = child.clone()
                else:
                    merged.append(child.clone())
        return " + ".join(path for path, _ in found), merged

    recipient_path, recipient_node = record(recipient, optional=True)
    if recipient_node is None:
        # A chassis without any interaction record runs on engine defaults;
        # the new unit gets no record either and behaves exactly the same.
        return None, {"chassis": None, "turret": None, "weapon_playback": "chassis_default", "native_weapons": [],
                      "source_hashes": provenance, "chassis_events": []}, [
            f"Chassis {recipient} has no interaction record; the new vehicle keeps the engine defaults like the original."]
    # Some native weapons, including both MG Lafettes, have no entity-specific
    # interaction entry anywhere in the effective include graph. Their existing
    # inventory/weapon definitions and engine defaults supply their behavior;
    # absence is distinct from an ambiguous or unsupported explicit record.
    donor_path, donor_node = record(donor, optional=True)
    rec_children = _expand_sound(recipient_node, defines)
    don_children = _expand_sound(donor_node, defines) if donor_node is not None else []
    movement = [node for node in rec_children if node.head.casefold() == "on" and node.args and node.args[0] == "add_movement_sound"]
    # {"daf_39_com tank" ...} inherits "tank": without an own movement event
    # the chassis uses its parent's, so take it from the inheritance chain.
    parent_head, seen = recipient_node.head, set()
    while not movement and len(parent_head.split()) > 1 and parent_head not in seen:
        seen.add(parent_head)
        name = parent_head.split()[1].casefold()
        parent = next((node for _, node in records if node.head.casefold() == name), None)  # exact: "tank", not "tank bunker"
        if parent is None:
            break
        movement = [node.clone() for node in _expand_sound(parent, defines)
                    if node.head.casefold() == "on" and node.args and node.args[0] == "add_movement_sound"]
        parent_head = parent.head
    weapons = [node for node in don_children if node.head.casefold() == "on" and node.args and node.args[0] == "add_weapon_sound"]
    if len(movement) > 1 or len(weapons) > 1:
        raise RegistrationError("Vehicle sound adapter requires explicit chassis movement and unambiguous donor weapon events")
    native_weapons, default_weapon_sound = [], False
    if donor_node is not None and not weapons:
        module_bones = (metadata or {}).get("state_profile", {}).get("donor_module_bones")
        try:
            native_weapons = _mountable_sound_authority(index, donor, module_bones)
        except RegistrationError:
            # No vehicle weapon event and no mountable weapon (rocket launchers,
            # some howitzers): the weapon keeps the sound its own definition
            # gives it, as when the donor has no interaction record at all.
            default_weapon_sound = True
    turret_links = [node for event in don_children for node in event.walk("link_sound")
                    if node.args and node.args[0].casefold() in ("rotate_turret", "rotate_gun")]
    # No movement event anywhere in the chain (MACE daf_39_com): the engine
    # supplies it, and the new record inherits the same parent, so none is added.
    movement = movement[0] if movement else None
    # A free addition with default donor interactions leaves the chassis and
    # its original weapons intact, including their rotation and fire sounds.
    preserve_chassis_interactions = str((metadata or {}).get("recipient_mount") or "").startswith("@free") and donor_node is None
    if not preserve_chassis_interactions and movement is not None:
        for sound in list(movement.children):
            if sound.head.casefold() == "link_sound" and sound.args and sound.args[0].casefold() in ("rotate_turret", "rotate_gun"):
                movement.remove(sound)
    for sound in turret_links if movement is not None else []:
        movement.append(sound.clone())
    suffix = recipient_node.head[len(recipient):]
    result = block("{" + quote(new_id + suffix) + "}")
    if movement is not None:
        result.append(movement)
    if weapons:
        result.append(weapons[0])
    # Preserve chassis-specific behavior; custom turret events are not guessed.
    retained = []
    for child in rec_children:
        if child.head.casefold() == "on" and child.args:
            if child.args[0] == "add_movement_sound" or (
                    child.args[0] == "add_weapon_sound" and not preserve_chassis_interactions):
                continue
        result.append(child)
        retained.append(child.head + " " + " ".join(child.args))
    warnings = []
    if donor_node is None:
        warnings.append(f"Donor {donor} has no entity-specific interaction record in the effective game files; "
                        "its native weapon behavior and engine defaults are retained without a custom donor sound event.")
    elif default_weapon_sound:
        warnings.append(f"Donor {donor} has no vehicle weapon sound event or mountable weapon; "
                        "its weapon keeps the sound of its own definition.")
    additional_donor = [node for node in don_children if node.head.casefold() == "on" and node.args
                        and node.args[0] not in ("add_movement_sound", "add_weapon_sound")]
    if additional_donor:
        warnings.append("Donor-specific non-sound interaction events require game acceptance; they were not copied across unrelated chassis bones.")
    return result, {"chassis": recipient_path, "turret": donor_path,
                    "weapon_playback": ("engine_default" if donor_node is None or default_weapon_sound else
                                        "native_stuff" if native_weapons else "vehicle_event"),
                    "native_weapons": native_weapons,
                    "source_hashes": provenance, "chassis_events": retained}, warnings


def _gun_entity_sound_record(index,recipient,donor,new_id,metadata):
    records=metadata.get('gun_assemblies') or [metadata['gun_assembly']]
    seed=_entity_sound_record(index,recipient,metadata['draft_turret_donor'],new_id) if metadata.get('draft_turret_donor') else None
    for contract in records:
        seed=_one_gun_entity_sound_record(index,recipient,contract['donor'].get('template') or donor,new_id,{**metadata,'gun_assembly':contract},seed)
    if len(records)>1:
        seed[1]['gun_changes']=[{'recipient_place':c['recipient']['place'],'donor_place':c['donor']['place'],'weapon':c['donor']['weapon']} for c in records]
        seed=(seed[0],seed[1],list(dict.fromkeys(seed[2])))
    return seed


def _one_gun_entity_sound_record(index,recipient,donor,new_id,metadata,seed=None):
    """Keep receiver class/movement/secondary sounds; import the selected gun's
    native sound channels and firing-animation dispatch, never towing handlers.
    """
    result,provenance,warnings=seed if seed is not None else _entity_sound_record(index,recipient,recipient,new_id)
    if result is None:
        result=block('{'+quote(new_id)+'}')
    records,sources=_load_include_graph(index,'set/interaction_entity/entity.set')
    defines={n.args[0].casefold():n for _,n in records if n.head.casefold()=='define' and n.args}
    contract=metadata['gun_assembly']
    props=set(contract['donor'].get('properties',[]))
    candidates=[n for _,n in records if n.head.casefold() in {p.casefold() for p in props}]
    candidates += [n for _,n in records if n.head.split(' ')[0].casefold()==donor.casefold()]
    # Native classes dispatch fire by Weaponry.place. Preserve the receiver's
    # remaining fire behavior and explicitly add only verified donor clips.
    # A clip the donor shares by name with the receiver (its "recoil") was
    # given this gun's own name in the model: play that one, never the shared one.
    renamed=contract.get('sequence_names') or {}
    clips=[]
    for node in candidates:
        for event in node.find_all('on'):
            if event.args[:2]!=['fire',contract['donor']['place']]:continue
            for play in event.find_all('ani_play'):
                if not play.args:continue
                name=renamed.get(play.args[0],play.args[0])
                if name in contract['sequences']:
                    play=play.clone()
                    if name!=play.args[0]:play.atoms[1].set(name)
                    clips.append(play)
    if clips:
        result=_interaction_events(result,defines)
        slot=contract['recipient']['place']
        handler=next((n for n in result.find_all('on') if n.args[:2]==['fire',slot]),None)
        if handler is None:
            handler=block('{on fire '+quote(slot)+' {inherited}}');result.append(handler)
        for play in clips:
            if play.render() not in [n.render() for n in handler.find_all('ani_play')]:handler.append(play)
    # Sound-channel ownership comes from the actual stuff sound resource's
    # shared category suffix, not a caliber table or the spelling of a vehicle.
    weapon=contract['donor']['weapon']
    sounds,_=_stuff_sound_profile(index,_stuff_definition(index,weapon))
    fire=sounds.get('fireSound','')
    suffix=fire.split('/Shared/',1)[1] if '/Shared/' in fire else None
    links={}
    for node in candidates:
        for event in node.find_all('on'):
            if event.args[:1]!=['add_weapon_sound']:continue
            expanded=_expand_macros(event,defines)
            for link in expanded.find_all('link_sound'):
                if len(link.args)>=2 and suffix and link.args[1].endswith('/Shared/'+suffix):
                    links[link.args[0]]=link.clone()
    if links:
        event=next((n for n in result.find_all('on') if n.args[:1]==['add_weapon_sound']),None)
        if event is None:event=block('{on "add_weapon_sound" overload}');result.append(event)
        expanded=_expand_macros(event,defines)
        event.items=expanded.items
        for old in list(event.find_all('link_sound')):
            if old.args and old.args[0] in links:event.remove(old)
        for link in links.values():event.append(link)
    else:
        warnings.append('GunAssembly: donor spatial sound channels could not be isolated; native stuff fire/reload sounds and receiver secondary channels retained.')
    provenance.update(gun_weapon=weapon,gun_native_fire_clips=[n.args[0] for n in clips],
                      gun_sound_channels=sorted(links),gun_source_hashes=sources)
    warnings.append('GunAssembly: native hull/turret wreck appearance retained; battle aiming, recoil and crew acceptance pending.')
    return result,provenance,warnings


def _macro_arguments(call):
    """name(value) pairs of a macro call such as ("vehicle2" side(ger) ...)."""
    values, pending = {}, None
    for item in call.items:
        if isinstance(item, Atom):
            pending = item.value if item is not call.atoms[0] else None
        elif isinstance(item, Node) and item.opener == "(" and pending:
            values[pending.casefold()] = item.render()[1:-1].strip()
            pending = None
    return values


def _expand_macros(node, defines, depth=0):
    """Inline (define ...) macro calls, substituting %parameters (missing ones are empty)."""
    if depth > 16:
        raise RegistrationError("Roster macro nesting too deep")
    items = []
    for item in node.items:
        if isinstance(item, Node) and item.opener == "(" and item.head.casefold() in defines:
            macro = defines[item.head.casefold()]
            arguments = _macro_arguments(item)
            body = "".join(part.render() for part in macro.items[macro.items.index(macro.atoms[1]) + 1:])
            body = re.sub(r"%(\w+)", lambda match: arguments.get(match.group(1).casefold(), ""), body)
            items.extend(_expand_macros(parse(body), defines, depth + 1).items)
        elif isinstance(item, Node):
            expanded = item.clone()
            expanded.items = _expand_macros(item, defines, depth + 1).items
            items.append(expanded)
        else:
            items.append(item)
    result = node.clone()
    result.items = items
    return result


def _hide_from_shop(entry, defines, side):
    """Keep a roster entry in the Conquest unit set but out of every shop.

    The shop lists units tagged with the player's army; owned squads only need
    the roster entry (captured enemy vehicles are owned without being buyable).
    Dropping the army tag therefore removes generated units from purchase
    while their campaign squads keep resolving.
    """
    expanded = _expand_macros(entry, defines)
    tags = list(expanded.walk("tags"))
    if not tags:
        raise RegistrationError("Roster entry has no tags; cannot keep it out of the shop")
    for node in tags:
        values = [value for value in node.args[0].split() if value.casefold() != side.casefold()] if node.args else []
        node.atoms[1].set(" ".join(values))
    return expanded


SHOP_SHARE = 0.75  # James Rommel: a Pz.III (40) + Pz.IV (40) hybrid costs 60


def _last_number(entry, head):
    for node in reversed(list(entry.walk(head))):
        try:
            return float(node.args[0])
        except (IndexError, ValueError):
            continue
    return None


class _ShopPrices:
    """Shop price and research stage per unit, from the Conquest roster read
    once; each unit's macros are expanded only when it is asked for."""
    def __init__(self, index):
        records, _ = _load_include_graph(index, "set/multiplayer/units/roster_conquest.set")
        self.defines = {node.args[0].casefold(): node for _, node in records if node.head.casefold() == "define" and node.args}
        self.entries = {}
        for _, node in records:
            if node.head.casefold() != "define":
                self.entries.setdefault(node.head.casefold(), {})[_roster_side(node)] = node  # the last per army wins
        self.cache = {}

    def get(self, template):
        """(cost, stage) of the unit's cheapest army, or None if it is not sold."""
        if template not in self.cache:
            found = []
            for node in self.entries.get(template, {}).values():
                expanded = _expand_macros(node, self.defines)
                cost, stage = _last_number(expanded, "cost"), _last_number(expanded, "research_stage")
                if cost is not None:
                    found.append((cost, int(stage or 1)))
            self.cache[template] = (min(c for c, _ in found), min(s for _, s in found)) if found else None
        return self.cache[template]


def shop_prices(index):
    return _ShopPrices(index)


def shop_offer(index, parts, recipes=None, share=SHOP_SHARE, prices=None):
    """Price and research stage of a workshop vehicle sold in the shop.

    The price is a share of what its parts cost (each part's cheapest army),
    rounded like the game to multiples of 5; it unlocks at the latest research
    stage among its parts. A workshop part counts as the native vehicles it is
    made of. Parts with no roster entry of their own (tractors sold inside
    squads) add nothing and are listed as unpriced.
    """
    prices = shop_prices(index) if prices is None else prices
    natives = []
    def expand(templates, seen):
        for template in templates:
            template = re.sub(r"\([^()]*\)$", "", template)
            if recipes and template in recipes and template not in seen:
                expand(vehicle_parts(recipes[template]), seen | {template})
            else:
                natives.append(template)
    expand(parts, frozenset())
    rows, unpriced = [], []
    for template in natives:
        price = prices.get(template.casefold())
        if price is None:
            unpriced.append(template)
            continue
        rows.append({"template": template, "cost": price[0], "stage": price[1]})
    total = sum(row["cost"] for row in rows) * share
    cost = max(5, int(5 * round(total / 5))) if rows else 0
    if not rows:
        raise RegistrationError("None of the vehicle's parts is sold in the Conquest shop; it has no price to derive.")
    return {"cost": cost, "stage": max(row["stage"] for row in rows), "share": share,
            "parts": rows, "unpriced": unpriced}


def vehicle_parts(metadata):
    """The vehicles a workshop vehicle is made of, one entry per unit used:
    the chassis, then every donor of a turret or gun (two T30 turrets on a
    T-28 are two T30s). An edit of a single vehicle is just that vehicle."""
    from .draft import design_turrets
    parts = [metadata.get("recipient")]
    def rows(transform):
        draft = transform.get("assembly_draft") or {}
        found = [row for row in design_turrets(draft) if not row.get("native")]
        found += list(draft.get("guns") or []) + list(transform.get("gun_swaps") or [])
        found += [transform["gun_swap"]] if transform.get("gun_swap") else []
        return [row.get("donor_template") for row in found if row.get("donor_template")]
    donors = rows(metadata.get("transform") or {})
    if not donors and metadata.get("donor") not in (None, metadata.get("recipient")):
        donors = [metadata.get("donor")]
    return [_source_id(part) for part in parts + donors if part]


def _offer_in_shop(entry, offer, side):
    """A roster entry sold in the army's shop at the derived price and stage."""
    for node in entry.walk("tags"):
        values = node.args[0].split() if node.args else []
        if side.casefold() not in (value.casefold() for value in values):
            values.insert(2 if len(values) >= 2 else len(values), side)
        node.atoms[1].set(" ".join(values))
    for head, value in (("cost", offer["cost"]), ("research_stage", offer["stage"]), ("research_stage_max", 99)):
        nodes = list(entry.walk(head))
        for node in nodes:
            node.atoms[1].set(str(value))
        if not nodes:
            entry.append(block("{%s %s}" % (head, value)))
    return entry


def _roster_defines(index):
    records, _ = _load_include_graph(index, "set/multiplayer/units/roster_conquest.set")
    return {node.args[0].casefold(): node for _, node in records if node.head.casefold() == "define" and node.args}


def _all_reserves(output, new_id, current):
    """Reserve squads of every stored variant (by template), this one's last."""
    rows = {}
    # Reserve squads of deleted vehicles (and of their crews) stay registered:
    # saves may hold them long after the vehicle is gone.
    stored_files = sorted(Path(output).glob("workshop_manifest_*.json")) + sorted(Path(output).glob("workshop_reserves_*.json"))
    for path in stored_files:
        if path.name == f"workshop_manifest_{new_id}.json":
            continue
        try:
            stored = json.loads(path.read_text(encoding="utf-8")).get("reserve_squads") or []
        except (OSError, ValueError):
            continue
        for row in stored:
            if isinstance(row, dict) and row.get("template"):
                rows.setdefault(str(row["template"]), row)
    for row in current:
        rows[str(row["template"])] = row
    return list(rows.values())


PORTRAIT_KINDS = ("portrait_squad", "unit_icon", "unit_icon_small")


def portrait_geometry_hashes(output, new_id):
    """Appearance signature of the compiled model and its copied dependencies."""
    output = Path(output)
    folder = output / "resource/entity/-vehicle/workshop" / _safe_id(new_id)
    return {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def portrait_state(output, new_id):
    """A rendered portrait is identified by its four hashes, never its filename.

    Older addons may contain a copied chassis portrait or an unmarked render;
    report that uncertainty so the UI can regenerate the actual assembled model.
    """
    output = Path(output)
    record = output / f"portrait_{_safe_id(new_id)}.json"
    try:
        state = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    folder = output / "resource/interface/scene/portrait_squad"
    files = [folder / f"{new_id}_{variant:02d}.png" for variant in range(4)]
    if state.get("source") == "rendered_model" and len(state.get("sha256", [])) == 4:
        if all(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
               for path, digest in zip(files, state["sha256"])):
            if state.get("geometry_sha256") != portrait_geometry_hashes(output, new_id):
                return {"source": "outdated_model", "reason": "compiled model changed after rendering"}
            return state
        return {"source": "unverified", "reason": "rendered portrait hashes changed"}
    if state.get("source") in {"chassis_fallback", "donor_fallback"} and any(folder.glob(f"{new_id}_00.*")):
        return state
    return {"source": "unverified" if any(folder.glob(f"{new_id}_00.*")) else "missing"}


def _copy_portraits(index, output, sources, new_id, kinds=PORTRAIT_KINDS):
    """Campaign portrait (portrait_squad, 112x140) and battle icons (unit_icon,
    unit_icon_small) are looked up by squad name as <name>_00.._03; a new name
    has none, so the game draws "PLACEHOLDER". Each kind the variant lacks
    (a portrait rendered by the tool is kept) is copied from the chassis, or
    from the module when the chassis has none. Returns the kinds copied."""
    copied = []
    for kind in kinds:
        folder = Path(output) / "resource/interface/scene" / kind
        if folder.is_dir() and any(folder.glob(f"{new_id}_00.*")):
            continue
        for source in sources:
            found = False
            for ext in ("png", "tga", "dds"):
                for variant in range(4):
                    name = f"{source}_{variant:02d}.{ext}"
                    local = folder / name  # a workshop chassis already in the addon
                    virtual = f"interface/scene/{kind}/{name}"
                    if local.is_file():
                        raw = local.read_bytes()
                    elif index.exists(virtual):
                        raw = index.read(virtual)
                    else:
                        continue
                    _write(output, f"resource/interface/scene/{kind}/{new_id}_{variant:02d}.{ext}", raw)
                    found = True
                if found:
                    break
            if found:
                copied.append(kind)
                if kind == "portrait_squad":
                    _write(Path(output), f"portrait_{new_id}.json", json.dumps({
                        "schema_version": 1, "id": new_id,
                        "source": "chassis_fallback" if source == sources[0] else "donor_fallback",
                        "template": source}, ensure_ascii=False, indent=2) + "\n")
                break
    return copied


def _reserve_portraits(index, output, rows):
    """Reserve squads (spare crew) take the portrait of a lone soldier of the
    same kind, e.g. mp/ger/mid/tankman -> single_tankman(ger)."""
    for row in rows:
        parts = str(row.get("breed", "")).split("/")
        match = re.fullmatch(r"([A-Za-z0-9_-]+)\(([A-Za-z0-9_-]+)\)", str(row.get("template", "")))
        if len(parts) != 4 or not match:
            continue
        army, token = parts[1], parts[3]
        base = re.sub(r"_\d+$", "", token)
        sources = [f"single_{token}({army})", f"single_{base}({army})", f"{token}({army})", f"squad_basic({army})"]
        _copy_portraits(index, output, list(dict.fromkeys(sources)), str(row["template"]), kinds=("portrait_squad",))


def _pot(rows, language=None):
    """gettext text as the game ships it: localizations/default/*.pot holds the
    source text (msgstr empty); other languages are *.po with the translation
    in msgstr (es.pak: es/interface/text/desc/*.po). A .pot under es/ is
    ignored by the game. rows: (context, source) or (context, source, translated)."""
    header = 'msgid ""\nmsgstr ""\n'
    if language:
        header += f'"Language: {language}\\n"\n'
    header += '"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
    return header + "\n\n".join("msgctxt " + json.dumps(row[0], ensure_ascii=False)
                                + "\nmsgid " + json.dumps(row[1], ensure_ascii=False)
                                + "\nmsgstr " + json.dumps(row[2] if language and len(row) > 2 else "", ensure_ascii=False)
                                for row in rows) + "\n"


def _write_text_tables(output, name, rows):
    """rows: (context, english, spanish) -> default/<name>.pot and es/<name>.po."""
    _write(output, f"localizations/default/interface/text/desc/{name}.pot", _pot([row[:2] for row in rows]))
    stale = Path(output) / f"localizations/es/interface/text/desc/{name}.pot"
    if stale.is_file():  # written by earlier versions, never read by the game
        stale.unlink()
    _write(output, f"localizations/es/interface/text/desc/{name}.po", _pot(rows, "es"))


def register_crew_reserves(index, output, rows):
    """Register reserve squads made outside a build (a crew fitted to fewer
    seats on opening a campaign), next to every variant's, in staging."""
    output = Path(output)
    if not rows:
        return
    path = output / "workshop_reserves_crew.json"
    try:
        stored = json.loads(path.read_text(encoding="utf-8")).get("reserve_squads") or []
    except (OSError, ValueError):
        stored = []
    merged = {str(row["template"]): row for row in stored}
    merged.update({str(row["template"]): row for row in rows})
    path.write_text(json.dumps({"schema": 1, "reserve_squads": list(merged.values())}, ensure_ascii=False, indent=2), encoding="utf-8")
    defines = _roster_defines(index)
    shared, lines = _all_reserves(output, "", []), []
    for row in shared:
        try:
            document = parse(_register_reserves(index, [row])[0])
        except RegistrationError:
            if str(row["template"]) in {str(r["template"]) for r in rows}:
                raise
            continue
        side = _macro_arguments(document.children[0]).get("side")
        lines.append(_hide_from_shop(document, defines, side).render().strip())
    roster_doc, rosters = _load_global(index, output, "set/multiplayer/units/roster_conquest.set", "units")
    _ensure_include(rosters, RESERVES + ".set")
    _write(output, "resource/set/multiplayer/units/roster_conquest.set", roster_doc.render())
    _write(output, "resource/set/multiplayer/units/" + RESERVES + ".set", "\n".join(lines) + "\n")
    _write_text_tables(output, "modular_workshop__reserves",
                       [("desc/squad/" + row["template"], "Reserve crew", "Tripulación de reserva") for row in shared])


def _register_reserves(index, rows):
    output = []
    for row in rows:
        template = str(row["template"])
        match = re.fullmatch(r"([A-Za-z0-9_-]+)\(([A-Za-z0-9_-]+)\)", template)
        if not match:
            raise RegistrationError(f"Reserve template must include army scope: {template}")
        name, army = match.groups()
        breed = str(row["breed"]).removesuffix(".set").replace("\\", "/")
        parts = breed.split("/")
        if len(parts) != 4 or parts[0] != "mp" or not all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in parts[1:]):
            raise RegistrationError(f"Reserve breed requires verified mp/army/period/name adapter: {breed}")
        if not index.exists("set/breed/" + breed + ".set"):
            raise RegistrationError(f"Reserve breed missing from active resources: {breed}")
        count = len(row.get("eids", [])) or int(row.get("crew_slots", 0))
        if not 1 <= count <= 100:
            raise RegistrationError("Invalid reserve squad size")
        period, token = parts[2:]
        # Official Conquest settings.set builds name(side) and infantry-only content.
        if parts[1] == army:
            output.append(f'("squad_with1types_conquest" side({army}) period({period}) min_stage(1) max_stage(99) name({name}) c1({token}:{count}))')
        else:
            # Soldiers of another army (French crews in a British campaign): the
            # macro would look for mp/<campaign army>/..., so spell the squad out
            # with the soldiers' own breed, as the macro would expand it.
            output.append(f'{{"{name}({army})" {{charge {{delay 0}}{{interval 0}}}} {{content "mp/{parts[1]}/{period}/{token}:{count}"}} '
                          f'{{tags "conquest conquestonly {army} {period} "}} {{level 1}} {{cost 0}} {{research_stage 1}} '
                          f'{{research_stage_max 99}} {{squad_cost_factor 1}} {{button "inf1"}}}}')
    return output


def _camera_profiles(index, vehicle_scope):
    """Resolve native camera inheritance, merging levels by their position."""
    profiles = {}
    provenance = []
    for include in vehicle_scope.find_all("include"):
        if not include.args:
            continue
        path = index.resolve_reference(include.args[0], "set/tp_control.set")
        records, sources = _load_include_graph(index, path)
        provenance.extend(sources)
        for source_path, node in records:
            if node.opener == "{" and node.head:
                profiles[node.head.casefold()] = node
    profiles.update({node.head.casefold(): node for node in vehicle_scope.children
                     if node.opener == '{' and node.head})

    def materialize(node, stack=()):
        identity = node.head.casefold()
        if identity in stack:
            raise RegistrationError("Cyclic third-person camera inheritance")
        atoms = node.args
        inherited = atoms[atoms.index("inherit") + 1] if "inherit" in atoms and atoms.index("inherit") + 1 < len(atoms) else None
        if inherited:
            parent_name = inherited.removeprefix("vehicle ").casefold()
            parent = profiles.get(parent_name)
            if parent is None:
                raise RegistrationError(f"Missing camera profile {inherited}")
            result = materialize(parent, (*stack, identity))
            def overlay(target, source):
                levels = target.find_all('level')
                position = 0
                for child in source.children:
                    if child.head.casefold() == 'level':
                        previous = levels[position] if position < len(levels) else None
                        position += 1
                    else:
                        previous = target.find(child.head)
                    if previous is not None and (child.head.casefold() == 'level' or (previous.children and child.children)):
                        overlay(previous, child)
                    else:
                        if previous is not None:
                            position_in_items = next(i for i, item in enumerate(target.items) if item is previous)
                            target.items[position_in_items] = child.clone()
                        else:
                            target.append(child.clone())
            overlay(result, node)
            return result
        return node.clone()

    return materialize, provenance, profiles


def _native_camera_profile(index, template):
    root = parse(index.read('set/tp_control.set')).find('third_person_control')
    scope = root.find('vehicle') if root is not None else None
    if scope is None:
        raise RegistrationError('No native vehicle camera scope available')
    materialize, provenance, profiles = _camera_profiles(index, scope)
    node = scope.find(template) or profiles.get('default')
    if node is None:
        raise RegistrationError('No native default camera profile available')
    return materialize(node), provenance


def _compose_camera(index, vehicle_scope, recipient, donor, new_id, bone_mapping):
    """Keep chassis external camera and transplant verified donor aiming levels."""
    materialize, provenance, profiles = _camera_profiles(index, vehicle_scope)
    recipient_mapping = vehicle_scope.find(recipient)
    recipient_profile = materialize(recipient_mapping or profiles.get("default")) if (recipient_mapping or profiles.get("default")) else None
    if recipient_profile is None:
        raise RegistrationError("No explicit or default chassis third-person profile available")
    recipient_profile.atoms[0].set(new_id)
    # Remove inherited arguments: complete expanded content is now independent.
    recipient_profile.items = [item for item in recipient_profile.items
                               if item not in recipient_profile.atoms[1:]]
    donor_mapping = vehicle_scope.find(donor)
    if donor_mapping is None:
        return recipient_profile, {"chassis": recipient, "turret": donor, "aiming_transferred": False}, ["Donor has no explicit third-person profile; chassis camera retained."]
    donor_profile = materialize(donor_mapping)
    donor_levels = donor_profile.find_all("level")
    if len(donor_levels) < 2:
        return recipient_profile, {"chassis": recipient, "turret": donor, "aiming_transferred": False}, ["Donor aiming camera levels unavailable; chassis camera retained."]
    aiming = donor_levels[1:]
    for level in aiming:
        for link in level.walk("linkBone"):
            if not link.args:
                continue
            original = link.args[0]
            mapped = bone_mapping.get(original)
            if not mapped:
                return recipient_profile, {"chassis": recipient, "turret": donor, "aiming_transferred": False}, [f"Donor camera bone {original} is not verified in the transplant; chassis profile retained."]
            link.atoms[1].set(mapped)
    chassis_levels = recipient_profile.find_all("level")
    for level in chassis_levels[1:]:
        recipient_profile.remove(level)
    for level in aiming:
        recipient_profile.append(level.clone())
    return recipient_profile, {"chassis": recipient, "turret": donor, "aiming_transferred": True,
                               "source_hashes": provenance}, []


def _adapt_roster(roster, roster_owner, new_id, metadata):
    """One roster entry renamed to the variant, available at any stage."""
    roster.atoms[0].set(new_id)
    for node in roster.walk():
        for token in node.atoms:
            if token.value == roster_owner:
                token.set(new_id)
    roster_text = re.sub(r"min_stage\(\s*\d+\s*\)", "min_stage(1)", roster.render())
    roster_text = re.sub(r"max_stage\(\s*\d+\s*\)", "max_stage(99)", roster_text)
    return block(roster_text)


def _resize_roster_crew(entry, roles):
    """Match the effective squad content to actual seats, including resupply.

    Work after roster macro expansion: inherited/default counts and previously
    generated entries must obey the same capacity as explicit crew(...) calls.
    Preserve native breeds, but retire a breed when fewer seats than breeds remain.
    """
    if roles is None:
        return entry
    pattern = re.compile(r'(?<![\w/])(mp/[^\s():]+)(?::(\d+))?')
    fields = [(n, list(pattern.finditer(n.args[0]))) for n in entry.walk('content') if n.args]
    counts = [int(m[2] or 1) for _, matches in fields for m in matches]
    if not counts:
        return entry  # No known native crew content; never invent a breed.
    delta = len(roles) - sum(counts)
    if delta > 0:
        counts[0] += delta
    else:
        for minimum in (1, 0):
            order = range(len(counts)) if minimum else reversed(range(len(counts)))
            for i in order:
                count = counts[i]
                take = min(max(0, count - minimum), -delta)
                counts[i] -= take
                delta += take
    offset = 0
    for node, matches in fields:
        text = node.args[0]
        for match, count in reversed(list(zip(matches, counts[offset:offset + len(matches)]))):
            text = text[:match.start()] + (match[1] + ':' + str(count) if count else '') + text[match.end():]
        node.atoms[1].set(text)
        offset += len(matches)
    return entry


def register_variant(index: ResourceIndex, compilation_result: dict, output_dir: str | Path,
                     new_id: str, display_name: str, shop: dict | None = None) -> dict:
    """Write an add-on into existing generated staging, then return provenance.

    shop: {"parts": [templates], "share": float} sells the vehicle in its
    army's Conquest shop (see shop_offer); otherwise it can only be owned.
    """
    new_id = _safe_id(new_id)
    output = Path(output_dir).resolve()
    if output == index.game_root or any(output.is_relative_to(Path(source["root"])) for source in index.sources):
        raise RegistrationError("Registration must use staging outside the installation and active mods")
    if new_id.casefold() in index._entity_by_id:
        raise RegistrationError(f"Generated ID already exists: {new_id}")
    metadata = compilation_result.get("metadata", {})
    recipient = _source_id(metadata.get("recipient") or compilation_result.get("recipient"))
    donor = _source_id(metadata.get("donor") or compilation_result.get("donor"))
    if not recipient or not donor:
        raise RegistrationError("Compilation provenance must identify recipient and donor")
    recipient = re.sub(r"\([^()]*\)$", "", recipient)
    donor = re.sub(r"\([^()]*\)$", "", donor)
    # Resolve and validate every source before modifying staging.
    recipient_entity = index.resolve_entity(recipient)
    donor_entity = index.resolve_entity(donor)
    roster_owner, roster_note = recipient, None
    try:
        entries, roster_provenance = _roster_entries(index, recipient)
    except RegistrationError as chassis_error:
        # Tractors such as the Kettenkrad are only sold inside towed-gun squads
        # ("towed_gun_doctrine" vehicle(kettenkrad) ...), so they own no entry;
        # the module's entry supplies army, crew breeds and tags instead.
        try:
            entries, roster_provenance = _roster_entries(index, donor)
        except RegistrationError:
            raise chassis_error from None
        roster_owner = donor
        roster_note = (f"Chassis {recipient} has no Conquest roster entry of its own (sold inside a squad); "
                       f"the roster entry of {donor} was used.")
    roster_source = entries[-1][0] if len(entries) == 1 else [path for path, _ in entries]
    variants = [_adapt_roster(roster, roster_owner, new_id, metadata) for _, roster in entries]
    sound, sound_provenance, warnings = _entity_sound_record(index, recipient, donor, new_id, metadata)
    if metadata.get('commander_mg'):
        from .commander_mg import add_interactions
        if sound is None:
            sound = block('{' + quote(new_id) + '}')
        add_interactions(sound, metadata['commander_mg'])
        warnings.append('Commander mount: hatch animation and weapon swapping require battle acceptance.')
    if (metadata.get('rider_mgs') or {}).get('mounts'):
        from .commander_mg import add_mountable_fire
        if sound is None:sound=block('{'+quote(new_id)+'}')
        for mount in metadata['rider_mgs']['mounts']:add_mountable_fire(sound,mount['slot'])
    if sound is not None or metadata.get('smoke_launchers'):
        from .smoke_launchers import add_interactions as add_smoke_interactions
        if sound is None: sound = block('{' + quote(new_id) + '}')
        add_smoke_interactions(sound, metadata.get('smoke_launchers'))
    if metadata.get('smoke_launchers'):
        warnings.append('Added smoke launchers: native discharge, inventory consumption and persistence require battle acceptance.')
    if sound is not None:_validate_interaction_events(index,sound)
    if roster_note:
        warnings.append(roster_note)
    campaign_plan = compilation_result.get("campaign_plan", {})
    reserves = campaign_plan.get("reserve_squads", [])
    # Generated units exist only through workshop operations unless the player
    # put them up for sale.
    defines = _roster_defines(index)
    offer = None
    if shop:
        offer = shop_offer(index, shop["parts"], _assembly_recipes(index, output), shop.get("share", SHOP_SHARE))
        if offer["unpriced"]:
            warnings.append("Not priced (no shop entry of their own): " + ", ".join(offer["unpriced"]) + ".")
    sides = [_macro_arguments(call).get("side") for roster in variants for call in roster.walk() if call.opener == "("]
    reserve_docs = [parse(line) for line in _register_reserves(index, reserves)]
    reserve_sides = [_macro_arguments(document.children[0]).get("side") for document in reserve_docs]
    side = next((value for value in sides + reserve_sides if value), None)
    if not side:
        # A workshop chassis reused from the depot: its own roster entry was
        # already expanded and stripped of the army tag. The army is the one
        # of the native vehicle it was built from.
        recipes = _assembly_recipes(index, output)
        ancestor = _chassis_ancestor(recipes, recipient)
        if ancestor != recipient:
            _, native_roster, _ = _roster_entry(index, ancestor)
            side = next((_macro_arguments(call).get("side") for call in native_roster.walk()
                         if call.opener == "(" and _macro_arguments(call).get("side")), None)
    if not side:
        raise RegistrationError("Cannot determine the chassis army to keep the variant out of the shop")
    if offer:
        variants = [_offer_in_shop(_expand_macros(roster, defines), offer, _roster_side(roster) or side) for roster in variants]
    else:
        variants = [_hide_from_shop(roster, defines, _roster_side(roster) or side) for roster in variants]
    variants = [_resize_roster_crew(roster, metadata.get('crew_roles')) for roster in variants]
    # Reserve squads are named by army+breed+size, so several variants share
    # one. They live in a single shared file built from every manifest: one
    # definition per squad (no "Localization key redefinition"), and it
    # survives the removal of whichever variant created it first.
    shared_reserves = _all_reserves(output, new_id, reserves)
    reserve_lines = []
    for row in shared_reserves:
        try:
            document = parse(_register_reserves(index, [row])[0])
        except RegistrationError:
            if row in reserves:
                raise
            continue  # another variant's reserve whose breed this stack lacks
        reserve_side = _macro_arguments(document.children[0]).get("side")
        reserve_lines.append(_hide_from_shop(document, defines, reserve_side or side).render().strip())
    unit_doc, units = _load_global(index, output, "set/registry/unit.reg", "units")
    original_unit = units.find(recipient)
    if original_unit is None:
        # Some mod chassis (MACE ka_mi_mid) have no unit.reg view of their own;
        # the module's view (icon/portrait) stands in for it.
        original_unit = units.find(donor)
        if original_unit is None:
            # Neither part has a view in the effective unit.reg (some mods ship
            # theirs as unit.reg.bak, which the game ignores): the original
            # vehicles work without one, and so does the new vehicle.
            warnings.append(f"Neither {recipient} nor {donor} has a unit.reg view; none is registered, as for the originals.")
        else:
            warnings.append(f"Chassis {recipient} has no unit.reg view; the view of {donor} was used.")
    if units.find(new_id):
        raise RegistrationError(f"Variant already registered in staging: {new_id}")
    if original_unit is not None:
        unit = original_unit.clone()
        unit.atoms[0].set(new_id)
        units.append(unit)
    tp_doc, tp_root = _load_global(index, output, "set/tp_control.set", "third_person_control")
    vehicle_scope = tp_root.find("vehicle")
    if vehicle_scope is None:
        raise RegistrationError("Effective third-person vehicle scope missing")
    camera = None
    if metadata.get('operation') == 'support_vehicle':
        # Keep the chassis external camera, but materialize inheritance before
        # removing aiming levels whose sockets belonged to the removed turret.
        camera, camera_provenance, camera_warnings = _compose_camera(
            index, vehicle_scope, recipient, recipient, new_id, {})
        retired = set(metadata.get('support_vehicle', {}).get('removed_bones', []))
        retired.update(metadata.get('support_vehicle', {}).get('removed_places', []))
        for level in list(camera.find_all('level')):
            if any(atom.value in retired for node in level.walk() for atom in node.atoms[1:]):
                camera.remove(level)
        if not camera.find_all('level'):
            camera = block('{'+quote(new_id)+' inherit "vehicle default"}')
        camera_provenance['support_turret_camera_removed'] = True
        warnings.append('Support conversion: native supply, damage and persistence require battle acceptance.')
    elif metadata.get("bone_mapping") or metadata.get("bone_map") or metadata.get('camera_bone_mapping'):
        try:
            camera, camera_provenance, camera_warnings = _compose_camera(
                index, vehicle_scope, recipient, metadata.get('gun_camera_donor') or donor, new_id,
                {**(metadata.get('bone_mapping') or metadata.get('bone_map') or {}), **metadata.get('camera_bone_mapping',{})})
            warnings.extend(camera_warnings)
        except RegistrationError as error:
            # e.g. a donor camera inheriting a profile no file defines
            # ("vehicle td.t29"): the chassis camera is used instead.
            warnings.append(f"Donor aiming camera unavailable ({error}); chassis camera retained.")
    if camera is None:
        camera = vehicle_scope.find(recipient)
        if camera is None:
            default_profiles, _ = _load_include_graph(index, "set/third_person/vehicle.inc")
            if not any(node.head.casefold() == "default" for _, node in default_profiles):
                raise RegistrationError("No explicit or default chassis third-person profile available")
            camera = block("{" + quote(new_id) + ' inherit "vehicle default"}')
            warnings.append("The chassis has no explicit third-person entry; its engine default profile was preserved.")
        else:
            camera = camera.clone()
            camera.atoms[0].set(new_id)
        camera_provenance = {"chassis": recipient, "turret": donor, "aiming_transferred": False}
        warnings.append("Aiming profile stays with chassis because no verified donor bone mapping was supplied.")
    model_path = output/'resource'/compilation_result.get('model_path',
        f'entity/-vehicle/workshop/{new_id}/{new_id}.mdl')
    if model_path.is_file():
        from .geometry import parse_model
        model = parse_model(model_path.read_bytes())
        names = {n.casefold() for n in model['bones']}
        materialize, _, _ = _camera_profiles(index, vehicle_scope)
        try:
            camera = materialize(camera)
            missing = {n.args[0] for n in camera.walk('linkBone') if n.args and n.args[0].casefold() not in names}
        except RegistrationError:
            missing = {'unresolved_camera_profile'}
        if missing:
            # Keep a usable native zoom/control profile instead of publishing
            # an aiming mode whose socket disappeared with the old turret.
            camera, _ = _native_camera_profile(index, '')
            root_bone = bone_name(model['skeleton'].find('bone'))
            for link in camera.walk('linkBone'):
                if link.args and link.args[0].casefold() not in names:
                    link.atoms[1].set(root_bone)
            camera_provenance.update(fallback='native_generic', missing_bones=sorted(missing))
            warnings.append('Unavailable aiming camera socket: a native generic control camera was retained.')
        camera.atoms[0].set(new_id)
        camera.items = [item for item in camera.items if item not in camera.atoms[1:]]
    vehicle_scope.append(camera)
    entity_doc, entities = _load_global(index, output, "set/interaction_entity/entity.set", "entities")
    roster_doc, rosters = _load_global(index, output, "set/multiplayer/units/roster_conquest.set", "units")
    namespace = "modular_workshop/" + new_id
    _ensure_include(entities, namespace + ".inc")
    _ensure_include(rosters, namespace + ".set")
    if reserve_lines:
        _ensure_include(rosters, RESERVES + ".set")
    # The installed 2026 game uses gettext POT with desc/entity and desc/squad
    # contexts, verified against default.pak and es.pak.
    locale_rows = [("desc/entity/" + new_id, display_name),
                   ("desc/squad/" + new_id, display_name)]
    # Installed mods only use name/tags/desc/version keys; an unknown {require}
    # key is unverified, so dependencies are listed in the description and the
    # campaign status keeps the declared source mods.
    required = [source["name"] for source in index.sources[1:] if not is_own_mod(source["id"])]
    description = f"Vehicles built with Cursed Workshop {__version__}." + (" Load after: " + ", ".join(required) + "." if required else "")
    info = "{mod\n" + "\n".join([f'\t{{name {quote(ADDON_NAME)}}}',
                                  f'\t{{desc {quote(description)}}}',
                                  '\t{minGameVersion "any"}', '\t{maxGameVersion "any"}']) + "\n}\n"
    provenance = {"schema": 1, "id": new_id, "display_name": display_name,
                  "mod_id": output.name, "active_sources": copy.deepcopy(index.sources),
                  "source_order": [source["id"] for source in index.sources],
                  "recipient": recipient_entity, "donor": donor_entity,
                  "roster_source": roster_source, "registration_source_hashes": roster_provenance,
                  "sound_authority": sound_provenance, "reserve_squads": reserves,
                  "camera_authority": camera_provenance,
                  "warnings": warnings + ["Verify transplanted optics and aiming in-game."],
                  "runtime_verified": False}
    if offer:
        provenance["shop"] = {"parts": list(shop["parts"]), "share": offer["share"], "offer": offer}
    _write(output, "mod.info", info)
    _write(output, "resource/set/registry/unit.reg", unit_doc.render())
    _write(output, "resource/set/tp_control.set", tp_doc.render())
    _write(output, "resource/set/interaction_entity/entity.set", entity_doc.render())
    _write(output, "resource/set/interaction_entity/" + namespace + ".inc",
           (sound.render() if sound is not None else "; no interaction record: engine defaults, as the chassis") + "\n")
    _write(output, "resource/set/multiplayer/units/roster_conquest.set", roster_doc.render())
    _write(output, "resource/set/multiplayer/units/" + namespace + ".set", "\n".join(r.render() for r in variants) + "\n")
    if reserve_lines:
        _write(output, "resource/set/multiplayer/units/" + RESERVES + ".set", "\n".join(reserve_lines) + "\n")
    _write_text_tables(output, f"modular_workshop_{new_id}", [(key, label, label) for key, label in locale_rows])
    if reserve_lines:
        _write_text_tables(output, "modular_workshop__reserves",
                           [("desc/squad/" + row["template"], "Reserve crew", "Tripulación de reserva") for row in shared_reserves])
    previous_manifest = output / ("workshop_manifest_" + new_id + ".json")
    if previous_manifest.is_file():
        # Re-registration (stack change, repair) keeps which campaign it belongs to.
        campaign = json.loads(previous_manifest.read_text(encoding="utf-8")).get("campaign")
        if campaign:
            provenance["campaign"] = campaign
    provenance["portraits"] = _copy_portraits(index, output, [recipient, donor], new_id)
    provenance["portrait"] = portrait_state(output, new_id)
    if provenance["portrait"]["source"] != "rendered_model":
        message = "El retrato armado todavía no está renderizado; se usó el retrato de respaldo si estaba disponible. Regenerá los retratos."
        provenance["warnings"].append(message)
        compilation_result.setdefault("diagnostics", []).append({
            "code": "assembled_portrait_pending", "level": "warning", "message": message})
    _reserve_portraits(index, output, shared_reserves)
    drop_missing_own_includes(output)
    _write(output, "workshop_manifest_" + new_id + ".json", json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
    compilation_result.setdefault("files", []).extend(["mod.info", "resource/set/registry/unit.reg", "resource/set/tp_control.set",
                                                      "resource/set/interaction_entity/entity.set", "resource/set/multiplayer/units/roster_conquest.set"])
    compilation_result["registration"] = provenance
    if sound_provenance["weapon_playback"] == "engine_default":
        compilation_result.setdefault("diagnostics", []).append({
            "code": "donor_interaction_defaults", "level": "warning", "donor": donor,
            "message": next(warning for warning in warnings if warning.startswith(f"Donor {donor} has no "))})
    return provenance
