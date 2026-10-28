"""Whole composite recipes against a real ResourceIndex of synthetic resources."""
import copy
import json
import tempfile
import unittest
import shutil
from unittest.mock import patch
from pathlib import Path

from tests.test_commander_mg import fixture
from tests.test_gun_assembly import Resources
from tests.test_smoke_launchers import resources as smoke_resources,row as smoke_row
from tests.test_extra_armor import resources as armor_resources
from workshop.compiler import compile_assembly
from workshop.resources import ResourceIndex
from workshop.geometry import build_preview,parse_model
from workshop.extra_armor import catalog
from workshop.syntax import parse,block


class CompositeRecipeTests(unittest.TestCase):
    def test_two_saved_guns_keep_first_placement_and_migrate_both_ammo_families(self):
        from workshop.campaign import load_campaign,plan_assembly,apply_plan
        from tests.test_campaign import make_save,SCENE
        from workshop.geometry import point
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);game=root/'game';r=Resources()
            r.files['entity/donor/donor.def']=r.files['entity/donor/donor.def'].replace(b'{weapon "mg" filling "mg_round belt" 50}',b'{weapon "heavy_mg" filling "heavy_round belt" 50}').replace(b'{item "mg_round belt" 500}',b'{item "heavy_round belt" 400}')
            r.files['set/stuff/gun/heavy_mg']=b'{from "pattern gun" {filling "heavy_round"}}'
            r.files['set/stuff/shell/heavy_round.belt.ammo']=b'{ammo {entity "heavy_bullet"}}'
            for path,raw in r.files.items():
                p=game/'resource'/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            index=ResourceIndex(game).build()
            try:
                main={'schema':1,'recipient_place':'main_slot','donor_place':'main_slot','pose':{'position':[3,4,5],'rotation':[0,0,12]}}
                coax={'schema':1,'recipient_place':'coax_slot','donor_place':'coax_slot','pose':{'position':[0,0,0],'rotation':[0,0,0]}}
                first=compile_assembly(index,'receiver','donor',{'gun_swaps':[main]},root/'first','mw_test')
                batch=compile_assembly(index,'receiver','donor',{'gun_swaps':[main,coax]},root/'batch','mw_test')
                a=parse_model((root/'first/resource'/first['model_path']).read_bytes())
                b=parse_model((root/'batch/resource'/batch['model_path']).read_bytes())
                for x,y in zip(point(a['bones']['breech']['world'],[0,0,0]),point(b['bones']['breech']['world'],[0,0,0])):self.assertAlmostEqual(x,y,places=6)
                definition=parse((root/'batch/resource'/batch['definition_path']).read_bytes()).find('game_entity')
                slots={p.args[0]:p for w in definition.find_all('Weaponry') for p in w.find_all('place')}
                self.assertEqual(slots['main_slot'].find('weapon').args[0],'donor_weapon')
                self.assertEqual(slots['coax_slot'].find('weapon').args[0],'heavy_mg')
                self.assertIn('azimuth',b['bones']);self.assertEqual(len(batch['metadata']['gun_assemblies']),2)
                self.assertFalse(any('/workshop/mw_draft_' in p for p in batch['metadata']['source_hashes']))
                again=compile_assembly(index,'receiver','donor',batch['metadata']['transform'],root/'again','mw_test')
                self.assertEqual(batch['metadata']['file_hashes'],again['metadata']['file_hashes'])
                # Editing and saving the first row moves it to the end of the
                # draft. The unchanged coax still keeps its world placement.
                reordered=compile_assembly(index,'receiver','donor',{'gun_swaps':[coax,main]},root/'reordered','mw_test')
                other=parse_model((root/'reordered/resource'/reordered['model_path']).read_bytes())
                for name in ('breech','coax'):
                    for x,y in zip(point(b['bones'][name]['world'],[0,0,0]),point(other['bones'][name]['world'],[0,0,0])):self.assertAlmostEqual(x,y,places=6)
                    for old_row,new_row in zip(b['bones'][name]['world'],other['bones'][name]['world']):
                        for x,y in zip(old_row,new_row):self.assertAlmostEqual(x,y,places=6)
                scene=parse(SCENE)
                for entity in scene.find('campaign').find_all('Entity'):
                    for node in [*entity.find_all('Component'),*entity.find_all('Armor')]:entity.remove(node)
                raw=scene.render().replace('tank_hull','receiver').replace('wheeled_donor','donor').replace('{item "old_ammo" "ammo" 3 {cell 0 0}}','{item "short_round ap" 3}{item "mg_round belt" 23}{item "toolkit" 1}')
                source=make_save(root/'fixture.sav',raw);original=source.read_bytes();snapshot=load_campaign(source)
                plan=plan_assembly(snapshot,'0x9000','0x9010','mw_test',3,state_profile=batch['metadata']['state_profile'])
                after=parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign')
                inventory=next(n for n in after.find_all('Inventory') if n.args[0]==plan['new_eid']).render()
                for kept in ('long_round ap','long_round he','heavy_round belt','toolkit'):self.assertIn(kept,inventory)
                self.assertNotIn('short_round ap',inventory);self.assertNotIn('mg_round belt',inventory)
                self.assertEqual(source.read_bytes(),original)
            finally:index.close()

    def test_gun_mount_armor_smoke_and_visibility_survive_recipe_recompile_and_chaining(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);game=root/'game';fixture(game)
            files=Resources().files
            files['set/stuff/shell/mg_round.belt.ammo']=b'{ammo {entity "mg_bullet"}}'
            files.update(smoke_resources().files)
            definition=parse(files['entity/receiver/receiver.def']);entity=definition.find('game_entity')
            entity.find('placer').append(block('{place "commander" {group "crew"}{LinkBone "seat"}}'))
            entity.append(block('{boarder {door "emit"}{link "emit" "commander" {anm "entry"}{forward putoff}{reverse puton}}{anm "entry" {forward {end "native_pose"}}}}'))
            files['entity/receiver/receiver.def']=definition.render().encode()
            files['entity/receiver/receiver.mdl']=files['entity/receiver/receiver.mdl'].replace(b'{bone "coax"',b'{bone "seat"}{bone "coax"')
            armor=armor_resources();prefix='entity/construction/_military/fortifications/sandbag_test/'
            files[prefix+'sandbag_test.def']=b'{game_entity {extension "sandbag_test.mdl"}{volume "body" {thickness 20}{material "sand"}{tags "armor"}}}'
            files[prefix+'sandbag_test.mdl']=armor.files['entity/armor/armor.mdl']
            files[prefix+'plate.ply']=armor.files['entity/armor/plate.ply']
            for name,raw in files.items():
                path=game/'resource'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            index=ResourceIndex(game).build()
            try:
                asset=next(p['id'] for p in catalog(index) if 'sandbag_test' in p['id'])
                transform={'gun_swaps':[{'schema':1,'recipient_place':p,'donor_place':p,
                    'pose':{'position':[0,0,0],'rotation':[0,0,0]}} for p in ('main_slot','coax_slot')],
                    'commander_mg':{'support':'support:aa','parent':'azimuth','operator':'commander','item':'base_gun',
                        'position':[1,2,3],'rotation':[0,0,10],'yaw':[-90,60],'pitch':[-10,30]},
                    'crew_hidden':['commander'],
                    'extra_armor':[{'asset':asset,'parent':'body','position':[4,5,6],'rotation':[0,0,90]},
                        {'asset':asset,'parent':'elevation','position':[1,3,5],'rotation':[0,0,0]},
                        {'asset':asset,'parent':'cmdmg_pitch2','position':[7,8,9],'rotation':[0,0,0]}],
                    'smoke_launchers':[smoke_row(3,'body')]}
                original=copy.deepcopy(transform)
                first=compile_assembly(index,'receiver','donor',transform,root/'first','mw_composite')
                recipe=json.loads((root/'first/assembly_mw_composite.json').read_text())
                self.assertEqual(transform,original)
                self.assertEqual(recipe['metadata']['transform'],original)
                again=compile_assembly(index,recipe['metadata']['recipient'],recipe['metadata']['donor'],
                                       recipe['metadata']['transform'],root/'again','mw_composite')
                self.assertEqual(first['metadata']['file_hashes'],again['metadata']['file_hashes'])
                view=index.with_overlay(root/'first')
                try:
                    shown=build_preview(view,'mw_composite','full',include_inventory=True)
                    self.assertTrue(shown['meshes'])
                    self.assertFalse(any(d.get('code')=='mesh_unavailable' for d in shown['diagnostics']))
                    self.assertTrue(any(m.get('inventory_slot')==first['metadata']['commander_mg']['slot'] for m in shown['meshes']),shown['diagnostics'])
                    # A later weapon swap removes armor on that gun, but keeps
                    # hull armor and armor hung from the commander's mount.
                    swap={'assembly_draft':{'schema':1,'turrets':[],'guns':[{
                        'recipient_place':'main_slot','donor_place':'main_slot','donor_template':'donor',
                        'pose':{'position':[2,0,1],'rotation':[0,0,5],'pivot':[1,0,0]}}]}}
                    replaced=compile_assembly(view,'mw_composite','donor',swap,root/'replaced','mw_replaced')
                    child=view.with_overlay(root/'replaced')
                    try:
                        from workshop.editable_recipe import reopen
                        model=parse_model((root/'replaced/resource'/replaced['model_path']).read_bytes())
                        restored,cmd,_=reopen(replaced['metadata'],{'mw_composite':first['metadata']},model)
                        self.assertEqual(len(restored['extra_armor']),2,'removed gun armor must not reappear')
                        self.assertTrue(any(r.get('parent_ref') for r in restored['extra_armor']))
                        # Match the editor's seed promotion after opening a built design.
                        edited=copy.deepcopy(restored);edited.pop('source_recipe',None)
                        edited['assembly_draft']={'schema':1,'turrets':[],'guns':[],
                            'seed':{'recipient':'mw_composite','donor':'donor','transform':restored}}
                        camera=block('{native {level {linkBone "frame"}}{level {zoom}{linkBone "AZIMUTH"}}}')
                        with patch('workshop.registration._native_camera_profile',return_value=(camera, [])):
                            rebuilt=compile_assembly(child,'mw_replaced','mw_replaced',edited,root/'reopened','mw_reopened')
                        self.assertEqual(len(rebuilt['metadata']['extra_armor']),2)
                        final=parse_model((root/'reopened/resource'/rebuilt['model_path']).read_bytes())
                        roots=[n for n in final['bones'] if n.startswith('mw_armor_') and n.endswith('_root')]
                        self.assertEqual(len(roots),2,'armor is reapplied once, after all structural changes')
                        self.assertEqual(set(roots),{'mw_armor_0_root','mw_armor_2_root'},'surviving damage IDs must not be renumbered into the removed fitting')
                        for name in ('mw_armor_0_root','mw_armor_2_root'):
                            for before,after in zip(model['bones'][name]['world'],final['bones'][name]['world']):
                                for x,y in zip(before,after):self.assertAlmostEqual(x,y,places=5)
                        self.assertEqual(rebuilt['metadata']['gun_camera_donor'],'donor')
                        self.assertIn(rebuilt['metadata']['camera_bone_mapping']['AZIMUTH'],final['bones'],
                            'an accessory edit must keep the seed weapon camera and exported optical socket')
                        root_def=parse((root/'reopened/resource'/rebuilt['definition_path']).read_bytes()).find('game_entity')
                        slots=[p.args[0] for w in root_def.find_all('Weaponry') for p in w.find_all('place') if p.args and p.args[0].startswith('mgun_cmd')]
                        self.assertEqual(len(slots),1,'the inherited commander MG must not be added twice')
                    finally:child.close()
                    # Add another fitting to the already compiled vehicle, retaining
                    # the original gun, mount and other independent attachments.
                    chained=compile_assembly(view,'mw_composite','mw_composite',{'commander_only':True,
                        'extra_armor':[{'asset':asset,'parent':'body','position':[-4,5,6],'rotation':[0,0,0]}]},root/'chained','mw_chained')
                    model=parse_model((root/'chained/resource'/chained['model_path']).read_bytes())
                    self.assertIn('azimuth',model['bones']);self.assertIn('coax',model['bones'])
                    self.assertIn('mw_armor_0_root',model['bones']);self.assertIn('mw_armor_1_root',model['bones'])
                    self.assertIn('cw_smoke_0_root',model['bones'])
                    text=(root/'chained/resource'/chained['definition_path']).read_text()
                    self.assertIn('donor_weapon',text);self.assertIn('mgun_cmd',text)
                    self.assertFalse(chained['runtime_verified'])
                    # Rebuild both recipe generations through the actual app. Only
                    # roster registration is a boundary double in this fixture.
                    from workshop.app import AppState
                    from workshop.transactions import _tree_hashes,OWNERSHIP_FILE,_owned_addon
                    addon=game/'mods/modular_workshop'
                    shutil.copytree(root/'first',addon)
                    shutil.copytree(root/'chained',addon,dirs_exist_ok=True)
                    (addon/'mod.info').write_text('{mod {name "Fixture"}}')
                    (addon/OWNERSHIP_FILE).write_text(json.dumps({'addon_mod':'modular_workshop:0','files':_tree_hashes(addon)}))
                    state=AppState(root/'workspace');state.settings={'game_root':str(game),'mod_roots':[str(addon)]}
                    from types import SimpleNamespace
                    save=root/'selected.sav';save.write_bytes(b'fixture')
                    state.snapshot=SimpleNamespace(source_path=save,to_dict=lambda:{'units':[{'template':i,'kind':'vehicle','owned':True} for i in ('mw_composite','mw_chained')]})
                    try:
                        with patch.object(state,'closed',return_value={}),patch('workshop.registration.rebuild_registrations',return_value={'skipped':[]}):
                            rebuilt=state.repair({})
                        self.assertEqual(rebuilt['rebuilt'],['mw_composite','mw_chained'])
                        self.assertEqual(rebuilt['skipped'],[])
                        for identifier in rebuilt['rebuilt']:
                            stored=json.loads((addon/f'assembly_{identifier}.json').read_text())
                            before=first if identifier=='mw_composite' else chained
                            self.assertEqual(stored['metadata']['transform'],before['metadata']['transform'])
                        _owned_addon(addon,'modular_workshop:0')
                        self.assertTrue(Path(rebuilt['backup']).is_dir())
                    finally:state.pool.shutdown(wait=True)
                finally:view.close()
            finally:index.close()


if __name__=='__main__':unittest.main()
