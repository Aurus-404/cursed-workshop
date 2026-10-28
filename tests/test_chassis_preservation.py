"""Native chassis transforms must survive complete and composite no-op edits."""
from pathlib import Path
import tempfile
import unittest
from workshop.compiler import compile_assembly
from workshop.geometry import parse_model, multiply, euler_matrix
from tests.test_compiler import FakeResources


class ChassisPreservationTests(unittest.TestCase):
    ROOT_TRANSFORM=b'{orientation 1 0 0 0 -1 0 0 0 1}{Position 1.25 -2.5 3.75}'
    def resource(self):
        resources=FakeResources();key='entity/hull/hull.mdl'
        resources.files[key]=resources.files[key].replace(b'{bone "basis"',
            b'{bone "basis" {parameters "wheelradius=0.29;"}'+self.ROOT_TRANSFORM,1)
        key='entity/hull/hull.def'
        resources.files[key]=resources.files[key].replace(b'{mass 9000}',b'{placer {place "driver" {group "crew"}}{place "gunner" {group "crew"}}}{mass 9000}')
        return resources

    def test_complete_edit_does_not_rewrite_native_root_when_hull_pose_is_unchanged(self):
        resources=self.resource();native=parse_model(resources.files['entity/hull/hull.mdl'])
        for pose in ({},{'position':[0,0,0],'rotation':[0,0,0]}):
            with self.subTest(pose=pose),tempfile.TemporaryDirectory() as temp:
                result=compile_assembly(resources,'hull','hull',{'commander_only':True,'hull':pose},temp,'mw_native_root')
                model=parse_model((Path(temp)/'resource'/result['model_path']).read_bytes())
                root=model['bones']['basis']['node'];original=native['bones']['basis']['node']
                for head in ('orientation','Position','parameters'):
                    self.assertIsNotNone(root.find(head))
                    self.assertEqual(root.find(head).render(),original.find(head).render())
                first_child=next(i for i,n in enumerate(root.children) if n.head.casefold()=='bone')
                self.assertTrue(all(i<first_child for i,n in enumerate(root.children)
                                    if n.head.casefold() in ('orientation','position','parameters')))
                self.assertIsNone(root.find('Matrix34'))
                self.assertEqual(model['bones']['body']['world'],native['bones']['body']['world'])

    def test_moved_hull_still_applies_requested_transformation(self):
        resources=self.resource();native=parse_model(resources.files['entity/hull/hull.mdl'])
        with tempfile.TemporaryDirectory() as temp:
            result=compile_assembly(resources,'hull','hull',{'commander_only':True,'hull':{'position':[0,0,8],'rotation':[0,0,15]}},temp,'mw_moved_root')
            model=parse_model((Path(temp)/'resource'/result['model_path']).read_bytes())
            expected=multiply(euler_matrix([0,0,15],[0,0,8]),native['bones']['basis']['local'])
            for row in range(4):
                for col in range(4):self.assertAlmostEqual(model['bones']['basis']['local'][row][col],expected[row][col],places=7)

    def test_composite_turret_finalization_preserves_chassis_root_declaration(self):
        import zipfile
        from workshop.resources import ResourceIndex
        from tests.test_commander_mg import fixture
        with tempfile.TemporaryDirectory() as temp:
            game=Path(temp)/'game';fixture(game)
            key='-vehicle/test/hull/hull.mdl'
            with zipfile.ZipFile(game/'resource/entity/fixture.pak') as archive:raw=archive.read(key)
            source=game/'resource/entity'/key;source.parent.mkdir(parents=True,exist_ok=True)
            source.write_bytes(raw.replace(b'{bone "basis"',b'{bone "basis" {parameters "wheelradius=0.29;"}'+self.ROOT_TRANSFORM,1))
            index=ResourceIndex(game).build()
            try:
                transform={'assembly_draft':{'schema':1,'guns':[],'turrets':[{'donor_template':'hull','recipient_mount':'turret','donor_mount':'turret','pose':{'position':[0,0,0],'rotation':[0,0,0]}}]}}
                output=Path(temp)/'compiled';result=compile_assembly(index,'hull','hull',transform,output,'mw_draft_root')
                model=parse_model((output/'resource'/result['model_path']).read_bytes())
                root=model['bones']['basis']['node']
                self.assertIsNotNone(root.find('orientation'))
                self.assertEqual(root.find('Position').args,['1.25','-2.5','3.75'])
                self.assertEqual(root.find('parameters').args,['wheelradius=0.29;'])
            finally:index.close()

if __name__=='__main__':unittest.main()
