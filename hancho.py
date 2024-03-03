#!/usr/bin/python3

import argparse, asyncio, builtins, inspect, io, json, os, re, subprocess, sys, types
from os import path

# If we were launched directly, a reference to this module is already in
# sys.modules[__name__]. Stash another reference in sys.modules["hancho"] so
# that build.hancho and descendants don't try to load a second copy of Hancho.

this = sys.modules[__name__]
sys.modules["hancho"] = this

################################################################################
# Build rule helper methods

def color(r = None, g = None, b = None):
  if r is None: return "\x1B[0m"
  return f"\x1B[38;2;{r};{g};{b}m"

def is_atom(x):
  return type(x) is str or not hasattr(x, "__iter__")

def join(x, delim = ' '):
  return delim.join([str(y) for y in flatten(x) if y is not None])

def run_cmd(cmd):
  return subprocess.check_output(cmd, shell=True, text=True).strip()

def swap_ext(name, new_ext):
  if is_atom(name): return path.splitext(name)[0] + new_ext
  return [swap_ext(n, new_ext) for n in flatten(name)]

def flatten(x):
  if is_atom(x): return [x]
  result = []
  for y in x: result.extend(flatten(y))
  return result

# Same as flatten(), except it awaits anything that needs awaiting.
async def flatten_async(x):
  if inspect.isawaitable(x): x = await x
  if is_atom(x): return [x]
  result = []
  for y in x: result.extend(await flatten_async(y))
  return result

################################################################################
# Simple logger that can do same-line log messages like Ninja

line_dirty = False

def log(message, *args, task = None, expand = False, sameline = False, **kwargs):
  global line_dirty
  if this.config.quiet: return

  if not sys.stdout.isatty(): sameline = False

  output = io.StringIO()
  if sameline: kwargs["end"] = ""
  if task and expand:
    message = task.expand(message)
  print(message, *args, file=output, **kwargs)
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

def err(*args, **kwargs):
  print(color(255, 128, 128), end="")
  log(*args, **kwargs)
  print("(Hancho exiting due to error)")
  print(color(), end="")
  sys.exit(-1)

################################################################################

def main():
  return asyncio.run(async_main())

################################################################################

async def async_main():

  # Reset all global state
  this.hancho_root = os.getcwd()
  this.hancho_mods = {}
  this.mod_stack   = []
  this.hancho_outs = set()
  this.tasks_total = 0
  this.tasks_index = 0
  this.tasks_fail  = 0
  this.tasks_pass  = 0
  this.tasks_skip  = 0
  this.mtime_calls = 0
  this.all_rebuilt = set()

  # Change directory and load top module(s).
  if not path.exists(this.config.filename):
    err(f"Could not find {this.config.filename}")

  if this.config.chdir: os.chdir(this.config.chdir)
  top_module = load2(this.config.filename)

  # Top module(s) loaded. Configure our job semaphore and run all tasks in the
  # queue until we run out.
  if not this.config.jobs: this.config.jobs = 1000
  this.semaphore = asyncio.Semaphore(this.config.jobs)

  while True:
    pending_tasks = asyncio.all_tasks() - {asyncio.current_task()}
    if not pending_tasks: break
    await asyncio.wait(pending_tasks)

  # Done, print status info if needed
  if this.config.debug:
    log(f"tasks total:   {this.tasks_total}")
    log(f"tasks skipped: {this.tasks_skip}")
    log(f"tasks passed:  {this.tasks_pass}")
    log(f"tasks failed:  {this.tasks_fail}")
    log(f"mtime calls:   {this.mtime_calls}")

  if this.tasks_fail:
    log(f"hancho: \x1B[31mBUILD FAILED\x1B[0m")
  elif this.tasks_pass:
    log(f"hancho: \x1B[32mBUILD PASSED\x1B[0m")
  else:
    log(f"hancho: \x1B[33mBUILD CLEAN\x1B[0m")

  if this.config.chdir: os.chdir(this.hancho_root)

  return -1 if this.tasks_fail else 0

