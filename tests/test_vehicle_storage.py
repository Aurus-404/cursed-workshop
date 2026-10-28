import tempfile,unittest,zipfile,json
from pathlib import Path
from unittest.mock import patch
from workshop.campaign import load_campaign,apply_plan,build_save_copy,_one,_entity_nodes,_node_eids
from workshop.vehicle_storage import plan_storage,plan_restoration,VehicleStorage,StorageError
from workshop.transactions import prepare_transaction,commit_transaction,rollback_transaction
from tests.test_campaign import make_save,SCENE,STATUS

class StorageTests(unittest.TestCase):
    def test_storage_and_restore_keep_existing_addon_after_other_mods(self):
        status=STATUS.replace('{mods ','{mods "modular_workshop:0" ')
        self.original=load_campaign(make_save(self.source,status=status))
        current=self.stored()
        self.assertEqual(current.mods,['mod_2905667604:0','other_mod:3','modular_workshop:0'])
        after,_=self.restored(current)
        self.assertEqual(after.mods,current.mods)

    def setUp(self):
        guard=patch('workshop.transactions.running_game_processes',return_value=[]);guard.start();self.addCleanup(guard.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=make_save(self.root/'campaign.sav');self.original=load_campaign(self.source)
    def stored(self):
        replacements=apply_plan(self.original,plan_storage(self.original,'0x9000'))
        self.source.write_bytes(build_save_copy(self.original,replacements))
        return load_campaign(self.source)
    def restored(self,current):
        archive=self.root/'original.sav';archive.write_bytes(self.original._zip_bytes)
        original=load_campaign(archive)
        plan=plan_restoration(current,original,'0x9000')
        self.source.write_bytes(build_save_copy(current,apply_plan(current,plan)))
        return load_campaign(self.source),plan
    def test_whole_unit_and_exact_equipment_survive_round_trip(self):
        before=self.original.to_dict();current=self.stored()
        self.assertFalse({'0x9000','0x9001','0x9002'}&{u['eid'] for u in current.units})
        after,plan=self.restored(current)
        old=_entity_nodes(_one(self.original._scene,'campaign'));new=_entity_nodes(_one(after._scene,'campaign'))
        self.assertEqual(set(new),set(old))
        for eid in old:self.assertEqual(old[eid].render(),new[eid].render())
        campaign=_one(after._scene,'campaign')
        for node in _one(self.original._scene,'campaign').children:
            if node.head in ('Inventory','Link'):self.assertIn(node.render(),campaign.render())
        self.assertEqual(plan['crew_slots'],2)
        squad=next(s for s in after.squads if '0x9000' in s['eids']);self.assertEqual(squad['stage'],'')
        with zipfile.ZipFile(self.source) as a:self.assertEqual(a.read('point_2_1'),b'unrelated binary map data\x00\xff')
    def test_cross_nation_preserves_foreign_squad_crew_damage_and_inventories(self):
        scene=SCENE.replace('mp/ger/','mp/rus/').replace('{"tank_hull" "stage_2"','{"tank_hull(rus)" "stage_2"')
        self.original=load_campaign(make_save(self.source,scene,STATUS.replace('army ger','army rus')))
        self.stored();source_after=self.source.read_bytes()
        destination=make_save(self.root/'destination.sav')
        target=load_campaign(destination);before=_entity_nodes(_one(target._scene,'campaign'))
        archive=self.root/'archive.sav';archive.write_bytes(self.original._zip_bytes)
        plan=plan_restoration(target,load_campaign(archive),'0x9000')
        destination.write_bytes(build_save_copy(target,apply_plan(target,plan)))
        after=load_campaign(destination);entities=_entity_nodes(_one(after._scene,'campaign'))
        self.assertEqual(after.army,'ger');self.assertEqual(after.mods,target.mods)
        self.assertEqual(self.source.read_bytes(),source_after)
        for eid,node in before.items():self.assertEqual(entities[eid].render(),node.render())
        for old,new in plan['eid_mapping'].items():
            self.assertNotEqual(old,new)
            expected=_entity_nodes(_one(self.original._scene,'campaign'))[old]
            actual=entities[new]
            self.assertEqual(actual.args[0],expected.args[0])
            self.assertEqual([n.render() for n in actual.children if n.head!='MID'],
                             [n.render() for n in expected.children if n.head!='MID'])
        squad=next(s for s in after.squads if plan['new_eid'] in s['vehicle_eids'])
        self.assertEqual(squad['template'],'tank_hull(rus)');self.assertEqual(squad['stage'],'')
        from workshop.vehicle_storage import _remap
        for node in _one(self.original._scene,'campaign').children:
            if node.head in ('Inventory','Link') and _node_eids(node)&{'0x9000','0x9001','0x9002'}:
                expected=node.clone();_remap(expected,plan['eid_mapping'])
                self.assertIn(expected.render(),after._scene.render())
    def test_collision_remaps_all_links_and_keeps_new_campaign_progress(self):
        current=self.stored()
        # New soldiers may reuse an archived entity id and MID while vehicle is away.
        scene=current._scene.clone();campaign=_one(scene,'campaign')
        from workshop.syntax import block
        campaign.append(block('{Human "mp/ger/mid/rifleman" 0x9001 {MID 20} {Score 999}}'))
        _one(campaign,'CampaignSquads').append(block('{"single_rifleman(ger)" "stage_1" 0x9001}'))
        self.source.write_bytes(build_save_copy(current,{'campaign.scn':scene.render().encode()}));current=load_campaign(self.source)
        after,plan=self.restored(current)
        self.assertNotEqual(plan['eid_mapping']['0x9001'],'0x9001')
        self.assertIn('{Score 999}',_entity_nodes(_one(after._scene,'campaign'))['0x9001'].render())
        mids=[n.find('MID').args[0] for n in _entity_nodes(_one(after._scene,'campaign')).values() if n.find('MID')]
        self.assertEqual(len(mids),len(set(mids)))
        self.assertIn('{Link '+plan['eid_mapping']['0x9001']+' {0x9000 "driver"}}',_one(after._scene,'campaign').render())
    def test_last_vehicle_with_empty_seats_and_all_humans_can_be_stored(self):
        scene='''{campaign {Entity "tank_hull" 0x9000 {MID 20}} {Human "tankman" 0x9001}
        {CampaignSquads {"tank_hull" "" 0x9000 0x9001 0xffffffff}}}'''
        self.source=make_save(self.source,scene);self.original=load_campaign(self.source)
        current=self.stored();self.assertEqual(current.units,[])
        after,plan=self.restored(current);self.assertEqual(plan['crew_slots'],2)
        self.assertIn('0xffffffff',after.squads[0]['eids'])
    def test_padded_entity_references_remap_without_touching_item_names(self):
        from workshop.vehicle_storage import _remap
        from workshop.syntax import block
        node=block('{Link 0x0000900A {0x900b "0x900a ammunition"}}')
        _remap(node,{'0x900a':'0x8000','0x900b':'0x8001'})
        self.assertEqual(node.render(),'{Link 0x8000 {0x8001 "0x900a ammunition"}}')

    def test_corrupt_entry_never_prevents_opening_other_storage_or_campaign(self):
        storage=VehicleStorage(self.root);identifier,folder=storage.allocate()
        for contents in ('broken interrupted metadata','[]','null'):
            with self.subTest(contents=contents):
                (folder/'entry.json').write_text(contents)
                self.assertEqual(storage.items(self.source),[])
                self.assertEqual(len(storage.problems),1)
                self.assertEqual((folder/'entry.json').read_text(),contents)

    def test_unrecognized_references_refuse_before_save_changed(self):
        source=make_save(self.source,SCENE.replace('{CampaignSquads','{Unknown 0x9001} {CampaignSquads'))
        raw=source.read_bytes();snapshot=load_campaign(source)
        with self.assertRaises(ValueError):apply_plan(snapshot,plan_storage(snapshot,'0x9000'))
        self.assertEqual(raw,source.read_bytes())
    def test_foreign_boarder_stays_in_own_squad_without_dangling_vehicle_link(self):
        self.source=make_save(self.source,SCENE.replace('{CampaignSquads','{Link 0x9030 {0x9000 "passenger1"}} {CampaignSquads'))
        self.original=load_campaign(self.source);current=self.stored()
        self.assertIn('0x9030',{u['eid'] for u in current.units})
        after,_=self.restored(current)
        self.assertNotIn('{Link 0x9030 {0x9000',after._scene.render())
    def test_durable_manifest_tracks_commit_and_restore_without_post_commit_flag(self):
        storage=VehicleStorage(self.root);identifier,folder=storage.allocate()
        def prepare(root,snapshot,plan):
            addon=root/'modular_workshop';addon.mkdir(parents=True)
            (addon/'mod.info').write_text('{mod {name "fixture"}}')
            (addon/'kept.def').write_text('preserved recipe and model')
            return prepare_transaction(root,snapshot,plan,addon_directory=addon)
        tx=prepare(folder/'store',self.original,plan_storage(self.original,'0x9000'))
        storage.create(identifier,self.source,self.original.units[0],[],tx)
        self.assertEqual(storage.items(self.source),[])
        destination=self.root/'game/mods/modular_workshop'
        with patch('workshop.transactions.assert_game_closed'):
            commit_transaction(tx['journal_path'],addon_destination=destination,save_destination=self.source,allow_install=True,in_place=True)
        storage=VehicleStorage(self.root);self.assertEqual(len(storage.items(self.source)),1)
        # Entries created before shared storage have no origin display fields.
        entry=json.loads((folder/'entry.json').read_text())
        entry.pop('campaign_name');entry.pop('army')
        (folder/'entry.json').write_text(json.dumps(entry))
        foreign=self.root/'another.sav'
        self.assertEqual(storage.items(foreign)[0]['origin_campaign'],self.source.stem)
        self.assertEqual(storage.get(identifier,foreign)[1]['id'],identifier)
        folder,data,original,_=storage.get(identifier,self.source)
        current=load_campaign(self.source);plan=plan_restoration(current,original,'0x9000')
        restored=prepare(folder/'restores'/'one',current,plan);storage.restoring(identifier,self.source,restored)
        # A second application that prepared from the same old view cannot
        # claim an entry already reserved by the first transfer.
        with self.assertRaises(StorageError):VehicleStorage(self.root).restoring(identifier,self.source,restored)
        self.assertTrue(storage.items(self.source)[0]['pending'])
        with self.assertRaises(StorageError):storage.get(identifier,self.source)
        with patch('workshop.transactions.assert_game_closed'):
            rollback_transaction(storage.pending(identifier,self.source))
        self.assertFalse(storage.items(self.source)[0]['pending'])
        second=prepare(folder/'restores'/'two',current,plan);storage.restoring(identifier,self.source,second)
        with patch('workshop.transactions.assert_game_closed'):
            commit_transaction(second['journal_path'],addon_destination=destination,save_destination=self.source,allow_install=True,in_place=True)
        self.assertEqual(storage.items(self.source),[])
        with self.assertRaises(StorageError):storage.get(identifier,self.source)
        self.assertEqual(original.source_path.read_bytes(),self.original._zip_bytes)
        self.assertEqual(list((folder/'store').glob('operations/*/stage/addon/kept.def'))[0].read_text(),'preserved recipe and model')
        with self.assertRaises(StorageError):storage.get(identifier,self.root/'another.sav')




class StorageApplicationTests(unittest.TestCase):
    def setUp(self):
        StorageTests.setUp(self)
        from workshop.app import AppState
        from workshop.transactions import _tree_hashes,OWNERSHIP_FILE
        from tests.test_commander_mg import fixture
        self.game=self.root/'game';fixture(self.game);(self.game/'binaries').mkdir()
        app_guard=patch('workshop.app.require_game_idle',return_value={'running':False});app_guard.start();self.addCleanup(app_guard.stop)
        self.state=AppState(self.root/'workspace');self.state.settings={'game_root':str(self.game),'mod_roots':[]}
        self.state.snapshot=load_campaign(make_save(self.source,SCENE.replace('tank_hull','mw_fixture')))
        self.state.campaign_view=self.state.snapshot.to_dict();self.original=self.state.snapshot
        self.addCleanup(self.state.pool.shutdown,wait=True)
        addon=self.state.addon_root();model=addon/'resource/entity/-vehicle/workshop/mw_fixture';model.mkdir(parents=True)
        (addon/'mod.info').write_text('{mod {name "fixture"}}')
        (addon/'assembly_mw_fixture.json').write_text(json.dumps({'id':'mw_fixture','metadata':{'crew_slots':2}}))
        (model/'mw_fixture.def').write_text('{game_entity {placer {place "driver" {LinkBone ""}}}}')
        marker={'addon_mod':'modular_workshop:0','files':_tree_hashes(addon)}
        (addon/OWNERSHIP_FILE).write_text(json.dumps(marker))
        def finish(source,event,data):
            self.state.snapshot=load_campaign(source);self.state.campaign_view=self.state.snapshot.to_dict()
            return self.state.campaign_data()
        finish_patch=patch.object(self.state,'_storage_finish',side_effect=finish);finish_patch.start();self.addCleanup(finish_patch.stop)
    def test_application_store_reopen_return_and_prevent_double_restore(self):
        result=self.state.store_vehicle({'eid':'0x9000','source_hash':self.original.source_hash})
        self.assertEqual(result['crew'],2);identifier=result['stored']
        self.assertEqual(len(self.state.campaign_data()['stored_vehicles']),1)
        # A newly opened application sees the durable entry too.
        self.assertEqual(len(VehicleStorage(self.state.workspace).items(self.source)),1)
        result=self.state.restore_stored_vehicle({'id':identifier,'source_hash':self.state.snapshot.source_hash})
        self.assertEqual(result['eid'],'0x9000');self.assertEqual(result['campaign']['stored_vehicles'],[])
        with self.assertRaises(StorageError):self.state.restore_stored_vehicle({'id':identifier,'source_hash':self.state.snapshot.source_hash})
    def open_destination(self,army='rus'):
        path=make_save(self.root/'destination.sav',status=STATUS.replace('army ger','army '+army))
        self.state.snapshot=load_campaign(path);self.state.campaign_view=self.state.snapshot.to_dict()
        return path

    def test_shared_storage_transfers_workshop_vehicle_and_declares_addon_in_destination(self):
        stored=self.state.store_vehicle({'eid':'0x9000','source_hash':self.original.source_hash})
        source_after=self.source.read_bytes();destination=self.open_destination()
        self.assertEqual(len(self.state.campaign_data()['stored_vehicles']),1)
        result=self.state.restore_stored_vehicle({'id':stored['stored'],'source_hash':self.state.snapshot.source_hash})
        self.assertNotEqual(result['eid'],'0x9000')  # destination already owns this EID
        self.assertEqual(self.state.snapshot.army,'rus')
        self.assertIn('modular_workshop:0',self.state.snapshot.mods)
        self.assertEqual(self.source.read_bytes(),source_after)
        self.assertEqual(VehicleStorage(self.state.workspace).items(self.source),[])
        with self.assertRaises(StorageError):VehicleStorage(self.state.workspace).get(stored['stored'],destination)

    def test_unmodified_native_vehicle_can_use_storage_before_any_addon_exists(self):
        from tests.test_commander_mg import fixture
        from workshop.resources import ResourceIndex
        game=self.root/'native_game';fixture(game);(game/'binaries').mkdir()
        self.state.settings['game_root']=str(game)
        self.state.index=ResourceIndex(game).build();self.addCleanup(self.state.index.close)
        self.state.snapshot=load_campaign(make_save(self.source,SCENE.replace('tank_hull','hull')))
        self.state.campaign_view=self.state.snapshot.to_dict()
        self.assertFalse(self.state.addon_root().exists())
        stored=self.state.store_vehicle({'eid':'0x9000','source_hash':self.state.snapshot.source_hash})
        destination=self.open_destination()
        result=self.state.restore_stored_vehicle({'id':stored['stored'],'source_hash':self.state.snapshot.source_hash})
        unit=next(u for u in self.state.snapshot.units if u['eid']==result['eid'])
        self.assertEqual(unit['template'],'hull')
        self.assertNotIn('modular_workshop:0',self.state.snapshot.mods)
        self.assertEqual(len(unit['crew_eids']),2)

    def test_interrupted_cross_campaign_restore_recovers_only_from_affected_save(self):
        import os
        stored=self.state.store_vehicle({'eid':'0x9000','source_hash':self.original.source_hash})
        destination=self.open_destination();original_destination=destination.read_bytes()
        original_replace=os.replace
        def interrupt(source,target):
            result=original_replace(source,target)
            if Path(target)==destination:raise KeyboardInterrupt('power loss after destination publication')
            return result
        with patch('workshop.transactions.os.replace',side_effect=interrupt),self.assertRaises(KeyboardInterrupt):
            self.state.restore_stored_vehicle({'id':stored['stored'],'source_hash':self.state.snapshot.source_hash})
        storage=VehicleStorage(self.state.workspace)
        row=storage.items(self.source)[0]
        self.assertTrue(row['pending']);self.assertFalse(row['recoverable'])
        with self.assertRaisesRegex(StorageError,'partida del traslado'):storage.pending(stored['stored'],self.source)
        self.assertNotEqual(destination.read_bytes(),original_destination)
        self.state.snapshot=load_campaign(destination);self.state.campaign_view=self.state.snapshot.to_dict()
        self.state.recover_stored_vehicle({'id':stored['stored'],'source_hash':self.state.snapshot.source_hash})
        self.assertEqual(destination.read_bytes(),original_destination)
        self.assertFalse(storage.items(destination)[0]['pending'])
        self.state.restore_stored_vehicle({'id':stored['stored'],'source_hash':self.state.snapshot.source_hash})
        self.assertEqual(storage.items(self.source),[])

    def test_wrong_version_or_missing_source_mod_keeps_destination_and_storage(self):
        stored=self.state.store_vehicle({'eid':'0x9000','source_hash':self.original.source_hash})
        destination=self.open_destination();before=destination.read_bytes()
        self.state.snapshot.game_version='other_version'
        with self.assertRaisesRegex(ValueError,'versión del juego'):
            self.state.restore_stored_vehicle({'id':stored['stored'],'source_hash':self.state.snapshot.source_hash})
        self.state.snapshot=load_campaign(destination);self.state.snapshot.mods=[]
        with self.assertRaisesRegex(ValueError,'mods de origen'):
            self.state.restore_stored_vehicle({'id':stored['stored'],'source_hash':self.state.snapshot.source_hash})
        self.assertEqual(before,destination.read_bytes())
        self.assertEqual(len(VehicleStorage(self.state.workspace).items(self.source)),1)
    def test_application_checks_campaign_identity_and_current_patched_definition(self):
        original=self.source.read_bytes()
        with self.assertRaisesRegex(ValueError,'campaña cambió'):self.state.store_vehicle({'eid':'0x9000','source_hash':'stale'})
        with self.assertRaisesRegex(ValueError,'campaña cambió'):
            self.state.store_vehicle({'eid':'0x9000','source_hash':self.original.source_hash,'source_path':str(self.root/'other.sav')})
        self.assertEqual(self.source.read_bytes(),original)
        stored=self.state.store_vehicle({'eid':'0x9000','source_hash':self.original.source_hash})
        model=self.state.addon_root()/'resource/entity/-vehicle/workshop/mw_fixture/mw_fixture.def'
        model.write_text('{game_entity {placer {place "driver" {LinkBone}}}}')
        current=self.source.read_bytes()
        with self.assertRaisesRegex(ValueError,'String expected'):
            self.state.restore_stored_vehicle({'id':stored['stored'],'source_hash':self.state.snapshot.source_hash})
        self.assertEqual(current,self.source.read_bytes())
        # Restore from the patched CURRENT definition, keeping old save state.
        model.write_text('{game_entity {placer {place "driver" {LinkBone ""}}}}')
        self.state.restore_stored_vehicle({'id':stored['stored'],'source_hash':self.state.snapshot.source_hash})
        self.assertIn('{LinkBone ""}',model.read_text())
        self.assertIn('{Score 71}',self.state.snapshot._scene.render())
    def test_application_recovers_interrupted_store_without_losing_crew(self):
        import os
        original_replace=os.replace
        def interrupt(source,target):
            result=original_replace(source,target)
            if Path(target)==self.source:raise KeyboardInterrupt('simulated power loss after campaign publication')
            return result
        original=self.source.read_bytes()
        with patch('workshop.transactions.os.replace',side_effect=interrupt),self.assertRaises(KeyboardInterrupt):
            self.state.store_vehicle({'eid':'0x9000','source_hash':self.original.source_hash})
        rows=VehicleStorage(self.state.workspace).items(self.source)
        self.assertEqual(len(rows),1);self.assertTrue(rows[0]['pending'])
        self.state.snapshot=load_campaign(self.source);self.state.campaign_view=self.state.snapshot.to_dict()
        self.state.recover_stored_vehicle({'id':rows[0]['id'],'source_hash':self.state.snapshot.source_hash})
        self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(self.state.campaign_data()['stored_vehicles'],[])

    def test_failed_store_rolls_back_save_and_previous_readiness(self):
        import os
        original_replace=os.replace
        def fail(source,target):
            if Path(target)==self.source:raise OSError('simulated disk failure before save replacement')
            return original_replace(source,target)
        original=self.source.read_bytes();fence={'generation':'previous'}
        self.state.settings['addon_publication']=fence
        with patch('workshop.transactions.os.replace',side_effect=fail),self.assertRaisesRegex(RuntimeError,'rolled back'):
            self.state.store_vehicle({'eid':'0x9000','source_hash':self.original.source_hash})
        self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(self.state.settings['addon_publication'],fence)
        self.assertEqual(VehicleStorage(self.state.workspace).items(self.source),[])

    def test_restore_refuses_unresolved_campaign_mods_and_keeps_archive(self):
        stored=self.state.store_vehicle({'eid':'0x9000','source_hash':self.original.source_hash})
        current=self.source.read_bytes();self.state.campaign_view['unresolved_mods']=['missing_source_mod']
        with self.assertRaisesRegex(ValueError,'mods que no se encontraron'):
            self.state.restore_stored_vehicle({'id':stored['stored'],'source_hash':self.state.snapshot.source_hash})
        self.assertEqual(self.source.read_bytes(),current)
        self.assertEqual(len(VehicleStorage(self.state.workspace).items(self.source)),1)

    def test_partially_staffed_vehicle_keeps_its_real_capacity_without_refilling_crew(self):
        scene=SCENE.replace('tank_hull','mw_fixture').replace(
            '{Human "mp/ger/mid/tank_commander" 0x9002 {MID 22} {NameId 11 15}}','').replace(
            '{Link 0x9002 {0x9000 "gunner"}}','').replace('0x9000 0x9001 0x9002','0x9000 0x9001')
        self.state.snapshot=load_campaign(make_save(self.source,scene))
        self.state.campaign_view=self.state.snapshot.to_dict()
        result=self.state.store_vehicle({'eid':'0x9000','source_hash':self.state.snapshot.source_hash})
        self.assertEqual(result['crew'],1)
        self.state.restore_stored_vehicle({'id':result['stored'],'source_hash':self.state.snapshot.source_hash})
        squad=next(s for s in self.state.snapshot.squads if '0x9000' in s['eids'])
        self.assertEqual(squad['human_eids'],['0x9001'])

if __name__=='__main__':unittest.main()
