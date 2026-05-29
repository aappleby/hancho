00 - How to download and run Hancho, Trivial .hancho example
01 - Example with {in_src} and {out_obj}
02 - Example with separate tasks for compile and link
03 - compile_cpp and link_cpp commands
04 - rules.hancho
05 -

# FIXME Questions
  should we be using mappingproxy to make Dicts immutable?
    probably not

# FIXME Refactoring
  work needs to be redistributed between task_main, task_init, etc - more smaller units.
  _all_ paths should be rel'd before running command. If you want abs, you can abs() it.
  the exception-throwing path and stats regarding failed/cancelled tasks needs a revisit
  expanding command in task_init should use expand-in-place or something
  tasks should auto-queue if they're created dynamically?
  Clean up the pile of globals we pass to scripts
    see if we can do something different with the eval() global and local contexts, idk.
    need to ensure that all the stuff accessible to the clients through hancho is clean.
    it's getting better.

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




hancho
cv_context
Tree

Utils
Dict
Log
Path

Task
Stats
Promise

Expander
Tracer
Loader
Runner

init
reset
main