#!/usr/bin/python3

"""
Hancho v0.4.0 @ 2024-11-01 - A simple, pleasant build system.

Hancho is a single-file build system that's designed to be dropped into your project folder - there
is no 'install' step.

Hancho's test suite can be found in 'test.hancho' in the root of the Hancho repo.
"""

# FIXME - only do await inside text expansion
# FIXME - better handling of multiple output fields for a task
#         (like something other than .promise(lksjdlksfj))

# pylint: disable=too-many-lines
# pylint: disable=protected-access
# pylint: disable=unused-argument
# pylint: disable=bad-indentation
# pylint: disable=reportAttributeAccessIssue

####################################################################################################
#region imports

from __future__ import annotations
from os import path
import argparse
import asyncio
#import builtins
import copy
import contextlib
#import functools
import glob
import inspect
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
import types
import importlib.util
import sys
from collections import abc
from typing import Callable
from xmlrpc.client import boolean

#endregion
####################################################################################################
#region Config

class Config(dict):
    """
    This class extends 'dict' in a couple ways -
    1. Config supports "foo.bar" attribute access in addition to "foo['bar']"
    2. Config supports "merging" instances by passing them (and any additional key-value pairs) in via the constructor.
    3. When merging Configs, the rightmost not-None value of an attribute will be kept.
    4. If two attributes with the same name are both Configs, we will recursively merge them.
    5. Config behaves like a value type, merging will make copies of all its inputs.
    """

    def __init__(self, *args, **kwargs):
        super().__init__()
        for arg in (*args, kwargs):
            self.merge(arg)

    def merge(self, arg : abc.Mapping | list[abc.Mapping] | None):
        if arg is None:
            return

        if isinstance(arg, list):
            for a in arg:
                self.merge(a)
            return

        if not isinstance(arg, abc.Mapping):
            raise TypeError(f"Argument {arg} is not a mapping")

        for key, rval in arg.items():
            lval = self.get(key, None)

            # Recursively merge mapping-type attributes.
            if isinstance(lval, abc.Mapping) and isinstance(rval, abc.Mapping):
                self[key] = Config(lval, rval)

            # Deep copy all other attributes.
            elif lval is None or rval is not None:
                self[key] = rval

    def to_dict(self):
        return {k: v.to_dict() if isinstance(v, Config) else v for k, v in self.items()}

    def copy(self):
        return Config(self)

    def __copy__(self):
        return self.copy()

    def __deepcopy__(self, memo):
        return Config(copy.deepcopy(dict(self), memo))

    def __setitem__(self, key, value):
        # Upgrade all mappings to Config, make deep copies of everything else.
        if isinstance(value, abc.Mapping) and not isinstance(value, Config):
            value = Config(value)
        else:
            value = copy.deepcopy(value)
        super().__setitem__(key, value)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'") from e

    def __setattr__(self, name, value):
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(f"Cannot set dunder attribute '{name}' on config")
        self[name] = value

    def __delattr__(self, name):
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(f"Cannot delete dunder attribute '{name}' on config")
        try:
            del self[name]
        except KeyError as e:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'") from e

    def __repr__(self):
        return Dumper(2).dump(self)

#endregion
####################################################################################################
#region Logger

class Logger:
    """Simple logger that can do same-line log messages like Ninja."""

    def __init__(self, quiet):
        self.line_dirty = False
        self.buffer = ""
        self.quiet = quiet

    def log_line(self, message):
        self.buffer += message
        if not self.quiet:
            sys.stdout.write(message)
            sys.stdout.flush()

    def __call__(self, message, *, sameline=False, **kwargs):
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
            self.log_line(output)
        else:
            if self.line_dirty:
                self.log_line("\n")
            self.log_line(output)

        self.line_dirty = sameline

#endregion
####################################################################################################
#region Path manipulation

def abs_path(raw_path):
    if raw_path is None:
        return None
    if listlike(raw_path):
        return [abs_path(p) for p in raw_path]
    return path.abspath(raw_path)

def rel_path(path1, path2):
    if path2 is None:
        return path1

    if listlike(path1):
        return [rel_path(p, path2) for p in path1]

    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with simple string manipulation.
    return path1.removeprefix(path2 + "/") if path1 != path2 else "."

def join_path(lhs, rhs, *args):
    if len(args) > 0:
        rhs = join_path(rhs, *args)
    result = [path.join(l, r) for l in flatten(lhs) for r in flatten(rhs)]
    return result[0] if len(result) == 1 else result


def normpath(val):
    if isinstance(val, list):
        result = [normpath(v) for v in val]
    elif val is None:
        result = None
    elif isinstance(val, str):
        result = path.normpath(val)
    else:
        assert False
    return result

def prepend_dir(task_dir, val):
    if isinstance(val, list):
        result = [prepend_dir(task_dir, v) for v in val]
    elif val is None:
        result = None
    elif isinstance(val, str):
        result = join_path(task_dir, val)
    else:
        assert False
    return result

#endregion
####################################################################################################
#region Helper Methods

def listlike(variant):
    return isinstance(variant, abc.Sequence) and not isinstance(variant, (str, bytes))

def flatten(variant):
    if listlike(variant):
        return [x for element in variant for x in flatten(element) if x is not None]
    if variant is None:
        return []
    return [variant]

def join(lhs, rhs, *args):
    if len(args) > 0:
        rhs = join(rhs, *args)
    return [l + r for l in flatten(lhs) for r in flatten(rhs)]

def stem(filename):
    filename = flatten(filename)[0]
    filename = path.basename(filename)
    return path.splitext(filename)[0]

def color(red=None, green=None, blue=None):
    """Converts RGB color to ANSI format string."""
    # Color strings don't work in Windows console, so don't emit them.
    # if not config.use_color or os.name == "nt":
    #    return ""
    if red is None:
        return "\x1B[0m"
    return f"\x1B[38;2;{red};{green};{blue}m"

def run_cmd(cmd):
    """Runs a console command synchronously and returns its stdout with whitespace stripped."""
    return subprocess.check_output(cmd, shell=True, text=True).strip()

