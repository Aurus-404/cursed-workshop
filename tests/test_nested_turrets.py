"""Roof-mounted modules must survive compilation, reopening and further edits."""
import tempfile
import unittest
from pathlib import Path

from tests.test_compiler import FakeResources
from workshop.compiler import compile_assembly
from workshop.geometry import parse_model, build_preview, free_mount_options, free_spot_parent
from workshop.resources import ResourceIndex
from workshop.syntax import parse, block
from workshop.weapon_binding import barrel_bones
from workshop.weapon_effects import capture, install


class NestedTurretTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        r = FakeResources()
        for name in ('hull', 'donor'):
            path = f'entity/{name}/{name}.def'
            r.files[path] = r.files[path].replace(b'{game_entity', b'{game_entity {placer {place "driver" {group "crew"}}{place "gunner" {group "crew"}}}')
        r.files['entity/donor/donor.def'] = r.files['entity/donor/donor.def'].replace(
            b'{game_entity', b'{game_entity {props "tank" "native_dual" "-hatch"}').replace(
            b'{place "gun"', b'{place "dual" {Barrels 2}')
        r.files['entity/donor/donor.mdl'] = r.files['entity/donor/donor.mdl'].replace(
            b'{bone "gun"}', b'{bone revolute "pitch" {Orientation 1 0 0 0 0 1 0 -1 0}'
            b'{limits -12 30}{VolumeView "turret.ply"}'
            b'{bone "shooter00" {Position 5 -2 0}}{bone "shooter01" {Position 5 2 0}}}')
        # A partial family collision used to rename only shooter00. A prefix
        # can only address a family if all its members are allocated together.
        r.files['entity/hull/hull.mdl'] = r.files['entity/hull/hull.mdl'].replace(
            b'{bone "driver"}', b'{bone "driver"}{bone "shooter00"}')
        r.files['set/interaction_entity/entity.set'] = b'''{entities
          (define "dual_effects"
            {on spawn {add_view "flash" "flash00" "shooter00"}{add_view "flash" "flash01" "shooter01"}}
            {on fire "%slot" overload
              (define "one_barrel" name "%0" {view start "flash%0"}
                {delay .2 "fire" {view pause "flash%0"}})
              {if ("one_barrel" args 00) else ("one_barrel" args 01)}})
          {"native_dual tank" ("dual_effects" slot(dual))}
          {"hull tank" {on spawn {tags add "chassis_hatch"}}}
          {"tank unrelated" {on fire "dual" overload {view start "wrong_centred_flash"}}}
        }'''
        for name, raw in r.files.items():
            p = self.root/'game/resource'/name
            p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
        self.index = ResourceIndex(self.root/'game').build()
        self.addCleanup(self.index.close)

    def load(self, result):
        view = self.index.with_overlay(result['output_dir'])
        self.addCleanup(view.close)
        info = view.resolve_template(result['id'])
        return view, parse_model(view.read(info['model_path'])), parse(view.read(info['definition_path'])).find('game_entity')

    def assert_effects(self, result, model, root):
        record = install(block('{"test tank" {on spawn {inherited}{tags add "chassis_hatch"}}}'), result['metadata']['weapon_effects'])
        self.assertIn('chassis_hatch', record.render())
        self.assertNotIn('%', record.render(), 'nested macro parameters must be fully resolved')
        labels = []
        for view in record.walk('add_view'):
            self.assertIn(view.args[2], model['bones']); labels.append(view.args[1])
        self.assertEqual(len(labels), len(set(labels)), 'independent modules cannot share effect names')
        places = {p.args[0]:p for p in root.find('Weaponry').find_all('place')}
        for effect in result['metadata']['weapon_effects']:
            self.assertEqual(len([e for e in record.find_all('on') if e.args[:2] == ['fire',effect['place']]]), 1,
                             'a delay named fire cannot rename the engine event')
            self.assertEqual(barrel_bones(places[effect['place']], model), effect['shooters'])
            self.assertEqual(len(effect['shooters']), 2)
        return record

    def test_built_nested_turret_can_be_moved_and_limited_independently(self):
        c = compile_assembly(self.index, 'hull', 'donor', {'recipient_mount':'@free:turret'}, self.root/'first', 'mw_roof')
        view, model, root = self.load(c)
        preview = build_preview(view, c['id'], 'full')
        mount = c['metadata']['bone_mapping']['turret']
        place=next(p for p in root.find('Weaponry').find_all('place') if p.find('Barrels'))
        self.assertIsNone(place.find('LinkBone'), 'inferred multi-barrel ownership must not create a central attachment')
        pitch = c['metadata']['bone_mapping']['pitch']
        self.assertIn(mount, preview['module_mounts'])
        self.assertIn('@free:' + mount, free_mount_options(model))
        self.assertEqual(free_spot_parent(model, mount), mount)
        self.assertNotIn(pitch, preview['module_mounts'])
        self.assertIn(mount, {j['bone'] for j in preview['joints']})
        self.assertIn(pitch, {j['bone'] for j in preview['joints']})
        self.assertNotIn('-hatch', root.find('props').args)
        self.assert_effects(c, model, root)
        from workshop.integrity import dangling
        self.assertFalse(dangling(root, model))
        broken = dict(model); broken['bones'] = dict(model['bones'])
        del broken['bones'][c['metadata']['weapon_effects'][0]['shooters'][0]]
        self.assertTrue(any(kind == 'shooter' for kind, _ in dangling(root, broken)), 'partial families remain invalid')
        edited = compile_assembly(view, c['id'], c['id'], {'commander_only':True,
            'module_poses':{mount:{'position':[3,0,1]}}, 'joint_limits':{'hull':{mount:[-45,60],pitch:[-5,20]}}}, self.root/'edit', 'mw_edited')
        _, after, after_root = self.load(edited)
        self.assertEqual(after['bones']['turret']['world'], model['bones']['turret']['world'])
        self.assertNotEqual(after['bones'][mount]['world'], model['bones'][mount]['world'])
        self.assertEqual(after['bones'][pitch]['node'].find('limits').args, ['-5','20'])
        self.assert_effects(edited, after, after_root)

    def test_multiple_modules_keep_separate_barrels_effects_and_saved_recipes(self):
        transform = {'assembly_draft':{'schema':1,'guns':[],'turrets':[
            {'donor_template':'donor','donor_catalog':True,'recipient_mount':spot,'donor_mount':'turret'}
            for spot in ('@free', '@free:turret')]}}
        c = compile_assembly(self.index, 'hull', 'donor', transform, self.root/'design', 'mw_design')
        view, model, root = self.load(c)
        self.assertEqual(len(c['metadata']['weapon_effects']), 2)
        self.assertEqual(c['metadata']['interaction_donor'], 'hull')
        self.assertEqual(capture(view, c['id']), c['metadata']['weapon_effects'])
        self.assert_effects(c, model, root)

        for mount in c['metadata']['draft_module_roots'].values():
            self.assertIn(mount, build_preview(view, c['id'], 'full')['module_mounts'])

    def test_inherited_sight_keeps_its_donor_frame_after_a_name_collision(self):
        resource=self.root/'game/resource'
        stuff=resource/'set/stuff/gun/20mm'
        stuff.parent.mkdir(parents=True,exist_ok=True)
        stuff.write_bytes(b'{weapon_gun {filling "ammo"}{foresight "sight"}}')
        for template,offset in (('hull',100),('donor',7)):
            path=resource/f'entity/{template}/{template}.mdl'
            raw=path.read_bytes()
            raw=raw.replace(b'{bone revolute "turret"',
                ('{bone "sight" {Position '+str(offset)+' 0 0}}{bone revolute "turret"').encode()) if template=='hull' else raw.replace(
                b'{bone "shooter00"',b'{bone "sight" {Position 7 0 0}}{bone "shooter00"')
            path.write_bytes(raw)
        self.index.close();self.index=ResourceIndex(self.root/'game').build()
        self.addCleanup(self.index.close)
        c=compile_assembly(self.index,'hull','donor',{'recipient_mount':'@free:turret'},self.root/'sight','mw_sight')
        _,model,root=self.load(c)
        place=next(p for p in root.find('Weaponry').find_all('place') if p.find('Barrels'))
        emitted=place.find('foresight').args[0]
        self.assertNotEqual(emitted,'sight')
        self.assertEqual(emitted,c['metadata']['bone_mapping']['sight'])
        self.assertIn('set/stuff/gun/20mm',c['metadata']['source_hashes'])
        self.assertIsNone(place.find('LinkBone'))

    def test_native_macro_handler_is_replaced_once_and_unrelated_tags_are_ignored(self):
        from workshop.registration import _validate_interaction_events
        effects = capture(self.index, 'donor')
        self.assertEqual(len(effects), 1)
        self.assertNotIn('wrong_centred_flash', str(effects))
        record = install(block('{"rebuilt tank" ("dual_effects" slot(dual))}'), effects, self.index)
        _validate_interaction_events(self.index, record)
        self.assertEqual(len([e for e in record.find_all('on') if e.args[:2] == ['fire','dual']]), 1)
        self.assertEqual(len(list(record.walk('add_view'))), 2)

    def test_identity_bones_are_owned_even_when_no_rename_was_needed(self):
        from workshop.weapon_effects import transplant
        effects = capture(self.index, 'donor')
        imported = transplant(effects, {'dual':'dual2'}, {}, namespace='identity', owned={'shooter00','shooter01'})
        views = [block(text) for text in imported[0]['setup'] if block(text).head == 'add_view']
        self.assertEqual({v.args[2] for v in views}, {'shooter00','shooter01'})

    def test_a_native_single_emitter_is_dispatched_to_every_declared_barrel(self):
        path = self.root/'game/resource/set/interaction_entity/entity.set'
        path.write_text('''{entities {"native_dual tank"
          {on spawn {add_view "native_flash" "muzzle" "shooter00"}
                    {add_view "unrelated_flash" "hull_flash" "body"}}
          {on fire "dual" {view start "muzzle"}{view start "hull_flash"}
             {delay .2 "fire" {view pause "muzzle"}}}}}''')
        self.index.close(); self.index = ResourceIndex(self.root/'game').build()
        self.addCleanup(self.index.close)
        c = compile_assembly(self.index,'hull','donor',{'recipient_mount':'@free:turret'},self.root/'projected','mw_projected')
        _, model, root = self.load(c)
        self.assertTrue(c['metadata']['weapon_effects'][0]['projected_barrels'])
        record = self.assert_effects(c, model, root)
        setup = list(record.walk('add_view'))
        self.assertEqual(len(setup), 2)
        self.assertEqual({v.args[0] for v in setup}, {'native_flash'})
        self.assertEqual({v.args[2] for v in setup}, set(c['metadata']['weapon_effects'][0]['shooters']))
        self.assertEqual(len([n for n in record.walk('if') if 'name' in n.args]), 1)


if __name__ == '__main__':
    unittest.main()
