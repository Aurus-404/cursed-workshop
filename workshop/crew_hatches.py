"""Hatches of crew seats the workshop names.

The game opens and closes hatches by seat name. On spawn and whenever crew
gets in or out, tank-main.inc's "hatch" record calls "<seat>_hatch_open" or
"<seat>_hatch_close" for driver, gunner, gunner2, gunner4, commander, charger
and charger2, and each name means a seat of its own: "gunner2" is the AA
gunner standing in the hatch, so its handler winds the vehicle's "open" clip
to the end and plays "mgun_aa". Vehicle records (tank-specifics.inc) change
what those events do for their own seats.

A seat the workshop renames to keep two seats apart (a turret's "gunner"
becomes "gunner2" next to a roof turret's "gunner"), or adds as extra crew,
is not the seat its new name stands for. With that name it ran another
seat's hatch script: an occupied "gunner2" held the turret hatch open for
the whole battle. Such a seat gets no hatch script: the vehicle's record
overloads its events with empty ones, as native records switch off the ones
they do not use ({on "event_no_crew" overload}).

`renamed` maps each such seat of the built vehicle to the seat it was in its
own vehicle (None for an added crewman). It follows the seat through every
build step, so a design made of several steps and a vehicle built from
another workshop vehicle keep it.
"""
from __future__ import annotations

from .syntax import block, quote

SUFFIXES = ('_hatch_open', '_hatch_close')


def inherited(resources, template):
    """The renamed seats a workshop vehicle used as a part already has."""
    from .weapon_effects import _recipe
    try:
        metadata = _recipe(resources, template)
    except (FileNotFoundError, KeyError, OSError, ValueError, AttributeError):
        return {}
    value = (metadata or {}).get('renamed_crew')
    return dict(value) if isinstance(value, dict) else {}


def carry(previous, final_roles, *, chassis=None, module=None, module_native=(), added=()):
    """The renamed seats after one build step.

    previous: the chassis' own renamed seats. chassis: {old: new} names the
    step gave to chassis seats. module: {old: new} names it gave to the
    added module's seats; module_native: module seats that kept their own
    name. added: seats this step created (extra crew).
    """
    result = {}
    for name, origin in (previous or {}).items():
        result[(chassis or {}).get(name, name)] = origin
    for old, new in (chassis or {}).items():
        if old not in (previous or {}):
            result[new] = old
    for name in module_native:
        result.pop(name, None)  # a native seat of the new module now has this name
    for old, new in (module or {}).items():
        result[new] = old
    for name in added:
        result[name] = None
    return {name: origin for name, origin in sorted(result.items())
            if name in final_roles and name != origin}


def _declared_events(records, record):
    names = set()
    for node in [*records, record]:
        if node is None:
            continue
        for event in node.walk('on'):
            if event.args and event.args[0].endswith(SUFFIXES):
                names.add(event.args[0])
    return names


def neutralize(record, renamed, records=()):
    """Empty hatch events for every renamed seat whose name the game's hatch
    scripts (records: the parsed interaction records) or this vehicle's
    record act on. Returns the events written."""
    if record is None or not renamed:
        return []
    declared = _declared_events(records, record)
    written = []
    for name in sorted(renamed):
        events = [name + suffix for suffix in SUFFIXES]
        if not any(event in declared for event in events):
            continue
        for event in events:
            for old in [n for n in record.find_all('on') if n.args and n.args[0] == event]:
                record.remove(old)
            record.append(block('{on %s overload}' % quote(event)))
            written.append(event)
    return written
