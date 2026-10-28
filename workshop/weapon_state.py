"""Persisted weapon state must describe the emitted loadout, not removed guns."""


def weapon_places(root):
    return sorted({p.args[0] for w in root.find_all('Weaponry')
                   for p in w.find_all('place') if p.args})


def preserve_unchanged_loadout(profile, original, emitted):
    """A placement-only edit keeps rounds already loaded in unchanged weapons."""
    def signature(root):
        return sorted((p.args[0], tuple(p.find('weapon').args[:1]) if p.find('weapon') else ())
                      for w in root.find_all('Weaponry') for p in w.find_all('place') if p.args)
    if signature(original) == signature(emitted):
        profile['weaponry_policy'] = 'recipient'


def reconcile_weaponry(entity, profile):
    """Use the same contract for edits, drafts, assembly and legacy repair.

    Native single-weapon States may have no slot argument. They cannot be
    matched by name after removal. Other vehicle and soldier state is untouched.
    """
    allowed = set(profile['weapon_places']) if 'weapon_places' in profile else None
    retired = set(profile.get('retired_inventory_users') or [])
    mount = profile.get('commander_mg') or {}
    retired.update(mount.get('removed_weapon_places') or [])
    if mount.get('previous_slot') and mount.get('previous_slot') != mount.get('slot'):
        retired.add(mount['previous_slot'])
    for weaponry in list(entity.find_all('Weaponry')):
        if profile.get('weaponry_policy') == 'reset' or allowed == set():
            entity.remove(weaponry)
            continue
        for state in list(weaponry.children):
            if state.head.casefold() not in ('current', 'state'):
                continue
            if ((state.args and (state.args[0] in retired or
                                (allowed is not None and state.args[0] not in allowed)))):
                weaponry.remove(state)
        if not weaponry.children:
            entity.remove(weaponry)


def validate_weaponry(entity, profile):
    """Independent publication guard; do not rely only on cleanup."""
    weapons = entity.find_all('Weaponry')
    if weapons and (profile.get('weaponry_policy') == 'reset' or profile.get('weapon_places') == []):
        raise ValueError('Persisted weapon state remains after the loadout was removed or reset')
    if 'weapon_places' in profile:
        allowed = set(profile['weapon_places'])
        for weaponry in weapons:
            for state in weaponry.children:
                if state.head.casefold() in ('current', 'state') and state.args and state.args[0] not in allowed:
                    raise ValueError('Persisted weapon state refers to a missing weapon slot: ' + state.args[0])
