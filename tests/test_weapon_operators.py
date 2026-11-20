"""One crewman can work several weapons; his old seat can then stay in reserve."""
import tempfile
import unittest
from pathlib import Path

from tests.test_compiler import FakeResources
from workshop.compiler import CompileError, compile_assembly
from workshop.resources import ResourceIndex
from workshop.syntax import parse
from workshop.weapon_operators import apply, configuration, listing


def vehicle(weaponry):
    return parse('{game_entity {Weaponry %s}}' % weaponry).find('game_entity')


class AssignTests(unittest.TestCase):
    def place(self, root, name):
        return next(p for p in root.find('Weaponry').find_all('place') if p.args[0] == name)

    def test_the_new_operator_takes_the_old_ones_share_of_the_reload(self):
        root = vehicle('{place "gun" {weapon "pak"}{gunner "gunner"}{charger "gunner charger" minWorkers 2}}'
                       '{place "rockets" {weapon "rpzb"}{gunner "gunner"}{charger "gunner"}}')
        changed, skipped = apply(root, {'rockets': 'gunner2', 'gone': 'gunner2'}, {'gunner', 'gunner2', 'charger'})
        self.assertEqual((changed, skipped), (['rockets'], ['gone']))
        rockets = self.place(root, 'rockets')
        self.assertEqual(rockets.find('gunner').args, ['gunner2'])
        self.assertEqual(rockets.find('charger').args, ['gunner2'])
        apply(root, {'gun': 'gunner2'}, {'gunner', 'gunner2', 'charger'})
        self.assertEqual(self.place(root, 'gun').find('charger').args, ['gunner2 charger', 'minWorkers', '2'])

    def test_a_weapon_nobody_named_a_loader_for_is_reloaded_by_its_operator(self):
        root = vehicle('{place "mgun" {weapon "mg34"}}')
        apply(root, {'mgun': 'driver'}, {'driver', 'gunner'})
        self.assertEqual(self.place(root, 'mgun').find('charger').args, ['driver'])
        root = vehicle('{place "mgun" {weapon "mg34"}}')
        apply(root, {'mgun': 'driver'}, {'driver', 'gunner', 'charger'})
        self.assertIsNone(self.place(root, 'mgun').find('charger'), 'the vehicle\'s loader keeps loading it')

    def test_a_weapon_left_without_its_crewman_goes_to_its_loader_first(self):
        from workshop.weapon_operators import cover
        root = vehicle('{place "gun" {weapon "pak"}{gunner "gunner"}{charger "charger"}}'
                       '{place "mgun" {weapon "mg34"}{gunner "gunner3"}}{place "rear" {weapon "dt"}{gunner "commander"}}')
        handed = cover(root, {'gunner', 'commander'}, {'gunner', 'gunner3', 'charger', 'commander', 'driver'}, 'gun')
        self.assertEqual(handed, {'gun': 'charger', 'rear': 'charger'}, 'the main weapon\'s new gunner takes the rear MG')
        self.assertEqual(self.place(root, 'gun').find('gunner').args, ['charger'])
        root = vehicle('{place "mgun" {weapon "mg34"}{gunner "gunner"}}')
        self.assertEqual(cover(root, {'gunner'}, {'gunner', 'driver'}), {'mgun': 'driver'}, 'the driver, when nobody else is left')
        with self.assertRaises(CompileError):
            cover(vehicle('{place "mgun" {weapon "mg34"}{gunner "gunner"}}'), {'gunner'}, {'gunner'})

    def test_names_are_checked(self):
        with self.assertRaises(CompileError):
            apply(vehicle('{place "gun" {weapon "pak"}}'), {'gun': 'nobody'}, {'gunner'})
        with self.assertRaises(CompileError):
            configuration({'gun': 3})
        self.assertIsNone(configuration({}))

    def test_listing_skips_vision_devices(self):
        root = vehicle('{place "gun" {weapon "pak"}{gunner "gunner2"}{charger "charger"}}'
                       '{place "binocular" {type "commander"}{weapon "commander_vision"}}{place "mgun" {weapon "mg34"}{type "coaxial"}}')
        self.assertEqual(listing(root), [
            {'place': 'gun', 'weapon': 'pak', 'type': '', 'gunner': 'gunner2', 'chargers': ['charger']},
            {'place': 'mgun', 'weapon': 'mg34', 'type': 'coaxial', 'gunner': 'gunner', 'chargers': []}])


class BuildTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        r = FakeResources()
        for name in ('hull', 'donor'):
            path = f'entity/{name}/{name}.def'
            r.files[path] = r.files[path].replace(b'{game_entity', b'{game_entity {placer {place "driver" {group "crew"}}{place "gunner" {group "crew"}}}')
        for name, raw in r.files.items():
            p = self.root/'game/resource'/name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        self.index = ResourceIndex(self.root/'game').build()
        self.addCleanup(self.index.close)
        # A roof turret with its own gunner, like a Wanze launcher on a Pz I turret.
        self.built = compile_assembly(self.index, 'hull', 'donor', {'recipient_mount': '@free:turret'}, self.root/'roof', 'mw_roof')
        self.view = self.index.with_overlay(self.built['output_dir'])
        self.addCleanup(self.view.close)
        root = parse((Path(self.built['output_dir'])/'resource'/self.built['definition_path']).read_bytes()).find('game_entity')
        self.weapons = {row['place']: row['gunner'] for row in listing(root)}
        self.assertEqual(len(set(self.weapons.values())), 2, 'each weapon starts with a gunner of its own')

    def test_the_turret_gunner_works_both_weapons_and_the_roof_seat_stays_behind(self):
        main = self.weapons['gun']
        roof_place, roof_gunner = next((p, g) for p, g in self.weapons.items() if p != 'gun')
        edited = compile_assembly(self.view, 'mw_roof', 'mw_roof', {'commander_only': True, 'crew_exclude': [roof_gunner],
                                  'weapon_operators': {roof_place: main}}, self.root/'edit', 'mw_edit')
        root = parse((Path(edited['output_dir'])/'resource'/edited['definition_path']).read_bytes()).find('game_entity')
        self.assertEqual({row['gunner'] for row in listing(root)}, {main})
        self.assertNotIn(roof_gunner, edited['metadata']['crew_roles'])
        self.assertEqual(edited['metadata']['crew_slots'], self.built['metadata']['crew_slots'] - 1)

    def test_any_crewman_can_stay_behind_and_his_weapon_goes_to_someone_aboard(self):
        # Aurus: "we should be able to remove any crewman". Nothing picked: the
        # main weapon's gunner takes the roof weapon.
        main = self.weapons['gun']
        roof_gunner = next(g for p, g in self.weapons.items() if p != 'gun')
        edited = compile_assembly(self.view, 'mw_roof', 'mw_roof', {'commander_only': True, 'crew_exclude': [roof_gunner]},
                                  self.root/'auto', 'mw_auto')
        root = parse((Path(edited['output_dir'])/'resource'/edited['definition_path']).read_bytes()).find('game_entity')
        self.assertEqual({row['gunner'] for row in listing(root)}, {main})
        # A weapon given to someone who then stays behind is handed over too.
        edited = compile_assembly(self.view, 'mw_roof', 'mw_roof', {'commander_only': True, 'crew_exclude': [main],
                                  'weapon_operators': {'gun': main}}, self.root/'main', 'mw_main')
        root = parse((Path(edited['output_dir'])/'resource'/edited['definition_path']).read_bytes()).find('game_entity')
        self.assertEqual({row['gunner'] for row in listing(root)}, {roof_gunner})
        with self.assertRaises(CompileError):
            compile_assembly(self.view, 'mw_roof', 'mw_roof', {'commander_only': True, 'crew_exclude': ['driver']},
                             self.root/'driver', 'mw_driver')


if __name__ == '__main__':
    unittest.main()
