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

VERBOSITY = "quiet"

# the hancho references hit this and it's bogus because of the weird way hancho intercepts
# attributes
# pyright: reportAttributeAccessIssue=false

if os.name == "nt" and "VCINSTALLDIR" not in os.environ:
    print("Tests must run from a Visual Studio developer prompt!", file=sys.stderr)
    sys.exit(1)

####################################################################################################


def setUpModule():
    os.chdir(os.path.dirname(__file__))


def load_tests(loader, tests, ignore):
    doctests = doctest.DocTestSuite(optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE)
    tests.addTests(doctests)
    return tests


def mtime_ns(filename):
    return os.stat(filename).st_mtime_ns

def force_touch(filename, append_text = None):
    if isinstance(filename, list):
        return (force_touch(f, append_text) for f in filename)

    if not Path(filename).exists():
        Path(filename).touch()

    if append_text:
        with open(filename, "a") as f:
            f.write(append_text)
    old_mtime = mtime_ns(filename)
    while old_mtime == mtime_ns(filename):
        os.utime(filename, None)


####################################################################################################


class TestTasks(unittest.TestCase):
    def setUp(self):
        self.startTime = time.time()
        # Always wipe the build dir before a test, but make sure we're in the right dir.
        assert os.getcwd().endswith(os.sep + "tests")
        shutil.rmtree("build", ignore_errors=True)
        # OK, now we should be good to start up Hancho.

        # Note: using 'max_errors = 0' will break the cancellation test, we have to tolerate the
        # failure to see the cancellation.
        hancho.init(verbosity = VERBOSITY, max_errors=999)
        sys.stdout.flush()

    def tearDown(self):
        sys.stdout.flush()

    def run_tasks(self, expected):
        hancho.Runner.enable_all_tasks()
        result = hancho.build()
        self.assertEqual(result, expected)

    # ----------------------------------------------------------------------------------------------

    def test_run_tasks_nonzero(self):
        # If any task fails, we should get -1 from run_tasks.
        bad_task = hancho.Task(command="echo test_run_tasks_zero && (exit 255)")
        self.run_tasks(1)
        self.assertIsInstance(bad_task._error, hancho.Task.FAILED) #type:ignore

    def test_run_tasks_zero(self):
        # If all tasks are OK, we should get 0 from run_tasks.
        good_task = hancho.Task(command="echo test_run_tasks_zero")
        self.run_tasks(0)
        self.assertIsNone(good_task._error)

    def test_asyncio_cancelled(self):
        def asyncio_cancelled(_):
            time.sleep(0.1)
            raise asyncio.CancelledError()

        hancho.Task(command=asyncio_cancelled, out_file="asyncio_cancelled.txt")
        self.assertEqual(0, hancho.Runner.tasks_cancelled)
        self.run_tasks(0)
        self.assertEqual(1, hancho.Runner.tasks_cancelled)


    # ----------------------------------------------------------------------------------------------

    #    def _test_manual_queue1(self):
    #        # If a task is _not_ queued, it should _not_ run.
    #        t = hancho.Task(
    #            command = lambda task : force_touch(task.config.out_file),
    #            out_file = "test_manual_queue.txt",
    #        )
    #        self.assertFalse(os.path.exists("build/test_manual_queue.txt"))
    #        self.run_tasks(0)
    #        self.assertFalse(os.path.exists("build/test_manual_queue.txt"))
    #
    #    def _test_manual_queue2(self):
    #        # If a task _is_ manually queued, it _should_ run.
    #        t = hancho.Task(
    #            command = lambda task : force_touch(task.config.out_file),
    #            out_file = "test_manual_queue.txt",
    #        )
    #        t.start2()
    #        self.assertFalse(os.path.exists("build/test_manual_queue.txt"))
    #        self.run_tasks(0)
    #        self.assertTrue(os.path.exists("build/test_manual_queue.txt"))
    #
    #    def _test_manual_queue3(self):
    #        # A manually queued task should trigger its inputs to run.
    #
    #        # t0 is _not_ queued
    #        t0 = hancho.Task(
    #            command = lambda task : force_touch(task.config.out_file),
    #            out_file = "test_manual_queue3a.txt",
    #        )
    #
    #        # t1 is _not_ queued
    #        t1 = hancho.Task(
    #            command = lambda task : force_touch(task.config.out_file),
    #            out_file = "test_manual_queue3b.txt",
    #        )
    #
    #        # t2 depends on t0 but not t1, t0 should be transitively queued
    #        t2 = hancho.Task(
    #            command = lambda task : force_touch(task.config.out_file),
    #            in_file  = t0,
    #            out_file = "test_manual_queue3c.txt",
    #        )
    #        t2.start2()
    #
    #        self.assertFalse(os.path.exists("build/test_manual_queue3a.txt"))
    #        self.assertFalse(os.path.exists("build/test_manual_queue3b.txt"))
    #        self.assertFalse(os.path.exists("build/test_manual_queue3c.txt"))
    #
    #        self.run_tasks(0)
    #
    #        self.assertTrue(os.path.exists("build/test_manual_queue3a.txt"))
    #        self.assertFalse(os.path.exists("build/test_manual_queue3b.txt"))
    #        self.assertTrue(os.path.exists("build/test_manual_queue3c.txt"))

    # ----------------------------------------------------------------------------------------------

    #  def _test_subrepos1(self):
    #      repo = self.hancho.repo("subrepo")
    #      task = repo.task(
    #          command = "cat {rel_source_files} > {rel_build_files}",
    #          source_files = "stuff.txt",
    #          build_files = "repo.txt",
    #          b*ase_path = os.path.abspath("subrepo")
    #      )
    #      self.run_tasks(0)


    #      self.assertTrue(Path("build/subrepo/repo.txt").exists())
    #
    #    def _test_subrepos1(self):
    #        shutil.rmtree("subrepo_tests/build", ignore_errors=True)
    #        result = subprocess.run(
    #            f"{sys.executable} ../../hancho.py -v -d top_test1.hancho".split(),
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
    #            f"{sys.executable} ../../hancho.py -v -d top_test2.hancho".split(),
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
    #            f"{sys.executable} ../../hancho.py -v -d top_test3.hancho".split(),
    #            shell=True,
    #            text=True,
    #            capture_output=True,
    #            cwd="subrepo_tests",
    #        )
    #        self.assertTrue(Path("subrepo_tests/build/submodule_tests/top.txt").exists())
    #        self.assertTrue(Path("subrepo_tests/build/repo1/repo1.txt").exists())
    #        self.assertTrue(Path("subrepo_tests/build/repo2/repo2.txt").exists())

    # ----------------------------------------------------------------------------------------------

    def test_good_build_path(self):
        good_task = hancho.Task(
            command="echo {in_src} >> {out_obj}",
            in_src="src/foo.c",
            out_obj="{build_dir}/narp/foo.o",
        )
        self.assertFalse(Path("build/narp/foo.o").exists())
        self.run_tasks(0)
        self.assertIsNone(good_task._error)
        self.assertTrue(Path("build/narp/foo.o").exists())

    def test_bad_build_path(self):
        bad_task = hancho.Task(
            desc="This task has a bad path for out_obj",
            command="echo {in_src} >> {out_obj}",
            in_src="src/foo.c",
            out_obj="../../../foo.o",
        )
        self.run_tasks(1)
        self.assertIsInstance(bad_task._error, hancho.Task.BROKEN)
        self.assertFalse(Path("build/foo.o").exists())

    # ----------------------------------------------------------------------------------------------

    @unittest.skipUnless(os.name == "posix", "requires Linux")
    def test_good_run_cmd(self):
        task = hancho.Task(
            desc="Testing run_cmd",
            command=r"echo I am runnning in {run_cmd('cd')}",
        )
        self.run_tasks(0)
        self.assertEqual(repr(f"I am runnning in {os.getcwd()}"), repr(task._stdout.strip()))

    def test_bad_run_cmd(self):
        """
        Trying to run an arbitrary command and use it in a template should report BROKEN if the
        embedded command is invalid.
        """
        task = hancho.Task(
            desc="Broken run_cmd",
            command=r"echo {run_cmd('This is totally not a valid command.')}",
        )
        self.run_tasks(1)
        self.assertIsInstance(task._error, hancho.Task.BROKEN)

    def test_unexpandable_command(self):
        """
        Commands that have residual braces after expansion should be reported as broken but ONLY
        if we are in 'strict' mode.
        """
        task = hancho.Task(
            desc="Unexpandable command",
            command=r"echo Hello {missing} world!",
        )
        self.run_tasks(1)
        self.assertIsInstance(task._error, hancho.Task.BROKEN)

    def test_garbage_command(self):
        """
        Non-existent command line commands should cause Hancho to fail the build.
        """
        garbage_task = hancho.Task(
            command="aklsjdflksjdlfkjldfk",
        )
        self.run_tasks(1)
        self.assertIsInstance(garbage_task._error, hancho.Task.FAILED)

