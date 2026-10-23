import os
import sys
import unittest

import hancho

# --------------------------------------------------------------------------------------------------

class TestUtils(unittest.TestCase):
    def setUp(self):
        os.chdir(os.path.dirname(__file__))
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    def test_trivial(self):
        self.assertIsNotNone(hancho)

    def test_weave(self):
        a = ["a", "b", "c"]
        b = ["1", "2", "3"]
        c = hancho.Utils.weave(a, b)
        self.assertEqual(['a1', 'a2', 'a3', 'b1', 'b2', 'b3', 'c1', 'c2', 'c3'], c)

# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=999)