def ext(name, new_ext):
    """Replaces file extensions on either a single filename or a list of filenames."""
    if listlike(name):
        return [ext(n, new_ext) for n in name]
    return path.splitext(name)[0] + new_ext

def mtime(filename):
    """Gets the file's mtime in nanoseconds"""
    return os.stat(filename).st_mtime_ns


#endregion
####################################################################################################
#region Helpers for managing variants

def map_variant(key, val, apply):
    val = apply(key, val)
    if isinstance(val, abc.MutableMapping):
        for key2, val2 in val.items():
            val[key2] = map_variant(key2, val2, apply)
    elif listlike(val):
        for key2, val2 in enumerate(val):
            val[key2] = map_variant(key2, val2, apply)
    return val


async def await_variant(variant):
    """Recursively replaces every awaitable in the variant with its awaited value."""

    if listlike(variant):
        for key, val in enumerate(variant):
            variant[key] = await await_variant(val)
        return variant

    if isinstance(variant, Promise):
        return await await_variant(await variant.get())

    if isinstance(variant, Task):
        await variant.await_done()
        return await await_variant(variant.out_files)

    if inspect.isawaitable(variant):
        return await await_variant(await variant)

    return variant

#endregion
####################################################################################################
# region Pretty-printer for various types

class Dumper:
    def __init__(self, max_depth=2):
        self.depth = 0
        self.max_depth = max_depth

    def indent(self):
        return "  " * self.depth

    def dump(self, variant):
        result = f"{type(variant).__name__} @ {hex(id(variant))} "
        if isinstance(variant, (Task, type(sys))):
            result += self.dump_dict(variant.__dict__)
        elif isinstance(variant, HanchoAPI):
            result += self.dump_dict(variant.__dict__)
        elif isinstance(variant, Config):
            result += self.dump_dict(variant)
        elif isinstance(variant, JitExpander):
            result += self.dump_dict(variant.config)
        elif listlike(variant):
            result += self.dump_list(variant)
        elif isinstance(variant, abc.Mapping):
            result = ""
            result += self.dump_dict(variant)
        elif isinstance(variant, str):
            result = ""
            result += '"' + str(variant) + '"'
        else:
            result = ""
            result += str(variant)
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

# endregion
####################################################################################################
# region Hancho's text expansion system.

# Works similarly to Python's F-strings, but with quite a bit more power.
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
# Also - TEFINAE - Text Expansion Failure Is Not An Error. Configs can contain macros that are not
# expandable by that config. This allows nested configs to contain templates that can only be
# expanded by a parent config, and things will still Just Work.

class Expander:

    # The maximum number of recursion levels we will do to expand a macro.
    # Tests currently require MAX_EXPAND_DEPTH >= 6
    MAX_EXPAND_DEPTH = 20

    def __init__(self, do_trace):
        self.do_trace = do_trace
        self.trace = ""
        self.expand_depth = 0

    def id_to_color(self, obj):
        random.seed(id(obj))
        return color(random.randint(64, 255), random.randint(64, 255), random.randint(64, 255))

    def log_trace(self, config, text):
        """Prints a trace message to the log."""
        if self.do_trace:
            prefix = self.id_to_color(config) + hex(id(config)) + color() + ": " + ("┃ " * self.expand_depth)
            self.trace = self.trace + '\n' + prefix + text

    def trace_variant(self, variant):
        """Prints the right-side values of the expansion traces."""
        if callable(variant):
            return f"Callable @ {hex(id(variant))}"
        elif isinstance(variant, Config):
            return f"Config @ {hex(id(variant))}'"
        elif isinstance(variant, JitExpander):
            return f"Expander @ {hex(id(variant.config))}'"
        else:
            return f"'{variant}'"

    def stringify_variant(self, variant):
        """Converts any type into an template-compatible string."""
        if variant is None:
            return ""
        elif listlike(variant):
            variant = [self.stringify_variant(val) for val in variant]
            return " ".join(variant)
        else:
            return str(variant)

    class Macro(str):
        pass

    class Literal(str):
        pass

    def eval_macro(self, config : Config, macro : Macro):
        """
        Evaluates the expression inside a {macro} and returns the result.
        Returns the full macro (with curly braces) unchanged if evaluation fails.
        """
        self.log_trace(config, f"┏ eval_macro {macro}")

        if self.expand_depth >= Expander.MAX_EXPAND_DEPTH:
            self.log_trace(config, f"┗ eval_macro {macro} failed due to recursion depth")
            raise RecursionError(f"eval_macro('{macro}') failed to terminate")

        failed = False
        self.expand_depth += 1

        try:
            result = eval(macro[1:-1], {}, JitExpander(config))  # type: ignore
        except BaseException:  # pylint: disable=broad-exception-caught
            # TEFINAE - Text Expansion Failure Is Not An Error, we return the original macro.
            failed = True
            result = macro

        self.expand_depth -= 1
        if failed:
            self.log_trace(config, f"┗ eval_macro {macro} failed")
        else:
            self.log_trace(config, f"┗ eval_macro {macro} = {result}")

        return result

    # ----------------------------------------
    # FIXME we need full-loop test cases for escaped {}s. Somewhere in the process we need to unescape
    # them and I'm not sure where it goes.

    def split_template(self, text):
        """
        Extracts all innermost single-brace-delimited spans from a block of text and produces a list of
        literals and macros. Escaped braces don't count as delimiters.
        """
        result = []
        cursor = 0
        lbrace = -1
        rbrace = -1
        escaped = False

        for i, c in enumerate(text):
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '{':
                lbrace = i
            elif c == '}' and lbrace >= 0:
                rbrace = i
                if cursor < lbrace:
                    result.append(Expander.Literal(text[cursor:lbrace]))
                result.append(Expander.Macro(text[lbrace:rbrace + 1]))
                cursor = rbrace + 1
                lbrace = -1
                rbrace = -1

        if cursor < len(text):
            result.append(Expander.Literal(text[cursor:]))

        return result

    # ----------------------------------------

    def expand_blocks(self, config, blocks):
        self.log_trace(config, f"┏ expand_blocks {blocks}")
        self.expand_depth += 1

        result = ""
        for block in blocks:
            if isinstance(block, Expander.Macro):
                value = self.eval_macro(config, block)
                result += self.stringify_variant(value)
            else:
                result += block

        self.expand_depth -= 1
        self.log_trace(config, f"┗ expand_blocks {blocks} = '{result}'")
        return result

    # ----------------------------------------

    def expand_variant(self, config, variant):
        """Expands single templates and nested lists of templates. Returns non-templates unchanged."""

        if listlike(variant):
            return [config.expand(val) for val in variant]

        if not isinstance(variant, str):
            return variant

        blocks = self.split_template(variant)
        if len(blocks) == 0 or (len(blocks) == 1 and not isinstance(blocks[0], Expander.Macro)):
            # Empty string or plain string
            return variant

        self.log_trace(config, f"┏ expand_variant '{variant}'")
        self.expand_depth += 1

        if len(blocks) == 1:
            result = self.eval_macro(config, blocks[0])
        else:
            result = self.expand_blocks(config, blocks)

        if result != variant:
            result = self.expand_variant(config, result)

        self.expand_depth -= 1
        self.log_trace(config, f"┗ expand_variant '{variant}' = '{result}'")

        return result

