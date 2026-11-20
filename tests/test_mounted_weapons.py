import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop.compiler import compile_assembly, CompileError
from workshop.geometry import parse_model, inverse, multiply
from workshop.pivots import pose_matrix
from workshop.syntax import block, parse, entity_root
from workshop.articulation import weapon_joints
from workshop import mounted_weapons
from workshop.weapon_operators import listing

try:
    from . import test_compiler as fixtures
except ImportError:
    import test_compiler as fixtures

WEAPONS = [{'id': 'mg42_belt', 'label': 'MG 42', 'entity': 'mg42', 'ammo_item': 'ammo mgun_ger belt', 'ammo_rounds': 300,
            'tags': ['mgun', 'mountable', 'weapon']},
           {'id': 'boys_mk1', 'label': 'Boys', 'entity': 'boys_mk1', 'ammo_item': 'ammo atrifle_eng ap', 'ammo_rounds': 15,
            'tags': ['at_rifle', 'mountable', 'rifle', 'weapon']},
           {'id': 'kar98k', 'label': 'Kar 98k', 'entity': 'kar98k', 'ammo_item': 'ammo rifle_ger', 'ammo_rounds': 15,
            'tags': ['mountable', 'rifle', 'weapon']}]


def catalog(index, *, item=None):
    return [w for w in WEAPONS if item is None or w['id'] == item]


class MountedWeaponTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.CompilerTests()
        self.addCleanup(fixture.doCleanups)
        self.resources = fixture._rider_resources()
        hull = 'entity/hull/hull.def'
        # A crewman of the hull's own (the bow gunner) beside the driver, and
        # a bow MG on two joints, as the Sherman's .30 cal.
        self.resources.files[hull] = self.resources.files[hull].replace(
            b'{placer {place "driver" {group "crew"}}}',
            b'{placer {place "driver" {group "crew"}} {place "gunner2" {group "crew"} {LinkBone "driver"}}}').replace(
            b'{Weaponry', b'{Weaponry {place "mgun1" {type "hull"} {weapon "browning_tank" filled} {foresight "bow_sight"}'
                          b' {gunner "gunner2"} {charger "gunner2"} ("abm_mgun") {basic}}')
        self.resources.files['entity/hull/hull.mdl'] = self.resources.files['entity/hull/hull.mdl'].replace(
            b'{bone "driver"}', b'{bone "driver"}{bone revolute "bow_yaw" {Position 20 0 2} {limits -20 20}'
                                b'{bone revolute "bow_pitch" {bone "bow_sight" {Position 5 0 0}}}}')
        self.resources.files['entity/hull/hull.def'] = self.resources.files[hull].replace(
            b'{item "ammo ap" 50}', b'{item "ammo ap" 50} {item "ammo mgun_usa belt" 500}')
        patcher = patch('workshop.commander_mg.weapon_catalog', side_effect=catalog)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, spec, extra=None):
        with tempfile.TemporaryDirectory() as temp:
            result = compile_assembly(self.resources, 'hull', 'hull',
                                      {'commander_only': True, 'mounted_weapons': spec, **(extra or {})}, temp, 'mw_mounted')
            out = Path(temp) / 'resource'
            root = entity_root(parse((out / result['definition_path']).read_bytes()))
            model = parse_model((out / result['model_path']).read_bytes())
        return result, root, model

    def test_a_weapon_anywhere_gets_its_own_bones_slot_ammo_and_crewman(self):
        row = {'parent': 'body', 'position': [10, -4, 6], 'rotation': [0, 0, 15], 'yaw': [-25, 25], 'pitch': [-8, 18],
               'link': {'position': [-5, 0, -1], 'rotation': [0, 0, 0]}, 'item': 'mg42_belt'}
        result, root, model = self.build({'added': [row]})
        mount = result['metadata']['mounted_weapons']['mounts'][0]
        self.assertEqual(result['metadata']['state_profile']['mounted_weapons'], result['metadata']['mounted_weapons'])
        self.assertEqual(mount['slot'], 'mgun2')
        self.assertIn(mount['slot'], result['metadata']['state_profile']['weapon_places'])
        text = ' '.join(root.render().split())
        # The Universal Carrier's own front slot: no {weapon}, the box's MG on it.
        self.assertIn('{weapon "mgun2" {mask "mountable"}}', text)
        self.assertIn('{item "mg42_belt" filled {user "mgun2"}}', text)
        self.assertIn('{item "ammo mgun_ger belt" 300}', text)
        self.assertIn('{place "mgun2" {type "hull"} {LinkBone "mw_weapon1_link"} {LinkAnimation "off"} {gunner "gunner2"} '
                      '{charger "gunner2"} ("abm_mgun") {rotate} {basic}}', text)
        # Traverse on the chosen part, elevation under it, the weapon's link under that.
        self.assertEqual(model['bones']['mw_weapon1_yaw']['parent'], 'body')
        self.assertEqual(model['bones']['mw_weapon1_pitch']['parent'], 'mw_weapon1_yaw')
        self.assertEqual(model['bones']['mw_weapon1_link']['parent'], 'mw_weapon1_pitch')
        local = multiply(inverse(model['bones']['body']['world']), model['bones']['mw_weapon1_yaw']['world'])
        for a, b in zip(sum(local, []), sum(pose_matrix(row), [])):
            self.assertAlmostEqual(a, b, places=6)
        joints = weapon_joints(model, root)
        self.assertEqual({j['bone'] for j in joints if 'mgun2' in j['weapons']}, {'mw_weapon1_yaw', 'mw_weapon1_pitch'})
        self.assertIsNotNone(model['bones']['mw_weapon1_yaw']['node'].find('terminator'))
        # A casemate's turning: {rotate} and a revolute root locked at 0 0.
        basis = model['bones']['basis']['node']
        self.assertEqual(basis.args[0], 'revolute')
        self.assertEqual(basis.find('limits').args, ['0', '0'])
        self.assertTrue(result['metadata']['mounted_weapons']['hull_locked'])
        # Worked by a crewman already aboard: the one who aims nothing yet.
        self.assertEqual(mount['operator'], 'gunner2', 'the bow gunner has one weapon, the driver is last')

    def test_who_works_each_weapon_picks_the_new_weapons_crewman(self):
        row = {'parent': 'turret', 'item': 'boys_mk1', 'type': 'aa', 'rotate': False}
        result, root, model = self.build({'added': [row]}, {'weapon_operators': {'mgun2': 'driver'}})
        place = next(p for p in root.find('Weaponry').find_all('place') if p.args == ['mgun2'])
        self.assertEqual(place.find('gunner').args, ['driver'])
        self.assertIsNone(place.find('rotate'))
        self.assertIsNotNone(place.find('ai_in_manual'))
        self.assertEqual(model['bones']['mw_weapon1_yaw']['parent'], 'turret')
        self.assertFalse(result['metadata']['mounted_weapons']['hull_locked'])
        # The Crew tool lists the slot with the weapon the box puts in it.
        row = next(r for r in listing(root) if r['place'] == 'mgun2')
        self.assertEqual((row['weapon'], row['gunner'], row.get('mountable')), ('boys_mk1', 'driver', True))

    def test_nobody_left_behind_works_the_new_weapon(self):
        with self.assertRaises(CompileError):
            self.build({'added': [{'parent': 'body', 'item': 'mg42_belt', 'operator': 'gunner2'}]},
                       {'crew_exclude': ['gunner2'], 'weapon_operators': {'mgun1': 'driver'}})

    def test_a_bow_mg_becomes_a_swappable_slot(self):
        spec = {'slots': [{'place': 'mgun1', 'item': 'mg42_belt', 'link': {'position': [1, 0, 0], 'rotation': [0, 0, 0]}}]}
        result, root, model = self.build(spec)
        place = next(p for p in root.find('Weaponry').find_all('place') if p.args == ['mgun1'])
        self.assertIsNone(place.find('weapon'))
        self.assertIsNone(place.find('foresight'))
        self.assertEqual(place.find('LinkBone').args, ['mw_slot_mgun1'])
        self.assertEqual(place.find('LinkAnimation').args, ['off'])
        self.assertEqual(place.find('gunner').args, ['gunner2'], 'the bow gunner keeps his weapon')
        self.assertEqual(model['bones']['mw_slot_mgun1']['parent'], 'bow_pitch')
        text = ' '.join(root.render().split())
        self.assertIn('{weapon "mgun1" {mask "mountable"}}', text)
        self.assertIn('{item "mg42_belt" filled {user "mgun1"}}', text)
        mount = result['metadata']['mounted_weapons']['mounts'][0]
        self.assertEqual((mount['converted'], mount['weapon'], mount['host']), (True, 'browning_tank', 'bow_pitch'))
        self.assertNotIn('rotate', [n.head for n in place.children], 'a converted slot keeps its own aiming')

    def test_the_main_gun_place_can_become_a_slot_and_a_native_slot_changes_its_weapon(self):
        spec = {'slots': [{'place': 'gun', 'item': 'boys_mk1'}]}
        self.resources.files['entity/hull/hull.mdl'] = self.resources.files['entity/hull/hull.mdl'].replace(
            b'{bone "gun"}', b'{bone revolute "gun_rot" {bone "gun"}}')
        result, root, model = self.build(spec)
        gun = next(p for p in root.find('Weaponry').find_all('place') if p.args == ['gun'])
        self.assertIsNone(gun.find('weapon'))
        self.assertEqual(model['bones']['mw_slot_gun']['parent'], 'gun_rot')
        # A slot the vehicle already has (the Carrier's) only changes its starting weapon.
        self.resources.files['entity/hull/hull.def'] = self.resources.files['entity/hull/hull.def'].replace(
            b'{extender "inventory" {box', b'{extender "inventory" {weapon "mgun1" {mask "mountable"}} {box').replace(
            b'{weapon "browning_tank" filled} {foresight "bow_sight"}', b'{LinkBone "bow_sight"} {LinkAnimation "off"}').replace(
            b'{item "ammo ap" 50}', b'{item "ammo ap" 50} {item "bren_mk1" filled {user "mgun1"}}')
        result, root, model = self.build({'slots': [{'place': 'mgun1', 'item': 'mg42_belt'}]})
        text = ' '.join(root.render().split()).replace(' }', '}')
        self.assertEqual(text.count('{weapon "mgun1" {mask "mountable"}}'), 1)
        self.assertIn('{item "bren_mk1" filled}', text, 'the old weapon stays as cargo')
        self.assertIn('{item "mg42_belt" filled {user "mgun1"}}', text)
        self.assertNotIn('mw_slot_mgun1', model['bones'])
        self.assertTrue(result['metadata']['mounted_weapons']['mounts'][0]['native_slot'])

    def test_a_place_gone_since_it_was_chosen_is_skipped(self):
        result, root, model = self.build({'slots': [{'place': 'mgun9', 'item': 'mg42_belt'}]})
        self.assertEqual(result['metadata']['mounted_weapons']['skipped'], ['mgun9'])
        self.assertTrue(any(d['code'] == 'mounted_weapon_slot_skipped' for d in result['diagnostics']))

    def test_values_are_checked(self):
        bad = [{'added': [{'parent': 'body', 'item': 'mg42_belt', 'yaw': [20, -20]}]},
               {'added': [{'parent': 'body', 'item': 'mg42_belt', 'type': 'coaxial'}]},
               {'added': [{'parent': '', 'item': 'mg42_belt'}]},
               {'added': [{'parent': 'body', 'item': 'pak38'}]},
               {'slots': [{'place': 'mgun1', 'item': 'mg42_belt'}, {'place': 'mgun1', 'item': 'boys_mk1'}]},
               {'added': [{'parent': 'body', 'item': 'mg42_belt'}] * (mounted_weapons.MAX_ADDED + 1)}]
        for spec in bad:
            with self.subTest(spec=spec), self.assertRaises(CompileError):
                mounted_weapons.configuration(spec, self.resources)
        self.assertIsNone(mounted_weapons.configuration({'added': [], 'slots': []}))

    def test_the_catalog_offers_machine_guns_then_anti_tank_rifles(self):
        self.assertEqual([w['id'] for w in mounted_weapons.catalog(self.resources)], ['mg42_belt', 'boys_mk1'])

    def test_slot_fire_reaches_the_weapon_in_it(self):
        record = block('{"mw_mounted"}')
        mounted_weapons.add_interactions(record, {'mounts': [{'slot': 'mgun2'}, {'slot': 'mgun1'}]})
        text = ' '.join(record.render().split()).replace(' }', '}')
        self.assertIn('{on fire "mgun2" {if effector "stuff weapon" {with_effector {call "fire"}}}}', text)
        self.assertIn('{on fire "mgun1"', text)

    def test_the_campaign_keeps_the_slots_inventory_profile(self):
        from workshop.campaign import _profile, CampaignError
        result, root, model = self.build({'added': [{'parent': 'body', 'item': 'mg42_belt'}]})
        profile = _profile(result['metadata']['state_profile'])
        self.assertEqual(profile['mounted_weapons']['mounts'][0]['slot'], 'mgun2')
        with self.assertRaises(CampaignError):
            _profile({'mounted_weapons': {'mounts': [{'slot': 'mgun2'}]}})

    def test_listing_shows_convertible_places_with_their_aiming_joint(self):
        from workshop.compiler import _load_entity
        _, _, root = _load_entity(self.resources, 'hull')
        model = parse_model(self.resources.files['entity/hull/hull.mdl'])
        rows = {r['place']: r for r in mounted_weapons.slot_candidates(root, model)}
        self.assertEqual((rows['mgun1']['host'], rows['mgun1']['mountable']), ('bow_pitch', False))
        self.assertIn('gun', rows)  # the main gun's place on its turret joint


if __name__ == '__main__':
    unittest.main()
