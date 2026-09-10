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
    #hancho.init_for_testing(argv = [], log_trace = True, verbosity = "quiet") # type: ignore
    hancho.init_for_testing(argv = [], verbosity = "quiet") # type: ignore

def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    tests.addTests(doctests)
    return tests

####################################################################################################

class TestTemplates(unittest.TestCase):

    def test_basic_eval(self):
        d = Dict(a = 1, b = 2)
        self.assertEqual('a', Expander.expand("a", d))
        self.assertEqual('b', Expander.expand("b", d))
        self.assertEqual(1, Expander.expand("{a}", d))
        self.assertEqual(2, Expander.expand("{b}", d))
        self.assertEqual('1212', Expander.expand("{a}{b}{a}{b}", d))
        self.assertEqual(1212,Expander.expand("{{a}{b}{a}{b}}", d))
        self.assertEqual(None, Expander.expand(None, d))
        self.assertEqual("", Expander.expand("", d))

    # Pathological test cases

    def test_mutual_cycle(self):
        # only macros
        d = Dict(a="{b}", b="{a}")
        with self.assertRaises(RecursionError):
            Expander.expand("{a}", d)

        # inside a template
        d = Dict(foo="foo", x="{y}", y="{x}")
        with self.assertRaises(RecursionError):
            Expander.expand("echo {foo} {x} {foo}", d)

    def test_self_cycle(self):
        # only macro
        d = Dict(a = "{a}")
        with self.assertRaises(RecursionError):
            Expander.expand("{a}", d)

        # inside a template
        d = Dict(a = "x{a}")
        with self.assertRaises(RecursionError):
            Expander.expand("{a}", d)

    def test_expand_big_array(self):
        d = Dict(name = "prefix")
        count = 300 - 11
        templates = [f"{{name}}_{i:04d}" for i in range(count)]
        print(templates)

        expanded = cast(list, Expander.expand(templates, d))
        self.assertEqual(count, len(expanded))
        self.assertEqual(f"prefix_{count//2:04d}", expanded[count//2])

    def test_expand_long_chain_good(self):
        def make_dict(links):
            d = Dict()
            for i in range(links):
                key = f"k{i}"
                val = f"{{k{i+1}}}"
                d[key] = val
            d[f"k{links}"] = "sentinel"
            return d

        chain = make_dict(Expander.MAX_DEPTH-1)
        self.assertEqual("sentinel", Expander.expand("{k0}", chain))

    def test_expand_long_chain_bad(self):
        def make_dict(links):
            d = Dict()

            for i in range(links):
                key = f"k{i}"
                val = f"{{k{i+1}}}"
                d[key] = val
            d[f"k{links}"] = "sentinel"
            return d

        chain = make_dict(Expander.MAX_DEPTH)
        with self.assertRaises(RecursionError):
            self.assertEqual("sentinel", Expander.expand("{k0}", chain))

    def _test_expand_giant_string(self):
        def _test_string(count):
            d = Dict(name = "foo")
            chunks = [f">{{name}}_{i:02d}<" for i in range(count)]
            giant_string = " ".join(chunks)
            return Expander.expand(giant_string, d)

        # MAX_EVALS should pass, MAX_EVALS+1 should fail.
        result = _test_string(Expander.MAX_EVALS)
        self.assertTrue(f">foo_{Expander.MAX_EVALS // 2:02d}<" in result) #type:ignore

        with self.assertRaises(RecursionError):
            result = _test_string(Expander.MAX_EVALS + 1)

    def _test_user_recursion(self):
        # A user function that generates a RecursionError that's used inside a template should
        # propagate the error.
        def recursive():
            return recursive()
        d = Dict(func = recursive)
        with self.assertRaises(RecursionError):
            Expander.expand("{func()}", d)

    def test_macro_evals_to_list_of_macros(self):
        print()
        # If a macro evals to a list of macros, the nested macros _shoud_ be auto-expanded
        d = Dict(a=["{b}","{b}"], b="x")
        e = Expander.expand("{a}", d)
        self.assertEqual(["x","x"], e)

    def test_template_expands_to_template(self):
        d = Dict(
            e = Dict(
                a = "{foo}{bar}{baz}",
            ),
            foo = "Hello {",
            bar = "blep",
            baz = "} World!",
            blep = "Template",
        )
        self.assertEqual("Hello Template World!", Expander.expand("{a}", d.e))

    def test_resolve_in_up_or_self(self):
        # d.bar.qux -> foo -> {baz} -> baz = 3
        d = Dict(foo = "{baz}", bar = Dict(qux = "{foo}", baz = 2), baz = 3)
        self.assertEqual(3, Expander.expand("{qux}", d.bar))

        # d.bar.qux -> foo -> {baz} -> (fail expansion and goback to d) -> {baz} = 3
        d = Dict(foo = "{baz}", bar = Dict(qux = "{foo}", baz = 2))
        self.assertEqual(2, Expander.expand("{qux}", d.bar))

    def test_template_to_macro(self):
        # template expands to macro, macro refers to something in up, thing in up is a template -
        # second template should expand in up, not top
        # FIXME
        pass

    def test_macro_to_template(self):
        # macro refers to something in up, thing in up is a template, template produces a macro -
        # the second macro should eval in up, not top
        # FIXME
        pass

    def _test_macro_passthrough(self):
        _number = 42
        _text="hello world"
        _func = lambda x : x + 1  # noqa: E731
        _tuple = (_number, _text, _func)
        _map = Dict({"1" : _number, "2" : _text, "3" : _func})
        d = Dict(_number = _number, _text = _text, _func = _func, _tuple = _tuple, _map = _map)

        # Scalar types should pass through unchanged.
        self.assertIs(_number, Expander.expand("{_number}", d))
        self.assertIs(_text,   Expander.expand("{_text}", d))
        self.assertIs(_func,   Expander.expand("{_func}", d))

        # Containers should get copied.
        _tuple2 = cast(list, Expander.expand("{_tuple}", d))
        self.assertEqual(_number, _tuple2[0])
        self.assertEqual(_text,   _tuple2[1])
        self.assertEqual(_func,   _tuple2[2])

        _map2 = cast(dict, Expander.expand("{_map}", d))
        self.assertEqual(_number, _map2["1"])
        self.assertEqual(_text,   _map2["2"])
        self.assertEqual(_func,   _map2["3"])

    def _test_read_nested_c_first(self):
        # Reading a field from a nested Dict should read the _innermost_ 'c', as it is expanded in
        # the nested context.
        d = Dict(a = Dict(b = "{c}", c = 10), c = 20)
        result = Expander.expand("{a.b}", d)
        self.assertEqual(result, 10)

    def _test_TEFINAE(self):
        # TEFINAE - Text Expansion Failure Is Not An Error
        d = Dict(a = 1)
        self.assertEqual("{missing}", Expander.expand("{missing}", d))
        self.assertEqual("1 {missing}", Expander.expand("{a} {missing}", d))
        self.assertEqual("{a + missing}", Expander.expand("{a + missing}", d))

    def _test_template_nones(self):
        # Nones should turn into empty strings
        d = Dict(a = None, b = "x{a}y")
        self.assertEqual(Expander.expand("{a}", d), None)
        self.assertEqual(Expander.expand("{b}", d), 'xy')

    def _test_flatten_lists(self):
        # Lists should be flattened before joining with spaces
        d = Dict(flags = [[['a'], 'b'], 'c', 'd', ['e', 'f']])
        self.assertEqual('flags', Expander.expand("flags", d))
        self.assertEqual([[['a'], 'b'], 'c', 'd', ['e', 'f']], Expander.expand("{flags}", d))
        self.assertEqual("flags = 'a b c d e f'", Expander.expand("flags = '{flags}'", d))

    def _test_templates_with_escaped_char_proxies(self):
        # Testing escape sequences in templates is annoying. Double-check that we can use proxies
        # to build strings with escape sequences.
        d = Dict(a=1, bs="\\", lb="{", rb="}")
        self.assertEqual(Expander.expand(r"{lb}a{rb}", d), 1)
        self.assertEqual(Expander.expand(r"{bs}{lb}a{bs}{rb}", d), r"\{a\}")

    def _test_expand_failed_to_terminate1(self):
        # Single recursion
        with self.assertRaises(RecursionError):
            bad_dict = Dict(flarp="asdf {flarp}")
            Expander.expand("{flarp}", bad_dict)

    def _test_expand_failed_to_terminate2(self):
        # Double recursion
        with self.assertRaises(RecursionError):
            bad_dict = Dict(foo="asdf {bar}", bar="qwer {foo}")
            Expander.expand("{foo}", bad_dict)

    def _test_expand_failed_to_terminate3(self):
        # Recursion through 'subthing.foo', which can't be evaluated in 'subthing' and gets re-evaluated
        # in 'bad_dict'
        with self.assertRaises(RecursionError):
            subthing = Dict(foo="{subthing.foo} x")
            bad_dict = Dict(command="{subthing.foo}", subthing=subthing)
            Expander.expand("{command}", bad_dict)

    def _test_expand_nested_list(self):
        d = Dict(a = 1, b = 2, c = 3)
        v = ['a', ['b', ['c', ['{a}{b}{c}'], '{a}+{b}+{c}']]]
        r = Expander.expand(v, d)
        self.assertEqual(r, ['a', ['b', ['c', ['123'], '1+2+3']]])

    def _test_multi_eval(self):
        d = Dict(
            a = "'  test_mul",
            b = "ti_eval   '.strip() ",
            c = "{a}{b}",
            test_multi_eval = "it works!"
        )

        self.assertEqual(Expander.expand("c", d),         "c")
        self.assertEqual(Expander.expand("{c}", d),       "'  test_multi_eval   '.strip() ")
        self.assertEqual(Expander.expand("{{c}}", d),     "test_multi_eval")
        self.assertEqual(Expander.expand("{{{c}}}", d),   "it works!")
        self.assertEqual(Expander.expand("{{{{c}}}}", d), "{it works!}")

    def _test_embedded_eval(self):
        d = Dict(foo = "1 + 1", bar = "{baz}", baz = "2 + 2")
        self.assertEqual('1 + 1', Expander.expand("{foo}", d))
        self.assertEqual('1 + 1 2 + 2', Expander.expand("{foo} {bar}", d))
        d = Dict(foo = "1 + 1", bar = "{baz}", baz = "\"2 + 2\"")
        self.assertEqual('1 + 1', Expander.expand("{foo}", d))
        self.assertEqual('\"2 + 2\"', Expander.expand("{bar}", d))
        self.assertEqual('1 + 1 \"2 + 2\"', Expander.expand("{foo} {bar}", d))

#    # FIXME broken
#    def _test_inline_script(self):
#        # Load a tiny test script.
#
#        parent_script = hancho.cv_script.get()
#        env = hancho.cv_env.get()
#
#        script_path = os.path.join(os.getcwd(), "fake_script.hancho")
#        source = textwrap.dedent("""
#        import hancho
#        foo_in_script = [1, 2, 3]
#        """)
#
#        child_params = Dict(
#            path = script_path,
#            root = os.path.dirname(script_path),
#            is_repo = True,
#            blarp = 1234
#        )
#
#        script = hancho.Hancho.load_source(
#            env,
#            parent=parent_script,
#            #child_params=child_params,
#            source=source
#        )
#
#        #with hancho.cv_script.enter(script):
#
#            # Expanding 'Task' should read from hancho.py
#            #self.assertEqual(hancho.Task, Dict().expand("{Task}"))
#
#            # Expanding 'blarp' should read from the options passed into the script
#            #self.assertEqual(1234, Dict().expand("{blarp}"))
#
#            # Expanding 'foo_in_script' should read from the script module...
#            #self.assertEqual([1, 2, 3], Dict().expand("{foo_in_script}"))
#
#        # but not after we've left the script context.
#
#        #self.assertEqual("{foo_in_script}", Dict().expand("{foo_in_script}"))
#        #self.assertEqual("{blarp}", Dict().expand("{blarp}"))
#        self.assertIsNotNone(script)

#    # FIXME broken
#    def _test_alternate_delims(self):
#        d = hancho.Dict(foo = "bar")
#        o1 = hancho.Onion(layer1 = d, layer2 = Dict(delims='{}'))
#        o2 = hancho.Onion(layer1 = d, layer2 = Dict(delims='«»'))
#
#        self.assertEqual("bar",   Expander.expand("{foo}", o1))
#        self.assertEqual("{foo{", Expander.expand("{foo{", o2))
#        self.assertEqual("}foo}", Expander.expand("}foo}", o2))
#        self.assertEqual("}foo{", Expander.expand("}foo{", o2))
#        self.assertEqual("«foo»", Expander.expand("«foo»", o1))
#
#        self.assertEqual("bar",   Expander.expand("«foo»", o2))
#        self.assertEqual("«foo«", Expander.expand("«foo«", o2))
#        self.assertEqual("»foo»", Expander.expand("»foo»", o2))
#        self.assertEqual("»foo«", Expander.expand("»foo«", o2))
#        self.assertEqual("{foo}", Expander.expand("{foo}", o2))

####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
