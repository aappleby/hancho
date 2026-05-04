#!/usr/bin/python3
"""Test cases for Hancho's Dict class"""

import io
import contextlib
import sys
import unittest
import doctest

sys.path.append("..")
from hancho import Dict

####################################################################################################

class TestDict(unittest.TestCase):
    def setUp(self):
        sys.stdout.flush()

    def test_basic_access(self):
        d = Dict({'a': 1, 'b': 2})
        self.assertEqual(d.a, 1)
        self.assertEqual(d['b'], 2)
        with self.assertRaises(AttributeError):
            _ = d.missing
        with self.assertRaises(AttributeError):
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

    def test_value_semantics(self):
        d1 = Dict({'a': [1, 2]})
        d2 = Dict(d1)
        d2.a.append(3)
        self.assertEqual(d1.a, [1, 2])  # d1 should not be affected

####################################################################################################

def load_tests(loader, tests, ignore):
    tests.addTests(doctest.DocTestSuite(
        optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE,
    ))
    return tests
