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

    def test_dict_doctest(self):
        self.run_doctest("""
        # Basic merging should work
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

        # Right side should always override left side if right val is not None
            >>> Dict(dict(bar = None), dict(bar = 3))
            Dict @ ... { bar = 3 }
            >>> Dict(dict(bar = 2), dict(bar = 3))
            Dict @ ... { bar = 3 }

        # Right side should _not_ override left side if its val is None
            >>> Dict(dict(bar = 2), dict(bar = None))
            Dict @ ... { bar = 2 }
            >>> Dict({'a': 1}, a = None)
            Dict @ ... { a = 1 }

        >>> Dict({'a': 1}, b = 2, c = 3)
        Dict @ ... { a = 1, b = 2, c = 3 }

        # Empty right side should not clobber left side
            >>> Dict(dict(bar = 2), dict())
            Dict @ ... { bar = 2 }

        # Both dict['foo'] and dict.foo should work
            >>> d = Dict({'a': 1, 'b': 2})
            >>> (d.a, d['b'])
            (1, 2)

        # Internal dicts should be upgraded to hancho.Dict
            >>> d = Dict(a = {'b' : {'c' : 1}})
            >>> type(d)
            <class 'hancho.Dict'>
            >>> type(d.a)
            <class 'hancho.Dict'>
            >>> type(d.a.b)
            <class 'hancho.Dict'>
            >>> type(d.a.b.c)
            <class 'int'>

        # hancho.Dicts should be (as) immutable (as possible)
            >>> d = Dict(a = 1)
            >>> d.a = 2
            Traceback (most recent call last):
            ...
            TypeError: ('Hancho.Dict is immutable', 'a', 2)

            >>> d['a'] = 2
            Traceback (most recent call last):
            ...
            TypeError: ('Hancho.Dict is immutable', 'a', 2)
        """)


    def run_doctest(self, docstring):
        parser = doctest.DocTestParser()
        test = parser.get_doctest(
            docstring,
            globs=globals(),
            name=self._testMethodName,
            filename=__file__,
            lineno=0,
        )
        flags = doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE
        runner = doctest.DocTestRunner(optionflags=flags)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            runner.run(test)
        self.assertEqual(
            runner.failures, 0,
            f"{runner.failures} doctest failure(s):\n{buf.getvalue()}"
        )

####################################################################################################

if __name__ == "__main__":
    unittest.main()
