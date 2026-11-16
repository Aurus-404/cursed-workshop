"""Read-only component plans and private compilation of reusable vehicle shells."""
import hashlib
import json
import re
from pathlib import Path

from .components import describe,strip_guns


def provenance(resources,template,part,mount=None,place=None,seen=()):
    if template in seen or len(seen)>32:return {}
    try:info=resources.resolve_entity(template)
    except (KeyError,OSError,ValueError):return {}
    match=re.match(r'entity/-vehicle/\+?([^/]+)/',info['definition_path'])
    if match and match.group(1)!='workshop':
        source=next((s for s in resources.sources if s['id']==info.get('source_id')), {})
        return {'nation':match.group(1),'origin':source.get('name') or info.get('source_id') or '',
                'origin_template':template,'definition_path':info['definition_path']}
    metadata=None
    for source in reversed(resources.sources):
        try:
            value=json.loads((Path(source['root'])/('assembly_'+template+'.json')).read_text(encoding='utf8'))
            metadata=value.get('metadata',value);break
        except (OSError,ValueError):continue
    if not metadata:return {}
    transform=metadata.get('transform') or {};draft=transform.get('assembly_draft') or {}
    if part=='gun':
        for gun in [*(draft.get('guns') or []),*(transform.get('gun_swaps') or [])]:
            if gun.get('recipient_place')==place:
                return provenance(resources,gun['donor_template'],'gun',place=gun.get('donor_place'),seen=(*seen,template))
        changes=[*(metadata.get('gun_assemblies') or []),*([metadata['gun_assembly']] if metadata.get('gun_assembly') else [])]
        for gun in changes:
            if (gun.get('recipient') or {}).get('place')==place:
                return provenance(resources,metadata['donor'],'gun',place=(gun.get('donor') or {}).get('place'),seen=(*seen,template))
    if part!='hull':
        roots=metadata.get('draft_module_roots') or {}
        for turret in draft.get('turrets') or []:
            recipient=turret.get('recipient_mount') or 'turret'
            if not turret.get('native') and roots.get(recipient,recipient)==mount:
                return provenance(resources,turret['donor_template'],'turret',mount=turret.get('donor_mount'),seen=(*seen,template))
        mapping=metadata.get('bone_mapping') or metadata.get('bone_map') or {}
        reverse={v:k for k,v in mapping.items()}
        if mount in reverse:
            return provenance(resources,metadata['donor'],'turret',mount=reverse[mount],seen=(*seen,template))
    return provenance(resources,metadata.get('recipient',''),'hull' if part=='hull' else part,mount=mount,place=place,seen=(*seen,template))


def inventory(resources,template,label=None):
    info=resources.resolve_entity(template)
    description=describe(resources,template)
    match=re.match(r'entity/-vehicle/\+?([^/]+)/',info['definition_path'])
    nation=match.group(1) if match and match.group(1)!='workshop' else ''
    common={'nation':nation,'origin':str(info.get('source_id') or ''),'origin_template':template,'definition_path':info['definition_path']}
    shown=label or resources.label_for(template)
    parts=[{**common,'part':'hull','label':shown}]
    parts += [{**common,'part':'turret','component_type':description['mount_types'][mount],'mount':mount,'label':shown+' · '+mount} for mount in description['mounts']]
    parts += [{**common,'part':'gun','place':gun['place'],'weapon':gun['weapon'],
               'label':gun.get('label') or gun['weapon']} for gun in description['guns']]
    for part in parts:
        mount=part.get('mount')
        if part['part']=='gun':
            gun=next(g for g in description['guns'] if g['place']==part['place']);mount=gun.get('yaw')
        part.update(provenance(resources,template,part['part'],mount=mount,place=part.get('place')))
    return {**description,'parts':parts}


def compile_parts(resources,template,label,output):
    from .compiler import compile_assembly
    from .build_info import source_code_sha256
    info=resources.resolve_entity(template)
    digest=hashlib.sha256(resources.read(info['definition_path'])+resources.read(info['model_path'])+
        source_code_sha256(Path(__file__).parent).encode()).hexdigest()[:14]
    identifier='mw_parts_'+digest;output=Path(output)
    plan=inventory(resources,template,label)
    hull_id=identifier+'_hull' if plan['mounts'] else identifier
    results=[];views=[];view=resources;current=identifier
    try:
        if not resources.has_entity(identifier):
            base=compile_assembly(resources,template,template,{'commander_only':True,'component_guns':[g['place'] for g in plan['guns']]},output/'shells',identifier)
            results.append(base)
            view=view.with_overlay(base['output_dir']);views.append(view)
        if plan['mounts'] and not resources.has_entity(hull_id):
            for i,mount in enumerate(plan['mounts']):
                last=i==len(plan['mounts'])-1
                result=compile_assembly(view,current,current,{'remove_module':{'schema':1,'mount':mount}},
                    output/('hull'+str(i)),hull_id if last else identifier+'_step'+str(i))
                view=view.with_overlay(result['output_dir']);views.append(view);current=result['id']
            result['metadata'].update(recipient=identifier,donor=identifier,transform={'assembly_draft':{'schema':1,'turrets':[],'guns':[],
                'removals':[{'schema':1,'mount':m} for m in plan['mounts']]}})
            result['metadata']['source_hashes']={p:h for p,h in result['metadata']['source_hashes'].items() if '/'+identifier+'_step' not in p}
            results.append(result)
        from .components import _save
        for result in results:
            result['metadata']['component_source']=True
            _save(result,{})
        for part in plan['parts']:
            part['template']=hull_id if part['part']=='hull' else identifier if part['part']=='turret' else template
        return plan,results
    finally:
        for v in reversed(views):v.close()
