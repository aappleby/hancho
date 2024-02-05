#!/usr/bin/python3

import asyncio, os, re, sys, subprocess
import doctest
import importlib.util
import importlib.machinery
from os import path

hancho_root  = os.getcwd()
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
proc_sem = None

################################################################################

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

config = Rule(
  name      = "hancho.config",
  verbose   = False, # Print verbose build info
  quiet     = False, # Don't print any task output
  serial    = False, # Do not parallelize tasks
  dryrun    = False, # Do not actually run tasks
  debug     = False, # Print debugging information
  force     = False, # Force all tasks to run
)

################################################################################

def load_module(name, path):
  if name in hancho_mods:
    return hancho_mods[name]

  path   = os.path.abspath(path)
  loader = importlib.machinery.SourceFileLoader(name, path)
  spec   = importlib.util.spec_from_loader(name, loader)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  hancho_mods[name] = module
  return module

def module2(mod_name, mod_dir, mod_file):
  absname = os.path.abspath(path.join(mod_dir, mod_file))
  if absname in hancho_mods:
    print(f"module already loaded {mod_name}")
    return hancho_mods[absname]

  old_dir = os.getcwd()
  os.chdir(mod_dir)
  result = load_module(mod_name, mod_file)
  os.chdir(old_dir)
  return result

def load(name):
  tail = name.split('/')[-1]

  mod_name = tail
  mod_dir  = name
  mod_file = f"{tail}.hancho"
  if path.exists(path.join(mod_dir, mod_file)):
    return module2(mod_name, mod_dir, mod_file)

  mod_name = tail
  mod_dir  = path.join(hancho_root, name)
  mod_file = f"{tail}.hancho"
  if path.exists(path.join(mod_dir, mod_file)):
    return module2(mod_name, mod_dir, mod_file)

  print(f"Could not load module {name}")
  sys.exit(-1)

################################################################################
# Minimal JSON-style pretty printer for Rule

def repr_dict(d, depth):
  result = "{\n"
  for (k,v) in d.items():
    result += "  " * (depth + 1) + repr_val(k, depth + 1) + " : " + repr_val(v, depth + 1) + ",\n"
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

def swap_ext(name, new_ext):
  return path.splitext(name)[0] + new_ext

def join(names, divider = ' '):
  return "" if names is None else divider.join(names)

def flatten(x):
  if x is None: return []
  if not type(x) is list: return [x]
  result = []
  for y in x: result.extend(flatten(y))
  return result

################################################################################

def expand_once(template, context):
  if template is None: return ""
  result = ""
  while s := template_regex.search(template):
    result += template[0:s.start()]
    exp = template[s.start():s.end()]
    try:
      replacement = eval(exp[1:-1], None, context)
      if replacement is not None: result += str(replacement)
    except:
      result += exp
    template = template[s.end():]
  result += template
  return result

def expand(template, context):
  for _ in range(100):
    if config.debug: print(f"expand \"{template}\"")
    new_template = expand_once(template, context)
    if template == new_template: return template
    template = new_template

  print(f"Expanding '{template[0:20]}...' failed to terminate")
  sys.exit(-1)

################################################################################

base_rule = Rule(
  build_dir = "build",
  quiet     = False, # Don't print this task's output
  force     = False, # Force this task to run
  join      = join,
  len       = len,
  swap_ext  = swap_ext,
  flatten   = flatten,
  expand    = expand,
  cmd       = lambda cmd : subprocess.check_output(cmd, shell=True, text=True).strip(),
)

################################################################################

def check_mtime(files_in, files_out):
  for file_in in files_in:
    mtime_in = path.getmtime(file_in)
    for file_out in files_out:
      mtime_out = path.getmtime(file_out)
      if mtime_in > mtime_out: return True
  return False

def needs_rebuild(self):
  files_in  = self.abs_files_in
  files_out = self.abs_files_out

  if not files_out:
    return "Always rebuild a target with no outputs?"

  # Check for missing outputs.
  for file_out in files_out:
    if not path.exists(file_out):
      return f"Rebuilding {files_out} because some are missing"

  # Check user-specified deps.
  if check_mtime(self.deps, files_out):
    return f"Rebuilding {files_out} because a manual dependency has changed"

  # Check depfile, if present.
  if self.depfile:
    depfile_name = expand(self.depfile, self)
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

