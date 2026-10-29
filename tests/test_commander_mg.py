"""Independent stock-resource fixtures: no installed game or user saves needed."""
import copy
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from workshop.resources import ResourceIndex
from workshop.syntax import parse, block
from workshop.compiler import compile_assembly, CompileError
from workshop.geometry import parse_model, multiply, euler_matrix, point
from workshop.commander_mg import weapon_catalog, support_catalog, configuration, add_interactions, operator_weapon_problem, operator_loader_warning
from tests.test_geometry import eply
from tests.test_campaign import make_save, SCENE
from workshop.campaign import load_campaign, plan_edit, plan_assembly, apply_plan


def archive(game, name, files):
    path = game / 'resource' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as z:
        for key, value in files.items():
            z.writestr(key, value)


def fixture(game):
    hull = ('{game_entity {extension "hull.mdl"} {mass 1000}'
            '{placer {place "driver" {group "crew"}} {place "gunner" {group "crew"}}'
            '{place "commander" {group "crew"} {LinkBone "seat"}}}'
            '{boarder {door "emit"} {link "emit" "commander" {anm "entry"}}'
            '{anm "entry" {forward {end "old_pose"}} {reverse {end "exit"}}}}'
            '{Weaponry {place "gun" {weapon "cannon" filling "shell ap" 1} {gunner "gunner"}}'
            '{place "vision" {type "commander"} {weapon "commander_vision"} {gunner "commander"}}}'
            '{extender "inventory" {box {item "shell ap" 10}}}}')
    model = ('{skeleton {bone "basis" {bone "body" {VolumeView "body.ply"} {bone "driver"}'
             '{bone "emit"} {bone revolute "turret" {Position 0 0 10} {VolumeView "body.ply"}'
             '{bone "gun"} {bone "gunner"} {bone "seat"}}}}}')
    support = ('{game_entity {extension "support.mdl"}'
               '{placer {place "commander" {group "crew"} {LinkBone "seat_open"}}}'
               '{boarder {link "door" "commander" {anm "climb"}}'
               '{anm "climb" {forward {end "native_pose"}}}}'
               '{extender "inventory" {weapon "aa" {mask "mountable"}} {box}}'
               '{Weaponry {place "aa" {type "aa"} {gunner "commander"} {charger "commander"}'
               '{LinkBone "socket"} {LinkAnimation "off"} {basic} {ai_in_manual}}}}')
    support_model = ('{skeleton {animation {sequence "deploy" {file "deploy.anm"}}}'
                     '{bone "basis" {bone revolute "yaw" {limits -90 90} {VolumeView "body.ply"}'
                     '{bone revolute "pitch" {limits -20 45} {bone "socket" {Position 2 0 1}}}'
                     '{bone "seat_open" {Position -2 0 -5}}}}}')
    clip = (b'EANM' + struct.pack('<HH', 1, 6) + b'FRMS' + struct.pack('<I', 1)
            + b'BMAP' + struct.pack('<I', 1) + struct.pack('<I', 5) + b'pitch'
            + b'FRM2' + struct.pack('<HB', 0, 1) + struct.pack('<BH3f', 0, 2, 0, 0, 0))
    archive(game, 'gamelogic.pak', {
        'set/registry/dlc.reg': '{dlc {paid 123} {locked_entity 123}}',
        'set/stuff/bullet.pattern': '{from {tag "weapon bullet"}}',
        'set/stuff/gun.pattern': '{from "pattern bullet" {tag "mountable"} {filling "ammo rifle clip" 5}}',
        'set/stuff/base_gun': '{from "pattern gun" {entity "base_gun"}}',
        'set/stuff/paid': '{from "pattern gun" {entity "base_gun"}}',
        'set/stuff/alias_paid': '{from "pattern gun" {entity "locked_entity"}}',
        'set/stuff/not_mountable': '{from "pattern gun" {tag "-mountable"}}',
        'set/stuff/rifle.clip.ammo': '{bullet {entity "ammo_box"}}',
    })
    archive(game, 'entity/fixture.pak', {
        '-vehicle/test/hull/hull.def': hull, '-vehicle/test/hull/hull.mdl': model,
        '-vehicle/test/hull/body.ply': eply(),
        '-vehicle/test/support/support.def': support, '-vehicle/test/support/support.mdl': support_model,
        '-vehicle/test/support/body.ply': eply(), '-vehicle/test/support/deploy.anm': clip,
        'inventory/base_gun/base_gun.def': '{actor {extension "base_gun.mdl"}}',
        'inventory/base_gun/base_gun.mdl': '{skeleton {bone "basis" {VolumeView "gun.ply"}}}',
        'inventory/base_gun/gun.ply': eply(),
    })


