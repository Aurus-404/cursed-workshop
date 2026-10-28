"""Read Conquest saves and plan a two-vehicles-to-one assembly, independently.

No function in this module writes to a save. Unknown references or persisted
module state require an explicit compiler profile; they are never guessed.
"""
from __future__ import annotations

import hashlib
import copy
import io
import struct
import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .syntax import Atom, Document, Node, Trivia, block, parse, quote, replace_atoms




class CampaignError(ValueError):
    """The save or proposed migration cannot be interpreted safely."""


_EID = re.compile(r"0x[0-9a-fA-F]+\Z")
_TEMPLATE = re.compile(r"[A-Za-z0-9_.+()/-]+\Z")
_PLACEHOLDER = 0xFFFFFFFF
MAX_MEMBER_BYTES = 256 * 1024 * 1024


def canonical_eid(value: str | int) -> str:
    if isinstance(value, bool):
        raise CampaignError("An entity identifier cannot be boolean")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and _EID.fullmatch(value):
        number = int(value, 16)
    else:
        raise CampaignError(f"Invalid entity identifier: {value!r}")
    if not 0 <= number <= _PLACEHOLDER:
        raise CampaignError("Entity identifier is outside the supported 32-bit range")
    return hex(number)


def _decode(raw: bytes) -> tuple[str, str]:
    # Keep the encoding and BOM, including the CRLF sequence, on serialization.
    for encoding in (["utf-16"] if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else
                     ["utf-8-sig"] if raw.startswith(b"\xef\xbb\xbf") else ["utf-8", "cp1251"]):
        try:
            text = raw.decode(encoding)
            if text.encode(encoding) != raw:
                continue
            return text, encoding
        except UnicodeError:
            continue
    raise CampaignError("Save text encoding cannot be round-tripped without changes")


def _one(parent: Node | Document, tag: str, *, required: bool = False) -> Node | None:
    found = parent.find_all(tag)
    if len(found) > 1 or (required and not found):
        raise CampaignError(f"Expected {'exactly one' if required else 'at most one'} {tag} block")
    return found[0] if found else None


def _field(node: Node | None, tag: str, default: Any = None) -> Any:
    child = _one(node, tag) if node else None
    return child.args[0] if child and child.args else default


def _addon_last(status, addon_mod, *, required=True):
    """Keep native requirements in order; our merged registries must load last."""
    info=_one(status,'saveinfo',required=True)
    mods=_one(info,'mods')
    if mods is None:
        if not required:return
        mods=block('{mods}');info.append(mods)
    matches=[atom for atom in mods.atoms[1:] if atom.value.casefold()==addon_mod.casefold()]
    if not required and not matches:return
    if len(matches)==1 and mods.atoms[-1] is matches[0] and matches[0].value==addon_mod:return
    mods.items=[item for item in mods.items if item not in matches]
    mods.items.extend([Trivia(' '),Atom(addon_mod,quote(addon_mod),True)])


def _numeric(value: Any) -> int | float | None:
    if value is None:
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def _all_atoms(node: Node | Document):
    for item in node.items:
        if isinstance(item, Atom):
            yield item
        elif isinstance(item, Node):
            yield from _all_atoms(item)


def _node_eids(node: Node | Document) -> set[str]:
    return {canonical_eid(a.value) for a in _all_atoms(node) if _EID.fullmatch(a.value)}


@dataclass
class CampaignSnapshot:
    source_path: Path
    source_hash: str
    campaign_name: str
    army: str
    game_version: str
    mods: list[str]
    units: list[dict]
    squads: list[dict]
    diagnostics: list[str]
    _zip_bytes: bytes = field(repr=False)
    _scene: Document = field(repr=False)
    _status: Document = field(repr=False)
    _scene_encoding: str = field(repr=False)
    _status_encoding: str = field(repr=False)

    def to_dict(self) -> dict:
        return json.loads(json.dumps({"source_path": str(self.source_path), "source_hash": self.source_hash,
                                      "campaign_name": self.campaign_name, "army": self.army,
                                      "game_version": self.game_version, "mods": self.mods,
                                      "units": self.units, "squads": self.squads,
                                      "diagnostics": self.diagnostics}))


def _snapshot(raw: bytes, source_path: Path) -> CampaignSnapshot:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            duplicates = [name for name, count in Counter(names).items() if count > 1]
            if duplicates:
                raise CampaignError(f"Duplicate ZIP entries are ambiguous: {duplicates[:5]}")
            for name in ("campaign.scn", "status"):
                if name not in names:
                    raise CampaignError(f"This is not a supported Conquest campaign: missing {name}")
                info = archive.getinfo(name)
                if info.file_size > MAX_MEMBER_BYTES or info.flag_bits & 1:
                    raise CampaignError(f"Unsupported size or encryption for {name}")
            scene_text, scene_encoding = _decode(archive.read("campaign.scn"))
            status_text, status_encoding = _decode(archive.read("status"))
    except (zipfile.BadZipFile, OSError, KeyError, RuntimeError) as exc:
        raise CampaignError(f"Cannot read Conquest ZIP: {exc}") from exc
    try:
        scene, status = parse(scene_text), parse(status_text)
        campaign = _one(scene, "campaign", required=True)
        saveinfo = _one(status, "saveinfo", required=True)
        empty = not campaign.children
        squad_block = None if empty else _one(campaign, "CampaignSquads", required=True)
    except ValueError as exc:
        raise CampaignError(f"Invalid campaign block structure: {exc}") from exc
    # A conquest saved before buying anything is just {campaign}: there is
    # nothing to build with yet, which is not a damaged or unknown save.
    if empty:
        raise CampaignError("This Conquest has no units yet")
    diagnostics: list[str] = []
    units, known = [], {}
    for node in campaign.children:
        if node.head.casefold() not in ("entity", "human"):
            continue
        if len(node.args) != 2:
            raise CampaignError(f"Malformed {node.head} header")
        template, eid = node.args[0], canonical_eid(node.args[1])
        if eid in known:
            raise CampaignError(f"Duplicate entity identifier {eid}")
        vitality = next((n for n in node.find_all("Extender") if n.args == ["vitality"]), None)
        unit = {"eid": eid, "template": template,
                "kind": "human" if node.head.casefold() == "human" else "vehicle",
                "mid": _numeric(_field(node, "MID")),
                "veterancy": _numeric(_field(vitality, "Veterancy", 0)),
                "score": _numeric(_field(node, "Score", 0)),
                "squad_index": None, "squad": None, "stage": None,
                "crew_eids": [], "owned": False,
                "persisted_state": sorted({n.head for n in node.children if n.head in
                                           ("Armor", "Component", "Weaponry", "Chassis", "Placer")})}
        units.append(unit)
        known[eid] = unit
    squads = []
    for index, row in enumerate(squad_block.children):
        values = [a.value for a in row.atoms]
        if row.children or len(values) < 2 or not row.atoms[0].quoted or not row.atoms[1].quoted:
            raise CampaignError(f"Unrecognized CampaignSquads row {index}")
        template, stage = values[:2]
        eids = [canonical_eid(e) for e in values[2:]]
        live = [e for e in eids if int(e, 16) != _PLACEHOLDER]
        vehicles = [e for e in live if e in known and known[e]["kind"] == "vehicle"]
        humans = [e for e in live if e in known and known[e]["kind"] == "human"]
        missing = [e for e in live if e not in known]
        if missing:
            diagnostics.append(f"Squad {index} has absent EIDs: {', '.join(missing)}")
        squads.append({"index": index, "template": template, "stage": stage,
                       "eids": eids, "vehicle_eids": vehicles, "human_eids": humans,
                       "missing_eids": missing})
        for eid in live:
            if eid not in known:
                continue
            unit = known[eid]
            if unit["owned"]:
                diagnostics.append(f"Entity {eid} is referenced by multiple squads")
            unit.update(owned=True, squad_index=index, squad=template, stage=stage)
            if unit["kind"] == "vehicle":
                unit["crew_eids"] = humans[:]
    mod_node = _one(saveinfo, "mods")
    mods = mod_node.args[:] if mod_node else []
    if mod_node and (mod_node.children or any(not a.quoted for a in mod_node.atoms[1:])):
        raise CampaignError("Unsupported mods block; expected a list of quoted requirement names")
    if len(mods) != len({name.casefold() for name in mods}):
        raise CampaignError("Duplicate mod requirements in the save are ambiguous")
    return CampaignSnapshot(source_path.resolve(), hashlib.sha256(raw).hexdigest(),
                            _field(saveinfo, "name", source_path.stem),
                            _field(saveinfo, "army", ""), _field(saveinfo, "gameVersion", ""),
                            mods, units, squads, diagnostics, raw, scene, status,
                            scene_encoding, status_encoding)


