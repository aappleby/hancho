#!/usr/bin/python3

"""
Hancho is a minimal build system that focuses only on doing two things:
1 - Only rebuild files that need rebuilding
2 - Make generating build commands simple

Build parameters can be specified globally, at rule scope, or at action scope.

>>> import tempfile
>>> tmpdirname = tempfile.TemporaryDirectory()
>>> print(tmpdirname)                                        #doctest: +ELLIPSIS
<TemporaryDirectory '/tmp/tmp...'>
>>> os.chdir(tmpdirname.name)
>>> print(os.getcwd())                                       #doctest: +ELLIPSIS
/tmp/tmp...

>>> import hancho
>>> print_hello = hancho.rule(command = "echo hello world")
>>> def my_task():
...   #print_hello(input_files = ["foo.c"], output_files = ["foo.o"])
...   pass
>>> hancho.run(my_task)
"""

import asyncio
import atexit
import os
import re
import sys
import subprocess
from functools import partial

hancho_loop  = asyncio.new_event_loop()
hancho_tasks = []

################################################################################
# Minimal JSON-style pretty printer for Config

def dump_dict(d, depth):
  print("{")
  for (k,v) in d.items():
    #if k == "prototype": continue
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
  if   v is None:            print("null", end="")
  elif type(v) is Config:
    try:
      self_name = object.__getattribute__(v, 'name')
    except AttributeError:
      self_name = "[?name?]"
    print(f"{self_name} ", end="")
    dump_dict(object.__getattribute__(v, "__dict__"), depth)

  elif type(v) is str:       print(f"\"{v}\"", end="")
  elif type(v) is dict:      dump_dict(v, depth + 1)
  elif type(v) is list:      dump_list(v, depth + 1)
  elif callable(v):          print(f"\"{v}\"", end="")
  else:                      print(v, end="")

################################################################################

def swap_ext(name, new_ext):
  """
  Swaps the extension of a filename.

    >>> filename = "src/foo.cpp"
    >>> swap_ext(filename, ".hpp")
    'src/foo.hpp'
  """
  return os.path.splitext(name)[0] + new_ext

def join(names, divider = ' '):
  """
  Sticks strings together with a space.

     >>> filenames = ["foo.cpp", "bar.cpp", "baz.cpp"]
     >>> join(filenames)
     'foo.cpp bar.cpp baz.cpp'
  """
  if names is None: return ""
  return divider.join(names)

def listify(x):
  """
  Wraps scalars in a list, flattens nested lists into a single list.

    >>> listify(None)
    []
    >>> listify("asdf")
    ['asdf']
    >>> listify([[[1]],[[[[2]]]],[[3],[4],[[5]]]])
    [1, 2, 3, 4, 5]
  """
  if x is None: return []
  if not type(x) is list: return [x]
  result = []
  for y in x: result.extend(listify(y))
  return result

################################################################################

template_regex = re.compile("{[^}]*}")

def is_template(text):
  return type(text) is str and template_regex.search(text) is not None

#-------------------------------------------------------------------------------

expand_depth = 0

def expand(text, context):
  global expand_depth
  debug = resolve(context, "debug")
  if text is None:
    return ""

  if type(text) is list:
    return [expand(t, context) for t in text]

  if is_template(text):
    while True:
      if debug:
        try:
          self_name = object.__getattribute__(context, "name")
        except AttributeError:
          self_name = "[?name?]"
        print(f"{'  ' * expand_depth}{self_name}.expand \"{text}\"")
      expand_depth = expand_depth + 1
      try:
        text = eval("f\"" + text + "\"", globals(), context)
      except AttributeError as e:
        break
      expand_depth = expand_depth - 1
      if not is_template(text): break
    if debug: print(f"{'  ' * expand_depth }result \"{text}\"")
    return text

  return text

#-------------------------------------------------------------------------------

def resolve(self, name):
  #print(f"resolve {name}")
  result = None
  cursor = self
  while cursor is not None:
    # See if the current object in the prototype chain contains {name}
    try:
      result = object.__getattribute__(cursor, name)
      if result is not None: break
    except AttributeError:
      pass

    # Didn't find it, step up the prototype chain.
    try:
      cursor = object.__getattribute__(cursor, "prototype")
    except AttributeError:
      break

  # Context lookup failed, check globals.
  if result is None:
    if name in globals():
      return globals()[name]
    if name in globals()["__builtins__"]:
      return globals()["__builtins__"][name]

  if result is None:
    raise AttributeError(f"Could not resolve attribute {name}")
  return result

################################################################################

config = None