#endregion
####################################################################################################
#region JitExpander

class JitExpander:
    """
    This class is used to fetch and expand text templates from a config and
    to provide utility methods like 'rel' to macro expressions.
    """

    def __init__(self, expander, config : Config):
        self.expander = expander
        self.config = config

    def __contains__(self, key):
        return hasattr(JitExpander, key) or hasattr(Utils, key) or key in self.config

    def __getitem__(self, key):
        return self.get(key)

    def __getattr__(self, key):
        return self.get(key)

    def get(self, key, default = None):
        expander = self.expander

        expander.log_trace(self.config, f"┏ expander.get('{key}')")
        expander.expand_depth += 1

        failed = False

        # Check to see if we're fetching a special method from the Utils class.
        if hasattr(Utils, key):
            val = getattr(Utils, key)
        # Neither of those special cases apply, so we fetch the key from the config and expand it
        # immediately.
        elif hasattr(self.config, key):
            val = self.config.expand(f"{{{key}}}")
        elif default is not None:
            val = default
        else:
            val = None
            failed = True

        expander.expand_depth -= 1
        if failed:
            expander.log_trace(self.config, f"┗ expander.get('{key}') failed")
        else:
            expander.log_trace(self.config, f"┗ expander.get('{key}') = {expander.trace_variant(val)}")

        if failed:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{key}'")

        # If we fetched a sub-config, wrap it in an Expander so we expand its sub-fields.
        if isinstance(val, Config):
            val = JitExpander(self.top, val)

        return val

    def __repr__(self):
        result = f"{self.__class__.__name__} @ {hex(id(self))} wraps "
        result += Dumper(2).dump(self.config)
        return result

#endregion
####################################################################################################
#region Utils
# FIXME we should just merge these into the config the moment we wrap it in an Expander or something.

class Utils:
    # fmt: off
    path        = path # path.dirname and path.basename used by makefile-related tools
    re          = re # why is sub() not working?
    color       = staticmethod(color)
    flatten     = staticmethod(flatten)
    glob        = staticmethod(glob.glob)
    join        = staticmethod(join)
    ext         = staticmethod(ext)
    rel_path    = staticmethod(rel_path)  # used by build_path etc
    run_cmd     = staticmethod(run_cmd)   # FIXME rename to run? cmd?
    stem        = staticmethod(stem)      # FIXME used by metron/tests?
    #expand      = staticmethod(expand_variant)

    # fmt: on

#endregion
####################################################################################################
#region Promise selects subsets of _out_files

class Promise:
    def __init__(self, task, *args):
        self.task = task
        self.args = args

    async def get(self):
        await self.task.await_done()
        if len(self.args) == 0:
            return self.task.out_files
        elif len(self.args) == 1:
            return self.task.config[self.args[0]]
        else:
            return [self.task.config[field] for field in self.args]

#endregion
####################################################################################################
# region Task object + bookkeeping

class TaskState:
    DECLARED = "DECLARED"
    QUEUED = "QUEUED"
    STARTED = "STARTED"
    AWAITING_INPUTS = "AWAITING_INPUTS"
    TASK_INIT = "TASK_INIT"
    AWAITING_JOBS = "AWAITING_JOBS"
    RUNNING_COMMANDS = "RUNNING_COMMANDS"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BROKEN = "BROKEN"

class Task:

    def __init__(self, config : Config, loaded_script_paths: list[str]):
        self.config : Config = config
        self.desc : str | None = None
        self.command : str | list[str] | Callable | None = None
        self.in_files : list[str] = []
        self.out_files : list[str] = []
        self.task_index : int | None = None
        self.state = TaskState.DECLARED
        self.reason : str | None = None
        self.asyncio_task : asyncio.Task | None = None
        self.loaded_script_paths = list(loaded_script_paths)
        self.stdout : str | None = None
        self.stderr : str | None = None
        self.returncode : int | None = None

    # ----------------------------------------

    # WARNING: Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
    def __copy__(self):
        assert False
        return self

    def __deepcopy__(self, memo):
        assert False
        return self

    def __repr__(self):
        return Dumper(2).dump(self)

    # FIXME where was this being called?
    def print_status(self, tasks : Tasks, log : Logger):
        """Print the "[1/N] Compiling foo.cpp -> foo.o" status line and debug information"""

        verbosity = self.config.expand("{verbosity}")
        log(
            f"{color(128,255,196)}[{self.task_index}/{tasks.count_started}]{color()} {self.desc}",
            sameline=verbosity == 0,
        )

#endregion
####################################################################################################
#region async_task_main

