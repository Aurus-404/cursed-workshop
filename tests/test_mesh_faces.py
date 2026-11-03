"""Visible-side preservation across native and exported mesh instances."""
import tempfile
import unittest
from pathlib import Path

from tests.test_geometry import eply
from workshop.compiler import _Assets
from workshop.geometry import parse_model, read_mesh


class MeshFaceTests(unittest.TestCase):
    def test_one_mesh_can_be_exported_with_both_handednesses_under_the_same_owner(self):
        class Resources:
            def read(self, path):
                return eply()
        source = parse_model('{skeleton {bone "root" {bone "a" {VolumeView "panel.ply"}}'
                             '{bone "b" {LODView "panel.ply"}}}}')
        final = parse_model('{skeleton {bone "root" {bone "kept" {VolumeView "panel.ply"}}'
                            '{bone "moved" {orientation 1 0 0 0 -1 0 0 0 1}{LODView "panel.ply"}}}}')
        with tempfile.TemporaryDirectory() as temp:
            assets = _Assets(Resources(), Path(temp), {}, [])
            assets.context('h', source, final, {'a': 'kept', 'b': 'moved'})
            assets.rewrite(final['document'], 'entity/source/model.mdl', 'h')
            refs = [final['bones'][name]['node'].children[-1].args[0] for name in ('kept', 'moved')]
            self.assertNotEqual(*refs, 'opposite instances need distinct index buffers')
            first, second = [read_mesh(assets.files[ref]) for ref in refs]
            self.assertEqual(first['indices'], [0, 1, 2])
            self.assertEqual(second['indices'], [0, 2, 1])
            for field in ('positions', 'normals', 'uv'):
                self.assertEqual(first[field], second[field])

    def test_an_absolute_native_mesh_is_privatized_when_its_instance_changes_side(self):
        class Resources:
            def read(self, path):
                if path != 'entity/shared/panel.ply': raise KeyError(path)
                return eply()
        source = parse_model('{skeleton {bone "panel" {VolumeView "/entity/shared/panel.ply"}}}')
        final = parse_model('{skeleton {bone "panel" {orientation 1 0 0 0 -1 0 0 0 1}'
                            '{VolumeView "/entity/shared/panel.ply"}}}')
        with tempfile.TemporaryDirectory() as temp:
            assets = _Assets(Resources(), Path(temp), {}, [])
            assets.context('h', source, final)
            assets.rewrite(final['document'], 'entity/source/model.mdl', 'h')
            reference = final['bones']['panel']['node'].find('VolumeView').args[0]
            self.assertFalse(reference.startswith('/'))
            self.assertEqual(read_mesh(assets.files[reference])['indices'], [0, 2, 1])