################################################################################
# The .hancho file loader does a small amount of work to keep track of the
# stack of .hancho files that have been loaded.


def load(mod_path):
  for parent_mod in reversed(this.mod_stack):
    abs_path = path.abspath(path.join(path.split(parent_mod.__file__)[0], mod_path))
    if os.path.exists(abs_path):
      return load2(abs_path)
  err(f"Could not load module {mod_path}")

def load2(mod_path):
  abs_path = path.abspath(mod_path)

  if abs_path in this.hancho_mods:
    return this.hancho_mods[abs_path]

  mod_dir  = path.split(abs_path)[0]
  mod_file = path.split(abs_path)[1]
  mod_name = mod_file.split(".")[0]

  header = "import hancho\n"
  source = header + open(abs_path, "r").read()
  code = compile(source, abs_path, 'exec', dont_inherit=True)

  module = type(sys)(mod_name)
  module.__file__ = abs_path
  module.__builtins__ = builtins
  this.hancho_mods[abs_path] = module

  sys.path.insert(0, mod_dir)
  old_dir = os.getcwd()

  # We must chdir()s into the .hancho file directory before running it so that
  # glob() can resolve files relative to the .hancho file itself.
  this.mod_stack.append(module)
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
        if callable(obj): return "<function>"
        if type(obj) is asyncio.Task: return "<task>"
        return super().default(obj)
    return json.dumps(self, indent = 2, cls=Encoder)

  def extend(self, **kwargs):
    return Rule(base = self, **kwargs)

  def expand(self, template):
    return expand(self, template)

  def __call__(self, files_in, files_out = None, **kwargs):
    this.tasks_total += 1
    task = self.extend()
    task.files_in = files_in
    if files_out is not None: task.files_out = files_out
    task.abs_cwd = path.split(this.mod_stack[-1].__file__)[0]
    task.set(**kwargs)
    promise = dispatch(task)
    return asyncio.create_task(promise)

################################################################################
# A trivial templating system that replaces {foo} with the value of rule.foo
# and keeps going until it can't replace anything. Templates that evaluate to
# None are replaced with the empty string.

template_regex = re.compile("{[^}]*}")

def expand_once(rule, template):
  if template is None: return ""
  result = ""
  while s := template_regex.search(template):
    result += template[0:s.start()]
    exp = template[s.start():s.end()]
    try:
      replacement = eval(exp[1:-1], globals(), rule)
      if replacement is not None: result += join(replacement)
    except Exception:
      result += exp
    template = template[s.end():]
  result += template
  return result

def expand(rule, template):
  if type(template) is list: return [expand(rule, t) for t in template]

  for _ in range(100):
    if rule.debug: log(f"expand \"{template}\"")
    new_template = expand_once(rule, template)
    if template == new_template:
      if template_regex.search(template):
        err(f"Expanding '{template[0:20]}' is stuck in a loop")
      return template
    template = new_template
  err(f"Expanding '{template[0:20]}...' failed to terminate")

################################################################################
# Checks if a task needs to be re-run, and returns a non-empty reason if so.

def mtime(filename):
  global mtime_calls
  this.mtime_calls += 1
  return path.getmtime(filename)

