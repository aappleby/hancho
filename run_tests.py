#!/usr/bin/env python3
import sys
import unittest
import doctest
import hancho

def run_hancho_doctests():
    suite = unittest.TestSuite()
    suite.addTests(doctest.DocTestSuite(hancho))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        print(f"Hancho doctests failed!")
        print(f"Result: {result}")
        sys.exit(1)


TEST_MODULES = [
    "tests.test_dict",
    "tests.test_templates",
]

def run_test_suite(mod_name):
    loader = unittest.TestLoader()
    runner = unittest.TextTestRunner(verbosity=2)
    suite = loader.loadTestsFromName(mod_name)
    result = runner.run(suite)
    if not result.wasSuccessful():
        print(f"Test suite {mod_name} failed!")
        print(f"Result: {result}")
        sys.exit(1)

if __name__ == "__main__":
    #run_hancho_doctests()
    for mod in TEST_MODULES:
        print(f"Running {mod}")
        run_test_suite(mod)
