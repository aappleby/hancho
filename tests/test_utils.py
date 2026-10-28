import os
import sys
import unittest
from collections import abc

import hancho

type branch[LEAF] = LEAF | abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]
type tree[LEAF] = abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]

la = ["foo", "bar"]
lb = ["-", "+"]
lc = ["000", "111", "222"]

# --------------------------------------------------------------------------------------------------

class TestUtils(unittest.TestCase):
    def setUp(self):
        os.chdir(os.path.dirname(__file__))
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    def test_trivial(self):
        self.assertIsNotNone(hancho)

    # ----------------------------------------
    # Yieldy stuff

    def test_yield_leaves(self):
        a = [1, 1, 1]
        b = list(hancho.Utils.yield_leaves(a))
        self.assertEqual(a, b)

    # ----------------------------------------
    # Cross-joiny stuff

    def test_cross_join(self):
        ld = hancho.Utils.cross_join(la, lb, lc)
        expected = [(a, b, c) for a in la for b in lb for c in lc]
        self.assertEqual(ld, expected)

    def test_yield_cross_join(self):
        ld = list(hancho.Utils.yield_cross_join(la, lb, lc))
        expected = [(a, b, c) for a in la for b in lb for c in lc]
        self.assertEqual(ld, expected)

    def test_cross_join_reduce(self):
        # Cross join reduce should be a left-reduce
        la : tree[int | str] = [1, 2, 3]
        lb : tree[int | str]  = [4]
        lc : tree[int | str]  = [5, 6, 7, 8]

        def reduce(x : int | str, y : int | str) -> str: return f"({x},{y})"

        ld = hancho.Utils.cross_join_reduce(reduce, la, lb, lc)
        expected = [f"(({a},{b}),{c})" for a in la for b in lb for c in lc]
        self.assertEqual(ld, expected)

    def test_yield_cross_join_reduce(self):
        # Cross join reduce should be a left-reduce
        ra : tree[int | str] = [1, 2, 3]
        rb : tree[int | str]  = [4, 5]
        rc : tree[int | str]  = [6, 7, 8, 9]

        def reduce(x : int | str, y : int | str) -> str: return f"({x},{y})"

        rd : tree[int | str]  = list(hancho.Utils.yield_cross_join_reduce(reduce, ra, rb, rc))
        expected = [f"(({a},{b}),{c})" for a in ra for b in rb for c in rc]
        self.assertEqual(rd, expected)

    def test_weave(self):
        # Weave is just cross_join_reduce(lambda +...)
        ld = hancho.Utils.weave(la, lb, lc)
        expected = [a+b+c for a in la for b in lb for c in lc]
        self.assertEqual(ld, expected)

    # ----------------------------------------

# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=999)
