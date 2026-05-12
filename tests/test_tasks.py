#!/usr/bin/python3
"""Test cases for Hancho's Dict class"""

import sys
import unittest
import os
import shutil
from pathlib import Path
import time

#(this_dir, this_file) = os.path.split(os.path.abspath(__file__))
#hancho_dir = os.path.normpath(f"{this_dir}/..")
#sys.path.append(hancho_dir)

sys.path.append("..")
import hancho

def setUpModule():
    # Change to your desired directory
    #os.chdir(this_dir)
    pass

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

        hancho.init(
            #this_dir  = this_dir,
            #this_file = this_file,
            #debug     = True,
            #verbose   = True,

            debug     = False,
            verbose   = False,
            quiet     = True,
        )

    def tearDown(self):
        """And wipe the build dir after a test too."""
        #shutil.rmtree("build", ignore_errors=True)

    def run_tasks(self, expected):
        hancho.Runner.queue_all_tasks()
        result = hancho.Runner.run_tasks()
        self.assertEqual(result, expected)

    #--------------------------------------------------------------------------------

    def _test_run_tasks_zero(self):
        # If all tasks are OK, we should get 0 from run_tasks.
        hancho.Task(command = "echo Hello World")
        self.run_tasks(0)

    def _test_run_tasks_nonzero(self):
        # If any task fails, we should get -1 from run_tasks.
        bad_task = hancho.Task(command = "echo skldjlksdlfj && (exit 255)")
        self.run_tasks(-1)
        self.assertEqual(bad_task._state, hancho.TaskState.FAILED)

    #--------------------------------------------------------------------------------

