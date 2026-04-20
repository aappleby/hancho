#!/usr/bin/python3
"""Test cases for Hancho's text templating system"""

import sys
import unittest

sys.path.append("..")
from hancho import Dict


####################################################################################################

class TestTemplates(unittest.TestCase):
    def setUp(self):
        sys.stdout.flush()

    #def test_basic_expansion(self):
    #    d = Dict(a = 1, b = 2, c = 3)
    #    e = d.expand("{a}{b}{c}")
    #    self.assertEqual(e, "123")

####################################################################################################

if __name__ == "__main__":
    unittest.main()
