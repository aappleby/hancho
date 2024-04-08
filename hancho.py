#!/usr/bin/python3

"""Hancho v0.1.0 @ 2024-03-25 - A simple, pleasant build system."""

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
import time
import types
from pathlib import Path
from glob import glob
from enum import Enum

# If we were launched directly, a reference to this module is already in
# sys.modules[__name__]. Stash another reference in sys.modules["hancho"] so
# that build.hancho and descendants don't try to load a second copy of Hancho.
sys.modules["hancho"] = sys.modules[__name__]

# The maximum number of recursion levels we will do to expand a macro.
# Tests currently require MAX_EXPAND_DEPTH >= 6
MAX_EXPAND_DEPTH = 20

# Matches "{expression}" macros
macro_regex = re.compile("^{[^}]*}$")

# Matches macros inside a template string.
template_regex = re.compile("{[^}]*}")


def log(message, *args, sameline=False, **kwargs):
    """Simple logger that can do same-line log messages like Ninja."""
    if global_config.quiet:
        return

    if not sys.stdout.isatty():
        sameline = False

    if sameline:
        kwargs.setdefault("end", "")

    output = io.StringIO()
    print(message, *args, file=output, **kwargs)
    output = output.getvalue()

    if not output:
        return

    if sameline:
        output = output[: os.get_terminal_size().columns - 1]
        sys.stdout.write("\r" + output + "\x1B[K")
    else:
        if app.line_dirty:
            sys.stdout.write("\n")
        sys.stdout.write(output)

    app.line_dirty = sameline
    sys.stdout.flush()


def abs_path(path, strict=True):
    if isinstance(path, list):
        return [abs_path(p, strict) for p in path]
    result = Path(path).absolute()
    if strict and not result.exists():
        raise FileNotFoundError(path)
    return result


def rel_path(path1, path2):
    if isinstance(path1, list):
        return [rel_path(p, path2) for p in path1]
    return Path(path1).relative_to(Path(path2))


def join_path(*args):
    """Returns an array of all possible concatenated paths from the given paths (or arrays of paths)."""
    if len(args) > 2:
        return join_path(args[0], join_path(*args[1:]))
    if isinstance(args[0], list):
        return [path for prefix in args[0] for path in join_path(prefix, args[1])]
    if isinstance(args[1], list):
        return [path for suffix in args[1] for path in join_path(args[0], suffix)]
    return [Path(args[0]) / Path(args[1])]


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


async def await_variant(variant):
    """Recursively replaces every awaitable in the variant with its awaited value."""
    match variant:
        case asyncio.CancelledError():
            raise variant
        case Task():
            # We don't recurse through subtasks because they should await themselves.
            if inspect.isawaitable(variant.promise):
                promise = await variant.promise
                variant.promise = await await_variant(promise)
        case Config():
            await await_variant(variant.__dict__)
        case dict():
            for key in variant:
                variant[key] = await await_variant(variant[key])
        case list():
            for index, value in enumerate(variant):
                variant[index] = await await_variant(value)
        case _ if inspect.isawaitable(variant):
            variant = await variant
    return variant


def flatten(variant):
    if isinstance(variant, list):
        return [x for element in variant for x in flatten(element)]
    return [variant]


class Chdir:
    """Based on Python 3.11's contextlib.py"""

    def __init__(self, path):
        self.path = path
        self._old_cwd = []

    def __enter__(self):
        self._old_cwd.append(Path.cwd())
        os.chdir(self.path)

    def __exit__(self, *excinfo):
        os.chdir(self._old_cwd.pop())

####################################################################################################

class Encoder(json.JSONEncoder):
    """Types the encoder doesn't understand just get stringified."""
    def default(self, o):
        if isinstance(o, Config):
            return f"{type(o).__name__} @ {hex(id(o))}"
        elif isinstance(o, Task):
            return f"{type(o).__name__}({expand(o.task_config, o.task_config.desc)})"
        else:
            return str(o)

class FieldState(Enum):
    MISSING = "Missing Field"

