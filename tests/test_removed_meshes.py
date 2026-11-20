"""Pieces taken off the built vehicle from the editor's viewport list."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from workshop.compiler import compile_assembly
from workshop.geometry import parse_model
from workshop.removed_meshes import configuration, strip
from workshop.syntax import parse
from tests.test_compiler import FakeResources


def views(model, name):
    node = model['bones'][name]['node']
    return [child.head for child in node.children if child.head.casefold() in ('volumeview', 'lodview')]


class RemovedMeshesTests(unittest.TestCase):
    def test_configuration_keeps_order_and_drops_repeats(self):
        self.assertEqual(configuration(None), [])
        self.assertEqual(configuration(['detail1', 'body', 'detail1']), ['detail1', 'body'])
        for bad in ('body', [''], ['  '], [3], ['x' * 161], ['a'] * 513, {'hull': ['body']}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                configuration(bad)

    def test_strip_removes_drawn_meshes_only(self):
        document = parse('{skeleton {bone "basis" {bone "body" {VolumeView "body.ply"} {LODView {VolumeView "a.ply"} {VolumeView "b.ply"}}'
                         ' {bone "detail1" {VolumeView "box.ply"} {Position 1 2 3}} {bone "visor1" {VolumeView "v.ply"}}}'
                         ' {bone "visor1" {VolumeView "v2.ply"}}}}'
                         '{volume "body" {bone "body"} {box 1 1 1}}')
        found = strip(document, ['body', 'visor1', 'missing'])
        self.assertEqual(found, {'body', 'visor1'})
        model = parse_model(document.render())
        self.assertEqual(views(model, 'body'), [])
        self.assertEqual(views(model, 'detail1'), ['VolumeView'], 'a child bone keeps its own meshes')
        self.assertIn('detail1', model['bones'], 'the bone itself stays')
        self.assertEqual(model['bones']['detail1']['node'].find('Position').args, ['1', '2', '3'])
        self.assertIn('body', model['volumes'], 'armour and collisions are untouched')
        self.assertNotIn('v2.ply', document.render(), 'a repeated locator name is one piece in the editor')

    def test_destroyed_state_uses_its_suffixed_bone_only_when_the_name_is_absent(self):
        wreck = parse('{skeleton {bone "basis" {bone "body_x" {VolumeView "body_x.ply"}} {bone "turret" {VolumeView "t.ply"}}}}')
        self.assertEqual(strip(wreck, ['body', 'turret'], 'x'), {'body', 'turret'})
        text = wreck.render()
        self.assertNotIn('body_x.ply', text)
        self.assertNotIn('t.ply', text)

    @staticmethod
    def resources():
        resources = FakeResources()
        key = 'entity/hull/hull.def'
        resources.files[key] = resources.files[key].replace(
            b'{mass 9000}', b'{placer {place "driver" {group "crew"}}{place "gunner" {group "crew"}}}{mass 9000}')
        return resources

    def test_edit_build_drops_the_pieces_and_keeps_them_in_the_recipe(self):
        resources = self.resources()
        with tempfile.TemporaryDirectory() as temp:
            result = compile_assembly(resources, 'hull', 'hull',
                                      {'commander_only': True, 'removed_meshes': ['body', 'gone']}, temp, 'mw_no_decor')
            raw = (Path(temp) / 'resource' / result['model_path']).read_bytes()
            model = parse_model(raw)
            self.assertEqual(views(model, 'body'), [])
            self.assertEqual(views(model, 'turret'), ['VolumeView'])
            self.assertIn('body', model['volumes'])
            metadata = result['metadata']
            self.assertEqual(metadata['transform']['removed_meshes'], ['body', 'gone'])
            self.assertEqual(metadata['removed_meshes'], {'bones': ['body'], 'skipped': ['gone']})
            codes = {d['code'] for d in result['diagnostics']}
            self.assertIn('removed_meshes', codes)
            self.assertIn('removed_meshes_skipped', codes)
            self.assertEqual(metadata['file_hashes'][Path(result['model_path']).name], hashlib.sha256(raw).hexdigest())

    def test_invalid_list_fails_before_building(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            compile_assembly(self.resources(), 'hull', 'hull', {'commander_only': True, 'removed_meshes': 'body'}, temp, 'mw_bad_decor')

    def test_design_applies_pieces_once_to_the_finished_vehicle(self):
        from workshop.resources import ResourceIndex
        from tests.test_commander_mg import fixture
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp) / 'game'
            fixture(game)
            index = ResourceIndex(game).build()
            try:
                # A reopened vehicle's seed carries its old choice; the design
                # (top level) is what the player sees and edits now.
                seed = {'recipient': 'hull', 'donor': 'hull', 'transform': {'commander_only': True, 'removed_meshes': ['body']}}
                kept = {'assembly_draft': {'schema': 1, 'guns': [], 'turrets': [], 'seed': seed}}
                result = compile_assembly(index, 'hull', 'hull', kept, Path(temp) / 'kept', 'mw_design_kept')
                model = parse_model((Path(temp) / 'kept' / 'resource' / result['model_path']).read_bytes())
                self.assertEqual(views(model, 'body'), ['VolumeView'], 'the seed never removes pieces by itself')
                removed = {**kept, 'removed_meshes': ['body']}
                result = compile_assembly(index, 'hull', 'hull', removed, Path(temp) / 'removed', 'mw_design_removed')
                model = parse_model((Path(temp) / 'removed' / 'resource' / result['model_path']).read_bytes())
                self.assertEqual(views(model, 'body'), [])
                self.assertEqual(result['metadata']['transform']['removed_meshes'], ['body'])
            finally:
                index.close()


if __name__ == '__main__':
    unittest.main()
