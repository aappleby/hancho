#!/usr/bin/python3
"""Template file for creating new test cases"""

import sys
import unittest
import os
import shutil
from pathlib import Path
import time
import random
from typing import cast
import doctest
import asyncio
import glob
import subprocess

sys.path.append("..")
import hancho

####################################################################################################

def setUpModule():
    # Change to your desired directory
    #os.chdir(this_dir)
    pass

def mtime_ns(filename):
    return os.stat(filename).st_mtime_ns

def force_touch(filename):
    if not Path(filename).exists():
        Path(filename).touch()
    old_mtime = mtime_ns(filename)
    while old_mtime == mtime_ns(filename):
        os.utime(filename, None)

def color(red=None, green=None, blue=None):
    """Converts RGB color to ANSI format string."""
    # Color strings don't work in Windows console, so don't emit them.
    if os.name == "nt":
        return ""
    if red is None:
        return "\x1B[0m"
    return f"\x1B[38;2;{red};{green};{blue}m"

####################################################################################################

class TestRepos(unittest.TestCase):

    def test_sticky_hancho(self):
        # Objects stuck to the hancho module should be visible from all loaded scripts and repos.
        result = subprocess.run("python3 ../hancho.py -f sticky_hancho1.hancho".split())
        self.assertEqual(0, result.returncode)

####################################################################################################

def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    for t in doctests:
        t.shortDescription = lambda: None # type: ignore
    tests.addTests(doctests)
    return tests

if __name__ == "__main__":
    unittest.main(verbosity=999)