def load_campaign(path: str | Path) -> CampaignSnapshot:
    path = Path(path)
    if path.stat().st_size > 1024 * 1024 * 1024:
        raise CampaignError("Save exceeds the supported 1 GiB ZIP size")
    return _snapshot(path.read_bytes(), path)


def _profile(profile: dict | None) -> dict:
    profile = json.loads(json.dumps(profile or {}))
    allowed = {"recipient_module_names", "donor_module_names", "donor_name_map",
               "weaponry_policy", "weapon_places", "inventory_policy", "compatible_recipient_inventory",
               "removed_module_bones", "removed_module_volumes", "donor_module_bones",
               "donor_module_volumes", "allowed_damage_ids", "damage_id_mapping",
               "hull_authority", "turret_authority", "commander_mg", "retired_inventory_users", "gun_swap", "smoke_launchers", "rider_mgs"}
    if set(profile) - allowed:
        raise CampaignError(f"Unknown state profile fields: {sorted(set(profile) - allowed)}")
    for key in ("recipient_module_names", "donor_module_names", "removed_module_bones",
                "removed_module_volumes", "donor_module_bones", "donor_module_volumes", "allowed_damage_ids", "retired_inventory_users", "weapon_places"):
        if key in profile and (not isinstance(profile[key], list) or
                               any(not isinstance(x, str) or not x for x in profile[key])):
            raise CampaignError(f"{key} must be a list of component/volume names")
    mapping = profile.get("donor_name_map", {})
    if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                           or not k or not v for k, v in mapping.items()):
        raise CampaignError("donor_name_map must map original donor names to emitted names")
    if profile.get("weaponry_policy") not in (None, "recipient", "donor_complete", "none_present", "reset"):
        raise CampaignError("Weaponry policy is unsupported; a verified slot mapping is required")
    if profile.get("inventory_policy") not in (None, "donor", "recipient", "none_present", "reset", "gun_replace"):
        raise CampaignError("Unsupported vehicle inventory policy; grids cannot be blindly merged")
    gun=profile.get('gun_swap')
    if profile.get('inventory_policy')=='gun_replace':
        if not isinstance(gun,dict) or not isinstance(gun.get('place'),str) or not isinstance(gun.get('ammo_items'),list):
            raise CampaignError('GunAssembly: invalid ammunition migration profile.')
        if gun.get('old_ammo') is not None and not isinstance(gun['old_ammo'],str):
            raise CampaignError('GunAssembly: invalid previous ammunition family.')
        if 'old_ammo_families' in gun and (not isinstance(gun['old_ammo_families'],list) or any(not isinstance(v,str) or not v for v in gun['old_ammo_families'])):
            raise CampaignError('GunAssembly: invalid previous ammunition family.')
        for text in gun['ammo_items']:
            if not isinstance(text,str) or block(text).head!='item':raise CampaignError('GunAssembly: invalid ammunition item.')
    smoke = profile.get('smoke_launchers')
    if smoke is not None:
        if (not isinstance(smoke, dict) or type(smoke.get('initialize')) is not bool
                or not isinstance(smoke.get('initial_item'), str) or block(smoke['initial_item']).head != 'item'):
            raise CampaignError('El perfil de munición de humo no es válido.')
    riders = profile.get('rider_mgs')
    if riders is not None:
        mounts = riders.get('mounts') if isinstance(riders, dict) else None
        if not isinstance(mounts, list) or any(
                not isinstance(m, dict) or any(not isinstance(m.get(key), str) or not m[key]
                                              for key in ('slot', 'item', 'initial_item', 'ammo_item')) for m in mounts):
            raise CampaignError('El perfil de inventario de las MG de pasajero no es válido.')
    mount = profile.get('commander_mg')
    if mount is not None:
        if not isinstance(mount, dict) or any(not isinstance(mount.get(key), str) or not mount[key]
                                             for key in ('slot','item','initial_item','ammo_item')):
            raise CampaignError('El perfil de inventario de la montura no es válido.')
        if 'initialize' in mount and not isinstance(mount['initialize'], bool):
            raise CampaignError('El perfil de inventario de la montura no es válido.')
    return profile


def _entity_nodes(campaign: Node) -> dict[str, Node]:
    return {canonical_eid(n.args[1]): n for n in campaign.children
            if n.head.casefold() in ("entity", "human")}


def _link_target(link: Node) -> tuple[str, str]:
    if len(link.args) != 1 or len(link.children) != 1:
        raise CampaignError("Unsupported Link structure")
    target = link.children[0]
    # {0x9000 "driver"} is a seat; {0xb816 "search_light" "gunner"} or {0x... "p05" "4"}
    # binds a soldier to a named point of an object (map emplacements, trenches).
    if len(target.atoms) not in (2, 3) or target.children:
        raise CampaignError("Unsupported Link attachment structure")
    return canonical_eid(link.args[0]), canonical_eid(target.head)


_OTHER_SQUAD_BOARDERS = "Boarded soldiers from other squads are dismounted and remain in their original squads."


def _owned_boarder(snapshot: CampaignSnapshot, link: Node, vehicles: set[str]) -> str:
    """A boarding human belongs to the campaign, not necessarily the vehicle's squad.

    Rebuilding detaches boarding links; it must not move these humans between
    squads or treat vehicle attachments as passengers.
    """
    owner, target = _link_target(link)
    human = next((u for u in snapshot.units if u['eid'] == owner), None)
    memberships = sum(s['eids'].count(owner) for s in snapshot.squads)
    bindings = [n for n in snapshot._scene.find('campaign').find_all('Link')
                if n.args and canonical_eid(n.args[0]) == owner]
    if (target not in vehicles or not human or human['kind'] != 'human'
            or not human['owned'] or memberships != 1 or len(bindings) != 1):
        raise CampaignError("Vehicle links include external attachments or unowned occupants")
    return owner


def _inventory_nodes(campaign: Node) -> dict[str, Node]:
    result = {}
    for inv in campaign.find_all("Inventory"):
        if len(inv.args) != 1:
            raise CampaignError("Unrecognized Inventory header")
        eid = canonical_eid(inv.args[0])
        if eid in result:
            raise CampaignError(f"Multiple inventories belong to {eid}")
        result[eid] = inv
    return result


def _is_comment(item) -> bool:
    return isinstance(item, Trivia) and item.raw.startswith((";", "//", "/*"))


def _clean_inventory(inventory: Node) -> bool:
    """Write an inventory the way the game does: a bare hexadecimal id and no
    comments. The save reader stops the game on a quoted id ("Integer expected"
    at {Inventory "0x..."}); DEF comments never appear in a game-written save."""
    changed = False
    header = inventory.atoms[1]
    if header.quoted:
        header.quoted = False
        header.set(header.value)
        changed = True
    for node in inventory.walk():
        if any(_is_comment(item) for item in node.items):
            node.items = [Trivia(" ") if _is_comment(item) else item for item in node.items]
            changed = True
    return changed


def _inventory_problems(campaign: Node) -> list[str]:
    return [inv.args[0] for inv in campaign.find_all("Inventory")
            if len(inv.atoms) > 1 and (inv.atoms[1].quoted or any(_is_comment(i) for n in inv.walk() for i in n.items))]


