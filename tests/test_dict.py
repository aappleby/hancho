#!/usr/bin/python3
"""Test cases for Hancho's Dict class"""

import unittest

from hancho import Dict

# FIXME test that nested dicts and arrays get deep copied
# FIXME test fill()

# --------------------------------------------------------------------------------------------------

class TestDict(unittest.TestCase):

    def test_basic_access(self):
        d = Dict({"a": 1, "b": 2})
        self.assertEqual(d.a, 1)
        self.assertEqual(d["b"], 2)
        with self.assertRaises(AttributeError):
            _ = d.missing
        with self.assertRaises(KeyError):
            _ = d["missing"]

    def test_dict_upgrades(self):
        # Internal dicts should be upgraded to hancho.Dict
        d = Dict(a = {'b' : {'c' : 1}})
        self.assertIs(Dict, type(d))
        self.assertIs(Dict, type(d.a))
        self.assertIs(Dict, type(d.a.b))
        self.assertIs(int,  type(d.a.b.c))

    def test_merge_rightmost_wins(self):
        d1 = Dict({"a": 1, "b": 2})
        d2 = Dict({"b": 3, "c": 4})
        merged = Dict(d1, d2)
        self.assertEqual(merged.a, 1)
        self.assertEqual(merged.b, 3)
        self.assertEqual(merged.c, 4)

    def test_recursive_merge(self):
        d1 = Dict({"a": {"x": 1, "y": 2}})
        d2 = Dict({"a": {"y": 3, "z": 4}})
        merged = Dict(d1, d2)
        self.assertIsInstance(merged.a, Dict)
        self.assertEqual(merged.a.x, 1)
        self.assertEqual(merged.a.y, 3)
        self.assertEqual(merged.a.z, 4)

    def test_basic_merging(self):
        # Basic merging should work
        r = Dict()
        self.assertEqual(0, len(r))
        r = Dict(Dict(), {}, {})
        self.assertEqual(0, len(r))
        r = Dict({}, {"bar": None})
        self.assertEqual(1, len(r))
        self.assertEqual(None, r.bar)

        r = Dict({}, {"bar" : 3})
        self.assertEqual(1, len(r))
        self.assertEqual(3, r.bar)

    def test_right_overrides_left(self):
        # Right side should always override left side if right val is not None
        a = Dict({"bar": None}, {"bar": 3})
        self.assertEqual(3, a.bar)
        b = Dict({"bar": 2}, {"bar": 3})
        self.assertEqual(3, b.bar)
        c = Dict({"bar": 4}, {"bar": None})
        self.assertEqual(4, c.bar)

    def test_none_doesnt_override(self):
        # Right side should _not_ override left side if its val is None
        r = Dict({"bar": 2}, {"bar": None})
        self.assertEqual(2, r.bar)
        r = Dict({'a': 1}, a = None)
        self.assertEqual(1, r.a)
        r = sorted(Dict({'a': 1}, b = 2, c = 3).items())
        self.assertEqual([('a', 1), ('b', 2), ('c', 3)], r)

    def test_empty_dict_doesnt_override(self):
        # Empty right side should not clobber left side
        d = Dict({"bar": 2}, {})
        self.assertEqual(1, len(d))
        self.assertEqual(2, d.bar)

# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=999)
