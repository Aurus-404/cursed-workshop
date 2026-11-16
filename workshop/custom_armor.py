"""Resizable steel fittings with matching visible and collision boundaries.

Legacy rectangular plates retain their box volumes. Shaped plates and hollow
cylinders use a single closed surface with native steel armor properties.
"""
import hashlib
import math
import struct

from .syntax import block, quote

ASSET = 'custom:plate'
UNIT = 50.0  # millimetres per model unit (native vehicles: about 20 units per metre)
PAINT = 'cw_plate_steel'
TILE = 1000/UNIT  # model units the paint covers before repeating
LIMITS = {'width': (50, 8000), 'height': (50, 8000), 'thickness': (1, 500)}  # mm
LABEL = 'Blindaje redimensionable'


def plate(row, error=ValueError):
    """The validated {'width','height','thickness'} of a row, in millimetres."""
    value = row.get('plate') if isinstance(row, dict) else None
    if not isinstance(value, dict) or not set(LIMITS) <= set(value) or set(value) - set(LIMITS) - {'shape', 'corners', 'hole', 'paint'}:
        raise error('La placa a medida necesita ancho, alto y grosor.')
    for key, (low, high) in LIMITS.items():
        v = value[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
            raise error('El tamaño o el grosor de la placa no es válido.')
    result = {key: round(float(value[key]), 1) for key in LIMITS}
    if 'paint' in value:
        from .armor_paints import PAINTS
        if not isinstance(value['paint'], str) or value['paint'] not in PAINTS:
            raise error('Invalid armor texture.')
        result['paint'] = value['paint']
    shape = value.get('shape', 'plate')
    if shape not in ('plate', 'triangle', 'cylinder', 'circle'):
        raise error('Invalid armor shape.')
    if 'shape' in value:
        result['shape'] = shape
    if shape == 'circle':
        result['height'] = result['width']
    corners = value.get('corners')
    if corners is not None:
        count = 3 if shape == 'triangle' else 64 if shape in ('circle','cylinder') else 4
        if not isinstance(corners, list) or len(corners) != count:
            raise error('Invalid number of shape points.')
        if any(not isinstance(p, list) or len(p) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 16000 for n in p) for p in corners):
            raise error('Invalid plate corner.')
        result['corners'] = [[round(n, 3) for n in p] for p in corners]
    polygon = outline(result)
    if any(cross(polygon[i-1], polygon[i], polygon[(i+1) % len(polygon)]) <= .0001 for i in range(len(polygon))):
        raise error('Keep the plate convex, with its corners in order.')
    if shape == 'cylinder' and result['thickness'] * 2 >= result['width']:
        raise error('Cylinder wall thickness must be smaller than its radius.')
    if shape == 'cylinder' and corners is not None:
        try:
            inner = inset_polygon(polygon,result['thickness'])
        except ValueError as exc:
            raise error(str(exc)) from exc
        if any(cross(a,b,p) <= 0 for p in inner for a,b in zip(polygon,polygon[1:]+polygon[:1])):
            raise error('Cylinder wall thickness exceeds the edited profile.')
    hole = value.get('hole')
    if hole is not None:
        if shape == 'cylinder' or not isinstance(hole, dict) or set(hole) != {'x', 'y', 'diameter'} or any(type(n) not in (int, float) or not math.isfinite(n) for n in hole.values()):
            raise error('Invalid circular opening.')
        radius = hole['diameter'] / 2
        if radius < 5 or any(cross(a, b, [hole['x'], hole['y']]) / math.dist(a, b) <= radius + .1 for a, b in zip(polygon, polygon[1:] + polygon[:1])):
            raise error('The opening must fit entirely inside the plate.')
        result['hole'] = {key: round(n, 1) for key, n in hole.items()}
    return result


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0])


def outline(value):
    w, h = value['width'], value['height']
    if 'corners' in value:
        return value['corners']
    if value.get('shape') in ('circle','cylinder'):
        return [[w/2+w/2*math.cos(i*math.tau/64),w/2+w/2*math.sin(i*math.tau/64)] for i in range(64)]
    if value.get('shape') == 'triangle':
        return [[0, 0], [w, 0], [w/2, h]]
    return value.get('corners', [[0, 0], [w, 0], [w, h], [0, h]])


def inset_polygon(polygon, distance):
    lines = []
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        dx,dy=b[0]-a[0],b[1]-a[1]
        length=math.hypot(dx,dy)
        lines.append(([a[0]-dy/length*distance,a[1]+dx/length*distance],[dx,dy]))
    result=[]
    for i,(b,v) in enumerate(lines):
        a,u=lines[i-1]
        denominator=u[0]*v[1]-u[1]*v[0]
        if abs(denominator)<1e-8:
            raise ValueError('Invalid cylinder profile.')
        t=((b[0]-a[0])*v[1]-(b[1]-a[1])*v[0])/denominator
        result.append([a[0]+u[0]*t,a[1]+u[1]*t])
    return result