def release_orphan_guns(snapshot: CampaignSnapshot, places: dict[str, set[str]]):
    """Workshop vehicles whose saved inventory binds a gun to a place the
    vehicle no longer has (a Stuart turned searchlight carrier kept its roof
    MG on "mgun"): the game crashes spawning them. The gun stays as cargo.
    places maps each installed workshop template to its Weaponry places.
    Returns (replacements, released [(eid, slot)]), or (None, []) when all is well."""
    scene = snapshot._scene.clone()
    campaign = _one(scene, "campaign", required=True)
    templates = {canonical_eid(n.args[1]): re.sub(r"\([^()]*\)$", "", n.args[0])
                 for n in campaign.children if n.head.casefold() == "entity" and len(n.args) > 1}
    released = []
    for inventory in campaign.find_all("Inventory"):
        if not inventory.args:
            continue
        eid = canonical_eid(inventory.args[0])
        valid = places.get(templates.get(eid, ""))
        if valid is None:
            continue
        for item in inventory.walk("item"):
            for user in list(item.find_all("user")):
                if user.args and user.args[0] not in valid:
                    item.remove(user)
                    released.append((eid, user.args[0]))
    if not released:
        return None, []
    replacements = {"campaign.scn": scene.render().encode(snapshot._scene_encoding)}
    _snapshot(build_save_copy(snapshot, replacements), snapshot.source_path)
    return replacements, released


def repair_inventory_syntax(snapshot: CampaignSnapshot):
    """Saves from 0.2.0-0.2.2 where a designed vehicle got a fresh inventory
    with a quoted id: the game closes while loading the campaign. Only the
    header and comments of those inventories change; contents stay as they are.
    Returns (replacements, repaired ids), or (None, []) when nothing is wrong."""
    scene = snapshot._scene.clone()
    repaired = [inv.args[0] for inv in _one(scene, "campaign", required=True).find_all("Inventory")
                if len(inv.atoms) > 1 and _clean_inventory(inv)]
    if not repaired:
        return None, []
    replacements = {"campaign.scn": scene.render().encode(snapshot._scene_encoding)}
    after = _snapshot(build_save_copy(snapshot, replacements), snapshot.source_path)
    if _inventory_problems(_one(after._scene, "campaign", required=True)):
        raise CampaignError("Inventory repair did not produce game-readable inventories")
    return replacements, repaired


def _guard_state(recipient: Node, donor: Node, inventories: dict, reid: str, deid: str,
                 profile: dict) -> None:
    state = [n for entity in (recipient, donor) for n in entity.children
             if n.head in ("Armor", "Component")]
    if state and ("recipient_module_names" not in profile or "donor_module_names" not in profile):
        raise CampaignError("Persisted damage needs compiler component ownership before migration")
    weapons = recipient.find_all("Weaponry") + donor.find_all("Weaponry")
    if len(recipient.find_all("Weaponry")) > 1 or len(donor.find_all("Weaponry")) > 1:
        raise CampaignError("Multiple persisted Weaponry blocks need a slot-specific adapter")
    # "reset": chassis keeps some weapons and the module brings others; slot
    # state cannot be mapped, so the game reloads weapons from the definition.
    if weapons and profile.get("weaponry_policy") not in ("donor_complete", "reset"):
        raise CampaignError("Persisted Weaponry needs a verified donor_complete migration profile")
    for entity in (recipient, donor):
        if any(n.head in ("Placer", "Skeleton") and _node_eids(n) for n in entity.children):
            raise CampaignError("Persisted skeleton/seat EID bindings need an explicit adapter")
    if reid in inventories or deid in inventories:
        policy = profile.get("inventory_policy")
        if policy not in ("donor", "recipient", "reset", "gun_replace"):
            raise CampaignError("Vehicle ammunition inventories need an explicit compatible source policy")
        if policy == "recipient" and not profile.get("compatible_recipient_inventory"):
            raise CampaignError("Recipient inventory requires a confirmed ammunition compatibility profile")
        # "reset" (mixed loadout): both ammo grids are dropped and the vehicle
        # is restocked from its generated definition. A chosen source with no
        # saved inventory (never fought) carries its definition's load; the new
        # vehicle then has no saved inventory either and the game stocks it
        # from the generated definition, which holds that same ammunition.
    recipient_names = set(profile.get("recipient_module_names", []))
    donor_names = set(profile.get("donor_module_names", []))
    name_map = profile.get("donor_name_map", {})
    if set(name_map) - donor_names:
        raise CampaignError("Donor component map includes components outside the transplanted module")
    if len(set(name_map.values())) != len(name_map):
        raise CampaignError("Donor component map contains collisions")
    # A missing renamed donor component would silently lose or misassign damage.
    if donor_names and not donor_names <= set(name_map):
        raise CampaignError("Every donor module component needs an emitted-name mapping")
    if recipient_names & set(name_map.values()):
        # Same original name is permitted: old recipient state is removed first.
        pass


def _fresh_eid(campaign: Node) -> str:
    used = {int(e, 16) for e in _node_eids(campaign)}
    # Existing saves use compact EIDs. Reuse a free 16-bit slot without touching
    # any reference, including references to absent entities.
    for value in range(0x8000, 0xFFFF):
        if value not in used:
            return hex(value)
    raise CampaignError("No free compact entity identifier is available")


def _is_slicer(node) -> bool:
    """{Slicer 0x82f4 {"shieldl5"} ...}: detachable parts (side skirts, shields)
    a vehicle has already lost in battle. One vehicle, plain part names."""
    if node.head != "Slicer":
        return False
    if len(_node_eids(node)) != 1 or any(child.children or child.args for child in node.children):
        raise CampaignError("Slicer block with several units or nested data; migration blocked")
    return True


def _is_trailer_link(node) -> bool:
    """{LinkTrailer <towed gun> <tractor>}: a gun hooked to its truck. Using either
    one unhooks it; the other stays as its own unit."""
    if node.head != "LinkTrailer":
        return False
    if len(node.args) != 2 or node.children or len(_node_eids(node)) != 2:
        raise CampaignError("LinkTrailer block with unexpected data; migration blocked")
    return True


