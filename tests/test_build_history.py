import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from workshop.app import AppState


class BuildHistoryTests(unittest.TestCase):
    def test_every_build_install_and_update_is_kept_one_line_each(self):
        with tempfile.TemporaryDirectory() as temp:
            state = AppState(Path(temp))
            state.record_history('built', {'vehicle': 'mw_1', 'save': 'conquest 12.sav', 'transform': {'rider_mgs': []}})
            state.record_history('installed', {'vehicle': 'mw_1'})
            state.record_history('updated', {'rebuilt': ['mw_1'], 'skipped': []})
            rows = [json.loads(line) for line in (Path(temp) / 'history' / 'builds.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['event'] for r in rows], ['built', 'installed', 'updated'])
        self.assertTrue(all(r['tool_version'] and r['time'] for r in rows))
        self.assertEqual(rows[0]['vehicle'], 'mw_1')

    def test_report_uses_only_the_open_campaign_never_the_latest_build(self):
        with tempfile.TemporaryDirectory() as temp:
            state = AppState(Path(temp))
            self.assertIsNone(state.report_save())
            save = Path(temp) / 'campaign.sav'
            save.write_bytes(b'save')
            operation = Path(temp) / 'operations' / '20261022_1'
            operation.mkdir(parents=True)
            (operation / 'result.json').write_text(json.dumps({'source_path': str(save)}), encoding='utf-8')
            self.assertIsNone(state.report_save(), 'A previous build is not a selected campaign')
            selected=Path(temp)/'selected.sav';selected.write_bytes(b'selected')
            state.snapshot=SimpleNamespace(source_path=selected)
            self.assertEqual(state.report_save(), selected)
            selected.unlink()
            self.assertIsNone(state.report_save(), 'A missing selected file must not fall back to another save')
            state.pool.shutdown(wait=True)


if __name__ == '__main__':
    unittest.main()
