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
    for t in doctests:
        t.shortDescription = lambda: None # type: ignore
    tests.addTests(doctests)
    return tests

M = Expander.Macro
L = Expander.Literal

####################################################################################################

class TestSplitTemplate(unittest.TestCase):

    def setUp(self):
        hancho.init(quiet = True)
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    def doctest_basic(self):
        r"""
        #Templates split into literal (L) and macro (M) chunks.
        >>> Expander.split(r"a {b} c")
        [L'a ', M'{b}', L' c']
        >>> Expander.split(r"a \{b\} c")
        [L'a \\{b\\} c']
        >>> Expander.split(r"{a}{b}{c}")
        [M'{a}', M'{b}', M'{c}']
        """

    def doctest_splitter(self):
        r"""
        # The splitter should tag each chunk of text as a literal or a macro
        >>> Expander.split("foo")
        [L'foo']
        >>> Expander.split("{bar}")
        [M'{bar}']
        >>> Expander.split("foo {bar}")
        [L'foo ', M'{bar}']
        >>> Expander.split("{bar} baz")
        [M'{bar}', L' baz']
        >>> Expander.split("foo {bar} baz")
        [L'foo ', M'{bar}', L' baz']
        >>> Expander.split("foo {bar} baz {flp} zrk")
        [L'foo ', M'{bar}', L' baz ', M'{flp}', L' zrk']
        """

    def doctest_mismatched_braces(self):
        r"""
        # Mismatched braces shouldn't break anything
        >>> Expander.split("{foo")
        [L'{foo']
        >>> Expander.split("foo}")
        [L'foo}']
        >>> Expander.split("{foo}}")
        [M'{foo}', L'}']
        >>> Expander.split("{{foo}")
        [L'{', M'{foo}']
        >>> Expander.split("{foo}}{")
        [M'{foo}', L'}{']
        >>> Expander.split("}{{foo}")
        [L'}{', M'{foo}']
        """

    def doctest_split_innermost(self):
        """
        # We should be extracting the innermost macros
        >>> Expander.split("{{foo}}")
        [L'{', M'{foo}', L'}']
        """

    # Temporarily disabled until we figure out what we want
#    def doctest_dont_split_inside_string(self):
#        r"""
#        # ...unless the innermost macro is inside a string
#        >>> Expander.split('{foo + "{bar}"}')
#        [M'foo + "{bar}"']
#        >>> Expander.split("{foo + '{bar}'}")
#        [E"foo + '{bar}'"]
#        """
#
#    def doctest_macros_inside_string(self):
#        r"""
#        # Macros inside a string should _not_ be split
#        >>> Expander.split("foo '{bar}' baz")
#        [L"foo '{bar}' baz"]
#        >>> Expander.split('foo "{bar}" baz')
#        [L'foo "{bar}" baz']
#        """


    def test_hash_matches_str(self):
        self.assertEqual(hash(L('a')), hash('a'))
        self.assertEqual(hash(M('{a}')), hash('{a}'))

    def test_basic(self):
        # Sanity check - Single braces should produce a block
        self.assertEqual(Expander.split("a {b} c"), [L('a '), M('{b}'), L(' c')])

        # Degenerate cases should produce single blocks
        self.assertEqual(Expander.split(""),  []   )
        self.assertEqual(Expander.split("{"), ['{'])
        self.assertEqual(Expander.split("}"), ['}'])
        self.assertEqual(Expander.split("a"), ['a'])

        # Multiple single-braced blocks should not produce empty text between them if they touch
        self.assertEqual(
            Expander.split("{a}{b}{c}"),
            [M('{a}'), M('{b}'), M('{c}')]
        )

        # But if there's whitespace between them, it should be preserved
        self.assertEqual(
            Expander.split(" {a} {b} {c} "),
            [' ', M('{a}'), ' ', M('{b}'), ' ', M('{c}'), ' ']
        )

        # Whitespace inside a block should not split the block
        self.assertEqual(
            Expander.split("{ a }{ b }{ c }"),
            [M('{ a }'), M('{ b }'), M('{ c }')]
        )

        # Unmatched braces
        self.assertEqual(Expander.split("{"),   [L('{')])
        self.assertEqual(Expander.split("}"),   [L('}')])

        self.assertEqual(Expander.split("{}"),  [M('{}')])
        self.assertEqual(Expander.split("}{"),  [L('}{')])
        self.assertEqual(Expander.split("{a"),  [L('{a')])
        self.assertEqual(Expander.split("a}"),  [L('a}')])

        self.assertEqual(Expander.split("a{b"), [L('a{b')])
        self.assertEqual(Expander.split("a}b"), [L('a}b')])
        self.assertEqual(Expander.split("}}{"), [L('}}{')])
        self.assertEqual(Expander.split("}{{"), [L('}{{')])
        self.assertEqual(Expander.split("{{}"), [L('{'), M('{}')])
        self.assertEqual(Expander.split("{}}"), [M('{}'),  L('}')])

        # Nesting
        self.assertEqual(Expander.split("a{{b}}c"),      [L('a{'), M('{b}'), L('}c')])
        self.assertEqual(Expander.split("{a{b}c}"),      [L('{a'), M('{b}'), L('c}')])
        self.assertEqual(Expander.split("x{a{b}{c}d}y"), [L('x{a'), M('{b}'), M('{c}'), L('d}y')])
        self.assertEqual(Expander.split("{{{{a}}}}"),    [L('{{{'), M('{a}'), L('}}}')])

        # Adjacent blocks with different brace counts
        self.assertEqual(Expander.split("{a}{{b}}{c}"),   [M('{a}'), L('{'), M('{b}'), L('}'), M('{c}')])
        self.assertEqual(Expander.split("{{a}}{b}{{c}}"), [L('{'), M('{a}'), L('}'), M('{b}'), L('{'), M('{c}'), L('}')])
        self.assertEqual(Expander.split("{{a}}"),         [L('{'), M('{a}'), L('}')])
        self.assertEqual(Expander.split("{{a}{b}}"),      [L('{'), M('{a}'), M('{b}'), L('}')])
        self.assertEqual(Expander.split("{{{a}}}"),       [L('{{'), M('{a}'), L('}}')])

        # Escaped braces should be ignored.
        self.assertEqual(Expander.split(r"a\{b\}c"), [L(r'a\{b\}c')])
        self.assertEqual(Expander.split(r"a{\}}b"),  [L('a'), M(r'{\}}'), L('b')])
        self.assertEqual(Expander.split(r"a{\{}b"),  [L('a'), M(r'{\{}'), L('b')])

        self.assertEqual(Expander.split("\\"),     [L('\\')])
        self.assertEqual(Expander.split(r"{\n}"),  [M(r'{\n}')])
        self.assertEqual(Expander.split(r"a\{b}"), [L(r'a\{b}')])
        self.assertEqual(Expander.split(r"a{b\}"), [L(r'a{b\}')])

        # Escaped backslashes should _not_ cause a following brace to be ignored.
        self.assertEqual(Expander.split(r"a\\{b}"), [L('a\\\\'), M('{b}')])
        self.assertEqual(Expander.split(r"a{b\\}"), [L('a'), M(r'{b\\}')])

        self.assertEqual(Expander.split(r"a \{a\} a"),     [L(r"a \{a\} a")])
        self.assertEqual(Expander.split(r"a \\{a\\} a"),   [L(r"a \\"), M(r"{a\\}"), L(r" a")])
        self.assertEqual(Expander.split(r"a \\\{a\\\} a"), [L(r"a \\\{a\\\} a")])

####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
