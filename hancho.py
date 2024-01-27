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

import asyncio, os, re, sys, subprocess
from os import path

hancho_loop  = asyncio.new_event_loop()
hancho_queue = []

################################################################################
# Minimal JSON-style pretty printer for Config

def repr_dict(d, depth):
  result = "{\n"
  for (k,v) in d.items():
    result += "  " * (depth + 1)
    result += repr_val(k, depth + 1)
    result += " : "
    result += repr_val(v, depth + 1)
    result += ",\n"
  result += "  " * depth + "}"
  return result

def repr_list(l, depth):
  return "[" + ", ".join([repr_val(v, depth) for v in l]) + "]"

def repr_val(v, depth):
  if v is None:           return "null"
  elif type(v) is str:    return '"' + v + '"'
  elif type(v) is dict:   return repr_dict(v, depth + 1)
  elif type(v) is list:   return repr_list(v, depth + 1)
  elif type(v) is Config: return repr_dict(v.__dict__, depth)
  else:                   return str(v)

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

def expand(text, context, debug = False):
  if type(text) is list:
    return [expand(t, context, debug) for t in text]

  while template_regex.search(text):
    if debug: print(f"expand \"{text}\"")
    try:
      text = eval("f\"" + text + "\"", None, context)
    except AttributeError as e:
      break

  if debug: print(f"result \"{text}\"")
  return text

################################################################################

def resolve(self, name):
  try:
    return object.__getattribute__(self, name)
  except AttributeError:
    #print(f"got attribute error for {name}")
    if proto := object.__getattribute__(self, "prototype"):
      return resolve(proto, name)
    else:
      raise AttributeError(f"Could not resolve attribute {name} for {self}")

################################################################################

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
    for name in kwargs: setattr(self, name, kwargs[name])
    self.prototype = prototype

  def __getitem__(self, name):
    #print(f"??? __getitem__ {name} ???")
    return resolve(self, name)

  def __getattribute__(self, name):
    #print(f"??? __getattribute__ {name} ???")
    return resolve(self, name)

  def __repr__(self):
    return repr_val(self, 0)

  def __call__(self, **kwargs):
    return queue(self.extend(**kwargs))

  #----------------------------------------

  def extend(self, **kwargs):
    return Config(prototype = self, **kwargs)

  #----------------------------------------

  def check_mtime(self, files_in, file_out):
    for file_in in files_in:
      if os.path.getmtime(file_in) > os.path.getmtime(file_out):
        return True
    return False

  #----------------------------------------

  def needs_rebuild(self):

    if not self.all_files_out:
      return "Always rebuild a target with no outputs?"

    for file in self.all_files_out:
      # Check for missing outputs.
      if not os.path.exists(file):
        return f"Rebuilding {file} because it's missing"

      # Check user-specified deps.
      if self.check_mtime(self.deps, file):
        return f"Rebuilding {file} because a dependency has changed"

      # Check depfile, if present.
      if os.path.exists(file + ".d"):
        deplines = open(file + ".d").read().split()
        deplines = [d for d in deplines[1:] if d != '\\']
        if self.check_mtime(deplines, file):
          return f"Rebuilding {file} because a dependency in {file}.d has changed"

      # Check input files.
      if self.check_mtime(self.all_files_in, file):
        return f"Rebuilding {file} because an input has changed"

      # All checks passed, so we don't need to rebuild this output.
      if self.debug:
        print(f"File {self.all_files_out} is up to date")

    # All deps were up-to-date, nothing to do.
    sys.stdout.flush()
    return ""

  #----------------------------------------

  async def wait_for_deps(self, deps):
    global promise_map
    for file in deps:
      if promise := promise_map.get(file, None):
        dep_result = await promise
        if dep_result != 0: return dep_result
      if not self.dryrun:
        if file and not os.path.exists(file):
          print(f"Dependency {file} missing!")
          sys.stdout.flush()
          return -1
    pass

  #----------------------------------------

  async def run_command_async(self):

    # Wait on all our dependencies to be updated
    await self.wait_for_deps(self.all_files_in)
    await self.wait_for_deps(self.deps)

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
        reason = f"Files {self.all_files_out} forced to rebuild"
      else:
        reason = self.needs_rebuild()

      if not reason: return 0

      # Print description
      description = expand(self.description, self)
      if self.verbose or self.debug:
        print(f"[{node_visit}/{node_total}] {description}")
      else:
        print("\r", end="")
        status = f"[{node_visit}/{node_total}] {description}"
        status = status[:os.get_terminal_size().columns - 1]
        print(status, end="")
        print("\x1B[K", end="")

      # Print rebuild reason
      if self.debug: print(reason)
      sys.stdout.flush()

      # Print debug dump of args if needed
      if self.debug: print(self)

      # Print command
      command = expand(self.command, self, self.debug)
      if not command:
        print(f"Command missing for input {self.file_in}!")
        sys.stdout.flush()
        return -1
      if self.verbose or self.debug:
        print(f"{command}")

      sys.stdout.flush()

      # Early-exit if this is just a dry run
      if self.dryrun: return 0

      # Make sure our output directories exist
      for file_out in self.all_files_out:
        if dirname := os.path.dirname(file_out):
          os.makedirs(dirname, exist_ok = True)

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
        if not (self.verbose or self.debug): print()
        print(f"\x1B[31mFAILED\x1B[0m: {self.all_files_out}")
        print(command)
        print(stderr, end="")
        print(stdout, end="")
        sys.stdout.flush()
        return returncode

      if not self.quiet:
        if stdout or stderr:
          if not self.verbose: print()
          print(stderr, end="")
          print(stdout, end="")

      node_built = node_built + 1

      sys.stdout.flush()

      return returncode