async def async_task_main(top: HanchoTop, task : Task, tasks : Tasks, job_pool : JobPool, log : Logger, dirstack : Dirstack):
    """Entry point for async task stuff, handles exceptions generated during task execution."""

    tasks = top.tasks
    job_pool = top.job_pool

    ex = Expander(do_trace = task.config.do_trace)

    verbosity = task.config.expand("{verbosity}")
    debug     = task.config.expand("{debug}")
    rebuild   = task.config.expand("{rebuild}")

    assert isinstance(verbosity, bool)
    assert isinstance(debug, bool)
    assert isinstance(rebuild, bool)

    # Await everything awaitable in this task's config.
    # If any of this tasks's dependencies were cancelled, we propagate the cancellation to
    # downstream tasks.
    try:
        assert task.state is TaskState.STARTED
        task.state = TaskState.AWAITING_INPUTS
        for key, val in task.config.items():
            task.config[key] = await await_variant(val)
    except BaseException as ex:  # pylint: disable=broad-exception-caught
        # Exceptions during awaiting inputs means that this task cannot proceed, cancel it.
        task.state = TaskState.CANCELLED
        tasks.count_cancelled += 1
        raise asyncio.CancelledError() from ex

    # Everything awaited, init_task runs synchronously.
    try:
        task.state = TaskState.TASK_INIT

        # Note that we chdir to task_dir before initializing the task so that any path.abspath
        # or whatever happen from the right place

        task_dir = task.config.expand("{task_dir}")
        assert isinstance(task_dir, str)
        try:
            dirstack.pushdir(task_dir)
            init_task(task, log, top.filename_to_fingerprint)
        finally:
            dirstack.popdir()

    except asyncio.CancelledError as ex:
        # We discovered during init that we don't need to run this task.
        task.state = TaskState.CANCELLED
        tasks.count_cancelled += 1
        raise asyncio.CancelledError() from ex
    except BaseException as ex:  # pylint: disable=broad-exception-caught
        task.state = TaskState.BROKEN
        tasks.count_broken += 1
        raise ex

    # Early-out if this is a no-op task
    if task.command is None:
        tasks.count_finished += 1
        task.state = TaskState.FINISHED
        return

    # Check if we need a rebuild
    task.reason = needs_rerun(task, log)
    if not task.reason:
        tasks.count_skipped += 1
        task.state = TaskState.SKIPPED
        return

    try:
        # Wait for enough jobs to free up to run this task.
        job_count = task.config.get("job_count", 1)
        task.state = TaskState.AWAITING_JOBS
        await job_pool.acquire_jobs(job_count, task)

        # Run the commands.
        task.state = TaskState.RUNNING_COMMANDS
        tasks.count_started += 1
        task.task_index = tasks.count_started

        task.print_status(tasks, log)
        if verbosity or debug:
            log(f"{color(128,128,128)}Reason: {task.reason}{color()}")

        for command in flatten(task.command):
            # async def async_run_command(task : Task, command : str | list[str] | Callable, log : Logger, tasks : Tasks, dirstack : Dirstack):
            await async_run_command(task, command, log, tasks, dirstack)
            if task.returncode != 0:
                break

    except BaseException as ex:  # pylint: disable=broad-exception-caught
        # If any command failed, we print the error and propagate it to downstream tasks.
        task.state = TaskState.FAILED
        tasks.count_failed += 1
        raise ex
    finally:
        await job_pool.release_jobs(job_count, task)

    # Task finished successfully
    task.state = TaskState.FINISHED
    tasks.count_finished += 1

#endregion
####################################################################################################
#region move_to_builddir

def move_to_builddir(file, task_dir, build_dir):
    if isinstance(file, list):
        return [move_to_builddir(f, task_dir, build_dir) for f in file]

    # needed for test_bad_build_path
    file = path.normpath(file)

    # Note this conditional needs to be first, as build_dir can itself be under
    # task_dir
    if file.startswith(build_dir):
        # Absolute path under build_dir.
        pass
    elif file.startswith(task_dir):
        # Absolute path under task_dir, move to build_dir
        file = rel_path(file, task_dir)
    elif path.isabs(file):
        raise ValueError(f"Output file has absolute path that is not under task_dir or build_dir : {file}")

    file = join_path(build_dir, file)
    return file

#endregion
####################################################################################################
#region init_task
# FIXME we need to expand task_dir first, then cd into task_dir, then expand the rest
# FIXME _all_ paths should be rel'd before running command. If you want abs, you can abs() it.

def init_task(task : Task, log : Logger, filename_to_fingerprint : dict):
    """All the setup steps needed before we run a task."""

    config = task.config

    debug = config.get("debug")
    if debug:
        log(f"\nTask before expand: {task}")

    # ----------------------------------------
    # Expand task_dir and build_dir

    repo_dir   = abs_path(config.expand("{repo_dir}"))
    task_dir   = abs_path(join_path(repo_dir, config.expand("{task_dir}")))
    build_dir  = abs_path(join_path(repo_dir, config.expand("{build_dir}")))

    assert isinstance(repo_dir, str)
    assert isinstance(task_dir, str)
    assert isinstance(build_dir, str)

    # Check for missing input files/paths
    if not path.exists(task_dir):
        raise FileNotFoundError(task_dir)

    if not build_dir.startswith(repo_dir):
        raise ValueError(
            f"Path error, build_dir {build_dir} is not under repo dir {repo_dir}"
        )

    config.task_dir   = task_dir
    config.build_dir  = build_dir

    # ----------------------------------------
    # Expand all in_ and out_ filenames
    # We _must_ expand these first before joining paths or the paths will be incorrect:
    # prefix + swap(abs_path) != abs(prefix + swap(path))

    # Make all in_ and out_ file paths absolute

    # FIXME I dislike all this "move_to" stuff

    # Gather all inputs to task.in_files and outputs to task.out_files

    # pylint: disable=consider-using-dict-items
    for key in config.keys():

        if key.startswith("in_"):
            file = config[key]
            file = config.expand(file)
            file = join_path(task_dir, normpath(file))
            task.in_files.extend(flatten(file))
            config[key] = rel_path(file, task_dir)

        if key.startswith("out_"):
            file = config[key]
            file = config.expand(file)
            file = move_to_builddir(file, task_dir, build_dir)
            task.out_files.extend(flatten(file)) # type: ignore
            # FIXME this breaks depfile checking, what dir are we in when we check depfiles?
            config[key] = rel_path(file, task_dir)

        if key == "depfile":
            file = config.expand("{depfile}")
            file = move_to_builddir(file, task_dir, build_dir)
            config[key] = file

    # ----------------------------------------
    # Check for task collisions

    # FIXME need a test for this that uses symlinks
    # FIXME do this check after init?

    for file in task.out_files:
        real_file = path.realpath(file)
        if real_file in filename_to_fingerprint:
            raise ValueError(f"TaskCollision: Multiple tasks build {real_file}")
        filename_to_fingerprint[real_file] = real_file

    # ----------------------------------------
    # Sanity checks

    for file in task.in_files:
        if file is None:
            raise ValueError("_in_files contained a None")
        if not path.exists(file):
            raise FileNotFoundError(file)

    # Check that all build files would end up under build_dir
    for file in task.out_files:
        if file is None:
            raise ValueError("_out_files contained a None")
        if not file.startswith(config.build_dir):
            raise ValueError(
                f"Path error, output file {file} is not under build_dir {config.build_dir}"
            )
        # Make sure our output directories exist
        if not config.dry_run:
            os.makedirs(path.dirname(file), exist_ok=True)

    # ----------------------------------------
    # And now we can expand the command.

    desc    = config.expand("{desc}")
    command = config.expand("{command}")

    if isinstance(desc, str) or desc is None:
        task.desc = desc
    task.command = command

    if debug:
        log(f"\nTask after expand: {task}")


