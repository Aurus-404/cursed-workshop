"""Individual weapon assemblies. Resource-backed discovery, no vehicle whitelist.

The elevation interface is transplanted; unrelated branches under that interface
are retained. Vehicle crew, yaw, mobility and boarding remain recipient-owned.
Runtime acceptance is deliberately independent of structural validation.
"""
import hashlib
import json
import struct
import posixpath
import copy
import tempfile
import math
from pathlib import Path

from .pivots import pose_matrix, pivot_of, rebase_joint, level_pose, level_joint
from .syntax import parse, block, quote, bone_name, Node
from .geometry import (parse_model, descendants, multiply, inverse, identity,
                       euler_matrix, translation, point, resolve_path, is_carried_weapon)
from .compiler import (CompileError, _load_entity, _state_template, _explicit_weapon_operators, _crew_expansion,
    _include_defines, _set_matrix, _remove_bone, _rename_bone_refs, _module_ids,
    _volume_mapping, _rename_volume, _bind_renamed_volumes, _parameters, _values,
    _edit_complete_crew, _Assets, _validate_export, _animation_map, _animation_layout, _animation_is_empty, _place_model_extension, _allowed_damage_ids, _prioritize_crew, _camera_sockets, _crew_roles, _validate_carriage_crew,
    _append_inherited_crew)


class GunError(CompileError):
    pass


def weapon_link(place, model):
    for head in ('LinkBone','bone','shooter','foresight'):
        for node in place.find_all(head):
            if node.args and _bone(model,node.args[0]):
                return _bone(model,node.args[0])
    return _bone(model,place.args[0]) if place.args else None


def _bone(model,name):
    matches=[n for n in model['bones'] if n.casefold()==name.casefold()]
    return matches[0] if len(matches)==1 else None


def _places(root):
    return [p for w in root.find_all('Weaponry') for p in w.find_all('place') if p.args]


def _elevation_interface(model, link):
    """Put a verified, coincident pitch/yaw socket into yaw -> pitch order.

    Some native casemates put a bare vertical yaw joint inside their bare
    elevation joint. The nearest revolute is then yaw, not a detachable gun.
    Only commute empty, coincident interfaces with no other elevation branch;
    keep both native joint frames/limits and every child's complete rest frame.
    This edits the private parsed model, never the source resource.
    """
    chain=[];name=link
    while name in model['bones']:
        entry=model['bones'][name]
        if entry['node'].args[0].casefold()=='revolute':chain.append(name)
        name=entry['parent']
    if len(chain)<2:return
    yaw_name,pitch_name=chain[:2]
    yaw,pitch=[model['bones'][n] for n in (yaw_name,pitch_name)]
    def vertical(entry):
        axis=[entry['world'][r][2] for r in range(3)]
        length=math.sqrt(sum(v*v for v in axis))
        return abs(axis[2])/length if length else None
    ya,pa=vertical(yaw),vertical(pitch)
    if ya is None or pa is None or ya<1-1e-4 or pa>1e-4:return
    if (yaw['parent']!=pitch_name or not pitch['parent'] or yaw['assets'] or pitch['assets']
        or [bone_name(n) for n in pitch['node'].find_all('bone')]!=[yaw_name]
        or any(abs(yaw['world'][r][3]-pitch['world'][r][3])>1e-6 for r in range(3))):
        raise GunError('GunAssembly: No se puede separar la elevación de la torreta o del carro.')
    parent=model['bones'][pitch['parent']]
    for child in list(yaw['node'].find_all('bone')):
        _set_matrix(child,multiply(inverse(pitch['world']),model['bones'][bone_name(child)]['world']))
        yaw['node'].remove(child);pitch['node'].append(child)
    pitch['node'].remove(yaw['node']);parent['node'].remove(pitch['node'])
    _set_matrix(yaw['node'],multiply(inverse(parent['world']),yaw['world']))
    _set_matrix(pitch['node'],multiply(inverse(yaw['world']),pitch['world']))
    yaw['node'].append(pitch['node']);parent['node'].append(yaw['node'])
    normalized={**model.get('normalized_interfaces',{}),pitch_name:yaw_name}
    updated=parse_model(model['document'].render())
    model.clear();model.update(updated);model['normalized_interfaces']=normalized


def _barrel(model, barrel, owned):
    """Bones whose meshes "barrel only" keeps: the barrel's own mesh (its breech
    block, levers and sights hang from it as separate parts and are left out),
    or its whole branch when the barrel bone itself shows nothing."""
    if not barrel or barrel not in owned:return None
    if model['bones'][barrel]['assets']:return [barrel]
    branch=sorted(descendants(model['bones'],barrel)&owned)
    return branch if any(model['bones'][n]['assets'] for n in branch) else None


def _contract(root, model, place, resources=None):
    link=weapon_link(place,model)
    if not link:raise GunError('GunAssembly: El arma no tiene un enlace de modelo inequívoco.')
    weapon=place.find('weapon')
    if not weapon or not weapon.args:raise GunError('GunAssembly: Las armas de inventario se colocan con la montura del comandante.')
    kind=place.find('type')
    if kind and kind.args and kind.args[0]=='commander':raise GunError('GunAssembly: El visor no es un cañón.')
    _elevation_interface(model,link)
    chain=[];name=link
    while name in model['bones']:
        chain.append(name);name=model['bones'][name]['parent']
    pivots=[n for n in chain if model['bones'][n]['node'].args[0].casefold()=='revolute']
    if not pivots:raise GunError('GunAssembly: El arma no tiene una articulación de elevación.')
    pivot=pivots[0]
    axis=[model['bones'][pivot]['world'][r][2] for r in range(3)]
    length=sum(v*v for v in axis)**.5
    # A single horizontal-axis pivot can be a casemate gun. A lone yaw root
    # would remove the entire turret/carriage, which belongs to another operation.
    if not model['bones'][pivot]['parent'] or (pivot==model['anchor_name'] and (not length or abs(axis[2])/length>1e-4)):
        raise GunError('GunAssembly: No se puede separar la elevación de la torreta o del carro.')
    names=descendants(model['bones'],pivot)
    shared=set()
    links=[]
    for other in _places(root):
        if other is place:continue
        other_link=weapon_link(other,model)
        if other_link is None and other.find('weapon') and resources is not None:
            refs={}
            _fillings(resources,other.find('weapon'),refs)
            other_link=next((_bone(model,n) for n in refs.values() if _bone(model,n)),None)
            if other_link is None:
                raise GunError('GunAssembly: Una referencia requerida queda fuera del conjunto desmontable.\n'+other.args[0])
        links.append((other_link,False))
    for placer in root.find_all('placer'):
        for seat in placer.find_all('place'):
            linkbone=seat.find('LinkBone')
            if linkbone and linkbone.args and linkbone.args[0]:links.append((_bone(model,linkbone.args[0]),True))
    for other_link,is_seat in links:
        if is_seat and other_link==pivot:continue  # Same retained elevation socket.
        if other_link==link or other_link==pivot:
            raise GunError('GunAssembly: El arma comparte su enganche de tiro o elevación con otra arma.')
        if other_link in names:
            # Keep the entire secondary branch, including its own recoil mesh.
            branch=other_link
            while model['bones'][branch]['parent'] in names and model['bones'][branch]['parent']!=pivot:
                branch=model['bones'][branch]['parent']
            if link in descendants(model['bones'],branch):
                raise GunError('GunAssembly: Una rama compartida no se puede separar del cañón elegido.')
            shared |= descendants(model['bones'],branch)
    owned=names-shared
    for head in ('LinkBone','bone','shooter','foresight'):
        for reference in place.find_all(head):
            if reference.args and _bone(model,reference.args[0]) not in owned:
                raise GunError('GunAssembly: Una referencia requerida queda fuera del conjunto desmontable.\n'+head+': '+reference.args[0])
    if not any(model['bones'][n]['assets'] for n in owned):
        raise GunError('GunAssembly: El conjunto desmontable no tiene geometría visible.')
    # The barrel: the piece right under the elevation pivot on the way to the
    # gun's own socket (the recoiling "gun" bone), with what hangs from it.
    barrel=chain[chain.index(pivot)-1] if chain.index(pivot)>0 else None
    spec={'place':place.args[0],'weapon':weapon.args[0],'link':link,'pivot':pivot,
          'bones':sorted(owned),'shared':sorted(shared),'yaw':pivots[1] if len(pivots)>1 else None,
          'barrel':_barrel(model,barrel,owned)}
    if pivot in model.get('normalized_interfaces',{}):
        spec['normalized_interface']={'elevation':pivot,'yaw':model['normalized_interfaces'][pivot]}
    return spec


