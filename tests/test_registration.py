import tempfile
import unittest
from pathlib import Path

import json

from workshop.registration import (RegistrationError, rebuild_registrations, recorded_stack, register_variant,
                                   stack_signature, validate_dependencies)
from workshop.resources import ResourceIndex
from workshop.syntax import parse


class RegistrationTests(unittest.TestCase):
    def test_scoped_registration_preserves_other_vehicles_and_their_global_links(self):
        register_variant(self.index,json.loads(json.dumps(self.result)),self.output,'mw_a','A')
        register_variant(self.index,json.loads(json.dumps(self.result)),self.output,'mw_b','B')
        for identifier in ('mw_a','mw_b'):
            (self.output/f'assembly_{identifier}.json').write_text(json.dumps(
                {'id':identifier,'metadata':self.result['metadata']}))
        preserved={p.relative_to(self.output):p.read_bytes() for p in self.output.rglob('*')
                   if p.is_file() and (p.stem=='mw_b' or p.name in ('workshop_manifest_mw_b.json','portrait_mw_b.json'))}
        self.assertTrue(preserved)
        result=rebuild_registrations(self.index,self.output,identifiers={'mw_a'})
        self.assertEqual(result['registered'],['mw_a'])
        for relative,raw in preserved.items():self.assertEqual((self.output/relative).read_bytes(),raw,relative)
        self.assertIn('mw_b',(self.output/'resource/set/registry/unit.reg').read_text())
        self.assertIn('modular_workshop/mw_b',(self.output/'resource/set/interaction_entity/entity.set').read_text())

    def test_stale_generated_globals_do_not_poison_checks_or_active_mod_authority(self):
        import shutil
        from workshop.app import AppState
        from workshop.registration import _load_include_graph
        from types import SimpleNamespace
        self.write('set/third_person/mace.inc',
                   '{"default" {level {linkBone "basis"}} {level {zoom}{camera "zoom2"}}}'
                   '{"base.camera" inherit "vehicle default"}', mod=True)
        self.index.build()
        owned=self.root/'modular_workshop'
        register_variant(self.index,copy_result:=json.loads(json.dumps(self.result)),owned,'mw_saved','Saved')
        (owned/'assembly_mw_saved.json').write_text(json.dumps({'id':'mw_saved','metadata':copy_result['metadata']}))
        folder=owned/'resource/entity/-vehicle/workshop/mw_saved';folder.mkdir(parents=True)
        (folder/'mw_saved.def').write_text('{game_entity {model "mw_saved"}}')
        (folder/'mw_saved.mdl').write_text('{skeleton {bone "body"}}')
        (owned/'resource/set/interaction_entity/entity.set').write_text(
            '{entities (include "vehicle/weapons-specifics.inc") (include "controcarro_mk2.inc")'
            ' (define "mp_gun_limits_mace" ;// v1.063 compat stub\n)}')
        (owned/'resource/set/multiplayer/units/roster_conquest.set').write_text('{units (include "conquest/inf_jap.set")}')
        previous={p.relative_to(owned):p.read_bytes() for p in owned.rglob('*') if p.is_file()}
        current=ResourceIndex(self.game,[self.mod,owned]).build()
        try:
            self.assertEqual(current.registration_sources().resolve('set/interaction_entity/entity.set').source_root,str(self.mod.resolve()))
            self.assertEqual(current.registration_sources().resolve_template('mw_saved')['id'],'mw_saved')
            state=SimpleNamespace(index=current,GLOBAL_FILES=AppState.GLOBAL_FILES)
            self.assertIsNone(AppState.mod_stack_problem(state,include_generated=False))
            self.assertIn('addon local',AppState.mod_stack_problem(state))
            checked=self.root/'clean_check'
            register_variant(current,json.loads(json.dumps(self.result)),checked,'mw_checked','Checked')
            self.assertNotIn('inf_jap', (checked/'resource/set/multiplayer/units/roster_conquest.set').read_text())
            self.assertNotIn('weapons-specifics', (checked/'resource/set/interaction_entity/entity.set').read_text())
            interactions=(checked/'resource/set/interaction_entity/entity.set').read_text()
            self.assertNotIn('controcarro_mk2',interactions)
            self.assertNotIn('v1.063 compat stub',interactions)
            staged=self.root/'staged_repair';shutil.copytree(owned,staged)
            self.assertEqual(rebuild_registrations(self.index,staged)['registered'],['mw_saved'])
            repaired=(staged/'resource/set/interaction_entity/entity.set').read_text()
            self.assertNotIn('controcarro_mk2',repaired)
            self.assertNotIn('v1.063 compat stub',repaired)
            self.assertEqual(previous,{p.relative_to(owned):p.read_bytes() for p in owned.rglob('*') if p.is_file()})
        finally:current.close()

    def test_missing_external_include_still_blocks_and_names_its_real_source(self):
        from workshop.app import AppState
        from types import SimpleNamespace
        self.write('set/interaction_entity/entity.set','{entities (include "missing_external.inc")}',mod=True)
        self.index.close();self.index=ResourceIndex(self.game,[self.mod]).build()
        state=SimpleNamespace(index=self.index,GLOBAL_FILES=AppState.GLOBAL_FILES)
        problem=AppState.mod_stack_problem(state,include_generated=False)
        self.assertIn('missing_external.inc',problem);self.assertIn('MACE',problem)

    def test_changed_gun_fires_its_own_renamed_recoil_clip(self):
        # The model gave the new gun its own "recoil_main" because the receiver
        # already used "recoil" for other bones: its shot must play that one.
        from workshop.registration import _entity_sound_record
        self.write('set/stuff/gun/test_cannon','{weapon_bullet {fireSound "cannon/Shared/NewGun/"}}')
        self.write('set/interaction_entity/tank/native.inc','''
          {"base tank" ("base_sound")}
          {"donor tank" ("donor_sound") {on fire "gun" {ani_play "recoil"}}}''',mod=True)
        self.write('set/interaction_entity/entity.set','{entities (include "tank/sounds.inc")(include "tank/native.inc")}',mod=True)
        self.index.close();self.index=ResourceIndex(self.game,[self.mod]).build()
        metadata={'operation':'gun_swap','gun_assembly':{'donor':{'place':'gun','weapon':'test_cannon','properties':[]},
                  'recipient':{'place':'main'},'sequences':['recoil','recoil_main'],'sequence_names':{'recoil':'recoil_main'}}}
        record,provenance,_=_entity_sound_record(self.index,'base','donor','mw_fire',metadata)
        main=[n for n in record.find_all('on') if n.args[:2]==['fire','main']]
        self.assertEqual(len(main),1)
        self.assertIn('ani_play "recoil_main"',main[0].render())
        self.assertNotIn('ani_play "recoil"}',main[0].render().replace(' ',''),'the shared receiver clip is not played by the new gun')
        self.assertEqual(provenance['gun_native_fire_clips'],['recoil_main'])

    def test_gun_fire_merges_into_parameterized_native_event_without_losing_other_slots(self):
        from workshop.registration import _entity_sound_record,_interaction_events,_load_include_graph
        self.write('set/stuff/gun/test_cannon','{weapon_bullet {fireSound "cannon/Shared/NewGun/"}}')
        self.write('set/interaction_entity/tank/native.inc','''
          (define "native_burst" {on fire "%slot" overload {tags add "native_fx"}
            (define "local_barrel" {view start "flash%0"}) ("local_barrel" args 06)})
          {"base tank" ("base_sound") ("native_burst" slot(main)) {on fire "coax" {tags add "keep_coax"}}}
          {"donor tank" ("donor_sound") {on fire "gun" {ani_play "recoil"}}}''',mod=True)
        self.write('set/interaction_entity/entity.set','{entities (include "tank/sounds.inc")(include "tank/native.inc")}',mod=True)
        self.index.close();self.index=ResourceIndex(self.game,[self.mod]).build()
        metadata={'operation':'gun_swap','gun_assembly':{'donor':{'place':'gun','weapon':'test_cannon','properties':[]},
                  'recipient':{'place':'main'},'sequences':['recoil']}}
        record,_,_=_entity_sound_record(self.index,'base','donor','mw_fire',metadata)
        events=record.find_all('on');main=[n for n in events if n.args[:2]==['fire','main']]
        self.assertEqual(len(main),1);self.assertIn('overload',main[0].args)
        self.assertIn('native_fx',main[0].render());self.assertIn('ani_play "recoil"',main[0].render())
        self.assertIn('flash%0',main[0].render());self.assertIn('args 06',main[0].render())
        self.assertEqual(len([n for n in events if n.args[:2]==['fire','coax']]),1)

    def test_same_event_type_on_different_weapon_slots_is_not_collapsed(self):
        from workshop.registration import _entity_sound_record
        self.write('set/interaction_entity/tank/mace.inc','''
          {"base tank" ("base_sound") {on fire "gun" {tags add "cannon"}}}
          {"base tank" {on fire "mgun" {tags add "machine_gun"}}}
          {"donor tank" ("donor_sound")}''',mod=True)
        self.index.close();self.index=ResourceIndex(self.game,[self.mod]).build()
        record,_,_=_entity_sound_record(self.index,'base','donor','mw_slots')
        self.assertEqual({tuple(n.args[:2]) for n in record.find_all('on') if n.args[:1]==['fire']},
                         {('fire','gun'),('fire','mgun')})

    def test_later_native_record_overrides_an_event_declared_by_an_earlier_macro(self):
        from workshop.registration import _entity_sound_record,_validate_interaction_events
        self.write('set/interaction_entity/tank/mace.inc','''
          (define "native_fire" {on fire "%slot" overload {tags add "old"}})
          {"base tank" ("base_sound") ("native_fire" slot(mgun))}
          {"base tank" {on fire "mgun" overload {tags add "new"}}}
          {"donor tank" ("donor_sound")}''',mod=True)
        self.index.close();self.index=ResourceIndex(self.game,[self.mod]).build()
        record,_,_=_entity_sound_record(self.index,'base','donor','mw_override')
        _validate_interaction_events(self.index,record)
        events=[n for n in record.find_all('on') if n.args[:2]==['fire','mgun']]
        self.assertEqual(len(events),1);self.assertIn('"new"',events[0].render())
        self.assertNotIn('"old"',events[0].render())

    def test_duplicate_macro_and_explicit_fire_event_is_rejected_before_registration(self):
        from workshop.registration import _validate_interaction_events
        from workshop.syntax import block
        self.write('set/interaction_entity/tank/mace.inc','''
          (define "native_fire" {on fire "%slot" overload {tags add "fire"}})''',mod=True)
        self.index.close();self.index=ResourceIndex(self.game,[self.mod]).build()
        record=block('{"mw_duplicate tank" ("native_fire" slot(mgun)) {on fire "mgun" {inherited}}}')
        with self.assertRaisesRegex(RegistrationError,'Duplicate generated interaction event: fire mgun'):
            _validate_interaction_events(self.index,record)

    def test_events_differ_by_every_qualifier_not_just_their_name(self):
        # Geofisika, MACE ZSU-37: ik begin/end on gun_rot and on turret are four
        # events; keyed by "ik" alone every build with it was refused.
        from workshop.registration import _validate_interaction_events
        from workshop.syntax import block
        record=block('{"mw_zsu tank" {on ik begin "gun_rot" overload {set "a" 1}} {on ik end "gun_rot" overload {set "a" 0}}'
                     ' {on ik begin "turret" overload {set "b" 1}} {on ik end "turret" {set "b" 0}}}')
        _validate_interaction_events(self.index,record)
        twice=block('{"mw_zsu tank" {on ik begin "turret" overload {set "b" 1}} {on ik begin "turret" {set "b" 2}}}')
        with self.assertRaisesRegex(RegistrationError,'Duplicate generated interaction event: ik begin turret'):
            _validate_interaction_events(self.index,twice)

    def test_dry_run_resolves_installed_chassis_recipe_instead_of_donor_country(self):
        import copy
        owned=self.root/'owned'/'modular_workshop'
        register_variant(self.index,copy.deepcopy(self.result),owned,'mw_first','First')
        (owned/'assembly_mw_first.json').write_text(json.dumps({'id':'mw_first','metadata':{'recipient':'base','donor':'donor'}}))
        folder=owned/'resource/entity/-vehicle/workshop/mw_first';folder.mkdir(parents=True)
        (folder/'mw_first.def').write_text('{game_entity {model "mw_first"}}')
        (folder/'mw_first.mdl').write_text('{skeleton {bone "body"}}')
        index=ResourceIndex(self.game,[self.mod,owned]).build()
        try:
            result=copy.deepcopy(self.result);result['metadata']['recipient']='mw_first'
            target=self.root/'empty_check'/'modular_workshop'
            register_variant(index,result,target,'mw_checked','Checked')
            roster=(target/'resource/set/multiplayer/units/conquest/modular_workshop.inc').read_text() if (target/'resource/set/multiplayer/units/conquest/modular_workshop.inc').exists() else (target/'resource/set/multiplayer/units/roster_conquest.set').read_text()
            self.assertTrue((target/'workshop_manifest_mw_checked.json').is_file())
            self.assertNotIn('side(ger)',roster)  # hidden native-army purchase tag is expanded/removed
        finally:index.close()

    def test_chained_recipe_uses_recipient_army_and_rejects_missing_parent(self):
        from workshop.registration import _chassis_ancestor
        recipes={'mw_first':{'recipient':'native_ger','donor':'native_rus'},
                 'mw_second':{'recipient':'mw_first','donor':'native_usa'}}
        self.assertEqual(_chassis_ancestor(recipes,'mw_second'),'native_ger')
        recipes['mw_first']['recipient']='mw_missing'
        with self.assertRaisesRegex(RegistrationError,'mw_missing'):_chassis_ancestor(recipes,'mw_second')

    def test_gun_interactions_preserve_yaw_and_mg_and_dispatch_native_recoil(self):
        from workshop.registration import _entity_sound_record
        self.write('set/stuff/gun/test_cannon','{weapon_bullet {fireSound "cannon/Shared/NewGun/"}}')
        self.write('set/interaction_entity/tank/mace.inc',
                   '{"base tank" ("base_sound") {on "add_weapon_sound" overload {link_sound "Gun_Mid" "old"}{link_sound "MG_Mid" "receiver_mg"}}}'
                   '{"donor tank" ("donor_sound") {on "add_weapon_sound" overload {link_sound "Gun_Mid" "cannon/Shared/NewGun/"}{link_sound "MG_Mid" "donor_mg"}}{on fire "gun" {ani_play "recoil"}}}',mod=True)
        self.index.close();self.index=ResourceIndex(self.game,[self.mod]).build()
        metadata={'operation':'gun_swap','gun_assembly':{'donor':{'place':'gun','weapon':'test_cannon','properties':[]},
                  'recipient':{'place':'main'},'sequences':['recoil']}}
        record,provenance,_=_entity_sound_record(self.index,'base','donor','mw_gun',metadata)
        text=record.render()
        self.assertIn('base_turret',text);self.assertIn('receiver_mg',text)
        self.assertNotIn('donor_mg',text);self.assertNotIn('donor_turret',text)
        self.assertIn('NewGun',text);self.assertIn('ani_play "recoil"',text)
        self.assertEqual(provenance['gun_native_fire_clips'],['recoil'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.mod = self.root / "mace"
        self.output = self.root / "staging/MW_test"
        for folder in [self.game, self.mod]:
            (folder / "resource").mkdir(parents=True)
        (self.mod / "mod.info").write_text('{mod {name "MACE"}}')
        self.write("entity/-vehicle/base/base.def", '{game_entity {model "base"}}')
        self.write("entity/-vehicle/base/base.mdl", "{Skeleton}")
        self.write("entity/-vehicle/donor/donor.def", '{game_entity {model "donor"}}')
        self.write("entity/-vehicle/donor/donor.mdl", "{Skeleton}")
        self.write("set/breed/mp/ger/mid/tankman.set", "{breed}")
        self.write("set/registry/unit.reg", '{units {"base" {vehicle_view "base_icon"}} {"mace_other" {vehicle_view "custom"}}}', mod=True)
        self.write("set/tp_control.set", '{third_person_control {vehicle (include "third_person/mace.inc") {"base" inherit "vehicle base.camera"} {"mace_other" inherit "vehicle mace.camera"}}}', mod=True)
        self.write("set/multiplayer/units/roster_conquest.set", '{units (include "conquest/settings.set") (include "conquest/mace.set")}', mod=True)
        self.write("set/multiplayer/units/conquest/settings.set",
                   '(define "generic_v_conquest" {tags "conquest conquestonly %t"} {research_stage %r} {research_stage_max %mr} {cp %cp})'
                   ' (define "vehicle2" ("generic_v_conquest" t(%side %period %tag) r(%min_stage) mr(%max_stage) cp(%cp))'
                   ' {content "* ( mp/%side/%period/%crew1 mp/%side/%period/%crew2 )"})'
                   ' (define "squad_with1types_conquest" {"%name(%side)" {content "mp/%side/%period/%c1"}'
                   ' {tags "conquest conquestonly %side %period %tag"} {research_stage %min_stage}})')
        self.write("set/multiplayer/units/conquest/mace.set", '{"base" ("vehicle2" side(ger) period(mid) min_stage(3) max_stage(8) cp(16) cw(4) crew1(tankman:3) crew2(commander:1)) {cost 330}}', mod=True)
        self.write("set/interaction_entity/entity.set", '{entities (include "tank/sounds.inc") (include "tank/mace.inc")}', mod=True)
        self.write("set/interaction_entity/tank/sounds.inc", '(define "base_sound" {on "add_movement_sound" overload {link_sound "engine_full" "base_engine"} {link_sound "rotate_turret" "base_turret"}}) (define "donor_sound" {on "add_movement_sound" overload {link_sound "engine_full" "donor_engine"} {link_sound "rotate_turret" "donor_turret"}})')
        self.write("set/interaction_entity/tank/mace.inc", '{"base tank" ("base_sound") {on "add_weapon_sound" overload ("base_gun")}} {"donor tank" ("donor_sound") {on "add_weapon_sound" overload ("donor_gun")}}', mod=True)
        self.index = ResourceIndex(self.game, [self.mod]).build()
        self.result = {"metadata": {"recipient": "base", "donor": "donor", "crew_roles": ["driver", "gunner", "commander"]},
                       "files": [], "campaign_plan": {"reserve_squads": [{"template": "mw_reserve_a(ger)", "breed": "mp/ger/mid/tankman", "eids": ["0xa"]}]}}

    def write(self, virtual, text, mod=False):
        file = (self.mod if mod else self.game) / "resource" / virtual
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)

    def tearDown(self):
        self.index.close()
        self.temp.cleanup()

    def test_reduced_vehicle_crew_can_retire_a_breed_in_campaign_content(self):
        self.result['metadata']['crew_roles'] = ['driver']
        register_variant(self.index, self.result, self.output, 'mw_new', 'Supply conversion')
        roster = parse((self.output/'resource/set/multiplayer/units/modular_workshop/mw_new.set').read_bytes())
        content = next(roster.walk('content')).args[0]
        self.assertIn('mp/ger/mid/tankman:1', content)
        self.assertNotIn('commander', content)
        self.assertNotIn(':0', content)

    def test_effective_crew_counts_include_defaults_and_already_expanded_content(self):
        from workshop.registration import _resize_roster_crew
        for source in ('* ( mp/ger/mid/tankman:3 mp/ger/mid/commander:1 )',
                       '* ( mp/ger/mid/tankman mp/ger/mid/commander )'):
            entry = parse('{unit {content "'+source+'"}}').find('unit')
            _resize_roster_crew(entry, ['driver'])
            self.assertIn('mp/ger/mid/tankman:1', entry.find('content').args[0])
            self.assertNotIn('commander', entry.find('content').args[0])
            before = entry.render()
            _resize_roster_crew(entry, ['driver'])
            self.assertEqual(entry.render(), before)

    def test_portraits_and_icons_come_from_the_chassis_unless_rendered(self):
        for variant in range(4):
            self.write(f"interface/scene/portrait_squad/base_0{variant}.png", f"base portrait {variant}")
            self.write(f"interface/scene/unit_icon/donor_0{variant}.tga", f"donor icon {variant}")
        self.index.close()
        self.index = ResourceIndex(self.game, [self.mod]).build()
        # The tool rendered its own portrait: it is kept; icons fall back to
        # the module because the chassis has none.
        custom = self.output / "resource/interface/scene/portrait_squad/mw_new_00.png"
        custom.parent.mkdir(parents=True)
        custom.write_bytes(b"rendered")
        metadata = register_variant(self.index, self.result, self.output, "mw_new", "New")
        scene = self.output / "resource/interface/scene"
        self.assertEqual(custom.read_bytes(), b"rendered")
        self.assertFalse((scene / "portrait_squad/mw_new_01.png").exists())
        self.assertEqual((scene / "unit_icon/mw_new_03.tga").read_text(), "donor icon 3")
        self.assertEqual(metadata["portraits"], ["unit_icon"])
        # Without a rendered one, the chassis portrait stands in.
        custom.unlink()
        other = register_variant(self.index, json.loads(json.dumps(self.result)), self.output, "mw_other", "Other")
        self.assertEqual((scene / "portrait_squad/mw_other_02.png").read_text(), "base portrait 2")
        self.assertIn("portrait_squad", other["portraits"])

    def test_reserve_squads_take_the_lone_soldier_portrait(self):
        self.write("interface/scene/portrait_squad/single_tankman(ger)_00.png", "tankman")
        self.index.close()
        self.index = ResourceIndex(self.game, [self.mod]).build()
        register_variant(self.index, self.result, self.output, "mw_new", "New")
        portrait = self.output / "resource/interface/scene/portrait_squad/mw_reserve_a(ger)_00.png"
        self.assertEqual(portrait.read_text(), "tankman")

    def test_reregistration_preserves_rendered_states_and_their_provenance(self):
        from workshop.app import AppState
        from tests.test_portraits import image, png
        portraits = [image(png(pixel=variant)) for variant in range(4)]
        AppState.write_portrait(self.output, "mw_new", portraits)
        first = register_variant(self.index, self.result, self.output, "mw_new", "New")
        folder = self.output / "resource/interface/scene/portrait_squad"
        before = [path.read_bytes() for path in sorted(folder.glob("mw_new_*.png"))]
        (self.output / "assembly_mw_new.json").write_text(json.dumps({"id": "mw_new", "metadata": self.result["metadata"]}))
        rebuilt = rebuild_registrations(self.index, self.output)
        self.assertEqual(rebuilt["registered"], ["mw_new"])
        second = json.loads((self.output / "workshop_manifest_mw_new.json").read_text())
        self.assertEqual([path.read_bytes() for path in sorted(folder.glob("mw_new_*.png"))], before)
        self.assertEqual(first["portrait"]["source"], "rendered_model")
        self.assertEqual(second["portrait"], first["portrait"])
        self.assertNotIn("assembled_portrait_pending", [row["code"] for row in self.result.get("diagnostics", [])])

    def test_chassis_fallback_is_explicit_and_requests_regeneration(self):
        for variant in range(4):
            self.write(f"interface/scene/portrait_squad/base_0{variant}.png", f"base portrait {variant}")
        self.index.close(); self.index = ResourceIndex(self.game, [self.mod]).build()
        metadata = register_variant(self.index, self.result, self.output, "mw_new", "New")
        self.assertEqual(metadata["portrait"]["source"], "chassis_fallback")
        self.assertTrue(any(row["code"] == "assembled_portrait_pending" for row in self.result["diagnostics"]))
        (self.output / "assembly_mw_new.json").write_text(json.dumps({"id": "mw_new", "metadata": self.result["metadata"]}))
        self.assertEqual(rebuild_registrations(self.index, self.output)["registered"], ["mw_new"])
        again = json.loads((self.output / "workshop_manifest_mw_new.json").read_text())
        self.assertEqual(again["portrait"]["source"], "chassis_fallback")

    def test_shared_reserve_squad_is_defined_once(self):
        # Two variants left the same spare crew (same army, breed and size):
        # one roster entry and one localization key, not one per variant.
        register_variant(self.index, json.loads(json.dumps(self.result)), self.output, "mw_one", "One")
        register_variant(self.index, json.loads(json.dumps(self.result)), self.output, "mw_two", "Two")
        units = self.output / "resource/set/multiplayer/units"
        reserves = (units / "modular_workshop/_reserves.set").read_text()
        self.assertEqual(reserves.count('"mw_reserve_a(ger)"'), 1)
        self.assertEqual((units / "roster_conquest.set").read_text().count("modular_workshop/_reserves.set"), 1)
        desc = self.output / "localizations/es/interface/text/desc"
        # Spanish is a .po with the translation in msgstr (a .pot under es/ is ignored by the game).
        spanish = (desc / "modular_workshop__reserves.po").read_text(encoding="utf-8")
        self.assertEqual(spanish.count("desc/squad/mw_reserve_a(ger)"), 1)
        self.assertIn('msgid "Reserve crew"\nmsgstr "Tripulación de reserva"', spanish)
        self.assertFalse((desc / "modular_workshop__reserves.pot").exists())
        self.assertIn("Reserve crew", (self.output / "localizations/default/interface/text/desc/modular_workshop__reserves.pot").read_text(encoding="utf-8"))
        for variant in ("mw_one", "mw_two"):
            self.assertNotIn("mw_reserve", (desc / f"modular_workshop_{variant}.po").read_text(encoding="utf-8"))
            self.assertNotIn("mw_reserve", (units / f"modular_workshop/{variant}.set").read_text())

    def test_effective_globals_and_chassis_vs_turret_authority(self):
        metadata = register_variant(self.index, self.result, self.output, "mw_new", 'New "vehicle"')
        unit = (self.output / "resource/set/registry/unit.reg").read_text()
        self.assertIn('"mace_other"', unit)
        self.assertIn('"mw_new"', unit)
        self.assertIn('"base_icon"', unit)
        control = (self.output / "resource/set/tp_control.set").read_text()
        self.assertIn('third_person/mace.inc', control)
        self.assertIn('"mw_new" inherit "vehicle base.camera"', control)
        roster = (self.output / "resource/set/multiplayer/units/modular_workshop/mw_new.set").read_text()
        # Macros are expanded; units stay in the Conquest unit set but carry no
        # army tag, so they can be owned (like captured vehicles) but not bought.
        self.assertIn('{content "* ( mp/ger/mid/tankman:2 mp/ger/mid/commander:1 )"}', roster)
        self.assertNotIn("mw_reserve", roster)
        reserves = (self.output / "resource/set/multiplayer/units/modular_workshop/_reserves.set").read_text()
        self.assertIn('{"mw_reserve_a(ger)"', reserves)
        self.assertIn('{content "mp/ger/mid/tankman:1"}', reserves)
        self.assertNotIn("vehicle(mw_reserve", reserves)
        self.assertIn('modular_workshop/_reserves.set', (self.output / "resource/set/multiplayer/units/roster_conquest.set").read_text())
        self.assertIn("{research_stage 1}", roster)
        tags = [node.args[0].split() for node in parse(roster + reserves).walk("tags")]
        self.assertEqual(len(tags), 2)
        for values in tags:
            self.assertIn("conquest", values)
            self.assertNotIn("ger", values)
        sound = (self.output / "resource/set/interaction_entity/modular_workshop/mw_new.inc").read_text()
        self.assertIn('"base_engine"', sound)
        self.assertIn('"donor_turret"', sound)
        self.assertIn('"donor_gun"', sound)
        self.assertNotIn('"donor_engine"', sound)
        self.assertNotIn('"base_turret"', sound)
        self.assertNotIn('"base_gun"', sound)
        parse(sound)
        self.assertEqual(metadata["source_order"], ["vanilla", "mace"])
        info = (self.output / "mod.info").read_text()
        self.assertIn("Load after: MACE", info)
        self.assertNotIn("{require", info)
        pot = (self.output / "localizations/default/interface/text/desc/modular_workshop_mw_new.pot").read_text()
        self.assertIn('msgctxt "desc/entity/mw_new"', pot)
        reserve_pot = (self.output / "localizations/default/interface/text/desc/modular_workshop__reserves.pot").read_text()
        self.assertIn('msgctxt "desc/squad/mw_reserve_a(ger)"', reserve_pot)

    def test_missing_donor_interaction_uses_defaults_and_keeps_chassis_behavior(self):
        # Native MG Lafettes legitimately have no entity-specific record. Do
        # not invent events or require a mountable adapter for an absent record.
        self.write("set/interaction_entity/tank/mace.inc",
                   '{"base tank" ("base_sound") {on "add_weapon_sound" overload ("base_gun")}'
                   ' {on "engine_failure" {effect "base_smoke"}}}', mod=True)
        self.index.build()
        metadata = register_variant(self.index, self.result, self.output, "mw_default", "Default donor")
        sound = (self.output / "resource/set/interaction_entity/modular_workshop/mw_default.inc").read_text()
        self.assertIn('"mw_default tank"', sound)
        self.assertIn('"base_engine"', sound)
        self.assertIn('"engine_failure"', sound)
        self.assertIn('"base_smoke"', sound)
        # With a replacement mount, the removed chassis turret cannot keep
        # supplying its obsolete weapon/rotation interactions.
        self.assertNotIn('"base_turret"', sound)
        self.assertNotIn('"base_gun"', sound)
        self.assertNotIn('"add_weapon_sound"', sound)
        self.assertIsNone(metadata["sound_authority"]["turret"])
        self.assertEqual(metadata["sound_authority"]["weapon_playback"], "engine_default")
        self.assertEqual(metadata["sound_authority"]["native_weapons"], [])
        self.assertTrue(any("Donor donor has no entity-specific" in warning for warning in metadata["warnings"]))
        self.assertEqual(self.result["diagnostics"][-1]["code"], "donor_interaction_defaults")
        manifest = json.loads((self.output / "workshop_manifest_mw_default.json").read_text())
        self.assertEqual(manifest["sound_authority"], metadata["sound_authority"])

    def test_missing_donor_interaction_free_mount_preserves_original_chassis_weapons(self):
        self.write("set/interaction_entity/tank/mace.inc",
                   '{"base tank" ("base_sound") {on "add_weapon_sound" overload ("base_gun")}}', mod=True)
        self.index.build()
        self.result["metadata"]["recipient_mount"] = "@free"
        metadata = register_variant(self.index, self.result, self.output, "mw_free", "Free native donor")
        sound = (self.output / "resource/set/interaction_entity/modular_workshop/mw_free.inc").read_text()
        self.assertIn('"base_engine"', sound)
        self.assertIn('"base_turret"', sound)
        self.assertIn('"base_gun"', sound)
        self.assertEqual(len(list(parse(sound).walk("on"))), 2)
        self.assertIn("on add_weapon_sound overload", metadata["sound_authority"]["chassis_events"])
        self.assertEqual(metadata["sound_authority"]["weapon_playback"], "engine_default")

    def test_repeated_donor_records_merge_like_the_game(self):
        # MACE re-declares vanilla records to extend them (cannon_mace.inc); the
        # game merges them in include order instead of rejecting the unit.
        path = "set/interaction_entity/tank/mace.inc"
        self.write(path, self.index.read_text(path) + ' {"donor tank" {on "spawn_tag" ("ai_tag")}}', mod=True)
        self.index.build()
        metadata = register_variant(self.index, self.result, self.output, "mw_merged", "Merged donor")
        self.assertIn(" + ", metadata["sound_authority"]["turret"])

    def test_ambiguous_donor_weapon_events_still_fail_before_writing(self):
        self.write("set/interaction_entity/tank/mace.inc",
                   '{"base tank" ("base_sound")} {"donor tank"'
                   ' {on "add_weapon_sound" overload ("donor_gun")}'
                   ' {on "add_weapon_sound" overload ("other_gun")}}', mod=True)
        self.index.build()
        with self.assertRaisesRegex(RegistrationError, "unambiguous donor weapon events"):
            register_variant(self.index, self.result, self.output, "mw_ambiguous", "Ambiguous donor")
        self.assertFalse(self.output.exists())

    def test_chassis_without_interaction_record_keeps_engine_defaults(self):
        self.write("set/interaction_entity/tank/mace.inc", '{"donor tank" ("donor_sound")}', mod=True)
        self.index.build()
        metadata = register_variant(self.index, self.result, self.output, "mw_no_chassis", "No chassis")
        self.assertEqual(metadata["sound_authority"]["weapon_playback"], "chassis_default")
        sound = (self.output / "resource/set/interaction_entity/modular_workshop/mw_no_chassis.inc").read_text()
        self.assertNotIn("{", sound)

    def test_donor_record_without_weapon_event_keeps_the_weapon_own_sound(self):
        # Rocket launchers/howitzers: a record without a vehicle weapon event and
        # no mountable weapon. The weapon keeps its own sound, with a warning.
        self.write("set/interaction_entity/tank/mace.inc",
                   '{"base tank" ("base_sound")} {"donor tank" ("donor_sound")}', mod=True)
        self.index.build()
        metadata = register_variant(self.index, self.result, self.output, "mw_default_sound", "Default sound")
        self.assertEqual(metadata["sound_authority"]["weapon_playback"], "engine_default")
        self.assertTrue(any("keeps the sound" in warning for warning in metadata["warnings"]))

    def test_a_chassis_sold_to_two_armies_registers_the_variant_for_both(self):
        # Methodical Conquest Expanded sells a captured Matilda I to the Axis:
        # one entry per army, and a repeated one in the same army overrides.
        self.write("set/breed/mp/eng/mid/tankman.set", "{breed}")
        self.write("set/multiplayer/units/conquest/mace.set",
                   '{"base" ("vehicle2" side(ger) period(mid) min_stage(3) max_stage(8) cp(16) cw(4) crew1(tankman:3) crew2(commander:1)) {cost 330}}'
                   '{"base" ("vehicle2" side(eng) period(mid) min_stage(3) max_stage(8) cp(16) cw(4) crew1(tankman:3) crew2(commander:1)) {cost 200}}'
                   '{"base" ("vehicle2" side(eng) period(mid) min_stage(3) max_stage(8) cp(16) cw(4) crew1(tankman:3) crew2(commander:1)) {cost 250}}', mod=True)
        index = ResourceIndex(self.game, [self.mod]).build()
        try:
            register_variant(index, self.result, self.output, "mw_new", "Two armies")
        finally:
            index.close()
        roster = parse((self.output / "resource/set/multiplayer/units/modular_workshop/mw_new.set").read_text())
        entries = [node for node in roster.children if node.head == "mw_new"]
        self.assertEqual(len(entries), 2)
        text = roster.render()
        self.assertIn("{cost 330}", text)
        self.assertIn("{cost 250}", text)
        self.assertNotIn("{cost 200}", text)

    def test_a_vehicle_put_up_for_sale_costs_three_quarters_of_its_parts_and_unlocks_with_the_last(self):
        # James Rommel: Pz.III (40) + Pz.IV (40) -> 60. Here base 330 (stage 3) + donor 200 (stage 5).
        self.write("set/multiplayer/units/conquest/donor.set",
                   '{"donor" ("vehicle2" side(ger) period(mid) min_stage(5) max_stage(8) cp(16) cw(4) crew1(tankman:3) crew2(commander:1)) {cost 200}}', mod=True)
        self.write("set/multiplayer/units/roster_conquest.set",
                   '{units (include "conquest/settings.set") (include "conquest/mace.set") (include "conquest/donor.set")}', mod=True)
        index = ResourceIndex(self.game, [self.mod]).build()
        try:
            from workshop.registration import shop_offer
            offer = shop_offer(index, ["base", "donor", "kettenkrad"])
            self.assertEqual((offer["cost"], offer["stage"], offer["unpriced"]), (400, 5, ["kettenkrad"]))
            provenance = register_variant(index, self.result, self.output, "mw_sale", "For sale",
                                          shop={"parts": ["base", "donor"]})
        finally:
            index.close()
        roster = parse((self.output / "resource/set/multiplayer/units/modular_workshop/mw_sale.set").read_text())
        entry = roster.find("mw_sale")
        self.assertIn("ger", entry.find("tags").args[0].split(), "the army tag lists it in that army's shop")
        self.assertEqual([entry.find(h).args[0] for h in ("cost", "research_stage", "research_stage_max")], ["400", "5", "99"])
        self.assertEqual(provenance["shop"]["parts"], ["base", "donor"])
        reserves = (self.output / "resource/set/multiplayer/units/modular_workshop/_reserves.set").read_text()
        self.assertNotIn("ger", parse(reserves).walk("tags").__next__().args[0].split(), "reserve crews stay out of the shop")

    def test_parts_count_every_unit_used_and_chained_vehicles_by_what_they_are_made_of(self):
        from workshop.registration import vehicle_parts, shop_offer
        two_turrets = {"recipient": "base", "donor": "donor", "transform": {"assembly_draft": {"schema": 1, "guns": [],
            "turrets": [{"donor_template": "donor"}, {"donor_template": "donor"}, {"native": True, "donor_template": "base"}]}}}
        self.assertEqual(vehicle_parts(two_turrets), ["base", "donor", "donor"])
        self.assertEqual(vehicle_parts({"recipient": "base", "donor": "base", "transform": {}}), ["base"], "an edit is one vehicle")
        self.assertEqual(vehicle_parts({"recipient": "base", "donor": "donor", "transform": {}}), ["base", "donor"])
        self.write("set/multiplayer/units/conquest/donor.set",
                   '{"donor" ("vehicle2" side(ger) period(mid) min_stage(5) max_stage(8) cp(16) cw(4) crew1(tankman:3) crew2(commander:1)) {cost 200}}', mod=True)
        self.write("set/multiplayer/units/roster_conquest.set",
                   '{units (include "conquest/settings.set") (include "conquest/mace.set") (include "conquest/donor.set")}', mod=True)
        index = ResourceIndex(self.game, [self.mod]).build()
        try:
            # A chained vehicle counts the units of the workshop vehicle it reuses: 330 + 200 + 200 + 200.
            offer = shop_offer(index, ["mw_hybrid", "donor"], {"mw_hybrid": two_turrets})
        finally:
            index.close()
        self.assertEqual([row["template"] for row in offer["parts"]], ["base", "donor", "donor", "donor"])
        self.assertEqual(offer["cost"], 700)  # 930 * 0.75 = 697.5

    def test_rebuild_keeps_a_vehicle_on_sale_and_prices_it_with_the_new_stack(self):
        provenance = register_variant(self.index, self.result, self.output, "mw_sale", "For sale", shop={"parts": ["base"]})
        self.assertEqual(provenance["shop"]["offer"]["cost"], 250)  # 330 * 0.75 = 247.5 -> 250
        (self.output / "assembly_mw_sale.json").write_text(json.dumps({"id": "mw_sale", "metadata": self.result["metadata"]}))
        self.write("set/multiplayer/units/conquest/mace.set", '{"base" ("vehicle2" side(ger) period(mid) min_stage(3) max_stage(8) cp(16) cw(4) crew1(tankman:3) crew2(commander:1)) {cost 400}}', mod=True)
        changed = ResourceIndex(self.game, [self.mod]).build()
        try:
            rebuild_registrations(changed, self.output)
        finally:
            changed.close()
        entry = parse((self.output / "resource/set/multiplayer/units/modular_workshop/mw_sale.set").read_text()).find("mw_sale")
        self.assertEqual(entry.find("cost").args[0], "300")
        self.assertIn("ger", entry.find("tags").args[0].split())

    def test_rebuild_reregisters_stored_variants_for_a_changed_mod_stack(self):
        # One shared addon serves every campaign; when the active mods change,
        # registrations are derived again from the new stack's files.
        register_variant(self.index, self.result, self.output, "mw_new", "Hybrid")
        (self.output / "assembly_mw_new.json").write_text(json.dumps({"id": "mw_new", "metadata": self.result["metadata"]}))
        self.write("set/multiplayer/units/conquest/mace.set", '{"base" ("vehicle2" side(ger) period(mid) min_stage(3) max_stage(8) cp(16) cw(4) crew1(tankman:3) crew2(commander:1)) {cost 500}}', mod=True)
        changed = ResourceIndex(self.game, [self.mod]).build()
        try:
            report = rebuild_registrations(changed, self.output)
            self.assertEqual(report, {"registered": ["mw_new"], "skipped": []})
            roster = (self.output / "resource/set/multiplayer/units/modular_workshop/mw_new.set").read_text()
            self.assertIn("{cost 500}", roster)
            self.assertIn('include "modular_workshop/mw_new.set"', (self.output / "resource/set/multiplayer/units/roster_conquest.set").read_text())
            self.assertEqual(recorded_stack(self.output), stack_signature(changed))
        finally:
            changed.close()

    def test_rebuild_registers_variants_built_from_workshop_vehicles(self):
        # T-20 + (Sd.Kfz 222 + Pak): recipient/donor are workshop IDs the native
        # stack does not know; they register through the parts they are made of.
        register_variant(self.index, self.result, self.output, "mw_new", "Hybrid")
        (self.output / "assembly_mw_new.json").write_text(json.dumps({"id": "mw_new", "metadata": self.result["metadata"]}))
        chained = {**self.result["metadata"], "recipient": "mw_new", "donor": "mw_new"}
        (self.output / "assembly_mw_chain.json").write_text(json.dumps({"id": "mw_chain", "metadata": chained}))
        manifest = json.loads((self.output / "workshop_manifest_mw_new.json").read_text(encoding="utf-8"))
        manifest.update({"id": "mw_chain", "display_name": "Chain"})
        (self.output / "workshop_manifest_mw_chain.json").write_text(json.dumps(manifest))
        report = rebuild_registrations(self.index, self.output)
        self.assertEqual((sorted(report["registered"]), report["skipped"]), (["mw_chain", "mw_new"], []))
        units = parse((self.output / "resource/set/registry/unit.reg").read_text()).find("units")
        self.assertEqual(units.find("mw_chain").find("vehicle_view").args, ["base_icon"])
        self.assertTrue((self.output / "resource/set/multiplayer/units/modular_workshop/mw_chain.set").exists())

    def test_chassis_without_unit_view_uses_the_module_view(self):
        # MACE ka_mi_mid has no unit.reg entry of its own.
        self.write("set/registry/unit.reg", '{units {"donor" {vehicle_view "donor_icon"}}}', mod=True)
        self.index.build()
        metadata = register_variant(self.index, self.result, self.output, "mw_view", "View")
        units = parse((self.output / "resource/set/registry/unit.reg").read_text()).find("units")
        self.assertEqual(units.find("mw_view").find("vehicle_view").args, ["donor_icon"])
        self.assertTrue(any("unit.reg view" in warning for warning in metadata["warnings"]))

    def test_rebuild_reports_variants_the_new_stack_cannot_register(self):
        register_variant(self.index, self.result, self.output, "mw_new", "Hybrid")
        (self.output / "assembly_mw_new.json").write_text(json.dumps({"id": "mw_new", "metadata": self.result["metadata"]}))
        vanilla = ResourceIndex(self.game, []).build()  # the chassis roster only exists in the mod
        try:
            report = rebuild_registrations(vanilla, self.output)
            self.assertEqual([row["id"] for row in report["skipped"]], ["mw_new"])
            self.assertFalse((self.output / "resource/set/multiplayer/units/modular_workshop/mw_new.set").exists())
        finally:
            vanilla.close()

    def test_invalid_reserve_fails_before_writing(self):
        self.result["campaign_plan"]["reserve_squads"][0]["breed"] = "unknown/token"
        with self.assertRaises(RegistrationError):
            register_variant(self.index, self.result, self.output, "mw_new", "New")
        self.assertFalse(self.output.exists())

    def test_staging_cannot_be_active_source(self):
        with self.assertRaises(RegistrationError):
            register_variant(self.index, self.result, self.mod / "generated", "mw_new", "New")

    def test_existing_ids_and_path_traversal_are_rejected(self):
        with self.assertRaises(RegistrationError):
            register_variant(self.index, self.result, self.output, "base", "New")
        with self.assertRaises(RegistrationError):
            register_variant(self.index, self.result, self.output, "../escape", "New")

    def test_second_variant_preserves_first_in_shared_effective_globals(self):
        register_variant(self.index, self.result, self.output, "mw_one", "One")
        self.result["campaign_plan"]["reserve_squads"] = []
        register_variant(self.index, self.result, self.output, "mw_two", "Two")
        for path in ("set/registry/unit.reg", "set/tp_control.set", "set/interaction_entity/entity.set", "set/multiplayer/units/roster_conquest.set"):
            text = (self.output / "resource" / path).read_text()
            self.assertIn("mw_one", text)
            self.assertIn("mw_two", text)

    def test_donor_aiming_profile_follows_verified_bone_map(self):
        self.write("set/tp_control.set", '{third_person_control {vehicle (include "third_person/mace.inc") {"base" inherit "vehicle base.camera"} {"donor" inherit "vehicle donor.camera"}}}', mod=True)
        self.write("set/third_person/mace.inc", '{"base.camera" {clip {distanceMax 350}} {level {linkBone "basis"} {offset 1 2 3}} {level {zoom} {linkBone "base_scope"}}} {"donor.camera" {clip {distanceMax 250}} {level {linkBone "basis"}} {level {zoom} {linkBone "turret"} {offset 39 7 15.5}} {level {camera "zoom2.4"}}}', mod=True)
        self.index.build()
        self.result["metadata"]["bone_mapping"] = {"turret": "turret"}
        metadata = register_variant(self.index, self.result, self.output, "mw_camera", "Camera")
        doc = parse((self.output / "resource/set/tp_control.set").read_text())
        camera = doc.find("third_person_control").find("vehicle").find("mw_camera")
        self.assertIn("distanceMax 350", camera.render())
        self.assertIn("offset 1 2 3", camera.render())
        self.assertIn('linkBone "turret"', camera.render())
        self.assertIn("offset 39 7 15.5", camera.render())
        self.assertNotIn("base_scope", camera.render())
        self.assertTrue(metadata["camera_authority"]["aiming_transferred"])
        self.assertEqual(len(camera.find_all("level")), 3)

    def test_partial_camera_overrides_keep_native_zoom_levels_and_clip_fields(self):
        from workshop.registration import _native_camera_profile
        self.write('set/third_person/mace.inc', '''
          {"parent" {clip {distanceMin 20}{distanceMax 300}}
            {level {linkBone "basis"}{camera "zoom1"}}
            {level {zoom}{linkBone "optic"}{offset 1 2 3}} {level {camera "zoom4"}}}
          {"base.camera" inherit "vehicle parent" {clip {distanceMax 400}}
            {level {nearoffset 4 5 6}} {level} {level {camera "zoom6"}}}''', mod=True)
        self.index.build()
        camera, _ = _native_camera_profile(self.index, 'base')
        self.assertEqual(len(camera.find_all('level')), 3)
        self.assertEqual(camera.find('clip').find('distanceMin').args, ['20'])
        self.assertEqual(camera.find('clip').find('distanceMax').args, ['400'])
        self.assertIsNotNone(camera.find_all('level')[1].find('zoom'))
        self.assertEqual(camera.find_all('level')[1].find('linkBone').args, ['optic'])
        self.assertEqual(camera.find_all('level')[2].find('camera').args, ['zoom6'])

    def test_unavailable_exported_camera_socket_uses_a_valid_native_fallback(self):
        self.write('set/third_person/mace.inc', '''
          {"default" {level {linkBone "basis"}} {level {zoom}{camera "zoom2"}}}
          {"base.camera" {level {linkBone "basis"}} {level {zoom}{linkBone "removed_optic"}}}''', mod=True)
        self.index.build()
        folder = self.output/'resource/entity/-vehicle/workshop/mw_camera'
        folder.mkdir(parents=True)
        (folder/'mw_camera.mdl').write_text('{skeleton {bone "frame"}}')
        metadata = register_variant(self.index, self.result, self.output, 'mw_camera', 'Camera')
        doc = parse((self.output/'resource/set/tp_control.set').read_text())
        camera = doc.find('third_person_control').find('vehicle').find('mw_camera')
        self.assertEqual([n.args[0] for n in camera.walk('linkBone')], ['frame'])
        self.assertIsNotNone(camera.find_all('level')[1].find('zoom'))
        self.assertEqual(metadata['camera_authority']['fallback'], 'native_generic')

    def test_source_fingerprints_detect_mod_updates_before_install(self):
        metadata = register_variant(self.index, self.result, self.output, "mw_new", "New")
        self.assertTrue(validate_dependencies(self.index, metadata)["valid"])
        self.write("set/breed/mp/ger/mid/tankman.set", "{breed {health 150}}")
        self.index.build()
        with self.assertRaises(RegistrationError):
            validate_dependencies(self.index, metadata)



class NativeAncestorTests(unittest.TestCase):
    def test_chassis_ancestry_rejects_missing_and_cyclic_recipes(self):
        from workshop.registration import _chassis_ancestor
        for recipes, template in [({},'mw_missing'),({'mw_a':{'recipient':'mw_a'}},'mw_a'),
                                  ({'mw_a':{'recipient':'mw_b'},'mw_b':{'recipient':'mw_a'}},'mw_a')]:
            with self.subTest(template=template),self.assertRaises(RegistrationError):
                _chassis_ancestor(recipes, template)

    def test_reused_workshop_chassis_resolves_to_its_native_vehicle(self):
        # A chassis from the depot that was already a workshop build: its army
        # comes from the game vehicle it was made from, following the chain.
        from workshop.registration import _native_template
        recipes = {"mw_b": {"recipient": "mw_a", "donor": "pz35t"}, "mw_a": {"recipient": {"id": "sdkfz222a"}, "donor": "t26"}}
        self.assertEqual(_native_template(recipes, "mw_b", "recipient"), "sdkfz222a")
        self.assertEqual(_native_template(recipes, "mw_b", "donor"), "pz35t")
        self.assertEqual(_native_template(recipes, "t34", "recipient"), "t34")
        self.assertEqual(_native_template({"mw_x": {"recipient": "mw_x"}}, "mw_x", "recipient"), "mw_x")


if __name__ == "__main__":
    unittest.main()


class StaleIncludeTests(unittest.TestCase):
    def test_includes_of_skipped_vehicles_are_removed(self):
        # A vehicle skipped on re-registration lost its .inc file while the
        # global entity.set still included it: the game closed at start.
        import tempfile
        from pathlib import Path
        from workshop.registration import drop_missing_own_includes
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "resource/set/interaction_entity"
            (folder / "modular_workshop").mkdir(parents=True)
            (folder / "modular_workshop" / "mw_kept.inc").write_text("{}")
            (folder / "entity.set").write_text('{entities\n (include "human.inc")\n (include "modular_workshop/mw_kept.inc")\n'
                                               ' (include "modular_workshop/mw_gone.inc")\n}', encoding="utf-8")
            self.assertEqual(drop_missing_own_includes(root), ["modular_workshop/mw_gone.inc"])
            text = (folder / "entity.set").read_text(encoding="utf-8")
            self.assertIn("mw_kept.inc", text)
            self.assertIn("human.inc", text)  # other mods' includes are never touched
            self.assertNotIn("mw_gone.inc", text)


class ReserveSquadTests(unittest.TestCase):
    def test_soldiers_of_another_army_keep_their_own_breed(self):
        # French crews (MACE) left over in a British campaign: the Conquest
        # macro would look for mp/eng/..., so the squad spells out mp/fra/...
        from workshop.registration import _register_reserves

        class Index:
            def exists(self, path):
                return path in ("set/breed/mp/eng/early/tankman.set", "set/breed/mp/fra/early/tankman.set")

        own, foreign = _register_reserves(Index(), [
            {"template": "mw_reserve_a(eng)", "breed": "mp/eng/early/tankman", "eids": ["0x1", "0x2"]},
            {"template": "mw_reserve_b(eng)", "breed": "mp/fra/early/tankman", "eids": ["0x3"]}])
        self.assertEqual(own, '("squad_with1types_conquest" side(eng) period(early) min_stage(1) max_stage(99) '
                              'name(mw_reserve_a) c1(tankman:2))')
        squad = parse(foreign).children[0]
        self.assertEqual(squad.head, "mw_reserve_b(eng)")
        self.assertEqual(squad.find("content").args, ["mp/fra/early/tankman:1"])
        with self.assertRaises(RegistrationError):
            _register_reserves(Index(), [{"template": "mw_reserve_c(eng)", "breed": "mp/ger/early/tankman", "eids": ["0x4"]}])
