#!/usr/bin/python3

import argparse, asyncio, builtins, inspect, io, json, os, re, subprocess, sys, types
from os import path

this = sys.modules[__name__]

################################################################################

line_dirty = False

def log(*args, sameline = False, **kwargs):
  global line_dirty
  if this.config.quiet: return

  if not sys.stdout.isatty(): sameline = False

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

def err(*args, **kwargs):
  log(*args, **kwargs)
  sys.exit(-1)

################################################################################

def main():
  # A reference to this module is already in sys.modules["__main__"].
  # Stash another reference in sys.modules["hancho"] so that build.hancho and
  # descendants don't try to load a second copy of us.
  sys.modules["hancho"] = this

  parser = argparse.ArgumentParser()
  parser.add_argument('filename',    default="build.hancho", nargs="?")
  parser.add_argument('-C', '--chdir',     default="",             type=str,   help='Change directory first')
  parser.add_argument('-j', '--jobs',      default=os.cpu_count(), type=int,   help='Run N jobs in parallel (default = cpu_count, 0 = infinity)')
  parser.add_argument('-v', '--verbose',   default=False, action='store_true', help='Print verbose build info')
  parser.add_argument('-q', '--quiet',     default=False, action='store_true', help='Mute command output')
  parser.add_argument('-n', '--dryrun',    default=False, action='store_true', help='Do not run commands')
  parser.add_argument('-d', '--debug',     default=False, action='store_true', help='Print debugging information')
  parser.add_argument('-f', '--force',     default=False, action='store_true', help='Force rebuild of everything')

  (flags, unrecognized) = parser.parse_known_args()

  sys.exit(asyncio.run(async_main(flags)))

################################################################################

async def async_main(flags):

  # Build rule helper methods
  def color(r = None, g = None, b = None):
    if r is None: return "\x1B[0m"
    return f"\x1B[38;2;{r};{g};{b}m"

  def join(names, divider = ' '):
    return "" if names is None else divider.join(names)

  def run_cmd(cmd):
    return subprocess.check_output(cmd, shell=True, text=True).strip()

  def swap_ext(name, new_ext):
    return path.splitext(name)[0] + new_ext

  # Initialize Hancho's global configuration object
  this.config = None # so this.config.base gets sets to None in the next line
  this.config = Rule(
    jobs      = flags.jobs,
    verbose   = flags.verbose,
    quiet     = flags.quiet,
    dryrun    = flags.dryrun,
    debug     = flags.debug,
    force     = flags.force,
    desc      = "{files_in} -> {files_out}",
    build_dir = None,
    expand    = expand,
    flatten   = flatten,
    join      = join,
    len       = len,
    run_cmd   = run_cmd,
    swap_ext  = swap_ext,
    color     = color,
  )

  # Reset all global state
  this.hancho_mods = {}
  this.hancho_outs = set()
  this.hancho_root = os.getcwd()
  this.mod_stack = []
  this.tasks_total = 0
  this.tasks_index = 0
  this.tasks_fail  = 0
  this.tasks_pass  = 0
  this.tasks_skip  = 0

  # Change directory and load top module(s).
  if flags.chdir: os.chdir(flags.chdir)
  top_module = load2(flags.filename)

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

  if this.tasks_fail != 0:
    log("hancho: some tasks failed!")
  elif this.tasks_pass == 0:
    log("hancho: no work to do.")
  else:
    log("", end="")

  if flags.chdir: os.chdir(this.hancho_root)

  return -1 if this.tasks_fail else 0

################################################################################
# The .hancho file loader does a small amount of work to keep track of the
# stack of .hancho files that have been loaded.

def load(mod_path):
  for parent_mod in this.mod_stack:
    abs_path = path.abspath(path.join(path.split(parent_mod)[0], mod_path))
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

  source = open(abs_path, "r").read()
  code = compile(source, abs_path, 'exec', dont_inherit=True)

  module = type(sys)(mod_name)
  module.__file__ = abs_path
  module.__builtins__ = builtins

  sys.path.insert(0, mod_dir)
  old_dir = os.getcwd()

  # We must chdir()s into the .hancho file directory before running it so that
  # glob() can resolve files relative to the .hancho file itself.
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

  def extend(self, **kwargs):
    return Rule(base = self, **kwargs)

  def expand(self, template):
    return expand(self, template)

  def __call__(self, **kwargs):
    this.tasks_total += 1
    task = self.extend(**kwargs)
    task.meta_deps = list(this.mod_stack)
    task.cwd = path.split(this.mod_stack[-1])[0]
    promise = dispatch(task)
    return asyncio.create_task(promise)

