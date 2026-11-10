import json
import tempfile
import unittest
from pathlib import Path

from tests.test_extra_armor import resources, row
from workshop import custom_armor
from workshop.compiler import compile_assembly
from workshop.extra_armor import configuration, ArmorError
from workshop.geometry import parse_model, read_mesh
from workshop.syntax import parse


def plate(**changes):
    return {'asset': custom_armor.ASSET, 'parent': 'azimuth', 'position': [5, 10, 15], 'rotation': [0, 0, 90],
            'plate': {'width': 1000, 'height': 500, 'thickness': 40, **changes}}


class CustomArmorTests(unittest.TestCase):
    def compile(self, transform):
        transform = {'recipient_mount': 'azimuth', 'donor_mount': 'azimuth', 'commander_only': True, **transform}
        with tempfile.TemporaryDirectory() as tmp:
            result = compile_assembly(resources(), 'receiver', 'receiver', transform, tmp, 'mw_plate')
            folder = Path(tmp)/'resource/entity/-vehicle/workshop/mw_plate'
            root = parse((folder/'mw_plate.def').read_bytes()).find('game_entity')
            model = parse_model((folder/'mw_plate.mdl').read_bytes())
            recipe = json.loads((Path(tmp)/'assembly_mw_plate.json').read_text(encoding='utf8'))
            files = {p.name: p.read_bytes() for p in folder.iterdir()}
            return result, root, model, recipe, files

    def test_a_plate_is_as_deep_as_it_is_thick_and_protects_with_that_thickness(self):
        result, root, model, _, files = self.compile({'extra_armor': [plate()]})
        self.assertEqual(model['bones']['mw_armor_0_root']['parent'], 'azimuth')
        volume = model['volumes']['mw_armor_0_v0']
        self.assertEqual(volume['bone'], 'mw_armor_0_root')
        self.assertEqual([float(v) for v in volume['node'].find('box').args], [0.8, 20.0, 10.0])  # 40 x 1000 x 500 mm
        contract = next(v for v in root.find_all('volume') if v.args == ['mw_armor_0_v0'])
        self.assertEqual((contract.find('thickness').args, contract.find('material').args, contract.find('tags').args),
                         (['40'], ['steel'], ['armor']))
        self.assertFalse(contract.find('component'), 'a hit on the plate never damages the hull')
        mesh = next(name for name in files if name.startswith('cw_plate_') and name.endswith('.ply'))
        self.assertIn(mesh, [a for a in model['bones']['mw_armor_0_root']['assets']])
        read = read_mesh(files[mesh])
        self.assertEqual(len(read['indices']), 36)
        xs = read['positions'][0::3]; ys = read['positions'][1::3]; zs = read['positions'][2::3]
        self.assertEqual([round(max(a)-min(a), 3) for a in (xs, ys, zs)], [0.8, 20.0, 10.0])
        self.assertIn(custom_armor.PAINT+'.dds', files); self.assertIn(custom_armor.PAINT+'.mtl', files)
        self.assertEqual((round(max(read['uv'][0::2])-min(read['uv'][0::2]), 3), round(max(read['uv'][1::2])-min(read['uv'][1::2]), 3)), (1.0, 1.0),
                         'a 1 m side spans the paint once: it repeats per metre instead of stretching')
        from workshop.textures import decode_dds
        from workshop.plate_paint import texture_bytes
        width, height, pixels = decode_dds(texture_bytes())
        self.assertEqual((width, height, files[custom_armor.PAINT+'.dds']), (128, 128, texture_bytes()))
        colours = {tuple(pixels[i:i+3]) for i in range(0, len(pixels), 4)}
        self.assertGreater(len(colours), 200, 'worn steel, not a flat colour')
        self.assertLess(max(max(c) for c in colours), 140, 'dark')
        self.assertIn('mw_armor_0_v0', result['metadata']['state_profile']['allowed_damage_ids'])
        placed = result['metadata']['extra_armor'][0]
        self.assertEqual((placed['thickness'], placed['bones'], placed['custom']), ([40.0], ['mw_armor_0_root'], True))

    def test_plates_sit_beside_native_fittings_and_survive_reopening(self):
        transform = {'extra_armor': [row(), plate(), plate(thickness=80, width=600)]}
        result, _, model, recipe, files = self.compile(transform)
        self.assertEqual(sorted(v for v in model['volumes'] if v.startswith('mw_armor_')), ['mw_armor_0_v0', 'mw_armor_1_v0', 'mw_armor_2_v0'])
        self.assertEqual(len([n for n in files if n.startswith('cw_plate_') and n.endswith('.ply')]), 2, 'one mesh per size')
        again = self.compile(recipe['metadata']['transform'])[0]
        self.assertEqual(result['metadata']['file_hashes'], again['metadata']['file_hashes'])
        from workshop.editable_recipe import FIELDS
        self.assertIn('extra_armor', FIELDS)
        self.assertEqual(recipe['metadata']['transform']['extra_armor'][1]['plate'], {'width': 1000.0, 'height': 500.0, 'thickness': 40.0})

    def test_the_editor_gets_the_same_paint_and_repeat_as_the_built_plate(self):
        from workshop.extra_armor import preview
        from workshop.plate_paint import texture_bytes
        from workshop.textures import decode_dds
        shown = preview(resources(), custom_armor.ASSET)
        width, height, pixels = decode_dds(texture_bytes())
        self.assertEqual((shown['texture']['width'], shown['texture']['height'], shown['texture']['rgba']), (width, height, list(pixels)))
        self.assertEqual(shown['tile'], 20.0, 'one repeat per metre')

    def test_sizes_outside_the_limits_are_refused(self):
        for bad in ({'thickness': 0}, {'thickness': 501}, {'width': 10}, {'height': float('nan')}, {'width': True}, {'extra': 1}):
            with self.subTest(bad=bad), self.assertRaises(ArmorError):
                configuration(resources(), [plate(**bad)])
        with self.assertRaises(ArmorError):
            configuration(resources(), [{k: v for k, v in plate().items() if k != 'plate'}])


if __name__ == '__main__':
    unittest.main()
