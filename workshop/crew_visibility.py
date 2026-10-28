"""Per-seat internal representation using the native empty LinkBone contract.

No actor state, AI, crew assignment, weapon, sensor or boarding animation is
changed. The base game's cannon_crew.ext/crew_wheeled_cannon_closed uses an
empty bone for working crew inside an emplacement, with normal boarding links.
"""
import re

from .syntax import block, parse, quote


class CrewVisibilityError(ValueError):
    pass


def hidden_places(value):
    if value is None:
        return set()
    if (not isinstance(value, list) or len(value) > 128
            or any(not isinstance(p, str) or not p or len(p) > 100 for p in value)):
        raise CrewVisibilityError('La lista de tripulantes ocultos no es válida.')
    return set(value)


def seat_declarations(resources, root):
    """Read effective seat declarations in order, including macro wrappers.

    Reuse the compiler's include and textual macro authorities. Expansion is
    read-only and limited to macros that can emit a Placer/Boarder/include;
    damage, armor and unrelated macros stay untouched in the exported entity.
    The last explicit LinkBone, including an empty one, is authoritative as in
    the native closed-cannon macro. Indirect seats need a final local override.
    """
    from .compiler import _include_defines, _macro_arguments
    from .geometry import resolve_path
    definitions = _include_defines(resources, root)
    for path in ('properties/tank_crew.ext', 'properties/car_crew.ext', 'properties/cannon_crew.ext'):
        try:
            for node in parse(resources.read(path)).find_all('define'):
                if node.args: definitions.setdefault(node.args[0], node)
        except (KeyError, OSError): pass
    definitions.update({n.args[0]: n for n in root.find_all('define') if n.args})
    effects = {}

    def relevant(name, seen=()):
        if name in effects: return effects[name]
        if name in seen or name not in definitions: return False
        result = any(n.head.casefold() in ('placer', 'boarder', 'include')
                     or (n.opener == '(' and relevant(n.head, (*seen, name)))
                     for n in definitions[name].children)
        effects[name] = result
        return result

    places, boarders, indirect = [], [], set()

    def collect(document, base='', depth=0, calls=(), includes=(), inherited=False):
        if depth > 32:
            raise CrewVisibilityError('No se pudieron resolver las declaraciones de tripulación.')
        for node in document.children:
            if node.head.casefold() == 'placer':
                for place in node.find_all('place'):
                    places.append(place)
                    if inherited and place.args: indirect.add(place.args[0])
            elif node.head.casefold() == 'boarder':
                boarders.append(node)
            elif node.head.casefold() == 'include' and node.args and (base or node.args[0].startswith('/')):
                path = resolve_path(base, node.args[0])
                if path in includes:
                    raise CrewVisibilityError('No se pudieron resolver las declaraciones de tripulación.')
                try: included = parse(resources.read(path))
                except (KeyError, OSError): continue
                collect(included, path, depth+1, calls, (*includes, path), True)
            elif node.opener == '(' and relevant(node.head):
                if node.head in calls:
                    raise CrewVisibilityError('No se pudieron resolver las declaraciones de tripulación.')
                definition = definitions[node.head]
                body = ''.join(n.render() for n in definition.items[definition.items.index(definition.atoms[1])+1:])
                arguments = _macro_arguments(node)
                body = re.sub(r'%(\w+)', lambda m: arguments.get(m.group(1).casefold(), ''), body)
                collect(parse(body), base, depth+1, (*calls, node.head), includes, True)
    collect(root)
    return places, boarders, indirect


def apply_visibility(root, value=None, origins=None, diagnostics=None, resources=None):
    """Apply after role remapping and commander mounts, before serializing DEF.

    Keys are final native crew place IDs, stable for the same source recipe.
    All repeated declarations are patched: an inherited/open seat must not
    restore a visual link later in the document. Return the pre-change visual
    anchors as well, for preview/checkboxes without moving or deleting bones.
    """
    hidden = hidden_places(value)
    origins = origins or {}
    local_places = [p for pl in root.find_all('placer') for p in pl.find_all('place')]
    declarations, boarders, indirect = (seat_declarations(resources, root) if resources else
                                      (local_places, root.find_all('boarder'), set()))
    places = {}
    for seat in declarations:
        if seat.args: places.setdefault(seat.args[0], []).append(seat)
    rows = []
    for role, declarations in places.items():
        groups = [n.args[0].casefold() for p in declarations for n in p.find_all('group') if n.args]
        if not groups or groups[-1] != 'crew':
            continue
        links = [n for p in declarations for n in p.find_all('LinkBone')]
        bone = links[-1].args[0] if links and links[-1].args else ''
        routes = [n for b in boarders for n in b.find_all('link')
                  if len(n.args) >= 2 and n.args[1] == role]
        # The native internal-seat mechanism preserves existing boarding routes.
        # A mod with an unknown seat lifecycle needs its own verified contract.
        supported = bool(bone and any(n.find('forward') is not None and n.find('forward').args == ['putoff']
                                     and n.find('reverse') is not None and n.find('reverse').args == ['puton']
                                     for n in routes))
        row = {'place': role, 'bone': bone, 'native_internal': not bool(bone),
               'can_hide': supported, 'hidden': role in hidden,
               **origins.get(role, {'part': 'hull', 'source_place': role})}
        if role in hidden:
            if bone and not supported:
                raise CrewVisibilityError('No se pudo verificar el embarque de ese puesto.\n' + role)
            for seat in local_places:
                if not seat.args or seat.args[0] != role: continue
                for link in list(seat.find_all('LinkBone')):
                    seat.remove(link)
                # Native bone() expands inside quotes: the engine requires a
                # string even for an internal seat. A valueless block crashes.
                seat.append(block('{LinkBone ""}'))
            # Keep source macros/includes and their hatch/boarding behavior.
            # Every initialization declaration precedes the player's override,
            # even if a mod uses a visual macro we cannot fully introspect.
            root.append(block('{placer {place '+quote(role)+' {LinkBone ""}}}'))
        rows.append(row)
    unknown = hidden - {r['place'] for r in rows}
    if unknown:
        raise CrewVisibilityError('El puesto a ocultar no existe en este ensamblaje.\n' + ', '.join(sorted(unknown)))
    if hidden and diagnostics is not None:
        diagnostics.append({'code': 'crew_internal_representation', 'level': 'warning',
                            'places': sorted(hidden),
                            'message': 'Native internal crew representation; battle verification pending.'})
    return rows
