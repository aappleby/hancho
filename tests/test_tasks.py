#!/usr/bin/python3
"""Test cases for Hancho's Dict class"""

import asyncio
import doctest
import glob
import os
import random
import shutil
import sys
import time
import unittest
from pathlib import Path
from typing import cast

import hancho

####################################################################################################

def setUpModule():
    os.chdir(os.path.dirname(__file__))

def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    for t in doctests:
        t.shortDescription = lambda: None # type: ignore
    tests.addTests(doctests)
    return tests

def mtime_ns(filename):
    return os.stat(filename).st_mtime_ns

def force_touch(filename):
    if not Path(filename).exists():
        Path(filename).touch()
    old_mtime = mtime_ns(filename)
    while old_mtime == mtime_ns(filename):
        os.utime(filename, None)

####################################################################################################

class TestTasks(unittest.TestCase):

    def setUp(self):
        self.startTime = time.time()
        # Always wipe the build dir before a test, but make sure we're in the right dir.
        assert os.getcwd().endswith("/tests")
        shutil.rmtree("build", ignore_errors = True)
        # OK, now we should be good to start up Hancho.
        hancho.init(quiet = True)
        #hancho.init(verbose = True)
        sys.stdout.flush()

    def tearDown(self):
        #duration = time.time() - self.startTime
        #print(f"{duration:.3f}s ", end="", file = sys.stderr)
        sys.stdout.flush()

    def run_tasks(self, expected):
        hancho.config.build_all = True
        hancho.Runner.enable_all_tasks()
        result = hancho.Runner.sync_run_tasks()
        self.assertEqual(result, expected)

    #--------------------------------------------------------------------------------

    def test_run_tasks_zero(self):
        # If all tasks are OK, we should get 0 from run_tasks.
        good_task = hancho.Task(command = "echo Hello World", debug = True)
        self.run_tasks(0)
        self.assertEqual(good_task.state(), "FINISHED") #type:ignore

    def test_run_tasks_nonzero(self):
        # If any task fails, we should get -1 from run_tasks.
        bad_task = hancho.Task(command = "echo skldjlksdlfj && (exit 255)")
        self.run_tasks(-1)
        self.assertEqual(bad_task.state(), "FAILED") #type:ignore

    #--------------------------------------------------------------------------------

#    def _test_manual_queue1(self):
#        # If a task is _not_ queued, it should _not_ run.
#        t = hancho.Task(
#            command  = "touch {out_file}",
#            out_file = "test_manual_queue.txt",
#        )
#        self.assertFalse(os.path.exists("build/test_manual_queue.txt"))
#        result = hancho.Runner.sync_run_tasks()
#        self.assertEqual(result, 0)
#        self.assertFalse(os.path.exists("build/test_manual_queue.txt"))
#
#    def _test_manual_queue2(self):
#        # If a task _is_ manually queued, it _should_ run.
#        t = hancho.Task(
#            command  = "touch {out_file}",
#            out_file = "test_manual_queue.txt",
#        )
#        t.start2()
#        self.assertFalse(os.path.exists("build/test_manual_queue.txt"))
#        result = hancho.Runner.sync_run_tasks()
#        self.assertEqual(result, 0)
#        self.assertTrue(os.path.exists("build/test_manual_queue.txt"))
#
#    def _test_manual_queue3(self):
#        # A manually queued task should trigger its inputs to run.
#
#        # t0 is _not_ queued
#        t0 = hancho.Task(
#            command  = "touch {out_file}",
#            out_file = "test_manual_queue3a.txt",
#        )
#
#        # t1 is _not_ queued
#        t1 = hancho.Task(
#            command  = "touch {out_file}",
#            out_file = "test_manual_queue3b.txt",
#        )
#
#        # t2 depends on t0 but not t1, t0 should be transitively queued
#        t2 = hancho.Task(
#            command  = "touch {out_file}",
#            in_file  = t0,
#            out_file = "test_manual_queue3c.txt",
#        )
#        t2.start2()
#
#        self.assertFalse(os.path.exists("build/test_manual_queue3a.txt"))
#        self.assertFalse(os.path.exists("build/test_manual_queue3b.txt"))
#        self.assertFalse(os.path.exists("build/test_manual_queue3c.txt"))
#
#        result = hancho.Runner.sync_run_tasks()
#        self.assertEqual(result, 0)
#
#        self.assertTrue(os.path.exists("build/test_manual_queue3a.txt"))
#        self.assertFalse(os.path.exists("build/test_manual_queue3b.txt"))
#        self.assertTrue(os.path.exists("build/test_manual_queue3c.txt"))

    #--------------------------------------------------------------------------------

