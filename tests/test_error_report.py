import os
import tempfile
import time
import unittest
import zipfile
import json
from datetime import datetime
from pathlib import Path

from workshop.error_report import build_report, redact


class ErrorReportTests(unittest.TestCase):
    def resource_index(self):
        from workshop.resources import ResourceIndex
        game=self.root/'native';mod=self.root/'custom'
        (game/'resource').mkdir(parents=True);(mod/'resource').mkdir(parents=True)
        (mod/'mod.info').write_text('{mod {name "Custom"}}')
        files={
          'set/multiplayer/units/roster_conquest.set':'{units (include "conquest/settings.set")(include "conquest/units_custom.set")}',
          'set/multiplayer/units/conquest/settings.set':'(define "vehicle_custom" {tags "native %side"})',
          'set/multiplayer/units/conquest/units_custom.set':'{"prototype" ("vehicle_custom" side(custom))}',
          'set/multiplayer/units/conquest/.presets':'(define "implicit_macro" {cost 17})',
          'set/interaction_entity/entity.set':'{entities (include "effects.inc")}',
          'set/interaction_entity/effects.inc':'{"prototype" {on fire "gun"}}',
          'entity/prototype/prototype.def':'{game_entity {extension "prototype.mdl"}(include "/properties/seat.ext")}',
          'entity/prototype/prototype.mdl':'{skeleton {bone "body" {VolumeView "body.ply"}}}',
          'properties/seat.ext':'(include "seat.ext") (define "seat" {placer})',
          'entity/prototype/body.ply':'BINARY SHOULD NOT BE COPIED'}
        with zipfile.ZipFile(game/'resource/game.pak','w') as archive:
            for name,text in files.items():archive.writestr(name,text)
        override=mod/'resource/set/multiplayer/units/conquest/settings.set'
        override.parent.mkdir(parents=True)
        override.write_text('(define "vehicle_custom" {tags "OVERRIDE %side"}) ; '+str(self.home),encoding='utf8')
        index=ResourceIndex(game,[mod]).build();self.addCleanup(index.close)
        return index

    def test_effective_catalogs_and_selection_include_pak_members_overrides_and_dependencies(self):
        index=self.resource_index()
        (self.workspace/'load-timings.log').write_text('loading took 123 ms')
        result=self.build('Editor selection:\n'+json.dumps({'hull':'prototype'})+'\nRecent errors:',resources=index)
        with zipfile.ZipFile(result['path']) as archive:
            names=archive.namelist();manifest=json.loads(archive.read('resources/manifest.json'))
            prefix='resources/registration/set/multiplayer/units/conquest/'
            self.assertIn('OVERRIDE',archive.read(prefix+'settings.set').decode())
            self.assertIn('%USERPROFILE%',archive.read(prefix+'settings.set').decode())
            self.assertIn(prefix+'units_custom.set',names)
            self.assertIn(prefix+'.presets',names,'implicit macro providers are part of the snapshot too')
            self.assertIn('resources/selection/entity/prototype/prototype.mdl',names)
            self.assertIn('resources/selection/properties/seat.ext',names)
            self.assertFalse(any(n.endswith(('.pak','.ply','.exe')) for n in names))
            self.assertIn('123 ms',archive.read('load-timings.log').decode())
            rows=[r for r in manifest['files'] if r['path'].endswith('settings.set')]
            self.assertEqual(rows[0]['source']['source_id'],'custom')
            self.assertEqual(rows[0]['shadowed_sources'][0]['source_id'],'vanilla')
            self.assertTrue(rows[0]['redacted'])
            self.assertNotEqual(rows[0]['source_sha256'],rows[0]['export_sha256'])
            pak=next(r for r in manifest['files'] if r['path'].endswith('units_custom.set'))
            self.assertEqual(pak['source']['archive_member'],'set/multiplayer/units/conquest/units_custom.set')
            self.assertEqual(len(names),len(set(names)),'cycles and repeated includes must not duplicate ZIP members')

    def test_resource_limits_and_missing_dependencies_are_explicit_and_do_not_lose_the_save(self):
        from unittest.mock import patch
        index=self.resource_index()
        with patch('workshop.report_resources.MAX_FILE_BYTES',80):
            result=self.build('Editor selection: {"hull":"missing_unit"}',resources=index,save=self.save())
        with zipfile.ZipFile(result['path']) as archive:
            manifest=json.loads(archive.read('resources/manifest.json'))
            self.assertTrue(any(r.get('reason')=='file_size_limit' for r in manifest['files']))
            self.assertTrue(any('missing_unit' in e for e in manifest['errors']))
            self.assertIn('save/conquest 12.sav',archive.namelist())
        with patch('workshop.report_resources.MAX_COMPRESSED_BYTES',0):
            result=self.build(resources=index)
        with zipfile.ZipFile(result['path']) as archive:
            manifest=json.loads(archive.read('resources/manifest.json'))
            self.assertTrue(any(r.get('reason')=='compressed_size_limit' for r in manifest['files']))
            self.assertFalse(any(r.get('status')=='included' for r in manifest['files']))
        self.assertLess(result['size'],9_500_000)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "Users" / "Somebody"
        self.documents = self.home / "Documents"
        self.game = self.documents / "My Games" / "gates of hell"
        (self.game / "log" / "minidumps").mkdir(parents=True)
        self.workspace = self.root / "ws"
        self.workspace.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def build(self, diagnostic="diag", **extra):
        return build_report(self.root / "reports", self.game, self.workspace, diagnostic,
                            home=self.home, now=datetime(2026, 10, 20, 12, 0, 0), **extra)

    def save(self, name="conquest 12.sav", maps=0):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("campaign.scn", "{campaign}")
            archive.writestr("status", '{saveinfo {name "Conquest 12"}}')
            for number in range(maps):
                archive.writestr(f"point_{number}.grs", os.urandom(1_000_000), compress_type=zipfile.ZIP_STORED)
        return path

    def test_everything_a_bug_needs_without_the_user_name_or_steam_account(self):
        lines = [f"[00:00:{i % 60:02d}] line {i} {self.home}\\Documents\\x" for i in range(20000)]
        lines += ["[00:00:01] Steam User ID: 76561199050019824", "[00:00:01] Steam Public Name: Somebody's Tank"]
        (self.game / "log" / "game.log").write_text("\n".join(lines) + "\nInteger expected!\n", encoding="utf-8")
        for number in range(6):
            crash = self.game / "log" / "minidumps" / f"crash{number}.mdmp.log"
            crash.write_text(f"crash {number}", encoding="utf-8")
            os.utime(crash, (time.time() + number, time.time() + number))
        (self.game / "log" / "minidumps" / "crash5.mdmp").write_bytes(b"MDMP memory image")
        profile = self.game / "profiles" / "1234"
        profile.mkdir(parents=True)
        (profile / "options.set").write_text('{options\n\t{mods\n\t\t"mod_2905667604:0"\n\t\t"modular_workshop:0"\n\t}\n}\n',
                                             encoding="utf-8")
        (self.workspace / "workshop.log").write_text("\n".join(f"tool {i}" for i in range(9000)), encoding="utf-8")
        (self.workspace / "workshop.log.1").write_text("older tool lines", encoding="utf-8")
        (self.workspace / "crash.log").write_text("Fatal Python error", encoding="utf-8")
        (self.workspace / "settings.json").write_text(f'{{"game_root": "{self.home.as_posix()}/steam"}}', encoding="utf-8")
        (self.workspace / "history").mkdir()
        (self.workspace / "history" / "builds.jsonl").write_text('{"event": "built", "vehicle": "mw_1"}\n', encoding="utf-8")
        (self.workspace / "operations" / "20261020_x").mkdir(parents=True)
        (self.workspace / "operations" / "20261020_x" / "result.json").write_text('{"new_template": "mw_1"}', encoding="utf-8")
        result = self.build(f"Cursed Workshop diagnostic\n{str(self.home).upper()}\\x", save=self.save())
        with zipfile.ZipFile(result["path"]) as archive:
            names = archive.namelist()
            game_log = archive.read("game.log").decode("utf-8")
            everything = "".join(archive.read(name).decode("utf-8") for name in names if not name.startswith("save/"))
            mods = archive.read("mods.txt").decode("utf-8")
            tool = archive.read("workshop.log").decode("utf-8").splitlines()
            self.assertIn("Fatal Python error", archive.read("crash.log").decode("utf-8"))
            with zipfile.ZipFile(archive.open("save/conquest 12.sav")) as save:
                self.assertEqual(save.read("status"), b'{saveinfo {name "Conquest 12"}}')
        self.assertEqual(game_log.count("\n"), 20003)  # the whole log, not its end
        self.assertIn("Integer expected!", game_log)
        self.assertEqual(sorted(n for n in names if n.startswith("crashes/")),
                         [f"crashes/crash{n}.mdmp.log" for n in range(1, 6)])
        self.assertNotIn("crashes/crash5.mdmp", names)  # never the memory image
        self.assertIn("1. mod_2905667604:0", mods)
        self.assertIn("2. modular_workshop:0", mods)
        self.assertEqual(tool[-1], "tool 8999")
        self.assertEqual(len(tool), 9000)  # the whole tool log
        for name in ("workshop.log.1", "settings.json", "history/builds.jsonl", "builds/20261020_x.json"):
            self.assertIn(name, names)
        self.assertIn("Steam User ID: (hidden)", game_log)
        self.assertNotIn("76561199050019824", everything)
        self.assertNotIn("Tank", game_log)
        self.assertNotIn("Somebody", everything)
        self.assertNotIn("SOMEBODY", everything)
        self.assertIn("%USERPROFILE%", game_log)
        self.assertEqual(result["missing"], [])

    def test_missing_logs_still_give_a_report_and_names_do_not_collide(self):
        first = self.build()
        second = self.build()
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(first["missing"], ["game.log", "workshop.log", "settings.json", "history/builds.jsonl",
                                            "save (no campaign opened yet)"])
        with zipfile.ZipFile(first["path"]) as archive:
            self.assertIn("No game profiles found.", archive.read("mods.txt").decode("utf-8"))
            self.assertEqual(archive.read("diagnostic.txt"), b"diag")

    def test_a_save_too_big_for_discord_travels_without_its_battle_maps(self):
        result = self.build(save=self.save("big.sav", maps=8))
        self.assertLess(result["size"], 9_500_000)
        with zipfile.ZipFile(result["path"]) as archive, zipfile.ZipFile(archive.open("save/big (campaign only).sav")) as save:
            self.assertEqual(sorted(save.namelist()), ["campaign.scn", "status"])

    def test_each_workshop_vehicle_comes_with_its_recipe_definition_and_model(self):
        addon = self.root / "mods" / "modular_workshop"
        folder = addon / "resource" / "entity" / "-vehicle" / "workshop" / "mw_abc"
        folder.mkdir(parents=True)
        (addon / "assembly_mw_abc.json").write_text(f'{{"output_dir": "{self.home.as_posix()}/x"}}', encoding="utf-8")
        (folder / "mw_abc.def").write_text('{game_entity {Weaponry {place "gun"}}}', encoding="utf-8")
        (folder / "mw_abc.mdl").write_text('{skeleton}', encoding="utf-8")
        (folder / "mw_abc.ext").write_text('{volume "body"}', encoding="utf-8")
        (addon / "assembly_mw_gone.json").write_text("{}", encoding="utf-8")  # definition missing
        result = build_report(self.root / "reports", self.game, self.workspace, "diag",
                              home=self.home, now=datetime(2026, 10, 21, 9, 0, 0), addon=addon)
        with zipfile.ZipFile(result["path"]) as archive:
            self.assertIn('{place "gun"}', archive.read("vehicles/mw_abc.def").decode("utf-8"))
            self.assertEqual(archive.read("vehicles/mw_abc.mdl"), b"{skeleton}")
            self.assertEqual(archive.read("vehicles/mw_abc.ext"), b'{volume "body"}')
            self.assertIn("%USERPROFILE%", archive.read("vehicles/assembly_mw_abc.json").decode("utf-8"))
            self.assertNotIn("vehicles/mw_gone.def", archive.namelist())

    def test_redaction_covers_every_spelling_of_the_home_folder(self):
        home = Path(r"C:\Users\Somebody")
        text = r"C:\Users\Somebody\a c:/users/somebody/b C:\\Users\\Somebody\\c"
        self.assertEqual(redact(text, home), r"%USERPROFILE%\a %USERPROFILE%/b %USERPROFILE%\\c")


if __name__ == "__main__":
    unittest.main()
