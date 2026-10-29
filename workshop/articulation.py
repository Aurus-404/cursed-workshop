"""Weapon-connected revolute joints and explicit recipe limit overrides."""
import math
from .geometry import identity, multiply
from .syntax import block, bone_name, quote


def _evaluate(item):
    """A number written as the game's prefix arithmetic: 12, (+ 4 (/ 25 3))."""
    from .syntax import Node
    if not isinstance(item, Node):
        return float(item.value if hasattr(item, 'value') else item)
    values = [_evaluate(v) for v in item.items[1:] if getattr(v, 'value', None) is not None or isinstance(v, Node)]
    operator = item.head
    if operator == '+': return sum(values)
    if operator == '*':
        result = 1.0
        for v in values: result *= v
        return result
    if operator == '-': return values[0] - sum(values[1:]) if len(values) > 1 else -values[0]
    if operator == '/':
        result = values[0]
        for v in values[1:]: result /= v
        return result
    raise ValueError('Unknown arithmetic ' + operator)


def _traverse_speed(*nodes, defines=None):
    """Degrees per second a joint turns ({speed2}), also when a macro sets it
    (("turret_heavy" power_traverse(9)): 4 + 9/3). None when unknown."""
    from .registration import _expand_macros
    for node in nodes:
        if node is None:
            continue
        if defines:
            try: node = _expand_macros(node, defines)
            except Exception: pass
        found = [n for n in node.children if n.head.casefold() == 'speed2']
        if found:
            item = next((i for i in found[-1].items[1:] if getattr(i, 'value', None) is not None or hasattr(i, 'items')), None)
            try:
                value = _evaluate(item)
                if math.isfinite(value) and value > 0: return value
            except (ValueError, TypeError, ZeroDivisionError, IndexError):
                pass
    return None


def weapon_joints(model, root, adjustment=None, visible=None, defines=None):
    joints = {}
    definitions = {bone_name(node):node for node in root.find_all('bone') if bone_name(node)}
    driven = {name for name,node in definitions.items()
              if any(child.head.casefold() in ('limits','speed','speed2') for child in node.children)}
    for weaponry in root.find_all('Weaponry'):
        for place in weaponry.find_all('place'):
            if not place.args:
                continue
            if place.find('type') and place.find('type').args == ['commander']:
                continue  # Binocular/vision pseudo-weapon, not a gun pivot.
            link = place.find('LinkBone')
            name = link.args[0] if link and link.args else place.args[0]
            if not link and name not in model['bones']:
                # Same verified ownership references used by compiler._selected_places:
                # multi-turret MGs often name only their barrel's foresight locator.
                name = next((node.args[0] for head in ('bone','foresight','shooter')
                             for node in place.find_all(head) if node.args and node.args[0] in model['bones']),None)
            # Only actual weapon paths, never hatch, suspension or wheel joints.
            if not place.find('weapon') and not (place.find('type') and place.find('type').args == ['aa']):
                continue
            linked = name
            while name in model['bones']:
                bone = model['bones'][name]
                structural_root = bone['parent'] is None and name != linked and name not in driven
                if not structural_root and bone['node'].args[0].casefold() == 'revolute' and (visible is None or name in visible):
                    if name not in joints:
                        limits = definitions[name].find('limits') if name in definitions else None
                        if limits is None: limits = bone['node'].find('limits')
                        values = [float(v) for v in limits.args] if limits else [-180., 180.]
                        if len(values) != 2 or not all(math.isfinite(v) for v in values):
                            break
                        joints[name] = {'bone': name, 'parent': bone['parent'], 'limits': values,
                                        'speed': _traverse_speed(definitions.get(name), bone['node'], defines=defines),
                                        'matrix': multiply(adjustment or identity(), bone['world']), 'weapons': []}
                    joints[name]['weapons'].append(place.args[0])
                name = bone['parent']
    return list(joints.values())


def apply_limits(model, root, spec, groups):
    """Write limit overrides; returns the joints skipped because the vehicle no
    longer has them or they no longer move a weapon (a limit set before its
    turret or weapon was replaced: Frenchies, M4A2 + T-34-85 turret). Limits are
    never written to a joint that moves no weapon; invalid values still fail."""
    if spec is None:
        return []
    skipped = []
    if not isinstance(spec, dict) or set(spec) - set(groups):
        raise ValueError('Los límites de articulación no son válidos.')
    allowed = {joint['bone'] for joint in weapon_joints(model, root)}
    for part, values in spec.items():
        if not isinstance(values, dict) or len(values) > 64:
            raise ValueError('Los límites de articulación no son válidos.')
        for source, limits in values.items():
            target = groups[part].get(source)
            # [minimum, maximum] or [minimum, maximum, degrees per second].
            speed = limits[2] if isinstance(limits, (list, tuple)) and len(limits) == 3 else None
            if (not isinstance(limits, (list, tuple)) or len(limits) not in (2, 3)
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                           or not -180 <= v <= 180 for v in limits[:2]) or limits[0] > limits[1]):
                raise ValueError('Usá límites entre -180 y 180 grados, con el mínimo primero.')
            if speed is not None and (isinstance(speed, bool) or not isinstance(speed, (int, float))
                                      or not math.isfinite(speed) or not 0 < speed <= 3600):
                raise ValueError('La velocidad de giro tiene que ser mayor que 0 grados por segundo.')
            limits = list(limits[:2])
            if target is None or target not in model['bones'] or target not in allowed:
                skipped.append(source)
                continue
            node = model['bones'][target]['node']
            for old in node.find_all('limits'):
                node.remove(old)
            node.append(block('{limits %s %s}' % tuple(limits)))
            # A .def bone override is read after the model/global properties.
            # Update it too; otherwise an existing native override wins in-game.
            definitions = [n for n in root.find_all('bone') if bone_name(n) == target]
            if not definitions:
                definitions = [block('{bone '+quote(target)+'}')]
                root.append(definitions[0])
            for definition in definitions:
                for old in definition.find_all('limits'): definition.remove(old)
                definition.append(block('{limits %s %s}' % tuple(limits)))
                if speed is not None:
                    # Read last, it wins over a traverse macro of the same bone.
                    for old in definition.find_all('speed2'): definition.remove(old)
                    definition.append(block('{speed2 %s}' % speed))
            if speed is not None:
                for old in node.find_all('speed2'): node.remove(old)
                node.append(block('{speed2 %s}' % speed))
    return skipped


def skipped_limits_diagnostic(skipped):
    return {'code': 'joint_limits_skipped', 'level': 'warning', 'joints': skipped,
            'message': 'Limits set on joints the vehicle no longer has, or that no longer move a weapon, were left out: ' + ', '.join(skipped)}
