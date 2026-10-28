import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.test_campaign import SCENE, make_save
from workshop.campaign import (load_campaign, plan_edit, apply_plan,
                               repair_weaponry_state, build_save_copy)
from workshop.syntax import block, parse
from workshop.weapon_state import reconcile_weaponry, validate_weaponry, preserve_unchanged_loadout


class WeaponStateTests(unittest.TestCase):
    def test_edit_reset_covers_native_unnamed_and_named_states_and_fallback(self):
        for keep_names in (True, False):
            for state in ('{State {Bullets 19}}', '{Current "removed"}{State "removed" {Bullets 19}}'):
                with self.subTest(keep_names=keep_names, state=state), tempfile.TemporaryDirectory() as tmp:
                    path = make_save(Path(tmp)/'campaign.sav', SCENE.replace('{State {Bullets 7}}', state))
                    source = path.read_bytes()
                    snapshot = load_campaign(path)
                    profile = {'weaponry_policy': 'reset', 'weapon_places': [], 'inventory_policy': 'recipient'}
                    plan = plan_edit(snapshot, '0x9000', 'mw_unarmed', 2,
                                     state_profile=profile, keep_names=keep_names)
                    after = parse(apply_plan(snapshot, plan)['campaign.scn']).find('campaign')
                    entity = next(e for e in after.find_all('Entity') if e.args[0] == 'mw_unarmed')
                    self.assertFalse(entity.find_all('Weaponry'))
                    self.assertIn('{Remain 76.4}', entity.render())
                    self.assertIn('{HealthBroken 42}', entity.render())
                    self.assertEqual(path.read_bytes(), source)

    def test_slot_contract_preserves_compatible_loaded_ammunition_and_unknown_fields(self):
        entity = block('{Entity "any" 0x1 {Weaponry {Current "gun"}{State "gun" {Bullets 2}}'
                       '{State "removed" {Bullets 19}}{CustomData 42}}}')
        profile = {'weapon_places': ['gun']}
        reconcile_weaponry(entity, profile)
        validate_weaponry(entity, profile)
        self.assertIn('{Bullets 2}', entity.render())
        self.assertIn('{CustomData 42}', entity.render())
        self.assertNotIn('removed', entity.render())
        original = entity.render()
        reconcile_weaponry(entity, profile)
        self.assertEqual(original, entity.render())

    def test_removed_slot_cannot_rebind_an_implicit_state_to_another_gun(self):
        entity = block('{Entity "any" 0x1 {Weaponry {State {Bullets 19}}{State "gun" {Bullets 3}}}}')
        reconcile_weaponry(entity, {'weaponry_policy': 'reset', 'retired_inventory_users': ['coax'], 'weapon_places': ['gun']})
        self.assertNotIn('19', entity.render())
        self.assertFalse(entity.find_all('Weaponry'))

    def test_placement_edits_preserve_loaded_rounds_but_weapon_changes_reset(self):
        original = block('{game_entity {Weaponry {place "gun" {weapon "old"}}}}')
        for weapon, policy in (('old', 'recipient'), ('new', 'reset')):
            profile = {'weaponry_policy': 'reset', 'weapon_places': ['gun']}
            emitted = block('{game_entity {Weaponry {place "gun" {weapon "'+weapon+'"}}}}')
            preserve_unchanged_loadout(profile, original, emitted)
            self.assertEqual(profile['weaponry_policy'], policy)

    def test_publication_guard_rejects_unsanitized_records(self):
        for profile in ({'weaponry_policy': 'reset'}, {'weapon_places': []}, {'weapon_places': ['other']}):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                validate_weaponry(block('{Entity "any" 0x1 {Weaponry {State "gone" {Bullets 19}}}}'), profile)

    def test_legacy_repair_is_definition_based_lossless_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_save(Path(tmp)/'campaign.sav')
            snapshot = load_campaign(path)
            replacements, changed = repair_weaponry_state(snapshot, {'tank_hull': [], 'wheeled_donor': ['gun']})
            self.assertEqual(changed, [{'template': 'tank_hull', 'eid': '0x9000'}])
            before = snapshot._scene.find('campaign')
            after = parse(replacements['campaign.scn']).find('campaign')
            for old, new in zip(before.children, after.children):
                if old.head == 'Entity' and old.args[0] == 'tank_hull':
                    old = old.clone()
                    for w in old.find_all('Weaponry'):
                        old.remove(w)
                self.assertEqual(old.render(), new.render())
            repaired = Path(tmp)/'repaired.sav'
            repaired.write_bytes(build_save_copy(snapshot, replacements))
            with zipfile.ZipFile(path) as a, zipfile.ZipFile(repaired) as b:
                for name in a.namelist():
                    if name != 'campaign.scn':
                        self.assertEqual(a.read(name), b.read(name))
            _, again = repair_weaponry_state(load_campaign(repaired), {'tank_hull': []})
            self.assertEqual(again, [])


if __name__ == '__main__':
    unittest.main()
