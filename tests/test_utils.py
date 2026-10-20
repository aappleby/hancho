#!/usr/bin/python3
"""Test cases for Hancho's text templating system"""

import os
import sys
import unittest

sys.path.append("..")
import hancho

VERBOSITY = "critical"

# the hancho references hit this and it's bogus because of the weird way hancho intercepts
# attributes
# pyright: reportAttributeAccessIssue=false

####################################################################################################

def setUpModule():
    os.chdir(os.path.dirname(__file__))

####################################################################################################

class TestUtils(unittest.TestCase):
    def test_none(self):
        self.assertIsNotNone(hancho.Utils)

    def test_weave(self):
        a = ["a", "b", "c"]
        b = ["1", "2", "3"]
        c = hancho.Utils.weave(a, b)
        self.assertEqual(['a1', 'a2', 'a3', 'b1', 'b2', 'b3', 'c1', 'c2', 'c3'], c)

####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
