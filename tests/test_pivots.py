import copy
import tempfile
import unittest
from pathlib import Path
from workshop.pivots import pose_matrix,rebase_joint,pivot_of
from workshop.geometry import parse_model,point,multiply,inverse,euler_matrix
from workshop.compiler import compile_assembly
from workshop.syntax import parse
from tests.test_gun_assembly import Resources


class PivotTests(unittest.TestCase):
    def test_pose_keeps_pivot_stationary_under_rotation(self):
        pose={'position':[2,3,4],'rotation':[20,30,40],'pivot':[7,8,9]}
        self.assertEqual([round(x,7) for x in point(pose_matrix(pose),pose['pivot'])],[9,11,13])
        with self.assertRaises(ValueError):pose_matrix({'position':[1,2],'rotation':[0,0,0]})
        for value in ([float('nan'),0,0],[True,0,0],[0,0],None):
            with self.assertRaises(ValueError):pivot_of({'pivot':value})

    def test_rebase_preserves_geometry_volumes_and_children_but_changes_live_rotation(self):
        doc=parse('{skeleton {bone "body" {bone revolute "joint" {Position 1 2 3}{VolumeView "own.ply"}{bone "muzzle" {Position 10 0 0}}}}}{volume "armour" {bone "joint"}{box 1 1 1}}')
        before=parse_model(doc.render());child=rebase_joint(doc,'joint',[5,0,0]);after=parse_model(doc.render())
        self.assertEqual(point(before['bones']['muzzle']['world'],[0,0,0]),point(after['bones']['muzzle']['world'],[0,0,0]))
        self.assertEqual(after['bones'][child]['assets'],['own.ply'])
        self.assertEqual(after['volumes']['armour']['bone'],child)
        self.assertEqual(point(after['bones']['joint']['world'],[0,0,0]),[6,2,3])
        # Simulated native 90-degree joint rotation uses the moved origin.
        moved=multiply(after['bones']['joint']['world'],euler_matrix([0,0,90]))
        local=multiply(inverse(after['bones']['joint']['world']),after['bones']['muzzle']['world'])
        self.assertEqual([round(v,6) for v in point(multiply(moved,local),[0,0,0])],[6,7,3])

    def test_gun_swap_exports_a_real_shifted_joint_and_retains_coax_geometry(self):
        resources=Resources()
        with tempfile.TemporaryDirectory() as temp:
            tr={'gun_swap':{'schema':1,'recipient_place':'main_slot','donor_place':'main_slot'},'turret':{'position':[0,0,0],'rotation':[0,0,0]}}
            a=compile_assembly(resources,'receiver','donor',tr,Path(temp)/'a','mw_pivot')
            tr=copy.deepcopy(tr);tr['turret']['pivot']=[4,0,0]
            b=compile_assembly(resources,'receiver','donor',tr,Path(temp)/'b','mw_pivot')
            read=lambda r:parse_model((Path(r['output_dir'])/'resource'/r['model_path']).read_bytes())
            old,new=read(a),read(b);contract=b['metadata']['gun_assembly'];root=contract['bone_mapping'][contract['donor']['pivot']];oldroot=contract['recipient']['pivot']
            self.assertEqual(point(new['bones'][root]['world'],[0,0,0]),[old['bones'][oldroot]['world'][0][3]+4,old['bones'][oldroot]['world'][1][3],old['bones'][oldroot]['world'][2][3]])
            self.assertEqual(old['bones'][oldroot]['world'],new['bones'][oldroot]['world'],'shared receiver joint must remain native')
            for name in ('coax','sight'):
                self.assertEqual(point(old['bones'][name]['world'],[0,0,0]),point(new['bones'][name]['world'],[0,0,0]))
            self.assertTrue(any(n.startswith('cw_pivot_') for n in new['bones']))
            self.assertTrue(b['metadata']['validation']['dependencies']['passed'])

    def test_turret_pivot_preserves_gun_world_at_rest(self):
        from tests.test_compiler import FakeResources
        with tempfile.TemporaryDirectory() as temp:
            r=FakeResources();base={'auto_seat':False,'turret':{'position':[0,0,0],'rotation':[0,0,0]}}
            a=compile_assembly(r,'hull','donor',base,Path(temp)/'a','mw_pivot')
            b=compile_assembly(r,'hull','donor',{**base,'turret':{**base['turret'],'pivot':[4,0,0]}},Path(temp)/'b','mw_pivot')
            read=lambda result:parse_model((Path(result['output_dir'])/'resource'/result['model_path']).read_bytes())
            old,new=read(a),read(b)
            self.assertEqual(point(old['bones']['gun']['world'],[0,0,0]),point(new['bones']['gun']['world'],[0,0,0]))
            self.assertNotEqual(point(old['bones']['turret']['world'],[0,0,0]),point(new['bones']['turret']['world'],[0,0,0]))

    def test_complete_design_chassis_pose_is_applied_after_saved_guns(self):
        from tests.test_vehicle_design import VehicleDesignTests
        fixture=VehicleDesignTests();fixture.setUp()
        try:
            tr={'assembly_draft':{'schema':1,'guns':fixture.rows()},'hull':{'position':[5,0,0],'rotation':[0,0,30],'pivot':[7,0,0]}}
            result=compile_assembly(fixture.index,'receiver','donor',tr,fixture.root/'placed','mw_pivot')
            model=parse_model((Path(result['output_dir'])/'resource'/result['model_path']).read_bytes())
            self.assertEqual(result['metadata']['transform'],tr)
            self.assertFalse(any('/workshop/mw_pivot/' in path for path in result['metadata']['source_hashes']))
            self.assertEqual(len(result['metadata']['draft_steps']),2)
            self.assertTrue(result['metadata']['validation']['dependencies']['passed'])
        finally:fixture.doCleanups()

    def test_commander_mount_uses_a_rebased_yaw_without_moving_socket_at_rest(self):
        from tests.test_commander_mg import fixture
        from workshop.resources import ResourceIndex
        with tempfile.TemporaryDirectory() as temp:
            temp=Path(temp);game=temp/'game';fixture(game);index=ResourceIndex(game).build()
            try:
                spec={'support':'support:aa','parent':'turret','item':'base_gun','position':[2,3,4],'rotation':[0,0,30],'yaw':[-90,90],'pitch':[-10,45]}
                old=compile_assembly(index,'hull','hull',{'commander_only':True,'commander_mg':spec},temp/'a','mw_pivot')
                # Compensated position keeps the complete previous pose unchanged.
                p=[5,0,0];rotation=euler_matrix(spec['rotation']);rp=point(rotation,p)
                spec={**spec,'pivot':p,'position':[spec['position'][i]-p[i]+rp[i] for i in range(3)]}
                new=compile_assembly(index,'hull','hull',{'commander_only':True,'commander_mg':spec},temp/'b','mw_pivot')
                model=lambda result:parse_model((Path(result['output_dir'])/'resource'/result['model_path']).read_bytes())
                a,b=model(old),model(new);names=new['metadata']['commander_mg']['bones']
                for bone in ('socket','seat_open'):
                    self.assertEqual([round(v,6) for v in point(a['bones'][names[bone]]['world'],[0,0,0])],[round(v,6) for v in point(b['bones'][names[bone]]['world'],[0,0,0])])
                self.assertNotEqual(a['bones'][names['yaw']]['world'],b['bones'][names['yaw']]['world'])
            finally:index.close()

    def test_design_hosts_remain_local_when_the_chassis_frame_moves(self):
        from tests.test_vehicle_design import VehicleDesignTests
        from workshop.app import AppState
        from unittest.mock import patch
        fixture=VehicleDesignTests();fixture.setUp();state=AppState(fixture.root/'workspace');state.index=fixture.index
        try:
            tr={'assembly_draft':{'schema':1,'guns':fixture.rows()}}
            with patch('workshop.registration.register_variant',return_value={}):
                a=state.check({'hull':'receiver','donor':'donor','transform':tr})
                b=state.check({'hull':'receiver','donor':'donor','transform':{**tr,'hull':{'position':[5,0,0],'rotation':[0,0,30],'pivot':[7,0,0]}}})
            self.assertTrue(a['ok'],a);self.assertTrue(b['ok'],b)
            for key in ('hosts','armor_hosts'):
                self.assertEqual([h['name'] for h in a[key]],[h['name'] for h in b[key]])
                for before,after in zip(a[key],b[key]):
                    for left,right in zip(before['matrix'],after['matrix']):
                        for x,y in zip(left,right):self.assertAlmostEqual(x,y)
        finally:state.clear_portrait_previews();state.pool.shutdown(wait=True);fixture.doCleanups()
