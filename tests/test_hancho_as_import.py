#!/usr/bin/python3
"""Tests using Hancho as an imported module"""

import sys
import os

sys.path.append("..")
import hancho

hancho.init(sys.argv)

hancho.Task(
    desc = "Write to {out_file}",
    command = "echo foo >> {out_file}",
    out_file = "bar.txt",
)

hancho.Task(
    desc = "Write to {out_file}",
    command = "echo bar >> {out_file}",
    out_file = "foo.txt",
)

hancho.Runner.queue_all_tasks()
hancho.Runner.run_tasks()
hancho.Log.log("Done!")