# Tinybuild is a minimal build system that focuses only on doing two things:
# 1 - Only rebuild files that need rebuilding
# 2 - Make generating build commands simple

# Build parameters can be specified globally, at rule scope, or at action scope.

import os
import re
import sys
import argparse
import asyncio
import subprocess
from functools import partial

"""
Special args
  desc:      Description of the rule printed every time it runs
  command:   Command to run for the rule
  files_in:  Either a single filename or a list of filenames
  files_out: Either a single filename or a list of filenames
  deps:      Additional dependencies for the rule
  force:     Makes the rule always run even if dependencies are up to date
"""

parser = argparse.ArgumentParser()
parser.add_argument('--verbose',  default=False, action='store_true', help='Print verbose build info')
parser.add_argument('--clean',    default=False, action='store_true', help='Delete intermediate files')
parser.add_argument('--serial',   default=False, action='store_true', help='Do not parallelize commands')
parser.add_argument('--dry_run',  default=False, action='store_true', help='Do not run commands')
parser.add_argument('--debug',    default=False, action='store_true', help='Dump debugging information')
parser.add_argument('--dotty',    default=False, action='store_true', help='Dump dependency graph as dotty')
flags = parser.parse_args()

################################################################################
# Minimal JSON-style pretty printer for ProtoArgs

def dump_dict(d, depth):
  print("{")
  for (k,v) in d.items():
    if k == "prototype": continue
    print("  " * (depth + 1), end="")
    print(f"\"{k}\" : ", end="")
    dump_val(v, depth + 1)
    print(",")
  print(("  " * depth) + "}", end="")

def dump_list(l, depth):
  print("[", end="")
  for s in l:
    dump_val(s, depth)
    print(", ", end="");
  print("]", end="")

def dump_val(v, depth):
  if   v is None:            print("null")
  elif type(v) is ProtoArgs: dump_dict(v.__dict__, depth)
  elif type(v) is str:       print(f"\"{v}\"", end="")
  elif type(v) is dict:      dump_dict(v, depth + 1)
  elif type(v) is list:      dump_list(v, depth + 1)
  elif callable(v):          print(f"\"{v}\"", end="")
  else:                      print(v, end="")

################################################################################

class ProtoArgs(object):
  """
  ProtoArgs is a Javascript-style prototypal-inheritance text-expansion tool.
  It allows you to create objects with trees of attributes (and attribute
  inheritance) and use those trees to repeatedly expand Python strings ala
  f-strings until they no longer contain {}s.

  ProtoArgs instances behave like Javascript objects. String fields can
  contain Python expressions in curly braces, which will be evaluated when
  the args are used to "expand" a template string.

  ```
    args1 = ProtoArgs()
    args1.foo = "foo_option1"
    args1.bar = "bar_option77"
    args1.message = "Foo is {foo}, bar is {bar}, undefined is {undefined}."
  ```

  ProtoArgs can use prototype-style inheritance. This "args2" instance will
  appear to contain all the fields of args1, but can override them.

  ```
    args2 = ProtoArgs(args1)
    args2.bar = "bar_override"
  ```

  ProtoArgs can be used to expand a string containing {}s. Variable lookup
  will happen using the arg object itself as a context, with lookup
  proceeding up the prototype chain until a match is found (or "" if there
  was no match).

  Prints "Foo is foo_option1, bar is bar_override, undefined is ."
  ```
    print(args2.expand(args2.message))
  ```
  """

  def __init__(self, **kwargs):
    self.prototype = getattr(kwargs, "prototype", None)
    self.__dict__.update(kwargs)

  def __getitem__(self, name):
    if name in self.__dict__: return self.__dict__[name]
    if self.prototype: return self.prototype[name]
    return ""

  def __getattr__(self, name):
    return self.__getitem__(name)

  def expand(self, text):
    if self.debug: print(f"expand: {text}")
    if text is not None:
      while re.search("{[^}]*}", text) is not None:
        text = eval("f\"" + text + "\"", None, self)
        if self.debug: print(f"expand: {text}")
    return text

  def dump(self, depth = 0):
    dump_dict(self.__dict__, depth)

################################################################################

def check_mtime(files_in, file_out):

  for file_in in files_in:
    if os.path.getmtime(file_in) > os.path.getmtime(file_out):
      if flags.verbose: print(f"Rebuilding {file_out} because it's older than dependency {file_in}")
      return True

  return False

################################################################################

def needs_rebuild(args):

  if args.force: return f"File {args.file_out} forced to rebuild"

  for file_out in args.files_out:

    if not os.path.exists(file_out):
      return f"Rebuilding {file_out} because it's missing"

    # Check user-specified deps
    if check_mtime(args.deps, file_out):
      return f"Rebuilding {file_out} because a dependency has changed"

    # Check depfile, if present
    if os.path.exists(file_out + ".d"):
      deplines = open(file_out + ".d").read().split()
      deplines = [d for d in deplines[1:] if d != '\\']
      if check_mtime(deplines, file_out):
        return f"Rebuilding {file_out} because a dependency in {args.file_out}.d has changed"

    # Check input files
    if check_mtime(args.files_in, file_out):
      return f"Rebuilding {file_out} because an input has changed"

    # All checks passed, don't need to rebuild this output
    if flags.verbose: print(f"File {args.file_out} is up to date")

  return ""

