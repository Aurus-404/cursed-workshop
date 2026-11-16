import json
import math
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
    def test_retired_bolted_finishes_stay_readable_but_are_not_offered(self):
        from workshop.armor_paints import catalog, RETIRED
        offered={p['id'] for p in catalog()}
        self.assertTrue(RETIRED.isdisjoint(offered))
        for name in RETIRED:
            fitting=plate(paint=name)
            result,_,_,recipe,files=self.compile({'extra_armor':[fitting]})
            self.assertEqual(recipe['metadata']['transform']['extra_armor'][0]['plate']['paint'],name)
            self.assertIn(custom_armor.paint_name(fitting['plate'])+'.dds',files)
            shown=custom_armor.preview(name)
            self.assertEqual(shown['selected_paint']['id'],name)
            self.assertTrue(RETIRED.isdisjoint(p['id'] for p in shown['paints']))
    def test_every_bundled_finish_exports_and_reopens_with_its_own_material(self):
        from workshop.armor_paints import PAINTS, texture_bytes
        fits=[plate(paint=name) for name in PAINTS if name!='steel']
        result,root,model,recipe,files=self.compile({'extra_armor':fits})
        for fitting in fits:
            value=custom_armor.plate(fitting)
            name=value['paint']
            material=custom_armor.paint_name(value)
            self.assertEqual(files[material+'.dds'],texture_bytes(name))
            self.assertIn(material+'.dds',files[material+'.mtl'].decode())
            mesh=read_mesh(files.get(custom_armor.mesh_name(value)) or files[custom_armor.mesh_name(value,True)])
            self.assertTrue(all(0<=u<=1 for u in mesh['uv']))
            self.assertEqual(custom_armor.preview(name)['texture_url'],'/assets/armor-paints/'+name+'.png')
        saved=recipe['metadata']['transform']
        self.assertEqual([p['plate']['paint'] for p in saved['extra_armor']],list(PAINTS)[1:])
        again=self.compile(saved)[0]
        self.assertEqual(result['metadata']['file_hashes'],again['metadata']['file_hashes'])
        with self.assertRaises(ArmorError):
            configuration(resources(),[plate(paint='missing_texture')])

    def test_selected_texture_covers_resized_shapes_and_wraps_cylinders(self):
        variants=[{}, {'shape':'triangle'}, {'shape':'circle'}, {'shape':'cylinder'},
                  {'corners':[[0,0],[2000,0],[1800,700],[100,700]]},
                  {'hole':{'x':1000,'y':350,'diameter':100}}]
        for changes in variants:
            value=custom_armor.plate(plate(width=2000,height=700,paint='british',**changes))
            mesh=read_mesh(custom_armor.mesh_bytes(value))
            self.assertGreaterEqual(min(mesh['uv']),0)
            self.assertLessEqual(max(mesh['uv']),1)
            self.assertAlmostEqual(min(mesh['uv'][1::2]),0)
            self.assertAlmostEqual(max(mesh['uv'][1::2]),1)

    def test_registration_failure_keeps_attachment_points_available_for_editing(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from workshop.app import AppState
        from workshop.registration import RegistrationError
        with tempfile.TemporaryDirectory() as temp:
            state=AppState(Path(temp))
            state.index=resources()
            state.index.dlc_availability=lambda:SimpleNamespace(key=lambda:'fixture')
            transform={'recipient_mount':'azimuth','donor_mount':'azimuth','commander_only':True}
            with patch('workshop.registration.register_variant',side_effect=RegistrationError('No Conquest entry')), patch.object(state,'suggested_shop_cost',return_value=None), patch.object(state,'passenger_listing',return_value=[]), patch.object(state,'weapon_listing',return_value=[]):
                checked=state.check({'hull':'receiver','donor':'receiver','transform':transform})
                self.assertFalse(checked['ok'])
                self.assertTrue(checked['armor_hosts'])
                self.assertIn('No Conquest entry',checked['message'])
                transform['extra_armor']=[plate()]
                fitted=state.check({'hull':'receiver','donor':'receiver','transform':transform})
                self.assertFalse(fitted['ok'])
                self.assertTrue(fitted['armor_hosts'])

    def test_shapes_and_openings_export_matching_meshes_and_collision_cells(self):
        from workshop.armor_view import read_volume_mesh
        variants = [
            {'corners': [[0, 0], [1000, 0], [800, 500], [100, 500]]},
            {'shape': 'triangle'},
            {'hole': {'x': 500, 'y': 250, 'diameter': 150}},
            {'shape': 'cylinder'},
            {'shape': 'circle'},
        ]
        for shape in ('triangle','circle','cylinder'):
            source=custom_armor.plate(plate(shape=shape))
            corners=[[x+5,y-3] for x,y in custom_armor.outline(source)]
            variants.append({'shape':shape,'corners':corners})
        for changes in variants:
            with self.subTest(changes=changes):
                fitting = plate(**changes)
                result, root, model, recipe, files = self.compile({'extra_armor': [fitting]})
                value = custom_armor.plate(fitting)
                volumes = [v for key, v in model['volumes'].items() if key.startswith('mw_armor_')]
                self.assertEqual(len(volumes), 1)
                for volume in volumes:
                    path = volume['node'].find('polyhedron').args[0]
                    points, indices, _ = read_volume_mesh(files[path])
                    expected, triangles = custom_armor.surface_mesh(value)
                    self.assertEqual(len(indices), len(triangles)*3)
                    for actual, wanted in zip(points, expected):
                        for a, b in zip(actual, wanted):
                            self.assertAlmostEqual(a, b, places=4)
                    self.assertTrue(all(math.isfinite(n) for p in points for n in p))
                again = self.compile(recipe['metadata']['transform'])[0]
                self.assertEqual(result['metadata']['file_hashes'], again['metadata']['file_hashes'])

    def test_invalid_corners_openings_and_cylinder_walls_are_rejected(self):
        invalid = [
            {'corners': [[0,0],[0,500],[1000,0],[1000,500]]},
            {'corners': [[0,0],[1000,0],[500,0],[0,500]]},
            {'hole': {'x': 0, 'y': 0, 'diameter': 100}},
            {'hole': {'x': 500, 'y': 250, 'diameter': 1000}},
            {'shape': 'cylinder', 'thickness': 500},
            {'shape': 'cylinder', 'hole': {'x': 500, 'y': 250, 'diameter': 100}},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ArmorError):
                configuration(resources(), [plate(**changes)])

    def test_cylinder_has_no_internal_sector_walls_and_preserves_its_open_center(self):
        value = custom_armor.plate(plate(shape='cylinder'))
        vertices, triangles = custom_armor.surface_mesh(value)
        self.assertEqual(len(triangles), 512)
        for triangle in triangles:
            a,b,c = [vertices[i] for i in triangle]
            u,v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
            normal = [u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
            if abs(normal[2]) > 1e-8:
                continue
            center = [sum(p[i] for p in (a,b,c))/3 for i in range(2)]
            alignment = abs(sum(normal[i]*center[i] for i in range(2))) / math.hypot(*normal[:2]) / math.hypot(*center)
            self.assertGreater(alignment, .99, 'a radial dividing wall remains inside the cylinder')

    def test_circular_plate_is_round_and_has_the_requested_diameter_and_thickness(self):
        value = custom_armor.plate(plate(shape='circle'))
        vertices, _ = custom_armor.surface_mesh(value)
        self.assertEqual(value['width'], value['height'])
        for x,y,z in vertices:
            self.assertAlmostEqual(abs(x), .4)
            self.assertAlmostEqual(math.hypot(y,z), 10)

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
