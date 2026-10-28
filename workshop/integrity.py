"""Bone references a definition makes and which of them its model lacks.

The engine tolerates some missing references (many base-game vehicles name a
"shooter" or "foresight" bone they do not have), so a missing reference alone
is not an error. A generated vehicle must not add new ones: a reference that
was valid in its source vehicles and is missing after assembly comes from the
tool (a renamed or removed bone) and is the kind of mistake that has crashed
the game before ("Bone not found").
"""
from __future__ import annotations

from .syntax import bone_name

_REFERENCE_HEADS = {"linkbone", "foresight", "shooter"}


def bone_references(root) -> set[tuple[str, str]]:
    """(kind, bone) pairs a definition refers to."""
    found = set()
    for node in root.children:
        if node.head.casefold() == "bone":
            found.add(("bone-block", bone_name(node)))
    for node in root.walk():
        head = node.head.casefold()
        if head in _REFERENCE_HEADS and node.args and node.args[0]:
            found.add((head, node.args[0]))
        elif head == "visor":
            bone = node.find("bone")
            if bone is not None and bone.args:
                found.add(("visor-bone", bone.args[0]))
    return {(kind, name) for kind, name in found if name and not name.startswith("%")}


def dangling(root, model) -> set[tuple[str, str]]:
    """References to bones the model does not have (case-insensitive, as the engine)."""
    bones = {name.casefold() for name in model["bones"]}
    return {(kind, name) for kind, name in bone_references(root) if name.casefold() not in bones}


def introduced_dangling(output, sources, mapping=None) -> set[tuple[str, str]]:
    """Dangling references of a generated vehicle that none of its sources had.

    output and sources are (definition root, parsed model) pairs; mapping maps a
    source bone name to its renamed name in the output (bone_mapping).
    """
    tolerated = set()
    for root, model in sources:
        for kind, name in dangling(root, model):
            tolerated.add((kind, name.casefold()))
            if mapping and name in mapping:
                tolerated.add((kind, str(mapping[name]).casefold()))
    return {(kind, name) for kind, name in dangling(*output) if (kind, name.casefold()) not in tolerated}
