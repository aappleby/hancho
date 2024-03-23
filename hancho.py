#!/usr/bin/python3
# pylint: disable=too-many-lines

"""
Hancho is a simple, pleasant build system.

Hancho v0.0.5, 19-03-2024

- Special dir-related fields are now start_dir, root_dir, leaf_dir, work_dir, and build_dir
- Hancho files in a submodule can be loaded via load(root="submodule/path", file="build.hancho)
- Each Hancho module now gets its own 'config' object extended from its parent module (or
  global_config). This prevents submodules from accidentally changing global fields that their
  parent modules use while still allowing sharing of configuration across files.
"""

import argparse
import asyncio
import builtins
import inspect
import io
import json
import os
import re
import subprocess
import sys
import traceback
import types
from pathlib import Path
from glob import glob

# If we were launched directly, a reference to this module is already in
# sys.modules[__name__]. Stash another reference in sys.modules["hancho"] so
# that build.hancho and descendants don't try to load a second copy of Hancho.
sys.modules["hancho"] = sys.modules[__name__]

# The maximum number of recursion levels we will do to expand a template
# Tests currently require MAX_EXPAND_DEPTH >= 6
MAX_EXPAND_DEPTH = 100

# Matches {} delimited regions inside a template string.
template_regex = re.compile("{[^}]*}")

####################################################################################################


def log(message, *args, sameline=False, **kwargs):
    """Simple logger that can do same-line log messages like Ninja."""
    if global_config.quiet:
        return

    if not sys.stdout.isatty():
        sameline = False

    output = io.StringIO()
    if sameline:
        kwargs["end"] = ""
    print(message, *args, file=output, **kwargs)
    output = output.getvalue()

    if not sameline and app.line_dirty:
        sys.stdout.write("\n")
        app.line_dirty = False

    if not output:
        return

    if sameline:
        sys.stdout.write("\r")
        output = output[: os.get_terminal_size().columns - 1]
        sys.stdout.write(output)
        sys.stdout.write("\x1B[K")
    else:
        sys.stdout.write(output)

    sys.stdout.flush()
    app.line_dirty = output[-1] != "\n"


def abspath(path):
    """Pathlib's path.absolute() doesn't resolve "foo/../bar", so we use os.path.abspath."""
    if template_regex.search(str(path)):
        raise ValueError("Abspath can't operate on templated strings")
    # Hmm this acutally works now, am I forgetting a corner case?
    # return Path(path).absolute()
    return Path(os.path.abspath(path))


def relpath(path1, path2):
    """Pathlib's path.relative_to() refuses to generate "../bar", so we use os.path.relpath."""
    if template_regex.search(str(path1)) or template_regex.search(str(path2)):
        raise ValueError("Relpath can't operate on templated strings")
    # This also works now, def need to check corner cases.
    # if path2 is None: return path1
    # return Path(path1).relative_to(path2)
    return Path(os.path.relpath(path1, path2))


def color(red=None, green=None, blue=None):
    """Converts RGB color to ANSI format string."""
    # Color strings don't work in Windows console, so don't emit them.
    if os.name == "nt":
        return ""
    if red is None:
        return "\x1B[0m"
    return f"\x1B[38;2;{red};{green};{blue}m"


def run_cmd(cmd):
    """Runs a console command synchronously and returns its stdout with whitespace stripped."""
    return subprocess.check_output(cmd, shell=True, text=True).strip()


def swap_ext(name, new_ext):
    """Replaces file extensions on either a single filename or a list of filenames."""
    if name is None:
        return None
    if isinstance(name, list):
        return [swap_ext(n, new_ext) for n in name]
    return Path(name).with_suffix(new_ext)


def mtime(filename):
    """Gets the file's mtime and tracks how many times we've called mtime()"""
    app.mtime_calls += 1
    return Path(filename).stat().st_mtime


def maybe_as_number(text):
    """Tries to convert a string to an int, then a float, then gives up. Used for ingesting
    unrecognized flag values."""
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def hancho_caller_filename():
    """Returns the filename of the topmost function call that was in a .hancho file."""
    for frame in inspect.stack():
        if frame.filename.endswith(".hancho"):
            return frame.filename
    assert False


