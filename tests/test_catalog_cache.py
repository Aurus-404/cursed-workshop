import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop import catalog_cache


class View:
    def __init__(self, fingerprint):
        self.sources = [{'id': 'vanilla', 'version': '1', 'fingerprint': fingerprint, 'root': 'game'}]


class CatalogCacheTests(unittest.TestCase):
    def test_packaged_catalog_identity_uses_backend_hash_with_legacy_fallback(self):
        for metadata,expected in [({'source_sha256':'a'*64,'catalog_code_sha256':'b'*64},'b'*64),
                                  ({'source_sha256':'a'*64},'a'*64)]:
            with patch.object(catalog_cache,'_CODE',None),patch('workshop.build_info.read_build_info',return_value=metadata):
                self.assertEqual(catalog_cache.code_identity(),expected)

    def test_catalog_source_identity_changes_for_backend_code_but_not_docs_or_line_endings(self):
        from workshop.build_info import source_code_sha256
        path=self.folder/'reader.py';path.write_bytes(b'first\r\n')
        initial=source_code_sha256(self.folder)
        path.write_bytes(b'first\n');(self.folder/'README.md').write_text('new documentation')
        self.assertEqual(source_code_sha256(self.folder),initial)
        path.write_bytes(b'second\n')
        self.assertNotEqual(source_code_sha256(self.folder),initial)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.calls = []

        def reader(index):
            if not hasattr(index, '_test_catalog'):
                self.calls.append(index.sources[0]['fingerprint'])
                index._test_catalog = [{'id': 'plate', 'thickness': [10.0]}]
            return index._test_catalog
        self.reader = reader
        patcher = patch.dict(catalog_cache.CATALOGS, {'test': (lambda index: index, '_test_catalog', reader)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_the_same_stack_loads_from_disk_without_reading_the_game(self):
        self.assertEqual(catalog_cache.load(View('a'), 'test', self.folder), [{'id': 'plate', 'thickness': [10.0]}])
        self.assertEqual(catalog_cache.load(View('a'), 'test', self.folder), [{'id': 'plate', 'thickness': [10.0]}])
        self.assertEqual(self.calls, ['a'])

    def test_building_a_vehicle_does_not_read_the_game_again(self):
        # TheOppsAreOntoMe: each build changed the workshop mod's fingerprint, every
        # whole-game catalog was read again for minutes and previews waited for it.
        def view(addon):
            v = View('a')
            v.sources.append({'id': 'modular_workshop', 'version': '0', 'fingerprint': addon, 'root': 'mods/modular_workshop'})
            return v
        catalog_cache.load(view('build 1'), 'test', self.folder)
        catalog_cache.load(view('build 2'), 'test', self.folder)
        self.assertEqual(self.calls, ['a'])

    def test_a_changed_mod_or_a_damaged_file_reads_the_game_again(self):
        catalog_cache.load(View('a'), 'test', self.folder)
        catalog_cache.load(View('b'), 'test', self.folder)
        saved = self.folder / f"test-{catalog_cache.stack_key(View('b'))[:24]}.json"
        saved.write_text('{"key": ', encoding='utf-8')
        catalog_cache.load(View('b'), 'test', self.folder)
        self.assertEqual(self.calls, ['a', 'b', 'b'])
        self.assertEqual(json.loads(saved.read_text(encoding='utf-8'))['value'][0]['id'], 'plate')

    def test_switching_between_mod_stacks_reuses_each_saved_catalog(self):
        (self.folder / 'test.json').write_text('{"key": "old layout"}', encoding='utf-8')
        for fingerprint in ('with mace', 'vanilla', 'with mace', 'vanilla'):
            catalog_cache.load(View(fingerprint), 'test', self.folder)
        self.assertEqual(self.calls, ['with mace', 'vanilla'])
        self.assertFalse((self.folder / 'test.json').exists())
        for fingerprint in 'cdefg':
            catalog_cache.load(View(fingerprint), 'test', self.folder)
        self.assertEqual(len(list(self.folder.glob('test-*.json'))), catalog_cache.STACKS_KEPT)

    def test_new_tool_code_reads_the_game_again(self):
        catalog_cache.load(View('a'), 'test', self.folder)
        with patch.object(catalog_cache, '_CODE', 'other build'):
            catalog_cache.load(View('a'), 'test', self.folder)
        self.assertEqual(self.calls, ['a', 'a'])

    def test_a_second_caller_waits_for_the_reading_in_progress(self):
        started, release = threading.Event(), threading.Event()
        view = View('a')

        def slow(index):
            if not hasattr(index, '_test_catalog'):
                started.set(); release.wait(5)
                self.calls.append('read')
                index._test_catalog = ['done']
            return index._test_catalog
        with patch.dict(catalog_cache.CATALOGS, {'test': (lambda index: index, '_test_catalog', slow)}):
            first = threading.Thread(target=catalog_cache.load, args=(view, 'test', self.folder))
            first.start(); started.wait(5)
            results = []
            second = threading.Thread(target=lambda: results.append(catalog_cache.load(view, 'test', self.folder)))
            second.start()
            for _ in range(100):
                if catalog_cache.awaited(): break
                threading.Event().wait(.01)
            self.assertTrue(catalog_cache.awaited())
            release.set(); first.join(5); second.join(5)
        self.assertEqual(results, [['done']])
        self.assertEqual(self.calls, ['read'])
        self.assertFalse(catalog_cache.awaited())


if __name__ == '__main__':
    unittest.main()
