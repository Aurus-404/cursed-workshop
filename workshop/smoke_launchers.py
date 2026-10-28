"""Original low-poly smoke dischargers, using the game's grenade deployment path.

Geometry/paint are ours; grenade definitions, impulses and cooldown are native.
One deployment fires all added tubes, alongside an existing compatible system.
"""
import hashlib
import math
import struct

from .pivots import pose_matrix, pivot_of
from .geometry import euler_matrix, multiply, point, parse_model, read_mesh, linear_determinant, reverse_mesh_winding
from .syntax import block, quote, parse
from .extra_armor import parents


class SmokeError(ValueError):
    pass


LABELS = {1: 'Lanzador de humo simple', 2: 'Lanzador de humo doble', 3: 'Lanzador de humo triple'}
PAINT = 'cw_smoke_gray'
NATIVE = 'set/interaction_entity/tank/tank-main.inc'


def configuration(value):
    if not isinstance(value, list):
        raise SmokeError('Elegí un lanzador de uno, dos o tres tubos y su fijación.')
    for row in value:
        if (not isinstance(row, dict) or type(row.get('tubes')) is not int or row['tubes'] not in LABELS
                or not isinstance(row.get('parent'), str) or not row['parent'] or len(row['parent']) > 128):
            raise SmokeError('Elegí un lanzador de uno, dos o tres tubos y su fijación.')
        pivot_of(row)
        for key in ('position', 'rotation'):
            values = row.get(key)
            if (not isinstance(values, list) or len(values) != 3
                    or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 100000 for v in values)):
                raise SmokeError('La posición u orientación del lanzador no es válida.')
    return value


def tube_frames(count):
    """Game units ~20/m: short 9.4 cm diameter, 15.2 cm tubes at 45 degrees.

    Original simplified interpretations of wartime cylindrical dischargers, not
    claims that every 1/2/3-tube arrangement was a historical production model.
    Local +X is outward, +Z up; mounting origin is the base plate's underside.
    """
    return [euler_matrix([0, -45, (i-(count-1)/2)*7],
                         [-.6, (i-(count-1)/2)*2.15, .55]) for i in range(count)]


def geometry(count):
    if type(count) is not int or count not in LABELS:
        raise SmokeError('Elegí un lanzador de uno, dos o tres tubos y su fijación.')
    vertices, indices = [], []

    def face(points, dark=False):
        # Flat faces give deliberate low-poly edges; native material is one atlas.
        a, b, c = points[:3]
        u = [b[i]-a[i] for i in range(3)];v = [c[i]-a[i] for i in range(3)]
        n = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
        length = sum(x*x for x in n)**.5
        if length < 1e-10: return
        n = [x/length for x in n];base = len(vertices)
        for i, p in enumerate(points):
            uv = ([.75, .5] if dark else [[.05,.05],[.45,.05],[.45,.95],[.05,.95]][i%4])
            # Tangent + handedness, as in shipped stride-48 EPLY meshes.
            tangent = [1,0,0] if abs(n[0]) < .9 else [0,1,0]
            projection = sum(x*y for x,y in zip(n,tangent));tangent = [tangent[j]-projection*n[j] for j in range(3)]
            length = sum(x*x for x in tangent)**.5;tangent = [x/length for x in tangent]
            vertices.append([*p,*n,*uv,*tangent,1.0])
        # Native EPLY front faces wind clockwise relative to outward normals.
        for i in range(1, len(points)-1): indices.extend((base,base+i+1,base+i))

    def box(lo, hi):
        p = [[hi[0] if i&1 else lo[0],hi[1] if i&2 else lo[1],hi[2] if i&4 else lo[2]] for i in range(8)]
        for f in ((0,2,3,1),(4,5,7,6),(0,1,5,4),(2,6,7,3),(0,4,6,2),(1,3,7,5)):
            face([p[i] for i in f])

    width = count*2.15
    box([-1.6,-width/2,0],[1.0,width/2,.3])
    for frame in tube_frames(count):
        # Two skins, annular muzzle and closed dark bottom: visibly hollow.
        ring = lambda x,r: [point(frame,[x,r*math.cos(i*math.tau/12),r*math.sin(i*math.tau/12)]) for i in range(12)]
        back, front = ring(0,.94), ring(3.04,.94)
        inner_back, inner_front = ring(.16,.77), ring(3.04,.77)
        for i in range(12):
            j=(i+1)%12
            face([back[i],back[j],front[j],front[i]])
            face([inner_front[i],inner_front[j],inner_back[j],inner_back[i]], True)
            face([front[i],front[j],inner_front[j],inner_front[i]])
        face(list(reversed(back)));face(inner_back, True)
    return vertices, indices