def needs_rerun(task):
  files_in  = task.abs_files_in
  files_out = task.abs_files_out

  # Forced tasks always run.
  if task.force:
    return f"Files {task.files_out} forced to rebuild"

  # Tasks with no inputs (generators?) always run.
  if not files_in:
    return "Always rebuild a target with no inputs"

  # Tasks with no outputs run if any of their inputs were rebuilt.
  if not files_out:
    for file in files_in:
      if file in this.all_rebuilt:
        return f"Rerunning {task.command} because some inputs were rebuilt"
    if task.debug: log(f"None of {task.files_in} changed")
    return None

  # Tasks with missing outputs always run.
  for file_out in files_out:
    if not path.exists(file_out):
      return f"Rebuilding {task.files_out} because some are missing"

  min_out = min(mtime(f) for f in files_out)

  # Check the hancho file(s) that generated the task
  if max(mtime(f) for f in this.hancho_mods.keys()) >= min_out:
    return f"Rebuilding {task.files_out} because its .hancho files have changed"

  # Check user-specified deps.
  if task.deps and max(mtime(f) for f in task.deps) >= min_out:
    return f"Rebuilding {task.files_out} because a manual dependency has changed"

  # Check GCC-format depfile, if present.
  if task.depfile:
    abs_depfile = path.abspath(path.join(
      this.hancho_root,
      task.expand(task.build_dir),
      task.expand(task.depfile)
    ))
    if path.exists(abs_depfile):
      if task.debug: log(f"Found depfile {abs_depfile}")
      deplines = open(abs_depfile).read().split()
      deplines = [d for d in deplines[1:] if d != '\\']
      if deplines and max(mtime(f) for f in deplines) >= min_out:
        return f"Rebuilding {task.files_out} because a dependency in {abs_depfile} has changed"

  # Check input files.
  if files_in and max(mtime(f) for f in files_in) >= min_out:
    return f"Rebuilding {task.files_out} because an input has changed"

  # All checks passed, so we don't need to rebuild this output.
  if task.debug: log(f"Files {task.files_out} are up to date")

  # All deps were up-to-date, nothing to do.
  return None

################################################################################
# Actually runs the command, either by calling it or running it in a subprocess

async def run_command(task):

  # Print the status line and debug information
  this.tasks_index += 1
  log(f"[{this.tasks_index}/{this.tasks_total - this.tasks_skip}] {task.expand(task.desc)}",
      sameline = not task.verbose)
  if task.verbose or task.debug:
    log(f"Reason: {task.reason}")
    if type(task.command) is str:
      log(f"{task.expand(task.command)}")
    if task.debug:
      log(task)

  # Early exit if this is just a dry run
  if task.dryrun:
    return task.abs_files_out

  # Custom commands just get await'ed and then early-out'ed.
  if callable(task.command):
    result = await task.command(task)
    if result is None:
      log(f"\x1B[31mFAILED\x1B[0m: {task.expand(task.desc)}")
    this.tasks_pass += 1
    return result

  # Non-string non-callable commands are not valid
  if not type(task.command) is str:
    err(f"Don't know what to do with {task.command}")

  # Dispatch the subprocess via asyncio and then await the result.
  proc = await asyncio.create_subprocess_shell(
    task.expand(task.command),
    stdout = asyncio.subprocess.PIPE,
    stderr = asyncio.subprocess.PIPE)
  (stdout_data, stderr_data) = await proc.communicate()
  task.stdout = stdout_data.decode()
  task.stderr = stderr_data.decode()
  task.returncode = proc.returncode

  # Print command output if needed
  if not task.quiet and (task.stdout or task.stderr):
    if task.stderr: log(task.stderr, end="")
    if task.stdout: log(task.stdout, end="")

  # Task complete, check the task return code
  if task.returncode:
    log(f"\x1B[31mFAILED\x1B[0m: {task.expand(task.desc)}")
    this.tasks_fail += 1
    return None

  # Task complete, check if it actually updated all the output files
  if task.files_in and task.files_out:
    if second_reason := needs_rerun(task):
      log(f"\x1B[33mFAILED\x1B[0m: Task \"{task.expand(task.desc)}\" still needs rerun after running!")
      log(f"Reason: {second_reason}")
      this.tasks_fail += 1
      return None

  # Task passed, return the output file list
  this.all_rebuilt.update(task.abs_files_out)
  this.tasks_pass += 1
  return task.abs_files_out

################################################################################
# Does all the bookkeeping and depedency checking, then runs the command if
# needed.

