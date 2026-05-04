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
        Templates split into literal (L) and expression (E) chunks.
        >>> Expander.split(r"a {b} c")
        [L'a ', E'b', L' c']
        >>> Expander.split(r"a \{b\} c")
        [L'a \\{b\\} c']
        >>> Expander.split(r"{a}{b}{c}")
        [E'a', E'b', E'c']
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
    tests.addTests(doctest.DocTestSuite(
        optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE,
    ))
    return tests
