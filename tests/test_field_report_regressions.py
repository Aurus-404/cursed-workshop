"""Regression cases from the real El epico report, with synthetic resources."""
import copy
import tempfile
import unittest
from pathlib import Path

from workshop.syntax import parse, block
from workshop.campaign import (_draft_inventory, load_campaign, plan_edit, apply_plan)
from workshop.compiler import compile_assembly
from workshop.smoke_launchers import add_interactions
from workshop.commander_mg import add_interactions as add_commander_interactions
from tests.test_gun_assembly import Resources
from tests.test_campaign import SCENE, make_save


class FieldReportTests(unittest.TestCase):
    def test_a_gun_whose_weapon_is_declared_by_its_variant_keeps_it(self):
        # Potato Emperor, 21/10: Valentine + British 6-pdr. 57mm_qf6_mk2.inc
        # declares the gun's crew and aiming; the variant adds the weapon in a
        # second Weaponry block. Only the first was read: the gun could not fire.
        from workshop.compiler import _merge_weaponry
        root=parse('''{game_entity
            {Weaponry ("restore_ik_time") {place "gun" {charger "charger"} ("abm_gun" zeroing(1.5))}}
            {extender "inventory" {box {item "bullet_57 ap" 49}}}
            {Weaponry ("restore_ik_time") {place "gun" {weapon "57mm_qf6" filling "bullet_57 ap" 1} ("abm_gun" zeroing(2))}
                      {place "mgun" {weapon "bren"}}}}''').find('game_entity')
        _merge_weaponry(root)
        self.assertEqual(len(root.find_all('Weaponry')),1)
        weaponry=root.find('Weaponry')
        self.assertEqual([p.args[0] for p in weaponry.find_all('place')],['gun','mgun'])
        gun=weaponry.find('place')
        self.assertEqual(gun.find('weapon').args[0],'57mm_qf6')
        self.assertEqual(gun.find('charger').args,['charger'])
        self.assertEqual([n.render().split()[-1] for n in gun.find_all('abm_gun')],['zeroing(2))'])
        self.assertEqual(len(weaponry.find_all('restore_ik_time')),1)

    def test_saved_item_formats_remove_retired_shells_and_keep_commander_reserves(self):
        inv=block('''{Inventory 0x9000 {box {clear}
            {item "bulletger_37" "aphe" "ammo" 28 {cell 0 3}}
            {item "bulletger_37" "he" "ammo" 36 {cell 3 3}}
            {item "bulletger_37 apcr" 7}
            {item "mgun_ger" "belt" "ammo" 2500 {cell 0 4}}
            {item "mg42" filled {user "mgun_cmd2"}}{item "repair_kit" 3}}}''')
        original=inv.render()
        steps=[{'kind':'seed','profile':{}},*[
            {'kind':'gun','donor_eid':eid,'profile':{'inventory_policy':'gun_replace',
             'gun_swap':{'old_ammo':old,'ammo_items':[item],'initialize':True}}}
            for eid,old,item in [('0x9010','bulletger_37','{item "bulletrus_76 he" 44}'),
                                 ('0x9020','mgun_ger','{item "bulletger_20_auto he" 180}')]]]
        profile={'gun_swap':{'old_ammo_families':['bulletger_37'],'ammo_items':[]}}
        result=_draft_inventory({'0x9000':inv},'0x9000',steps,profile)
        items=list(result.walk('item'))
        self.assertFalse(any('bulletger_37' in i.args[0] for i in items))
        self.assertEqual(next(i for i in items if i.args[0]=='mgun_ger').args,['mgun_ger','belt','ammo','2500'])
        self.assertEqual(next(i for i in items if i.args[0]=='repair_kit').args,['repair_kit','3'])
        self.assertEqual(next(i for i in items if i.args[0]=='mg42').find('user').args,['mgun_cmd2'])
        self.assertEqual(inv.render(),original)
        self.assertEqual(len(items),5)

    def test_final_cleanup_removes_intermediate_grants_and_legacy_draft_still_works(self):
        inv=block('{Inventory 0x1 {box {item "old" "he" "ammo" 12}}}')
        steps=[{'kind':'gun','donor_eid':'0x2','profile':{'inventory_policy':'gun_replace',
            'gun_swap':{'old_ammo':'old','ammo_items':['{item "intermediate he" 9}']}}}]
        legacy=_draft_inventory({'0x1':inv},'0x1',steps)
        self.assertEqual([i.args[0] for i in legacy.walk('item')],['intermediate he'])
        final=_draft_inventory({'0x1':inv},'0x1',steps,{'gun_swap':{'old_ammo_families':['old','intermediate']}})
        self.assertEqual(list(final.walk('item')),[])

    def test_edit_existing_design_cleans_proven_unused_ammo_without_replenishing(self):
        scene=SCENE.replace('{item "old_ammo" "ammo" 3 {cell 0 0}}',
            '{item "bulletger_37" "he" "ammo" 36}{item "mgun_ger" "belt" "ammo" 123}'
            '{item "bulletrus_76" "he" "ammo" 2}{item "repair_kit" 1}')
        with tempfile.TemporaryDirectory() as folder:
            source=make_save(Path(folder)/'copy.sav',scene);before=source.read_bytes()
            snapshot=load_campaign(source)
            profile={'gun_swap':{'place':'gun','old_ammo':None,'old_ammo_families':['bulletger_37'],
                     'ammo_items':['{item "bulletrus_76 he" 44}'],'initialize':False}}
            plan=plan_edit(snapshot,'0x9000','mw_field_edit',2,state_profile=profile,keep_names=True)
            out=parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign')
            inv=next(n for n in out.find_all('Inventory') if n.args==[plan['new_eid']])
            items={i.args[0]:i.args for i in inv.walk('item')}
            self.assertNotIn('bulletger_37',items)
            self.assertEqual(items['bulletrus_76'],['bulletrus_76','he','ammo','2'])
            self.assertEqual(items['mgun_ger'],['mgun_ger','belt','ammo','123'])
            self.assertEqual(items['repair_kit'],['repair_kit','1'])
            self.assertEqual(source.read_bytes(),before)

    def test_coaxial_selection_and_role_survive_vehicle_and_towed_primary_donors(self):
        for donor in ('donor','towed'):
            with self.subTest(donor=donor),tempfile.TemporaryDirectory() as folder:
                resources=Resources()
                path='entity/receiver/receiver.def'
                resources.files[path]=resources.files[path].replace(b'{type "coaxial"}',b'{type "coaxial"}{basic}{ai_in_manual}')
                source='entity/'+donor+'/'+donor+'.def'
                resources.files[source]=resources.files[source].replace(b'{place "main_slot"',b'{place "main_slot" {type "hull"}')
                tr={'gun_swap':{'schema':1,'recipient_place':'coax_slot','donor_place':'main_slot'}}
                result=compile_assembly(resources,'receiver',donor,tr,Path(folder)/'out','mw_field')
                root=parse((Path(result['output_dir'])/'resource'/result['definition_path']).read_bytes()).find('game_entity')
                places={p.args[0]:p for w in root.find_all('Weaponry') for p in w.find_all('place')}
                self.assertEqual(places['coax_slot'].find('type').args,['coaxial'])
                self.assertIsNotNone(places['coax_slot'].find('basic'))
                self.assertIsNotNone(places['coax_slot'].find('ai_in_manual'))
                self.assertEqual(places['coax_slot'].find('weapon').args[0],donor+'_weapon')
                self.assertEqual(places['coax_slot'].find('gunner').args,['gunner'])
                self.assertEqual(places['main_slot'].find('weapon').args[0],'receiver_weapon')
                self.assertFalse(result['runtime_verified'])

    def test_repeated_smoke_registration_updates_mounts_and_preserves_native_effects(self):
        contract={'grenade':'nbk39','mounts':[{'muzzles':[
            {'bone':'cw_smoke_0_shot'+str(i),'fx':'cw_smoke_0_flash'+str(i)} for i in range(3)]}]}
        record=block('''{"mw_field tank" {on "smoke_screen" overload {inherited}
            {tags add "native"}{delay 1 {spawn "nbk39" "smoke_shot1" x}}
            {delay 5 "native_timer"}}}''')
        for _ in range(3):add_interactions(record,contract)
        event=record.find_all('on')[0]
        generated=[s for s in event.walk('spawn') if len(s.args)>1 and s.args[1].startswith('cw_smoke_')]
        self.assertEqual(len(generated),6)  # three projectiles and three flashes
        self.assertEqual(len({s.args[1] for s in generated}),6)
        self.assertEqual(len(list(event.walk('inherited'))),1)
        self.assertIn('native_timer',event.render());self.assertIn('smoke_shot1',event.render())
        changed=copy.deepcopy(contract);changed['mounts'][0]['muzzles']=changed['mounts'][0]['muzzles'][:1]
        add_interactions(record,changed)
        self.assertNotIn('cw_smoke_0_shot2',event.render())
        add_interactions(record,None)
        self.assertNotIn('cw_smoke_',event.render())
        self.assertIn('smoke_shot1',event.render());self.assertIn('native_timer',event.render())

    def test_commander_registration_does_not_accumulate_generated_hatch_clips(self):
        record=block('{"mw_field tank" {on "commander_hatch_open" {ani_play "open"}}}')
        for sequence in ['cmdmg_pose2','cmdmg_pose2','cmdmg_pose3']:
            add_commander_interactions(record,{'sequence':sequence})
        for h in record.find_all('on'):
            clips=[p.args[0] for p in h.find_all('ani_play')]
            self.assertEqual(clips.count('cmdmg_pose3'),1)
            self.assertNotIn('cmdmg_pose2',clips)
        self.assertIn('open',record.find_all('on')[0].render())


if __name__=='__main__':unittest.main()
