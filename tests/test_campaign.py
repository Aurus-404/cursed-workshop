import io
import json
import os
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from workshop.campaign import (CampaignError, apply_plan, build_save_copy,
                               load_campaign, plan_assembly, plan_edit, plan_refit, release_orphan_guns)
from workshop.syntax import parse


SCENE = '''; fixture written independently from observed block contracts
{campaign
 {Entity "tank_hull" 0x9000 {MID 20} {Score 13.25} {Chassis {FuelBag {Remain 76.4}}}
  {Armor {wounds {damage "body" 0.25 1} {wound "turret" 0.5 1 20}}}
  {Component "engine" {HealthBroken 42}} {Component "turret" {HealthBroken 12}}
  {Weaponry {State {Bullets 7}}}}
 {Human "mp/ger/mid/tankman" 0x9001 {MID 21} {Extender "vitality" {Veterancy 8}} {Score 71}}
 {Human "mp/ger/mid/tank_commander" 0x9002 {MID 22} {NameId 11 15}}
 {Entity "wheeled_donor" 0x9010 {MID 30}
  {Armor {wounds {damage "wheel" 0.3 1} {wound "turret" 0.7 1 7.62}}}
  {Component "turret" {HealthBroken 17}} {Weaponry {State {Bullets 19}}}}
 {Human "mp/ger/mid/tankman" 0x9011 {MID 31} {Extender "vitality" {Veterancy 6}}}
 {Human "mp/ger/mid/tankman" 0x9012 {MID 32} {Extender "vitality" {Veterancy 4}}}
 {Entity "truck_third" 0x9020 {MID 40} {UnknownState "preserve me"}}
 {Human "mp/ger/mid/driver" 0x9021 {MID 41}}
 {Human "mp/ger/mid/rifleman" 0x9030 {MID 50} {Extender "vitality" {Veterancy 9}}}
 {Inventory 0x9000 {box {clear} {item "old_ammo" "ammo" 3 {cell 0 0}}}}
 {Inventory 0x9010 {box {clear} {item "20mm" "ammo" 9 {cell 0 0}}}}
 {Inventory 0x9001 {box {item "p38" filled {cell 0 0}}}}
 {Link 0x9001 {0x9000 "driver"}}
 {Link 0x9002 {0x9000 "gunner"}}
 {Link 0x9011 {0x9010 "driver"}}
 {Link 0x9012 {0x9010 "gunner"}}
 {Link 0x9021 {0x9020 "driver"}}
 {CampaignSquads
  {"tank_hull" "stage_2" 0x9000 0x9001 0x9002}
  {"wheeled_donor" "stage_1" 0x9010 0x9011 0x9012}
  {"truck_third" "stage_1" 0x9020 0x9021}
  {"single_rifleman(ger)" "" 0x9030}}
}
'''
STATUS = '{saveinfo {version 9} {name "Test campaign"} {army ger} {gameVersion "1.060.0"} {mods "mod_2905667604:0" "other_mod:3"} {mp 120}}\r\n'
PROFILE = {"recipient_module_names": ["turret"], "donor_module_names": ["turret"],
           "donor_name_map": {"turret": "mw_turret"}, "weaponry_policy": "donor_complete",
           "inventory_policy": "donor"}


def make_save(path, scene=SCENE, status=STATUS):
    info = zipfile.ZipInfo("point_2_1", (2024, 2, 3, 4, 5, 6))
    info.comment, info.extra, info.external_attr = b"retain-comment", b"", 0o600 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = b"fixture ZIP comment"
        archive.writestr("campaign.scn", scene.encode("utf-8") if isinstance(scene, str) else scene)
        archive.writestr("status", status.encode("utf-8") if isinstance(status, str) else status)
        archive.writestr(info, b"unrelated binary map data\x00\xff")
    return Path(path)