################################################################################

def expand_files(files, command, prefix = None):
  result = []
  for file in listify(files):
    if prefix:
      file = path.join(prefix, file)
    file = expand(file, command, command.debug)
    result.append(file)
  return result

################################################################################

def queue(command):
  global promise_map
  global hancho_queue

  # Expand all filenames
  file_in   = getattr(command, "file_in",   None)
  files_in  = getattr(command, "files_in",  None)

  if files_in != None and file_in != None:
    print("can only have one of file_in and files_in")
    sys.exit(-1)

  all_files_in  = []

  if files_in:
    files_in = [expand(f, command) for f in listify(files_in)]
    command.files_in = files_in
    all_files_in = files_in
  if file_in:
    file_in = expand(file_in, command)
    command.file_in = file_in
    all_files_in = [file_in]

  command.all_files_in = all_files_in

  file_out  = getattr(command, "file_out",  None)
  files_out = getattr(command, "files_out", None)

  if files_out != None and file_out != None:
    print("can only have one of file_out and files_out")
    sys.exit(-1)

  all_files_out = []

  if files_out:
    files_out = [expand(f, command) for f in listify(files_out)]
    command.files_out = files_out
    all_files_out = files_out
  if file_out:
    file_out = expand(file_out, command)
    command.file_out = file_out
    all_files_out = [file_out]

  command.all_files_out = all_files_out

  # Check for duplicate outputs
  for file in all_files_out:
    if file in promise_map:
      print(f"####### Multiple rules build {file}!")
      sys.exit()

  # OK, we can queue up the rule now.
  hancho_queue.append(command)

  if files_out:
    return files_out
  else:
    return file_out

################################################################################

def queue2(command, **kwargs):
  return queue(command.extend(**kwargs))

################################################################################

"""
Special action args
  description: Description of the rule printed every time it runs
  command:     Command to run for the rule
  files_in:    Either a single filename or a list of filenames
  files_out:   Either a single filename or a list of filenames
  force:       Makes the rule always run even if dependencies are up to date
"""

config = Config(
  prototype = None,
  name      = "hancho.config",
  verbose   = False, # Print verbose build info
  quiet     = False, # Don't print command results
  serial    = False, # Do not parallelize commands
  dryrun    = False, # Do not run commands
  debug     = False, # Print debugging information
  dotty     = False, # Print dependency graph as dotty instead of building

  description = "{all_files_in} -> {all_files_out}",
  command     = "echo You forgot the command for {all_files_out}",
  deps        = [],
  force       = False,
  out_dir     = "build",

  join     = join,
  len      = len,
  swap_ext = swap_ext,
  listify  = listify
)

node_total = 0
node_visit = 0
node_built = 0
promise_map = {}

proc_sem = None

################################################################################

def reset():
  global hancho_queue
  global node_built
  global node_total
  global node_visit
  global proc_sem
  global promise_map

  hancho_queue.clear()
  node_built = 0
  node_total = 0
  node_visit = 0
  proc_sem = None
  promise_map.clear()

################################################################################

def build():
  global hancho_queue
  global node_built
  global node_total
  global proc_sem
  global promise_map

  if not hancho_queue:
    reset()
    return False

  hancho_tasks = []
  for command in hancho_queue:
    coroutine = command.run_command_async()
    promise = hancho_loop.create_task(coroutine)
    hancho_tasks.append(promise)
    for output in command.all_files_out:
      promise_map[output] = promise

  if proc_sem is None:
    proc_sem = asyncio.Semaphore(1 if config.serial else os.cpu_count())

  node_total = len(hancho_tasks)

  all_ok = True
  async def wait(tasks):
    results = await asyncio.gather(*tasks)
    for r in results:
      if r: all_ok = False

  hancho_loop.run_until_complete(wait(hancho_tasks))
  if node_built and not config.verbose: print()
  reset()
  return all_ok

################################################################################

if __name__ == "__main__":
    import doctest
    #doctest.testmod()
    doctest.testfile("TUTORIAL.md")
