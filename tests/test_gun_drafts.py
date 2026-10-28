import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_gun_assembly import Resources
from workshop.resources import ResourceIndex
from workshop.app import AppState
from workshop.gun_assembly import gun_changes,GunError


class GunDraftTests(unittest.TestCase):
    def test_preview_keeps_saved_main_when_selecting_coax_and_expires_old_texture_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);game=root/'game'
            for name,raw in Resources().files.items():
                p=game/'resource'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            state=AppState(root/'workspace');state.settings={'game_root':str(game),'mod_roots':[]}
            state.index=ResourceIndex(game).build()
            row={'schema':1,'recipient_place':'main_slot','donor_place':'main_slot','pose':{'position':[1,2,3],'rotation':[0,0,0]}}
            body={'hull':'receiver','donor':'donor','gun_swaps':[row]};original=copy.deepcopy(body)
            try:
                first=state.gun_preview(body);old=state.gun_draft_preview
                self.assertEqual(first['turret']['meshes'],[])
                self.assertTrue(first['hull']['meshes']);self.assertEqual(body,original)
                second=state.gun_preview({**body,'gun_swap':{'schema':1,'recipient_place':'coax_slot','donor_place':'coax_slot'}})
                self.assertTrue(second['hull']['meshes']);self.assertTrue(second['turret']['meshes'])
                from workshop.gun_assembly import selected
                from workshop.geometry import point
                _,_,_,model,_,spec=selected(state.index,'receiver','coax_slot')
                self.assertEqual(second['hull']['anchor'],point(model['bones'][spec['pivot']]['world'],[0,0,0]))
                self.assertFalse(Path(old['temp'].name).exists())
                self.assertNotEqual(old['token'],state.gun_draft_preview['token'])
                self.assertTrue(all(s['texture_token']==state.gun_draft_preview['token'] for part in ('hull','turret') for m in second[part]['meshes'] for s in m['submeshes']))
                with self.assertRaises(ValueError):state.texture('anything.mtl',old['token'])
                current=Path(state.gun_draft_preview['temp'].name);state.clear_portrait_previews();self.assertFalse(current.exists())
            finally:state.clear_portrait_previews();state.index.close();state.pool.shutdown(wait=True)

    def test_saved_changes_never_accept_duplicate_or_invalid_placements(self):
        row={'schema':1,'recipient_place':'gun','donor_place':'gun','pose':{'position':[0,0,0],'rotation':[0,0,0]}}
        for value in ([row,row],[{**row,'recipient_place':''}],[{**row,'pose':{'position':[True,0,0]}}],
                      [{**row,'pose':{'rotation':[0,float('nan'),0]}}],[{**row,'pose':{'position':[100001,0,0]}}]):
            with self.subTest(value=value),self.assertRaises(GunError):gun_changes({'gun_swaps':value})
        # No cap on how many weapons one design changes: every place once.
        valid=[{**row,'recipient_place':str(i)} for i in range(20)]
        self.assertEqual(len(gun_changes({'gun_swaps':valid})),20)
        self.assertEqual(len(gun_changes({'gun_swaps':valid,'gun_swap':{'recipient_place':'last','donor_place':'gun'}})),21)


if __name__=='__main__':unittest.main()