def sections(value):
    """Convex cells leave openings empty in both render and collision meshes."""
    if value.get('shape') == 'cylinder':
        if value.get('corners'):
            outer=outline(value)
            inner=inset_polygon(outer,value['thickness'])
            center=value['width']/2
            return [[[p[0]-center,p[1]-center] for p in (inner[i],outer[i],outer[(i+1)%len(outer)],inner[(i+1)%len(outer)])] for i in range(len(outer))]
        radius, inner = value['width']/2, value['width']/2-value['thickness']
        cells = []
        for i in range(64):
            a, b = i*math.tau/64, (i+1)*math.tau/64
            cells.append([[r*math.cos(t), r*math.sin(t)] for r, t in ((inner,a),(radius,a),(radius,b),(inner,b))])
        return cells
    polygon = outline(value)
    hole = value.get('hole')
    if not hole:
        return [polygon]
    cx, cy, radius = hole['x'], hole['y'], hole['diameter']/2
    angles = sorted({round(a, 10) for a in [i*math.tau/64 for i in range(64)] + [math.atan2(y-cy,x-cx) % math.tau for x,y in polygon]})
    inner, outer = [], []
    for angle in angles:
        dx, dy = math.cos(angle), math.sin(angle)
        distances = []
        for a,b in zip(polygon,polygon[1:]+polygon[:1]):
            ex,ey=b[0]-a[0],b[1]-a[1]
            denominator=dx*ey-dy*ex
            if abs(denominator)<1e-10:
                continue
            distance=((a[0]-cx)*ey-(a[1]-cy)*ex)/denominator
            along=((a[0]-cx)*dy-(a[1]-cy)*dx)/denominator
            if distance>0 and -1e-8<=along<=1+1e-8:
                distances.append(distance)
        reach=min(distances)
        inner.append([cx+radius*dx,cy+radius*dy])
        outer.append([cx+reach*dx,cy+reach*dy])
    return [[inner[i],outer[i],outer[(i+1)%len(angles)],inner[(i+1)%len(angles)]] for i in range(len(angles))]


def cell_mesh(value, polygon):
    cylinder = value.get('shape') == 'cylinder'
    depth = value['height'] if cylinder else value['thickness']
    def vertex(p, side):
        if cylinder:
            return [p[0]/UNIT,p[1]/UNIT,side*depth/UNIT/2]
        return [side*depth/UNIT/2,(p[0]-value['width']/2)/UNIT,(p[1]-value['height']/2)/UNIT]
    vertices = [vertex(p,side) for side in (-1,1) for p in polygon]
    n=len(polygon)
    triangles=[]
    for i in range(1,n-1):
        triangles.extend([(0,i+1,i),(n,n+i,n+i+1)])
    for i in range(n):
        j=(i+1)%n
        triangles.extend([(i,j,n+j),(i,n+j,n+i)])
    return vertices, triangles


def volume_bytes(vertices, triangles):
    indices=[i for triangle in triangles for i in triangle]
    return (b'EVLMVERT'+struct.pack('<I',len(vertices))+b''.join(struct.pack('<3f',*p) for p in vertices)
            +b'INDX'+struct.pack('<I',len(indices))+struct.pack('<%dH'%len(indices),*indices)
            +b'SIDE'+struct.pack('<I',len(triangles))+bytes(len(triangles)))


def surface_mesh(value):
    """Join adjacent cells and discard their shared internal walls."""
    faces = {}
    for polygon in sections(value):
        points, _ = cell_mesh(value, polygon)
        n = len(polygon)
        boundary = [list(range(n-1,-1,-1)),list(range(n,n*2))]
        boundary += [[i,(i+1)%n,(i+1)%n+n,i+n] for i in range(n)]
        for face in boundary:
            coordinates = [points[i] for i in face]
            key = tuple(sorted(tuple(round(x,8) for x in p) for p in coordinates))
            if key in faces:
                del faces[key]
            else:
                faces[key] = coordinates
    vertices, triangles = [], []
    for face in faces.values():
        base = len(vertices)
        vertices.extend(face)
        triangles.extend((base,base+i,base+i+1) for i in range(1,len(face)-1))
    return vertices, triangles


def sizes(value):
    """Box sizes in model units: thickness along local X (its front and rear
    faces), width along Y, height along Z; centred on its bone."""
    return [value['thickness']/UNIT, value['width']/UNIT, value['height']/UNIT]


