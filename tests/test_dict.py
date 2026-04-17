#!/usr/bin/python3
"""Test cases for Hancho's Dict class"""

import sys
import unittest

sys.path.append("..")
from hancho import Dict

####################################################################################################

class TestDict(unittest.TestCase):
    def setUp(self):
        sys.stdout.flush()

    def test_attribute_access(self):
        d = Dict({'a': 1, 'b': 2})
        self.assertEqual(d.a, 1)
        self.assertEqual(d['b'], 2)
        with self.assertRaises(AttributeError):
            _ = d.not_present

    def test_setattr_upgrades_dict(self):
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

    def test_immutable(self):
        d = Dict(foo = 1, bar = 2)
        with self.assertRaises(TypeError):
            d.foo = 123
        with self.assertRaises(TypeError):
            d['foo'] = 123

    def test_rightmost_none_does_not_override(self):
        d1 = Dict({'a': 1, 'b': 2})
        d2 = Dict({'a': None})
        merged = Dict(d1, d2)
        self.assertEqual(merged.a, 1)
        self.assertEqual(merged.b, 2)

    def test_nested_dicts_are_upgraded(self):
        d = Dict({'a': {'b': {'c': 1}}})
        self.assertIsInstance(d.a, Dict)
        self.assertIsInstance(d.a.b, Dict)
        self.assertEqual(d.a.b.c, 1)

    def test_setitem_upgrades_dict(self):
        d = Dict(foo = {'bar' : 42})
        self.assertIsInstance(d.foo, Dict)
        self.assertEqual(d.foo.bar, 42)

    def test_merge_with_multiple_dicts(self):
        d1 = Dict({'a': 1})
        d2 = Dict({'b': 2})
        d3 = Dict({'c': 3})
        merged = Dict(d1, d2, d3)
        self.assertEqual(merged.a, 1)
        self.assertEqual(merged.b, 2)
        self.assertEqual(merged.c, 3)

    def test_merge_with_kwargs(self):
        d1 = Dict({'a': 1})
        merged = Dict(d1, b=2, c=3)
        self.assertEqual(merged.a, 1)
        self.assertEqual(merged.b, 2)
        self.assertEqual(merged.c, 3)

    def test_attribute_error_on_missing(self):
        d = Dict()
        with self.assertRaises(AttributeError):
            _ = d.missing

    def test_no_override_with_none_in_kwargs(self):
        d1 = Dict({'a': 1})
        merged = Dict(d1, a=None)
        self.assertEqual(merged.a, 1)

####################################################################################################

if __name__ == "__main__":
    unittest.main()
