"""Model ownership shared by discovery, articulation and transplantation."""
import re


def mountable_slot(root):
    """Allocate a native numbered MG slot, including inherited/default users.

    Vehicle control and inventory dispatch use native gun/mgun slot families.
    Keep custom seat/bone identities separate from these engine slot names.
    """
    reserved = {'mgun', 'mgun0'}
    for node in root.walk():
        if node.head.casefold() in ('place', 'weapon', 'user') and node.args:
            name=node.args[0].casefold();reserved.add(name)
            match=re.fullmatch(r'mgun(\d+)',name)
            if match:reserved.add('mgun'+str(int(match[1])))
    number = 1
    while f'mgun{number}' in reserved: number += 1
    return f'mgun{number}'


def isolate_aiming(node, root):
    """A mounted weapon stops its IK chain at its own traverse joint."""
    from .syntax import block, bone_name
    joint=next(n for n in node.walk('bone') if bone_name(n)==root)
    if joint.find('terminator') is None:joint.append(block('{terminator}'))


def bone(model, name):
    matches = [n for n in model['bones'] if n.casefold() == name.casefold()]
    return matches[0] if len(matches) == 1 else None


def barrel_bones(place, model, *, prefix=None):
    count = place.find('Barrels')
    if count is None or not count.args:
        return []
    try:
        count = int(count.args[0])
    except ValueError:
        return []
    if count <= 0 or count > len(model['bones']):
        return []
    # GEM's native multibarrel contract: shot names 00, 01, ... use shooter00,
    # shooter01, ... . An explicit prefix travels with a renamed assembly.
    if prefix is None:
        reference = place.find('shooter')
        prefix = reference.args[0] if reference and reference.args else 'shooter'
    names = [bone(model, prefix + f'{i:02}') for i in range(count)]
    return names if names and all(names) else []


def reference_bones(place, model, head, name):
    """A shooter reference may identify a complete indexed family, not a bone."""
    if head.casefold()=='shooter':
        family=barrel_bones(place,model,prefix=name)
        if family:return family
    actual=bone(model,name)
    return [actual] if actual else []


def weapon_link(place, model):
    for head in ('LinkBone', 'bone', 'shooter', 'foresight'):
        for node in place.find_all(head):
            if node.args and (name := bone(model, node.args[0])):
                return name
    if place.args and (name := bone(model, place.args[0])):
        return name
    barrels = barrel_bones(place, model)
    if barrels:
        chains = []
        for name in barrels:
            chain = []
            while name in model['bones']:
                chain.append(name)
                name = model['bones'][name]['parent']
            chains.append(chain)
        return next((n for n in chains[0] if all(n in c for c in chains[1:])), None)
    return None


def bind_barrels(places, model, mapping, reserved):
    """Allocate a whole shooter family together, never one locator at a time."""
    from .syntax import block, quote
    reserved = set(reserved) | set(mapping.values())
    for place in places:
        names = barrel_bones(place, model)
        if not names:
            continue
        prefix_node = place.find('shooter')
        prefix = prefix_node.args[0] if prefix_node and prefix_node.args else 'shooter'
        emitted = [mapping.get(n, n) for n in names]
        coherent = emitted[0][:-2]
        if emitted == [coherent + f'{i:02}' for i in range(len(names))]:
            prefix = coherent
        else:
            serial = 1
            candidate = 'module_' + prefix
            while any(candidate + f'{i:02}' in reserved for i in range(len(names))):
                serial += 1
                candidate = f'module{serial}_' + prefix
            for i, name in enumerate(names):
                mapping[name] = candidate + f'{i:02}'
                reserved.add(mapping[name])
            prefix = candidate
        # A place rename must not switch its implicit shooter family.
        if prefix_node is None:
            place.append(block('{shooter ' + quote(prefix) + '}'))
        else:
            prefix_node.atoms[1].set(prefix)