####################################################################################################
#region needs_rerun

def needs_rerun(task : Task, log : Logger):
    """Checks if a task needs to be re-run, and returns a non-empty reason if so."""

    debug   = task.config.expand("{debug}")
    rebuild = task.config.expand("{rebuild}")

    if rebuild:
        return f"Files {task.out_files} forced to rebuild"
    if not task.in_files:
        return "Always rebuild a target with no inputs"
    if not task.out_files:
        return "Always rebuild a target with no outputs"

    # Check if any of our output files are missing.
    for file in task.out_files:
        if not path.exists(file):
            return f"Rebuilding because {file} is missing"

    # Check if any of our input files are newer than the output files.
    min_out = min(mtime(f) for f in task.out_files)

    if mtime(__file__) >= min_out:
        return "Rebuilding because hancho.py has changed"

    for file in task.in_files:
        if mtime(file) >= min_out:
            return f"Rebuilding because {file} has changed"

    for script_path in task.loaded_script_paths:
        if mtime(script_path) >= min_out:
            return f"Rebuilding because {script_path} has changed"

    # Check all dependencies in the C dependencies file, if present.
    if (depfile := task.config.get("depfile", None)) and path.exists(depfile):
        depformat = task.config.get("depformat", "gcc")
        if debug:
            log(f"Found C dependencies file {depfile}")
        with open(depfile, encoding="utf-8") as depfile2:
            deplines = None
            if depformat == "msvc":
                # MSVC /sourceDependencies
                deplines = json.load(depfile2)["Data"]["Includes"]
            elif depformat == "gcc":
                # GCC -MMD
                deplines = depfile2.read().split()
                deplines = [d for d in deplines[1:] if d != "\\"]
            else:
                raise ValueError(f"Invalid dependency file format {depformat}")

            # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
            deplines = [path.join(task.config.task_dir, d) for d in deplines]
            for abs_file in deplines:
                if mtime(abs_file) >= min_out:
                    return f"Rebuilding because {abs_file} has changed"

    # All checks passed; we don't need to rebuild this output.
    # Empty string = no reason to rebuild
    return ""

####################################################################################################
#region Dirstack

class Dirstack:
    def __init__(self):
        self.stack = [os.getcwd()]

    def pushdir(self, new_dir : str):
        new_dir = path.abspath(new_dir)
        if not path.exists(new_dir):
            raise FileNotFoundError(new_dir)
        self.stack.append(new_dir)
        os.chdir(new_dir)

    def popdir(self):
        assert len(self.stack) > 1
        self.stack.pop()
        os.chdir(self.stack[-1])

#endregion
####################################################################################################
#region async_run_command

async def async_run_command(task : Task, command : str | list[str] | Callable, log : Logger, tasks : Tasks, dirstack : Dirstack):
    """Runs a single command, either by calling it or running it in a subprocess."""

    verbosity = task.config.expand("{verbosity}")
    debug     = task.config.expand("{debug}")
    dry_run   = task.config.expand("{dry_run}")

    if verbosity or debug:
        log(color(128, 128, 255), end="")
        if dry_run:
            log("(DRY RUN) ", end="")
        log(f"{rel_path(task.config.task_dir, task.config.repo_dir)}$ ", end="")
        log(color(), end="")
        log(command)

    # Dry runs get early-out'ed before we do anything.
    if dry_run:
        return

    # Custom commands just get called and then early-out'ed.
    if callable(command):
        dirstack.pushdir(task.config.task_dir)
        await await_variant(command(task))
        dirstack.popdir()
        task.returncode = 0
        return

    # Non-string non-callable commands are not valid
    if not isinstance(command, str):
        raise ValueError(f"Don't know what to do with {command}")

    # Create the subprocess via asyncio and then await the result.
    if debug:
        log(f"Task {hex(id(task))} subprocess start '{command}'")

    #proc : asyncio.subprocess.Process = await asyncio.create_subprocess_shell(
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=task.config.task_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    (stdout_data, stderr_data) = await proc.communicate()

    if debug:
        log(f"Task {hex(id(task))} subprocess done '{command}'")

    task.stdout = stdout_data.decode()
    task.stderr = stderr_data.decode()
    task.returncode = proc.returncode

    # We need a better way to handle "should fail" so we don't constantly keep rerunning
    # intentionally-failing tests every build
    command_pass = (task.returncode == 0) != task.config.get("should_fail", False)

    if not command_pass:
        message = f"CommandFailure: Command exited with return code {task.returncode}\n"
        if task.stdout:
            message += "Stdout:\n"
            message += task.stdout
        if task.stderr:
            message += "Stderr:\n"
            message += task.stderr
        raise ValueError(message)

    if debug or verbosity:
        log(
            f"{color(128,255,196)}[{task.task_index}/{tasks.count_started}]{color()} Task passed - '{task.desc}'"
        )
        if task.stdout:
            log("Stdout:")
            log(task.stdout, end="")
        if task.stderr:
            log("Stderr:")
            log(task.stderr, end="")

