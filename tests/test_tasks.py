#!/usr/bin/python3
"""Test cases for Hancho's Dict class"""

import sys
import unittest
import os
import shutil
from pathlib import Path

sys.path.append("..")
import hancho
from hancho import Dict

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

        #hancho_py.app.reset()
        #hancho_py.app.parse_flags(["--quiet"])
        #hancho_py.app.parse_flags([])
        #hancho_py.app.parse_flags(["-v"])
        #hancho_py.app.parse_flags(["-d"])
        #self.hancho = hancho_py.app.create_root_mod()
        hancho.init(['-q'])

    def tearDown(self):
        """And wipe the build dir after a test too."""
        #shutil.rmtree("build", ignore_errors=True)

    def run_tasks(self):
        hancho.Runner.queue_all_tasks()
        result = hancho.Runner.run_tasks()
        self.assertEqual(result, 0)

    ########################################

    def test_dummy(self):
        self.assertEqual(0, 0)

    def test_should_pass(self):
        hancho.Task(command = "echo Hello World")
        self.run_tasks()

    def test_out_file_dir(self):
        hancho.Task(
            command = "echo Hello File >> {out_file}",
            out_file = "test_command_lists.txt"
        )
        self.run_tasks()

    def test_run_cmd(self):
        command = r"echo I am runnning the {run_cmd('uname')} operating system."
        hancho.Task(desc = "Working run_cmd", command = command)
        self.run_tasks()

    def test_broken_run_cmd(self):
        command = r"echo {run_cmd('This is totally not a valid command.')}",
        hancho.Task(desc = "Broken run_cmd", command = command, should_fail = True)
        self.run_tasks()
