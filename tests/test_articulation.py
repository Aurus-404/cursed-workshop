import tempfile
from pathlib import Path
import unittest
from workshop.geometry import parse_model, euler_matrix, multiply
from workshop.syntax import parse
from workshop.articulation import weapon_joints, apply_limits
from workshop.compiler import compile_assembly
from workshop.resources import ResourceIndex
from tests.test_commander_mg import fixture


class ArticulationTests(unittest.TestCase):
    def test_locator_ownership_and_definition_limits_override_model(self):
        model=parse_model('{skeleton {bone revolute "basis" {bone revolute "traverse" {limits -90 90} {bone "aimpoint"}}}}')
        root=parse('{game_entity {bone "traverse" {limits -30 50} {speed2 20}} {Weaponry {place "mgun2" {weapon "mg"} {foresight "aimpoint"}} {place "vision" {type "commander"} {weapon "binoculars"} {LinkBone "basis"}}}}').find('game_entity')
        joints=weapon_joints(model,root)
        self.assertEqual([j['bone'] for j in joints],['traverse'])
        self.assertEqual(joints[0]['limits'],[-30,50])
        apply_limits(model,root,{'turret':{'traverse':[-20,40]}},{'turret':{'traverse':'traverse'}})
        self.assertEqual(root.find('bone').find('limits').args,['-20','40'])
        self.assertEqual(model['bones']['traverse']['node'].find('limits').args,['-20','40'])
    def test_traverse_speed_is_read_from_macros_and_can_be_set(self):
        # TheOppsAreOntoMe: the Panther's turret traverse; the speed a macro sets is shown and can be changed.
        model=parse_model('{skeleton {bone revolute "basis" {bone revolute "turret" {bone "gun"}}}}')
        root=parse('{game_entity {bone "turret" ("turret_heavy" power_traverse(9))} {Weaponry {place "gun" {weapon "cannon"} {LinkBone "gun"}}}}').find('game_entity')
        defines={'turret_heavy':parse('(define "turret_heavy" {speed2 (+ 4 (/ %power_traverse 3))})').children[0]}
        self.assertAlmostEqual(weapon_joints(model,root,defines=defines)[0]['speed'],7.0)
        apply_limits(model,root,{'turret':{'turret':[-180,180,24]}},{'turret':{'turret':'turret'}})
        self.assertEqual(root.find('bone').find_all('speed2')[-1].args,['24'])
        self.assertEqual(root.find('bone').children[-1].head,'speed2')  # read after the macro, it wins
        self.assertEqual(model['bones']['turret']['node'].find('speed2').args,['24'])
        with self.assertRaises(ValueError):
            apply_limits(model,root,{'turret':{'turret':[-180,180,0]}},{'turret':{'turret':'turret'}})

    def test_only_weapon_ancestors_with_their_actual_reflected_frames(self):
        model=parse_model('{skeleton {bone "body" {bone revolute "wheel"} {bone revolute "hatch"}'
                          '{bone revolute "turret" {limits -180 180} {position 0 0 10}'
                          '{bone revolute "gun" {orientation 1 0 0 0 0 1 0 1 0} {limits -10 45}}}}}')
        root=parse('{game_entity {Weaponry {place "gun" {weapon "cannon"}}}}').find('game_entity')
        joints=weapon_joints(model,root,euler_matrix([0,0,30],[1,2,3]))
        self.assertEqual({j['bone'] for j in joints},{'turret','gun'})
        gun=next(j for j in joints if j['bone']=='gun')
        self.assertEqual(gun['limits'],[-10,45])
        self.assertEqual(gun['matrix'],multiply(euler_matrix([0,0,30],[1,2,3]),model['bones']['gun']['world']))

    def test_compiler_exports_limits_on_mapped_module_and_keeps_recipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            game=Path(tmp)/'game';fixture(game)
            index=ResourceIndex(game).build()
            try:
                tr={'joint_limits':{'turret':{'turret':[-70,90]}}}
                result=compile_assembly(index,'hull','hull',tr,Path(tmp)/'out','mw_limits')
                model=parse_model((Path(tmp)/'out/resource/entity/-vehicle/workshop/mw_limits/mw_limits.mdl').read_bytes())
                name=result['metadata']['bone_mapping']['turret']
                self.assertEqual(model['bones'][name]['node'].find('limits').args,['-70','90'])
                self.assertEqual(result['metadata']['transform'],tr)
            finally:
                index.close()

    def test_invalid_limits_fail_closed(self):
        model=parse_model('{skeleton {bone revolute "wheel"} {bone revolute "gun"}}')
        root=parse('{game_entity {Weaponry {place "gun" {weapon "cannon"}}}}').find('game_entity')
        for spec in ({'turret':{'gun':[float('nan'),2]}},{'hull':{'wheel':[3,-2]}},
                     {'turret':{'gun':[3,-2]}},{'turret':{'gun':[-181,180]}}):
            with self.subTest(spec=spec),self.assertRaises(ValueError):
                apply_limits(model,root,spec,{'hull':{'wheel':'wheel'},'turret':{'gun':'gun'}})

    def test_a_stale_limit_on_a_joint_that_moves_no_weapon_is_skipped_never_written(self):
        # Frenchies: limits set before the turret swap named a joint that, on the
        # new vehicle, exists but moves no weapon; every build failed.
        model=parse_model('{skeleton {bone revolute "wheel"} {bone revolute "gun"}}')
        root=parse('{game_entity {Weaponry {place "gun" {weapon "cannon"}}}}').find('game_entity')
        skipped=apply_limits(model,root,{'hull':{'wheel':[-2,2]},'turret':{'gun':[-5,20]}},{'hull':{'wheel':'wheel'},'turret':{'gun':'gun'}})
        self.assertEqual(skipped,['wheel'])
        self.assertIsNone(model['bones']['wheel']['node'].find('limits'),'no limit is written to a joint that moves no weapon')
        self.assertEqual(model['bones']['gun']['node'].find('limits').args,['-5','20'])
