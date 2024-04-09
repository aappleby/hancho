#!/usr/bin/python3
"""Test cases for Hancho"""

import sys
import os
from os import path
import subprocess
import unittest
import shutil
import glob
from pathlib import Path


sys.path.append("..")
import hancho
from hancho import Config

# tests still needed -
# calling hancho in src dir
# meta deps changed
# transitive dependencies
# dry run not creating files/dirs
# loading a module directly and then via "../foo.hancho" should not load two
# copies
# all the predefined directories need test cases
# overriding in_dir/out_dir/work_dir need test cases
# loading multiple copies of rules.hancho with different build_params to test module_key

# min delta seems to be 4 msec on linux, 1 msec on windows?
# os.system("touch blahblah.txt")
# old_mtime = path.getmtime("blahblah.txt")
# min_delta = 1000000
# for _ in range(10000):
#   #os.system("touch blahblah.txt")
#   os.utime("blahblah.txt", None)
#   new_mtime = path.getmtime("blahblah.txt")
#   delta = new_mtime - old_mtime
#   if delta and delta < min_delta:
#     print(delta)
#     min_delta = delta
#   old_mtime = new_mtime
# sys.exit(0)


def mtime(file):
    """Shorthand for path.getmtime()"""
    return path.getmtime(file)


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
        print()
        print(f"Running {type(self).__name__}::{self._testMethodName}..", end="")
        sys.stdout.flush()

    """
    def test_subconfig_lookup(self):
        a = Config(
            b = Config(
                c = Config(
                    d = 1
                )
            )
        )
        self.assertEqual(a.d, 1)
    """

    """
    def test_missing(self):
        # Reading a MISSING should raise a KeyError
        a = Config(foo = hancho.MISSING)
        with self.assertRaises(KeyError):
            print(a.foo)

        # But if it's overridden later, we should _not_ raise a KeyError
        a = Config(foo = hancho.MISSING, bar = Config(foo = "present"))
        print(a)
        self.assertEqual("present", a.foo)
    """


################################################################################


