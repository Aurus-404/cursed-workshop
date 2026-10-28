import hashlib
import json
import tempfile
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop.campaign import load_campaign, plan_assembly
from workshop.transactions import (TransactionError, assert_game_closed,
                                   commit_transaction, prepare_transaction,
                                   rollback_transaction, verify_transaction)
try:
    from .test_campaign import PROFILE, make_save
except ImportError:
    from test_campaign import PROFILE, make_save


class TransactionTests(unittest.TestCase):
    def updated_destination(self):
        first=self.prepare();published=commit_transaction(first['journal_path'])
        destination=Path(published['addon_destination'])
        old=(destination/'resource/entity/mw_hybrid.def').read_bytes()
        self.files['resource/entity/mw_hybrid.def']=b'new generation'
        return self.prepare(),destination,old

    def test_previous_addon_renames_on_same_volume_and_records_every_install_phase(self):
        from contextlib import contextmanager
        prepared,destination,old=self.updated_destination();phases=[]
        @contextmanager
        def phase(label):phases.append(label);yield
        with patch('workshop.transactions.shutil.copytree',side_effect=AssertionError('No backup copy on one volume')):
            installed=commit_transaction(prepared['journal_path'],addon_destination=destination,phase=phase)
        self.assertEqual(installed['archive_method'],'rename')
        self.assertEqual((Path(installed['archived_backup'])/'resource/entity/mw_hybrid.def').read_bytes(),old)
        for part in ('verify prepared transaction','verify previous addon','copy new addon','verify publication copy','recheck previous addon','archive previous addon'):
            self.assertIn('Install: '+part,phases)
        rollback_transaction(prepared['journal_path'])
        self.assertEqual((destination/'resource/entity/mw_hybrid.def').read_bytes(),old)

    def test_cross_volume_backup_falls_back_to_verified_copy(self):
        import errno
        prepared,destination,old=self.updated_destination();original=os.replace
        def replace(source,target):
            if Path(target).name=='previous_addon':raise OSError(errno.EXDEV,'Different volume')
            return original(source,target)
        with patch('workshop.transactions.os.replace',side_effect=replace):
            installed=commit_transaction(prepared['journal_path'],addon_destination=destination)
        self.assertEqual(installed['archive_method'],'copy')
        self.assertFalse(Path(installed['backup_addon']).exists())
        rollback_transaction(prepared['journal_path'])
        self.assertEqual((destination/'resource/entity/mw_hybrid.def').read_bytes(),old)

    def test_interruption_after_backup_rename_is_recoverable_from_persisted_intent(self):
        prepared,destination,old=self.updated_destination();original=os.replace
        def interrupt(source,target):
            result=original(source,target)
            if Path(target).name=='previous_addon':raise KeyboardInterrupt('Power loss after rename')
            return result
        with patch('workshop.transactions.os.replace',side_effect=interrupt),self.assertRaises(KeyboardInterrupt):
            commit_transaction(prepared['journal_path'],addon_destination=destination)
        journal=json.loads(Path(prepared['journal_path']).read_text())
        self.assertTrue(Path(journal['publication']['archived_backup']).is_dir())
        rollback_transaction(prepared['journal_path'])
        self.assertEqual((destination/'resource/entity/mw_hybrid.def').read_bytes(),old)

    def test_failed_cross_volume_copy_keeps_original_backup_usable_for_rollback(self):
        import errno
        prepared,destination,old=self.updated_destination();original=os.replace
        def replace(source,target):
            if Path(target).name=='previous_addon':raise OSError(errno.EXDEV,'Different volume')
            return original(source,target)
        def partial(source,target):
            Path(target).mkdir();(Path(target)/'partial').write_bytes(b'incomplete')
            raise OSError('Archive disk full')
        with patch('workshop.transactions.os.replace',side_effect=replace),patch('workshop.transactions.shutil.copytree',side_effect=partial):
            installed=commit_transaction(prepared['journal_path'],addon_destination=destination)
        self.assertTrue(Path(installed['backup_addon']).is_dir())
        rollback_transaction(prepared['journal_path'])
        self.assertEqual((destination/'resource/entity/mw_hybrid.def').read_bytes(),old)
        self.assertEqual((Path(installed['archived_backup'])/'partial').read_bytes(),b'incomplete')

    def test_tampered_or_missing_archive_blocks_rollback_before_removing_new_addon(self):
        prepared,destination,_=self.updated_destination()
        installed=commit_transaction(prepared['journal_path'],addon_destination=destination)
        archive=Path(installed['archived_backup']);member=archive/'mod.info';original=member.read_bytes()
        member.write_bytes(b'external change')
        for missing in (False,True):
            if missing:
                member.write_bytes(original);os.replace(archive,archive.with_name('moved_by_user'))
            with self.assertRaises(TransactionError):rollback_transaction(prepared['journal_path'])
            self.assertEqual((destination/'resource/entity/mw_hybrid.def').read_bytes(),b'new generation')
            self.assertTrue(Path(installed['save_destination']).is_file())

    def test_parallel_verification_finishes_readers_before_returning_an_error(self):
        from workshop.transactions import _tree_hashes,_file_hash
        folder=self.root/'verify';folder.mkdir()
        for name in ['a_broken','b_slow',*[f'z{i}' for i in range(20)]]:(folder/name).write_bytes(b'data')
        entered,failed,finished=threading.Event(),threading.Event(),threading.Event()
        def read(path):
            if path.name=='b_slow':
                entered.set()
                if not failed.wait(3):raise AssertionError('Failing worker never ran')
                time.sleep(.05);finished.set()
            if path.name=='a_broken':
                if not entered.wait(3):raise AssertionError('Slow worker never ran')
                failed.set();raise OSError('Injected hash failure')
            return _file_hash(path)
        with patch('workshop.transactions._file_hash',side_effect=read):
            with self.assertRaisesRegex(OSError,'Injected hash failure'):_tree_hashes(folder)
        self.assertTrue(finished.is_set())

    def test_prepared_directory_is_durable_verified_and_independently_publishable(self):
        from workshop.transactions import _write_addon_files
        folder=self.root/self.plan['addon_mod'].split(':')[0]
        for name,raw in self.files.items():
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        with patch('workshop.transactions._write_addon_files',wraps=_write_addon_files) as copy_files,patch('workshop.transactions.os.fsync',wraps=os.fsync) as sync:
            result=prepare_transaction(self.root,self.snapshot,self.plan,addon_directory=folder)
            copy_files.assert_not_called()
            self.assertGreaterEqual(sync.call_count,len(self.files)+4)
        self.assertFalse(folder.exists())
        verified=verify_transaction(result['journal_path'])
        self.assertEqual(verified['addon_hashes'],{name:hashlib.sha256(raw).hexdigest() for name,raw in self.files.items()})
        commit_transaction(result['journal_path'])
        rollback_transaction(result['journal_path'])
        self.assertEqual(self.source.read_bytes(),self.original_bytes)

    def test_directory_staging_refuses_unapproved_roots_and_detects_changed_content(self):
        for folder in (self.root/'wrong_name',self.root/'nested'/self.plan['addon_mod'].split(':')[0]):
            folder.mkdir(parents=True);(folder/'mod.info').write_bytes(b'unchanged')
            with self.assertRaises(TransactionError):prepare_transaction(self.root,self.snapshot,self.plan,addon_directory=folder)
            self.assertEqual((folder/'mod.info').read_bytes(),b'unchanged')
        folder=self.root/self.plan['addon_mod'].split(':')[0];folder.mkdir()
        for name,raw in self.files.items():
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        original_replace=os.replace
        def changed(source,target):
            from workshop.transactions import long_path
            if Path(source) in (folder,long_path(folder)):(folder/'mod.info').write_bytes(b'changed after verification')
            return original_replace(source,target)
        with patch('workshop.transactions.os.replace',side_effect=changed):
            with self.assertRaisesRegex(TransactionError,'Staged addon changed'):
                prepare_transaction(self.root,self.snapshot,self.plan,addon_directory=folder)

    def test_parallel_staging_keeps_every_file_durable_and_rejects_traversal_before_writing(self):
        from workshop.transactions import _write_addon_files, _tree_hashes
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'addon'
            files={f'resource/folder{i%3}/member{i}.bin':bytes([i])*100 for i in range(24)}
            with patch('workshop.transactions.os.fsync', wraps=os.fsync) as sync:
                _write_addon_files(root, files, files.__getitem__)
            self.assertEqual(sync.call_count,len(files))
            self.assertEqual(_tree_hashes(root),{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()})
            other=Path(tmp)/'unsafe'
            with self.assertRaisesRegex(TransactionError,'escapes'):
                _write_addon_files(other,['good','../outside'],lambda name:b'x')
            self.assertFalse(other.exists())
            self.assertFalse((Path(tmp)/'outside').exists())

    def test_parallel_writer_joins_inflight_work_before_error_returns(self):
        from workshop.transactions import _write_addon_files
        entered,failed,finished=threading.Event(),threading.Event(),threading.Event()
        def read(name):
            if name=='slow':
                entered.set()
                if not failed.wait(3):raise AssertionError('Other worker never ran')
                time.sleep(.05)
                finished.set()
                return b'completed before failure returns'
            if not entered.wait(3):raise AssertionError('Slow worker never ran')
            failed.set()
            raise OSError('Injected copy failure')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(OSError,'Injected copy'):
                _write_addon_files(Path(tmp),['broken','slow'],read)
            self.assertTrue(finished.is_set())
            self.assertEqual((Path(tmp)/'slow').read_bytes(),b'completed before failure returns')

    def test_install_flushes_every_copied_member_before_publishing(self):
        from workshop.transactions import _file_hash
        transaction=self.prepare();synced=set();original_replace=os.replace
        def file_hash(path,*,durable=False):
            value=_file_hash(path,durable=durable)
            if durable and any('.mw-stage-' in p.name for p in path.parents):synced.add(path.name)
            return value
        def replace(source,target):
            if Path(source).is_dir() and '.mw-stage-' in Path(source).name:
                self.assertTrue({Path(p).name for p in self.files}<=synced)
            return original_replace(source,target)
        with patch('workshop.transactions._file_hash',side_effect=file_hash),patch('workshop.transactions.os.replace',side_effect=replace):
            commit_transaction(transaction['journal_path'])
        self.assertEqual(self.source.read_bytes(),self.original_bytes)

    def test_failed_deferred_sync_cannot_publish_addon_or_save(self):
        from workshop.transactions import _file_hash
        transaction=self.prepare()
        def file_hash(path,*,durable=False):
            if durable and any('.mw-stage-' in p.name for p in path.parents):raise OSError('Injected durable flush failure')
            return _file_hash(path,durable=durable)
        with patch('workshop.transactions._file_hash',side_effect=file_hash):
            with self.assertRaisesRegex(TransactionError,'recovery required'):commit_transaction(transaction['journal_path'])
        journal=json.loads(Path(transaction['journal_path']).read_text())
        self.assertFalse(Path(journal['publication']['addon_destination']).exists())
        self.assertFalse(Path(journal['publication']['save_destination']).exists())
        self.assertEqual(self.source.read_bytes(),self.original_bytes)

    def test_owned_addon_returns_the_snapshot_it_validated(self):
        from workshop.transactions import _owned_addon,OWNERSHIP_FILE
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            members={'mod.info':'verified'}
            (root/OWNERSHIP_FILE).write_text(json.dumps({'addon_mod':'modular_workshop:0','files':members}))
            snapshot={**members,OWNERSHIP_FILE:'marker'}
            with patch('workshop.transactions._tree_hashes',side_effect=[snapshot,{'mod.info':'unverified'}]):
                self.assertEqual(_owned_addon(root,'modular_workshop:0'),snapshot)

    def test_tampering_during_marker_write_is_detected_before_publication(self):
        from workshop.transactions import _write_file,OWNERSHIP_FILE
        transaction=self.prepare()
        def tamper(path,data,**kwargs):
            _write_file(path,data,**kwargs)
            if path.name==OWNERSHIP_FILE:
                (path.parent/'mod.info').write_bytes(b'changed after file copy')
        with patch('workshop.transactions._write_file',side_effect=tamper):
            with self.assertRaises(TransactionError):
                commit_transaction(transaction['journal_path'])
        self.assertEqual(self.source.read_bytes(),self.original_bytes)
        journal=json.loads(Path(transaction['journal_path']).read_text())
        self.assertFalse(Path(journal['publication']['addon_destination']).exists())
        self.assertFalse(Path(journal['publication']['save_destination']).exists())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = make_save(self.root / "original.sav")
        self.original_bytes = self.source.read_bytes()
        self.snapshot = load_campaign(self.source)
        self.plan = plan_assembly(self.snapshot, "0x9000", "0x9010", "mw_hybrid", 2, state_profile=PROFILE)
        self.files = {"mod.info": b'{mod {name "Modular Workshop"}}', "resource/entity/mw_hybrid.def": b'{entity "mw_hybrid"}'}
        self.process_patch = patch("workshop.transactions.running_game_processes", return_value=[])
        self.process_patch.start()

    def tearDown(self):
        self.assertEqual(self.source.read_bytes(), self.original_bytes, "No transaction may write the original save")
        self.process_patch.stop()
        self.temp.cleanup()

    def prepare(self):
        return prepare_transaction(self.root, self.snapshot, self.plan, self.files)

    def test_prepare_is_reviewable_and_original_is_byte_identical(self):
        result = self.prepare()
        self.assertTrue(Path(result["addon_path"]).is_dir())
        self.assertTrue(Path(result["save_path"]).is_file())
        self.assertEqual(Path(result["backup_path"]).read_bytes(), self.original_bytes)
        self.assertTrue(verify_transaction(result["journal_path"])["valid"])
        after = load_campaign(result["save_path"])
        self.assertEqual(len(after.units), len(self.snapshot.units) - 1)

    def test_in_place_updates_the_opened_save_and_rollback_restores_it(self):
        # The player keeps ONE campaign: the opened save itself is updated.
        prepared = self.prepare()
        published = commit_transaction(prepared["journal_path"], addon_destination=self.root / "mods" / "modular_workshop",
                                       save_destination=self.source, allow_install=True, in_place=True)
        self.assertEqual(Path(published["save_destination"]), self.source.resolve())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), prepared["save_hash"])
        self.assertEqual(list(self.root.glob(".original.sav*")), [])
        rollback_transaction(prepared["journal_path"])
        self.assertEqual(self.source.read_bytes(), self.original_bytes)

    def test_in_place_only_targets_the_opened_save_and_refuses_changed_saves(self):
        prepared = self.prepare()
        with self.assertRaisesRegex(TransactionError, "only update the opened save"):
            commit_transaction(prepared["journal_path"], save_destination=self.root / "other.sav", in_place=True)
        commit_transaction(prepared["journal_path"], save_destination=self.source, in_place=True)
        self.source.write_bytes(b"the player saved again in the game")
        with self.assertRaisesRegex(TransactionError, "changed after publication"):
            rollback_transaction(prepared["journal_path"])
        self.source.write_bytes(self.original_bytes)

    def test_publish_and_rollback_own_outputs(self):
        prepared = self.prepare()
        published = commit_transaction(prepared["journal_path"])
        addon, save = Path(published["addon_destination"]), Path(published["save_destination"])
        self.assertTrue(addon.is_dir())
        self.assertTrue(save.is_file())
        self.assertEqual(hashlib.sha256(save.read_bytes()).hexdigest(), prepared["save_hash"])
        rollback = rollback_transaction(prepared["journal_path"])
        self.assertEqual(rollback["state"], "rolled_back")
        self.assertFalse(addon.exists())
        self.assertFalse(save.exists())
        self.assertTrue(Path(prepared["save_path"]).exists())
        self.assertEqual(rollback_transaction(prepared["journal_path"])["state"], "rolled_back")

    def test_rollback_refuses_changed_outputs_without_deleting_any(self):
        prepared = self.prepare()
        published = commit_transaction(prepared["journal_path"])
        addon, save = Path(published["addon_destination"]), Path(published["save_destination"])
        (addon / "external-edit.txt").write_bytes(b"user work")
        with self.assertRaisesRegex(TransactionError, "changed externally"):
            rollback_transaction(prepared["journal_path"])
        self.assertTrue(save.is_file())
        self.assertEqual((addon / "external-edit.txt").read_bytes(), b"user work")

    def test_original_destination_and_unsafe_paths_are_rejected(self):
        prepared = self.prepare()
        with self.assertRaisesRegex(TransactionError, "original save"):
            commit_transaction(prepared["journal_path"], save_destination=self.source)
        for name in ("../escaped.def", "C:/escaped.def", "/absolute.def", "resource/../escaped.def", "CON.def", "resource/trailing. "):
            with self.subTest(name=name), self.assertRaises(TransactionError):
                prepare_transaction(self.root, self.snapshot, self.plan, {"mod.info": b"x", name: b"bad"})

    def test_existing_unowned_addon_and_existing_save_are_preserved(self):
        prepared = self.prepare()
        addon = self.root / "modular_workshop"
        addon.mkdir()
        (addon / "user-file").write_text("preserve")
        with self.assertRaisesRegex(TransactionError, "not owned"):
            commit_transaction(prepared["journal_path"], addon_destination=addon)
        save = self.root / "already-existing.sav"
        save.write_bytes(b"unrelated")
        with self.assertRaisesRegex(TransactionError, "already exists"):
            commit_transaction(prepared["journal_path"], save_destination=save)
        self.assertEqual(save.read_bytes(), b"unrelated")

    def test_changed_source_or_staged_files_blocks_publication(self):
        prepared = self.prepare()
        staged = Path(prepared["addon_path"]) / "resource/entity/mw_hybrid.def"
        staged.write_bytes(b"changed after review")
        with self.assertRaisesRegex(TransactionError, "Staged addon changed"):
            commit_transaction(prepared["journal_path"])
        self.source.write_bytes(self.original_bytes + b"changed")
        try:
            with self.assertRaisesRegex(TransactionError, "Original save changed"):
                verify_transaction(prepared["journal_path"])
        finally:
            # The test itself simulates a game write, then restores its fixture.
            self.source.write_bytes(self.original_bytes)

    def test_game_open_guard_blocks_even_preparation(self):
        with patch("workshop.transactions.running_game_processes", return_value=["call_to_arms.exe"]):
            for page in ("page_dynamic_campaign", "battle", "campaign", None):
                with patch("workshop.transactions.menu_page", return_value=page):
                    with self.assertRaisesRegex(TransactionError, "Close the game"):
                        self.prepare()
                    with self.assertRaises(TransactionError):
                        assert_game_closed()
            # In a main-menu page no campaign is held in memory: allowed.
            with patch("workshop.transactions.menu_page", return_value="page_main"):
                assert_game_closed()
        with patch("workshop.transactions.running_game_processes", return_value=["call_to_arms.exe", "call_to_arms_ed.exe"]):
            with patch("workshop.transactions.menu_page", return_value="page_main"):
                with self.assertRaises(TransactionError):
                    assert_game_closed()

    def test_menu_page_reads_the_live_game_log(self):
        from workshop.process_guard import menu_page
        log = Path(self.temp.name) / "game.log"
        lines = ["[00:00:34] GUI: Show page page_main with mods [ 5 unique]",
                 "[00:00:41] GUI: Show page page_loader_popup with mods [ 0 overlay]",
                 "[00:00:41] GUI: Show page page_dynamic_campaign with mods [ 1 unique]"]
        log.write_text("\n".join(lines[:2]))
        self.assertEqual(menu_page(log), "page_main")  # overlays do not count
        log.write_text("\n".join(lines))
        self.assertEqual(menu_page(log), "page_dynamic_campaign")
        log.write_text("\n".join(lines + ["[00:01:18] Game: Create game campaign_capture_the_flag mode:1"]))
        self.assertEqual(menu_page(log), "battle")
        self.assertIsNone(menu_page(log.with_name("missing.log")))

    def test_failed_atomic_replace_does_not_fallback_and_recovers(self):
        prepared = self.prepare()
        import workshop.transactions as transactions
        original_replace = transactions.os.replace
        failed_once = False

        def replacement(source, destination):
            nonlocal failed_once
            if str(destination).endswith(".sav") and not failed_once:
                failed_once = True
                raise OSError("simulated atomic replacement failure")
            return original_replace(source, destination)

        with patch("workshop.transactions.os.replace", side_effect=replacement):
            with self.assertRaisesRegex(TransactionError, "rolled back"):
                commit_transaction(prepared["journal_path"])
        journal = json.loads(Path(prepared["journal_path"]).read_text())
        self.assertTrue(failed_once)
        self.assertEqual(journal["state"], "rolled_back")
        self.assertFalse(Path(journal["publication"]["addon_destination"]).exists())
        self.assertFalse(Path(journal["publication"]["save_destination"]).exists())

    def test_update_owned_addon_rollback_restores_previous_generation(self):
        first = self.prepare()
        destination = self.root / "modular_workshop"
        commit_transaction(first["journal_path"], addon_destination=destination, save_destination=self.root / "first.sav")
        previous = (destination / "resource/entity/mw_hybrid.def").read_bytes()
        self.files["resource/entity/mw_hybrid.def"] = b"second generated version"
        second = self.prepare()
        commit_transaction(second["journal_path"], addon_destination=destination, save_destination=self.root / "second.sav")
        self.assertEqual((destination / "resource/entity/mw_hybrid.def").read_bytes(), b"second generated version")
        # No backup may stay beside the addon: mod managers list it as a second mod.
        self.assertEqual([p.name for p in self.root.glob(".modular_workshop*")], [])
        rollback_transaction(second["journal_path"])
        self.assertEqual((destination / "resource/entity/mw_hybrid.def").read_bytes(), previous)
        self.assertTrue((self.root / "first.sav").exists())