################################################################################

async def wait_for_deps(self, deps):
  for file in deps:
    if promise := promise_map.get(file, None):
      dep_result = await promise
      if dep_result != 0: return dep_result
    if not config.dryrun:
      if file and not path.exists(file):
        print(f"Dependency {file} missing!")
        return -1
  return 0

################################################################################

async def run_task_async(task):

  # Wait on all our dependencies to be updated
  if any_fail := await wait_for_deps(task, task.abs_files_in):
    return any_fail

  if any_fail := await wait_for_deps(task, task.deps):
    return any_fail

  # Our dependencies are ready, we can grab a process semaphore slot now.
  async with proc_sem:
    global node_visit
    global node_total
    global node_built

    node_visit = node_visit + 1

    # Check if we need a rebuild
    reason = needs_rebuild(task)
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
      print(status, end="")
      print("\x1B[K", end="")

    # Print rebuild reason
    if config.debug: print(reason)

    # Print debug dump of args if needed
    if config.debug: print(task)

    # Print the tasks' command
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

    # OK, we're ready to start the subprocess. In serial mode we run the
    # subprocess synchronously.
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

    stdout = stdout_data.decode()
    stderr = stderr_data.decode()

    # Print command results
    if returncode:
      if not (config.verbose or config.debug): print()
      print(f"\x1B[31mFAILED\x1B[0m: {task.abs_files_out}")
      print(command)

    show_output = returncode or not (task.quiet or config.quiet)
    if show_output and (stdout or stderr):
      if not config.verbose: print()
      print(stderr, end="")
      print(stdout, end="")

    node_built = node_built + 1
    sys.stdout.flush()
    return returncode

################################################################################

def queue(task):
  # Expand all filenames
  src_dir   = path.relpath(os.getcwd(), hancho_root)
  build_dir = path.join(expand(task.build_dir, task), src_dir)

  task.src_dir   = src_dir
  task.build_dir = build_dir

  task.files_in  = [expand(f, task) for f in flatten(task.files_in)]
  task.files_out = [expand(f, task) for f in flatten(task.files_out)]

  task.files_in  = [path.join(src_dir,   f) for f in task.files_in]
  task.files_out = [path.join(build_dir, f) for f in task.files_out]

  task.deps      = flatten(task.deps)

  # Add the absolute paths of all filenames
  task.abs_files_in  = [path.join(hancho_root, f) for f in task.files_in]
  task.abs_files_out = [path.join(hancho_root, f) for f in task.files_out]

  # Check for duplicate outputs
  for file in task.abs_files_out:
    if file in hancho_outs:
      print(f"Multiple rules build {file}!")
      sys.exit(-1)
    hancho_outs.add(file)

  # OK, we can queue up the rule now.
  hancho_queue.append(task)
  return task.abs_files_out

################################################################################

def reset():
  hancho_queue.clear()
  promise_map.clear()
  hancho_outs.clear()

  global node_built
  global node_total
  global node_visit
  global proc_sem

  node_built = 0
  node_total = 0
  node_visit = 0
  proc_sem = None

################################################################################

def build():
  global node_built
  global node_total
  global proc_sem

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

  if proc_sem is None:
    proc_sem = asyncio.Semaphore(1 if config.serial else os.cpu_count())

  async def wait(promise_map):
    global any_failed
    results = await asyncio.gather(*promise_map.values())
    any_failed = any(results)

  hancho_loop.run_until_complete(wait(promise_map))
  if node_built and not config.verbose: print()
  reset()
  return not any_failed

################################################################################

def dump():
  for i in range(len(hancho_queue)):
    print(f"Target [{i}/{len(hancho_queue)}]")
    print(hancho_queue[i])

################################################################################

if __name__ == "__main__":
    #doctest.testmod()
    doctest.testfile("TUTORIAL.md")
