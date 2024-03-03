#!/usr/bin/python3

import os
from os import path
import sys
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
  return os.path.getmtime(file)

def run(cmd):
  return subprocess.check_output(cmd, shell=True, text=True).strip()

class TestHancho(unittest.TestCase):

  def test_always_rebuild_if_no_inputs(self):
    os.system("rm -rf build")
    os.system("../hancho.py --quiet always_rebuild_if_no_inputs.hancho")
    old_mtime = mtime(f"build/always_rebuild_if_no_inputs/result.txt")
    os.system("../hancho.py --quiet always_rebuild_if_no_inputs.hancho")
    new_mtime = mtime(f"build/always_rebuild_if_no_inputs/result.txt")
    self.assertGreater(new_mtime, old_mtime)

  def test_build_dir_works(self):
    os.system("rm -rf build")
    os.system("../hancho.py --quiet build_dir_works.hancho")
    self.assertTrue(path.exists("build/build_dir_works/result.txt"))

  def test_check_output(self):
    os.system("rm -rf build")
    result = os.system("../hancho.py --quiet check_output.hancho")
    self.assertNotEqual(result, 0)

  def test_command_missing(self):
    os.system("rm -rf build")
    result = os.system("../hancho.py --quiet command_missing.hancho")
    self.assertNotEqual(result, 0)

  def test_dep_changed(self):
    os.system("rm -rf build")
    os.system("mkdir build")

    os.system("touch build/dummy.txt")
    os.system("../hancho.py --quiet dep_changed.hancho")
    mtime1 = mtime(f"build/dep_changed/result.txt")

    os.system("../hancho.py --quiet dep_changed.hancho")
    mtime2 = mtime(f"build/dep_changed/result.txt")
    self.assertEqual(mtime1, mtime2)

    os.system("touch build/dummy.txt")
    os.system("../hancho.py --quiet dep_changed.hancho")
    mtime3 = mtime(f"build/dep_changed/result.txt")
    self.assertLess(mtime2, mtime3)

  def test_does_create_output(self):
    os.system("rm -rf build")
    os.system("../hancho.py --quiet does_create_output.hancho")
    self.assertTrue(path.exists("build/does_create_output/result.txt"))

  def test_header_changed(self):
    os.system("rm -rf build")

    os.system("../hancho.py --quiet header_changed.hancho")
    old_mtime = mtime(f"build/header_changed/src/test.o")

    os.system("touch src/test.hpp")
    os.system("../hancho.py --quiet header_changed.hancho")
    new_mtime = mtime(f"build/header_changed/src/test.o")
    self.assertGreater(new_mtime, old_mtime)

################################################################################

if __name__ == '__main__':
    unittest.main()

################################################################################

