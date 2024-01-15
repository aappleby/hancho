# Tinybuild is a minimal build system that focuses only on doing two things:
# 1 - Only rebuild files that need rebuilding
# 2 - Make generating build commands simple

# Build parameters can be specified globally, at rule scope, or at action scope.

import os
import re
import sys
import argparse
import asyncio
import pprint

parser = argparse.ArgumentParser()
parser.add_argument('--verbose',  default=False, action='store_true', help='Print verbose build info')
parser.add_argument('--clean',    default=False, action='store_true', help='Delete intermediate files')
parser.add_argument('--serial',   default=False, action='store_true', help='Do not parallelize commands')
parser.add_argument('--dry_run',  default=False, action='store_true', help='Do not run commands')
parser.add_argument('--debug',    default=False, action='store_true', help='Dump debugging information')
flags = parser.parse_args()

################################################################################

class ProtoDict(object):
  def __init__(self, obj=None):
    self.proto = obj

  def __getitem__(self, name):
    if name in self.__dict__: return self.__dict__[name]
    if self.proto: return self.proto[name]
    return None

  def __getattr__(self, name):
    if name in self.__dict__: return self.__dict__[name]
    if self.proto: return self.proto[name]
    return None

  #=============================================================================

  def expand(self, text):
    if self.verbose: print(f"expand: {text}")
    if text is not None:
      while re.search("{[^}]*}", text) is not None:
        text = eval("f\"" + text + "\"", None, self)
        if self.verbose: print(f"expand: {text}")
    return text

  #=============================================================================

  def dump(self, depth=0):
    for (k,v) in self.__dict__.items():
      if k == "proto": continue
      print(f"{'  ' * depth}{k} : ", end="")
      if type(v) is ProtoDict:
        print()
        v.dump(depth + 1)
      else:
        print(v)

  #=============================================================================

  def needs_rebuild(self):

    for file_in in self.files_in:
      file_in = self.expand(file_in)
      if not os.path.exists(file_in):
        print(f"Input file {file_in} missing, aborting build!")
        # Is there a better way to handle this?
        sys.exit(-1)

    for dep in self.deps:
      dep = self.expand(dep)
      if not os.path.exists(dep):
        print(f"Dependency {dep} missing, aborting build!")
        # Is there a better way to handle this?
        sys.exit(-1)

    if self.force: return True

    for file_out in self.files_out:
      file_out = self.expand(file_out)

      # Check user-specified deps
      if self.check_mtime(self.deps, file_out): return True

      # Check depfile, if present
      if os.path.exists(file_out + ".d"):
        deplines = open(file_out + ".d").read().split()
        deplines = [d for d in deplines[1:] if d != '\\']
        if self.check_mtime(deplines, file_out):
          return True

      # Check input files
      if self.check_mtime(self.files_in, file_out): return True

      # All checks passed, don't need to rebuild this output
      if self.verbose: print(f"File {self.file_out} is up to date")

    return False

  #=============================================================================

  def check_mtime(self, files_in, file_out):

    file_out = self.expand(file_out)
    if not os.path.exists(file_out):
      if self.verbose: print(f"Rebuilding {file_out} because it's missing")
      return True

    for file_in in files_in:
      file_in = self.expand(file_in)
      if os.path.getmtime(file_in) > os.path.getmtime(file_out):
        if self.verbose: print(f"Rebuilding {file_out} because it's older than dependency {file_in}")
        return True

    return False



################################################################################

def swap_ext(name, new_ext):
  return os.path.splitext(name)[0] + new_ext

def join(names, divider = ' '):
  return divider.join(names)

# Repeatedly expands 'text' as if it was a f-string until it contains no {}s.
"""
def expand(text, arg_dict, verbose = False):
  if text is not None:
    if verbose: print(f"expand: {text}")
    while re.search("{[^}]*}", text) is not None:
      text = eval("f\"" + text + "\"", {}, arg_dict)
      if verbose: print(f"expand: {text}")
  return text
"""

