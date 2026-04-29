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
  # Absolute path under build_dir, do nothing.
  # Absolute path under task_dir, move to build_dir
  Output file has absolute path that is not under task_dir or build_dir
  debug mode
  trace mode
  task dir not found