def plan_assembly(snapshot: CampaignSnapshot, recipient_eid: str | int, donor_eid: str | int,
                  new_template: str, crew_slots: int | None = None, *,
                  addon_mod: str = "modular_workshop:0", state_profile: dict | None = None) -> dict:
    """Create a deterministic, JSON-serializable migration plan; no writes.

    Compiler ownership profiles are mandatory for existing damage, loaded weapon
    state or vehicle inventories. Infantry entities and their inventories survive
    unchanged. Boarding Links to consumed vehicles are detached in reserve.
    """
    reid, deid = canonical_eid(recipient_eid), canonical_eid(donor_eid)
    if reid == deid:
        raise CampaignError("Recipient and donor must be two distinct owned vehicles")
    if not isinstance(new_template, str) or not _TEMPLATE.fullmatch(new_template) or ".." in new_template:
        raise CampaignError("Invalid generated vehicle template identifier")
    if not isinstance(addon_mod, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+:[0-9]+", addon_mod):
        raise CampaignError("Addon requirement must have the form mod_name:version")
    addon_name = addon_mod.split(":")[0].casefold()
    if any(mod.split(":")[0].casefold() == addon_name and mod.casefold() != addon_mod.casefold()
           for mod in snapshot.mods):
        raise CampaignError("Campaign requires a different addon version; an explicit version migration is needed")
    known = {u["eid"]: u for u in snapshot.units}
    for eid in (reid, deid):
        if eid not in known or known[eid]["kind"] != "vehicle" or not known[eid]["owned"]:
            raise CampaignError(f"{eid} is not a vehicle owned by this campaign")
        memberships = [s for s in snapshot.squads if eid in s["eids"]]
        if len(memberships) != 1 or memberships[0]["vehicle_eids"] != [eid] or memberships[0]["missing_eids"]:
            raise CampaignError(f"Ambiguous or incomplete squad for vehicle {eid}")
    r, d = known[reid], known[deid]
    selected_rows = [snapshot.squads[r["squad_index"]], snapshot.squads[d["squad_index"]]]
    humans = selected_rows[0]["human_eids"] + selected_rows[1]["human_eids"]
    if len(humans) != len(set(humans)):
        raise CampaignError("Human crew appears more than once in selected squads")
    for eid in humans:
        if sum(eid in s["eids"] for s in snapshot.squads) != 1:
            raise CampaignError(f"Human {eid} is shared by multiple squads")
    if crew_slots is None:
        crew_slots = len(selected_rows[0]["human_eids"])
    if isinstance(crew_slots, bool) or not isinstance(crew_slots, int) or not 0 <= crew_slots <= 128:
        raise CampaignError("Crew capacity must be an integer from 0 to 128")
    profile = _profile(state_profile)
    if profile.get("hull_authority", r["template"]) != r["template"] or profile.get("turret_authority", d["template"]) != d["template"]:
        raise CampaignError("Compiler state profile belongs to different source vehicles")
    campaign = _one(snapshot._scene, "campaign", required=True)
    entities, inventories = _entity_nodes(campaign), _inventory_nodes(campaign)
    _guard_state(entities[reid], entities[deid], inventories, reid, deid, profile)
    selected = {reid, deid}
    links_removed = []
    other_boarders = False
    for node in campaign.children:
        refs = _node_eids(node) & selected
        if not refs:
            continue
        if node is entities[reid] or node is entities[deid]:
            # Self references inside vehicle blocks are unknown unless headers.
            nested = set().union(*(_node_eids(n) for n in node.children)) if node.children else set()
            if nested & selected:
                raise CampaignError("Vehicle contains nested references to consumed vehicles")
            continue
        if node.head in ("Inventory", "CampaignSquads"):
            continue
        if node.head == "Tags":
            # Game labels on one unit ({Tags "_user" "player_armor" ... 0x800a}).
            # The chassis' labels pass to the new vehicle; the donor's go with it.
            if node.children or len(_node_eids(node)) != 1:
                raise CampaignError("Tags block with nested data or several units; migration blocked")
            continue
        if _is_slicer(node) or _is_trailer_link(node):
            continue  # lost parts follow the chassis; a towing hook is released
        if node.head == "Link":
            owner = _owned_boarder(snapshot, node, selected)
            other_boarders |= owner not in humans
            links_removed.append(node.render())
            continue
        raise CampaignError(f"Unknown {node.head} reference to a consumed vehicle; migration blocked")
    reserve_groups = defaultdict(list)
    assigned, spare = humans[:crew_slots], humans[crew_slots:]
    for eid in spare:
        reserve_groups[known[eid]["template"]].append(eid)
    reserves = []
    for breed, eids in sorted(reserve_groups.items()):
        identity = hashlib.sha256((snapshot.army + "\0" + breed + "\0" + str(len(eids))).encode()).hexdigest()[:12]
        pieces = breed.split("/")
        reserves.append({"template": f"mw_reserve_{identity}({snapshot.army})", "army": snapshot.army,
                         "breed": breed, "period": pieces[2] if len(pieces) >= 4 and pieces[0] == "mp" else None,
                         "eids": eids, "crew_slots": len(eids)})
    warnings = ["Vehicle state follows compiler ownership; no game-load validation has been performed."]
    if links_removed:
        warnings.append("Existing crew boarding links are detached; modified squads return to reserve.")
    if other_boarders:
        warnings.append(_OTHER_SQUAD_BOARDERS)
    archived_inventory = [inventories[e].render() for e in (reid, deid) if e in inventories]
    if len(archived_inventory) == 2:
        warnings.append("Both original vehicle inventories are archived; only the verified selected source is transferred.")
    plan = {"schema_version": 1, "operation": "assemble_two_into_one", "source_hash": snapshot.source_hash,
            "recipient_eid": reid, "donor_eid": deid, "recipient_template": r["template"],
            "donor_template": d["template"], "new_template": new_template, "new_eid": _fresh_eid(campaign),
            "addon_mod": addon_mod, "crew_slots": crew_slots, "crew_eids": assigned,
            "vacant_crew_slots": max(0, crew_slots - len(assigned)), "reserve_squads": reserves,
            "consumed_eids": [reid, deid], "consumed_squad_indices": [r["squad_index"], d["squad_index"]],
            "state_profile": profile, "archived_vehicle_inventories": archived_inventory,
            "detached_links": links_removed, "warnings": warnings}
    plan["plan_id"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False,
                                                separators=(",", ":")).encode()).hexdigest()
    return plan


REFIT_RESET = ("Armor", "Component", "Weaponry", "Placer", "Volume", "Chassis")


def plan_draft(snapshot,recipient_eid,new_template,crew_slots,steps,*,addon_mod='modular_workshop:0',state_profile=None):
    """Validate all distinct donors once; preserve original soldiers in reserve."""
    reid=canonical_eid(recipient_eid);known={u['eid']:u for u in snapshot.units};steps=copy.deepcopy(steps)
    donors=[]
    for step in steps:
        step['profile']=_profile(step['profile'])
        if step['kind'] in ('seed','removal','module_edit'):continue
        if step.get('donor_depot') or step.get('donor_catalog'):continue
        eid=canonical_eid(step['donor_eid']);step['donor_eid']=eid
        if eid==reid:raise CampaignError('A design cannot consume its receiver as a donor')
        if eid not in known or known[eid]['template']!=step['donor_template']:
            raise CampaignError('A saved donor no longer matches this campaign')
        if eid not in donors:donors.append(eid)
    validated=[]
    for eid in donors:
        step=next(s for s in steps if s.get('donor_eid')==eid)
        profile={**step['profile'],'hull_authority':known[reid]['template'],'turret_authority':known[eid]['template']}
        validated.append(plan_assembly(snapshot,reid,eid,new_template,crew_slots,addon_mod=addon_mod,state_profile=profile))
    result=validated[0] if validated else plan_edit(snapshot,reid,new_template,crew_slots,addon_mod=addon_mod,state_profile=state_profile,keep_names=True)
    selected=[reid,*donors];humans=[]
    for eid in selected:
        for human in snapshot.squads[known[eid]['squad_index']]['human_eids']:
            if human in humans:raise CampaignError('Human crew appears more than once in the design')
            humans.append(human)
    groups=defaultdict(list)
    for eid in humans[crew_slots:]:groups[known[eid]['template']].append(eid)
    reserves=[]
    for breed,eids in sorted(groups.items()):
        identity=hashlib.sha256((snapshot.army+'\0'+breed+'\0'+str(len(eids))).encode()).hexdigest()[:12];pieces=breed.split('/')
        reserves.append({'template':f'mw_reserve_{identity}({snapshot.army})','army':snapshot.army,'breed':breed,
            'period':pieces[2] if len(pieces)>=4 and pieces[0]=='mp' else None,'eids':eids,'crew_slots':len(eids)})
    result.update(operation='assemble_draft',draft_steps=steps,crew_eids=humans[:crew_slots],reserve_squads=reserves,
        vacant_crew_slots=max(0,crew_slots-len(humans)),consumed_eids=selected,
        consumed_squad_indices=[known[e]['squad_index'] for e in selected],state_profile=_profile(state_profile))
    result['archived_vehicle_inventories']=list(dict.fromkeys(s for p in validated for s in p['archived_vehicle_inventories']))
    result.pop('plan_id',None);result['plan_id']=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return result


def _inventory_ammo_family(item):
    """Read DEF shorthand and the engine's split, persisted item arguments."""
    if not item.args:return ''
    tokens=item.args[0].removeprefix('ammo ').split()
    return ' '.join(tokens[:-1]) if len(tokens)>1 else ' '.join(tokens)


def _retired_ammo(gun):
    return {f.removeprefix('ammo ') for f in [*(gun or {}).get('old_ammo_families',[]),
            (gun or {}).get('old_ammo')] if f}


def _remove_inventory_ammo(inventory, families):
    for box in inventory.find_all('box'):
        for item in list(box.find_all('item')):
            if _inventory_ammo_family(item) in families:box.remove(item)


