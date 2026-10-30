import io
import os
import subprocess
import sys
import textwrap
import unittest

import hancho

# FIXME we need some tests that run hancho as if it were launched from the command line

# --------------------------------------------------------------------------------------------------

class TestApp(unittest.TestCase):
    def setUp(self, *args):
        os.chdir(os.path.dirname(__file__))
        self.maxDiff = 99999

        options =[
            # We use 'info' here because we're capturing the log to a StringIO
            "--log_level=debug",
            "--log_wrap=False",
            "--log_color=False",
            "--log_timestamp=False",
            "--log_width=9999",
        ]

        self.stdout = io.StringIO()
        self.script = hancho.create_test_script(
            name   = "TestApp",
            file   = __file__,
            root   = os.getcwd(),
            stdout = self.stdout,
            extra_flags = [*options, *args]
        )
        self.log = hancho.cg.log
        self.clear_stdout()

    def clear_stdout(self):
        self.stdout.truncate(0)
        self.stdout.seek(0)

    def tearDown(self):
        pass

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

    def test_verbosities(self):
        self.setUp("--log_level=trace")
        self.assertEqual("trace", self.log.log_level)
        self.assertEqual(self.log.TRACE, self.log._level_int)

        self.setUp("--log_level=debug")
        self.assertEqual("debug", self.log.log_level)
        self.assertEqual(self.log.DEBUG, self.log._level_int)

        self.setUp("--log_level=info")
        self.assertEqual("info", self.log.log_level)
        self.assertEqual(self.log.INFO, self.log._level_int)

        self.setUp("--log_level=warning")
        self.assertEqual("warning", self.log.log_level)
        self.assertEqual(self.log.WARNING, self.log._level_int)

        self.setUp("--log_level=error")
        self.assertEqual("error", self.log.log_level)
        self.assertEqual(self.log.ERROR, self.log._level_int)

        self.setUp("--log_level=critical")
        self.assertEqual("critical", self.log.log_level)
        self.assertEqual(self.log.CRITICAL, self.log._level_int)

        self.setUp("--log_level=quiet")
        self.assertEqual("quiet", self.log.log_level)
        self.assertEqual(self.log.QUIET, self.log._level_int)

        # FIXME We need to test this some other way, it seems to f' up the debugger
        #cmd = [sys.executable, "../hancho.py", "--log_level=boo"]
        #result = subprocess.run(cmd, capture_output=True, text=True)
        #self.assertIn("invalid choice: 'boo'", result.stderr)

    def test_indentation(self):
        self.log.info("line1\n")
        self.log.indent(hancho.Utils.hex_to_ansi(0xFFFFFFFF))
        self.log.info("line2\n")
        self.log.dedent()
        self.log.info("line3\n")
        self.assertEqual(
            repr("line1\n│line2\nline3\n"),
            repr(self.stdout.getvalue())
        )

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

        self.assertEqual(
            repr('┌ one\n│boop\n└ two\nsoop\n'),
            repr(self.stdout.getvalue())
        )

    def test_dumper(self):
        def dump(text, **kwargs):
            return hancho.Dumper.depointer(hancho.Dumper.dump(text, **kwargs))

        def check(value, expected, **kwargs):
            result = dump(value, **kwargs)
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
        :dict = {
            a = 1,
            b:list = [2, 'two'],
            c = (3, 3, 3),
            d:object = <object object at 0x...>,
            e = 'foobar',
            f:Blarp = {},
            g:dict = {
                a = 'laksdjflaksdjfa;sldkjfas;ldkjfa;sdlkjf;alkdsjf;askldjf;alsdkjfa;skdjfa;sdlkfja;sdlkfja;sdklfja;sldkfja;sldkjfa'
            }
        }
        """
        check(thing1, expected, print_id = False)

# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=999)
