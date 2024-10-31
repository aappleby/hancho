#!/usr/bin/python3

"""Hancho v0.3.0 @ 2024-10-16 - A simple, pleasant build system."""

from os import path
import argparse
import asyncio
import builtins
import collections
import copy
import glob
import inspect
import io
import json
import os
import random
import re
import subprocess
import sys
import time
import traceback
import types
from enum import IntEnum

# If we were launched directly, a reference to this module is already in sys.modules[__name__].
# Stash another reference in sys.modules["hancho"] so that build.hancho and descendants don't try
# to load a second copy of Hancho.
sys.modules["hancho"] = sys.modules[__name__]

#---------------------------------------------------------------------------------------------------
# Logging stuff

first_line_block = True

def log_line(message):
    app.log += message
    if not app.quiet:
        sys.stdout.write(message)
        sys.stdout.flush()

def log(message, *, sameline=False, **kwargs):
    """Simple logger that can do same-line log messages like Ninja."""
    if not sys.stdout.isatty():
        sameline = False

    if sameline:
        kwargs.setdefault("end", "")

    output = io.StringIO()
    print(message, file=output, **kwargs)
    output = output.getvalue()

    if not output:
        return

    if sameline:
        output = output[: os.get_terminal_size().columns - 1]
        output = "\r" + output + "\x1B[K"
        log_line(output)
    else:
        if app.line_dirty:
            log_line("\n")
        log_line(output)

    app.line_dirty = sameline

def line_block(lines):
    count = len(lines)
    global first_line_block
    if not first_line_block:
        print(f"\x1b[{count}A")
    else:
        first_line_block = False
    for y in range(count):
        if (y > 0): print()
        line = lines[y]
        if line is not None:
            line = line[: os.get_terminal_size().columns - 20]
        print(line, end="")
        print("\x1b[K", end="")
        sys.stdout.flush()

def log_exception():
    log(color(255, 128, 128))
    log(traceback.format_exc())
    log(color())

#---------------------------------------------------------------------------------------------------
# Path manipulation

def abs_path(raw_path, strict=False) -> str | list[str]:

    if isinstance(raw_path, list):
        return [abs_path(p, strict) for p in raw_path]

    result = path.realpath(raw_path)
    if strict and not path.exists(result):
        raise FileNotFoundError(raw_path)
    return result

def rel_path(path1, path2):

    if isinstance(path1, list):
        return [rel_path(p, path2) for p in path1]

    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with simple string manipulation.
    return path1.removeprefix(path2 + "/") if path1 != path2 else "."

def join_path(path1, path2, *args):
    result = join_path2(path1, path2, *args)
    return flatten(result) if isinstance(result, list) else result

def join_path2(path1, path2, *args):

    if len(args) > 0:
        return [join_path(path1, p) for p in join_path(path2, *args)]
    if isinstance(path1, list):
        return [join_path(p, path2) for p in flatten(path1)]
    if isinstance(path2, list):
        return [join_path(path1, p) for p in flatten(path2)]

    if not path2:
        raise ValueError(f"Cannot join '{path1}' with '{type(path2)}' == '{path2}'")
    return path.join(path1, path2)

def normalize_path(file_path):
    assert isinstance(file_path, str)
    assert not macro_regex.search(file_path)

    file_path = path.realpath(path.join(os.getcwd(), file_path))
    assert path.isabs(file_path)
    if not path.isfile(file_path):
        print(f"Could not find file {file_path}")
        assert path.isfile(file_path)

    return file_path

#---------------------------------------------------------------------------------------------------
# Helper methods

def flatten(variant):
    if isinstance(variant, (list, tuple)):
        return [x for element in variant for x in flatten(element)]
    return [variant]

def join_prefix(prefix, strings):
    return [prefix+str(s) for s in flatten(strings)]

def join_suffix(strings, suffix):
    return [str(s)+suffix for s in flatten(strings)]

def stem(filename):
    filename = flatten(filename)[0]
    filename = path.basename(filename)
    return path.splitext(filename)[0]

def color(red=None, green=None, blue=None):
    """Converts RGB color to ANSI format string."""
    # Color strings don't work in Windows console, so don't emit them.
    #if not Config.use_color or os.name == "nt":
    #    return ""
    if red is None:
        return "\x1B[0m"
    return f"\x1B[38;2;{red};{green};{blue}m"

def run_cmd(cmd):
    """Runs a console command synchronously and returns its stdout with whitespace stripped."""
    return subprocess.check_output(cmd, shell=True, text=True).strip()

def swap_ext(name, new_ext):
    """Replaces file extensions on either a single filename or a list of filenames."""
    if isinstance(name, Task):
        name = name._out_files
    if isinstance(name, list):
        return [swap_ext(n, new_ext) for n in name]
    return path.splitext(name)[0] + new_ext

def mtime(filename):
    """Gets the file's mtime and tracks how many times we've called mtime()"""
    app.mtime_calls += 1
    return os.stat(filename).st_mtime_ns

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

####################################################################################################
# Variant stuff that I need to clean up

def merge_variant(lhs, rhs):
    if isinstance(lhs, Config) and isinstance(rhs, (Config, dict)):
        for key, rval in rhs.items():
            lhs[key] = merge_variant(lhs.get(key, None), rval)
        return lhs
    return copy.deepcopy(rhs)