#endregion
####################################################################################################
#region Hancho API object

class HanchoAPI:
    """This is what gets passed into .hancho files."""

    def __init__(self, config : Config, log : Logger, dirstack : Dirstack, is_repo : boolean):
        self.config = config
        self.log = log
        self.dirstack = dirstack
        self.is_repo = is_repo
        # FIXME where is Config/Tool/Task/etc?
        # FIXME log/dirstack/is_repo don't need to be here, the client doesn't use them

    def __repr__(self):
        return Dumper(2).dump(self)

    def __contains__(self, key : str):
        return key in self.__dict__

    def __call__(self, arg1=None, /, *args, **kwargs):
        if callable(arg1):
            temp_config = Config(*args, **kwargs)
            # Note that we spread temp_config so that we can take advantage of parameter list
            # checking when we call the callback.
            return arg1(self, **temp_config)
        else:
            temp_config = Config(arg1, *args, **kwargs)
            return Task(temp_config)

#endregion
####################################################################################################
#region JobPool

class JobPool:
    def __init__(self, max_jobs : int):
        self.max_jobs = max_jobs
        self.jobs_available = os.cpu_count() or 1
        self.jobs_lock = asyncio.Condition()
        self.job_slots = [None] * self.jobs_available

    def reset(self, job_count):
        self.jobs_available = job_count
        self.job_slots = [None] * self.jobs_available

    ########################################

    async def acquire_jobs(self, count, token):
        """Waits until 'count' jobs are available and then removes them from the job pool."""

        if count > self.max_jobs:
            raise ValueError(f"Need {count} jobs, but pool is {self.max_jobs}.")

        await self.jobs_lock.acquire()
        await self.jobs_lock.wait_for(lambda: self.jobs_available >= count)

        slots_remaining = count
        for i, val in enumerate(self.job_slots):
            if val is None and slots_remaining:
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

#endregion
####################################################################################################
#region Tasks

class Tasks:
    def __init__(self, log : Logger, job_pool : JobPool, flags : Config):
        self.log = log
        self.job_pool = job_pool
        self.flags = flags
        self.all = []
        self.queued = []
        self.started = []
        self.finished = []
        self.count_started : int = 0
        self.count_running : int = 0
        self.count_finished : int = 0
        self.count_failed : int = 0
        self.count_skipped : int = 0
        self.count_cancelled : int = 0
        self.count_broken : int = 0

    def queue_task(self, task : Task):
        if task.state is not TaskState.DECLARED:
            return

        # Queue all tasks referenced by this task's config.
        def apply(_, val):
            if isinstance(val, Task):
                self.queue_task(val)
            return val
        map_variant(None, task.config, apply)

        # And now queue this task.
        self.queued.append(task)
        task.state = TaskState.QUEUED

    async def await_done(self, task : Task):
        self.queue_task(task)
        if task.state is TaskState.QUEUED:
            task.asyncio_task = asyncio.create_task(task.async_task_main())
            task.state = TaskState.STARTED
            self.count_started += 1
        assert task.asyncio_task is not None
        await task.asyncio_task

    async def async_run_tasks(self):
        """ Run all tasks in the queue until we run out. """

        # Tasks can create other tasks, and we don't want to block waiting on a whole batch of
        # tasks to complete before queueing up more. Instead, we just keep queuing up any pending
        # tasks after awaiting each one. Because we're awaiting tasks in the order they were
        # created, this will effectively walk through all tasks in dependency order.

        self.job_pool.reset(self.flags.max_jobs)
        time_a = time.perf_counter()
        log = self.log

        while self.queued or task.started:
            if self.flags.shuffle:
                log(f"Shufflin' {len(self.queued)} tasks")
                random.shuffle(self.queued)

            while task.queued:
                task = self.queued.pop(0)
                task.start()
                self.started.append(task)

            task = self.started.pop(0)
            try:
                await task.asyncio_task
            except BaseException:  # pylint: disable=broad-exception-caught
                log(color(255, 128, 0), end="")
                log(f"Task failed: {task.desc}")
                log(color(), end="")
                log(str(task))
                log(color(255, 128, 128), end="")
                log(traceback.format_exc())
                log(color(), end="")
                fail_count = self.count_failed + self.count_cancelled + self.count_broken
                if self.flags.keep_going and fail_count >= self.flags.keep_going:
                    log("Too many failures, cancelling tasks and stopping build")
                    for task in self.started:
                        task.asyncio_task.cancel()
                        self.count_cancelled += 1
                    break
            self.finished.append(task)

        time_b = time.perf_counter()

        # if top.config.debug or top.config.verbosity:
        self.log(f"Running {self.count_finished} tasks took {time_b-time_a:.3f} seconds")

        # Done, print status info if needed
        if self.flags.debug or self.flags.verbosity:
            self.log(f"tasks started:   {self.count_started}")
            self.log(f"tasks finished:  {self.count_finished}")
            self.log(f"tasks failed:    {self.count_failed}")
            self.log(f"tasks skipped:   {self.count_skipped}")
            self.log(f"tasks cancelled: {self.count_cancelled}")
            self.log(f"tasks broken:    {self.count_broken}")
            #self.log(f"mtime calls:     {self.count_mtime_calls}")

        if self.count_failed or self.count_broken:
            self.log(f"hancho: {color(255, 128, 128)}BUILD FAILED{color()}")
        elif self.count_finished:
            self.log(f"hancho: {color(128, 255, 128)}BUILD PASSED{color()}")
        else:
            self.log(f"hancho: {color(128, 128, 255)}BUILD CLEAN{color()}")

        return -1 if self.count_failed or self.count_broken else 0

#endregion
####################################################################################################
#region HanchoTop

