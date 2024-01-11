# Tinybuild is a minimal build system that focuses only on doing two things:
# 1 - Only rebuild files that need rebuilding
# 2 - Make generating build commands simple

# Build parameters can be specified globally, at rule scope, or at command
# scope.

################################################################################

import os
import re
import sys
import multiprocessing
import argparse
import atexit

import pprint
pp = pprint.PrettyPrinter(indent=2, width=120)

# FIXME Emit ninja file?

def swap_ext(name, new_ext):
  return os.path.splitext(name)[0] + new_ext

def join(names, divider = ' '):
  return divider.join(names)

def expand(text, arg_dict):
  if text is not None:
    while re.search("{[^}]+}", text) is not None:
      text = eval("f\"" + text + "\"", {}, arg_dict)
  return text

def listify(x):
  return x if type(x) is list else [x]

parser = argparse.ArgumentParser()
parser.add_argument('--verbose',  default=False, action='store_true', help='Print verbose build info')
parser.add_argument('--clean',    default=False, action='store_true', help='Delete intermediate files')
parser.add_argument('--serial',   default=False, action='store_true', help='Do not parallelize commands')
parser.add_argument('--dry_run',  default=False, action='store_true', help='Do not run commands')
parser.add_argument('--debug',    default=False, action='store_true', help='Dump debugging information')
options = parser.parse_args()

global_config = {
  "verbose"    : options.verbose or options.debug,
  "clean"      : options.clean,
  "serial"     : options.serial,
  "dry_run"    : options.dry_run,
  "debug"      : options.debug,
  "swap_ext"   : swap_ext,
  "join"       : join
}

################################################################################

def check_deps(files_in, file_out, file_kwargs):
  file_out = expand(file_out, file_kwargs)
  if not os.path.exists(file_out):
    if file_kwargs['verbose']:
      print(f"Rebuilding {file_out} because it's missing")
    return True;

  for file_in in files_in:
    file_in = expand(file_in, file_kwargs)
    if os.path.exists(file_in):
      if os.path.getmtime(file_in) > os.path.getmtime(file_out):
        if file_kwargs['verbose']:
          print(f"Rebuilding {file_out} because it's older than dependency {file_in}")
        return True
    else:
      print(f"Dependency {file_in} missing, aborting build!")
      # Is there a better way to handle this?
      sys.exit(-1)
  return False

################################################################################

def needs_rebuild(files_in, files_out, file_kwargs):
  if file_kwargs.get("force", False):
    return True

  files_in  = listify(files_in)
  files_out = listify(files_out)

  for file_out in files_out:
    file_out = expand(file_out, file_kwargs)

    # Check user-specified deps
    if check_deps(file_kwargs.get("deps", []), file_out, file_kwargs):
      return True

    # Check depfile, if present
    if os.path.exists(file_out + ".d"):
      deps = open(file_out + ".d").read().split()
      deps = [d for d in deps[1:] if d != '\\']
      if check_deps(deps, file_out, file_kwargs):
        return True

    # Check input files
    if check_deps(files_in, file_out, file_kwargs):
      return True

    # All checks passed, don't need to rebuild this output
    if file_kwargs.get("verbose", False):
      print(f"File {file_out} is up to date")

  return False

################################################################################

def await_array(input):
  result = []

  for f in input:

    if type(f) is str:
      result.append(f)
      continue

    f = f.get()
    if type(f) is str:
      result.append(f)
      continue

    return None

  return result

################################################################################


def run_command(file_kwargs):

  files_in  = file_kwargs["files_in"]
  files_out = file_kwargs["files_out"]

  files_in = await_array(files_in)

  if not needs_rebuild(files_in, files_out, file_kwargs):
    return 0

  if desc := file_kwargs.get("desc", None):
    print(expand(desc, file_kwargs))

  if file_kwargs["debug"]:
    pp.pprint(file_kwargs)

  command = expand(file_kwargs["command"], file_kwargs)
  result = -1

  if file_kwargs["verbose"]:
    print(f"Command starting: \"{command}\"")

  if file_kwargs.get("dry_run", False):
    print(f"Dry run: \"{command}\"")
    return 0

  result = os.system(command)
  if result:
    print(f"Command failed: \"{command}\"")
  elif file_kwargs["verbose"]:
    print(f"Command done: \"{command}\"")

  return result

################################################################################

def create_rule(do_map, do_reduce, kwargs):
  # Take a snapshot of the global config at the time the rule is defined
  global_kwargs = dict(global_config)

  # Take a snapshot of the config kwargs and patch in our rule kwargs
  rule_kwargs = dict(global_kwargs)
  rule_kwargs.update(kwargs)
  rule_kwargs["global_args"] = global_kwargs;

  def rule(files_in, files_out, **kwargs):
    files_in  = listify(files_in)
    files_out = listify(files_out)

    # Make sure our output directories exist
    for file_out in files_out:
      if dirname := os.path.dirname(file_out):
        os.makedirs(dirname, exist_ok = True)

    if do_map:
      assert len(files_in) == len(files_out)

    # Take a snapshot of the rule kwargs and patch in our command kwargs
    command_kwargs = dict(rule_kwargs)
    command_kwargs.update(kwargs)
    command_kwargs["rule_args"] = rule_kwargs
    command_kwargs["files_in"]  = files_in
    command_kwargs["files_out"] = files_out

    ########################################
    # Clean files if requested

    if command_kwargs.get("clean", None):
      for file_out in command_kwargs["files_out"]:
        file_out = expand(file_out, command_kwargs)
        if command_kwargs.get("verbose", False):
          print(f"rm -f {file_out}")
        os.system(f"rm -f {file_out}")
      return []

    results = []

    ########################################
    # Dispatch the command as a map

    if do_map:
      for i in range(len(files_in)):
        file_in  = files_in[i]
        file_out = files_out[i]

        file_kwargs = dict(command_kwargs)
        file_kwargs["file_in"]      = file_in
        file_kwargs["file_out"]     = file_out

        if file_kwargs["serial"]:
          run_command(file_kwargs)
        else:
          result = pool.apply_async(run_command, [file_kwargs])
          results.append(result)

    ########################################
    # Dispatch the command as a reduce

    if do_reduce:
      file_kwargs = dict(command_kwargs)
      file_kwargs["command_args"]   = command_kwargs
      file_kwargs["file_in"]  = files_in[0]
      file_kwargs["file_out"] = files_out[0]
      result = pool.apply_async(run_command, [file_kwargs])
      results.append(result)

    ########################################
    # Block until all tasks done

    sum = 0
    for result in results:
      r = result.get()
      sum = sum + result.get()
    if sum:
      print("Command failed, aborting build")
      sys.exit(-1)

    return files_out

  ########################################

  return rule

################################################################################

def map(**kwargs):
  return create_rule(do_map = True, do_reduce = False, kwargs = kwargs)

def reduce(**kwargs):
  return create_rule(do_map = False, do_reduce = True, kwargs = kwargs)

def finish():
  pool.close()
  pool.join()

def blah():
  pool.close()
  pool.join()
  print("blkasjdlkjasdjf")

atexit.register(blah)

################################################################################

# Warning - this _must_ be done _after_ the rest of the module is initialized
pool = multiprocessing.Pool(multiprocessing.cpu_count())
