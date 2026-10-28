"""Native module boundaries shared by removal, assembly and their previews.

This describes existing GEM hierarchies; it does not manufacture geometry or
infer that an exported model has passed a battle test.
"""
from .geometry import descendants, is_carried_weapon, whole_model_mount


def apply_native_poses(model, spec, *, root=None, wreck=False):
    """Adjust existing owned branches without transplanting weapons or crew."""
    from .compiler import _set_matrix, _hull_system
    from .geometry import replaceable_mounts, point, translation, multiply, inverse
    from .pivots import pose_matrix
    if not isinstance(spec,dict) or len(spec)>16:
        raise ValueError('La selección de módulos instalados no es válida.')
    candidates=set(replaceable_mounts(model))
    if root is not None:
        from .gun_assembly import weapon_link
        for weaponry in root.find_all('Weaponry'):
            for place in weaponry.find_all('place'):
                name=weapon_link(place,model)
                while name in model['bones']:
                    bone=model['bones'][name]
                    if bone['parent'] and bone['node'].args[0].casefold()=='revolute':candidates.add(name)
                    name=bone['parent']
    top={n for n in candidates if not any(n!=p and n in descendants(model['bones'],p) for p in candidates)}
    for mount,pose in spec.items():
        if wreck and mount not in model['bones']:continue
        if mount not in model['bones'] or (not wreck and mount not in top):
            raise ValueError('Elegí una torreta separable para ajustar.')
        if any(_hull_system(n) for n in descendants(model['bones'],mount)):
            raise ValueError('La torreta comparte sistemas del chasis que no se pueden mover.')
        bone=model['bones'][mount];origin=point(bone['world'],[0,0,0])
        world=multiply(multiply(multiply(translation(origin),pose_matrix(pose)),translation([-v for v in origin])),bone['world'])
        parent=model['bones'][bone['parent']]['world']
        _set_matrix(bone['node'],multiply(inverse(parent),world))


def removal_mount(spec):
    if (not isinstance(spec, dict) or spec.get('schema') != 1
            or not isinstance(spec.get('mount'), str) or not spec['mount']
            or len(spec['mount']) > 160 or spec['mount'].startswith('@')):
        raise ValueError('Elegí una torreta separable para retirar.')
    return spec['mount']


def complete_contract(info, root, model):
    """Opt-in whole artillery, only with one unambiguous structural root.

    A complete tank would bring a second powertrain. A multi-root artillery
    needs an explicit adapter rather than silently losing sibling geometry.
    Native wheels/trails remain passive geometry on the recipient chassis.
    """
    from .compiler import _hull_system, _selected_places, _place_roles
    if not is_carried_weapon(info):
        raise ValueError('El conjunto completo requiere una pieza de artillería transportable.')
    roots = [n for n, b in model['bones'].items() if b['parent'] is None]
    if len(roots) != 1:
        raise ValueError('El conjunto completo requiere una única raíz de modelo verificable.')
    mount = whole_model_mount(model)
    names = descendants(model['bones'], mount)
    protected = {n for n in names if _hull_system(n)}
    protected |= {n.args[0] for n in root.walk('component') if n.args and _hull_system(n.args[0])}
    if protected:
        raise ValueError('El conjunto completo contiene sistemas de propulsión que requieren un adaptador.')
    weapons, unknown = _selected_places(root, model, names)
    if unknown or not weapons or len(root.find_all('Weaponry')) != 1:
        raise ValueError('No se pudieron resolver las dependencias del armamento completo.')
    roles = set().union(*(_place_roles(p, implicit_gunner=True) for p in weapons)) - {'driver'}
    return {'schema': 1, 'scope': 'complete', 'root': mount,
            'bones': sorted(names), 'weapon_places': [p.args[0] for p in weapons],
            'crew_roles': sorted(roles),
            'weapons': [{'place':p.args[0], 'definition':[n.render() for n in p.find_all('weapon')],
                         'operators':sorted(_place_roles(p,implicit_gunner=True))} for p in weapons],
            'sequences': [n.render() for n in model['document'].walk('sequence')],
            'volumes': sorted(n for n, v in model['volumes'].items() if v['bone'] in names),
            'joints': [n for n in sorted(names) if model['bones'][n]['node'].args[0].casefold() == 'revolute'],
            'passive_carriage': True, 'runtime_verified': False}


def resolve_complete(resources, template):
    from .compiler import _load_entity, _explicit_weapon_operators
    from .geometry import parse_model
    info, _, root = _load_entity(resources, template)
    _explicit_weapon_operators(resources, root)
    return complete_contract(info, root, parse_model(resources.read(info['model_path'])))
