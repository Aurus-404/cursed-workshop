import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop.cache_maintenance import ROOTS, maintain


class CacheMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, content=b'cached'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_only_explicit_cache_roots_are_cleared_and_reusable(self):
        files = [self.write(name+'/nested/data.bin') for name in ROOTS]
        retained = [self.write(name) for name in (
            'settings.json','editor-draft.json','reports/report.zip','operations/recovery.sav',
            'storage/vehicle.bin','templates/design.json','cache/unrelated/keep.txt',
            'game/mods/modular_workshop/vehicle.def')]
        info = maintain(self.root)
        self.assertEqual((info['files'],info['bytes']), (4,24))
        self.assertTrue(all(path.exists() for path in files))
        result = maintain(self.root,clear=True)
        self.assertEqual((result['removed_files'],result['removed_bytes'],result['skipped']), (4,24,0))
        self.assertTrue(all(not path.exists() for path in files))
        self.assertTrue(all(path.read_bytes()==b'cached' for path in retained))
        self.assertEqual(maintain(self.root)['files'],0)
        # Readers can regenerate a missing cache without a restart or losing roots.
        files[0].write_bytes(b'regenerated')
        self.assertEqual(maintain(self.root)['bytes'],len(b'regenerated'))

    def test_locked_files_do_not_prevent_the_other_caches_being_removed(self):
        locked = self.write('texture_cache/locked.png')
        other = self.write('cache/catalogs/armor.json')
        original = Path.unlink
        def unlink(path,*args,**kwargs):
            if path == locked:
                raise PermissionError('in use')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):
            result = maintain(self.root,clear=True)
        self.assertEqual(result['skipped'],1)
        self.assertEqual(result['removed_files'],1)
        self.assertTrue(locked.exists());self.assertFalse(other.exists())

    def test_symlinks_to_external_files_and_folders_are_never_followed(self):
        outside = self.root/'originals'
        outside.mkdir();saved = outside/'campaign.sav';saved.write_bytes(b'irreplaceable')
        folder = self.root/'texture_cache';folder.mkdir()
        try:
            (folder/'file.png').symlink_to(saved)
            (folder/'directory').symlink_to(outside,target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks is not available on this Windows account')
        result = maintain(self.root,clear=True)
        self.assertEqual(result['removed_files'],0)
        self.assertEqual(result['skipped'],2)
        self.assertEqual(saved.read_bytes(),b'irreplaceable')

    def test_reparse_parent_is_rejected_before_walking_the_subdirectory(self):
        target = self.write('cache/catalogs/keep.json')
        from workshop import cache_maintenance as module
        plain = module._plain
        with patch.object(module,'_plain',side_effect=lambda path:False if path==self.root/'cache' else plain(path)):
            result = maintain(self.root,clear=True)
        self.assertEqual(result['removed_files'],0)
        self.assertEqual(result['skipped'],1)
        self.assertTrue(target.exists())

    def test_empty_workspace_and_repeated_cleanup_are_harmless(self):
        self.assertEqual(maintain(self.root,clear=True),
                         {'bytes':0,'files':0,'removed_bytes':0,'removed_files':0,'skipped':0})

    def test_windows_reparse_attribute_is_rejected_even_without_symlink_mode(self):
        from types import SimpleNamespace
        from workshop.cache_maintenance import _plain
        with patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=0x400)):
            self.assertFalse(_plain(self.root))


if __name__=='__main__':
    unittest.main()
