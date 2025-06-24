#!/usr/bin/python3
"""Test cases for Hancho"""

import sys
import os
from os import path
import random
#import subprocess
import unittest
import shutil
import glob
from pathlib import Path
import time

sys.path.append("..")
import hancho as hancho_py

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

class TestContext(unittest.TestCase):
    """Test cases for weird things our Context objects can do"""

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
        hancho_py.app.reset()
        hancho_py.app.parse_flags(["--quiet"])
        #hancho_py.app.parse_flags([])
        #hancho_py.app.parse_flags(["-v"])
        #hancho_py.app.parse_flags(["-d"])
        self.hancho = hancho_py.app.create_root_context()

    ########################################

    def tearDown(self):
        """And wipe the build dir after a test too."""
        shutil.rmtree("build", ignore_errors=True)

    ########################################

    def test_dummy(self):
        self.assertEqual(0, 0)

    ########################################

    def test_log(self):
        hancho_py.app.reset()
        hancho_py.log("")

    ########################################

    def test_run_cmd(self):
        #hancho_py.app.reset()
        #self.hancho = hancho_py.app.create_root_context()
        task = self.hancho(command = "echo \'{run_cmd('ls')}\'")
        self.assertEqual(0, self.hancho.app.build_all())

    ########################################

    def test_should_pass(self):
        """Sanity check"""
        self.hancho(command = "(exit 0)")
        self.assertEqual(0, hancho_py.app.build_all())

    ########################################

    def test_should_fail(self):
        """Sanity check"""
        bad_task = self.hancho(command = "echo skldjlksdlfj && (exit 255)")
        self.assertNotEqual(0, hancho_py.app.build_all())
        self.assertEqual(bad_task._state, hancho_py.TaskState.FAILED)

    ########################################

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
        self.hancho(
            command  = "touch {rel(out_obj)}",
            in_src   = "src/foo.c",
            out_obj  = "{repo_dir}/build/narp/foo.o",
        )
        self.assertEqual(0, hancho_py.app.build_all())
        self.assertTrue(Path("build/narp/foo.o").exists())

    ########################################

    def test_bad_build_path(self):
        bad_task = self.hancho(
            command  = "touch {rel(out_obj)}",
            in_src   = "src/foo.c",
            out_obj  = "{repo_dir}/../build/foo.o",
        )
        self.assertNotEqual(0, hancho_py.app.build_all())
        self.assertEqual(bad_task._state, hancho_py.TaskState.BROKEN)
        self.assertFalse(Path("build/foo.o").exists())

    ########################################

    def test_raw_task(self):
        self.hancho.Task(
            command    = "touch {rel(out_obj)}",
            in_src     = "src/foo.c",
            out_obj    = "foo.o",
            repo_dir   = os.getcwd(),
            task_dir   = ".",
            build_dir = "build"
        )
        #print(task)
        self.assertEqual(0, hancho_py.app.build_all())
        self.assertTrue(Path("build/foo.o").exists())

    ########################################

    def test_missing_input(self):
        """We should fail if an input is missing"""
        bad_task = self.hancho(
            command = "touch {rel(out_obj)}",
            in_src  = "src/does_not_exist.txt",
            out_obj = "missing_src.txt"
        )
        self.assertNotEqual(0, hancho_py.app.build_all())
        self.assertEqual(bad_task._state, hancho_py.TaskState.BROKEN)
        self.assertTrue("FileNotFoundError" in hancho_py.app.log)
        self.assertTrue("does_not_exist.txt" in hancho_py.app.log)

    ########################################

    def test_absolute_inputs(self):
        """
        If input filenames are absolute paths, we should still end up with build files under
        build_root.
        """

        self.hancho(
            command = "cp {in_src} {out_obj}",
            in_src  = path.abspath("src/foo.c"),
            out_obj = "{ext(in_src, '.o')}",
        )

        self.assertEqual(0, hancho_py.app.build_all())
        self.assertTrue(Path("build/src/foo.o").exists())


    ########################################

    def test_missing_dep(self):
        """Missing dep should fail"""
        bad_task = self.hancho(
            command = "touch {rel(out_obj)}",
            in_src  = "src/test.cpp",
            in_dep  = ["missing_dep.txt"],
            out_obj = "result.txt",
        )
        self.assertNotEqual(0, hancho_py.app.build_all())
        self.assertEqual(bad_task._state, hancho_py.TaskState.BROKEN)
        self.assertTrue("FileNotFoundError" in hancho_py.app.log)
        self.assertTrue("missing_dep.txt" in hancho_py.app.log)

    ########################################
    # A recursive text template should cause an 'expand failed to terminate' error.

    """
    # FIXME after we're done redoing template expansion
    def test_expand_failed_to_terminate(self):
        # Single recursion
        bad_task = self.hancho(
            command = "{flarp}",
            in_src  = [],
            out_obj = [],
            flarp   = "asdf {flarp}",
        )
        self.assertNotEqual(0, hancho_py.app.build_all())
        self.assertEqual(bad_task._state, hancho_py.TaskState.BROKEN)
        self.assertTrue("TemplateRecursion" in hancho_py.app.log)

    def test_expand_failed_to_terminate2(self):
        # Mutual recursion
        bad_task = self.hancho(
            command = "{flarp}",
            in_src  = [],
            out_obj = [],
            flarp   = "{command}",
        )
        self.assertNotEqual(0, hancho_py.app.build_all())
        self.assertEqual(bad_task._state, hancho_py.TaskState.BROKEN)
        self.assertTrue("TemplateRecursion" in hancho_py.app.log)

    def test_expand_failed_to_terminate3(self):
        # Recursion via TXINAE
        #hancho_py.app.reset()
        print("lksjflskdflsdjk?")
        bad_task = self.hancho(
            command = "{subthing.foo}",
            in_src  = [],
            out_obj = [],
            subthing = dict(foo = "{subthing.foo} x"),
            #trace = True
        )
        self.assertNotEqual(0, hancho_py.app.build_all())
        self.assertEqual(bad_task._state, hancho_py.TaskState.BROKEN)
        self.assertTrue("TemplateRecursion" in hancho_py.app.log)
    """

    ########################################

    def test_nested_macros(self):
        c = hancho_py.DotDict(
            foo = "piece1",
            bar = "piece2",
            baz = "{ {foo}{bar} }",
            piece1piece2 = 1234
        )
        d = c.expand("{baz}")
        self.assertEqual(d, 1234)

    ########################################

    def test_garbage_command(self):
        """Non-existent command line commands should cause Hancho to fail the build."""
        garbage_task = self.hancho(
            command = "aklsjdflksjdlfkjldfk",
            in_src  = __file__,
            out_obj = "result.txt",
        )
        self.assertNotEqual(0, hancho_py.app.build_all())
        self.assertEqual(garbage_task._state, hancho_py.TaskState.FAILED)
        self.assertTrue("CommandFailure" in hancho_py.app.log)

    ########################################

    def test_task_collision(self):
        """If multiple distinct commands generate the same output file, that's an error."""
        self.hancho(
            command = "touch {rel(out_obj)}",
            in_src  = __file__,
            out_obj = "colliding_output.txt",
        )
        self.hancho(
            command = "touch {rel(out_obj)}",
            in_src  = __file__,
            out_obj = "colliding_output.txt",
        )
        self.assertNotEqual(0, hancho_py.app.build_all())
        self.assertTrue("TaskCollision" in hancho_py.app.log)

    ########################################

    def test_always_rebuild_if_no_inputs(self):
        """A rule with no inputs should always rebuild"""
        def run():
            hancho_py.app.reset()
            hancho_py.app.parse_flags(["--quiet"])
            self.hancho(
                command = "sleep 0.1 && touch {rel(out_obj)}",
                in_src  = [],
                out_obj = "result.txt",
            )
            self.assertEqual(0, hancho_py.app.build_all())
            return mtime_ns("build/result.txt")

        mtime1 = run()
        mtime2 = run()
        mtime3 = run()
        self.assertLess(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)

    ########################################

    def test_dep_changed(self):
        """Changing a file in in_files[] should trigger a rebuild"""
        # This test is flaky without the "sleep 0.1" because of filesystem mtime granularity
        def run():
            hancho_py.app.reset()
            hancho_py.app.parse_flags(["--quiet"])
            self.hancho(
                command = "sleep 0.1 && touch {rel(out_obj)}",
                in_temp = ["build/dummy.txt"],
                in_src  = "src/test.cpp",
                out_obj = "result.txt",
            )
            self.assertEqual(0, hancho_py.app.build_all())
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
        self.hancho(
            command = "touch {rel(out_obj)}",
            in_src  = [],
            out_obj = "result.txt",
        )
        self.assertEqual(0, hancho_py.app.build_all())
        self.assertTrue(path.exists("build/result.txt"))

    ########################################

    def test_doesnt_create_output(self):
        """Having a file mentioned in out_obj should not magically create it"""
        self.hancho(
            command = "echo",
            in_src  = [],
            out_obj = "result.txt"
        )
        self.assertEqual(0, hancho_py.app.build_all())
        self.assertFalse(path.exists("build/result.txt"))

    ########################################

    def test_header_changed(self):
        """Changing a header file tracked in the GCC dependencies file should trigger a rebuild"""
        def run():
            hancho_py.app.reset()
            hancho_py.app.parse_flags(["--quiet"])
            time.sleep(0.01)
            compile = dict(
                command = "gcc -MMD -c {rel(in_src)} -o {rel(out_obj)}",
                out_obj = "{ext(in_src, '.o')}",
                depfile = "{ext(out_obj, '.d')}",
            )
            self.hancho(compile, in_src = "src/test.cpp")
            self.assertEqual(0, hancho_py.app.build_all())
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
        def run():
            hancho_py.app.reset()
            hancho_py.app.parse_flags(["--quiet"])
            time.sleep(0.01)
            compile = dict(
                command = "gcc -MMD -c {rel(in_src)} -o {rel(out_obj)}",
                out_obj = "{ext(in_src, '.o')}",
                depfile = "{ext(out_obj, '.d')}",
            )
            self.hancho(compile, in_src = "src/test.cpp")
            self.assertEqual(0, hancho_py.app.build_all())
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
        self.hancho(
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

        self.assertEqual(0, hancho_py.app.build_all())
        self.assertTrue(path.exists("build/foo.txt"))
        self.assertTrue(path.exists("build/bar.txt"))
        self.assertTrue(path.exists("build/baz.txt"))

    ########################################

    def test_arbitrary_flags(self):
        """Passing arbitrary flags to Hancho should work"""
        hancho_py.app.reset()
        hancho_py.app.parse_flags(["--quiet", "--flarpy=flarp.txt"])
        self.hancho = hancho_py.app.create_root_context()
        self.assertEqual("flarp.txt", self.hancho.context.flarpy)

        self.hancho(
            command = "touch {out_file}",
            source_files = [],
            out_file = "{flarpy}",
        )
        self.assertEqual(0, hancho_py.app.build_all())
        self.assertTrue(path.exists("build/flarp.txt"))

    ########################################

    def test_sync_command(self):
        """The 'command' field of rules should be OK handling a sync function"""
        def sync_command(task):
            force_touch(task.context.out_obj)

        self.hancho(
            command = sync_command,
            in_src  = [],
            out_obj = "result.txt",
        )
        self.assertEqual(0, hancho_py.app.build_all())
        self.assertTrue(path.exists("build/result.txt"))

    ########################################

    def test_cancellation(self):
        """A task that receives a cancellation exception should not run."""

        # Note: not using -k0 will break the cancellation test
        hancho_py.app.reset()
        hancho_py.app.parse_flags(["--quiet", "-k0"])

        task_that_fails = self.hancho(
            command = "(exit 255)",
            in_src  = [],
            out_obj = "fail_result.txt",
        )
        task_that_passes = self.hancho(
            command = "touch {rel(out_obj)}",
            in_src  = [],
            out_obj = "pass_result.txt",
        )
        should_be_cancelled = self.hancho(
            command = "touch {rel(out_obj)}",
            in_src  = [task_that_fails, task_that_passes],
            out_obj = "should_not_be_created.txt",
        )
        self.assertNotEqual(0, hancho_py.app.build_all())
        self.assertEqual(1, hancho_py.app.tasks_finished)
        self.assertEqual(1, hancho_py.app.tasks_failed)
        self.assertEqual(1, hancho_py.app.tasks_cancelled)
        self.assertEqual(task_that_fails._state, hancho_py.TaskState.FAILED)
        self.assertEqual(task_that_passes._state, hancho_py.TaskState.FINISHED)
        self.assertEqual(should_be_cancelled._state, hancho_py.TaskState.CANCELLED)
        self.assertTrue(Path("build/pass_result.txt").exists())
        self.assertFalse(Path("build/fail_result.txt").exists())
        self.assertFalse(Path("build/should_not_be_created.txt").exists())

    ########################################

    def test_task_creates_task(self):
        """Tasks using callbacks can create new tasks when they run."""
        def callback(task):
            new_task = self.hancho(
                command = "touch {rel(out_obj)}",
                in_src  = [],
                out_obj = "dummy.txt"
            )
            # FIXME these should auto-queue
            new_task.queue()
            return []

        self.hancho(
            command = callback,
            in_src  = [],
            out_obj = []
        )

        self.assertEqual(0, hancho_py.app.build_all())
        self.assertTrue(Path("build/dummy.txt").exists())

    ########################################

    def test_tons_of_tasks(self):
        """We should be able to queue up 1000+ tasks at once."""
        for i in range(1000):
            self.hancho(
                desc    = "I am task {index}",
                command = "echo {index} > {rel(out_obj)}",
                in_src  = [],
                out_obj = "dummy{index}.txt",
                index   = i
            )
        self.assertEqual(0, hancho_py.app.build_all())
        self.assertEqual(1000, len(glob.glob("build/*")))

    ########################################

    def test_job_count(self):
        """We should be able to dispatch tasks that require various numbers of jobs/cores."""
        # Queues up 100 tasks that use random numbers of cores, then a "Job Hog" that uses all cores, then
        # another batch of 100 tasks that use random numbers of cores.

        for i in range(100):
            self.hancho(
                desc    = "I am task {index}, I use {job_count} cores",
                command = "(exit 0)",
                in_src  = [],
                out_obj = [],
                job_count = random.randrange(1, os.cpu_count() + 1),
                index = i
            )

        self.hancho(
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
            self.hancho(
                desc = "I am task {index}, I use {job_count} cores",
                command = "(exit 0)",
                in_src  = [],
                out_obj = [],
                job_count = random.randrange(1, os.cpu_count() + 1),
                index = 100 + i
            )

        self.assertEqual(0, hancho_py.app.build_all())
        self.assertTrue(Path("build/slow_result.txt").exists())

####################################################################################################

class TestSplitTemplate(unittest.TestCase):
    def test_basic(self):
        def split_template(text):
            blocks = hancho_py.split_template(text)
            return [block[1] for block in blocks]

        # Sanity check - Single braces should produce a block
        self.assertEqual(split_template("a {b} c"), ['a ', '{b}', ' c'])

        # Degenerate cases should produce single blocks
        self.assertEqual(split_template(""),  []   )
        self.assertEqual(split_template("{"), ['{'])
        self.assertEqual(split_template("}"), ['}'])
        self.assertEqual(split_template("a"), ['a'])

        # Multiple single-braced blocks should not produce empty text between them if they touch
        self.assertEqual(split_template("{a}{b}{c}"), ['{a}', '{b}', '{c}']  )

        # But if there's whitespace between them, it should be preserved
        self.assertEqual(split_template(" {a} {b} {c} "), [' ', '{a}', ' ', '{b}', ' ', '{c}', ' '])

        # Whitespace inside a block should not split the block
        self.assertEqual(split_template("{ a }{ b }{ c }"), ['{ a }', '{ b }', '{ c }'])

        # Unmatched braces
        self.assertEqual(split_template("{"),   ['{'])
        self.assertEqual(split_template("}"),   ['}'])

        self.assertEqual(split_template("{}"),  ['{}'])
        self.assertEqual(split_template("}{"),  ['}{'])
        self.assertEqual(split_template("{a"),  ['{a'])
        self.assertEqual(split_template("a}"),  ['a}'])

        self.assertEqual(split_template("a{b"), ['a{b'])
        self.assertEqual(split_template("a}b"), ['a}b'])
        self.assertEqual(split_template("}}{"), ['}}{'])
        self.assertEqual(split_template("}{{"), ['}{{'])
        self.assertEqual(split_template("{{}"), ['{', '{}'])
        self.assertEqual(split_template("{{}"), ['{', '{}'])
        self.assertEqual(split_template("{}}"), ['{}', '}'])

        # Nesting
        self.assertEqual(split_template("a{{b}}c"),      ['a{', '{b}', '}c'])
        self.assertEqual(split_template("{a{b}c}"),      ['{a', '{b}', 'c}'])
        self.assertEqual(split_template("x{a{b}{c}d}y"), ['x{a', '{b}', '{c}', 'd}y'])
        self.assertEqual(split_template("{{{{a}}}}"),    ['{{{', '{a}', '}}}'])

        # Adjacent blocks with different brace counts
        self.assertEqual(split_template("{a}{{b}}{c}"),   ['{a}', '{', '{b}', '}', '{c}'])
        self.assertEqual(split_template("{{a}}{b}{{c}}"), ['{', '{a}', '}', '{b}', '{', '{c}', '}'])
        self.assertEqual(split_template("{{a}}"),         ['{', '{a}', '}']       )
        self.assertEqual(split_template("{{a}{b}}"),      ['{', '{a}', '{b}', '}'])
        self.assertEqual(split_template("{{{a}}}"),       ['{{', '{a}', '}}']     )

        # Escaped braces should be ignored.
        self.assertEqual(split_template("a\\{b\\}c"),  ['a\\{b\\}c']      )
        self.assertEqual(split_template("a{\\}}b"),    ['a', '{\\}}', 'b'])
        self.assertEqual(split_template("a{\\{}b"),    ['a', '{\\{}', 'b'])

        self.assertEqual(split_template("\\"),            ['\\'])
        self.assertEqual(split_template("{\\n}"),         ['{\\n}'])
        self.assertEqual(split_template("a\\{b}"),        ['a\\{b}'])
        self.assertEqual(split_template("a{b\\}"),        ['a{b\\}'])

        # Escaped backslashes should _not_ cause a following brace to be ignored.
        self.assertEqual(split_template("a\\\\{b}"),      ['a\\\\', '{b}'])
        self.assertEqual(split_template("a{b\\\\}"),      ['a', '{b\\\\}'])

####################################################################################################

#import cProfile
#import pstats

if __name__ == "__main__":
    #with cProfile.Profile() as pr:
    #    print("derp")
    #    unittest.main(verbosity=0,exit=False)
    #    print("done")
    #    pr.print_stats(sort=pstats.SortKey.TIME)
    #unittest.main(verbosity=0)
    unittest.main(verbosity=0)
