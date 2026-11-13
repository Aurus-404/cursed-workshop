import os
import unittest

from workshop import memory


class MemoryLogTests(unittest.TestCase):
    def test_describe_never_fails(self):
        text = memory.describe()
        self.assertIsInstance(text, str)
        if os.name == 'nt':
            figures = memory.snapshot()
            self.assertGreater(figures['rss'], 0)
            self.assertGreater(figures['total'], 0)
            self.assertIn('free=', text)


if __name__ == '__main__':
    unittest.main()


class MemoryGuardTests(unittest.TestCase):
    def test_a_runaway_background_read_stops_and_names_the_file(self):
        from unittest.mock import patch
        from workshop import memory
        with patch.object(memory, 'snapshot', return_value={'rss': 900, 'peak': 900, 'available': 9000, 'total': 16000}):
            memory.guard('entity/a.def')
        with patch.object(memory, 'snapshot', return_value={'rss': 12000, 'peak': 12000, 'available': 500, 'total': 16000}):
            with self.assertRaisesRegex(memory.MemoryLimitError, 'entity/b.def'):
                memory.guard('entity/b.def')
        with patch.object(memory, 'snapshot', return_value={}):
            memory.guard('entity/c.def')  # Linux native: no figures, never stops
