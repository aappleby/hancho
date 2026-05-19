00 - How to download and run Hancho, Trivial .hancho example
01 - Example with {in_src} and {out_obj}
02 - Example with separate tasks for compile and link
03 - compile_cpp and link_cpp commands
04 - rules.hancho
05 -

need test case for:
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



# FIXME should we be using mappingproxy to make Dicts immutable?
  probably not

# FIXME need to ensure that all the stuff accessible to the clients through hancho is clean. Right now it's messy.
  it's getting better.

# FIXME need tests for brace-delimited sections inside quote-delimited strings, etc

# FIXME we need full-loop test cases for escaped {}s.

# FIXME work needs to be redistributed between task_main, task_init, etc - more smaller units.

# FIXME _all_ paths should be rel'd before running command. If you want abs, you can abs() it.

# FIXME need a test for task output collision that uses symlinks

# FIXME need a test for dry run

# FIXME the exception-throwing path and stats regarding failed/cancelled/should-fail tasks needs a revisit

# FIXME test Promise thingy

# FIXME need tests for brace-delimited sections inside quote-delimited strings, etc

# FIXME we need full-loop test cases for escaped {}s.
# Somewhere in the process we need to unescape them and I'm not sure where it goes.

# FIXME write expand-in-place

# FIXME expanding command in task_init should use expand-in-place or something

# FIXME ditch task_main2

# FIXME queue_root_tasks - really we need to build everything load()ed by the root repo
