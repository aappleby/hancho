import io
import os
import subprocess
import sys
import textwrap
import unittest
from typing import Any

import hancho

# FIXME we need some tests that run hancho as if it were launched from the command line

cvg: hancho.CVGlobals

# --------------------------------------------------------------------------------------------------

class TestApp(unittest.TestCase):
    def setUp(self, *args : Any) -> None:
        os.chdir(os.path.dirname(__file__))
        self.maxDiff = 99999

        options: list[str] =[
            # We use 'info' here because we're capturing the log to a StringIO
            "--log.level=debug",
            "--log.wrap=False",
            "--log.color=False",
            "--log.timestamp=False",
            "--log.width=9999",
        ]

        self.stdout = io.StringIO()
        self.script = hancho.create_test_script(
            in_file = __file__,
            stdout = self.stdout,
            extra_flags = [*options, *args]
        )
        self.clear_stdout()

        global cvg
        cvg = hancho.cvg

    def clear_stdout(self) -> None:
        self.stdout.truncate(0)
        self.stdout.seek(0)

    def tearDown(self) -> None:
        pass

    def test_log_redirection(self) -> None:
        # Our default verbosity is 'critical', so these should appear in the log.
        cvg.log.critical("This is a message\n")
        cvg.log.critical("This is another message\n")
        cvg.log._flush()
        self.assertEqual("This is a message\nThis is another message\n", self.stdout.getvalue())

        # If we clear the log and print the same thing at 'trace' level they should not appear.
        self.stdout.seek(0)
        self.stdout.truncate(0)

        cvg.log.trace("This is a message\n")
        cvg.log.trace("This is another message\n")
        cvg.log._flush()
        self.assertEqual("", self.stdout.getvalue())

    def test_we_can_show_help(self) -> None:
        cmd: list[str] = [sys.executable, "../hancho.py", "--help"]
        result: subprocess.CompletedProcess[str] = subprocess.run(cmd, capture_output = True, text=True)
        self.assertIn("Hancho is a simple, pleasant build system", result.stdout)

    def test_verbosities(self) -> None:
        self.setUp("--log.level=trace")
        self.assertEqual("trace", cvg.log.level)
        self.assertEqual(cvg.log.TRACE, cvg.log._level_int)

        self.setUp("--log.level=debug")
        self.assertEqual("debug", cvg.log.level)
        self.assertEqual(cvg.log.DEBUG, cvg.log._level_int)

        self.setUp("--log.level=info")
        self.assertEqual("info", cvg.log.level)
        self.assertEqual(cvg.log.INFO, cvg.log._level_int)

        self.setUp("--log.level=warning")
        self.assertEqual("warning", cvg.log.level)
        self.assertEqual(cvg.log.WARNING, cvg.log._level_int)

        self.setUp("--log.level=error")
        self.assertEqual("error", cvg.log.level)
        self.assertEqual(cvg.log.ERROR, cvg.log._level_int)

        self.setUp("--log.level=critical")
        self.assertEqual("critical", cvg.log.level)
        self.assertEqual(cvg.log.CRITICAL, cvg.log._level_int)

        self.setUp("--log.level=quiet")
        self.assertEqual("quiet", cvg.log.level)
        self.assertEqual(cvg.log.QUIET, cvg.log._level_int)

        # FIXME We need to test this some other way, it seems to f' up the debugger
        #cmd = [sys.executable, "../hancho.py", "--log.level=boo"]
        #result = subprocess.run(cmd, capture_output=True, text=True)
        #self.assertIn("invalid choice: 'boo'", result.stderr)

    def test_indentation(self) -> None:
        cvg.log.info("line1\n")
        cvg.log.indent(hancho.Utils.hex_to_ansi(0xFFFFFFFF))
        cvg.log.info("line2\n")
        cvg.log.dedent()
        cvg.log.info("line3\n")
        self.assertEqual(
            repr("line1\n│ line2\nline3\n"),
            repr(self.stdout.getvalue())
        )

    def test_no_color(self) -> None:
        cvg.log.info("this should _not_ be blue\n")
        self.assertEqual("this should _not_ be blue\n", self.stdout.getvalue())
        self.assertNotIn("\x1B", self.stdout.getvalue())

    def test_newlines(self) -> None:
        cvg.log.info("one")
        cvg.log.info("two")
        cvg.log.info("three")
        cvg.log.info("four\n")
        self.assertEqual('onetwothreefour\n', self.stdout.getvalue())

    def test_flush(self) -> None:
        cvg.log.info("one")
        cvg.log.info("two")
        cvg.log.info("three")
        self.assertEqual('', self.stdout.getvalue())
        cvg.log._flush()
        self.assertEqual('onetwothree\n', self.stdout.getvalue())

    def test_indent_dedent(self) -> None:
        cvg.log.info("┌ one\n")
        cvg.log.indent(hancho.Utils.hex_to_ansi(0xFFFFFFFF))
        cvg.log.info("boop\n")
        cvg.log.dedent()
        cvg.log.info("└ two\n")
        cvg.log.info("soop\n")

        self.assertEqual(
            repr('┌ one\n│ boop\n└ two\nsoop\n'),
            repr(self.stdout.getvalue())
        )

    def test_dumper(self) -> None:
        def dump(text : str, **kwargs : Any) -> str:
            return hancho.Dumper.depointer(hancho.Dumper.dump(text, **kwargs))

        def check(value : Any, expected : Any, **kwargs : Any) -> None:
            result: str = dump(value, **kwargs)
            expected = textwrap.dedent(expected).strip()
            self.assertEqual(expected, result)

        class Blarp: pass

        thing1 : dict[str, Any] = {
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
            f:Blarp = <tests.test_app.TestApp.test_dumper.<locals>.Blarp object at 0x...>,
            g:dict = {
                a = 'laksdjflaksdjfa;sldkjfas;ldkjfa;sdlkjf;alkdsjf;askldjf;alsdkjfa;skdjfa;sdlkfja;sdlkfja;sdklfja;sldkfja;sldkjfa'
            }
        }
        """
        check(thing1, expected, print_id = False)

# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=999)