def map_variant(key, val, apply):
    val = apply(key, val)
    if isinstance(val, (Config, dict)):
        for key2, val2 in val.items():
            val[key2] = map_variant(key2, val2, apply)
    elif isinstance(val, (list, tuple, set)):
        for key2, val2 in enumerate(val):
            val[key2] = map_variant(key2, val2, apply)
    return val

async def await_variant(variant):
    """Recursively replaces every awaitable in the variant with its awaited value."""

    match variant:
        case Exception() | asyncio.CancelledError():
            # This has to be here, it's what cancels downstream tasks.
            raise variant
        case Promise():
            variant = await variant.get()
            variant = await await_variant(variant)
        case Task():
            await variant.await_done()
            variant = await await_variant(variant._out_files)
        case Config() | dict():
            for key, val in variant.items():
                variant[key] = await await_variant(val)
        case list() | tuple() | set():
            for key, val in enumerate(variant):
                variant[key] = await await_variant(val)
        case _:
            while inspect.isawaitable(variant):
                variant = await variant
    return variant

####################################################################################################

class Dumper:
    def __init__(self, max_depth = 2):
        self.depth = 0
        self.max_depth = max_depth

    def indent(self):
        return "  " * self.depth

    def dump(self, variant):
        result = f"{type(variant).__name__} @ {hex(id(variant))} "
        match variant:
            case Task() | HanchoAPI():
                result += self.dump_dict(variant.config)
            case Config():
                result += self.dump_dict(variant)
            case list():
                result = self.dump_list(variant)
            case dict():
                result = self.dump_dict(variant)
            case str():
                result = '"' + str(variant) + '"'
            case _:
                result = str(variant)
        return result

    def dump_list(self, l):
        if len(l) == 0:
            return "[]"

        if len(l) == 1:
            return f"[{self.dump(l[0])}]"

        if self.depth >= self.max_depth:
            return "[...]"

        result = "[\n"
        self.depth += 1
        for val in l:
            result += self.indent() + self.dump(val) + ",\n"
        self.depth -= 1
        result += self.indent() + "]"
        return result

    def dump_dict(self, d):
        if self.depth >= self.max_depth:
            return "{...}"

        result = "{\n"
        self.depth += 1
        for key, val in d.items():
            result += self.indent() + f"{key} = " + self.dump(val) + ",\n"
        self.depth -= 1
        result += self.indent() + "}"
        return result

####################################################################################################

class Utils:
    # fmt: off
    abs_path    = staticmethod(abs_path)
    rel_path    = staticmethod(rel_path)
    join_path   = staticmethod(join_path)
    color       = staticmethod(color)
    glob        = staticmethod(glob.glob)
    len         = staticmethod(len)
    run_cmd     = staticmethod(run_cmd)
    swap_ext    = staticmethod(swap_ext)
    flatten     = staticmethod(flatten)
    print       = staticmethod(print)
    log         = staticmethod(log)
    path        = path
    re          = re
    join_prefix = staticmethod(join_prefix)
    join_suffix = staticmethod(join_suffix)
    stem        = staticmethod(stem)
    # fmt: on

####################################################################################################
# FIXME this should probably just inherit from dict...

class Config(collections.abc.MutableMapping, Utils):
    """A Config object is just a 'bag of fields'."""

    def __init__(self, *args, **kwargs):
        self.merge(*args, **kwargs)

    def fork(self, *args, **kwargs):
        return type(self)(self, *args, **kwargs)

    def merge(self, *args, **kwargs):
        for arg in flatten(args):
            if arg is not None:
                assert isinstance(arg, (Config, dict))
                merge_variant(self, arg)
        merge_variant(self, kwargs)
        return self

    def __repr__(self):
        return Dumper(1).dump(self)

    def expand(self, variant):
        return expand_variant(Expander(self), variant)

    def rel(self, sub_path):
        return rel_path(sub_path, expand_variant(self, self.task_dir))

    #----------------------------------------

    def __contains__(self, key):
        return key in self.__dict__

    def __iter__(self):
        return self.__dict__.__iter__()

    def __len__(self):
        return len(self.__dict__.keys())

    def __setitem__(self, key, val):
        self.__dict__[key] = val

    def __delitem__(self, key):
        del self.__dict__[key]

    def __getitem__(self, key):
        return self.__dict__[key]

####################################################################################################

class Command(Config):
    pass

####################################################################################################
# Hancho's text expansion system. Works similarly to Python's F-strings, but with quite a bit more
# power.
#
# The code here requires some explanation.
#
# We do not necessarily know in advance how the users will nest strings, macros, callbacks,
# etcetera. Text expansion therefore requires dynamic-dispatch-type stuff to ensure that we always
# end up with flat strings.
#
# The result of this is that the functions here are mutually recursive in a way that can lead to
# confusing callstacks, but that should handle every possible case of stuff inside other stuff.
#
# The depth checks are to prevent recursive runaway - the MAX_EXPAND_DEPTH limit is arbitrary but
# should suffice.
#
# Also - TEFINAE - Text Expansion Failure Is Not An Error. Config objects can contain macros that
# are not expandable inside the config. This allows config objects nested inside other configs to
# contain templates that can only be expanded in the context of the outer config, and things will
# still Just Work.

# The maximum number of recursion levels we will do to expand a macro.
# Tests currently require MAX_EXPAND_DEPTH >= 6
MAX_EXPAND_DEPTH = 20

