#!/usr/bin/env python3
import sys
import unittest
import hancho
import shutil

shutil.rmtree("build", ignore_errors=True)

#hancho.init()

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
    "tests.test_tasks",

    #"tests.test_hancho_as_import",
    #"tests.test_scratch",
]

if __name__ == "__main__":
    for mod_name in TEST_MODULES:
        #print(f"Running {mod_name}")
        loader = unittest.TestLoader()
        runner = unittest.TextTestRunner(verbosity=2)
        #suite = loader.discover(start_dir="tests", pattern="test_tasks.py")
        suite  = loader.loadTestsFromName(mod_name)
        runner.run(suite)