class Config:
    """Config is a 'bag of fields' that behaves sort of like a Javascript object."""

    def __init__(self, **kwargs):
        self.__dict__['_fields'] = dict(**kwargs)

    def __getitem__(self, key):
        if key == "_fields":
            return self._fields
        val = self.get(key, default = None)
        if val is FieldState.MISSING:
            raise KeyError(f"{type(self).__name__} @ {id(self)} - Config key '{key}' was missing")
        if val is None:
            raise KeyError(f"{type(self).__name__} @ {id(self)} - Config key '{key}' was never defined")
        return val

    def __setitem__(self, key, val):
        if val is None:
            raise ValueError(f"{type(self).__name__} @ {id(self)} - Config key '{key}' cannot be set to None")
        self._fields[key] = val

    def __delitem__(self, key):
        del self._fields[key]

    def __getattr__(self, key):
        return self.__getitem__(key)

    def __setattr__(self, key, val):
        self.__setitem__(key, val)

    def __delattr__(self, key):
        self.__delitem__(key)

    def __add__(self, val):
        return Config(lhs = self, rhs = val)

    ########################################

    def __iter__(self):
        for named_config in self.named_iter():
            yield named_config[1]

    def named_iter(self):
        """
        Yields all config objects in the config graph in the order that we use them to resolve
        fields.
        We iterate over the queue in _reverse_ order so that newer sub-configs can override older
        sub-configs.
        """
        queue = [(None, self)]
        done = set()
        while(queue):
            named_config = queue.pop()
            if named_config[1] not in done:
                done.add(named_config[1])
                yield named_config
                for k, v in named_config[1]._fields.items():
                    if isinstance(v, Config):
                        queue.append((k,v))

    def flatten(self):
        return [config for config in self]

    ########################################

    def to_string(self):
        name = f"{type(self).__name__} @ {hex(id(self))} "
        return name + json.dumps(self._fields, indent=2, cls=Encoder)

    def __repr__(self):
        result = ""
        for named_config in self.named_iter():
            result += "." + named_config[0] + " = " if named_config[0] else ""
            result += named_config[1].to_string() + "\n"
        return result

    def to_dict(self):
        result = {}
        for config in reversed(self.flatten()):
            for key, val in config._fields.items():
                if not isinstance(val, Config) and val is not None:
                    result[key] = val
        return result

    def get(self, key, default=None, is_global = False):
        for config in self:
            if (val := config._fields.get(key, default)) is not None:
                return val
        if is_global:
            return None
        return global_config.get(key, default, is_global = True)

    def expand(self, variant):
        return expand(self, variant)

    def collapse(self):
        return Config(**self.to_dict())

    def clone(self):
        return Config(**self._fields)

    def merge(self, *args, **kwargs):
        for arg in args:
            if isinstance(arg, Config):
                config = arg._fields
            elif isinstance(arg, dict):
                config = arg
            else:
                raise ValueError(f"Unnamed args to merge() must be Configs or Dicts - got '{arg}'")
            self._fields.update(config)
        self._fields.update(kwargs)
        return self

    ########################################

    def rule(self, *args, **kwargs):
        """Returns a callable rule that uses this config blob (plus any kwargs)."""
        return Rule(self, *args, **kwargs)

    def task(self, source_files=None, build_files=None, *args, **kwargs):
        """Creates a task directly from this config object."""
        kwargs.setdefault('name', "<no_name>")
        if source_files is not None:
            kwargs.setdefault('source_files', source_files)
        if build_files is not None:
            kwargs.setdefault('build_files', build_files)
        return Task(self, *args, **kwargs)

    def subrepo(self, subrepo_path, *args, **kwargs):
        subrepo_path = abs_path(self.expand(self.this_path) / self.expand(subrepo_path))
        subrepo_config = Config(
            name = "Repo Config",
            repo_path = subrepo_path,
        )
        subrepo_config.merge(*args, **kwargs)
        return subrepo_config

    def include(self, hancho_file, *args, **kwargs):
        hancho_filepath = abs_path(self.expand(self.this_path) / self.expand(hancho_file))
        mod_config = self.clone(
            name      = "Include Config",
            this_file = hancho_filepath,
            this_path = hancho_filepath.parent,
        )
        mod_config.merge(*args, **kwargs)
        return app.load_module(mod_config, hancho_filepath)

    def load(self, hancho_file, *args, **kwargs):
        hancho_filepath = abs_path(self.expand(self.this_path) / self.expand(hancho_file))
        mod_config = self.clone(
            name      = "Mod Config",
            this_file = hancho_filepath,
            this_path = hancho_filepath.parent,
            mod_path  = hancho_filepath.parent,
        )
        mod_config.merge(*args, **kwargs)
        return app.load_module(mod_config, hancho_filepath)