class Config(object):
  """
  Config is a Javascript-style prototypal-inheritance text-expansion tool.
  It allows you to create objects with trees of attributes (and attribute
  inheritance) and use those trees to repeatedly expand Python strings ala
  f-strings until they no longer contain {}s.

  Config instances behave like Javascript objects. String fields can
  contain Python expressions in curly braces, which will be evaluated when
  the args are used to "expand" a template string.

    >>> args1 = Config()
    >>> args1.foo = "foo_option1"
    >>> args1.bar = "bar_option77"
    >>> args1.message = "Foo is {foo}, bar is {bar}, undefined is {undefined}."

  Config can use prototype-style inheritance. This "args2" instance will
  appear to contain all the fields of args1, but can override them.

    >>> args2 = Config(args1)
    >>> args2.bar = "bar_override"

  Config can be used to expand a string containing {}s. Variable lookup
  will happen using the arg object itself as a context, with lookup
  proceeding up the prototype chain until a match is found (or "" if there
  was no match).

    >>> print(expand(args2.message, args2))
    Foo is foo_option1, bar is bar_override, undefined is .

  """

  #----------------------------------------
  def __init__(self, *, prototype, **kwargs):
    for name in kwargs:
        setattr(self, name, kwargs[name])
    self.prototype = prototype

  #----------------------------------------

  def __getitem__(self, name):
    result = resolve(self, name)
    result = expand(result, self)
    #self_name = object.__getattribute__(self, "name")
    #print(f"__getitem__ {self_name} {name} = \"{result}\"")
    return result

  #----------------------------------------

  def __getattribute__(self, name):
    result = resolve(self, name)
    result = expand(result, self)
    #self_name = object.__getattribute__(self, "name")
    #print(f"__getattribute__ {self_name} {name} = \"{result}\"")
    return result

  #----------------------------------------

  def dump(self, depth = 0):
    dump_val(self, depth)
    print()

  #----------------------------------------

  def queue(self):
    if all(output in promise_map for output in self.files_out):
      #print(f"####### Output already built for {self.files_out}")
      return

    if any(output in promise_map for output in self.files_out):
      print(f"####### Overlapping output sets for {self.files_out}")
      sys.exit(-1)

    coroutine = self.run_command_async()
    promise = hancho_loop.create_task(coroutine)
    hancho_tasks.append(promise)
    for output in self.files_out:
      promise_map[output] = promise

  #----------------------------------------

  def check_mtime(self, files_in, file_out):
    for file_in in files_in:
      if os.path.getmtime(file_in) > os.path.getmtime(file_out):
        return True
    return False

  #----------------------------------------

  def needs_rebuild(self):

    #if not self.files_out:
    #  return f"Rebuilding {self.file_in} because it has no outputs"

    for file_out in self.files_out:
      # Check for missing outputs.
      if not os.path.exists(file_out):
        return f"Rebuilding {file_out} because it's missing"

      # Check user-specified deps.
      if self.check_mtime(self.deps, file_out):
        return f"Rebuilding {file_out} because a dependency has changed"

      # Check depfile, if present.
      if os.path.exists(file_out + ".d"):
        deplines = open(file_out + ".d").read().split()
        deplines = [d for d in deplines[1:] if d != '\\']
        if self.check_mtime(deplines, file_out):
          return f"Rebuilding {file_out} because a dependency in {file_out}.d has changed"

      # Check input files.
      if self.check_mtime(self.files_in, file_out):
        return f"Rebuilding {file_out} because an input has changed"

      # All checks passed, so we don't need to rebuild this output.
      if self.debug: print(f"File {self.file_out} is up to date")

    # All deps were up-to-date, nothing to do.
    sys.stdout.flush()
    return ""

  #----------------------------------------

  async def check_deps(self, deps):
    for file in deps:
      if promise := promise_map.get(file, None):
        dep_result = await promise
        if dep_result != 0: return dep_result
      if file and not os.path.exists(file):
        if self.description:
          print(self.description)
        else:
          print(self.command)
        print(f"Dependency {file} missing!")
        sys.stdout.flush()
        return -1
    pass

  #----------------------------------------
  # Our generic rule dispatcher has dispatch_as_map/rule_args bound by
  # create_rule(), and the rest are provided at rule invocation.

  def __call__(self, *, files_in, files_out, deps, **kwargs):

    assert type(files_in) is list
    assert type(files_out) is list
    assert type(deps) is list

    files_in  = listify(files_in)
    files_out = listify(files_out)
    deps      = listify(deps)

    call_args = Config(
      prototype = self,
      name      = "call_args",
      #file_in   = files_in[0],
      #file_out  = files_out[0] if len(files_out) else "",
      files_in  = files_in,
      files_out = files_out,
      deps      = deps,
      **kwargs,
    )

    # Print dotty graph if requested
    if call_args.dotty:
      for file_in in call_args.files_in:
        for file_out in call_args.files_out:
          print(f"  \"{file_in}\" -> \"{file_out}\";")
      return

    # Clean files if requested
    if call_args.clean:
      for file_out in call_args.files_out:
        if call_args.verbose:
          print(f"rm -f {file_out}")
        os.system(f"rm -f {file_out}")
      return

    # Make sure our output directories exist
    for file_out in call_args.files_out:
      if dirname := os.path.dirname(file_out):
        os.makedirs(dirname, exist_ok = True)

    # OK, we can queue up the rule now.
    call_args.queue()

  #----------------------------------------

  async def run_command_async(self):

    # Wait on all our dependencies to be updated
    await self.check_deps(self.files_in)
    await self.check_deps(self.deps)

    # Our dependencies are ready, we can grab a process semaphore slot now.
    async with proc_sem:

      global node_visit
      global node_total
      global node_built

      node_visit = node_visit+1

      #----------
      # Check if we need a rebuild

      reason = ""

      if self.force:
        # Adding "args.force = True" makes the rule always rebuild.
        reason = f"Files {self.files_out} forced to rebuild"
      else:
        reason = self.needs_rebuild()

      if not reason: return 0

      if self.debug: print(reason)
      sys.stdout.flush()

      #----------
      # Print debug dump of args if needed
      if self.debug:
        self.dump()

      # Expand "command" as late as possible, just in case some previous action
      # changed some arg somehow.
      command = expand(self.command, self)
      if not command:
        print(f"Command missing for input {self.file_in}!")
        sys.stdout.flush()
        return -1

      # Print description

      if self.verbose or self.debug:
        print(f"[{node_visit}/{node_total}] {self.description}")
        print(f"{command}")
        print()
      else:
        print("\r", end="")
        status = f"[{node_visit}/{node_total}] {self.description}"
        status = status[:os.get_terminal_size().columns - 1]
        print(status, end="")
        print("\x1B[K", end="")

      sys.stdout.flush()

      # Early-exit if this is just a dry run
      if self.dry_run: return 0

      # OK, we're ready to start the subprocess. In serial mode we run the
      # subprocess synchronously.
      if self.serial:
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

      # If the command failed, print stdout/stderr and return the error code.
      if returncode != 0:
        if not self.verbose: print()
        print(f"\x1B[31mFAILED\x1B[0m: {self.files_out}")
        print(command)
        print(stderr, end="")
        print(stdout, end="")
        sys.stdout.flush()
        return returncode

      if stdout or stderr:
        if not self.verbose: print()
        print(stderr, end="")
        print(stdout, end="")

      node_built = node_built + 1

      sys.stdout.flush()

      return returncode


