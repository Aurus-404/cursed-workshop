"""Reopen authored fittings inherited through an owned vehicle's build history.

Replay uses the existing compiler in private overlays. The installed addon and
the campaign are never changed here.
"""
import copy
import hashlib
import json
import re
import tempfile
from pathlib import Path

FIELDS = ('commander_mg', 'smoke_launchers', 'extra_armor')


def fitting_parents(transform, commander):
    """Resolve authored attachment sockets after recreating a fitted mount.

    Generated bone names may change when older fittings are stripped. Bind by
    the mount's native socket instead, unless the user chose a different parent.
    """
    values = copy.deepcopy(transform)
    for field in ('extra_armor', 'smoke_launchers', 'rider_mgs'):
        rows = values.get(field)
        if not isinstance(rows, list):continue
        for row in rows:
            if not isinstance(row, dict):continue
            ref = row.get('parent_ref')
            if not isinstance(ref, dict) or ref.get('name') != row.get('parent'):
                continue
            target = (commander or {}).get('bones', {}).get(ref['bone']) if ref.get('kind') == 'commander_mg' and isinstance(ref.get('bone'), str) else None
            if not target:
                raise ValueError('El punto de fijación del accesorio ya no existe en este vehículo.')
            row['parent'] = target
    return values


def reopen(metadata, recipes, model):
    transform = copy.deepcopy(metadata.get('transform') or {})
    source = transform.get('source_recipe')
    if source and set(FIELDS) <= set(source.get('deferred_fittings', [])):
        return transform, metadata.get('commander_mg'), metadata.get('smoke_launchers')
    bones = set(model['bones'])
    smoke, seen, armor, armor_seen, commander, commander_contract = [], set(), [], set(), None, None
    inherited = False
    current, visited = metadata, set()
    while current:
        own = current is metadata
        tr = current.get('transform') or {}
        contract = current.get('commander_mg') or {}
        names = set((contract.get('bones') or {}).values())
        if commander is None and names and names <= bones and tr.get('commander_mg'):
            commander = copy.deepcopy(tr['commander_mg'])
            commander_contract = copy.deepcopy(contract)
            inherited |= not own
        for mount in (current.get('smoke_launchers') or {}).get('mounts', []):
            root = mount.get('root')
            if root not in bones or root in seen:
                continue
            seen.add(root)
            smoke.append({k:copy.deepcopy(v) for k,v in mount.items()
                          if k in ('tubes', 'parent', 'position', 'rotation', 'pivot')})
            inherited |= not own
        for mount in current.get('extra_armor') or []:
            names = mount.get('bones') or []
            if not names or names[0] in armor_seen or not set(names) <= bones:
                continue
            armor_seen.add(names[0])
            row = {k:copy.deepcopy(v) for k,v in mount.items()
                   if k in ('asset', 'parent', 'position', 'rotation', 'pivot')}
            serial = re.fullmatch(r'mw_armor_(\d+)_root', names[0])
            if serial:row['serial'] = int(serial[1])
            armor.append(row)
            inherited |= not own
        parent = current.get('recipient')
        if isinstance(parent, dict):parent = parent.get('id')
        if parent in visited:raise ValueError('Cyclic chassis assembly recipe: ' + parent)
        visited.add(parent)
        current = recipes.get(parent)
    if not inherited and not source:
        return transform, metadata.get('commander_mg'), metadata.get('smoke_launchers')

    def strip(tr):
        for key in FIELDS:tr.pop(key,None)
        seed = (tr.get('assembly_draft') or {}).get('seed')
        if seed:strip(seed['transform'])
        existing = tr.get('source_recipe')
        if existing:
            ancestor = existing
            while ancestor:
                strip(ancestor['transform']);ancestor=ancestor.get('parent')

    def record(template, seen):
        if template not in recipes:return None
        if template in seen:raise ValueError('Cyclic chassis assembly recipe: ' + template)
        entry = recipes[template]
        recipient, donor = entry['recipient'], entry['donor']
        recipient = recipient.get('id') if isinstance(recipient,dict) else recipient
        donor = donor.get('id') if isinstance(donor,dict) else donor
        core = copy.deepcopy(entry.get('transform') or {})
        strip(core)
        return {'template':template,'recipient':recipient,'donor':donor,'transform':core,
                'parent':record(recipient, seen | {template})}

    parent = metadata['recipient']
    parent = parent.get('id') if isinstance(parent,dict) else parent
    if source:
        # Upgrade recipes saved before armor joined the deferred fittings.
        ancestor = source
        while ancestor:
            strip(ancestor['transform']);ancestor=ancestor.get('parent')
        transform['source_recipe'] = source
    else:transform['source_recipe'] = record(parent,set())
    if transform['source_recipe'] is not None:
        transform['source_recipe']['deferred_fittings'] = list(FIELDS)
    if commander:transform['commander_mg'] = commander
    if smoke:transform['smoke_launchers'] = smoke
    if armor:transform['extra_armor'] = armor
    # Recreated commander mounts allocate names from the rebuilt skeleton,
    # which may differ from the installed vehicle. Keep native socket identity.
    sockets = {name: native for native,name in (commander_contract or {}).get('bones', {}).items()}
    for row in [*smoke, *armor]:
        if row.get('parent') in sockets:
            row['parent_ref'] = {'kind':'commander_mg', 'bone':sockets[row['parent']], 'name':row['parent']}
    # Original fitted slot/charges are needed by the campaign inventory policy.
    return transform, commander_contract, {'mounts':smoke} if smoke else None


