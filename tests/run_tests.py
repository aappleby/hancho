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
#from hancho import Config
#from hancho import app

# tests still needed -
# calling hancho in src dir
# meta deps changed
# transitive dependencies
# dry run not creating files/dirs
# loading a module directly and then via "../foo.hancho" should not load two
# copies
# all the predefined directories need test cases
# overriding in_dir/out_dir/work_dir need test cases

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

#sys.exit(0)


def mtime_ns(filename):
  return os.stat(filename).st_mtime_ns

def force_touch(filename):
  if not Path(filename).exists():
    Path(filename).touch()
  old_mtime = mtime_ns(filename)
  while old_mtime == mtime_ns(filename):
    os.utime(filename, None)

def run(cmd):
  """Runs a command line and returns its stdout with whitespace stripped"""
  return subprocess.check_output(cmd, shell=True, text=True).strip()


def run_hancho(name):
  """Runs a Hancho build script and returns a subprocess.CompletedProcess."""
  return subprocess.run(
    f"python3 ../hancho.py -v -d {name}.hancho",
    shell=True,
    text=True,
    capture_output=True,
  )


################################################################################

class TestConfig(unittest.TestCase):
  """Test cases for weird things our Config objects can do"""

  def setUp(self):
    print(f"Running {type(self).__name__}::{self._testMethodName}")
    sys.stdout.flush()


################################################################################

#hancho = Config()
#hancho.module = app.modstack[-1]
hancho.Config.use_color = False
hancho.Config.quiet = True
#hancho.Config.debug = True
#hancho.Config.verbose = True

def color(red=None, green=None, blue=None):
  """Converts RGB color to ANSI format string."""
  # Color strings don't work in Windows console, so don't emit them.
  if os.name == "nt":
    return ""
  if red is None:
    return "\x1B[0m"
  return f"\x1B[38;2;{red};{green};{blue}m"

################################################################################