# Wraps scalars in a list, flattens nested lists into a single list.
def listify(x):
  if x is None: return []
  if not type(x) is list: return [x]
  result = []
  for y in x: result.extend(listify(y))
  return result

################################################################################

global_args = ProtoDict()
global_args.verbose  = flags.verbose or flags.debug
global_args.clean    = flags.clean
global_args.serial   = flags.serial
global_args.dry_run  = flags.dry_run
global_args.debug    = flags.debug
global_args.swap_ext = swap_ext
global_args.join     = join

################################################################################

proc_sem = asyncio.Semaphore(1 if flags.serial else os.cpu_count())

promise_map = {}

################################################################################

async def run_command_async(args):

  for f in args.files_in:
    if promise := promise_map.get(f, None):
      dep_result = await promise
      if dep_result != 0: return dep_result

  for d in args.deps:
    if promise := promise_map.get(d, None):
      dep_result = await promise
      if dep_result != 0: return dep_result

  if not args.needs_rebuild(): return 0

  if args.desc: print(args.expand(args.desc))

  if args.debug: args.dump(1)

  command = args.expand(args.command)

  if args.dry_run:
    print(f"Dry run: \"{command}\"")
    return 0

  if args.verbose: print(f"Command starting: \"{command}\"")

  async with proc_sem:
    proc = await asyncio.create_subprocess_shell(
      command,
      stdout = asyncio.subprocess.PIPE,
      stderr = asyncio.subprocess.PIPE)
    (stdout_data, stderr_data) = await proc.communicate()

  if proc.returncode != 0:
    print(f"Command failed: \"{command}\"")
    stderr_text = stderr_data.decode()
    if len(stderr_text): print(f"stderr =\n{stderr_text}")
  elif args.verbose:
    print(f"Command done: \"{command}\"")
    stdout_text = stdout_data.decode()
    if len(stdout_text): print(f"stdout =\n{stdout_text}")

  return proc.returncode

################################################################################

def create_rule(do_map, do_reduce, rule_dict):

  rule_args = ProtoDict(global_args)
  rule_args.__dict__.update(rule_dict)

  def run_rule(_files_in, _files_out, **kwargs):

    # Take a snapshot of the rule args and patch in our command args
    action_args = ProtoDict(rule_args)
    action_args.__dict__.update(kwargs)
    action_args.files_in  = listify(_files_in)
    action_args.files_out = listify(_files_out)
    action_args.deps      = listify(action_args.deps)
    action_args.rule      = rule_args

    # Clean files if requested
    if action_args.clean:
      for file_out in action_args.files_out:
        file_out = action_args.expand(file_out)
        if action_args.verbose:
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
        command_args = ProtoDict(action_args)
        command_args.file_in  = action_args.files_in[i]
        command_args.file_out = action_args.files_out[i]
        command_args.action   = action_args

        promise = asyncio.create_task(run_command_async(command_args))
        promise_map[file_out] = promise
      return

    # Or dispatch the command as a reduce
    if do_reduce:
      command_args = ProtoDict(action_args)
      command_args.file_in  = action_args.files_in[0]
      command_args.file_out = action_args.files_out[0]
      command_args.action   = action_args
      promise = asyncio.create_task(run_command_async(command_args))
      for file_out in action_args.files_out:
        promise_map[file_out] = promise
      return

  #----------

  return run_rule

################################################################################

def map(**kwargs):
  return create_rule(do_map = True, do_reduce = False, rule_dict = kwargs)

def reduce(**kwargs):
  return create_rule(do_map = False, do_reduce = True, rule_dict = kwargs)

async def top(build_func):
  build_func()
  await asyncio.gather(*asyncio.all_tasks() - {asyncio.current_task()})

def build(build_func):
  asyncio.run(top(build_func))

################################################################################
