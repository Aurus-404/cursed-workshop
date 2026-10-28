import copy
import json
import tempfile
import unittest
from pathlib import Path
from tests.test_commander_mg import fixture
from tests.test_smoke_launchers import resources as smoke_resources
from workshop.compiler import compile_assembly
from workshop.editable_recipe import reopen
from workshop.geometry import parse_model
from workshop.resources import ResourceIndex
from workshop.draft import weapon_identity


class EditableRecipeTests(unittest.TestCase):
    def test_old_replayed_source_defers_armor_without_losing_surviving_rows(self):
        row={'asset':'fixture#plate','parent':'body','position':[1,2,3],'rotation':[0,0,0]}
        parent={'recipient':'hull','donor':'hull','transform':{'extra_armor':[row]},
            'extra_armor':[{**row,'bones':['armor_root','plate']}]}
        source={'template':'mw_parent','recipient':'hull','donor':'hull','transform':{'extra_armor':[row]},'parent':None}
        metadata={'recipient':'mw_parent','donor':'mw_parent','transform':{'source_recipe':source,'commander_only':True}}
        original=copy.deepcopy(metadata)
        model=parse_model('{skeleton {bone "body" {bone "armor_root" {bone "plate"}}}}')
        restored,_,_=reopen(metadata,{'mw_parent':parent},model)
        self.assertEqual(restored['extra_armor'],[row])
        self.assertNotIn('extra_armor',restored['source_recipe']['transform'])
        self.assertIn('extra_armor',restored['source_recipe']['deferred_fittings'])
        self.assertEqual(metadata,original)

    def test_attachment_socket_follows_rebuilt_mount_but_respects_manual_parent_change(self):
        from workshop.editable_recipe import fitting_parents
        row={'parent':'old_pitch','parent_ref':{'name':'old_pitch','kind':'commander_mg','bone':'pitch'}}
        tr={field:[copy.deepcopy(row)] for field in ('extra_armor','smoke_launchers','rider_mgs')}
        resolved=fitting_parents(tr,{'bones':{'pitch':'new_pitch'}})
        for field in tr:self.assertEqual(resolved[field][0]['parent'],'new_pitch')
        self.assertEqual(tr['extra_armor'][0]['parent'],'old_pitch','saved recipes stay immutable')
        tr['extra_armor'][0]['parent']='body'
        self.assertEqual(fitting_parents(tr,{'bones':{'pitch':'new_pitch'}})['extra_armor'][0]['parent'],'body')
        with self.assertRaisesRegex(ValueError,'punto de fijación'):fitting_parents(tr,None)

    def test_inherited_accessories_reopen_move_remove_and_roundtrip_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);game=root/'game';fixture(game)
            smoke=smoke_resources()
            for path,raw in smoke.files.items():
                if path.startswith('entity/receiver/') or path.startswith('entity/donor/'):continue
                target=game/'resource'/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            index=ResourceIndex(game).build();views=[]
            try:
                cmd={'parent':'turret','support':'native_support:aa','item':'base_gun','position':[1,2,3],
                     'rotation':[0,0,0],'yaw':[-90,90],'pitch':[-20,45]}
                # Use the fixture's actual catalog identity, independent of a vehicle name.
                from workshop.commander_mg import support_catalog
                cmd['support']=support_catalog(index)[0]['id']
                tr={'commander_only':True,'commander_mg':cmd,
                    'smoke_launchers':[{'parent':'body','tubes':2,'position':[2,3,4],'rotation':[0,0,0]}]}
                first=compile_assembly(index,'hull','hull',tr,root/'first','mw_first')
                view=index.with_overlay(root/'first');views.append(view)
                second=compile_assembly(view,'mw_first','mw_first',{'commander_only':True},root/'second','mw_second')
                view=view.with_overlay(root/'second');views.append(view)
                model=parse_model((root/'second/resource'/second['model_path']).read_bytes())
                recipes={'mw_first':first['metadata'],'mw_second':second['metadata']}
                # Exercise the actual Edit endpoint's source selection too.
                from workshop.app import AppState
                from types import SimpleNamespace
                (root/'second/assembly_mw_first.json').write_text(json.dumps(first),encoding='utf-8')
                state=AppState(root/'workspace');state.index=view
                state.snapshot=SimpleNamespace(to_dict=lambda:{'units':[{'eid':'0x1','kind':'vehicle','template':'mw_second'}]})
                state.campaign_data=lambda:state.snapshot.to_dict()
                state.addon_root=lambda:root/'second'
                source=state.edit_source({'eid':'0x1'})
                self.assertIn('commander_mg',source['transform'])
                self.assertEqual(len(source['transform']['smoke_launchers']),1)
                state.pool.shutdown(wait=True)
                restored,contract,smoke=reopen(second['metadata'],recipes,model)
                self.assertEqual(contract['slot'],first['metadata']['commander_mg']['slot'])
                self.assertEqual(len(restored['smoke_launchers']),1)
                self.assertEqual(restored['commander_mg'],cmd)
                self.assertEqual(recipes['mw_second'],second['metadata'],'reopening never changes stored recipes')
                restored['commander_mg']['position'][0]=7
                restored['smoke_launchers'][0]['position'][1]=8
                result=compile_assembly(view,'mw_first','mw_first',restored,root/'moved','mw_moved')
                final=parse_model((root/'moved/resource'/result['model_path']).read_bytes())
                self.assertEqual(len([n for n in final['bones'] if n.startswith('cw_smoke_') and n.endswith('_root')]),1)
                self.assertEqual(result['metadata']['smoke_launchers']['mounts'][0]['position'][1],8)
                self.assertEqual(len(view._reopened_sources),1)
                cached=next(iter(view._reopened_sources.values()))['temp'].name
                removed=copy.deepcopy(restored);removed.pop('commander_mg');removed['smoke_launchers']=[]
                gone=compile_assembly(view,'mw_first','mw_first',removed,root/'removed','mw_removed')
                gone_model=parse_model((root/'removed/resource'/gone['model_path']).read_bytes())
                self.assertFalse(any(n.startswith(('cmdmg_','cw_smoke_')) for n in gone_model['bones']))
                again=compile_assembly(view,'mw_first','mw_first',restored,root/'again','mw_moved')
                self.assertEqual(result['metadata']['file_hashes'],again['metadata']['file_hashes'])
                seeded={'assembly_draft':{'schema':1,'guns':[],'turrets':[],
                    'seed':{'recipient':'mw_first','donor':'mw_first','transform':restored}},
                    'commander_mg':restored['commander_mg'],'smoke_launchers':restored['smoke_launchers']}
                next_design=compile_assembly(view,'mw_second','mw_second',seeded,root/'next','mw_next')
                next_model=parse_model((root/'next/resource'/next_design['model_path']).read_bytes())
                self.assertEqual(len([n for n in next_model['bones'] if n.startswith('cw_smoke_') and n.endswith('_root')]),1)
                self.assertEqual(next_design['metadata']['state_profile']['hull_authority'],'mw_second')
                self.assertNotEqual(weapon_identity(restored),weapon_identity({**restored,'source_recipe':{}}))
                view.close()
                self.assertFalse(Path(cached).exists(),'index close releases replay staging')
            finally:
                for view in reversed(views):view.close()
                index.close()

    def test_missing_fittings_are_not_restored_and_cycles_are_rejected(self):
        metadata={'recipient':'mw_parent','donor':'mw_parent','transform':{'commander_only':True}}
        parent={'recipient':'hull','donor':'hull','transform':{'smoke_launchers':[]},
                'smoke_launchers':{'mounts':[{'root':'removed','tubes':1,'parent':'body'}]}}
        tr,_,smoke=reopen(metadata,{'mw_parent':parent},parse_model('{skeleton {bone "body"}}'))
        self.assertNotIn('source_recipe',tr);self.assertIsNone(smoke)
        parent['recipient']='mw_parent'
        with self.assertRaisesRegex(ValueError,'Cyclic'):reopen(metadata,{'mw_parent':parent},parse_model('{skeleton {bone "body"}}'))
