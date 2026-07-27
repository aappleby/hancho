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

def split(template):
    #delims = "{}"
    out = []
    Expander._split_text(template, out)
    return out

####################################################################################################

# FIXME test a nested dict using different delimiters than the parent dict

class TestSplitTemplate(unittest.TestCase):
    def setUp(self):
        hancho.init(verbosity = "quiet")
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    def doctest_basic(self):
        r"""
        # Escaped braces should _not_ split.
        >>> split(r"a \{b\} c")
        ['a \\{b\\} c']
        """

    def doctest_splitter(self):
        r"""
        # The splitter should tag each chunk of text as a literal or a macro
        >>> split("foo")
        ['foo']
        >>> split("{bar}")
        ['{bar}']
        >>> split("foo {bar}")
        ['foo ', '{bar}']
        >>> split("{bar} baz")
        ['{bar}', ' baz']
        >>> split("foo {bar} baz")
        ['foo ', '{bar}', ' baz']
        >>> split("foo {bar} baz {flp} zrk")
        ['foo ', '{bar}', ' baz ', '{flp}', ' zrk']
        """

    def doctest_mismatched_braces(self):
        r"""
        # Mismatched braces shouldn't break anything
        >>> split("{foo")
        ['{foo']
        >>> split("foo}")
        ['foo}']
        >>> split("{foo}}")
        ['{foo}', '}']
        >>> split("{{foo}")
        ['{', '{foo}']
        >>> split("{foo}}{")
        ['{foo}', '}{']
        >>> split("}{{foo}")
        ['}{', '{foo}']
        """

    def doctest_split_innermost(self):
        """
        # We should be extracting the innermost macros
        >>> split("{{foo}}")
        ['{', '{foo}', '}']
        """

    # Temporarily disabled until we figure out what we want
    # def doctest_dont_split_inside_string(self):
    #     r"""
    #     # ...unless the innermost macro is inside a string
    #     >>> split('{foo + "{bar}"}')
    #     ['foo + "{bar}"']
    #     >>> split("{foo + '{bar}'}")
    #     [E"foo + '{bar}'"]
    #     """

    # def doctest_macros_inside_string(self):
    #     r"""
    #     # Macros inside a string should _not_ be split
    #     >>> split("foo '{bar}' baz")
    #     [L"foo '{bar}' baz"]
    #     >>> split('foo "{bar}" baz')
    #     ['foo "{bar}" baz']
    #     """

    def test_hash_matches_str(self):
        self.assertEqual(hash("a"), hash("a"))
        self.assertEqual(hash("{a}"), hash("{a}"))

    def test_basic(self):
        # Sanity check - Single braces should produce a block
        self.assertEqual(split("a {b} c"), ["a ", "{b}", " c"])

        # Degenerate cases should produce single blocks
        self.assertEqual(split(""), [])
        self.assertEqual(split("{"), ["{"])
        self.assertEqual(split("}"), ["}"])
        self.assertEqual(split("a"), ["a"])

        # Multiple single-braced blocks should not produce empty text between them if they touch
        self.assertEqual(split("{a}{b}{c}"), ["{a}", "{b}", "{c}"])

        # But if there's whitespace between them, it should be preserved
        self.assertEqual(
            split(" {a} {b} {c} "), [" ", "{a}", " ", "{b}", " ", "{c}", " "]
        )

        # Whitespace inside a block should not split the block
        self.assertEqual(split("{ a }{ b }{ c }"), ["{ a }", "{ b }", "{ c }"])

        # Unmatched braces
        self.assertEqual(split("{"), ["{"])
        self.assertEqual(split("}"), ["}"])

        self.assertEqual(split("{}"), ["{}"])
        self.assertEqual(split("}{"), ["}{"])
        self.assertEqual(split("{a"), ["{a"])
        self.assertEqual(split("a}"), ["a}"])

        self.assertEqual(split("a{b"), ["a{b"])
        self.assertEqual(split("a}b"), ["a}b"])
        self.assertEqual(split("}}{"), ["}}{"])
        self.assertEqual(split("}{{"), ["}{{"])
        self.assertEqual(split("{{}"), ["{", "{}"])
        self.assertEqual(split("{}}"), ["{}", "}"])

        # Nesting
        self.assertEqual(split("a{{b}}c"), ["a{", "{b}", "}c"])
        self.assertEqual(split("{a{b}c}"), ["{a", "{b}", "c}"])
        self.assertEqual(split("x{a{b}{c}d}y"), ["x{a", "{b}", "{c}", "d}y"])
        self.assertEqual(split("{{{{a}}}}"), ["{{{", "{a}", "}}}"])

        # Adjacent blocks with different brace counts
        self.assertEqual(
            split("{a}{{b}}{c}"), ["{a}", "{", "{b}", "}", "{c}"]
        )
        self.assertEqual(
            split("{{a}}{b}{{c}}"),
            ["{", "{a}", "}", "{b}", "{", "{c}", "}"],
        )
        self.assertEqual(split("{{a}}"), ["{", "{a}", "}"])
        self.assertEqual(split("{{a}{b}}"), ["{", "{a}", "{b}", "}"])
        self.assertEqual(split("{{{a}}}"), ["{{", "{a}", "}}"])

        # Escaped braces should be ignored.
        self.assertEqual(split(r"a\{b\}c"), [r"a\{b\}c"])
        self.assertEqual(split(r"a{\}}b"), ["a", r"{\}}", "b"])
        self.assertEqual(split(r"a{\{}b"), ["a", r"{\{}", "b"])

        self.assertEqual(split("\\"), ["\\"])
        self.assertEqual(split(r"{\n}"), [r"{\n}"])
        self.assertEqual(split(r"a\{b}"), [r"a\{b}"])
        self.assertEqual(split(r"a{b\}"), [r"a{b\}"])

        # Escaped backslashes should _not_ cause a following brace to be ignored.
        self.assertEqual(split(r"a\\{b}"), [r"a\\", r"{b}"])
        self.assertEqual(split(r"a{b\\}"), [r"a", r"{b\\}"])

        self.assertEqual(split(r"a \{a\} a"), [r"a \{a\} a"])
        self.assertEqual(split(r"a \\{a\\} a"), [r"a \\", r"{a\\}", r" a"])
        self.assertEqual(split(r"a \\\{a\\\} a"), [r"a \\\{a\\\} a"])


####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