################################################################################
# A trivial templating system that replaces {foo} with the value of rule.foo
# and keeps going until it can't replace anything. Templates that evaluate to
# None are replaced with the empty string.

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
    except Exception:
      result += exp
    template = template[s.end():]
  result += template
  return result

def expand(self, template):
  for _ in range(100):
    if self.debug: log(f"expand \"{template}\"")
    new_template = expand_once(self, template)
    if template == new_template:
      if template_regex.search(template):
        err(f"Expanding '{template[0:20]}' is stuck in a loop")
      return template
    template = new_template
  err(f"Expanding '{template[0:20]}...' failed to terminate")

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
  if task.debug: log(f"Files {files_out} are up to date")

  # All deps were up-to-date, nothing to do.
  return None

################################################################################
# Slightly weird method that flattens out an arbitrarily-nested list of strings
# and promises-for-strings into a flat array of actual strings.

async def flatten(x):
  if x is None: return []
  if inspect.isawaitable(x):
    x = await x
  if not type(x) is list:
    return [x]
  result = []
  for y in x: result.extend(await flatten(y))
  return result

################################################################################
# Actually runs the command, either by calling it or running it in a subprocess

async def run_command(task):

  this.tasks_index += 1

  # Print the status line and debug information
  if not task.quiet:
    log(f"[{this.tasks_index}/{this.tasks_total - this.tasks_skip}] {task.expand(task.desc)}",
        sameline = not task.verbose)
    if task.verbose or task.debug:
      log(f"Rebuild reason: {task.reason}")
      if type(task.command) is str:
        log(f"{task.expand(task.command)}")
      if task.debug:
        log(task)

  # Early exit if this is just a dry run
  if task.dryrun:
    return task.abs_files_out

  # Custom commands just get await'ed and then early-out.
  if callable(task.command):
    result = await task.command(task)
    if result is None:
      log(f"\x1B[31mFAILED\x1B[0m: {task.expand(task.desc)}")
    return result

  # Non-string non-function commands are not valid
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
    log(f"\x1B[31mFAILED\x1B[0m: {task.desc}")
    this.tasks_fail += 1
    return None

  # Task complete, check if it actually updated all the output files
  if task.files_in and task.files_out:
    if second_reason := needs_rerun(task):
      log(f"\x1B[33mFAILED\x1B[0m: Task \"{desc}\" still needs rerun after running!")
      log(f"Reason: {second_reason}")
      this.tasks_fail += 1
      return None

  # Task passed, return the output file list
  this.tasks_pass += 1
  return task.abs_files_out

################################################################################
# Does all the bookkeeping and depedency checking, then runs the command if
# needed.

async def dispatch(task):

  # Expand our build paths
  src_dir   = path.relpath(task.cwd, this.hancho_root)
  build_dir = path.join(task.expand(task.build_dir), src_dir)

  # Flatten all filename promises in any of the input filename arrays.
  if task.files_in is None:  err("Task missing files_in")
  if task.files_out is None: err("Task missing files_out")
  task.files_in  = await flatten(task.files_in)
  task.files_out = await flatten(task.files_out)
  task.deps      = await flatten(task.deps)

  # Early-out with no result if any of our inputs or outputs are None (failed)
  if None in task.files_in:  return None
  if None in task.files_out: return None
  if None in task.deps:      return None

  # Do the actual template expansion to produce real filename lists
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
  task.abs_files_in  = [path.abspath(path.join(this.hancho_root, f)) for f in task.files_in]
  task.abs_files_out = [path.abspath(path.join(this.hancho_root, f)) for f in task.files_out]
  task.abs_deps      = [path.abspath(path.join(this.hancho_root, f)) for f in task.deps]

  # And now strip hancho_root off the absolute paths to produce the final
  # root-relative paths
  task.files_in  = [path.relpath(f, this.hancho_root) for f in task.abs_files_in]
  task.files_out = [path.relpath(f, this.hancho_root) for f in task.abs_files_out]
  task.deps      = [path.relpath(f, this.hancho_root) for f in task.abs_deps]

  # Check for duplicate task outputs
  for file in task.abs_files_out:
    if file in this.hancho_outs:
      err(f"Multiple rules build {file}!")
    this.hancho_outs.add(file)

  # Check for valid command
  if not task.command:
    err(f"Command missing for input {task.files_in}!")

  # Check if we need a rebuild
  task.reason = needs_rerun(task)
  if task.force: task.reason = f"Files {task.abs_files_out} forced to rebuild"
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

if __name__ == "__main__": main()