#    def test_missing_command(self):
#        """
#        Non-existent commands should cause Hancho to fail the build.
#        """
#        bad_task = hancho.Task(not_a_command="echo test_missing_command")
#        self.run_tasks(1)
#        self.assertIsInstance(bad_task._error, hancho.Task.BROKEN)

    def test_task_collision(self):
        """
        If multiple distinct commands generate the same output file, that's an error.
        """
        hancho.Task(
            command = lambda task : (os.utime(src, None) for src in task.config.out_obj),
            in_src=__file__,
            out_obj="colliding_output.txt",
        )
        task2 = hancho.Task(
            command = lambda task : force_touch(task.config.out_obj),
            in_src=__file__,
            out_obj="colliding_output.txt",
        )
        self.run_tasks(1)
        self.assertIsInstance(task2._error, hancho.Task.BROKEN)

    # ----------------------------------------------------------------------------------------------

    def test_always_rebuild_if_no_inputs(self):
        # A rule with no inputs should always rebuild
        # This test is flaky without the "sleep 0.1" because of filesystem mtime granularity

        def run():
            hancho.init(verbosity = VERBOSITY)
            hancho.Task(
                command=[
                    lambda task : time.sleep(0.1),
                    lambda task : force_touch(task.config.out_obj),
                ],
                in_src=[],
                out_obj="result.txt",
            )
            self.run_tasks(0)
            return mtime_ns("build/result.txt")

        mtime1 = run()
        mtime2 = run()
        mtime3 = run()
        self.assertLess(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)

    def test_input_changed(self):
        # Changing a source file should trigger a rebuild
        def run():
            hancho.init(verbosity = VERBOSITY)
            time.sleep(0.01)
            compile = hancho.Dict(
                desc="test_input_changed {in_src}",
                command = lambda task : shutil.copy(task.config.in_src, task.config.out_obj),
                in_src=None,
                in_depfile="{ext(out_obj, '.d')}",
                out_obj="{ext(in_src, '.o')}",
            )
            hancho.Task(compile, in_src="src/test.cpp")
            self.run_tasks(0)
            return mtime_ns("build/src/test.o")

        mtime1 = run()
        mtime2 = run()
        force_touch("src/test.cpp")
        mtime3 = run()

        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)


    def test_dep_changed(self):
        # Changing a file in in_files[] should trigger a rebuild, even if it isn't used by the
        # command.
        # This test is flaky without the "sleep 0.1" because of filesystem mtime granularity

        dummy = "data/dummy.txt"

        def run():
            hancho.init(verbosity = VERBOSITY)
            hancho.Task(
                desc="test_dep_changed {in_src}",
                #command="sleep 0.1 && touch {out_obj}",
                command = [
                    lambda task : time.sleep(0.1),
                    lambda task : force_touch(task.config.out_obj),
                ],
                in_temp=dummy,
                in_src="src/test.cpp",
                out_obj="result.txt",
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

    # ----------------------------------------------------------------------------------------------

    # FIXME need a test that checks that a task with no outputs always rebuilds

#    def test_no_output_always_rebuilds(self):
#        task = hancho.Task()

    # FIXME test_command_changed
    def test_command_changed(self):
        pass

    # FIXME how the hell do we test size changed / hash changed while _not_ changing the mtime?

    # ----------------------------------------------------------------------------------------------

    def test_missing_input(self):
        # We should fail if an input is missing
        task = hancho.Task(
            desc="Should fail due to missing input",
            command = lambda task : force_touch(task.config.out_obj),
            in_src="src/does_not_exist.txt",
            out_obj="missing_src.txt",
        )
        self.run_tasks(1)
        self.assertIsInstance(task._error, hancho.Task.BROKEN)

    def test_missing_dep(self):
        # We should fail if a dependency is missing even if it's not used by the command.
        task = hancho.Task(
            desc="Missing dep should fail",
            command = lambda task : force_touch(task.config.out_obj),
            in_src="src/test.cpp",
            in_dep=["missing_dep.txt"],
            out_obj="result.txt",
        )
        self.run_tasks(1)
        self.assertIsInstance(task._error, hancho.Task.BROKEN)

    # ----------------------------------------------------------------------------------------------

    def test_absolute_inputs(self):
        # If input filenames are absolute paths, we should still end up with build files under
        # build_root.

        hancho.Task(
            desc="In_src is absolute path",
            #command="cp {in_src} {out_obj}",
            command = lambda task : shutil.copy(task.config.in_src, task.config.out_obj),
            in_src=os.path.abspath("src/foo.c"),
            out_obj="{ext(in_src, '.o')}",
        )

        self.assertFalse(Path("build/src/foo.o").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/src/foo.o").exists())

    # ----------------------------------------------------------------------------------------------

    def test_does_create_output(self):
        # Output files should appear in build/ by default
        hancho.Task(
            command = lambda task : force_touch(task.config.out_obj),
            in_src=[],
            out_obj="result.txt",
        )
        self.assertFalse(os.path.exists("build/result.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/result.txt"))

    def test_doesnt_create_output(self):
        # Having a file mentioned in out_obj should not magically create it
        bad_task = hancho.Task(
            command="echo test_doesnt_create_output >> {out_txt}",
            in_src=[],
            out_txt="blarp.txt",
            out_obj="result.txt",
        )
        self.assertFalse(os.path.exists("build/result.txt"))
        self.assertFalse(os.path.exists("build/blarp.txt"))
        self.run_tasks(1)
        self.assertIsInstance(bad_task._error, hancho.Task.FAILED)
        self.assertFalse(os.path.exists("build/result.txt"))
        self.assertTrue(os.path.exists("build/blarp.txt"))

    @unittest.skipUnless(sys.platform.startswith('linux'), "Requires Linux")
    def test_header_changed_linux(self):
        if not sys.platform.startswith('linux'):
            return

        # Changing a header file tracked in the GCC dependencies file should trigger a rebuild
        def run():
            hancho.init(verbosity = VERBOSITY)
            time.sleep(0.01)
            compile = hancho.Tool(
                desc="test_header_changed {in_src}",
                command="gcc -MMD -c {in_src} -o {out_obj}",
                in_depfile="{ext(out_obj, '.d')}",
                out_obj="{ext(in_src, '.o')}",
            )
            hancho.Task(compile, in_src="src/test.cpp")
            self.run_tasks(0)
            return mtime_ns("build/src/test.o")

        mtime1 = run()
        mtime2 = run()
        force_touch("src/test.hpp")
        mtime3 = run()

        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)

    @unittest.skipUnless(sys.platform.startswith('win32'), "Requires Windows _and_ a developer prompt")
    def test_header_changed_windows(self):
        # Changing a header file tracked in the GCC dependencies file should trigger a rebuild
        def run():
            hancho.init(verbosity = VERBOSITY) #type:ignore
            time.sleep(0.01)
            compile = hancho.Tool(
                desc="test_header_changed {in_src}",
                command="cl.exe /nologo /c {in_src} /sourceDependencies {in_depfile} /Fo:{out_obj}",
                in_depfile="{ext(out_obj, '.d')}",
                out_obj="{ext(in_src, '.o')}",
                depformat="msvc",
            )
            hancho.Task(compile, in_src="src/test.cpp")
            self.run_tasks(0)
            return mtime_ns("build/src/test.o")

        mtime1 = run()
        mtime2 = run()
        force_touch("src/test.hpp")
        mtime3 = run()

        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)


    def test_multiple_depfiles(self):
        # Creating a task with multiple depfile inputs should fail.
        bad_task = hancho.Task(command="echo test_multiple_depfiles", in_depfile=["foo.txt", "bar.txt"])
        self.run_tasks(1)
        self.assertIsInstance(bad_task._error, hancho.Task.BROKEN)

    def test_multiple_commands(self):
        # Rules with arrays of commands should run all of them
        hancho.Task(
            command=[
                "echo foo > {out_foo}",
                "echo bar > {out_bar}",
                "echo baz > {out_baz}",
            ],
            out_foo="foo.txt",
            out_bar="bar.txt",
            out_baz="baz.txt",
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
        hancho.init(verbosity = VERBOSITY, flarpy="flarp.txt")
        self.assertEqual("flarp.txt", hancho.config.flarpy)

        hancho.Task(
            command = lambda task : force_touch(task.config.out_file),
            source_files=[],
            out_file="{flarpy}",
        )
        self.assertFalse(os.path.exists("build/flarp.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/flarp.txt"))

    def test_sync_command(self):
        def sync_command(task):
            force_touch(task.config.out_obj)

        hancho.Task(
            name="result.txt",
            desc="The 'command' field of rules should be OK handling a sync function",
            command=sync_command,
            in_src=[],
            out_obj="{name}",
        )
        self.assertFalse(os.path.exists("build/result.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/result.txt"))

    def test_lambda_command(self):
        hancho.Task(
            name="result.txt",
            desc="The 'command' field of rules should be OK handling a lambda",
            command=lambda task: force_touch(task.config.out_obj),
            in_src=[],
            out_obj="{name}",
        )
        self.assertFalse(os.path.exists("build/result.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/result.txt"))

    def test_sync_callback(self):
        def sync_callback(task):
            time.sleep(0.1)
            force_touch(task.config.out_file)

        hancho.Task(command=sync_callback, out_file="test_sync_callback.txt")
        self.assertFalse(Path("build/test_sync_callback.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/test_sync_callback.txt").exists())

    def test_sync_callback_raises(self):
        def sync_callback_raises(_):
            time.sleep(0.1)
            raise ValueError("I do not like value.")

        task = hancho.Task(command=sync_callback_raises, out_file="test_sync_callback_raises.txt")
        self.run_tasks(1)
        self.assertIsInstance(task._error, ValueError)
        self.assertFalse(Path("build/test_sync_callback_raises.txt").exists())

    def test_async_callback(self):
        async def async_callback(task):
            await asyncio.sleep(0.1)
            force_touch(task.config.out_file)

        hancho.Task(command=async_callback, out_file="test_async_callback.txt")
        self.assertFalse(Path("build/test_async_callback.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/test_async_callback.txt").exists())

    def test_async_callback_raises(self):
        async def async_callback_raises(_):
            await asyncio.sleep(0.1)
            raise ValueError("I do not like value.")

        task = hancho.Task(command=async_callback_raises, out_file="test_async_callback_raises.txt")
        self.run_tasks(1)
        self.assertIsInstance(task._error, ValueError)
        self.assertFalse(Path("build/test_async_callback_raises.txt").exists())

    def test_cancellation(self):
        # A task that receives a cancellation exception should not run.
        task_that_fails = hancho.Task(
            desc="task that fails",
            command="(exit 255)",
            in_src=[],
            out_obj="fail_result.txt",
        )
        task_that_passes = hancho.Task(
            desc="task that passes",
            command = lambda task : force_touch(task.config.out_obj),
            in_src=[],
            out_obj="pass_result.txt",
        )
        should_be_cancelled = hancho.Task(
            desc="should be cancelled",
            command = lambda task : force_touch(task.config.out_obj),
            in_src=[task_that_fails, task_that_passes],
            out_obj="should_not_be_created.txt",
        )
        self.assertFalse(os.path.exists("build/pass_result.txt"))
        self.run_tasks(1)

        self.assertIsInstance(task_that_fails._error, hancho.Task.FAILED)
        self.assertIsNone(task_that_passes._error)
        self.assertIsInstance(should_be_cancelled._error, hancho.Task.CANCELLED)

        self.assertEqual(1, hancho.Runner.tasks_cancelled)
        self.assertEqual(1, hancho.Runner.tasks_failed)
        self.assertEqual(1, hancho.Runner.tasks_finished)

        self.assertTrue(os.path.exists("build/pass_result.txt"))
        self.assertFalse(os.path.exists("build/fail_result.txt"))
        self.assertFalse(os.path.exists("build/should_not_be_created.txt"))

    def test_no_mixed_commands(self):
        bad_task = hancho.Task(
            command=["echo test_no_mixed_commands", lambda task: print(f"test_no_mixed_commands {type(task)}")]
        )

        self.run_tasks(1)
        self.assertIsInstance(bad_task._error, hancho.Task.BROKEN)

    def test_task_creates_task(self):
        # Tasks using callbacks can create new tasks when they run.
        def callback(task):
            hancho.Task(command = lambda task : force_touch(task.config.out_obj), in_src=[], out_obj="dummy.txt")
            return []

        hancho.Task(command=callback, in_src=[], out_obj=[])

        self.assertFalse(Path("build/dummy.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/dummy.txt").exists())

    # This is really slow on Windows for some reason - takes 10 secondss.
    @unittest.skipUnless(os.name == "posix", "requires Linux")
    def test_tons_of_tasks(self):
        # We should be able to queue up 1000+ tasks at once.
        for i in range(1000):
            hancho.Task(
                desc="I am task {index}",
                command="echo {index} > {out_obj}",
                in_src=[],
                out_obj="dummy{index}.txt",
                index=i,
            )
        self.assertEqual(0, len(glob.glob("build/dummy*.txt")))
        self.run_tasks(0)
        self.assertEqual(1000, len(glob.glob("build/dummy*.txt")))

    # This one takes about a second on Windows
    def test_job_count(self):
        # We should be able to dispatch tasks that require various numbers of jobs/cores.
        # Queues up 100 tasks that use random numbers of cores, then a "Job Hog" that uses all cores, then
        # another batch of 100 tasks that use random numbers of cores.

        for i in range(100):
            hancho.Task(
                desc="I am task {index}, I use {job_count} cores",
                command="(exit 0)",
                in_src=[],
                out_obj=[],
                job_count=random.randrange(1, cast(int, os.cpu_count()) + 1),
                index=i,
            )

        hancho.Task(
            desc="********** I am the slow task, I eat all the cores **********",
            command=[
                lambda task : force_touch(task.config.out_obj),
                lambda task : time.sleep(0.3)
            ],
            job_count=os.cpu_count(),
            in_src=[],
            out_obj="slow_result.txt",
        )

        for i in range(100):
            hancho.Task(
                desc="I am task {index}, I use {job_count} cores",
                command="(exit 0)",
                in_src=[],
                out_obj=[],
                job_count=random.randrange(1, cast(int, os.cpu_count()) + 1),
                index=100 + i,
            )

        self.assertFalse(Path("build/slow_result.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/slow_result.txt").exists())

    def test_dry_run(self):
        hancho.init(verbosity = VERBOSITY, max_errors=999, dry_run = True)
        task1 = hancho.Task(
            command = "echo foo >> {out_file}",
            out_file = "dry_stuff/test1.txt",
        )
        hancho.Task(
            command = "cp {in_file} {out_file}",
            in_file = task1,
            out_file = "dry_stuff/test2.txt"
        )
        self.assertFalse(Path("build").exists())
        self.run_tasks(0)
        self.assertFalse(Path("build").exists())

    def test_dependency_skipped(self):
        def run():
            hancho.init(verbosity = VERBOSITY, core_max=1)
            task1 = hancho.Task(
                name="task1",
                #command="cp {in_file} {out_file}",
                command = lambda task : shutil.copy(task.config.in_file, task.config.out_file),
                in_file="data/dummy.txt",
                out_file="blerp/sherp",
            )
            task2 = hancho.Task(
                name="task2",
                #command="cp {in_file} {out_file}",
                command = lambda task : shutil.copy(task.config.in_file, task.config.out_file),
                in_file=task1,
                out_file="blerp/nerp",
                rebuild=True,
            )
            self.run_tasks(0)
            return (task1, task2)

        self.assertFalse(Path("build/blerp/sherp").exists())
        self.assertFalse(Path("build/blerp/nerp").exists())
        (task1, task2) = run()
        self.assertTrue(task1._error is None)
        self.assertTrue(task2._error is None)
        self.assertTrue(Path("build/blerp/sherp").exists())
        self.assertTrue(Path("build/blerp/nerp").exists())
        mtime1a = mtime_ns("build/blerp/sherp")
        mtime2a = mtime_ns("build/blerp/nerp")

        self.assertEqual(hancho.Runner.tasks_finished, 2)
        self.assertEqual(hancho.Runner.tasks_broken, 0)
        self.assertEqual(hancho.Runner.tasks_failed, 0)
        self.assertEqual(hancho.Runner.tasks_cancelled, 0)
        self.assertEqual(hancho.Runner.tasks_skipped, 0)

        (task1, task2) = run()
        self.assertTrue(isinstance(task1._error, hancho.Task.SKIPPED))
        self.assertTrue(task2._error is None)
        self.assertTrue(Path("build/blerp/sherp").exists())
        self.assertTrue(Path("build/blerp/nerp").exists())
        mtime1b = mtime_ns("build/blerp/sherp")
        mtime2b = mtime_ns("build/blerp/nerp")
        self.assertEqual(mtime1a, mtime1b) # first task clean, should be skipped
        self.assertLess(mtime2a, mtime2b)  # second task always rebuilds and the skipped task shouldn't stop it.

        self.assertEqual(hancho.Runner.tasks_finished, 1)
        self.assertEqual(hancho.Runner.tasks_broken, 0)
        self.assertEqual(hancho.Runner.tasks_failed, 0)
        self.assertEqual(hancho.Runner.tasks_cancelled, 0)
        self.assertEqual(hancho.Runner.tasks_skipped, 1)

####################################################################################################

if __name__ == "__main__":
    unittest.main(verbosity=999)