"""
################################################################################

def prep_hancho(task):
  divider1()

def prep_wipe_outputs(task):
  for f in task.files_out:
    if path.exists(f):
      log(f"Removing {f}")
      os.remove(f)
  divider1()

def prep_timestamp_outputs(task):
  if not task.timestamps:
    task.timestamps = {}
  for f in task.files_out:
    task.timestamps[f] = path.getmtime(f)
  divider1()

def prep_touch_inputs(task):
  for f in task.files_out:
    os.system(f"touch {f}")

def check_outputs_created(task):
  missing = False
  for f in task.files_out:
    if not path.exists(f):
      missing = True
  log("^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^")
  return -1 if missing else 0

def check_hancho_passed(task):
  divider2()
  return task.returncode

def check_hancho_failed(task):
  divider2()
  return 0 if task.returncode else -1

def check_stdout_has(regex, task):
  divider2()
  found = re.search(regex, task.stdout)
  return 0 if found else -1

def check_timestamp_outputs(task):
  failed = False
  for f in task.files_out:
    old_timestamp = task.timestamps[f]
    new_timestamp = path.getmtime(f)
    if not new_timestamp > old_timestamp:
      log(f"Output {f} was not modified")
      failed = True
  divider2()
  return -1 if failed else 0

def hancho_should_pass(**kwargs):
  rule = Rule(
    desc = "{hanchofile} should pass",
    prep = prep_hancho,
    command = "hancho.py {hanchofile} --verbose",
    check = check_hancho_passed,
  )
  rule.extend(**kwargs)()

def hancho_should_fail(**kwargs):
   rule = Rule(
    desc = "{hanchofile} should fail",
    prep = prep_hancho,
    command = "hancho.py {hanchofile} --verbose",
    check = check_hancho_failed
   )
   rule.extend(**kwargs)()

#hancho_should_pass(
#  hanchofile = "should_pass.hancho"
#)
#
#hancho_should_fail(
#  hanchofile = "should_fail.hancho"
#)
#
#hancho_should_fail(
#  hanchofile = "command_missing.hancho",
#  check      = partial(check_stdout_has, "Command missing"),
#)
#
#hancho_should_pass(
#  hanchofile = "does_create_output.hancho",
#  files_out  = "build/output_created.txt",
#  prep       = prep_wipe_outputs,
#  check      = check_outputs_created,
#)
#
#hancho_should_fail(
#  hanchofile = "doesnt_create_output.hancho",
#  check      = partial(check_stdout_has, "still needs rerun"),
#)

#hancho_should_fail(
#  hanchofile = "garbage_command.hancho",
#  check      = partial(check_stdout_has, "not found"),
#)
#
#hancho_should_pass(
#  hanchofile = "build_dir_works.hancho",
#  files_out  = "build/build_dir_works.txt",
#  prep       = prep_wipe_outputs,
#  check      = check_outputs_created,
#)
#
#hancho_should_fail(
#  hanchofile = "missing_src.hancho",
#)
#
#hancho_should_fail(
#  hanchofile = "expand_failed_to_terminate.hancho",
#  check      = partial(check_stdout_has, "failed to terminate"),
#)
#
#hancho_should_fail(
#  hanchofile = "recursive_base_is_bad.hancho",
#  check      = partial(check_stdout_has, "is stuck in a loop"),
#)

#class TestCustomCommands(unittest.TestCase):
class TestCustomCommands(unittest.TestCase):
  #def setUp(self):
    #flags.silent = True
    #self.foo = 0

  async def custom_command(self, task):
    self.foo = 1

  #def test_fail(self):
  #  self.assertEqual(1, 0)
  #  pass

  def test_custom_command(self):
    pass
    #self.assertEqual(0, 0)
    #Rule(
    #  desc = "Test custom command",
    #  command = self.custom_command,
    #)()
    #self.assertEqual(self.foo, 0)
    #self.assertEqual(self.foo, 1)

# log(module.TestCustomCommands)
# suite = unittest.TestLoader().loadTestsFromTestCase(module.TestCustomCommands)
# log(suite)
# unittest.TextTestRunner().os.system(suite)

async def run_testcase(task):
  log("run_testcase")
  suite = unittest.TestLoader().loadTestsFromTestCase(task.test_case)
  for s in suite:
    result = unittest.TestResult()
    log(result)
    s(result)
    log(result)
  log("blah")
  log(len(result.failures))
  return len(result.failures)

testsuite = Rule(
  command = run_testcase
)

#testsuite(test_case = TestCustomCommands)

class TestRebuildTriggers(unittest.TestCase):

  def setUp(self):
    flags.silent = True

  ##########

  def test_always_rebuild_if_no_inputs(self):
    test_name = "always_rebuild_if_no_inputs"
    mtime0 = path.getmtime(f"build/{test_name}.txt")

    rule = Rule(
      #desc      = "Always rebuild build/{test_name}.txt if the rule has no files_in",
      command   = "touch {files_out}",
      test_name = test_name
    )

    time.sleep(0.01)
    rule(files_out = "build/{test_name}.txt")
    build()
    mtime1 = path.getmtime(f"build/{test_name}.txt")
    self.assertGreater(mtime1, mtime0)

    time.sleep(0.01)
    rule(files_out = "build/{test_name}.txt")
    mtime2 = path.getmtime(f"build/{test_name}.txt")
    self.assertGreater(mtime2, mtime1)

  ##########

  def test_always_rebuild_if_no_outputs(self):
    test_name = "always_rebuild_if_no_outputs"
    mtime0 = path.getmtime(f"build/{test_name}.txt")

    rule = Rule(
      #desc      = "Always rebuild build/{test_name}.txt if the rule has no files_out",
      command   = "touch build/{test_name}.txt",
      test_name = test_name
    )

    time.sleep(0.01)
    rule("{test_name}.hancho")
    mtime1 = path.getmtime(f"build/{test_name}.txt")
    self.assertGreater(mtime1, mtime0)

    time.sleep(0.01)
    rule("{test_name}.hancho")
    mtime2 = path.getmtime(f"build/{test_name}.txt")
    self.assertGreater(mtime2, mtime1)

#fails = 0
#fails += test_always_rebuild_if_no_inputs()
#fails += test_always_rebuild_if_no_outputs()
#
#if fails:
#  log(f"\x1B[31msome tests failed!\x1B[0m")
#else:
#  log(f"\x1B[32mall tests passed!\x1B[0m")


#sys.exit(0)

#hancho_should_pass(
#  hanchofile = "always_rebuild_if_no_inputs.hancho",
#  prep       = prep_timestamp_outputs,
#  files_out  = "build/always_rebuild_if_no_inputs.txt",
#  check      = check_timestamp_outputs,
#)

#hancho_should_pass(
#  hanchofile = "always_rebuild_if_no_outputs.hancho",
#  prep       = prep_timestamp_outputs,
#  files_out  = "build/always_rebuild_if_no_outputs.txt",
#  check      = check_timestamp_outputs,
#)

deps1 = Rule(
  desc = "deps1",
  command = "touch {files_out}",
  files_out = "build/blep",
)()

deps2 = Rule(
  desc = "deps2",
  command = "touch {files_in}.bar",
)(deps1)
"""
