#!/usr/bin/python3

import argparse, asyncio, builtins, inspect, io, json, os, re, subprocess, sys, types
from os import path

this = sys.modules[__name__]

################################################################################
# Build rule helper methods

def join(names, divider = ' '):
  return "" if names is None else divider.join(names)

def run_cmd(cmd):
  return subprocess.check_output(cmd, shell=True, text=True).strip()

def swap_ext(name, new_ext):
  return path.splitext(name)[0] + new_ext

################################################################################

line_dirty = False

def log(*args, sameline = False, **kwargs):
  global line_dirty
  if this.flags.silent: return

  output = io.StringIO()
  if sameline: kwargs["end"] = ""
  print(*args, file=output, **kwargs)
  output = output.getvalue()

  if not sameline and line_dirty:
    sys.stdout.write("\n")
    line_dirty = False

  if not output: return

  if sameline:
    sys.stdout.write("\r")
    output = output[:os.get_terminal_size().columns - 1]
    sys.stdout.write(output)
    sys.stdout.write("\x1B[K")
  else:
    sys.stdout.write(output)

  sys.stdout.flush()
  line_dirty = output[-1] != '\n'

################################################################################

async def async_main():

  this.hancho_root = os.getcwd()
  this.hancho_mods  = {}
  this.config = None
  this.proc_sem = None

  this.mod_stack = []

  this.hancho_outs = set()

  # Hancho's global configuration object
  #this.config = None
  this.config = Rule(
    verbose   = False, # Print verbose build info
    quiet     = False, # Don't print any task output
    serial    = False, # Do not parallelize tasks
    dryrun    = False, # Do not actually run tasks
    debug     = False, # Print debugging information
    force     = False, # Force all tasks to run
    desc      = "{files_in} -> {files_out}",
    build_dir = None,
    expand    = expand,
    flatten   = flatten,
    join      = join,
    len       = len,
    run_cmd   = run_cmd,
    swap_ext  = swap_ext
  )

  this.config.force     = this.flags.force
  this.config.verbose   = this.flags.verbose   # Print verbose build info
  this.config.quiet     = this.flags.quiet     # Don't print any task output
  this.config.serial    = this.flags.serial    # Do not parallelize tasks
  this.config.dryrun    = this.flags.dryrun    # Do not actually run tasks
  this.config.debug     = this.flags.debug     # Print debugging information
  this.config.force     = this.flags.force     # Force all tasks to run
  this.config.multiline = this.flags.multiline # Print multiple lines of output

  this.tasks_total = 0
  this.tasks_index = 0
  this.tasks_pass  = 0
  this.tasks_fail  = 0

  this.proc_sem = asyncio.Semaphore(1 if this.flags.serial else os.cpu_count())

  top_module = load(this.flags.filename)
  while True:
    pending_tasks = asyncio.all_tasks() - {asyncio.current_task()}
    if not pending_tasks: break
    await asyncio.wait(pending_tasks)

  if line_dirty: sys.stdout.write("\n")

  if this.tasks_fail != 0:
    log("hancho: some tasks failed!")
  elif this.tasks_pass == 0:
    log("hancho: no work to do.")

  return -1 if this.tasks_fail else 0

################################################################################

