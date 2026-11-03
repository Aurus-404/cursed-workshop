import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop.dlc import Availability, parse_scan
from workshop.resources import ResourceIndex
from workshop.commander_mg import weapon_catalog, configuration
from workshop.rider_mg import weapon_catalog as rider_weapons
from workshop.catalog_cache import load, stack_key
from workshop.compiler import compile_assembly, CompileError
from workshop.syntax import parse
from tests.test_commander_mg import fixture, archive
from tests.test_geometry import eply


def scan(game, rows=('123',), *, unowned=(), missing=(), archives=('gamelogic.pak',)):
    ids = [*rows, *unowned, *missing]
    return '\n'.join([
        f'[00:00:00] Steam DLC Scan: {len(ids)} total',
        *[f'[00:00:00] Found DLC {i} "DLC" (state=1, owned={int(i not in unowned)}, installed={int(i not in missing)})' for i in ids],
        *[f'[00:00:04] Mount package {(game / "resource" / a).as_posix()} with 10 entries' for a in archives]])


class DlcScanTests(unittest.TestCase):
    def test_owned_and_installed_only_with_matching_game_mounts(self):
        game = Path('game').resolve()
        state = parse_scan(scan(game, unowned=('456',), missing=('789',), archives=('gamelogic.pak', 'dlc1.pak')), game)
        self.assertEqual(state.available, ('123',))
        self.assertTrue(state.verified)
        self.assertEqual(len(state.archives), 1)
        self.assertEqual(parse_scan(scan(game), game / 'other'), Availability())

    def test_partial_new_scan_and_conflicting_rows_never_reuse_old_ownership(self):
        game = Path('game').resolve()
        complete = scan(game)
        self.assertEqual(parse_scan(complete + '\nSteam DLC Scan: 2 total\n', game), Availability())
        self.assertEqual(parse_scan(complete.replace('1 total', '2 total'), game), Availability())
        self.assertEqual(parse_scan(complete + '\nFound DLC 123 "DLC" (state=1, owned=0, installed=1)', game), Availability())
        self.assertEqual(parse_scan('', game), Availability())

    def test_latest_complete_scan_replaces_old_and_normalizes_windows_slashes(self):
        game = Path('game').resolve()
        state = parse_scan(scan(game) + '\n' + scan(game, rows=('456',), unowned=('123',)), game)
        self.assertEqual(state.available, ('456',))
        self.assertEqual(parse_scan(scan(game).replace('/', '\\'), game).available, ('123',))
        self.assertEqual(parse_scan(iter(scan(game).splitlines()), game).available, ('123',))


class DlcWeaponTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / 'game'
        fixture(self.game)
        archive(self.game, 'entity/extra.pak', {
            'inventory/locked_entity/locked_entity.def': '{actor {extension "locked_entity.mdl"}}',
            'inventory/locked_entity/locked_entity.mdl': '{skeleton {bone "basis" {VolumeView "gun.ply"}}}',
            'inventory/locked_entity/gun.ply': eply()})
        self.log = self.root / 'game.log'
        self.log.write_text(scan(self.game), encoding='utf-8')
        patcher = patch('workshop.dlc.game_log_path', return_value=self.log)
        patcher.start(); self.addCleanup(patcher.stop)
        self.index = ResourceIndex(self.game).build()
        self.addCleanup(self.index.close)

    def test_catalog_compilation_reopening_and_revocation_use_same_rule(self):
        self.assertEqual({w['id'] for w in weapon_catalog(self.index)}, {'base_gun', 'paid', 'alias_paid'})
        spec = {'support': 'support:aa', 'parent': 'turret', 'item': 'paid',
                'position': [0, 0, 0], 'rotation': [0, 0, 0], 'yaw': [-90, 90], 'pitch': [-10, 40]}
        result = compile_assembly(self.index, 'hull', 'hull', {'commander_only': True, 'commander_mg': spec}, self.root/'first', 'mw_dlc')
        again = compile_assembly(self.index, 'hull', 'hull', result['metadata']['transform'], self.root/'again', 'mw_again')
        self.assertEqual(again['metadata']['commander_mg']['item'], 'paid')
        self.assertEqual(configuration(self.index, spec)[1]['id'], 'paid')
        self.log.write_text(scan(self.game, rows=(), unowned=('123',)), encoding='utf-8')
        self.assertEqual([w['id'] for w in weapon_catalog(self.index)], ['base_gun'])
        with self.assertRaises(CompileError): configuration(self.index, spec)

    def test_multiple_dlc_ids_are_alternatives_and_missing_log_is_base_only(self):
        archive(self.game, 'gamelogic.pak', {
            'set/registry/dlc.reg': '{dlc {paid "123 456"} {locked_entity 456}}',
            'set/stuff/gun.pattern': '{from {tag "weapon mountable mgun"} {filling "ammo rifle clip" 5}}',
            'set/stuff/base_gun': '{from "pattern gun" {entity "base_gun"}}',
            'set/stuff/paid': '{from "pattern gun" {entity "base_gun"}}',
            'set/stuff/alias_paid': '{from "pattern gun" {entity "locked_entity"}}',
            'set/stuff/rifle.clip.ammo': '{bullet {entity "ammo_box"}}'})
        self.index.build()
        self.assertEqual({w['id'] for w in rider_weapons(self.index)}, {'base_gun', 'paid'})
        self.log.unlink()
        self.assertEqual([w['id'] for w in rider_weapons(self.index)], ['base_gun'])

    def test_owned_dlc_and_mod_weapons_are_offered_but_a_mod_never_unlocks_a_dlc(self):
        archive(self.game, 'dlc1.pak', {
            'set/stuff/paid': '{from "pattern gun" {entity "dlc_gun"}}',
            'entity/inventory/dlc_gun/dlc_gun.def': '{actor {extension "dlc_gun.mdl"}}',
            'entity/inventory/dlc_gun/dlc_gun.mdl': '{skeleton {bone "basis" {VolumeView "gun.ply"}}}',
            'entity/inventory/dlc_gun/gun.ply': eply()})
        self.log.write_text(scan(self.game, archives=('gamelogic.pak','dlc1.pak')), encoding='utf-8')
        self.index.build()
        self.assertEqual(weapon_catalog(self.index, item='paid')[0]['entity'], 'dlc_gun')
        from workshop.geometry import build_preview
        self.assertTrue(build_preview(self.index.mountable_game(), 'dlc_gun', 'full')['meshes'])
        mod = self.root / 'mod'
        archive(mod, 'mod.pak', {'set/stuff/paid': '{from "pattern gun" {entity "dlc_gun"} {range 999}}'})
        self.index.close(); self.index = ResourceIndex(self.game, [mod]).build(); self.addCleanup(self.index.close)
        # An active mod's version is the one the game uses, so it is offered (MACE weapons: AGrievousGuy)...
        self.assertEqual(weapon_catalog(self.index, item='paid')[0]['entity'], 'dlc_gun')
        # ...but a mod never makes a DLC weapon available without the DLC.
        self.log.write_text(scan(self.game, rows=(), missing=('123',)), encoding='utf-8')
        self.index.close(); self.index = ResourceIndex(self.game, [mod]).build(); self.addCleanup(self.index.close)
        self.assertEqual(weapon_catalog(self.index, item='paid'), [])

    def test_cache_changes_on_scan_without_changing_any_game_files(self):
        folder = self.root / 'catalogs'
        before = stack_key(self.index)
        self.assertIn('paid', {w['id'] for w in load(self.index, 'commander_weapons', folder)})
        self.log.write_text(scan(self.game, rows=(), missing=('123',)), encoding='utf-8')
        self.assertNotEqual(before, stack_key(self.index))
        self.assertEqual([w['id'] for w in load(self.index, 'commander_weapons', folder)], ['base_gun'])
        fresh = ResourceIndex(self.game).build()
        try: self.assertEqual([w['id'] for w in load(fresh, 'commander_weapons', folder)], ['base_gun'])
        finally: fresh.close()

    def test_a_scan_changed_during_discovery_cannot_republish_the_old_dlc_list(self):
        import workshop.commander_mg as commander
        original = commander._blocked
        changed = False
        def changed_scan(base):
            nonlocal changed
            result = original(base)
            if not changed:
                changed = True
                self.log.write_text(scan(self.game, rows=(), unowned=('123',)), encoding='utf-8')
                self.index.mountable_game()
            return result
        with patch.object(commander, '_blocked', side_effect=changed_scan):
            self.assertEqual([w['id'] for w in weapon_catalog(self.index)], ['base_gun'])
        self.assertEqual([w['id'] for w in weapon_catalog(self.index)], ['base_gun'])

    def test_disk_cache_cannot_attach_newly_owned_weapons_to_the_unowned_key(self):
        import workshop.commander_mg as commander
        original = commander._blocked
        self.log.write_text(scan(self.game, rows=(), unowned=('123',)), encoding='utf-8')
        changed = False
        def changed_scan(base):
            nonlocal changed
            result = original(base)
            if not changed:
                changed = True
                self.log.write_text(scan(self.game), encoding='utf-8')
                self.index.mountable_game()
            return result
        folder = self.root/'catalogs'
        with patch.object(commander, '_blocked', side_effect=changed_scan):
            self.assertIn('paid', {w['id'] for w in load(self.index, 'commander_weapons', folder)})
        self.log.write_text(scan(self.game, rows=(), unowned=('123',)), encoding='utf-8')
        fresh = ResourceIndex(self.game).build()
        try: self.assertEqual([w['id'] for w in load(fresh, 'commander_weapons', folder)], ['base_gun'])
        finally: fresh.close()


if __name__ == '__main__':
    unittest.main()
