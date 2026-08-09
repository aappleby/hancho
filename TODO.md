
# FIXME Implement module.__dir__() so "import * from hancho" works
# FIXME We probably don't need to completely rebuild the stat db in post_build
# FIXME Do we want to re-enable rel'ing all in_/out_ paths?
# FIXME Can we build examples/tutorial from examples/tutorial and also matcheroni/ and have it work somehow?
# FIXME tests for the various tools in tools/*
# FIXME need an example that drives Hancho through hancho.main()
# FIXME why is text during the dirty run in go.py orange?

# FIXME We should probably migrate path manipulation to pathlib.Path, even if we convert to/from
#       strings. It'll save us string concat debugging.

# FIXME merging lists concats, merging tuples replaces
# FIXME dicts stringify into lists of keys, tasks resolve to dicts of their outputs

00 - How to download and run Hancho, Trivial .hancho example
01 - Example with {in_src} and {out_obj}
02 - Example with separate tasks for compile and link
03 - compile_cpp and link_cpp commands
04 - tools.hancho
05 -

# FIXME Refactoring
  can we run "python3 -m unittest" with a callback instead of starting another process?
  README.md is out of date
  tutorial is hella out of date
  tools_fpga.synth is messy, replace with "def synth(*, ...):
  riscv_rules.hancho could merge with base_rules.hancho, or at least share stuff?
  tools_fpga could stand to be cleaned up a bit.
  We can probably use more map/reduce to clean up some verbosity

# FIXME Tests
  big long cancellation chain test
  dry run
  task output collision that uses symlinks
  brace-delimited sections inside quote-delimited strings, etc
  full-loop test cases for escaped {}s.
    Somewhere in the process we need to unescape them and I'm not sure where it goes.
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



