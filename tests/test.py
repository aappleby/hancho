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

# tests still needed -
# calling hancho in src dir
# meta deps changed
# transitive dependencies
# dry run not creating files/dirs
# loading a module directly and then via "../foo.hancho" should not load two
# copies
# all the predefined directories need test cases
# overriding in_dir/out_dir/task_dir need test cases

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
    """Runs a Hancho build script, quietly."""
    return os.system(f"python3 ../hancho.py --quiet {name}.hancho")


################################################################################


# pylint: disable=too-many-public-methods
class TestHancho(unittest.TestCase):
    """Basic test cases"""

    def setUp(self):
        """Always wipe the build dir before a test"""
        print()
        print(f"Running {self._testMethodName}..", end="")
        if path.exists("build"):
            shutil.rmtree("build")
        sys.stdout.flush()

    def tearDown(self):
        """And wipe the build dir after a test too."""
        if path.exists("build"):
            shutil.rmtree("build")

    def test_should_pass(self):
        """Sanity check"""
        self.assertEqual(0, run_hancho("should_pass"))

    def test_should_fail(self):
        """Sanity check"""
        self.assertNotEqual(0, run_hancho("should_fail"))

    def test_check_output(self):
        """A build rule that doesn't update one of its outputs should fail"""
        self.assertNotEqual(0, run_hancho("check_output"))

    def test_check_missing_src(self):
        """We should fail if a source file is missing"""
        self.assertNotEqual(0, run_hancho("missing_src"))

    def test_recursive_base_is_bad(self):
        """Referring to base.attrib in a template is a bad idea"""
        self.assertNotEqual(0, run_hancho("recursive_base_is_bad"))

    def test_command_missing(self):
        """Rules with missing commands should fail"""
        self.assertNotEqual(0, run_hancho("command_missing"))

    def test_missing_field(self):
        """Missing fields should turn into empty strings when expanded"""
        self.assertEqual(0, run_hancho("missing_field"))

    def test_expand_failed_to_terminate(self):
        """A recursive text template should cause an 'expand failed to terminate' error."""
        self.assertNotEqual(0, run_hancho("expand_failed_to_terminate"))

    def test_garbage_command(self):
        """Non-existent command line commands should cause Hancho to fail the build."""
        self.assertNotEqual(0, run_hancho("garbage_command"))

    def test_garbage_template(self):
        """Templates that can't be eval()d should cause Hancho to fail the build."""
        self.assertNotEqual(0, run_hancho("garbage_template"))

    def test_rule_collision(self):
        """If multiple rules generate the same output file, that's an error."""
        self.assertNotEqual(0, run_hancho("rule_collision"))

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

    def test_build_dir_works(self):
        """Customizing build_dir should put output files in build_dir"""
        run_hancho("build_dir_works")
        self.assertTrue(path.exists("build/build_dir_works/result.txt"))

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
            "python3 ../hancho.py --build_dir=build/some/other/dir --quiet "
            + "does_create_output.hancho"
        )
        self.assertTrue(path.exists("build/some/other/dir/result.txt"))

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
        self.assertEqual(0, run_hancho("task_creates_task"))
        self.assertTrue(Path("build/dummy.txt").exists())

    def test_tons_of_tasks(self):
        """We should be able to queue up 1000+ tasks at once."""
        self.assertEqual(0, run_hancho("tons_of_tasks"))
        self.assertEqual(1000, len(glob.glob("build/*")))

    def test_job_count(self):
        """We should be able to dispatch tasks that require various numbers of jobs/cores."""
        self.assertEqual(0, run_hancho("job_count"))
        self.assertTrue(Path("build/slow_result.txt").exists())


################################################################################

if __name__ == "__main__":
    unittest.main()
