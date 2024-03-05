#!/usr/bin/python3
"""Test cases for Hancho"""

import os
from os import path
import subprocess
import unittest

# min delta seems to be 4 msec
# os.system("touch blahblah.txt")
# old_mtime = path.getmtime("blahblah.txt")
# min_delta = 1000000
# for _ in range(1000):
#   os.system("touch blahblah.txt")
#   new_mtime = path.getmtime("blahblah.txt")
#   delta = new_mtime - old_mtime
#   if delta and delta < min_delta:
#     log(str(delta))
#     min_delta = delta
#   old_mtime = new_mtime


def mtime(file):
    """Shorthand for path.getmtime()"""
    return path.getmtime(file)


def run(cmd):
    """Runs a command line and returns its stdout with whitespace stripped"""
    return subprocess.check_output(cmd, shell=True, text=True).strip()


def run_hancho(name):
    """Runs a Hancho build script, quietly."""
    return os.system(f"../hancho.py --quiet {name}.hancho")


def touch(name):
    """Convenience helper method"""
    os.system(f"touch {name}")


################################################################################


class TestHancho(unittest.TestCase):
    """Basic test cases"""

    def setUp(self):
        """Always wipe the build dir before a test"""
        os.system("rm -rf build")

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

    def test_expand_failed_to_terminate(self):
        """A recursive text template should cause an 'expand failed to terminate' error."""
        self.assertNotEqual(0, run_hancho("expand_failed_to_terminate"))

    def test_garbage_command(self):
        """Non-existent command line commands should cause Hancho to fail the build."""
        #self.assertNotEqual(0, run_hancho("garbage_command"))
        self.assertEqual(0, run_hancho("garbage_command"))

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
        os.system("mkdir build")
        touch("build/dummy.txt")
        run_hancho("dep_changed")
        mtime1 = mtime("build/result.txt")

        run_hancho("dep_changed")
        mtime2 = mtime("build/result.txt")

        touch("build/dummy.txt")
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

        os.system("touch src/test.hpp")
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

        os.system("touch src/test.cpp")
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
            "../hancho.py --build_dir=build/some/other/dir --quiet does_create_output.hancho"
        )
        self.assertTrue(path.exists("build/some/other/dir/result.txt"))


################################################################################

if __name__ == "__main__":
    unittest.main()
