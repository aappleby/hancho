00 - How to download and run Hancho, Trivial .hancho example
01 - Example with {in_src} and {out_obj}
02 - Example with separate tasks for compile and link
03 - compile_cpp and link_cpp commands
04 - rules.hancho
05 -

# FIXME Refactoring
  can we run "python3 -m unittest" with a callback instead of starting another process?
  README.md is out of date
  tutorial is hella out of date
  tools_fpga.synth is messy, replace with "def synth(*, ...):
  riscv_rules.hancho could merge with base_rules.hancho, or at least share stuff?
  Does "promises that resolve to filenames can be used in place of actual filenames in tools." still work?
  tools_fpga could stand to be cleaned up a bit.

# FIXME Tests
  dry run
  task output collision that uses symlinks
  Promise thingy
  brace-delimited sections inside quote-delimited strings, etc
  full-loop test cases for escaped {}s.
    Somewhere in the process we need to unescape them and I'm not sure where it goes.
  task.promise
  command is None
  cancelled during init
  failing during init
  can init even throw?
  task with return code non-zero
  Absolute path under build_dir, do nothing.
  Absolute path under task_cwd, move to build_dir
  Output file has absolute path that is not under task_cwd or build_dir
  debug mode
  trace mode
  task dir not found
  input file = None
  boolean cli flags can be true/True/1 false/False/0
  merging multiple nested configs into one task - like merging toolchain.blah and config.blee



