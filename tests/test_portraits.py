"""Assembled portrait contracts: complete PNGs, exact geometry and safe writes."""
import base64
import copy
import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from workshop.app import AppState
from workshop.registration import ADDON_MOD, portrait_state
from workshop.transactions import OWNERSHIP_FILE, TransactionError, _owned_addon, _tree_hashes


def chunk(kind, payload):
    return (struct.pack('>I', len(payload)) + kind + payload
            + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))


def png(width=112, height=140, *, filters=0, interlace=0, pixel=0):
    passes = [(0, 0, 1, 1)] if not interlace else [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8),
              (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    raw_pixels = b''
    for x, y, dx, dy in passes:
        w, h = max(0, (width-x+dx-1)//dx), max(0, (height-y+dy-1)//dy)
        if w and h:
            raw_pixels += (bytes([filters]) + bytes([pixel]) * (w * 4)) * h
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, interlace))
            + chunk(b'IDAT', zlib.compress(raw_pixels)) + chunk(b'IEND', b''))


def image(raw):
    return 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')


class PNGTests(unittest.TestCase):
    def test_complete_rgba_and_adam7_are_accepted(self):
        raws = [png(pixel=i, interlace=i%2) for i in range(4)]
        self.assertEqual(AppState.decode_portrait([image(raw) for raw in raws]), raws)

    def test_invalid_pngs_and_bounded_inflate_are_rejected(self):
        good = png()
        wrong_crc = bytearray(good); wrong_crc[-1] ^= 1
        forged_header = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 112, 140, 8, 6, 0, 0, 0))
        bomb = forged_header + chunk(b'IDAT', zlib.compress(bytes(300_000))) + chunk(b'IEND', b'')
        for raw in (bytes(wrong_crc), good[:-1], good + b'trailing', forged_header, bomb,
                    png(64, 64), png(filters=5), b'not PNG', good[:20]):
            with self.subTest(size=len(raw)), self.assertRaisesRegex(ValueError, 'Retrato inválido'):
                AppState.decode_portrait([image(raw)] * 4)

    def test_validate_every_image_before_writing_any_output(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'staging'
            with self.assertRaises(ValueError):
                AppState.write_portrait(target, 'mw_fixture', [image(png())] * 3 + [image(png(64, 64))])
            self.assertFalse(target.exists())
            with self.assertRaises(ValueError):
                AppState.write_portrait(target, '../outside', [image(png())] * 4)


class PortraitApplicationTests(unittest.TestCase):
    def test_portrait_uses_session_overlay_without_rescanning_the_game(self):
        from workshop.resources import ResourceIndex
        self.state.index=ResourceIndex(self.game).build()
        preview={'meshes':[{'name':'full'}],'diagnostics':[]}
        original_build=ResourceIndex.build;scanned=[]
        def scan(index):scanned.append(index.game_root);return original_build(index)
        try:
            with patch.object(ResourceIndex,'build',scan),patch('workshop.geometry.build_preview',return_value=preview):
                result=self.state.portrait_preview({'id':self.identifier})
            self.assertEqual(scanned,[self.addon.resolve()])
            index=self.state.portrait_previews[result['preview_token']]['index']
            self.assertTrue(index.has_entity(self.identifier))
            self.state.clear_portrait_previews()
            self.assertTrue(self.state.index.built)
        finally:self.state.index.close();self.state.index=None

    def test_icon_strip_uses_native_chassis_ancestry_in_journaled_save(self):
        from workshop.icons import ICON_KINDS, write_tga, read_tga
        recipe=json.loads((self.addon/'assembly_mw_fixture.json').read_text())
        recipe['metadata']['recipient']='mw_previous'
        self.write('assembly_mw_fixture.json',json.dumps(recipe).encode())
        self.write('assembly_mw_previous.json',json.dumps({'id':'mw_previous','metadata':{'recipient':'native_hull'}}).encode())
        self.mark_owned()
        body=self.preview_context()
        reads=[]
        def native_icon(path):
            reads.append(path)
            self.assertIn('/native_hull_',path)
            kind='unit_icon_small' if '/unit_icon_small/' in path else 'unit_icon'
            w,h=ICON_KINDS[kind]
            return write_tga(w,h,bytes((77,88,99,255))*w*h)
        self.state.portrait_previews['fixture_token']['index']=SimpleNamespace(read_bytes=native_icon,close=lambda:None)
        body['icons']={kind:'rgba:'+base64.b64encode(bytes(w*h*4)).decode() for kind,(w,h) in ICON_KINDS.items()}
        result=self.state.save_portrait(body)
        self.assertTrue(Path(result['backup']).is_dir())
        self.assertEqual(len(reads),8)
        for kind in ICON_KINDS:
            raw=(self.addon/f'resource/interface/scene/{kind}/mw_fixture_00.tga').read_bytes()
            self.assertEqual(read_tga(raw)[2][:4],bytes((77,88,99,255)))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.game = self.root/'game'
        (self.game/'resource').mkdir(parents=True)
        self.addon = self.game/'mods/modular_workshop'
        self.addon.mkdir(parents=True)
        self.state = AppState(self.root/'workspace')
        self.state.settings = {'game_root': str(self.game), 'mod_roots': []}
        self.guard = patch('workshop.app.require_game_idle', return_value={'running': False}).start()
        self.identifier = 'mw_fixture'
        self.write('mod.info', b'{mod {name "Cursed Workshop"}}')
        self.write('assembly_mw_fixture.json', json.dumps({'id': self.identifier, 'metadata': {
            'recipient': 'native_hull', 'donor': 'native_module', 'crew_slots': 3,
            'transform': {'hull': {'position': [2, 3, 4]}, 'recipient_mount': '@free:upper'}}}).encode())
        self.write('workshop_manifest_mw_fixture.json', json.dumps({'id': self.identifier,
                   'display_name': 'Assembled vehicle', 'warnings': ['other warning',
                   'El retrato armado todavía no está renderizado; respaldo.']}).encode())
        self.write('resource/entity/-vehicle/workshop/mw_fixture/mw_fixture.def', b'{game_entity {model "mw_fixture"}}')
        self.write('resource/entity/-vehicle/workshop/mw_fixture/mw_fixture.mdl', b'{Skeleton {Bone "body"}}')
        self.write('unrelated.txt', b'preserve this')
        self.mark_owned()

    def tearDown(self):
        self.state.clear_portrait_previews()
        self.state.pool.shutdown(wait=True)
        self.guard.stop()
        self.temp.cleanup()

    def write(self, relative, raw):
        target = self.addon/relative
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)

    def mark_owned(self):
        files = _tree_hashes(self.addon); files.pop(OWNERSHIP_FILE, None)
        self.write(OWNERSHIP_FILE, json.dumps({'addon_mod': ADDON_MOD, 'files': files}).encode())

    def preview_context(self, *, addon=None, staged=False):
        addon = addon or self.addon
        self.state.portrait_previews['fixture_token'] = {'id': self.identifier, 'index': SimpleNamespace(close=lambda: None),
            'addon': str(addon), 'staged': staged, 'hashes': _tree_hashes(addon),
            'settings': json.dumps(self.state.settings, sort_keys=True), 'created': __import__('time').time()}
        return {'id': self.identifier, 'images': [image(png(pixel=i)) for i in range(4)],
                'preview_token': 'fixture_token', 'staged': staged}

    def test_list_includes_recipe_and_rendered_provenance(self):
        AppState.write_portrait(self.addon, self.identifier, [image(png())] * 4)
        self.mark_owned()
        rows = self.state.generated_vehicles()['vehicles']
        self.assertEqual(rows[0]['recipe']['transform']['recipient_mount'], '@free:upper')
        self.assertEqual(rows[0]['portrait_source'], 'rendered_model')
        self.assertTrue(rows[0]['has_rendered_portrait'])

    def test_legacy_portrait_is_not_claimed_to_show_assembled_model(self):
        self.write('resource/interface/scene/portrait_squad/mw_fixture_00.png', png())
        self.mark_owned()
        row = self.state.generated_vehicles()['vehicles'][0]
        self.assertEqual(row['portrait_source'], 'unverified')
        self.assertFalse(row['has_rendered_portrait'])

    def test_recompiled_model_marks_previous_render_outdated_without_erasing_it(self):
        AppState.write_portrait(self.addon, self.identifier, [image(png())] * 4)
        old = (self.addon/'resource/interface/scene/portrait_squad/mw_fixture_00.png').read_bytes()
        self.write('resource/entity/-vehicle/workshop/mw_fixture/mw_fixture.mdl', b'{Skeleton {Bone "changed"}}')
        self.mark_owned()
        row = self.state.generated_vehicles()['vehicles'][0]
        self.assertEqual(row['portrait_source'], 'outdated_model')
        self.assertFalse(row['has_rendered_portrait'])
        self.assertEqual((self.addon/'resource/interface/scene/portrait_squad/mw_fixture_00.png').read_bytes(), old)

    def test_exact_preview_reads_full_compiled_geometry(self):
        preview = {'meshes': [{'name': 'body'}], 'diagnostics': [], 'bounds': {'min': [0, 0, 0], 'max': [1, 1, 1]}}
        with patch('workshop.geometry.build_preview', return_value=preview) as build:
            result = self.state.portrait_preview({'id': self.identifier})
        self.assertEqual(build.call_args.args[1:], (self.identifier, 'full'))
        self.assertTrue(build.call_args.args[0].has_entity(self.identifier))
        self.assertEqual(result['preview'], preview)
        self.assertIn(result['preview_token'], self.state.portrait_previews)

    def test_incomplete_geometry_cannot_be_reported_as_rendered(self):
        before = _tree_hashes(self.addon)
        with patch('workshop.geometry.build_preview', return_value={
                'meshes': [{'name': 'body'}], 'diagnostics': [{'code': 'mesh_unavailable'}]}), \
             self.assertRaisesRegex(ValueError, 'mallas'):
            self.state.portrait_preview({'id': self.identifier})
        self.assertEqual(_tree_hashes(self.addon), before)

    def test_installed_write_archives_files_and_preserves_unrelated_assets(self):
        old_images = [image(png(pixel=9))] * 4
        AppState.write_portrait(self.addon, self.identifier, old_images)
        self.mark_owned()
        source_save = self.root/'user.sav'; source_save.write_bytes(b'original campaign')
        body = self.preview_context()
        from workshop.registration import portrait_geometry_hashes
        expected_geometry=portrait_geometry_hashes(self.addon,self.identifier)
        with patch('workshop.registration.portrait_geometry_hashes',side_effect=AssertionError('Do not hash verified geometry again')):
            result = self.state.save_portrait(body)
        backup = Path(result['backup'])
        self.assertEqual((backup/'resource/interface/scene/portrait_squad/mw_fixture_00.png').read_bytes(), png(pixel=9))
        self.assertEqual((self.addon/'unrelated.txt').read_bytes(), b'preserve this')
        self.assertEqual(source_save.read_bytes(), b'original campaign')
        _owned_addon(self.addon, ADDON_MOD)
        self.assertEqual(portrait_state(self.addon, self.identifier)['source'], 'rendered_model')
        manifest = json.loads((self.addon/'workshop_manifest_mw_fixture.json').read_text())
        self.assertEqual(manifest['warnings'], ['other warning'])
        self.assertEqual(manifest['portrait']['geometry_sha256'],expected_geometry)
        journal = json.loads(Path(result['journal_path']).read_text())
        self.assertEqual(journal['state'], 'committed')
        self.assertEqual(len(journal['published']), 7)  # PNGs, provenance, manifest, ownership

    def test_guard_and_expired_token_block_all_writes(self):
        before = _tree_hashes(self.addon)
        body = self.preview_context()
        with patch.object(self.state, 'closed', side_effect=RuntimeError('campaign loaded')), self.assertRaises(RuntimeError):
            self.state.save_portrait(body)
        body['preview_token'] = 'expired'
        with self.assertRaisesRegex(ValueError, 'venció'):
            self.state.save_portrait(body)
        self.assertEqual(_tree_hashes(self.addon), before)

    def test_externally_changed_addon_is_never_overwritten(self):
        body = self.preview_context()
        self.write('unrelated.txt', b'user changed this')
        # The changed addon is adopted (with a backup); the stale portrait view is refused.
        with self.assertRaises((TransactionError, ValueError)):
            self.state.save_portrait(body)
        self.assertEqual((self.addon/'unrelated.txt').read_bytes(), b'user changed this')

    def test_replacement_failure_rolls_back_all_portrait_files_and_ownership(self):
        AppState.write_portrait(self.addon, self.identifier, [image(png(pixel=7))] * 4)
        self.mark_owned()
        body = self.preview_context(); before = _tree_hashes(self.addon)
        original_replace = __import__('os').replace; failed = []
        def fail_second_png(source, target):
            if Path(target).name == 'mw_fixture_01.png' and not failed:
                failed.append(True); raise OSError('injected disk failure')
            return original_replace(source, target)
        with patch('workshop.app.os.replace', side_effect=fail_second_png), self.assertRaisesRegex(OSError, 'injected'):
            self.state.save_portrait(body)
        self.assertEqual(_tree_hashes(self.addon), before)
        _owned_addon(self.addon, ADDON_MOD)
        journal = next((self.state.workspace/'operations').glob('*/journal.json'))
        self.assertEqual(json.loads(journal.read_text())['state'], 'rolled_back')

    def test_staged_write_updates_prepared_hashes_without_touching_installed_addon(self):
        import shutil
        operation = self.state.workspace/'operations/prepared'
        staged = operation/'stage/addon'
        shutil.copytree(self.addon, staged); (staged/OWNERSHIP_FILE).unlink()
        save = operation/'stage/save/copy.sav'; save.parent.mkdir(parents=True); save.write_bytes(b'prepared save')
        tx_path = operation/'journal.json'
        tx = {'schema_version': 1, 'state': 'prepared', 'operation_dir': str(operation),
              'workspace': str(self.state.workspace), 'staged_addon': str(staged), 'staged_save': str(save),
              'addon_hashes': _tree_hashes(staged)}
        tx_path.write_text(json.dumps(tx))
        self.state.last_result = {'new_template': self.identifier, 'transaction': {'journal_path': str(tx_path)}}
        original_addon = _tree_hashes(self.addon)
        body = self.preview_context(addon=staged, staged=True)
        def verify(path):
            data = json.loads(Path(path).read_text())
            self.assertEqual(data['addon_hashes'], _tree_hashes(staged))
            self.assertEqual(save.read_bytes(), b'prepared save')
            return {'valid': True,'addon_hashes':dict(data['addon_hashes'])}
        with patch('workshop.transactions.verify_transaction', side_effect=verify):
            result = self.state.save_portrait(body)
        self.assertEqual(result['portrait_source'], 'rendered_model')
        self.assertEqual(_tree_hashes(self.addon), original_addon)
        self.assertEqual(json.loads(tx_path.read_text())['addon_hashes'], _tree_hashes(staged))
        self.assertEqual(portrait_state(staged, self.identifier)['source'], 'rendered_model')


if __name__ == '__main__':
    unittest.main()