#  def _test_subrepos1(self):
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
#            "python3 ../../hancho.py -v -d top_test1.hancho".split(),
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
#            "python3 ../../hancho.py -v -d top_test2.hancho".split(),
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
#            "python3 ../../hancho.py -v -d top_test3.hancho".split(),
#            shell=True,
#            text=True,
#            capture_output=True,
#            cwd="subrepo_tests",
#        )
#        self.assertTrue(Path("subrepo_tests/build/submodule_tests/top.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo1/repo1.txt").exists())
#        self.assertTrue(Path("subrepo_tests/build/repo2/repo2.txt").exists())

    #--------------------------------------------------------------------------------

    def test_good_build_path(self):
        good_task = hancho.Task(
            command  = "echo {in_src} >> {out_obj}",
            in_src   = "src/foo.c",
            out_obj  = "{build_dir}/narp/foo.o",
        )
        self.assertFalse(Path("build/narp/foo.o").exists())
        self.run_tasks(0)
        self.assertEqual(good_task.state(), "FINISHED") #type:ignore
        self.assertTrue(Path("build/narp/foo.o").exists())

    def test_bad_build_path(self):
        bad_task = hancho.Task(
            desc     = "This task has a bad path for out_obj",
            command  = "echo {in_src} >> {out_obj}",
            in_src   = "src/foo.c",
            out_obj  = "../../../foo.o",
        )
        self.run_tasks(-1)
        self.assertEqual(bad_task.state(), "BROKEN")
        self.assertFalse(Path("build/foo.o").exists())

    #--------------------------------------------------------------------------------

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux")
    def test_good_run_cmd(self):
        task = hancho.Task(
            desc    = "Testing run_cmd",
            command = r"echo I am runnning the {run_cmd('uname')} operating system."
        )
        self.run_tasks(0)
        self.assertTrue("I am runnning the Linux operating system.\n" in task._stdout)

    def test_bad_run_cmd(self):
        task = hancho.Task(
            desc    = "Broken run_cmd",
            command = r"echo {run_cmd('This is totally not a valid command.')}",
        )
        self.run_tasks(-1)
        self.assertEqual(task.state(), "FAILED")

    def test_garbage_command(self):
        # Non-existent command line commands should cause Hancho to fail the build.
        garbage_task = hancho.Task(
            command = "aklsjdflksjdlfkjldfk",
        )
        self.run_tasks(-1)
        self.assertEqual(garbage_task.state(), "FAILED")

    def test_missing_command(self):
        # Non-existent command line commands should cause Hancho to fail the build.
        with self.assertRaises(ValueError):
            hancho.Task(not_a_command = "echo Hello World")

    def test_task_collision(self):
        # If multiple distinct commands generate the same output file, that's an error.
        hancho.Task(
            command = "touch {out_obj}",
            in_src  = __file__,
            out_obj = "colliding_output.txt",
        )
        task2 = hancho.Task(
            command = "touch {out_obj}",
            in_src  = __file__,
            out_obj = "colliding_output.txt",
        )
        self.run_tasks(-1)
        self.assertEqual(task2.state(), "BROKEN")

    #--------------------------------------------------------------------------------

    def test_always_rebuild_if_no_inputs(self):
        # A rule with no inputs should always rebuild
        # This test is flaky without the "sleep 0.1" because of filesystem mtime granularity

        def run():
            hancho.init(quiet = True)
            hancho.Task(
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

    def test_dep_changed(self):
        # Changing a file in in_files[] should trigger a rebuild
        # This test is flaky without the "sleep 0.1" because of filesystem mtime granularity

        dummy = "data/dummy.txt"

        def run():
            hancho.init(quiet = True)
            hancho.Task(
                name    = "test_dep_changed {in_src}",
                command = "sleep 0.1 && touch {out_obj}",
                in_temp = dummy,
                in_src  = "src/test.cpp",
                out_obj = "result.txt",
            )
            self.run_tasks(0)
            return mtime_ns("build/result.txt")

        force_touch(dummy)
        mtime1 = run()
        mtime2 = run()
        force_touch(dummy)
        mtime3 = run()
        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)


    #--------------------------------------------------------------------------------

    def test_missing_input(self):
        # We should fail if an input is missing
        task = hancho.Task(
            desc    = "Should fail due to missing input",
            command = "touch {out_obj}",
            in_src  = "src/does_not_exist.txt",
            out_obj = "missing_src.txt",
        )
        self.run_tasks(-1)
        self.assertEqual(task.state(), "BROKEN")

    def test_missing_dep(self):
        # We should fail if a dependency is missing even if it's not used by the command.
        task = hancho.Task(
            desc    = "Missing dep should fail",
            command = "touch {out_obj}",
            in_src  = "src/test.cpp",
            in_dep  = ["missing_dep.txt"],
            out_obj = "result.txt",
        )
        self.run_tasks(-1)
        self.assertEqual(task.state(), "BROKEN")

    #--------------------------------------------------------------------------------

    def test_absolute_inputs(self):
        # If input filenames are absolute paths, we should still end up with build files under
        # build_root.

        hancho.Task(
            desc    = "In_src is absolute path",
            command = "cp {in_src} {out_obj}",
            in_src  = os.path.abspath("src/foo.c"),
            out_obj = "{ext(in_src, '.o')}",
        )

        self.assertFalse(Path("build/src/foo.o").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/src/foo.o").exists())

    #--------------------------------------------------------------------------------

    def test_does_create_output(self):
        # Output files should appear in build/ by default
        hancho.Task(
            command = "touch {out_obj}",
            in_src  = [],
            out_obj = "result.txt",
        )
        self.assertFalse(os.path.exists("build/result.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/result.txt"))

    def test_doesnt_create_output(self):
        # Having a file mentioned in out_obj should not magically create it
        hancho.Task(
            command = "echo Hello World >> {out_txt}",
            in_src  = [],
            out_txt = "blarp.txt",
            out_obj = "result.txt"
        )
        self.assertFalse(os.path.exists("build/result.txt"))
        self.assertFalse(os.path.exists("build/blarp.txt"))
        self.run_tasks(0)
        self.assertFalse(os.path.exists("build/result.txt"))
        self.assertTrue(os.path.exists("build/blarp.txt"))

    def test_header_changed(self):
        # Changing a header file tracked in the GCC dependencies file should trigger a rebuild
        def run():
            hancho.init(quiet = True)
            time.sleep(0.01)
            compile = hancho.Tool(
                name       = "test_header_changed {in_src}",
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

    def test_input_changed(self):
        # Changing a source file should trigger a rebuild
        def run():
            hancho.init(quiet = True)
            time.sleep(0.01)
            compile = hancho.Dict(
                name       = "test_input_changed {in_src}",
                command    = "gcc -MMD -c {in_src} -o {out_obj}",
                in_src     = None,
                in_depfile = "{ext(out_obj, '.d')}",
                out_obj    = "{ext(in_src, '.o')}",
            )
            hancho.Task(compile, in_src = "src/test.cpp")
            self.run_tasks(0)
            return mtime_ns("build/src/test.o")

        mtime1 = run()
        mtime2 = run()
        force_touch("src/test.cpp")
        mtime3 = run()

        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)

    def test_multiple_commands(self):
        # Rules with arrays of commands should run all of them
        hancho.Task(
            command = [
                "echo foo > {out_foo}",
                "echo bar > {out_bar}",
                "echo baz > {out_baz}",
            ],
            out_foo = "foo.txt",
            out_bar = "bar.txt",
            out_baz = "baz.txt",
        )

        self.assertFalse(os.path.exists("build/foo.txt"))
        self.assertFalse(os.path.exists("build/bar.txt"))
        self.assertFalse(os.path.exists("build/baz.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/foo.txt"))
        self.assertTrue(os.path.exists("build/bar.txt"))
        self.assertTrue(os.path.exists("build/baz.txt"))

    def test_arbitrary_flags(self):
        # Passing arbitrary flags to Hancho should work
        hancho.init(quiet = True, flarpy="flarp.txt")
        self.assertEqual("flarp.txt", hancho.config.flarpy)

        hancho.Task(
            command = "touch {out_file}",
            source_files = [],
            out_file = "{flarpy}",
        )
        self.assertFalse(os.path.exists("build/flarp.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/flarp.txt"))

    def test_sync_command(self):
        def sync_command(task):
            force_touch(task._config.out_obj)

        hancho.Task(
            desc = "The 'command' field of rules should be OK handling a sync function",
            command = sync_command,
            in_src  = [],
            out_obj = "{name}",
            name = "result.txt",
        )
        self.assertFalse(os.path.exists("build/result.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/result.txt"))

    def test_lambda_command(self):
        hancho.Task(
            desc = "The 'command' field of rules should be OK handling a lambda",
            command = lambda task: force_touch(task._config.out_obj),
            in_src  = [],
            out_obj = "{name}",
            name = "result.txt",
        )
        self.assertFalse(os.path.exists("build/result.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/result.txt"))

    def test_sync_callback(self):
        def callback(task):
            time.sleep(0.1)
            force_touch(task._config.out_file)

        hancho.Task(command = callback, out_file = "test_async_callback.txt")
        self.assertFalse(Path("build/test_async_callback.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/test_async_callback.txt").exists())

    def test_async_callback(self):
        async def callback(task):
            await asyncio.sleep(0.1)
            force_touch(task._config.out_file)

        hancho.Task(command = callback, out_file = "test_async_callback.txt")
        self.assertFalse(Path("build/test_async_callback.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/test_async_callback.txt").exists())

    def test_cancellation(self):
        # A task that receives a cancellation exception should not run.

        # Note: not using -k0 will break the cancellation test
        hancho.init(quiet = True, keep_going = 0)

        task_that_fails = hancho.Task(
            desc    = "task that fails",
            command = "(exit 255)",
            in_src  = [],
            out_obj = "fail_result.txt",
        )
        task_that_passes = hancho.Task(
            desc    = "task that passes",
            command = "touch {out_obj}",
            in_src  = [],
            out_obj = "pass_result.txt",
        )
        should_be_cancelled = hancho.Task(
            desc    = "should be cancelled",
            command = "touch {out_obj}",
            in_src  = [task_that_fails, task_that_passes],
            out_obj = "should_not_be_created.txt",
        )
        self.assertFalse(os.path.exists("build/pass_result.txt"))
        self.run_tasks(-1)
        self.assertEqual(1, hancho.Stats.tasks_finished)
        self.assertEqual(1, hancho.Stats.tasks_failed)
        self.assertEqual(1, hancho.Stats.tasks_cancelled)
        self.assertEqual(task_that_fails.state(), "FAILED")
        self.assertEqual(task_that_passes.state(), "FINISHED")
        self.assertEqual(should_be_cancelled.state(), "CANCELLED")
        self.assertTrue(os.path.exists("build/pass_result.txt"))
        self.assertFalse(os.path.exists("build/fail_result.txt"))
        self.assertFalse(os.path.exists("build/should_not_be_created.txt"))


    def test_no_mixed_commands(self):
        with self.assertRaises(ValueError):
            hancho.Task(
                command = [
                    "echo Hello World",
                    lambda task: print(f"Hello World {type(task)}"),
                ]
            )

    def test_task_creates_task(self):
        # Tasks using callbacks can create new tasks when they run.
        def callback(task):
            hancho.Task(
                command = "touch {out_obj}",
                in_src  = [],
                out_obj = "dummy.txt"
            )
            return []

        hancho.Task(
            command = callback,
            in_src  = [],
            out_obj = []
        )

        self.assertFalse(Path("build/dummy.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/dummy.txt").exists())

    def test_tons_of_tasks(self):
        # We should be able to queue up 1000+ tasks at once.
        for i in range(1000):
            hancho.Task(
                desc    = "I am task {index}",
                command = "echo {index} > {out_obj}",
                in_src  = [],
                out_obj = "dummy{index}.txt",
                index   = i
            )
        self.assertEqual(0, len(glob.glob("build/*")))
        self.run_tasks(0)
        self.assertEqual(1000, len(glob.glob("build/*")))

    def test_job_count(self):
        # We should be able to dispatch tasks that require various numbers of jobs/cores.
        # Queues up 100 tasks that use random numbers of cores, then a "Job Hog" that uses all cores, then
        # another batch of 100 tasks that use random numbers of cores.

        for i in range(100):
            hancho.Task(
                desc    = "I am task {index}, I use {job_count} cores",
                command = "(exit 0)",
                in_src  = [],
                out_obj = [],
                job_count = random.randrange(1, cast(int, os.cpu_count()) + 1),
                index = i
            )

        hancho.Task(
            desc = "********** I am the slow task, I eat all the cores **********",
            command = [
                "touch {out_obj}",
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
                job_count = random.randrange(1, cast(int, os.cpu_count()) + 1),
                index = 100 + i
            )

        self.assertFalse(Path("build/slow_result.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/slow_result.txt").exists())



####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