if __name__ == "__main__":
    unittest.main()


class FastVerificationTests(unittest.TestCase):
    """Speed-ups of the addon pipeline keep every integrity guarantee."""

    def test_digest_cache_notices_a_changed_file(self):
        from workshop import transactions
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "a.bin"
            path.write_bytes(b"first")
            old = time.time() - 60
            os.utime(path, (old, old))
            self.assertEqual(transactions._file_hash(path), hashlib.sha256(b"first").hexdigest())
            self.assertEqual(transactions._file_hash(path), hashlib.sha256(b"first").hexdigest())  # cached
            path.write_bytes(b"other")  # same size, new content
            self.assertEqual(transactions._file_hash(path), hashlib.sha256(b"other").hexdigest())

    def test_recently_written_files_are_never_trusted_from_the_cache(self):
        from workshop import transactions
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "b.bin"
            path.write_bytes(b"one")
            transactions._file_hash(path)
            stat = os.stat(path)
            self.assertNotIn((stat.st_dev, stat.st_ino), transactions._HASH_CACHE)

    def test_unchanged_files_are_linked_and_changed_ones_written(self):
        from workshop import transactions
        with tempfile.TemporaryDirectory() as temp:
            installed, target = Path(temp) / "installed", Path(temp) / "new"
            (installed / "entity").mkdir(parents=True)
            (installed / "entity" / "same.def").write_bytes(b"same")
            files = {"entity/same.def": b"same", "entity/new.def": b"new"}
            linked = transactions._write_addon_files(target, files, files.__getitem__, defer_sync=True,
                                                     reuse={"entity/same.def": installed / "entity" / "same.def"})
            self.assertEqual(linked, {"entity/same.def"})
            self.assertEqual((target / "entity" / "same.def").read_bytes(), b"same")
            self.assertEqual((target / "entity" / "new.def").read_bytes(), b"new")
            self.assertEqual(transactions._tree_hashes(target, durable=True),
                             {name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
            # Publication flushes only what it wrote; a link still gets its digest checked.
            flushed, original = [], transactions._file_hash
            def file_hash(path, *, durable=False):
                if durable: flushed.append(path.name)
                return original(path, durable=durable)
            with patch.object(transactions, "_file_hash", side_effect=file_hash):
                hashes = transactions._tree_hashes(target, durable=True, settled=linked)
            self.assertEqual(flushed, ["new.def"])
            self.assertEqual(hashes["entity/same.def"], hashlib.sha256(b"same").hexdigest())
            (installed / "entity" / "same.def").write_bytes(b"edit")
            self.assertEqual(transactions._tree_hashes(target, durable=True, settled=linked)["entity/same.def"],
                             hashlib.sha256(b"edit").hexdigest())

    def test_links_above_the_users_folders_are_the_systems_layout(self):
        # SteamOS /home, a moved profile or a Documents junction: only links
        # below the user's own folders are refused.
        from workshop import transactions
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home" / "deck"
            target = home / "Desktop" / "conquest 8.sav"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"save")
            with patch.object(transactions, "_SYSTEM_FOLDERS", (os.path.normcase(str(home)),)):
                with patch.object(Path, "is_symlink", lambda item: item == Path(temp) / "home"):
                    transactions._no_symlink(target)
                with patch.object(Path, "is_symlink", lambda item: item == home / "Desktop"):
                    with self.assertRaises(transactions.TransactionError):
                        transactions._no_symlink(target)

    def test_a_link_inside_the_tree_is_still_rejected(self):
        from workshop import transactions
        with tempfile.TemporaryDirectory() as temp:
            root, outside = Path(temp) / "addon", Path(temp) / "outside"
            (root / "entity").mkdir(parents=True)
            outside.mkdir()
            (root / "entity" / "a.def").write_bytes(b"a")
            try:
                import _winapi  # a junction needs no privileges on Windows
                _winapi.CreateJunction(str(outside), str(root / "entity" / "linked"))
            except (ImportError, OSError):
                try:
                    os.symlink(outside, root / "entity" / "linked", target_is_directory=True)
                except OSError:
                    self.skipTest("Directory links are not available on this system")
            with self.assertRaises(transactions.TransactionError):
                transactions._tree_hashes(root)


class PruneOperationsTests(unittest.TestCase):
    def _operation(self, root, name, state="committed"):
        op = root / "operations" / name
        tx = op / "operations" / "tx"
        for folder in ("stage/addon/resource", "previous_addon/resource", "backup", "stage/save"):
            (tx / folder).mkdir(parents=True)
        (tx / "stage/addon/resource/a.def").write_bytes(b"x" * 100)
        (tx / "previous_addon/resource/a.def").write_bytes(b"y" * 100)
        (tx / "backup/original.sav").write_bytes(b"save")
        (tx / "stage/save/s.sav").write_bytes(b"save")
        (tx / "journal.json").write_text(json.dumps({"state": state}), encoding="utf-8")
        (op / "modular_workshop").mkdir()
        (op / "modular_workshop" / "b.def").write_bytes(b"z")
        return op, tx

    def test_old_bulky_copies_go_and_save_backups_stay(self):
        from workshop.transactions import prune_operations
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, old_tx = self._operation(root, "20261013_000000_a")
            stuck, stuck_tx = self._operation(root, "20261013_000001_b", state="committing")
            recent = [self._operation(root, f"20261019_00000{i}_c") for i in range(3)]
            result = prune_operations(root, keep=3)
            self.assertEqual(result["pruned"], ["20261013_000000_a"])
            self.assertGreater(result["freed_bytes"], 0)
            self.assertFalse((old_tx / "stage/addon").exists())
            self.assertFalse((old_tx / "previous_addon").exists())
            self.assertFalse((old / "modular_workshop").exists())
            self.assertTrue((old_tx / "backup/original.sav").exists())
            self.assertTrue((old_tx / "journal.json").exists())
            self.assertTrue((old / "pruned.json").exists())
            self.assertTrue((stuck_tx / "previous_addon").exists())  # interrupted: kept for recovery
            for op, tx in recent:
                self.assertTrue((tx / "previous_addon").exists())
            self.assertEqual(prune_operations(root, keep=3)["pruned"], [])  # idempotent


class ReplaceFileVerifiedTests(unittest.TestCase):
    def test_backup_first_then_swap_and_refuse_a_changed_file(self):
        from workshop.transactions import replace_file_verified
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save = root / "campaign.sav"
            save.write_bytes(b"broken")
            expected = hashlib.sha256(b"broken").hexdigest()
            backup = root / "backups" / "saves" / "campaign.sav"
            replace_file_verified(save, expected, b"fixed", backup)
            self.assertEqual(save.read_bytes(), b"fixed")
            self.assertEqual(backup.read_bytes(), b"broken")
            self.assertEqual([p.name for p in root.iterdir() if p.name.startswith(".")], [])
            with self.assertRaises(TransactionError):  # no longer the file that was read
                replace_file_verified(save, expected, b"again", root / "backups" / "saves" / "second.sav")
            self.assertEqual(save.read_bytes(), b"fixed")
