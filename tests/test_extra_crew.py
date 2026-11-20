import tempfile
import unittest
from pathlib import Path

from workshop.compiler import compile_assembly, CompileError
from workshop.extra_crew import configuration, reload_factors
from workshop.geometry import parse_model, point
from tests.test_compiler import FakeResources


def _crewed_resources():
    """The fixture hull with a gunner, a loader and a commander who board
    through a hatch and may swap seats, as tank_crew.ext writes them."""
    resources = FakeResources()
    hull = 'entity/hull/hull.def'
    resources.files['entity/hull/hull.mdl'] = resources.files['entity/hull/hull.mdl'].replace(
        b'{bone "driver"}', b'{bone "driver"}{bone "emit1"}{bone "commander"}').replace(
        b'{bone "gunner"}', b'{bone "gunner"}{bone "charger"}')
    resources.files[hull] = resources.files[hull].replace(b'{mass 9000}',
        b'{placer {place "driver" {group "crew"} {LinkBone "driver"}} {place "gunner" {group "crew"} {LinkBone "gunner"}}'
        b' {place "charger" {group "crew"} {LinkBone "charger"} {sealable}} {place "commander" {group "crew"} {LinkBone "commander"}}}'
        b'{boarder {door "emit1"} {anm "charger" {forward {end "pose_stand"}}}'
        b' {link "emit1" "driver"} {link "emit1" "gunner"} {link "emit1" "charger" {anm "charger"}} {link "emit1" "commander"}'
        b' {link "charger" "gunner"} {link "commander" "driver"}}'
        b'{mass 9000}').replace(b'{gunner "gunner"}', b'{gunner "gunner"} {charger "charger"}')
    return resources


class ExtraCrewTests(unittest.TestCase):
    def build(self, spec, name='mw_crew'):
        resources = _crewed_resources()
        with tempfile.TemporaryDirectory() as temp:
            result = compile_assembly(resources, 'hull', 'hull', {'commander_only': True, 'extra_crew': spec}, temp, name)
            out = Path(temp) / 'resource'
            text = ' '.join((out / result['definition_path']).read_text(encoding='utf-8').split())
            model = parse_model((out / result['model_path']).read_bytes())
        return result, text, model

    def test_extra_loaders_join_the_gun_and_count_as_crew(self):
        base, _, _ = self.build(None, 'mw_base')
        spec = {'reload_bonus': 20, 'added': [
            {'role': 'charger', 'parent': 'turret', 'position': [3, 0, 0], 'rotation': [0, 0, 0]},
            {'role': 'charger', 'parent': 'turret', 'position': [-3, 0, 0], 'rotation': [0, 0, 0]},
            {'role': 'commander', 'parent': 'body', 'position': [0, 2, 4], 'rotation': [0, 0, 90]}]}
        result, text, model = self.build(spec)
        metadata = result['metadata']
        self.assertEqual([r['place'] for r in metadata['extra_crew']['added']], ['charger2', 'charger3', 'commander2'])
        self.assertEqual(metadata['extra_crew']['loaders'], {'gun': 2})
        self.assertEqual(metadata['crew_slots'], base['metadata']['crew_slots'] + 3)
        self.assertTrue({'charger2', 'charger3', 'commander2'} <= set(metadata['crew_roles']))
        # The gun still fires with its one native loader if the extra ones fall.
        self.assertIn('{charger "charger charger2 charger3" minWorkers 1}', text)
        # Each one copies its native seat: group, hatch state, boarding and swaps.
        self.assertIn('{place "charger2" {group "crew"} {sealable} {LinkBone "mw_crew1_pos"}', text)
        self.assertIn('{anm "charger2" {forward {end "pose_stand"}}}', text)
        self.assertIn('{link "emit1" "charger2" {anm "charger2"}}', text)
        self.assertIn('{link "charger2" "gunner"}', text)
        self.assertIn('{link "commander2" "driver"}', text)
        self.assertIn('{place "commander2" {group "crew"} {LinkBone "mw_crew3_pos"}', text)
        self.assertEqual(model['bones']['mw_crew2_pos']['parent'], 'turret')
        self.assertEqual(model['bones']['mw_crew3_pos']['parent'], 'body')
        self.assertEqual([round(v, 3) for v in point(model['bones']['mw_crew1_pos']['world'], [0, 0, 0])], [3.0, 0.0, 10.0])
        self.assertEqual(reload_factors(metadata['extra_crew']), {'gun': 0.6})

    def test_a_loader_needs_a_weapon_on_its_part_and_values_are_checked(self):
        with self.assertRaisesRegex(CompileError, 'arma'):
            self.build({'added': [{'role': 'charger', 'parent': 'driver'}]})
        with self.assertRaisesRegex(CompileError, 'parte elegida'):
            self.build({'added': [{'role': 'gunner', 'parent': 'nowhere'}]})
        with self.assertRaisesRegex(CompileError, 'puesto'):
            configuration({'added': [{'role': 'passenger', 'parent': 'body'}]})
        with self.assertRaisesRegex(CompileError, '50'):
            configuration({'added': [{'role': 'charger', 'parent': 'turret'}], 'reload_bonus': 80})
        self.assertIsNone(configuration({'added': [], 'reload_bonus': 10}))

    def test_reload_never_drops_below_a_quarter(self):
        self.assertEqual(reload_factors({'reload_bonus': 30, 'loaders': {'gun': 5}}), {'gun': 0.25})
        self.assertEqual(reload_factors({'reload_bonus': 0, 'loaders': {'gun': 2}}), {})


if __name__ == '__main__':
    unittest.main()
