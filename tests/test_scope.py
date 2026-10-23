import os
import sys
import unittest

import hancho

# --------------------------------------------------------------------------------------------------

class TestScope(unittest.TestCase):
    def setUp(self):
        os.chdir(os.path.dirname(__file__))
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    def test_trivial(self):
        self.assertIsNotNone(hancho)

# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=999)
