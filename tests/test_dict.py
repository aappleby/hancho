import os
import unittest

import hancho

# FIXME test fill()
# FIXME add public/private tests

# --------------------------------------------------------------------------------------------------

class TestDict(unittest.TestCase):
    def setUp(self, *args):
        os.chdir(os.path.dirname(__file__))

    def test_basic_access(self):
        d = hancho.Dict({"a": 1, "b": 2})
        self.assertEqual(d.a, 1)
        self.assertEqual(d["b"], 2)

        d = hancho.Dict({"a": 1}, {"b": 2})
        self.assertEqual(d.a, 1)
        self.assertEqual(d["b"], 2)

        d = hancho.Dict(a = 1, b = 2)
        self.assertEqual(d.a, 1)
        self.assertEqual(d["b"], 2)

        with self.assertRaises(AttributeError):
            _ = d.missing
        with self.assertRaises(KeyError):
            _ = d["missing"]

    def test_merge_rightmost_wins(self):
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 3, "c": 4}
        merged = hancho.Dict(d1, d2)
        self.assertEqual(merged.a, 1)
        self.assertEqual(merged.b, 3)
        self.assertEqual(merged.c, 4)

    def test_recursive_merge(self):
        d1 = {"a": {"x": 1, "y": 2}}
        d2 = {"a": {"y": 3, "z": 4}}

        d3 = {}
        hancho.Utils.merge_variant(d3, d1) # type: ignore
        hancho.Utils.merge_variant(d3, d2) # type: ignore

        self.assertIsInstance(d3, dict)
        self.assertIsInstance(d3['a'], dict)

        self.assertEqual(d3['a']['x'], 1)
        self.assertEqual(d3['a']['y'], 3)
        self.assertEqual(d3['a']['z'], 4)

    def test_basic_merging(self):
        # Basic merging should work
        r = hancho.Dict()
        self.assertEqual(0, len(r))
        r = hancho.Dict({}, {}, {})
        self.assertEqual(0, len(r))
        r = hancho.Dict({}, {"bar": None})
        self.assertEqual(1, len(r))
        self.assertEqual(None, r.bar)

        r = hancho.Dict({}, {"bar" : 3})
        self.assertEqual(1, len(r))
        self.assertEqual(3, r.bar)

    def test_right_overrides_left(self):
        # Right side should always override left side if right val is not None
        a = hancho.Dict({"bar": None}, {"bar": 3})
        self.assertEqual(3, a.bar)
        b = hancho.Dict({"bar": 2}, {"bar": 3})
        self.assertEqual(3, b.bar)
        c = hancho.Dict({"bar": 4}, {"bar": None})
        self.assertEqual(4, c.bar)

    def test_none_doesnt_override(self):
        # Right side should _not_ override left side if its val is None
        r = hancho.Dict({"bar": 2}, {"bar": None})
        self.assertEqual(2, r.bar)
        r = hancho.Dict({'a': 1}, a = None)
        self.assertEqual(1, r.a)
        r = sorted(hancho.Dict({'a': 1}, b = 2, c = 3).items())
        self.assertEqual([('a', 1), ('b', 2), ('c', 3)], r)

    def test_empty_dict_doesnt_override(self):
        # Empty right side should not clobber left side
        d = hancho.Dict({"bar": 2}, {})
        self.assertEqual(1, len(d))
        self.assertEqual(2, d.bar)

# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=999)
