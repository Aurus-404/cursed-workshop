import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.test_gun_assembly import Resources
from workshop.compiler import compile_assembly
from workshop.geometry import parse_model, multiply
from workshop.pivots import pose_matrix
from workshop.sights import camera_override, configuration
from workshop.syntax import block


class SightTests(unittest.TestCase):
    def test_socket_is_invisible_attached_and_hashed_in_every_build_path(self):
        for draft in (False, True):
            with self.subTest(draft=draft), tempfile.TemporaryDirectory() as temp:
                spec={'parent':'body','position':[12,3,40],'rotation':[0,10,20]}
                transform={'commander_only':True,'sight':spec}
                if draft:
                    transform={'assembly_draft':{'schema':1,'turrets':[],'guns':[]},'sight':spec}
                result=compile_assembly(Resources(),'receiver','receiver',transform,temp,'mw_sight_test')
                path=Path(result['output_dir'])/'resource'/result['model_path']
                model=parse_model(path.read_bytes());contract=result['metadata']['sight']
                socket=model['bones'][contract['bone']]
                self.assertEqual(socket['parent'],'body')
                self.assertEqual(socket['assets'],[])
                expected=multiply(model['bones']['body']['world'],pose_matrix(spec))
                for actual,row in zip(socket['world'],expected):
                    for a,b in zip(actual,row):self.assertAlmostEqual(a,b)
                self.assertEqual(result['metadata']['file_hashes'][path.name],hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertEqual(result['metadata']['transform']['sight'],spec)

    def test_override_preserves_external_camera_zoom_and_controls(self):
        camera=block('{vehicle {level {linkBone "basis"}{offset 30 0 50}} {level {zoom}{linkBone "turret"}{offset 50 1 2}{distance 30}{camera "zoom2.5"}{ManualAccuracyZoom}} {level {zoom}{camera "zoom5"}}}')
        external=camera.find_all('level')[0].render()
        camera_override(camera,{'bone':'cw_sight_0'})
        self.assertEqual(camera.find_all('level')[0].render(),external)
        for level in camera.find_all('level')[1:]:
            self.assertEqual(level.find('linkBone').args,['cw_sight_0'])
            self.assertEqual(level.find('offset').args,['0','0','0'])
            self.assertEqual(level.find('distance').args,['0'])
        self.assertEqual(camera.find_all('level')[1].find('camera').args,['zoom2.5'])
        self.assertIsNotNone(camera.find_all('level')[1].find('ManualAccuracyZoom'))

    def test_invalid_parent_and_pose_do_not_export(self):
        with self.assertRaises(ValueError):configuration({'parent':''})
        with self.assertRaises(ValueError):configuration({'parent':'body','position':[float('nan'),0,0]})
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError,'ya no existe'):
            compile_assembly(Resources(),'receiver','receiver',{'commander_only':True,'sight':{'parent':'missing'}},temp,'mw_sight_test')