def identity(record):
    return hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()


def compile_reopened(resources, recipient, donor, transform, output, identifier):
    from .compiler import compile_assembly
    full_transform = copy.deepcopy(transform)
    cache=resources.__dict__.setdefault('_reopened_sources',{})
    key=identity([transform['source_recipe'],resources.dlc_availability().key()])
    cached=cache.get(key)
    views, hashes, aliases = [], {}, {}
    def remap(value):
        if isinstance(value,list):return [remap(v) for v in value]
        if not isinstance(value,dict):return value
        return {k:aliases.get(v,v) if k in ('recipient','donor','donor_template') and isinstance(v,str)
                else remap(v) for k,v in value.items()}
    def remember(result):
        hashes.update(result['metadata']['source_hashes'])
    if cached is None:
        temporary=tempfile.TemporaryDirectory(prefix='cw_reopen_')
        view = resources
        def replay(record, seen):
            nonlocal view
            template = record['template']
            if template in seen:raise ValueError('Cyclic chassis assembly recipe: ' + template)
            if record.get('parent'):replay(record['parent'], seen | {template})
            serial = len(views)
            result = compile_assembly(view,aliases.get(record['recipient'],record['recipient']),
                aliases.get(record['donor'],record['donor']),remap(record['transform']),
                Path(temporary.name)/str(serial),'mw_reopen_'+str(serial))
            remember(result)
            view = resources.with_overlay(Path(result['output_dir'])) if not views else view.with_overlay(Path(result['output_dir']))
            views.append(view);aliases[template]=result['id']
        try:
            replay(transform['source_recipe'],set())
            cached={'view':view,'views':views,'hashes':hashes,'aliases':aliases,'temp':temporary}
            cache[key]=cached
        except BaseException:
            for view in reversed(views):view.close()
            temporary.cleanup();raise
    view=cached['view'];aliases=cached['aliases'];hashes=dict(cached['hashes'])
    core=copy.deepcopy(transform);core.pop('source_recipe')
    result=compile_assembly(view,aliases.get(recipient,recipient),aliases.get(donor,donor),remap(core),output,identifier)
    remember(result)
    metadata=result['metadata']
    metadata.update(recipient=recipient,donor=donor,transform=full_transform,
        source_hashes={p:h for p,h in hashes.items() if '/workshop/mw_reopen_' not in p and '/workshop/mw_draft_' not in p})
    profile=metadata['state_profile']
    for key,value in (('hull_authority',recipient),('turret_authority',donor)):
        if key in profile:profile[key]=value
    (Path(output)/('assembly_'+identifier+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