def main():
  # A reference to this module is already in sys.modules["__main__"].
  # Stash another reference in sys.modules["hancho"] so that build.hancho and
  # descendants don't try to load a second copy of us.
  sys.modules["hancho"] = this

  parser = argparse.ArgumentParser()
  parser.add_argument('filename',   default="build.hancho", nargs="?")
  parser.add_argument('--verbose',   default=False, action='store_true', help='Print verbose build info')
  parser.add_argument('--serial',    default=False, action='store_true', help='Do not parallelize commands')
  parser.add_argument('--dryrun',    default=False, action='store_true', help='Do not run commands')
  parser.add_argument('--debug',     default=False, action='store_true', help='Dump debugging information')
  parser.add_argument('--force',     default=False, action='store_true', help='Force rebuild of everything')
  parser.add_argument('--quiet',     default=False, action='store_true', help='Mute command output')
  parser.add_argument('--dump',      default=False, action='store_true', help='Dump debugging info for all tasks')
  parser.add_argument('--multiline', default=False, action='store_true', help='Print multiple lines of output')
  parser.add_argument('--test',      default=False, action='store_true', help='Run .hancho file as a unit test')
  parser.add_argument('--silent',    default=False, action='store_true', help='No output')

  parser.add_argument('-D', action='append', type=str)
  (this.flags, unrecognized) = parser.parse_known_args()

  retcode = asyncio.run(async_main())

  #this.config.debug = False
  #retcode = asyncio.run(async_main())

  sys.exit(retcode)
  pass

################################################################################
# The .hancho file loader does a small amount of work to keep track of the
# stack of .hancho files that have been loaded, and chdir()s into the .hancho
# file directory before running it so that glob() can resolve files relative
# to the .hancho file itself.

def load(mod_path):
  abs_path = path.abspath(mod_path)
  if abs_path in this.hancho_mods:
    return this.hancho_mods[abs_path]

  if not os.path.exists(abs_path):
    log(f"Could not load module {mod_path}")
    sys.exit(-1)

  mod_dir  = path.split(abs_path)[0]
  mod_file = path.split(abs_path)[1]
  mod_name = mod_file.split(".")[0]

  source = open(abs_path, "r").read()
  code = compile(source, abs_path, 'exec', dont_inherit=True)

  module = type(sys)(mod_name)
  module.__file__ = abs_path
  module.__builtins__ = builtins

  sys.path.insert(0, mod_dir)
  old_dir = os.getcwd()

  this.mod_stack.append(abs_path)
  os.chdir(mod_dir)
  types.FunctionType(code, module.__dict__)()
  os.chdir(old_dir)
  this.mod_stack.pop()

  return module

################################################################################
# Hancho's Rule object behaves like a Javascript object and implements a basic
# form of prototypal inheritance via Rule.base

class Rule(dict):

  def __init__(self, *, base = None, **kwargs):
    self.set(**kwargs)
    self.base = this.config if base is None else base

  def __missing__(self, key):
    if self.base:
      return self.base[key]
    return None

  def set(self, **kwargs):
    self |= kwargs

  def __setattr__(self, key, value):
    self.__setitem__(key, value)

  def __getattr__(self, key):
    return self.__getitem__(key)

  def __repr__(self):
    class Encoder(json.JSONEncoder):
        def default(self, obj):
            return "<function>" if callable(obj) else super().default(obj)
    return json.dumps(self, indent = 2, cls=Encoder)

  def __call__(self, **kwargs):
    return queue2(self.extend(**kwargs))

  def extend(self, **kwargs):
    return Rule(base = self, **kwargs)

  def expand(self, template):
    return expand(self, template)

################################################################################
# A trivial templating system that replaces {foo} with the value of rule.foo
# and keeps going until it can't replace anything.

template_regex = re.compile("{[^}]*}")

def expand_once(self, template):
  if template is None: return ""
  result = ""
  while s := template_regex.search(template):
    result += template[0:s.start()]
    exp = template[s.start():s.end()]
    try:
      replacement = eval(exp[1:-1], globals(), self)
      if replacement is not None: result += str(replacement)
    except Exception as foo:
      result += exp
    template = template[s.end():]
  result += template
  return result

def expand(self, template):
  for _ in range(100):
    if this.config.debug: log(f"expand \"{template}\"")
    new_template = expand_once(self, template)
    if template == new_template:
      if template_regex.search(template):
        log(f"Expanding '{template[0:20]}' is stuck in a loop")
        sys.exit(-1)
      return template
    template = new_template

  log(f"Expanding '{template[0:20]}...' failed to terminate")
  sys.exit(-1)

