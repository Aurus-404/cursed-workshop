"""Application contract tests using isolated fixtures and a loopback-only server."""
import copy
import hashlib
import http.client
import json
import tempfile
import zipfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from workshop.app import AppState, Handler
from workshop.transactions import OWNERSHIP_FILE, TransactionError
from workshop.depot import Depot
# Imported before any test patches workshop.compiler._load_entity: modules that
# import it lazily would otherwise keep the mock after the patch is stopped.
import workshop.gun_assembly  # noqa: F401
import workshop.weapon_state  # noqa: F401


def snapshot(path, mods=()):
    data = {"source_path": str(path), "mods": list(mods), "units": [
        {"eid": "0x100", "template": "test_hull", "kind": "vehicle"},
        {"eid": "0x200", "template": "test_donor", "kind": "vehicle"},
        {"eid": "0x300", "template": "mp/ger/mid/tankman", "kind": "human"},
    ]}
    return SimpleNamespace(to_dict=lambda: copy.deepcopy(data))


class ApplicationTests(unittest.TestCase):
    def test_repair_requires_open_campaign_before_accessing_or_creating_files(self):
        with patch.object(self.state,'closed',side_effect=AssertionError('Guard must run first')):
            with self.assertRaisesRegex(ValueError,'Seleccioná primero una campaña'):
                self.state.repair({})
        self.assertFalse((self.state.workspace/'operations').exists())

    def test_repair_rejects_a_save_changed_since_it_was_opened(self):
        from tests.test_campaign import make_save,SCENE
        from workshop.campaign import load_campaign
        selected=make_save(self.root/'selected.sav',SCENE)
        self.state.snapshot=load_campaign(selected)
        selected.write_bytes(b'changed')
        with patch.object(self.state,'closed',side_effect=AssertionError('Guard must run first')):
            with self.assertRaisesRegex(ValueError,'La partida cambió'):
                self.state.repair({})
        self.assertFalse((self.state.workspace/'operations').exists())

    def test_error_report_requires_selected_campaign_before_creating_any_files(self):
        with patch('workshop.error_report.build_report') as build:
            with self.assertRaisesRegex(ValueError,'Seleccioná primero una campaña'):
                self.state.error_report({'quiet':True})
            build.assert_not_called()
        self.assertFalse((self.state.workspace/'reports').exists())

    def test_error_report_contains_only_the_current_selected_campaign(self):
        from tests.test_campaign import make_save,SCENE
        from workshop.campaign import load_campaign
        selected=make_save(self.root/'selected.sav',SCENE)
        self.state.snapshot=load_campaign(selected)
        operation=self.state.workspace/'operations'/'previous'
        operation.mkdir(parents=True)
        previous=make_save(self.root/'previous.sav',SCENE)
        (operation/'result.json').write_text(json.dumps({'source_path':str(previous)}),'utf-8')
        with patch('workshop.app.config_dirs',return_value=[self.root/'game-data']):
            result=self.state.error_report({'quiet':True,'diagnostic':'selected campaign'})
        with zipfile.ZipFile(result['path']) as archive:
            saves=[name for name in archive.namelist() if name.startswith('save/')]
            self.assertEqual(saves,['save/selected.sav'])
            self.assertEqual(archive.read(saves[0]),selected.read_bytes())
        selected.unlink()
        with patch('workshop.error_report.build_report') as build:
            with self.assertRaisesRegex(ValueError,'Seleccioná primero una campaña'):
                self.state.error_report({'quiet':True})
            build.assert_not_called()

    def test_editing_saved_guns_allows_placement_but_cannot_select_another_weapon(self):
        self._generate_with_fixture_compiler()
        row={'schema':1,'recipient_place':'gun','donor_place':'gun','pose':{'position':[0,0,0],'rotation':[0,0,0]}}
        source={'eid':'0x100','recipient':'test_hull','donor':'test_donor','recipe':True,'transform':{'gun_swaps':[row]}}
        moved={**row,'pose':{'position':[1,2,3],'rotation':[0,0,10]}}
        with patch.object(self.state,'edit_source',return_value=source),patch('workshop.campaign.plan_edit',return_value={'fixture_edit':True}):
            self.state.generate({'mode':'edit','eid':'0x100','transform':{'gun_swaps':[moved]}})
            # Opening a saved row for placement converts it into the pending
            # representation without granting a weapon from another donor.
            self.state.generate({'mode':'edit','eid':'0x100','transform':{
                'gun_swaps':[],'gun_swap':{'schema':1,'recipient_place':'gun','donor_place':'gun'},'turret':moved['pose']}})
            with self.assertRaisesRegex(ValueError,'conserva el cañón'):
                self.state.generate({'mode':'edit','eid':'0x100','transform':{'gun_swaps':[{**moved,'donor_place':'another'}]}})
    def test_generation_moves_owned_staging_and_portrait_verifies_the_new_location(self):
        from tests.test_commander_mg import fixture
        from tests.test_campaign import make_save,SCENE
        from workshop.campaign import load_campaign
        from workshop.resources import ResourceIndex
        from workshop.transactions import commit_transaction
        from workshop.syntax import parse
        game=self.root/'integration_game';fixture(game)
        scene=parse(SCENE.replace('tank_hull','hull').replace('wheeled_donor','hull'))
        for entity in scene.find('campaign').find_all('Entity'):
            # This minimal model fixture has no damage volumes/components.
            for component in [*entity.find_all('Component'),*entity.find_all('Armor')]:entity.remove(component)
        source=make_save(self.root/'integration.sav',scene.render())
        original=source.read_bytes();self.state.snapshot=load_campaign(source)
        self.state.settings={'game_root':str(game),'mod_roots':[]}
        index=ResourceIndex(game).build();self.state.index=index
        def register(index,result,addon,identifier,name):
            # Registration is a boundary double; compilation, campaign planning,
            # directory preparation, verification and publication are real.
            (Path(addon)/'mod.info').write_text('{mod {name "Fixture"}}')
            return {'fixture':True}
        try:
            with patch('workshop.registration.register_variant',side_effect=register),patch('workshop.transactions.running_game_processes',return_value=[]):
                result=self.state.generate({'recipient_eid':'0x9000','donor_eid':'0x9010','transform':{}})
                tx=result['transaction'];staged=Path(tx['addon_path'])
                self.assertFalse((Path(result['operation_dir'])/'modular_workshop').exists())
                addon,hashes,_,_,recipe=self.state._portrait_target({'id':result['new_template'],'staged':True})
                self.assertEqual(addon,staged);self.assertIn('mod.info',hashes)
                self.assertEqual(recipe['id'],result['new_template'])
                installed=commit_transaction(tx['journal_path'])
                self.assertEqual(installed['state'],'committed')
                after=load_campaign(installed['save_destination'])
                self.assertIn(result['new_template'],{u['template'] for u in after.units})
        finally:index.close()
        self.assertEqual(source.read_bytes(),original)

    def test_unit_images_are_local_artwork_with_safe_fallback(self):
        from workshop.textures import png
        from workshop.icons import write_tga
        raw=png(1,1,bytes([20,30,40,255]));files={'interface/scene/portrait_squad/test_00.png':raw}
        self.state.index=SimpleNamespace(sources=[],entries=files,resolve_entity=lambda name:{},
            exists=lambda p:p in files,read_bytes=lambda p,**kwargs:files[p])
        self.assertEqual(self.state.unit_image('test'),raw)
        with self.assertRaises(ValueError):self.state.unit_image('../settings')
        with self.assertRaises(FileNotFoundError):self.state.unit_image('missing')
        files['interface/scene/unit_icon/other_00.tga']=write_tga(1,1,bytes([30,20,10,255]))
        self.assertTrue(self.state.unit_image('other').startswith(b'\x89PNG'))

    def test_cancel_running_read_job_without_recording_backend_error(self):
        import time
        from workshop.cancellation import checkpoint
        entered=threading.Event()
        def read():
            entered.set()
            while True:checkpoint();time.sleep(.005)
        key=self.state.job('read',read,cancellable=True)['job_id']
        self.assertTrue(entered.wait(2));self.assertTrue(self.state.cancel_job({'id':key})['accepted'])
        self.state.pool.shutdown(wait=True)
        self.assertEqual(self.state.jobs[key]['status'],'cancelled')
        self.assertEqual(list(self.state.diagnostics.errors),[])

    def test_publication_jobs_cannot_be_cancelled(self):
        release=threading.Event()
        key=self.state.job('publish',lambda:release.wait(2))['job_id']
        try:self.assertFalse(self.state.cancel_job({'id':key})['accepted'])
        finally:release.set();self.state.pool.shutdown(wait=True)
        self.assertEqual(self.state.jobs[key]['status'],'done')

    def test_queued_read_cancels_immediately_and_never_runs(self):
        release=threading.Event();entered=threading.Event();executed=[]
        def write():entered.set();release.wait(2)
        self.state.job('publish',write);self.assertTrue(entered.wait(1))
        key=self.state.job('queued read',lambda:executed.append(True),cancellable=True)['job_id']
        try:
            self.assertTrue(self.state.cancel_job({'id':key})['accepted'])
            self.assertEqual(self.state.jobs[key]['status'],'cancelled')
        finally:release.set();self.state.pool.shutdown(wait=True)
        self.assertEqual(executed,[])

    def test_editing_smoke_mounts_does_not_replenish_campaign_charges(self):
        _,compiler,_,_,_=self._generate_with_fixture_compiler()
        original=compiler.side_effect
        def smoke_compile(*args):
            result=original(*args)
            contract={'initialize':True,'initial_item':'{item "nbks grenade" 3}'}
            result['metadata']['smoke_launchers']=contract
            result['metadata'].setdefault('state_profile',{})['smoke_launchers']=contract
            return result
        compiler.side_effect=smoke_compile
        source={'eid':'0x100','recipient':'test_hull','donor':'test_donor','recipe':True,'transform':{},
                'smoke_launchers':{'schema':1}}
        with patch.object(self.state,'edit_source',return_value=source):
            with patch('workshop.campaign.plan_edit',return_value={'fixture_edit':True}) as edit:
                self.state.generate({'mode':'edit','eid':'0x100','transform':{}})
                self.assertFalse(edit.call_args.kwargs['state_profile']['smoke_launchers']['initialize'])

    def test_support_conversion_uses_one_owned_unit_and_preserving_edit_plan(self):
        _,compiler,_,planner,_=self._generate_with_fixture_compiler()
        original=compiler.side_effect
        def support_compile(*args):
            result=original(*args)
            result['metadata']['support_vehicle']={'removed_mount':'turret'}
            return result
        compiler.side_effect=support_compile
        with patch('workshop.campaign.plan_edit',return_value={'fixture_support':True}) as edit:
            result=self.state.generate({'mode':'support','recipient_eid':'0x100',
                'transform':{'support_vehicle':{'schema':1,'profile':'native','cargo':[{'asset':'crate'}]}}})
        self.assertEqual(compiler.call_args.args[1:3],('test_hull','test_hull'))
        planner.assert_not_called()
        self.assertTrue(edit.call_args.kwargs['keep_names'])
        self.assertEqual(edit.call_args.args[1],'0x100')
        self.assertEqual([p['part'] for p in result['depot_change']['add']],['turret'])
        self.assertEqual(result['depot_change']['take'],[])

    def test_support_conversion_cannot_use_a_depot_hull_without_owned_vehicle(self):
        _,compiler,_,_,_=self._generate_with_fixture_compiler()
        stored=Depot(self.state.workspace).apply(str(self.root/'source.sav'),
            [{'part':'hull','template':'test_hull'}],[])
        with self.assertRaises(ValueError):
            self.state.generate({'mode':'support','recipient_depot':stored[0]['id'],
                'transform':{'support_vehicle':{'schema':1}}})
        compiler.assert_not_called()

    def test_edit_cannot_conjure_a_different_gun_from_the_consumed_donor(self):
        _,compiler,_,_,_=self._generate_with_fixture_compiler()
        source={'eid':'0x100','recipient':'test_hull','donor':'test_donor','recipe':True,
                'transform':{'gun_swap':{'schema':1,'recipient_place':'gun','donor_place':'gun'}}}
        with patch.object(self.state,'edit_source',return_value=source):
            with self.assertRaises(ValueError):
                self.state.generate({'mode':'edit','eid':'0x100','transform':{'gun_swap':{'donor_place':'other'}}})
        compiler.assert_not_called()

    def test_gun_catalog_and_preview_use_same_structural_selection(self):
        from tests.test_gun_assembly import Resources
        self.state.index=Resources()
        catalog=self.state.gun_catalog({'template':'receiver'})
        self.assertEqual(catalog['weapons'][0]['place'],'main_slot')
        preview=self.state.gun_preview({'hull':'receiver','donor':'towed',
                          'gun_swap':{'recipient_place':'main_slot','donor_place':'main_slot'}})
        self.assertTrue(preview['preview_valid']);self.assertFalse(preview['runtime_verified'])

    def test_gun_generation_does_not_duplicate_complete_parts_in_depot(self):
        profile,compiler,registrar,planner,transaction=self._generate_with_fixture_compiler()
        result=self.state.generate({'recipient_eid':'0x100','donor_eid':'0x200',
                   'transform':{'gun_swap':{'schema':1,'recipient_place':'gun','donor_place':'gun'}}})
        self.assertEqual(result['depot_change']['add'],[])
        self.assertEqual(compiler.call_args.args[3]['gun_swap']['recipient_place'],'gun')
        self.assertTrue(transaction.called)

    def test_interface_preferences_survive_restart_without_changing_game_configuration(self):
        original=copy.deepcopy(self.state.settings)
        with patch.object(self.state,'closed',side_effect=AssertionError('No game action')):
            self.assertEqual({k:v for k,v in self.state.ui_preferences().items() if k!='system_language'},{})
            self.assertIn(self.state.ui_preferences().get('system_language'),(None,'es','en','zh-CN'))
            self.state.ui_preferences({'theme':'paper','language':'zh-CN','help':False})
            self.state.ui_preferences({'sound':False,'size':'large'})
        other=AppState(self.state.workspace)
        try:
            self.assertEqual(other.ui_preferences(),{'theme':'paper','language':'zh-CN','help':False,'sound':False,'size':'large'})
            self.assertEqual(other.settings['game_root'],original['game_root'])
            self.assertEqual(other.settings['mod_roots'],original['mod_roots'])
        finally:other.pool.shutdown(wait=True)

    def test_interface_preferences_reject_unknown_keys_and_invalid_types_without_writing(self):
        self.state.ui_preferences({'theme':'olive'})
        before=self.state.config_path.read_bytes()
        for bad in ([],{'game_root':'elsewhere'},{'theme':'__proto__'},{'help':1},{'size':None},{'language':'fr'},{'motion':'true'}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):self.state.ui_preferences(bad)
            self.assertEqual(self.state.config_path.read_bytes(),before)

    def test_failed_preferences_write_keeps_previous_settings(self):
        self.state.ui_preferences({'theme':'earth'})
        with patch.object(self.state,'save_settings',side_effect=OSError('fixture full disk')):
            with self.assertRaises(OSError):self.state.ui_preferences({'theme':'paper'})
        self.assertEqual({k:v for k,v in self.state.ui_preferences().items() if k!='system_language'},{'theme':'earth'})

    def test_new_preview_supersedes_queued_preview_without_discarding_writes(self):
        from threading import Event
        entered,release=Event(),Event()
        executed=[]
        def blocking():
            entered.set();release.wait(5)
        self.state.job('existing write',blocking)
        self.assertTrue(entered.wait(2))
        try:
            old=self.state.job('old preview',lambda:executed.append('old'),lane='/api/check')['job_id']
            self.state.job('save write',lambda:executed.append('write'))
            new=self.state.job('new preview',lambda:executed.append('new'),lane='/api/check')['job_id']
        finally:
            release.set()
        self.state.pool.shutdown(wait=True)
        self.assertEqual(executed,['write','new'])
        self.assertEqual(self.state.jobs[old]['status'],'superseded')
        self.assertEqual(self.state.jobs[new]['status'],'done')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = self.root / "fixture_game"
        (self.game / "resource").mkdir(parents=True)
        with zipfile.ZipFile(self.game / "resource" / "gamelogic.pak", "w"):
            pass  # what marks a folder as the game's
        self.state = AppState(self.root / "workspace")
        self.state.settings = {"game_root": str(self.game), "mod_roots": []}
        self.guard = patch("workshop.app.require_game_idle", return_value={"running": False, "page": None}).start()
        self.state.index = SimpleNamespace(stats={}, sources=[{"id": "vanilla", "fingerprint": "fixture"}],
                                           has_entity=lambda _: False, label_for=lambda t: t, close=lambda: None)

    def tearDown(self):
        self.state.pool.shutdown(wait=True)
        patch.stopall()
        self.temp.cleanup()

    def reload_index(self):
        self.state.index = SimpleNamespace(stats={"fixture": True}, sources=[], has_entity=lambda _: False, label_for=lambda t: t, close=lambda: None)
        return {"stats": self.state.index.stats}

    def test_rendered_portrait_is_validated_and_written(self):
        import base64
        import struct
        import zlib
        def png(width, height):
            def chunk(kind, payload):
                return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
            raw = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                   + chunk(b"IDAT", zlib.compress((b"\0" + bytes(width * 4)) * height)) + chunk(b"IEND", b""))
            return "data:image/png;base64," + base64.b64encode(raw).decode()
        staging = self.root / "staging"
        AppState.write_portrait(staging, "mw_x", [png(112, 140)] * 4)
        folder = staging / "resource/interface/scene/portrait_squad"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [f"mw_x_0{i}.png" for i in range(4)])
        for bad in ([png(112, 140)] * 3, [png(64, 64)] * 4, ["data:image/jpeg;base64,AAAA"] * 4):
            with self.assertRaises(ValueError):
                AppState.write_portrait(staging, "mw_y", bad)

    def test_reload_steps_only_when_the_game_is_open(self):
        self.assertEqual(AppState.reload_steps({"running": False}), [])
        steps = AppState.reload_steps({"running": True, "page": "page_main"})
        self.assertEqual(len(steps), 3)
        self.assertIn("modular_workshop", steps[0])
        self.assertIn("Built Vehicles", AppState.reload_steps({"running": True}, activated=True)[0])

    def test_loading_vanilla_save_clears_previously_active_mods(self):
        self.state.settings["mod_roots"] = [str(self.root / "old_mace")]
        fixture = snapshot(self.root / "vanilla.sav")
        with patch("workshop.campaign.load_campaign", return_value=fixture), \
             patch("workshop.resources.discover_installed_mods", return_value=[]), \
             patch.object(self.state, "index_resources", side_effect=self.reload_index) as rebuild:
            result = self.state.load_save({"path": str(self.root / "vanilla.sav")})
        self.assertEqual(self.state.settings["mod_roots"], [])
        self.assertEqual(result["campaign"]["active_mod_roots"], [])
        rebuild.assert_called_once()

    def test_loading_another_campaign_invalidates_previous_installable_result(self):
        self.state.last_result = {"source_path": "previous-campaign.sav", "transaction": {"journal_path": "previous"}}
        fixture = snapshot(self.root / "new-campaign.sav")
        with patch("workshop.campaign.load_campaign", return_value=fixture), \
             patch("workshop.resources.discover_installed_mods", return_value=[]):
            self.state.load_save({"path": str(self.root / "new-campaign.sav")})
        self.assertIsNone(self.state.last_result)

    def test_changing_resource_stack_invalidates_loaded_campaign_and_result(self):
        (self.game / "resource/gamelogic.pak").write_bytes(b"fixture-placeholder")
        mod = self.root / "different_mod"
        mod.mkdir()
        (mod / "mod.info").write_text('{mod {name "Different source stack"}}')
        self.state.snapshot = snapshot(self.root / "campaign.sav")
        self.state.last_result = {"source_path": "campaign.sav"}
        with patch.object(self.state, "index_resources", side_effect=self.reload_index):
            self.state.configure({"game_root": str(self.game), "mod_roots": [str(mod)]})
        self.assertIsNone(self.state.snapshot)
        self.assertIsNone(self.state.last_result)

    def test_save_mod_order_is_preserved_and_unknown_mod_blocks_generation(self):
        installed = [{"id": "second", "name": "Second", "root": str(self.root / "second")},
                     {"id": "first", "name": "First", "root": str(self.root / "first")}]
        fixture = snapshot(self.root / "mixed.sav", ["mod_first:0", "second:3", "missing:0"])
        with patch("workshop.campaign.load_campaign", return_value=fixture), \
             patch("workshop.resources.discover_installed_mods", return_value=installed), \
             patch.object(self.state, "index_resources", side_effect=self.reload_index):
            result = self.state.load_save({"path": str(self.root / "mixed.sav")})
        self.assertEqual(self.state.settings["mod_roots"], [str(self.root / "first"), str(self.root / "second")])
        self.assertEqual(result["campaign"]["unresolved_mods"], ["missing:0"])
        with patch("workshop.compiler.compile_assembly") as compiler, self.assertRaisesRegex(ValueError, "missing"):
            self.state.generate({"recipient_eid": "0x100", "donor_eid": "0x200"})
        compiler.assert_not_called()

    def _generate_with_fixture_compiler(self):
        from workshop.syntax import block
        # Model the additional read boundary used to compare edit loadouts.
        patch('workshop.compiler._load_entity', return_value=(None, None, block('{game_entity}'))).start()
        self.state.snapshot = snapshot(self.root / "source.sav")
        profile = {"allowed_damage_ids": ["body", "turret"], "donor_name_map": {"turret": "turret"},
                   "weaponry_policy": "donor_complete", "inventory_policy": "donor"}

        def compile_fixture(index, hull, donor, transform, staging, new_id):
            target = Path(staging) / "resource/entity/-vehicle/workshop" / new_id / (new_id + ".def")
            target.parent.mkdir(parents=True)
            target.write_bytes(b"{game_entity}")
            return {"id": new_id, "metadata": {"recipient": hull, "donor": donor, "crew_slots": 3,
                                               "crew_roles": ["driver", "gunner", "charger"], "state_profile": profile},
                    "definition_path": target.relative_to(Path(staging)/'resource').as_posix(),
                    "ready_for_campaign": True, "files": []}

        def register_fixture(index, result, staging, new_id, display):
            (Path(staging) / "mod.info").write_bytes(b'{mod {name "fixture generated mod"}}')
            return {"id": new_id, "mod_id": Path(staging).name, "active_sources": copy.deepcopy(index.sources)}

        self.compile_patch = patch("workshop.compiler.compile_assembly", side_effect=compile_fixture)
        self.register_patch = patch("workshop.registration.register_variant", side_effect=register_fixture)
        self.plan_patch = patch("workshop.campaign.plan_assembly", return_value={"fixture_plan": True})
        self.transaction_patch = patch("workshop.transactions.prepare_transaction", return_value={"journal_path": "fixture-journal.json"})
        compiler, registrar, planner, transaction = [item.start() for item in (
            self.compile_patch, self.register_patch, self.plan_patch, self.transaction_patch)]
        return profile, compiler, registrar, planner, transaction

    def test_depot_turret_refits_one_vehicle_and_records_leftovers(self):
        profile, compiler, registrar, planner, transaction = self._generate_with_fixture_compiler()
        source = str(self.root / "source.sav")
        stored = Depot(self.state.workspace).apply(source, [{"part": "turret", "template": "kept_turret", "label": "Torreta guardada"}], [])
        with patch("workshop.campaign.plan_refit", return_value={"fixture_refit": True}) as refit:
            result = self.state.generate({"recipient_eid": "0x100", "donor_depot": stored[0]["id"]})
        self.assertEqual(compiler.call_args.args[2], "kept_turret")
        planner.assert_not_called()
        self.assertEqual(refit.call_args.args[1], "0x100")
        self.assertEqual(refit.call_args.kwargs["role"], "hull")
        # The chassis vehicle's own turret is left over; the depot turret is used up.
        self.assertEqual(result["depot_change"]["take"], [stored[0]["id"]])
        self.assertEqual([(row["part"], row["source"]) for row in result["depot_change"]["add"]], [("turret", "0x100")])
        with self.assertRaisesRegex(ValueError, "mismo vehículo"):
            self.state.generate({"recipient_eid": "0x100", "donor_eid": "0x100"})
        with self.assertRaisesRegex(ValueError, "pieza del depósito no sirve"):
            self.state.generate({"recipient_depot": stored[0]["id"], "donor_eid": "0x200"})

    def test_edit_rebuilds_one_vehicle_from_its_recipe_without_depot_parts(self):
        profile, compiler, registrar, planner, transaction = self._generate_with_fixture_compiler()
        units = [{"eid": "0x400", "template": "mw_old", "kind": "vehicle", "label": "Old build"}]
        self.state.snapshot = SimpleNamespace(to_dict=lambda: {"source_path": str(self.root / "source.sav"), "mods": [], "units": units})
        addon = self.state.addon_root()
        addon.mkdir(parents=True)
        (addon / "assembly_mw_old.json").write_text(json.dumps({"id": "mw_old", "metadata": {
            "recipient": "test_hull", "donor": "test_donor", "recipient_mount": "turret", "donor_mount": "turret",
            "transform": {"crew_hidden": ["gunner"], "turret": {"position": [0, 0, 5], "rotation": [0, 0, 0]}}}}), encoding="utf-8")
        source = self.state.edit_source({"eid": "0x400"})
        self.assertEqual((source["recipient"], source["donor"], source["recipe"]), ("test_hull", "test_donor", True))
        self.assertEqual(source["transform"]["turret"]["position"], [0, 0, 5])
        self.assertEqual(source["transform"]["donor_mount"], "turret")
        self.assertEqual(source["transform"]["crew_hidden"], ["gunner"])
        patch("workshop.transactions._owned_addon", return_value={}).start()
        patch("workshop.registration.recorded_stack", return_value=None).start()
        with patch("workshop.campaign.plan_edit", return_value={"fixture_edit": True}) as edit:
            result = self.state.generate({"mode": "edit", "eid": "0x400", "transform": {"turret": {"position": [0, 0, 7]}}})
        self.assertEqual(compiler.call_args.args[1:3], ("test_hull", "test_donor"))
        planner.assert_not_called()
        self.assertEqual(edit.call_args.args[1], "0x400")
        self.assertTrue(edit.call_args.kwargs["keep_names"])
        self.assertEqual(result["depot_change"], {"add": [], "take": []})
        self.assertEqual(result["name"], "Old build")

    def test_edit_mount_only_initializes_inventory_when_the_selected_item_changes(self):
        profile,compiler,registrar,planner,transaction=self._generate_with_fixture_compiler()
        original=compiler.side_effect
        def mount_compile(*args):
            result=original(*args)
            mount={'slot':'new_slot','item':'base_gun','initial_item':'{item "base_gun" filled}',
                   'ammo_item':'{item "ammo rifle clip" 15}'}
            result['metadata']['commander_mg']=mount
            result['metadata']['state_profile']['commander_mg']=mount
            return result
        compiler.side_effect=mount_compile
        units=[{'eid':'0x400','template':'mw_old','kind':'vehicle','label':'Old mount'}]
        self.state.snapshot=SimpleNamespace(to_dict=lambda:{'source_path':str(self.root/'source.sav'),'mods':[],'units':units})
        addon=self.state.addon_root();addon.mkdir(parents=True)
        patch('workshop.transactions._owned_addon',return_value={}).start()
        patch('workshop.registration.recorded_stack',return_value=None).start()
        for item,initializes in [('base_gun',False),('other_gun',True)]:
            (addon/'assembly_mw_old.json').write_text(json.dumps({'id':'mw_old','metadata':{
                'recipient':'test_hull','donor':'test_donor','transform':{},
                'commander_mg':{'slot':'old_slot','item':item}}}),encoding='utf8')
            with patch('workshop.campaign.plan_edit',return_value={'fixture_edit':True}) as edit:
                self.state.generate({'mode':'edit','eid':'0x400','transform':{}})
            contract=edit.call_args.kwargs['state_profile']['commander_mg']
            self.assertEqual(contract['initialize'],initializes)
            self.assertEqual(contract['previous_slot'],'old_slot')

    def test_full_damage_inventory_profile_and_crew_capacity_reach_campaign_planner(self):
        profile, compiler, registrar, planner, transaction = self._generate_with_fixture_compiler()
        result = self.state.generate({"recipient_eid": "0x100", "donor_eid": "0x200", "transform": {"position": [1, 2, 3]}})
        arguments = planner.call_args
        self.assertEqual(arguments.kwargs["crew_slots"], 3)
        self.assertIs(arguments.kwargs["state_profile"], profile)
        self.assertEqual(arguments.args[1:3], ("0x100", "0x200"))
        self.assertEqual(arguments.kwargs["addon_mod"], result["addon_id"] + ":0")
        # Compilation happens in a fresh folder beside the addon staging, then merges in.
        compiled = Path(compiler.call_args.args[4])
        self.assertEqual(compiled.name, "compile")
        self.assertFalse(compiled.exists())
        self.assertTrue((compiled.parent / result["addon_id"] / "resource/entity/-vehicle/workshop" / result["new_template"]).is_dir())
        self.assertIs(registrar.call_args.args[1]["campaign_plan"], planner.return_value)
        prepared=Path(transaction.call_args.kwargs['addon_directory'])
        self.assertEqual(prepared.parent,compiled.parent)
        self.assertTrue((prepared/'mod.info').is_file())

    def test_new_generation_keeps_owned_existing_assets_and_excludes_ownership_marker(self):
        profile, compiler, registrar, planner, transaction = self._generate_with_fixture_compiler()
        first = self.state.generate({"recipient_eid": "0x100", "donor_eid": "0x200"})
        addon_id = first["addon_id"]
        previous = self.game / "mods" / addon_id
        prior_path = previous / "resource/entity/-vehicle/old_variant/old_variant.def"
        prior_path.parent.mkdir(parents=True)
        prior_bytes = b"previous generated variant must remain usable"
        prior_path.write_bytes(prior_bytes)
        relative = prior_path.relative_to(previous).as_posix()
        marker = {"schema_version": 1, "addon_mod": addon_id + ":0", "files": {relative: hashlib.sha256(prior_bytes).hexdigest()}}
        (previous / OWNERSHIP_FILE).write_text(json.dumps(marker), encoding="utf-8")
        before = {p.relative_to(previous).as_posix(): p.read_bytes() for p in previous.rglob("*") if p.is_file()}
        self.state.generate({"recipient_eid": "0x100", "donor_eid": "0x200"})
        prepared=Path(transaction.call_args.kwargs['addon_directory'])
        bundled={p.relative_to(prepared).as_posix():p.read_bytes() for p in prepared.rglob('*') if p.is_file()}
        self.assertEqual(bundled[relative], prior_bytes)
        self.assertTrue(any(name.endswith(".def") and "/workshop/mw_" in name for name in bundled))
        self.assertNotIn(OWNERSHIP_FILE, bundled)
        self.assertEqual({p.relative_to(previous).as_posix(): p.read_bytes() for p in previous.rglob("*") if p.is_file()}, before)

    def test_changed_existing_addon_is_never_copied_as_owned(self):
        profile, compiler, registrar, planner, transaction = self._generate_with_fixture_compiler()
        first = self.state.generate({"recipient_eid": "0x100", "donor_eid": "0x200"})
        addon_id = first["addon_id"]
        compiler.reset_mock()
        previous = self.game / "mods" / addon_id
        previous.mkdir(parents=True)
        (previous / "foreign.txt").write_text("user resource")
        with self.assertRaises(TransactionError):
            self.state.generate({"recipient_eid": "0x100", "donor_eid": "0x200"})
        compiler.assert_not_called()
        self.assertEqual((previous / "foreign.txt").read_text(), "user resource")

    def test_mutating_jobs_do_not_run_concurrently(self):
        first_started, release_first, second_started = threading.Event(), threading.Event(), threading.Event()

        def first():
            first_started.set()
            if not release_first.wait(2):
                raise RuntimeError("test release timed out")
            return "first completed"

        one = self.state.job("first", first)["job_id"]
        self.assertTrue(first_started.wait(1))
        two = self.state.job("second", lambda: second_started.set() or "second completed")["job_id"]
        try:
            self.assertFalse(second_started.wait(0.05))
        finally:
            release_first.set()
        self.state.pool.shutdown(wait=True)
        self.assertTrue(second_started.is_set())
        self.assertEqual(self.state.jobs[one]["status"], "done")
        self.assertEqual(self.state.jobs[two]["status"], "done")

    def test_dependency_change_blocks_publication_before_commit(self):
        self.state.last_result = {"source_path": str(self.root / "source.sav"), "addon_id": "fixture_generated",
                                  "transaction": {"journal_path": "fixture-journal.json"}, "registration": {"fixture": True}}
        fresh = SimpleNamespace(close=lambda: None)
        with patch("workshop.resources.ResourceIndex") as factory, \
             patch("workshop.registration.validate_dependencies", side_effect=ValueError("changed MACE")), \
             patch("workshop.transactions.commit_transaction") as commit:
            factory.return_value.build.return_value = fresh
            with self.assertRaisesRegex(ValueError, "changed MACE"):
                self.state.install({})
        commit.assert_not_called()

    def test_repair_refuses_an_addon_changed_while_recipes_are_rebuilding(self):
        save=self.root/'selected.sav';save.write_bytes(b'fixture')
        self.state.snapshot=SimpleNamespace(source_path=save,to_dict=lambda:{'units':[{'template': 'mw_a', 'kind': 'vehicle', 'owned': True}]})
        from workshop.transactions import _tree_hashes,TransactionError
        addon=self.game/'mods/modular_workshop';folder=addon/'resource/entity/-vehicle/workshop/mw_a'
        folder.mkdir(parents=True);definition=folder/'mw_a.def';definition.write_bytes(b'original')
        (addon/'mod.info').write_text('{mod {name "Fixture"}}')
        (addon/'assembly_mw_a.json').write_text(json.dumps({'id':'mw_a','metadata':{'recipient':'hull','donor':'donor','transform':{}}}))
        (addon/OWNERSHIP_FILE).write_text(json.dumps({'addon_mod':'modular_workshop:0','files':_tree_hashes(addon)}))
        def compile(index,hull,donor,tr,out,identifier):
            target=Path(out)/'resource/entity/-vehicle/workshop/mw_a/mw_a.def'
            target.parent.mkdir(parents=True);target.write_bytes(b'rebuilt')
            return {'id':identifier,'metadata':{'recipient':hull,'donor':donor,'transform':tr}}
        def register(*args,**kwargs):
            definition.write_bytes(b'external change');return {'skipped':[]}
        with patch('workshop.compiler.compile_assembly',side_effect=compile),patch('workshop.resources.ResourceIndex'),patch('workshop.registration.rebuild_registrations',side_effect=register):
            with self.assertRaises(TransactionError):self.state.repair({})
        self.assertEqual(definition.read_bytes(),b'external change')
        self.assertFalse(list((self.state.workspace/'operations').glob('*/previous_addon')))

    def test_an_addon_changed_outside_the_tool_is_kept_in_backups_and_adopted(self):
        # koro (0.4.8): "Existing addon changed externally" and nothing could be built anymore.
        from workshop.transactions import _tree_hashes, TransactionError
        addon=self.game/'mods/modular_workshop';folder=addon/'resource/entity/-vehicle/workshop/mw_a'
        folder.mkdir(parents=True);(folder/'mw_a.def').write_bytes(b'original')
        (addon/'mod.info').write_text('{mod {name "Fixture"}}')
        (addon/OWNERSHIP_FILE).write_text(json.dumps({'addon_mod':'modular_workshop:0','files':_tree_hashes(addon)}))
        (folder/'mw_a.def').write_bytes(b'edited by hand');(addon/'notes.txt').write_text('x')
        hashes=self.state.owned_addon(addon)
        self.assertIn('notes.txt',hashes)
        backup=next((self.state.workspace/'backups'/'addon').glob('*_changed_outside'))
        self.assertEqual((backup/'resource/entity/-vehicle/workshop/mw_a/mw_a.def').read_bytes(),b'edited by hand')
        history=[json.loads(line) for line in (self.state.workspace/'history'/'builds.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual((history[-1]['event'],history[-1]['added'],history[-1]['changed']),
                         ('addon_changed_outside',['notes.txt'],['resource/entity/-vehicle/workshop/mw_a/mw_a.def']))
        self.assertEqual(self.state.owned_addon(addon),hashes)  # now the record again
        (addon/OWNERSHIP_FILE).write_text(json.dumps({'addon_mod':'someone_else:0','files':{}}))
        with self.assertRaises(TransactionError):self.state.owned_addon(addon)

    def test_repair_rebuilds_recipes_keeps_ids_and_archives_previous_addon(self):
        save=self.root/'selected.sav';save.write_bytes(b'fixture')
        self.state.snapshot=SimpleNamespace(source_path=save,to_dict=lambda:{'units':[{'template': 'mw_a', 'kind': 'vehicle', 'owned': True}]})
        from workshop.transactions import _owned_addon, _tree_hashes
        addon = self.game / "mods" / "modular_workshop"
        old_def = addon / "resource/entity/-vehicle/workshop/mw_a/mw_a.def"
        old_def.parent.mkdir(parents=True)
        old_def.write_bytes(b'{game_entity {placer {place "driver" {LinkBone "%bone"}}}}')
        (addon / "mod.info").write_text('{mod {name "Modular Workshop"}}')
        (addon / "assembly_mw_a.json").write_text(json.dumps(
            {"id": "mw_a", "metadata": {"recipient": "hull", "donor": "donor", "transform": {}}}))
        (addon / OWNERSHIP_FILE).write_text(json.dumps({"addon_mod": "modular_workshop:0", "files": _tree_hashes(addon)}))

        def compile_fixture(index, hull, donor, transform, out, new_id):
            target = Path(out) / "resource/entity/-vehicle/workshop" / new_id / (new_id + ".def")
            target.parent.mkdir(parents=True)
            target.write_bytes(b'{game_entity {placer {place "driver" {LinkBone ""}}}}')
            return {"id": new_id, "metadata": {"recipient": hull, "donor": donor, "transform": transform}}

        with patch("workshop.compiler.compile_assembly", side_effect=compile_fixture), \
             patch("workshop.registration.rebuild_registrations", return_value={"registered": ["mw_a"], "skipped": []}), \
             patch("workshop.resources.ResourceIndex"):
            result = self.state.repair({})
        self.assertEqual(result["rebuilt"], ["mw_a"])
        # Each rebuilt vehicle goes into the build history, older ones included.
        rows = [json.loads(line) for line in (self.state.workspace / "history" / "builds.jsonl").read_text(encoding="utf-8").splitlines()]
        vehicle = next(r for r in rows if r["event"] == "updated_vehicle")
        self.assertEqual((vehicle["vehicle"], vehicle["recipient"], vehicle["donor"], vehicle["status"]), ("mw_a", "hull", "donor", "rebuilt"))
        self.assertEqual(rows[-1]["event"], "updated")
        self.assertNotIn(b"%bone", old_def.read_bytes())
        self.assertIn(b"%bone", (Path(result["backup"]) / "resource/entity/-vehicle/workshop/mw_a/mw_a.def").read_bytes())
        _owned_addon(addon, "modular_workshop:0")  # later installs accept the repaired addon
        self.assertIsNone(self.state.index)

    def test_shop_puts_a_vehicle_on_sale_and_back_without_touching_any_save(self):
        from workshop.transactions import _owned_addon, _tree_hashes
        addon = self.game / "mods" / "modular_workshop"
        addon.mkdir(parents=True)
        (addon / "mod.info").write_text('{mod {name "Modular Workshop"}}')
        (addon / "assembly_mw_a.json").write_text(json.dumps(
            {"id": "mw_a", "metadata": {"recipient": "hull", "donor": "donor", "transform": {}}}))
        (addon / "workshop_manifest_mw_a.json").write_text(json.dumps({"id": "mw_a", "display_name": "A", "campaign": "c1"}))
        (addon / OWNERSHIP_FILE).write_text(json.dumps({"addon_mod": "modular_workshop:0", "files": _tree_hashes(addon)}))
        seen = []
        def register(index, staging, identifiers):
            manifest = json.loads((Path(staging) / "workshop_manifest_mw_a.json").read_text(encoding="utf-8"))
            seen.append((sorted(identifiers), json.loads(json.dumps(manifest.get("shop")))))
            if manifest.get("shop"):
                manifest["shop"]["offer"] = {"cost": 400, "stage": 5}
            (Path(staging) / "workshop_manifest_mw_a.json").write_text(json.dumps(manifest))
            return {"registered": ["mw_a"], "skipped": []}
        with patch("workshop.registration.rebuild_registrations", side_effect=register), patch("workshop.resources.ResourceIndex"):
            result = self.state.set_shop({"changes": {"mw_a": True}})
            self.assertEqual(seen[-1], (["mw_a"], {"parts": ["hull", "donor"], "share": 0.75}))
            self.assertEqual(result["offers"], {"mw_a": {"cost": 400, "stage": 5}})
            manifest = json.loads((addon / "workshop_manifest_mw_a.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["campaign"], "c1")
            _owned_addon(addon, "modular_workshop:0")  # the published addon is ours again
            self.assertTrue((Path(result["backup"]) / "workshop_manifest_mw_a.json").is_file())
            self.state.set_shop({"changes": {"mw_a": False}})
            self.assertIsNone(seen[-1][1])
            self.assertNotIn("shop", json.loads((addon / "workshop_manifest_mw_a.json").read_text(encoding="utf-8")))
        with self.assertRaises(ValueError):
            self.state.set_shop({"changes": {"mw_missing": True}})
        rows = [json.loads(line) for line in (self.state.workspace / "history" / "builds.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["event"] for r in rows if r["event"] == "shop"], ["shop", "shop"])

    def test_creative_build_goes_to_the_shop_without_any_campaign(self):
        # Geofisika: any parts from the catalog, no unit spent, sold in the shop.
        from workshop.transactions import _owned_addon
        self.state.snapshot=None
        self.state.index=SimpleNamespace(has_entity=lambda t:t in ('hull','donor'),label_for=lambda t:t.title(),sources=[],
                                         close=lambda:None)
        seen={}
        def compile_fixture(index,hull,donor,transform,out,new_id):
            seen['transform']=transform
            target=Path(out)/'resource/entity/-vehicle/workshop'/new_id/(new_id+'.def')
            target.parent.mkdir(parents=True);target.write_text('{game_entity}')
            return {'id':new_id,'metadata':{'recipient':hull,'donor':donor,'transform':transform},'diagnostics':[]}
        def register(index,result,staging,new_id,name,shop=None):
            seen.update(shop=shop,plan=result['campaign_plan'],name=name)
            (Path(staging)/'mod.info').write_text('{mod}')
            return {'shop':{'offer':{'cost':300,'stage':2}}}
        with patch('workshop.compiler.compile_assembly',side_effect=compile_fixture), \
             patch('workshop.registration.register_variant',side_effect=register), \
             patch('workshop.registration.describe_addon'),patch('workshop.registration.write_stack'), \
             patch.object(self.state,'mod_stack_problem',return_value=None):
            result=self.state.creative({'recipient':'hull','donor':'donor','transform':{'gun_swaps':[{'recipient_place':'gun','donor_place':'gun'}],
                'gun_swap':{'recipient_place':'gun'},'assembly_draft':{'schema':1},'towing':{'tier':'light'}}})
            with self.assertRaises(ValueError):self.state.creative({'recipient':'hull','donor':'nope'})
        self.assertEqual(seen['shop'],{'parts':['hull','donor']})
        self.assertEqual(seen['plan'],{},'no campaign plan: no save is written')
        # Potato Emperor: saved gun changes only name catalog models, so they build;
        # a design or an unsaved change belongs to a campaign and is dropped.
        self.assertEqual(seen['transform']['gun_swaps'],[{'recipient_place':'gun','donor_place':'gun'}])
        self.assertNotIn('gun_swap',seen['transform']);self.assertNotIn('assembly_draft',seen['transform'])
        self.assertEqual(seen['name'],'Hull + Donor')
        self.assertEqual(result['offer'],{'cost':300,'stage':2})
        addon=self.state.addon_root()
        self.assertTrue(any(addon.rglob('*.def')))
        _owned_addon(addon,'modular_workshop:0')
        rows=[json.loads(line) for line in (self.state.workspace/'history'/'builds.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(rows[-1]['event'],'creative')

    def test_linux_finds_saves_in_the_games_proton_prefix(self):
        # Field report 20/10 (Steam Deck): the tool runs in its own Proton prefix
        # and listed no saves; the game's are in compatdata/400750 of a library.
        from workshop import app, locations
        linux = self.root / "linux"
        def unix(path):
            return linux.joinpath(*path.strip("/").split("/"))
        def user(steamapps):
            return steamapps / "compatdata" / "400750" / "pfx" / "drive_c" / "users" / "steamuser"
        internal = unix("/home/deck/.local/share/Steam/steamapps")
        card = unix("/run/media/mmcblk0p1/steamapps")
        internal.mkdir(parents=True)
        (internal / "libraryfolders.vdf").write_text('"libraryfolders"\n{\n "1"\n {\n  "path"\t\t"/run/media/mmcblk0p1"\n }\n}\n', encoding="utf-8")
        game = user(card) / "Documents" / "My Games" / "gates of hell"
        save = game / "profiles" / "123" / "campaign" / "conquest 1.sav"
        save.parent.mkdir(parents=True)
        save.write_bytes(b"zip")
        with patch.object(locations, "running_under_wine", return_value=True), patch.object(locations, "unix_path", side_effect=unix), \
             patch.dict(app.os.environ, {"HOME": "/home/deck", "STEAM_COMPAT_CLIENT_INSTALL_PATH": "/home/deck/.local/share/Steam",
                                         "LOCALAPPDATA": str(self.root / "tool_prefix_local")}), \
             patch.object(locations, "windows_documents", return_value=[]):
            self.assertEqual(locations.proton_users(), [user(card)])
            self.assertEqual(locations.config_dirs(), [game])
            self.assertEqual([row["path"] for row in app.discover_saves()], [str(save)])
        self.assertEqual(locations.proton_users(), [])  # Windows: nothing changes

    def test_saves_are_found_wherever_the_game_keeps_its_data(self):
        # Field reports 20/10: OneDrive moved Documents ("Dokumente") with broken
        # sync, and some installs keep profiles in %LOCALAPPDATA%\digitalmindsoft.
        # The folder the game writes now wins over a stale same-named copy.
        from workshop import app, locations
        home = self.root / "home"
        local = home / "AppData" / "Local"
        def save_in(game, profile, name, age):
            save = game / "profiles" / profile / "campaign" / name
            save.parent.mkdir(parents=True, exist_ok=True)
            save.write_bytes(b"zip")
            app.os.utime(save, (age, age))
            return save
        def log_in(game, age):
            log = game / "log" / "game.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("x")
            app.os.utime(log, (age, age))
        documents = home / "Documents" / "My Games" / "gates of hell"
        cloud = home / "OneDrive - Personal" / "Dokumente" / "My Games" / "gates of hell"
        appdata = local / "digitalmindsoft" / "gates of hell"
        stale = save_in(documents, "7", "conquest 3.sav", 1000)
        old_cloud = save_in(cloud, "7", "eroberung 1.sav", 900)
        current = save_in(appdata, "7", "conquest 3.sav", 3000)
        only_here = save_in(appdata, "7", "conquest 4.sav", 2500)
        log_in(documents, 1000)
        log_in(appdata, 3000)  # the game writes here now
        (home / "OneDrive - Personal" / "Bilder").mkdir()
        with patch.object(locations.Path, "home", return_value=home), \
             patch.object(locations, "documents_dir", return_value=home / "OneDrive - Personal" / "Dokumente"), \
             patch.dict(app.os.environ, {"OneDrive": str(home / "OneDrive - Personal"), "LOCALAPPDATA": str(local)}), \
             patch.object(locations, "running_under_wine", return_value=False):
            self.assertEqual(locations.config_dirs()[0], appdata)
            self.assertEqual({str(p) for p in locations.config_dirs()}, {str(appdata), str(documents), str(cloud)})
            self.assertEqual(locations.game_log_path(), appdata / "log" / "game.log")
            paths = [row["path"] for row in app.discover_saves()]
            self.assertEqual(paths, [str(current), str(only_here), str(old_cloud)])
            self.assertNotIn(str(stale), paths)

    def test_onedrive_only_saves_get_a_plain_explanation(self):
        from workshop import app
        cloud = OSError(22, "Invalid argument")
        self.assertIn("OneDrive", app.unreadable_save(cloud))
        other = PermissionError(13, "Permission denied")
        self.assertTrue(app.unreadable_save(other).startswith("No se pudo leer la partida: "))
        self.assertNotIn("OneDrive", app.unreadable_save(other))

    def test_without_edge_webview2_the_system_browser_is_used(self):
        # Field report 20/10 (SteamOS/Proton): pywebview fell back to Internet
        # Explorer's engine, which showed the page without running the app.
        import sys, types
        from workshop import app
        winforms = types.ModuleType("webview.platforms.winforms")
        modules = {"webview": types.ModuleType("webview"), "webview.platforms": types.ModuleType("webview.platforms"),
                   "webview.platforms.winforms": winforms}
        modules["webview.platforms"].winforms = winforms
        server = SimpleNamespace(serve_forever=lambda **_: self.fail("the window path must not start"))
        with patch.dict(sys.modules, modules), patch.object(app.os, "name", "nt"):
            winforms.renderer = "mshtml"
            self.assertFalse(app.run_window(server, "http://127.0.0.1:1/"))

    def test_mod_stack_check_never_blocks_opening_a_campaign(self):
        # Field report 20/10: a mod's global file had a stray ")"; the check failed opening.
        from workshop.syntax import SyntaxError as SDLSyntaxError
        self.state.index = SimpleNamespace(exists=lambda path: True, disabled_mods=[])
        with patch("workshop.registration._load_include_graph", side_effect=SDLSyntaxError("Unexpected ')' at line 66, column 1")):
            self.assertIsNone(self.state.mod_stack_problem())
        with patch("workshop.registration._load_include_graph",
                   side_effect=FileNotFoundError("Missing reference 'x.inc' from set/a.set")):
            self.assertIn("x.inc", self.state.mod_stack_problem())

    def test_repair_rebuilds_chained_vehicles_after_their_parent_even_when_crew_changes(self):
        save=self.root/'selected.sav';save.write_bytes(b'fixture')
        self.state.snapshot=SimpleNamespace(source_path=save,to_dict=lambda:{'units':[{'template': 'mw_a', 'kind': 'vehicle', 'owned': True}, {'template': 'mw_b', 'kind': 'vehicle', 'owned': True}, {'template': 'mw_c', 'kind': 'vehicle', 'owned': True}]})
        from workshop.transactions import _tree_hashes
        addon = self.game / "mods" / "modular_workshop"
        recipes = {"mw_a": ("hull", "donor", 3), "mw_b": ("t20", "mw_a", 5), "mw_c": ("hull", "donor", 2)}
        for identifier, (recipient, donor, crew) in recipes.items():
            folder = addon / "resource/entity/-vehicle/workshop" / identifier
            folder.mkdir(parents=True)
            (folder / (identifier + ".def")).write_bytes(b"{game_entity}")
            (addon / f"assembly_{identifier}.json").write_text(json.dumps(
                {"id": identifier, "metadata": {"recipient": recipient, "donor": donor, "transform": {}, "crew_slots": crew}}))
        (addon / "mod.info").write_text('{mod {name "Modular Workshop"}}')
        (addon / OWNERSHIP_FILE).write_text(json.dumps({"addon_mod": "modular_workshop:0", "files": _tree_hashes(addon)}))
        order, indexes = [], []

        def index_factory(game_root, roots):
            index = SimpleNamespace(roots=list(roots), close=lambda: None)
            index.build = lambda: index
            index.with_overlay=lambda root:index_factory(game_root,[*roots,root])
            indexes.append(index)
            return index

        def compile_fixture(index, hull, donor, transform, out, new_id):
            order.append((new_id, any("modular_workshop" in str(root) for root in index.roots)))
            target = Path(out) / "resource/entity/-vehicle/workshop" / new_id / (new_id + ".def")
            target.parent.mkdir(parents=True)
            target.write_bytes(b"{game_entity {rebuilt}}")
            crew = {"mw_a": 3, "mw_b": 5, "mw_c": 4}[new_id]  # mw_c would gain crew slots
            return {"id": new_id, "metadata": {"recipient": hull, "donor": donor, "transform": transform, "crew_slots": crew}}

        with patch("workshop.compiler.compile_assembly", side_effect=compile_fixture), \
             patch("workshop.registration.rebuild_registrations", return_value={"registered": [], "skipped": []}), \
             patch("workshop.resources.ResourceIndex", side_effect=index_factory):
            result = self.state.repair({})
        # Parents first against the base stack; the chained vehicle sees the updated addon.
        self.assertEqual(order, [("mw_a", False), ("mw_c", False), ("mw_b", True)])
        # A changed crew size no longer keeps an old (maybe broken) version: the
        # vehicle is rebuilt and its campaign crew is fitted when opened.
        self.assertEqual(result["rebuilt"], ["mw_a", "mw_c", "mw_b"])
        self.assertEqual(result["skipped"], [])
        self.assertEqual((addon / "resource/entity/-vehicle/workshop/mw_c/mw_c.def").read_bytes(), b"{game_entity {rebuilt}}")
        self.assertEqual((addon / "resource/entity/-vehicle/workshop/mw_b/mw_b.def").read_bytes(), b"{game_entity {rebuilt}}")
        # Only the child is still present in this save. Its old parent is a
        # read-only compilation input, not another vehicle to update.
        save=self.root/'selected.sav'
        self.state.snapshot=SimpleNamespace(source_path=save,to_dict=lambda:{'units':[
            {'template':'mw_b','kind':'vehicle','owned':True},
            {'template':'mw_a','kind':'vehicle','owned':False}]})
        parent=addon/'resource/entity/-vehicle/workshop/mw_a/mw_a.def'
        previous=parent.read_bytes()
        (addon/'assembly_mw_unrelated.json').write_text('invalid unrelated recipe')
        order.clear()
        with patch("workshop.compiler.compile_assembly",side_effect=compile_fixture), \
             patch("workshop.registration.rebuild_registrations",return_value={"skipped":[]}) as register, \
             patch("workshop.resources.ResourceIndex",side_effect=index_factory):
            result=self.state.repair({})
        self.assertEqual(order,[("mw_b",True)])
        self.assertEqual(result['rebuilt'],['mw_b'])
        self.assertEqual(result['skipped'],[])
        self.assertEqual(parent.read_bytes(),previous)
        self.assertEqual(register.call_args.kwargs['identifiers'],{'mw_b'})


class LocalHTTPTests(unittest.TestCase):
    def test_report_endpoint_rejects_requests_without_an_open_campaign(self):
        headers={'X-Workshop-Token':self.state.token,'Content-Type':'application/json'}
        with patch('workshop.error_report.build_report') as build:
            status,raw,_=self.request('POST','/api/error-report',headers,b'{"quiet":true}')
            self.assertEqual(status,400)
            self.assertIn('Seleccioná primero una campaña',json.loads(raw)['error'])
            build.assert_not_called()
        self.assertFalse((self.state.workspace/'reports').exists())

    def test_interface_preferences_require_token_and_use_no_game_job(self):
        self.assertEqual(self.request('GET','/api/ui-preferences')[0],403)
        self.assertEqual(self.request('POST','/api/ui-preferences',body=b'{}')[0],403)
        headers={'X-Workshop-Token':self.state.token,'Content-Type':'application/json'}
        with patch.object(self.state,'job',side_effect=AssertionError('Not a game job')):
            status,data,_=self.request('POST','/api/ui-preferences',headers,b'{"language":"en","motion":true}')
            self.assertEqual(status,200)
            self.assertEqual(json.loads(data),{'language':'en','motion':True})
            self.assertEqual({k:v for k,v in json.loads(self.request('GET','/api/ui-preferences',headers)[1]).items() if k!='system_language'},json.loads(data))
            with patch('workshop.app.logging.exception'):
                self.assertEqual(self.request('POST','/api/ui-preferences',headers,b'null')[0],400)

    def test_cancelled_preview_response_does_not_record_error_or_send_twice(self):
        for method, route, body in [('GET', '/api/config', None), ('POST', '/api/check', b'{}')]:
            with self.subTest(method=method), patch.object(Handler, 'send_json', side_effect=ConnectionAbortedError()), patch.object(self.state, 'job', return_value={'job_id': 'fixture'}) as job, patch.object(self.state, 'record_error') as record:
                with self.assertRaises(http.client.RemoteDisconnected):
                    self.request(method, route, {'X-Workshop-Token': self.state.token}, body)
                self.assertEqual(Handler.send_json.call_count, 1)
                record.assert_not_called()
                self.assertEqual(job.call_count, int(method == 'POST'))
        self.assertEqual(self.request('GET', '/api/config', {'X-Workshop-Token': self.state.token})[0], 200)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = AppState(Path(self.temp.name) / "workspace")
        self.config_patch = patch.object(self.state, "config", return_value={"fixture": True}).start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.state = self.state
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.state.pool.shutdown(wait=True)
        patch.stopall()
        self.temp.cleanup()

    def request(self, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_closing_the_page_in_browser_mode_is_heard_only_with_the_token(self):
        self.server.bye = None
        self.assertEqual(self.request("POST", "/api/bye", {}, b'{"token":"wrong"}')[0], 204)
        self.assertIsNone(self.server.bye)
        self.assertEqual(self.request("POST", "/api/bye", {}, json.dumps({"token": self.state.token}).encode())[0], 204)
        self.assertGreaterEqual(self.server.bye, self.server.last_seen)

    def test_browser_mode_ends_after_the_page_closes_but_not_on_reload_or_while_busy(self):
        from workshop.app import page_gone
        self.assertFalse(page_gone(None, None, False, 10_000))       # no page yet
        self.assertFalse(page_gone(100, 100, False, 110))            # closing: short grace
        self.assertTrue(page_gone(100, 100, False, 120))
        self.assertFalse(page_gone(105, 100, False, 200))            # reloaded: the page called again
        self.assertFalse(page_gone(100, 100, True, 500))             # an operation is running
        self.assertFalse(page_gone(100, None, False, 350))           # a background tab polls slowly
        self.assertTrue(page_gone(100, None, False, 450))

    def test_api_requires_token_and_foreign_host_or_origin_cannot_obtain_session(self):
        status, data, headers = self.request("GET", "/api/config")
        self.assertEqual(status, 403)
        status, data, headers = self.request("GET", "/api/session", {"Host": "attacker.example"})
        self.assertEqual(status, 403)
        self.assertNotIn(self.state.token.encode(), data)
        status, data, headers = self.request("GET", "/api/session", {"Origin": "https://attacker.example"})
        self.assertEqual(status, 403)
        self.assertNotIn(self.state.token.encode(), data)
        status, data, headers = self.request("GET", "/api/config", {"X-Workshop-Token": self.state.token})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {"fixture": True})

    def test_static_paths_cannot_escape_web_root_and_deliver_security_headers(self):
        for path in ("/..%2Fapp.py", "/%2E%2E%5Capp.py", "/..%2F..%2FREADME.md"):
            with self.subTest(path=path):
                status, data, headers = self.request("GET", path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"class AppState", data)
        status, data, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_diagnostic_is_protected_read_only_json_not_a_file_export(self):
        for headers in ({}, {'X-Workshop-Token': self.state.token, 'Origin': 'https://attacker.example'}):
            status, data, _ = self.request('GET', '/api/diagnostic', headers)
            self.assertEqual(status, 403)
        with patch.object(self.state, 'closed', side_effect=AssertionError('No game action')):
            status, data, headers = self.request('GET', '/api/diagnostic', {'X-Workshop-Token': self.state.token})
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(json.loads(data)['schema'], 1)
        self.assertNotIn(self.state.token.encode(), data)

    def test_mutating_request_checks_origin_token_and_payload_size_before_dispatch(self):
        with patch.object(self.state, "job") as dispatch, patch("workshop.app.logging.exception"):
            status, data, headers = self.request("POST", "/api/generate", body=b"{}")
            self.assertEqual(status, 403)
            status, data, headers = self.request("POST", "/api/generate", {"X-Workshop-Token": self.state.token,
                                               "Origin": "https://attacker.example"}, body=b"{}")
            self.assertEqual(status, 403)
            status, data, headers = self.request("POST", "/api/generate", {"X-Workshop-Token": self.state.token,
                                               "Content-Length": str(1024 * 1024 + 1)}, body=b"{}")
            self.assertEqual(status, 400)
            dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class GameFolderDetectionTests(unittest.TestCase):
    """Field report 20/10: on any PC but the author's the game folder stayed
    empty and the tool looked for the game inside its own Workshop folder."""

    def make_game(self, library, folder='Call to Arms - Gates of Hell'):
        game = Path(library) / 'steamapps' / 'common' / folder
        (game / 'resource').mkdir(parents=True)
        (game / 'resource' / 'gamelogic.pak').write_bytes(b'')
        return game

    def test_found_next_to_the_workshop_item_and_in_other_libraries(self):
        from workshop import app
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = self.make_game(root / 'SteamLibrary')
            tool = root / 'SteamLibrary' / 'steamapps' / 'workshop' / 'content' / '400750' / '3823214707' / 'CursedWorkshop'
            tool.mkdir(parents=True)
            with patch.object(app, 'steam_libraries', return_value=[]):
                self.assertEqual(app.find_game_root(tool), str(game.resolve()))
                self.assertEqual(app.find_game_root(root), '')
            other = self.make_game(root / 'Games', 'GatesOfHell')
            (root / 'Games' / 'steamapps' / 'appmanifest_400750.acf').write_text(
                '"AppState"\n{\n\t"appid"\t\t"400750"\n\t"installdir"\t\t"GatesOfHell"\n}\n', encoding='utf-8')
            with patch.object(app, 'steam_libraries', return_value=[root / 'Games']):
                self.assertEqual(app.find_game_root(root), str(other.resolve()))

    def test_saved_folder_that_is_not_the_game_is_replaced_and_explained(self):
        from workshop import app
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / 'ws'
            workspace.mkdir()
            (workspace / 'settings.json').write_text(json.dumps({'game_root': directory, 'mod_roots': []}), encoding='utf-8')
            with patch.object(app, 'find_game_root', return_value=''):
                state = AppState(workspace)
                self.assertEqual(state.settings['game_root'], '')
                with self.assertRaisesRegex(ValueError, 'No se encontró la carpeta del juego'):
                    state.index_resources()
            self.assertTrue(app.friendly_error(ValueError('Game resource folder missing: x')).startswith(
                'No se encontró la carpeta del juego'))


class TextureTests(unittest.TestCase):
    def test_a_material_without_texture_is_drawn_plain_not_reported_as_an_error(self):
        # Glass and bare parts have no diffuse map: the 3D view draws them plain.
        with tempfile.TemporaryDirectory() as temp:
            state = AppState(Path(temp))
            state.index = SimpleNamespace(exists=lambda path: True)
            with patch('workshop.textures.diffuse_path', return_value=None):
                self.assertIsNone(state.texture('entity/vehicle/glass.mtl'))
            with self.assertRaises(ValueError):
                state.texture('entity/vehicle/glass.png')
