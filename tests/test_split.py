#!/usr/bin/env python3
import doctest
import os
import sys
import unittest

import hancho
from hancho import Expander

####################################################################################################


def setUpModule():
    os.chdir(os.path.dirname(__file__))


def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    tests.addTests(doctests)
    return tests


####################################################################################################


class TestSplitTemplate(unittest.TestCase):
    def setUp(self):
        hancho.init(verbosity = "QUIET")
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    def doctest_basic(self):
        r"""
        #Templates split into literal (L) and macro (M) chunks.
        >>> Expander.split(r"a \{b\} c")
        ['a \\{b\\} c']
        """

    def doctest_splitter(self):
        r"""
        # The splitter should tag each chunk of text as a literal or a macro
        >>> Expander.split("foo")
        ['foo']
        >>> Expander.split("{bar}")
        ['{bar}']
        >>> Expander.split("foo {bar}")
        ['foo ', '{bar}']
        >>> Expander.split("{bar} baz")
        ['{bar}', ' baz']
        >>> Expander.split("foo {bar} baz")
        ['foo ', '{bar}', ' baz']
        >>> Expander.split("foo {bar} baz {flp} zrk")
        ['foo ', '{bar}', ' baz ', '{flp}', ' zrk']
        """

    def doctest_mismatched_braces(self):
        r"""
        # Mismatched braces shouldn't break anything
        >>> Expander.split("{foo")
        ['{foo']
        >>> Expander.split("foo}")
        ['foo}']
        >>> Expander.split("{foo}}")
        ['{foo}', '}']
        >>> Expander.split("{{foo}")
        ['{', '{foo}']
        >>> Expander.split("{foo}}{")
        ['{foo}', '}{']
        >>> Expander.split("}{{foo}")
        ['}{', '{foo}']
        """

    def doctest_split_innermost(self):
        """
        # We should be extracting the innermost macros
        >>> Expander.split("{{foo}}")
        ['{', '{foo}', '}']
        """

    # Temporarily disabled until we figure out what we want
    # def doctest_dont_split_inside_string(self):
    #     r"""
    #     # ...unless the innermost macro is inside a string
    #     >>> Expander.split('{foo + "{bar}"}')
    #     ['foo + "{bar}"']
    #     >>> Expander.split("{foo + '{bar}'}")
    #     [E"foo + '{bar}'"]
    #     """

    # def doctest_macros_inside_string(self):
    #     r"""
    #     # Macros inside a string should _not_ be split
    #     >>> Expander.split("foo '{bar}' baz")
    #     [L"foo '{bar}' baz"]
    #     >>> Expander.split('foo "{bar}" baz')
    #     ['foo "{bar}" baz']
    #     """

    def test_hash_matches_str(self):
        self.assertEqual(hash("a"), hash("a"))
        self.assertEqual(hash("{a}"), hash("{a}"))

    def test_basic(self):
        # Sanity check - Single braces should produce a block
        self.assertEqual(Expander.split("a {b} c"), ["a ", "{b}", " c"])

        # Degenerate cases should produce single blocks
        self.assertEqual(Expander.split(""), [])
        self.assertEqual(Expander.split("{"), ["{"])
        self.assertEqual(Expander.split("}"), ["}"])
        self.assertEqual(Expander.split("a"), ["a"])

        # Multiple single-braced blocks should not produce empty text between them if they touch
        self.assertEqual(Expander.split("{a}{b}{c}"), ["{a}", "{b}", "{c}"])

        # But if there's whitespace between them, it should be preserved
        self.assertEqual(
            Expander.split(" {a} {b} {c} "), [" ", "{a}", " ", "{b}", " ", "{c}", " "]
        )

        # Whitespace inside a block should not split the block
        self.assertEqual(Expander.split("{ a }{ b }{ c }"), ["{ a }", "{ b }", "{ c }"])

        # Unmatched braces
        self.assertEqual(Expander.split("{"), ["{"])
        self.assertEqual(Expander.split("}"), ["}"])

        self.assertEqual(Expander.split("{}"), ["{}"])
        self.assertEqual(Expander.split("}{"), ["}{"])
        self.assertEqual(Expander.split("{a"), ["{a"])
        self.assertEqual(Expander.split("a}"), ["a}"])

        self.assertEqual(Expander.split("a{b"), ["a{b"])
        self.assertEqual(Expander.split("a}b"), ["a}b"])
        self.assertEqual(Expander.split("}}{"), ["}}{"])
        self.assertEqual(Expander.split("}{{"), ["}{{"])
        self.assertEqual(Expander.split("{{}"), ["{", "{}"])
        self.assertEqual(Expander.split("{}}"), ["{}", "}"])

        # Nesting
        self.assertEqual(Expander.split("a{{b}}c"), ["a{", "{b}", "}c"])
        self.assertEqual(Expander.split("{a{b}c}"), ["{a", "{b}", "c}"])
        self.assertEqual(Expander.split("x{a{b}{c}d}y"), ["x{a", "{b}", "{c}", "d}y"])
        self.assertEqual(Expander.split("{{{{a}}}}"), ["{{{", "{a}", "}}}"])

        # Adjacent blocks with different brace counts
        self.assertEqual(
            Expander.split("{a}{{b}}{c}"), ["{a}", "{", "{b}", "}", "{c}"]
        )
        self.assertEqual(
            Expander.split("{{a}}{b}{{c}}"),
            ["{", "{a}", "}", "{b}", "{", "{c}", "}"],
        )
        self.assertEqual(Expander.split("{{a}}"), ["{", "{a}", "}"])
        self.assertEqual(Expander.split("{{a}{b}}"), ["{", "{a}", "{b}", "}"])
        self.assertEqual(Expander.split("{{{a}}}"), ["{{", "{a}", "}}"])

        # Escaped braces should be ignored.
        self.assertEqual(Expander.split(r"a\{b\}c"), [r"a\{b\}c"])
        self.assertEqual(Expander.split(r"a{\}}b"), ["a", r"{\}}", "b"])
        self.assertEqual(Expander.split(r"a{\{}b"), ["a", r"{\{}", "b"])

        self.assertEqual(Expander.split("\\"), ["\\"])
        self.assertEqual(Expander.split(r"{\n}"), [r"{\n}"])
        self.assertEqual(Expander.split(r"a\{b}"), [r"a\{b}"])
        self.assertEqual(Expander.split(r"a{b\}"), [r"a{b\}"])

        # Escaped backslashes should _not_ cause a following brace to be ignored.
        self.assertEqual(Expander.split(r"a\\{b}"), ["a\\\\", "{b}"])
        self.assertEqual(Expander.split(r"a{b\\}"), ["a", r"{b\\}"])

        self.assertEqual(Expander.split(r"a \{a\} a"), [r"a \{a\} a"])
        self.assertEqual(Expander.split(r"a \\{a\\} a"), [r"a \\", r"{a\\}", r" a"])
        self.assertEqual(Expander.split(r"a \\\{a\\\} a"), [r"a \\\{a\\\} a"])


####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
