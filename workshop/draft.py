"""A persistent vehicle design composed through the existing compilers.

No campaign mutation here. Every consumed unit is resolved again by AppState;
temporary models are private overlays and never become recipe dependencies.
"""
import copy
import hashlib
import json
import tempfile
import shutil
from pathlib import Path

from .syntax import parse
from .gun_assembly import GunError, gun_changes, selected



def design_turrets(draft):
    """The design's saved turret changes, one per chassis mount. Designs from
    before several turrets kept a single 'turret' row."""
    if not isinstance(draft,dict):return []
    rows=draft.get('turrets')
    if rows is None:rows=[draft['turret']] if draft.get('turret') else []
    return rows if isinstance(rows,list) else []


EXTRAS=('commander_mg','extra_armor','smoke_launchers','towing','charge','rider_mgs','passenger_seats','crew_hidden','crew_seats','crew_exclude','crew_static','crew_fixed','joint_limits')


def weapon_identity(transform):
    """Ignore placement/appearance, including nested starting recipes."""
    rows=[*(transform.get('gun_swaps') or []),*([transform['gun_swap']] if transform.get('gun_swap') else [])]
    draft=transform.get('assembly_draft') or {};rows+=draft.get('guns') or []
    identity=sorted(('gun',r.get('recipient_place',''),r.get('donor_place',''),r.get('donor_template','')) for r in rows)
    for turret in design_turrets(draft):
        if turret.get('native'):continue
        identity.append(('turret',turret.get('donor_template',''),turret.get('recipient_mount',''),turret.get('donor_mount',''),turret.get('assembly_scope','')))
    for row in draft.get('removals') or []:
        identity.append(('remove',row['mount']))
    if transform.get('remove_module'):identity.append(('remove',transform['remove_module']['mount']))
    if transform.get('assembly_scope'):identity.append(('assembly_scope',transform['assembly_scope']))
    seed=draft.get('seed')
    if seed:identity.append(('seed',seed['recipient'],seed['donor'],weapon_identity(seed['transform'])))
    if transform.get('source_recipe'):
        from .editable_recipe import identity as source_identity
        identity.append(('source_recipe',source_identity(transform['source_recipe'])))
    return identity


def recipe_dependencies(metadata):
    """Every generated parent needed by a saved design, including later donors."""
    result=set()
    def add(value):
        if isinstance(value,dict):value=value.get('id')
        if isinstance(value,str) and value:result.add(value)
    def transform(tr):
        draft=tr.get('assembly_draft') or {}
        for row in [*(tr.get('gun_swaps') or []),*(draft.get('guns') or []),*design_turrets(draft)]:add(row.get('donor_template'))
        seed=draft.get('seed')
        if seed:add(seed.get('recipient'));add(seed.get('donor'));transform(seed.get('transform') or {})
    add(metadata.get('recipient'));add(metadata.get('donor'));transform(metadata.get('transform') or {})
    return result


def specification(transform):
    draft=copy.deepcopy(transform.get('assembly_draft'))
    if not isinstance(draft,dict) or draft.get('schema')!=1:
        raise GunError('El diseño del vehículo no es válido.')
    guns=draft.get('guns',[])
    if guns:gun_changes({'gun_swaps':guns})
    if not isinstance(guns,list) or len(guns)>8:raise GunError('El diseño del vehículo no es válido.')
    turrets=design_turrets(draft)
    if not isinstance(draft.get('turrets',[]),list):raise GunError('El diseño del vehículo no es válido.')
    for row in [*guns,*turrets]:
        if row in turrets and isinstance(row,dict) and row.get('native') is True:
            if row.get('donor_eid') or row.get('donor_depot') or row.get('donor_catalog') or row.get('assembly_scope'):
                raise GunError('Editar un módulo instalado no consume un donante.')
            continue
        if not isinstance(row,dict) or not isinstance(row.get('donor_template'),str) or not row['donor_template']:
            raise GunError('Elegí un donante para cada cambio guardado.')
        if len(row['donor_template'])>128:raise GunError('El diseño del vehículo no es válido.')
    mounts=[row.get('recipient_mount') or 'turret' for row in turrets]
    if any(not isinstance(m,str) or len(m)>160 for m in mounts) or len(set(mounts))!=len(mounts):
        raise GunError('Cada torreta del chasis se puede cambiar una sola vez en el diseño.')
    for turret in turrets:
        gun_changes({'gun_swaps':[{'recipient_place':'turret','donor_place':'turret','pose':turret.get('pose')} ]})
        if turret.get('assembly_scope') not in (None, 'complete'):
            raise GunError('El alcance del conjunto de armamento no es válido.')
        if turret.get('assembly_scope')=='complete':
            if turret.get('donor_depot'):
                raise GunError('El conjunto completo requiere un donante entero de la campaña.')
            if turret.get('donor_catalog'):continue  # the catalog never runs out
            source=lambda r:r.get('donor_eid') or r.get('donor_depot') or r.get('donor_template')
            if sum(source(r)==source(turret) for r in [*turrets,*guns])!=1:
                raise GunError('Un donante completo solo se puede montar una vez en el diseño.')
    from .modular_assembly import removal_mount
    removals=draft.get('removals',[])
    if not isinstance(removals,list) or len(removals)>16:
        raise GunError('La lista de torretas retiradas no es válida.')
    removed=[removal_mount({'schema':1,**r}) if isinstance(r,dict) else removal_mount(None) for r in removals]
    if len(removed)!=len(set(removed)):
        raise GunError('Cada torreta se puede retirar o reemplazar una sola vez en el diseño.')
    draft['turrets']=turrets;draft.pop('turret',None)
    return draft