def hancho_caller_dir():
    """Returns the directory of the topmost function call that was in a .hancho file."""
    return Path(hancho_caller_filename()).parent


def check_path(path):
    """Sanity-checks an expanded path - it must be absolute, under start_dir, and without
    '..'s."""
    path = str(path)
    if path[0] != "/":
        log(f"Path does not start with / : {path}")
        assert False
    if not path.startswith(str(global_config.start_dir)):
        log(f"Path not under start_dir : {path}")
        assert False
    if ".." in path:
        log(f"Path contains '..' : {path}")
        log(f"Abspath {abspath(path)}")
        assert False
    return True


####################################################################################################
# The next three functions require some explanation.
#
# We do not necessarily know in advance how the users will nest strings, templates, callbacks,
# etcetera. So, when we need to produce a flat list of files from whatever was passed to files_in,
# we need to do a bunch of dynamic-dispatch-type stuff to ensure that we can always turn that thing
# into a flat list of files.
#
# We also need to ensure that if anything in this process throws an exception (or if an exception
# was passed into a rule due to a previous rule failing) that we always propagate the exception up
# to Task.run_async, where it will be handled and propagated to other Tasks.
#
# The result of this is that the following three functions are mutually recursive in a way that can
# lead to confusing callstacks, but that should handle every possible case of stuff inside other
# stuff.
#
# The 'depth' checks are to prevent recursive runaway - 100 is an arbitrary limit but it should
# suffice.


def flatten(variant, rule=None, depth=0):
    """Turns 'variant' into a flat array of non-templated strings, paths, and callbacks."""
    # pylint: disable=too-many-return-statements

    if depth > MAX_EXPAND_DEPTH:
        raise ValueError(f"Flattening '{variant}' failed to terminate")

    if rule is None:
        rule = app.current_config()

    match variant:
        case None:
            return []
        case asyncio.CancelledError():
            raise variant
        case Task():
            return flatten(variant.promise, rule, depth + 1)
        case Path():
            return [Path(stringize(str(variant), rule, depth + 1))]
        case list():
            result = []
            for element in variant:
                result.extend(flatten(element, rule, depth + 1))
            return result
        case _ if inspect.isfunction(variant):
            return [variant]
        case _:
            return [stringize(variant, rule, depth + 1)]


def stringize(variant, rule=None, depth=0):
    """Turns 'variant' into a non-templated string."""
    # pylint: disable=too-many-return-statements

    if depth > MAX_EXPAND_DEPTH:
        raise ValueError(f"Stringizing '{variant}' failed to terminate")

    if rule is None:
        rule = app.current_config()

    match variant:
        case None:
            return ""
        case asyncio.CancelledError():
            raise variant
        case Task():
            return stringize(variant.promise, rule, depth + 1)
        case Path():
            return stringize(str(variant), rule, depth + 1)
        case list():
            variant = flatten(variant, rule, depth + 1)
            variant = [str(s) for s in variant if s is not None]
            variant = " ".join(variant)
            return variant
        case str():
            if template_regex.search(variant):
                return expand(variant, rule, depth + 1)
            return variant
        case _:
            return str(variant)


def expand(template, rule=None, depth=0):
    """Expands all templates to produce a non-templated string."""

    if depth > MAX_EXPAND_DEPTH:
        raise ValueError(f"Expanding '{template}' failed to terminate")

    if rule is None:
        rule = app.current_config()

    if not isinstance(template, str):
        raise ValueError(f"Don't know how to expand {type(template)}")

    result = ""

    if isinstance(rule, Expander):
        expander = Expander(rule, depth)
    else:
        expander = rule

    expander.depth = depth

    while span := template_regex.search(template):
        result += template[0 : span.start()]
        exp = template[span.start() : span.end()]

        # Evaluate the template contents.
        try:
            # pylint: disable=eval-used
            #replacement = eval(exp[1:-1], globals(), Expander(rule))
            replacement = eval(exp[1:-1], globals(), expander)
            result += stringize(replacement, rule, depth + 1)
        except Exception as exc:  # pylint: disable=broad-except
            raise exc
            result += exp

        template = template[span.end() :]

    result += template
    return result


####################################################################################################