# pylint: disable=too-many-public-methods
class TestHancho(unittest.TestCase):
    """Basic test cases"""

    def setUp(self):
        """Always wipe the build dir before a test"""
        print()
        print(f"Running {type(self).__name__}::{self._testMethodName}..", end="")
        shutil.rmtree("build", ignore_errors=True)
        sys.stdout.flush()

    def tearDown(self):
        """And wipe the build dir after a test too."""
        if path.exists("build"):
            shutil.rmtree("build")

    def test_should_pass(self):
        """Sanity check"""
        result = run_hancho("should_pass")
        self.assertEqual(0, result.returncode)

    def test_should_fail(self):
        """Sanity check"""
        result = run_hancho("should_fail")
        self.assertTrue(
            "ValueError: Command '(exit 255)' exited with return code 255"
            in result.stderr
        )

    def test_submodules1(self):
        shutil.rmtree("submodule_tests/build", ignore_errors=True)
        result = subprocess.run(
            f"python3 ../../hancho.py -v -d top_test1.hancho",
            shell=True,
            text=True,
            capture_output=True,
            cwd="submodule_tests",
        )
        self.assertTrue(Path("submodule_tests/build/top.txt").exists())
        self.assertTrue(Path("submodule_tests/build/repo1/repo1.txt").exists())
        self.assertTrue(Path("submodule_tests/build/repo2/repo2.txt").exists())

    def test_submodules2(self):
        shutil.rmtree("submodule_tests/build", ignore_errors=True)
        result = subprocess.run(
            f"python3 ../../hancho.py -v -d top_test2.hancho",
            shell=True,
            text=True,
            capture_output=True,
            cwd="submodule_tests",
        )
        self.assertTrue(Path("submodule_tests/build/top.txt").exists())
        self.assertTrue(Path("submodule_tests/build/repo1/repo1.txt").exists())
        self.assertTrue(Path("submodule_tests/build/repo2/repo2.txt").exists())

    def test_submodules3(self):
        shutil.rmtree("submodule_tests/build", ignore_errors=True)
        result = subprocess.run(
            f"python3 ../../hancho.py -v -d top_test3.hancho",
            shell=True,
            text=True,
            capture_output=True,
            cwd="submodule_tests",
        )
        self.assertTrue(Path("submodule_tests/build/top.txt").exists())
        self.assertTrue(Path("submodule_tests/build/repo1/repo1.txt").exists())
        self.assertTrue(Path("submodule_tests/build/repo2/repo2.txt").exists())

    def test_bad_build_path(self):
        result = run_hancho("bad_build_path")
        self.assertTrue("is not under root_path" in result.stderr)

    def test_check_output(self):
        """A build rule that doesn't update one of its outputs should fail"""
        result = run_hancho("check_output")
        self.assertTrue(Path("build/result.txt").exists())
        self.assertFalse(Path("build/not_modified.txt").exists())
        self.assertTrue("still needs rerun after running" in result.stderr)

    def test_config_inheritance(self):
        """A module should inherit a config object extended from its parent, but should not be able
        to modify its parent's config object."""
        self.assertEqual(0, run_hancho("config_parent").returncode)

        # This should fail because it was expecting inheritance from its parent.
        self.assertNotEqual(0, run_hancho("config_child").returncode)

    def test_missing_command(self):
        """Rules with missing commands should fail"""
        result = run_hancho("command_missing")
        self.assertTrue("Don't know how to expand NoneType ='None'" in result.stderr)

    def test_missing_field(self):
        """Missing fields should raise an error when expanded"""
        result = run_hancho("missing_field")
        self.assertTrue(
            "Could not find does_not_exist"
            in result.stderr
        )

    def test_missing_input(self):
        """We should fail if an input is missing"""
        result = run_hancho("missing_input")
        self.assertTrue("FileNotFoundError" in result.stderr)
        self.assertTrue("does_not_exist.txt" in result.stderr)

    def test_missing_dep(self):
        """Missing dep should fail"""
        result = run_hancho("missing_dep")
        self.assertTrue("FileNotFoundError" in result.stderr)
        self.assertTrue("missing_dep.txt" in result.stderr)

    def test_expand_failed_to_terminate(self):
        """A recursive text template should cause an 'expand failed to terminate' error."""
        result = run_hancho("expand_failed_to_terminate")
        self.assertTrue(
            "RecursionError: Expanding '{flarp}' failed to terminate" in result.stderr
        )

    def test_garbage_command(self):
        """Non-existent command line commands should cause Hancho to fail the build."""
        result = run_hancho("garbage_command")
        self.assertTrue(
            "ValueError: Command 'aklsjdflksjdlfkjldfk' exited with return code 127"
            in result.stderr
        )

    def test_garbage_template(self):
        """Templates that can't be eval()d should cause Hancho to fail the build."""
        result = run_hancho("garbage_template")
        self.assertTrue("SyntaxError: invalid syntax" in result.stderr)

    def test_rule_collision(self):
        """If multiple rules generate the same output file, that's an error."""
        result = run_hancho("rule_collision")
        self.assertTrue("NameError: Multiple rules build" in result.stderr)

    def test_always_rebuild_if_no_inputs(self):
        """A rule with no inputs should always rebuild"""
        run_hancho("always_rebuild_if_no_inputs")
        mtime1 = mtime("build/result.txt")

        run_hancho("always_rebuild_if_no_inputs")
        mtime2 = mtime("build/result.txt")

        run_hancho("always_rebuild_if_no_inputs")
        mtime3 = mtime("build/result.txt")
        self.assertLess(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)

    def test_build_path_works(self):
        """Customizing build_path should put output files in build_path"""
        run_hancho("build_path_works")
        self.assertTrue(path.exists("build/build_path_works/result.txt"))

    def test_dep_changed(self):
        """Changing a file in deps[] should trigger a rebuild"""
        os.makedirs("build", exist_ok=True)
        Path("build/dummy.txt").touch()
        run_hancho("dep_changed")
        mtime1 = mtime("build/result.txt")

        run_hancho("dep_changed")
        mtime2 = mtime("build/result.txt")

        Path("build/dummy.txt").touch()
        run_hancho("dep_changed")
        mtime3 = mtime("build/result.txt")
        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)

    def test_does_create_output(self):
        """Output files should appear in build/ by default"""
        run_hancho("does_create_output")
        self.assertTrue(path.exists("build/result.txt"))

    def test_doesnt_create_output(self):
        """Having a file mentioned in files_out should not magically create it"""
        run_hancho("doesnt_create_output")
        self.assertFalse(path.exists("build/result.txt"))

    def test_header_changed(self):
        """Changing a header file tracked in the GCC depfile should trigger a rebuild"""
        run_hancho("header_changed")
        mtime1 = mtime("build/src/test.o")

        run_hancho("header_changed")
        mtime2 = mtime("build/src/test.o")

        Path("src/test.hpp").touch()
        run_hancho("header_changed")
        mtime3 = mtime("build/src/test.o")
        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)

    def test_input_changed(self):
        """Changing a source file should trigger a rebuild"""
        run_hancho("input_changed")
        mtime1 = mtime("build/src/test.o")

        run_hancho("input_changed")
        mtime2 = mtime("build/src/test.o")

        Path("src/test.cpp").touch()
        run_hancho("input_changed")
        mtime3 = mtime("build/src/test.o")
        self.assertEqual(mtime1, mtime2)
        self.assertLess(mtime2, mtime3)

    def test_multiple_commands(self):
        """Rules with arrays of commands should run all of them"""
        run_hancho("multiple_commands")
        self.assertTrue(path.exists("build/foo.txt"))
        self.assertTrue(path.exists("build/bar.txt"))
        self.assertTrue(path.exists("build/baz.txt"))

    def test_arbitrary_flags(self):
        """Passing arbitrary flags to Hancho should work"""
        os.system(
            "python3 ../hancho.py --output_filename=flarp.txt --quiet arbitrary_flags.hancho"
        )
        self.assertTrue(path.exists("build/flarp.txt"))

    def test_sync_command(self):
        """The 'command' field of rules should be OK handling a sync function"""
        run_hancho("sync_command")
        self.assertTrue(path.exists("build/result.txt"))

    def test_cancellation(self):
        """A task that receives a cancellation exception should not run."""
        self.assertNotEqual(0, run_hancho("cancellation"))
        self.assertTrue(Path("build/pass_result.txt").exists())
        self.assertFalse(Path("build/fail_result.txt").exists())
        self.assertFalse(Path("build/should_not_be_created.txt").exists())

    def test_task_creates_task(self):
        """Tasks using callbacks can create new tasks when they run."""
        result = run_hancho("task_creates_task")
        self.assertEqual(0, result.returncode)
        self.assertTrue(Path("build/dummy.txt").exists())

    def test_tons_of_tasks(self):
        """We should be able to queue up 1000+ tasks at once."""
        result = run_hancho("tons_of_tasks")
        self.assertEqual(0, result.returncode)
        self.assertEqual(1000, len(glob.glob("build/*")))

    def test_job_count(self):
        """We should be able to dispatch tasks that require various numbers of jobs/cores."""
        result = run_hancho("job_count")
        self.assertEqual(0, result.returncode)
        self.assertTrue(Path("build/slow_result.txt").exists())


################################################################################

if __name__ == "__main__":
    unittest.main()
