"""Support reports use isolated fixtures and never export full application state."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workshop.app import AppState
from workshop.diagnostics import Diagnostics, clean_text


class DiagnosticTests(unittest.TestCase):
    def test_packaged_build_metadata_is_fixed_bounded_and_whitelisted(self):
        from workshop import build_info
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'build_info.json'
            with patch.object(build_info,'__file__',str(Path(folder)/'build_info.py')):
                path.write_text(json.dumps({'source_sha256':'a'*64,'revision':'b'*40,'dirty_source':False,
                    'built_utc':'2026-10-18T00:00:00+00:00','private_path':'C:/private'}))
                result=build_info.read_build_info()
                self.assertEqual(result['revision'],'b'*40)
                self.assertNotIn('private_path',result)
                path.write_text('{"source_sha256":"'+('a'*5000)+'"}')
                self.assertNotIn('source_sha256',build_info.read_build_info())
    def test_phase_timings_are_separate_bounded_and_redacted(self):
        history=Diagnostics()
        for n in range(30):history.record_phase(f'Phase {n}','done',n+.25)
        history.record_phase('ignored','done',float('nan'))
        history.record_operation('Build','done',1000,0)
        result=history.snapshot(None,False)
        self.assertEqual(len(result['recent_phases']),24)
        self.assertEqual(result['recent_phases'][0]['operation'],'Phase 6')
        self.assertEqual(len(result['recent_operations']),1)
        history.record_phase('Phase TOKEN=hidden C:\\Users\\Private\\file','done',1)
        self.assertNotIn('Private',history.snapshot(None,False)['recent_phases'][-1]['operation'])

    def test_operation_timings_are_bounded_and_contain_only_numeric_durations(self):
        history=Diagnostics()
        for n in range(20):
            history.record_operation(f'Build {n}','done',n+.25,n+.75)
        for status,elapsed,queued in [('running',1,0),('done',float('nan'),0),('done',1,-1)]:
            history.record_operation('ignored',status,elapsed,queued)
        result=history.snapshot(None,False)['recent_operations']
        self.assertEqual(len(result),12)
        self.assertEqual(result[0]['operation'],'Build 8')
        self.assertEqual(result[-1]['elapsed_ms'],19)
        self.assertEqual(result[-1]['queued_ms'],20)
        self.assertEqual(set(result[-1]),{'operation','status','elapsed_ms','queued_ms','time_utc'})

    def test_text_is_bounded_and_scrubs_paths_credentials_mentions_and_controls(self):
        raw = 'Failure TOKEN=private password=bad https://bad.example/run C:\\Users\\Private\\save.sav\n'
        raw += '\\\\share\\private\n/home/private/file @everyone ``` \u202e\x00 SESSION'
        text = clean_text(raw, secrets=('SESSION',))
        for secret in ('private', 'bad.example', 'SESSION', '@', '`', '\u202e', '\x00'):
            self.assertNotIn(secret, text)
        self.assertLessEqual(len(clean_text('x' * 1000000)), 1220)

    def test_history_is_bounded_and_frames_never_include_paths_source_or_locals(self):
        history = Diagnostics()
        for i in range(20):
            try:
                private_local = 'do not export this'
                raise ValueError(f'failure {i}')
            except ValueError as exc:
                history.record(exc, 'Check compatibility')
        result = history.snapshot(None, False)
        self.assertEqual(len(result['errors']), 12)
        self.assertEqual(result['errors'][0]['message'], 'failure 8')
        frames = json.dumps(result['errors'][-1]['frames'])
        self.assertIn('test_diagnostics', frames)
        for private in (private_local, str(Path(__file__).parent), 'raise ValueError'):
            self.assertNotIn(private, frames)

    def test_app_report_never_reads_logs_saves_settings_or_processes(self):
        with tempfile.TemporaryDirectory() as folder:
            state = AppState(Path(folder))
            try:
                state.settings = {'game_root': 'private game', 'mod_roots': ['private mod']}
                state.snapshot = object()
                state.last_result = {'source_path': 'private save', 'transaction': 'private journal'}
                state.index = SimpleNamespace(sources=[{'id': '123', 'version': '2', 'root': 'private root', 'name': 'private name'}] * 70)
                state.record_error(ValueError('session ' + state.token), 'Open campaign')
                with patch.object(Path, 'read_text', side_effect=AssertionError('No file reads')), \
                     patch('workshop.app.game_state', side_effect=AssertionError('No process reads')):
                    result = state.diagnostic()
                serialized = json.dumps(result)
                for secret in ('private', state.token, 'source_path', 'settings', 'transaction'):
                    self.assertNotIn(secret, serialized)
                self.assertTrue(result['campaign_loaded'])
                self.assertEqual(len(result['sources']), 60)
                self.assertEqual(result['sources_omitted'], 10)
            finally:
                state.pool.shutdown(wait=True)

    def test_failed_job_and_cached_compatibility_failure_are_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            state = AppState(Path(folder))
            from workshop.dlc import Availability
            state.index = SimpleNamespace(sources=[], dlc_availability=Availability)
            try:
                with patch('workshop.app.logging.exception'):
                    state.job('Load model', lambda: (_ for _ in ()).throw(ValueError('Job failure')))
                    state.pool.shutdown(wait=True)
                with patch('workshop.compiler.compile_assembly', side_effect=ValueError('Compile failure')):
                    for _ in range(2):
                        self.assertFalse(state.check({'hull': 'h', 'donor': 'd'})['ok'])
                errors = state.diagnostic()['errors']
                self.assertEqual([e['message'] for e in errors], ['Job failure', 'Compile failure'])
                self.assertEqual(errors[1]['operation'], 'Compatibility check')
                operations=state.diagnostic()['recent_operations']
                self.assertEqual(len(operations),1)
                self.assertEqual(operations[0]['status'],'error')
                self.assertGreaterEqual(operations[0]['elapsed_ms'],0)
                self.assertGreaterEqual(operations[0]['queued_ms'],0)
            finally:
                state.pool.shutdown(wait=True)


if __name__ == '__main__':
    unittest.main()
