import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_gun_assembly import Resources
from workshop.compiler import compile_assembly
from workshop.dismantling import compile_parts
from workshop.geometry import parse_model
from workshop.gun_assembly import discover
from workshop.resources import ResourceIndex
from workshop.syntax import entity_root,parse


class ComponentTests(unittest.TestCase):
    def test_dismantle_and_reassemble_shell_and_gun_without_duplicate_geometry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);game=root/'game';fixture=Resources()
            for path,raw in fixture.files.items():
                target=game/'resource'/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            index=ResourceIndex(game).build();views=[];view=index
            try:
                parts,results=compile_parts(index,'receiver','Receiver',root/'parts')
                self.assertEqual([r['part'] for r in parts['parts']],['hull','turret','gun'])
                for result in results:view=view.with_overlay(result['output_dir']);views.append(view)
                hull=next(p for p in parts['parts'] if p['part']=='hull')
                turret=next(p for p in parts['parts'] if p['part']=='turret')
                gun=next(p for p in parts['parts'] if p['part']=='gun')
                empty=next(r for r in results if r['id']==turret['template'])
                model=parse_model((Path(empty['output_dir'])/'resource'/empty['model_path']).read_bytes())
                self.assertEqual(model['bones']['breech']['assets'],[])
                runtime=(Path(empty['output_dir'])/'resource'/empty['definition_path']).read_text()
                self.assertNotIn('cw_empty_socket',runtime)
                self.assertNotIn('receiver_weapon',runtime)
                installed=compile_assembly(view,hull['template'],turret['template'],{'recipient_mount':'@free','donor_mount':turret['mount']},root/'empty','mw_empty_components')
                view=view.with_overlay(installed['output_dir']);views.append(view)
                socket=next(w for w in discover(view,installed['id'])['weapons'] if w.get('empty'))
                armed=compile_assembly(view,installed['id'],gun['template'],{'gun_swap':{'schema':1,'recipient_place':socket['place'],'donor_place':gun['place']}},root/'armed','mw_armed_components')
                final=entity_root(parse((Path(armed['output_dir'])/'resource'/armed['definition_path']).read_bytes()))
                self.assertEqual(sum(p.find('weapon').args[0]=='receiver_weapon' for w in final.find_all('Weaponry') for p in w.find_all('place') if p.find('weapon')),1)
                self.assertFalse(any(n.head=='cw_empty_socket' for n in final.walk()))
                # The same source can back a second physical vehicle's stock
                # without making another persistent asset copy.
                _,reused=compile_parts(view,'receiver','Receiver',root/'repeat')
                self.assertEqual(reused,[])
            finally:
                for value in reversed(views):value.close()
                index.close()


class DismantlingApplicationTests(unittest.TestCase):
    def setUp(self):
        from tests.test_vehicle_storage import StorageApplicationTests
        from tests.test_campaign import make_save,SCENE
        from workshop.campaign import load_campaign
        StorageApplicationTests.setUp(self)
        for path,raw in Resources().files.items():
            target=self.game/'resource'/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        self.state.index=ResourceIndex(self.game).build();self.addCleanup(self.state.index.close)
        self.state.snapshot=load_campaign(make_save(self.source,SCENE.replace('tank_hull','receiver')))
        self.state.campaign_view=self.state.snapshot.to_dict();self.original=self.state.snapshot

    def test_confirmed_dismantle_consumes_one_vehicle_preserves_crew_and_credits_parts_once(self):
        from workshop.campaign import load_campaign
        from workshop.depot import Depot
        body={'eid':'0x9000','source_hash':self.original.source_hash}
        preview=self.state.dismantle_preview(body)
        self.assertEqual(len(preview['parts']),3)
        self.assertEqual(self.source.read_bytes(),self.original._zip_bytes)
        with patch('workshop.registration.register_crew_reserves'):
            result=self.state.dismantle_vehicle(body)
        after=load_campaign(self.source)
        self.assertNotIn('0x9000',{u['eid'] for u in after.units})
        self.assertTrue({'0x9001','0x9002'}<={u['eid'] for u in after.units})
        depot=Depot(self.state.workspace)
        first=depot.items(self.source);self.assertEqual(len(first),3)
        self.assertEqual(first,depot.items(self.source))
        self.assertEqual({p['part'] for p in first},{'hull','turret','gun'})
        with self.assertRaises(ValueError):self.state.dismantle_vehicle(body)
        self.assertEqual(len(depot.items(self.source)),3)

    def test_failed_publication_never_credits_components(self):
        from workshop.depot import Depot
        body={'eid':'0x9000','source_hash':self.original.source_hash}
        with patch('workshop.registration.register_crew_reserves'),patch('workshop.transactions.commit_transaction',side_effect=OSError('publication failed')):
            with self.assertRaisesRegex(OSError,'publication failed'):self.state.dismantle_vehicle(body)
        self.assertEqual(self.source.read_bytes(),self.original._zip_bytes)
        self.assertEqual(Depot(self.state.workspace).items(self.source),[])

    def test_interruption_after_commit_recovers_the_stock_exactly_once(self):
        from workshop.depot import Depot
        body={'eid':'0x9000','source_hash':self.original.source_hash}
        with patch('workshop.registration.register_crew_reserves'),patch.object(Depot,'recover',side_effect=OSError('stopped after commit')):
            with self.assertRaisesRegex(OSError,'stopped after commit'):self.state.dismantle_vehicle(body)
        self.assertNotEqual(self.source.read_bytes(),self.original._zip_bytes)
        depot=Depot(self.state.workspace)
        rows=depot.items(self.source);self.assertEqual(len(rows),3)
        self.assertEqual(rows,Depot(self.state.workspace).items(self.source))

    def test_a_standalone_gun_cannot_donate_another_weapon_from_its_source(self):
        from workshop.depot import Depot
        item=Depot(self.state.workspace).apply(self.source,[{'part':'gun','template':'receiver','place':'main_slot','label':'Gun'}],[])[0]
        before=self.source.read_bytes()
        with self.assertRaisesRegex(ValueError,'cañón guardado'):
            self.state.generate({'recipient_eid':'0x9000','donor_depot':item['id'],'name':'Invalid stock',
                'transform':{'gun_swap':{'schema':1,'recipient_place':'main_slot','donor_place':'coax_slot'}}})
        self.assertEqual(self.source.read_bytes(),before)
        self.assertEqual(len(Depot(self.state.workspace).items(self.source)),1)