#  def _test_subrepos1(self):
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
#    def _test_subrepos1(self):
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
#    def _test_subrepos2(self):
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
#    def _test_subrepos3(self):
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

    def _test_good_build_path(self):
        good_task = hancho.Task(
            command  = "echo {in_src} >> {out_obj}",
            in_src   = "src/foo.c",
            out_obj  = "{build_dir}/narp/foo.o",
        )
        self.run_tasks(0)
        self.assertEqual(good_task._state, hancho.TaskState.FINISHED)
        self.assertTrue(Path("build/narp/foo.o").exists())

    def _test_bad_build_path(self):
        hancho.init(debug=True)
        bad_task = hancho.Task(
            desc     = "This task has a bad path for out_obj",
            command  = "echo {in_src} >> {out_obj}",
            in_src   = "src/foo.c",
            out_obj  = "{repo_dir}/foo.o",
            should_fail = True,
        )
        self.run_tasks(0)
        self.assertEqual(bad_task._state, hancho.TaskState.BROKEN)
        self.assertFalse(Path("build/foo.o").exists())

    #--------------------------------------------------------------------------------

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux")
    def _test_good_run_cmd(self):
        test_task = hancho.Task(
            desc    = "Testing run_cmd",
            command = r"echo I am runnning the {run_cmd('uname')} operating system."
        )
        self.run_tasks(0)
        self.assertEqual(test_task._stdout, f"I am runnning the Linux operating system.\n")

    def _test_bad_run_cmd(self):
        task = hancho.Task(
            desc    = "Broken run_cmd",
            command = r"echo {run_cmd('This is totally not a valid command.')}",
            should_fail = True,
        )
        self.run_tasks(0)
        self.assertEqual(task._state, hancho.TaskState.FAILED)

    def _test_garbage_command(self):
        """Non-existent command line commands should cause Hancho to fail the build."""
        garbage_task = hancho.Task(
            command = "aklsjdflksjdlfkjldfk",
        )
        self.run_tasks(-1)
        self.assertEqual(garbage_task._state, hancho.TaskState.FAILED)
        self.assertTrue("CommandFailure" in hancho.Log.buffer)

    def _test_task_collision(self):
        """If multiple distinct commands generate the same output file, that's an error."""
        hancho.Task(
            command = "touch {out_obj}",
            in_src  = __file__,
            out_obj = "colliding_output.txt",
        )
        hancho.Task(
            command = "touch {out_obj}",
            in_src  = __file__,
            out_obj = "colliding_output.txt",
        )
        self.run_tasks(-1)
        self.assertTrue("TaskCollision" in hancho.Log.buffer)

    #--------------------------------------------------------------------------------

    def _test_always_rebuild_if_no_inputs(self):
        # A rule with no inputs should always rebuild

        def run():
            #hancho.init(quiet = True, this_dir = this_dir, this_file = this_file)
            hancho.init(quiet = True)
            t = hancho.Task(
                command = "sleep 0.1 && touch {out_obj}",
                in_src  = [],
                out_obj = "result.txt",
            )
            self.run_tasks(0)
            return mtime_ns("build/result.txt")

        mtime1 = run()
        mtime2 = run()
        mtime3 = run()
        self.assertLess(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)

    def _test_dep_changed(self):
        # Changing a file in in_files[] should trigger a rebuild
        # This test is flaky without the "sleep 0.1" because of filesystem mtime granularity
        # FIXME ok dealing with repo-relative and tests/-relative paths here is annoying.

        dummy = "data/dummy.txt"

        def run():
            #hancho.init(quiet = True, this_dir = this_dir, this_file = this_file)
            hancho.init(quiet = True)
            hancho.Task(
                command = "sleep 0.1 && touch {out_obj}",
                in_temp = [dummy],
                in_src  = "src/test.cpp",
                out_obj = "result.txt",
            )
            self.run_tasks(0)
            return mtime_ns("build/result.txt")

        #os.makedirs("build", exist_ok=True)
        force_touch(dummy)
        mtime1 = run()
        mtime2 = run()
        force_touch(dummy)
        mtime3 = run()
        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)


    #--------------------------------------------------------------------------------

    def _test_missing_input(self):
        # We should fail if an input is missing
        task = hancho.Task(
            desc    = "Should fail due to missing input",
            command = "touch {out_obj}",
            in_src  = "src/does_not_exist.txt",
            out_obj = "missing_src.txt",
            should_fail = True,
        )
        self.run_tasks(0)
        self.assertEqual(task._state, hancho.TaskState.BROKEN)
        self.assertTrue("FileNotFoundError" in hancho.Log.buffer)
        self.assertTrue("does_not_exist.txt" in hancho.Log.buffer)

    def _test_missing_dep(self):
        # We should fail if a dependency is missing even if it's not used by the command.
        task = hancho.Task(
            desc    = "Missing dep should fail",
            command = "touch {out_obj}",
            in_src  = "src/test.cpp",
            in_dep  = ["missing_dep.txt"],
            out_obj = "result.txt",
            should_fail  = True,
        )
        self.run_tasks(0)
        self.assertEqual(task._state, hancho.TaskState.BROKEN)
        self.assertTrue("FileNotFoundError" in hancho.Log.buffer)
        self.assertTrue("missing_dep.txt" in hancho.Log.buffer)

    #--------------------------------------------------------------------------------

    def _test_absolute_inputs(self):
        # If input filenames are absolute paths, we should still end up with build files under
        # build_root.

        t = hancho.Task(
            desc    = "In_src is absolute path",
            command = "cp {in_src} {out_obj}",
            in_src  = os.path.abspath("src/foo.c"),
            out_obj = "{ext(in_src, '.o')}",
        )

        self.run_tasks(0)
        self.assertTrue(Path("build/src/foo.o").exists())

    #--------------------------------------------------------------------------------

    def _test_does_create_output(self):
        # Output files should appear in build/ by default
        hancho.Task(
            command = "touch {out_obj}",
            in_src  = [],
            out_obj = "result.txt",
        )
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/result.txt"))

    def _test_doesnt_create_output(self):
        # Having a file mentioned in out_obj should not magically create it
        hancho.Task(
            command = "echo",
            in_src  = [],
            out_obj = "result.txt"
        )
        self.run_tasks(0)
        self.assertFalse(os.path.exists("../build/tetts/result.txt"))

    def test_header_changed(self):
        # Changing a header file tracked in the GCC dependencies file should trigger a rebuild
        def run():
            #hancho.init(quiet = True, this_dir = this_dir, this_file = this_file)
            hancho.init(quiet = True)
            time.sleep(0.01)
            compile = hancho.Tool(
                command    = "gcc -MMD -c {in_src} -o {out_obj}",
                in_depfile = "{ext(out_obj, '.d')}",
                out_obj    = "{ext(in_src, '.o')}",
            )
            hancho.Task(compile, in_src = "src/test.cpp")
            self.run_tasks(0)
            return mtime_ns("build/src/test.o")

        mtime1 = run()
        mtime2 = run()
        force_touch("src/test.hpp")
        mtime3 = run()

        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)



if __name__ == "__main__":
    unittest.main(verbosity=1)
