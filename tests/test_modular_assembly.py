"""Module removal and complete artillery: structural and campaign contracts."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests.test_gun_assembly import Resources
from tests.test_campaign import SCENE, make_save
from workshop.compiler import compile_assembly
from workshop.geometry import parse_model
from workshop.resources import ResourceIndex
from workshop.syntax import parse, block
from workshop.draft import specification
from workshop.campaign import load_campaign, plan_draft, apply_plan


class ModularAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.fixture = Resources()
        self.fixture.files['entity/towed/towed.def'] = self.fixture.files['entity/towed/towed.def'].replace(b'{game_entity',b'{game_entity {props "cannon"}')

    def compile(self, transform, donor='receiver', name='mw_removed'):
        result = compile_assembly(self.fixture, 'receiver', donor, transform, self.folder/name, name)
        return result, parse((self.folder/name/'resource'/result['definition_path']).read_bytes()).find('game_entity'), parse_model((self.folder/name/'resource'/result['model_path']).read_bytes())

    def index(self):
        for path, raw in self.fixture.files.items():
            target = self.folder/'game/resource'/path
            target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(raw)
        index = ResourceIndex(self.folder/'game').build()
        self.addCleanup(index.close)
        return index

    def test_remove_without_supply_preserves_hull_and_retained_weapon(self):
        key = 'entity/receiver/receiver.def'
        doc = parse(self.fixture.files[key]);root = doc.find('game_entity')
        root.find('Weaponry').append(block('{place "hull_mg" {LinkBone "body"}{weapon "mg" filling "mg_round belt" 50}{gunner "driver"}{charger "driver"}}'))
        self.fixture.files[key] = doc.render().encode()
        originals = dict(self.fixture.files)
        result, root, model = self.compile({'remove_module':{'schema':1,'mount':'azimuth'}})
        self.assertEqual(result['metadata']['crew_roles'], ['driver'])
        self.assertEqual(root.find('Chassis').render(), parse(originals[key]).find('game_entity').find('Chassis').render())
        self.assertEqual(root.find('mass').args, ['19000'])
        self.assertEqual([p.args[0] for p in root.find('Weaponry').find_all('place')], ['hull_mg'])
        self.assertNotIn('azimuth', model['bones'])
        self.assertIn('body', model['bones'])
        self.assertNotIn('support_vehicle', result['metadata'])
        self.assertEqual(result['metadata']['state_profile']['inventory_policy'], 'recipient')
        self.assertFalse(result['runtime_verified'])
        self.assertEqual(self.fixture.files, originals)

    def test_root_nested_joint_and_protected_systems_cannot_be_removed(self):
        for mount in ('frame', 'elevation', '@free', '', 'missing'):
            with self.subTest(mount=mount), self.assertRaises(ValueError):
                self.compile({'remove_module':{'schema':1,'mount':mount}}, name='mw_invalid_'+str(len(mount)))
        key='entity/receiver/receiver.mdl'
        self.fixture.files[key]=self.fixture.files[key].replace(b'{bone "breech"', b'{bone "fuel"}{bone "breech"')
        with self.assertRaisesRegex(ValueError,'sistemas del chasis'):
            self.compile({'remove_module':{'schema':1,'mount':'azimuth'}})

    def test_destroyed_state_with_turret_already_lost_stays_valid(self):
        from workshop.compiler import _remove_bone
        for tag in ('x','xx'):
            folder=f'entity/receiver_{tag}/'
            self.fixture.files[folder+f'receiver_{tag}.def']=b'{game_entity {extension "wreck.mdl"}}'
            model=parse_model(self.fixture.files['entity/receiver/receiver.mdl'])
            if tag=='xx':_remove_bone(model,'azimuth')
            self.fixture.files[folder+f'receiver_{tag}.mdl']=model['document'].render().encode()
            self.fixture.files[folder+'wreck.mdl']=model['document'].render().encode()
            for path, raw in list(self.fixture.files.items()):
                if path.startswith('entity/receiver/') and path.endswith('.ply'):
                    self.fixture.files[folder+Path(path).name]=raw
        # The fixture resolver knows these native state template names too.
        result, _, _=self.compile({'remove_module':{'schema':1,'mount':'azimuth'}})
        for tag in ('x','xx'):
            path=self.folder/result['id']/'resource/entity/-vehicle/workshop'/result['id']/(result['id']+'_'+tag+'.mdl')
            self.assertNotIn('azimuth',parse_model(path.read_bytes())['bones'])

    def test_complete_artillery_keeps_carriage_and_native_joints(self):
        result, root, model=self.compile({'assembly_scope':'complete','recipient_mount':'azimuth'},donor='towed',name='mw_complete')
        mapping=result['metadata']['bone_mapping']
        self.assertIn('body',mapping)
        self.assertTrue(model['bones'][mapping['body']]['assets'])
        self.assertEqual(root.find('Chassis').find('speed').args,['30'])
        self.assertEqual(root.find('mass').args,['19000'])
        contract=result['metadata']['weapon_assembly']
        self.assertEqual(contract['root'],'frame')
        self.assertIn('elevation',contract['joints'])
        self.assertIn('barrel_volume',contract['volumes'])
        self.assertFalse(contract['runtime_verified'])

    def test_complete_scope_rejects_tanks_multiple_roots_and_powertrain(self):
        with self.assertRaisesRegex(ValueError,'artillería'):
            self.compile({'assembly_scope':'complete'},donor='donor')
        key='entity/towed/towed.mdl';original=self.fixture.files[key]
        self.fixture.files[key]=original.replace(b'{skeleton',b'{skeleton {bone "sibling"}')
        with self.assertRaisesRegex(ValueError,'raíz'):
            self.compile({'assembly_scope':'complete'},donor='towed')
        self.fixture.files[key]=original.replace(b'{bone "breech"',b'{bone "engine"}{bone "breech"')
        with self.assertRaisesRegex(ValueError,'propulsión'):
            self.compile({'assembly_scope':'complete'},donor='towed')

    def test_draft_rejects_duplicate_conflicting_and_malformed_removals(self):
        for rows in (None,['azimuth'],[{}],[{'mount':'@free'}],[{'mount':'azimuth'}]*2):
            with self.subTest(rows=rows),self.assertRaises(ValueError):
                specification({'assembly_draft':{'schema':1,'removals':rows}})
        self.assertEqual(specification({'assembly_draft':{'schema':1,'removals':[{'mount':'azimuth'}],
                'turrets':[{'recipient_mount':'azimuth','donor_template':'donor'}]}})['removals'],[{'mount':'azimuth'}])
        for extra in ({'donor_depot':'stored'}, {'extra_gun':True}):
            row={'donor_template':'towed','donor_eid':'0x9010','assembly_scope':'complete',**extra}
            guns=[{'schema':1,'donor_template':'towed','donor_eid':'0x9010','recipient_place':'gun','donor_place':'gun'}] if extra.get('extra_gun') else []
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                specification({'assembly_draft':{'schema':1,'turrets':[row],'guns':guns}})

    def test_removal_draft_preserves_spent_inventory_fuel_veterans_and_replays(self):
        index=self.index()
        tr={'assembly_draft':{'schema':1,'removals':[{'mount':'azimuth'}],'turrets':[],'guns':[]}}
        first=compile_assembly(index,'receiver','receiver',tr,self.folder/'first','mw_design')
        again=compile_assembly(index,'receiver','receiver',tr,self.folder/'again','mw_design')
        self.assertEqual(first['metadata']['file_hashes'],again['metadata']['file_hashes'])
        metadata=first['metadata'];self.assertEqual(metadata['draft_steps'][0]['kind'],'removal')
        raw=SCENE.replace('tank_hull','receiver').replace('wheeled_donor','donor')
        scene=parse(raw);campaign=scene.find('campaign')
        vehicle=next(e for e in campaign.find_all('Entity') if '0x9000' in e.args)
        for node in [*vehicle.find_all('Component'),*vehicle.find_all('Armor')]:vehicle.remove(node)
        vehicle.append(block('{Weaponry {State "main_slot" {ammo 0}}}'))
        save=make_save(self.folder/'copy.sav',scene.render());original=save.read_bytes();snapshot=load_campaign(save)
        plan=plan_draft(snapshot,'0x9000','mw_design',metadata['crew_slots'],metadata['draft_steps'],state_profile=metadata['state_profile'])
        after=parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign')
        self.assertEqual(plan['consumed_eids'],['0x9000'])
        new=next(e for e in after.find_all('Entity') if plan['new_eid'] in e.args)
        self.assertFalse(new.find_all('Weaponry'))
        for head in ('Chassis',):
            self.assertEqual([n.render() for n in new.find_all(head)],[n.render() for n in vehicle.find_all(head)])
        inventory=next(n for n in after.find_all('Inventory') if n.args[0]==plan['new_eid'])
        before=next(n for n in campaign.find_all('Inventory') if n.args[0]=='0x9000')
        self.assertEqual([n.render() for n in inventory.children],[n.render() for n in before.children])
        humans=lambda c:sorted(e.render() for e in c.find_all('Entity') if e.args and e.args[0].startswith('mp/'))
        self.assertEqual(humans(after),humans(campaign))
        self.assertEqual(save.read_bytes(),original)

    def test_empty_final_damage_contract_removes_all_retired_components(self):
        from workshop.campaign import _edited_state
        entity=block('{Entity "receiver" 0x1 {Component "azimuth" {health 0.2}}{Armor {wounds {hit "turret_volume" 3}}}}')
        emitted=_edited_state(entity,{'allowed_damage_ids':[]},True)
        self.assertFalse(emitted.find_all('Component'))
        self.assertFalse(emitted.find('Armor').find('wounds').children)

    def test_remove_then_mount_complete_draft_is_reproducible(self):
        index=self.index();tr={'assembly_draft':{'schema':1,'removals':[{'mount':'azimuth'}],
            'turrets':[{'donor_template':'towed','donor_eid':'0x9010','recipient_mount':'@free','assembly_scope':'complete'}],'guns':[]}}
        result=compile_assembly(index,'receiver','towed',tr,self.folder/'one','mw_rearmed')
        again=compile_assembly(index,'receiver','towed',tr,self.folder/'two','mw_rearmed')
        self.assertEqual(result['metadata']['file_hashes'],again['metadata']['file_hashes'])
        self.assertEqual(len(result['metadata']['weapon_assemblies']),1)
        self.assertEqual(result['metadata']['removed_modules'][0]['template'],'mw_rearmed_parts')
        self.assertTrue(result['metadata']['validation']['dependencies']['passed'])
        scene=parse(SCENE.replace('tank_hull','receiver').replace('wheeled_donor','towed'))
        for entity in scene.find('campaign').find_all('Entity'):
            for node in [*entity.find_all('Component'),*entity.find_all('Armor')]:entity.remove(node)
        save=make_save(self.folder/'combined.sav',scene.render());original=save.read_bytes()
        snapshot=load_campaign(save);meta=result['metadata']
        plan=plan_draft(snapshot,'0x9000','mw_rearmed',meta['crew_slots'],meta['draft_steps'],state_profile=meta['state_profile'])
        after=parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign')
        self.assertEqual(plan['consumed_eids'],['0x9000','0x9010'])
        self.assertEqual(sum(e.args[0]=='mw_rearmed' for e in after.find_all('Entity')),1)
        self.assertEqual(save.read_bytes(),original)

    def test_pending_free_mount_uses_removed_design_not_original_turret_roof(self):
        from workshop.app import AppState
        from workshop.geometry import free_anchor
        index=self.index();state=AppState(self.folder/'workspace');state.index=index
        self.addCleanup(state.pool.shutdown,wait=True)
        self.addCleanup(state.clear_portrait_previews)
        tr={'assembly_draft':{'schema':1,'removals':[{'mount':'azimuth'}],'turrets':[],'guns':[]}}
        data=state.design_preview_data({'hull':'receiver','transform':tr,
            'pending':{'kind':'turret','donor_template':'towed','recipient_mount':'@free','assembly_scope':'complete'}})
        old=index.resolve_template('receiver');donor=index.resolve_template('towed')
        obsolete=free_anchor(index,parse_model(index.read(old['model_path'])),old['model_path'],
            parse_model(index.read(donor['model_path'])),donor['model_path'],'frame')
        self.assertLess(data['hull']['free_anchor'][2],obsolete[2])
        self.assertEqual(data['turret']['anchor_name'],'frame')

    def test_installed_module_pose_and_limits_need_no_donor_and_keep_other_weapons(self):
        index=self.index()
        tr={'assembly_draft':{'schema':1,'turrets':[{'native':True,'recipient_mount':'azimuth',
            'pose':{'position':[3,4,5],'rotation':[0,0,12]},'joint_limits':{'elevation':[-6,18]}}],'guns':[]}}
        result=compile_assembly(index,'receiver','receiver',tr,self.folder/'native','mw_native')
        model=parse_model((self.folder/'native/resource'/result['model_path']).read_bytes())
        before=parse_model(self.fixture.files['entity/receiver/receiver.mdl'])
        self.assertEqual(model['bones']['body']['world'],before['bones']['body']['world'])
        self.assertNotEqual(model['bones']['azimuth']['world'],before['bones']['azimuth']['world'])
        self.assertEqual(model['bones']['elevation']['node'].find('limits').args,['-6','18'])
        self.assertEqual(result['metadata']['draft_steps'][0]['kind'],'module_edit')
        self.assertIsNone(result['metadata']['draft_steps'][0]['donor_eid'])
        self.assertEqual(result['metadata']['state_profile']['weapon_places'],['coax_slot','main_slot'])
        raw=SCENE.replace('tank_hull','receiver').replace('wheeled_donor','donor')
        save=make_save(self.folder/'native.sav',raw);snapshot=load_campaign(save)
        metadata=result['metadata'];plan=plan_draft(snapshot,'0x9000','mw_native',metadata['crew_slots'],metadata['draft_steps'],state_profile=metadata['state_profile'])
        after=apply_plan(snapshot,plan)
        self.assertEqual(plan['consumed_eids'],['0x9000'])
        self.assertIn(b'mw_native',after['campaign.scn'])

    def test_removed_equipped_module_source_keeps_new_gun_and_roundtrips_from_depot(self):
        index=self.index()
        tr={'assembly_draft':{'schema':1,'removals':[{'mount':'azimuth'}],'turrets':[],
            'guns':[{'schema':1,'recipient_place':'main_slot','donor_place':'main_slot',
                'donor_template':'donor','donor_eid':'0x9010','pose':{'position':[0,0,0],'rotation':[0,0,0]}}]}}
        result=compile_assembly(index,'receiver','donor',tr,self.folder/'removed','mw_equipped')
        removed=result['metadata']['removed_modules'][0]
        view=index.with_overlay(self.folder/'removed');self.addCleanup(view.close)
        from workshop.compiler import _load_entity
        source=_load_entity(view,removed['template'])[2]
        weapon=source.find('Weaponry').find_all('place')[0].find('weapon').args[0]
        self.assertEqual(weapon,'donor_weapon')
        final=_load_entity(view,result['id'])[2]
        self.assertFalse(any(w.find_all('place') for w in final.find_all('Weaponry')))
        restored=compile_assembly(view,'receiver',removed['template'],{'recipient_mount':'azimuth',
            'donor_mount':removed['source_mount']},self.folder/'restored','mw_restored')
        installed=parse((self.folder/'restored/resource'/restored['definition_path']).read_bytes()).find('game_entity')
        self.assertIn('donor_weapon',[p.find('weapon').args[0] for w in installed.find_all('Weaponry') for p in w.find_all('place') if p.find('weapon')])


if __name__=='__main__':unittest.main()