def discover(resources, template):
    info,_,root=_load_entity(resources,template)
    props=_explicit_weapon_operators(resources,root)
    _crew_expansion(resources,root)
    model=parse_model(resources.read(info['model_path']))
    rows=[]
    for place in _places(root):
        if not place.find('weapon'):continue
        try:
            spec=_contract(root,model,place,resources)
            rows.append({**spec,'available':True,'reason':''})
        except GunError as error:
            rows.append({'place':place.args[0],'weapon':place.find('weapon').args[0],
                         'available':False,'reason':str(error)})
    for row in rows:
        row['label']=resources.weapon_label(row['weapon']) if hasattr(resources,'weapon_label') else row['weapon'].replace('_',' ')
    return {'template':template,'source':info['source_id'],'kind':'towed' if is_carried_weapon(info) else 'vehicle','weapons':rows}


def selected(resources,template,place_id):
    info,document,root=_load_entity(resources,template)
    props=_explicit_weapon_operators(resources,root)
    _crew_expansion(resources,root)
    model=parse_model(resources.read(info['model_path']))
    matches=[p for p in _places(root) if p.args[0]==place_id]
    if len(matches)!=1:raise GunError('GunAssembly: Elegí exactamente un Weaponry.place de cada vehículo.')
    spec=_contract(root,model,matches[0],resources)
    spec['properties']=sorted(props)
    return info,document,root,model,matches[0],spec


def _upright(receiver_frame, donor_frame):
    """A half turn about the barrel when the receiver's joint frame would hang
    the donor gun upside down. Every native gun's elevation axis points to the
    left of its barrel; the Nb.Fz's point right, and guns changed there came out
    belly up. Rotation only, in the receiver joint's own frame."""
    v=[donor_frame[2][i] for i in range(3)]  # world up, seen from the donor pivot
    if sum(receiver_frame[2][i]*v[i] for i in range(3))<0:
        return euler_matrix([180,0,0])
    return identity()


def _flip_limits(node):
    """Limits written for the opposite joint axis: [a,b] becomes [-b,-a]."""
    for limits in node.walk('limits'):
        try:low,high=(float(v) for v in limits.args[:2])
        except ValueError:continue
        for atom,value in zip(limits.atoms[1:3],(-high,-low)):atom.set(('%g'%value))


