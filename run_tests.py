#!/usr/bin/env python3
import sys
import unittest
import hancho

hancho.init(args = sys.argv[1:])


# tests still needed -
# calling hancho in src dir
# meta deps changed
# transitive dependencies
# dry run not creating files/dirs
# all the predefined directories need test cases

# min delta seems to be 4 msec on linux (wsl), 1 msec on windows?

#os.system("touch blahblah.txt")
#old_mtime = os.stat("blahblah.txt").st_mtime_ns
#print(old_mtime)
#min_delta = 100000000000
#for _ in range(10000):
#  #os.system("touch blahblah.txt")
#  os.utime("blahblah.txt", None)
#  new_mtime = os.stat("blahblah.txt").st_mtime_ns
#  delta = new_mtime - old_mtime
#  if delta and delta < min_delta:
#    print(delta)
#    min_delta = delta
#  old_mtime = new_mtime


TEST_MODULES = [
    "tests.test_dict",
    "tests.test_templates",
    "tests.test_split",
    "tests.test_scratch",
    "tests.test_tasks",
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
