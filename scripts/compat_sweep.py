"""Broad compatibility sweep: compile + register many chassis/module pairs.

Samples vehicles from every nation and family folder of the active stack
(vanilla, DLCs, mods such as MACE), builds random pairs into throwaway folders
and writes one JSON line per pair. Nothing touches saves or the installed addon.

    python scripts/compat_sweep.py SETTINGS.json OUT.jsonl [--pairs 400] [--seed 7]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tempfile
import time
import traceback
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop.compiler import compile_assembly
from workshop.geometry import is_carried_weapon, parse_model
from workshop.registration import register_variant
from workshop.resources import ResourceIndex

SKIP = re.compile(r"/(airborne|marine|placeholder|test|train|railway|-?static)/|_(x|xx|ai_x|ai_xx)$", re.IGNORECASE)


def conquest_names(index):
    """Every name the multiplayer/Conquest unit files mention: own entries and
    vehicles sold inside squads (towed_gun_doctrine vehicle(kettenkrad))."""
    names = set()
    for key, entry in index.entries.items():
        if key.startswith("set/multiplayer/units/") and key.endswith((".set", ".inc")):
            try:
                names |= set(re.findall(r"[A-Za-z0-9_]+", index.read_text(entry.path).casefold()))
            except Exception:
                pass
    return names


def candidates(index):
    groups = defaultdict(list)
    for unit in index.list_units(limit=None):
        try:
            info = index.resolve_entity(unit["id"])
        except Exception:
            continue
        path = info["definition_path"]
        if not path.startswith("entity/-vehicle/") or SKIP.search(path.rsplit(".", 1)[0]) or not info.get("model_path"):
            continue
        parts = path.split("/")
        nation, family = parts[2], parts[3] if len(parts) > 4 else "?"
        groups[(nation, family)].append((unit["id"], info))
    return groups


def audit(index, hull, donor, result):
    """Bone references the generated vehicle breaks that its sources did not."""
    from workshop.compiler import _load_entity, _crew_expansion, _expand_place_macros
    from workshop.integrity import introduced_dangling

    def written_out(root):
        # Seats declared by macros (("gunner_open" ... bone(gunner2))) count too.
        _crew_expansion(index, root)
        _expand_place_macros(index, root, lambda nodes, places: True)
        return root
    from workshop.syntax import entity_root, parse
    folder = Path(result["output_dir"]) / "resource" / "entity" / "-vehicle" / "workshop" / result["id"]
    root = written_out(entity_root(parse((folder / (result["id"] + ".def")).read_bytes())))
    model = parse_model((folder / (result["id"] + ".mdl")).read_bytes())
    sources = []
    for template in {hull, donor}:
        info, _, source_root = _load_entity(index, template)
        sources.append((written_out(source_root), parse_model(index.read(info["model_path"]))))
    return introduced_dangling((root, model), sources, result["metadata"].get("bone_mapping"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("settings"); parser.add_argument("out")
    parser.add_argument("--pairs", type=int, default=400); parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--shard", default="0/1", help="i/n: run every n-th pair starting at i (parallel runs)")
    parser.add_argument("--edit", action="store_true", help="Editar: each chassis with a turret rebuilt from itself")
    parser.add_argument("--audit", action="store_true", help="Record bone references the build breaks (crash risk)")
    args = parser.parse_args()
    settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    roots = [root for root in settings["mod_roots"] if "modular_workshop" not in root]
    index = ResourceIndex(settings["game_root"], roots).build()
    rng = random.Random(args.seed)
    groups = candidates(index)
    owned = conquest_names(index)
    hulls, modules = [], []
    for key, items in sorted(groups.items()):
        rng.shuffle(items)
        for template, info in items[:4]:
            carried = is_carried_weapon(info)
            if carried:
                modules.append((template, key))
            elif template.casefold() in owned:  # only vehicles a campaign can hold
                hulls.append((template, key))
            if not carried:
                try:
                    if parse_model(index.read(info["model_path"]))["anchor_name"]:
                        modules.append((template, key))
                except Exception:
                    pass
    print(f"{len(groups)} groups, {len(hulls)} chassis, {len(modules)} modules", flush=True)
    pairs = set()
    if args.edit:
        turreted = [hull for hull in hulls if any(module[0] == hull[0] for module in modules)]
        rng.shuffle(turreted)
        pairs = {(hull, hull) for hull in turreted[:args.pairs]}
    while not args.edit and len(pairs) < min(args.pairs, len(hulls)*len(modules)):
        hull, module = rng.choice(hulls), rng.choice(modules)
        if hull[0] != module[0]:
            pairs.add((hull, module))
    with open(args.out, "w", encoding="utf-8") as out:
        shard, shards = (int(value) for value in args.shard.split("/"))
        for number, ((hull, hull_group), (donor, donor_group)) in enumerate(sorted(pairs)):
            if number % shards != shard:
                continue
            started, row = time.time(), {"hull": hull, "donor": donor, "hull_group": hull_group, "donor_group": donor_group}
            with tempfile.TemporaryDirectory(prefix="mw_sweep_") as temp:
                stage = "compile"
                try:
                    result = compile_assembly(index, hull, donor, {}, Path(temp) / "c", "mw_sweep")
                    if args.audit:
                        row["introduced_dangling"] = sorted(map(list, audit(index, hull, donor, result)))
                    stage = "register"
                    result["campaign_plan"] = {}
                    register_variant(index, result, Path(temp) / "modular_workshop", "mw_sweep", "sweep")
                    row.update(ok=True, crew=result["metadata"].get("crew_roles"),
                               mount=result["metadata"].get("recipient_mount"))
                except Exception as error:
                    row.update(ok=False, stage=stage, error=f"{type(error).__name__}: {error}",
                               where=traceback.extract_tb(error.__traceback__)[-1].name)
            row["seconds"] = round(time.time()-started, 1)
            out.write(json.dumps(row, ensure_ascii=False) + "\n"); out.flush()
            if number % 25 == 0:
                print(number, flush=True)
    index.close()


if __name__ == "__main__":
    main()
