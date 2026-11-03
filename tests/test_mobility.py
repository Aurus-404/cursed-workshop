import unittest

from workshop.mobility import read, apply, validate
from workshop.syntax import parse


TANK = '''{game_entity {Chassis ("tank_trace" fx("tracks_mid"))
  ("mobility_tank" speed(40) ;-- comment
     reverse(8) traverse(30) weight(23.9) power(265) track(5) fuel(310) type(fuel) range(150))}}'''


class MobilityTests(unittest.TestCase):
    def root(self, text=TANK):
        return parse(text).find('game_entity')

    def test_reads_the_native_mobility_macro_values(self):
        self.assertEqual(read(self.root()), {'macro': 'mobility_tank', 'values': {
            'speed': 40.0, 'reverse': 8.0, 'traverse': 30.0, 'power': 265.0, 'track': 5.0, 'fuel': 310.0, 'range': 150.0}})
        self.assertIsNone(read(self.root('{game_entity {Chassis ("tank_trace")}}')), 'inherited mobility is not editable')
        self.assertIsNone(read(self.root('{game_entity}')))

    def test_writes_only_the_chosen_values_and_keeps_weight_and_layout(self):
        root = self.root()
        diagnostics = []
        self.assertEqual(apply(root, {'speed': 65, 'traverse': 45.5, 'speedatmaxturn': 20}, diagnostics), ['speedatmaxturn'])
        self.assertEqual(read(root)['values']['speed'], 65.0)
        self.assertEqual(read(root)['values']['traverse'], 45.5)
        text = root.render()
        self.assertIn('speed(65) ;-- comment', text)
        self.assertIn('weight(23.9)', text, 'weight stays native')
        self.assertIn('type(fuel)', text)
        self.assertEqual(diagnostics[0]['code'], 'mobility_skipped', 'a parameter the chassis lacks is never invented')

    def test_rejects_values_the_game_formulas_cannot_use(self):
        for spec in ({'speed': 0}, {'speed': 999}, {'weight': 10}, {'speed': True}, {'speed': float('nan')}, ['speed']):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                validate(spec)
        validate(None)
        validate({})


if __name__ == '__main__':
    unittest.main()
