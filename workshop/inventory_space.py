"""Room in a vehicle's inventory box for the weapons the editor adds.

A vehicle box is a grid ({size W H}): a tank's is 9 x 12 from
properties/tank_unarmed.ext, and the game silently drops what does not fit.
A Sherman's own load already fills it, so an added commander MG and a rider
M2 (7 x 3) were lost with their ammunition (Nr9T). Every added item gets its
own rows on top of the box the vehicle had.
"""
from .syntax import Trivia, block, parse


def _size(node):
    found = node.find('size') if node is not None else None
    try:
        return [int(float(v)) for v in found.args[:2]] if found is not None and len(found.args) >= 2 else None
    except ValueError:
        return None


def _inherited_size(resources, document, base='', seen=None, depth=0):
    """The box size an entity ends with through its includes (last one wins)."""
    from .compiler import _shared_include
    from .geometry import resolve_path
    seen = set() if seen is None else seen
    size = None
    if depth > 12:
        return None
    for node in document.children:
        head = node.head.casefold()
        if head == 'include' and node.args and (base or node.args[0].startswith('/')):
            path = resolve_path(base, node.args[0])
            if path.casefold() in seen:
                continue
            seen.add(path.casefold())
            try:
                size = _inherited_size(resources, _shared_include(resources, path), path, seen, depth + 1) or size
            except (KeyError, OSError, ValueError):
                continue
        elif head == 'extender' and node.args and node.args[0].casefold() == 'inventory':
            size = _size(node.find('box')) or size
    return size


def footprint(resources, identifier):
    """{inventory {size W H}} of a stuff item, through its {from} patterns."""
    from .registration import _stuff_definition, RegistrationError
    try:
        path = _stuff_definition(resources, identifier)
    except (RegistrationError, FileNotFoundError, KeyError, ValueError):
        return None
    for _ in range(8):
        try:
            document = parse(resources.read(path), lenient=True)
        except (KeyError, OSError, ValueError):
            return None
        root = document.find('from') or (document.children[0] if document.children else None)
        if root is None:
            return None
        size = _size(root.find('inventory'))
        if size:
            return size
        if root.head.casefold() != 'from' or not root.args or not root.args[0].startswith('pattern '):
            return None
        try:
            path = _stuff_definition(resources, root.args[0][len('pattern '):], from_path=path, pattern=True)
        except (RegistrationError, FileNotFoundError, KeyError, ValueError):
            return None
    return None


def make_room(resources, root, items):
    """Grow the box so the added items (rendered {item} blocks) fit."""
    inventory = next((n for n in root.find_all('extender') if n.args and n.args[0].casefold() == 'inventory'), None)
    box = inventory.find('box') if inventory is not None else None
    if box is None or not items:
        return None
    own = box.find('size')
    size = _size(box) or _inherited_size(resources, root)
    if size is None:
        return None  # a box without a size has no limit
    width, height = size
    for text in items:
        item = parse(text).children[0]
        identifier = item.args[0] if item.args else ''
        w, h = footprint(resources, identifier) or (1, 1)
        width, height = max(width, w), height + h
    if own is not None:
        box.remove(own)
    box.items[1:1] = [Trivia(' '), block('{size %d %d}' % (width, height))]
    return [width, height]
