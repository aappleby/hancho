#!/usr/bin/python3
"""Test cases for Hancho's text templating system"""

import sys
import unittest
import doctest

sys.path.append("..")
from hancho import Dict

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

    def doctest_nested_braces(self):
        # Multiply-nested braces should work
        """
        >>> d = Dict(a = "b", b = 1)
        >>> d.eval("{{a}}")
        1
        >>> d.eval("{{{{{{a}}}}}}")
        1
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


    def test_basic_expansion(self):
        # Basic evaluation should work
        d = Dict(a = 1, b = 2, c = 3)
        e = d.expand("{a}{b}{c}")
        self.assertEqual(e, "123")

    def test_nested_braces(self):
        # Multiply-nested braces should work
        d = Dict(a = "b", b = 1)
        self.assertEqual(1, d.eval("{{a}}"))
        self.assertEqual(1, d.eval("{{{{{{a}}}}}}"))

    def test_extra_braces(self):
        # Additional {} around an expression essentially adds extra evals
        d = Dict(a = "1 + 1", b = "{a}")
        self.assertEqual('1 + 1', d.expand("{a}"))
        self.assertEqual('2', d.expand("{{a}}"))

####################################################################################################

def load_tests(loader, tests, ignore):
    tests.addTests(doctest.DocTestSuite(
        optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE,
    ))
    return tests
