import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from workshop import resources
from workshop.resources import ResourceError, ResourceIndex, normalize_path, roots_for_mod_ids


class ResourceIndexTests(unittest.TestCase):
    def test_global_include_libraries_are_parsed_once_and_still_guarded(self):
        from workshop.compiler import _include_defines
        from workshop.syntax import parse
        library=self.game/'resource/properties/shared.ext'
        library.parent.mkdir(parents=True,exist_ok=True)
        library.write_text('(include "more.ext")\n(define "crew" {place "gunner"})')
        (library.parent/'more.ext').write_text('(define "engine" {power 300})')
        self.index=ResourceIndex(self.game).build()
        entity=parse('{game_entity (include "/properties/shared.ext")}')
        first=_include_defines(self.index,entity);second=_include_defines(self.index,entity)
        self.assertEqual(sorted(first),['crew','engine'])
        self.assertIs(first['crew'],second['crew'])
        library.write_text('(define "crew" {place "changed"})')
        self.assertEqual(_include_defines(self.index,entity),{},'a changed file needs a new index, as a read does')

    def test_archive_members_are_read_directly_and_verified(self):
        pak=self.game/'resource/direct.pak'
        pak.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(pak,'w') as archive:
            archive.writestr(zipfile.ZipInfo('set/stored'),b'plain')
            archive.writestr('set/packed','x'*5000,compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr('set/twice','first')
            with self.assertWarns(UserWarning):archive.writestr('set/twice','last')
        self.index=ResourceIndex(self.game).build()
        self.assertEqual(self.index.read('set/stored'),b'plain')
        self.assertEqual(self.index.read('set/packed'),b'x'*5000)
        self.assertEqual(self.index.read('set/twice'),b'last','the last repeated member wins, as in zipfile')
        self.assertEqual(len(self.index.collisions['set/twice']),2)
        self.index.close();self.index=ResourceIndex(self.game).build()
        raw=bytearray(pak.read_bytes());at=raw.index(b'plain');raw[at:at+5]=b'PLAIN'
        stat=pak.stat();pak.write_bytes(bytes(raw));os.utime(pak,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        resources._PAK_LAYOUTS.clear()
        with self.assertRaisesRegex(ResourceError,'CRC'):self.index.read('set/stored')

    def test_archive_directories_kept_on_disk_rebuild_the_same_index_without_reading_archives(self):
        from unittest.mock import patch
        def forget():
            resources._PAK_LAYOUTS.clear();resources._PAK_MEMBERS.clear()
            resources._STOCK_ARCHIVES=None;resources._MOD_ARCHIVES.clear()
        resources.set_directory_cache(self.root/'directories');self.addCleanup(resources.set_directory_cache,None)
        self.addCleanup(forget)
        self.pak(self.game,'base.pak',{'set/a':'one','set/b':'two'})
        mod=self.mod('layer');self.pak(mod,'data.pak',{'set/a':'mod'})
        forget();self.index=ResourceIndex(self.game,[mod]).build()
        forget()
        with patch.object(zipfile,'ZipFile',side_effect=AssertionError('Directories must come from disk')):
            warm=ResourceIndex(self.game,[mod]).build()
            try:
                self.assertEqual(warm.read('set/a'),b'mod');self.assertEqual(warm.read('set/b'),b'two')
                self.assertEqual(warm.sources,self.index.sources)
                self.assertEqual(warm.collisions,self.index.collisions)
            finally:warm.close()
        self.pak(mod,'data.pak',{'set/a':'changed'})
        forget();changed=ResourceIndex(self.game,[mod]).build()
        try:
            self.assertEqual(changed.read('set/a'),b'changed')
            self.assertNotEqual(changed.sources[1]['fingerprint'],self.index.sources[1]['fingerprint'])
        finally:changed.close()
        for item in (self.root/'directories').iterdir():item.write_bytes(b'damaged')
        forget();damaged=ResourceIndex(self.game,[mod]).build()
        try:self.assertEqual(damaged.read('set/a'),b'changed')
        finally:damaged.close()

    def test_mod_archive_reuse_preserves_order_and_removed_overrides(self):
        self.pak(self.game,'base.pak',{'set/shared':'stock'})
        mod=self.mod('layer')
        self.pak(mod,'a.pak',{'set/shared':'a','set/removed':'gone'})
        self.pak(mod,'b.pak',{'set/shared':'b'})
        self.index=ResourceIndex(self.game,[mod]).build()
        warm=ResourceIndex(self.game,[mod]).build()
        try:
            self.assertEqual(self.index.collisions,warm.collisions)
            self.assertIs(self.index.entries['set/shared'],warm.entries['set/shared'])
            self.assertEqual(self.index.sources,warm.sources)
        finally:warm.close()
        (mod/'resource/a.pak').unlink()
        changed=ResourceIndex(self.game,[mod]).build()
        try:
            self.assertFalse(changed.exists('set/removed'))
            self.assertEqual(len(changed.collisions['set/shared']),2)
        finally:changed.close()

    def test_reused_archive_entries_preserve_collisions_and_see_external_changes(self):
        self.pak(self.game,'a.pak',{'set/shared':'first'})
        self.pak(self.game,'b.pak',{'set/shared':'second'})
        self.index=ResourceIndex(self.game).build()
        first=self.index.entries['set/shared']
        loose=self.game/'resource/set/shared'
        loose.parent.mkdir(exist_ok=True)
        loose.write_bytes(b'override')
        warm=ResourceIndex(self.game).build()
        try:
            self.assertEqual(warm.read('set/shared'),b'override')
            self.assertIs(warm.collisions['set/shared'][-2],first)
            self.assertEqual(len(self.index.collisions['set/shared']),2)
        finally:warm.close()
        loose.unlink()
        self.pak(self.game,'b.pak',{'set/shared':'replacement'})
        changed=ResourceIndex(self.game).build()
        try:
            self.assertEqual(changed.read('set/shared'),b'replacement')
            self.assertNotEqual(changed.sources[0]['fingerprint'],self.index.sources[0]['fingerprint'])
        finally:changed.close()

    def test_generated_output_reuses_written_bytes_but_rejects_later_file_changes(self):
        from workshop.compiler import _write_generated
        from unittest.mock import patch
        self.pak(self.game,'base.pak',{'set/base':'native'})
        self.index=ResourceIndex(self.game).build();overlay=self.mod('compiled')
        path=overlay/'resource/entity/-vehicle/workshop/test/part.ply';path.parent.mkdir(parents=True)
        _write_generated(self.index,path,b'emitted bytes')
        view=self.index.with_overlay(overlay)
        try:
            with patch.object(Path,'open',side_effect=AssertionError('Compiler output read back from disk')):
                self.assertEqual(view.read('entity/-vehicle/workshop/test/part.ply'),b'emitted bytes')
            path.write_bytes(b'changed externally')
            with self.assertRaises(ResourceError):view.read('entity/-vehicle/workshop/test/part.ply')
        finally:view.close()

    def test_compiled_stages_reuse_only_identical_native_include_graphs(self):
        from workshop.registration import _load_include_graph
        from unittest.mock import patch
        path='entity/-vehicle/workshop/native/camera.inc'
        self.pak(self.game,'base.pak',{'set/native.set':'{entities {include "/'+path+'"}}',path:'{native {zoom 1}}'})
        unchanged=self.mod('unchanged');changed=self.mod('changed')
        self.pak(unchanged,'data.pak',{'entity/-vehicle/workshop/other/part.mdl':'{}'})
        self.pak(changed,'data.pak',{path:'{native {zoom 2}}'})
        own=self.mod('modular_workshop')
        self.pak(own,'data.pak',{'set/tp_control.set':'{third_person_control {vehicle}}'})
        for mods in ([],[own]):
            with self.subTest(own_addon=bool(mods)):
                self.index=ResourceIndex(self.game,mods).build()
                first=self.index.with_overlay(unchanged);second=self.index.with_overlay(unchanged)
                override=self.index.with_overlay(changed)
                try:
                    records,_=_load_include_graph(first,'set/native.set')
                    with patch('workshop.registration.parse',side_effect=AssertionError('Native includes parsed twice')):
                        repeated,_=_load_include_graph(second,'set/native.set')
                        native,_=_load_include_graph(self.index,'set/native.set')
                    self.assertEqual(records,repeated);self.assertEqual(records,native)
                    altered,_=_load_include_graph(override,'set/native.set')
                    self.assertEqual(altered[0][1].find('zoom').args,['2'])
                    self.assertEqual(native[0][1].find('zoom').args,['1'])
                finally:
                    for view in (first,second,override):view.close()
                    self.index.close()

    def test_fast_overlay_keeps_parent_collisions_and_entity_lists_unchanged(self):
        first = self.mod('first'); overlay = self.mod('overlay')
        self.pak(self.game,'base.pak',{'set/shared':'base','entity/test/unit.def':'{game_entity}'})
        self.pak(first,'data.pak',{'set/shared':'first'})
        self.pak(overlay,'data.pak',{'set/shared':'overlay','entity/test/unit.def':'{game_entity {mass 1}}',
                                    'entity/other/unit.def':'{game_entity {mass 2}}'})
        self.index = ResourceIndex(self.game,[first]).build()
        collisions = list(self.index.collisions['set/shared']); paths = list(self.index._entity_by_id['unit'])
        view = self.index.with_overlay(overlay); fresh = ResourceIndex(self.game,[first,overlay]).build()
        try:
            self.assertEqual(view._entity_by_id, fresh._entity_by_id)
            self.assertEqual(self.index.collisions['set/shared'], collisions)
            self.assertEqual(self.index._entity_by_id['unit'], paths)
            self.assertEqual(view.read_text('set/shared'), 'overlay')
            self.assertEqual(self.index.read_text('set/shared'), 'first')
        finally: view.close(); fresh.close()

    def test_resource_byte_cache_evicts_old_data_at_its_memory_bound(self):
        from workshop.resources import _ReadCache
        from unittest.mock import patch
        self.pak(self.game, 'base.pak', {'set/a': 'aaa', 'set/b': 'bbb'})
        self.index = ResourceIndex(self.game).build(); self.index._read_cache = _ReadCache(budget=4, max_item=4)
        calls = []; original = resources._read_member
        def counted(pak, member, layout):
            calls.append(member); return original(pak, member, layout)
        with patch.object(resources, '_read_member', counted):
            for path in ('set/a', 'set/b', 'set/b', 'set/a'): self.index.read(path)
        self.assertEqual(calls, ['set/a', 'set/b', 'set/a'])
        self.assertLessEqual(self.index._read_cache.bytes, 4)
        empty = _ReadCache(max_entries=1)
        empty.put('first', b'', None); empty.put('second', b'', None)
        self.assertIsNone(empty.get('first')); self.assertEqual(len(empty.items), 1)

    def test_compiled_preview_overlays_share_only_unchanged_game_catalogs(self):
        from workshop.rider_mg import catalog
        from unittest.mock import patch
        self.pak(self.game, 'base.pak', {'entity/test/native.def': '{game_entity}'})
        self.index = ResourceIndex(self.game).build()
        self.index._rider_catalog = {'supports': [{'id': 'prepared'}], 'poses': []}
        generated = self.mod('compiled')
        (generated/'resource/entity/-vehicle/workshop/mw_preview').mkdir(parents=True)
        (generated/'resource/entity/-vehicle/workshop/mw_preview/mw_preview.def').write_text('{game_entity}')
        view = self.index.with_overlay(generated)
        try:
            with patch('workshop.rider_mg._discover', side_effect=AssertionError('Must reuse the prepared catalog')):
                self.assertIs(catalog(view), self.index._rider_catalog)
            self.assertIs(view.base_game(), self.index.base_game())
            self.assertEqual(view.read_text('entity/-vehicle/workshop/mw_preview/mw_preview.def'), '{game_entity}')
        finally: view.close()
        self.assertEqual(self.index.read_text('entity/test/native.def'), '{game_entity}')

    def test_an_overlay_that_changes_native_inputs_keeps_its_own_catalog_scope(self):
        self.pak(self.game, 'base.pak', {'entity/test/native.def': '{game_entity}'})
        self.index = ResourceIndex(self.game).build()
        changed = self.mod('changed')
        self.pak(changed, 'data.pak', {'entity/test/native.def': '{game_entity {mass 1}}'})
        view = self.index.with_overlay(changed)
        try:
            self.assertIs(view.catalog_source(), view)
            self.assertIsNot(view.base_game(), self.index.base_game())
        finally: view.close()

    def test_catalog_scope_is_not_shared_when_a_native_mod_is_excluded(self):
        mod = self.mod('native_mod'); generated = self.mod('compiled')
        self.pak(mod, 'data.pak', {'entity/test/native.def': '{game_entity}'})
        self.pak(generated, 'data.pak', {'entity/-vehicle/workshop/mw_p/mw_p.def': '{game_entity}'})
        self.index = ResourceIndex(self.game, [mod]).build()
        view = self.index.with_overlay(generated, exclude_roots=[mod])
        try: self.assertIs(view.catalog_source(), view)
        finally: view.close()

    def test_unchanged_archive_bytes_are_read_once_across_independent_views(self):
        from unittest.mock import patch
        self.pak(self.game, 'base.pak', {'set/data': 'original'})
        self.index = ResourceIndex(self.game).build(); view = self.index.with_overlay(self.mod('overlay'))
        calls = []; original = resources._read_member
        def counted(pak, member, layout):
            calls.append(member); return original(pak, member, layout)
        try:
            with patch.object(resources, '_read_member', counted):
                self.assertEqual(self.index.read('set/data'), b'original')
                self.assertEqual(self.index.read('set/data'), b'original')
                self.assertEqual(view.read('set/data'), b'original')
            self.assertEqual(calls, ['set/data'])
            with self.assertRaisesRegex(ResourceError, 'read limit'): view.read_bytes('set/data', max_bytes=1)
        finally: view.close()

    def test_cached_bytes_do_not_hide_changed_loose_files_or_archives(self):
        loose = self.game/'resource/properties/tank.ext'
        self.pak(self.game, 'base.pak', {'set/data': 'original'})
        self.index = ResourceIndex(self.game).build()
        self.index.read('properties/tank.ext'); self.index.read('set/data')
        loose.write_text('changed loose resource')
        with self.assertRaisesRegex(ResourceError, 'changed'): self.index.read('properties/tank.ext')
        self.pak(self.game, 'base.pak', {'set/data': 'replacement'})
        with self.assertRaisesRegex(ResourceError, 'changed'): self.index.read('set/data')

    def test_resource_less_mods_keep_metadata_localizations_and_active_precedence(self):
        utility=self.root/'utility';utility.mkdir()
        (utility/'mod.info').write_text('{mod {name "External tool"}}')
        translation=self.root/'translation';translation.mkdir()
        (translation/'mod.info').write_text('{mod {name "Translation"}}')
        text=translation/'localizations/default/interface/text/desc/test.pot'
        text.parent.mkdir(parents=True);text.write_text('msgctxt "desc/entity/test"\nmsgid "Translated label"\nmsgstr ""\n')
        content=self.mod('content')
        self.pak(self.game,'base.pak',{'set/common':'base'})
        self.pak(content,'data.pak',{'set/common':'override'})
        self.index=ResourceIndex(self.game,[utility,translation,content]).build()
        self.assertEqual([s['id'] for s in self.index.sources][1:],['utility','translation','content'])
        self.assertEqual(self.index.read_text('set/common'),'override')
        self.assertIn('Translated label',self.index.read_text('localizations/default/interface/text/desc/test.pot'))
        self.assertEqual(sum('has no resource folder' in warning for warning in self.index.warnings),2)
        with self.assertRaises(FileNotFoundError):self.index.resolve_entity('missing_vehicle')

    def test_missing_or_unrecognized_mod_root_still_fails(self):
        bare=self.root/'bare';bare.mkdir()
        for root,message in [(self.root/'not_installed','Active mod folder missing'),(bare,'no mod.info')]:
            with self.subTest(root=root),self.assertRaisesRegex(ResourceError,message):
                ResourceIndex(self.game,[root]).build()

    def test_resource_file_cannot_be_mistaken_for_resource_less_mod(self):
        root=self.root/'malformed';root.mkdir()
        (root/'mod.info').write_text('{mod {name "Malformed"}}')
        (root/'resource').write_text('not a directory')
        with self.assertRaisesRegex(ResourceError,'resource path is not a folder'):
            ResourceIndex(self.game,[root]).build()

    def test_overlay_matches_fresh_index_and_owns_its_archive_handles(self):
        first,old,new=self.mod('first'),self.mod('modular_workshop'),self.mod('staging')
        self.pak(self.game,'base.pak',{'set/common':'base','entity/test/native.def':'{game_entity}'})
        self.pak(first,'data.pak',{'set/common':'mod','set/retained':'retained'})
        self.pak(old,'data.pak',{'set/common':'old','set/obsolete':'must disappear'})
        self.pak(new,'data.pak',{'set/new':'new','entity/test/native.def':'{game_entity {mass 1}}'})
        self.index=ResourceIndex(self.game,[first,old]).build()
        view=self.index.with_overlay(new,exclude_roots=[old])
        fresh=ResourceIndex(self.game,[first,new]).build()
        try:
            self.assertEqual(set(view.entries),set(fresh.entries))
            for path in fresh.entries:
                self.assertEqual(view.read_bytes(path),fresh.read_bytes(path))
                self.assertEqual(view.resolve(path).source_id,fresh.resolve(path).source_id)
            self.assertEqual(view.resolve_entity('native'),fresh.resolve_entity('native'))
            self.assertFalse(view.exists('set/obsolete'))
            self.assertEqual(self.index.read_text('set/common'),'old')
            view.close()
            self.assertEqual(self.index.read_text('set/common'),'old')
            self.assertEqual(view.read_text('set/retained'),'retained')
            self.index.close()
            self.assertEqual(view.read_text('set/retained'),'retained')
        finally:view.close();fresh.close()

    def test_overlay_detects_changed_loose_resources_and_never_rescans_base_archives(self):
        mod=self.mod('new');(mod/'resource/set').mkdir();(mod/'resource/set/new').write_text('fresh')
        self.pak(self.game,'base.pak',{'set/archived':'original'})
        self.index=ResourceIndex(self.game).build()
        from unittest.mock import patch
        original=zipfile.ZipFile
        def only_overlay(path,*args,**kwargs):
            self.assertNotEqual(Path(path),self.game/'resource/base.pak')
            return original(path,*args,**kwargs)
        with patch('workshop.resources.zipfile.ZipFile',side_effect=only_overlay):view=self.index.with_overlay(mod)
        try:
            (self.game/'resource/properties/tank.ext').write_text('{game_entity {mass 10}}')
            with self.assertRaisesRegex(ResourceError,'changed'):view.read_bytes('properties/tank.ext')
            self.assertEqual(view.read_text('set/archived'),'original')
        finally:view.close()

    def test_weapon_labels_follow_effective_gettext_and_reset_on_rebuild(self):
        path=self.game/'localizations/default/interface/text/desc/weapon.pot';path.parent.mkdir(parents=True)
        path.write_text('msgctxt "desc/weapon/test_gun"\nmsgid "Native 75 mm"\nmsgstr ""\n')
        self.index=ResourceIndex(self.game).build()
        self.assertEqual(self.index.weapon_label('test_gun'),'Native 75 mm')
        self.assertEqual(self.index.weapon_label('unknown_gun'),'unknown gun')
        path.write_text('msgctxt "desc/weapon/test_gun"\nmsgid "Changed 75 mm"\nmsgstr ""\n')
        self.index.build();self.assertEqual(self.index.weapon_label('test_gun'),'Changed 75 mm')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = self.root / "steamapps/common/game"
        (self.game / "resource/entity").mkdir(parents=True)
        (self.game / "resource/properties").mkdir()
        (self.game / "resource/properties/tank.ext").write_text("{game_entity}")
        self.index = None

    def tearDown(self):
        if self.index:
            self.index.close()
        self.temp.cleanup()

    def pak(self, root, name, entries):
        target = root / "resource" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w") as archive:
            for key, value in entries.items():
                archive.writestr(key, value)

    def mod(self, name):
        root = self.root / name
        (root / "resource").mkdir(parents=True)
        (root / "mod.info").write_text('{mod {name "' + name + '"} {version "1"}}')
        return root

    def test_nested_archive_mount_case_insensitive_and_loose_override(self):
        self.pak(self.game, "entity/-vehicle.pak", {"-vehicle/Tank/Example/Example.def": "archive",
                                                  "-vehicle/Tank/Example/mesh.ply": b"mesh"})
        loose = self.game / "resource/entity/-vehicle/tank/example/EXAMPLE.def"
        loose.parent.mkdir(parents=True)
        loose.write_text("loose")
        self.index = ResourceIndex(self.game).build()
        self.assertEqual(self.index.read_text("/RESOURCE/entity/-vehicle/TANK/EXAMPLE/example.def"), "loose")
        self.assertEqual(self.index.read("entity/-vehicle/tank/example/MESH.ply"), b"mesh")
        self.assertEqual(len(self.index.collisions), 1)
        self.assertIsNone(self.index.resolve("entity/-vehicle/tank/example/mesh.ply").physical_path)

    def test_explicit_mod_order_is_authority(self):
        first, second = self.mod("first"), self.mod("second")
        self.pak(self.game, "gamelogic.pak", {"set/test.inc": "game"})
        self.pak(first, "gamelogic.pak", {"set/test.inc": "first"})
        self.pak(second, "gamelogic.pak", {"set/test.inc": "second"})
        self.index = ResourceIndex(self.game, [second, first]).build()
        self.assertEqual(self.index.read_text("SET/TEST.INC"), "first")
        self.assertEqual(self.index.resolve("set/test.inc").origin, "first")
        self.assertEqual(self.index.sources[-1]["version"], "1")

    def test_archive_traversal_and_read_limits(self):
        self.pak(self.game, "gamelogic.pak", {"../../outside": "bad", "set/huge": "12345"})
        self.index = ResourceIndex(self.game).build()
        self.assertEqual(len(self.index.entries), 2)  # valid entry + loose tank.ext
        with self.assertRaises(ResourceError):
            self.index.read_bytes("set/huge", max_bytes=4)
        for path in ("../a", "C:/a", "//server/share", "a/../../b", "x\x00"):
            with self.subTest(path=path), self.assertRaises(ResourceError):
                normalize_path(path)

    def test_entity_include_and_model_resolution(self):
        self.pak(self.game, "entity/-vehicle.pak", {
            "-vehicle/test/a/a.def": '{game_entity (include "/properties/tank.ext") (include "a.ext") {model "a_shared"} {targetclass "armoredcar"}}',
            "-vehicle/test/a/a_shared.mdl": "{Skeleton}",
            "-vehicle/test/a/a.ext": "{game_entity}",
        })
        self.index = ResourceIndex(self.game).build()
        row = self.index.resolve_entity("A(ger)")
        self.assertEqual(row["model_path"], "entity/-vehicle/test/a/a_shared.mdl")
        self.assertEqual(row["extension_path"], "entity/-vehicle/test/a/a.ext")
        self.assertEqual(row["kind"], "armoredcar")
        self.assertEqual(len(self.index.list_units()), 1)

    def test_destruction_states_hidden_from_catalog(self):
        self.pak(self.game, "entity/-vehicle.pak", {
            "-vehicle/tank/pz/pz.def": "{game_entity}", "-vehicle/tank/pz/pz_x.def": "{game_entity}",
            "-vehicle/tank/x/PZ_XX.def": "{game_entity}", "-vehicle/tank/max/max.def": "{game_entity}",
            "-vehicle/tank/lone_x/lone_x.def": "{game_entity}"})
        self.index = ResourceIndex(self.game).build()
        ids = [row["id"] for row in self.index.list_units()]
        # A wreck needs its intact entity; "lone_x" has none, so it stays.
        self.assertEqual(sorted(i.casefold() for i in ids), ["lone_x", "max", "pz"])
        self.assertTrue(self.index.is_wreck("Pz_XX"))
        self.assertFalse(self.index.is_wreck("max"))

    def test_bare_block_definition_with_local_include(self):
        # MG lafettes: {(include "lafette.inc") ...} with the model and props in the .inc.
        self.pak(self.game, "entity/-vehicle.pak", {
            "-vehicle/test/lafette/mg34_lafette.def": '{\n\t(include "lafette.inc")\n\t{props "sandbag"}\n}',
            "-vehicle/test/lafette/lafette.inc": '(include "/properties/tank.ext")\n{extension "mg42_lafette.mdl"}\n{props "carried" "cannon"}',
            "-vehicle/test/lafette/mg42_lafette.mdl": "{Skeleton}",
        })
        self.index = ResourceIndex(self.game).build()
        row = self.index.resolve_entity("mg34_lafette")
        self.assertEqual(row["model_path"], "entity/-vehicle/test/lafette/mg42_lafette.mdl")
        self.assertEqual(row["props"], ["carried", "cannon", "sandbag"])
        self.assertEqual(row["extension_paths"], ["properties/tank.ext"])
        self.assertIn("entity/-vehicle/test/lafette/lafette.inc", row["files"])

    def test_included_inc_names_its_model_relative_to_its_own_folder(self):
        # bedford_oyd_boys.def includes ../bedford_oyd/bedford_oyd.inc, whose model is "bedford_oyd.mdl".
        self.pak(self.game, "entity/-vehicle.pak", {
            "-vehicle/car/truck_boys/truck_boys.def": '{game_entity (include "../truck/truck.inc")}',
            "-vehicle/car/truck/truck.inc": '{extension "truck.mdl"}',
            "-vehicle/car/truck/truck.mdl": "{Skeleton}",
        })
        self.index = ResourceIndex(self.game).build()
        self.assertEqual(self.index.resolve_entity("truck_boys")["model_path"], "entity/-vehicle/car/truck/truck.mdl")

    def test_ambiguous_entity_ids_fail_instead_of_pick_arbitrarily(self):
        self.pak(self.game, "entity/-vehicle.pak", {"-vehicle/a/foo.def": "{game_entity {mass 1}}",
                                                  "-vehicle/b/foo.def": "{game_entity {mass 2}}"})
        self.index = ResourceIndex(self.game).build()
        with self.assertRaises(ResourceError):
            self.index.resolve_entity("foo")
        self.assertTrue(all(row["ambiguous"] for row in self.index.list_units()))

    def test_byte_identical_copies_and_a_vehicles_own_wreck_folder_resolve(self):
        from workshop.compiler import _state_template
        # MACE ships the Chi-Ha wreck twice; the Chi-Ha itself lives in chi_ha_early.
        self.pak(self.game, "entity/-vehicle.pak", {
            "-vehicle/tank/chi_ha_early/chi_ha.def": "{game_entity}",
            "-vehicle/tank/x/chi_ha_early_x/chi_ha_x.def": "{game_entity {mass 1}}",
            "-vehicle/tank/x/chi_ha_x/chi_ha_x.def": "{game_entity {mass 2}}",
            "-vehicle/a/copy.def": "{game_entity}", "-vehicle/test/copy.def": "{game_entity}"})
        self.index = ResourceIndex(self.game).build()
        self.assertTrue(self.index.resolve_entity("copy")["definition_path"].endswith("copy.def"))
        with self.assertRaises(ResourceError):
            self.index.resolve_entity("chi_ha_x")
        state = _state_template(self.index, "chi_ha", "x")
        self.assertEqual(self.index.resolve_entity(state)["definition_path"], "entity/-vehicle/tank/x/chi_ha_early_x/chi_ha_x.def")

    def test_namespaced_mod_file_and_gettext_label(self):
        mod = self.mod("MACE")
        self.pak(mod, "entity/-vehicle.pak", {"-vehicle/+germany/light/pz38/pz38.def": "{game_entity}",
                                               "-vehicle/+germany/light/pz38/pz38.mdl": "{Skeleton}"})
        (mod / "localizations").mkdir()
        with zipfile.ZipFile(mod / "localizations/default.pak", "w") as archive:
            archive.writestr("default/interface/text/desc/vehicles.pot", 'msgctxt "desc/entity/pz38"\nmsgid "Panzer 38"\nmsgstr ""\n')
        self.index = ResourceIndex(self.game, [mod]).build()
        self.assertEqual(self.index.resolve_entity("pz38")["label"], "Panzer 38")
        self.assertEqual(self.index.list_units()[0]["source_id"], "MACE")

    def test_dlc_is_after_base_and_order_is_numeric(self):
        self.pak(self.game, "gamelogic.pak", {"set/version.inc": "base"})
        self.pak(self.game, "dlc9.pak", {"set/version.inc": "nine"})
        self.pak(self.game, "_dlc10.pak", {"set/version.inc": "ten"})
        self.index = ResourceIndex(self.game).build()
        self.assertEqual(self.index.read_text("set/version.inc"), "ten")

    def test_safe_sibling_includes_and_changed_loose_file(self):
        self.pak(self.game, "gamelogic.pak", {"set/common.inc": "common"})
        self.index = ResourceIndex(self.game).build()
        self.assertEqual(self.index.resolve_reference("../common.inc", "set/tank/main.inc"), "set/common.inc")
        with self.assertRaises(ResourceError):
            self.index.resolve_reference("../../../../outside", "set/main.inc")
        (self.game / "resource/properties/tank.ext").write_text("changed")
        with self.assertRaises(ResourceError):
            self.index.read_text("properties/tank.ext")


if __name__ == "__main__":
    unittest.main()


class FastIndexTests(unittest.TestCase):
    def test_pak_member_cache_rereads_a_changed_archive(self):
        import os, time, zipfile, tempfile
        from pathlib import Path
        from workshop.resources import _pak_members
        with tempfile.TemporaryDirectory() as temp:
            pak = Path(temp) / "a.pak"
            with zipfile.ZipFile(pak, "w") as archive:
                archive.writestr("entity/one.def", b"1")
            self.assertEqual([row[0] for row in _pak_members(pak, "")], ["entity/one.def"])
            time.sleep(0.01)
            with zipfile.ZipFile(pak, "w") as archive:
                archive.writestr("entity/one.def", b"1")
                archive.writestr("entity/two.def", b"22")
            later = time.time() + 5
            os.utime(pak, (later, later))
            self.assertEqual(sorted(row[0] for row in _pak_members(pak, "")), ["entity/one.def", "entity/two.def"])

    def test_loose_files_skip_a_linked_folder_outside_the_source(self):
        import tempfile
        from pathlib import Path
        from workshop.resources import _loose_files
        with tempfile.TemporaryDirectory() as temp:
            root, outside = Path(temp) / "mod", Path(temp) / "outside"
            (root / "resource" / "entity").mkdir(parents=True)
            outside.mkdir()
            (root / "resource" / "entity" / "a.def").write_bytes(b"a")
            (outside / "secret.def").write_bytes(b"s")
            try:
                import _winapi
                _winapi.CreateJunction(str(outside), str(root / "resource" / "linked"))
            except (ImportError, OSError):
                self.skipTest("Directory junctions are not available")
            warnings = []
            found = _loose_files(root / "resource", root, warnings)
            self.assertEqual([path.name for path in found], ["a.def"])
            self.assertEqual(len(warnings), 1)


class DisabledModTests(unittest.TestCase):
    """Mods the game switches off by version must not feed generated files
    (Conquest Enhanced v2 for 1.065 under game 1.067: the addon copied its
    entity.set includes and the game closed at start)."""

    def test_disabled_mod_does_not_require_a_resource_directory(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            game=Path(temp)/'game';(game/'resource').mkdir(parents=True)
            old=Path(temp)/'old';old.mkdir()
            (old/'mod.info').write_text('{mod {name "Old tool"} {maxGameVersion "1.065.0"}}')
            with patch('workshop.resources.game_version',return_value=(1,67,0)):
                index=ResourceIndex(game,[old]).build()
            try:
                self.assertEqual(len(index.sources),1)
                self.assertEqual(index.disabled_mods[0]['name'],'Old tool')
            finally:index.close()

    def test_version_ranges_like_the_game(self):
        import tempfile
        from pathlib import Path
        from workshop.resources import disabled_reason
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def mod(low, high):
                (root / "mod.info").write_text('{mod {name "M"} {minGameVersion "%s"} {maxGameVersion "%s"}}' % (low, high))
                return disabled_reason(root, (1, 67, 0))
            self.assertIsNone(mod("any", "any"))
            self.assertIsNone(mod("Any", "1.067"))
            self.assertIsNone(mod("1.067.0", "1.067.0"))
            self.assertIn("1.065", mod("1.065.0", "1.065.0"))
            self.assertIn("or newer", mod("1.068", "any"))
            (root / "mod.info").write_text('{mod {name "M"} {maxGameVersion "1.065.0"}}')
            self.assertIsNone(disabled_reason(root, None))  # unknown game version: never disable

    def test_index_skips_a_disabled_mod(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from workshop.resources import ResourceIndex
        with tempfile.TemporaryDirectory() as temp:
            game, old = Path(temp) / "game", Path(temp) / "old_mod"
            (game / "resource" / "set").mkdir(parents=True)
            (old / "resource" / "set").mkdir(parents=True)
            (old / "resource" / "set" / "entity.set").write_text("{include \"dummy_ce.inc\"}")
            (old / "mod.info").write_text('{mod {name "Old"} {maxGameVersion "1.065.0"}}')
            with patch("workshop.resources.game_version", return_value=(1, 67, 0)):
                index = ResourceIndex(game, [old]).build()
            self.assertNotIn("set/entity.set", index.entries)
            self.assertEqual([m["name"] for m in index.disabled_mods], ["Old"])
