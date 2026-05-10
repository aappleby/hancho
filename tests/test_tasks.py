#!/usr/bin/python3
"""Test cases for Hancho's Dict class"""

import sys
import unittest
import os
import shutil
from pathlib import Path

print(f"************** {os.getcwd()} **************")

sys.path.append("..")
import hancho

def mtime_ns(filename):
    return os.stat(filename).st_mtime_ns

def force_touch(filename):
    if not Path(filename).exists():
        Path(filename).touch()
    old_mtime = mtime_ns(filename)
    while old_mtime == mtime_ns(filename):
        os.utime(filename, None)

def color(red=None, green=None, blue=None):
    """Converts RGB color to ANSI format string."""
    # Color strings don't work in Windows console, so don't emit them.
    if os.name == "nt":
        return ""
    if red is None:
        return "\x1B[0m"
    return f"\x1B[38;2;{red};{green};{blue}m"

####################################################################################################

class TestTasks(unittest.TestCase):
    def setUp(self):
        #print(f"Running {self.__class__.__name__}::{self._testMethodName}")
        sys.stdout.flush()

        # Always wipe the build dir before a test
        shutil.rmtree("build", ignore_errors=True)

        (this_dir, this_file) = os.path.split(__file__)
        #print(this_dir)

        #hancho.init(['-q', f'-C {this_dir}'])
        #hancho.init(['-d', '-v', f'-C {this_dir}'])

        hancho.init(
            this_dir  = this_dir,
            this_file = this_file,
            debug     = True,
            quiet     = False,
            verbose   = True,
        )
        #print(f"*******({hancho.config.this_dir})*******")

    def tearDown(self):
        """And wipe the build dir after a test too."""
        #shutil.rmtree("build", ignore_errors=True)

    def run_tasks(self, expected = 0):
        hancho.Runner.queue_all_tasks()
        result = hancho.Runner.run_tasks()
        self.assertEqual(result, expected)

    #--------------------------------------------------------------------------------

#    def test_should_pass(self):
#        hancho.Task(command = "echo Hello World")
#        self.run_tasks()
#
#    def test_should_fail(self):
#        """Sanity check"""
#        bad_task = hancho.Task(command = "echo skldjlksdlfj && (exit 255)")
#        self.run_tasks(-1)
#        self.assertEqual(bad_task._state, hancho.TaskState.FAILED)

    #--------------------------------------------------------------------------------

#  def test_subrepos1(self):
#      """Outputs from a subrepo should go in build/repo_name/..."""
#      repo = self.hancho.repo("subrepo")
#      task = repo.task(
#          command = "cat {rel_source_files} > {rel_build_files}",
#          source_files = "stuff.txt",
#          build_files = "repo.txt",
#          b*ase_path = os.path.abspath("subrepo")
#      )
#      self.assertEqual(0, hancho.app.build_all())
#      self.assertTrue(Path("build/subrepo/repo.txt").exists())
#
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
#
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

    #--------------------------------------------------------------------------------

#    def test_out_file_dir(self):
#        hancho.Task(
#            command  = "echo Hello File >> {out_file}",
#            out_file = "test_command_lists.txt"
#        )
#        self.run_tasks()
#        self.assertEqual(True, os.path.isfile(os.getcwd() + "/build/test_command_lists.txt"))

    def test_good_build_path(self):
        good_task = hancho.Task(
            command  = "touch {out_obj}",
            in_src   = "{repo_dir}/src/foo.c",
            out_obj  = "{repo_dir}/build/narp/foo.o",
        )
        self.run_tasks(0)
        #self.assertEqual(good_task._state, hancho.TaskState.FINISHED)
        self.assertTrue(Path("build/narp/foo.o").exists())

#    def test_bad_build_path(self):
#        bad_task = hancho.Task(
#            command  = "touch {out_obj}",
#            in_src   = "src/foo.c",
#            out_obj  = "{repo_dir}/../build/foo.o",
#        )
#        self.run_tasks(-1)
#        self.assertEqual(bad_task._state, hancho.TaskState.BROKEN)
#        self.assertFalse(Path("build/foo.o").exists())

    #--------------------------------------------------------------------------------

#    def test_run_cmd(self):
#        if sys.platform != 'linux':
#            return
#
#        test_task = hancho.Task(
#            desc    = "Testing run_cmd",
#            command = r"echo I am runnning the {run_cmd('uname')} operating system."
#        )
#        self.run_tasks()
#
#        self.assertEqual(
#            test_task._stdout,
#            f"I am runnning the {hancho.Utils.run_cmd('uname')} operating system.\n"
#        )
#
#    def test_broken_run_cmd(self):
#        command = r"echo {run_cmd('This is totally not a valid command.')}",
#        hancho.Task(desc = "Broken run_cmd", command = command)
#        self.run_tasks(-1)

    #--------------------------------------------------------------------------------