# Matches macros inside a string.
macro_regex = re.compile("{[^{}]*}")

#----------------------------------------
# Helper methods

def trace_prefix(expander):
    """ Prints the left-side trellis of the expansion traces. """
    assert isinstance(expander, Expander)
    return hex(id(expander.config)) + ": " + ("┃ " * app.expand_depth)

def trace_variant(variant):
    """ Prints the right-side values of the expansion traces. """
    if callable(variant):
        return f"Callable @ {hex(id(variant))}"
    elif isinstance(variant, Config):
        return f"Config @ {hex(id(variant))}'"
    elif isinstance(variant, Expander):
        return f"Expander @ {hex(id(variant.config))}'"
    else:
        return f"'{variant}'"

def expand_inc():
    """ Increments the current expansion recursion depth. """
    app.expand_depth += 1
    if app.expand_depth > MAX_EXPAND_DEPTH:
        log("Text expansion failed to terminate")
        raise RecursionError("Text expansion failed to terminate")

def expand_dec():
    """ Decrements the current expansion recursion depth. """
    app.expand_depth -= 1
    if app.expand_depth < 0:
        raise RecursionError("Text expand_inc/dec unbalanced")

def stringify_variant(variant):
    """ Converts any type into an expansion-compatible string. """
    match variant:
        case Expander():
            return stringify_variant(variant.config)
        case Task():
            return stringify_variant(variant._out_files)
        case list():
            variant = [stringify_variant(val) for val in variant]
            return " ".join(variant)
        case _:
            return str(variant)

class Expander:
    """ Wraps a Config object and expands all fields read from it. """

    def __init__(self, config):
        self.config = config
        # We save a copy of 'trace', otherwise we end up printing traces of reading trace.... :P
        self.trace = config.get('trace', app.default_trace)

    def __getitem__(self, key):
        return self.get(key)

    def __getattr__(self, key):
        return self.get(key)

    def get(self, key):
        # FIXME - I don't remember why this has to be getattr(), but if I change it it stops
        # working.
        try:
            val = getattr(self.config, key)
        except KeyError:
            if self.trace:
                log(trace_prefix(self) + f"Read '{key}' failed")
            raise

        if self.trace:
            log(trace_prefix(self) + f"Read '{key}' = {trace_variant(val)}")
        val = expand_variant(self, val)
        return val

def expand_text(expander, text):
    """ Replaces all macros in 'text' with their expanded, stringified values. """

    if not macro_regex.search(text):
        return text

    if expander.trace:
        log(trace_prefix(expander) + f"┏ expand_text '{text}'")
    expand_inc()

    #==========

    temp = text
    result = ""
    while span := macro_regex.search(temp):
        result += temp[0 : span.start()]
        macro = temp[span.start() : span.end()]
        variant = expand_macro(expander, macro)
        result += stringify_variant(variant)
        temp = temp[span.end() :]
    result += temp

    #==========

    expand_dec()
    if expander.trace:
        log(trace_prefix(expander) + f"┗ expand_text '{text}' = '{result}'")

    # If expansion changed the text, try to expand it again.
    if result != text:
        result = expand_text(expander, result)

    return result

def expand_macro(expander, macro):
    """ Evaluates the contents of a "{macro}" string. If eval throws an exception, the macro is
    returned unchanged. """

    assert isinstance(expander, Expander)

    if expander.trace:
        log(trace_prefix(expander) + f"┏ expand_macro '{macro}'")
    expand_inc()

    #==========

    result = macro
    failed = False

    try:
        result = eval(macro[1:-1], {}, expander) # pylint: disable=eval-used
    except Exception: # pylint: disable=broad-exception-caught
        failed = True

    #==========

    expand_dec()
    if expander.trace:
        if failed:
            log(trace_prefix(expander) + f"┗ expand_macro '{macro}' failed")
        else:
            log(trace_prefix(expander) + f"┗ expand_macro '{macro}' = {result}")
    return result

def expand_variant(expander, variant):
    """ Expands all macros anywhere inside 'variant', making deep copies where needed so we don't
    expand someone else's data. """

    # This level of tracing is too spammy to be useful.
    #if expander.trace:
    #   log(trace_config(expander) + f"┏ expand_variant {trace_variant(variant)}")
    #expand_inc()

    #==========

    match variant:
        case str():
            result = expand_text(expander, variant)
        case list():
            result = [expand_variant(expander, val) for val in variant]
        case dict():
            result = {expand_variant(expander, key): expand_variant(expander, val) for key, val in variant.items()}
        case Config():
            result = Expander(variant)
        case _:
            result = variant

    #==========

    #expand_dec()
    #if expander.trace:
    #    log(trace_config(expander) + f"┗ expand_variant {trace_variant(variant)} = {trace_variant(result)}")

    return result

####################################################################################################

class Promise:
    def __init__(self, task, *args):
        self.task = task
        self.args = args

    async def get(self):
        await self.task.await_done()
        match len(self.args):
            case 0:
                return self.task._out_files
            case 1:
                return self.task.config[self.args[0]]
            case _:
                return [self.task.config[field] for field in self.args]

####################################################################################################

