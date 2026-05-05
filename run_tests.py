#!/usr/bin/env python3
import sys
import unittest

TEST_MODULES = [
    "tests.test_dict",
    "tests.test_templates",
    "tests.test_split",
    "tests.test_scratch",
]

def run_test_suite(mod_name):
    loader = unittest.TestLoader()
    runner = unittest.TextTestRunner(verbosity=2)
    suite  = loader.loadTestsFromName(mod_name)
    result = runner.run(suite)

    if not result.wasSuccessful():
        print(f"Test suite {mod_name} failed!")
        print(f"Result: {result}")
        sys.exit(1)

if __name__ == "__main__":
    for mod in TEST_MODULES:
        print(f"Running {mod}")
        run_test_suite(mod)