################################################################################
# Returns true if any file in files_in is newer than any file in files_out.

def check_mtime(files_in, files_out):
  for file_in in files_in:
    mtime_in = path.getmtime(file_in)
    for file_out in files_out:
      mtime_out = path.getmtime(file_out)
      if mtime_in > mtime_out: return True
  return False

################################################################################
# Checks if a task needs to be re-run, and returns a non-empty reason if so.

def needs_rerun(task):
  files_in  = task.abs_files_in
  files_out = task.abs_files_out

  if not files_in:
    return "Always rebuild a target with no inputs"

  if not files_out:
    return "Always rebuild a target with no outputs"

  # Check for missing outputs.
  for file_out in files_out:
    if not path.exists(file_out):
      return f"Rebuilding {files_out} because some are missing"

  # Check the hancho file(s) that generated the task
  if check_mtime(task.meta_deps, files_out):
    return f"Rebuilding {files_out} because its .hancho files have changed"

  # Check user-specified deps.
  if check_mtime(task.deps, files_out):
    return f"Rebuilding {files_out} because a manual dependency has changed"

  # Check GCC-format depfile, if present.
  if task.depfile:
    depfile_name = task.expand(task.depfile)
    if path.exists(depfile_name):
      deplines = open(depfile_name).read().split()
      deplines = [d for d in deplines[1:] if d != '\\']
      if check_mtime(deplines, files_out):
        return f"Rebuilding {files_out} because a dependency in {depfile_name} has changed"

  # Check input files.
  if check_mtime(files_in, files_out):
    return f"Rebuilding {files_out} because an input has changed"

  # All checks passed, so we don't need to rebuild this output.
  if this.config.debug: log(f"Files {files_out} are up to date")

  # All deps were up-to-date, nothing to do.
  return None

################################################################################
# Slightly weird method that flattens out an arbitrarily-nested list of strings
# and promises-for-strings into a flat array of actual strings.

async def flatten(x):
  if x is None: return []
  if inspect.iscoroutine(x):
    log("Can't flatten a raw coroutine!")
    sys.exit(-1)
  if type(x) is asyncio.Task:
    x = await x
  if not type(x) is list:
    return [x]
  result = []
  for y in x: result.extend(await flatten(y))
  return result

################################################################################

async def run_command(task):

  if callable(task.command):
    await task.command(task)
    return

  if not type(task.command) is str:
    log(f"Don't know what to do with {task.command}")
    sys.exit(-1)

  command = task.expand(task.command)

  quiet = task.quiet and not (task.verbose or task.debug)
  if task.verbose or task.debug:
    log(f"{command}")

  # Early-exit if this is just a dry run
  if task.dryrun:
    return task.abs_files_out

  # In serial mode we run the subprocess synchronously.
  if task.serial:
    result = subprocess.run(
      command,
      shell = True,
      stdout = subprocess.PIPE,
      stderr = subprocess.PIPE)
    task.stdout = result.stdout.decode()
    task.stderr = result.stderr.decode()
    task.returncode = result.returncode

  # In parallel mode we dispatch the subprocess via asyncio and then await
  # the result.
  else:
    proc = await asyncio.create_subprocess_shell(
      command,
      stdout = asyncio.subprocess.PIPE,
      stderr = asyncio.subprocess.PIPE)
    (stdout_data, stderr_data) = await proc.communicate()
    task.stdout = stdout_data.decode()
    task.stderr = stderr_data.decode()
    task.returncode = proc.returncode

  # Print command output if needed
  if not quiet and (task.stdout or task.stderr):
    if task.stderr: log(task.stderr, end="")
    if task.stdout: log(task.stdout, end="")

################################################################################

