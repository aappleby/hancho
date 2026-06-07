#!/usr/bin/python3
"""Test cases for Hancho's text templating system"""

import doctest
import os
import sys
import unittest

sys.path.append("..")

import hancho
from hancho import Dict, Expander

####################################################################################################


def setUpModule():
#    d = Dict(a=1, bs="\\", lb="{", rb="}")
#    result = d.eval(r"{lb}a{rb}")
#    print(repr(result))
#    result = d.expand(r"{bs}{lb}a{bs}{rb}")
#    print(repr(result))

    os.chdir(os.path.dirname(__file__))


def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    tests.addTests(doctests)
    return tests


####################################################################################################

class TestTemplates(unittest.TestCase):
    def setUp(self):
        hancho.init(verbosity = "quiet")
        sys.stdout.flush()

    def doctest_basic_eval(self):
        # Basic evaluation should work
        """
        >>> d = Dict(a = 1, b = 2)
        >>> d.expand("a")
        'a'
        >>> d.expand("b")
        'b'
        >>> d.expand("{a}{b}{a}{b}")
        '1212'
        >>> d.expand("{{a}{b}{a}{b}}")
        1212
        >>> d.expand("{a}")
        1
        >>> d.expand("{b}")
        2
        """

    def doctest_basic_expand(self):
        r"""
        # Expanding basic templates should work
        >>> d = Dict(a = 1, b = 2, c = 3)
        >>> d.expand("a")
        'a'

        >>> d.expand("{a}")
        1

        >>> d.expand("{a}{b}{c}")
        '123'
        """

    def doctest_nested_braces(self):
        r"""
        # Multiply-nested braces should work
        >>> d = Dict(a = "b", b = 777)
        >>> d.expand("{{a}}")
        777
        >>> d.expand("{{{{{{a}}}}}}")
        777
        """

    def doctest_nested_templates(self):
        r"""
        # We should be able to expand nested templates
        >>> d = Dict(a = Dict(b = "{c}"), c = 10)
        >>> d.expand("a.b")
        'a.b'
        >>> d.expand("{a.b}")
        10
        """

    def doctest_extra_braces(self):
        r"""
        # Additional {} around an expression essentially adds extra evals
        >>> d = Dict(a = "1 + 1", b = "{a}")
        >>> d.expand("{a}")
        '1 + 1'
        >>> d.expand("{{a}}")
        2
        """

    def doctest_read_nested_c_first(self):
        r"""
        # Reading a field from a nested Dict should read the _innermost_ 'c' if the Dict is inside
        # an Expander, as it is then expanded in the nested context.
        >>> d = Dict(a = Dict(b = "{c}", c = 10), c = 20)
        >>> e = Expander(d)
        >>> d.expand("{a.b}")
        20
        >>> e.expand("{a.b}")
        10
        """

    def test_read_nested_c_first(self):
        # Reading a field from a nested Dict should read the _innermost_ 'c', as it is expanded in the
        # nested context.
        d = Dict(a = Dict(b = "{c}", c = 10), c = 20)
        e = Expander(d)

        # This read is _not_ through the expander, so "{c}" will be evaluated in the _outer_
        # context.
        result = d.expand("{a.b}")
        self.assertEqual(result, 20)

        # This read _is_ through the expander - reading a.b will produce an Expander wrapped around
        # the inner dict which will then immediately expand "{c}" in the context of the inner dict
        # and return 10.
        # FIXME BROKEN
        result = Expander._expand("{a.b}", e)
        self.assertEqual(result, 10)

    def doctest_expand_before_eval(self):
        r"""
        # Wrapping a field in {} makes us expand it before eval
        # using ABC instead of abc because abc is a module name :/
        >>> d = Dict(A = 1, B = 2, C = 3, name_a = 'A', name_b = 'B', name_c = 'C')

        >>> d.expand("name_a + name_b + name_c")
        'name_a + name_b + name_c'

        >>> d.expand("{name_a + name_b + name_c}")
        'ABC'

        >>> d.expand("{name_a} + {name_b} + {name_c}")
        'A + B + C'

        >>> d.expand("{{name_a} + {name_b} + {name_c}}")
        6
        """

    def doctest_assemble_pieces(self):
        r"""
        # Expanding a template can assemble a new template from pieces, which then also gets expanded
        >>> d = Dict(part_a = '{f', part_b = 'o', part_c = 'o}', foo = 10)
        >>> d.expand("{part_a}{part_b}{part_c}")
        10

        # And that can go multiple levels deep
        >>> d = Dict(part_a1 = '{f', part_b1 = 'o', part_c1 = 'o}',
        ...   foo = "{part_a2}{part_b2}{part_c2}",
        ...   part_a2 = '{b', part_b2 = 'a', part_c2 = 'r}',
        ...   bar = 12)
        >>> d.expand("{part_a1}{part_b1}{part_c1}")
        12
        """

    def doctest_template_lambdas(self):
        r"""
        # Templates can call lambdas
        >>> d = Dict(a = 1, b = lambda x : x + 1)
        >>> d
        _ : Dict = {a = 1, b = <function>}
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
        >>> d.expand("a")
        'a'
        >>> d.expand("b")
        'b'
        >>> d.expand("{a}") is None
        True
        >>> d.expand("{b}")
        'xy'
        """

    def test_template_nones(self):
        # Nones should turn into empty strings
        d = Dict(a = None, b = "x{a}y")
        self.assertEqual(Expander._expand("a",   d), 'a')
        self.assertEqual(Expander._expand("b",   d), 'b')
        self.assertEqual(Expander._expand("{a}", d),  None)
        self.assertEqual(Expander._expand("{b}", d), 'xy')


    def doctest_order_of_expansion(self):
        r"""
        >>> d = Dict(a = "{b}", b = 10)
        >>> d.expand("{a} + 0")
        '10 + 0'
        """

    def doctest_join_lists(self):
        r"""
        # Lists should be joined with spaces
        >>> d = Dict(flags = ["-O2", "-Wall"])
        >>> d.expand("cc {flags} main.c")
        'cc -O2 -Wall main.c'

        #>>> d.eval("flags")
        #['-O2', '-Wall']
        """

    def doctest_flatten_lists(self):
        r"""
        # Lists should be flattened before joining with spaces
        >>> d = Dict(flags = [[['a'], 'b'], 'c', 'd', ['e', 'f']])
        >>> d.expand("flags")
        'flags'
        >>> d.expand("{flags}")
        [[['a'], 'b'], 'c', 'd', ['e', 'f']]
        >>> d.expand("flags = '{flags}'")
        "flags = 'a b c d e f'"
        """

    def test_templates_with_escaped_char_proxies(self):
        # Testing escape sequences in templates is annoying. Double-check that we can use proxies
        # to build strings with escape sequences.
        d = Dict(a=1, bs="\\", lb="{", rb="}")
        self.assertEqual(d.expand(r"{lb}a{rb}"), 1)
        self.assertEqual(d.expand(r"{bs}{lb}a{bs}{rb}"), r"\{a\}")

    def test_expand_failed_to_terminate1(self):
        # Single recursion
        with self.assertRaises(RecursionError):
            bad_dict = Dict(flarp="asdf {flarp}")
            bad_dict.expand("{flarp}")

    def test_expand_failed_to_terminate2(self):
        # Double recursion
        with self.assertRaises(RecursionError):
            bad_dict = Dict(foo="asdf {bar}", bar="qwer {foo}")
            bad_dict.expand("{foo}")

    def test_expand_failed_to_terminate3(self):
        # Recursion through 'subthing.foo', which can't be evaluated in 'subthing' and gets re-evaluated
        # in 'bad_dict'
        with self.assertRaises(RecursionError):
            subthing = Dict(foo="{subthing.foo} x")
            bad_dict = Dict(command="{subthing.foo}", subthing=subthing)
            bad_dict.expand("{command}")
        pass

    def test_recursive_expansion(self):
        d = Dict(a = 1, b = 2, c = 3)
        v = ['a', ['b', ['c', ['{a}{b}{c}'], '{a}+{b}+{c}']]]
        r = d.expand(v)
        self.assertEqual(r, ['a', ['b', ['c', ['123'], '1+2+3']]])

    def test_multi_eval(self):
        d = Dict(a = "'  test_mul", b = "ti_eval   '.strip() ", c = "{a}{b}")

        self.assertEqual(d.expand("c"),         "c")
        self.assertEqual(d.expand("{c}"),       "'  test_multi_eval   '.strip() ")
        self.assertEqual(d.expand("{{c}}"),     "test_multi_eval")
        self.assertEqual(d.expand("{{{c}}}"),   "{test_multi_eval}")
        self.assertEqual(d.expand("{{{{c}}}}"), "{{test_multi_eval}}")


    # This is gonna fail right now.
    def doctest_embedded_eval(self):
        """
        >>> d = Dict(foo = "1 + 1", bar = "{baz}", baz = "2 + 2")
        >>> d.expand("{foo}")
        '1 + 1'
        >>> d.expand("{foo} {bar}")
        '1 + 1 2 + 2'

        >>> d = Dict(foo = "1 + 1", bar = "{baz}", baz = "\\"2 + 2\\"")
        >>> d.expand("{foo}")
        '1 + 1'
        >>> d.expand("{bar}")
        '\"2 + 2\"'
        >>> d.expand("{foo} {bar}")
        '1 + 1 \"2 + 2\"'
        """


####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
