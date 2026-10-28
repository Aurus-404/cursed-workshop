import json
import tempfile
import unittest
from pathlib import Path

from workshop.compiler import compile_assembly
from workshop.crew_visibility import apply_visibility, seat_declarations, CrewVisibilityError
from workshop.syntax import parse, block
from tests.test_crew_placement import resources_with_crew
from tests.test_gun_assembly import Resources


def boarding(resources, path, roles):
    doc = parse(resources.files[path]); root = doc.find('game_entity')
    for role in roles:
        root.append(block('{boarder {door "emit1"}{link "emit1" "' + role +
                          '" {anm "shared"}{forward putoff}{reverse puton}}'
                          '{anm "shared" {forward {end "pose_native"}}'
                          '{reverse {end "emit_tank_1_hold"}}}}'))
    resources.files[path] = doc.render().encode()


class CrewVisibilityTests(unittest.TestCase):
    def test_cyclic_visual_macros_and_includes_fail_closed(self):
        from types import SimpleNamespace
        r = SimpleNamespace(read=lambda path: {'properties/cycle.ext':b'(include "cycle.ext")'}[path])
        for text in ('''{game_entity (define "cycle" {placer {place "gunner" {group "crew"}}} ("cycle")) ("cycle")}''',
                     '''{game_entity (include "/properties/cycle.ext")}'''):
            with self.subTest(text=text), self.assertRaises(CrewVisibilityError):
                seat_declarations(r, parse(text).find('game_entity'))

    def test_visual_macro_wrappers_and_inherited_seats_get_a_final_override(self):
        from types import SimpleNamespace
        files = {'properties/outer.ext': b'''(include "inner.ext")
          (define "custom_wrapper" ("custom_open" place(%who) bone(%seat)))''',
                 'properties/inner.ext': b'''(define "custom_open"
          {placer {place "%place" {group "crew"}{LinkBone "%bone"}{sealable}}}
          {boarder {door "emit"}{link "emit" "%place" {anm "entry"}{forward putoff}{reverse puton}}})
          {placer {place "driver" {group "crew"}{LinkBone "seat_driver"}}}
          {boarder {link "emit" "driver" {forward putoff}{reverse puton}}}'''}
        r = SimpleNamespace(read=lambda path: files[path])
        text = '''{game_entity (include "/properties/outer.ext")
          {placer {place "commander" {group "crew"}}}
          ("custom_wrapper" who(commander) seat(seat_open))
          {Weaponry {place "aa" {gunner "commander"}{charger "commander"}}}}'''
        root = parse(text).find('game_entity'); before = root.render()
        rows = {row['place']: row for row in apply_visibility(root, resources=r)}
        self.assertEqual(root.render(), before, 'catalog discovery must not materialize or alter macros')
        self.assertTrue(rows['commander']['can_hide']); self.assertTrue(rows['driver']['can_hide'])
        self.assertEqual(rows['commander']['bone'], 'seat_open')
        apply_visibility(root, ['commander','driver'], resources=r)
        self.assertIn('("custom_wrapper" who(commander) seat(seat_open))', root.render())
        declarations, _, _ = seat_declarations(r, root)
        for role in ('commander','driver'):
            links = [link for p in declarations if p.args == [role] for link in p.find_all('LinkBone')]
            self.assertEqual(links[-1].args, [''], 'final quoted empty override must follow inherited and macro links')
            self.assertTrue(links[-1].atoms[1].quoted)
        self.assertEqual(root.find('Weaponry').render(),parse(text).find('game_entity').find('Weaponry').render())

    def test_preview_resolves_later_visual_and_empty_overrides_without_duplicate_seats(self):
        from workshop.compiler import seat_markers
        r = resources_with_crew(False)
        path = 'entity/donor/donor.def';doc=parse(r.files[path]);root=doc.find('game_entity')
        root.append(block('(define "external_wrapper" {placer {place "gunner" {LinkBone "commander"}}})'))
        root.append(block('("external_wrapper")'));r.files[path]=doc.render().encode()
        seats = seat_markers(r, 'donor')
        self.assertEqual(next(s for s in seats if s['place']=='gunner')['bone'], 'commander')
        self.assertEqual(len([s for s in seats if s['place']=='gunner']), 1)
        root.append(block('{placer {place "gunner" {LinkBone}}}'));r.files[path]=doc.render().encode()
        self.assertEqual(next(s for s in seat_markers(r,'donor') if s['place']=='gunner')['bone'], '')

    def test_only_visual_link_changes_and_all_repeated_declarations_are_internal(self):
        root = parse('''{game_entity
          {placer {place "gunner" {group "crew"}{LinkBone "seat"}{visor "main"}{sealable}{openForCrush}}}
          {placer {place "gunner" {LinkBone "seat"}}{place "charger" {group "crew"}{LinkBone "loader"}}}
          {boarder {link "emit" "gunner" {anm "shared"}{forward putoff}{reverse puton}}
           {link "emit" "charger" {anm "shared"}}{anm "shared" {forward {end "pose"}}}}
          {Weaponry {place "gun" {gunner "gunner"}{charger "charger"}}}
        }''').find('game_entity')
        before = root.clone()
        rows = apply_visibility(root, ['gunner'])
        self.assertEqual([r['place'] for r in rows if r['hidden']], ['gunner'])
        self.assertEqual(root.find('Weaponry').render(), before.find('Weaponry').render())
        self.assertEqual(root.find('boarder').render(), before.find('boarder').render())
        for old, new in zip([p for pl in before.find_all('placer') for p in pl.find_all('place')],
                            [p for pl in root.find_all('placer') for p in pl.find_all('place')]):
            if new.args == ['gunner']:
                self.assertEqual(new.find('LinkBone').args, [''])
                for n in list(old.find_all('LinkBone')): old.remove(n)
                for n in list(new.find_all('LinkBone')): new.remove(n)
            self.assertEqual([n.render() for n in old.children], [n.render() for n in new.children])

    def test_bad_config_unknown_passenger_and_unsupported_lifecycle_fail(self):
        root = parse('{game_entity {placer {place "gunner" {group "crew"}{LinkBone "seat"}}'
                     '{place "passenger1" {group "passenger"}}}}').find('game_entity')
        for invalid in (True, {}, 'gunner', [None], [''], ['x'*101], ['x']*129):
            with self.subTest(invalid=invalid), self.assertRaises(CrewVisibilityError): apply_visibility(root.clone(), invalid)
        for role in ('unknown', 'passenger1', 'gunner'):
            with self.subTest(role=role), self.assertRaises(CrewVisibilityError): apply_visibility(root.clone(), [role])

    def test_native_internal_seat_needs_no_fake_bone_or_animation(self):
        root = parse('{game_entity {placer {place "driver" {group "crew"}}}}').find('game_entity')
        row = apply_visibility(root, ['driver'])[0]
        self.assertTrue(row['native_internal']); self.assertFalse(row['can_hide'])
        self.assertEqual(root.find('placer').find('place').find('LinkBone').args, [''])
        self.assertFalse(list(root.walk('anm')))

    def test_later_empty_link_is_reported_as_native_internal(self):
        root = parse('{game_entity {placer {place "gunner" {group "crew"}{LinkBone "seat"}}}'
                     '{placer {place "gunner" {LinkBone}}}}').find('game_entity')
        before = root.render()
        row = apply_visibility(root)[0]
        self.assertTrue(row['native_internal']); self.assertFalse(row['can_hide'])
        self.assertEqual(before, root.render())

    def test_defaults_and_recipe_restore_do_not_change_files_or_soldier_count(self):
        resources = resources_with_crew()
        boarding(resources, 'entity/donor/donor.def', ['gunner', 'charger', 'commander'])
        with tempfile.TemporaryDirectory() as tmp:
            results = []
            for label, hidden in [('before', None), ('hidden', ['gunner', 'charger']), ('restored', [])]:
                transform = {'crew_static': True, 'crew_seats': {'gunner': {'position': [1, 2, 3], 'space': 'module'}}}
                if hidden is not None: transform['crew_hidden'] = hidden
                result = compile_assembly(resources, 'hull', 'donor', json.loads(json.dumps(transform)), Path(tmp)/label, 'mw_hide')
                folder = Path(result['output_dir'])/'resource/entity/-vehicle/workshop/mw_hide'
                results.append((result, {p.name: p.read_bytes() for p in folder.iterdir()}))
            self.assertEqual(results[0][1], results[2][1])
            for result, files in results:
                self.assertEqual(result['metadata']['crew_slots'], results[0][0]['metadata']['crew_slots'])
                self.assertEqual(result['metadata']['manual_crew'], results[0][0]['metadata']['manual_crew'])
                doc = parse(files['mw_hide.def']).find('game_entity')
                for head in ('Weaponry', 'boarder'):
                    self.assertEqual([n.render() for n in doc.find_all(head)],
                                     [n.render() for n in parse(results[0][1]['mw_hide.def']).find('game_entity').find_all(head)])
            self.assertEqual([name for name in results[0][1] if results[0][1][name] != results[1][1][name]], ['mw_hide.def'])
            recipe = results[1][0]['metadata']['transform']
            repeated = compile_assembly(resources, 'hull', 'donor', json.loads(json.dumps(recipe)), Path(tmp)/'repeat', 'mw_hide')
            self.assertEqual(repeated['metadata']['crew_visibility'], results[1][0]['metadata']['crew_visibility'])

    def test_colliding_native_roles_use_final_ids_and_preserve_preview_origins(self):
        r = resources_with_crew()
        key = 'entity/hull/hull.def'
        r.files[key] = r.files[key].replace(b'{Chassis', b'{placer {place "gunner" {group "crew"}{LinkBone "driver"}}}{Chassis')
        boarding(r, key, ['gunner']); boarding(r, 'entity/donor/donor.def', ['gunner', 'charger', 'commander'])
        with tempfile.TemporaryDirectory() as tmp:
            result = compile_assembly(r, 'hull', 'donor', {'recipient_mount': '@free', 'crew_hidden': ['gunner2']}, tmp, 'mw_hide')
            rows = {r['place']: r for r in result['metadata']['crew_visibility']}
            self.assertEqual((rows['gunner2']['part'], rows['gunner2']['source_place']), ('turret', 'gunner'))
            self.assertEqual((rows['gunner']['part'], rows['gunner']['source_place']), ('hull', 'gunner'))
            self.assertTrue(rows['gunner2']['hidden']); self.assertFalse(rows['gunner']['hidden'])

    def test_gun_swap_keeps_operator_and_full_vehicle_edit_uses_same_contract(self):
        for full in (False, True):
            r = Resources(); key = 'entity/receiver/receiver.def'
            r.files[key] = r.files[key].replace(b'{place "gunner" {group "crew"}}', b'{place "gunner" {group "crew"}{LinkBone "coax"}}')
            boarding(r, key, ['gunner'])
            transform = {'crew_hidden': ['gunner']}
            transform.update({'commander_only': True} if full else {'gun_swap': {'schema': 1, 'recipient_place': 'main_slot', 'donor_place': 'main_slot'}})
            with self.subTest(full=full), tempfile.TemporaryDirectory() as tmp:
                result = compile_assembly(r, 'receiver', 'receiver' if full else 'towed', transform, tmp, 'mw_hide')
                root = parse((Path(tmp)/'resource'/result['definition_path']).read_bytes()).find('game_entity')
                seat = root.find('placer').find_all('place')[1]
                self.assertEqual(seat.find('LinkBone').args, [''])
                self.assertEqual(root.find('Weaponry').find_all('place')[0].find('gunner').args, ['gunner'])
                self.assertEqual(result['metadata']['crew_slots'], 3)


if __name__ == '__main__': unittest.main()
