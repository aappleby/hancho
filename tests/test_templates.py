#!/usr/bin/python3
"""Test cases for Hancho's text templating system"""

import os
import sys
import unittest

sys.path.append("..")

import hancho as hancho_proxy
from hancho import Dict, Expander

VERBOSITY = "critical"

# the hancho references hit this and it's bogus because of the weird way hancho intercepts
# attributes
# pyright: reportAttributeAccessIssue=false

####################################################################################################

def setUpModule():
    os.chdir(os.path.dirname(__file__))

    global hancho
    hancho = hancho_proxy.init_for_testing(__file__, f"--log.level={VERBOSITY}")

####################################################################################################

class TestTemplates(unittest.TestCase):

    def test_basic_eval(self):
        d = Dict(a = 1, b = 2)
        self.assertEqual('a', d.expand("a"))
        self.assertEqual('b', d.expand("b"))
        self.assertEqual(1, d.expand("{a}"))
        self.assertEqual(2, d.expand("{b}"))
        self.assertEqual('1212', d.expand("{a}{b}{a}{b}"))
        self.assertEqual(1212, d.expand("{{a}{b}{a}{b}}"))
        self.assertEqual(None, d.expand(None))
        self.assertEqual("", d.expand(""))

    def test_macro_to_fixed_point(self):
        d = Dict(a = "{b}", b = "{c}", c = "{d}")
        self.assertEqual("{d}", d.expand("{a}"))

    def test_expand_dict(self):
        d = Dict(a = Dict(foo = 1, bar = 2, baz = "x{foo}x{bar}x"))
        self.assertEqual(
            d.expand(">{a}<"),
            ">1 2 x{foo}x{bar}x<"
        )

        d = Dict(a = Dict(foo = 1, bar = 2, baz = "x{foo}x{bar}x"), foo = 3, bar = 4)
        self.assertEqual(
            d.expand(">{a}<"),
            ">1 2 x3x4x<"
        )

    def test_read_from_up(self):
        d1 = Dict(a = "foo")
        d2 = Dict(a = "bar")
        b = Dict(c = "{a}")

        b.set_up(d1)
        self.assertEqual("foo", b.expand("{c}"))

        b.set_up(d2)
        self.assertEqual("bar", b.expand("{c}"))

    def test_mutual_cycle(self):
        # only macros
        d = Dict(a="{b}", b="{a}")
        with self.assertRaises(RecursionError):
            d.expand("{a}")

        # inside a template
        d = Dict(foo="foo", x="{y}", y="{x}")
        with self.assertRaises(RecursionError):
            d.expand("echo {foo} {x} {foo}")

    def test_self_cycle(self):
        # only macro
        d = Dict(a = "{a}")
        with self.assertRaises(RecursionError):
            d.expand("{a}")

        # inside a template
        d = Dict(a = "x{a}")
        with self.assertRaises(RecursionError):
            d.expand("{a}")

    def test_expand_big_array(self):
        d = Dict(name = "prefix")
        count = Expander.MAX_EVALS
        templates = [f"{{name}}_{i:04d}" for i in range(count)]
        expanded = d.expand(templates, recursive = True)
        self.assertEqual(count, len(expanded))
        self.assertEqual(f"prefix_{count//2:04d}", expanded[count//2])

        with self.assertRaises(RecursionError):
            count = Expander.MAX_EVALS + 1
            templates = [f"{{name}}_{i:04d}" for i in range(count)]
            expanded = d.expand(templates, recursive = True)

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
        self.assertEqual("sentinel", chain.expand("{k0}"))

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
            self.assertEqual("sentinel", chain.expand("{k0}"))

    def test_expand_giant_string(self):
        def expand_string(count):
            d = Dict(name = "foo")
            chunks = [f">{{name}}_{i:02d}<" for i in range(count)]
            giant_string = " ".join(chunks)
            return d.expand(giant_string)

        # MAX_EVALS should pass, MAX_EVALS+1 should fail.
        result = expand_string(Expander.MAX_EVALS)
        self.assertTrue(f">foo_{Expander.MAX_EVALS // 2:02d}<" in result) #type:ignore

        with self.assertRaises(RecursionError):
            result = expand_string(Expander.MAX_EVALS + 1)

    def test_user_recursion(self):
        # A user function that generates a RecursionError that's used inside a template should
        # propagate the error.
        def recursive():
            return recursive()
        d = Dict(func = recursive)
        with self.assertRaises(RecursionError):
            d.expand("{func()}")

    def test_macro_evals_to_list_of_macros(self):
        # If a macro evals to a list of macros, the nested macros _shoud_ be auto-expanded
        d = Dict(a=["{b}","{b}"], b="x")
        e = d.expand("{a}", recursive = True)
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
        self.assertEqual("Hello Template World!", d.e.expand("{a}"))

    def test_resolve_in_up_or_self(self):
        # d.bar.qux -> foo -> {baz} -> baz = 3
        d = Dict(foo = "{baz}", bar = Dict(qux = "{foo}", baz = 2), baz = 3)
        self.assertEqual(3, d.bar.expand("{qux}"))

        # d.bar.qux -> foo -> {baz} -> (fail expansion and goback to d) -> {baz} = 3
        d = Dict(foo = "{baz}", bar = Dict(qux = "{foo}", baz = 2))
        self.assertEqual(2, d.bar.expand("{qux}"))

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

    def test_macro_passthrough(self):
        _number = 42
        _text="hello world"
        _func = lambda x : x + 1  # noqa: E731
        _tuple = (_number, _text, _func)
        _list = [1, 2, 3]
        _map = Dict({"1" : _number, "2" : _text, "3" : _func})
        d = Dict(x_number = _number, x_text = _text, x_func = _func, x_tuple = _tuple, x_list = _list, x_map = _map)

        self.assertIs(_number, d.expand("{x_number}"))
        self.assertIs(_text,   d.expand("{x_text}"))
        self.assertIs(_func,   d.expand("{x_func}"))
        self.assertIs(_tuple,  d.expand("{x_tuple}"))
        self.assertIs(_list,   d.expand("{x_list}"))
        self.assertIs(_map,    d.expand("{x_map}"))

    def test_read_nested_c_first(self):
        # Reading a field from a nested Dict should read the _innermost_ 'c', as it is expanded in
        # the nested context.
        d = Dict(a = Dict(b = "{c}", c = 10), c = 20)
        result = d.expand("{a.b}")
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
        self.assertEqual(d.expand("{a}"), None)
        self.assertEqual(d.expand("{b}"), 'xy')

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

    def test_expand_nested_list(self):
        d = Dict(a = 1, b = 2, c = 3)
        v = ['a', ['b', ['c', ['{a}{b}{c}'], '{a}+{b}+{c}']]]
        r = d.expand(v, recursive = True)
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

    def test_embedded_eval(self):
        d = Dict(foo = "1 + 1", bar = "{baz}", baz = "2 + 2")
        self.assertEqual('1 + 1', d.expand("{foo}"))
        self.assertEqual('1 + 1 2 + 2', d.expand("{foo} {bar}"))
        d = Dict(foo = "1 + 1", bar = "{baz}", baz = "\"2 + 2\"")
        self.assertEqual('1 + 1', d.expand("{foo}"))
        self.assertEqual('\"2 + 2\"', d.expand("{bar}"))
        self.assertEqual('1 + 1 \"2 + 2\"', d.expand("{foo} {bar}"))

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

####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
