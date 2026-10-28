"""Initial rendering must not expose damage variants or their attached actors."""
import unittest
from unittest.mock import patch

from workshop.geometry import build_preview, inventory_weapon_meshes, parse_model
from workshop.syntax import parse
from tests.test_geometry import eply


class Resources:
    def __init__(self, model):
        self.model = model
        self.reads = []

    def resolve_template(self, name):
        return {'model_path': 'entity/test.mdl', 'definition_path': 'entity/test.def'}

    def read(self, path):
        self.reads.append(path)
        if path.endswith('.mdl'):
            return self.model.encode()
        if path.endswith('.def'):
            return b'{game_entity}'
        if path.endswith('missing.ply'):
            raise KeyError(path)
        return eply()


class ModelVisibilityTests(unittest.TestCase):
    def test_initial_visibility_filters_meshes_without_changing_source_or_structure(self):
        text = ('{skeleton {bone "body" {VolumeView "body.ply"}'
                '{bone "damage" {visibility 0} {position 10000 0 0} {VolumeView "missing.ply"}'
                '{bone "child" {VolumeView "child.ply"}}'
                '{bone "explicit_child" {visibility 1} {VolumeView "child.ply"}}}'
                '{bone "visible_xx" {visibility 1} {VolumeView "visible.ply"}}}}')
        resources = Resources(text)
        model = parse_model(text)
        self.assertEqual(model['document'].render(), text)
        self.assertEqual(model['bones']['damage']['assets'], ['missing.ply'])
        for name in ('damage', 'child', 'explicit_child'):
            self.assertFalse(model['bones'][name]['visible'])
        preview = build_preview(resources, 'test')
        self.assertEqual([m['bone'] for m in preview['meshes']], ['body', 'visible_xx'])
        self.assertEqual(preview['stats'], {'meshes': 2, 'vertices': 6, 'triangles': 2})
        self.assertEqual(preview['bounds']['max'], [1.0, 1.0, 0.0])
        self.assertFalse(any(d['code'] == 'mesh_unavailable' for d in preview['diagnostics']))
        self.assertNotIn('entity/missing.ply', resources.reads)
        self.assertTrue({'damage', 'child', 'explicit_child'} <= {b['name'] for b in preview['bones']})

    def test_hull_and_selected_module_share_initial_visibility(self):
        text = ('{skeleton {bone "body" {VolumeView "body.ply"}'
                '{bone "damage" {visibility 0.0} {VolumeView "damage.ply"}}'
                '{bone revolute "turret" {VolumeView "turret.ply"}'
                '{bone "gun" {VolumeView "gun.ply"}}'
                '{bone "cover" {visibility 0} {VolumeView "cover.ply"}}}}}')
        hull = build_preview(Resources(text), 'test', 'hull', 'turret')
        turret = build_preview(Resources(text), 'test', 'turret', 'turret')
        self.assertEqual([m['bone'] for m in hull['meshes']], ['body'])
        self.assertEqual([m['bone'] for m in turret['meshes']], ['turret', 'gun'])
        hidden = text.replace('{bone revolute "turret"', '{bone revolute "turret" {visibility 0}')
        self.assertEqual(build_preview(Resources(hidden), 'test', 'turret', 'turret')['meshes'], [])

    def test_skeleton_visibility_and_duplicate_parent_context_are_preserved(self):
        text = ('{skeleton {bone "body" {bone "locator" {visibility 0}}'
                '{bone "other" {bone "locator" {bone "child" {VolumeView "child.ply"}}}}}}')
        model = parse_model(text)
        self.assertFalse(model['bones']['locator']['visible'])
        self.assertTrue(model['duplicates'][0]['visible'])
        self.assertTrue(model['bones']['child']['visible'])
        self.assertEqual(model['document'].render(), text)
        hidden = text.replace('{skeleton', '{skeleton {visibility 0}')
        self.assertEqual(build_preview(Resources(hidden), 'test')['meshes'], [])

    def test_inventory_actor_on_hidden_host_is_not_loaded(self):
        text = '{skeleton {bone "body" {visibility 0} {bone "socket"}}}'
        root = parse('{game_entity {extender "inventory"'
                     '{weapon "aa" {mask "mountable"}} {box {item "test" {user "aa"}}}}'
                     '{Weaponry {place "aa" {LinkBone "socket"}}}}').find('game_entity')
        resources = Resources(text)
        with patch('workshop.compiler._load_entity', return_value=(None, None, root)):
            meshes, warnings = inventory_weapon_meshes(resources, 'test', parse_model(text))
        self.assertEqual((meshes, warnings), ([], []))
        self.assertEqual(resources.reads, [])


if __name__ == '__main__':
    unittest.main()
