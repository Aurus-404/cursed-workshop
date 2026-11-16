import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workshop.editor_draft import access,valid_options

class EditorDraftTests(unittest.TestCase):
    def test_persistent_recovery_and_explicit_discard(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);draft={'schema':1,'hull':{'template':'tank'},'transform':{'hull':{'position':[1,2,3]}}}
            self.assertIsNone(access(root)['draft'])
            access(root,{'draft':draft});self.assertEqual(access(root)['draft'],draft)
            self.assertFalse((root/'editor-draft.tmp').exists())
            access(root,{'draft':None});self.assertIsNone(access(root)['draft'])

    def test_interrupted_replace_preserves_original_and_rejects_invalid_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);draft={'schema':1,'hull':{},'transform':{}}
            access(root,{'draft':draft});before=(root/'editor-draft.json').read_bytes()
            with patch('workshop.editor_draft.os.replace',side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):access(root,{'draft':None})
            self.assertEqual((root/'editor-draft.json').read_bytes(),before)
            for payload in ({},{'draft':[]},{'draft':{'schema':1}}, {'draft':{**draft,'transform':{'x':float('nan')}}}):
                with self.assertRaises((ValueError,TypeError)):access(root,payload)
            self.assertEqual((root/'editor-draft.json').read_bytes(),before)

    def test_options_are_typed_and_bounded(self):
        self.assertTrue(valid_options({'autosave':True,'angle_step':45,'bindings':{'move':'G','snap_hold':'Control'}}))
        for value in ({'snap':1},{'bindings':{'unknown':'F'}},{'angle_step':float('nan')},{'move_step':0},[]):
            self.assertFalse(valid_options(value))
