#!/usr/bin/python3
"""Template file for creating new test cases"""

import os
import subprocess
import sys
import textwrap
import unittest
from io import StringIO

sys.path.append("..")
import hancho

# the hancho references hit this and it's bogus because of the weird way hancho intercepts
# attributes
# pyright: reportAttributeAccessIssue=false

# FIXME we need some tests that run hancho as if it were launched from the command line

VERBOSITY = "critical"

def setUpModule():
    #print("setUpModule")
    pass


def tearDownModule():
    #print("tearDownModule")
    pass

####################################################################################################
# High level tests that don't belong in one of the other test suites

class TestApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        #print("setUpClass")
        pass

    def setUp(self, *args):
        #print("setUp")
        os.chdir(os.path.dirname(__file__))
        self.maxDiff = 99999
        self.stdout = StringIO()

        options =[
            #f"--log.level={VERBOSITY}",
            "--log.level=info",
            "--log.wrap=False",
            "--log.color=False",
            "--log.timestamp=False",
            "--log.width=9999",
        ]

        self.script : hancho.Script = hancho.init_for_testing(__file__, *options, *args)
        self.log : hancho.Log = hancho.cv.log
        self.log._stdout = self.stdout

    def tearDown(self):
        #print("tearDown")
        pass

    @classmethod
    def tearDownClass(cls):
        #print("tearDownClass")
        pass

    # ----------------------------------------------------------------------------------------------

    def test_log_redirection(self):
        # Our default verbosity is 'critical', so these should appear in the log.
        self.log.critical("This is a message\n")
        self.log.critical("This is another message\n")
        self.log._flush()
        self.assertEqual("This is a message\nThis is another message\n", self.stdout.getvalue())

        # If we clear the log and print the same thing at 'trace' level they should not appear.
        self.stdout.seek(0)
        self.stdout.truncate(0)

        self.log.trace("This is a message\n")
        self.log.trace("This is another message\n")
        self.log._flush()
        self.assertEqual("", self.stdout.getvalue())

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
        self.setUp("--log.level=debug")
        self.assertEqual(self.log.DEBUG, self.log.level)

        self.setUp("--log.level=info")
        self.assertEqual(self.log.INFO, self.log.level)

        self.setUp("--log.level=warning")
        self.assertEqual(self.log.WARNING, self.log.level)

        self.setUp("--log.level=error")
        self.assertEqual(self.log.ERROR, self.log.level)

        self.setUp("--log.level=critical")
        self.assertEqual(self.log.CRITICAL, self.log.level)

        # FIXME We need to test this some other way, it seems to f' up the debugger
        #cmd = [sys.executable, "../hancho.py", "--log.level=boo"]
        #result = subprocess.run(cmd, capture_output=True, text=True)
        #self.assertIn("invalid choice: 'boo'", result.stderr)

    def test_indentation(self):
        self.log.info("line1\n")
        self.log.indent(hancho.Utils.hex_to_ansi(0xFFFFFFFF))
        self.log.info("line2\n")
        self.log.dedent()
        self.log.info("line3\n")
        self.assertEqual('line1\n│ line2\nline3\n', self.stdout.getvalue())

    def test_no_color(self):
        self.log.info("this should _not_ be blue\n")
        self.assertEqual("this should _not_ be blue\n", self.stdout.getvalue())
        self.assertNotIn("\x1B", self.stdout.getvalue())

    def test_newlines(self):
        self.log.info("one")
        self.log.info("two")
        self.log.info("three")
        self.log.info("four\n")
        self.assertEqual('onetwothreefour\n', self.stdout.getvalue())

    def test_flush(self):
        self.log.info("one")
        self.log.info("two")
        self.log.info("three")
        self.assertEqual('', self.stdout.getvalue())
        self.log._flush()
        self.assertEqual('onetwothree\n', self.stdout.getvalue())

    def test_indent_dedent(self):
        self.log.info("┌ one\n")
        self.log.indent(hancho.Utils.hex_to_ansi(0xFFFFFFFF))
        self.log.info("boop\n")
        self.log.dedent()
        self.log.info("└ two\n")
        self.log.info("soop\n")

        text = '┌ one\n│ boop\n└ two\nsoop\n'
        self.assertEqual(text, self.stdout.getvalue())

    def test_dumper(self):
        def dump(text, **kwargs):
            return hancho.Dumper.depointer(hancho.Dumper.dump(text, **kwargs))

        def check(value, expected, **kwargs):
            result = dump(value, **kwargs)
            print(result)
            expected = textwrap.dedent(expected).strip()
            self.assertEqual(expected, result)

        class Blarp: pass

        thing1 = {
            "a": 1,
            "b": [2, "two"],
            "c": (3, 3, 3),
            "d": object(),
            "e": "foobar",
            "f": Blarp(),
            "g": {"a" : "laksdjflaksdjfa;sldkjfas;ldkjfa;sdlkjf;alkdsjf;askldjf;alsdkjfa;skdjfa;sdlkfja;sdlkfja;sdklfja;sldkfja;sldkjfa"}
        }

        expected = """
        {
            a = 1,
            b = [2, 'two'],
            c = (3, 3, 3),
            d:object = <object object at 0x...>,
            e = 'foobar',
            f:Blarp = {},
            g = {
                a = 'laksdjflaksdjfa;sldkjfas;ldkjfa;sdlkjf;alkdsjf;askldjf;alsdkjfa;skdjfa;sdlkfja;sdlkfja;sdklfja;sldkfja;sldkjfa'
            }
        }
        """
        check(thing1, expected, print_id = False)

        #expected = """
        #{
        #    a = 1,
        #    b = [2, 'two'],
        #    c = (3, 3, 3),
        #    d:object@0x... = <object object at 0x...>,
        #    e = 'foobar',
        #    f:Blarp@0x... = {}
        #}
        #"""
        #check(thing1, expected, print_id = True)
        #check(contextvars.Context(), ":Context@0x... = {}")
        #check(print, ":builtin_function_or_method@0x... = <built-in function print>")


