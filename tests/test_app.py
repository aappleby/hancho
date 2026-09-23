#!/usr/bin/python3
"""Template file for creating new test cases"""

import argparse
import contextvars
import doctest
import os
import re
import subprocess
import sys
import textwrap
import unittest
from io import StringIO

import hancho

Log = hancho.Log
Utils = hancho.Utils
Dumper = hancho.Dumper

def dump(text, **kwargs):
    return Dumper.depointer(Dumper.dump(text, **kwargs))

# the hancho references hit this and it's bogus because of the weird way hancho intercepts
# attributes
# pyright: reportAttributeAccessIssue=false

# FIXME we need some tests that run hancho as if it were launched from the command line

####################################################################################################

def setUpModule():
    os.chdir(os.path.dirname(__file__))

def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    tests.addTests(doctests)
    return tests

####################################################################################################
# High level tests that don't belong in one of the other test suites

class TestApp(unittest.TestCase):
    def setUp(self):
        self.old_stdout = sys.stdout
        sys.stdout = StringIO()

    def init(self, *, argv):
        global hancho
        hancho = hancho.init_for_testing(argv = [*argv, f"--script.path={__file__}"]) # type: ignore

    def tearDown(self):
        sys.stdout = self.old_stdout
        sys.stdout.flush()

    def test_foo(self):
        pass

    def test_we_can_show_help(self):
        cmd = [sys.executable, "../hancho.py", "--help"]
        result = subprocess.run(cmd, capture_output = True, text=True)
        self.assertIn("Hancho is a simple, pleasant build system", result.stdout)

    # FIXME disabling this while we fiddle with what should/should not be ferried between scripts
#    def _test_script_globals(self):
#        hancho.foo = 12
#        script = hancho.cv_script.get()
#        self.assertEqual(12, script.globals.foo)

    def test_verbosities(self):
        self.init(argv = ["--log.level=debug"])
        self.assertEqual(Log.DEBUG, Log.log_level)

        self.init(argv = ["--log.level=info"])
        self.assertEqual(Log.INFO, Log.log_level)

        self.init(argv = ["--log.level=warning"])
        self.assertEqual(Log.WARNING, Log.log_level)

        self.init(argv = ["--log.level=error"])
        self.assertEqual(Log.ERROR, Log.log_level)

        self.init(argv = ["--log.level=critical"])
        self.assertEqual(Log.CRITICAL, Log.log_level)

        cmd = [sys.executable, "../hancho.py", "--log.level=boo"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertIn("invalid choice: 'boo'", result.stderr)

    def test_indentation(self):
        self.init(argv = ["--log.color=False", "--log.time=False"])
        Log.info("line1\n")
        Log.indent(0xFFFFFFFF)
        Log.info("line2\n")
        Log.dedent()
        Log.info("line3\n")
        self.assertEqual('line1\n│ line2\nline3\n', sys.stdout.getvalue())

    def test_no_color(self):
        self.init(argv = ["--log.color=False", "--log.time=False"])
        Log.info("this should _not_ be blue\n")
        self.assertEqual("this should _not_ be blue\n", sys.stdout.getvalue())
        self.assertNotIn("\x1B", sys.stdout.getvalue())

    def test_newlines(self):
        self.init(argv = ["--log.color=False", "--log.time=False"])
        Log.info("one")
        Log.info("two")
        Log.info("three")
        Log.info("four\n")
        self.assertEqual('onetwothreefour\n', sys.stdout.getvalue())

    def test_flush(self):
        self.init(argv = ["--log.color=False", "--log.time=False"])
        Log.info("one")
        Log.info("two")
        Log.info("three")
        self.assertEqual('', sys.stdout.getvalue())
        Log._flush()
        self.assertEqual('onetwothree\n', sys.stdout.getvalue())

    def test_indent_dedent(self):
        self.init(argv = ["--log.color=False", "--log.time=False"])

        Log.info("┌ one\n")
        Log.indent(0xFFFFFFFF)
        Log.info("boop\n")
        Log.dedent()
        Log.info("└ two\n")
        Log.info("soop\n")

        text = '┌ one\n│ boop\n└ two\nsoop\n'
        self.assertEqual(text, sys.stdout.getvalue())

    def test_dumper(self):
        self.init(argv = ["--log.color=False", "--log.time=False"])

        def check(value, expected, **kwargs):
            result = dump(value, **kwargs)
            expected = textwrap.dedent(expected).strip()
            self.assertEqual(result, expected)

        thing1 = {
            "a": 1,
            "b": [2, "two"],
            "c": (3, 3, 3),
            "d": object(),
            "e": "foobar",
        }

        expected = """
        :dict = {
            a = 1,
            b:list = [2, 'two'],
            c:tuple = (3, 3, 3),
            d:object = <object object at 0x...>,
            e = 'foobar'
        }
        """
        check(thing1, expected, print_id = False)


        expected = """
        :dict@0x... = {
            a = 1,
            b:list@0x... = [2, 'two'],
            c:tuple@0x... = (3, 3, 3),
            d:object@0x... = <object object at 0x...>,
            e = 'foobar'
        }
        """
        check(thing1, expected)

        check(contextvars.Context(), ":Context@0x... = {}")

        check(print, ":builtin_function_or_method@0x... = <built-in function print>")

    def test_weave(self):
        a = ["a", "b", "c"]
        b = ["1", "2", "3"]
        c = Utils.weave(a, b)
        self.assertEqual(['a1', 'a2', 'a3', 'b1', 'b2', 'b3', 'c1', 'c2', 'c3'], c)
#
