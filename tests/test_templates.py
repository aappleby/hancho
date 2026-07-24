#!/usr/bin/python3
"""Test cases for Hancho's text templating system"""

import doctest
import os
import sys
import textwrap
import unittest
from typing import cast

sys.path.append("..")

import hancho
from hancho import Dict, Expander

####################################################################################################

def setUpModule():
    os.chdir(os.path.dirname(__file__))
    hancho.init(verbosity = "quiet")

def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    tests.addTests(doctests)
    return tests

####################################################################################################

class TestTemplates(unittest.TestCase):

    def test_basic_eval(self):
        d = Dict(a = 1, b = 2)
        self.assertEqual('a', d.expand2("a"))
        self.assertEqual('b', d.expand2("b"))
        self.assertEqual(1, d.expand2("{a}"))
        self.assertEqual(2, d.expand2("{b}"))
        self.assertEqual('1212', d.expand2("{a}{b}{a}{b}"))
        self.assertEqual(1212,d.expand2("{{a}{b}{a}{b}}"))

    # Pathological test cases

    def test_mutual_cycle(self):
        # only macros
        d = Dict(a="{b}", b="{a}")
        with self.assertRaises(RecursionError):
            d.expand2("{a}")

        # inside a template
        d = Dict(foo="foo", x="{y}", y="{x}")
        with self.assertRaises(RecursionError):
            d.expand2("echo {foo} {x} {foo}")

    def test_self_cycle(self):
        # only macro
        d = Dict(a = "{a}")
        with self.assertRaises(RecursionError):
            d.expand2("{a}")

        # inside a template
        d = Dict(a = "x{a}")
        with self.assertRaises(RecursionError):
            d.expand2("{a}")

    def test_expand_big_array(self):
        d = Dict(name = "prefix")
        count = 1000
        templates = [f"{{name}}_{i:04d}" for i in range(count)]

        expanded = cast(list, d.expand2(templates))
        self.assertEqual(count, len(expanded))
        self.assertEqual("prefix_0123", expanded[123])

        expanded = cast(list, d.expand2(templates))
        self.assertEqual(count, len(expanded))
        self.assertEqual("prefix_0123", expanded[123])

    def test_expand_long_chain(self):
        def make_dict(links):
            d = Dict()
            for i in range(links):
                key = f"k{i}"
                val = f"{{k{i+1}}}"
                d[key] = val
            d[f"k{links}"] = "sentinel"
            return d

        # Expanding a chain of macros or templates uses the recursion budget.
        # FIXME why is our budget off by one here?

        chain = make_dict(Expander.MAX_DEPTH - 1)
        self.assertEqual("sentinel", chain.expand2("{k0}"))

        chain = make_dict(Expander.MAX_DEPTH)
        with self.assertRaises(RecursionError):
            self.assertEqual("sentinel", chain.expand2("{k0}"))

        chain = make_dict(Expander.MAX_DEPTH + 1)
        with self.assertRaises(RecursionError):
            self.assertEqual("sentinel", chain.expand2("{k0}"))

    def test_expand_giant_string(self):
        def test(count):
            d = Dict(name = "foo")
            chunks = [f">{{name}}_{i:02d}<" for i in range(count)]
            giant_string = " ".join(chunks)
            return d.expand2(giant_string)

        # MAX_EVALS should pass, MAX_EVALS+1 should fail.
        result = test(Expander.MAX_EVALS)
        self.assertTrue(f">foo_{Expander.MAX_EVALS // 2:02d}<" in result) #type:ignore

        with self.assertRaises(RecursionError):
            result = test(Expander.MAX_EVALS + 1)

    def test_user_recursion(self):
        # A user function that generates a RecursionError that's used inside a template should
        # propagate the error.
        def recursive():
            return recursive()
        d = Dict(func = recursive)
        with self.assertRaises(RecursionError):
            d.expand2("{func()}")

    def test_macro_evals_to_list_of_macros(self):
        # If a macro evals to a list of macros, the nested macros _shoud_ be auto-expanded
        d = Dict(a=["{b}","{b}"], b="x")
        self.assertEqual(["x","x"], d.expand2("{a}"))

    def test_macro_passthrough(self):
        _number = 42
        _text="hello world"
        _func = lambda x : x + 1  # noqa: E731
        _tuple = (_number, _text, _func)
        _map = Dict({"1" : _number, "2" : _text, "3" : _func})
        d = Dict(_number = _number, _text = _text, _func = _func, _tuple = _tuple, _map = _map)

        # Scalar types should pass through unchanged.
        self.assertIs(_number, d.expand2("{_number}"))
        self.assertIs(_text,   d.expand2("{_text}"))
        self.assertIs(_func,   d.expand2("{_func}"))

        # Containers should get copied.
        _tuple2 = cast(list, d.expand2("{_tuple}"))
        self.assertEqual(_number, _tuple2[0])
        self.assertEqual(_text,   _tuple2[1])
        self.assertEqual(_func,   _tuple2[2])

        _map2 = cast(dict, d.expand2("{_map}"))
        self.assertEqual(_number, _map2["1"])
        self.assertEqual(_text,   _map2["2"])
        self.assertEqual(_func,   _map2["3"])

    def test_brace_escaping(self):
        d = Dict(text = "!!!!")
        template = r"{text} \{inside_esc{text}aped_braces\} {text}"
        self.assertEqual(r"!!!! \{inside_esc!!!!aped_braces\} !!!!", d.expand2(template))

    def test_read_nested_c_first(self):
        # Reading a field from a nested Dict should read the _innermost_ 'c', as it is expanded in
        # the nested context.
        d = Dict(a = Dict(b = "{c}", c = 10), c = 20)
        result = d.expand2("{a.b}")
        self.assertEqual(result, 10)

    def test_TEFINAE(self):
        # TEFINAE - Text Expansion Failure Is Not An Error
        d = Dict(a = 1)
        self.assertEqual("{missing}", d.expand2("{missing}"))
        self.assertEqual("1 {missing}", d.expand2("{a} {missing}"))
        self.assertEqual("{a + missing}", d.expand2("{a + missing}"))

    def test_template_nones(self):
        # Nones should turn into empty strings
        d = Dict(a = None, b = "x{a}y")
        self.assertEqual(d.expand2("{a}"), None)
        self.assertEqual(d.expand2("{b}"), 'xy')

    def test_flatten_lists(self):
        # Lists should be flattened before joining with spaces
        d = Dict(flags = [[['a'], 'b'], 'c', 'd', ['e', 'f']])
        self.assertEqual('flags', d.expand2("flags"))
        self.assertEqual([[['a'], 'b'], 'c', 'd', ['e', 'f']], d.expand2("{flags}"))
        self.assertEqual("flags = 'a b c d e f'", d.expand2("flags = '{flags}'"))

    def test_templates_with_escaped_char_proxies(self):
        # Testing escape sequences in templates is annoying. Double-check that we can use proxies
        # to build strings with escape sequences.
        d = Dict(a=1, bs="\\", lb="{", rb="}")
        self.assertEqual(d.expand2(r"{lb}a{rb}"), 1)
        self.assertEqual(d.expand2(r"{bs}{lb}a{bs}{rb}"), r"\{a\}")

    def test_expand_failed_to_terminate1(self):
        # Single recursion
        with self.assertRaises(RecursionError):
            bad_dict = Dict(flarp="asdf {flarp}")
            bad_dict.expand2("{flarp}")

    def test_expand_failed_to_terminate2(self):
        # Double recursion
        with self.assertRaises(RecursionError):
            bad_dict = Dict(foo="asdf {bar}", bar="qwer {foo}")
            bad_dict.expand2("{foo}")

    def test_expand_failed_to_terminate3(self):
        # Recursion through 'subthing.foo', which can't be evaluated in 'subthing' and gets re-evaluated
        # in 'bad_dict'
        with self.assertRaises(RecursionError):
            subthing = Dict(foo="{subthing.foo} x")
            bad_dict = Dict(command="{subthing.foo}", subthing=subthing)
            bad_dict.expand2("{command}")

    def test_expand_nested_list(self):
        d = Dict(a = 1, b = 2, c = 3)
        v = ['a', ['b', ['c', ['{a}{b}{c}'], '{a}+{b}+{c}']]]
        r = d.expand2(v)
        self.assertEqual(r, ['a', ['b', ['c', ['123'], '1+2+3']]])

    def test_multi_eval(self):
        d = Dict(
            a = "'  test_mul",
            b = "ti_eval   '.strip() ",
            c = "{a}{b}",
            test_multi_eval = "it works!"
        )

        self.assertEqual(d.expand2("c"),         "c")
        self.assertEqual(d.expand2("{c}"),       "'  test_multi_eval   '.strip() ")
        self.assertEqual(d.expand2("{{c}}"),     "test_multi_eval")
        self.assertEqual(d.expand2("{{{c}}}"),   "it works!")
        self.assertEqual(d.expand2("{{{{c}}}}"), "{it works!}")

    def test_embedded_eval(self):
        d = Dict(foo = "1 + 1", bar = "{baz}", baz = "2 + 2")
        self.assertEqual('1 + 1', d.expand2("{foo}"))
        self.assertEqual('1 + 1 2 + 2', d.expand2("{foo} {bar}"))
        d = Dict(foo = "1 + 1", bar = "{baz}", baz = "\"2 + 2\"")
        self.assertEqual('1 + 1', d.expand2("{foo}"))
        self.assertEqual('\"2 + 2\"', d.expand2("{bar}"))
        self.assertEqual('1 + 1 \"2 + 2\"', d.expand2("{foo} {bar}"))

    def test_inline_script(self):
        # Load a tiny test script.
        source = textwrap.dedent("""
        import hancho
        foo_in_script = [1, 2, 3]
        """)

        code = compile(source, __file__, "exec", dont_inherit=True)

        #def load2(cls, script_path, flags : Dict, source = None, code = None) -> Script:

        new_flags = Dict(hancho.ctx.flags, blarp = 1234, is_repo = False)

        new_script = hancho.Loader.load2("fake_script.hancho", new_flags, source = None, code = code)

        with hancho.ctx.set(Dict(hancho.ctx.get(), flags = new_flags, script = new_script)):
            # Expanding 'Task' should read from hancho.py
            self.assertEqual(hancho.Task, Dict().expand2("{Task}"))

            # Expanding 'blarp' should read from the options passed into the script
            self.assertEqual(1234, Dict().expand2("{blarp}"))

            # Expanding 'foo_in_script' should read from the script module...
            self.assertEqual([1, 2, 3], Dict().expand2("{foo_in_script}"))

        # but not after we've left the script context.

        self.assertEqual("{foo_in_script}", Dict().expand2("{foo_in_script}"))
        self.assertEqual("{blarp}", Dict().expand2("{blarp}"))

    def test_alternate_delims(self):
        d = hancho.Dict(foo = "bar")
        o1 = hancho.Onion(d, delims={'{':'}'})
        o2 = hancho.Onion(d, delims={'«':'»'})

        self.assertEqual("bar",   o1.expand("{foo}"))
        self.assertEqual("«foo»", o1.expand("«foo»"))

        self.assertEqual("{foo}", o2.expand("{foo}"))
        self.assertEqual("bar",   o2.expand("«foo»"))

####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
