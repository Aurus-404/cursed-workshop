"""Seats the workshop names must not run another seat's hatch script."""
import tempfile
import unittest
from pathlib import Path

from tests.test_compiler import FakeResources
from workshop.compiler import compile_assembly
from workshop.crew_hatches import carry, neutralize
from workshop.resources import ResourceIndex
from workshop.syntax import block, parse

# tank-main.inc 1.067: an occupied "gunner2" is the AA gunner standing in the hatch.
HATCH = '''{"hatch"
  {on "gunner2_hatch_open" {ani_wind "open" end} {ani_play "mgun_aa" +1.0 resume}}
  {on "gunner2_hatch_close" {ani_play "mgun_aa" -1.0 resume}}
  {on "commander_hatch_open"}
  {on "commander_hatch_close"}
}'''


class CarryTests(unittest.TestCase):
    def test_a_seat_keeps_its_origin_through_every_step(self):
        first = carry({}, {'driver', 'gunner', 'gunner2'}, module={'gunner': 'gunner2'}, module_native={'driver'})
        self.assertEqual(first, {'gunner2': 'gunner'})
        # Next step: the chassis' gunner2 is moved again; a new module brings its own gunner.
        second = carry(first, {'driver', 'gunner', 'gunner4', 'gunner5'}, chassis={'gunner2': 'gunner4'},
                       module={'gunner': 'gunner5'}, module_native=set())
        self.assertEqual(second, {'gunner4': 'gunner', 'gunner5': 'gunner'})

    def test_a_native_seat_that_takes_a_freed_name_is_its_own(self):
        # The roof turret that had become gunner2 was removed; a donor's own gunner2 arrives.
        self.assertEqual(carry({'gunner2': 'gunner'}, {'driver', 'gunner', 'gunner2'}, module_native={'gunner2'}), {})

    def test_seats_that_are_gone_or_added_are_reported_as_such(self):
        self.assertEqual(carry({'gunner2': 'gunner'}, {'driver', 'gunner'}), {}, 'a seat left behind needs nothing')
        self.assertEqual(carry({}, {'driver', 'gunner', 'gunner2'}, added=['gunner2']), {'gunner2': None})


class NeutralizeTests(unittest.TestCase):
    def test_renamed_seats_get_empty_hatch_events_only_where_scripts_act(self):
        records = [parse(HATCH).children[0]]
        record = block('{"mw_x tank" {on spawn {inherited}} {on "gunner2_hatch_open" overload {ani_play "mgun_aa" 1}}}')
        written = neutralize(record, {'gunner2': 'gunner', 'gunner5': 'gunner'}, records)
        self.assertEqual(written, ['gunner2_hatch_open', 'gunner2_hatch_close'], 'no script calls gunner5')
        events = {n.args[0]: n for n in record.find_all('on')}
        self.assertEqual(events['gunner2_hatch_open'].render(), '{on "gunner2_hatch_open" overload}')
        self.assertEqual(len([n for n in record.find_all('on') if n.args[0] == 'gunner2_hatch_open']), 1)
        self.assertIn('spawn', events)
        self.assertEqual(neutralize(record, {}, records), [])


class CompiledHatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        r = FakeResources()
        for name in ('hull', 'donor'):
            path = f'entity/{name}/{name}.def'
            r.files[path] = r.files[path].replace(b'{game_entity', b'{game_entity {placer {place "driver" {group "crew"}}{place "gunner" {group "crew"}}}')
        for name, raw in r.files.items():
            p = self.root/'game/resource'/name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        self.index = ResourceIndex(self.root/'game').build()
        self.addCleanup(self.index.close)

    def test_a_roof_turret_records_the_seat_it_renamed_and_its_next_build_keeps_it(self):
        # Two "gunner" seats: one of them becomes "gunner2", the hatch AA gunner's name.
        built = compile_assembly(self.index, 'hull', 'donor', {'recipient_mount': '@free:turret'}, self.root/'roof', 'mw_roof')
        renamed = built['metadata']['renamed_crew']
        self.assertEqual(set(renamed.values()), {'gunner'})
        self.assertTrue(set(renamed) <= set(built['metadata']['crew_roles']))
        self.assertNotIn('gunner', renamed)
        view = self.index.with_overlay(built['output_dir'])
        self.addCleanup(view.close)
        edited = compile_assembly(view, 'mw_roof', 'mw_roof', {'commander_only': True}, self.root/'edit', 'mw_edit')
        self.assertEqual(edited['metadata']['renamed_crew'], renamed, 'a vehicle built from it keeps the record')


if __name__ == '__main__':
    unittest.main()
