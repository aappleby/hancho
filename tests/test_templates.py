#!/usr/bin/python3
"""Test cases for Hancho's text templating system"""

import sys
import unittest

sys.path.append("..")

####################################################################################################

class TestTemplates(unittest.TestCase):
    def setUp(self):
        sys.stdout.flush()

####################################################################################################

if __name__ == "__main__":
    unittest.main()
