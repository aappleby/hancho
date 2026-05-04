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
