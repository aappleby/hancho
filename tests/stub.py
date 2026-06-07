#!/usr/bin/python3
"""Template file for creating new test cases"""

import doctest
import os
import sys
import unittest

import hancho

####################################################################################################


def setUpModule():
    os.chdir(os.path.dirname(__file__))
    hancho.init(verbosity = "QUIET")


def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
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
