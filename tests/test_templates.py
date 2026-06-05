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
        hancho.init(quiet=True)
        #hancho.init(debug=True, verbose=True, trace=True)
        sys.stdout.flush()

    def doctest_basic_eval(self):
        # Basic evaluation should work
        """
        >>> d = Dict(a = 1, b = 2)
        >>> d.eval("a")
        1
        >>> d.expand("a")
        'a'
        >>> d.eval("b")
        2
        >>> d.expand("b")
        'b'
        >>> d.eval("{a}{b}{a}{b}")
        1212
        >>> d.expand("{a}{b}{a}{b}")
        '1212'
        >>> d.eval("{a}")
        1
        >>> d.expand("{a}")
        '1'
        >>> d.eval("{b}")
        2
        >>> d.expand("{b}")
        '2'
        """

    def doctest_basic_expand(self):
        r"""
        # Expanding basic templates should work
        >>> d = Dict(a = 1, b = 2, c = 3)
        >>> d.eval("a")
        1
        >>> d.expand("a")
        'a'

        >>> d.eval("{a}")
        1
        >>> d.expand("{a}")
        '1'

        >>> d.eval("{a}{b}{c}")
        123
        >>> d.expand("{a}{b}{c}")
        '123'
        """

    def doctest_nested_braces(self):
        r"""
        # Multiply-nested braces should work
        >>> d = Dict(a = "b", b = 777)
        >>> Expander._eval_template("{{a}}", d)
        '{b}'
        >>> d.expand("{{a}}")
        '777'
        >>> Expander._eval_template("{{{{{{a}}}}}}", d)
        '{{{{{b}}}}}'
        >>> d.expand("{{{{{{a}}}}}}")
        '777'
        """

    def doctest_nested_templates(self):
        r"""
        # We should be able to expand nested templates
        >>> d = Dict(a = Dict(b = "{c}"), c = 10)
        >>> Expander._eval_expr("a.b", d)
        '{c}'
        >>> Expander._eval_macro("{a.b}", d)
        '{c}'
        >>> Expander._eval_template(">{a.b}<", d)
        '>{c}<'
        >>> Expander._eval_expr("a.b", d)
        '{c}'
        >>> Expander._eval_macro("{a.b}", d)
        '{c}'
        >>> d.expand("a.b")
        'a.b'
        >>> d.expand("{a.b}")
        '10'
        """

    def doctest_extra_braces(self):
        r"""
        # Additional {} around an expression essentially adds extra evals
        >>> d = Dict(a = "1 + 1", b = "{a}")
        >>> Expander._eval_macro("{a}", d)
        '1 + 1'
        >>> Expander._eval_template("{{a}}", d)
        '{1 + 1}'
        >>> d.expand("{{a}}")
        '2'
        """

    def doctest_read_nested_c_first(self):
        r"""
        # Reading a field from a nested Dict should read the _innermost_ 'c' if the Dict is inside
        # an Expander, as it is then expanded in the nested context.
        >>> d = Dict(a = Dict(b = "{c}", c = 10), c = 20)
        >>> e = Expander(d, True)
        >>> Expander.expand("{a.b}", d)
        '20'
        >>> Expander.expand("{a.b}", e)
        '10'
        """

    def test_read_nested_c_first(self):
        # Reading a field from a nested Dict should read the _innermost_ 'c', as it is expanded in the
        # nested context.
        d = Dict(a = Dict(b = "{c}", c = 10), c = 20)
        e = Expander(d, True)

        # This read is _not_ through the expander, so "{c}" will be evaluated in the _outer_
        # context.
        result = Expander.expand("{a.b}", d)
        self.assertEqual(result, '20')

        result = d.expand("{a.b}")
        self.assertEqual(result, '10')

        # This read _is_ through the expander - reading a.b will produce an Expander wrapped around
        # the inner dict which will then immediately expand "{c}" in the context of the inner dict
        # and return 10.
        result = Expander.expand("{a.b}", e)
        self.assertEqual(result, '10')

    def doctest_expand_before_eval(self):
        r"""
        # Wrapping a field in {} makes us expand it before eval
        # using ABC instead of abc because abc is a module name :/
        >>> d = Dict(A = 1, B = 2, C = 3, name_a = 'A', name_b = 'B', name_c = 'C')

        >>> d.eval("name_a + name_b + name_c")
        'ABC'
        >>> d.expand("name_a + name_b + name_c")
        'name_a + name_b + name_c'

        >>> d.eval("{name_a + name_b + name_c}")
        'ABC'
        >>> d.expand("{name_a + name_b + name_c}")
        'ABC'

        >>> d.eval("{name_a} + {name_b} + {name_c}")
        6
        >>> d.expand("{name_a} + {name_b} + {name_c}")
        'A + B + C'

        >>> d.eval("{{name_a} + {name_b} + {name_c}}")
        6
        >>> d.expand("{{name_a} + {name_b} + {name_c}}")
        '6'
        """

    def doctest_assemble_pieces(self):
        r"""
        # Expanding a template can assemble a new template from pieces, which then also gets expanded
        >>> d = Dict(part_a = '{f', part_b = 'o', part_c = 'o}', foo = 10)
        >>> Expander._eval_template("{part_a}{part_b}{part_c}", d)
        '{foo}'
        >>> d.eval("{part_a}{part_b}{part_c}")
        10

        # And that can go multiple levels deep
        >>> d = Dict(part_a1 = '{f', part_b1 = 'o', part_c1 = 'o}',
        ...   foo = "{part_a2}{part_b2}{part_c2}",
        ...   part_a2 = '{b', part_b2 = 'a', part_c2 = 'r}',
        ...   bar = 12)
        >>> Expander._eval_template("{part_a1}{part_b1}{part_c1}", d)
        '{foo}'
        >>> d.eval("{part_a1}{part_b1}{part_c1}")
        12
        """

    def doctest_template_lambdas(self):
        r"""
        # Templates can call lambdas
        >>> d = Dict(a = 1, b = lambda x : x + 1)
        >>> d
        _ : Dict = {a = 1, b : function = <object>}
        >>> Expander._eval_template("foo {b(a)} bar", d)
        'foo 2 bar'
        """

    def doctest_TEFINAE(self):
        r"""
        # TEFINAE - Text Expansion Failure Is Not An Error
        >>> d = Dict(a = 1)
        >>> Expander._eval_macro("{missing}", d)
        '{missing}'
        >>> Expander._eval_template("{a} {missing}", d)
        '1 {missing}'
        >>> Expander._eval_macro("{a + missing}", d)
        '{a + missing}'
        """

    def doctest_template_nones(self):
        r"""
        # Nones should turn into empty strings
        >>> d = Dict(a = None, b = "x{a}y")
        >>> Expander._eval_expr("a", d) is None
        True
        >>> Expander._eval_expr("b", d)
        'x{a}y'
        >>> Expander._eval_macro("{a}", d) is None
        True
        >>> Expander._eval_macro("{b}", d)
        'x{a}y'
        >>> Expander.expand("a", d)
        'a'
        >>> Expander.expand("b", d)
        'b'
        >>> Expander._eval_macro("{b}", d)
        'x{a}y'
        >>> d.expand("{b}")
        'xy'
        >>> Expander._eval_template("foo {b} bar", d)
        'foo x{a}y bar'
        >>> d.expand("foo {b} bar")
        'foo xy bar'
        """

    def doctest_order_of_expansion(self):
        r"""
        >>> d = Dict(a = "{b}", b = 10)
        >>> Expander._eval_template("{a} + 0", d)
        '{b} + 0'
        >>> d.expand("{a} + 0")
        '10 + 0'
        """

    def doctest_join_lists(self):
        r"""
        # Lists should be joined with spaces
        >>> d = Dict(flags = ["-O2", "-Wall"])
        >>> Expander._eval_template("cc {flags} main.c", d)
        'cc -O2 -Wall main.c'
        >>> d.eval("flags")
        ['-O2', '-Wall']
        """

    def doctest_flatten_lists(self):
        r"""
        # Lists should be flattened before joining with spaces
        >>> d = Dict(flags = [[['a'], 'b'], 'c', 'd', ['e', 'f']])
        >>> d.eval("flags")
        [[['a'], 'b'], 'c', 'd', ['e', 'f']]
        >>> d.expand("flags")
        'flags'
        >>> d.eval("{flags}")
        [[['a'], 'b'], 'c', 'd', ['e', 'f']]
        >>> d.expand("{flags}")
        'a b c d e f'
        >>> d.eval("flags = '{flags}'")
        "flags = 'a b c d e f'"
        >>> d.expand("flags = '{flags}'")
        "flags = 'a b c d e f'"
        """

    def test_templates_with_escaped_char_proxies(self):
        # Testing escape sequences in templates is annoying. Double-check that we can use proxies
        # to build strings with escape sequences.
        d = Dict(a=1, bs="\\", lb="{", rb="}")
        self.assertEqual(d.eval(r"{lb}a{rb}"), 1)
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


####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
