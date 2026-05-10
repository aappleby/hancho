#!/usr/bin/python3
"""Tests using Hancho as an imported module"""

import sys
import os

sys.path.append("..")
import hancho

(this_dir, this_file) = os.path.split(__file__)
hancho.init(this_dir = this_dir, this_file = this_file)

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

build_dir = hancho.config.eval("build_dir")
assert os.path.isfile(os.path.join(build_dir, "foo.txt"))
assert os.path.isfile(os.path.join(build_dir, "bar.txt"))