async def dispatch(task):
  # Check for missing fields
  if not task.command:       err(f"Command missing for input {task.files_in}!")
  if task.files_in is None:  err("Task missing files_in")
  if task.files_out is None: err("Task missing files_out")

  # Flatten all filename promises in any of the input filename arrays.
  task.files_in  = await flatten_async(task.files_in)
  task.files_out = await flatten_async(task.files_out)
  task.deps      = await flatten_async(task.deps)

  # Early-out with no result if any of our inputs or outputs are None (failed)
  if None in task.files_in: return None
  if None in task.files_out: return None
  if None in task.deps: return None

  # Do the actual template expansion to produce real filename lists
  task.files_in  = task.expand(task.files_in)
  task.files_out = task.expand(task.files_out)
  task.deps      = task.expand(task.deps)

  # Prepend directories to filenames and then normalize + absolute them.
  # If they're already absolute, this does nothing.
  src_dir   = path.relpath(task.abs_cwd, this.hancho_root)
  build_dir = path.join(task.expand(task.build_dir), src_dir)

  task.abs_files_in  = [path.abspath(path.join(this.hancho_root, src_dir,   f)) for f in task.files_in]
  task.abs_files_out = [path.abspath(path.join(this.hancho_root, build_dir, f)) for f in task.files_out]
  task.abs_deps      = [path.abspath(path.join(this.hancho_root, src_dir,   f)) for f in task.deps]

  # Strip hancho_root off the absolute paths to produce root-relative paths
  task.files_in  = [path.relpath(f, this.hancho_root) for f in task.abs_files_in]
  task.files_out = [path.relpath(f, this.hancho_root) for f in task.abs_files_out]
  task.deps      = [path.relpath(f, this.hancho_root) for f in task.abs_deps]

  # Check for duplicate task outputs
  for file in task.abs_files_out:
    if file in this.hancho_outs:
      err(f"Multiple rules build {file}!")
    this.hancho_outs.add(file)

  # Check if we need a rebuild
  task.reason = needs_rerun(task)
  if not task.reason:
    this.tasks_skip += 1
    return task.abs_files_out

  # Make sure our output directories exist
  for file_out in task.abs_files_out:
    if dirname := path.dirname(file_out):
      os.makedirs(dirname, exist_ok = True)

  # OK, we're ready to start the task. Run it while holding a semaphore so we
  # don't run too many tasks at once.
  async with this.semaphore:
    return await run_command(task)

################################################################################

# We set this to None first so that this.config.base gets sets to None in the
# next line.
this.config = None

this.config = Rule(
  filename  = "build.hancho",
  chdir     = None,
  jobs      = os.cpu_count(),
  verbose   = False,
  quiet     = False,
  dryrun    = False,
  debug     = False,
  force     = False,
  desc      = "{files_in} -> {files_out}",
  files_out = [],
  deps      = [],
  expand    = expand,
  join      = join,
  len       = len,
  run_cmd   = run_cmd,
  swap_ext  = swap_ext,
  color     = color,
)

if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.add_argument('filename',          default="build.hancho", nargs="?")
  parser.add_argument('-C', '--chdir',     default="",             type=str,   help='Change directory first')
  parser.add_argument('-j', '--jobs',      default=os.cpu_count(), type=int,   help='Run N jobs in parallel (default = cpu_count, 0 = infinity)')
  parser.add_argument('-v', '--verbose',   default=False, action='store_true', help='Print verbose build info')
  parser.add_argument('-q', '--quiet',     default=False, action='store_true', help='Mute command output')
  parser.add_argument('-n', '--dryrun',    default=False, action='store_true', help='Do not run commands')
  parser.add_argument('-d', '--debug',     default=False, action='store_true', help='Print debugging information')
  parser.add_argument('-f', '--force',     default=False, action='store_true', help='Force rebuild of everything')

  (flags, unrecognized) = parser.parse_known_args()

  this.config.filename = flags.filename
  this.config.chdir    = flags.chdir
  this.config.jobs     = flags.jobs
  this.config.verbose  = flags.verbose
  this.config.quiet    = flags.quiet
  this.config.dryrun   = flags.dryrun
  this.config.debug    = flags.debug
  this.config.force    = flags.force

  result = main()
  sys.exit(result)
