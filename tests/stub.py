#!/usr/bin/python3
"""Template file for creating new test cases"""

from pathlib import Path
from typing import cast
import doctest
import hancho
import os
import sys
import unittest

####################################################################################################

def setUpModule():
    os.chdir(os.path.dirname(__file__))
    hancho.init(quiet = True)

def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    for t in doctests:
        t.shortDescription = lambda: None # type: ignore
    tests.addTests(doctests)
    return tests

####################################################################################################

class TestTest(unittest.TestCase):

    def setUp(self):
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    def test_foo(self):
        pass

####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
