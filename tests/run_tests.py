#!/usr/bin/python3
"""Test cases for Hancho"""

import sys
import os
from os import path
import random
import subprocess
import unittest
import shutil
import glob
from pathlib import Path
import time

sys.path.append("..")
import hancho

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

####################################################################################################

def mtime_ns(filename):
  return os.stat(filename).st_mtime_ns

def force_touch(filename):
  if not Path(filename).exists():
    Path(filename).touch()
  old_mtime = mtime_ns(filename)
  while old_mtime == mtime_ns(filename):
    os.utime(filename, None)

####################################################################################################

def color(red=None, green=None, blue=None):
  """Converts RGB color to ANSI format string."""
  # Color strings don't work in Windows console, so don't emit them.
  if os.name == "nt":
    return ""
  if red is None:
    return "\x1B[0m"
  return f"\x1B[38;2;{red};{green};{blue}m"

####################################################################################################

class TestConfig(unittest.TestCase):
  """Test cases for weird things our Config objects can do"""

  def setUp(self):
    #print(f"Running {type(self).__name__}::{self._testMethodName}")
    #sys.stdout.flush()
    pass

  def test_nothing(self):
    pass

####################################################################################################

# pylint: disable=too-many-public-methods
class TestHancho(unittest.TestCase):
  """Basic test cases"""

  def setUp(self):
    print(f"{color(255, 255, 0)}Running {type(self).__name__}::{self._testMethodName}{color()}")
    sys.stdout.flush()

    # Always wipe the build dir before a test
    shutil.rmtree("build", ignore_errors=True)
    hancho.app.reset()
    hancho.app.quiet = True

  ########################################

  def tearDown(self):
    """And wipe the build dir after a test too."""
    shutil.rmtree("build", ignore_errors=True)

  ########################################

  def create_ctx(self, flags, extra_flags):
    #argv = commandline.split()

    default_flags = hancho.Config(
      shuffle   = False,
      use_color = True,
      quiet     = False,
      dry_run   = False,
      jobs      = os.cpu_count(),
      target    = None,
      verbose   = False,
      debug     = False,
      force     = False,
      trace     = False,
      root_file = 'build.hancho',
      root_dir  = os.getcwd(),
    )

    default_extra_flags = hancho.Config()

    default_flags.merge(flags)
    default_extra_flags.merge(extra_flags)

    ctx = hancho.app.create_root_context(default_flags.__dict__, default_extra_flags.__dict__)
    return ctx

  ########################################

  def test_dummy(self):
    self.assertEqual(0, 0)

  ########################################

  def test_should_pass(self):
    """Sanity check"""
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(command = "(exit 0)")
    self.assertEqual(0, hancho.app.build_all())

  ########################################

  def test_should_fail(self):
    """Sanity check"""
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(command = "echo skldjlksdlfj && (exit 255)")
    self.assertNotEqual(0, hancho.app.build_all())

  ########################################

#  def test_subrepos1(self):
#      """Outputs from a subrepo should go in build/repo_name/..."""
#      repo = hancho.repo("subrepo")
#      task = repo.task(
#          command = "cat {rel_source_files} > {rel_build_files}",
#          source_files = "stuff.txt",
#          build_files = "repo.txt",
#          b*ase_path = os.path.abspath("subrepo")
#      )
#      self.assertEqual(0, hancho.app.build_all())
#      self.assertTrue(Path("build/subrepo/repo.txt").exists())

  ########################################

#    def test_subrepos1(self):
#        shutil.rmtree("subrepo_tests/build", ignore_errors=True)
#        result = subprocess.run(
#            f"python3 ../../hancho.py -v -d top_test1.hancho",
#            shell=True,
#            text=True,
#            capture_output=True,
#            cwd="subrepo_tests",
#        )
#        self.assertTrue(Path("subrepo_tests/build/submodule_tests/top.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo1/repo1.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo2/repo2.txt").exists())

  ########################################

#    def test_subrepos2(self):
#        shutil.rmtree("subrepo_tests/build", ignore_errors=True)
#        result = subprocess.run(
#            f"python3 ../../hancho.py -v -d top_test2.hancho",
#            shell=True,
#            text=True,
#            capture_output=True,
#            cwd="subrepo_tests",
#        )
#        self.assertTrue(Path("subrepo_tests/build/submodule_tests/top.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo1/repo1.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo2/repo2.txt").exists())

  ########################################

