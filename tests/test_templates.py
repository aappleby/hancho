#!/usr/bin/python3
"""Test cases for Hancho's text templating system"""

import os
from reprlib import recursive_repr
import sys
import unittest
import doctest

#sys.path.append("..")
(this_dir, this_file) = os.path.split(os.path.abspath(__file__))
hancho_dir = os.path.normpath(f"{this_dir}/..")
sys.path.append(hancho_dir)
import hancho


from hancho import Dict, Expander

####################################################################################################

class TestTemplates(unittest.TestCase):
    def setUp(self):
        hancho.init(
            this_dir  = this_dir,
            this_file = this_file,
            debug     = False,
            verbose   = False,
            quiet     = True,
        )

        sys.stdout.flush()

    def doctest_basic_eval(self):
        r"""
        # Basic evaluation should work
        >>> d = Dict(a = 1, b = 2)
        >>> d.eval("{a}")
        1
        >>> d.eval("{b}")
        2
        >>> d.eval("{a}{b}{a}{b}")
        1212
        """

    def doctest_basic_expand(self):
        r"""
        # Expanding basic templates should work
        >>> d = Dict(a = 1, b = 2, c = 3)
        >>> d.expand("{a}{b}{c}")
        '123'
        """

    def doctest_nested_braces(self):
        r"""
        # Multiply-nested braces should work
        >>> d = Dict(a = "b", b = 1)
        >>> d.eval("{{a}}")
        1
        >>> d.eval("{{{{{{a}}}}}}")
        1
        """

    def doctest_nested_templates(self):
        r"""
        # We should be able to expand nested templates
        >>> d = Dict(a = Dict(b = "{c}"), c = 10)
        >>> d.eval("a.b")
        '{c}'
        >>> d.eval("{a.b}")
        10
        >>> d.expand("a.b")
        'a.b'
        >>> d.expand("{a.b}")
        '10'
        """

    def doctest_extra_braces(self):
        r"""
        # Additional {} around an expression essentially adds extra evals
        >>> d = Dict(a = "1 + 1", b = "{a}")
        >>> d.expand("{a}")
        '1 + 1'
        >>> d.expand("{{a}}")
        '2'
        """

    def doctest_read_nested_c_first(self):
        r"""
        # Reading a field from a nested Dict should read the _innermost_ 'c', as it is expanded in the
        # nested context.
        >>> d = Dict(a = Dict(b = "{c}", c = 10), c = 20)
        >>> d.expand("{a.b}")
        '10'
        >>> d.eval("{a.b}")
        10
        """

    def doctest_expand_before_eval(self):
        r"""
        # Wrapping a field in {} makes us expand it before eval
        >>> d = Dict(a = 1, b = 2, c = 3, name_a = 'a', name_b = 'b', name_c = 'c')
        >>> d.eval("name_a + name_b + name_c")
        'abc'
        >>> d.eval("{name_a} + {name_b} + {name_c}")
        6
        """

    def doctest_assemble_pieces(self):
        r"""
        # Expanding a template can assemble a new template from pieces, which then also gets expanded
        >>> d = Dict(part_a = '{f', part_b = 'o', part_c = 'o}', foo = 10)
        >>> d.eval("{part_a}{part_b}{part_c}")
        10

        # And that can go multiple levels deep
        >>> d = Dict(part_a1 = '{f', part_b1 = 'o', part_c1 = 'o}',
        ...   foo = "{part_a2}{part_b2}{part_c2}",
        ...   part_a2 = '{b', part_b2 = 'a', part_c2 = 'r}',
        ...   bar = 12)
        >>> d.eval("{part_a1}{part_b1}{part_c1}")
        12
        """

    def doctest_template_lambdas(self):
        r"""
        # Templates can call lambdas
        >>> d = Dict(a = 1, b = lambda x : x + 1)
        >>> d
        Dict @ 0x... { a = 1, b = <function <lambda> at 0x...> }
        >>> d.expand("foo {b(a)} bar")
        'foo 2 bar'
        """

    def doctest_TEFINAE(self):
        r"""
        # TEFINAE - Text Expansion Failure Is Not An Error
        >>> d = Dict(a = 1)
        >>> d.expand("{missing}")
        '{missing}'
        >>> d.expand("{a} {missing}")
        '1 {missing}'
        >>> d.expand("{a + missing}")
        '{a + missing}'
        """

    def doctest_template_nones(self):
        r"""
        # Nones should turn into empty strings
        >>> d = Dict(a = None, b = "x{a}y")
        >>> d.eval("a") is None
        True
        >>> d.eval("{a}")
        Traceback (most recent call last):
        ...
        SyntaxError: invalid syntax
        >>> d.expand("{a}")
        ''
        >>> d.expand("{b}")
        'xy'
        >>> d.expand("foo {b} bar")
        'foo xy bar'
        """

    def doctest_order_of_expansion(self):
        r"""
        # Expand produces strings, but the below does _not_ try to add (string) "10" and (int) 0
        # because expanding {a} -> {b} -> "10" then joins the "10" with " + 0" to produce "10 + 0"
        # before the final eval.

        ┏ expand '{a} + 0'
        ┃ ┏ eval 'a'
        ┃ ┃ ┏ get 'a'
        ┃ ┃ ┗ '{b}'
        ┃ ┃ ┏ expand '{b}'
        ┃ ┃ ┃ ┏ eval 'b'
        ┃ ┃ ┃ ┃ ┏ get 'b'
        ┃ ┃ ┃ ┃ ┗ 10
        ┃ ┃ ┃ ┗ 10
        ┃ ┃ ┗ '10'
        ┃ ┗ '10'
        ┗ '10 + 0'
        ┏ eval '10 + 0'
        ┗ 10

        >>> d = Dict(a = "{b}", b = 10)
        >>> d.eval("{a} + 0")
        10
        """

    def doctest_join_lists(self):
        r"""
        # Lists should be joined with spaces
        >>> d = Dict(flags = ["-O2", "-Wall"])
        >>> d.expand("cc {flags} main.c")
        'cc -O2 -Wall main.c'
        >>> d.eval("flags")
        ['-O2', '-Wall']

#        >>> d.eval("{flags}")
#        Traceback (most recent call last):
#        ...
#        AttributeError: 'Dict' object has no attribute 'O2'
        """

    def doctest_flatten_lists(self):
        r"""
        # Lists should be flattened before joining with spaces
        >>> d = Dict(flags = [[['a'], 'b'], 'c', 'd', ['e', 'f']])
        >>> d.expand("{flags}")
        'a b c d e f'
        >>> d.eval("flags")
        [[['a'], 'b'], 'c', 'd', ['e', 'f']]
        >>> d.eval("{flags}")
        Traceback (most recent call last):
        ...
            a b c d e f
              ^
        SyntaxError: invalid syntax
        """

    def test_templates_with_escaped_char_proxies(self):
        # Testing escape sequences in templates is annoying. Double-check that we can use proxies
        # to build strings with escape sequences.

        d = Dict(a = 1, bs = '\\', lb = '{', rb = '}')
        self.assertEqual(d.expand(r"{lb}a{rb}"), r"1")
        self.assertEqual(d.expand(r"{bs}{lb}a{bs}{rb}"), r"\{a\}")

    def test_expand_failed_to_terminate1(self):
        # Single recursion
        with self.assertRaises(RecursionError):
            bad_dict = Dict(flarp = "asdf {flarp}")
            bad_dict.expand("{flarp}")

        # Double recursion
        with self.assertRaises(RecursionError):
            bad_dict = Dict(foo = "asdf {bar}", bar = "qwer {foo}")
            bad_dict.eval("foo")

        # Recursion through 'subthing.foo', which can't be evaluated in 'subthing' and gets re-evaluated
        # in 'bad_dict'
        with self.assertRaises(RecursionError):
            subthing = Dict(foo = "{subthing.foo} x")
            bad_dict = Dict(command = "{subthing.foo}", subthing = subthing)
            bad_dict.eval("command")


####################################################################################################

def load_tests(loader, tests, ignore):
    tests.addTests(doctest.DocTestSuite(
        optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE
    ))
    return tests

if __name__ == "__main__":
    unittest.main(verbosity=1)
