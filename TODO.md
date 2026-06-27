
# FIXME - investigate shadow dict of expanded templates as a cache
# FIXME We need an option to save the log to the build directory
# FIXME if we're gonna put stuff in the build directory, we should just do content hashing.
# FIXME Implement module.__dir__() so "import * from hancho" works
# FIXME We probably don't need to completely rebuild the stat db in post_build
# FIXME I feel like we need an explicit "split blob of flags/config into per-task and per-app options" function...
# FIXME Do we want to keep _loaded_files now that we have better change detection?
# FIXME Do we want to re-enable rel'ing all in_/out_ paths?
# FIXME Can we build examples/tutorial from examples/tutorial and also matcheroni/ and have it work somehow?
# FIXME tests for the various rools in tools/*
# FIXME need an example that drives Hancho through hancho.main()

It needs to be made VERY CLEAR that no matter where you are in the callstack or what file you have
open on your screen, hancho.config _always_ points at the config owned by the script that is
_currently_ _being_ _loaded_, or the script that created a task if we're in an async task.

And something similar about sticking stuff on the hancho object -> they go into script.globals, so
the same deal as config (except those aren't visible to templates)


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



