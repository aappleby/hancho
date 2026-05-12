#!/usr/bin/python3
"""Test cases for Hancho's Dict class"""

import io
import contextlib
import sys
import unittest
import doctest

sys.path.append("..")
import hancho
from hancho import Dict

####################################################################################################

class TestDict(unittest.TestCase):
    def setUp(self):
        hancho.init()
        sys.stdout.flush()

    def test_basic_access(self):
        d = Dict({'a': 1, 'b': 2})
        self.assertEqual(d.a, 1)
        self.assertEqual(d['b'], 2)
        with self.assertRaises(AttributeError):
            _ = d.missing
        with self.assertRaises(KeyError):
            _ = d['missing']

    def doctest_dict_upgrades(self):
        # Internal dicts should be upgraded to hancho.Dict
        """
        >>> d = Dict(a = {'b' : {'c' : 1}})
        >>> type(d)
        <class 'hancho.Dict'>
        >>> type(d.a)
        <class 'hancho.Dict'>
        >>> type(d.a.b)
        <class 'hancho.Dict'>
        >>> type(d.a.b.c)
        <class 'int'>
        """

    def test_init_upgrades_dict(self):
        d = Dict(child = {'x': 1})
        self.assertIsInstance(d.child, Dict)
        self.assertEqual(d.child.x, 1)

    def test_merge_rightmost_wins(self):
        d1 = Dict({'a': 1, 'b': 2})
        d2 = Dict({'b': 3, 'c': 4})
        merged = Dict(d1, d2)
        self.assertEqual(merged.a, 1)
        self.assertEqual(merged.b, 3)
        self.assertEqual(merged.c, 4)

    def test_recursive_merge(self):
        d1 = Dict({'a': {'x': 1, 'y': 2}})
        d2 = Dict({'a': {'y': 3, 'z': 4}})
        merged = Dict(d1, d2)
        self.assertIsInstance(merged.a, Dict)
        self.assertEqual(merged.a.x, 1)
        self.assertEqual(merged.a.y, 3)
        self.assertEqual(merged.a.z, 4)

    def doctest_basic_merging(self):
        # Basic merging should work
        r"""
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

    def doctest_none_doesnt_override(self):
        # Right side should _not_ override left side if its val is None
        r"""
        >>> Dict(dict(bar = 2), dict(bar = None))
        Dict @ ... { bar = 2 }
        >>> Dict({'a': 1}, a = None)
        Dict @ ... { a = 1 }
        >>> Dict({'a': 1}, b = 2, c = 3)
        Dict @ ... { a = 1, b = 2, c = 3 }
        """

    def doctest_empty_dict_doesnt_override(self):
        # Empty right side should not clobber left side
        r"""
        >>> Dict(dict(bar = 2), dict())
        Dict @ ... { bar = 2 }
        """

    def doctest_attribute_and_item(self):
        # Both dict['foo'] and dict.foo should work
        r"""
        >>> d = Dict({'a': 1, 'b': 2})
        >>> (d.a, d['b'])
        (1, 2)
        """

    # Immutability disabled for now, going to revisit with MappingProxyType later

#    def doctest_immutable_dicts(self):
#        # hancho.Dicts should be (as) immutable (as possible)
#        r"""
#        >>> d = Dict(a = 1)
#        >>> d.a = 2
#        Traceback (most recent call last):
#        ...
#        TypeError: ('Hancho.Dict is immutable', 'a', 2)
#
#        >>> d['a'] = 2
#        Traceback (most recent call last):
#        ...
#        TypeError: ('Hancho.Dict is immutable', 'a', 2)
#        """

    def doctest_right_overrides_left(self):
        # Right side should always override left side if right val is not None
        r"""
        >>> Dict(dict(bar = None), dict(bar = 3))
        Dict @ ... { bar = 3 }
        >>> Dict(dict(bar = 2), dict(bar = 3))
        Dict @ ... { bar = 3 }
        """

####################################################################################################

def load_tests(loader, tests, ignore):
    tests.addTests(doctest.DocTestSuite(
        optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE,
    ))
    return tests
