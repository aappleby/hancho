import os
import sys
import unittest

import hancho

# --------------------------------------------------------------------------------------------------

class TestScripts(unittest.TestCase):
    def setUp(self) -> None:
        os.chdir(os.path.dirname(__file__))
        sys.stdout.flush()

    def tearDown(self) -> None:
        sys.stdout.flush()

    def test_trivial(self) -> None:
        self.assertIsNotNone(hancho)
