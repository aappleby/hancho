#!/usr/bin/env python3
import sys
import unittest
import doctest
import hancho

TEST_MODULES = [
    "tests.test_dict",
    "tests.test_templates",
]

def run_file_doctests(filename):
    suite = doctest.DocFileSuite(filename, optionflags=doctest.NORMALIZE_WHITESPACE | doctest.ELLIPSIS)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        print(f"Doctests in {filename} failed!")
        print(f"Result: {result}")
        sys.exit(1)

def run_module_doctests(module):
    suite = unittest.TestSuite()
    suite.addTests(doctest.DocTestSuite(module, optionflags=doctest.NORMALIZE_WHITESPACE | doctest.ELLIPSIS))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        print(f"{module.__name__} doctests failed!")
        print(f"Result: {result}")
        sys.exit(1)

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

    print("Running hancho module doctests")
    run_module_doctests(hancho)

    print("Running external doctests")
    run_file_doctests("tests/doctest_all.txt")

    #run_hancho_doctests()
    for mod in TEST_MODULES:
        print(f"Running {mod}")
        run_test_suite(mod)
