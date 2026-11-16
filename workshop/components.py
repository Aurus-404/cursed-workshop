"""Reusable disassembled components and empty native weapon sockets.

Only ordinary GEM declarations reach game definitions. Empty sockets live in a
JSON sidecar and are materialized privately while composing the next vehicle.
"""
import hashlib
import json
import posixpath
from pathlib import Path

from .syntax import block, entity_root, parse

MARKER = 'cw_empty_socket'


def sidecar(info):
    return posixpath.splitext(info['definition_path'])[0]+'.components.json'


def inject(resources, info, root):
    try:
        data=json.loads(resources.read(sidecar(info)))
    except (KeyError, OSError):
        return
    if data.get('schema')!=1 or not isinstance(data.get('slots'),list):
        raise ValueError('Los puntos de montaje de esta pieza no son válidos.')
    weaponry=root.find('Weaponry')
    if weaponry is None:weaponry=block('{Weaponry}');root.append(weaponry)
    present={p.args[0] for p in weaponry.find_all('place') if p.args}
    for text in data['slots']:
        place=block(text)
        if place.head.casefold()!='place' or not place.args:
            raise ValueError('Los puntos de montaje de esta pieza no son válidos.')
        if place.args[0] not in present:
            place.append(block('{'+MARKER+'}'));weaponry.append(place)


def _save(result, files):
    output=Path(result['output_dir']);hashes=result['metadata'].setdefault('file_hashes',{})
    for path,raw in files.items():
        target=output/'resource'/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        if target.suffix!='.json':hashes[target.name]=hashlib.sha256(raw).hexdigest()
    result['files']=sorted(set(result.get('files',[]))|{'resource/'+p for p in files})
    (output/('assembly_'+result['id']+'.json')).write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf8')
    return result


def finalize(result):
    output=Path(result['output_dir']);path=result['definition_path']
    document=parse((output/'resource'/path).read_bytes());root=entity_root(document)
    slots=[]
    for weaponry in root.find_all('Weaponry'):
        for place in list(weaponry.find_all('place')):
            marker=place.find(MARKER)
            if marker is None:continue
            place.remove(marker);slots.append(place.render());weaponry.remove(place)
    if not slots:return result
    result['metadata']['empty_weapon_slots']=[block(s).args[0] for s in slots]
    from .weapon_state import weapon_places
    result['metadata']['state_profile']['weapon_places']=weapon_places(root)
    return _save(result,{path:document.render().encode('utf8'),
        posixpath.splitext(path)[0]+'.components.json':json.dumps({'schema':1,'slots':slots},ensure_ascii=False).encode('utf8')})


def describe(resources, template):
    from .compiler import _load_entity, _crew_expansion
    from .geometry import parse_model, editable_mounts, descendants, is_carried_weapon
    from .gun_assembly import discover
    info,_,root=_load_entity(resources,template);_crew_expansion(resources,root)
    model=parse_model(resources.read(info['model_path']))
    candidates=[] if is_carried_weapon(info) else editable_mounts(model,root)
    mounts=[m for m in candidates if not any(m!=other and m in descendants(model['bones'],other) for other in candidates)]
    weapons=discover(resources,template)['weapons']
    guns=[w for w in weapons if w['available'] and w.get('donatable',True) and not w.get('empty')]
    # Shared branches belong to one physical part only.
    selected=[];used=set()
    for gun in guns:
        if used&set(gun['bones']):continue
        selected.append(gun);used.update(gun['bones'])
    # A bare articulation is a socket on the chassis, not another physical
    # turret. The detachable weapon already owns all geometry on that branch.
    mounts=[m for m in mounts if any(model['bones'][n]['assets'] for n in descendants(model['bones'],m)-used)]
    from .compiler import _selected_places
    mount_types={}
    for mount in mounts:
        places,_=_selected_places(root,model,descendants(model['bones'],mount))
        types=[p.find('type').args[0].casefold() if p.find('type') and p.find('type').args else 'main' for p in places]
        mount_types[mount]='weapon_mount' if types and all(t in ('hull','aa','coaxial') for t in types) else 'turret'
    return {'template':template,'mounts':mounts,'mount_types':mount_types,'guns':selected,
            'integrated':[w for w in weapons if not w.get('empty') and w not in selected]}


def strip_guns(result, guns):
    """Keep the native attachment bones, remove each extracted gun's geometry."""
    from .geometry import parse_model
    output=Path(result['output_dir']);model_path=result['model_path'];definition=result['definition_path']
    model=parse_model((output/'resource'/model_path).read_bytes())
    document=parse((output/'resource'/definition).read_bytes());root=entity_root(document)
    names=set().union(set(),*(set(g['bones']) for g in guns))
    removed={n for n,v in model['volumes'].items() if v['bone'] in names}
    for name in names:
        node=model['bones'][name]['node']
        for view in list(node.children):
            if view.head.casefold() in ('volumeview','lodview'):node.remove(view)
    for volume in list(model['document'].find_all('volume')):
        if volume.args and volume.args[0] in removed:model['document'].remove(volume)
    files={model_path:model['document'].render().encode('utf8')}
    for path in (output/'resource'/Path(definition).parent).glob('*.ext'):
        ext=parse(path.read_bytes())
        for volume in list(ext.find_all('volume')):
            if volume.args and volume.args[0] in removed:ext.remove(volume)
        files[path.relative_to(output/'resource').as_posix()]=ext.render().encode('utf8')
    existing=output/'resource'/(posixpath.splitext(definition)[0]+'.components.json')
    slots=json.loads(existing.read_text(encoding='utf8')).get('slots',[]) if existing.is_file() else []
    places={g['place'] for g in guns}
    for weaponry in root.find_all('Weaponry'):
        for place in list(weaponry.find_all('place')):
            if place.args and place.args[0] in places:
                slots.append(place.render());weaponry.remove(place)
    for volume in list(root.find_all('volume')):
        if volume.args and volume.args[0] in removed:root.remove(volume)
    for weaponry in root.find_all('Weaponry'):
        for place in weaponry.find_all('place'):
            weapon=place.find('weapon')
            if weapon is not None and weapon.args:
                name=weapon.args[0];place.remove(weapon)
                from .syntax import quote
                place.append(block('{weapon '+quote(name)+'}'))
    # Supplies are not cloned into several component sources. The original
    # save and its inventories remain in the transaction's recovery backup.
    for extender in root.find_all('extender'):
        if extender.args==['inventory']:
            for box in extender.find_all('box'):
                for item in list(box.find_all('item')):box.remove(item)
    files[definition]=document.render().encode('utf8')
    files[posixpath.splitext(definition)[0]+'.components.json']=json.dumps({'schema':1,'slots':slots},ensure_ascii=False).encode('utf8')
    return _save(result,files)
