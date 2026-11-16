import unittest

from workshop.mobility import read, apply, validate
from workshop.syntax import parse


TANK = '''{game_entity {Chassis ("tank_trace" fx("tracks_mid"))
  ("mobility_tank" speed(40) ;-- comment
     reverse(8) traverse(30) weight(23.9) power(265) track(5) fuel(310) type(fuel) range(150))}}'''


class MobilityTests(unittest.TestCase):
    def test_native_gear_formula_stays_strictly_ascending_at_boundary_and_beyond(self):
        from types import SimpleNamespace
        from workshop.registration import _expand_macros
        from workshop.compiler import _include_defines
        from workshop.weapon_attributes import evaluate
        from workshop.syntax import Atom, Node
        native=b'(define "mobility_tank" {Locomotion {Gears 0.5 0.65 1}{RearGears 0.05 (/ %reverse %speed)}{MaxSpeed %speed}})'
        resources=SimpleNamespace(read=lambda path:native)
        for speed,reverse,expected in [(100,5,[.05]),(200,1,[.005]),(90,5,[.05,5/90]),(40,8,[.05,.2])]:
            with self.subTest(speed=speed,reverse=reverse):
                doc=parse(TANK.replace('{game_entity','{game_entity (include "/mobility.inc")'))
                root=doc.find('game_entity');diagnostics=[]
                apply(root,{'speed':speed,'reverse':reverse},diagnostics,resources=resources,document=doc)
                definitions=_include_defines(resources,root)
                definitions.update({n.args[0]:n for n in doc.find_all('define')})
                expanded=_expand_macros(root,definitions)
                rear=[n for n in expanded.walk('RearGears')][-1]
                values=[evaluate(v) for v in rear.items[1:] if isinstance(v,(Atom,Node))]
                self.assertEqual(len(values),len(expected))
                for actual,wanted in zip(values,expected):self.assertAlmostEqual(actual,wanted)
                self.assertTrue(all(b>a for a,b in zip(values,values[1:])))
                self.assertEqual(read(root)['values']['speed'],speed)
                self.assertEqual(resources.read('mobility.inc'),native)
                # Editing a previously corrected vehicle recalculates the ratios,
                # including restoring native lower gears when reachable again.
                apply(root,{'speed':40,'reverse':8},resources=resources,document=doc)
                self.assertFalse(doc.find_all('define'))
                self.assertNotIn('cw_mobility_gears',root.render())

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
