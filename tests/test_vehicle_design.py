import copy
import json
import tempfile
import unittest
from pathlib import Path
from tests.test_gun_assembly import Resources
from tests.test_campaign import SCENE,make_save
from workshop.resources import ResourceIndex
from workshop.compiler import compile_assembly
from workshop.campaign import load_campaign,plan_draft,apply_plan,CampaignError
from workshop.syntax import parse
from workshop.geometry import read_mesh,mesh_materials


class VehicleDesignTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        r=Resources();r.files['set/stuff/shell/mg_round.belt.ammo']=b'{ammo {entity "mg_bullet"}}'
        for path,raw in list(r.files.items()):
            if path.startswith('entity/donor/'):
                new=path.replace('entity/donor/','entity/other/').replace('/donor.','/other.')
                r.files[new]=raw.replace(b'donor.mdl',b'other.mdl')
        for path,raw in r.files.items():
            p=self.root/'game/resource'/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        self.index=ResourceIndex(self.root/'game').build();self.addCleanup(self.index.close)

    def rows(self):
        return [{'schema':1,'recipient_place':r,'donor_place':r,'donor_template':d,'donor_eid':e,
            'pose':{'position':[i,0,0],'rotation':[0,0,i*5]}} for i,(r,d,e) in enumerate([
                ('main_slot','donor','0x9010'),('coax_slot','other','0x9020')])]

    def snapshot(self):
        doc=parse(SCENE);campaign=doc.find('campaign')
        for entity in campaign.find_all('Entity'):
            for node in [*entity.find_all('Component'),*entity.find_all('Armor')]:entity.remove(node)
        scene=doc.render().replace('tank_hull','receiver').replace('wheeled_donor','donor').replace('truck_third','other')
        scene=scene.replace('{item "old_ammo" "ammo" 3 {cell 0 0}}','{item "short_round ap" 3}{item "mg_round belt" 23}{item "toolkit" 1}')
        source=make_save(self.root/'source.sav',scene);return load_campaign(source),source

    def test_distinct_donors_compile_roundtrip_and_single_atomic_plan_preserves_people(self):
        tr={'assembly_draft':{'schema':1,'guns':self.rows()}}
        original=copy.deepcopy(tr);r=compile_assembly(self.index,'receiver','donor',tr,self.root/'compiled','mw_design')
        self.assertEqual(tr,original);self.assertEqual(len(r['metadata']['draft_steps']),2)
        first,last=r['metadata']['draft_steps']
        self.assertEqual(first['profile']['gun_swap']['place'],'main_slot')
        self.assertEqual(last['profile']['gun_swap']['place'],'coax_slot')
        self.assertNotIn('long_round ap',' '.join(last['profile']['gun_swap']['ammo_items']))
        self.assertIsNot(last['profile'],r['metadata']['state_profile'])
        self.assertFalse(any('/workshop/mw_draft_' in p for p in r['metadata']['source_hashes']))
        again=compile_assembly(self.index,'receiver','donor',tr,self.root/'again','mw_design')
        self.assertEqual(r['metadata']['file_hashes'],again['metadata']['file_hashes'])
        snapshot,source=self.snapshot();before=source.read_bytes()
        plan=plan_draft(snapshot,'0x9000','mw_design',3,r['metadata']['draft_steps'],state_profile=r['metadata']['state_profile'])
        self.assertEqual(plan['consumed_eids'],['0x9000','0x9010','0x9020'])
        self.assertEqual(len(plan['crew_eids'])+sum(len(s['eids']) for s in plan['reserve_squads']),5)
        after=parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign')
        self.assertEqual(len(after.find_all('Entity')),1)
        inventory=next(n for n in after.find_all('Inventory') if n.args==[plan['new_eid']]).render()
        self.assertIn('toolkit',inventory);self.assertIn('long_round ap',inventory);self.assertNotIn('short_round ap',inventory)
        self.assertEqual(source.read_bytes(),before)
        tampered=copy.deepcopy(plan);tampered['consumed_eids'].pop()
        with self.assertRaises(CampaignError):apply_plan(snapshot,tampered)

    def test_no_donor_design_clears_persisted_seat_bindings(self):
        from workshop.syntax import block
        snapshot,_=self.snapshot()
        profile={'inventory_policy':'recipient','weaponry_policy':'recipient','allowed_damage_ids':[]}
        entity=next(e for e in snapshot._scene.find('campaign').find_all('Entity') if e.args[-1]=='0x9000')
        entity.append(block('{Placer {place "stale"}}'))
        plan=plan_draft(snapshot,'0x9000','mw_seats',3,[],state_profile=profile)
        after=parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign')
        rebuilt=next(e for e in after.find_all('Entity') if e.args[0]=='mw_seats')
        self.assertIsNone(rebuilt.find('Placer'))

    def test_final_crew_positions_and_exclusions_survive_saved_guns(self):
        from workshop.geometry import parse_model,point
        path=self.root/'game/resource/entity/receiver/receiver.def'
        path.write_bytes(path.read_bytes().replace(b'{place "gunner" {group "crew"}}',b'{place "gunner" {group "crew"}{LinkBone "gunner"}}'))
        path=self.root/'game/resource/entity/receiver/receiver.mdl'
        path.write_bytes(path.read_bytes().replace(b'{bone "body"',b'{bone "gunner" {Position 1 2 3}}{bone "body"',1))
        self.index.build()
        for guns in ([],self.rows()):
            with self.subTest(guns=bool(guns)):
                tr={'assembly_draft':{'schema':1,'guns':guns},'crew_seats':{'gunner':{'position':[7,8,9],'space':'module'}},'crew_exclude':['charger']}
                result=compile_assembly(self.index,'receiver','donor',tr,self.root/('crew'+str(len(guns))),'mw_design')
                model=parse_model((Path(result['output_dir'])/'resource'/result['model_path']).read_bytes())
                self.assertEqual(point(model['bones']['gunner']['world'],[0,0,0]),[7,8,9])
                self.assertNotIn('charger',result['metadata']['crew_roles'])
                self.assertIn('gunner',result['metadata']['crew_roles'])

    def test_each_saved_turret_replaces_its_own_chassis_mount(self):
        # A chassis with a second turret (like the Nb.Fz): both take a donor.
        folder=self.root/'game/resource/entity/receiver'
        model=(folder/'receiver.mdl').read_bytes().replace(b'{bone revolute "azimuth"',
            b'{bone revolute "aux_turret1" {Position -6 0 9}{VolumeView "tower.ply"}{bone "mgun_aux" {VolumeView "coax.ply"}}}{bone revolute "azimuth"',1)
        (folder/'receiver.mdl').write_bytes(model)
        definition=(folder/'receiver.def').read_bytes().replace(b'{place "coax_slot"',
            b'{place "aux_slot" {LinkBone "mgun_aux"}{weapon "mg" filling "mg_round belt" 50}{gunner "charger"}}{place "coax_slot"',1)
        (folder/'receiver.def').write_bytes(definition)
        crew=self.root/'game/resource/properties/tank_crew.ext';crew.parent.mkdir(parents=True,exist_ok=True)
        crew.write_bytes(b'{placer {place "gunner2"} {place "charger2"}}')  # native numbered roles
        index=ResourceIndex(self.root/'game').build();self.addCleanup(index.close)
        pose={'position':[0,0,0],'rotation':[0,0,0]}
        draft={'schema':1,'guns':[],'turrets':[
            {'donor_template':'donor','donor_eid':'0x9010','recipient_mount':'azimuth','donor_mount':'azimuth','pose':pose},
            {'donor_template':'other','donor_eid':'0x9020','recipient_mount':'aux_turret1','donor_mount':'azimuth','pose':pose}]}
        r=compile_assembly(index,'receiver','donor',{'assembly_draft':draft},self.root/'two','mw_two')
        self.assertEqual([(s['kind'],s['donor_template']) for s in r['metadata']['draft_steps']],[('turret','donor'),('turret','other')])
        model=parse((Path(r['output_dir'])/'resource'/r['model_path']).read_bytes())
        names={n.args[-1] for n in model.walk('bone') if n.args}
        self.assertNotIn('aux_turret1',names,'the second chassis turret was replaced')
        self.assertNotIn('mgun_aux',names)
        with self.assertRaisesRegex(Exception,'una sola vez'):
            compile_assembly(index,'receiver','donor',{'assembly_draft':{**draft,'turrets':[draft['turrets'][0],{**draft['turrets'][1],'recipient_mount':'azimuth'}]}},
                             self.root/'twice','mw_twice')

    def test_limits_on_the_design_turret_and_on_replaced_joints_do_not_stop_the_build(self):
        from workshop.compiler import _load_entity
        from workshop.geometry import parse_model
        from workshop.articulation import weapon_joints
        info,_,root=_load_entity(self.index,self.index.resolve_entity('donor')['definition_path'])
        joints=[j['bone'] for j in weapon_joints(parse_model(self.index.read(info['model_path'])),root)]
        self.assertTrue(joints)
        tr={'assembly_draft':{'schema':1,'guns':[],'turret':{'donor_template':'donor','donor_eid':'0x9010',
            'recipient_mount':'azimuth','donor_mount':'azimuth','pose':{'position':[0,0,0],'rotation':[0,0,0]}}},
            'joint_limits':{'turret':{joints[0]:[-25,25]},'hull':{'replaced_joint':[-5,5]}}}
        r=compile_assembly(self.index,'receiver','donor',tr,self.root/'limits','mw_limits')
        model=parse_model((Path(r['output_dir'])/'resource'/r['model_path']).read_bytes())
        self.assertIn(['-25','25'],[b['node'].find('limits').args for b in model['bones'].values() if b['node'].find('limits')])
        skipped=[d for d in r['diagnostics'] if d['code']=='joint_limits_skipped']
        self.assertEqual([d['joints'] for d in skipped],[['replaced_joint']])

    def test_catalog_parts_change_the_save_vehicle_without_consuming_units(self):
        rows=self.rows()
        for row in rows:row.pop('donor_eid');row['donor_catalog']=True
        tr={'assembly_draft':{'schema':1,'guns':rows}}
        r=compile_assembly(self.index,'receiver','donor',tr,self.root/'compiled','mw_design')
        self.assertTrue(all(step['donor_catalog'] for step in r['metadata']['draft_steps']))
        snapshot,_=self.snapshot();plan=plan_draft(snapshot,'0x9000','mw_design',3,r['metadata']['draft_steps'],state_profile=r['metadata']['state_profile'])
        self.assertEqual(plan['consumed_eids'],['0x9000'])
        after=parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign')
        self.assertEqual(len(after.find_all('Entity')),3)

    def test_same_donor_consumed_once_and_turret_plus_gun_compiles(self):
        rows=self.rows();rows[1].update(donor_template='donor',donor_eid='0x9010')
        for row in rows:row['pose']['pivot']=[4,0,0]
        tr={'assembly_draft':{'schema':1,'turret':{'donor_template':'donor','donor_eid':'0x9010',
            'recipient_mount':'azimuth','donor_mount':'azimuth','pose':{'position':[0,0,0],'rotation':[0,0,0]}},'guns':rows}}
        r=compile_assembly(self.index,'receiver','donor',tr,self.root/'compiled','mw_design')
        snapshot,_=self.snapshot();plan=plan_draft(snapshot,'0x9000','mw_design',3,r['metadata']['draft_steps'],state_profile=r['metadata']['state_profile'])
        self.assertEqual(plan['consumed_eids'],['0x9000','0x9010']);apply_plan(snapshot,plan)
        from workshop.gun_assembly import GunError
        with self.assertRaisesRegex(GunError,'componente de daño'):
            compile_assembly(self.index,'receiver','donor',{'gun_swaps':rows},self.root/'legacy_ambiguous','mw_legacy')

    def test_preview_and_check_share_compilation_and_pending_turret_tokens_survive(self):
        from workshop.app import AppState
        from unittest.mock import patch
        state=AppState(self.root/'workspace');state.index=self.index
        body={'hull':'receiver','transform':{'assembly_draft':{'schema':1,'guns':self.rows()}}}
        try:
            first=state.design_preview_data(body);token=first['hull']['meshes'][0]['submeshes'][0]['texture_token']
            self.assertTrue(first['crew_visibility'],'crew controls arrive with the model, before compatibility')
            excluded=copy.deepcopy(body);role=first['crew_visibility'][0]['place']
            excluded['transform']['crew_exclude']=[role]
            self.assertNotIn(role,[r['place'] for r in state.design_preview_data(excluded)['crew_visibility']])
            with patch('workshop.draft.compile_draft',side_effect=AssertionError('Duplicate compilation')),patch('workshop.registration.register_variant',return_value={}):
                checked=state.check({**body,'donor':'donor'})
                state.design_catalog(body)
                moved=copy.deepcopy(body);moved['transform']['hull']={'position':[4,0,0],'rotation':[0,0,15],'pivot':[3,0,0]}
                state.design_preview_data(moved);state.design_catalog(moved)
                invalid=copy.deepcopy(body);invalid['transform']['hull']={'pivot':[float('nan'),0,0]}
                with self.assertRaises(ValueError):state.design_context(invalid)
                with self.assertRaises(ValueError):state.design_catalog(invalid)
            self.assertTrue(checked['ok'],checked)
            state.design_preview_data({**body,'pending':{'kind':'turret','donor_template':'donor'}})
            self.assertIn(token,[c['token'] for c in state.design_previews.values()])
            contexts=list(state.design_previews.values());state.clear_portrait_previews()
            self.assertTrue(all(not Path(c['temp'].name).exists() for c in contexts))
        finally:state.clear_portrait_previews();state.pool.shutdown(wait=True)

    def test_seed_recipe_is_rebuilt_without_consuming_its_historical_donor(self):
        tr={'assembly_draft':{'schema':1,'seed':{'recipient':'receiver','donor':'donor',
            'transform':{'gun_swap':{'schema':1,'recipient_place':'main_slot','donor_place':'main_slot'}}},'guns':[self.rows()[1]]}}
        r=compile_assembly(self.index,'receiver','other',tr,self.root/'seeded','mw_design')
        snapshot,_=self.snapshot();plan=plan_draft(snapshot,'0x9000','mw_design',3,r['metadata']['draft_steps'],state_profile=r['metadata']['state_profile'])
        self.assertEqual(plan['consumed_eids'],['0x9000','0x9020']);apply_plan(snapshot,plan)

    def test_generate_prepares_one_transaction_for_all_saved_donors_without_installing(self):
        from workshop.app import AppState
        from unittest.mock import patch
        state=AppState(self.root/'workspace');state.index=self.index;state.settings={'game_root':str(self.root/'game'),'mod_roots':[]}
        snapshot,source=self.snapshot();state.snapshot=snapshot;before=source.read_bytes()
        data=snapshot.to_dict();data['unresolved_mods']=[]
        def register(index,result,output,identifier,name):
            (Path(output)/'mod.info').write_text('{mod {name "Fixture"}}')
            return {'id':identifier}
        try:
            # This transaction is entirely inside the temporary fixture game;
            # a player's unrelated running battle must not affect the test.
            with patch.object(state,'closed',return_value={}),patch('workshop.transactions.assert_game_closed'),patch.object(state,'campaign_data',return_value=data),patch('workshop.registration.register_variant',side_effect=register):
                result=state.generate({'recipient_eid':'0x9000','mode':'design','transform':{'assembly_draft':{'schema':1,'guns':self.rows()}}})
            self.assertEqual(result['plan']['consumed_eids'],['0x9000','0x9010','0x9020'])
            self.assertTrue(result['transaction']['journal_path']);self.assertEqual(source.read_bytes(),before)
            self.assertFalse((self.root/'game/mods/modular_workshop').exists())
        finally:state.clear_portrait_previews();state.pool.shutdown(wait=True)

    def test_skinned_material_is_read_across_a_bone_table(self):
        import struct
        from tests.test_geometry import eply
        prefix=b'MESH'+struct.pack('<5I',0x112,0,1,0,3)+bytes([9])+b'track.mtl'+b'BONES'+bytes(32)
        raw=b'EPLY'+prefix+eply()[4:]
        self.assertEqual(read_mesh(raw)['submeshes'][0]['material'],'track.mtl')
        self.assertEqual(mesh_materials(raw)[0][1],'track.mtl')

    def test_nonstructural_smoke_cannot_lift_a_turret_seat(self):
        from workshop.geometry import parse_model,mount_seat
        r=Resources();path='entity/receiver/receiver.mdl';raw=r.files[path]
        base=parse_model(raw);expected=mount_seat(r,base,path,'azimuth')
        raw=raw.replace(b'{bone "body"',b'{bone "cw_smoke_0_root" {Position 0 0 100}{VolumeView "hull.ply"}}{bone "body"')
        self.assertEqual(mount_seat(r,parse_model(raw),path,'azimuth'),expected)


if __name__=='__main__':unittest.main()
