#!/usr/bin/python3

"""
Hancho v0.4.0 @ 2024-11-01 - A simple, pleasant build system.

Hancho is a single-file build system that's designed to be dropped into your project folder - there
is no 'install' step.

Hancho's test suite can be found in 'test.hancho' in the root of the Hancho repo.
"""

# pylint: disable=too-many-lines
# pylint: disable=protected-access
# pylint: disable=unused-argument
# pylint: disable=bad-indentation

#region imports
from os import path
import argparse
import asyncio
import builtins
import copy
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
from collections import abc
#endregion

####################################################################################################
#region Subclass of dict that allows attribute access

class DotDict(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'") from e

    def __setattr__(self, name, value):
        if hasattr(type(self), name):
            raise AttributeError(f"Cannot set attribute '{name}' on dict")
        self[name] = value

    def __delattr__(self, name):
            if hasattr(type(self), name):
                raise AttributeError(f"Cannot delete attribute '{name}' on dict")
            try:
                del self[name]
            except KeyError as e:
                raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'") from e

    def __repr__(self):
        return Dumper(2).dump(self)

    def expand(self, text):
        return expand_variant(self, text)

#endregion

####################################################################################################
#region Logging

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

    def log_line(message):
        app.log += message
        if not app.flags.quiet:
            sys.stdout.write(message)
            sys.stdout.flush()

    if sameline:
        output = output[: os.get_terminal_size().columns - 1]
        output = "\r" + output + "\x1B[K"
        log_line(output)
    else:
        if app.line_dirty:
            log_line("\n")
        log_line(output)

    app.line_dirty = sameline

#endregion

####################################################################################################
#region Path manipulation

def abs_path(raw_path) -> str | list[str]:
    if listlike(raw_path):
        return [abs_path(p) for p in raw_path]
    return path.abspath(raw_path)

def rel_path(path1, path2):

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
    result = [path.join(lhs, rhs) for l in flatten(lhs) for r in flatten(rhs)]
    return result[0] if len(result) == 1 else result


def normalize_path(file_path):
    assert isinstance(file_path, str)

    file_path = path.abspath(path.join(os.getcwd(), file_path))
    file_path = path.normpath(file_path)

    assert path.isabs(file_path)
    return file_path

#endregion

####################################################################################################
#region Helper Methods

def listlike(variant):
    return isinstance(variant, abc.Sequence) and not isinstance(variant, (str, bytes))

def dictlike(variant):
    return isinstance(variant, abc.Mapping)

def flatten(variant):
    if listlike(variant):
        return [x for element in variant for x in flatten(element)]
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
    # if not app.flags.use_color or os.name == "nt":
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
    """Gets the file's mtime and tracks how many times we've called mtime()"""
    app.mtime_calls += 1
    return os.stat(filename).st_mtime_ns

#endregion

####################################################################################################
#region Helpers for managing variants

def merge_dicts(*args, **kwargs):
    """
    >>> merge_dicts(dict(),           dict())
    {}
    >>> merge_dicts(dict(),           dict(bar = None))
    {'bar': None}
    >>> merge_dicts(dict(),           dict(bar = 3))
    {'bar': 3}
    >>> merge_dicts(dict(bar = None), dict())
    {'bar': None}
    >>> merge_dicts(dict(bar = None), dict(bar = None))
    {'bar': None}
    >>> merge_dicts(dict(bar = None), dict(bar = 3))
    {'bar': 3}
    >>> merge_dicts(dict(bar = 2),    dict())
    {'bar': 2}
    >>> merge_dicts(dict(bar = 2),    dict(bar = None))
    {'bar': 2}
    >>> merge_dicts(dict(bar = 2),    dict(bar = 3))
    {'bar': 3}
    """

    result = DotDict()
    args = list(args) + [kwargs]
    for arg in args:
        if arg is None:
            continue
        assert dictlike(arg)
        for key, rval in arg.items():
            lval = result.get(key, None)
            if lval is None or rval is not None:
                result[key] = rval
    return result


def map_variant(key, val, apply):
    val = apply(key, val)
    if dictlike(val):
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
        return await await_variant(variant.config._out_files)

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
        if isinstance(variant, Task):
            result += self.dump_dict(variant.__dict__)
        elif isinstance(variant, HanchoAPI):
            result += self.dump_dict(variant.__dict__)
        elif isinstance(variant, DotDict):
            result += self.dump_dict(variant)
        elif listlike(variant):
            result += self.dump_list(variant)
        elif dictlike(variant):
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
# Also - TEFINAE - Text Expansion Failure Is Not An Error. Dicts can contain macros that are not
# expandable by that dict. This allows nested dicts to contain templates that can only be expanded
# an outer dict, and things will still Just Work.

# The maximum number of recursion levels we will do to expand a macro.
# Tests currently require MAX_EXPAND_DEPTH >= 6
MAX_EXPAND_DEPTH = 20

def log_trace(context, text):
    """Prints a trace message to the log."""
    prefix = hex(id(context)) + ": " + ("┃ " * app.expand_depth)
    log(prefix + text)

def trace_variant(variant):
    """Prints the right-side values of the expansion traces."""
    if callable(variant):
        return f"Callable @ {hex(id(variant))}"
    elif isinstance(variant, dict):
        return f"dict @ {hex(id(variant))}'"
    elif isinstance(variant, Expander):
        return f"Expander @ {hex(id(variant.context3))}'"
    else:
        return f"'{variant}'"

def stringify_variant(variant):
    """Converts any type into an template-compatible string."""
    if variant is None:
        return ""
    elif listlike(variant):
        variant = [stringify_variant(val) for val in variant]
        return " ".join(variant)
    else:
        return str(variant)

# ----------------------------------------

class Expander:
    """
    This class is used to fetch and expand text templates from a dict during text expansion, and
    also to provide utility methods like 'rel' that return relative paths from the task directory.
    It allows for both dictionary-like access (using `expander[key]`) and attribute-like access
    (using `expander.key`), making it versatile for accessing template variables and methods.
    """

#    def __new__(cls, data):
#        if isinstance(data, cls):
#            print("S?SD??SS?FSJK?DFKL?DFJ?SLDKJF")
#            return data
#        return super().__new__(cls)

    def __init__(self, context3):
        assert isinstance(context3, DotDict)
        object.__setattr__(self, "context3", context3)
        # We save a copy of 'trace', otherwise we end up printing traces of reading trace.... :P
        object.__setattr__(self, "trace", context3.get("trace", app.flags.trace))

    def __contains__(self, key):
        return hasattr(Expander, key) or hasattr(Utils, key) or key in self.context3

    def __getitem__(self, key):
        return self.get(key)

    def __getattr__(self, key):
        return self.get(key)

    def __setitem__(self, name, value):
        print("should not be setting things on an expander")
        assert False

    def __setattr__(self, name, value):
        print("should not be setting things on an expander")
        assert False

    def get(self, key, default = None):
        # Check to see if we're fetching an Expander method. Note we getattr(self, key) so that the
        # method is called on the Expander instance, not the class.
        if hasattr(Expander, key):
            val = getattr(self, key)
        # Check to see if we're fetching a special method from the Utils class.
        elif hasattr(Utils, key):
            val = getattr(Utils, key)
        # Neither of those special cases apply, so we fetch the key from the context3 and expand it
        # immediately.
        elif hasattr(self.context3, key):
            val = expand_variant(self.context3, getattr(self.context3, key))
        elif key in self.context3:
            val = expand_variant(self.context3, self.context3[key])
        elif default is not None:
            val = default
        # If the key is not found, raise an AttributeError.
        else:
            if self.trace:
                log_trace(self, f"┃ Read '{key}' failed")
            raise AttributeError(key)

        if self.trace:
            log_trace(self, f"┃ Read '{key}' = {trace_variant(val)}")

        # If we fetched a dict, wrap it in an Expander so we expand its sub-fields.
        if isinstance(val, dict):
            val = Expander(val)
        return val

    # Returns a relative path from the task directory to the sub_path.
    def rel(self, sub_path):
        result = rel_path(sub_path, expand_variant(self.context3, self.context3.task_dir))
        return result

    def __repr__(self):
        result = f"{type(self).__name__} @ {hex(id(self))} wraps "
        result += Dumper(2).dump(self.context3)
        return result

# ----------------------------------------

def eval_macro(context, macro):
    trace = context.get("trace", app.flags.trace)
    if trace:
        log_trace(context, f"┏ eval_macro {macro}")

    #----------

    try:
        result = eval(macro[1:-1], {}, Expander(context))  # pylint: disable=eval-used
        if trace:
            log_trace(context, f"┗ eval_macro {macro} = {result}")
    except BaseException:  # pylint: disable=broad-exception-caught
        # TEFINAE - Text Expansion Failure Is Not An Error, we return the original macro.
        result = macro
        if trace:
            log_trace(context, f"┗ eval_macro {macro} failed")

    return result

# ----------------------------------------

def split_template(text):
    """
    Extract all innermost single-brace-delimited spans from a block of text and produce a list of
    non-delimited and delimited blocks. Escaped braces don't count as delimiters.
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
                result.append((False, text[cursor:lbrace]))
            result.append((True, text[lbrace:rbrace + 1]))
            cursor = rbrace + 1
            lbrace = -1
            rbrace = -1

    if cursor < len(text):
        result.append((False, text[cursor:]))

    return result

# ----------------------------------------

def eval_template(context, template):
    """Replaces all macros in 'template' with their stringified values."""

    trace = context.get("trace", app.flags.trace)
    if trace:
        log_trace(context, f"┏ eval_template '{template}'")

    #----------

    try:
        app.expand_depth += 1
        blocks = split_template(template)
        result = ""
        for block in blocks:
            if block[0] is True:
                value = eval_macro(context, block[1])
                result += stringify_variant(value)
            else:
                result += block[1]
    finally:
        app.expand_depth -= 1


    #----------

    if trace:
        log_trace(context, f"┗ eval_template '{template}' = '{result}'")

    #print(app.expand_depth)

    return result

# ----------------------------------------

def expand_variant(context, variant):
    """Expands single templates and nested lists of templates."""

    recurse = False

    if isinstance(variant, str):
        blocks = split_template(variant)

        if len(blocks) == 0:
            result = variant
        elif len(blocks) > 1:
            template = variant
            result = eval_template(context, template)
            if result != template:
                recurse = True
        elif blocks[0][0] is True:
            macro = blocks[0][1]
            result = eval_macro(context, macro)
            if result != macro:
                recurse = True
        else:
            result = variant
    elif listlike(variant):
        result = [expand_variant(context, val) for val in variant]
    else:
        result = variant

    if recurse:
        app.expand_depth += 1
        if app.expand_depth > MAX_EXPAND_DEPTH:
            raise RecursionError(f"TemplateRecursion: Text expansion of {variant} failed to terminate")

        # If we are recursing, we need to ensure that we expand the result again, in case it
        # contains any macros or templates that need to be expanded.
        result = expand_variant(context, result)
        app.expand_depth -= 1


    return result

# ----------------------------------------

def expand_everything(context, variant):
    pass

#endregion

####################################################################################################

class Utils:
    # fmt: off
    path        = path # path.dirname and path.basename used by makefile-related rules
    re          = re # why is sub() not working?

    color       = staticmethod(color)
    flatten     = staticmethod(flatten)
    glob        = staticmethod(glob.glob)
    join        = staticmethod(join)
    ext         = staticmethod(ext)
    log         = staticmethod(log)
    rel_path    = staticmethod(rel_path)  # used by build_path etc
    run_cmd     = staticmethod(run_cmd)   # FIXME rename to run? cmd?
    stem        = staticmethod(stem)      # FIXME used by metron/tests?

    #expand      = staticmethod(expand_variant)

    hancho_dir  = path.dirname(path.realpath(__file__))
    # fmt: on

####################################################################################################

class Promise:
    def __init__(self, task, *args):
        self.task = task
        self.args = args

    async def get(self):
        await self.task.await_done()
        if len(self.args) == 0:
            return self.task.config._out_files
        elif len(self.args) == 1:
            return self.task.context[self.args[0]]
        else:
            return [self.task.context[field] for field in self.args]

####################################################################################################

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

    default_desc = "{command}"
    default_command = None
    default_task_dir = "{mod_dir}"
    default_build_dir = "{build_root}/{build_tag}/{rel_path(task_dir, repo_dir)}"
    default_build_root = "{repo_dir}/build"
    default_build_tag = ""

    def __init__(self, *args, **kwargs):

        default_context = dict(
            desc = Task.default_desc,
            command = Task.default_command,
        )

        self.context = merge_dicts(default_context, *args, **kwargs)
        self.expanded_context = Expander(self.context)

        self.config = DotDict(
            _desc = None,
            _command = None,
            _in_files = [],
            _out_files = [],
        )

        self._task_index = 0
        self._state = TaskState.DECLARED
        self._reason = None
        self.asyncio_task = None
        self._loaded_files = list(app.loaded_files)
        self._stdout = ""
        self._stderr = ""
        self._returncode = -1

        app.all_tasks.append(self)

    # ----------------------------------------

    # WARNING: Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __repr__(self):
        return Dumper(2).dump(self)

    # ----------------------------------------

    def queue(self):
        if self._state is TaskState.DECLARED:

            # Queue all tasks referenced by this task's context.
            def apply(_, val):
                if isinstance(val, Task):
                    val.queue()
                return val
            map_variant(None, self.context, apply)

            # And now queue this task.
            app.queued_tasks.append(self)
            self._state = TaskState.QUEUED

    def start(self):
        self.queue()
        if self._state is TaskState.QUEUED:
            self.asyncio_task = asyncio.create_task(self.task_main())
            self._state = TaskState.STARTED
            app.tasks_started += 1

    async def await_done(self):
        self.start()
        await self.asyncio_task

    def promise(self, *args):
        return Promise(self, *args)

    # -----------------------------------------------------------------------------------------------

    def print_status(self):
        """Print the "[1/N] Compiling foo.cpp -> foo.o" status line and debug information"""

        # FIXME what if things like 'verbosity' could also be templates?
        # hancho(verbosity = "{True if blah else False}")

        verbosity = self.context.get("verbosity", app.flags.verbosity)
        log(
            f"{color(128,255,196)}[{self._task_index}/{app.tasks_started}]{color()} {self.config._desc}",
            sameline=verbosity == 0,
        )

    # -----------------------------------------------------------------------------------------------

    async def task_main(self):
        """Entry point for async task stuff, handles exceptions generated during task execution."""

        verbosity = self.context.get("verbosity", app.flags.verbosity)
        debug = self.context.get("debug", app.flags.debug)
        rebuild = self.context.get("rebuild", app.flags.rebuild)

        # Await everything awaitable in this task's context.
        # If any of this tasks's dependencies were cancelled, we propagate the cancellation to
        # downstream tasks.
        try:
            assert self._state is TaskState.STARTED
            self._state = TaskState.AWAITING_INPUTS
            for key, val in self.context.items():
                self.context[key] = await await_variant(val)
        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # Exceptions during awaiting inputs means that this task cannot proceed, cancel it.
            self._state = TaskState.CANCELLED
            app.tasks_cancelled += 1
            raise asyncio.CancelledError() from ex

        # Everything awaited, task_init runs synchronously.
        try:
            self._state = TaskState.TASK_INIT
            self.task_init()
        except asyncio.CancelledError as ex:
            # We discovered during init that we don't need to run this task.
            self._state = TaskState.CANCELLED
            app.tasks_cancelled += 1
            raise asyncio.CancelledError() from ex
        except BaseException as ex:  # pylint: disable=broad-exception-caught
            self._state = TaskState.BROKEN
            app.tasks_broken += 1
            raise ex

        # Early-out if this is a no-op task
        if self.config._command is None:
            app.tasks_finished += 1
            self._state = TaskState.FINISHED
            return

        # Check if we need a rebuild
        self._reason = self.needs_rerun(rebuild)
        if not self._reason:
            app.tasks_skipped += 1
            self._state = TaskState.SKIPPED
            return

        try:
            # Wait for enough jobs to free up to run this task.
            job_count = self.context.get("job_count", 1)
            self._state = TaskState.AWAITING_JOBS
            await app.job_pool.acquire_jobs(job_count, self)

            # Run the commands.
            self._state = TaskState.RUNNING_COMMANDS
            app.tasks_running += 1
            self._task_index = app.tasks_running

            self.print_status()
            if verbosity or debug:
                log(f"{color(128,128,128)}Reason: {self._reason}{color()}")

            for command in flatten(self.config._command):
                await self.run_command(command)
                if self._returncode != 0:
                    break

        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # If any command failed, we print the error and propagate it to downstream tasks.
            self._state = TaskState.FAILED
            app.tasks_failed += 1
            raise ex
        finally:
            await app.job_pool.release_jobs(job_count, self)

        # Task finished successfully
        self._state = TaskState.FINISHED
        app.tasks_finished += 1

    # -----------------------------------------------------------------------------------------------

    def task_init(self):
        """All the setup steps needed before we run a task."""

        debug = self.context.get("debug", app.flags.debug)

        if debug:
            log(f"\nTask before expand: {self}")

        # ----------------------------------------
        # Expand task_dir and build_dir

        # pylint: disable=attribute-defined-outside-init

        # FIXME we should be putting these on self.config.task_dir or something so we don't clobber the original

        self.config.task_dir   = abs_path(self.expanded_context.task_dir)
        self.config.build_dir  = abs_path(self.expanded_context.build_dir)

        self.context.task_dir   = abs_path(self.expanded_context.task_dir)
        self.context.build_dir  = abs_path(self.expanded_context.build_dir)

        # Raw tasks may not have a repo_dir.
        repo_dir = self.context.get("repo_dir", None)
        if repo_dir is not None:
            if not self.context.build_dir.startswith(repo_dir):
                raise ValueError(
                    f"Path error, build_dir {self.context.build_dir} is not under repo dir {repo_dir}"
                )

        # ----------------------------------------
        # Expand all in_ and out_ filenames
        # We _must_ expand these first before joining paths or the paths will be incorrect:
        # prefix + swap(abs_path) != abs(prefix + swap(path))

        #for key in expander.keys():
        #    print(f"key {key}")

        for key, val in self.context.items():
            if key.startswith("in_") or key.startswith("out_"):
                def expand_path(_, val):
                    if not isinstance(val, str):
                        return val
                    val = expand_variant(self.context, val)
                    val = path.normpath(val)
                    return val
                #val = self.context[key]
                expanded = map_variant(key, val, expand_path)
                self.config[key] = expanded
                self.context[key] = expanded

        # Make all in_ and out_ file paths absolute

        # FIXME feeling like in_depfile should really be io_depfile...
        # FIXME I dislike all this "move_to" stuff

        for key, val in self.context.items():
            if key.startswith("out_") or key == "in_depfile":
                def move_to_builddir(_, val):
                    if not isinstance(val, str):
                        return val
                    # Note this conditional needs to be first, as build_dir can itself be under
                    # task_dir
                    if val.startswith(self.context.build_dir):
                        # Absolute path under build_dir, do nothing.
                        pass
                    elif val.startswith(self.context.task_dir):
                        # Absolute path under task_dir, move to build_dir
                        val = rel_path(val, self.context.task_dir)
                        val = join_path(self.context.build_dir, val)
                    elif path.isabs(val):
                        raise ValueError(f"Output file has absolute path that is not under task_dir or build_dir : {val}")
                    else:
                        # Relative path, add build_dir
                        val = join_path(self.context.build_dir, val)
                    return val
                moved = map_variant(key, val, move_to_builddir)
                self.config[key] = moved
                self.context[key] = moved
            elif key.startswith("in_"):
                def move_to_taskdir(key, val):
                    if not isinstance(val, str):
                        return val
                    if not path.isabs(val):
                        val = join_path(self.context.task_dir, val)
                    return val
                moved = map_variant(key, val, move_to_taskdir)
                self.config[key] = moved
                self.context[key] = moved

        # Gather all inputs to task.config._in_files and outputs to task.config._out_files

        for key, val in self.context.items():
            # Note - we only add the depfile to _in_files _if_it_exists_, otherwise we will fail a check
            # that all our inputs are present.
            if key == "in_depfile":
                if path.isfile(val):
                    self.config._in_files.append(val)
            elif key.startswith("out_"):
                self.config._out_files.extend(flatten(val))
            elif key.startswith("in_"):
                self.config._in_files.extend(flatten(val))

        # ----------------------------------------
        # And now we can expand the command.

        self.config._desc    = self.expanded_context.desc
        self.config._command = self.expanded_context.command

        if debug:
            log(f"\nTask after expand: {self}")

        # ----------------------------------------
        # Check for task collisions

        # FIXME need a test for this that uses symlinks

        if self.config._out_files and self.config._command is not None:
            for file in self.config._out_files:
                file = path.realpath(file)
                if file in app.filename_to_fingerprint:
                    raise ValueError(f"TaskCollision: Multiple tasks build {file}")
                app.filename_to_fingerprint[file] = self.config._command

        # ----------------------------------------
        # Sanity checks

        # Check for missing input files/paths
        if not path.exists(self.context.task_dir):
            raise FileNotFoundError(self.context.task_dir)

        for file in self.config._in_files:
            if file is None:
                raise ValueError("config._in_files contained a None")
            if not path.exists(file):
                raise FileNotFoundError(file)

        # Check that all build files would end up under build_dir
        for file in self.config._out_files:
            if file is None:
                raise ValueError("config._out_files contained a None")
            if not file.startswith(self.context.build_dir):
                raise ValueError(
                    f"Path error, output file {file} is not under build_dir {self.context.build_dir}"
                )

        # Check for duplicate task outputs
        if self.config._command:
            for file in self.config._out_files:
                #if file in app.all_out_files:
                #    raise NameError(f"Multiple rules build {file}!")
                app.all_out_files.add(file)

        # Make sure our output directories exist
        if not app.flags.dry_run:
            for file in self.config._out_files:
                os.makedirs(path.dirname(file), exist_ok=True)

    # -----------------------------------------------------------------------------------------------

    def needs_rerun(self, rebuild=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""

        debug = self.context.get("debug", app.flags.debug)

        if rebuild:
            return f"Files {self.config._out_files} forced to rebuild"
        if not self.config._in_files:
            return "Always rebuild a target with no inputs"
        if not self.config._out_files:
            return "Always rebuild a target with no outputs"

        # Check if any of our output files are missing.
        for file in self.config._out_files:
            if not path.exists(file):
                return f"Rebuilding because {file} is missing"

        # Check if any of our input files are newer than the output files.
        min_out = min(mtime(f) for f in self.config._out_files)

        if mtime(__file__) >= min_out:
            return "Rebuilding because hancho.py has changed"

        for file in self.config._in_files:
            if mtime(file) >= min_out:
                return f"Rebuilding because {file} has changed"

        for mod_filename in self._loaded_files:
            if mtime(mod_filename) >= min_out:
                return f"Rebuilding because {mod_filename} has changed"

        # Check all dependencies in the C dependencies file, if present.
        if (in_depfile := self.context.get("in_depfile", None)) and path.exists(
            in_depfile
        ):
            depformat = self.context.get("depformat", "gcc")
            if debug:
                log(f"Found C dependencies file {in_depfile}")
            with open(in_depfile, encoding="utf-8") as depfile:
                deplines = None
                if depformat == "msvc":
                    # MSVC /sourceDependencies
                    deplines = json.load(depfile)["Data"]["Includes"]
                elif depformat == "gcc":
                    # GCC -MMD
                    deplines = depfile.read().split()
                    deplines = [d for d in deplines[1:] if d != "\\"]
                else:
                    raise ValueError(f"Invalid dependency file format {depformat}")

                # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
                deplines = [path.join(self.context.task_dir, d) for d in deplines]
                for abs_file in deplines:
                    if mtime(abs_file) >= min_out:
                        return f"Rebuilding because {abs_file} has changed"

        # All checks passed; we don't need to rebuild this output.
        # Empty string = no reason to rebuild
        return ""

    # -----------------------------------------------------------------------------------------------

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        verbosity = self.context.get("verbosity", app.flags.verbosity)
        debug = self.context.get("debug", app.flags.debug)

        if verbosity or debug:
            log(color(128, 128, 255), end="")
            if app.flags.dry_run:
                log("(DRY RUN) ", end="")
            log(f"{rel_path(self.context.task_dir, self.context.repo_dir)}$ ", end="")
            log(color(), end="")
            log(command)

        # Dry runs get early-out'ed before we do anything.
        if app.flags.dry_run:
            return

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            app.pushdir(self.context.task_dir)
            await await_variant(command(self))
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
            cwd=self.context.task_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        (stdout_data, stderr_data) = await proc.communicate()

        if debug:
            log(f"Task {hex(id(self))} subprocess done '{command}'")

        self._stdout = stdout_data.decode()
        self._stderr = stderr_data.decode()
        self._returncode = proc.returncode

        # We need a better way to handle "should fail" so we don't constantly keep rerunning
        # intentionally-failing tests every build
        command_pass = (self._returncode == 0) != self.context.get("should_fail", False)

        if not command_pass:
            message = f"CommandFailure: Command exited with return code {self._returncode}\n"
            if self._stdout:
                message += "Stdout:\n"
                message += self._stdout
            if self._stderr:
                message += "Stderr:\n"
                message += self._stderr
            raise ValueError(message)

        if debug or verbosity:
            log(
                f"{color(128,255,196)}[{self._task_index}/{app.tasks_started}]{color()} Task passed - '{self.config._desc}'"
            )
            if self._stdout:
                log("Stdout:")
                log(self._stdout, end="")
            if self._stderr:
                log("Stderr:")
                log(self._stderr, end="")

####################################################################################################

class HanchoAPI(Utils):

    def __init__(self, context, is_repo):
        self.app = app
        self.context = context
        self.is_repo = is_repo
        self.Task    = Task

    def __repr__(self):
        return Dumper(2).dump(self)

    def __contains__(self, key):
        return key in self.__dict__

    def __call__(self, arg1=None, /, *args, **kwargs):
        if callable(arg1):
            temp_context = merge_dicts(*args, **kwargs)
            # Note that we spread temp_context so that we can take advantage of parameter list
            # checking when we call the callback.
            return arg1(self, **temp_context)
        return Task(self.context, arg1, *args, **kwargs)



    def repo(self, mod_path, *args, **kwargs):

        mod_path = expand_variant(self.context, mod_path)
        mod_path = normalize_path(mod_path)
        mod_path = path.realpath(mod_path)
        #real_path = path.realpath(mod_path)

        dedupe = app.realpath_to_repo.get(mod_path, None)
        if dedupe is not None:
            return dedupe

        new_api = create_repo(mod_path, *args, **kwargs)

        result = new_api._load()
        app.realpath_to_repo[mod_path] = result
        return result



    def load(self, mod_path):
        mod_path = expand_variant(self.context, mod_path)
        mod_path = normalize_path(mod_path)
        new_context = create_mod(self, mod_path)
        return new_context._load()



    def _load(self):
        #if len(app.dirstack) == 1 or app.flags.verbosity or app.flags.debug:
        if True:
            log(("┃ " * (len(app.dirstack) - 1)), end="")
            if self.is_repo:
                log(color(128, 128, 255) + f"Loading repo {self.context.mod_path}" + color())
            else:
                log(color(128, 255, 128) + f"Loading file {self.context.mod_path}" + color())

        app.loaded_files.append(self.context.mod_path)

        # We're using compile() and FunctionType()() here beause exec() doesn't preserve source
        # code for debugging.
        file = open(self.context.mod_path, encoding="utf-8")
        source = file.read()
        code = compile(source, self.context.mod_path, "exec", dont_inherit=True)

        # We must chdir()s into the .hancho file directory before running it so that
        # glob() can resolve files relative to the .hancho file itself. We are _not_ in an async
        # context here so there should be no other threads trying to change cwd.
        app.pushdir(path.dirname(self.context.mod_path))

        # Monkeypatch builtins to replace dict with DotDict to enable dot notation
        new_builtins = types.ModuleType(builtins.__name__)
        new_builtins.__dict__.update(builtins.__dict__)
        new_builtins.__dict__['dict'] = DotDict
        temp_globals = {"hancho": self, "__builtins__": new_builtins}

        # Pylint is just wrong here
        # pylint: disable=not-callable
        types.FunctionType(code, temp_globals)()
        app.popdir()

        # Module loaded, turn the module's globals into a dict that doesn't include __builtins__,
        # hancho, imports, and private fields so we don't have files that end up transitively
        # containing the universe
        new_module = DotDict()
        for key, val in temp_globals.items():
            if key.startswith("_") or key == "hancho" or isinstance(val, type(sys)):
                continue
            new_module[key] = val

        # Tack the context onto the module so people who load it can see the paths it was built with, etc.
        new_module['context'] = temp_globals['hancho'].context

        return new_module

    def expand(self, text):
        return expand_variant(self.context, text)

####################################################################################################

def create_repo(mod_path, *args, **kwargs):
    assert path.isabs(mod_path)
    assert mod_path == normalize_path(mod_path)
    assert mod_path == path.realpath(mod_path)
    assert mod_path not in app.realpath_to_repo

    mod_dir  = path.split(mod_path)[0]
    mod_file = path.split(mod_path)[1]
    mod_name = path.splitext(mod_file)[0]

    mod_context = DotDict(
        repo_name  = path.split(mod_dir)[1],
        repo_dir   = mod_dir,
        repo_path  = mod_path,

        mod_name   = mod_name,
        mod_dir    = mod_dir,
        mod_path   = mod_path,

        # These have to be here so that expand_variant(hancho.context, "{build_dir}") works.
        build_root = Task.default_build_root,
        build_tag  = Task.default_build_tag,
        build_dir  = Task.default_build_dir,

        task_dir   = Task.default_task_dir,
    )

    mod_context = merge_dicts(mod_context, *args, **kwargs)

    mod_api = HanchoAPI(mod_context, True)
    return mod_api

####################################################################################################

def create_mod(parent_api, mod_path, *args, **kwargs):
    assert isinstance(parent_api, HanchoAPI)

    mod_path = normalize_path(expand_variant(parent_api.context, mod_path))
    mod_dir  = path.split(mod_path)[0]
    mod_file = path.split(mod_path)[1]
    mod_name = path.splitext(mod_file)[0]

    mod_api = copy.deepcopy(parent_api)
    mod_api.is_repo = False

    mod_api.context.mod_name = mod_name
    mod_api.context.mod_dir  = mod_dir
    mod_api.context.mod_path = mod_path

    mod_api.context = merge_dicts(mod_api.context, *args, kwargs)

    return mod_api

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

        if count > app.flags.jobs:
            raise ValueError(f"Need {count} jobs, but pool is {app.flags.jobs}.")

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


####################################################################################################

class App:

    def __init__(self):
        self.flags = None
        self.extra_flags = None
        self.target_regex = None

        self.root_context = None
        self.loaded_files = []
        self.dirstack = [os.getcwd()]

        self.all_out_files = set()
        self.filename_to_fingerprint = {}

        self.realpath_to_repo = {}

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
        self.parse_flags([])

    def reset(self):
        self.__init__()  # pylint: disable=unnecessary-dunder-call

    ########################################

    def parse_flags(self, argv):
        assert listlike(argv)

        # pylint: disable=line-too-long
        # fmt: off
        parser = argparse.ArgumentParser()
        parser.add_argument("target",             default=None, nargs="?", type=str,   help="A regex that selects the targets to build. Defaults to all targets.")
        parser.add_argument("-C", "--root_dir",   default=os.getcwd(),     type=str,   help="Change directory before starting the build")
        parser.add_argument("-f", "--root_file",  default="build.hancho",  type=str,   help="The name of the .hancho file(s) to build")
        parser.add_argument("-j", "--jobs",       default=os.cpu_count(),  type=int,   help="Run N jobs in parallel (default = cpu_count)")

        parser.add_argument("-k", "--keep_going", default=1,     type=int,                                        help="Keep going until N jobs fail (0 means infinity)")
        parser.add_argument("-t", "--tool",       default=None,  type=str,                                        help="Run a subtool.")
        parser.add_argument("-v",                 default=0,     action="count",  dest = "verbosity", help="Increase verbosity (-v, -vv, -vvv)")

        parser.add_argument("-d", "--debug",      default=False, action="store_true",  help="Print debugging information")
        parser.add_argument("-n", "--dry_run",    default=False, action="store_true",  help="Do not run commands")
        parser.add_argument("-q", "--quiet",      default=False, action="store_true",  help="Mute all output")
        parser.add_argument("-r", "--rebuild",    default=False, action="store_true",  help="Rebuild everything")
        parser.add_argument("-s", "--shuffle",    default=False, action="store_true",  help="Shuffle task order to shake out dependency issues")
        parser.add_argument("--trace",            default=False, action="store_true",  help="Trace all text expansion")
        parser.add_argument("--use_color",        default=False, action="store_true",  help="Use color in the console output")
        # fmt: on

        (flags, unrecognized) = parser.parse_known_args(argv)

        # Unrecognized command line parameters also become global context fields if they are
        # flag-like
        extra_flags = DotDict()
        for span in unrecognized:
            if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
                key = match.group(1)
                val = match.group(2)

                if val is None:
                    val = True
                else:
                    try:
                        val = int(val)
                    except ValueError:
                        try:
                            val = float(val)
                        except ValueError:
                            pass

                #val = maybe_as_number(val) if val is not None else True
                extra_flags[key] = val

        self.flags = flags
        self.extra_flags = extra_flags

    ########################################

    def create_root_context(self):
        """ Needs to be its own function, used by run_tests.py """

        root_file = self.flags.root_file
        root_dir  = path.abspath(self.flags.root_dir)  # Root path must be absolute.
        root_path = path.normpath(path.join(root_dir, root_file))
        root_path = path.realpath(root_path)
        root_context = create_repo(root_path)

        # All the unrecognized flags get stuck on the root context.
        for key, val in self.extra_flags.items():
            setattr(root_context.context, key, val)

        return root_context

    ########################################

    def main(self):
        app.root_context = self.create_root_context()

        if app.root_context.context.get("debug", None):
            log(f"root_context = {Dumper(2).dump(app.root_context)}")

        if not path.isfile(app.root_context.context.repo_path):
            print(
                f"Could not find Hancho file {app.root_context.context.repo_path}!"
            )
            sys.exit(-1)

        assert path.isabs(app.root_context.context.repo_path)
        assert path.isfile(app.root_context.context.repo_path)
        assert path.isabs(app.root_context.context.repo_dir)
        assert path.isdir(app.root_context.context.repo_dir)

        os.chdir(app.root_context.context.repo_dir)
        time_a = time.perf_counter()
        app.root_context._load()
        time_b = time.perf_counter()

        if app.flags.debug or app.flags.verbosity:
            log(f"Loading .hancho files took {time_b-time_a:.3f} seconds")

        if app.flags.tool:
            print(f"Running tool {app.flags.tool}")
            if app.flags.tool == "clean":
                print("Cleaning build directories")
                build_dirs = set()
                for task in app.all_tasks:
                    build_dir = expand_variant(task.context, "{build_root}")
                    build_dir = normalize_path(build_dir)
                    build_dir = path.realpath(build_dir)
                    if path.isdir(build_dir):
                        build_dirs.add(build_dir)
                for build_dir in build_dirs:
                    print(f"Deleting build root {build_dir}")
                    shutil.rmtree(build_dir, ignore_errors=True)
            return 0

        time_a = time.perf_counter()

        # FIXME selecting targets by regex needs revisiting
        if app.flags.target:
            app.target_regex = re.compile(app.flags.target)
            for task in app.all_tasks:
                queue_task = False
                task_name = None
                # This doesn't work because we haven't expanded output filenames yet
                # for out_file in flatten(task.config._out_files):
                #    if app.target_regex.search(out_file):
                #        queue_task = True
                #        task_name = out_file
                #        break
                if name := task.context.get("name", None):
                    if app.target_regex.search(name):
                        queue_task = True
                        task_name = name
                if queue_task:
                    log(f"Queueing task for '{task_name}'")
                    task.queue()
        else:
            for task in app.all_tasks:
                # If no target was specified, we queue up all tasks that build stuff in the root
                # repo

                # FIXME we are not currently doing that....

                # build_dir = expand_variant(task.context, task.context.build_dir)
                # build_dir = normalize_path(build_dir)
                # repo_dir = expand_variant(app.root_context.context, "{build_dir}")
                # repo_dir = normalize_path(repo_dir)
                # print(build_dir)
                # print(repo_dir)
                # if build_dir.startswith(repo_dir):
                #    task.queue()
                task.queue()

        time_b = time.perf_counter()

        # if app.flags.debug or app.flags.verbosity:
        log(f"Queueing {len(app.queued_tasks)} tasks took {time_b-time_a:.3f} seconds")

        result = self.build()
        return result

    ########################################

    def pushdir(self, new_dir: str):
        new_dir = abs_path(new_dir)
        if not path.exists(new_dir):
            raise FileNotFoundError(new_dir)
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
        result = asyncio.run(self.async_run_tasks())
        loop.close()
        return result

    def build_all(self):
        for task in self.all_tasks:
            task.queue()
        return self.build()

    ########################################

    async def async_run_tasks(self):
        # Run all tasks in the queue until we run out.

        self.job_pool.reset(self.flags.jobs)

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
            try:
                await task.asyncio_task
            except BaseException:  # pylint: disable=broad-exception-caught
                log(color(255, 128, 0), end="")
                log(f"Task failed: {task.config._desc}")
                log(color(), end="")
                log(str(task))
                log(color(255, 128, 128), end="")
                log(traceback.format_exc())
                log(color(), end="")
                fail_count = app.tasks_failed + app.tasks_cancelled + app.tasks_broken
                if app.flags.keep_going and fail_count >= app.flags.keep_going:
                    log("Too many failures, cancelling tasks and stopping build")
                    for task in self.started_tasks:
                        task.asyncio_task.cancel()
                        app.tasks_cancelled += 1
                    break
            self.finished_tasks.append(task)

        time_b = time.perf_counter()

        # if app.flags.debug or app.flags.verbosity:
        log(f"Running {app.tasks_finished} tasks took {time_b-time_a:.3f} seconds")

        # Done, print status info if needed
        if app.flags.debug or app.flags.verbosity:
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
# region Main
# Always create an App() object so we can use it for bookkeeping even if we loaded Hancho as a
# module instead of running it directly.

app = App()

#def test_main():
#    app.flags.trace = True
#
#    foo = DotDict(
#        a = "bee",
#        b = "boo",
#        beeboo = "1234 {foobar}",
#        foobar = "1234 {barbaz}",
#        barbaz = 1234,
#    )
#
#    result = foo.expand("blarp {{a}{b}}")
#    print(result)
#    print(type(result))
#main()

if __name__ == "__main__":
    app.parse_flags(sys.argv[1:])
    sys.exit(app.main())

# endregion
####################################################################################################

#import doctest
#doctest.testmod(verbose=True)
#doctest.testmod()

