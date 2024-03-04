#!/usr/bin/python3

import os
from os import path
import subprocess
import unittest

# min delta seems to be 4 msec
#os.system("touch blahblah.txt")
#old_mtime = path.getmtime("blahblah.txt")
#min_delta = 1000000
#for _ in range(1000):
#  os.system("touch blahblah.txt")
#  new_mtime = path.getmtime("blahblah.txt")
#  delta = new_mtime - old_mtime
#  if delta and delta < min_delta:
#    log(str(delta))
#    min_delta = delta
#  old_mtime = new_mtime

def mtime(file):
  return path.getmtime(file)

def run(cmd):
  return subprocess.check_output(cmd, shell=True, text=True).strip()

def run_hancho(name):
  return os.system(f"../hancho.py --quiet {name}.hancho")

def touch(name):
  os.system(f"touch {name}")

################################################################################

class TestHancho(unittest.TestCase):

  def setUp(self):
    os.system("rm -rf build")
    os.system("mkdir build")

  def test_should_pass(self):
    self.assertEqual(0, run_hancho("should_pass"))

  def test_check_output(self):
    self.assertNotEqual(0, run_hancho("check_output"))

  def test_check_missing_src(self):
    self.assertNotEqual(0, run_hancho("missing_src"))

  def test_recursive_base_is_bad(self):
    self.assertNotEqual(0, run_hancho("recursive_base_is_bad"))

  def test_should_fail(self):
    self.assertNotEqual(0, run_hancho("should_fail"))

  def test_command_missing(self):
    self.assertNotEqual(0, run_hancho("command_missing"))

  def test_expand_failed_to_terminate(self):
    self.assertNotEqual(0, run_hancho("expand_failed_to_terminate"))

  def test_garbage_command(self):
    self.assertNotEqual(0, run_hancho("garbage_command"))

  def test_always_rebuild_if_no_inputs(self):
    run_hancho("always_rebuild_if_no_inputs")
    mtime1 = mtime(f"build/result.txt")

    run_hancho("always_rebuild_if_no_inputs")
    mtime2 = mtime(f"build/result.txt")

    run_hancho("always_rebuild_if_no_inputs")
    mtime3 = mtime(f"build/result.txt")
    self.assertLess(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  def test_build_dir_works(self):
    run_hancho("build_dir_works")
    self.assertTrue(path.exists("build/build_dir_works/result.txt"))

  def test_dep_changed(self):
    touch("build/dummy.txt")
    run_hancho("dep_changed")
    mtime1 = mtime(f"build/result.txt")

    run_hancho("dep_changed")
    mtime2 = mtime(f"build/result.txt")

    touch("build/dummy.txt")
    run_hancho("dep_changed")
    mtime3 = mtime(f"build/result.txt")
    self.assertEqual(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  def test_does_create_output(self):
    run_hancho("does_create_output")
    self.assertTrue(path.exists("build/result.txt"))

  def test_doesnt_create_output(self):
    run_hancho("doesnt_create_output")
    self.assertFalse(path.exists("build/result.txt"))

  def test_header_changed(self):
    run_hancho("header_changed")
    mtime1 = mtime(f"build/src/test.o")

    run_hancho("header_changed")
    mtime2 = mtime(f"build/src/test.o")

    os.system("touch src/test.hpp")
    run_hancho("header_changed")
    mtime3 = mtime(f"build/src/test.o")
    self.assertEqual(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  def test_input_changed(self):
    run_hancho("input_changed")
    mtime1 = mtime(f"build/src/test.o")

    run_hancho("input_changed")
    mtime2 = mtime(f"build/src/test.o")

    os.system("touch src/test.cpp")
    run_hancho("input_changed")
    mtime3 = mtime(f"build/src/test.o")
    self.assertEqual(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  def test_multiple_commands(self):
    run_hancho("multiple_commands")
    self.assertTrue(path.exists("build/foo.txt"))
    self.assertTrue(path.exists("build/bar.txt"))
    self.assertTrue(path.exists("build/baz.txt"))

  def test_arbitrary_flags(self):
    os.system(f"../hancho.py --build_dir=build/some/other/dir --quiet does_create_output.hancho")
    self.assertTrue(path.exists("build/some/other/dir/result.txt"))

################################################################################

if __name__ == '__main__':
    unittest.main()