def _draft_inventory(inventories,reid,steps,final_profile=None):
    current=inventories[reid].clone() if reid in inventories else None
    final_gun=(final_profile or {}).get('gun_swap') or {}
    # The final loadout is authoritative: an intermediate replacement may
    # retire belt ammo that the commander mount still needs in the result.
    final_removals=_retired_ammo(final_gun) if 'old_ammo_families' in final_gun else None
    full_sources=set()
    for step in steps:
        if step['kind'] in ('seed','module_edit'):continue
        profile=step['profile'];policy=profile.get('inventory_policy')
        if policy=='donor':
            current=inventories[step['donor_eid']].clone() if step['donor_eid'] in inventories else None
            if current is not None:full_sources.add(step['donor_eid'])
        elif policy=='reset':
            current=block('{Inventory '+reid+' '+''.join(step['inventory_boxes'])+'}');_clean_inventory(current)
        gun=profile.get('gun_swap')
        if current is not None and gun and gun.get('initialize',True):
            families=_retired_ammo(gun)
            if final_removals is not None:families &= final_removals
            _remove_inventory_ammo(current,families)
            box=current.find('box')
            if box is None:box=block('{box}');current.append(box)
            if step.get('donor_eid') not in full_sources:
                for text in gun['ammo_items']:box.append(block(text))
    if current is not None and final_removals is not None:
        _remove_inventory_ammo(current,final_removals)
    return current


def plan_refit(snapshot: CampaignSnapshot, vehicle_eid: str | int, new_template: str,
               crew_slots: int | None = None, *, addon_mod: str = "modular_workshop:0", role: str = "hull") -> dict:
    """One owned vehicle + a part from the workshop depot -> one new vehicle.

    role says what the owned vehicle provides ("hull" or "turret"); the other
    part has no campaign entity. The new vehicle takes the vehicle's place,
    crew, tags and MID, and starts repaired, refuelled and restocked: the
    depot part has no damage history and mixed slot state cannot be mapped.
    """
    eid = canonical_eid(vehicle_eid)
    if role not in ("hull", "turret"):
        raise CampaignError("Refit role must be hull or turret")
    if not isinstance(new_template, str) or not _TEMPLATE.fullmatch(new_template) or ".." in new_template:
        raise CampaignError("Invalid generated vehicle template identifier")
    if not isinstance(addon_mod, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+:[0-9]+", addon_mod):
        raise CampaignError("Addon requirement must have the form mod_name:version")
    addon_name = addon_mod.split(":")[0].casefold()
    if any(mod.split(":")[0].casefold() == addon_name and mod.casefold() != addon_mod.casefold()
           for mod in snapshot.mods):
        raise CampaignError("Campaign requires a different addon version; an explicit version migration is needed")
    known = {u["eid"]: u for u in snapshot.units}
    if eid not in known or known[eid]["kind"] != "vehicle" or not known[eid]["owned"]:
        raise CampaignError(f"{eid} is not a vehicle owned by this campaign")
    memberships = [s for s in snapshot.squads if eid in s["eids"]]
    if len(memberships) != 1 or memberships[0]["vehicle_eids"] != [eid] or memberships[0]["missing_eids"]:
        raise CampaignError(f"Ambiguous or incomplete squad for vehicle {eid}")
    vehicle = known[eid]
    humans = list(snapshot.squads[vehicle["squad_index"]]["human_eids"])
    for human in humans:
        if sum(human in s["eids"] for s in snapshot.squads) != 1:
            raise CampaignError(f"Human {human} is shared by multiple squads")
    if crew_slots is None:
        crew_slots = len(humans)
    if isinstance(crew_slots, bool) or not isinstance(crew_slots, int) or not 0 <= crew_slots <= 128:
        raise CampaignError("Crew capacity must be an integer from 0 to 128")
    campaign = _one(snapshot._scene, "campaign", required=True)
    entities = _entity_nodes(campaign)
    if any(n.head in ("Placer", "Skeleton") and _node_eids(n) for n in entities[eid].children):
        raise CampaignError("Persisted skeleton/seat EID bindings need an explicit adapter")
    links_removed = []
    other_boarders = False
    for node in campaign.children:
        refs = _node_eids(node) & {eid}
        if not refs or node.head in ("Inventory", "CampaignSquads"):
            continue
        if node is entities[eid]:
            nested = set().union(*(_node_eids(n) for n in node.children)) if node.children else set()
            if eid in nested:
                raise CampaignError("Vehicle contains nested references to itself")
            continue
        if node.head == "Tags":
            if node.children or len(_node_eids(node)) != 1:
                raise CampaignError("Tags block with nested data or several units; migration blocked")
            continue
        if _is_slicer(node) or _is_trailer_link(node):
            continue
        if node.head == "Link":
            owner = _owned_boarder(snapshot, node, {eid})
            other_boarders |= owner not in humans
            links_removed.append(node.render())
            continue
        raise CampaignError(f"Unknown {node.head} reference to the refitted vehicle; migration blocked")
    reserve_groups = defaultdict(list)
    assigned, spare = humans[:crew_slots], humans[crew_slots:]
    for human in spare:
        reserve_groups[known[human]["template"]].append(human)
    reserves = []
    for breed, eids in sorted(reserve_groups.items()):
        identity = hashlib.sha256((snapshot.army + "\0" + breed + "\0" + str(len(eids))).encode()).hexdigest()[:12]
        pieces = breed.split("/")
        reserves.append({"template": f"mw_reserve_{identity}({snapshot.army})", "army": snapshot.army,
                         "breed": breed, "period": pieces[2] if len(pieces) >= 4 and pieces[0] == "mp" else None,
                         "eids": eids, "crew_slots": len(eids)})
    plan = {"schema_version": 1, "operation": "refit_one", "source_hash": snapshot.source_hash,
            "recipient_eid": eid, "donor_eid": None, "role": role,
            "recipient_template": vehicle["template"], "donor_template": None,
            "new_template": new_template, "new_eid": _fresh_eid(campaign),
            "addon_mod": addon_mod, "crew_slots": crew_slots, "crew_eids": assigned,
            "vacant_crew_slots": max(0, crew_slots - len(assigned)), "reserve_squads": reserves,
            "consumed_eids": [eid], "consumed_squad_indices": [vehicle["squad_index"]],
            "state_profile": {}, "archived_vehicle_inventories": [],
            "detached_links": links_removed,
            "warnings": ["The refitted vehicle starts repaired, refuelled and restocked."] +
                        ([_OTHER_SQUAD_BOARDERS] if other_boarders else [])}
    plan["plan_id"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False,
                                                separators=(",", ":")).encode()).hexdigest()
    return plan


def plan_edit(snapshot: CampaignSnapshot, vehicle_eid: str | int, new_template: str,
              crew_slots: int | None = None, *, addon_mod: str = "modular_workshop:0",
              state_profile: dict | None = None, keep_names: bool = False) -> dict:
    """Editar: one owned vehicle rebuilt from its own parts (moved, rotated).

    Same checks and squad handling as a refit, but nothing is consumed besides
    the vehicle itself and its state is kept: crew, MID, inventory, fuel and
    the damage of every part that still exists. keep_names: a workshop vehicle
    rebuilt from its recipe keeps its part names; a game vehicle used as its
    own module renames the module's parts like any donor (state_profile).
    """
    plan = plan_refit(snapshot, vehicle_eid, new_template, crew_slots, addon_mod=addon_mod, role="hull")
    plan.pop("plan_id")
    plan.update({"operation": "edit_one", "role": "edit", "state_profile": _profile(state_profile),
                 "keep_names": bool(keep_names),
                 "warnings": ["The edited vehicle keeps its crew, inventory and the damage of parts that still exist."] +
                             ([_OTHER_SQUAD_BOARDERS] if _OTHER_SQUAD_BOARDERS in plan['warnings'] else [])})
    plan["plan_id"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False,
                                                separators=(",", ":")).encode()).hexdigest()
    return plan


