import json
import tempfile
import unittest
from pathlib import Path

from tests.test_compiler import FakeResources
from workshop.compiler import compile_assembly, CompileError, _Assets, seat_markers, _position_crew
from workshop.geometry import parse_model, point, translation, euler_matrix, multiply, build_preview
from workshop.syntax import parse


def resources_with_crew(carried=True):
    resources = FakeResources()
    resources.files['entity/donor/donor.def'] = (
        b'{game_entity {Extension "donor.mdl"}{bone "turret" {speed2 30}}{bone "gun" {speed2 20}}'
        b'{placer {place "gunner" {group "crew"}{LinkBone "gunner"}}'
        b'{place "charger" {group "crew"}{LinkBone "charger"}}'
        b'{place "commander" {group "crew"}{LinkBone "commander"}}}'
        b'{Weaponry {place "gun" {weapon "20mm"}{gunner "gunner"}'
        b'{charger "charger commander"}}}{mass 9000}}')
    resources.files['entity/donor/donor.mdl'] = (
        b'{skeleton {bone "basis" {bone "body" {VolumeView "body.ply"}'
        b'{bone revolute "turret" {Position 0 0 20}'
        b'{Orientation 1 0 0 0 -1 0 0 0 1}{VolumeView "turret.ply"}'
        b'{bone revolute "gun" {Position 4 0 2}{Orientation 1 0 0 0 0 -1 0 1 0}'
        b'{bone "gunner" {Position 1 3 0}}}'
        b'{bone "charger" {Position -1 4 0}}'
        b'{bone "commander" {Position 2 -3 1}}}}}}')
    resolve = resources.resolve_template
    resources.resolve_template = lambda name: {**resolve(name), 'kind': 'artillery'} if name == 'donor' and carried else resolve(name)
    return resources


