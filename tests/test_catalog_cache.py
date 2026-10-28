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

    def test_a_changed_mod_or_a_damaged_file_reads_the_game_again(self):
        catalog_cache.load(View('a'), 'test', self.folder)
        catalog_cache.load(View('b'), 'test', self.folder)
        (self.folder / 'test.json').write_text('{"key": ', encoding='utf-8')
        catalog_cache.load(View('b'), 'test', self.folder)
        self.assertEqual(self.calls, ['a', 'b', 'b'])
        self.assertEqual(json.loads((self.folder / 'test.json').read_text(encoding='utf-8'))['value'][0]['id'], 'plate')

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