def _edited_state(vehicle: Node, profile: dict, keep_names: bool) -> Node:
    """State of an edited vehicle: everything it had, minus seat bindings of
    the old mount and damage records of parts that no longer exist."""
    if not keep_names:
        try:
            return _migrate_state(vehicle, vehicle, profile)
        except CampaignError:
            pass  # unknown persisted state: keep it and only drop stale names
    new = vehicle.clone()
    for node in new.find_all("Placer"):
        new.remove(node)
    allowed = set(profile.get("allowed_damage_ids") or [])
    if 'allowed_damage_ids' in profile:
        for component in new.find_all("Component"):
            if not component.args or component.args[0] not in allowed:
                new.remove(component)
        for armor in new.find_all("Armor"):
            for wounds in armor.find_all("wounds"):
                for wound in list(wounds.children):
                    if not wound.args or wound.args[0] not in allowed:
                        wounds.remove(wound)
    from .weapon_state import reconcile_weaponry
    reconcile_weaponry(new, profile)
    return new


def _fresh_state(vehicle: Node) -> Node:
    """Keep identity (MID, position, tags-relevant fields); drop part state."""
    new = vehicle.clone()
    for child in list(new.children):
        if child.head in REFIT_RESET:
            new.remove(child)
    return new


def _migrate_state(recipient: Node, donor: Node, profile: dict) -> Node:
    new = recipient.clone()
    old_names = set(profile.get("recipient_module_names", []))
    donor_names = set(profile.get("donor_module_names", []))
    mapping = profile.get("donor_name_map", {})
    for component in new.find_all("Component"):
        if component.args and component.args[0] in old_names:
            new.remove(component)
    for component in donor.find_all("Component"):
        if component.args and component.args[0] in donor_names:
            copied = component.clone()
            replace_atoms(copied, mapping)
            new.append(copied)
    new_armor = _one(new, "Armor")
    donor_armor = _one(donor, "Armor")
    if new_armor:
        wounds = _one(new_armor, "wounds")
        if wounds:
            for wound in wounds.children:
                if wound.head not in ("damage", "wound") or not wound.args:
                    raise CampaignError("Unknown persisted armor state; component adapter required")
                if wound.args[0] in old_names:
                    wounds.remove(wound)
    if donor_armor:
        wounds = _one(donor_armor, "wounds")
        if wounds:
            for wound in wounds.children:
                if wound.head not in ("damage", "wound") or not wound.args:
                    raise CampaignError("Unknown donor armor state; component adapter required")
                if wound.args[0] not in donor_names:
                    continue
                if new_armor is None:
                    new_armor = block("{Armor {wounds}}")
                    new.append(new_armor)
                destination = _one(new_armor, "wounds")
                if destination is None:
                    destination = block("{wounds}")
                    new_armor.append(destination)
                copied = wound.clone()
                replace_atoms(copied, mapping)
                destination.append(copied)
    if profile.get("weaponry_policy") == "donor_complete":
        for weaponry in new.find_all("Weaponry"):
            new.remove(weaponry)
        for weaponry in donor.find_all("Weaponry"):
            copied = weaponry.clone()
            replace_atoms(copied, mapping)
            new.append(copied)
    elif profile.get("weaponry_policy") == "reset":
        for weaponry in new.find_all("Weaponry"):
            new.remove(weaponry)
    # These blocks describe the previous mount, not hull physics or crew history.
    for node in new.find_all("Placer"):
        new.remove(node)
    if "allowed_damage_ids" in profile:
        allowed = set(profile["allowed_damage_ids"])
        for component in new.find_all("Component"):
            if not component.args or component.args[0] not in allowed:
                raise CampaignError(f"Persisted component {component.args[:1]} is absent from emitted resources")
        for armor in new.find_all("Armor"):
            for wounds in armor.find_all("wounds"):
                for wound in wounds.children:
                    if not wound.args or wound.args[0] not in allowed:
                        raise CampaignError(f"Persisted wound {wound.args[:1]} is absent from emitted resources")
    return new


def plan_removal(snapshot: CampaignSnapshot, vehicle_eid: str | int, *, addon_mod: str = "modular_workshop:0") -> dict:
    """Delete one owned vehicle from the campaign: its crew goes to reserve.

    The same checks as a refit (one squad, no unknown references); every
    soldier of its squad stays in the campaign, in reserve squads by breed.
    Boarding soldiers of other squads dismount. Nothing else changes.
    """
    eid = canonical_eid(vehicle_eid)
    known = {u["eid"]: u for u in snapshot.units}
    template = re.sub(r"\([^()]*\)$", "", str((known.get(eid) or {}).get("template") or ""))
    if not template:
        raise CampaignError(f"{eid} is not a vehicle owned by this campaign")
    plan = plan_refit(snapshot, eid, template, 0, addon_mod=addon_mod, role="hull")
    plan.pop("plan_id")
    plan.update({"operation": "remove_one", "role": "remove", "removed_template": template,
                 "warnings": ["The vehicle is deleted; its crew goes to reserve squads."] +
                             ([_OTHER_SQUAD_BOARDERS] if _OTHER_SQUAD_BOARDERS in plan["warnings"] else [])})
    plan["plan_id"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False,
                                                separators=(",", ":")).encode()).hexdigest()
    return plan


def _apply_removal(snapshot: CampaignSnapshot, plan: dict) -> dict[str, bytes]:
    if plan != plan_removal(snapshot, plan["recipient_eid"], addon_mod=plan["addon_mod"]):
        raise CampaignError("Migration plan differs from the validated snapshot; recompute it")
    eid = plan["recipient_eid"]
    scene = snapshot._scene.clone()
    campaign = _one(scene, "campaign", required=True)
    squad_block = _one(campaign, "CampaignSquads", required=True)
    for node in list(campaign.children):
        if eid not in _node_eids(node) or node.head == "CampaignSquads":
            continue
        if node.head.casefold() in ("entity", "inventory", "tags", "link", "linktrailer") or _is_slicer(node):
            campaign.remove(node)
        else:
            raise CampaignError(f"Unknown {node.head} reference to the deleted vehicle")
    rows = squad_block.children
    row = rows[plan["consumed_squad_indices"][0]]
    squad_block.items = [item for item in squad_block.items if item is not row]
    for reserve in plan["reserve_squads"]:
        squad_block.append(block("{" + quote(reserve["template"]) + ' "" ' + " ".join(reserve["eids"]) + "}"))
    replacements = {"campaign.scn": scene.render().encode(snapshot._scene_encoding)}
    status=snapshot._status.clone()
    _addon_last(status,plan['addon_mod'],required=bool(plan['reserve_squads']))
    if status.render()!=snapshot._status.render():
        replacements['status']=status.render().encode(snapshot._status_encoding)
    after = _snapshot(build_save_copy(snapshot, replacements), snapshot.source_path)
    before_nodes = _entity_nodes(_one(snapshot._scene, "campaign", required=True))
    after_nodes = _entity_nodes(_one(after._scene, "campaign", required=True))
    if set(after_nodes) != set(before_nodes) - {eid}:
        raise CampaignError("Emitted save does not delete exactly the selected vehicle")
    for other, node in before_nodes.items():
        if other != eid and after_nodes[other].render() != node.render():
            raise CampaignError(f"Unrelated entity or veteran {other} was altered")
    members = Counter(e for squad in after.squads for e in squad["human_eids"])
    for human in [e for squad in plan["reserve_squads"] for e in squad["eids"]]:
        if members[human] != 1:
            raise CampaignError(f"Veteran {human} was duplicated or omitted from squads")
    if eid in _node_eids(_one(after._scene, "campaign", required=True)):
        raise CampaignError("References to the deleted vehicle remain in the emitted scene")
    return replacements


