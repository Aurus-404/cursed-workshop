import json
import tempfile
import unittest
from pathlib import Path

from workshop import design_templates as templates


def design(**extra):
    return {'hull': {'position': [0, 0, 0], 'rotation': [0, 0, 0]},
            'assembly_draft': {'schema': 1, 'guns': [], 'turrets': []}, **extra}


class DesignTemplateTests(unittest.TestCase):
    # AGrievousGuy (Discord): modify one of four captured Bren carriers and
    # apply the same changes to the other three.
    def test_saved_templates_are_listed_replaced_and_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            armored = design(extra_armor=[{'asset': 'panzer4h#shieldl@body', 'position': [1, 2, 3]}])
            rows = templates.save(tmp, {'name': '  Pz IV   blindado ', 'chassis': 'panzer4h', 'label': 'Pz.Kpfw IV Ausf.H', 'transform': armored})
            self.assertEqual([r['name'] for r in rows], ['Pz IV blindado'])
            self.assertEqual(rows[0]['transform'], armored)
            templates.save(tmp, {'name': 'Pz IV blindado', 'chassis': 'panzer4h', 'transform': design(mobility={'speed': 50})})
            rows = templates.listing(tmp)
            self.assertEqual(len(rows), 1, 'the same name replaces the template')
            self.assertEqual(rows[0]['transform']['mobility'], {'speed': 50})
            self.assertEqual(json.loads((Path(tmp) / templates.FILE).read_text(encoding='utf-8'))['Pz IV blindado']['chassis'], 'panzer4h')
            self.assertEqual(templates.delete(tmp, {'name': 'Pz IV blindado'}), [])

    def test_malformed_templates_are_refused_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ({'name': '', 'chassis': 'bren', 'transform': design()},
                        {'name': 'x' * 81, 'chassis': 'bren', 'transform': design()},
                        {'name': 'a', 'chassis': '', 'transform': design()},
                        {'name': 'a', 'chassis': 'bren', 'transform': {'hull': {}}},
                        {'name': 'a', 'chassis': 'bren', 'transform': design(extra_armor=['x' * templates.SIZE])}):
                with self.subTest(bad=str(bad)[:50]), self.assertRaises(ValueError):
                    templates.save(tmp, bad)
            self.assertFalse((Path(tmp) / templates.FILE).exists())

    def test_a_damaged_file_reads_as_no_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / templates.FILE).write_text('{broken', encoding='utf-8')
            self.assertEqual(templates.listing(tmp), [])
            self.assertEqual(len(templates.save(tmp, {'name': 'a', 'chassis': 'bren', 'transform': design()})), 1)


if __name__ == '__main__':
    unittest.main()