class TaskState(IntEnum):
    DECLARED = 0
    QUEUED = 1
    STARTED = 2
    AWAITING_INPUTS = 3
    TASK_INIT = 4
    AWAITING_JOBS = 5
    RUNNING_COMMANDS = 6
    FINISHED = 7
    CANCELLED = 8
    FAILED = 9
    SKIPPED = 10
    BROKEN = 11

####################################################################################################

class Task:
    """Calling a Command creates a Task."""

    def __init__(self, *args, **kwargs):
        #super().__init__(*args, **kwargs)

        default_config = Config(
            desc      = app.default_desc,
            command   = app.default_command,
            task_dir  = app.default_task_dir,
            build_dir = app.default_build_dir,
            log_path  = app.default_log_path,
        )

        self.config = Config(
            default_config,
            *args,
            **kwargs
        )

        assert isinstance(self.config.command, (str, list)) or callable(self.config.command) or self.config.command is None
        assert isinstance(self.config.task_dir, str)
        assert isinstance(self.config.build_dir, str)

        self._task_index = 0
        self._in_files  = []
        self._out_files = []
        self._state = TaskState.DECLARED
        self._reason = None
        self._promise = None
        self._loaded_files = list(app.loaded_files)
        self._stdout = ""
        self._stderr = ""
        self._returncode = -1

        app.all_tasks.append(self)

    #----------------------------------------

    # WARNING: Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    #----------------------------------------

    def queue(self):
        if self._state is TaskState.DECLARED:
            app.queued_tasks.append(self)
            self._state = TaskState.QUEUED
            def apply(key, val):
                if isinstance(val, Task):
                    val.queue()
                return val
            map_variant(None, self.config, apply)

    def start(self):
        self.queue()
        if self._state is TaskState.QUEUED:
            self._promise = asyncio.create_task(self.task_main())
            self._state = TaskState.STARTED
            app.tasks_started += 1

    async def await_done(self):
        self.start()
        await self._promise

    def promise(self, *args):
        return Promise(self, *args)

    #-----------------------------------------------------------------------------------------------

    def print_status(self):
        """ Print the "[1/N] Compiling foo.cpp -> foo.o" status line and debug information """
        verbose = self.config.get('verbose', app.default_verbose)
        log(
            f"{color(128,255,196)}[{self._task_index}/{app.tasks_started}]{color()} {self.config.desc}",
            sameline=not verbose,
        )

    #-----------------------------------------------------------------------------------------------

    async def task_main(self):
        """Entry point for async task stuff, handles exceptions generated during task execution."""

        verbose = self.config.get('verbose', app.default_verbose)
        debug   = self.config.get('debug',   app.default_debug)
        force   = self.config.get('force',   app.default_force)


        # Await everything awaitable in this task except the task's own promise.
        # If any of this tasks's dependencies were cancelled, we propagate the cancellation to
        # downstream tasks.
        try:
            assert self._state is TaskState.STARTED
            self._state = TaskState.AWAITING_INPUTS
            for key, val in self.config.items():
                if key != "_promise":
                    self.config[key] = await await_variant(val)

        except Exception as err: # pylint: disable=broad-exception-caught
            log_exception()
            self._state = TaskState.CANCELLED
            app.tasks_cancelled += 1
            return err

        # Everything awaited, task_init runs synchronously.
        try:
            self._state = TaskState.TASK_INIT
            self.task_init()
        except Exception as err: # pylint: disable=broad-exception-caught
            log_exception()
            self._state = TaskState.BROKEN
            app.tasks_broken += 1
            return err

        # Early-out if this is a no-op task
        if self.config.command is None:
            app.tasks_finished += 1
            self._state = TaskState.FINISHED
            return self._out_files

        # Check if we need a rebuild
        self._reason = self.needs_rerun(force)
        if not self._reason:
            app.tasks_skipped += 1
            self._state = TaskState.SKIPPED
            return self._out_files

        if verbose or debug:
            log(f"{color(128,128,128)}Reason: {self._reason}{color()}")

        # Wait for enough jobs to free up to run this task.
        job_count = self.config.get("job_count", 1)
        self._state = TaskState.AWAITING_JOBS
        await app.job_pool.acquire_jobs(job_count, self)

        # Run the commands.
        self._state = TaskState.RUNNING_COMMANDS
        app.tasks_running += 1
        self._task_index = app.tasks_running
        self.print_status()

        try:
            for command in flatten(self.config.command):
                await self.run_command(command)
                if self._returncode != 0:
                    break
        except Exception as err: # pylint: disable=broad-exception-caught
            # If any command failed, we print the error and propagate it to downstream tasks.
            log_exception()
            self._state = TaskState.FAILED
            app.tasks_failed += 1
            return err
        finally:
            await app.job_pool.release_jobs(job_count, self)

        # Task finished successfully
        self._state = TaskState.FINISHED
        app.tasks_finished += 1

    #-----------------------------------------------------------------------------------------------

    def task_init(self):
        """All the setup steps needed before we run a task."""

        debug = self.config.get('debug', app.default_debug)

        if debug:
            log(f"\nTask before expand: {self.config}")

        #----------------------------------------
        # Expand task_dir and build_dir

        self.config.task_dir  = abs_path(self.config.expand(self.config.task_dir))
        self.config.build_dir = abs_path(self.config.expand(self.config.build_dir))

        #----------------------------------------
        # Expand all in_ and out_ filenames.
        # We _must_ expand these first before joining paths or the paths will be incorrect:
        # prefix + swap(abs_path) != abs(prefix + swap(path))

        for key, val in self.config.items():
            if key.startswith("in_") or key.startswith("out_") or key == "c_deps":
                self.config[key] = self.config.expand(val)

        #----------------------------------------
        # Convert all in_, out_, and deps filenames to absolute paths.

        def join_dir(key, val):
            if isinstance(key, str):
                if key.startswith("out_"):
                    val = join_path(self.config.build_dir, val)
                    val = abs_path(val)
                    self._out_files.extend(flatten(val))
                if key.startswith("in_"):
                    val = join_path(self.config.task_dir, val)
                    val = abs_path(val)
                    self._in_files.extend(flatten(val))
                if key == "c_deps":
                    val = join_path(self.config.build_dir, val)
                    val = abs_path(val)
                    if path.isfile(val):
                        self._in_files.append(val)
            return val

        map_variant(None, self.config, join_dir)

        #----------------------------------------
        # And now we can expand the command.

        self.config.desc     = self.config.expand(self.config.desc)
        self.config.command  = self.config.expand(self.config.command)
        self.config.log_path = self.config.expand(self.config.log_path)

        if debug:
            log(f"\nTask after expand: {self.config}")

        #----------------------------------------
        # Sanity checks

        # Check for missing input files/paths
        if not path.exists(self.config.task_dir):
            raise FileNotFoundError(self.config.task_dir)

        for file in self._in_files:
            if file is None:
                raise ValueError("_in_files contained a None")
            if not path.exists(file):
                raise FileNotFoundError(file)

        # Check that all build files would end up under root_dir
        for file in self._out_files:
            if file is None:
                raise ValueError("_out_files contained a None")
            # Raw tasks may not have a root_dir
            if root_dir := self.config.get("root_dir", None):
                if not file.startswith(root_dir):
                    raise ValueError(f"Path error, output file {file} is not under root_dir {root_dir}")

        # Check for duplicate task outputs
        if self.config.command:
            for file in self._out_files:
                if file in app.all_out_files:
                    raise NameError(f"Multiple rules build {file}!")
                app.all_out_files.add(file)

        # Make sure our output directories exist
        if not app.dry_run:
            for file in self._out_files:
                os.makedirs(path.dirname(file), exist_ok=True)

    #-----------------------------------------------------------------------------------------------

    def needs_rerun(self, force=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""

        debug = self.config.get('debug', app.default_debug)

        if force:
            return f"Files {self._out_files} forced to rebuild"
        if not self._in_files:
            return "Always rebuild a target with no inputs"
        if not self._out_files:
            return "Always rebuild a target with no outputs"

        # Check if any of our output files are missing.
        for file in self._out_files:
            if not path.exists(file):
                return f"Rebuilding because {file} is missing"

        # Check if any of our input files are newer than the output files.
        min_out = min(mtime(f) for f in self._out_files)

        if mtime(__file__) >= min_out:
            return "Rebuilding because hancho.py has changed"

        for file in self._in_files:
            if mtime(file) >= min_out:
                return f"Rebuilding because {file} has changed"

        for mod_filename in self._loaded_files:
            if mtime(mod_filename) >= min_out:
                return f"Rebuilding because {mod_filename} has changed"

        # Check all dependencies in the C dependencies file, if present.
        if (c_deps := self.config.get("c_deps", None)) and path.exists(c_deps):
            c_depformat = self.config.get("c_depformat", "gcc")
            if debug:
                log(f"Found C dependencies file {c_deps}")
            with open(c_deps, encoding="utf-8") as c_deps:
                deplines = None
                if c_depformat == "msvc":
                    # MSVC /sourceDependencies json c_deps
                    deplines = json.load(c_deps)["Data"]["Includes"]
                elif c_depformat == "gcc":
                    # GCC -MMD .d c_deps
                    deplines = c_deps.read().split()
                    deplines = [d for d in deplines[1:] if d != "\\"]
                else:
                    raise ValueError(f"Invalid dependency file format {c_depformat}")

                # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
                deplines = [path.join(self.config.task_dir, d) for d in deplines]
                for abs_file in deplines:
                    if mtime(abs_file) >= min_out:
                        return f"Rebuilding because {abs_file} has changed"

        # All checks passed; we don't need to rebuild this output.
        # Empty string = no reason to rebuild
        return ""

    #-----------------------------------------------------------------------------------------------

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        verbose = self.config.get('verbose', app.default_verbose)
        debug   = self.config.get('debug',   app.default_debug)

        if verbose or debug:
            root_dir = self.config.get("root_dir", "/")
            log(color(128,128,255), end="")
            if app.dry_run: log("(DRY RUN) ", end="")
            log(f"{rel_path(self.config.task_dir, root_dir)}$ ", end="")
            log(color(), end="")
            log(command)

        # Dry runs get early-out'ed before we do anything.
        if app.dry_run:
            return

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            app.pushdir(self.config.task_dir)
            command(self)
            app.popdir()
            self._returncode = 0
            return

        # Non-string non-callable commands are not valid
        if not isinstance(command, str):
            raise ValueError(f"Don't know what to do with {command}")

        # Create the subprocess via asyncio and then await the result.
        if debug:
            log(f"Task {hex(id(self))} subprocess start '{command}'")

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=self.config.task_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        (stdout_data, stderr_data) = await proc.communicate()

        if debug:
            log(f"Task {hex(id(self))} subprocess done '{command}'")

        self._stdout = stdout_data.decode()
        self._stderr = stderr_data.decode()
        self._returncode = proc.returncode

        # FIXME we need a better way to handle "should fail" so we don't constantly keep rerunning
        # intentionally-failing tests every build
        command_pass = (self._returncode == 0) != self.config.get('should_fail', False)

        if (log_path := self.config.get('log_path', app.default_log_path)) is not None:
            result = open(log_path, "w", encoding="utf-8")
            result.write(str(self))
            result.write("\n")
            result.close()

        if debug or verbose or not command_pass:
            if not command_pass:
                log(f"{color(128,255,196)}[{self._task_index}/{app.tasks_started}]{color(255,128,128)} Task failed {color()}- '{self.config.desc}'")
                log(f"Task dir: {self.config.task_dir}")
                log(f"Command : {self.config.command}")
                log(f"Return  : {self._returncode}")
            else:
                log(f"{color(128,255,196)}[{self._task_index}/{app.tasks_started}]{color()} Task passed - '{self.config.desc}'")
            if self._stdout:
                log("Stdout:")
                log(self._stdout, end="")
            if self._stderr:
                log("Stderr:")
                log(self._stderr, end="")

        if not command_pass:
            raise ValueError(self._returncode)

####################################################################################################

class HanchoAPI(Utils):

    def __init__(self):
        self.config  = Config()
        self.Config  = Config
        self.Command = Command
        self.Task    = Task

    def __repr__(self):
        return Dumper(2).dump(self)

    def __contains__(self, key):
        return key in self.__dict__

    def __call__(self, arg1 = None, /, *args, **kwargs):
        if callable(arg1):
            return arg1(self, *args, **kwargs)
        return Task(self.config, arg1, *args, **kwargs)

    def load(self, mod_path):
        mod_path = self.config.expand(mod_path)
        mod_path = normalize_path(mod_path)
        new_config = Config(
            self.config,
            mod_name = path.splitext(path.basename(mod_path))[0],
            mod_dir  = path.dirname(mod_path),
            mod_path = mod_path,
            task_dir = path.dirname(mod_path),
            build_dir = app.default_build_dir,
        )

        new_context = copy.deepcopy(self)
        new_context.config = new_config
        return new_context._load_module()

    def repo(self, mod_path):
        mod_path = self.config.expand(mod_path)
        mod_path = normalize_path(mod_path)
        new_config = Config(
            self.config,
            repo_name = path.basename(path.dirname(mod_path)),
            repo_dir  = path.dirname(mod_path),
            mod_name  = path.splitext(path.basename(mod_path))[0],
            mod_dir   = path.dirname(mod_path),
            mod_path  = mod_path,
            task_dir  = path.dirname(mod_path),
            build_dir = app.default_build_dir,
        )

        new_context = copy.deepcopy(self)
        new_context.config = new_config
        return new_context._load_module()

    def root(self, mod_path):
        mod_path = self.config.expand(mod_path)
        mod_path = normalize_path(mod_path)
        new_config = Config(
            self.config,
            root_dir  = path.dirname(mod_path),
            root_path = mod_path,
            repo_name = path.basename(path.dirname(mod_path)),
            repo_dir  = path.dirname(mod_path),
            build_root  = app.default_build_root,
            build_tag   = app.default_build_tag,
            mod_name  = path.splitext(path.basename(mod_path))[0],
            mod_dir   = path.dirname(mod_path),
            mod_path  = mod_path,
        )

        new_context = copy.deepcopy(self)
        new_context.config = new_config
        return new_context._load_module()

    def _load_module(self):
        log(("┃ " * (len(app.dirstack) - 1)), end="")
        log(color(128,255,128) + f"Loading {self.config.mod_path}" + color())

        app.loaded_files.append(self.config.mod_path)

        # We're using compile() and FunctionType()() here beause exec() doesn't preserve source
        # code for debugging.
        file = open(self.config.mod_path, encoding="utf-8")
        source = file.read()
        code = compile(source, self.config.mod_path, "exec", dont_inherit=True)

        # We must chdir()s into the .hancho file directory before running it so that
        # glob() can resolve files relative to the .hancho file itself. We are _not_ in an async
        # context here so there should be no other threads trying to change cwd.
        app.pushdir(path.dirname(self.config.mod_path))
        temp_globals = { "hancho" : self, "__builtins__": builtins }
        types.FunctionType(code, temp_globals)()
        app.popdir()

        # Module loaded, turn the module's globals into a Config that doesn't include __builtins__,
        # hancho, and imports so we don't have files that end up transitively containing the
        # universe
        new_module = Config()
        for key, val in temp_globals.items():
            if key.startswith('_') or key == 'hancho' or isinstance(val, type(sys)):
                continue
            new_module[key] = val
        return new_module

####################################################################################################

class JobPool:
    def __init__(self):
        self.jobs_available = os.cpu_count()
        self.jobs_lock = asyncio.Condition()
        self.job_slots = [None] * self.jobs_available

    def reset(self, job_count):
        self.jobs_available = job_count
        self.job_slots = [None] * self.jobs_available

    ########################################

    async def acquire_jobs(self, count, token):
        """Waits until 'count' jobs are available and then removes them from the job pool."""

        if count > app.jobs:
            raise ValueError(f"Nedd {count} jobs, but pool is {app.jobs}.")

        await self.jobs_lock.acquire()
        await self.jobs_lock.wait_for(lambda: self.jobs_available >= count)

        slots_remaining = count
        for i, val in enumerate(self.job_slots):
            if val == None and slots_remaining:
                self.job_slots[i] = token
                slots_remaining -= 1

        self.jobs_available -= count
        self.jobs_lock.release()

    ########################################
    # NOTE: The notify_all here is required because we don't know in advance which tasks will
    # be capable of running after we return jobs to the pool. HOWEVER, this also creates an
    # O(N^2) slowdown when we have a very large number of pending tasks (>1000) due to the
    # "Thundering Herd" problem - all tasks will wake up, only a few will acquire jobs, the
    # rest will go back to sleep again, this will repeat for every call to release_jobs().

    async def release_jobs(self, count, token):
        """Returns 'count' jobs back to the job pool."""

        await self.jobs_lock.acquire()
        self.jobs_available += count

        slots_remaining = count
        for i, val in enumerate(self.job_slots):
            if val == token:
                self.job_slots[i] = None
                slots_remaining -= 1

        self.jobs_lock.notify_all()
        self.jobs_lock.release()

####################################################################################################

class App:

    def __init__(self):
        self.shuffle   = False
        self.use_color = True
        self.quiet     = False
        self.dry_run   = False
        self.jobs      = os.cpu_count()
        self.target    = None

        self.loaded_files = []
        self.dirstack = [os.getcwd()]

        self.all_out_files = set()

        self.mtime_calls = 0
        self.line_dirty = False
        self.expand_depth = 0
        self.shuffle = False

        self.tasks_started = 0
        self.tasks_running = 0
        self.tasks_finished = 0
        self.tasks_failed = 0
        self.tasks_skipped = 0
        self.tasks_cancelled = 0
        self.tasks_broken = 0

        self.all_tasks = []
        self.queued_tasks = []
        self.started_tasks = []
        self.finished_tasks = []
        self.log = ""

        self.job_pool = JobPool()

        self.default_desc       = "{command}"
        self.default_command    = None
        self.default_task_dir   = "{mod_dir}"
        self.default_build_dir  = "{build_root}/{build_tag}/{repo_name}/{rel_path(task_dir, repo_dir)}"
        self.default_build_root = "{root_dir}/build"
        self.default_build_tag  = ""
        self.default_log_path   = None

        self.default_verbose = False
        self.default_debug   = False
        self.default_force   = False
        self.default_trace   = False


    def reset(self):
        self.__init__() # pylint: disable=unnecessary-dunder-call

    ########################################
    # Needs to be its own function, used by run_tests.py

    def create_root_context(self, flags, extra_flags):

        root_file = flags['root_file']
        root_dir  = path.realpath(flags['root_dir']) # Root path must be absolute.
        root_path = path.join(root_dir, root_file)

        root_config = Config(
            root_dir    = root_dir,
            root_path   = root_path,

            repo_name   = "",
            repo_dir    = root_dir,

            mod_name    = path.splitext(root_file)[0],
            mod_dir     = root_dir,
            mod_path    = root_path,

            build_root  = app.default_build_root,
            build_tag   = app.default_build_tag,
        )

        # All the unrecognized flags get stuck on the root context.
        for key, val in extra_flags.items():
            setattr(root_config, key, val)

        root_context = HanchoAPI()
        root_context.config = root_config
        return root_context

    ########################################

    def main(self, flags, extra_flags):

        # These flags are app-wide and not context-wide.
        app_flags = ['shuffle', 'use_color', 'quiet', 'dry_run', 'jobs', 'target']
        for flag in app_flags:
            setattr(app, flag, flags[flag])
            del flags[flag]

        app.default_verbose = flags['verbose']
        app.default_debug =   flags['debug']
        app.default_force =   flags['force']
        app.default_trace =   flags['trace']

        app.root_context = self.create_root_context(flags, extra_flags)

        if app.root_context.config.get("debug", None):
            log(f"root_context = {Dumper().dump(app.root_context)}")

        if not path.isfile(app.root_context.config.root_path):
            print(f"Could not find root Hancho file {app.root_context.config.root_path}!")
            sys.exit(-1)

        assert path.isabs(app.root_context.config.root_dir)
        assert path.isdir(app.root_context.config.root_dir)
        assert path.isabs(app.root_context.config.root_path)
        assert path.isfile(app.root_context.config.root_path)

        os.chdir(app.root_context.config.root_dir)
        time_a = time.perf_counter()
        app.root_context._load_module()
        time_b = time.perf_counter()

        #if app.default_debug or app.default_verbose:
        log(f"Loading .hancho files took {time_b-time_a:.3f} seconds")

        #========================================

        time_a = time.perf_counter()
        if app.target:
            target_regex = re.compile(app.target)
            for task in app.all_tasks:
                queue_task = False
                task_name = None
                # FIXME this doesn't work because we haven't expanded output filenames yet
                #for out_file in flatten(task._out_files):
                #    if target_regex.search(out_file):
                #        queue_task = True
                #        task_name = out_file
                #        break
                if name := task.get('name', None):
                    if target_regex.search(task.get('name', None)):
                        queue_task = True
                        task_name = name
                if queue_task:
                    log(f"Queueing task for '{task_name}'")
                    task.queue()
        else:
            for task in app.all_tasks:
                # If no target was specified, we queue up all tasks from the root repo.
                root_dir = task.config.get("root_dir", None)
                repo_dir = task.config.get("repo_dir", None)
                if root_dir == repo_dir:
                    task.queue()
        time_b = time.perf_counter()
        #if app.default_debug or app.default_verbose:
        log(f"Queueing {len(app.queued_tasks)} tasks took {time_b-time_a:.3f} seconds")

        result = self.build()
        return result

    ########################################

    def pushdir(self, new_dir : str):
        new_dir = abs_path(new_dir, strict=True)
        self.dirstack.append(new_dir)
        os.chdir(new_dir)

    def popdir(self):
        self.dirstack.pop()
        os.chdir(self.dirstack[-1])

    ########################################

    def build(self):
        """Run tasks until we're done with all of them."""
        result = -1
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = asyncio.run(self.async_run_tasks())
        except Exception:
            log_exception()
        loop.close()
        return result

    def build_all(self):
        for task in self.all_tasks:
            task.queue()
        return self.build()

    ########################################

    async def async_run_tasks(self):
        # Run all tasks in the queue until we run out.

        self.job_pool.reset(self.jobs)

        # Tasks can create other tasks, and we don't want to block waiting on a whole batch of
        # tasks to complete before queueing up more. Instead, we just keep queuing up any pending
        # tasks after awaiting each one. Because we're awaiting tasks in the order they were
        # created, this will effectively walk through all tasks in dependency order.

        time_a = time.perf_counter()

        while self.queued_tasks or self.started_tasks:
            if app.shuffle:
                log(f"Shufflin' {len(self.queued_tasks)} tasks")
                random.shuffle(self.queued_tasks)

            while self.queued_tasks:
                task = self.queued_tasks.pop(0)
                task.start()
                self.started_tasks.append(task)

            task = self.started_tasks.pop(0)
            await task._promise
            self.finished_tasks.append(task)

        time_b = time.perf_counter()

        #if app.debug or app.verbose:
        log(f"Running {app.tasks_started} tasks took {time_b-time_a:.3f} seconds")

        # Done, print status info if needed
        if app.default_debug or app.default_verbose:
            log(f"tasks started:   {app.tasks_started}")
            log(f"tasks finished:  {app.tasks_finished}")
            log(f"tasks failed:    {app.tasks_failed}")
            log(f"tasks skipped:   {app.tasks_skipped}")
            log(f"tasks cancelled: {app.tasks_cancelled}")
            log(f"tasks broken:    {app.tasks_broken}")
            log(f"mtime calls:     {app.mtime_calls}")

        if self.tasks_failed or self.tasks_broken:
            log(f"hancho: {color(255, 128, 128)}BUILD FAILED{color()}")
        elif self.tasks_finished:
            log(f"hancho: {color(128, 255, 128)}BUILD PASSED{color()}")
        else:
            log(f"hancho: {color(128, 128, 255)}BUILD CLEAN{color()}")

        return -1 if self.tasks_failed or self.tasks_broken else 0

####################################################################################################
# Always create an App() object so we can use it for bookkeeping even if we loaded Hancho as a
# module instead of running it directly.

app = App()

def main(args):
    # pylint: disable=line-too-long
    # fmt: off
    parser = argparse.ArgumentParser()
    parser.add_argument("target",            default=None, nargs="?", type=str,   help="A regex that selects the targets to build. Defaults to all targets.")

    parser.add_argument("-f", "--root_file", default="build.hancho",  type=str,   help="The name of the .hancho file(s) to build")
    parser.add_argument("-C", "--root_dir",  default=os.getcwd(),     type=str,   help="Change directory before starting the build")

    parser.add_argument("-v", "--verbose",   default=False, action="store_true",  help="Print verbose build info")
    parser.add_argument("-d", "--debug",     default=False, action="store_true",  help="Print debugging information")
    parser.add_argument("--force",           default=False, action="store_true",  help="Force rebuild of everything")
    parser.add_argument("--trace",           default=False, action="store_true",  help="Trace all text expansion")

    parser.add_argument("-j", "--jobs",      default=os.cpu_count(),  type=int,   help="Run N jobs in parallel (default = cpu_count)")
    parser.add_argument("-q", "--quiet",     default=False, action="store_true",  help="Mute all output")
    parser.add_argument("-n", "--dry_run",   default=False, action="store_true",  help="Do not run commands")
    parser.add_argument("-s", "--shuffle",   default=False, action="store_true",  help="Shuffle task order to shake out dependency issues")
    parser.add_argument("--use_color",       default=False, action="store_true",  help="Use color in the console output")
    # fmt: on

    (flags, unrecognized) = parser.parse_known_args(args)
    flags = flags.__dict__

    # Unrecognized command line parameters also become global config fields if they are
    # flag-like
    extra_flags = {}
    for span in unrecognized:
        if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
            key = match.group(1)
            val = match.group(2)
            val = maybe_as_number(val) if val is not None else True
            extra_flags[key] = val

    return app.main(flags, extra_flags)

####################################################################################################

if __name__ == "__main__":
    exitcode = main(sys.argv[1:])
    sys.exit(exitcode)