class CrewPlacementTests(unittest.TestCase):
    def test_complete_design_preview_keeps_native_editable_seats(self):
        from workshop.app import AppState
        body={'hull':'fixture','transform':{'crew_seats':{'charger':{'position':[1,2,3],'rotation':[0,0,90]}},'crew_exclude':['charger'],'assembly_draft':{'schema':1,'guns':[]}}}
        original=json.loads(json.dumps(body))
        preview=AppState.design_display_body(body)
        self.assertNotIn('crew_seats',preview['transform'])
        self.assertNotIn('crew_exclude',preview['transform'])
        self.assertEqual(body,original,'display must not strip adjustments from the build recipe')

    def test_relative_rotations_preserve_frames_fixed_and_moving(self):
        for fixed in (True,False):
            with self.subTest(fixed=fixed),tempfile.TemporaryDirectory() as tmp:
                tr={'auto_seat':False,'crew_static':fixed,'turret':{'position':[3,4,5],'rotation':[10,20,35]}}
                r=resources_with_crew()
                _,before,_=self.compile(r,Path(tmp)/'before',tr)
                tr['crew_seats']={role:{'position':[1,2,3],'space':'module','rotation':[0,0,90]} for role in ('gunner','charger','commander')}
                result,after,_=self.compile(r,Path(tmp)/'after',tr)
                from workshop.geometry import inverse,linear_determinant
                frame=multiply(translation([0,0,10]),euler_matrix([10,20,35],[3,4,5]))
                delta=multiply(multiply(frame,euler_matrix([0,0,90])),inverse(frame))
                for role in tr['crew_seats']:
                    bone=result['metadata']['bone_mapping'].get(role,role)
                    expected=multiply(delta,before['bones'][bone]['world'])
                    actual=after['bones'][bone]['world']
                    for row in range(3):
                        for col in range(3):self.assertAlmostEqual(actual[row][col],expected[row][col],places=5)
                    self.assertAlmostEqual(linear_determinant(actual),linear_determinant(before['bones'][bone]['world']),places=5)
                _,reopened,_=self.compile(r,Path(tmp)/'again',json.loads(json.dumps(result['metadata']['transform'])))
                self.assertEqual(after['bones']['charger']['world'],reopened['bones']['charger']['world'])

    def test_position_changes_skeleton_not_earlier_volume_reference(self):
        document = parse('{volume "seat_hit" {bone "gunner"}}{skeleton {bone "body" {bone "gunner"}}}')
        _position_crew(document, {'gunner': {'position': [3, 4, 5]}})
        self.assertEqual(document.find('volume').find('bone').render(), '{bone "gunner"}')
        self.assertPosition(parse_model(document.render()), 'gunner', [3, 4, 5])

    def test_preview_does_not_offer_discarded_carriage_crew_as_module_workers(self):
        resources = resources_with_crew()
        key = 'entity/donor/donor.def'
        resources.files[key] = resources.files[key].replace(b'charger commander', b'charger')
        key = 'entity/donor/donor.mdl'
        resources.files[key] = resources.files[key].replace(b'{bone "commander" {Position 2 -3 1}}', b'')
        resources.files[key] = resources.files[key].replace(b'{bone "basis"', b'{bone "basis" {bone "commander"}')
        preview = build_preview(resources, 'donor', 'turret', seats=seat_markers(resources, 'donor'))
        self.assertEqual({s['place'] for s in preview['seats'] if s['weapon']}, {'gunner', 'charger'})
        self.assertNotIn('commander', {s['place'] for s in preview['seats']})

    def assertPosition(self, model, name, expected):
        for actual, wanted in zip(point(model['bones'][name]['world'], [0, 0, 0]), expected):
            self.assertAlmostEqual(actual, wanted, places=5)

    def compile(self, resources, path, transform):
        result = compile_assembly(resources, 'hull', 'donor', transform, path, 'mw_seats')
        folder = Path(path)/'resource/entity/-vehicle/workshop/mw_seats'
        return result, parse_model((folder/'mw_seats.mdl').read_bytes()), parse((folder/'mw_seats.def').read_bytes())

    def test_aimers_loaders_and_commander_move_with_or_without_fixed_on_carried_and_turret(self):
        for carried in (True, False):
            for fixed in (True, False):
                with self.subTest(carried=carried, fixed=fixed), tempfile.TemporaryDirectory() as tmp:
                    resources = resources_with_crew(carried)
                    tr = {'auto_seat': False, 'crew_static': fixed,
                          'hull': {'position': [10, 2, 3], 'rotation': [0, 0, 25]},
                          'turret': {'position': [2, 4, 6], 'rotation': [10, -20, 35]}}
                    before, original, original_def = self.compile(resources, Path(tmp)/'before', tr)
                    positions = {'gunner': [9, 3, 2], 'charger': [5, -8, 4], 'commander': [-5, 6, 7]}
                    tr['crew_seats'] = {role: {'space': 'module', 'position': pos} for role, pos in positions.items()}
                    result, final, definition = self.compile(resources, Path(tmp)/'after', tr)
                    module_frame = multiply(translation([0, 0, 10]), euler_matrix([10, -20, 35], [2, 4, 6]))
                    mapping = result['metadata']['bone_mapping']
                    for role, pos in positions.items():
                        name = mapping.get(role, role)
                        self.assertPosition(final, name, point(module_frame, pos))
                        self.assertEqual(final['bones'][name]['parent'], original['bones'][name]['parent'])
                        for r in range(3):
                            for c in range(3):
                                self.assertAlmostEqual(final['bones'][name]['world'][r][c], original['bones'][name]['world'][r][c], places=5)
                    gunner = mapping.get('gunner', 'gunner')
                    self.assertEqual(final['bones'][gunner]['parent'], mapping.get('gun', 'gun'))
                    loader = mapping.get('charger', 'charger')
                    self.assertEqual(final['bones'][loader]['parent'], 'body' if fixed and carried else mapping.get('turret', 'turret'))
                    self.assertEqual(final['bones'][mapping.get('gun', 'gun')]['world'], original['bones'][mapping.get('gun', 'gun')]['world'])
                    self.assertEqual(definition.find('game_entity').find('Weaponry').render(), original_def.find('game_entity').find('Weaponry').render())
                    self.assertEqual(result['metadata']['crew_roles'], before['metadata']['crew_roles'])
                    self.assertEqual(len(result['metadata']['manual_crew']), 3)
                    # Retargeting sees the modified local rest pose, preserving the offset in animation.
                    source = parse_model(resources.files['entity/donor/donor.mdl'])
                    assets = _Assets(resources, Path(tmp)/'assets', {}, [])
                    assets.context('d', source, final, mapping)
                    self.assertTrue(any(abs(assets.animation_context['d']['gunner'][r][3]) > .01 for r in range(3)))
                    # Recipe JSON round trip reaches exactly the same seat frames.
                    _, reopened, _ = self.compile(resources, Path(tmp)/'reopen', json.loads(json.dumps(result['metadata']['transform'])))
                    self.assertEqual(reopened['bones'][gunner]['world'], final['bones'][gunner]['world'])

    def test_legacy_world_positions_and_native_role_collision(self):
        resources = resources_with_crew()
        key = 'entity/hull/hull.def'
        resources.files[key] = resources.files[key].replace(
            b'{Chassis', b'{placer {place "gunner" {group "crew"}{LinkBone "driver"}}}{Chassis')
        # Free addition keeps the original main cannon and its gunner; donor becomes gunner2.
        with tempfile.TemporaryDirectory() as tmp:
            result, final, definition = self.compile(resources, tmp, {
                'recipient_mount': '@free', 'crew_static': False,
                'crew_seats': {'gunner': {'position': [12, -5, 28]}}})
            mapped = result['metadata']['bone_mapping'].get('gunner', 'gunner')
            self.assertPosition(final, mapped, [12, -5, 28])
            places = {n.args[0]: n for pl in definition.find('game_entity').find_all('placer') for n in pl.find_all('place')}
            self.assertEqual(places['gunner2'].find('LinkBone').args, [mapped])
            self.assertEqual(places['gunner'].find('LinkBone').args, ['driver'])

    def test_invalid_positions_fail_and_excluded_workers_keep_editor_adjustment(self):
        for value in ({'position': [0, float('nan'), 0]}, {'position': [True, 0, 0]},
                      {'position': [0, 0, 0], 'space': 'unknown'}, {'position': [0, 0]},
                      *({'position':[0,0,0],'rotation':v} for v in ([0,0],[0,True,0],[0,0,float('inf')]))):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp, self.assertRaises(CompileError):
                self.compile(resources_with_crew(), tmp, {'crew_seats': {'gunner': value}})
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _ = self.compile(resources_with_crew(), tmp, {
                'crew_exclude': ['charger'], 'crew_seats': {'charger': {'position': [1, 2, 3], 'space': 'module'}}})
            self.assertNotIn('charger', result['metadata']['crew_roles'])
            self.assertEqual(result['metadata']['manual_crew'], [])


if __name__ == '__main__':
    unittest.main()
