#!/usr/bin/python3
"""Test cases for Hancho's text templating system"""

import sys
import unittest
import doctest

sys.path.append("..")
from hancho import Dict, Expander

####################################################################################################

class TestTemplates(unittest.TestCase):
    def setUp(self):
        sys.stdout.flush()

    def doctest_basic_eval(self):
        # Basic evaluation should work
        """
        >>> d = Dict(a = 1, b = 2)
        >>> d.eval("{a}")
        1
        >>> d.eval("{b}")
        2
        >>> d.eval("{a}{b}{a}{b}")
        1212
        """

    def doctest_basic_expand(self):
        # Expanding basic templates should work
        """
        >>> d = Dict(a = 1, b = 2, c = 3)
        >>> d.expand("{a}{b}{c}")
        '123'
        """

    def doctest_nested_braces(self):
        # Multiply-nested braces should work
        """
        >>> d = Dict(a = "b", b = 1)
        >>> d.eval("{{a}}")
        1
        >>> d.eval("{{{{{{a}}}}}}")
        1
        """

    def doctest_nested_templates(self):
        # We should be able to expand nested templates
        """
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
        # Additional {} around an expression essentially adds extra evals
        """
        >>> d = Dict(a = "1 + 1", b = "{a}")
        >>> d.expand("{a}")
        '1 + 1'
        >>> d.expand("{{a}}")
        '2'
        """

    def doctest_basic_merging(self):
        # Basic merging should work
        """
        >>> Dict()
        Dict @ ... { }
        >>> Dict(Dict(), dict(), dict())
        Dict @ ... { }
        >>> Dict(dict(), dict(bar = None))
        Dict @ ... { bar = None }
        >>> Dict(dict(), dict(bar = 3))
        Dict @ ... { bar = 3 }
        >>> Dict(foo = 1, bar = 2)
        Dict @ ... { foo = 1, bar = 2 }
        >>> Dict(dict(bar = None), dict())
        Dict @ ... { bar = None }
        >>> Dict(dict(bar = None), dict(bar = None))
        Dict @ ... { bar = None }
        """

    def doctest_right_overrides_left(self):
        # Right side should always override left side if right val is not None
        """
        >>> Dict(dict(bar = None), dict(bar = 3))
        Dict @ ... { bar = 3 }
        >>> Dict(dict(bar = 2), dict(bar = 3))
        Dict @ ... { bar = 3 }
        """

    def doctest_none_doesnt_override(self):
        # Right side should _not_ override left side if its val is None
        """
        >>> Dict(dict(bar = 2), dict(bar = None))
        Dict @ ... { bar = 2 }
        >>> Dict({'a': 1}, a = None)
        Dict @ ... { a = 1 }
        >>> Dict({'a': 1}, b = 2, c = 3)
        Dict @ ... { a = 1, b = 2, c = 3 }
        """

    def doctest_empty_dict_doesnt_override(self):
        # Empty right side should not clobber left side
        """
        >>> Dict(dict(bar = 2), dict())
        Dict @ ... { bar = 2 }
        """

    def doctest_attribute_and_item(self):
        # Both dict['foo'] and dict.foo should work
        """
        >>> d = Dict({'a': 1, 'b': 2})
        >>> (d.a, d['b'])
        (1, 2)
        """

    def doctest_immutable_dicts(self):
        # hancho.Dicts should be (as) immutable (as possible)
        """
        >>> d = Dict(a = 1)
        >>> d.a = 2
        Traceback (most recent call last):
        ...
        TypeError: ('Hancho.Dict is immutable', 'a', 2)

        >>> d['a'] = 2
        Traceback (most recent call last):
        ...
        TypeError: ('Hancho.Dict is immutable', 'a', 2)
        """


    def doctest_splitter(self):
        # The splitter should tag each chunk of text as a literal or a macro
        """
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
        # Mismatched braces shouldn't break anything
        """
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
        # Macros inside a string should _not_ be split
        """
        >>> Expander.split("foo '{bar}' baz")
        [L"foo '{bar}' baz"]
        >>> Expander.split('foo "{bar}" baz')
        [L'foo "{bar}" baz']
        """

    def doctest_split_innermost(self):
        # We should be extracting the innermost macros
        """
        >>> Expander.split("{{foo}}")
        [L'{', E'foo', L'}']
        """

    def doctest_dont_split_inside_string(self):
        # ...unless the innermost macro is inside a string
        """
        >>> Expander.split('{foo + "{bar}"}')
        [E'foo + "{bar}"']
        >>> Expander.split("{foo + '{bar}'}")
        [E"foo + '{bar}'"]
        """

    def doctest_order_of_expansion(self):
        """
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

    def test_templates_with_escaped_char_proxies(self):
        d = Dict(a = 1, bs = '\\', lb = '{', rb = '}')
        self.assertEqual(d.expand(r"{lb}a{rb}"), r"1")
        self.assertEqual(d.expand(r"{bs}{lb}a{bs}{rb}"), r"\{a\}")

####################################################################################################

def load_tests(loader, tests, ignore):
    tests.addTests(doctest.DocTestSuite(
        optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE,
    ))
    return tests
