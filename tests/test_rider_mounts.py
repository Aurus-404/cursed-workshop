import copy
import tempfile
import unittest
from pathlib import Path

from workshop.compiler import compile_assembly
from workshop.geometry import parse_model, inverse, multiply
from workshop.pivots import pose_matrix
from workshop.syntax import block
from workshop.weapon_binding import mountable_slot
from workshop.articulation import weapon_joints
try:
    from . import test_compiler as fixtures
except ImportError:
    import test_compiler as fixtures


class RiderMountTests(unittest.TestCase):
    def test_native_slots_reserve_existing_inventory_and_numeric_aliases(self):
        root=block('{game_entity {Weaponry {place "MGUN01"}} {extender "inventory" {weapon "mgun2" {mask "mountable"}}}}')
        self.assertEqual(mountable_slot(root),'mgun3')

    def test_operator_firearm_and_hardware_adjustments_do_not_move_the_cannon(self):
        fixture=fixtures.CompilerTests();self.addCleanup(fixture.doCleanups)
        resources=fixture._rider_resources()
        resources.files['entity/mount/mount.mdl']=resources.files['entity/mount/mount.mdl'].replace(b'{bone revolute "pitch"',b'{bone revolute "pitch" {VolumeView "mount.ply"}')
        resources.files['entity/mount/mount.ply']=next(raw for name,raw in resources.files.items() if name.endswith('.ply'))
        row={'support':'ring','pose':'pose_ring','item':'mg42_belt','parent':'turret',
             'position':[1,2,3],'rotation':[0,0,25],'seat':[1,0,0],'yaw':[-20,30],'pitch':[-10,40],
             'operator_pose':{'position':[2,3,4],'rotation':[10,20,30]},
             'gun_pose':{'position':[4,5,6],'rotation':[0,0,35]},
             'mount_pose':{'position':[3,0,1],'rotation':[0,15,0]}}
        original=copy.deepcopy(row)
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'mounted';result=compile_assembly(resources,'hull','donor',{'rider_mgs':[row]},output,'mw_mounted')
            model=parse_model((output/'resource'/result['model_path']).read_bytes())
            definition=(output/'resource'/result['definition_path']).read_text()
            plain=compile_assembly(resources,'hull','donor',{},Path(tmp)/'plain','mw_plain')
            baseline=parse_model((Path(tmp)/'plain/resource'/plain['model_path']).read_bytes())
        self.assertEqual(row,original)
        for name,bone in baseline['bones'].items():self.assertEqual(model['bones'][name]['world'],bone['world'])
        contract=result['metadata']['rider_mgs']['mounts'][0]
        self.assertEqual(contract['slot'],'mgun1')
        self.assertIn('{bone "'+contract['bones']['@weapon']+'"}',definition)
        link=model['bones'][contract['bones']['@weapon']]
        actual=multiply(inverse(model['bones'][link['parent']]['world']),link['world'])
        for a,b in zip(sum(actual,[]),sum(pose_matrix(row['gun_pose']),[])):self.assertAlmostEqual(a,b,places=7)
        seat=model['bones'][contract['bones']['@seat']]
        from workshop.rider_mg import catalog
        native=copy.deepcopy(catalog(resources)['supports'][0]['seat_frame'])
        native[0][3]+=1
        desired=multiply(native,pose_matrix(row['operator_pose']))
        actual=multiply(inverse(model['bones'][contract['root']]['world']),seat['world'])
        for a,b in zip(sum(actual,[]),sum(desired,[])):self.assertAlmostEqual(a,b,places=6)
        joints=weapon_joints(model,block('{game_entity {Weaponry {place "mgun1" {type "aa"} {LinkBone "'+contract['bones']['@weapon']+'"}}}}'))
        self.assertEqual({j['bone'] for j in joints},{'rider1_yaw','rider1_pitch'})
        joint=next(j for j in joints if j['bone']==contract['root'])
        self.assertEqual(joint['weapons'],['mgun1'])
        self.assertTrue(model['bones'][contract['root']]['node'].find('terminator') is not None)
        self.assertIn('rider1_pitch_visual',model['bones'])