class CampaignTests(unittest.TestCase):
    def test_existing_addon_is_moved_last_without_reordering_other_mods(self):
        from workshop.campaign import _snapshot
        for position in ('"modular_workshop:0" "first:0" "second:1"',
                         '"first:0" "MODULAR_WORKSHOP:0" "second:1"',
                         '"first:0" "second:1" "modular_workshop:0"'):
            with self.subTest(position=position):
                source=make_save(self.path,SCENE,'{saveinfo {army ger}{gameVersion "1.060.0"}{mods '+position+'}}')
                snapshot=load_campaign(source)
                plan=plan_edit(snapshot,'0x9000','mw_order',2,state_profile=PROFILE,keep_names=True)
                replacements=apply_plan(snapshot,plan)
                after=_snapshot(build_save_copy(snapshot,replacements),source)
                self.assertEqual(after.mods,['first:0','second:1','modular_workshop:0'])
                self.assertEqual(len(after.units),len(snapshot.units))
                self.assertEqual(source.read_bytes(),snapshot._zip_bytes)

    def test_repeated_edits_keep_spent_shells_smoke_mount_inventory_and_veterans(self):
        for charges in (0,2):
            with self.subTest(charges=charges):
                contents=('{item "new_shell ap" 2}{item "mg_round belt" 23}{item "toolkit" 1}'
                          '{item "base_gun" filled {user "cmd_old"}}'+
                          (f'{{item "nbks grenade" {charges}}}' if charges else ''))
                source=make_save(Path(self.temp.name)/f'spent_{charges}.sav',SCENE.replace(
                    '{item "old_ammo" "ammo" 3 {cell 0 0}}',contents))
                original=source.read_bytes();snapshot=load_campaign(source);eid='0x9000';slot='cmd_old'
                for cycle in range(3):
                    next_slot=f'cmd_{cycle}'
                    profile={'allowed_damage_ids':['body','engine','turret'],
                        'gun_swap':{'place':'gun','old_ammo':'new_shell','ammo_items':['{item "new_shell ap" 99}'],'initialize':False},
                        'smoke_launchers':{'initial_item':'{item "nbks grenade" 3}','initialize':False},
                        'commander_mg':{'slot':next_slot,'previous_slot':slot,'item':'base_gun',
                            'initial_item':'{item "base_gun" filled}','ammo_item':'{item "mg_round belt" 999}','initialize':False}}
                    plan=plan_edit(snapshot,eid,f'mw_cycle_{cycle}',2,state_profile=profile,keep_names=True)
                    replacements=apply_plan(snapshot,plan);campaign=parse(replacements['campaign.scn']).find('campaign')
                    inventory=next(n for n in campaign.find_all('Inventory') if n.args[0]==plan['new_eid'])
                    items={n.args[0]:n for n in inventory.walk('item')}
                    self.assertEqual(items['new_shell ap'].args,['new_shell ap','2'])
                    self.assertEqual(items['mg_round belt'].args,['mg_round belt','23'])
                    self.assertEqual(items['base_gun'].find('user').args,[next_slot])
                    self.assertEqual('nbks grenade' in items,bool(charges))
                    if charges:self.assertEqual(items['nbks grenade'].args,['nbks grenade','2'])
                    self.assertEqual(len(items),4+bool(charges))
                    vehicle=next(n for n in campaign.find_all('Entity') if n.args[0]==f'mw_cycle_{cycle}')
                    for kept in ('{MID 20}','{Remain 76.4}','Bullets 7','Component "engine"'):
                        self.assertIn(kept,vehicle.render())
                    self.assertIn('0x9001 0x9002',campaign.find('CampaignSquads').render())
                    path=Path(self.temp.name)/f'cycle_{charges}_{cycle}.sav';path.write_bytes(build_save_copy(snapshot,replacements))
                    snapshot=load_campaign(path);eid=plan['new_eid'];slot=next_slot
                self.assertEqual(source.read_bytes(),original)

    def test_passenger_mgs_get_their_gun_once_and_a_removed_one_leaves_it_as_cargo(self):
        scene=SCENE.replace('{item "old_ammo" "ammo" 3 {cell 0 0}}',
                            '{item "mg42_belt" filled {user "mgun_rider1"}}{item "mg34_belt" filled {user "mgun_rider2"}}{item "toolkit" 1}')
        source=make_save(Path(self.temp.name)/'riders.sav',scene);snapshot=load_campaign(source)
        mounts=[{'slot':'mgun_rider1','item':'mg42_belt','initial_item':'{item "mg42_belt" filled {user "mgun_rider1"}}',
                 'ammo_item':'{item "ammo mgun_ger belt" 300}'},
                {'slot':'mgun_rider3','item':'dp_bipod','initial_item':'{item "dp_bipod" filled {user "mgun_rider3"}}',
                 'ammo_item':'{item "ammo mgun_rus belt" 141}'}]
        plan=plan_edit(snapshot,'0x9000','mw_riders',2,state_profile={'rider_mgs':{'mounts':mounts}},keep_names=True)
        campaign=parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign')
        inventory=next(n for n in campaign.find_all('Inventory') if n.args[0]==plan['new_eid'])
        users={(n.args[0],n.find('user').args[0] if n.find('user') else None) for n in inventory.walk('item')}
        # Rider 1 already holds its gun: not doubled, no extra ammo.
        self.assertEqual([n.args[0] for n in inventory.walk('item')].count('mg42_belt'),1)
        self.assertNotIn('ammo mgun_ger belt',inventory.render())
        # Rider 2 was removed: its MG stays as cargo, bound to nothing.
        self.assertIn(('mg34_belt',None),users)
        # Rider 3 is new: its gun and ammunition are added.
        self.assertIn(('dp_bipod','mgun_rider3'),users);self.assertIn('ammo mgun_rus belt',inventory.render())
        with self.assertRaises(CampaignError):
            plan_edit(snapshot,'0x9000','mw_bad',2,state_profile={'rider_mgs':{'mounts':[{'slot':'x'}]}},keep_names=True)

    def test_a_gun_bound_to_a_place_the_vehicle_lost_becomes_cargo(self):
        # Vidarr's Stuart turned searchlight carrier kept its roof MG on "mgun":
        # the game crashed spawning it. Opening the save releases it; a build too.
        scene=SCENE.replace('{item "old_ammo" "ammo" 3 {cell 0 0}}','{item "browning" filled {user "mgun"}}{item "dt" filled {user "mgun1"}}')
        source=make_save(Path(self.temp.name)/'orphan.sav',scene);snapshot=load_campaign(source)
        template=next(u['template'] for u in snapshot.units if u['eid']=='0x9000')
        replacements,released=release_orphan_guns(snapshot,{template:{'gun','mgun1'}})
        self.assertEqual(released,[('0x9000','mgun')])
        inventory=next(n for n in parse(replacements['campaign.scn']).find('campaign').find_all('Inventory') if n.args[0]=='0x9000')
        users={i.args[0]:[u.args[0] for u in i.find_all('user')] for i in inventory.walk('item')}
        self.assertEqual(users,{'browning':[],'dt':['mgun1']})
        self.assertEqual(release_orphan_guns(snapshot,{template:{'gun','mgun','mgun1'}}),(None,[]))
        plan=plan_edit(snapshot,'0x9000','mw_orphan',2,state_profile={'weapon_places':['gun','mgun1']},keep_names=True)
        inventory=next(n for n in parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign').find_all('Inventory') if n.args[0]==plan['new_eid'])
        self.assertEqual({i.args[0]:[u.args[0] for u in i.find_all('user')] for i in inventory.walk('item')},{'browning':[],'dt':['mgun1']})

    def test_deleting_a_vehicle_keeps_every_soldier_in_reserve_and_nothing_else_changes(self):
        from workshop.campaign import plan_removal
        plan=plan_removal(self.snapshot,'0x9000')
        self.assertEqual(plan['operation'],'remove_one')
        self.assertEqual(sorted(e for r in plan['reserve_squads'] for e in r['eids']),['0x9001','0x9002'])
        replacements=apply_plan(self.snapshot,plan)
        out=Path(self.temp.name)/'deleted.sav';out.write_bytes(build_save_copy(self.snapshot,replacements))
        after=load_campaign(out)
        self.assertNotIn('0x9000',{u['eid'] for u in after.units})
        self.assertEqual({u['eid'] for u in after.units},{u['eid'] for u in self.snapshot.units}-{'0x9000'})
        text=replacements['campaign.scn'].decode('utf-8')
        self.assertNotIn('0x9000',text)
        self.assertIn('"wheeled_donor" "stage_1" 0x9010 0x9011 0x9012',' '.join(text.split()))
        self.assertEqual(sorted(e for s in after.squads for e in s['human_eids'] if e in ('0x9001','0x9002')),['0x9001','0x9002'])
        with self.assertRaises(CampaignError):plan_removal(self.snapshot,'0x9001')  # a soldier is no vehicle

    def test_removing_mount_retires_its_binding_without_destroying_weapon_or_ammo(self):
        scene=SCENE.replace('{item "old_ammo" "ammo" 3 {cell 0 0}}',
                            '{item "base_gun" filled {user "cmd_old"}}{item "mg_round belt" 23}{item "toolkit" 1}')
        source=make_save(Path(self.temp.name)/'remove.sav',scene);snapshot=load_campaign(source)
        plan=plan_edit(snapshot,'0x9000','mw_retired',2,state_profile={'retired_inventory_users':['cmd_old']},keep_names=True)
        campaign=parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign')
        inventory=next(n for n in campaign.find_all('Inventory') if n.args[0]==plan['new_eid'])
        self.assertIn('base_gun',inventory.render());self.assertIn('mg_round belt',inventory.render())
        self.assertFalse(inventory.find_all('user'))

    def test_gun_replacement_keeps_cargo_and_mg_ammo_and_initializes_only_new_shells(self):
        scene=SCENE.replace('{item "old_ammo" "ammo" 3 {cell 0 0}}',
                            '{item "old_shell ap" 3 {cell 0 0}}{item "mg_round belt" 500}{item "toolkit" 1}')
        source=make_save(Path(self.temp.name)/'gun.sav',scene)
        original_bytes=source.read_bytes()
        snap=load_campaign(source)
        profile=dict(PROFILE,inventory_policy='gun_replace',weaponry_policy='reset',
                     gun_swap={'place':'gun','old_ammo':'old_shell','ammo_items':['{item "new_shell ap" 10}'],'initialize':True})
        plan=plan_assembly(snap,'0x9000','0x9010','mw_gun',2,state_profile=profile)
        raw=apply_plan(snap,plan)['campaign.scn'];root=parse(raw).find('campaign')
        inv=next(n for n in root.find_all('Inventory') if n.args[0]==plan['new_eid'])
        text=inv.render();self.assertIn('mg_round belt',text);self.assertIn('toolkit',text)
        self.assertIn('new_shell ap',text);self.assertNotIn('old_shell ap',text)
        self.assertEqual(source.read_bytes(),original_bytes)

    def test_gun_recipe_edit_does_not_replenish_ammo(self):
        profile=dict(PROFILE,inventory_policy='gun_replace',weaponry_policy='reset',
                     gun_swap={'place':'gun','old_ammo':'old_ammo','ammo_items':['{item "new_shell ap" 10}'],'initialize':False})
        plan=plan_edit(self.snapshot,'0x9000','mw_gun',2,state_profile=profile,keep_names=True)
        raw=apply_plan(self.snapshot,plan)['campaign.scn']
        self.assertNotIn(b'new_shell',raw);self.assertIn(b'old_ammo',raw)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = make_save(Path(self.temp.name) / "source.sav")
        self.snapshot = load_campaign(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, capacity=2, **kwargs):
        return plan_assembly(self.snapshot, "0x9000", "0x9010", "mw_hybrid", capacity,
                             state_profile=PROFILE, **kwargs)

    def test_lists_every_vehicle_and_human_and_mods(self):
        result = self.snapshot.to_dict()
        self.assertEqual(len(result["units"]), 9)
        self.assertEqual(sum(u["kind"] == "human" for u in result["units"]), 6)
        self.assertEqual(result["mods"], ["mod_2905667604:0", "other_mod:3"])
        self.assertEqual(result["units"][0]["crew_eids"], ["0x9001", "0x9002"])
        self.assertEqual(result["units"][1]["veterancy"], 8)

    def test_consumes_two_vehicles_preserves_veterans_and_third_parties(self):
        original_bytes = self.path.read_bytes()
        plan = self.plan()
        self.assertEqual(plan["crew_eids"], ["0x9001", "0x9002"])
        self.assertEqual(plan["reserve_squads"][0]["eids"], ["0x9011", "0x9012"])
        replacements = apply_plan(self.snapshot, plan)
        copied = Path(self.temp.name) / "output.sav"
        copied.write_bytes(build_save_copy(self.snapshot, replacements))
        after = load_campaign(copied)
        ids = {u["eid"] for u in after.units}
        self.assertEqual(ids, ({u["eid"] for u in self.snapshot.units} - {"0x9000", "0x9010"}) | {plan["new_eid"]})
        self.assertEqual(self.path.read_bytes(), original_bytes)
        before_scene = parse(SCENE).find("campaign")
        after_scene = parse(replacements["campaign.scn"]).find("campaign")
        for before in before_scene.find_all("Human"):
            actual = next(n for n in after_scene.find_all("Human") if n.args[1] == before.args[1])
            self.assertEqual(actual.render(), before.render())
        before_third = next(n for n in before_scene.find_all("Entity") if n.args[1] == "0x9020")
        after_third = next(n for n in after_scene.find_all("Entity") if n.args[1] == "0x9020")
        self.assertEqual(before_third.render(), after_third.render())
        self.assertEqual(after.mods, ["mod_2905667604:0", "other_mod:3", "modular_workshop:0"])
        # The opened save itself is updated now: its name stays the player's.
        self.assertEqual(after.campaign_name, "Test campaign")
        self.assertEqual([n.render() for n in after_scene.find_all("Link")], ['{Link 0x9021 {0x9020 "driver"}}'])

    def test_hull_damage_fuel_and_module_damage_are_not_reset(self):
        scene = parse(apply_plan(self.snapshot, self.plan())["campaign.scn"]).find("campaign")
        hybrid = next(n for n in scene.find_all("Entity") if n.args[0] == "mw_hybrid")
        text = hybrid.render()
        self.assertIn('{damage "body" 0.25 1}', text)
        self.assertIn('{wound "mw_turret" 0.7 1 7.62}', text)
        self.assertNotIn('{wound "turret" 0.5 1 20}', text)
        self.assertNotIn('{damage "wheel"', text)
        self.assertIn('{Component "engine" {HealthBroken 42}}', text)
        self.assertIn('{Component "mw_turret" {HealthBroken 17}}', text)
        self.assertIn('{Remain 76.4}', text)
        self.assertIn('{Bullets 19}', text)
        self.assertNotIn('{Bullets 7}', text)

    def test_selected_inventory_is_conserved_without_duplication(self):
        plan = self.plan()
        scene = parse(apply_plan(self.snapshot, plan)["campaign.scn"]).find("campaign")
        invs = scene.find_all("Inventory")
        self.assertEqual(len(invs), 2)
        transferred = next(n for n in invs if n.args == [plan["new_eid"]])
        self.assertIn('{item "20mm" "ammo" 9 {cell 0 0}}', transferred.render())
        self.assertEqual(len(plan["archived_vehicle_inventories"]), 2)

    def test_mixed_loadout_resets_weapon_state_and_ammo_but_keeps_damage(self):
        # Chassis keeps a hull weapon while the module brings its own: slot state
        # cannot be mapped, so both are reinitialized from the definition.
        profile = dict(PROFILE, weaponry_policy="reset", inventory_policy="reset")
        plan = plan_assembly(self.snapshot, "0x9000", "0x9010", "mw_hybrid", 2, state_profile=profile)
        scene = parse(apply_plan(self.snapshot, plan)["campaign.scn"]).find("campaign")
        hybrid = next(n for n in scene.find_all("Entity") if n.args[0] == "mw_hybrid")
        self.assertEqual(hybrid.find_all("Weaponry"), [])
        self.assertIn('{wound "mw_turret" 0.7 1 7.62}', hybrid.render())
        self.assertEqual([n.args for n in scene.find_all("Inventory")], [["0x9001"]])

    def test_unit_tags_follow_the_chassis_and_leave_with_the_donor(self):
        scene = SCENE.replace(" {CampaignSquads",
                              ' {Tags "_user" "player_armor" "_noted" 0x9000}\n {Tags "_user" "player_armor" 0x9010}\n'
                              ' {Tags "_user" "player" 0x9001}\n {CampaignSquads')
        snapshot = load_campaign(make_save(Path(self.temp.name) / "tagged.sav", scene))
        plan = plan_assembly(snapshot, "0x9000", "0x9010", "mw_hybrid", 2, state_profile=PROFILE)
        after = parse(apply_plan(snapshot, plan)["campaign.scn"]).find("campaign")
        tags = [node.render() for node in after.find_all("Tags")]
        self.assertIn('{Tags "_user" "player_armor" "_noted" ' + plan["new_eid"] + "}", tags)
        self.assertIn('{Tags "_user" "player" 0x9001}', tags)
        self.assertEqual(len(tags), 2)

    def test_a_conquest_saved_before_buying_anything_says_so(self):
        # Field report 20/10: a brand-new conquest's campaign.scn is just {campaign}.
        save = make_save(Path(self.temp.name) / "new_conquest.sav", "{campaign\r\n}\r\n")
        with self.assertRaisesRegex(CampaignError, "^This Conquest has no units yet$"):
            load_campaign(save)

    def test_ammo_source_that_never_fought_restocks_from_the_definition(self):
        # Field report 20/10 ("Chosen ammunition source has no inventory"): a
        # module vehicle that never fought has no saved inventory; it carries
        # its definition's load, and so does the vehicle built from it.
        for policy, missing in (("donor", "0x9010"), ("recipient", "0x9000")):
            with self.subTest(policy=policy):
                scene = re.sub(r" \{Inventory " + missing + r" \{box \{clear\} \{item [^\n]*\n", "", SCENE)
                self.assertNotIn("{Inventory " + missing, scene)
                snapshot = load_campaign(make_save(Path(self.temp.name) / f"fresh_{policy}.sav", scene))
                profile = dict(PROFILE, inventory_policy=policy, compatible_recipient_inventory=True)
                plan = plan_assembly(snapshot, "0x9000", "0x9010", "mw_hybrid", 2, state_profile=profile)
                after = parse(apply_plan(snapshot, plan)["campaign.scn"]).find("campaign")
                inventories = {node.args[0] for node in after.find_all("Inventory")}
                self.assertNotIn(plan["new_eid"], inventories)
                self.assertNotIn("0x9000", inventories)
                self.assertNotIn("0x9010", inventories)
                self.assertIn("0x9001", inventories)  # a soldier's inventory is untouched

    def test_lost_detachable_parts_follow_the_chassis_and_leave_with_the_donor(self):
        # Battle-worn saves list parts a vehicle has lost: {Slicer 0x82f4 {"shieldl5"}}.
        # Reported by players ("Unknown Slicer reference to a consumed vehicle").
        scene = SCENE.replace(" {CampaignSquads",
                              ' {Slicer 0x9000\n  {"shieldl1"}\n  {"turret"}\n }\n {Slicer 0x9010\n  {"shieldr2"}\n }\n'
                              ' {Slicer 0x9020\n  {"shieldl3"}\n }\n {CampaignSquads')
        snapshot = load_campaign(make_save(Path(self.temp.name) / "worn.sav", scene))
        plan = plan_assembly(snapshot, "0x9000", "0x9010", "mw_hybrid", 2, state_profile=PROFILE)
        after = parse(apply_plan(snapshot, plan)["campaign.scn"]).find("campaign")
        slicers = {node.args[0]: [part.head for part in node.children] for node in after.find_all("Slicer")}
        self.assertEqual(slicers[plan["new_eid"]], ["shieldl1"])  # the replaced turret's entry is gone
        self.assertEqual(slicers["0x9020"], ["shieldl3"])  # third vehicle untouched
        self.assertNotIn("0x9000", slicers)
        self.assertNotIn("0x9010", slicers)
        # A fresh refit (depot part) comes out repaired.
        refit = plan_refit(snapshot, "0x9000", "mw_refit", 2, role="hull")
        after = parse(apply_plan(snapshot, refit)["campaign.scn"]).find("campaign")
        self.assertEqual([node.args[0] for node in after.find_all("Slicer")], ["0x9010", "0x9020"])
        bad = scene.replace('{Slicer 0x9010', '{Slicer 0x9010 0x9020')
        with self.assertRaisesRegex(CampaignError, "Slicer block"):
            plan_assembly(load_campaign(make_save(Path(self.temp.name) / "bad.sav", bad)), "0x9000", "0x9010",
                          "mw_hybrid", 2, state_profile=PROFILE)

    def test_soldiers_garrisoned_in_map_objects_do_not_block_a_build(self):
        # {Link human {object "p05" "4"}}: a soldier in a trench or bunker point.
        scene = SCENE.replace(" {CampaignSquads", ' {Link 0x9030 {0x7777 "p05" "4"}}\n {CampaignSquads')
        snapshot = load_campaign(make_save(Path(self.temp.name) / "garrison.sav", scene))
        plan = plan_assembly(snapshot, "0x9000", "0x9010", "mw_hybrid", 2, state_profile=PROFILE)
        after = parse(apply_plan(snapshot, plan)["campaign.scn"]).find("campaign")
        self.assertIn('{Link 0x9030 {0x7777 "p05" "4"}}', [node.render() for node in after.find_all("Link")])

    def test_refit_with_a_depot_part_replaces_one_vehicle_fresh_and_keeps_crew(self):
        # One owned vehicle + a turret (or chassis) from the workshop depot.
        plan = plan_refit(self.snapshot, "0x9000", "mw_refit", 2, role="hull")
        self.assertEqual(plan["consumed_eids"], ["0x9000"])
        scene = parse(apply_plan(self.snapshot, plan)["campaign.scn"]).find("campaign")
        new = next(n for n in scene.find_all("Entity") if n.args[0] == "mw_refit")
        text = new.render()
        self.assertIn("{MID 20}", text)
        for state in ("Armor", "Component", "Weaponry", "Chassis"):
            self.assertNotIn("{" + state, text)
        self.assertTrue(any(n.args[1] == "0x9010" for n in scene.find_all("Entity")))  # other vehicle untouched
        self.assertEqual([n.args for n in scene.find_all("Inventory") if n.args[0] in ("0x9000", plan["new_eid"])], [])
        squads = parse(apply_plan(self.snapshot, plan)["campaign.scn"]).find("campaign").find("CampaignSquads").render()
        self.assertIn('{"mw_refit" "" ' + plan["new_eid"] + " 0x9001 0x9002}", squads)
        self.assertIn('{"wheeled_donor" "stage_1" 0x9010 0x9011 0x9012}', squads)

    def test_edit_replaces_only_that_vehicle_and_keeps_its_state(self):
        # Editar: same parts, new placement. Crew, MID, fuel, inventory and the
        # damage of parts that still exist stay; nothing else is consumed.
        profile = {"allowed_damage_ids": ["body", "engine"]}
        plan = plan_edit(self.snapshot, "0x9000", "mw_edit", 2, state_profile=profile, keep_names=True)
        self.assertEqual(plan["operation"], "edit_one")
        self.assertEqual(plan["consumed_eids"], ["0x9000"])
        scene = parse(apply_plan(self.snapshot, plan)["campaign.scn"]).find("campaign")
        new = next(n for n in scene.find_all("Entity") if n.args[0] == "mw_edit")
        text = new.render()
        for kept in ("{MID 20}", "{Remain 76.4}", 'damage "body"', 'Component "engine"', "Bullets 7"):
            self.assertIn(kept, text)
        self.assertNotIn('Component "turret"', text)  # part no longer emitted
        self.assertNotIn('wound "turret"', text)
        inventory = [n for n in scene.find_all("Inventory") if n.args[0] == plan["new_eid"]]
        self.assertEqual(len(inventory), 1)
        self.assertIn("old_ammo", inventory[0].render())
        squads = scene.find("CampaignSquads").render()
        self.assertIn('{"mw_edit" "" ' + plan["new_eid"] + " 0x9001 0x9002}", squads)
        self.assertIn('{"wheeled_donor" "stage_1" 0x9010 0x9011 0x9012}', squads)

    def test_unknown_state_and_references_fail_closed(self):
        with self.assertRaisesRegex(CampaignError, "ownership"):
            plan_assembly(self.snapshot, "0x9000", "0x9010", "mw_hybrid")
        make_save(self.path, SCENE.replace('{CampaignSquads', '{ExternalThing 0x9010}\n {CampaignSquads'))
        with self.assertRaisesRegex(CampaignError, "Unknown ExternalThing"):
            plan_assembly(load_campaign(self.path), "0x9000", "0x9010", "mw_hybrid", state_profile=PROFILE)

    def test_emitted_damage_contract_rejects_missing_component_instead_of_repairing_it(self):
        profile = dict(PROFILE, allowed_damage_ids=["body", "mw_turret"])
        plan = plan_assembly(self.snapshot, "0x9000", "0x9010", "mw_hybrid", state_profile=profile)
        with self.assertRaisesRegex(CampaignError, "Persisted component.*absent"):
            apply_plan(self.snapshot, plan)

    def test_plan_tampering_does_not_apply(self):
        plan = self.plan()
        plan["crew_eids"].append("0x9030")
        with self.assertRaisesRegex(CampaignError, "differs"):
            apply_plan(self.snapshot, plan)

    def test_public_snapshot_does_not_mutate_authoritative_crew(self):
        result = self.snapshot.to_dict()
        result["units"][0]["crew_eids"].clear()
        result["squads"][0]["eids"].clear()
        self.assertEqual(self.snapshot.units[0]["crew_eids"], ["0x9001", "0x9002"])
        self.assertTrue(self.snapshot.squads[0]["eids"])

    def test_no_infantry_donors_duplicate_instances_or_invalid_capacity(self):
        for reid, deid, capacity in [("0x9000", "0x9000", 2), ("0x9000", "0x9001", 2),
                                    ("0x9000", "0x9010", -1), ("0x9000", "0x9010", True)]:
            with self.subTest(reid=reid, deid=deid, capacity=capacity), self.assertRaises(CampaignError):
                plan_assembly(self.snapshot, reid, deid, "mw_hybrid", capacity, state_profile=PROFILE)

    def test_capacity_fills_from_donor_preserves_spares_and_empty_slots(self):
        filled = self.plan(3)
        self.assertEqual(filled["crew_eids"], ["0x9001", "0x9002", "0x9011"])
        self.assertEqual(filled["reserve_squads"][0]["eids"], ["0x9012"])
        roomy = self.plan(6)
        self.assertEqual(roomy["vacant_crew_slots"], 2)
        self.assertEqual(roomy["reserve_squads"], [])
        self.assertIn(b'0xffffffff 0xffffffff', apply_plan(self.snapshot, roomy)["campaign.scn"])
        no_crew = self.plan(0)
        self.assertEqual(sum(len(r["eids"]) for r in no_crew["reserve_squads"]), 4)
        apply_plan(self.snapshot, no_crew)

    def test_unrelated_zip_members_and_metadata_survive(self):
        data = build_save_copy(self.snapshot, apply_plan(self.snapshot, self.plan()))
        with zipfile.ZipFile(self.path) as original, zipfile.ZipFile(io.BytesIO(data)) as output:
            self.assertEqual(original.namelist(), output.namelist())
            self.assertEqual(original.comment, output.comment)
            self.assertEqual(original.read("point_2_1"), output.read("point_2_1"))
            first, second = original.getinfo("point_2_1"), output.getinfo("point_2_1")
            for name in ("date_time", "comment", "external_attr", "compress_type", "extra"):
                self.assertEqual(getattr(first, name), getattr(second, name))

    def test_duplicate_zip_members_and_malformed_scenes_fail(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.path, "a") as archive:
                archive.writestr("status", STATUS)
        with self.assertRaisesRegex(CampaignError, "Duplicate ZIP"):
            load_campaign(self.path)
        make_save(self.path, SCENE[:-3])
        with self.assertRaises(CampaignError):
            load_campaign(self.path)

    def test_bom_and_crlf_round_trip(self):
        scene = SCENE.replace("\n", "\r\n").encode("utf-8-sig")
        make_save(self.path, scene)
        self.snapshot = load_campaign(self.path)
        output = apply_plan(self.snapshot, self.plan())["campaign.scn"]
        self.assertTrue(output.startswith(b"\xef\xbb\xbf"))
        # Unchanged Human text preserves CRLF exactly, although appended blocks
        # use regular newlines accepted by the block grammar.
        self.assertIn(b'{MID 21} {Extender "vitality" {Veterancy 8}} {Score 71}}\r\n', output)


@unittest.skipUnless(os.environ.get("WORKSHOP_REAL_SAVE"), "Set WORKSHOP_REAL_SAVE for a read-only real-save copy test")
class RealCampaignCopyTests(unittest.TestCase):
    def test_actual_campaign_consumption_and_round_trip_on_workspace_copy(self):
        original = Path(os.environ["WORKSHOP_REAL_SAVE"])
        before_bytes = original.read_bytes()
        workspace = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="real-save-test-", dir=workspace) as folder:
            copied = Path(folder) / "input.sav"
            shutil.copyfile(original, copied)
            snapshot = load_campaign(copied)
            vehicles = [u for u in snapshot.units if u["kind"] == "vehicle" and u["owned"]
                        and not set(u["persisted_state"]) & {"Armor", "Component", "Weaponry"}]
            self.assertGreaterEqual(len(vehicles), 2, "Real fixture needs two vehicles with no persisted module damage")
            plan = plan_assembly(snapshot, vehicles[0]["eid"], vehicles[1]["eid"], "mw_real_copy_fixture")
            output = Path(folder) / "output.sav"
            output.write_bytes(build_save_copy(snapshot, apply_plan(snapshot, plan)))
            after = load_campaign(output)
            self.assertEqual(len(after.units), len(snapshot.units) - 1)
            self.assertEqual(sum(u["kind"] == "human" for u in after.units), sum(u["kind"] == "human" for u in snapshot.units))
            with zipfile.ZipFile(copied) as first, zipfile.ZipFile(output) as second:
                for name in first.namelist():
                    if name not in ("campaign.scn", "status"):
                        self.assertEqual(first.read(name), second.read(name))
        self.assertEqual(original.read_bytes(), before_bytes)