####################################################################################################

class Rule(Config):
    """Rules are callable Configs that create a Task when called."""

    def __call__(self, source_files=None, build_files=None, **kwargs):
        if source_files is not None:
            kwargs['source_files'] = source_files
        if build_files is not None:
            kwargs['build_files'] = build_files
        return Task(rule=self, **kwargs)

####################################################################################################
# The template expansion / macro evaluation code requires some explanation.
#
# We do not necessarily know in advance how the users will nest strings, templates, callbacks,
# etcetera. So, when we need to produce a flat list of files from whatever was passed to
# source_files, we need to do a bunch of dynamic-dispatch-type stuff to ensure that we can always
# turn that thing into a flat list of files.
#
# We also need to ensure that if anything in this process throws an exception (or if an exception
# was passed into a rule due to a previous rule failing) that we always propagate the exception up
# to Task.run_async, where it will be handled and propagated to other Tasks.
#
# The result of this is that the functions here are mutually recursive in a way that can lead to
# confusing callstacks, but that should handle every possible case of stuff inside other stuff.
#
# The depth checks are to prevent recursive runaway - the MAX_EXPAND_DEPTH limit is arbitrary but
# should suffice.


def expand(config, variant):
    """Expands all templates anywhere inside 'variant'."""
    match variant:
        case BaseException():
            raise variant
        case Task():
            return expand(config, variant.promise)
        case Path():
            return Path(expand(config, str(variant)))
        case list():
            return [expand(config, s) for s in variant]
        case str() if macro_regex.search(variant):
            return eval_macro(config, variant)
        case str() if template_regex.search(variant):
            return expand_template(config, variant)
        case int() | bool() | float() | str():
            return variant
        case _ if inspect.isfunction(variant):
            return variant
        case _:
            raise ValueError(f"Don't know how to expand {type(variant)}='{variant}'")


def expand_template(config, template):
    """Replaces all macros in template with their stringified values."""
    if config.debug_expansion:
        log(f"┏ Expand '{template}'")

    try:
        app.expand_depth += 1
        old_template = template
        result = ""
        while span := template_regex.search(template):
            result += template[0 : span.start()]
            try:
                macro = template[span.start() : span.end()]
                variant = eval_macro(config, macro)
                result += " ".join([str(s) for s in flatten(variant)])
            except:
                log(color(255, 255, 0))
                log(f"Expanding template '{old_template}' failed!")
                log(color())
                raise
            template = template[span.end() :]
        result += template
    finally:
        app.expand_depth -= 1

    if config.debug_expansion:
        log(f"┗ '{result}'")
    return result