class HanchoTop:
    """
    This class holds global state that is shared across all build scripts and 'import hancho's.
    """

    def __init__(self, top_config : Config, log : Logger, dirstack, tasks : Tasks, job_pool):
        self.reset(top_config, log, dirstack, tasks, job_pool)

    def reset(self, top_config, log, dirstack, tasks, job_pool):
        self.top_config = top_config
        self.log = log
        self.dirstack = dirstack
        self.tasks = tasks
        self.job_pool = job_pool

        self.filename_to_fingerprint = {}
        self.loaded_script_paths = []
        self.realpath_to_repo = {}

#endregion
####################################################################################################
#region exec_script exec_module

def load_script(parent_api : HanchoAPI, script_path : str, log : Logger, realpath_to_repo : dict, dirstack : Dirstack, loaded_script_paths : list[str], is_repo : bool, *args, **kwargs):
    script_path = parent_api.config.expand(script_path) # type: ignore
    assert isinstance(script_path, str)
    script_path = path.abspath(script_path)
    script_path = path.realpath(script_path)

    dedupe = realpath_to_repo.get(script_path, None)
    if dedupe is not None:
        return dedupe

    #script_path = rel_path(self.config.script_path, self.config.root_dir)
    log(("┃ " * (len(dirstack.stack) - 1)), end="")
    if is_repo:
        log(color(128, 128, 255) + f"Loading repo {script_path}" + color())
    else:
        log(color(128, 255, 128) + f"Loading file {script_path}" + color())

    if is_repo:
        new_api = create_repo_api(parent_api, script_path, *args, **kwargs)
    else:
        new_api = create_script_api(parent_api, script_path, *args, **kwargs)

    # We must chdir()s into the .hancho file directory before running it so that
    # glob() can resolve files relative to the .hancho file itself. We are _not_ in an async
    # context here so there should be no other threads trying to change cwd.
    try:
        dirstack.pushdir(path.dirname(script_path))
        result  = exec_script(script_path, new_api.config, log, dirstack, is_repo)
    finally:
        dirstack.popdir()

    realpath_to_repo[script_path] = result
    loaded_script_paths.append(script_path)

    return result

####################################################################################################
#region create_top_config create_repo_config create_script_config

def create_top_config(flags : Config, *args, **kwargs):

    top_config = Config(
        flags,
        *args,

        repo_path   = "{top_path}",
        repo_dir    = "{top_dir}",
        repo_file   = "{top_file}",

        build_root  = "{repo_dir}/build",
        build_dir   = "{build_root}/{build_tag}/{rel_path(task_dir, repo_dir)}",
        build_tag   = "",

        script_path = "{top_path}",
        script_dir  = "{top_dir}",
        script_file = "{top_file}",

        source_dir  = "{script_dir}",
        task_dir    = "{source_dir}",

        **kwargs
    )

    return top_config

########################################

def create_repo_config(repo_script_path : str, flags : Config, realpath_to_repo : dict, *args, **kwargs):
    assert path.isabs(repo_script_path)
    assert repo_script_path == path.abspath(repo_script_path)
    assert repo_script_path == path.realpath(repo_script_path)
    assert repo_script_path not in realpath_to_repo

    # Repo config does _not_ inherit its parent config, only the flags.

    repo_config = Config(
        flags,
        Config(
            repo_path   = repo_script_path,
            repo_dir    = path.split(repo_script_path)[0],
            repo_file   = path.split(repo_script_path)[1],

            build_root  = "{repo_dir}/build",
            build_dir   = "{build_root}/{build_tag}/{rel_path(task_dir, repo_dir)}",
            build_tag   = "",

            script_path = "{repo_path}",
            script_file = "{repo_file}",
            script_dir  = "{repo_dir}",

            source_dir  = "{script_dir}",
            task_dir    = "{source_dir}"
        ),
        *args,
        kwargs
    )

    return repo_config

########################################

def create_script_config(parent_config : Config, script_path : str, *args, **kwargs):
    script_path = parent_config.expand(script_path) #type:ignore
    assert isinstance(script_path, str)
    script_path = path.abspath(script_path)
    script_dir  = path.split(script_path)[0]
    script_file = path.split(script_path)[1]
    script_name = path.splitext(script_file)[0]

    script_config = copy.deepcopy(parent_config)

    script_config.script_name = script_name
    script_config.script_dir  = script_dir
    script_config.script_path = script_path
    script_config = Config(script_config, *args, kwargs)

    return script_config

#endregion
####################################################################################################

def create_repo_api(parent_api : HanchoAPI, script_path: str, flags : Config, realpath_to_repo : dict, *args, **kwargs):
    #def create_repo_config(parent_api : HanchoAPI, flags : Config, repo_script_path : str, realpath_to_repo : dict, *args, **kwargs):

    repo_config = create_repo_config(script_path, flags, realpath_to_repo, *args, **kwargs)
    repo_api = HanchoAPI(repo_config, parent_api.log, parent_api.dirstack, True)
    return repo_api

def create_script_api(parent_api : HanchoAPI, script_path : str, *args, **kwargs):
    script_config = create_script_config(parent_api.config, script_path, *args, **kwargs)
    script_api = HanchoAPI(script_config, parent_api.log, parent_api.dirstack, False)
    return script_api

####################################################################################################
# FIXME haven't tried this yet, this is supposed to be Python-er than exec_script