def mesh_bytes(count):
    vertices, indices = geometry(count)
    bounds = [min(v[j] for v in vertices) for j in range(3)]+[max(v[j] for v in vertices) for j in range(3)]
    mtl = (PAINT+'.mtl').encode('ascii')
    return (b'EPLYBNDS'+struct.pack('<6f',*bounds)+b'MESH'+struct.pack('<5I',0x112,0,len(indices)//3,0x704,0x19e6e6e6)
            +bytes([len(mtl)])+mtl+b'VERT'+struct.pack('<IHH',len(vertices),48,7)
            +b''.join(struct.pack('<12f',*v) for v in vertices)+b'INDX'+struct.pack('<I',len(indices))
            +struct.pack('<'+str(len(indices))+'H',*indices))


def texture_bytes():
    """Original muted gray DXT1 atlas, with every mip level down to 1x1.

    Uniform matte paint avoids the visible DXT block/dither grid at close range.
    The right half is the dark bore; no game texture is redistributed or required.
    """
    chunks=[];size=64
    while size:
        for y in range(max(1,(size+3)//4)):
            for x in range(max(1,(size+3)//4)):
                gray = 18 if x*4 >= size/2 and size > 1 else 112
                encode=lambda v: ((v*31//255)<<11)|((v*63//255)<<5)|(v*31//255)
                c0,c1=encode(gray),encode(gray)
                bits=0
                chunks.append(struct.pack('<HHI',c0,c1,bits))
        size//=2
    header=struct.pack('<7I',124,0xA1007,64,64,64*64//2,0,7)+bytes(44)
    header+=struct.pack('<II4s5I',32,4,b'DXT1',0,0,0,0,0)+struct.pack('<5I',0x401008,0,0,0,0)
    return b'DDS '+header+b''.join(chunks)


def preview(count):
    mesh=read_mesh(mesh_bytes(count))
    # Three.js uses the opposite local front-face convention to native EPLY.
    # Keep its default one-sided material instead of hiding errors with DoubleSide.
    for i in range(0,len(mesh['indices']),3):
        mesh['indices'][i+1],mesh['indices'][i+2]=mesh['indices'][i+2],mesh['indices'][i+1]
    from .textures import decode_dds
    w,h,pixels=decode_dds(texture_bytes())
    return {'meshes':[mesh], 'texture':{'width':w,'height':h,'rgba':list(pixels)},
            'tubes':count,'label':LABELS[count], 'muzzles':[point(f,[3.15,0,0]) for f in tube_frames(count)],
            'directions':[[f[i][0] for i in range(3)] for f in tube_frames(count)],
            'preview_valid':True,'runtime_verified':False}


def catalog():
    return {'items':[{'id':str(n),'tubes':n,'label':label} for n,label in LABELS.items()]}


def _extenders(resources, root, seen=None, base=''):
    """Follow property includes for inherited smoke configuration, last wins."""
    from .geometry import resolve_path
    seen=set() if seen is None else seen
    found=[]
    for node in root.children:
        if node.head.casefold()=='include' and node.args and (base or node.args[0].startswith('/')):
            path=resolve_path(base,node.args[0])
            if path in seen:continue
            seen.add(path)
            if resources.exists(path):found.extend(_extenders(resources,parse(resources.read(path)),seen,path))
        elif node.head.casefold()=='extender' and node.args==['smokescreen']:found.append(node)
    return found


def apply(resources, root, model, value, assets, diagnostics):
    if value is None:return None
    rows=configuration(value)
    if not rows:return None
    from .compiler import _set_matrix
    # Pin native functionality to actual installed definitions, never a decorative FX.
    required=(NATIVE,'set/stuff/grenade/smoke grenade/nbks.grenade',
              'entity/fx/flash/flash_grenade_launcher.def')
    if not all(resources.exists(p) for p in required):
        raise SmokeError('No están disponibles los recursos nativos de humo requeridos.')
    native=parse(resources.read(NATIVE))
    definition=next((n for n in native.walk('define') if n.args==['smoke_shot']),None)
    impulse=next(iter(definition.walk('impulse')),None) if definition else None
    calls=[n for n in native.walk() if n.opener=='(' and n.head=='smoke_shot' and n.args[:2]==['args','nbk39']]
    if (impulse is None or impulse.args!=['forward','%2','%3','rnd_dir','%3','%3']
            or not any(n.args[3:5]==['-17','0.5'] for n in calls)):
        raise SmokeError('El contrato nativo del lanzador de humo no es compatible.')
    extenders=_extenders(resources,root)
    tag=next((n.args[0] for e in reversed(extenders) for n in e.find_all('itemTags') if n.args),'grenade nbks')
    contracts={'grenade nbks':('nbks grenade','nbk39'),'grenade no8_smoke':('no8_smoke grenade','no8_smoke')}
    if tag not in contracts:
        raise SmokeError('El sistema de humo original usa una munición incompatible con estas monturas.')
    item,grenade=contracts[tag]
    try:
        projectile=resources.resolve_entity(grenade)
        dependencies={projectile['definition_path'],projectile['model_path'],
                      'set/stuff/grenade/smoke grenade/'+item.split()[0]+'.grenade'}
        for path in dependencies:assets.sources[path]=hashlib.sha256(resources.read(path)).hexdigest()
    except (KeyError,OSError,ValueError) as error:
        raise SmokeError('No están disponibles los recursos nativos de humo requeridos.') from error
    # A turret swap can remove all original launchers while retaining the
    # compatible ammo/cooldown extender. New tubes then replace that dispatch;
    # inheriting it would still fire from bones that no longer exist.
    replace_native_dispatch = False
    if extenders:
        native_shots={n for n in parse_model(model['document'].render())['bones']
                      if (n.startswith('smoke_shot') and not n.endswith('_cap'))
                      or (n.startswith('cw_smoke_') and '_shot' in n)}
        replace_native_dispatch = not native_shots
    if not extenders:
        root.append(block('{extender "smokescreen" {cooldown 20}{itemTags "grenade nbks"}}'))
        root.append(block('{props "smoke"}'))
    final=parse_model(model['document'].render());model.update(final)
    hosts={p['name'] for p in parents(final)};reserved=set(final['bones'])
    files={PAINT+'.dds':texture_bytes(),PAINT+'.mtl':('{material bump {diffuse "'+PAINT+'.dds"}{color "230 230 230 25"}{blend none}}').encode()}
    result=[];serial=0
    for row in rows:
        if row['parent'] not in hosts:raise SmokeError('El punto de fijación del lanzador ya no existe en este vehículo.')
        while any(n.startswith('cw_smoke_'+str(serial)+'_') for n in reserved):serial+=1
        prefix='cw_smoke_'+str(serial)+'_';serial+=1
        # Native standalone meshes use a reflected basis. Preserve that visible
        # side also on unusual hosts whose complete frame has positive parity.
        flipped=linear_determinant(final['bones'][row['parent']]['world'])>0
        mesh_name='cw_smoke_'+str(row['tubes'])+('_flipped' if flipped else '')+'.ply'
        node=block('{bone '+quote(prefix+'root')+' {VolumeView '+quote(mesh_name)+'}}')
        _set_matrix(node,pose_matrix(row))
        muzzles=[]
        for i,frame in enumerate(tube_frames(row['tubes'])):
            muzzle=prefix+'shot'+str(i);fx=prefix+'flash'+str(i)
            at=[r[:] for r in frame]
            for axis in range(3):at[axis][3]=point(frame,[3.15,0,0])[axis]
            for name,matrix in ((muzzle,multiply(at,euler_matrix([0,0,180]))),(fx,at)):
                locator=block('{bone '+quote(name)+'}');_set_matrix(locator,matrix);node.append(locator)
            muzzles.append({'bone':muzzle,'fx':fx})
        final['bones'][row['parent']]['node'].append(node);reserved.add(prefix+'root')
        raw=mesh_bytes(row['tubes'])
        files[mesh_name]=reverse_mesh_winding(raw) if flipped else raw
        result.append({**row,'root':prefix+'root','muzzles':muzzles})
    if any(name in assets.files for name in files):raise SmokeError('Los recursos del lanzador chocan con archivos existentes.')
    assets.files.update(files)
    for path in required:assets.sources[path]=hashlib.sha256(resources.read(path)).hexdigest()
    inventory=next((n for n in root.find_all('extender') if n.args==['inventory']),None)
    if inventory is None:inventory=block('{extender "inventory" {box}}');root.append(inventory)
    box=inventory.find('box')
    if box is None:box=block('{box}');inventory.append(box)
    initial='{item '+quote(item)+' 3}';box.append(block(initial))
    diagnostics.append({'code':'smoke_launchers_runtime_gate','level':'warning',
                        'message':'Added smoke tubes use native deployment and finite inventory charges; firing, consumption and persistence require battle acceptance.'})
    return {'schema':1,'mounts':result,'item':item,'grenade':grenade,'initial_item':initial,
            'initialize':True,'native_system_retained':bool(extenders) and not replace_native_dispatch,
            'replace_native_dispatch':replace_native_dispatch,'runtime_verified':False}


def add_interactions(record, contract):
    """Replace our previous dispatch; retain native smoke behavior and effects."""
    handlers=[n for n in record.find_all('on') if n.args[:1]==['smoke_screen']]
    if len(handlers)>1:raise SmokeError('El vehículo tiene eventos de humo ambiguos.')
    replace_native_dispatch = bool(contract and contract.get('replace_native_dispatch'))
    if handlers:handler=handlers[0]
    else:
        if not contract:return
        handler=block('{on "smoke_screen" overload'+('' if replace_native_dispatch else ' {inherited}')+'}')
        record.append(handler)
    if replace_native_dispatch:
        # Only the obsolete smoke event is replaced; other chassis events and
        # the native ammo/cooldown contract remain in force.
        handler.items=block('{on "smoke_screen" overload}').items
    def retire(node):
        changed=False
        for child in list(node.children):
            if child.head.casefold()=='spawn' and len(child.args)>1 and child.args[1].startswith('cw_smoke_'):
                node.remove(child)
                changed=True
            else:
                removed=retire(child);changed |= removed
                if removed and child.head.casefold()=='delay' and not child.children:
                    node.remove(child)
        return changed
    retire(handler)
    if not contract:return
    # Basic chassis need not inherit the tank interaction record.
    if not any(n.args[:1]==['deploy_smoke'] for n in record.find_all('on')):
        record.append(block('{on "deploy_smoke" overload {if operatable {if work "body" {call "smoke_screen"}}}}'))
    index=0
    for mount in contract['mounts']:
        for muzzle in mount['muzzles']:
            handler.append(block('{delay '+str(round(index*.1,2))+' {spawn '+quote(contract['grenade'])+' '+quote(muzzle['bone'])
                +' x {impulse forward -17 0.5 rnd_dir 0.5 0.5}{call "activate"}}{spawn "flash_grenade_launcher" '+quote(muzzle['fx'])+' x}}'))
            index+=1
