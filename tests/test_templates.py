#!/usr/bin/python3
"""Test cases for Hancho's text templating system"""

import doctest
import os
import sys
import unittest
from typing import cast

sys.path.append("..")

import hancho
from hancho import Dict, Expander

####################################################################################################


def setUpModule():
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

    def tearDown(self) -> None:
        return super().tearDown()

    def test_basic_eval(self):
        d = Dict(a = 1, b = 2)
        self.assertEqual('a', d.expand("a"))
        self.assertEqual('b', d.expand("b"))
        self.assertEqual(1, d.expand("{a}"))
        self.assertEqual(2, d.expand("{b}"))
        self.assertEqual('1212', d.expand("{a}{b}{a}{b}"))
        self.assertEqual(1212,d.expand("{{a}{b}{a}{b}}"))

    # Pathological test cases

    def test_mutual_cycle(self):
        # only macros
        d = Dict(a="{b}", b="{a}")
        with self.assertRaises(RecursionError):
            _ = Expander(d).a

        # inside a template
        d = Dict(foo="foo", x="{y}", y="{x}")
        with self.assertRaises(RecursionError):
            _ = Expander(d).expand("echo {foo} {x} {foo}")

    def test_self_cycle(self):
        # only macro
        d = Dict(a = "{a}")
        with self.assertRaises(RecursionError):
            _ = Expander(d).a

        # inside a template
        d = Dict(a = "x{a}")
        with self.assertRaises(RecursionError):
            _ = Expander(d).a

    def test_expand_big_array(self):
        d = Dict(name = "prefix")
        count = 1000
        templates = [f"{{name}}_{i:04d}" for i in range(count)]

        expanded = cast(list, d.expand(templates))
        self.assertEqual(count, len(expanded))
        self.assertEqual("prefix_0123", expanded[123])

        expanded = cast(list, Expander(d).expand(templates))
        self.assertEqual(count, len(expanded))
        self.assertEqual("prefix_0123", expanded[123])

    def test_autoexpand_long_chain(self):
        def make_dict(links):
            d = Dict()
            for i in range(links):
                key = f"k{i}"
                val = f"{{k{i+1}}}"
                d[key] = val
            d[f"k{links}"] = "sentinel"
            return d

        # Auto-expanding a chain of macros or templates uses the recursion budget.
        # FIXME why is our budget off by one here?

        chain = make_dict(Expander.MAX_DEPTH - 1)
        self.assertEqual("sentinel", Expander._expand("{k0}", Expander(chain)))

        chain = make_dict(Expander.MAX_DEPTH)
        with self.assertRaises(RecursionError):
            self.assertEqual("sentinel", Expander._expand("{k0}", Expander(chain)))

        chain = make_dict(Expander.MAX_DEPTH + 1)
        with self.assertRaises(RecursionError):
            self.assertEqual("sentinel", Expander._expand("{k0}", Expander(chain)))

    def test_expand_long_chain(self):
        def make_dict(links):
            d = Dict()
            for i in range(links):
                key = f"k{i}"
                val = f"{{k{i+1}}}"
                d[key] = val
            d[f"k{links}"] = "sentinel"
            return d

        # Expanding a chain of macros or templates _without_ using Expander uses the eval budget.
        # FIXME why is our budget off by one here?

        chain = make_dict(Expander.MAX_EVALS - 1)
        self.assertEqual("sentinel", Expander._expand("{k0}", chain))

        chain = make_dict(Expander.MAX_EVALS)
        with self.assertRaises(RecursionError):
            self.assertEqual("sentinel", Expander._expand("{k0}", chain))

        chain = make_dict(Expander.MAX_EVALS + 1)
        with self.assertRaises(RecursionError):
            self.assertEqual("sentinel", Expander._expand("{k0}", chain))

    def test_expand_giant_string(self):
        def test(count):
            d = Dict(name = "foo")
            chunks = [f">{{name}}_{i:02d}<" for i in range(count)]
            giant_string = " ".join(chunks)
            return Expander(d).expand(giant_string)

        # MAX_EVALS should pass, MAX_EVALS+1 should fail.
        result = test(Expander.MAX_EVALS)
        self.assertTrue(f">foo_{Expander.MAX_EVALS // 2:02d}<" in result) # type: ignore

        with self.assertRaises(RecursionError):
            result = test(Expander.MAX_EVALS + 1)

    def test_user_recursion(self):
        # A user function that generates a RecursionError that's used inside a template should
        # propagate the error.
        def recursive():
            return recursive()
        d = Dict(func = recursive)
        with self.assertRaises(RecursionError):
            d.expand("{func()}")

    def test_macro_evals_to_list_of_macros(self):
        # If a macro evals to a list of macros and we're expanding it in a Dict context,
        # the list should _not_ be expanded as a non-template or non-string terminates expansion.
        d = Dict(a=["{b}","{b}"], b="x")
        self.assertEqual(["{b}","{b}"], Expander._expand("{a}", d))

        # But, if we're in an Expander context, the list _should_ be auto-expanded.
        e = Expander(d)
        self.assertEqual(["x","x"], Expander._expand("{a}", e))

    def test_macro_passthrough(self):
        number = 42
        text="hello world"
        thing = object()
        func = lambda x : x + 1  # noqa: E731
        map = {"1" : number, "2" : text, "3" : thing, "4" : func}
        tuple = (number, text, thing, func, map)
        d = Dict(number = number, text = text, thing = thing, func = func, map = map, tuple = tuple)

        self.assertIs(number, d.expand("{number}"))
        self.assertIs(text,   d.expand("{text}"))
        self.assertIs(thing,  d.expand("{thing}"))
        self.assertIs(func,   d.expand("{func}"))
        #self.assertIs(map,    d.expand("{map}"))
        #self.assertIs(tuple,  d.expand("{tuple}"))

        self.assertIs(number, Expander(d).number)
        self.assertIs(text,   Expander(d).text)
        self.assertIs(thing,  Expander(d).thing)
        self.assertIs(func,   Expander(d).func)
        # FIXME this one fails?  why?
        #self.assertIs(map,    Expander(d).map)
        # FIXME this one fails?  why?
        #self.assertIs(tuple,  Expander(d).tuple)

    def test_brace_escaping(self):
        d = Dict(text = "!!!!")
        template = r"{text} \{inside_esc{text}aped_braces\} {text}"
        self.assertEqual(r"!!!! \{inside_esc!!!!aped_braces\} !!!!", d.expand(template))

    def test_read_nested_c_first(self):
        # Reading a field from a nested Dict should read the _innermost_ 'c', as it is expanded in the
        # nested context.
        d = Dict(a = Dict(b = "{c}", c = 10), c = 20)

        # This read is _not_ through the expander, so "{c}" will be evaluated in the _outer_
        # context.
        result = Expander._expand("{a.b}", d)
        self.assertEqual(result, 20)

        # This read _is_ through the expander - reading a.b will produce an Expander wrapped around
        # the inner dict which will then immediately expand "{c}" in the context of the inner dict
        # and return 10.
        e = Expander(d)
        result = Expander._expand("{a.b}", e)
        self.assertEqual(result, 10)

    def test_TEFINAE(self):
        # TEFINAE - Text Expansion Failure Is Not An Error

        d = Dict(a = 1)
        self.assertEqual("{missing}", d.expand("{missing}"))
        self.assertEqual("1 {missing}", d.expand("{a} {missing}"))
        self.assertEqual("{a + missing}", d.expand("{a + missing}"))

    def test_template_nones(self):
        # Nones should turn into empty strings
        d = Dict(a = None, b = "x{a}y")
        self.assertEqual(Expander._expand("a",   d), 'a')
        self.assertEqual(Expander._expand("b",   d), 'b')
        self.assertEqual(Expander._expand("{a}", d),  None)
        self.assertEqual(Expander._expand("{b}", d), 'xy')


    def test_flatten_lists(self):
        # Lists should be flattened before joining with spaces
        d = Dict(flags = [[['a'], 'b'], 'c', 'd', ['e', 'f']])
        self.assertEqual('flags', d.expand("flags"))
        self.assertEqual([[['a'], 'b'], 'c', 'd', ['e', 'f']], d.expand("{flags}"))
        self.assertEqual("flags = 'a b c d e f'", d.expand("flags = '{flags}'"))

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

        with self.assertRaises(RecursionError):
            bad_dict = Expander.wrap(Dict(flarp="asdf {flarp}"))
            bad_dict.expand("{flarp}")

    def test_expand_failed_to_terminate2(self):
        # Double recursion
        with self.assertRaises(RecursionError):
            bad_dict = Dict(foo="asdf {bar}", bar="qwer {foo}")
            bad_dict.expand("{foo}")
        with self.assertRaises(RecursionError):
            bad_dict = Expander.wrap(Dict(foo="asdf {bar}", bar="qwer {foo}"))
            bad_dict.expand("{foo}")

    def test_expand_failed_to_terminate3(self):
        # Recursion through 'subthing.foo', which can't be evaluated in 'subthing' and gets re-evaluated
        # in 'bad_dict'
        with self.assertRaises(RecursionError):
            subthing = Dict(foo="{subthing.foo} x")
            bad_dict = Dict(command="{subthing.foo}", subthing=subthing)
            bad_dict.expand("{command}")
        with self.assertRaises(RecursionError):
            subthing = Expander.wrap(Dict(foo="{subthing.foo} x"))
            bad_dict = Expander.wrap(Dict(command="{subthing.foo}", subthing=subthing))
            bad_dict.expand("{command}")

    def test_expand_nested_list(self):
        d = Dict(a = 1, b = 2, c = 3)
        v = ['a', ['b', ['c', ['{a}{b}{c}'], '{a}+{b}+{c}']]]
        r = d.expand(v)
        self.assertEqual(r, ['a', ['b', ['c', ['123'], '1+2+3']]])

    def test_multi_eval(self):
        d = Dict(
            a = "'  test_mul",
            b = "ti_eval   '.strip() ",
            c = "{a}{b}",
            test_multi_eval = "it works!"
        )

        self.assertEqual(d.expand("c"),         "c")
        self.assertEqual(d.expand("{c}"),       "'  test_multi_eval   '.strip() ")
        self.assertEqual(d.expand("{{c}}"),     "test_multi_eval")
        self.assertEqual(d.expand("{{{c}}}"),   "it works!")
        self.assertEqual(d.expand("{{{{c}}}}"), "{it works!}")


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