################################################################################

"""
Special action args
  description: Description of the rule printed every time it runs
  command:     Command to run for the rule
  files_in:    Either a single filename or a list of filenames
  files_out:   Either a single filename or a list of filenames
  deps:        Additional dependencies for the rule
  force:       Makes the rule always run even if dependencies are up to date
"""

config = Config(
  prototype = None,
  verbose   = False, # Print verbose build info
  clean     = False, # Delete intermediate files
  serial    = False, # Do not parallelize commands
  dry_run   = False, # Do not run commands
  debug     = False, # Dump debugging information
  dotty     = False, # Dump dependency graph as dotty

  description = "{command}",
  command   = "echo You forgot the command for {file_out}",
  file_in   = "{files_in[0]}",
  file_out  = "{files_out[0] if len(files_out) else ''}",
  force     = False,
)

node_total = 0
node_visit = 0
node_built = 0
promise_map = {}

proc_sem = None

################################################################################

any_failed = False

def hancho_atexit():
  global node_visit
  global node_total
  global node_built
  global proc_sem

  all_tasks = asyncio.all_tasks(hancho_loop)

  if not all_tasks:
    return

  if proc_sem is None:
    proc_sem = asyncio.Semaphore(1 if config.serial else os.cpu_count())

  node_total = len(all_tasks)

  async def wait(tasks):
    results = await asyncio.gather(*tasks)
    global any_failed
    for r in results:
      if r: any_failed = True
  hancho_loop.run_until_complete(wait(all_tasks))
  if node_built and not config.verbose: print()
  #print(f"any_failed = {any_failed}")
  #print(f"node_total {node_total}")
  #print(f"node_count {node_visit}")
  #print(f"node_built {node_built}")

atexit.register(hancho_atexit)

################################################################################

if __name__ == "__main__":
    import doctest
    #doctest.testmod()
    doctest.testfile("TUTORIAL.md")
