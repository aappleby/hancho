#!/usr/bin/env python3
import sys
import unittest
import doctest

sys.path.append("..")
from hancho import Dict, Expander

E = Expander.Expr
L = Expander.Lit

class TestSplitTemplate(unittest.TestCase):

    def doctest_basic(self):
        r"""
        #Templates split into literal (L) and expression (E) chunks.
        >>> Expander.split(r"a {b} c")
        [L'a ', E'b', L' c']
        >>> Expander.split(r"a \{b\} c")
        [L'a \\{b\\} c']
        >>> Expander.split(r"{a}{b}{c}")
        [E'a', E'b', E'c']
        """

    def doctest_splitter(self):
        r"""
        # The splitter should tag each chunk of text as a literal or a macro
        >>> Expander.split("foo")
        [L'foo']
        >>> Expander.split("{bar}")
        [E'bar']
        >>> Expander.split("foo {bar}")
        [L'foo ', E'bar']
        >>> Expander.split("{bar} baz")
        [E'bar', L' baz']
        >>> Expander.split("foo {bar} baz")
        [L'foo ', E'bar', L' baz']
        >>> Expander.split("foo {bar} baz {flp} zrk")
        [L'foo ', E'bar', L' baz ', E'flp', L' zrk']
        """

    def doctest_mismatched_braces(self):
        r"""
        # Mismatched braces shouldn't break anything
        >>> Expander.split("{foo")
        [L'{foo']
        >>> Expander.split("foo}")
        [L'foo}']
        >>> Expander.split("{foo}}")
        [E'foo', L'}']
        >>> Expander.split("{{foo}")
        [L'{', E'foo']
        >>> Expander.split("{foo}}{")
        [E'foo', L'}{']
        >>> Expander.split("}{{foo}")
        [L'}{', E'foo']
        """

    def doctest_macros_inside_string(self):
        r"""
        # Macros inside a string should _not_ be split
        >>> Expander.split("foo '{bar}' baz")
        [L"foo '{bar}' baz"]
        >>> Expander.split('foo "{bar}" baz')
        [L'foo "{bar}" baz']
        """

    def doctest_split_innermost(self):
        """
        # We should be extracting the innermost macros
        >>> Expander.split("{{foo}}")
        [L'{', E'foo', L'}']
        """

    def doctest_dont_split_inside_string(self):
        r"""
        # ...unless the innermost macro is inside a string
        >>> Expander.split('{foo + "{bar}"}')
        [E'foo + "{bar}"']
        >>> Expander.split("{foo + '{bar}'}")
        [E"foo + '{bar}'"]
        """

    def test_hash_matches_str(self):
        self.assertEqual(hash(L('a')), hash('a'))
        self.assertEqual(hash(E('a')), hash('a'))

    def test_basic(self):
        # Sanity check - Single braces should produce a block
        self.assertEqual(Expander.split("a {b} c"), [L('a '), E('b'), L(' c')])

        # Degenerate cases should produce single blocks
        self.assertEqual(Expander.split(""),  []   )
        self.assertEqual(Expander.split("{"), ['{'])
        self.assertEqual(Expander.split("}"), ['}'])
        self.assertEqual(Expander.split("a"), ['a'])

        # Multiple single-braced blocks should not produce empty text between them if they touch
        self.assertEqual(Expander.split("{a}{b}{c}"), ['a', 'b', 'c'])

        # But if there's whitespace between them, it should be preserved
        self.assertEqual(Expander.split(" {a} {b} {c} "), [' ', 'a', ' ', 'b', ' ', 'c', ' '])

        # Whitespace inside a block should not split the block
        self.assertEqual(Expander.split("{ a }{ b }{ c }"), [' a ', ' b ', ' c '])

        # Unmatched braces
        self.assertEqual(Expander.split("{"),   [L('{')])
        self.assertEqual(Expander.split("}"),   [L('}')])

        self.assertEqual(Expander.split("{}"),  [E('')])
        self.assertEqual(Expander.split("}{"),  [L('}{')])
        self.assertEqual(Expander.split("{a"),  [L('{a')])
        self.assertEqual(Expander.split("a}"),  [L('a}')])

        self.assertEqual(Expander.split("a{b"), [L('a{b')])
        self.assertEqual(Expander.split("a}b"), [L('a}b')])
        self.assertEqual(Expander.split("}}{"), [L('}}{')])
        self.assertEqual(Expander.split("}{{"), [L('}{{')])
        self.assertEqual(Expander.split("{{}"), [L('{'), E('')])
        self.assertEqual(Expander.split("{{}"), [L('{'), E('')])
        self.assertEqual(Expander.split("{}}"), [E(''),  L('}')])

        # Nesting
        self.assertEqual(Expander.split("a{{b}}c"),      ['a{', 'b', '}c'])
        self.assertEqual(Expander.split("{a{b}c}"),      ['{a', 'b', 'c}'])
        self.assertEqual(Expander.split("x{a{b}{c}d}y"), ['x{a', 'b', 'c', 'd}y'])
        self.assertEqual(Expander.split("{{{{a}}}}"),    ['{{{', 'a', '}}}'])

        # Adjacent blocks with different brace counts
        self.assertEqual(Expander.split("{a}{{b}}{c}"),   ['a', '{', 'b', '}', 'c'])
        self.assertEqual(Expander.split("{{a}}{b}{{c}}"), ['{', 'a', '}', 'b', '{', 'c', '}'])
        self.assertEqual(Expander.split("{{a}}"),         ['{', 'a', '}'])
        self.assertEqual(Expander.split("{{a}{b}}"),      ['{', 'a', 'b', '}'])
        self.assertEqual(Expander.split("{{{a}}}"),       ['{{', 'a', '}}'])

        # Escaped braces should be ignored.
        self.assertEqual(Expander.split(r"a\{b\}c"), [r'a\{b\}c'])
        self.assertEqual(Expander.split(r"a{\}}b"),  ['a', r'\}', 'b'])
        self.assertEqual(Expander.split(r"a{\{}b"),  ['a', r'\{', 'b'])

        self.assertEqual(Expander.split("\\"),     ['\\'])
        self.assertEqual(Expander.split(r"{\n}"),  [r'\n'])
        self.assertEqual(Expander.split(r"a\{b}"), [r'a\{b}'])
        self.assertEqual(Expander.split(r"a{b\}"), [r'a{b\}'])

        # Escaped backslashes should _not_ cause a following brace to be ignored.
        self.assertEqual(Expander.split(r"a\\{b}"), ['a\\\\', 'b'])
        self.assertEqual(Expander.split(r"a{b\\}"), ['a', 'b\\\\'])

        self.assertEqual(Expander.split(r"a \{a\} a"),     [L(r"a \{a\} a")])
        self.assertEqual(Expander.split(r"a \\{a\\} a"),   [L("a \\\\"), E("a\\\\"), L(" a")])
        self.assertEqual(Expander.split(r"a \\\{a\\\} a"), [L(r"a \\\{a\\\} a")])


####################################################################################################

def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    for t in doctests:
        t.shortDescription = lambda: None
    tests.addTests(doctests)
    return tests

if __name__ == "__main__":
    unittest.main(verbosity=1)
