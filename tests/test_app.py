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

proxy = hancho.Hancho.proxy

####################################################################################################
# High level tests that don't belong in one of the other test suites

class TestApp(unittest.TestCase):
    def setUp(self):
        self.old_stdout = sys.stdout
        sys.stdout = StringIO()

    def init(self, **kwargs):
        hancho.init_for_testing(argv = [], **kwargs) # type: ignore

    def tearDown(self):
        sys.stdout = self.old_stdout
        sys.stdout.flush()

    def test_foo(self):
        pass

    def test_we_can_show_help(self):
        cmd = [sys.executable, "../hancho.py", "--help"]
        result = subprocess.check_output(cmd, text=True)
        self.assertTrue("Hancho is a simple, pleasant build system" in result)

    def test_log_levels(self):
        pass

    # FIXME disabling this while we fiddle with what should/should not be ferried between scripts
#    def test_script_globals(self):
#        hancho.foo = 12
#        script = hancho.cv_script.get()
#        self.assertEqual(12, script.globals.foo)

    def test_integer_verbosity(self):
        self.init(log_level = 40)
        self.assertEqual(hancho.Log.Level.WARNING, hancho.Log.log_level_out)

    def test_verbosities(self):
        self.init(log_verbose = True)
        self.assertEqual(hancho.Log.Level.VERBOSE, hancho.Log.log_level_out)
        self.init(log_debug = True)
        self.assertEqual(hancho.Log.Level.DEBUG, hancho.Log.log_level_out)
        self.init(log_quiet = True)
        self.assertEqual(hancho.Log.Level.QUIET, hancho.Log.log_level_out)

        with self.assertRaises(ValueError):
            self.init(log_level = ["boo"])

    def test_indentation(self):
        self.init(log_color = False, log_time = False)
        hancho.Log.log("line1\n")
        hancho.Log.indent(0xFFFFFFFF)
        hancho.Log.log("line2\n")
        hancho.Log.dedent()
        hancho.Log.log("line3\n")

        self.assertEqual('line1\n│ line2\nline3\n', sys.stdout.getvalue())

    def test_no_color(self):
        self.init(log_color = False, log_time = False)
        hancho.Log.log("this should _not_ be blue\n")
        self.assertEqual("this should _not_ be blue\n", sys.stdout.getvalue())
        self.assertNotIn("\x1B", sys.stdout.getvalue())

    def test_newlines(self):
        self.init(log_color = False, log_time = False)
        hancho.Log.log("one")
        hancho.Log.log("two")
        hancho.Log.log("three")
        hancho.Log.log("four\n")
        self.assertEqual('onetwothreefour\n', sys.stdout.getvalue())

    def test_flush(self):
        self.init(log_color = False, log_time = False)
        hancho.Log.log("one")
        hancho.Log.log("two")
        hancho.Log.log("three")
        hancho.Log.flush()
        self.assertEqual('onetwothree\n', sys.stdout.getvalue())

    def test_indent_dedent(self):
        self.init(log_color = False, log_time = False)

        hancho.Log.log("┌ one\n")
        hancho.Log.indent(0xFFFFFFFF)
        hancho.Log.log("boop\n")
        hancho.Log.dedent()
        hancho.Log.log("└ two\n")
        hancho.Log.log("soop\n")

        text = '┌ one\n│ boop\n└ two\nsoop\n'
        self.assertEqual(text, sys.stdout.getvalue())

#        f = StringIO()
#        with redirect_stdout(f):
#            hancho.log_indent(0xFFFFFF, "one")
#            hancho.Log.log("boop\n")
#            hancho.log_dedent(0xFFFFFF, "two")
#            hancho.Log.log("soop\n")
#
#        text = 'oneboop\n└ twosoop\n'
#        self.assertEqual(text, f.getvalue())

    def test_hash(self):
        # The hash values themselves are meaningless, but we do want to check that they change when
        # the seed changes.

        # Byte strings
        val1 = hancho.Stats.hash(b'1234', 0)
        val2 = hancho.Stats.hash(b'1234', 1)
        val3 = hancho.Stats.hash(b'2234', 0)
        self.assertNotEqual(val1, val2, val3)

        # String strings. Since there's no utf8 encoding going on, these should hash to the same
        # values as byte strings.
        val1 = hancho.Stats.hash('1234', 0)
        val2 = hancho.Stats.hash('1234', 1)
        val3 = hancho.Stats.hash('2234', 0)
        self.assertNotEqual(val1, val2, val3)

        # Functions
        def foo(): return 1 #type:ignore
        val1 = hancho.Stats.hash(foo, 0)
        def foo(): return 2
        val2 = hancho.Stats.hash(foo, 0)
        def goo(): return 2
        val3 = hancho.Stats.hash(goo, 0)
        self.assertNotEqual(val1, val2, val3)

        # Lists
        val1 = hancho.Stats.hash([1, 2, 3], 0)
        val2 = hancho.Stats.hash([1, 2, 3], 1)
        val3 = hancho.Stats.hash([1, 2, 3, 0], 0)
        self.assertNotEqual(val1, val2, val3)

        # Ints
        val1 = hancho.Stats.hash(123456789, 0)
        val2 = hancho.Stats.hash(123456789, 1)
        val3 = hancho.Stats.hash(123456788, 0)
        self.assertNotEqual(val1, val2, val3)

        # Dicts
        val1 = hancho.Stats.hash({"a":1, "b":2, "c":3}, 0)
        val2 = hancho.Stats.hash({"a":1, "b":2, "c":3}, 1)
        val3 = hancho.Stats.hash({"a":1, "b":2, "c":4}, 0)
        self.assertNotEqual(val1, val2, val3)

        # Should assert on anything else
        with self.assertRaises(TypeError):
            val1 = hancho.Stats.hash(subprocess, 0)

    def test_dumper(self):
        def dump(text, **kwargs):
            return hancho.Dumper .depointer(hancho.Dumper.dump(text, **kwargs))

        thing1 = {"a": 1, "b":[2, "two"], "c":(3,3,3), "d":object()}

        d = dump(thing1, print_id = False)
        print(d)
        expected = textwrap.dedent(
        """
        {
            a = 1,
            b = [2, 'two'],
            c = (3, 3, 3),
            d: object = <object object at 0x...>
        }
        """).strip()
        self.assertEqual(d, expected)

        d = dump(thing1, print_id = True)
        expected = textwrap.dedent("""
        {
            a = 1,
            b = [2, 'two'],
            c = (3, 3, 3),
            d: object@0x... = <object object at 0x...>
        }
        """).strip()
        self.assertEqual(expected, d)

        d = dump(contextvars.Context())
        self.assertEqual("Context@0x... = {}", d)

        d = dump(contextvars)
        d = re.sub("from .*", "", d)
        self.assertEqual( "module@0x... = <module 'contextvars' ", d)

        d = dump(print)
        self.assertEqual("<built-in function print>", d)

        def blep():
            pass

        d = dump(blep)
        self.assertEqual("function@0x... = <function TestApp.test_dumper.<locals>.blep at 0x...>", d)

        n = argparse.Namespace(foo = 1, bar = 2)
        d =dump(n)
        self.assertEqual("Namespace@0x... = {foo = 1, bar = 2}", d)

        class Blarp:
            pass

        d = dump(Blarp())
        self.assertEqual("Blarp@0x... = {}", d)

    def test_weave(self):
        a = ["a", "b", "c"]
        b = ["1", "2", "3"]
        c = hancho.Utils.weave(a, b)
        self.assertEqual(['a1', 'a2', 'a3', 'b1', 'b2', 'b3', 'c1', 'c2', 'c3'], c)
#
