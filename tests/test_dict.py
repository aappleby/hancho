#!/usr/bin/python3
"""Test cases for Hancho's Dict class"""

import copy
import unittest
from collections import abc

from hancho import Dict

# FIXME test fill()

# --------------------------------------------------------------------------------------------------

class TestDict(unittest.TestCase):

    def test_basic_access(self):
        d = Dict({"a": 1, "b": 2})
        self.assertEqual(d.a, 1)
        self.assertEqual(d["b"], 2)

        d = Dict({"a": 1}, {"b": 2})
        self.assertEqual(d.a, 1)
        self.assertEqual(d["b"], 2)

        d = Dict(a = 1, b = 2)
        self.assertEqual(d.a, 1)
        self.assertEqual(d["b"], 2)

        with self.assertRaises(AttributeError):
            _ = d.missing
        with self.assertRaises(KeyError):
            _ = d["missing"]

    def test_merge_rightmost_wins(self):
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 3, "c": 4}
        merged = Dict(d1, d2)
        self.assertEqual(merged.a, 1)
        self.assertEqual(merged.b, 3)
        self.assertEqual(merged.c, 4)

    def test_recursive_merge(self):
        d1 = {"a": {"x": 1, "y": 2}}
        d2 = {"a": {"y": 3, "z": 4}}
        merged = Dict(d1, d2)
        #self.assertIsInstance(merged.a, Dict)
        self.assertEqual(merged.a.x, 1)
        self.assertEqual(merged.a.y, 3)
        self.assertEqual(merged.a.z, 4)

    def test_basic_merging(self):
        # Basic merging should work
        r = Dict()
        self.assertEqual(0, len(r))
        r = Dict({}, {}, {})
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


    def test_copy_and_deep_copy(self):
        def set_all_items(d):
            if isinstance(d, abc.MutableMapping):
                for k, v in list(d.items()): d[k] = set_all_items(v)
            elif isinstance(d, abc.MutableSequence):
                for i, v in enumerate(d): d[i] = set_all_items(v)
            else:
                d = "SENTINEL"
            return d

        def check_all_items(d):
            if isinstance(d, (str, bytes, bytearray)):
                self.assertNotEqual(d, "SENTINEL")
            elif isinstance(d, abc.Mapping):
                for v in d.values(): check_all_items(v)
            elif isinstance(d, abc.Collection):
                for v in d: check_all_items(v)
            else:
                self.assertNotEqual(d, "SENTINEL")

        with self.assertRaises(AssertionError):
            check_all_items("SENTINEL")
        with self.assertRaises(AssertionError):
            check_all_items(["SENTINEL"])
        with self.assertRaises(AssertionError):
            check_all_items({"SENTINEL"})
        with self.assertRaises(AssertionError):
            check_all_items(("SENTINEL",))
        with self.assertRaises(AssertionError):
            check_all_items({"SENTINEL":"SENTINEL"})

        a = Dict(
            foo=1,
            bar=2,
            baz=Dict(q=1,r=2,z=3),
            qux=Dict(q=Dict(a=1,b=2,c=3)),
            blar = [[1,2,3,[4,5,6,[7]]]],
            bulp = (1, 2, 3),
            fwen = {1, 2, 3},
        )

        b = copy.copy(a)
        assert a == b
        set_all_items(b)
        check_all_items(a)

        b = copy.deepcopy(a)
        assert a == b
        set_all_items(b)
        check_all_items(a)

        b = Dict(a)
        assert a == b
        set_all_items(b)
        check_all_items(a)

# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=999)