def exec_script(script_path: str, config : Config, log : Logger, dirstack : Dirstack, is_repo):
    script_path = path.abspath(script_path)
    script_dir  = path.split(script_path)[0]
    script_file = path.split(script_path)[1]
    script_name = path.splitext(script_file)[0]

    spec = importlib.util.spec_from_file_location(script_name, script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create module spec for {script_path}")

    user_module = importlib.util.module_from_spec(spec)
    user_module.__dict__['hancho'] = HanchoAPI(config, log, dirstack, is_repo)
    try:
        # We must chdir()s into the .hancho file directory before running it so that glob() can
        # resolve files relative to the .hancho file itself.
        # We are _not_ in an async context here so there should be no other threads trying to
        # change cwd.
        dirstack.pushdir(script_dir)
        spec.loader.exec_module(user_module)
    finally:
        dirstack.popdir()
    return user_module


#endregion
####################################################################################################
#region parse_flags

def parse_flags(argv):
    # pylint: disable=line-too-long
    # fmt: off
    parser = argparse.ArgumentParser()
    parser.add_argument("target",             default=None, nargs="?", type=str,   help="A regex that selects the targets to build. Defaults to all targets.")
    parser.add_argument("-C", "--root_dir",   default=os.getcwd(),     type=str,   help="Change directory before starting the build")
    parser.add_argument("-f", "--top_path",   default="build.hancho",  type=str,   help="The name of the .hancho file(s) to build")
    parser.add_argument("-j", "--jobs",       default=os.cpu_count(),  type=int,   dest="max_jobs", help="Run N jobs in parallel (default = cpu_count)")

    parser.add_argument("-k", "--keep_going", default=1,     type=int,             help="Keep going until N jobs fail (0 means infinity)")
    parser.add_argument("-t", "--tool",       default=None,  type=str,             help="Run a subtool.")
    parser.add_argument("-v",                 default=0,     action="count",  dest = "verbosity", help="Increase verbosity (-v, -vv, -vvv)")

    parser.add_argument("-d", "--debug",      default=False, action="store_true",  help="Print debugging information")
    parser.add_argument("-n", "--dry_run",    default=False, action="store_true",  help="Do not run commands")
    parser.add_argument("-q", "--quiet",      default=False, action="store_true",  help="Mute all output")
    parser.add_argument("-r", "--rebuild",    default=False, action="store_true",  help="Rebuild everything")
    parser.add_argument("-s", "--shuffle",    default=False, action="store_true",  help="Shuffle task order to shake out dependency issues")
    parser.add_argument("--trace",            default=False, action="store_true",  help="Trace all text expansion")
    parser.add_argument("--use_color",        default=False, action="store_true",  help="Use color in the console output")
    # fmt: on

    flags = argparse.Namespace()
    (flags, unrecognized) = parser.parse_known_args(argv)
    flags = Config(vars(flags))

    # Unrecognized command line parameters also become module config fields if they are
    # flag-like
    for span in unrecognized:
        if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
            key = match.group(1)
            val = match.group(2)
            if val is None:
                val = True
            else:
                for converter in (int, float):
                    try:
                        val = converter(val)
                        break
                    except ValueError:
                        pass
            flags[key] = val

    flags.hancho_dir  = path.dirname(path.realpath(__file__)),
    flags.root_dir    = path.abspath(flags.root_dir)
    flags.top_path    = path.abspath(flags.top_path)
    flags.top_dir     = path.split(flags.top_path)[0],
    flags.top_file    = path.split(flags.top_path)[1],

    return flags

#endregion
####################################################################################################
#region Main

def main():
    flags = parse_flags(sys.argv[1:])
    top_config = create_top_config(flags)

    job_pool = JobPool(flags.max_jobs)
    log = Logger(flags.quiet)
    dirstack = Dirstack()

    tasks = Tasks(log, job_pool, flags)

    top = HanchoTop(flags, log, dirstack, tasks)

    all_tasks    = tasks.all
    queued_tasks = tasks.queued

    #----------

    if not path.isfile(top_config.root_path):
        print(f"Could not find build script {top_config.root_path}!")
        sys.exit(-1)

    assert path.isabs (top_config.root_path)
    assert path.isfile(top_config.root_path)
    assert path.isabs (top_config.root_dir)
    assert path.isdir (top_config.root_dir)

    expanded_repo_dir : str = top_config.expand(top_config.repo_dir) #type:ignore
    os.chdir(expanded_repo_dir)
    time_a = time.perf_counter()
    exec_script(top_config.root_path, top_config, log, dirstack, is_repo = True)
    time_b = time.perf_counter()

    if top_config.debug or top_config.verbosity:
        log(f"Loading .hancho files took {time_b-time_a:.3f} seconds")

    if top_config.tool:
        print(f"Running tool {top_config.tool}")
        if top_config.tool == "clean":
            print("Deleting build directories")
            build_roots = set()
            for task in all_tasks:
                build_root = task.config.get_expanded("{build_root}")
                assert isinstance(build_root, str)
                build_root = path.abspath(build_root)
                build_root = path.realpath(build_root)
                if path.isdir(build_root):
                    build_roots.add(build_root)
            for root in build_roots:
                print(f"Deleting build root {root}")
                shutil.rmtree(root, ignore_errors=True)
        return 0

    time_a = time.perf_counter()

    # FIXME selecting targets by regex needs revisiting
    """
    if top_config.target:
        target_regex = re.compile(top_config.target)
        for task in all_tasks:
            q_ueue_task = False
            task_name = None
            # This doesn't work because we haven't expanded output filenames yet
            # for out_file in flatten(task.out_files):
            #    if top.target_regex.search(out_file):
            #        q_ueue_task = True
            #        task_name = out_file
            #        break
            if name := task.config.get_expanded("name", None):
            if target_regex.search(name):
                    q_ueue_task = True
                    task_name = name
            if q_ueue_task:
                log(f"Queueing task for '{task_name}'")
                task.queue()
    else:
    for task in all_tasks:
            # If no target was specified, we queue up all tasks that build stuff in the root
            # repo

            # FIXME we are not currently doing that....

            # build_dir = task.config.get_expanded("{build_dir}")
            # build_dir = path.abspath(build_dir)
            # repo_dir = root_mod.config.get_expanded("{repo_dir}")
            # repo_dir = path.abspath(repo_dir)
            # print(build_dir)
            # print(repo_dir)
            # if build_dir.startswith(repo_dir):
            #    task.queue()
            task.queue()
    """

    for task in all_tasks:
        top.tasks.queue_task(task)

    time_b = time.perf_counter()

    # if top_config.debug or top_config.verbosity:
    log(f"Queueing {len(queued_tasks)} tasks took {time_b-time_a} seconds")

    result = -1
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = asyncio.run(top.tasks.async_run_tasks())
    loop.close()

    return result

####################################################################################################
#region Entrypoint

if __name__ == "__main__":
    sys.exit(main())

# endregion
####################################################################################################

#import doctest
#doctest.testmod(verbose=True)
#doctest.testmod()

