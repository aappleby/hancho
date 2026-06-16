#!/usr/bin/python3
"""Template file for creating new test cases"""

import doctest
import os
import subprocess
import sys
import unittest

import hancho

####################################################################################################


def setUpModule():
    os.chdir(os.path.dirname(__file__))
    hancho.init(verbosity = "quiet")


def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    tests.addTests(doctests)
    return tests


####################################################################################################
# High level tests that don't belong in one of the other test suites

class TestApp(unittest.TestCase):
    def setUp(self):
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    def test_foo(self):
        pass

    def test_we_can_show_help(self):
        cmd = ["python3", "../hancho.py", "--help"]
        result = subprocess.check_output(cmd, text=True)
        self.assertTrue("Hancho is a simple, pleasant build system" in result)

    def test_log_levels(self):
        pass



####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