class Expander:
    """Expander does template expasion on read so that eval() always sees expanded templates."""

    def __init__(self, task, depth):
        self.task = task
        self.depth = depth
        #self.cache = {}

    def __getitem__(self, key):
        #if key in self.cache:
        #    # print(f"key {key} was in cache")
        #    return self.cache[key]
        val = self.task[key]

        if isinstance(val, Path) and template_regex.search(str(val)):
            val = Path(stringize(str(val), self, self.depth + 1))

        if isinstance(val, str) and template_regex.search(val):
            val = stringize(val, self, self.depth + 1)

        #self.cache[key] = val
        return val



####################################################################################################


async def await_variant(variant):
    """Recursively replaces every awaitable in the variant with its awaited value."""

    match variant:
        case Task():
            # We don't iterate through subtasks because they should await themselves except for
            # their own promise.
            if inspect.isawaitable(variant.promise):
                variant.promise = await variant.promise
        case dict():
            for key in variant:
                variant[key] = await await_variant(variant[key])
        case list():
            for index, value in enumerate(variant):
                variant[index] = await await_variant(value)
        case _ if inspect.isawaitable(variant):
            variant = await variant
    return variant



####################################################################################################


def load(file=None, root=None):
    """Module loader entry point for .hancho files. Searches the loaded Hancho module stack for a
    module whose directory contains 'mod_path', then loads the module relative to that path.
    """

    if file is None:
        raise FileNotFoundError("No .hancho filename given")

    config = app.current_config()
    file = stringize(file, config)

    if root is not None:
        root = stringize(root, config)
        new_root = abspath(app.current_leaf_dir() / root)
        new_leaf = abspath(new_root / file)
    else:
        new_root = app.current_root_dir()
        new_leaf = abspath(app.current_leaf_dir() / file)

    if not new_leaf.exists():
        raise FileNotFoundError(f"Could not load module {new_leaf}")

    return app.load_module(new_leaf, new_root)


####################################################################################################


class Chdir:
    """Copied from Python 3.11 contextlib.py"""

    def __init__(self, path):
        self.path = path
        self._old_cwd = []

    def __enter__(self):
        self._old_cwd.append(os.getcwd())
        os.chdir(self.path)

    def __exit__(self, *excinfo):
        os.chdir(self._old_cwd.pop())


####################################################################################################


class Config(dict):
    """Config is a 'bag of fields' that behaves sort of like a Javascript object."""

    def __getitem__(self, key):
        try:
            val = super().__getitem__(key)
        except Exception:  # pylint: disable=broad-except
            val = None

        # Don't recurse if we found the key, or if we were trying to find our base instance.
        if key == "base" or val is not None:
            return val

        # Key was missing or value was None, recurse into base if present.
        try:
            return super().__getitem__("base")[key]
        except Exception:  # pylint: disable=broad-except
            return None

    # Attributes and items are the same for Config.
    def __setattr__(self, key, value):
        self.__setitem__(key, value)

    def __getattr__(self, key):
        return self.__getitem__(key)

    def __repr__(self):
        """Turns this config blob into a JSON doc for debugging."""

        class Encoder(json.JSONEncoder):
            """Types the encoder doesn't understand just get stringified."""

            def default(self, o):
                if isinstance(o, Path):
                    return f"Path {o}"
                return str(o)

        return json.dumps(self, indent=2, cls=Encoder)

    def extend(self, **kwargs):
        """Returns a 'subclass' of this config blob that can override its fields."""
        return type(self)(**kwargs, base=self)


####################################################################################################


class Rule(Config):
    """Rules are callable Configs that create a Task when called. Rules also inherit from their
    parent module's config if they have no other ancestor."""

    # pylint: disable=attribute-defined-outside-init
    # pylint: disable=super-init-not-called

    def __init__(self, **kwargs):
        kwargs.setdefault("name", "<Rule>")
        kwargs.setdefault("rule_dir", Path(inspect.stack(context=0)[1].filename).parent)
        kwargs.setdefault("base", app.current_config())
        super().__init__(**kwargs)

    def __call__(self, files_in=None, files_out=None, **kwargs):
        print(files_in)
        print(files_out)

        kwargs.setdefault("name", "<Task>")
        kwargs.setdefault("files_in", files_in)
        kwargs.setdefault("files_out", files_out)
        kwargs.setdefault("root_dir", app.current_root_dir())
        kwargs.setdefault("leaf_dir", app.current_leaf_dir())
        kwargs.setdefault("call_dir", Path(inspect.stack(context=0)[1].filename).parent)

        rule = self.extend(**kwargs)
        task = Task(rule)
        return task


