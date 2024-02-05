#!/usr/bin/python3

import asyncio, os, re, sys, subprocess
import importlib.util
import importlib.machinery
import inspect
import argparse
from os import path

################################################################################

def dothancho_dir():
  f = inspect.currentframe()
  while True:
    if f.f_code.co_filename.endswith(".hancho"):
      break
    f = f.f_back
  absdir = path.split(f.f_code.co_filename)[0]
  reldir = path.relpath(absdir, hancho_root)
  return reldir

################################################################################
# Hancho's Rule object behaves like a Javascript object and implements a basic
# form of prototypal inheritance via Rule.base

class Rule(dict):

  # "base" defaulted because base must always be present, otherwise we
  # infinite-recurse.
  def __init__(self, *, base = None, **kwargs):
    self |= kwargs
    self.base = base

  def __missing__(self, key):
    return self.base[key] if self.base else None

  def __setattr__(self, key, value):
    self.__setitem__(key, value)

  def __getattr__(self, key):
    return self.__getitem__(key)

  def __repr__(self):
    return repr_val(self, 0)

  def __call__(self, **kwargs):
    return queue(self.extend(**kwargs))

  def extend(self, **kwargs):
    return Rule(base = self, **kwargs)

################################################################################
# Hancho's module loader. Looks for {mod_dir}.hancho or build.hancho in either
# the calling .hancho file's directory, or relative to hancho_root

def load(mod_dir):
  base_dirs    = [dothancho_dir(), hancho_root]
  hancho_names = [f"{path.split(mod_dir)[1]}.hancho", "build.hancho"]

  for base_dir in base_dirs:
    for hancho_name in hancho_names:
      full_path = path.abspath(path.join(base_dir, mod_dir, hancho_name))
      if not path.exists(full_path):
        continue
      if full_path in hancho_mods:
        return hancho_mods[full_path]
      if result := load_module_path(full_path):
        hancho_mods[full_path] = result
        return result

  print(f"Could not load module {mod_dir}")
  sys.exit(-1)

def load_module_path(mod_path):
  mod_dir  = path.split(mod_path)[0]
  mod_name = path.split(mod_dir)[1]

  loader = importlib.machinery.SourceFileLoader(mod_name, mod_path)
  spec   = importlib.util.spec_from_loader(mod_name, loader)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module

################################################################################
# Minimal JSON-style pretty printer for Rule, used by --debug

def repr_dict(d, depth):
  result = "{\n"
  for (k,v) in d.items():
    result += "  " * (depth + 1) + repr_val(k, depth + 1) + " : "
    result += repr_val(v, depth + 1) + ",\n"
  result += "  " * depth + "}"
  return result

def repr_list(l, depth):
  if len(l) == 0: return "[]"
  if len(l) == 1: return "[" + repr_val(l[0], depth + 1) + "]"
  result = "[\n"
  for v in l:
    result += "  " * (depth + 1) + repr_val(v, depth + 1) + ",\n"
  result += "  " * depth + "]"
  return result

def repr_val(v, depth):
  if v is None:           return "null"
  if isinstance(v, str):  return '"' + v + '"'
  if isinstance(v, dict): return repr_dict(v, depth)
  if isinstance(v, list): return repr_list(v, depth)
  return str(v)

################################################################################
# A trivial templating system that replaces {foo} with the value of rule.foo
# and keeps going until it can't replace anything.

def expand_once(template, rule):
  if template is None: return ""
  result = ""
  while s := template_regex.search(template):
    result += template[0:s.start()]
    exp = template[s.start():s.end()]
    try:
      replacement = eval(exp[1:-1], None, rule)
      if replacement is not None: result += str(replacement)
    except:
      result += exp
    template = template[s.end():]
  result += template
  return result

def expand(template, rule):
  for _ in range(100):
    if config.debug: print(f"expand \"{template}\"")
    new_template = expand_once(template, rule)
    if template == new_template: return template
    template = new_template

  print(f"Expanding '{template[0:20]}...' failed to terminate")
  sys.exit(-1)

################################################################################
# Build rule helper methods

def flatten(x):
  if x is None: return []
  if not type(x) is list: return [x]
  result = []
  for y in x: result.extend(flatten(y))
  return result

def join(names, divider = ' '):
  return "" if names is None else divider.join(names)

def run_cmd(cmd):
  return subprocess.check_output(cmd, shell=True, text=True).strip()

def swap_ext(name, new_ext):
  return path.splitext(name)[0] + new_ext

################################################################################
# Checks if a task needs to be re-run, and returns a non-empty reason if so.