async def dispatch(task):

  # Expand our build paths
  src_dir   = path.relpath(task.cwd, hancho_root)
  build_dir = path.join(task.expand(task.build_dir), src_dir)

  # Flatten will await all filename promises in any of these arrays.
  task.files_in  = await flatten(task.files_in)
  task.files_out = await flatten(task.files_out)
  task.deps      = await flatten(task.deps)

  #print(task.cwd)

  # Early-out with no result if any of our inputs or outputs are None (failed)
  if None in task.files_in:  return None
  if None in task.files_out: return None
  if None in task.deps:      return None

  task.files_in  = [task.expand(f) for f in task.files_in]
  task.files_out = [task.expand(f) for f in task.files_out]
  task.deps      = [task.expand(f) for f in task.deps]

  # Prepend directories to filenames.
  # If they're already absolute, this does nothing.
  task.files_in  = [path.join(src_dir,f)    for f in task.files_in]
  task.files_out = [path.join(build_dir, f) for f in task.files_out]
  task.deps      = [path.join(src_dir, f)   for f in task.deps]

  # Append hancho_root to all in/out filenames.
  # If they're already absolute, this does nothing.
  task.abs_files_in  = [path.abspath(path.join(hancho_root, f)) for f in task.files_in]
  task.abs_files_out = [path.abspath(path.join(hancho_root, f)) for f in task.files_out]
  task.abs_deps      = [path.abspath(path.join(hancho_root, f)) for f in task.deps]

  # And now strip hancho_root off the absolute paths to produce the final
  # root-relative paths
  task.files_in  = [path.relpath(f, hancho_root) for f in task.abs_files_in]
  task.files_out = [path.relpath(f, hancho_root) for f in task.abs_files_out]
  task.deps      = [path.relpath(f, hancho_root) for f in task.abs_deps]

  # Check for duplicate task outputs
  for file in task.abs_files_out:
    if file in this.hancho_outs:
      log(f"Multiple rules build {file}!")
      return None
    this.hancho_outs.add(file)

  # Check for valid command
  if not task.command:
    log(f"Command missing for input {task.files_in}!")
    return None

  # Check if we need a rebuild
  reason = needs_rerun(task)
  if config.force or task.force: reason = f"Files {task.abs_files_out} forced to rebuild"
  if not reason: return task.abs_files_out

  # Print the status line
  command = task.expand(task.command) if type(task.command) is str else "<callback>"
  desc    = task.expand(task.desc) if task.desc else command
  quiet   = task.quiet and not (task.verbose or task.debug)

  this.tasks_index += 1
  log(f"[{this.tasks_index}/{this.tasks_total}] {desc}",
      sameline = sys.stdout.isatty() and not task.multiline)

  if task.debug:
    log(f"Rebuild reason: {reason}")

  if task.debug:
    log(task)

  # Make sure our output directories exist
  for file_out in task.abs_files_out:
    if dirname := path.dirname(file_out):
      os.makedirs(dirname, exist_ok = True)

  # OK, we're ready to start the task. Grab a semaphore so we don't run too
  # many at once.
  async with this.proc_sem:
    await run_command(task)

  # Task complete. Check return code and return abs_files_out if we succeeded,
  # which will resolve the task's promise.
  if task.returncode:
    log(f"\x1B[31mFAILED\x1B[0m: {command}")
    this.tasks_fail += 1
    return None

  if task.files_in and task.files_out and not config.dryrun:
    if reason := needs_rerun(task):
      log(f"\x1B[33mFAILED\x1B[0m: Task \"{desc}\" still needs rerun after running!")
      log(f"Reason: {reason}")
      this.tasks_fail += 1
      return None

  this.tasks_pass += 1
  return task.abs_files_out

################################################################################

def queue2(task):
  if task.files_in is None:
    log("no files_in")
    sys.exit(-1)
  if task.files_out is None:
    log("no files_out")
    sys.exit(-1)

  this.tasks_total += 1

  task.meta_deps = list(this.mod_stack)
  task.cwd = path.split(this.mod_stack[-1])[0]
  promise = dispatch(task)
  return asyncio.create_task(promise)

################################################################################

if __name__ == "__main__": main()