def mesh_name(value, flipped=False):
    digest = hashlib.sha1(repr(sorted(value.items())).encode()).hexdigest()[:10]
    return 'cw_plate_'+digest+('_f' if flipped else '')+'.ply'


def paint_name(value):
    paint=value.get('paint','steel')
    return PAINT if paint=='steel' else 'cw_plate_'+paint


def mesh_bytes(value):
    """Six flat faces in the stride-48 layout of shipped meshes (position,
    normal, uv, tangent + handedness), clockwise front faces as the game expects."""
    if any(key in value for key in ('shape', 'corners', 'hole')) or value.get('paint','steel')!='steel':
        return shaped_mesh_bytes(value)
    hx, hy, hz = (s/2 for s in sizes(value))
    corners = [[hx if i & 1 else -hx, hy if i & 2 else -hy, hz if i & 4 else -hz] for i in range(8)]
    vertices, indices = [], []
    for face, normal in (((0, 2, 3, 1), (0, 0, -1)), ((4, 5, 7, 6), (0, 0, 1)), ((0, 1, 5, 4), (0, -1, 0)),
                         ((2, 6, 7, 3), (0, 1, 0)), ((0, 4, 6, 2), (-1, 0, 0)), ((1, 3, 7, 5), (1, 0, 0))):
        tangent = [0.0, 1.0, 0.0] if normal[0] else [1.0, 0.0, 0.0]
        base = len(vertices)
        for i, corner in enumerate(face):
            # The paint repeats once per metre, whatever the plate's size.
            u, v = [corners[corner][axis]/TILE for axis in range(3) if not normal[axis]]
            vertices.append([*corners[corner], *map(float, normal), u, v, *tangent, 1.0])
        indices.extend((base, base+2, base+1, base, base+3, base+2))
    bounds = [-hx, -hy, -hz, hx, hy, hz]
    mtl = (paint_name(value)+'.mtl').encode('ascii')
    return (b'EPLYBNDS'+struct.pack('<6f', *bounds)+b'MESH'+struct.pack('<5I', 0x112, 0, len(indices)//3, 0x704, 0x19e6e6e6)
            + bytes([len(mtl)])+mtl+b'VERT'+struct.pack('<IHH', len(vertices), 48, 7)
            + b''.join(struct.pack('<12f', *v) for v in vertices)+b'INDX'+struct.pack('<I', len(indices))
            + struct.pack('<'+str(len(indices))+'H', *indices))


def shaped_mesh_bytes(value):
    vertices, indices = [], []
    points, triangles = surface_mesh(value)
    bounds=[min(p[i] for p in points) for i in range(3)]+[max(p[i] for p in points) for i in range(3)]
    for triangle in triangles:
        a,b,c = [points[i] for i in triangle]
        u,v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
        normal = [u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
        length=math.sqrt(sum(n*n for n in normal))
        if length < 1e-10:
            continue
        normal=[n/length for n in normal]
        tangent=[-normal[1],normal[0],0]
        if math.hypot(*tangent[:2])<1e-8:
            tangent=[1,0,0]
        size=math.sqrt(sum(n*n for n in tangent))
        tangent=[n/size for n in tangent]
        base=len(vertices)
        axis=max(range(3),key=lambda i:abs(normal[i]))
        uvs=texture_uv(value,(a,b,c),normal,bounds)
        for p,uv in zip((a,b,c),uvs):
            smooth = normal
            if value.get('shape') == 'cylinder' and 'corners' not in value and abs(normal[2]) < .5:
                radius = math.hypot(p[0],p[1])
                sign = 1 if normal[0]*p[0]+normal[1]*p[1] > 0 else -1
                smooth = [sign*p[0]/radius,sign*p[1]/radius,0]
            vertices.append([*p,*smooth,*uv,*tangent,1])
        indices.extend([base,base+2,base+1])
    bounds=[min(p[i] for p in vertices) for i in range(3)]+[max(p[i] for p in vertices) for i in range(3)]
    mtl=(paint_name(value)+'.mtl').encode()
    return (b'EPLYBNDS'+struct.pack('<6f',*bounds)+b'MESH'+struct.pack('<5I',0x112,0,len(indices)//3,0x704,0x19e6e6e6)
            +bytes([len(mtl)])+mtl+b'VERT'+struct.pack('<IHH',len(vertices),48,7)
            +b''.join(struct.pack('<12f',*v) for v in vertices)+b'INDX'+struct.pack('<I',len(indices))
            +struct.pack('<%dH'%len(indices),*indices))


def texture_uv(value, points, normal, bounds):
    axis=max(range(3),key=lambda i:abs(normal[i]))
    if value.get('paint','steel')=='steel':
        return [[p[i]/TILE for i in range(3) if i!=axis] for p in points]
    if value.get('shape')=='cylinder' and abs(normal[2])<.5:
        uv=[[math.atan2(p[1],p[0])%math.tau/math.tau,1-(p[2]-bounds[2])/(bounds[5]-bounds[2])] for p in points]
        if max(p[0] for p in uv)-min(p[0] for p in uv)>.5:
            uv=[[u+1 if u<.5 else u,v] for u,v in uv]
        return uv
    axes=[i for i in range(3) if i!=axis]
    return [[(p[axes[0]]-bounds[axes[0]])/max(bounds[axes[0]+3]-bounds[axes[0]],1e-8),
             1-(p[axes[1]]-bounds[axes[1]])/max(bounds[axes[1]+3]-bounds[axes[1]],1e-8)] for p in points]


def paint_files(value=None):
    value=value or {}
    paint=value.get('paint','steel')
    if paint=='steel':
        from .plate_paint import texture_bytes
        raw=texture_bytes()
    else:
        from .armor_paints import texture_bytes
        raw=texture_bytes(paint)
    name=paint_name(value)
    color='230 230 230 25' if paint=='steel' else '255 255 255 25'
    return {name+'.dds': raw,
            name+'.mtl': ('{material bump {diffuse "'+name+'.dds"}{color "'+color+'"}{blend none}}').encode()}


def preview(paint='steel'):
    """What the editor needs to draw a plate as the game will: its paint and how far it reaches."""
    from .armor_paints import PAINTS, catalog
    if not isinstance(paint,str) or paint not in PAINTS:
        raise ValueError('Invalid armor texture.')
    result={'paints':catalog(), 'selected_paint':{'id':paint,'label':PAINTS[paint]},
            'tile':TILE, 'preview_valid':True, 'runtime_verified':False,
            'limits':{key:list(value) for key,value in LIMITS.items()}}
    if paint!='steel':
        return {**result,'texture_url':'/assets/armor-paints/'+paint+'.png'}
    from .plate_paint import texture_bytes
    from .textures import decode_dds
    width, height, pixels = decode_dds(texture_bytes())
    return {**result,'texture': {'width': width, 'height': height, 'rgba': list(pixels)}}


def contract(value):
    thickness = value['thickness']
    text = str(int(thickness)) if thickness == int(thickness) else str(thickness)
    return '{volume "source" {tags "armor"}{thickness '+text+'}{material "steel"}{able {NoWoundDecal}}}'


def item(value):
    """The catalog-shaped description every placed fitting carries."""
    return {'id': ASSET, 'custom': True, 'label': LABEL, 'kind': 'plates', 'plate': value,
            'materials': ['steel'], 'thickness': [value['thickness']], 'cast_steel': False}


def attach(root, final, host, prefix, pose, value, files, flipped, set_matrix):
    """Add the plate's bone, mesh, box volume and armor contract; return its names."""
    name = mesh_name(value, flipped)
    from .geometry import reverse_mesh_winding
    raw = mesh_bytes(value)
    raw = reverse_mesh_winding(raw) if flipped else raw
    for path, data in {name: raw, **paint_files(value)}.items():
        if files.get(path, data) != data:
            raise ValueError('Los recursos de la placa chocan con archivos existentes.')
        files[path] = data
    bone = block('{bone '+quote(prefix+'root')+' {VolumeView '+quote(name)+'}}')
    set_matrix(bone, pose)
    host.append(bone)
    if any(key in value for key in ('shape', 'corners', 'hole')):
        vertices, triangles = surface_mesh(value)
        volume = prefix+'v0'
        path = name.removesuffix('.ply')+'_0.vol'
        if flipped:
            triangles = [(a,c,b) for a,b,c in triangles]
        files[path] = volume_bytes(vertices, triangles)
        final['document'].append(block('{volume '+quote(volume)+' {polyhedron '+quote(path)+'} {bone '+quote(prefix+'root')+'}}'))
        behavior = block(contract(value)); behavior.atoms[1].set(volume); root.append(behavior)
        return [prefix+'root'], {'plate': volume}
    volume = prefix+'v0'
    final['document'].append(block('{volume '+quote(volume)+' {box '+' '.join('%.4f' % s for s in sizes(value))+'} {bone '+quote(prefix+'root')+'}}'))
    behavior = block(contract(value)); behavior.atoms[1].set(volume); root.append(behavior)
    return [prefix+'root'], {'plate': volume}
