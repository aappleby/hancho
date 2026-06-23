#!/usr/bin/python3
"""Template file for creating new test cases"""

import doctest
import os
import subprocess
import sys
import textwrap
import unittest
from contextlib import redirect_stdout
from io import StringIO

import hancho

# the hancho references hit this and it's bogus because of the weird way hancho intercepts
# attributes
# pyright: reportAttributeAccessIssue=false

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

    def test_script_globals(self):
        hancho.foo = 12
        script = hancho.cv_script.get()
        self.assertEqual(12, script.globals.foo)

    def test_integer_verbosity(self):
        hancho.init(verbosity = 40)
        self.assertEqual(hancho.LogLevel.WARNING, hancho.Log.verbosity_out)

    def test_verbosities(self):
        f = StringIO()
        with redirect_stdout(f):
            hancho.init(trace = True)
            self.assertEqual(hancho.LogLevel.TRACE, hancho.Log.verbosity_out)
            hancho.init(verbose = True)
            self.assertEqual(hancho.LogLevel.VERBOSE, hancho.Log.verbosity_out)
            hancho.init(debug = True)
            self.assertEqual(hancho.LogLevel.DEBUG, hancho.Log.verbosity_out)
            hancho.init(quiet = True)
            self.assertEqual(hancho.LogLevel.QUIET, hancho.Log.verbosity_out)

            with self.assertRaises(ValueError):
                hancho.init(verbosity = ["boo"])

    def test_indentation(self):
        f = StringIO()
        hancho.init(no_color = True)
        with redirect_stdout(f):
            hancho.Log.log("line1\n")
            with hancho.Log.indent(0):
                hancho.Log.log("line2\n")

        self.assertIn("line1", f.getvalue())
        self.assertIn("│ line2", f.getvalue())

    def test_no_color(self):
        f = StringIO()
        hancho.init(log_color = False, log_timestamp = False)
        with redirect_stdout(f):
            hancho.Log.log("this should _not_ be blue\n")
        self.assertEqual("this should _not_ be blue\n", f.getvalue())
        self.assertNotIn("\x1B", f.getvalue())

    def test_newlines(self):
        f = StringIO()
        hancho.init(log_color = False, log_timestamp = False)
        with redirect_stdout(f):
            hancho.Log.log("one")
            hancho.Log.log("two")
            hancho.Log.log("three")
            hancho.Log.log("four\n")
        self.assertEqual('onetwothreefour\n', f.getvalue())

    def test_flush(self):
        f = StringIO()
        hancho.init(log_color = False, log_timestamp = False)
        with redirect_stdout(f):
            hancho.Log.log("one")
            hancho.Log.log("two")
            hancho.Log.log("three")
            hancho.Log.flush()
        self.assertEqual('onetwothree\n', f.getvalue())

    def test_indent_dedent(self):
        hancho.init(log_color = False, log_timestamp = False)

        f = StringIO()
        with redirect_stdout(f):
            hancho.Log.log_indent(0xFFFFFF, "one\n")
            hancho.log("boop\n")
            hancho.Log.log_dedent(0xFFFFFF, "two\n")
            hancho.log("soop\n")

        text = 'one\n│ boop\n└ two\nsoop\n'
        self.assertEqual(text, f.getvalue())

        f = StringIO()
        with redirect_stdout(f):
            hancho.Log.log_indent(0xFFFFFF, "one")
            hancho.log("boop\n")
            hancho.Log.log_dedent(0xFFFFFF, "two")
            hancho.log("soop\n")

        text = 'oneboop\n└ twosoop\n'
        self.assertEqual(text, f.getvalue())

    def test_hash(self):
        pass