class InventorySyntaxTests(unittest.TestCase):
    """Field report 20/10: a designed vehicle (turret step, "reset" inventory)
    got {Inventory "0x8006" ...} with DEF comments and the game closed loading
    the campaign: "Integer expected" (eSDLReader)."""

    DEF_BOX = ('{box\r\n\t\t\t{item "bulleteng_40 ap" 149}\t\t\t\t;150 rounds\r\n'
               '\t\t\t{item "bulleteng_76_l25 he" 28}\t\t\t\t;58 rounds\r\n'
               '\t\t\t{item "ammo mgun_ger belt mk2" 4725}\t\t;22 belts (225 rounds per belt)\r\n\t\t}')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_reset_inventory_is_written_like_the_game(self):
        from workshop.campaign import _draft_inventory, _inventory_problems
        from workshop.syntax import block
        steps = [{'kind': 'turret', 'profile': {'inventory_policy': 'reset'}, 'inventory_boxes': [self.DEF_BOX],
                  'donor_eid': None}]
        inventory = _draft_inventory({}, '0x8006', steps,
                                     {'gun_swap': {'old_ammo_families': ['bulleteng_40', 'mgun_ger belt']}})
        text = inventory.render()
        self.assertTrue(text.startswith('{Inventory 0x8006 '), text)
        self.assertNotIn(';', text)
        self.assertNotIn('bulleteng_40', text)
        self.assertIn('{item "bulleteng_76_l25 he" 28}', text)
        self.assertEqual(_inventory_problems(block('{campaign ' + text + '}')), [])

    def test_open_repairs_a_save_left_unreadable(self):
        from workshop.campaign import _inventory_problems, _one, repair_inventory_syntax
        broken = SCENE.replace('{Inventory 0x9010 {box {clear} {item "20mm" "ammo" 9 {cell 0 0}}}}',
                               '{Inventory "0x9010"\t' + self.DEF_BOX + '}')
        snapshot = load_campaign(make_save(Path(self.temp.name) / 'broken.sav', broken))
        replacements, repaired = repair_inventory_syntax(snapshot)
        self.assertEqual(repaired, ['0x9010'])
        scene = replacements['campaign.scn'].decode('utf-8')
        self.assertIn('{Inventory 0x9010\t{box', scene)
        self.assertIn('{item "bulleteng_40 ap" 149}', scene)  # contents stay as they are
        self.assertNotIn('rounds', scene)
        # Outside that inventory nothing changes, not even the scene's own comment.
        start, end = broken.index('{Inventory "0x9010"'), broken.index('{Inventory 0x9001')
        self.assertEqual(scene[:start], broken[:start])
        self.assertEqual(scene[scene.index('{Inventory 0x9001'):], broken[end:])
        repaired_save = Path(self.temp.name) / 'repaired.sav'
        repaired_save.write_bytes(build_save_copy(snapshot, replacements))
        after = load_campaign(repaired_save)
        self.assertEqual(_inventory_problems(_one(after._scene, 'campaign', required=True)), [])
        self.assertEqual(repair_inventory_syntax(after), (None, []))

    def test_game_written_saves_need_no_repair(self):
        from workshop.campaign import repair_inventory_syntax
        snapshot = load_campaign(make_save(Path(self.temp.name) / 'fine.sav'))
        self.assertEqual(repair_inventory_syntax(snapshot), (None, []))


if __name__ == "__main__":
    unittest.main()