#    def test_subrepos3(self):
#        shutil.rmtree("subrepo_tests/build", ignore_errors=True)
#        result = subprocess.run(
#            f"python3 ../../hancho.py -v -d top_test3.hancho",
#            shell=True,
#            text=True,
#            capture_output=True,
#            cwd="subrepo_tests",
#        )
#        self.assertTrue(Path("subrepo_tests/build/submodule_tests/top.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo1/repo1.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo2/repo2.txt").exists())

  ########################################

  def test_good_build_path(self):
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(
      command  = "touch {rel(out_obj)}",
      in_src   = "src/foo.c",
      out_obj  = "{repo_dir}/build/narp/foo.o",
    )
    self.assertEqual(0, hancho.app.build_all())
    self.assertTrue(Path("build/narp/foo.o").exists())

  ########################################

  def test_bad_build_path(self):
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(
      command  = "touch {rel(out_obj)}",
      in_src   = "src/foo.c",
      out_obj  = "{repo_dir}/../build/foo.o",
    )
    self.assertNotEqual(0, hancho.app.build_all())
    self.assertFalse(Path("build/foo.o").exists())
    self.assertTrue("Path error" in hancho.app.log)

  ########################################

  def test_raw_task(self):
    ctx = self.create_ctx({'quiet':True}, {})
    #ctx = self.create_ctx("-d")
    task = ctx.Task(
      command   = "touch {rel(out_obj)}",
      in_src    = "src/foo.c",
      out_obj   = "foo.o",
      task_dir  = os.getcwd(),
      build_dir = "build"
    )
    #print(task)
    self.assertEqual(0, hancho.app.build_all())
    self.assertTrue(Path("build/foo.o").exists())

  ########################################

  def test_missing_input(self):
    """We should fail if an input is missing"""
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(
      command = "touch {rel(out_obj)}",
      in_src  = "src/does_not_exist.txt",
      out_obj = "missing_src.txt"
    )
    self.assertNotEqual(0, hancho.app.build_all())
    self.assertTrue("FileNotFoundError" in hancho.app.log)
    self.assertTrue("does_not_exist.txt" in hancho.app.log)

  ########################################

  def test_missing_dep(self):
    """Missing dep should fail"""
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(
      command = "touch {rel(out_obj)}",
      in_src  = "src/test.cpp",
      in_dep  = ["missing_dep.txt"],
      out_obj = "result.txt",
    )
    self.assertNotEqual(0, hancho.app.build_all())
    self.assertTrue("FileNotFoundError" in hancho.app.log)
    self.assertTrue("missing_dep.txt" in hancho.app.log)

  ########################################

  def test_expand_failed_to_terminate(self):
    """A recursive text template should cause an 'expand failed to terminate' error."""
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(
      command = "{flarp}",
      in_src  = [],
      out_obj = [],
      flarp   = "asdf {flarp}",
      #verbose = True
    )
    self.assertNotEqual(0, hancho.app.build_all())
    #print(hancho.app.log)
    self.assertTrue("Text expansion failed to terminate" in hancho.app.log)

  ########################################

  def test_garbage_command(self):
    """Non-existent command line commands should cause Hancho to fail the build."""
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(
      command = "aklsjdflksjdlfkjldfk",
      in_src  = __file__,
      out_obj = "result.txt",
    )
    self.assertNotEqual(0, hancho.app.build_all())
    self.assertTrue("ValueError: 127" in hancho.app.log)

  ########################################

  def test_rule_collision(self):
    """If multiple rules generate the same output file, that's an error."""
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(
      command = "sleep 0.1 && touch {rel(out_obj)}",
      in_src  = __file__,
      out_obj = "colliding_output.txt",
    )
    ctx(
      command = "touch {rel(out_obj)}",
      in_src  = __file__,
      out_obj = "colliding_output.txt",
    )
    self.assertNotEqual(0, hancho.app.build_all())
    self.assertTrue("Multiple rules build" in hancho.app.log)

  ########################################

  def test_always_rebuild_if_no_inputs(self):
    """A rule with no inputs should always rebuild"""
    ctx = self.create_ctx({'quiet':True}, {})
    def run():
      hancho.app.reset()
      hancho.app.quiet = True
      ctx(
        command = "sleep 0.1 && touch {rel(out_obj)}",
        in_src  = [],
        out_obj = "result.txt",
      )
      self.assertEqual(0, hancho.app.build_all())
      return mtime_ns("build/result.txt")

    mtime1 = run()
    mtime2 = run()
    mtime3 = run()
    self.assertLess(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  ########################################

  def test_dep_changed(self):
    """Changing a file in deps[] should trigger a rebuild"""
    ctx = self.create_ctx({'quiet':True}, {})
    # This test is flaky without the "sleep 0.1" because of filesystem mtime granularity
    def run():
      hancho.app.reset()
      hancho.app.quiet = True
      ctx(
        command = "sleep 0.1 && touch {rel(out_obj)}",
        in_temp = ["build/dummy.txt"],
        in_src  = "src/test.cpp",
        out_obj = "result.txt",
      )
      self.assertEqual(0, hancho.app.build_all())
      return mtime_ns("build/result.txt")

    os.makedirs("build", exist_ok=True)
    force_touch("build/dummy.txt")
    mtime1 = run()
    mtime2 = run()
    force_touch("build/dummy.txt")
    mtime3 = run()
    self.assertEqual(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  ########################################

  def test_does_create_output(self):
    """Output files should appear in build/ by default"""
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(
      command = "touch {rel(out_obj)}",
      in_src  = [],
      out_obj = "result.txt",
    )
    self.assertEqual(0, hancho.app.build_all())
    self.assertTrue(path.exists("build/result.txt"))

  ########################################

  def test_doesnt_create_output(self):
    """Having a file mentioned in out_obj should not magically create it"""
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(
      command = "echo",
      in_src  = [],
      out_obj = "result.txt"
    )
    self.assertEqual(0, hancho.app.build_all())
    self.assertFalse(path.exists("build/result.txt"))

  ########################################

  def test_header_changed(self):
    """Changing a header file tracked in the GCC dependencies file should trigger a rebuild"""
    ctx = self.create_ctx({'quiet':True}, {})
    def run():
      hancho.app.reset()
      hancho.app.quiet = True
      time.sleep(0.01)
      compile = ctx.Command(
        command = "gcc -MMD -c {rel(in_src)} -o {rel(out_obj)}",
        out_obj = "{swap_ext(in_src, '.o')}",
        c_deps  = "{swap_ext(in_src, '.d')}",
      )
      ctx(compile, in_src = "src/test.cpp")
      self.assertEqual(0, hancho.app.build_all())
      return mtime_ns("build/src/test.o")

    mtime1 = run()
    mtime2 = run()
    force_touch("src/test.hpp")
    mtime3 = run()

    self.assertEqual(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  ########################################

  def test_input_changed(self):
    """Changing a source file should trigger a rebuild"""
    ctx = self.create_ctx({'quiet':True}, {})
    def run():
      hancho.app.reset()
      hancho.app.quiet = True
      time.sleep(0.01)
      compile = ctx.Command(
        command = "gcc -MMD -c {rel(in_src)} -o {rel(out_obj)}",
        out_obj = "{swap_ext(in_src, '.o')}",
        c_deps  = "{swap_ext(in_src, '.d')}",
      )
      ctx(compile, in_src = "src/test.cpp")
      self.assertEqual(0, hancho.app.build_all())
      return mtime_ns("build/src/test.o")

    mtime1 = run()
    mtime2 = run()
    force_touch("src/test.cpp")
    mtime3 = run()

    self.assertEqual(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  ########################################

  def test_multiple_commands(self):
    """Rules with arrays of commands should run all of them"""
    ctx = self.create_ctx({'quiet':True}, {})
    ctx(
      command = [
        "echo foo > {rel(out_foo)}",
        "echo bar > {rel(out_bar)}",
        "echo baz > {rel(out_baz)}",
      ],
      in_src  = __file__,
      out_foo = "foo.txt",
      out_bar = "bar.txt",
      out_baz = "baz.txt",
    )

    self.assertEqual(0, hancho.app.build_all())
    self.assertTrue(path.exists("build/foo.txt"))
    self.assertTrue(path.exists("build/bar.txt"))
    self.assertTrue(path.exists("build/baz.txt"))

  ########################################

  def test_arbitrary_flags(self):
    """Passing arbitrary flags to Hancho should work"""
    ctx = self.create_ctx({'quiet':True}, {'flarpy':'flarp.txt'})
    self.assertEqual("flarp.txt", ctx['flarpy'])

    ctx(
      command = "touch {out_file}",
      source_files = [],
      out_file = ctx['flarpy'],
    )
    self.assertEqual(0, hancho.app.build_all())
    self.assertTrue(path.exists("build/flarp.txt"))

  ########################################

  #def test_what_is_in_a_task(self):
  #  task = hancho.Task(
  #    command = "",
  #    task_dir = "",
  #    build_dir = ""
  #  )
  #  print(task)

  ########################################

  def test_sync_command(self):
    """The 'command' field of rules should be OK handling a sync function"""
    ctx = self.create_ctx({'quiet':True}, {})

    def sync_command(task):
      force_touch(task.out_obj)

    ctx(
      command = sync_command,
      in_src  = [],
      out_obj = "result.txt",
    )
    self.assertEqual(0, hancho.app.build_all())
    self.assertTrue(path.exists("build/result.txt"))

  ########################################

  def test_cancellation(self):
    """A task that receives a cancellation exception should not run."""
    ctx = self.create_ctx({'quiet':True}, {})
    task_that_fails = ctx(
      command = "(exit 255)",
      in_src  = [],
      out_obj = "fail_result.txt",
    )
    task_that_passes = ctx(
      command = "touch {rel(out_obj)}",
      in_src  = [],
      out_obj = "pass_result.txt",
    )
    should_be_cancelled = ctx(
      command = "touch {rel(out_obj)}",
      in_src  = [task_that_fails, task_that_passes],
      out_obj = "should_not_be_created.txt",
    )
    self.assertNotEqual(0, hancho.app.build_all())
    self.assertTrue(Path("build/pass_result.txt").exists())
    self.assertFalse(Path("build/fail_result.txt").exists())
    self.assertFalse(Path("build/should_not_be_created.txt").exists())

  ########################################

  def test_task_creates_task(self):
    """Tasks using callbacks can create new tasks when they run."""
    ctx = self.create_ctx({'quiet':True}, {})
    def callback(task):
      new_task = ctx(
        command = "touch {rel(out_obj)}",
        in_src  = [],
        out_obj = "dummy.txt"
      )
      # FIXME these should auto-queue
      new_task.queue()
      return []

    ctx(
      command = callback,
      in_src  = [],
      out_obj = []
    )

    self.assertEqual(0, hancho.app.build_all())
    self.assertTrue(Path("build/dummy.txt").exists())

  ########################################

  def test_tons_of_tasks(self):
    """We should be able to queue up 1000+ tasks at once."""
    ctx = self.create_ctx({'quiet':True}, {})
    for i in range(1000):
      ctx(
        desc    = "I am task {index}",
        command = "echo {index} > {rel(out_obj)}",
        in_src  = [],
        out_obj = "dummy{index}.txt",
        index   = i
      )
    self.assertEqual(0, hancho.app.build_all())
    self.assertEqual(1000, len(glob.glob("build/*")))

  ########################################

  def test_job_count(self):
    """We should be able to dispatch tasks that require various numbers of jobs/cores."""
    # Queues up 100 tasks that use random numbers of cores, then a "Job Hog" that uses all cores, then
    # another batch of 100 tasks that use random numbers of cores.
    ctx = self.create_ctx({'quiet':True}, {})

    for i in range(100):
      ctx(
        desc    = "I am task {index}, I use {job_count} cores",
        command = "(exit 0)",
        in_src  = [],
        out_obj = [],
        job_count = random.randrange(1, os.cpu_count() + 1),
        index = i
      )

    ctx(
      desc = "********** I am the slow task, I eat all the cores **********",
      command = [
        "touch {rel(out_obj)}",
        "sleep 0.3",
      ],
      job_count = os.cpu_count(),
      in_src  = [],
      out_obj = "slow_result.txt",
    )

    for i in range(100):
      ctx(
        desc = "I am task {index}, I use {job_count} cores",
        command = "(exit 0)",
        in_src  = [],
        out_obj = [],
        job_count = random.randrange(1, os.cpu_count() + 1),
        index = 100 + i
      )

    self.assertEqual(0, hancho.app.build_all())
    self.assertTrue(Path("build/slow_result.txt").exists())

####################################################################################################

if __name__ == "__main__":
  unittest.main(verbosity=0)