def eval_macro(config, macro):
    """Evaluates the contents of a "{macro}" string."""
    if app.expand_depth > MAX_EXPAND_DEPTH:
        raise RecursionError(f"Expanding '{macro}' failed to terminate")
    if config.debug_expansion:
        log(("┃" * app.expand_depth) + f"┏ Eval '{macro}'")
    app.expand_depth += 1
    # pylint: disable=eval-used
    try:
        # We must pass the JIT expanded config to eval() otherwise we'll try and join unexpanded
        # paths and stuff, which will break.
        class Expander:
            """JIT template expansion for use in eval()."""

            def __init__(self, config):
                self.config = config

            def __getitem__(self, key):
                return expand(self, self.config[key])

            def __getattr__(self, key):
                return expand(self, self.config[key])

        if not isinstance(config, Expander):
            result = eval(macro[1:-1], {}, Expander(config))
        else:
            result = eval(macro[1:-1], {}, config)
    except:
        log(color(255, 255, 0))
        log(f"Expanding macro '{macro}' failed!")
        log(color())
        raise
    finally:
        app.expand_depth -= 1

    if config.debug_expansion:
        log(("┃" * app.expand_depth) + f"┗ {result}")
    return result

####################################################################################################

class Task:
    """Calling a Rule creates a Task."""

    # pylint: disable=too-many-instance-attributes
    # pylint: disable=attribute-defined-outside-init

    def __init__(self, *args, **kwargs):
        self.desc        = None
        self.reason      = None
        self.task_index  = None
        self.promise     = None

        default_task_config = Config(
            name          = "Task Config",
            desc          = "{source_files} -> {build_files}",
            command       = FieldState.MISSING,
            command_path  = Path.cwd(),
            command_files = [],
            source_path   = Path.cwd(),
            source_files  = FieldState.MISSING,
            build_tag     = "",
            build_dir     = "build",
            build_path    = "{root_path/build_dir/build_tag/repo_name/rel_path(source_path, repo_path)}",
            build_files   = FieldState.MISSING,
            build_deps    = [],
        )

        self.task_config = default_task_config.merge(*args, **kwargs).collapse()
        app.tasks_total += 1
        app.pending_tasks.append(self)

    def __repr__(self):
        dump = json.dumps(self.__dict__, indent=2, cls=Encoder)
        return "task = " + dump + "\n.task_config = " + str(self.task_config)

    async def run_async(self):
        """Entry point for async task stuff, handles exceptions generated during task execution."""
        try:
            # Await everything awaitable in this task's rule.
            await await_variant(self.task_config)

            # Everything awaited, task_init runs synchronously.
            self.task_init()

            # Run the commands if we need to.
            if self.reason:
                result = await self.run_commands()
                app.tasks_pass += 1
            else:
                result = self.build_files
                app.tasks_skip += 1

            return result

        # If this task failed, we print the error and propagate a cancellation to downstream tasks.
        except BaseException:
            if not self.task_config.quiet:
                log(color(255, 128, 128))
                traceback.print_exception(*sys.exc_info())
                log(color())
            app.tasks_fail += 1
            return asyncio.CancelledError()

        # If any of this tasks's dependencies were cancelled, we propagate the cancellation to
        # downstream tasks.
        except asyncio.CancelledError as cancel:
            app.tasks_cancel += 1
            return cancel

        finally:
            if self.task_config.debug:
                log("")

    def task_init(self):
        """All the setup steps needed before we run a task."""

        # Expand all the critical fields
        self.desc          = expand(self.task_config, self.task_config.desc)
        self.command       = flatten(expand(self.task_config, self.task_config.command))
        self.command_path  = abs_path(expand(self.task_config, self.task_config.command_path))
        self.command_files = abs_path(flatten(expand(self.task_config, self.task_config.abs_command_files)))
        self.source_path   = abs_path(expand(self.task_config, self.task_config.source_path))
        self.source_files  = abs_path(flatten(expand(self.task_config, self.task_config.abs_source_files)))
        self.build_path    = abs_path(expand(self.task_config, self.task_config.build_path), strict=False)
        self.build_files   = abs_path(flatten(expand(self.task_config, self.task_config.abs_build_files)), strict=False)
        self.build_deps    = abs_path(flatten(expand(self.task_config, self.task_config.abs_build_deps)), strict=False)

        print(self.build_files)

        if not str(self.build_path).startswith(str(global_config.root_path)):
            raise ValueError(f"Path error, build_path {self.build_path} is not under root_path {global_config.root_path}")

        # Check for duplicate task outputs
        for abs_file in self.build_files:
            if abs_file in app.all_build_files:
                raise NameError(f"Multiple rules build {abs_file}!")
            app.all_build_files.add(abs_file)

        # Make sure our output directories exist
        if not self.task_config.dry_run:
            for abs_file in self.build_files:
                abs_file.parent.mkdir(parents=True, exist_ok=True)

        # Check if we need a rebuild
        self.reason = self.needs_rerun(self.task_config.force)

    def needs_rerun(self, force=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""
        # Pylint really doesn't like this function, lol.
        # pylint: disable=too-many-return-statements
        # pylint: disable=too-many-branches

        if force:
            return f"Files {self.build_files} forced to rebuild"
        if not self.source_files:
            return "Always rebuild a target with no inputs"
        if not self.build_files:
            return "Always rebuild a target with no outputs"

        # Check if any of our output files are missing.
        for abs_file in self.build_files:
            if not abs_file.exists():
                return f"Rebuilding because {abs_file} is missing"

        # Check if any of our input files are newer than the output files.
        min_out = min(mtime(f) for f in self.build_files)

        for abs_file in self.source_files:
            if mtime(abs_file) >= min_out:
                return f"Rebuilding because {abs_file} has changed"

        for abs_file in self.command_files:
            if mtime(abs_file) >= min_out:
                return f"Rebuilding because {abs_file} has changed"

        for mod in app.loaded_modules:
            if mtime(mod.__file__) >= min_out:
                return f"Rebuilding because {mod.__file__} has changed"

        # Check all dependencies in the depfile, if present.
        for abs_depfile in self.build_deps:
            if not abs_depfile.exists():
                continue
            if self.task_config.debug:
                log(f"Found depfile {abs_depfile}")
            with open(abs_depfile, encoding="utf-8") as depfile:
                deplines = None
                depformat = self.task_config.get('depformat', 'gcc')
                if depformat == "msvc":
                    # MSVC /sourceDependencies json depfile
                    deplines = json.load(depfile)["Data"]["Includes"]
                elif depformat == "gcc":
                    # GCC .d depfile
                    deplines = depfile.read().split()
                    deplines = [d for d in deplines[1:] if d != "\\"]
                else:
                    raise ValueError(f"Invalid depformat {depformat}")

                # The contents of the depfile are RELATIVE TO THE WORKING DIRECTORY
                deplines = [self.command_path / d for d in deplines]
                for abs_file in deplines:
                    if mtime(abs_file) >= min_out:
                        return f"Rebuilding because {abs_file} has changed"

        # All checks passed; we don't need to rebuild this output.
        if self.task_config.debug:
            log(f"Files {self.build_files} are up to date")

        # Empty string = no reason to rebuild
        return ""

    async def run_commands(self):
        """Grabs a lock on the jobs needed to run this task's commands, then runs all of them."""

        job_count = self.task_config.get('job_count', 1)
        try:
            # Wait for enough jobs to free up to run this task.
            await app.acquire_jobs(job_count)

            # Jobs acquired, we are now runnable so grab a task index.
            app.task_counter += 1
            self.task_index = app.task_counter

            # Print the "[1/N] Compiling foo.cpp -> foo.o" status line and debug information
            log(
                f"{color(128,255,196)}[{self.task_index}/{app.tasks_total}]{color()} {self.desc}",
                sameline=not self.task_config.verbose,
            )

            if self.task_config.verbose or self.task_config.debug:
                log(f"{color(128,128,128)}Reason: {self.reason}{color()}")

            if self.task_config.debug:
                log(self)

            result = []
            for exp_command in self.command:
                if self.task_config.verbose or self.task_config.debug:
                    sys.stdout.flush()
                    rel_command_path = rel_path(self.command_path, self.task_config.root_path)
                    log(f"{color(128,128,255)}{rel_command_path}$ {color()}", end="")
                    log("(DRY RUN) " if self.task_config.dry_run else "", end="")
                    log(exp_command)
                result = await self.run_command(exp_command)
        finally:
            await app.release_jobs(job_count)

        # After the build, the deps files should exist if specified.
        for abs_file in self.build_deps:
            if not abs_file.exists() and not self.task_config.dry_run:
                raise NameError(f"Dep file {abs_file} wasn't created")

        # Check if the commands actually updated all the output files.
        # _Don't_ do this if this task represents a call to an external build system, as that
        # system might not actually write to the output files.
        if (
            self.source_files
            and self.build_files
            and not (self.task_config.dry_run or self.task_config.get('ext_build', False))
        ):
            if second_reason := self.needs_rerun():
                raise ValueError(
                    f"Task '{self.desc}' still needs rerun after running!\n"
                    + f"Reason: {second_reason}"
                )

        return result

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        # Early exit if this is just a dry run
        if self.task_config.dry_run:
            return self.build_files

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            result = command(self)
            if inspect.isawaitable(result):
                result = await result
            return result

        # Non-string non-callable commands are not valid
        if not isinstance(command, str):
            raise ValueError(f"Don't know what to do with {command}")

        # Create the subprocess via asyncio and then await the result.
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=self.command_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        (stdout_data, stderr_data) = await proc.communicate()

        self.stdout = stdout_data.decode()
        self.stderr = stderr_data.decode()
        self.returncode = proc.returncode

        # Print command output if needed
        if not self.task_config.quiet and (self.stdout or self.stderr):
            if self.stderr:
                log("-----stderr-----")
                log(self.stderr, end="")
            if self.stdout:
                log("-----stdout-----")
                log(self.stdout, end="")

        # Task complete, check the task return code
        if self.returncode:
            raise ValueError(
                f"Command '{command}' exited with return code {self.returncode}"
            )

        # Task passed, return the output file list
        return self.build_files

####################################################################################################

# fmt: off

default_mod_config = Config(
    name          = "Default Mod Config",
    mod_file      = FieldState.MISSING,
    mod_path      = FieldState.MISSING,
    repo_path     = FieldState.MISSING,
    root_path     = FieldState.MISSING,
)

helper_functions = Config(
    name = "Helper Functions",
    abs_path=abs_path,
    rel_path=rel_path,
    join_path=join_path,
    color=color,
    glob=glob,
    len=len,
    Path=Path,
    run_cmd=run_cmd,
    swap_ext=swap_ext,
    flatten=flatten,
    print=print,
)

helper_macros = Config(
    name = "Helper Macros",

    repo_name  = "{repo_path.name if repo_path != root_path else ''}",

    rel_source_path   = "{rel_path(source_path, command_path)}",
    rel_build_path    = "{rel_path(build_path, command_path)}",

    abs_command_files = "{join_path(command_path, command_files)}",
    abs_source_files  = "{join_path(source_path, source_files)}",
    abs_build_files   = "{join_path(build_path, build_files)}",
    abs_build_deps    = "{join_path(build_path, build_deps)}",

    rel_command_files = "{rel_path(abs_command_files, command_path)}",
    rel_source_files  = "{rel_path(abs_source_files, command_path)}",
    rel_build_files   = "{rel_path(abs_build_files, command_path)}",
    rel_build_deps    = "{rel_path(abs_build_deps, command_path)}",
)

global_config = Config(
    name             = "Global Config",
    helper_functions = helper_functions,
    helper_macros    = helper_macros,
)

# fmt: on

####################################################################################################

class App:
    """The application state. Mostly here so that the linter will stop complaining about my use of
    global variables. :D"""

    # pylint: disable=too-many-instance-attributes
    def __init__(self):
        self.root_repo_config = None
        self.root_mod_config = None
        self.repos = []
        self.loaded_modules = []
        self.all_build_files = set()

        self.tasks_total = 0
        self.tasks_pass = 0
        self.tasks_fail = 0
        self.tasks_skip = 0
        self.tasks_cancel = 0
        self.task_counter = 0

        self.mtime_calls = 0
        self.line_dirty = False
        self.expand_depth = 0

        self.pending_tasks = []
        self.jobs_available = os.cpu_count()
        self.jobs_lock = asyncio.Condition()
        pass

    ########################################

    def main(self):
        """Our main() just handles command line args and delegates to async_main()"""

        time_a = time.perf_counter()

        if global_config.debug:
            log(f"global_config = {global_config}")

        root_config = Config(
            name         = "Root Config",
            this_file    = global_config.root_file,
            this_path    = global_config.root_path,
            mod_path     = global_config.root_path,
            repo_path    = global_config.root_path,
            root_path    = global_config.root_path,
            command_path = "{repo_path}",
            source_path  = "{mod_path}",
            build_path   = "{root_path/build_dir/build_tag/repo_name/rel_path(source_path, repo_path)}",
        )

        self.load_module(root_config, global_config.root_file)
        time_b = time.perf_counter()

        if global_config.debug or global_config.verbose:
            log(f"Loading .hancho files took {time_b-time_a:.3f} seconds")

        # For some reason "result = asyncio.run(self.async_main())" might be breaking actions in
        # Github, so I'm using get_event_loop().run_until_complete(). Seems to fix the issue.

        # Run tasks until we're done with all of them.
        result = asyncio.get_event_loop().run_until_complete(self.async_run_tasks())
        return result

    ########################################

    def queue_pending_tasks(self):
        """Creates an asyncio.Task for each task in the pending list and clears the pending list."""
        tasks = self.pending_tasks
        self.pending_tasks = []
        for task in tasks:
            task.promise = asyncio.create_task(task.run_async())
        return tasks

    ########################################

    async def async_run_tasks(self):
        # Root module(s) loaded. Run all tasks in the queue until we run out.

        self.jobs_available = global_config.jobs

        # Tasks can create other tasks, and we don't want to block waiting on a whole batch of
        # tasks to complete before queueing up more. Instead, we just keep queuing up any pending
        # tasks after awaiting each one. Because we're awaiting tasks in the order they were
        # created, this will effectively walk through all tasks in dependency order.
        time_a = time.perf_counter()
        tasks = self.queue_pending_tasks()
        while tasks:
            task = tasks.pop(0)
            if inspect.isawaitable(task.promise):
                await task.promise
            tasks.extend(self.queue_pending_tasks())
        time_b = time.perf_counter()
        if global_config.debug or global_config.verbose:
            log(f"Running tasks took {time_b-time_a:.3f} seconds")

        # Done, print status info if needed
        if global_config.debug:
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

    ########################################

    def load_module(self, build_config, mod_filepath):
        """Loads a Hancho module ***while chdir'd into its directory***"""

        # Look through our loaded modules and see if there's already a compatible one loaded.
        new_initial_dict = build_config.to_dict()
        reuse = None
        for mod in self.loaded_modules:
            if mod.__file__ != mod_filepath:
                continue

            old_initial_dict = mod.build_config.to_dict()
            # FIXME we just need to check that overlapping keys have matching values
            if old_initial_dict | new_initial_dict == old_initial_dict:
                if reuse is not None:
                    raise RuntimeError(f"Module load for {mod_filepath} is ambiguous")
                reuse = mod

        if reuse:
            if global_config.debug or global_config.verbose:
                log(color(255, 255, 128) + f"Reusing module {mod_filepath}" + color())
            return reuse

        if global_config.debug or global_config.verbose:
            log(color(128,255,128) + f"Loading module {mod_filepath}" + color())

        # There was no compatible module loaded, so make a new one.
        with open(mod_filepath, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, mod_filepath, "exec", dont_inherit=True)

        module = type(sys)(mod_filepath.stem)
        module.__file__ = mod_filepath
        module.__builtins__ = builtins
        module.self = module
        module.hancho = sys.modules["hancho"]
        module.build_config = build_config
        module.glob = glob

        self.loaded_modules.append(module)

        # We must chdir()s into the .hancho file directory before running it so that
        # glob() can resolve files relative to the .hancho file itself. We are _not_ in an async
        # context here so there should be no other threads trying to change cwd.
        with Chdir(module.build_config.this_path):
            # Why Pylint thinks this is not callable is a mystery.
            # pylint: disable=not-callable
            types.FunctionType(code, module.__dict__)()

        return module

    ########################################

    async def acquire_jobs(self, count):
        """Waits until 'count' jobs are available and then removes them from the job pool."""

        if count > global_config.jobs:
            raise ValueError(f"Nedd {count} jobs, but pool is {global_config.jobs}.")

        await self.jobs_lock.acquire()
        await self.jobs_lock.wait_for(lambda: self.jobs_available >= count)
        self.jobs_available -= count
        self.jobs_lock.release()

    ########################################

    async def release_jobs(self, count):
        """Returns 'count' jobs back to the job pool."""

        # NOTE: The notify_all here is required because we don't know in advance which tasks will
        # be capable of running after we return jobs to the pool. HOWEVER, this also creates an
        # O(N^2) slowdown when we have a very large number of pending tasks (>1000) due to the
        # "Thundering Herd" problem - all tasks will wake up, only a few will acquire jobs, the
        # rest will go back to sleep again, this will repeat for every call to release_jobs().
        await self.jobs_lock.acquire()
        self.jobs_available += count
        self.jobs_lock.notify_all()
        self.jobs_lock.release()

####################################################################################################
# Always create an App() object so we can use it for bookkeeping even if we loaded Hancho as a
# module instead of running it directly.

app = None

def main():

#    a = Config(foo = 1)
#    a.merge(Config(bar = 2, baz = 3), baq = 2)
#    a.merge({"foo": 7})
#    #a.merge("adsf")
#
#    print("----------")
#    print(a)
#    print("----------")
#
#    sys.exit(0)

    # pylint: disable=line-too-long
    # fmt: off
    parser = argparse.ArgumentParser()
    parser.add_argument("root_file",               default="build.hancho", type=str, nargs="?", help="The name of the .hancho file(s) to build")
    parser.add_argument("-C", "--chdir",           default=".", dest="root_path", type=str,     help="Change directory before starting the build")
    parser.add_argument("-j", "--jobs",            default=os.cpu_count(), type=int,            help="Run N jobs in parallel (default = cpu_count)")
    parser.add_argument("-v", "--verbose",         default=False, action="store_true",          help="Print verbose build info")
    parser.add_argument("-q", "--quiet",           default=False, action="store_true",          help="Mute all output")
    parser.add_argument("-n", "--dry_run",         default=False, action="store_true",          help="Do not run commands")
    parser.add_argument("-d", "--debug",           default=False, action="store_true",          help="Print debugging information")
    parser.add_argument("-f", "--force",           default=False, action="store_true",          help="Force rebuild of everything")
    parser.add_argument("-e", "--debug_expansion", default=False, action="store_true",          help="Debug template & macro expansion")
    # fmt: on

    # Parse the command line
    (flags, unrecognized) = parser.parse_known_args()
    flag_dict = flags.__dict__
    flag_dict['root_file'] = Path(flag_dict['root_file']).absolute()
    flag_dict['root_path'] = Path(flag_dict['root_path']).absolute()
    flag_dict['repo_path'] = Path(flag_dict['root_path']).absolute()

    global_config.config_flags = Config(name = "Config Flags", **flag_dict)

    # Unrecognized command line parameters also become config fields if they are flag-like
    global_config.unrecognized_flags = Config(name = "Unrecognized Flags")
    for span in unrecognized:
        if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
            key = match.group(1)
            val = match.group(2)
            val = maybe_as_number(val) if val is not None else True
            global_config.unrecognized_flags[key] = val

    #print(global_config)
    #print(global_config.debug)

    result = -1
    with Chdir(global_config.root_path):
        global app
        app = App()
        result = app.main()
    sys.exit(result)

if __name__ == "__main__":
    main()