def needs_rerun(task):
  files_in  = task.abs_files_in
  files_out = task.abs_files_out

  if not files_out:
    return "Always rebuild a target with no outputs?"

  # Check for missing outputs.
  for file_out in files_out:
    if not path.exists(file_out):
      return f"Rebuilding {files_out} because some are missing"

  # Check user-specified deps.
  if check_mtime(task.deps, files_out):
    return f"Rebuilding {files_out} because a manual dependency has changed"

  # Check depfile, if present.
  if task.depfile:
    depfile_name = expand(task.depfile, task)
    if path.exists(depfile_name):
      deplines = open(depfile_name).read().split()
      deplines = [d for d in deplines[1:] if d != '\\']
      if check_mtime(deplines, files_out):
        return f"Rebuilding {files_out} because a dependency in {depfile_name} has changed"

  # Check input files.
  if check_mtime(files_in, files_out):
    return f"Rebuilding {files_out} because an input has changed"

  # All checks passed, so we don't need to rebuild this output.
  if config.debug: print(f"Files {files_out} are up to date")

  # All deps were up-to-date, nothing to do.
  return None

def check_mtime(files_in, files_out):
  for file_in in files_in:
    mtime_in = path.getmtime(file_in)
    for file_out in files_out:
      mtime_out = path.getmtime(file_out)
      if mtime_in > mtime_out: return True
  return False

################################################################################
# Waits until all the promises for a list of dependencies have been fulfilled.
# Returns zero if all dependencies are satisfied, otherwise non-zero.

async def wait_for_deps(deps):
  for dep in deps:
    promise = promise_map.get(dep, None)
    task_result = await promise if promise else 0
    if task_result != 0:
      return task_result
    if not config.dryrun and not path.exists(dep):
      print(f"Dependency {dep} missing!")
      return -1
  return 0

################################################################################
# Actually runs a task

async def run_task_async(task):
  # Wait on all our dependencies to be fulfilled
  if any_fail := await wait_for_deps(task.abs_files_in): return any_fail
  if any_fail := await wait_for_deps(task.abs_deps): return any_fail

  # Our dependencies are ready, we can grab a process semaphore slot now.
  async with proc_sem:
    global node_visit
    global node_total
    global node_built

    node_visit = node_visit + 1

    # Check if we need a rebuild
    reason = needs_rerun(task)
    if config.force or task.force: reason = f"Files {task.abs_files_out} forced to rebuild"
    if not reason: return 0

    # Print description
    description = expand(task.description, task)
    if config.verbose or config.debug:
      print(f"[{node_visit}/{node_total}] {description}")
    else:
      print("\r", end="")
      status = f"[{node_visit}/{node_total}] {description}"
      status = status[:os.get_terminal_size().columns - 1]
      print(f"{status}\x1B[K", end="") # Clear text to the end of the line

    # Print rebuild reason
    if config.debug: print(reason)

    # Print debug dump of args if needed
    if config.debug: print(task)

    # Print the task's command
    command = expand(task.command, task)
    if not command:
      print(f"Command missing for input {task.files_in}!")
      return -1
    if config.verbose or config.debug:
      print(f"{command}")

    # Flush before we run the task so that the debug output above appears in order
    sys.stdout.flush()

    # Early-exit if this is just a dry run
    if config.dryrun: return 0

    # Make sure our output directories exist
    for file_out in task.abs_files_out:
      if dirname := path.dirname(file_out):
        os.makedirs(dirname, exist_ok = True)

    # OK, we're ready to start the subprocess.
    # In serial mode we run the subprocess synchronously.
    if config.serial:
      result = subprocess.run(
        command,
        shell = True,
        stdout = subprocess.PIPE,
        stderr = subprocess.PIPE)
      stdout_data = result.stdout
      stderr_data = result.stderr
      returncode = result.returncode

    # In parallel mode we dispatch the subprocess via asyncio and then await
    # the result.
    else:
      proc = await asyncio.create_subprocess_shell(
        command,
        stdout = asyncio.subprocess.PIPE,
        stderr = asyncio.subprocess.PIPE)
      (stdout_data, stderr_data) = await proc.communicate()
      returncode = proc.returncode

    # Print failure message if needed
    if returncode:
      if not (config.verbose or config.debug): print()
      print(f"\x1B[31mFAILED\x1B[0m: {task.abs_files_out}")
      print(command)

    # Print command output if needed
    show_output = returncode or not (task.quiet or config.quiet)
    stdout = stdout_data.decode()
    stderr = stderr_data.decode()
    if show_output and (stdout or stderr):
      if not config.verbose: print()
      print(stderr, end="")
      print(stdout, end="")

    node_built = node_built + 1
    sys.stdout.flush()
    return returncode

################################################################################
# Adds a task to the global task queue, expanding filenames and dependencies
# in the process.

