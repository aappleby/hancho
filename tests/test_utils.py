import os
import sys
import unittest
from collections import abc
from typing import Any

import hancho

type branch[LEAF] = LEAF | abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]] | None
type tree[LEAF] = abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]

la = ["foo", "bar"]
lb = ["-", "+"]
lc = ["000", "111", "222"]

# --------------------------------------------------------------------------------------------------

class TestUtils(unittest.TestCase):
    def setUp(self) -> None:
        os.chdir(os.path.dirname(__file__))
        sys.stdout.flush()

    def tearDown(self) -> None:
        sys.stdout.flush()

    def test_trivial(self) -> None:
        self.assertIsNotNone(hancho)

    # ----------------------------------------
    # Yieldy stuff

    def test_yield_leaves(self) -> None:
        a = [1, 1, 1]
        b = list(hancho.Utils.leaves(a))
        self.assertEqual(a, b)

    # ----------------------------------------
    # Tree map stuff

    def test_tree_map_basic(self) -> None:
        def upperize(x : str) -> str:
            return x.upper()

        upperize_t = hancho.Utils.tree_map(upperize)

        def tree_equal(a : Any, b : Any) -> None:
            self.assertIs(type(a), type(b))
            if isinstance(a, list):
                self.assertEqual(len(a), len(b))
                for i in range(len(a)):
                    tree_equal(a[i], b[i])
            elif isinstance(a, dict):
                self.assertEqual(a.keys(), b.keys())
                for k in a:
                    tree_equal(a[k], b[k])
            else:
                self.assertEqual(a, b)

        tree_equal(upperize_t("foo"), "FOO")
        tree_equal(upperize_t(["foo", "bar"]), ['FOO', 'BAR'])
        tree_equal(upperize_t(["foo", "bar", ["baz"]]), ['FOO', 'BAR', ['BAZ']])
        tree_equal(upperize_t({"foo":"bar"}), {'foo': 'BAR'})

        thing1 = ["foo", "bar", ["baz", "qux"], {"a" : "blarp", "b" : "glarp", "c" : ["narp", "barp"]}]
        thing2 = ['FOO', 'BAR', ['BAZ', 'QUX'], {'a' : 'BLARP', 'b' : 'GLARP', 'c' : ['NARP', 'BARP']}]
        tree_equal(upperize_t(thing1), thing2)

        class MyDict(dict): pass
        class MyList(list): pass

        thing3 = MyList(["foo", "bar", MyList(["baz", "qux"]), MyDict(a = "blarp", b = "glarp", c = MyList(["narp", "barp"]))])
        thing4 = MyList(['FOO', 'BAR', MyList(['BAZ', 'QUX']), MyDict(a = 'BLARP', b = 'GLARP', c = MyList(['NARP', 'BARP']))])

        thing5 = upperize_t(thing3)

        tree_equal(thing4, thing5)

    # ----------------------------------------
    # Cross-joiny stuff

#    def test_cross_join(self):
#        ld = hancho.Utils.cross_join(la, lb, lc)
#        expected = [(a, b, c) for a in la for b in lb for c in lc]
#        self.assertEqual(ld, expected)
#
#    def test_yield_cross_join(self):
#        ld = list(hancho.Utils.yield_cross_join(la, lb, lc))
#        expected = [(a, b, c) for a in la for b in lb for c in lc]
#        self.assertEqual(ld, expected)
#
#    def test_cross_join_reduce(self):
#        # Cross join reduce should be a left-reduce
#        la : tree[int | str] = [1, 2, 3]
#        lb : tree[int | str]  = [4]
#        lc : tree[int | str]  = [5, 6, 7, 8]
#
#        def reduce(x : int | str, y : int | str) -> str: return f"({x},{y})"
#
#        ld = hancho.Utils.cross_join_reduce(reduce, la, lb, lc)
#        expected = [f"(({a},{b}),{c})" for a in la for b in lb for c in lc]
#        self.assertEqual(ld, expected)
#
#    def test_yield_cross_join_reduce(self):
#        # Cross join reduce should be a left-reduce
#        ra : tree[int | str] = [1, 2, 3]
#        rb : tree[int | str]  = [4, 5]
#        rc : tree[int | str]  = [6, 7, 8, 9]
#
#        def reduce(x : int | str, y : int | str) -> str: return f"({x},{y})"
#
#        rd : tree[int | str]  = list(hancho.Utils.yield_cross_join_reduce(reduce, ra, rb, rc))
#        expected = [f"(({a},{b}),{c})" for a in ra for b in rb for c in rc]
#        self.assertEqual(rd, expected)

    def test_weave(self) -> None:
        # Weave is just cross_join_reduce(lambda +...)
        ld = hancho.Utils.weave(la, lb, lc)
        expected = [a+b+c for a in la for b in lb for c in lc]
        self.assertEqual(ld, expected)