def preview_parts(resources, recipient, donor, spec, *, recipient_frame=None):
    from .geometry import build_preview, bounds_of
    from .compiler import seat_markers
    _,_,_,rm,_,rs=selected(resources,recipient,spec.get('recipient_place'))
    _,_,_,dm,_,ds=selected(resources,donor,spec.get('donor_place'))
    hull=build_preview(resources,recipient,'full',seats=seat_markers(resources,recipient),include_inventory=True)
    module=build_preview(resources,donor,'full')
    receiver_frame=recipient_frame if recipient_frame is not None else rm['bones'][rs['pivot']]['world']
    anchor=point(receiver_frame,[0,0,0])
    flip=_upright(receiver_frame,dm['bones'][ds['pivot']]['world']) if spec.get('upright') else identity()
    frame=multiply(multiply(multiply(translation([-v for v in anchor]),receiver_frame),flip),inverse(dm['bones'][ds['pivot']]['world']))
    # A shared receiver elevation housing survives export, unless the player
    # leaves the original mantlet out. Keep it visible while positioning the
    # replacement too; the other weapon owns its branch.
    mantlet=not spec.get('drop_mantlet')
    hull['meshes']=[m for m in hull['meshes'] if m['bone'] not in rs['bones']
                    or (mantlet and rs['shared'] and m['bone']==rs['pivot'])
                    or (mantlet and m['bone'].startswith('cw_mantlet_'))]
    shown=set(ds['barrel']) if spec.get('barrel_only') and ds.get('barrel') else set(ds['bones'])
    module['meshes']=[m for m in module['meshes'] if m['bone'] in shown]
    if not module['meshes'] or any(d.get('code')=='mesh_unavailable' for d in module['diagnostics']):
        raise GunError('GunAssembly: La vista previa no puede resolver la geometría del cañón donante.')
    normal=inverse(frame)
    for mesh in module['meshes']:
        mesh['positions']=[v for p in zip(*[iter(mesh['positions'])]*3) for v in point(frame,p)]
        values=[]
        for p in zip(*[iter(mesh['normals'])]*3):
            n=[sum(normal[c][r]*p[c] for c in range(3)) for r in range(3)]
            length=sum(v*v for v in n)**.5 or 1;values.extend(v/length for v in n)
        mesh['normals']=values
    # Armour follows the same parts as the meshes and the export (Lazarus_Opti:
    # the 75 mm carriage's armour showed around a gun change until it was saved).
    hull['armor']=[a for a in hull.get('armor',[]) if a['bone'] not in rs['bones'] or (mantlet and rs['shared'] and a['bone']==rs['pivot'])]
    module['armor']=[{**a,'positions':[v for p in zip(*[iter(a['positions'])]*3) for v in point(frame,p)]}
                     for a in module.get('armor',[]) if a['bone'] in set(ds['bones'])]
    hull['joints']=[j for j in hull['joints'] if j['bone'] not in rs['bones']]
    # The compiled elevation joint keeps the receiver's axis; a gun turned
    # upright about it reads its own limits the other way round.
    bare=multiply(multiply(translation([-v for v in anchor]),receiver_frame),inverse(dm['bones'][ds['pivot']]['world']))
    joints=[]
    for joint in module['joints']:
        if joint['bone'] not in ds['bones']:continue
        if joint['bone']==ds['pivot'] and flip!=identity():
            low,high=joint['limits'];joints.append({**joint,'matrix':multiply(bare,joint['matrix']),'limits':[-high,-low]})
        else:joints.append({**joint,'matrix':multiply(frame,joint['matrix'])})
    module['joints']=joints
    for data,part,pivot in ((hull,'hull',rs['pivot']),(module,'turret',ds['pivot'])):
        data.update(part=part,anchor_name=pivot,anchor=anchor if part=='hull' else [0,0,0],mounts=[pivot],seat=0,carried=False)
        data['bounds']=bounds_of([v for m in data['meshes'] for v in m['positions']])
        data['stats']={'meshes':len(data['meshes']),'triangles':sum(len(m['indices'])//3 for m in data['meshes'])}
    return {'hull':hull,'turret':module,'preview_valid':True,'runtime_verified':False}


def _ammo_key(name):
    # "family type" -> family ("bulletger_75 he"); the "ammo " container prefix
    # is not part of it: "ammo flame_thrower" is a whole family with no type.
    tokens=name.split()
    prefix=tokens[:1] if tokens[:1]==['ammo'] else []
    rest=tokens[len(prefix):]
    return ' '.join(prefix+(rest[:-1] if len(rest)>1 else rest))


def _same_ammo(first,second):
    return first.removeprefix('ammo ')==second.removeprefix('ammo ')


def _ammo_in(family,families):
    return any(_same_ammo(family,other) for other in families)


def _mounted_ammo(resources,root):
    families=set()
    for inv in root.find_all('extender'):
        if inv.args!=['inventory']:continue
        slots={n.args[0] for n in inv.find_all('weapon') if n.args and n.find('mask') and 'mountable' in n.find('mask').args}
        for item in inv.walk('item'):
            if item.args and any(n.args and n.args[0] in slots for n in item.find_all('user')):
                try:
                    family=_fillings(resources,block('{weapon '+quote(item.args[0])+'}'))[0]
                    if family.startswith('ammo '):families.add(_ammo_key(family))
                    else:families.update((family,'ammo '+family))
                except (ValueError,KeyError,OSError):pass
    return families


def _ammunition(resources,item,sources):
    from .registration import _stuff_definition
    tokens=item.removeprefix('ammo ').casefold().split()
    matches=[entry.path for key,entry in resources.entries.items() if key.startswith('set/stuff/')
             and key.endswith('.ammo') and posixpath.basename(key)[:-5].split('.')==tokens]
    if len(matches)!=1:
        raise GunError('GunAssembly: La munición no tiene una definición efectiva inequívoca.\n'+item)
    seen=set()
    def visit(path):
        if path in seen:raise GunError('GunAssembly: La herencia del arma contiene un ciclo.')
        seen.add(path);raw=resources.read(path);sources[path]=hashlib.sha256(raw).hexdigest()
        doc=parse(raw);node=doc.find('from');entity=None
        if node and node.args:
            parent=node.args[0];pattern=parent.startswith('pattern ')
            if pattern:parent_path=_stuff_definition(resources,parent[8:],path,True)
            else:
                try:parent_path=resources.resolve_reference(parent,path,('.ammo',''))
                except FileNotFoundError:parent_path=_stuff_definition(resources,parent,path)
            entity=visit(parent_path)
        else:node=next((n for n in doc.children if n.opener=='{'),None)
        own=node.find('entity') if node else None
        return own.args[0] if own and own.args else entity
    return {'item':item,'definition':matches[0],'projectile_entity':visit(matches[0])}


def _place_sequence_name(name, place, taken):
    """A free sequence name for one gun's own copy of a shared clip name."""
    base=name+'_'+''.join(c if c.isalnum() or c=='_' else '_' for c in place)
    candidate,n=base,2
    while candidate in taken:candidate,n=f'{base}_{n}',n+1
    return candidate


def merge_animations(recipient, donor):
    """Merge disjoint sparse EANM tracks without resampling their native frames."""
    names=[];frames={};duration=0
    for raw in (recipient,donor):
        bone_names,cursor,count=_animation_map(raw)
        if raw[cursor:cursor+4]==b'FRMN':
            raise GunError('GunAssembly: No se pueden combinar pistas globales de animaciones antiguas.')
        _,layout=_animation_layout(raw);duration=max(duration,count)
        if set(names)&set(bone_names):raise GunError('GunAssembly: Hay pistas de animación compartidas en conflicto.')
        names.extend(bone_names)
        for frame in layout:
            records=frames.setdefault(frame['index'],[])
            for name,pos,rot,flags,deformation in frame['tracks']:
                payload=(raw[pos:pos+12] if pos is not None else b'')+(raw[rot:rot+12] if rot is not None else b'')
                if deformation:payload+=raw[deformation[0]:sum(deformation)]
                records.append((name,flags,payload))
    if len(names)>256:raise GunError('GunAssembly: La animación combinada excede el mapa de huesos del motor.')
    result=donor[:8]+b'FRMS'+struct.pack('<I',duration)+b'BMAP'+struct.pack('<I',len(names))
    for name in names:
        encoded=name.encode('ascii');result+=struct.pack('<I',len(encoded))+encoded
    for index,tracks in sorted(frames.items()):
        if len(tracks)>255:raise GunError('GunAssembly: La animación combinada excede las pistas por cuadro del motor.')
        result+=b'FRM2'+struct.pack('<HB',index,len(tracks))
        for name,flags,payload in tracks:result+=struct.pack('<BH',names.index(name),flags)+payload
    return result


def _stuff_presets(resources, path):
    """Macros a weapon file sees: the .presets of its folder and of every folder
    above it under set/stuff (the Crocodile's flamer fills its tank with
    ("filling_tank" fuel(20)) from set/stuff/flame/.presets)."""
    defines={};folder=posixpath.dirname(path)
    chain=[]
    while folder and folder.casefold().startswith('set/stuff'):
        chain.append(folder);folder=posixpath.dirname(folder)
    for folder in reversed(chain):  # nearer folders override
        preset=folder+'/.presets'
        try:document=parse(resources.read(preset))
        except (KeyError,OSError,ValueError):continue
        defines.update({k.casefold():v for k,v in _include_defines(resources,document,preset).items()})
        defines.update({n.args[0].casefold():n for n in document.find_all('define') if n.args})
    return defines


def _fillings(resources, weapon, references=None):
    """Effective stuff inheritance, used only to resolve the actual ammo family."""
    from .registration import _stuff_definition, _expand_macros
    hashes={};seen=set()
    def visit(path):
        if path in seen:raise GunError('GunAssembly: La herencia del arma contiene un ciclo.')
        seen.add(path);raw=resources.read(path);hashes[path]=hashlib.sha256(raw).hexdigest()
        doc=parse(raw);node=doc.find('from')
        result=None
        if node and node.args:
            parent=node.args[0];pattern=parent.startswith('pattern ')
            result=visit(_stuff_definition(resources,parent.removeprefix('pattern '),path,pattern))
        else:node=next((n for n in doc.children if n.opener=='{'),None)
        if node is not None:node=_expand_macros(node,_stuff_presets(resources,path))
        filling=node.find('filling') if node else None
        if references is not None and node:
            for head in ('foresight','shooter'):
                reference=node.find(head)
                if reference and reference.args:references[head]=reference.args[0]
        return filling.args[0] if filling and filling.args else result
    effective=visit(_stuff_definition(resources,weapon.args[0]))
    explicit=weapon.args[2] if len(weapon.args)>=3 and weapon.args[1]=='filling' else None
    if not explicit and not effective:raise GunError('GunAssembly: El arma no tiene un filling resuelto.')
    family=_ammo_key(explicit) if explicit else effective
    # Native automatic weapons declare e.g. filling "mgun_rus" while vehicle
    # inventory uses "ammo mgun_rus dt". "ammo " is a container prefix, not
    # another caliber. The selected inventory item's definition is resolved
    # separately by _ammunition before anything can compile.
    families=(effective,'ammo '+effective) if effective else ()
    if effective and explicit and not any(explicit==name or explicit.startswith(name+' ') for name in families):
        raise GunError('GunAssembly: El filling explícito no coincide con la definición efectiva del arma.')
    return family,hashes


def _recruit_crew(resources, root, model, original, donor_root, donor_model, recruits, parent, to_receiver, gunners):
    """Seats for the operators a gun needs and the receiver lacks.

    Each recruit keeps the place it has around the donor gun (a field gun's
    loaders stand behind its breech), on the floor of the receiver's own
    gunner, takes its native pose and boards through that gunner's door.
    """
    if not recruits:
        return
    donor_crew=donor_root.clone();_crew_expansion(resources,donor_crew)
    def seat(node,role):
        place=next((p for p in node.walk('place') if p.args[:1]==[role] and p.find('linkbone') and p.find('linkbone').args),None)
        return place.find('linkbone').args[0] if place else None
    def boarding(node,head,role):
        return next((n for b in node.find_all('boarder') for n in b.find_all(head) if (n.args[1:2] if head=='link' else n.args[:1])==[role]),None)
    gunner=next((g for g in gunners if seat(root,g) in original['bones']),None)
    floor=original['bones'][seat(root,gunner)]['world'] if gunner else None
    link=boarding(root,'link',gunner) if gunner else None
    door=link.args[0] if link else next((n.args[0] for b in root.find_all('boarder') for n in b.find_all('door') if n.args),None)
    for source,role in recruits:
        bone=seat(donor_crew,source)
        if bone in donor_model['bones']:
            world=[list(row) for row in multiply(to_receiver,donor_model['bones'][bone]['world'])]
            if floor is not None:world[2][3]=floor[2][3]
        else:
            world=[list(row) for row in (floor or original['bones'][parent]['world'])]
        name='cw_crew_'+role;serial=0
        while name in model['bones'] or name in original['bones']:serial+=1;name='cw_crew_'+role+'_'+str(serial)
        node=block('{bone '+quote(name)+'}')
        _set_matrix(node,multiply(inverse(original['bones'][parent]['world']),world))
        model['bones'][parent]['node'].append(node)
        root.append(block('{placer {place '+quote(role)+' {group "crew"} {linkbone '+quote(name)+'} {turnoff {shadow}}}}'))
        if door:root.append(block('{boarder {link '+quote(door)+' '+quote(role)+' {anm '+quote(role)+'} {forward putoff} {reverse puton}}}'))
        pose=boarding(donor_crew,'anm',source)
        pose=pose.clone() if pose else block('{anm '+quote(role)+' {forward {end "pose_squat_empty"}}}')
        pose.atoms[1].set(role)
        native=boarding(root,'anm',gunner) if gunner else None
        if native and native.find('reverse') and not pose.find('reverse'):pose.append(native.find('reverse').clone())
        holder=block('{boarder}');holder.append(pose);root.append(holder)


def compile_gun(resources, recipient, donor, transform, output, identifier, *, recipient_frame=None):
    spec=transform['gun_swap']
    if not isinstance(spec,dict) or spec.get('schema',1)!=1:
        raise GunError('GunAssembly: La versión de receta no está admitida.')
    hull=transform.get('hull') or {}
    if any(hull.get('position',[0,0,0])) or any(hull.get('rotation',[0,0,0])) or ((transform.get('crew_seats') or transform.get('crew_exclude') or transform.get('crew_fixed')) and not transform.get('_design_crew')):
        raise GunError('GunAssembly: Conservá la posición y los tripulantes del receptor; ajustá solamente el cañón elegido.')
    ri,rd,rr,rm,rp,rs=selected(resources,recipient,spec.get('recipient_place'))
    di,dd,dr,dm,dp,ds=selected(resources,donor,spec.get('donor_place'))
    _crew_expansion(resources,rr)
    _validate_carriage_crew(rr)
    # A carried weapon (tripod MG, AT rifle) gets its crew from shared includes
    # that the emitted definition keeps: count them, without writing them twice.
    crew_view=rr
    if is_carried_weapon(ri):
        crew_view=rr.clone();_append_inherited_crew(resources,crew_view);_crew_expansion(resources,crew_view)
    roles = _crew_roles(crew_view)
    if not roles:raise GunError('GunAssembly: No se pudo determinar la tripulación del receptor.')
    # The weapon defines the operators it needs: the recipient's own seats
    # serve it first (an unnamed loader is the crew's "charger", as in game),
    # and any gunner or loader it still lacks comes with the gun (recruits).
    operators={};recruits=[]
    for role in ('gunner','charger'):
        field=rp.find(role)
        names=field.args[0].split() if field and field.args else ['gunner'] if role=='gunner' else ['charger'] if 'charger' in roles else []
        names=[name for name in names if name in roles]
        source=dp.find(role)
        wanted=source.args[0].split() if source and source.args else ['gunner'] if role=='gunner' else []
        required=1 if role=='gunner' else 0
        if source and 'minWorkers' in source.args:
            required=max(required,int(source.args[source.args.index('minWorkers')+1]))
        for name in wanted:
            if len(names)>=required:break
            if name in names:continue
            if name not in roles:recruits.append((name,name));roles.add(name)
            names.append(name)
        if role=='gunner' and not names:
            raise GunError('GunAssembly: El receptor no tiene un puesto válido para operar el arma.\n'+role)
        # Without any loader the gunner loads alone, as on any one-man mount.
        operators[role]=names
    old_ammo,_=_fillings(resources,rp.find('weapon'))
    effective_refs={}
    ammo,weapon_hashes=_fillings(resources,dp.find('weapon'),effective_refs)
    sources=dict(weapon_hashes);diagnostics=[]
    for info in (ri,di):
        for path in (info['definition_path'],info['model_path'],*info.get('extension_paths',[])):
            sources[path]=hashlib.sha256(resources.read(path)).hexdigest()
    rnames,dnames=set(rs['bones']),set(ds['bones'])
    for head,name in effective_refs.items():
        explicit=dp.find(head)
        if explicit and explicit.args:name=explicit.args[0]
        name=_bone(dm,name)
        if name not in dnames:
            raise GunError('GunAssembly: Una referencia requerida queda fuera del conjunto desmontable.\n'+head+': '+str(name))
        effective_refs[head]=name
    rshared,dshared=set(rs['shared']),set(ds['shared'])
    # Keep housings preserved by an earlier replacement through later edits,
    # unless the player leaves the original mantlet out ("drop_mantlet").
    mantlet=not spec.get('drop_mantlet')
    for name in list(rnames) if mantlet else []:
        ancestor=rm['bones'][name]['parent']
        while ancestor in rm['bones'] and ancestor!=rs['pivot'] and rm['bones'][ancestor]['node'].args[0].casefold()!='revolute':
            ancestor=rm['bones'][ancestor]['parent']
        if name.startswith('cw_mantlet_') and ancestor==rs['pivot']:
            branch=descendants(rm['bones'],name)
            rshared |= branch;rnames -= branch
    roriginal=parse_model(rm['document'].render())
    # Receiver coaxial branches keep their exact rest frame and follow elevation.
    retained=[]
    for name in sorted(rshared):
        if rm['bones'][name]['parent'] not in rshared:
            retained.append((name,rm['bones'][name]['node'].clone()))
    mounted=dm['bones'][ds['pivot']]['node'].clone()
    for node in list(mounted.walk('bone')):
        if bone_name(node) in dshared:
            for parent in [mounted,*mounted.walk('bone')]:
                if node in parent.children:parent.remove(node);break
    # "Barrel only" (TheOppsAreOntoMe): an emplacement or field gun's breech,
    # cradle and shield stick out of a vehicle turret. Its bones stay (sights,
    # recoil and crew still use them); only the barrel keeps its meshes.
    if (transform.get('gun_swap') or {}).get('barrel_only') and ds.get('barrel'):
        keep=set(ds['barrel'])
        for node in [mounted,*mounted.walk('bone')]:
            if bone_name(node) not in keep:
                for view in [*node.find_all('VolumeView'),*node.find_all('LODView')]:node.remove(view)
        diagnostics.append({'code':'barrel_only','level':'info','message':'Only the donor barrel is shown; breech, cradle and shield were left out.'})
    parent=rm['bones'][rs['pivot']]['parent']
    offset=transform.get('turret') or {}
    adjustment=pose_matrix(offset)
    # Editor offsets use world axes, as existing XYZ gizmos do.
    receiver_frame=recipient_frame if recipient_frame is not None else rm['bones'][rs['pivot']]['world']
    anchor=point(receiver_frame,[0,0,0])
    # Gun changes saved since 0.7.8 stand upright on any receiver; older ones
    # kept the receiver's roll and may carry a manual half turn to undo it.
    flip=_upright(receiver_frame,dm['bones'][ds['pivot']]['world']) if (transform.get('gun_swap') or {}).get('upright') else identity()
    world=multiply(multiply(translation(anchor),adjustment),multiply(multiply(translation([-v for v in anchor]),receiver_frame),flip))
    local=multiply(inverse(rm['bones'][parent]['world']),world)
    _set_matrix(mounted,local)
    # A Weaponry place may bind by its own name even without a same-named
    # source bone. Imported geometry must not create a new implicit host for
    # a retained receiver weapon (e.g. BT-2 mgun2 on a T-35).
    kept_slots={p.args[0] for p in _places(rr) if p is not rp and p.args}
    mapping=_volume_mapping(dnames,(set(rm['bones'])-rnames)|kept_slots)
    split=bool(any(pivot_of(offset)) and rs['shared'])
    emitted_pivot=rs['pivot']
    if split:
        serial=0
        while 'cw_joint_'+str(serial) in rm['bones'] or 'cw_joint_'+str(serial) in dnames:serial+=1
        emitted_pivot='cw_joint_'+str(serial)
    # The donor may carry its own bone named like the receiver's pivot (leIG 18
    # "gun_rot1" under its "gun_rot", taking the Nb.Fz auxiliary MG's gun_rot1).
    clash=next((n for n in dnames if n!=ds['pivot'] and mapping.get(n,n)==emitted_pivot),None)
    if clash:
        used=set(rm['bones'])|set(dnames)|set(mapping.values());serial=0
        while 'module_'+clash+('_'+str(serial) if serial else '') in used:serial+=1
        mapping[clash]='module_'+clash+('_'+str(serial) if serial else '')
    mapping[ds['pivot']]=emitted_pivot
    if len({mapping.get(n,n) for n in dnames})!=len(dnames):
        raise GunError('GunAssembly: El remapeo de huesos es ambiguo.')
    _rename_bone_refs(mounted,mapping)
    housing=None
    if mantlet and rs['shared'] and rm['bones'][rs['pivot']]['assets']:
        # Preserve the native housing without leaving its mesh on the old
        # independent coax joint. A rigid child follows the selected gun's
        # elevation, even when its rotation centre was edited separately.
        serial=0
        used=set(rm['bones'])|set(dnames)|set(mapping.values())
        while 'cw_mantlet_'+str(serial) in used:serial+=1
        housing_name='cw_mantlet_'+str(serial)
        housing=block('{bone '+quote(housing_name)+'}')
        for child in rm['bones'][rs['pivot']]['node'].children:
            if child.head.casefold() in ('volumeview','lodview','visibility'):
                housing.append(child.clone())
        _set_matrix(housing,multiply(inverse(world),rm['bones'][rs['pivot']]['world']))
    _remove_bone(rm,rs['pivot'])
    if split:
        # Other weapons retain the native receiver joint. The selected weapon
        # receives its own copied native donor joint, with a separate centre.
        kept=rm['bones'][rs['pivot']]['node'].clone()
        for child in list(kept.children):
            if child.head.casefold()=='bone' or (housing is not None and child.head.casefold() in ('volumeview','lodview')):kept.remove(child)
        for name,node in retained:
            _set_matrix(node,multiply(inverse(rm['bones'][rs['pivot']]['world']),rm['bones'][name]['world']))
            kept.append(node)
        rm['bones'][parent]['node'].append(kept);retained=[];rnames.discard(rs['pivot'])
    rm['bones'][parent]['node'].append(mounted)
    _recruit_crew(resources,rr,rm,roriginal,dr,dm,recruits,parent,multiply(world,inverse(dm['bones'][ds['pivot']]['world'])),operators['gunner'])
    for name,node in retained:
        _set_matrix(node,multiply(inverse(world),rm['bones'][name]['world']))
        mounted.append(node)
    if housing is not None:
        mounted.append(housing)
        retained.append((rs['pivot'],housing))
        for value in rm['volumes'].values():
            if value['bone']==rs['pivot']:
                _rename_bone_refs(value['node'],{rs['pivot']:housing_name})
    rv={n for n,v in rm['volumes'].items() if v['bone'] in rnames
        and not (housing is not None and v['bone']==rs['pivot'])}
    dv={n for n,v in dm['volumes'].items() if v['bone'] in dnames}
    kept_ids=_module_ids(rm,set(rm['bones'])-rnames)
    donor_ids={v for name in dnames for k,v in _parameters(dm['bones'][name]['node']).items() if k=='id'}
    vm=_volume_mapping(dv|donor_ids,(set(rm['volumes'])-rv)|kept_ids)
    for node in mounted.walk('parameters'):
        for atom in node.atoms[1:]:
            parts=[]
            for pair in atom.value.split(';'):
                key,separator,value=pair.partition('=')
                parts.append(key+separator+vm.get(value,value) if separator and key.casefold()=='id' else pair)
            atom.set(';'.join(parts))
    donor_volumes=[]
    for name in rv:rm['document'].remove(rm['volumes'][name]['node'])
    for name in sorted(dv):
        node=_rename_volume(dm['volumes'][name]['node'],vm);_rename_bone_refs(node,mapping)
        rm['document'].append(node);donor_volumes.append(node)
    donor_nodes=[]
    for node in list(rr.children):
        if (node.head.casefold()=='volume' and node.args and node.args[0] in rv) or (node.opener=='(' and _values(node)&rv):rr.remove(node)
        elif node.head.casefold()=='bone' and _bone(rm,bone_name(node) or '') in rnames:rr.remove(node)
    for node in dr.children:
        if (node.head.casefold()=='volume' and node.args and node.args[0] in dv) or (node.opener=='(' and _values(node)&dv):
            donor_nodes.append(_rename_volume(node,vm))
        elif node.head.casefold()=='bone' and _bone(dm,bone_name(node) or '') in dnames:
            clone=node.clone();clone.atoms[-1].set(_bone(dm,bone_name(node)));_rename_bone_refs(clone,mapping);donor_nodes.append(clone)
    donor_joint=[n for n in donor_nodes if n.head.casefold()=='bone' and bone_name(n)==emitted_pivot]
    # Keep native receiver elevation limits/speed where donor only inherits them.
    if not donor_joint:
        # Effective explicit overrides come from the original definition.
        _,_,original_root=_load_entity(resources,recipient)
        for n in original_root.find_all('bone'):
            if bone_name(n)==rs['pivot']:
                clone=n.clone();_rename_bone_refs(clone,{rs['pivot']:emitted_pivot});donor_nodes.append(clone)
    from .registration import _expand_macros
    new_place=_expand_macros(dp,{k.casefold():v for k,v in _include_defines(resources,dr).items()})
    new_place.atoms[1].set(rp.args[0])
    # Selection belongs to the recipient slot, not the donor's old role. A
    # towed primary often omits basic, which a selectable secondary requires.
    recipient_slot=_expand_macros(rp,{k.casefold():v for k,v in _include_defines(resources,rr).items()})
    for head in ('type','basic','ai_in_manual'):
        for old in list(new_place.find_all(head)):new_place.remove(old)
        for field in recipient_slot.find_all(head):new_place.append(field.clone())
    for role,names in operators.items():
        source_operator=dp.find(role)
        for old in list(new_place.find_all(role)):new_place.remove(old)
        if not names:continue
        worker=source_operator.clone() if source_operator else block('{'+role+' '+quote(' '.join(names))+'}')
        worker.atoms[1].set(' '.join(names))
        if 'minWorkers' in worker.args:
            # The donor's crew size never stops the gun: it needs at most the recipient's operators.
            at=worker.args.index('minWorkers')+1
            if int(worker.args[at])>len(names):worker.atoms[at+1].set(str(len(names)))
        new_place.append(worker)
    for head,name in effective_refs.items():
        for old in list(new_place.find_all(head)):new_place.remove(old)
        new_place.append(block('{'+head+' '+quote(name)+'}'))
    if new_place.find('LinkBone') is None:new_place.append(block('{LinkBone '+quote(ds['link'])+'}'))
    for head in ('LinkBone','foresight','shooter','bone'):
        for ref in new_place.find_all(head):
            if ref.args and _bone(dm,ref.args[0]):ref.atoms[1].set(_bone(dm,ref.args[0]))
    _rename_bone_refs(new_place,mapping)
    weaponry_owner=next(w for w in rr.find_all('Weaponry') if any(p is rp for p in w.children))
    weaponry_owner.items=[new_place if n is rp else n for n in weaponry_owner.items]
    old_muzzle={p for p in rs['properties'] if p.startswith('muzzle_')}
    new_muzzle={p for p in ds['properties'] if p.startswith('muzzle_')}
    # Muzzle props are the vehicle's main gun effects ({on fire "gun"} at
    # foresight1): only a new weapon in the "gun" place changes them. A 2 cm
    # in the hull MG slot (Kenway) gave the kept 75 mm its flash and sound.
    if new_muzzle and old_muzzle!=new_muzzle and rp.args[0].casefold()=='gun':
        rr.append(block('{props '+' '.join(quote(p) for p in sorted({'-'+p for p in old_muzzle}|new_muzzle))+'}'))
    # Only selected ammo, never donor fuel, tools, towing gear or other weapons.
    kept_ammo=_mounted_ammo(resources,rr)
    for p in _places(rr):
        if p is new_place:continue
        w=p.find('weapon')
        if w:
            try:kept_ammo.add(_fillings(resources,w)[0])
            except (ValueError,KeyError,OSError):pass
    inventories=[n for n in rr.find_all('extender') if n.args==['inventory']]
    if not inventories:
        rr.append(block('{extender "inventory" {box}}'));inventories=[rr.children[-1]]
    inv=inventories[0];boxnode=inv.find('box')
    if boxnode is None:boxnode=block('{box}');inv.append(boxnode)
    for inventory in inventories:
        for box in inventory.find_all('box'):
            for item in list(box.find_all('item')):
                if item.args and _same_ammo(_ammo_key(item.args[0]),old_ammo) and not _ammo_in(old_ammo,kept_ammo):box.remove(item)
    ammo_items=[];ammo_contract=[]
    for invd in dr.find_all('extender'):
        if invd.args!=['inventory']:continue
        for box in invd.find_all('box'):
            for item in box.find_all('item'):
                if item.args and _same_ammo(_ammo_key(item.args[0]),ammo):
                    ammo_contract.append(_ammunition(resources,item.args[0],sources))
                    ammo_items.append(item.render());boxnode.append(item.clone())
    if not ammo_items:raise GunError('GunAssembly: El donante no tiene munición de inventario compatible con su filling.')
    pivot=pivot_of(offset)
    # Tilting or rolling a gun (a cannon laid on its side) must not turn its
    # elevation into a traverse: the joint follows only the horizontal turn and
    # keeps its origin; a static child carries the tilted model.
    level=multiply(multiply(translation(anchor),pose_matrix(level_pose(offset))),multiply(translation([-v for v in anchor]),receiver_frame))
    level=[[*level[r][:3],world[r][3]] for r in range(3)]+[[0,0,0,1]]
    # The donor's own limits are written for its pivot's axis. When the level
    # joint turns about the opposite axis (the gun hangs on its back, or the
    # receiver's axis points the other way), up became down and every limit
    # the player set looked reversed.
    if sum(world[r][2]*level[r][2] for r in range(3))<0:
        for node in donor_joint:_flip_limits(node)
    level_joint(rm['document'],emitted_pivot,multiply(inverse(level),world))
    world=level
    if any(pivot):
        centre=point(multiply(translation(anchor),adjustment),pivot)
        rebase_joint(rm['document'],emitted_pivot,point(inverse(world),centre))
    camera_mapping, camera_sources = _camera_sockets(
        resources, donor, dm, rm['document'], ds['pivot'], emitted_pivot,
        {n: mapping.get(n, n) for n in dnames}, diagnostics, external_parent=rs.get('yaw'))
    sources.update(camera_sources)
    assets=_Assets(resources,output/'resource/entity/-vehicle/workshop'/identifier,sources,diagnostics)
    if transform.get('_design_crew'):
        _edit_complete_crew(resources,rr,rm,transform)
        roles -= set(transform.get('crew_exclude') or [])
    final=parse_model(rm['document'].render())
    assets.note_source(roriginal);assets.note_source(dm)
    assets.context('h',roriginal,final,exclude=rnames)
    # Do not retarget donor tracks onto retained same-named receiver bones.
    donor_context=parse_model(dm['document'].render())
    donor_context['bones']={n:b for n,b in dm['bones'].items() if n in dnames}
    assets.context('d',donor_context,final,mapping)
    retained_parents={id(node):next(p for p in [mounted,*mounted.walk('bone')] if node in p.children) for _,node in retained}
    for _,node in retained:retained_parents[id(node)].remove(node)
    assets.rewrite(mounted,di['model_path'],'d')
    for node in donor_volumes:assets.rewrite(node,di['model_path'],'d')
    rm['bones'][parent]['node'].remove(mounted)
    for node in donor_volumes:rm['document'].remove(node)
    assets.rewrite(rm['document'],ri['model_path'],'h')
    for _,node in retained:assets.rewrite(node,ri['model_path'],'h');retained_parents[id(node)].append(node)
    rm['bones'][parent]['node'].append(mounted)
    for node in donor_volumes:rm['document'].append(node)
    assets.rewrite(rd,ri['definition_path'],'h')
    for node in donor_nodes:assets.rewrite(node,di['definition_path'],'d');rr.append(node)
    _bind_renamed_volumes(resources,rr,vm)
    # Bring the donor's global sequences only when they actually animate this gun.
    # One the receiver already uses for other bones (its own "recoil") keeps
    # them: the new gun gets its own copy named after its place, or every gun
    # sharing the name would recoil on any shot.
    renamed_sequences={}
    anim=rm['skeleton'].find('animation')
    if anim is None:anim=block('{animation}');rm['skeleton'].append(anim)
    donor_anim=dm['skeleton'].find('animation')
    for seq in list(anim.find_all('sequence')):
        if seq.find('file') is None and seq.args:
            path=resolve_path(ri['model_path'],seq.args[0]+'.anm')
            if resources.exists(path):seq.append(block('{file '+quote(assets.add(path,'h'))+'}'))
    for seq in donor_anim.find_all('sequence') if donor_anim else []:
        if not seq.args:continue
        file=seq.find('file');path=resolve_path(di['model_path'],file.args[0] if file and file.args else seq.args[0]+'.anm')
        if not resources.exists(path):continue
        tracks=set(_animation_map(resources.read(path))[0])
        if not tracks&dnames:continue
        old=next((n for n in anim.find_all('sequence') if n.args==seq.args),None)
        newfile=assets.add(path,'d')
        clone=seq.clone()
        if old:
            oldfile=old.find('file')
            if oldfile and set(_animation_map(assets.files[oldfile.args[0]])[0]):
                name=_place_sequence_name(seq.args[0],rs['place'],{n.args[0] for n in anim.find_all('sequence') if n.args})
                clone.atoms[1].set(name);renamed_sequences[seq.args[0]]=name
            else:anim.remove(old)
        if clone.find('file'):clone.remove(clone.find('file'))
        clone.append(block('{file '+quote(newfile)+'}'));anim.append(clone)
    _place_model_extension(rr,identifier+'.mdl')
    wrecks=[]
    # Preserve native damaged hull/turret; modified gun is lost on destruction.
    for tag in ('x','xx'):
        try:wi,wd,wr=_load_entity(resources,_state_template(resources,recipient,tag))
        except (OSError,KeyError):continue
        wm=parse_model(resources.read(wi['model_path']));assets.note_source(wm)
        assets.context('h'+tag,wm,wm);assets.rewrite(wm['document'],wi['model_path'],'h'+tag)
        for seq in wm['document'].walk('sequence'):
            if seq.args and not seq.find('file'):
                path=resolve_path(wi['model_path'],seq.args[0]+'.anm')
                if resources.exists(path):seq.append(block('{file '+quote(assets.add(path,'h'+tag))+'}'))
        assets.rewrite(wd,wi['definition_path'],'h'+tag);_place_model_extension(wr,identifier+'_'+tag+'.mdl')
        assets.prune(wm['document'])
        assets.files[identifier+'_'+tag+'.mdl']=wm['document'].render().encode('utf8')
        assets.files[identifier+'_'+tag+'.def']=wd.render().encode('utf8')
        wrecks.append({'state':tag,'native_recipient_wreck':True,'gun_not_transferred':True})
    if transform.get('commander_mg') is not None:
        from .commander_mg import apply_mount
        rm=parse_model(rm['document'].render())
        commander=apply_mount(resources,rr,rm,transform['commander_mg'],assets,diagnostics)
    else:commander=None
    from .editable_recipe import fitting_parents
    fittings = fitting_parents(transform, commander)
    from .smoke_launchers import apply as apply_smoke
    smoke_launchers = apply_smoke(resources, rr, rm, fittings.get('smoke_launchers'), assets, diagnostics)
    from .point_fittings import apply_towing, apply_charge
    towing = apply_towing(rr, rm, transform.get('towing'), diagnostics)
    charge = apply_charge(resources, rr, rm, transform.get('charge'), diagnostics)
    from .rider_mg import apply as apply_riders
    rider_mgs = apply_riders(resources, rr, rm, fittings.get('rider_mgs'), assets, diagnostics)
    from .passenger_seats import apply as apply_passengers
    passenger_seats = apply_passengers(resources, rr, rm, transform.get('passenger_seats'), assets, diagnostics)
    from .extra_armor import apply as apply_extra_armor
    armor_fittings = apply_extra_armor(resources, rr, rm, fittings.get('extra_armor'), assets, diagnostics)
    from .crew_attachment import apply_crew_fixed
    crew_attachment=apply_crew_fixed(resources,rr,rm,transform.get('crew_fixed'))
    from .crew_visibility import apply_visibility
    crew_visibility=apply_visibility(rr,transform.get('crew_hidden'),diagnostics=diagnostics,resources=resources)
    if transform.get('joint_limits') is not None:
        from .articulation import apply_limits, skipped_limits_diagnostic
        rm=parse_model(rm['document'].render())
        skipped=apply_limits(rm,rr,transform['joint_limits'],
                             {'hull':{n:n for n in set(rm['bones'])-rnames},'turret':{n:mapping.get(n,n) for n in dnames}})
        if skipped:diagnostics.append(skipped_limits_diagnostic(skipped))
    assets.prune(rm['document'])
    if any(d.get('code')=='animation_tracks_dropped' for d in diagnostics):
        raise GunError('GunAssembly: No se pueden adaptar todas las pistas de animación necesarias.')
    files={n:raw for n,raw in assets.files.items() if not(n.endswith('.anm') and _animation_is_empty(raw))}
    _prioritize_crew(resources,rr)
    _validate_carriage_crew(rr)
    files[identifier+'.def']=rd.render().encode('utf8');files[identifier+'.mdl']=rm['document'].render().encode('utf8')
    validation=_validate_export(files,frozenset(assets.missing_materials),frozenset(assets.missing_bones))
    final=parse_model(rm['document'].render())
    for place in _places(rr):
        for head in ('LinkBone','foresight','shooter'):
            for node in place.find_all(head):
                if node.args and not _bone(final,node.args[0]):raise GunError('GunAssembly: Una referencia de arma no existe en el modelo final.\n'+head+': '+node.args[0])
    for placer in rr.find_all('placer'):
        for seat in placer.find_all('place'):
            link=seat.find('LinkBone')
            if link and link.args and link.args[0] and not _bone(final,link.args[0]):
                raise GunError('GunAssembly: Una referencia de arma no existe en el modelo final.\n'+seat.args[0]+': '+link.args[0])
    allowed=_allowed_damage_ids(resources,rr,final)
    old_ids=_module_ids(roriginal,rnames)-kept_ids
    new_ids=_module_ids(dm,dnames)|dv
    from .weapon_state import weapon_places
    profile={'weapon_places':weapon_places(rr),'recipient_module_names':sorted(old_ids|rv),'donor_module_names':sorted(new_ids),
             'donor_name_map':{n:vm.get(n,mapping.get(n,n)) for n in new_ids},'allowed_damage_ids':sorted(allowed),
             'weaponry_policy':'reset','inventory_policy':'gun_replace','hull_authority':recipient,'turret_authority':donor,
             'gun_swap':{'place':rs['place'],'old_ammo':old_ammo if not _ammo_in(old_ammo,kept_ammo) else None,
                         'ammo_items':ammo_items,'initialize':True}}
    if commander:profile['commander_mg']=commander
    contract={'schema':1,'recipient':rs,'donor':ds,'operators':operators,'filling':ammo,
              'camera_bone_mapping':camera_mapping,
              'ammunition':ammo_contract,
              'bone_mapping':mapping,'volume_mapping':vm,'retained_weapons':[p.args[0] for p in _places(rr) if p is not new_place],
              'sequences':[s.args[0] for s in rm['document'].walk('sequence') if s.args],
              'sequence_names':renamed_sequences,
              'recipient_mount_frame':roriginal['bones'][rs['pivot']]['world'],
              'donor_mount_frame':dm['bones'][ds['pivot']]['world'],'emitted_mount_frame':world}
    metadata={'schema_version':1,'compiler':'independent-gem-assembly','id':identifier,'recipient':recipient,'donor':donor,
              'operation':'gun_swap','transform':transform,'gun_assembly':contract,'recipient_mount':rs['pivot'],
              'camera_bone_mapping':camera_mapping,
              'donor_mount':ds['pivot'],'bone_mapping':{n:mapping.get(n,n) for n in dnames},'crew_roles':sorted(roles),'crew_slots':len(roles),
              'source_hashes':sources,'state_profile':profile,'wrecks':wrecks,'crew_visibility':crew_visibility,'crew_attachment':crew_attachment,
              'validation':{'structural':'passed','dependencies':validation,'game_runtime':'pending','campaign_acceptance':'pending'}}
    if commander:metadata['commander_mg']=commander
    if smoke_launchers:
        metadata['smoke_launchers']=smoke_launchers
        profile['smoke_launchers']=smoke_launchers
    if towing:metadata['towing']=towing
    if charge:metadata['charge']=charge
    if rider_mgs:
        metadata['rider_mgs']=rider_mgs
        profile['rider_mgs']=rider_mgs
    if passenger_seats:
        metadata['passenger_seats']=passenger_seats
    if armor_fittings is not None:metadata['extra_armor']=armor_fittings
    for n,raw in list(files.items()):
        if n.endswith(('.def','.mdl','.ext','.mtl')):files[n]=raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
    metadata['file_hashes']={n:hashlib.sha256(raw).hexdigest() for n,raw in files.items()}
    assets.target.mkdir(parents=True)
    for n,raw in files.items():(assets.target/n).write_bytes(raw)
    result={'id':identifier,'output_dir':str(output),'entity_path':'-vehicle/workshop/'+identifier,
            'definition_path':'entity/-vehicle/workshop/'+identifier+'/'+identifier+'.def',
            'model_path':'entity/-vehicle/workshop/'+identifier+'/'+identifier+'.mdl',
            'files':[(p.relative_to(output)).as_posix() for p in assets.target.rglob('*') if p.is_file()],
            'metadata':metadata,'diagnostics':diagnostics,'ready_for_campaign':True,
            'compiled':True,'preview_valid':False,'runtime_verified':False}
    (output/('assembly_'+identifier+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return result


def gun_changes(transform):
    saved=transform.get('gun_swaps',[])
    if not isinstance(saved,list):raise GunError('El diseño del vehículo no es válido.')
    rows=copy.deepcopy(saved)
    if transform.get('gun_swap'):
        rows.append({**copy.deepcopy(transform['gun_swap']),'pose':copy.deepcopy(transform.get('turret') or {})})
    places=set()
    for row in rows:
        if not isinstance(row,dict) or row.get('schema',1)!=1 or any(not isinstance(row.get(k),str) or not row[k] or len(row[k])>128 for k in ('recipient_place','donor_place')):
            raise GunError('Elegí las dos armas antes de guardar el cambio.')
        if row['recipient_place'] in places:raise GunError('Ese puesto ya tiene un cambio guardado. Quitalo antes de reemplazarlo otra vez.')
        places.add(row['recipient_place'])
        pose=row.get('pose') or {};pivot_of(pose)
        if not isinstance(pose,dict):raise GunError('La posición u orientación del cambio de arma no es válida.')
        for key,bound in (('position',100000),('rotation',360)):
            values=pose.get(key,[0,0,0])
            if not isinstance(values,list) or len(values)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>bound for v in values):
                raise GunError('La posición u orientación del cambio de arma no es válida.')
        euler_matrix(pose.get('rotation',[0,0,0]),pose.get('position',[0,0,0]))
    if not rows:raise GunError('Guardá al menos un cambio de arma.')
    return rows


def compile_guns(resources,recipient,donor,transform,output,identifier,*,stage_profiles=False):
    """Several selected places from one owned donor, one campaign transaction.

    Each pass uses the existing compiler and independent ResourceIndex overlay.
    Intermediate variants stay temporary and never become campaigns or registry
    dependencies. Attachments are applied once on the final pass.
    """
    rows=gun_changes(transform);contracts=[];profiles=[];sources={};diagnostics=[];view=resources;views=[];granted=set()
    output=Path(output)
    if not hasattr(resources,'with_overlay'):raise GunError('Los cambios guardados requieren un índice de recursos activo.')
    # All row poses refer to their original receiver sockets. A prior swap may
    # move a shared elevation pivot; it must not shift another row's origin.
    frames={}
    for row in rows:
        _,_,_,model,_,spec=selected(resources,recipient,row['recipient_place'])
        frames[row['recipient_place']]=model['bones'][spec['pivot']]['world']
    with tempfile.TemporaryDirectory(prefix='cw_gun_steps_') as temp:
        current=recipient
        try:
            for i,row in enumerate(rows):
                last=i==len(rows)-1;name=identifier if last else 'mw_draft_'+hashlib.sha256((identifier+str(i)).encode()).hexdigest()[:12]
                folder=output if last else Path(temp)/str(i)
                tr=copy.deepcopy(transform) if last else {}
                tr.pop('gun_swaps',None);tr['gun_swap']={k:v for k,v in row.items() if k!='pose'}
                tr['turret']=copy.deepcopy(row.get('pose') or {'position':[0,0,0],'rotation':[0,0,0]})
                row_donor=row.get('donor_template') or donor
                result=compile_gun(view,current,row_donor,tr,folder,name,recipient_frame=frames[row['recipient_place']])
                metadata=result['metadata'];contracts.append(metadata['gun_assembly'])
                contracts[-1]['donor']['template']=row_donor
                # One donor's ammunition is transferred once even when two
                # selected slots share its ammo family. Separate owned donors
                # remain separate grants, including identical vehicle models.
                ammo=metadata['state_profile']['gun_swap']['ammo_items'];duplicates=[];unique=[]
                for text in ammo:
                    grant=(row.get('donor_eid') or row_donor,text)
                    if grant in granted:duplicates.append(text)
                    else:granted.add(grant);unique.append(text)
                if duplicates:
                    path=folder/'resource'/result['definition_path'];document=parse(path.read_bytes());root=document.find('game_entity')
                    for text in duplicates:
                        removed=False
                        for inv in root.find_all('extender'):
                            if inv.args!=['inventory']:continue
                            for box in inv.find_all('box'):
                                for item in list(box.find_all('item')):
                                    if not removed and item.render()==text:box.remove(item);removed=True
                    raw=document.render().replace('\r\n','\n').replace('\n','\r\n').encode('utf8');path.write_bytes(raw)
                    metadata['file_hashes'][path.name]=hashlib.sha256(raw).hexdigest()
                    metadata['state_profile']['gun_swap']['ammo_items']=unique
                profiles.append(copy.deepcopy(metadata['state_profile']))
                for path,digest in metadata['source_hashes'].items():
                    if '/workshop/mw_draft_' not in path:sources[path]=digest
                diagnostics.extend(result['diagnostics'])
                if not last:
                    view=resources.with_overlay(folder);views.append(view)
                    # A later generated level may still reference only its own
                    # copied dependencies; it does not need earlier temp roots.
                    current=name
            metadata=result['metadata'];metadata.update(recipient=recipient,donor=donor,transform=copy.deepcopy(transform),
                gun_assemblies=contracts,source_hashes=sources,
                gun_profiles=[{'donor_template':r.get('donor_template') or donor,'donor_eid':r.get('donor_eid'),'profile':p} for r,p in zip(rows,profiles)])
            from .registration import _stuff_definition
            primary=next((c for c in contracts if '/gun/' in _stuff_definition(resources,c['donor']['weapon'])),contracts[0])
            metadata['gun_assembly']=primary;metadata['gun_camera_donor']=primary['donor']['template']
            metadata['camera_bone_mapping']=primary.get('camera_bone_mapping',{})
            metadata['bone_mapping']={n:primary['bone_mapping'].get(n,n) for n in primary['donor']['bones']}
            profile=metadata['state_profile'];profile['hull_authority']=recipient
            profile['recipient_module_names']=sorted({n for p in profiles for n in p['recipient_module_names']})
            mapping={};ambiguous=set()
            for p in profiles:
                for key,value in p['donor_name_map'].items():
                    if key in mapping and mapping[key]!=value:
                        if not stage_profiles and len({r.get('donor_template') or donor for r in rows})==1:
                            raise GunError('Los cambios comparten un componente de daño del donante que no se puede asignar sin ambigüedad.')
                        if stage_profiles:
                            ambiguous.add(key);mapping.pop(key,None);continue
                    if key not in ambiguous:mapping[key]=value
            profile['donor_name_map']=mapping;profile['donor_module_names']=sorted(mapping)
            # Use the final effective loadout to avoid deleting ammunition still
            # used by another retained/replaced weapon, and avoid duplicate grants.
            root=parse((output/'resource'/result['definition_path']).read_bytes()).find('game_entity')
            final_ammo=_mounted_ammo(resources,root)
            for place in _places(root):
                weapon=place.find('weapon')
                if weapon:
                    try:final_ammo.add(_fillings(resources,weapon)[0])
                    except (ValueError,KeyError,OSError):pass
            old={p['gun_swap'].get('old_ammo') for p in profiles}-{None}
            original_ammo=set()
            _,_,original_root=_load_entity(resources,recipient)
            replaced_places={row['recipient_place'] for row in rows}
            for place in _places(original_root):
                if not place.args or place.args[0] not in replaced_places:continue
                weapon=place.find('weapon')
                if weapon:original_ammo.add(_fillings(resources,weapon)[0])
            profile['gun_swap']['old_ammo_families']=sorted(f for f in old|original_ammo if not _ammo_in(f,final_ammo))
            profile['gun_swap']['old_ammo']=None
            profile['gun_swap']['ammo_items']=list(dict.fromkeys(text for p in profiles for text in p['gun_swap']['ammo_items']))
            result['diagnostics']=diagnostics
            (output/f'assembly_{identifier}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
            return result
        finally:
            for item in reversed(views):item.close()
