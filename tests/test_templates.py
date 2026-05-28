#!/usr/bin/python3
"""Test cases for Hancho's text templating system"""

import os
from reprlib import recursive_repr
import sys
import unittest
import doctest

sys.path.append("..")
import hancho
from hancho import Dict, Expander

####################################################################################################

class TestTemplates(unittest.TestCase):
    def setUp(self):
        #print(f"Running {self.__class__.__name__}::{self._testMethodName}")
        #hancho.init(quiet   = True)
        hancho.init(quiet = False, trace = True)

        sys.stdout.flush()

    def doctest_basic_eval(self):
        # Basic evaluation should work
        """
        >>> d = Dict(a = 1, b = 2)
        >>> d.eval("a")
        1
        >>> d.eval("b")
        2
        >>> d.eval("{a}{b}{a}{b}")
        '1212'
        >>> d.eval("{a}")
        asdf
        >>> d.eval("{b}")
        Traceback (most recent call last):
        ...
        AssertionError
        """

    def doctest_basic_expand(self):
        r"""
        # Expanding basic templates should work
        >>> d = Dict(a = 1, b = 2, c = 3)
        >>> d.expand_once("a")
        a
        >>> d.expand_once("{a}")
        '1'
        >>> d.expand_once("{a}{b}{c}")
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
        >>> d.expand_once("a.b")
        'a.b'
        >>> d.expand_once("{a.b}")
        '10'
        """

    def doctest_extra_braces(self):
        r"""
        # Additional {} around an expression essentially adds extra evals
        >>> d = Dict(a = "1 + 1", b = "{a}")
        >>> d.expand_once("{a}")
        '1 + 1'
        >>> d.expand_once("{{a}}")
        '2'
        """

    def doctest_read_nested_c_first(self):
        r"""
        # Reading a field from a nested Dict should read the _innermost_ 'c', as it is expanded in the
        # nested context.
        >>> d = Dict(a = Dict(b = "{c}", c = 10), c = 20, trace = True)
        >>> d.expand_all("{a.b}")
        '10'
        """
        #>>> d.expand_once("{a.b}")
        #'{c}'
        #>>> d.expand_all("{a.b}")
        #'20'

    def doctest_expand_before_eval(self):
        r"""
        # Wrapping a field in {} makes us expand it before eval
        >>> d = Dict(a = 1, b = 2, c = 3, name_a = 'a', name_b = 'b', name_c = 'c')
        >>> d.expand_once("name_a + name_b + name_c")
        'abc'
        >>> d.expand_once("{name_a} + {name_b} + {name_c}")
        6
        """

    def doctest_assemble_pieces(self):
        r"""
        # Expanding a template can assemble a new template from pieces, which then also gets expanded
        >>> d = Dict(part_a = '{f', part_b = 'o', part_c = 'o}', foo = 10)
        >>> d.expand_once("{part_a}{part_b}{part_c}")
        10

        # And that can go multiple levels deep
        >>> d = Dict(part_a1 = '{f', part_b1 = 'o', part_c1 = 'o}',
        ...   foo = "{part_a2}{part_b2}{part_c2}",
        ...   part_a2 = '{b', part_b2 = 'a', part_c2 = 'r}',
        ...   bar = 12)
        >>> d.expand_once("{part_a1}{part_b1}{part_c1}")
        12
        """

    def doctest_template_lambdas(self):
        r"""
        # Templates can call lambdas
        >>> d = Dict(a = 1, b = lambda x : x + 1)
        >>> d
        : Dict = {a = 1, b = <function <lambda> at 0x...>}
        >>> d.expand_once("foo {b(a)} bar")
        'foo 2 bar'
        """

    def doctest_TEFINAE(self):
        r"""
        # TEFINAE - Text Expansion Failure Is Not An Error
        >>> d = Dict(a = 1)
        >>> d.expand_once("{missing}")
        '{missing}'
        >>> d.expand_once("{a} {missing}")
        '1 {missing}'
        >>> d.expand_once("{a + missing}")
        '{a + missing}'
        """

    def doctest_template_nones(self):
        r"""
        # Nones should turn into empty strings
        >>> d = Dict(a = None, b = "x{a}y")
        >>> d.eval("a") is None
        True
        >>> d.eval("b")
        'x{a}y'
        >>> d.eval("{a}")
        Traceback (most recent call last):
        ...
        AssertionError
        >>> d.eval("{b}")
        Traceback (most recent call last):
        ...
        AssertionError
        >>> d.expand_once("a")
        'a'
        >>> d.expand_once("b")
        'b'
        >>> d.expand_once("{a}")
        ''
        >>> d.expand_once("{b}")
        'x{a}y'
        >>> d.expand_all("{b}")
        'xy'
        >>> d.expand_once("foo {b} bar")
        'foo x{a}y bar'
        >>> d.expand_all("foo {b} bar")
        'foo xy bar'
        """

    def doctest_order_of_expansion(self):
        r"""
        # Expand produces strings, but the below does _not_ try to add (string) "10" and (int) 0
        # because expanding {a} -> {b} -> "10" then joins the "10" with " + 0" to produce "10 + 0"
        # before the final eval.
        # FIXME this test isn't valid anymore

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
        >>> d.expand_once("{a} + 0")
        '{b} + 0'
        >>> d.expand_all("{a} + 0")
        '10 + 0'
        """

    def doctest_join_lists(self):
        r"""
        # Lists should be joined with spaces
        >>> d = Dict(flags = ["-O2", "-Wall"])
        >>> d.expand_once("cc {flags} main.c")
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
        >>> d.expand_once("{flags}")
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
        self.assertEqual(d.expand_all(r"{lb}a{rb}"), 1)
        self.assertEqual(d.expand_all(r"{bs}{lb}a{bs}{rb}"), r"\{a\}")

    def test_expand_failed_to_terminate1(self):
        # Single recursion
        with self.assertRaises(RecursionError):
            bad_dict = Dict(flarp = "asdf {flarp}")
            bad_dict.expand_once("{flarp}")

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
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    for t in doctests:
        t.shortDescription = lambda: None # type: ignore
    tests.addTests(doctests)
    return tests

if __name__ == "__main__":
    #d = Dict(a = Dict(b = "{c}", c = 10), c = 20, trace = True)
    #print(d.expand_once("{a.b}"))
    #e = d.expand_all("{a.b}")
    #print(d.expand_all("{a.b}"))

    d = Dict(a = "{{{{{{{{b.c}}}}}}}}", b = Dict(c = 888, d = 999), trace = True)
    print(d.expand_all("{a}"))


    #unittest.main(verbosity=1)