def apply_plan(snapshot: CampaignSnapshot, plan: dict) -> dict[str, bytes]:
    """Return validated replacements for two ZIP members. Never writes files."""
    if plan.get('operation') in ('store_one','restore_stored'):
        from .vehicle_storage import apply_storage,apply_restoration
        return (apply_storage if plan['operation']=='store_one' else apply_restoration)(snapshot,plan)
    if plan.get('operation') == 'remove_one':
        return _apply_removal(snapshot, plan)
    try:
        if plan.get('operation')=='assemble_draft':
            expected=plan_draft(snapshot,plan['recipient_eid'],plan['new_template'],plan['crew_slots'],plan['draft_steps'],
                addon_mod=plan['addon_mod'],state_profile=plan['state_profile'])
        elif plan.get("operation") == "edit_one":
            expected = plan_edit(snapshot, plan["recipient_eid"], plan["new_template"], plan["crew_slots"],
                                 addon_mod=plan["addon_mod"], state_profile=plan["state_profile"],
                                 keep_names=plan["keep_names"])
        elif plan.get("operation") == "refit_one":
            expected = plan_refit(snapshot, plan["recipient_eid"], plan["new_template"], plan["crew_slots"],
                                  addon_mod=plan["addon_mod"], role=plan["role"])
        else:
            expected = plan_assembly(snapshot, plan["recipient_eid"], plan["donor_eid"],
                                     plan["new_template"], plan["crew_slots"],
                                     addon_mod=plan["addon_mod"], state_profile=plan["state_profile"])
    except (KeyError, TypeError) as exc:
        raise CampaignError("Incomplete migration plan") from exc
    if plan != expected:
        raise CampaignError("Migration plan differs from the validated snapshot; recompute it")
    scene, status = snapshot._scene.clone(), snapshot._status.clone()
    campaign = _one(scene, "campaign", required=True)
    squad_block = _one(campaign, "CampaignSquads", required=True)
    entities, inventories = _entity_nodes(campaign), _inventory_nodes(campaign)
    reid, deid, new_eid = plan["recipient_eid"], plan["donor_eid"], plan["new_eid"]
    consumed = set(plan["consumed_eids"])
    editing = plan.get("operation") == "edit_one"
    if plan.get('operation')=='assemble_draft':
        new=entities[reid].clone()
        for step in plan['draft_steps']:
            new=_edited_state(new,step['profile'],True) if step['kind'] in ('seed','removal','module_edit') else _migrate_state(new,entities.get(step.get('donor_eid')) or block('{Entity "depot" 0x0}'),step['profile'])
        new=_edited_state(new,plan['state_profile'],True)
    elif editing:
        new = _edited_state(entities[reid], plan["state_profile"], plan["keep_names"])
    elif deid is None:
        new = _fresh_state(entities[reid])
    else:
        new = _migrate_state(entities[reid], entities[deid], plan["state_profile"])
    mount = plan['state_profile'].get('commander_mg')
    retired = set(plan['state_profile'].get('retired_inventory_users') or [])
    from .weapon_state import reconcile_weaponry, validate_weaponry
    reconcile_weaponry(new, plan['state_profile'])
    try:
        validate_weaponry(new, plan['state_profile'])
    except ValueError as exc:
        raise CampaignError(str(exc)) from exc
    new.atoms[1].set(plan["new_template"])
    new.atoms[2].set(new_eid)
    # Preserve the recipient's MID, score, hull chassis state and unknown fields.
    campaign.items = [new if item is entities[reid] else item for item in campaign.items
                      if not any(item is entities[eid] for eid in consumed if eid!=reid)]
    for link in campaign.find_all("Link"):
        if _link_target(link)[1] in consumed:
            campaign.remove(link)
    for tags in campaign.find_all("Tags"):
        if (_node_eids(tags) & (consumed-{reid})):
            campaign.remove(tags)
        elif reid in _node_eids(tags):
            for atom in tags.atoms[1:]:
                if _EID.fullmatch(atom.value) and canonical_eid(atom.value) == reid:
                    atom.set(new_eid)
    # Lost detachable parts. The chassis keeps those it still has (minus parts of the
    # replaced module); a consumed donor's and a fresh refit's are gone (repaired).
    keep_lost = editing or deid is not None or plan.get('operation') == 'assemble_draft'
    replaced = set(plan["state_profile"].get("recipient_module_names") or [])
    for step in plan.get('draft_steps') or []:
        replaced |= set((step.get('profile') or {}).get("recipient_module_names") or [])
    for slicer in list(campaign.find_all("Slicer")):
        owners = _node_eids(slicer) & consumed
        if not owners:
            continue
        if keep_lost and owners == {reid}:
            # Only parts the emitted vehicle really has (bone and volume names):
            # an unknown name in a save is never handed to the engine.
            emitted = set(plan["state_profile"].get("allowed_damage_ids") or [])
            for part in list(slicer.children):
                if part.head in replaced or (emitted and part.head not in emitted):
                    slicer.remove(part)
            if slicer.children:
                for atom in slicer.atoms[1:]:
                    if _EID.fullmatch(atom.value) and canonical_eid(atom.value) == reid:
                        atom.set(new_eid)
                continue
        campaign.remove(slicer)
    for hook in list(campaign.find_all("LinkTrailer")):
        if _node_eids(hook) & consumed:
            campaign.remove(hook)
    policy = plan["state_profile"].get("inventory_policy")
    chosen = reid if editing else deid if policy == "donor" else reid if policy in ("recipient","gun_replace") else None
    draft_inventory=None
    if plan.get('operation')=='assemble_draft':
        draft_inventory=_draft_inventory(inventories,reid,plan['draft_steps'],plan['state_profile']);chosen=reid if draft_inventory is not None else None
    has_inventory=chosen in inventories or draft_inventory is not None
    if has_inventory:
        copied = draft_inventory if draft_inventory is not None else inventories[chosen].clone()
        copied.atoms[1].set(new_eid)
        _clean_inventory(copied)
        # A passenger MG removed in this edit leaves its gun as plain cargo.
        rider_slots = {m['slot'] for m in (plan['state_profile'].get('rider_mgs') or {}).get('mounts', [])}
        # A gun bound to a place the new vehicle does not have becomes cargo:
        # the game crashes spawning a vehicle that must mount it (Vidarr's Stuart).
        places = plan['state_profile'].get('weapon_places')
        keep = set(places or []) | {(plan['state_profile'].get('commander_mg') or {}).get('previous_slot')}
        for item in copied.walk('item'):
            for user in list(item.find_all('user')):
                if user.args and (user.args[0] in retired or (re.fullmatch(r'mgun_rider\d+', user.args[0])
                                                              and user.args[0] not in rider_slots)
                                  or (places is not None and user.args[0] not in keep)):
                    item.remove(user)
        campaign.append(copied)
        gun=plan['state_profile'].get('gun_swap')
        if gun and plan.get('operation')!='assemble_draft':
            # An edit must not replenish spent ammo. It may still remove only
            # families explicitly proved unused by the final compiled loadout.
            removals=_retired_ammo(gun) if gun.get('initialize',True) else _retired_ammo({'old_ammo_families':gun.get('old_ammo_families',[])})
            _remove_inventory_ammo(copied,removals)
        if gun and gun.get('initialize',True) and plan.get('operation')!='assemble_draft':
            box=copied.find('box')
            if box is None:box=block('{box}');copied.append(box)
            for text in gun['ammo_items']:box.append(block(text))
    smoke = plan['state_profile'].get('smoke_launchers')
    if smoke and has_inventory and smoke['initialize']:
        smoke_box = copied.find('box')
        if smoke_box is None: smoke_box = block('{box}');copied.append(smoke_box)
        smoke_box.append(block(smoke['initial_item']))
    if mount and has_inventory:
        previous_slot = mount.get('previous_slot')
        if previous_slot and previous_slot != mount['slot']:
            for user in copied.walk('user'):
                if user.args == [previous_slot]:
                    user.atoms[1].set(mount['slot'])
    for rider in ((plan['state_profile'].get('rider_mgs') or {}).get('mounts', []) if has_inventory else []):
        # Same rule as the commander's mount: a slot already holding the chosen
        # gun keeps it with its rounds; anything else there becomes cargo.
        box_node = copied.find('box')
        if box_node is None:
            box_node = block('{box}')
            copied.append(box_node)
        held = [item for item in box_node.find_all('item') if any(user.args == [rider['slot']] for user in item.find_all('user'))]
        if not any(item.args and item.args[0] == rider['item'] for item in held):
            for item in held:
                for user in list(item.find_all('user')):
                    if user.args == [rider['slot']]:
                        item.remove(user)
            box_node.append(block(rider['initial_item']))
            box_node.append(block(rider['ammo_item']))
    if mount and has_inventory and mount.get('initialize', True):
        # An edited unit's persisted inventory overrides its DEF inventory.
        # Keep every unrelated item and only initialize the new/replaced slot.
        box_node = copied.find('box')
        if box_node is None:
            box_node = block('{box}')
            copied.append(box_node)
        mounted = [item for item in box_node.find_all('item')
                   if any(user.args == [mount['slot']] for user in item.find_all('user'))]
        if not any(item.args and item.args[0] == mount['item'] for item in mounted):
            for item in mounted:
                # Changing the initial weapon does not destroy the previous
                # weapon or its loaded ammunition: it becomes ordinary cargo.
                for user in list(item.find_all('user')):
                    if user.args == [mount['slot']]:
                        item.remove(user)
            box_node.append(block(mount['initial_item']))
            box_node.append(block(mount['ammo_item']))
    for eid in consumed:
        if eid in inventories:
            campaign.remove(inventories[eid])
    rows = squad_block.children
    rindex = plan["consumed_squad_indices"][0]
    dropped = {id(rows[index]) for index in plan["consumed_squad_indices"][1:]}
    values = " ".join([new_eid] + plan["crew_eids"] + ["0xffffffff"] * plan["vacant_crew_slots"])
    replacement = block("{" + quote(plan["new_template"]) + ' "" ' + values + "}")
    squad_block.items = [replacement if item is rows[rindex] else item
                         for item in squad_block.items if id(item) not in dropped]
    for reserve in plan["reserve_squads"]:
        squad_block.append(block("{" + quote(reserve["template"]) + ' "" ' + " ".join(reserve["eids"]) + "}"))
    _addon_last(status,plan['addon_mod'])
    replacements = {"campaign.scn": scene.render().encode(snapshot._scene_encoding),
                    "status": status.render().encode(snapshot._status_encoding)}
    # Validate the emitted scene structurally and compare every unrelated entity,
    # every human, and every unrelated inventory by its exact serialized text.
    output = build_save_copy(snapshot, replacements)
    after = _snapshot(output, snapshot.source_path)
    before_nodes = _entity_nodes(_one(snapshot._scene, "campaign", required=True))
    after_nodes = _entity_nodes(_one(after._scene, "campaign", required=True))
    if set(after_nodes) != (set(before_nodes) - consumed) | {new_eid}:
        raise CampaignError("Emitted save does not replace exactly the selected vehicles")
    for eid, node in before_nodes.items():
        if eid not in consumed and after_nodes[eid].render() != node.render():
            raise CampaignError(f"Unrelated entity or veteran {eid} was altered")
    after_inventories = _inventory_nodes(_one(after._scene, "campaign", required=True))
    for eid, node in _inventory_nodes(_one(snapshot._scene, "campaign", required=True)).items():
        if eid not in consumed and (eid not in after_inventories or after_inventories[eid].render() != node.render()):
            raise CampaignError(f"Unrelated inventory {eid} was altered")
    human_members = Counter(e for s in after.squads for e in s["human_eids"])
    for eid in plan["crew_eids"] + [e for s in plan["reserve_squads"] for e in s["eids"]]:
        if human_members[eid] != 1:
            raise CampaignError(f"Veteran {eid} was duplicated or omitted from squads")
    if _node_eids(_one(after._scene, "campaign", required=True)) & consumed:
        raise CampaignError("References to consumed vehicles remain in the emitted scene")
    if canonical_eid(new_eid) in {canonical_eid(e) for e in _inventory_problems(_one(after._scene, "campaign", required=True))}:
        raise CampaignError("The new vehicle's inventory is not written the way the game reads it")
    if not {m.casefold() for m in snapshot.mods} <= {m.casefold() for m in after.mods} or plan["addon_mod"] not in after.mods:
        raise CampaignError("Existing mod requirements were not preserved")
    return replacements


