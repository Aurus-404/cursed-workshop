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
