import asyncio
import glob
import os
import random
import shutil
import sys
import time
import unittest
from pathlib import Path
from typing import Any, NoReturn, cast

import hancho

# FIXME we need some tests that verify that using a shared data structure in multiple tasks
# (see gcc_flags) does _not_ modify it even if it contains template stuff.

cvg: hancho.CVGlobals = hancho.cvg

# --------------------------------------------------------------------------------------------------

def mtime_ns(filename : str) -> int:
    return os.stat(filename).st_mtime_ns

def force_touch(filename : str, append_text  : str | None = None) -> None:
    if isinstance(filename, list):
        (force_touch(f, append_text) for f in filename)

    if not Path(filename).exists():
        Path(filename).touch()

    if append_text:
        with open(filename, "a") as f:
            f.write(append_text)
    old_mtime: int = mtime_ns(filename)
    while old_mtime == mtime_ns(filename):
        os.utime(filename, None)

# --------------------------------------------------------------------------------------------------

class TestTasks(unittest.TestCase):

    def setUp(self, *args : Any) -> None:
        # Always wipe the build dir before a test, but make sure we're in the right dir.
        os.chdir(os.path.dirname(__file__))
        assert os.getcwd().endswith(os.sep + "tests")
        shutil.rmtree("build", ignore_errors=True)

        self.reset(*args)

    def tearDown(self) -> None:
        cvg.log._flush()

    def reset(self, *args : Any) -> None:
        self.script: hancho.Script = hancho.create_test_script(
            in_file = __file__,
            stdout = sys.stdout,
            extra_flags = [
                "--log_level=critical",
                #"--log_level=debug",
                #"--log_level=trace",
                "--log_wrap=True",
                "--max_errors=999",
                *args
            ],
        )

    def run_tasks(self, expected : int) -> None:
        result: int = hancho.build()
        self.assertEqual(result, expected)

    def test_trivial(self) -> None:
        self.assertIsNotNone(hancho)

    def test_basic(self) -> None:
        # If all tasks are OK, we should get 0 from run_tasks.
        good_task = hancho.Task(command="echo test_run_tasks_zero")
        self.run_tasks(0)
        self.assertIsNone(good_task._error)

    # ----------------------------------------------------------------------------------------------

    def test_run_failing_command(self) -> None:
        # If any task fails, we should get -1 from run_tasks.
        bad_task = hancho.Task(command="echo test_run_tasks_zero && (exit 255)")
        self.run_tasks(1)
        self.assertIsInstance(bad_task._error, hancho.Task.FAILED) #type:ignore

    def test_asyncio_cancelled(self) -> None:
        def asyncio_cancelled(_ : Any) -> NoReturn:
            time.sleep(0.1)
            raise asyncio.CancelledError()

        hancho.Task(command=asyncio_cancelled, out_file="asyncio_cancelled.txt")
        self.assertEqual(0, hancho.cvg.stats.tasks_cancelled)
        self.run_tasks(0)
        self.assertEqual(1, hancho.cvg.stats.tasks_cancelled)

    # ----------------------------------------------------------------------------------------------

    def test_good_build_path(self) -> None:
        good_task = hancho.Task(
            command="echo {in_src} >> {out_obj}",
            in_src="src/foo.c",
            out_obj="{repo.root_b}/narp/foo.o",
        )
        self.assertFalse(Path("build/narp/foo.o").exists())
        self.run_tasks(0)
        self.assertIsNone(good_task._error)
        self.assertTrue(Path("build/narp/foo.o").exists())

    def test_bad_build_path(self) -> None:
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
    def test_good_run_cmd(self) -> None:

        command : str = r"echo I am runnning in {run_cmd('pwd')}" if os.name == 'posix' else r"echo I am runnning in {run_cmd('cd')}"

        task = hancho.Task(
            desc="Testing run_cmd",
            command=command,
        )
        self.run_tasks(0)
        self.assertEqual(repr(f"I am runnning in {os.getcwd()}"), repr(task._stdout_text.strip()))

    def test_bad_run_cmd(self) -> None:
        # Trying to run an arbitrary command and use it in a template should report BROKEN if the
        # embedded command is invalid.
        task = hancho.Task(
            desc="Broken run_cmd",
            command=r"echo {run_cmd('This is totally not a valid command')}",
        )
        self.run_tasks(1)
        self.assertIsInstance(task._error, hancho.Task.BROKEN)

    def test_unexpandable_command(self) -> None:
        #Commands that have residual braces after expansion should be reported as broken but ONLY
        #if we are in 'strict' mode.
        task = hancho.Task(
            desc="Unexpandable command",
            command=r"echo Hello {missing} world!",
        )
        self.run_tasks(1)
        self.assertIsInstance(task._error, hancho.Task.BROKEN)

    def test_garbage_command(self) -> None:
        # Non-existent command line commands should cause Hancho to fail the build.
        garbage_task = hancho.Task(
            command="aklsjdflksjdlfkjldfk",
        )
        self.run_tasks(1)
        self.assertIsInstance(garbage_task._error, hancho.Task.FAILED)

    def test_missing_command(self) -> None:
        # Tasks with no commands are fine, they can still be used to group and coordinate other
        # tasks.
        hancho.Task(not_a_command="echo test_missing_command")
        self.run_tasks(0)

    def test_task_collision(self) -> None:
        # If multiple distinct commands generate the same output file, that's an error.
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

    def test_always_rebuild_if_no_inputs(self) -> None:
        # A rule with no inputs should always rebuild
        # This test is flaky without the "sleep 0.1" because of filesystem mtime granularity

        def run() -> int:
            self.reset()
            hancho.Task(
                command= lambda task : (time.sleep(0.1), force_touch(task.out_obj)),
                in_src=[],
                out_obj="result.txt",
            )
            self.run_tasks(0)
            return mtime_ns("build/result.txt")

        mtime1 = run()
        mtime2: int = run()
        self.assertLess(mtime1, mtime2)
        mtime3: int = run()
        self.assertLess(mtime2, mtime3)

    def test_input_changed(self) -> None:
        # Changing a source file should trigger a rebuild
        def run() -> int:
            self.reset()
            time.sleep(0.01)
            compile = hancho.Dict(
                desc="test_input_changed {in_src}",
                command = lambda task : shutil.copy(task.in_src, task.out_obj),
                in_src=None,
                out_obj="{swapext(in_src, '.o')}",
            )
            hancho.Task(compile, in_src="src/test.cpp")
            self.run_tasks(0)
            return mtime_ns("build/src/test.o")

        mtime1 = run()
        mtime2 = run()
        self.assertEqual(mtime1, mtime2)

        force_touch("src/test.cpp")
        mtime3 = run()

        self.assertLess(mtime2, mtime3)


    def test_dep_changed(self) -> None:
        # Changing a file in in_files[] should trigger a rebuild, even if it isn't used by the
        # command.
        # This test is flaky without the "sleep 0.1" because of filesystem mtime granularity

        dummy = "data/dummy.txt"

        def run() -> int:
            self.reset()
            hancho.Task(
                desc="test_dep_changed {in_src}",
                #command="sleep 0.1 && touch {out_obj}",
                command = lambda task : (time.sleep(0.1),force_touch(task.out_obj)),
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

    def test_command_changed(self) -> None:
        self.reset()
        hancho.Task(
            command="echo foo{in_src} > {out_txt}",
            in_src = "src/test.cpp",
            out_txt="test_command_changed.txt",
        )
        self.assertFalse(Path("build/test_command_changed.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/test_command_changed.txt").exists())
        mtime1 = mtime_ns("build/test_command_changed.txt")

        self.reset()
        hancho.Task(
            command="echo foo{in_src} > {out_txt}",
            in_src = "src/test.cpp",
            out_txt="test_command_changed.txt",
        )
        self.run_tasks(0)
        mtime2 = mtime_ns("build/test_command_changed.txt")

        self.reset()
        hancho.Task(
            command="echo bar{in_src} > {out_txt}",
            in_src = "src/test.cpp",
            out_txt="test_command_changed.txt",
        )
        self.run_tasks(0)
        mtime3 = mtime_ns("build/test_command_changed.txt")

        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)

    # ----------------------------------------------------------------------------------------------

    def test_missing_input(self) -> None:
        # We should fail if an input is missing
        task = hancho.Task(
            desc="Should fail due to missing input",
            command = lambda task : force_touch(task.out_obj),
            in_src="src/does_not_exist.txt",
            out_obj="missing_src.txt",
        )
        self.run_tasks(1)
        self.assertIsInstance(task._error, hancho.Task.BROKEN)

    def test_missing_dep(self) -> None:
        # We should fail if a dependency is missing even if it's not used by the command.
        task = hancho.Task(
            desc="Missing dep should fail",
            command = lambda task : force_touch(task.out_obj),
            in_src="src/test.cpp",
            in_dep=["missing_dep.txt"],
            out_obj="result.txt",
        )
        self.run_tasks(1)
        self.assertIsInstance(task._error, hancho.Task.BROKEN)

    # ----------------------------------------------------------------------------------------------

    def test_absolute_inputs(self) -> None:
        # If input filenames are absolute paths, we should still end up with build files under
        # build_root.

        hancho.Task(
            desc="In_src is absolute path",
            #command="cp {in_src} {out_obj}",
            command = lambda task : shutil.copy(task.in_src, task.out_obj),
            in_src=os.path.abspath("src/foo.c"),
            out_obj="{swapext(in_src, '.o')}",
        )

        self.assertFalse(Path("build/src/foo.o").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/src/foo.o").exists())

    # ----------------------------------------------------------------------------------------------

    def test_does_create_output(self) -> None:
        # Output files should appear in build/ by default
        hancho.Task(
            command = lambda task : force_touch(task.out_obj),
            in_src=[],
            out_obj="result.txt",
        )
        self.assertFalse(os.path.exists("build/result.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/result.txt"))

    def test_doesnt_create_output(self) -> None:
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

    def test_header_changed(self) -> None:
        # Changing a header file tracked in the GCC dependencies file should trigger a rebuild

        if os.name == 'posix':
            command="gcc -MMD -c {in_src} -o {out_obj}"
            depformat="gcc"
        elif os.name == 'nt':
            command="cl.exe /nologo /c {in_src} /sourceDependencies {in_depfile} /Fo:{out_obj}",
            depformat="msvc"
        else:
            raise AssertionError("Don't know this platform")

        def run() -> int:
            self.reset()
            time.sleep(0.01)
            compile = hancho.Tool(
                desc="test_header_changed {in_src}",
                command=command,
                in_depfile="{swapext(out_obj, '.d')}",
                out_obj="{swapext(in_src, '.o')}",
                depformat=depformat,
            )
            compile(in_src="src/test.cpp")
            self.run_tasks(0)
            return mtime_ns("build/src/test.o")

        mtime1 = run()
        mtime2 = run()
        force_touch("src/test.hpp")
        mtime3 = run()

        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)


    def test_multiple_depfiles(self) -> None:
        # Creating a task with multiple depfile inputs should fail.
        bad_task = hancho.Task(command="echo test_multiple_depfiles", in_depfile=["foo.txt", "bar.txt"])
        self.run_tasks(1)
        self.assertIsInstance(bad_task._error, hancho.Task.BROKEN)

    def test_multiple_commands(self) -> None:
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

    def test_arbitrary_flags(self) -> None:
        # Passing arbitrary flags to Hancho should work
        self.reset(
            "--flarp=flarp.txt",
            "--gnarp=1",
            "--snarp=True"
        )

        def callback(task : hancho.Task) -> None:
            force_touch(task.out_file) # type: ignore

        t = hancho.Task(
            name = "test_arbitrary_flags",
            desc = "yes it's a test case",
            command = callback,
            in_files=[],
            out_file="{flags.flarp}",
        )
        self.assertEqual(hancho.Expander(t).flarp, "flarp.txt")
        self.assertEqual(hancho.Expander(t).gnarp, 1)
        self.assertEqual(hancho.Expander(t).snarp, True)

        self.assertFalse(os.path.exists("build/flarp.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/flarp.txt"))

    def test_sync_command(self) -> None:
        def sync_command(task : hancho.Task) -> None:
            force_touch(task.out_obj)

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

    def test_lambda_command(self) -> None:
        hancho.Task(
            name="result.txt",
            desc="The 'command' field of rules should be OK handling a lambda",
            command=lambda task: force_touch(task.out_obj),
            in_src=[],
            out_obj="{name}",
        )
        self.assertFalse(os.path.exists("build/result.txt"))
        self.run_tasks(0)
        self.assertTrue(os.path.exists("build/result.txt"))

    def test_sync_callback(self) -> None:
        def sync_callback(task : hancho.Task) -> None:
            time.sleep(0.1)
            force_touch(task.out_file)

        hancho.Task(command=sync_callback, out_file="test_sync_callback.txt")
        self.assertFalse(Path("build/test_sync_callback.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/test_sync_callback.txt").exists())

    def test_sync_callback_raises(self) -> None:
        def sync_callback_raises(_ : hancho.Task) -> None:
            time.sleep(0.1)
            raise ValueError("I do not like value.")

        task = hancho.Task(command=sync_callback_raises, out_file="test_sync_callback_raises.txt")
        self.run_tasks(1)
        self.assertIsInstance(task._error, ValueError)
        self.assertFalse(Path("build/test_sync_callback_raises.txt").exists())

    def test_async_callback(self) -> None:
        async def async_callback(task : hancho.Task) -> None:
            await asyncio.sleep(0.1)
            force_touch(task.out_file)

        hancho.Task(command=async_callback, out_file="test_async_callback.txt")
        self.assertFalse(Path("build/test_async_callback.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/test_async_callback.txt").exists())

    def test_async_callback_raises(self) -> None:
        async def async_callback_raises(_ : hancho.Task) -> None:
            await asyncio.sleep(0.1)
            raise ValueError("I do not like value.")

        task = hancho.Task(command=async_callback_raises, out_file="test_async_callback_raises.txt")
        self.run_tasks(1)
        self.assertIsInstance(task._error, ValueError)
        self.assertFalse(Path("build/test_async_callback_raises.txt").exists())

    def test_cancellation(self) -> None:
        # A task that receives a cancellation exception should not run.
        task_that_fails = hancho.Task(
            desc="task that fails",
            command="(exit 255)",
            in_src=[],
            out_obj="fail_result.txt",
        )
        task_that_passes = hancho.Task(
            desc="task that passes",
            command = lambda task : force_touch(task.out_obj),
            in_src=[],
            out_obj="pass_result.txt",
        )
        should_be_cancelled = hancho.Task(
            desc="should be cancelled",
            command = lambda task : force_touch(task.out_obj),
            in_src=[task_that_fails, task_that_passes],
            out_obj="should_not_be_created.txt",
        )
        self.assertFalse(os.path.exists("build/pass_result.txt"))
        self.run_tasks(1)

        self.assertIsInstance(task_that_fails._error, hancho.Task.FAILED)
        self.assertIsNone(task_that_passes._error)
        self.assertIsInstance(should_be_cancelled._error, hancho.Task.CANCELLED)

        self.assertEqual(1, hancho.cvg.stats.tasks_cancelled)
        self.assertEqual(1, hancho.cvg.stats.tasks_failed)
        self.assertEqual(1, hancho.cvg.stats.tasks_finished)

        self.assertTrue(os.path.exists("build/pass_result.txt"))
        self.assertFalse(os.path.exists("build/fail_result.txt"))
        self.assertFalse(os.path.exists("build/should_not_be_created.txt"))

    def test_no_mixed_commands(self) -> None:
        bad_task = hancho.Task(
            command = [
                "echo test_no_mixed_commands",
                lambda task: print(f"test_no_mixed_commands {type(task)}")
            ]
        )

        self.run_tasks(1)
        self.assertIsInstance(bad_task._error, hancho.Task.BROKEN)

    def test_task_creates_task(self) -> None:
        # Tasks using callbacks can create new tasks when they run.

        def callback2(task : hancho.Task) -> None:
            force_touch(task.out_obj)

        def callback1(_ : hancho.Task) -> None:
            hancho.Task(command = callback2, in_src=[], out_obj="dummy.txt")

        hancho.Task(command=callback1, in_src=[], out_obj=[])

        self.assertFalse(Path("build/dummy.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/dummy.txt").exists())

    # This is really slow on Windows for some reason - takes 10 secondss.
    @unittest.skipUnless(os.name == "posix", "requires Linux")
    def test_tons_of_tasks(self) -> None:
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
    def test_jobs(self) -> None:
        # We should be able to dispatch tasks that require various numbers of jobs/cores.
        # Queues up 100 tasks that use random numbers of cores, then a "Job Hog" that uses all cores, then
        # another batch of 100 tasks that use random numbers of cores.

        for i in range(100):
            hancho.Task(
                desc="I am task {index}, I use {job_size} cores",
                command="(exit 0)",
                in_src=[],
                out_obj=[],
                job_size=random.randrange(1, cast(int, os.cpu_count()) + 1),
                index=i,
            )

        hancho.Task(
            desc="********** I am the slow task, I eat all the cores **********",
            command=lambda task : (force_touch(task.out_obj), time.sleep(0.3)),
            job_size=os.cpu_count(),
            in_src=[],
            out_obj="slow_result.txt",
        )

        for i in range(100):
            hancho.Task(
                desc="I am task {index}, I use {job_size} cores",
                command="(exit 0)",
                in_src=[],
                out_obj=[],
                job_size=random.randrange(1, cast(int, os.cpu_count()) + 1),
                index=100 + i,
            )

        self.assertFalse(Path("build/slow_result.txt").exists())
        self.run_tasks(0)
        self.assertTrue(Path("build/slow_result.txt").exists())

    def test_dry_run(self) -> None:
        self.reset("--build_dry=True")
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

    def test_dependency_skipped(self) -> None:
        def run() -> tuple[hancho.Task, hancho.Task]:
            self.reset("--max_jobs=1")
            task1 = hancho.Task(
                name="task1",
                #command="cp {in_file} {out_file}",
                command = lambda task : shutil.copy(task.in_file, task.out_file),
                in_file="data/dummy.txt",
                out_file="blerp/sherp",
            )
            task2 = hancho.Task(
                name="task2",
                #command="cp {in_file} {out_file}",
                command = lambda task : shutil.copy(task.in_file, task.out_file),
                in_file=task1,
                out_file="blerp/nerp",
                build_force=True,
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

        self.assertEqual(hancho.cvg.stats.tasks_finished, 2)
        self.assertEqual(hancho.cvg.stats.tasks_broken, 0)
        self.assertEqual(hancho.cvg.stats.tasks_failed, 0)
        self.assertEqual(hancho.cvg.stats.tasks_cancelled, 0)
        self.assertEqual(hancho.cvg.stats.tasks_skipped, 0)

        (task1, task2) = run()
        self.assertTrue(task1._skipped)
        self.assertTrue(task2._error is None)
        self.assertTrue(Path("build/blerp/sherp").exists())
        self.assertTrue(Path("build/blerp/nerp").exists())
        mtime1b = mtime_ns("build/blerp/sherp")
        mtime2b = mtime_ns("build/blerp/nerp")
        self.assertEqual(mtime1a, mtime1b) # first task clean, should be skipped
        self.assertLess (mtime2a, mtime2b)  # second task always rebuilds and the skipped task shouldn't stop it.

        self.assertEqual(hancho.cvg.stats.tasks_finished, 1)
        self.assertEqual(hancho.cvg.stats.tasks_broken, 0)
        self.assertEqual(hancho.cvg.stats.tasks_failed, 0)
        self.assertEqual(hancho.cvg.stats.tasks_cancelled, 0)
        self.assertEqual(hancho.cvg.stats.tasks_skipped, 1)

# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=999)
