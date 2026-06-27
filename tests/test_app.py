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
    hancho.init(verbosity = "quiet")


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
        hancho.init(verbosity = 40)
        self.assertEqual(hancho.LogLevel.WARNING, hancho.Log.verbosity_out)

    def test_verbosities(self):
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
        hancho.init(log_color = False, log_timestamp = False)
        hancho.Log.log("line1\n")
        hancho.Log.indent2(0xFFFFFFFF)
        hancho.Log.log("line2\n")
        hancho.Log.dedent2()
        hancho.Log.log("line3\n")

        self.assertEqual('line1\n│ line2\nline3\n', sys.stdout.getvalue())

    def test_no_color(self):
        hancho.init(log_color = False, log_timestamp = False)
        hancho.Log.log("this should _not_ be blue\n")
        self.assertEqual("this should _not_ be blue\n", sys.stdout.getvalue())
        self.assertNotIn("\x1B", sys.stdout.getvalue())

    def test_newlines(self):
        hancho.init(log_color = False, log_timestamp = False)
        hancho.Log.log("one")
        hancho.Log.log("two")
        hancho.Log.log("three")
        hancho.Log.log("four\n")
        self.assertEqual('onetwothreefour\n', sys.stdout.getvalue())

    def test_flush(self):
        hancho.init(log_color = False, log_timestamp = False)
        hancho.Log.log("one")
        hancho.Log.log("two")
        hancho.Log.log("three")
        hancho.Log.flush()
        self.assertEqual('onetwothree\n', sys.stdout.getvalue())

    def test_indent_dedent(self):
        hancho.init(log_color = False, log_timestamp = False)

        hancho.Log.log("┌ one\n")
        hancho.Log.indent2(0xFFFFFFFF)
        hancho.log("boop\n")
        hancho.Log.dedent2()
        hancho.Log.log("└ two\n")
        hancho.log("soop\n")

        text = '┌ one\n│ boop\n└ two\nsoop\n'
        self.assertEqual(text, sys.stdout.getvalue())

#        f = StringIO()
#        with redirect_stdout(f):
#            hancho.Log.log_indent(0xFFFFFF, "one")
#            hancho.log("boop\n")
#            hancho.Log.log_dedent(0xFFFFFF, "two")
#            hancho.log("soop\n")
#
#        text = 'oneboop\n└ twosoop\n'
#        self.assertEqual(text, f.getvalue())

    def test_hash(self):
        # The hash values themselves are meaningless, but we do want to check that they change when
        # the seed changes.

        # Byte strings
        val1 = hancho.Utils.hash(b'1234', 0)
        val2 = hancho.Utils.hash(b'1234', 1)
        val3 = hancho.Utils.hash(b'2234', 0)
        self.assertNotEqual(val1, val2, val3)

        # String strings. Since there's no utf8 encoding going on, these should hash to the same
        # values as byte strings.
        val1 = hancho.Utils.hash('1234', 0)
        val2 = hancho.Utils.hash('1234', 1)
        val3 = hancho.Utils.hash('2234', 0)
        self.assertNotEqual(val1, val2, val3)

        # Functions
        def foo(): return 1 #type:ignore
        val1 = hancho.Utils.hash(foo, 0)
        def foo(): return 2
        val2 = hancho.Utils.hash(foo, 0)
        def goo(): return 2
        val3 = hancho.Utils.hash(goo, 0)
        self.assertNotEqual(val1, val2, val3)

        # Lists
        val1 = hancho.Utils.hash([1, 2, 3], 0)
        val2 = hancho.Utils.hash([1, 2, 3], 1)
        val3 = hancho.Utils.hash([1, 2, 3, 0], 0)
        self.assertNotEqual(val1, val2, val3)

        # Ints
        val1 = hancho.Utils.hash(123456789, 0)
        val2 = hancho.Utils.hash(123456789, 1)
        val3 = hancho.Utils.hash(123456788, 0)
        self.assertNotEqual(val1, val2, val3)

        # Dicts
        val1 = hancho.Utils.hash({"a":1, "b":2, "c":3}, 0)
        val2 = hancho.Utils.hash({"a":1, "b":2, "c":3}, 1)
        val3 = hancho.Utils.hash({"a":1, "b":2, "c":4}, 0)
        self.assertNotEqual(val1, val2, val3)

        # Should assert on anything else
        with self.assertRaises(TypeError):
            val1 = hancho.Utils.hash(subprocess, 0)

    def test_dumper(self):
        thing1 = {"a": 1, "b":[2, "two"], "c":(3,3,3)}
        d = hancho.Utils.dump_to_str("name", thing1)
        self.assertEqual("name: dict = {a = 1, b = [2, 'two'], c = (3, 3, 3)}", d)

        # Print IDs, but erase pointers before comparing
        d = hancho.Utils.dump_to_str("name", thing1, print_id = True, max_width = 80)
        match_pointer : re.Pattern = re.compile(r"0[xX][0-9a-fA-F]+")
        d = match_pointer.sub("0x?", d)

        expected = textwrap.dedent("""
        name: dict: 0x? = {
            a: 0x? = 1,
            b: 0x? = [
                : 0x? = 2,
                : 0x? = 'two'
            ],
            c: 0x? = (
                : 0x? = 3,
                : 0x? = 3,
                : 0x? = 3
            )
        }
        """).strip()
        self.assertEqual(expected, d)

        c = contextvars.Context()
        d = hancho.Utils.dump_to_str("name", c)
        self.assertEqual("name: Context = '<Context>'", d)

        d = hancho.Utils.dump_to_str("name", contextvars)
        self.assertEqual("name = '<Module contextvars>'", d)

        d = hancho.Utils.dump_to_str("name", print)
        self.assertEqual("name = <builtin>", d)

        def blep():
            pass

        d = hancho.Utils.dump_to_str("name", blep)
        self.assertEqual("name: function = '<Function blep>'", d)

        n = argparse.Namespace(foo = 1, bar = 2)
        d = hancho.Utils.dump_to_str("name", n)
        self.assertEqual("name: Namespace = {foo = 1, bar = 2}", d)

        class Blarp:
            pass

        d = hancho.Utils.dump_to_str("name", Blarp())
        self.assertEqual("name: Blarp = <object>", d)

    def test_weave(self):
        a = ["a", "b", "c"]
        b = ["1", "2", "3"]
        c = hancho.Utils.weave(a, b)
        self.assertEqual(['a1', 'a2', 'a3', 'b1', 'b2', 'b3', 'c1', 'c2', 'c3'], c)
#