################################################################################

def swap_ext(name, new_ext):
  return os.path.splitext(name)[0] + new_ext

def join(names, divider = ' '):
  if names is None: return ""
  return divider.join(names)

# Wraps scalars in a list, flattens nested lists into a single list.
def listify(x):
  if x is None: return []
  if not type(x) is list: return [x]
  result = []
  for y in x: result.extend(listify(y))
  return result

################################################################################

global_args = ProtoArgs(
  swap_ext = swap_ext,
  join     = join,
  desc     = "{files_in} -> {files_out}",
)

promise_map = {}

################################################################################

proc_sem = asyncio.Semaphore(1 if flags.serial else os.cpu_count())

async def run_command_async(args):

  # Wait on all our input files to be updated
  for file_in in args.files_in:
    if promise := promise_map.get(file_in, None):
      dep_result = await promise
      if dep_result != 0: return dep_result
    if file_in and not os.path.exists(file_in):
      print(args.expand(args.desc))
      print(f"Input file {file_in} missing!")
      return -1

  # Wait on all our dependencies to be updated
  for dep in args.deps:
    if promise := promise_map.get(dep, None):
      dep_result = await promise
      if dep_result != 0: return dep_result
    if dep and not os.path.exists(dep):
      print(args.expand(args.desc))
      print(f"Dependency {dep} missing!")
      return -1

  async with proc_sem:
    # Print description
    print(args.expand(args.desc))

    # Check if we need a rebuild
    reason = needs_rebuild(args)
    if not reason: return 0
    if flags.verbose: print(reason)

    # Print debug dump of args if needed
    if flags.debug:
      args.dump()
      print()

    # Expand "command" as late as possible, just in case some previous action
    # changed some arg somehow.
    command = args.expand(args.command)
    if not command:
      print(f"Command missing for input {args.file_in}!")
      return -1

    # Early-exit if this is just a dry run
    if flags.dry_run:
      print(f"Dry run: \"{command}\"")
      print()
      return 0

    # OK, we're ready to start the subprocess
    if flags.verbose: print(f"Command starting: \"{command}\"")

    # In serial mode we run the subprocess synchronously.
    if flags.serial:
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

    # Command done, print output and fulfill our promise with the return code.
    if returncode != 0:
      print(f"Command failed: \"{command}\"")
      stderr_text = stderr_data.decode()
      if len(stderr_text): print(f"stderr =\n{stderr_text}")
    elif flags.verbose:
      print(f"Command done: \"{command}\"")
      stdout_text = stdout_data.decode()
      if len(stdout_text): print(f"stdout =\n{stdout_text}")
      print()

    return returncode

################################################################################

def queue_command(files_in, files_out, action_args):
  command_args = ProtoArgs(
    prototype   = action_args,
    file_in     = files_in[0],
    file_out    = files_out[0],
    action_args = action_args,
  )

  coroutine = run_command_async(command_args)
  promise = asyncio.create_task(coroutine)
  for output in files_out:
    promise_map[output] = promise

################################################################################

def eval_rule(
    do_map,
    do_reduce,
    rule_args,
    files_in = [],
    files_out = [],
    deps = [],
    **kwargs):

  # Build our per-action args by expanding the templates in rule_args
  action_args = ProtoArgs(
    prototype = rule_args,
    files_in  = [rule_args.expand(f) for f in listify(files_in)],
    files_out = [rule_args.expand(f) for f in listify(files_out)],
    deps      = [rule_args.expand(f) for f in listify(deps)],
    **kwargs,
    rule_args = rule_args,
  )

  # Print dotty graph if requested
  if flags.dotty:
    for file_in in action_args.files_in:
      for file_out in action_args.files_out:
        print(f"  \"{file_in}\" -> \"{file_out}\";")
    return

  # Clean files if requested
  if flags.clean:
    for file_out in action_args.files_out:
      if flags.verbose:
        print(f"rm -f {file_out}")
      os.system(f"rm -f {file_out}")
    return

  # Make sure our output directories exist
  for file_out in action_args.files_out:
    if dirname := os.path.dirname(file_out):
      os.makedirs(dirname, exist_ok = True)

  # Dispatch the command as a map
  if do_map:
    assert len(action_args.files_in) == len(action_args.files_out)
    for i in range(len(action_args.files_in)):
      queue_command([action_args.files_in[i]], [action_args.files_out[i]], action_args)

  # Or dispatch the command as a reduce
  elif do_reduce:
    queue_command(action_args.files_in, action_args.files_out, action_args)

################################################################################

def create_rule(do_map, do_reduce, rule_dict):
  rule_args = ProtoArgs(
    prototype = global_args,
    **rule_dict,
    global_args = global_args,
  )
  return partial(eval_rule, do_map, do_reduce, rule_args)

################################################################################

def map(**kwargs):
  return create_rule(do_map = True, do_reduce = False, rule_dict = kwargs)

def reduce(**kwargs):
  return create_rule(do_map = False, do_reduce = True, rule_dict = kwargs)

async def top(build_func):
  if flags.dotty: print("digraph {")
  build_func()
  await asyncio.gather(*asyncio.all_tasks() - {asyncio.current_task()})
  if flags.dotty: print("}")

def run(build_func):
  asyncio.run(top(build_func))

################################################################################