####################################################################################################


class Task():
    """Calling a Rule creates a Task."""

    # pylint: disable=too-many-instance-attributes
    # pylint: disable=attribute-defined-outside-init
    # pylint: disable=super-init-not-called

    def __init__(self, config):
        self.config = config
        self.reason = None
        self.command = None

        self.task_index = None

        self.files_in = None
        self.deps = None
        self.files_out = None

        self.work_dir = None
        self.in_dir = None
        self.out_dir = None
        self.deps_dir = None


        self.abs_files_in = None
        self.abs_deps = None
        self.abs_files_out = None
        self.abs_named_deps = None

        self.stdout = None
        self.stderr = None
        self.named_deps = None
        self.desc = None
        self.depfile = None
        self.promise = None
        app.tasks_total += 1
        coroutine = self.run_async()
        self.promise = asyncio.create_task(coroutine)

    def __repr__(self):
        """Turns this config blob into a JSON doc for debugging."""

        print("SLKDJFLSKJF")
        this = self.config
        class Encoder(json.JSONEncoder):
            """Types the encoder doesn't understand just get stringified."""
            def default(self, o):
                return str(o)
        return json.dumps(self.__dict__, indent=2, cls=Encoder)

    async def run_async(self):
        """Entry point for async task stuff, handles exceptions generated
        during task execution."""

        print(self)
        config = self.config

        try:
            # Await everything awaitable in this task except the task's own promise.
            for key in config:
                config[key] = await await_variant(config[key])

            # Everything awaited, task_init runs synchronously.
            self.task_init()

            # Run the commands if we need to.
            if self.reason:
                result = await self.run_commands()
                app.tasks_pass += 1
            else:
                result = self.abs_files_out
                app.tasks_skip += 1

            return result

        # If this task failed, we print the error and propagate a cancellation
        # to downstream tasks.
        except Exception:  # pylint: disable=broad-except
            if not self.config.quiet:
                log(color(255, 128, 128))
                traceback.print_exception(*sys.exc_info())
                log(color())
            app.tasks_fail += 1
            return asyncio.CancelledError()

        # If any of this tasks's dependencies were cancelled, we propagate the
        # cancellation to downstream tasks.
        except asyncio.CancelledError as cancel:
            app.tasks_cancel += 1
            return cancel

        finally:
            if self.config.debug:
                log("")

    # pylint: disable=too-many-branches
    def task_init(self):
        """All the setup steps needed before we run a task."""

        config = self.config
        expander = Expander(config, 0)

        # Check for missing fields
        # pylint: disable=access-member-before-definition
        if config.command is None:
            raise ValueError("Task missing command")
        if config.files_in is None:
            raise ValueError("Task missing files_in")
        if config.files_out is None:
            raise ValueError("Task missing files_out")

        # Stringize our directories
        self.work_dir = Path(stringize(config.work_dir, expander))
        self.in_dir   = Path(stringize(config.in_dir, expander))
        self.deps_dir = Path(stringize(config.deps_dir, expander))
        self.out_dir  = Path(stringize(config.out_dir, expander))

        assert check_path(self.work_dir) and self.work_dir.exists()
        assert check_path(self.in_dir) and self.in_dir.exists()
        assert check_path(self.deps_dir) and self.deps_dir.exists()

        # 'out_dir' may not exist yet and that's OK, we will create it.
        assert check_path(self.out_dir)

        # Flatten our file lists
        self.files_in  = flatten(config.files_in, expander)
        self.deps      = flatten(config.deps, expander)
        self.files_out = flatten(config.files_out, expander)

        self.named_deps = {}
        for key in config.named_deps:
            self.named_deps[key] = stringize(config.named_deps[key], expander)

        # Prepend directories to filenames and then normalize + absolute them.
        # If they're already absolute, this does nothing.
        self.abs_files_in  = [self.in_dir / f for f in self.files_in]
        self.abs_deps      = [self.deps_dir / f for f in self.deps]
        self.abs_files_out = [self.out_dir / f for f in self.files_out]

        for f in self.abs_files_in:
            check_path(f)
        for f in self.abs_deps:
            check_path(f)
        for f in self.abs_files_out:
            check_path(f)

        self.abs_named_deps = {}
        for key in self.named_deps:
            self.abs_named_deps[key] = self.deps_dir / self.named_deps[key]

        for f in self.abs_named_deps.values():
            check_path(f)

        # Strip the working directory off all our file paths to make our command lines less bulky.
        # Note that we _don't_ want relpath() here as it could add "../../.." that would go up
        # through a symlink to the wrong directory.
        def strip(f):
            work_dir_prefix = str(self.work_dir) + "/"
            return Path(str(f).removeprefix(work_dir_prefix))

        self.files_in  = [strip(f) for f in self.abs_files_in]
        self.deps      = [strip(f) for f in self.abs_deps]
        self.files_out = [strip(f) for f in self.abs_files_out]
        for key in self.named_deps:
            self.named_deps[key] = strip(self.abs_named_deps[key])

        # Now that files_in/files_out/deps are flat, we can expand our description and command
        # list.
        self.command = flatten(config.command, expander)

        #print(self.command)

        # pylint: disable=access-member-before-definition
        self.desc    = stringize(config.desc, expander)
        self.depfile = stringize(config.depfile, expander)

        # Check for missing inputs
        if not config.dryrun:
            for file in self.abs_files_in:
                if not file.exists():
                    raise NameError(f"Input file doesn't exist - {file}")
            for file in self.abs_deps:
                if not file.exists():
                    raise NameError(f"Dependency doesn't exist - {file}")
            for kv in self.abs_named_deps.items():
                if not kv[1].exists():
                    raise NameError(f"Named dependency doesn't exist - {kv[0]}:{kv[1]}")

        # Check for duplicate task outputs
        for file in self.abs_files_out:
            if file in app.all_files_out:
                raise NameError(f"Multiple rules build {file}!")
            app.all_files_out.add(file)

        # Make sure our output directories exist
        if not config.dryrun:
            for file_out in self.abs_files_out:
                file_out.parent.mkdir(parents=True, exist_ok=True)

        # Check if we need a rebuild
        self.reason = self.needs_rerun(config.force)
        print(self)

    def needs_rerun(self, force=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""

        # Pylint really doesn't like this function, lol.
        # pylint: disable=too-many-return-statements
        # pylint: disable=too-many-branches

        config    = self.config
        files_in  = self.abs_files_in
        files_out = self.abs_files_out

        if force:
            return f"Files {self.files_out} forced to rebuild"
        if not files_in:
            return "Always rebuild a target with no inputs"
        if not files_out:
            return "Always rebuild a target with no outputs"

        # Tasks with missing outputs always run.
        for file_out in files_out:
            if not file_out.exists():
                return f"Rebuilding {self.files_out} because some are missing"

        # Check if any task inputs are newer than any outputs.
        min_out = min(mtime(f) for f in files_out)
        if files_in and max(mtime(f) for f in files_in) >= min_out:
            return f"Rebuilding {self.files_out} because an input has changed"

        # Check if the hancho file(s) that generated the task have changed.
        if max(mtime(f) for f in app.hancho_mods) >= min_out:
            return f"Rebuilding {self.files_out} because its .hancho files have changed"

        # Check if any user-specified deps have changed.
        if self.deps and max(mtime(f) for f in self.deps) >= min_out:
            return f"Rebuilding {self.files_out} because a dependency has changed"

        for key in self.named_deps:
            if mtime(self.named_deps[key]) >= min_out:
                return f"Rebuilding {self.files_out} because a named dependency has changed"

        # Check all dependencies in the depfile, if present.
        if self.depfile:
            assert os.path.isabs(self.work_dir)
            abs_depfile = self.work_dir / self.depfile
            check_path(abs_depfile)
            if abs_depfile.exists():
                if config.debug:
                    log(f"Found depfile {abs_depfile}")
                with open(abs_depfile, encoding="utf-8") as depfile:
                    deplines = None
                    if config.depformat == "msvc":
                        # MSVC /sourceDependencies json depfile
                        deplines = json.load(depfile)["Data"]["Includes"]
                    elif config.depformat == "gcc":
                        # GCC .d depfile
                        deplines = depfile.read().split()
                        deplines = [d for d in deplines[1:] if d != "\\"]
                    else:
                        raise ValueError(f"Invalid depformat {config.depformat}")

                    # The contents of the depfile are RELATIVE TO THE WORKING DIRECTORY
                    deplines = [self.work_dir / Path(d) for d in deplines]
                    if deplines and max(mtime(f) for f in deplines) >= min_out:
                        return (
                            f"Rebuilding {self.files_out} because a dependency in "
                            + f"{abs_depfile} has changed"
                        )

        # All checks passed; we don't need to rebuild this output.
        if config.debug:
            log(f"Files {self.files_out} are up to date")

        # Empty string = no reason to rebuild
        return ""

    async def run_commands(self):
        """Grabs a lock on the jobs needed to run this task's commands, then runs all of them."""

        config = self.config

        try:
            # Wait for enough jobs to free up to run this task.
            await app.acquire_jobs(config.job_count)

            # Deps fulfilled and jobs acquired, we are now runnable so grab a task index.
            app.task_counter += 1
            self.task_index = app.task_counter

            # Print the "[1/N] Foo foo.foo foo.o" status line and debug information
            log(
                f"{color(128,255,196)}[{self.task_index}/{app.tasks_total}]{color()} {self.desc}",
                sameline=not config.verbose,
            )

            if self.work_dir == global_config.start_dir:
                work_dir = "."
            else:
                work_dir = str(self.work_dir).removeprefix(
                    str(global_config.start_dir) + "/"
                )
            dryrun = "(DRY RUN) " if config.dryrun else ""

            if config.verbose or config.debug:
                log(f"{color(128,128,128)}Reason: {self.reason}{color()}")

            if config.debug:
                log(self)

            result = []
            for command in self.command:
                if config.verbose or config.debug:
                    log(f"{color(128,128,255)}{work_dir}$ {color()}{dryrun}{command}")
                result = await self.run_command(command)
        finally:
            await app.release_jobs(config.job_count)

        # Check if the commands actually updated all the output files.
        # _Don't_ do this if this task represents a call to an external build system, as that
        # system might not actually write to the output files.
        if self.files_in and self.files_out and not (config.dryrun or config.ext_build):
            if second_reason := self.needs_rerun():
                raise ValueError(
                    f"Task '{self.desc}' still needs rerun after running!\n"
                    + f"Reason: {second_reason}"
                )

        return result

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        config = self.config

        # Early exit if this is just a dry run
        if config.dryrun:
            return self.abs_files_out

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            return command(self)

        # Non-string non-callable commands are not valid
        if not isinstance(command, str):
            raise ValueError(f"Don't know what to do with {command}")

        # Create the subprocess via asyncio and then await the result.
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=self.work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        (stdout_data, stderr_data) = await proc.communicate()

        self.stdout = stdout_data.decode()
        self.stderr = stderr_data.decode()
        self.returncode = proc.returncode

        # Print command output if needed
        if not config.quiet and (self.stdout or self.stderr):
            if self.stderr:
                log(self.stderr, end="")
            if self.stdout:
                log(self.stdout, end="")

        # Task complete, check the task return code
        if self.returncode:
            raise ValueError(
                f"Command '{command}' exited with return code {self.returncode}"
            )

        # Task passed, return the output file list
        return self.abs_files_out


####################################################################################################


class App:
    """The application state. Mostly here so that the linter will stop complaining about my use of
    global variables. :D"""

    # pylint: disable=too-many-instance-attributes
    def __init__(self):
        self.hancho_mods = {}
        self.mod_stack = []
        self.all_files_out = set()
        self.tasks_total = 0
        self.tasks_pass = 0
        self.tasks_fail = 0
        self.tasks_skip = 0
        self.tasks_cancel = 0
        self.task_counter = 0
        self.mtime_calls = 0
        self.line_dirty = False
        self.jobs_available = os.cpu_count()
        self.jobs_lock = asyncio.Condition()

    def current_mod(self):
        """Returns the module on top of the mod stack."""
        return self.mod_stack[-1] if self.mod_stack else None

    def current_root_dir(self):
        """Returns the directory of the module on top of the mod stack, or the root directory of
        the whole build if there is no mod stack."""
        return (
            self.current_mod().config.root_dir
            if self.mod_stack
            else global_config.start_dir
        )

    def current_leaf_dir(self):
        """Returns the directory of the module on top of the mod stack, or the directory of the
        topmost hancho file in the call stack if there is no mod stack."""
        return (
            self.current_mod().config.leaf_dir
            if self.mod_stack
            else hancho_caller_dir()
        )

    def current_config(self):
        """Returns the config object of the module on top of the mod stack, or the global config if
        there is no mod stack."""
        return self.current_mod().config if self.mod_stack else global_config

    def main(self):
        """Our main() just handles command line args and delegates to async_main()"""

        # pylint: disable=line-too-long
        # fmt: off
        parser = argparse.ArgumentParser()
        parser.add_argument("start_filename",  default="build.hancho", type=str, nargs="?", help="The name of the .hancho file to build")
        parser.add_argument("-C", "--chdir",   default=".",            type=str,            help="Change directory before starting the build")
        parser.add_argument("-j", "--jobs",    default=os.cpu_count(), type=int,            help="Run N jobs in parallel (default = cpu_count)")
        parser.add_argument("-v", "--verbose", default=False,          action="store_true", help="Print verbose build info")
        parser.add_argument("-q", "--quiet",   default=False,          action="store_true", help="Mute all output")
        parser.add_argument("-n", "--dryrun",  default=False,          action="store_true", help="Do not run commands")
        parser.add_argument("-d", "--debug",   default=False,          action="store_true", help="Print debugging information")
        parser.add_argument("-f", "--force",   default=False,          action="store_true", help="Force rebuild of everything")
        # fmt: on

        # Parse the command line
        (flags, unrecognized) = parser.parse_known_args()

        # Merge all known command line flags into our global config object.

        # pylint: disable=global-statement
        global global_config
        # pylint: disable=attribute-defined-outside-init
        global_config |= flags.__dict__

        # Unrecognized command line parameters also become global config fields if
        # they are flag-like
        for span in unrecognized:
            if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
                global_config[match.group(1)] = (
                    maybe_as_number(match.group(2))
                    if match.group(2) is not None
                    else True
                )

        # Change directory if needed and kick off the build.
        with Chdir(global_config.chdir):
            # For some reason "result = asyncio.run(self.async_main())" might be breaking actions
            # in Github, so I'm gonna try this. Seems to fix the issue.
            result = asyncio.get_event_loop().run_until_complete(self.async_main())

        return result

    async def async_main(self):
        """All the actual Hancho stuff runs in an async context so that clients can schedule their
        own async tasks as needed."""

        self.jobs_available = global_config.jobs

        # Load the starting .hancho file.
        start_filename = Path.cwd() / global_config.start_filename
        if not start_filename.exists():
            raise FileNotFoundError(f"Could not find {start_filename}")
        self.load_module(start_filename, Path.cwd())

        # Root module(s) loaded. Run all tasks in the queue until we run out.
        while True:
            pending_tasks = asyncio.all_tasks() - {asyncio.current_task()}
            if not pending_tasks:
                break
            await asyncio.wait(pending_tasks)

        # Done, print status info if needed
        if global_config.debug or global_config.verbose:
            log(f"tasks total:     {self.tasks_total}")
            log(f"tasks passed:    {self.tasks_pass}")
            log(f"tasks failed:    {self.tasks_fail}")
            log(f"tasks skipped:   {self.tasks_skip}")
            log(f"tasks cancelled: {self.tasks_cancel}")
            log(f"mtime calls:     {self.mtime_calls}")

        if self.tasks_fail:
            log(f"hancho: {color(255, 128, 128)}BUILD FAILED{color()}")
        elif self.tasks_pass:
            log(f"hancho: {color(128, 255, 128)}BUILD PASSED{color()}")
        else:
            log(f"hancho: {color(128, 128, 255)}BUILD CLEAN{color()}")

        return -1 if self.tasks_fail else 0

    def load_module(self, abs_path, root_dir):
        """Loads a Hancho module ***while chdir'd into its directory***"""

        check_path(abs_path)
        check_path(root_dir)

        phys_path = Path(abs_path).resolve()
        if phys_path in self.hancho_mods:
            return self.hancho_mods[phys_path]

        with open(abs_path, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, abs_path, "exec", dont_inherit=True)

        module = type(sys)(abs_path.stem)
        module.__file__ = abs_path
        module.__builtins__ = builtins

        self.hancho_mods[phys_path] = module

        # The directory the module is in gets added to the global path so we can
        # import .py modules in the same directory as it if needed. This may not
        # be necessary.
        sys.path.insert(0, str(abs_path.parent))

        # Each module gets a configuration object extended from its parent module's config
        module.config = app.current_config().extend(
            name=f"<Config for {abs_path}>",
            root_dir=root_dir,
            leaf_dir=Path(abs_path).parent,
        )

        # We must chdir()s into the .hancho file directory before running it so that
        # glob() can resolve files relative to the .hancho file itself. We are _not_ in an async
        # context here so there should be no other threads trying to change cwd.
        try:
            self.mod_stack.append(module)
            with Chdir(abs_path.parent):
                # Why Pylint thinks this is not callable is a mystery.
                # pylint: disable=not-callable
                types.FunctionType(code, module.__dict__)()
        finally:
            self.mod_stack.pop()

        return module

    async def acquire_jobs(self, count):
        """Waits until 'count' jobs are available and then removes them from the job pool."""

        if count > global_config.jobs:
            raise ValueError(
                f"Tried to acquire {count} jobs, but we only have {global_config.jobs} in the pool."
            )

        await self.jobs_lock.acquire()
        await self.jobs_lock.wait_for(lambda: self.jobs_available >= count)
        self.jobs_available -= count
        self.jobs_lock.release()

    async def release_jobs(self, count):
        """Returns 'count' jobs back to the job pool."""

        await self.jobs_lock.acquire()
        self.jobs_available += count

        # NOTE: The notify_all here is required because we don't know in advance which tasks will
        # be capable of running after we return jobs to the pool. HOWEVER, this also creates an
        # O(N^2) slowdown when we have a very large number of pending tasks (>1000) due to the
        # "Thundering Herd" problem - all tasks will wake up, only a few will acquire jobs, the
        # rest will go back to sleep again, this will repeat for every call to release_jobs().
        self.jobs_lock.notify_all()
        self.jobs_lock.release()


####################################################################################################
# The global config object. All fields here can be used in any template.

global_config = Config(
    name="<Global Config>",
    start_filename="build.hancho",
    start_dir=Path.cwd(),
    # The working directory that we run commands in, defaults to root_dir.
    work_dir=Path("{root_dir}"),
    # Input filenames are resolved relative to in_dir, defaults to leaf_dir.
    in_dir=Path("{leaf_dir}"),
    # Dependency filenames are resolved relative to deps_dir, defaults to leaf_dir.
    deps_dir=Path("{in_dir}"),
    # All output files from all tasks go under build_dir.
    build_dir=Path("build"),
    # Each .hancho file gets a separate directory under build_dir for its output files.
    out_dir=Path("{start_dir / build_dir / build_tag / relpath(in_dir, start_dir)}"),
    desc="{files_in} -> {files_out}",
    # Use build_tag to split outputs into separate debug/profile/release/etc folders.
    build_tag="",
    files_out=[],
    deps=[],
    named_deps={},
    # The default number of parallel jobs a task consumes.
    job_count=1,
    depformat="gcc",
    chdir=".",
    jobs=os.cpu_count(),
    verbose=False,
    quiet=False,
    dryrun=False,
    debug=False,
    force=False,
    ext_build=False,
    abspath=abspath,
    color=color,
    expand=expand,
    flatten=flatten,
    glob=glob,
    len=len,
    Path=Path,
    relpath=relpath,
    run_cmd=run_cmd,
    stringize=stringize,
    swap_ext=swap_ext,
    base=None,
)

####################################################################################################

app = App()


####################################################################################################


if __name__ == "__main__":
    #recursive_config = Config(flarp = "asdf {flarp}")
    #print(recursive_config)

    #x = expand("{flarp}", recursive_config)

    #sys.exit(0)


    sys.exit(app.main())