def queue(task):

  # Expand all filenames
  src_dir   = dothancho_dir()
  build_dir = expand(task.build_dir, task)
  build_dir = path.join(build_dir, src_dir)

  task.src_dir   = src_dir
  task.build_dir = build_dir

  task.files_in  = [expand(f, task) for f in flatten(task.files_in)]
  task.files_out = [expand(f, task) for f in flatten(task.files_out)]
  task.deps      = [expand(f, task) for f in flatten(task.deps)]

  # Prepend directories to filenames.
  # If they're already absolute, this does nothing.
  task.files_in  = [path.join(src_dir,   f) for f in task.files_in]
  task.files_out = [path.join(build_dir, f) for f in task.files_out]

  # Append the absolute paths of all in/out filenames to the task.
  # If they're already absolute, the filenames do not change
  task.abs_files_in  = [path.abspath(f) for f in task.files_in]
  task.abs_files_out = [path.abspath(f) for f in task.files_out]
  task.abs_deps      = [path.abspath(f) for f in task.deps]

  # And now strip hancho_root off the absolute paths to produce the final
  # root-relative paths
  task.files_in  = [path.relpath(f, hancho_root) for f in task.abs_files_in]
  task.files_out = [path.relpath(f, hancho_root) for f in task.abs_files_out]
  task.deps      = [path.relpath(f, hancho_root) for f in task.abs_deps]

  # Check for duplicate task outputs
  for file in task.abs_files_out:
    if file in hancho_outs:
      print(f"Multiple rules build {file}!")
      sys.exit(-1)
    hancho_outs.add(file)

  # OK, we can queue up the rule now.
  hancho_queue.append(task)
  return task.abs_files_out

################################################################################
# Runs all tasks in the queue and waits for them all to be finished

def build():
  global node_built
  global node_total

  node_total = len(hancho_queue)

  for task in hancho_queue:
    coroutine = run_task_async(task)
    promise = hancho_loop.create_task(coroutine)
    if task.abs_files_out:
      for output in task.abs_files_out:
        promise_map[output] = promise
    else:
      # We need an entry in the promise map for the task even if it doesn't
      # have any outputs so we don't exit the build before it's done.
      promise_map[f"task{id(task)}"] = promise

  async def wait(promise_map):
    global any_failed
    results = await asyncio.gather(*promise_map.values())
    any_failed = any(results)

  hancho_loop.run_until_complete(wait(promise_map))
  if node_built and not config.verbose: print()
  if node_built == 0:
    print("hancho: no work to do.")
  reset()
  return not any_failed

################################################################################
# Resets all internal global state

def reset():
  hancho_queue.clear()
  promise_map.clear()
  hancho_outs.clear()

  global node_built
  global node_total
  global node_visit

  node_built = 0
  node_total = 0
  node_visit = 0

################################################################################
# Dumps debugging info for all tasks in the queue

def dump():
  for i in range(len(hancho_queue)):
    print(f"Target [{i}/{len(hancho_queue)}]")
    print(hancho_queue[i])

################################################################################

if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument('--verbose',  default=False, action='store_true', help='Print verbose build info')
  parser.add_argument('--serial',   default=False, action='store_true', help='Do not parallelize commands')
  parser.add_argument('--dryrun',   default=False, action='store_true', help='Do not run commands')
  parser.add_argument('--debug',    default=False, action='store_true', help='Dump debugging information')
  parser.add_argument('--force',    default=False, action='store_true', help='Force rebuild of everything')
  parser.add_argument('--quiet',    default=False, action='store_true', help='Mute command output')
  (flags, unrecognized) = parser.parse_known_args()

  hancho_root  = os.getcwd()

  base_rule = Rule(
    build_dir = "build",
    root_dir  = hancho_root,
    quiet     = False, # Don't print this task's output
    force     = False, # Force this task to run
    expand    = expand,
    flatten   = flatten,
    join      = join,
    len       = len,
    run_cmd   = run_cmd,
    swap_ext  = swap_ext,
  )

  hancho_loop  = asyncio.new_event_loop()
  hancho_queue = []
  hancho_mods  = {}
  hancho_outs  = set()

  template_regex = re.compile("{[^}]*}")

  any_failed = False

  promise_map = {}

  node_total = 0
  node_visit = 0
  node_built = 0

  # Hancho's global configuration object
  config = Rule(
    verbose   = flags.verbose, # Print verbose build info
    quiet     = flags.quiet,   # Don't print any task output
    serial    = flags.serial,  # Do not parallelize tasks
    dryrun    = flags.dryrun,  # Do not actually run tasks
    debug     = flags.debug,   # Print debugging information
    force     = flags.force,   # Force all tasks to run
  )

  proc_sem = asyncio.Semaphore(1 if config.serial else os.cpu_count())

  # Stash a reference to ourself in sys.modules so that build.hancho and
  # descendants don't try to load a second copy of us.
  sys.modules["hancho"] = sys.modules["__main__"]

  build_path = path.join(hancho_root, "build.hancho")
  load_module_path(build_path)
