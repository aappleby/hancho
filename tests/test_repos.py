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


def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    tests.addTests(doctests)
    return tests


####################################################################################################


class TestRepos(unittest.TestCase):
    def setUp(self):
        hancho.init(verbosity = "quiet")
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    # FIXME
    def _test_sticky_hancho(self):
        # Objects stuck to the hancho module should be visible from all loaded scripts and repos.
        result = subprocess.run(
            ["python3", "../hancho.py", "-v=quiet", "-f", "sticky_hancho1.hancho"],
            cwd=os.path.dirname(__file__),
        )
        self.assertEqual(0, result.returncode)


####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