def compile_draft(resources,recipient,donor,transform,output,identifier):
    from .compiler import compile_assembly
    from .gun_assembly import compile_guns
    draft=specification(transform);output=Path(output);steps=[];hashes={};views=[];view=resources;current=recipient;seed_camera={};removals=[];assemblies=[]
    if draft.get('removals'):
        return _compile_removed_design(resources,recipient,donor,transform,output,identifier,draft)
    extras={k:copy.deepcopy(transform[k]) for k in EXTRAS if k in transform}
    def remember(result,kind,source=None):
        metadata=result['metadata']
        hashes.update({p:h for p,h in metadata['source_hashes'].items() if '/workshop/mw_draft_' not in p})
        root=parse((Path(result['output_dir'])/'resource'/result['definition_path']).read_bytes()).find('game_entity')
        boxes=[n.render() for inv in root.find_all('extender') if inv.args==['inventory'] for n in inv.find_all('box')]
        steps.append({'kind':kind,'donor_template':(source or {}).get('donor_template',recipient),
            'donor_eid':(source or {}).get('donor_eid'),'donor_depot':(source or {}).get('donor_depot'),'donor_catalog':bool((source or {}).get('donor_catalog')),'profile':copy.deepcopy(metadata['state_profile']),
            'inventory_boxes':boxes})
    def overlay(result):
        nonlocal view,current
        view=resources.with_overlay(Path(result['output_dir']));views.append(view);current=result['id']
    hull_pose=copy.deepcopy(transform.get('hull') or {})
    from .pivots import pivot_of
    pivot_of(hull_pose)
    move_hull=any(hull_pose.get('position',[]))+any(hull_pose.get('rotation',[]))
    with tempfile.TemporaryDirectory(prefix='cw_vehicle_design_') as temporary:
        temp=Path(temporary)
        try:
            seed=draft.get('seed')
            if seed:
                core={k:v for k,v in seed['transform'].items() if k not in ('commander_mg','extra_armor','smoke_launchers','rider_mgs','passenger_seats','crew_hidden')}
                initial=compile_assembly(view,seed['recipient'],seed['donor'],core,temp/'seed','mw_draft_seed')
                seed_camera={k:copy.deepcopy(initial['metadata'][k]) for k in
                    ('gun_camera_donor','camera_bone_mapping','bone_mapping','bone_map') if k in initial['metadata']}
                remember(initial,'seed');overlay(initial)
            for number,row in enumerate(draft.get('removals') or []):
                initial=compile_assembly(view,current,current,{'remove_module':{'schema':1,**row}},
                    temp/f'remove{number}',f'mw_draft_remove{number}')
                removed=copy.deepcopy(initial['metadata']['removed_module'])
                removed['template']=current;removals.append(removed)
                remember(initial,'removal');overlay(initial)
            # Each saved turret replaces one chassis mount, in order, on the
            # vehicle the previous ones left: a Nb.Fz can take three donors.
            turrets=draft.get('turrets') or [];main=None
            module_roots={}
            limits=extras.get('joint_limits') or {}
            if turrets and 'joint_limits' in extras:extras['joint_limits']={k:v for k,v in limits.items() if k!='turret'}
            for number,turret in enumerate(turrets):
                native=turret.get('native') is True
                if native and turret.get('donor_template') not in (None,recipient):
                    raise GunError('Editar un módulo instalado no consume un donante.')
                tr={'turret':turret.get('pose') or {},'recipient_mount':turret.get('recipient_mount'),
                    'donor_mount':turret.get('recipient_mount') if native else turret.get('donor_mount'),'crew_static':transform.get('crew_static',True)}
                if native:tr['auto_seat']=False
                if turret.get('assembly_scope'):tr['assembly_scope']=turret['assembly_scope']
                # Limits set on a design turret name the donor's own joints:
                # the turret step knows how they are renamed on the vehicle.
                own=turret.get('joint_limits') or (limits.get('turret') if number==0 else None)
                if own:tr['joint_limits']={'turret':own}
                if native:
                    tr={'commander_only':True,'module_poses':{turret['recipient_mount']:turret.get('pose') or {}}}
                    if own:tr['joint_limits']={'hull':own}
                initial=compile_assembly(view,current,current if native else turret['donor_template'],tr,temp/f'turret{number}',f'mw_draft_turret{number}')
                if native:
                    initial['metadata']['state_profile'].update(weaponry_policy='recipient',inventory_policy='recipient')
                module_roots[turret.get('recipient_mount') or 'turret']=turret['recipient_mount'] if native else initial['metadata']['bone_mapping'].get(tr['donor_mount'],initial['metadata']['donor_mount'])
                remember(initial,'module_edit' if native else 'turret',turret);overlay(initial)
                if initial['metadata'].get('weapon_assembly'):assemblies.append(initial['metadata']['weapon_assembly'])
                if not native and (main is None or (turret.get('recipient_mount') or 'turret')=='turret'):main=(turret,initial)
            # All gun placements refer to the structural receiver after its
            # saved turret, never to another gun's newly moved elevation pivot.
            frames={}
            from .gun_assembly import discover
            for row in discover(view,current)['weapons']:
                if row['available']:
                    _,_,_,model,_,spec=selected(view,current,row['place'])
                    frames[row['place']]=model['bones'][spec['pivot']]['world']
            guns=draft.get('guns') or [];assembled=temp/'assembled' if move_hull else output
            if guns:
                result=compile_guns(view,current,guns[0]['donor_template'],{**extras,'_design_crew':True,'gun_swaps':guns},assembled,identifier,stage_profiles=True)
                for row,entry in zip(guns,result['metadata']['gun_profiles']):
                    steps.append({'kind':'gun','donor_template':entry['donor_template'],'donor_eid':row.get('donor_eid'),'donor_catalog':bool(row.get('donor_catalog')),
                        'profile':entry['profile'],'inventory_boxes':[]})
            else:
                result=compile_assembly(view,current,current,{**extras,'commander_only':True},assembled,identifier)
            if move_hull:
                original_metadata=copy.deepcopy(result['metadata']);overlay(result)
                placed=compile_assembly(view,current,current,{'commander_only':True,'hull':hull_pose},output,identifier)
                final_metadata=placed['metadata']
                placed['metadata']={**original_metadata,**{k:final_metadata[k] for k in ('file_hashes','validation')}}
                placed['diagnostics']=[*result['diagnostics'],*placed['diagnostics']];result=placed
            metadata=result['metadata'];hashes.update({p:h for p,h in metadata['source_hashes'].items() if '/workshop/mw_draft_' not in p})
            metadata.update(recipient=recipient,donor=donor,transform=copy.deepcopy(transform),draft_steps=steps,
                draft_frames=frames,source_hashes=hashes,draft_module_roots=module_roots)
            if removals:
                # No transient overlay may become a depot/recipe dependency.
                for row in removals:row['template']=recipient
                metadata['removed_modules']=removals
                final_places=set(metadata['state_profile'].get('weapon_places',[]))
                retired=set(metadata['state_profile'].get('retired_inventory_users',[]))
                retired.update(p for row in removals for p in row['removed_places'])
                metadata['state_profile']['retired_inventory_users']=sorted(retired-final_places)
            if assemblies:metadata['weapon_assemblies']=assemblies
            metadata['state_profile'].update(hull_authority=recipient,turret_authority=donor)
            if guns:metadata['operation']='gun_swap'
            if main:metadata['draft_turret_donor']=main[0].get('donor_template') or recipient
            if main and not guns:metadata['bone_mapping']=main[1]['metadata'].get('bone_mapping') or main[1]['metadata'].get('bone_map') or {}
            if main and not guns:metadata['camera_bone_mapping']=main[1]['metadata'].get('camera_bone_mapping',{})
            # Editing fittings does not replace the seed's aiming weapon. Its
            # native camera donor and exported optical sockets remain authoritative.
            if seed and not guns and main is None:metadata.update(seed_camera)
            (output/f'assembly_{identifier}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
            return result
        finally:
            for item in reversed(views):item.close()


def _compile_removed_design(resources,recipient,donor,transform,output,identifier,draft):
    """Keep an immutable, reproducible source for the removed equipped modules.

    Finish the current design before cutting it. The depot must never refer to
    the unmodified receiver when its turret already has other installed guns.
    The source entity has no roster entry: it is a part dependency, not another
    campaign vehicle. All files travel in the same transaction as the result.
    """
    from .compiler import compile_assembly
    from .geometry import parse_model
    core=copy.deepcopy(transform);core['assembly_draft'].pop('removals',None)
    # New deck mounts are fitted after the removed turret has freed its crew
    # roles. They are not part of the turret being put in the depot.
    deferred=[r for r in design_turrets(core['assembly_draft']) if (r.get('recipient_mount') or '').startswith('@free')]
    core['assembly_draft']['turrets']=[r for r in design_turrets(core['assembly_draft']) if r not in deferred]
    views=[]
    with tempfile.TemporaryDirectory(prefix='cw_removed_design_') as temporary:
        temp=Path(temporary);snapshot=compile_draft(resources,recipient,donor,core,temp/'parts',identifier+'_parts')
        view=resources.with_overlay(temp/'parts');views.append(view);current=snapshot['id']
        removed=[];steps=copy.deepcopy(snapshot['metadata']['draft_steps'])
        try:
            for number,row in enumerate(draft['removals']):
                mount=snapshot['metadata'].get('draft_module_roots',{}).get(row['mount'],row['mount'])
                last=number==len(draft['removals'])-1;target=output if last and not deferred else temp/str(number)
                result=compile_assembly(view,current,current,{'remove_module':{'schema':1,'mount':mount}},target,
                    identifier if last and not deferred else 'mw_draft_remove'+str(number))
                contract=copy.deepcopy(result['metadata']['removed_module'])
                contract.update(template=snapshot['id'],source_mount=mount,design_mount=row['mount'])
                removed.append(contract)
                steps.append({'kind':'removal','donor_template':recipient,'donor_eid':None,
                    'profile':copy.deepcopy(result['metadata']['state_profile']),'inventory_boxes':[]})
                if not last or deferred:
                    view=view.with_overlay(target);views.append(view);current=result['id']
            if deferred:
                result=compile_draft(view,current,donor,{'assembly_draft':{'schema':1,'turrets':deferred,'guns':[]}},output,identifier)
                steps.extend(result['metadata']['draft_steps'])
            metadata=result['metadata'];base=snapshot['metadata'];final_profile=metadata['state_profile']
            retired={p for row in removed for p in row['removed_places']}
            profile=copy.deepcopy(base['state_profile']);profile.update(final_profile)
            profile['retired_inventory_users']=sorted(retired|set(final_profile.get('retired_inventory_users',[])))
            final=parse_model((output/'resource'/result['model_path']).read_bytes());bones=set(final['bones']);slots=set(profile['weapon_places'])
            for field in ('commander_mg','smoke_launchers','rider_mgs'):
                contract=copy.deepcopy(base.get(field))
                if not contract:continue
                if field=='commander_mg':
                    if contract.get('slot') not in slots:profile.pop(field,None);continue
                elif contract.get('mounts') is not None:
                    contract['mounts']=[m for m in contract['mounts'] if m.get('root') in bones or m.get('slot') in slots]
                    if not contract['mounts']:profile.pop(field,None);continue
                metadata[field]=contract
                if field in profile:profile[field]=contract
            metadata.update(recipient=recipient,donor=donor,transform=copy.deepcopy(transform),state_profile=profile,
                draft_steps=steps,draft_frames=base.get('draft_frames',{}),removed_modules=removed,
                draft_module_roots=base.get('draft_module_roots',{}),source_hashes={**base['source_hashes'],
                    **{p:h for p,h in metadata['source_hashes'].items() if '/workshop/mw_draft_' not in p and '/workshop/'+snapshot['id']+'/' not in p}})
            metadata['state_profile'].update(hull_authority=recipient,turret_authority=donor)
            metadata['extra_armor']=[m for m in base.get('extra_armor') or [] if set(m.get('bones',[]))<=bones]
            metadata['operation']='remove_module'
            for path in (temp/'parts').rglob('*'):
                if path.is_file():
                    dest=output/path.relative_to(temp/'parts');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
            metadata['depot_sources']=[snapshot['id']]
            (output/f'assembly_{identifier}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
            return result
        finally:
            for view in reversed(views):view.close()
