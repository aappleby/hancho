#!/usr/bin/python3
"""Test cases for Hancho's script loader/runner"""

import unittest

import hancho

# --------------------------------------------------------------------------------------------------

class TestScript(unittest.TestCase):

    def test_none(self):
        pass

    def test_basic(self):
        self.assertIsNotNone(hancho)
        #print(hancho)
        #print(dir(hancho))
        #print(hancho.Task)


# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=999)
