import unittest

from workshop.compiler import _Assets
from workshop.syntax import parse
from workshop.textures import diffuse_path, texture_path
from tests.test_textures import FakeResources


class MaterialExportTests(unittest.TestCase):
    def test_relocated_material_preserves_all_native_map_authorities(self):
        source='entity/module/paint.mtl'
        resources=FakeResources({source:b'{material bump {diffuse "paint"}{bump "../normals"}{specular "shine.dds"}{mask "cutout"}{color "230 230 230 25"}}',
            'entity/module/paint.dds':b'diffuse','entity/normals.dds':b'normals',
            'entity/module/shine.dds':b'specular','texture/common/cutout.dds':b'mask'})
        assets=_Assets(resources,'test',{},[])
        exported=assets.add(source,'d')
        relocated='entity/new/'+exported
        resources.files[relocated]=assets.files[exported]
        self.assertEqual(diffuse_path(resources,source),diffuse_path(resources,relocated))
        document=parse(resources.files[relocated])
        for channel,path in [('diffuse','entity/module/paint.dds'),('bump','entity/normals.dds'),
                             ('specular','entity/module/shine.dds'),('mask','texture/common/cutout.dds')]:
            ref=next(document.walk(channel)).args[0]
            self.assertEqual(texture_path(resources,relocated,ref),path)
            self.assertIn(path,assets.sources)
        self.assertEqual(next(document.walk('color')).args,['230 230 230 25'])

    def test_same_texture_name_in_different_modules_does_not_change_ownership(self):
        resources=FakeResources({f'entity/{part}/paint.mtl':b'{material bump {diffuse "paint"}}' for part in ('a','b')})
        resources.files.update({'entity/a/paint.dds':b'a','entity/b/paint.dds':b'b'})
        assets=_Assets(resources,'test',{},[])
        results=[]
        for part in ('a','b'):
            name=assets.add(f'entity/{part}/paint.mtl','d')
            resources.files['entity/export/'+name]=assets.files[name]
            results.append(diffuse_path(resources,'entity/export/'+name))
        self.assertEqual(results,['entity/a/paint.dds','entity/b/paint.dds'])

    def test_unavailable_source_texture_is_reported_without_inventing_a_replacement(self):
        resources=FakeResources({'entity/source/paint.mtl':b'{material bump {diffuse "missing"}}'})
        diagnostics=[];assets=_Assets(resources,'test',{},diagnostics)
        name=assets.add('entity/source/paint.mtl','d')
        self.assertEqual(next(parse(assets.files[name]).walk('diffuse')).args,['missing'])
        self.assertEqual(diagnostics[0]['code'],'source_missing_texture')