# pylint: disable=too-many-public-methods
class TestHancho(unittest.TestCase):
  """Basic test cases"""

  def setUp(self):
    """Always wipe the build dir before a test"""
    #print(f"{color(255, 255, 0)}Running {type(self).__name__}::{self._testMethodName}{color()}")
    shutil.rmtree("build", ignore_errors=True)
    sys.stdout.flush()
    hancho.reset()

  def tearDown(self):
    """And wipe the build dir after a test too."""
    shutil.rmtree("build", ignore_errors=True)

  def test_dummy(self):
    self.assertEqual(0, 0)

  def test_should_pass(self):
    """Sanity check"""
    hancho.Task(command = "(exit 0)")
    self.assertEqual(0, hancho.build_all())

  def test_should_fail(self):
    """Sanity check"""
    hancho.Task(command = "echo skldjlksdlfj && (exit 255)")
    self.assertNotEqual(0, hancho.build_all())


  #def test_subrepos1(self):
  #    """Outputs from a subrepo should go in build/repo_name/..."""
  #    repo = hancho.repo("subrepo")
  #    task = repo.task(
  #        command = "cat {rel_source_files} > {rel_build_files}",
  #        source_files = "stuff.txt",
  #        build_files = "repo.txt",
  #        b*ase_path = os.path.abspath("subrepo")
  #    )
  #    self.assertEqual(0, hancho.build_all())
  #    self.assertTrue(Path("build/subrepo/repo.txt").exists())


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
#
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

  def test_good_build_path(self):
    hancho.Task(
      command  = "touch {rel(out_obj)}",
      in_src   = "src/foo.c",
      out_path = "{repo_path}/build/narp",
      out_obj  = "foo.o",
    )
    self.assertEqual(0, hancho.build_all())
    self.assertTrue(Path("build/narp/foo.o").exists())

  def test_bad_build_path(self):
    hancho.Task(
      command  = "touch {rel(out_obj)}",
      in_src   = "src/foo.c",
      out_path = "{repo_path}/../build",
      out_obj  = "foo.o",
    )
    self.assertNotEqual(0, hancho.build_all())
    self.assertFalse(Path("build/foo.o").exists())
    self.assertTrue("Path error" in hancho.get_log())

  def test_missing_input(self):
    """We should fail if an input is missing"""
    hancho.Task(
      command = "touch {rel(out_obj)}",
      in_src  = "src/does_not_exist.txt",
      out_obj = "missing_src.txt"
    )
    self.assertNotEqual(0, hancho.build_all())
    self.assertTrue("FileNotFoundError" in hancho.get_log())
    self.assertTrue("does_not_exist.txt" in hancho.get_log())

  def test_missing_dep(self):
    """Missing dep should fail"""
    hancho.Task(
      command = "touch {rel(out_obj)}",
      in_src  = "src/test.cpp",
      in_dep  = ["missing_dep.txt"],
      out_obj = "result.txt",
    )
    self.assertNotEqual(0, hancho.build_all())
    self.assertTrue("FileNotFoundError" in hancho.get_log())
    self.assertTrue("missing_dep.txt" in hancho.get_log())

  def test_expand_failed_to_terminate(self):
    """A recursive text template should cause an 'expand failed to terminate' error."""
    hancho.Task(
      command = "{flarp}",
      in_src  = [],
      out_obj = [],
      flarp   = "asdf {flarp}",
    )
    self.assertNotEqual(0, hancho.build_all())
    #self.assertTrue("Expanding '{flarp}' failed to terminate" in hancho.get_log())

  def test_garbage_command(self):
    """Non-existent command line commands should cause Hancho to fail the build."""
    hancho.Task(
      command = "aklsjdflksjdlfkjldfk",
      in_src  = __file__,
      out_obj = "result.txt",
    )
    self.assertNotEqual(0, hancho.build_all())
    self.assertTrue("ValueError: 127" in hancho.get_log())

  def test_rule_collision(self):
    """If multiple rules generate the same output file, that's an error."""
    hancho.Task(
      command = "sleep 0.1 && touch {rel(out_obj)}",
      in_src  = __file__,
      out_obj = "colliding_output.txt",
    )
    hancho.Task(
      command = "touch {rel(out_obj)}",
      in_src  = __file__,
      out_obj = "colliding_output.txt",
    )
    self.assertNotEqual(0, hancho.build_all())
    self.assertTrue("Multiple rules build" in hancho.get_log())

  def test_always_rebuild_if_no_inputs(self):
    """A rule with no inputs should always rebuild"""
    def run():
      hancho.reset()
      hancho.Task(
        command = "sleep 0.1 && touch {rel(out_obj)}",
        in_src  = [],
        out_obj = "result.txt",
      )
      self.assertEqual(0, hancho.build_all())
      return mtime_ns("build/result.txt")

    mtime1 = run()
    mtime2 = run()
    mtime3 = run()
    self.assertLess(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  def test_dep_changed(self):
    """Changing a file in deps[] should trigger a rebuild"""
    # This test is flaky without the "sleep 0.1" because of filesystem mtime granularity
    def run():
      hancho.reset()
      hancho.Task(
        command = "sleep 0.1 && touch {rel(out_obj)}",
        in_temp = ["build/dummy.txt"],
        in_src  = "src/test.cpp",
        out_obj = "result.txt",
      )
      self.assertEqual(0, hancho.build_all())
      return mtime_ns("build/result.txt")

    os.makedirs("build", exist_ok=True)
    force_touch("build/dummy.txt")
    mtime1 = run()
    mtime2 = run()
    force_touch("build/dummy.txt")
    mtime3 = run()
    self.assertEqual(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  def test_does_create_output(self):
    """Output files should appear in build/ by default"""
    hancho.Task(
      command = "touch {rel(out_obj)}",
      in_src  = [],
      out_obj = "result.txt",
    )
    self.assertEqual(0, hancho.build_all())
    self.assertTrue(path.exists("build/result.txt"))

  def test_doesnt_create_output(self):
    """Having a file mentioned in files_out should not magically create it"""
    hancho.Task(
      command = None,
      in_src  = [],
      out_obj = "result.txt"
    )
    self.assertEqual(0, hancho.build_all())
    self.assertFalse(path.exists("build/result.txt"))

  def test_header_changed(self):
    """Changing a header file tracked in the GCC depfile should trigger a rebuild"""

    def run():
      hancho.reset()
      time.sleep(0.01)
      compile = hancho.Command(
        command = "gcc -MMD -c {rel(in_src)} -o {rel(out_obj)}",
        out_obj = "{swap_ext(in_src, '.o')}",
        depfile = "{swap_ext(in_src, '.d')}",
      )
      compile(in_src = "src/test.cpp")
      self.assertEqual(0, hancho.build_all())
      return mtime_ns("build/src/test.o")

    mtime1 = run()
    mtime2 = run()
    force_touch("src/test.hpp")
    mtime3 = run()

    self.assertEqual(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  def test_input_changed(self):
    """Changing a source file should trigger a rebuild"""

    def run():
      hancho.reset()
      time.sleep(0.01)
      compile = hancho.Command(
        command = "gcc -MMD -c {rel(in_src)} -o {rel(out_obj)}",
        out_obj = "{swap_ext(in_src, '.o')}",
        depfile = "{swap_ext(in_src, '.d')}",
      )
      compile(in_src = "src/test.cpp")
      self.assertEqual(0, hancho.build_all())
      return mtime_ns("build/src/test.o")

    mtime1 = run()
    mtime2 = run()
    force_touch("src/test.cpp")
    mtime3 = run()

    self.assertEqual(mtime1, mtime2)
    self.assertLess(mtime2, mtime3)

  def test_multiple_commands(self):
    """Rules with arrays of commands should run all of them"""
    hancho.Task(
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

    self.assertEqual(0, hancho.build_all())
    self.assertTrue(path.exists("build/foo.txt"))
    self.assertTrue(path.exists("build/bar.txt"))
    self.assertTrue(path.exists("build/baz.txt"))

##    def test_arbitrary_flags(self):
##        """Passing arbitrary flags to Hancho should work"""
##        hancho.Task(
##            command = "touch {rel_build_files}",
##            source_files = [],
##            build_files = hancho.output_filename,
##        )
##
##        os.system(
##            "python3 ../hancho.py --output_filename=flarp.txt --quiet arbitrary_flags.hancho"
##        )
##        self.assertTrue(path.exists("build/tests/flarp.txt"))

  def test_sync_command(self):
    """The 'command' field of rules should be OK handling a sync function"""
    def sync_command(task):
      force_touch(task.out_obj)

    hancho.Task(
      command = sync_command,
      in_src  = [],
      out_obj = "result.txt",
    )
    self.assertEqual(0, hancho.build_all())
    self.assertTrue(path.exists("build/result.txt"))

#    def test_cancellation(self):
#        """A task that receives a cancellation exception should not run."""
#        task_that_fails = hancho.Task(
#            command = "(exit 255)",
#            in_src  = [],
#            out_obj = "fail_result.txt",
#        )
#
#        task_that_passes = hancho.Task(
#            command = "touch {rel(out_obj)}",
#            in_src  = [],
#            out_obj = "pass_result.txt",
#        )
#
#        should_be_cancelled = hancho.Task(
#            command = "touch {rel(out_obj)}",
#            in_src  = [task_that_fails, task_that_passes],
#            out_obj = "should_not_be_created.txt",
#        )
#
#        self.assertNotEqual(0, hancho.build_all())
#        self.assertTrue(Path("build/pass_result.txt").exists())
#        self.assertFalse(Path("build/fail_result.txt").exists())
#        self.assertFalse(Path("build/should_not_be_created.txt").exists())

  def test_task_creates_task(self):
    """Tasks using callbacks can create new tasks when they run."""
    def callback(task):
      new_task = hancho.Task(
        command = "touch {rel(out_obj)}",
        in_src  = [],
        out_obj = "dummy.txt"
      )
      # FIXME these should auto-queue
      new_task.queue()
      return []

    hancho.Task(
      command = callback,
      in_src  = [],
      out_obj = []
    )

    self.assertEqual(0, hancho.build_all())
    self.assertTrue(Path("build/dummy.txt").exists())

  def test_tons_of_tasks(self):
    """We should be able to queue up 1000+ tasks at once."""
    for i in range(1000):
      hancho.Task(
        desc    = "I am task {index}",
        command = "echo {index} > {rel(out_obj)}",
        in_src  = [],
        out_obj = "dummy{index}.txt",
        index   = i
      )
    self.assertEqual(0, hancho.build_all())
    self.assertEqual(1000, len(glob.glob("build/*")))

  def test_job_count(self):
    """We should be able to dispatch tasks that require various numbers of jobs/cores."""
    # Queues up 100 tasks that use random numbers of cores, then a "Job Hog" that uses all cores, then
    # another batch of 100 tasks that use random numbers of cores.

    for i in range(100):
      hancho.Task(
        desc    = "I am task {index}, I use {job_count} cores",
        command = "(exit 0)",
        in_src  = [],
        out_obj = [],
        job_count = random.randrange(1, os.cpu_count() + 1),
        index = i
      )

    hancho.Task(
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
      hancho.Task(
        desc = "I am task {index}, I use {job_count} cores",
        command = "(exit 0)",
        in_src  = [],
        out_obj = [],
        job_count = random.randrange(1, os.cpu_count() + 1),
        index = 100 + i
      )

    self.assertEqual(0, hancho.build_all())
    self.assertTrue(Path("build/slow_result.txt").exists())

####################################################################################################

if __name__ == "__main__":
  unittest.main()