class CommanderMountTests(unittest.TestCase):
    def test_hidden_commander_keeps_exclusive_mount_operators_and_boarding(self):
        original_read=self.index.read
        path='entity/-vehicle/test/hull/hull.def'
        raw=original_read(path).replace(b'{anm "entry"}}',b'{anm "entry"}{forward putoff}{reverse puton}}')
        self.index.read=lambda p: raw if p==path else original_read(p)
        outputs=[]
        for name,hidden in [('visible',[]),('hidden',['commander'])]:
            result=compile_assembly(self.index,'hull','hull',{
                'commander_only':True,'commander_mg':self.spec(),'crew_hidden':hidden},self.root/name,'mw_visibility')
            folder=self.root/name/'resource/entity/-vehicle/workshop/mw_visibility'
            root=parse((folder/'mw_visibility.def').read_bytes()).find('game_entity')
            outputs.append((result,root))
        before,after=outputs
        self.assertEqual(before[0]['metadata']['crew_slots'],after[0]['metadata']['crew_slots'])
        for head in ('Weaponry','boarder','extender'):
            self.assertEqual([n.render() for n in before[1].find_all(head)], [n.render() for n in after[1].find_all(head)])
        commander=next(p for pl in after[1].find_all('placer') for p in pl.find_all('place') if p.args==['commander'])
        self.assertEqual(commander.find('LinkBone').args,[''])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = self.root / 'game'
        fixture(self.game)
        self.index = ResourceIndex(self.game).build()

    def tearDown(self):
        self.index.close()
        self.temp.cleanup()

    def spec(self):
        return {'support': 'support:aa', 'parent': 'turret', 'operator': 'commander',
                'item': 'base_gun', 'position': [2, 3, 4], 'rotation': [0, 0, 30],
                'yaw': [-100, 80], 'pitch': [-10, 60]}

    def test_catalog_inherits_tags_and_excludes_dlc_aliases_and_negative_tags(self):
        self.assertEqual([w['id'] for w in weapon_catalog(self.index)], ['base_gun'])
        self.assertEqual(weapon_catalog(self.index)[0]['ammo_item'], 'ammo rifle clip')
        self.assertEqual(weapon_catalog(self.index)[0]['ammo_rounds'], 15)
        self.assertEqual([s['id'] for s in support_catalog(self.index)], ['support:aa'])

    def test_selected_support_validates_only_its_source_and_matches_full_catalog(self):
        base=self.index.base_game();original=base.read
        def read(path,*args,**kwargs):
            if path.startswith('entity/-vehicle/') and path.endswith('.def') and 'support' not in path:
                raise AssertionError('Unrelated vehicle read while validating selected mount: '+path)
            return original(path,*args,**kwargs)
        from unittest.mock import patch
        with patch.object(base,'read',side_effect=read):direct=support_catalog(self.index,template='support')
        self.assertEqual(direct,support_catalog(self.index))

    def test_selected_weapon_has_same_ammo_and_dependency_guards_as_full_catalog(self):
        direct=weapon_catalog(self.index,item='base_gun')
        self.assertEqual(direct,weapon_catalog(self.index))
        self.assertEqual(weapon_catalog(self.index,item='paid'),[])

    def test_base_view_ignores_dlc_and_catalog_offers_the_mod_version(self):
        self.index.close()
        archive(self.game, 'dlc3.pak', {'set/stuff/base_gun': '{from "pattern gun" {entity "base_gun"} {tag "-mountable"}}'})
        self.index = ResourceIndex(self.game).build()
        self.assertIn('mountable', self.index.base_game().read_text('set/stuff/gun.pattern'))
        self.assertEqual(weapon_catalog(self.index), [])  # Effective item differs from base.
        self.index.close()
        (self.game/'resource/dlc3.pak').unlink()
        mod = self.root/'mod'
        archive(mod, 'mod.pak', {'set/stuff/base_gun': '{from "pattern gun" {entity "base_gun"} {range 999}}'})
        self.index = ResourceIndex(self.game,[mod]).build()
        # The active mod's version is the one the game uses.
        self.assertEqual([w['id'] for w in weapon_catalog(self.index)], ['base_gun'])

    def test_rejects_other_operator_nonfinite_and_invalid_limits(self):
        for change in ({'operator':'driver'}, {'position':[float('nan'),0,0]},
                       {'yaw':[30,-30]}, {'pitch':[-91,30]}, {'item':'paid'}):
            with self.subTest(change=change), self.assertRaises(CompileError):
                configuration(self.index, dict(self.spec(),**change))

    def test_compiler_has_one_operator_keeps_capacity_and_uses_parent_local_transform(self):
        result = compile_assembly(self.index,'hull','hull',{'commander_mg':self.spec()},self.root/'out','mw_fixture')
        metadata = result['metadata']
        contract = metadata['commander_mg']
        self.assertEqual(metadata['crew_roles'],['commander','driver','gunner'])
        self.assertEqual(contract['crew_slots_added'],0)
        self.assertFalse(result['runtime_verified'])
        folder=self.root/'out/resource/entity/-vehicle/workshop/mw_fixture'
        root=parse((folder/'mw_fixture.def').read_bytes()).find('game_entity')
        places=root.find('Weaponry').find_all('place')
        aa=next(p for p in places if p.args==[contract['slot']])
        self.assertIsNone(aa.find('weapon'))
        self.assertEqual(aa.find('gunner').args,['commander'])
        self.assertEqual(aa.find('charger').args,['commander'])
        self.assertFalse(any(p.find('type') and p.find('type').args==['commander'] for p in places))
        self.assertIn('native_pose',root.render())
        model=parse_model((folder/'mw_fixture.mdl').read_bytes())
        yaw=model['bones'][contract['bones']['yaw']]
        self.assertEqual(yaw['parent'],'turret')
        for actual,expected in zip(yaw['local'],euler_matrix([0,0,30],[2,3,4])):
            for a,e in zip(actual,expected):self.assertAlmostEqual(a,e,places=6)
        self.assertEqual(yaw['node'].find('limits').args,['-100','80'])
        self.assertTrue(metadata['validation']['dependencies']['passed'])

    def test_missing_commander_or_busy_commander_rejected_before_writing(self):
        original_read = self.index.read
        original_raw = original_read('entity/-vehicle/test/hull/hull.def')
        for role in ('absent','busy'):
            hullpath='entity/-vehicle/test/hull/hull.def'
            raw=original_raw
            if role=='absent':raw=raw.replace(b'"commander"',b'"charger"')
            else:raw=raw.replace(b'{gunner "gunner"}',b'{gunner "commander"}')
            self.index.read=lambda p,raw=raw: raw if p==hullpath else original_read(p)
            out=self.root/role
            with self.assertRaises(CompileError):
                compile_assembly(self.index,'hull','hull',{'commander_mg':self.spec()},out,'mw_fixture')
            self.assertFalse(out.exists())
        self.index.read=original_read

    def test_commander_preflight_distinguishes_vision_from_weapon_duties(self):
        root=block('{game_entity {Weaponry {place "vision" {type "commander"}'
                   '{weapon "commander_vision"} {gunner "commander"}}}}')
        self.assertEqual(operator_weapon_problem(root),'')
        for duty in ('gunner','charger'):
            armed=block('{game_entity {Weaponry {place "coax" {weapon "dt"}'
                        '{'+duty+' "commander"}}}}')
            if duty=='gunner':self.assertIn('apunta otra arma',operator_weapon_problem(armed))
            else:
                self.assertEqual(operator_weapon_problem(armed),'')
                self.assertIn('recarga',operator_loader_warning(armed))

    def test_mount_delegates_shared_loader_without_adding_or_duplicating_crew(self):
        original_read=self.index.read
        path='entity/-vehicle/test/hull/hull.def'
        raw=original_read(path).replace(b'{gunner "gunner"}',b'{gunner "gunner"} {charger "commander"}')
        self.index.read=lambda p:raw if p==path else original_read(p)
        for whole_vehicle in (False,True):
            with self.subTest(whole_vehicle=whole_vehicle):
                output=self.root/('loader_edit' if whole_vehicle else 'loader_combine')
                result=compile_assembly(self.index,'hull','hull',{'commander_only':whole_vehicle,'commander_mg':self.spec()},output,'mw_loader')
                root=parse((output/'resource/entity/-vehicle/workshop/mw_loader/mw_loader.def').read_bytes()).find('game_entity')
                places=[p for w in root.find_all('Weaponry') for p in w.find_all('place')]
                gun=next(p for p in places if p.find('weapon') and p.find('weapon').args[0]=='cannon')
                self.assertEqual(gun.find('gunner').args,['gunner'])
                self.assertEqual(gun.find('charger').args,['gunner'])
                aa=next(p for p in places if p.find('type') and p.find('type').args==['aa'])
                self.assertEqual(aa.find('gunner').args,['commander'])
                self.assertEqual(aa.find('charger').args,['commander'])
                seats=[p for pl in root.find_all('placer') for p in pl.find_all('place') if p.args==['commander']]
                self.assertEqual(len(seats),1)
                self.assertEqual(result['metadata']['commander_mg']['crew_slots_added'],0)
                self.assertEqual(result['metadata']['commander_mg']['loader_transfers'],[{'place':'gun','from':'commander','to':'gunner'}])
                self.assertTrue(any(d['code']=='commander_mg_loader_handoff' for d in result['diagnostics']))
                self.assertFalse(result['runtime_verified'])
        result=compile_assembly(self.index,'hull','hull',{'commander_only':True},self.root/'without_mg','mw_no_mg')
        root=parse((Path(result['output_dir'])/'resource'/result['definition_path']).read_bytes()).find('game_entity')
        cannon=next(p for w in root.find_all('Weaponry') for p in w.find_all('place') if p.args==['gun'])
        self.assertEqual(cannon.find('charger').args,['commander'])

    def test_loader_delegation_keeps_other_workers_minimum_and_exterior_operator(self):
        from workshop.commander_mg import _delegate_loaders
        root=block('{game_entity {placer {place "gunner" {group "crew"}}}'
            '{Weaponry {place "gun" {gunner "gunner"}{charger "commander charger2" minWorkers 2}}'
            '{place "coax" {gunner "gunner"}{charger "commander" minWorkers 1}}'
            '{place "implicit" {charger "commander"}}'
            '{place "hull" {gunner "driver"}{charger "driver"}}'
            '{place "aa" {gunner "commander"}{charger "commander"}}}}')
        changes=_delegate_loaders(root)
        places={p.args[0]:p for w in root.find_all('Weaponry') for p in w.find_all('place')}
        self.assertEqual(places['gun'].find('charger').args,['gunner charger2','minWorkers','2'])
        self.assertEqual(places['coax'].find('charger').args,['gunner','minWorkers','1'])
        self.assertEqual(places['implicit'].find('charger').args,['gunner'])
        self.assertEqual(places['hull'].find('charger').args,['driver'])
        self.assertEqual(places['aa'].find('charger').args,['commander'])
        self.assertEqual([c['place'] for c in changes],['gun','coax','implicit'])
        self.assertEqual(_delegate_loaders(root),[])  # repeated application is stable

    def test_loader_delegation_rejects_ambiguous_or_insufficient_crew_atomically(self):
        from workshop.commander_mg import _delegate_loaders
        for fields in ('{gunner ""}{charger "commander"}',
                       '{gunner "gunner missing"}{charger "commander"}',
                       '{gunner "missing"}{charger "commander"}',
                       '{gunner "gunner"}{charger "commander gunner" minWorkers 2}'):
            root=block('{game_entity {placer {place "gunner" {group "crew"}}}'
                '{Weaponry {place "valid" {gunner "gunner"}{charger "commander"}}'
                '{place "invalid" '+fields+'}}}')
            before=root.render()
            with self.subTest(fields=fields),self.assertRaises(CompileError):_delegate_loaders(root)
            self.assertEqual(root.render(),before)

    def test_interactions_preserve_chassis_hatch_and_only_add_mount_clip(self):
        root=block('{"new tank" {on "commander_hatch_open" overload {ani_play "my_hatch" 1}} {on "fire" {call "native"}}}')
        add_interactions(root,{'sequence':'cmdmg_pose'})
        self.assertEqual(len([n for n in root.find_all('on') if n.args[0]=='commander_hatch_open']),1)
        self.assertIn('my_hatch',root.render())
        self.assertIn('native',root.render())
        self.assertIn('cmdmg_pose',root.render())

    def test_edit_adds_slot_without_erasing_inventory_or_duplicating_on_reedit(self):
        snapshot=load_campaign(make_save(self.root/'copy.sav'))
        contract={'slot':'mgun_cmd','item':'base_gun',
                  'initial_item':'{item "base_gun" filled {user "mgun_cmd"}}',
                  'ammo_item':'{item "ammo rifle clip" 15}'}
        profile={'commander_mg':contract}
        plan=plan_edit(snapshot,'0x9000','mw_edited',2,state_profile=profile,keep_names=True)
        scene=parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign')
        inv=next(n for n in scene.find_all('Inventory') if n.args==[plan['new_eid']])
        self.assertIn('old_ammo',inv.render())
        self.assertEqual(len([n for n in inv.walk('item') if n.args[0]=='base_gun']),1)
        # Same selected item in a persisted slot is kept, not replenished.
        original=SCENE.replace('{item "old_ammo" "ammo" 3 {cell 0 0}}',
                               '{item "base_gun" filled {user "mgun_cmd"}}')
        snapshot=load_campaign(make_save(self.root/'again.sav',scene=original))
        plan=plan_edit(snapshot,'0x9000','mw_again',2,state_profile=profile,keep_names=True)
        result=apply_plan(snapshot,plan)['campaign.scn'].decode()
        self.assertEqual(result.count('{user "mgun_cmd"}'),1)
        self.assertNotIn('ammo rifle clip',result)

    def test_app_preview_and_check_use_the_compiled_host_coordinates(self):
        from workshop.app import AppState
        from unittest.mock import patch
        state=AppState(self.root/'workspace')
        state.index=self.index
        try:
            preview=state.commander_preview(self.spec())
            self.assertGreater(len(preview['meshes']),1)
            self.assertEqual(preview['seats'][0]['place'],'commander')
            self.assertEqual(len(preview['pitch_matrix']),4)
            with patch('workshop.registration.register_variant',return_value={}):
                result=state.check({'hull':'hull','donor':'hull',
                                    'transform':{'turret':{'position':[7,0,0],'rotation':[0,0,40]}}})
            self.assertTrue(result['ok'],result)
            turret=next(h for h in result['hosts'] if h['name']=='turret')
            self.assertAlmostEqual(turret['matrix'][0][3],7)
            self.assertIn('commander',result['crew'])
            self.assertTrue(any(row['place']=='commander' for row in result['crew_visibility']))
        finally:
            state.pool.shutdown(wait=True)

    def test_standalone_mount_preserves_vehicle_without_turret(self):
        # A complete-vehicle edit does not need a donor weapon or a turret.
        original=self.index.read
        path='entity/-vehicle/test/hull/hull.mdl'
        raw=original(path).replace(b'bone revolute "turret"',b'bone "deck"')
        self.index.read=lambda p:raw if p==path else original(p)
        spec=dict(self.spec(),parent='deck')
        result=compile_assembly(self.index,'hull','hull',{'commander_only':True,'commander_mg':spec},self.root/'full','mw_full')
        self.assertEqual(result['metadata']['crew_slots'],3)
        self.assertEqual(result['metadata']['state_profile']['inventory_policy'],'recipient')
        self.assertEqual(result['metadata']['commander_mg']['parent'],'deck')
        self.assertEqual(result['metadata']['commander_mg']['crew_slots_added'],0)
        self.assertTrue(result['metadata']['validation']['dependencies']['passed'])

    def test_removing_mount_keeps_weapon_but_drops_stale_inventory_binding(self):
        scene=SCENE.replace('{item "old_ammo" "ammo" 3 {cell 0 0}}',
                            '{item "base_gun" filling "rifle" "ammo" 2 {user "mgun_cmd"}}')
        snapshot=load_campaign(make_save(self.root/'remove.sav',scene=scene))
        plan=plan_edit(snapshot,'0x9000','mw_removed',2,keep_names=True,
                       state_profile={'retired_inventory_users':['mgun_cmd']})
        result=apply_plan(snapshot,plan)['campaign.scn'].decode()
        self.assertIn('base_gun',result)
        self.assertNotIn('{user "mgun_cmd"}',result)

    def test_switching_mount_keeps_old_weapon_and_loaded_ammunition(self):
        scene=SCENE.replace('{item "old_ammo" "ammo" 3 {cell 0 0}}',
                            '{item "old_gun" filling "rifle" "ammo" 2 {user "mgun_cmd"}}')
        snapshot=load_campaign(make_save(self.root/'switch.sav',scene=scene))
        profile={'commander_mg':{'slot':'mgun_cmd','item':'base_gun',
                 'initial_item':'{item "base_gun" filled {user "mgun_cmd"}}',
                 'ammo_item':'{item "ammo rifle clip" 15}'}}
        plan=plan_edit(snapshot,'0x9000','mw_switch',2,keep_names=True,state_profile=profile)
        result=apply_plan(snapshot,plan)['campaign.scn'].decode()
        self.assertIn('old_gun',result)
        self.assertIn('filling "rifle" "ammo" 2',result)
        self.assertEqual(result.count('{user "mgun_cmd"}'),1)

    def test_full_vehicle_wreck_keeps_the_same_placement(self):
        model=self.index.read('entity/-vehicle/test/hull/hull.mdl')
        self.index.close()
        archive(self.game,'states.pak',{
            'entity/-vehicle/test/hull_x/hull_x.def':'{game_entity {extension "hull_x.mdl"}}',
            'entity/-vehicle/test/hull_x/hull_x.mdl':model,
            'entity/-vehicle/test/hull_x/body.ply':eply(),
        })
        self.index=ResourceIndex(self.game).build()
        result=compile_assembly(self.index,'hull','hull',{'commander_only':True,
            'hull':{'position':[3,4,5],'rotation':[0,0,90]}},self.root/'wreck','mw_wreck')
        folder=self.root/'wreck/resource/entity/-vehicle/workshop/mw_wreck'
        for name in ('mw_wreck.mdl','mw_wreck_x.mdl'):
            model=parse_model((folder/name).read_bytes())
            actual=point(model['bones']['basis']['world'],[1,0,0])
            for a,e in zip(actual,[3,5,5]):self.assertAlmostEqual(a,e,places=6)

    def test_mount_profile_is_accepted_when_combining_two_owned_vehicles(self):
        result=compile_assembly(self.index,'hull','hull',{'commander_mg':self.spec()},self.root/'combine','mw_combined')
        doc=parse(SCENE.replace('"tank_hull"','"hull"').replace('"wheeled_donor"','"hull"'))
        for entity in doc.find('campaign').find_all('Entity'):
            for node in list(entity.children):
                if node.head in ('Armor','Component'):entity.remove(node)
        snapshot=load_campaign(make_save(self.root/'two_owned.sav',scene=doc.render()))
        plan=plan_assembly(snapshot,'0x9000','0x9010','mw_combined',3,state_profile=result['metadata']['state_profile'])
        scene=parse(apply_plan(snapshot,plan)['campaign.scn']).find('campaign')
        squad=next(n for n in scene.find('CampaignSquads').children if n.head=='mw_combined')
        self.assertEqual(len(squad.args[2:]),3)  # exactly the compiled crew capacity
        self.assertEqual(plan['state_profile']['commander_mg']['operator'],'commander')

    def test_moving_existing_empty_or_swapped_mount_does_not_restock_it(self):
        for contents in ('', '{item "battle_pickup" filling "ammo" 2 {user "old_slot"}}'):
            scene=SCENE.replace('{item "old_ammo" "ammo" 3 {cell 0 0}}',contents)
            scene=scene.replace('{Weaponry {State {Bullets 7}}}', '{Weaponry {Current "old_slot"}{State "old_slot" {Bullets 7}}}')
            snapshot=load_campaign(make_save(self.root/'used.sav',scene=scene))
            profile={'commander_mg':{'slot':'new_slot','previous_slot':'old_slot','item':'base_gun',
                     'initialize':False,'initial_item':'{item "base_gun" filled {user "new_slot"}}',
                     'ammo_item':'{item "ammo rifle clip" 15}'}}
            plan=plan_edit(snapshot,'0x9000','mw_moved',2,keep_names=True,state_profile=profile)
            result=apply_plan(snapshot,plan)['campaign.scn'].decode()
            self.assertNotIn('base_gun',result)
            self.assertNotIn('ammo rifle clip',result)
            self.assertNotIn('old_slot',result)
            if contents:self.assertIn('{user "new_slot"}',result)

    def test_whole_vehicle_edit_blocks_inherited_commander_weapon(self):
        self.index.close()
        original=Path(self.game/'resource/entity/fixture.pak')
        with zipfile.ZipFile(original) as archive_file:
            hull=archive_file.read('-vehicle/test/hull/hull.def')
        archive(self.game,'inherited.pak',{
            'entity/-vehicle/test/hull/hull.def':hull.replace(b'{game_entity',b'{game_entity (include "/properties/operator.ext")').replace(b'{gunner "gunner"}',b''),
            'properties/operator.ext':'{Weaponry {place "gun" {gunner "commander"}}}'})
        self.index=ResourceIndex(self.game).build()
        out=self.root/'busy_inherited'
        with self.assertRaisesRegex(CompileError,'ya apunta otra arma'):
            compile_assembly(self.index,'hull','hull',{'commander_only':True,'commander_mg':self.spec()},out,'mw_busy')
        self.assertFalse(out.exists())
