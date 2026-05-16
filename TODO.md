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
  Absolute path under task_dir, move to build_dir
  Output file has absolute path that is not under task_dir or build_dir
  debug mode
  trace mode
  task dir not found



# FIXME should we be using mappingproxy to make Dicts immutable?
# FIXME need to ensure that all the stuff accessible to the clients through hancho is clean. Right now it's messy.
# FIXME this needs to use a semaphore
# FIXME this could use some cleanup, I don't think we need _all_ these methods.
# FIXME need tests for brace-delimited sections inside quote-delimited strings, etc
# FIXME It feels slightly odd to have expansion_globals, should we just use the hancho.py
# FIXME we need full-loop test cases for escaped {}s.
# FIXME work needs to be redistributed between task_main, task_init, etc - more smaller units.
# FIXME _all_ paths should be rel'd before running command. If you want abs, you can abs() it.
# FIXME need a test for task output collision that uses symlinks
# FIXME We need a better way to handle "should fail" so we don't constantly keep rerunning
# FIXME we are not currently doing that.... (If no target was specified, we queue up all tasks that build stuff in the root repo)
# FIXME need a test for dry run
# FIXME try: with contextlib.chdir(): is a bit deep