def _restore_header_fields(data: bytes, source_infos: list) -> bytes:
    """Copy general-purpose flags and external attributes from the game's ZIP.

    Python's writer resets flag bits and substitutes 0o600 permissions for a
    zero attribute; the copy should be indistinguishable from a game save.
    """
    result = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as written:
        infos = {info.filename: info for info in written.infolist()}
    end = data.rfind(b"PK\x05\x06")
    if end < 0:
        raise CampaignError("Output ZIP has no central directory")
    entries, offset = struct.unpack_from("<HI", data, end + 10)[0], struct.unpack_from("<I", data, end + 16)[0]
    for _ in range(entries):
        if data[offset:offset+4] != b"PK\x01\x02":
            raise CampaignError("Malformed output ZIP central directory")
        name_length, extra_length, comment_length = struct.unpack_from("<HHH", data, offset + 28)
        name = data[offset+46:offset+46+name_length].decode("utf-8" if struct.unpack_from("<H", data, offset+8)[0] & 0x800 else "cp437")
        source = next((info for info in source_infos if info.filename == name), None)
        if source is not None:
            flags = source.flag_bits & 0x6  # compression-option bits only; never encryption/descriptors
            local = infos[name].header_offset
            for position in (offset + 8, local + 6):
                current = struct.unpack_from("<H", result, position)[0]
                struct.pack_into("<H", result, position, (current & ~0x6) | flags)
            struct.pack_into("<I", result, offset + 38, source.external_attr)
        offset += 46 + name_length + extra_length + comment_length
    return bytes(result)


def repair_weaponry_state(snapshot: CampaignSnapshot, contracts: dict[str, list[str]]):
    """Repair stale weapon records against current definitions, without a refit.

    Callers resolve contracts from the effective resource stack. Reset policies
    from historical recipes are intentionally not replayed: compatible loaded
    ammunition is preserved. No IDs, squads, humans or inventory are changed.
    """
    from .weapon_state import reconcile_weaponry, validate_weaponry
    for slots in contracts.values():
        _profile({'weapon_places': slots})
    scene = snapshot._scene.clone()
    changed = []
    for entity in _one(scene, 'campaign', required=True).find_all('Entity'):
        if not entity.args or entity.args[0] not in contracts:
            continue
        before = entity.render()
        profile = {'weapon_places': contracts[entity.args[0]]}
        reconcile_weaponry(entity, profile)
        validate_weaponry(entity, profile)
        if entity.render() != before:
            changed.append({'template': entity.args[0], 'eid': entity.args[1]})
    replacements = {'campaign.scn': scene.render().encode(snapshot._scene_encoding)}
    _snapshot(build_save_copy(snapshot, replacements), snapshot.source_path)
    return replacements, changed


def build_save_copy(snapshot: CampaignSnapshot, replacements: dict[str, bytes]) -> bytes:
    """Build a ZIP in memory, retaining all original entries and ZIP metadata."""
    if set(replacements) - {"campaign.scn", "status"}:
        raise CampaignError("Only campaign.scn and status may be replaced by this operation")
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(snapshot._zip_bytes)) as source, zipfile.ZipFile(output, "w") as target:
        target.comment = source.comment
        for info in source.infolist():
            if info.file_size > MAX_MEMBER_BYTES:
                raise CampaignError(f"ZIP member {info.filename} exceeds supported size")
            payload = replacements.get(info.filename)
            if payload is None:
                payload = source.read(info)
            target.writestr(info, payload, compresslevel=9 if info.flag_bits & 0x6 == 0x2 else None)
    data = _restore_header_fields(output.getvalue(), source_infos=zipfile.ZipFile(io.BytesIO(snapshot._zip_bytes)).infolist())
    with zipfile.ZipFile(io.BytesIO(data)) as check:
        bad = check.testzip()
        if bad:
            raise CampaignError(f"Output ZIP failed CRC validation: {bad}")
    return data
