"""Pieces of the vehicle's own model left out of the built vehicle.

The editor's viewport list ("Piezas de la vista") can mark a bone's meshes as
removed for the game: a toolbox, spare track links, a stowage bin. Only what is
drawn goes. The bone itself stays, because animations, crew, sights and
attached fittings may still hang from it, and its volumes keep armour,
collisions and damage exactly as in the source vehicle.

The names are the bones of the finished vehicle, as the editor draws it: the
chassis of a design (with its saved turrets), or a native turret being moved.
They are applied once, to the compiled output, like edited weapon attributes;
the steps that build a design never see them.
"""
import posixpath
from pathlib import Path

from .syntax import parse, bone_name

LIMIT = 512
NAME_LENGTH = 160
INVALID = 'La lista de piezas quitadas del vehículo no es válida.'


def configuration(value):
    """The validated bone names, in order and without repeats ([] for none)."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > LIMIT:
        raise ValueError(INVALID)
    names = []
    for name in value:
        if not isinstance(name, str) or not name.strip() or len(name) > NAME_LENGTH:
            raise ValueError(INVALID)
        if name not in names:
            names.append(name)
    return names


def strip(document, names, suffix=None):
    """Drop the drawn meshes ({VolumeView}, {LODView}) of every bone so named.

    A destroyed state may carry the same bone with its state suffix
    ("detail1_x"); it is used only when the plain name is absent. Repeated
    locator names are all cleared: the editor shows them as one piece.
    Returns the requested names that exist in this model."""
    skeleton = document.find('skeleton')
    if skeleton is None:
        return set()
    bones = list(skeleton.walk('bone'))
    present = {bone_name(node) for node in bones}
    targets = {}
    for name in names:
        if name in present:
            targets[name] = name
        elif suffix and name + '_' + suffix in present:
            targets[name + '_' + suffix] = name
    for node in bones:
        if bone_name(node) not in targets:
            continue
        for view in list(node.children):
            if view.head.casefold() in ('volumeview', 'lodview'):
                node.remove(view)
    return set(targets.values())


def apply(result, value):
    """Remove the chosen pieces from the compiled model and its wrecks."""
    names = configuration(value)
    metadata = result.setdefault('metadata', {})
    if not names:
        return result
    from .components import _save
    output = Path(result['output_dir'])
    model_path = result['model_path']
    files, found = {}, set()
    document = parse((output / 'resource' / model_path).read_bytes())
    found |= strip(document, names)
    files[model_path] = document.render().encode('utf8')
    folder, stem = posixpath.dirname(model_path), posixpath.splitext(posixpath.basename(model_path))[0]
    for tag in ('x', 'xx'):
        wreck = posixpath.join(folder, f'{stem}_{tag}.mdl')
        source = output / 'resource' / wreck
        if not source.is_file():
            continue
        state = parse(source.read_bytes())
        strip(state, names, tag)
        files[wreck] = state.render().encode('utf8')
    skipped = [name for name in names if name not in found]
    kept = [name for name in names if name in found]
    diagnostics = result.setdefault('diagnostics', [])
    if kept:
        diagnostics.append({'code': 'removed_meshes', 'level': 'info', 'bones': kept,
                            'message': 'Pieces left out of the built vehicle (only what is drawn; armour unchanged): ' + ', '.join(kept)})
    if skipped:
        diagnostics.append({'code': 'removed_meshes_skipped', 'level': 'warning', 'bones': skipped,
                            'message': 'Pieces marked as removed that this vehicle no longer has were left out: ' + ', '.join(skipped)})
    metadata['removed_meshes'] = {'bones': kept, 'skipped': skipped}
    # The recipe keeps the choice: editing or updating the vehicle builds it again.
    metadata.setdefault('transform', {})['removed_meshes'] = names
    return _save(result, files)
