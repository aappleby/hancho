#!/usr/bin/python3

"""Hancho v0.1.0 @ 2024-03-25 - A simple, pleasant build system."""

# root_path    = Path Hancho was started in, or the one specified by -C
# repo_path    = Path Hancho was started in, or the path passed to the most recent hancho.repo(...)
# base_path    = os.getcwd() when the task was created

# build_path   = "{root_path}/{build_dir}/{build_tag}/{repo_name}/{rel_path(base_path, repo_path)}",
# command_path = "{base_path}",
# in_path      = "{base_path}",
# out_path     = "{build_path}",

from os import path
from types import MappingProxyType
import argparse
import asyncio
import builtins
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
    if not Config.quiet:
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

#---------------------------------------------------------------------------------------------------
# Path manipulation

def unwrap_path(variant):
    if isinstance(variant, (Task, Expander)):
        variant = variant._out_files
    return variant

def abs_path(raw_path, strict=False):
    raw_path = unwrap_path(raw_path)

    if isinstance(raw_path, list):
        return [abs_path(p, strict) for p in raw_path]

    result = path.abspath(raw_path)
    if strict and not path.exists(result):
        raise FileNotFoundError(raw_path)
    return result

def rel_path(path1, path2):
    path1 = unwrap_path(path1)
    path2 = unwrap_path(path2)

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
    path1 = unwrap_path(path1)
    path2 = unwrap_path(path2)

    if len(args):
        return [join_path(path1, p) for p in join_path(path2, *args)]
    if isinstance(path1, list):
        return [join_path(p, path2) for p in flatten(path1)]
    if isinstance(path2, list):
        return [join_path(path1, p) for p in flatten(path2)]

    if not path2:
        raise ValueError(f"Cannot join '{path1}' with '{type(path2)}' == '{path2}'")
    return path.join(path1, path2)

#---------------------------------------------------------------------------------------------------
# Helper methods

def flatten(variant):
    if isinstance(variant, list):
        return [x for element in variant for x in flatten(element)]
    return [variant]

def join_prefix(prefix, strings):
    return [prefix+str(s) for s in flatten(strings)]

def join_suffix(strings, suffix):
    return [str(s)+suffix for s in flatten(strings)]

def color(red=None, green=None, blue=None):
    """Converts RGB color to ANSI format string."""
    # Color strings don't work in Windows console, so don't emit them.
    if not Config.use_color or os.name == "nt":
        return ""
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

class Dumper:
    def __init__(self, max_depth = 2):
        self.tabs = 0
        self.depth = 0
        self.max_depth = max_depth

    def indent(self):
        return "  " * self.tabs

    def dump(self, variant):
        result = ""
        match variant:
            case Task():
                result = f"{type(variant).__name__} @ {hex(id(variant))} "
                if self.depth >= self.max_depth:
                    result += f"{{name = '{variant.name}', ...}}"
                else:
                    result += self.dump(variant.__dict__)
            case Config():
                result = f"{type(variant).__name__} @ {hex(id(variant))} "
                if self.depth >= self.max_depth:
                    result += f"{{name = '{variant.name}', ...}}"
                else:
                    result += self.dump(variant.__dict__)
            case list():
                result = self.dump_list(variant)
            case dict() | MappingProxyType():
                result = self.dump_dict(variant)
            case str():
                result = '"' + str(variant) + '"'
            case _:
                result = str(variant)
        return result

    def dump_list(self, l):
        result = "["
        #self.depth += 1
        self.tabs += 1
        for val in l:
            if len(l) > 0:
                result += "\n" + self.indent()
            result += self.dump(val)
            result += ", "
        #self.depth -= 1
        self.tabs -= 1
        if len(l) > 0:
            result += "\n" + self.indent()
        result += "]"
        return result

    def dump_dict(self, d):
        result = "{\n"
        self.depth += 1
        self.tabs += 1
        for key, val in d.items():
            result += self.indent() + f"{key} = {self.dump(val)},\n"
        self.tabs -= 1
        self.depth -= 1
        result += self.indent() + "}"
        return result

####################################################################################################

class Config:
    """A Config object is just a 'bag of fields'."""

    def __init__(self, *args, **kwargs):
        self.merge(args, kwargs)

    #----------------------------------------

    def items(self):
        return self.__dict__.items()

    def __getitem__(self, key):
        return self.__dict__[key]

    def __setitem__(self, key, val):
        self.__dict__[key] = val

    # required to use Config as a mapping
    def keys(self):
        return self.__dict__.keys()

    def pop(self, field):
        return self.__dict__.pop(field)

    def merge(self, *args, **kwargs):
        for arg in args:
            if isinstance(arg, (tuple, list)):
                for item in arg:
                    self.merge(item)
            elif arg is not None:
                self.__dict__.update(arg)
        self.__dict__.update(kwargs)
        return self

    #----------------------------------------

    def __repr__(self):
        return Dumper(1).dump(self)

    def expand(self, variant):
        return expand_variant(Expander(self), variant)

    def extend(self, *args, **kwargs):
        result = type(self)(self)
        result.merge(args, kwargs)
        return result

    def rel(self, path):
        return rel_path(path, expand_variant(self, self.command_path))

    def stem(self, p):
        if isinstance(p, Task):
            p = p._out_files[0]
        return path.splitext(path.basename(p))[0]

####################################################################################################

class Command(Config):
    # FIXME - are we still using this func_or_config stuff?

    def __init__(self, func_or_config = None, *args, **kwargs):
        if callable(func_or_config):
            super().__init__(args, kwargs, call = func_or_config)
        else:
            super().__init__(func_or_config, args, kwargs)

    def __call__(self, *args, **kwargs):
        merged = Config(self, args, kwargs)
        if custom_call := self.__dict__.get("call", None):
            merged.pop("call")
            return custom_call(**merged)
        else:
            return Task(merged)

####################################################################################################
# All static methods and fields are available to use in any macro.

# fmt: off

Config.root_path = os.getcwd()
Config.root_file = "build.hancho"
Config.repo_path = os.getcwd()
Config.repo_name = ""
Config.base_path = os.getcwd()

Config.desc          = "{rel(_in_files)} -> {rel(_out_files)}"

Config.command       = None
Config.command_path  = "{base_path}"

Config.build_dir     = "build"
Config.build_path    = "{root_path}/{build_dir}/{build_tag}/{repo_name}/{rel_path(base_path, repo_path)}"

Config.in_path       = "{base_path}"
Config.out_path      = "{build_path}"

Config.jobs      = os.cpu_count()
Config.name      = ""
Config.build_tag = ""
Config.tags      = []
Config.verbose   = False
Config.quiet     = False
Config.dry_run   = False
Config.debug     = False
Config.force     = False
Config.shuffle   = False
Config.trace     = False
Config.use_color = True

Config.should_fail = False
Config.save_log    = False

Config.abs_path    = staticmethod(abs_path)
Config.rel_path    = staticmethod(rel_path)
Config.join_path   = staticmethod(join_path)
Config.color       = staticmethod(color)
Config.glob        = staticmethod(glob.glob)
Config.len         = staticmethod(len)
Config.run_cmd     = staticmethod(run_cmd)
Config.swap_ext    = staticmethod(swap_ext)
Config.flatten     = staticmethod(flatten)
Config.print       = staticmethod(print)
Config.log         = staticmethod(log)
Config.path        = path
Config.re          = re
Config.join_prefix = staticmethod(join_prefix)
Config.join_suffix = staticmethod(join_suffix)

# fmt: on

####################################################################################################

def load_file(file_name, as_repo, args, kwargs):
    mod_config = Config(*args, **kwargs)

    file_name = mod_config.expand(file_name)
    abs_file_path = join_path(os.getcwd(), file_name)

    repo_path = path.dirname(abs_file_path) if as_repo else Config.repo_path
    repo_name = path.basename(repo_path) if as_repo else Config.repo_name
    file_path = path.dirname(abs_file_path)
    file_name = path.basename(abs_file_path)

    return app.load_module(repo_path, repo_name, file_path, file_name, mod_config)

def load(file_name, *args, **kwargs):
    return load_file(file_name, as_repo=False, args=args, kwargs=kwargs)

def repo(file_name, *args, **kwargs):
    return load_file(file_name, as_repo=True, args=args, kwargs=kwargs)

def reset():
    return app.reset()

def build():
    return app.build()

def build_all():
    result = app.build_all()
    return result

def get_log():
    return app.log














































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

#----------------------------------------

class Expander:
    """ Wraps a Config object and expands all fields read from it. """

    def __init__(self, config):
        self.config = config
        # We save a copy of 'trace', otherwise we end up printing traces of reading trace.... :P
        self.trace = config.trace

    def __getitem__(self, key):
        return self.get(key)

    def __getattr__(self, key):
        return self.get(key)

    def get(self, key):
        try:
            val = getattr(self.config, key)
        except Exception as err:
            if self.trace:
                log(trace_prefix(self) + f"Read '{key}' failed")
            raise err

        if self.trace:
            log(trace_prefix(self) + f"Read '{key}' = {trace_variant(val)}")
        val = expand_variant(self, val)
        return val

#----------------------------------------

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

#----------------------------------------

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

#----------------------------------------

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

























































####################################################################################################

def get_awaitables(variant, result):
    if inspect.isawaitable(variant):
        result.append(variant)
        return

    match variant:
        case Promise():
            get_awaitables(variant.task, result)
        case Task():
            result.append(variant.await_done())
        case Config() | dict():
            for key, val in variant.items():
                get_awaitables(val, result)
        case list():
            for key, val in enumerate(variant):
                get_awaitables(val, result)

async def await_variant(variant):
    """Recursively replaces every awaitable in the variant with its awaited value."""

    while inspect.isawaitable(variant):
        variant = await variant

    match variant:
        case Exception() | asyncio.CancelledError():
            raise variant
        case Promise():
            variant = await variant.get()
            variant = await await_variant(variant)
        case Task():
            variant = await variant.await_done()
            variant = await await_variant(variant)
        case Config() | dict():
            for key, val in variant.items():
                variant[key] = await await_variant(val)
        case list():
            for key, val in enumerate(variant):
                variant[key] = await await_variant(val)
    return variant

####################################################################################################

def visit_variant(key, val, visitor):
    match val:
        case Exception():
            raise val
        case Task():
            for key2, val2 in enumerate(val._out_files):
                val._out_files[key2] = visit_variant(key2, val2, visitor)
        case Config() | dict():
            for key2, val2 in val.items():
                val[key2] = visit_variant(key2, val2, visitor)
        case list():
            for key2, val2 in enumerate(val):
                val[key2] = visit_variant(key2, val2, visitor)
        case _:
            val = visitor(key, val)
    return val

####################################################################################################

class Promise:
    def __init__(self, task, *args):
        self.task = task
        self.args = args

    async def get(self):
        await self.task.await_done()
        if len(self.args) == 0:
            return self.task._out_files
        elif len(self.args) == 1:
            return self.task.__dict__[self.args[0]]
        else:
            return [self.task.__dict__[field] for field in args]

####################################################################################################

class TaskState(IntEnum):
    DECLARED = 0
    QUEUED = 1
    STARTED = 2
    AWAITING_INPUTS = 3
    AWAITING_JOBS = 4
    RUNNING_COMMANDS = 5
    FINISHED = 6

####################################################################################################


class Task(Config):
    """Calling a Command creates a Task."""

    def __init__(self, *args, **kwargs):

        self.repo_path = Config.repo_path
        self.repo_name = Config.repo_name
        self.base_path = os.getcwd()
        self.merge(args)
        self.merge(kwargs)

        # Note - We can't set _promise = asyncio.create_task() here, as we're not guaranteed to be
        # in an event loop yet
        self._state = TaskState.DECLARED
        self._reason = None
        self._promise = None
        self._loaded_modules = [m.__file__ for m in app.loaded_modules]

        if not self.command is None:
            app.all_tasks.append(self)
            self._task_index = len(app.all_tasks)

    def queue(self):
        if self._state is TaskState.DECLARED:
            app.queued_tasks.append(self)
            self._state = TaskState.QUEUED

    def start(self):
        if self._state is TaskState.QUEUED or self._state is TaskState.DECLARED:
            self._promise = asyncio.create_task(self.task_main())
            self._state = TaskState.STARTED
            if not self.command is None:
                app.tasks_started += 1

    async def await_done(self):
        self.start()
        return await self._promise

    def promise(self, *args):
        return Promise(self, *args)

    def __repr__(self):
        return Dumper(1).dump(self)

    #-----------------------------------------------------------------------------------------------

    def print_status(self):
        # Print the "[1/N] Compiling foo.cpp -> foo.o" status line and debug information
        log(
            #f"{color(128,255,196)}[{app.tasks_running}/{app.tasks_started}]{color()} {self.desc}",
            #{self._task_index}/
            f"{color(128,255,196)}[{app.tasks_running}/{app.tasks_started}]{color()} {self.desc}",
            sameline=not self.verbose,
        )

    #-----------------------------------------------------------------------------------------------

    async def task_main(self):
        """Entry point for async task stuff, handles exceptions generated during task execution."""

        try:
            # Await everything awaitable in this task except the task's own promise.

            assert self._state is TaskState.STARTED
            awaitables = []
            for key, val in self.__dict__.items():
                if key != "_promise":
                    get_awaitables(val, awaitables)

            self._state = TaskState.AWAITING_INPUTS
            await asyncio.gather(*awaitables)

            for key, val in self.__dict__.items():
                if key != "_promise":
                    self.__dict__[key] = await await_variant(val)

            if self.debug:
                log(f"Task {hex(id(self))} start")

            # Everything awaited, task_init runs synchronously.
            self.task_init()

            # Early-out if this is a no-op task
            if self.command is None:
                #app.tasks_running += 1
                #app.tasks_pass += 1
                self._state = TaskState.FINISHED
                return

            # Check if we need a rebuild
            self._reason = self.needs_rerun(self.force)

            # Run the commands if we need to.
            if not self._reason:
                app.task_skipped += 1
            else:
                # Wait for enough jobs to free up to run this task.
                job_count = self.__dict__.get("job_count", 1)
                self._state = TaskState.AWAITING_JOBS
                await app.acquire_jobs(job_count, self.desc)

                self._state = TaskState.RUNNING_COMMANDS
                # Print the "[1/N] Compiling foo.cpp -> foo.o" status line and debug information

                app.tasks_running += 1
                self.print_status()

                if self.verbose or self.debug:
                    log(f"{color(128,128,128)}Reason: {self._reason}{color()}")

                #line_block(app.job_slots)
                #for line in app.job_slots:
                #    print(line)

                commands = flatten(self.command)

                try:
                    for command in commands:
                        if self.verbose or self.debug:
                            log(color(128,128,255), end="")
                            if self.dry_run: log("(DRY RUN) ", end="")
                            log(f"{rel_path(self.command_path, Config.root_path)}$ ", end="")
                            log(color(), end="")
                            log(command)
                        if not self.dry_run:
                            await self.run_command(command)
                finally:
                    await app.release_jobs(job_count, self.desc)
                app.tasks_pass += 1

        # If any of this tasks's dependencies were cancelled, we propagate the cancellation to
        # downstream tasks.
        except asyncio.CancelledError as cancel:
            app.tasks_cancel += 1
            return cancel

        # If this task failed, we print the error and propagate a cancellation to downstream tasks.
        except Exception as err:
            log(f"{color(255, 128, 128)}{traceback.format_exc()}{color()}")
            app.tasks_fail += 1
            return asyncio.CancelledError()

        finally:
            self._state = TaskState.FINISHED
            if self.debug:
                log(f"Task {hex(id(self))} done")

        return self._out_files

    #-----------------------------------------------------------------------------------------------

    def task_init(self):
        """All the setup steps needed before we run a task."""

        if self.debug:
            log(f"Task before expand: {self}")

        # Expand the in and out paths first
        self.command_path = abs_path(join_path(self.base_path, self.expand(self.command_path)))
        self.in_path      = abs_path(join_path(self.base_path, self.expand(self.in_path)))
        self.out_path     = abs_path(join_path(self.base_path, self.expand(self.out_path)))

        # We _must_ expand first before prepending paths or paths will break
        # prefix + swap(abs_path) != abs(prefix + swap(path))
        for key, val in self.__dict__.items():
            if key.startswith("in_") or key.startswith("out_") or key == "depfile":
                self.__dict__[key] = self.expand(val)

        # Prepend the in/out path to the filenames
        self._in_files = []
        self._out_files = []

        def handle_in_path(key, val):
            if val is None:
                raise ValueError(f"Key {key} was None")
            if isinstance(val, str):
                val = abs_path(join_path(self.in_path, val))
                self._in_files.append(val)
            return val

        def handle_out_path(key, val):
            if val is None:
                raise ValueError(f"Key {key} was None")
            if isinstance(val, str):
                val = abs_path(join_path(self.out_path, val))
                self._out_files.append(val)
            return val

        for key, val in self.__dict__.items():
            if key.startswith("in_") and key != "in_path":
                self.__dict__[key] = visit_variant(key, val, handle_in_path)
            if key.startswith("out_") and key != "out_path":
                self.__dict__[key] = visit_variant(key, val, handle_out_path)

        if "depfile" in self.__dict__:
            self.depfile = join_path(self.out_path, self.depfile)

        # And now we can expand the command.
        self.desc = self.expand(self.desc)
        self.command = self.expand(self.command)

        if self.debug:
            log(f"Task after expand: {self}")

        # Check for missing input files/paths
        if not path.exists(self.command_path):
            raise FileNotFoundError(self.command_path)

        for file in self._in_files:
            if file is None:
                raise ValueErorr("_in_files contained a None")
            if not path.exists(file):
                raise FileNotFoundError(file)

        # Check that all build files would end up under root_path
        for file in self._out_files:
            if file is None:
                raise ValueErorr("_out_files contained a None")
            if not file.startswith(Config.root_path):
                raise ValueError(f"Path error, output file {file} is not under root_path {Config.root_path}")

        # Check for duplicate task outputs
        if self.command:
            for file in self._out_files:
                if file in app.all_out_files:
                    raise NameError(f"Multiple rules build {file}!")
                app.all_out_files.add(file)

        # Make sure our output directories exist
        if not self.dry_run:
            for file in self._out_files:
                os.makedirs(path.dirname(file), exist_ok=True)

    #-----------------------------------------------------------------------------------------------

    def needs_rerun(self, force=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""
        # Pylint really doesn't like this function, lol.
        # pylint: disable=too-many-return-statements
        # pylint: disable=too-many-branches

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
            return f"Rebuilding because hancho.py has changed"

        for file in self._in_files:
            if mtime(file) >= min_out:
                return f"Rebuilding because {file} has changed"

        for mod in self._loaded_modules:
            if mtime(mod) >= min_out:
                return f"Rebuilding because {mod} has changed"

        # Check all dependencies in the depfile, if present.
        depfile   = getattr(self, "depfile", None)
        depformat = getattr(self, "depformat", "gcc")

        if depfile is not None and path.exists(depfile):
            if self.debug:
                log(f"Found depfile {depfile}")
            with open(depfile, encoding="utf-8") as depfile:
                deplines = None
                if depformat == "msvc":
                    # MSVC /sourceDependencies json depfile
                    deplines = json.load(depfile)["Data"]["Includes"]
                elif depformat == "gcc":
                    # GCC -MMD .d depfile
                    deplines = depfile.read().split()
                    deplines = [d for d in deplines[1:] if d != "\\"]
                else:
                    raise ValueError(f"Invalid depformat {depformat}")

                # The contents of the depfile are RELATIVE TO THE WORKING DIRECTORY
                deplines = [path.join(self.command_path, d) for d in deplines]
                for abs_file in deplines:
                    if mtime(abs_file) >= min_out:
                        return f"Rebuilding because {abs_file} has changed"

        # All checks passed; we don't need to rebuild this output.
        # Empty string = no reason to rebuild
        return ""

    #-----------------------------------------------------------------------------------------------

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            #print("DJFJFJFJFJFJ")
            #print(self.command_path)
            #print("DJFJFJFJFJFJ")
            app.pushdir(self.command_path)
            result = command(self)
            app.popdir()
            while inspect.isawaitable(result):
                result = await result
            return

        # Non-string non-callable commands are not valid
        if not isinstance(command, str):
            raise ValueError(f"Don't know what to do with {command}")

        # Create the subprocess via asyncio and then await the result.
        try:
            if self.debug:
                log(f"Task {hex(id(self))} subprocess start '{command}'")

            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=self.command_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            (stdout_data, stderr_data) = await proc.communicate()
            if self.debug:
                log(f"Task {hex(id(self))} subprocess done '{command}'")
        except RuntimeError:
            sys.exit(-1)

        self.stdout = stdout_data.decode()
        self.stderr = stderr_data.decode()
        self.returncode = proc.returncode

        command_pass = (self.returncode == 0) != self.should_fail

        if self.save_log:
            result = open(self.out_log, "w")
            result.write("-----stderr-----\n")
            result.write(self.stderr)
            result.write("-----stdout-----\n")
            result.write(self.stdout)
            result.close()

        if self.verbose or not command_pass or self.stderr:
            if self.stderr and not self.should_fail:
                self.print_status()
                log("-----stderr-----")
                log(self.stderr, end="")
            if self.stdout:
                self.print_status()
                log("-----stdout-----")
                log(self.stdout, end="")

        if not command_pass:
            raise ValueError(self.returncode)


####################################################################################################

class App:
    """The application state. Mostly here so that the linter will stop complaining about my use of
    global variables. :D"""

    # pylint: disable=too-many-instance-attributes
    def __init__(self):
        self.loaded_modules = []
        self.dirstack = [os.getcwd()]
        self.modstack = []

        self.all_out_files = set()

        self.tasks_pass = 0
        self.tasks_fail = 0
        self.task_skipped = 0
        self.tasks_cancel = 0

        self.mtime_calls = 0
        self.line_dirty = False
        self.expand_depth = 0

        self.tasks_started = 0
        self.tasks_running = 0
        self.all_tasks = []
        self.queued_tasks = []
        self.started_tasks = []
        self.finished_tasks = []

        self.jobs_available = os.cpu_count()
        self.jobs_lock = asyncio.Condition()
        self.job_slots = [None] * self.jobs_available
        self.log = ""

    ########################################

    def pushdir(self, path):
        path = abs_path(path, strict=True)
        self.dirstack.append(path)
        os.chdir(path)

    def popdir(self):
        self.dirstack.pop()
        os.chdir(self.dirstack[-1])

    ########################################

    def reset(self):
        self.__init__()

    ########################################

    def main(self):
        result = -1
        self.parse_args()

        try:
            self.pushdir(Config.root_path)

            time_a = time.perf_counter()

            if Config.debug:
                log(f"global_config = {Dumper().dump(Config.__dict__)}")

            root_config = Config()
            self.load_module(
                repo_path = root_config.root_path,
                repo_name = path.basename(root_config.root_path),
                file_path = root_config.root_path,
                file_name = root_config.root_file,
                config    = root_config
            )
            time_b = time.perf_counter()

            #if Config.debug or Config.verbose:
            #    log(f"Loading .hancho files took {time_b-time_a:.3f} seconds")
            log(f"Loading .hancho files took {time_b-time_a:.3f} seconds")

            if Config.target:
                target_regex = re.compile(Config.target)
                for task in self.all_tasks:
                    queue_task = False
                    for name in flatten(task.name):
                        if target_regex.search(name):
                            queue_task = True
                    for desc in flatten(task.desc):
                        if target_regex.search(desc):
                            queue_task = True
                    for tag in flatten(task.tags):
                        if target_regex.search(tag):
                            queue_task = True
                    if queue_task:
                        log(f"Queueing task '{task.name}'")
                        task.queue()
            else:
                for task in self.all_tasks:
                    task.queue()

            result = self.build()
        finally:
            self.popdir()

        print()
        return result

    ########################################

    def parse_args(self):
        # pylint: disable=line-too-long
        # fmt: off
        parser = argparse.ArgumentParser()
        parser.add_argument("target",          default=None, nargs="?", type=str,           help="A regex that selects the targets to build. Defaults to all targets.")
        parser.add_argument("-f", "--file",    default="build.hancho",  type=str,           help="The name of the .hancho file(s) to build")
        parser.add_argument("-C", "--dir",     default=os.getcwd(),     type=str,           help="Change directory before starting the build")
        parser.add_argument("-j", "--jobs",    default=os.cpu_count(),  type=int,           help="Run N jobs in parallel (default = cpu_count)")
        parser.add_argument("-v", "--verbose", default=False, action="store_true",          help="Print verbose build info")
        parser.add_argument("-q", "--quiet",   default=False, action="store_true",          help="Mute all output")
        parser.add_argument("-n", "--dry_run", default=False, action="store_true",          help="Do not run commands")
        parser.add_argument("-d", "--debug",   default=False, action="store_true",          help="Print debugging information")
        parser.add_argument("-s", "--shuffle", default=False, action="store_true",          help="Shuffle task order to shake out dependency issues")
        parser.add_argument("-t", "--trace",   default=False, action="store_true",          help="Trace all text expansion")
        parser.add_argument(      "--force",   default=False, action="store_true",          help="Force rebuild of everything")
        # fmt: on

        (flags, unrecognized) = parser.parse_known_args()
        flags = flags.__dict__

        root_dir  = abs_path(flags['dir']) # Root path must be absolute.
        root_file = flags['file']
        root_path = os.path.join(root_dir, root_file)
        #print(root_dir)
        #print(root_file)
        #print(root_path)
        
        Config.root_path = root_dir
        Config.root_file = root_file
        Config.repo_path = Config.root_path
        Config.repo_name = ""

        Config.target  = flags['target']
        Config.jobs    = flags['jobs']
        Config.verbose = flags['verbose']
        Config.quiet   = flags['quiet']
        Config.dry_run = flags['dry_run']
        Config.debug   = flags['debug']
        Config.force   = flags['force']
        Config.shuffle = flags['shuffle']
        Config.trace   = flags['trace']

        for key, val in flags.items():
            setattr(Config, key, val)

        # Unrecognized command line parameters also become global Config fields if they are
        # flag-like
        unrecognized_flags = {}
        for span in unrecognized:
            if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
                key = match.group(1)
                val = match.group(2)
                val = maybe_as_number(val) if val is not None else True
                unrecognized_flags[key] = val

        for key, val in unrecognized_flags.items():
            setattr(Config, key, val)

    ########################################

    def build(self):
        """Run tasks until we're done with all of them."""
        result = -1
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = asyncio.run(self.async_run_tasks())
            # For some reason "result = asyncio.run(self.async_main())" might be breaking actions
            # in Github, so I'm using get_event_loop().run_until_complete().
            # Seems to fix the issue.
            #result = asyncio.get_event_loop().run_until_complete(self.async_run_tasks())
        except Exception as err:
            log(color(255, 128, 128), end = "")
            log("Build failed:")
            log(traceback.format_exc())
            log(color(), end="")
        loop.close()
        return result

    def build_all(self):
        for task in self.all_tasks:
            task.queue()
        return self.build()

    ########################################

    def start_queued_tasks(self):
        """Creates an asyncio.Task for each task in the queue and clears the queue."""

        if Config.shuffle:
            log(f"Shufflin' {len(self.queued_tasks)} tasks")
            random.shuffle(self.queued_tasks)

        while self.queued_tasks:
            task = self.queued_tasks.pop(0)
            task.start()
            self.started_tasks.append(task)

    ########################################

    async def async_run_tasks(self):
        # Run all tasks in the queue until we run out.

        self.jobs_available = Config.jobs
        self.job_slots = ["[----]"] * self.jobs_available

        # Tasks can create other tasks, and we don't want to block waiting on a whole batch of
        # tasks to complete before queueing up more. Instead, we just keep queuing up any pending
        # tasks after awaiting each one. Because we're awaiting tasks in the order they were
        # created, this will effectively walk through all tasks in dependency order.

        time_a = time.perf_counter()

        self.start_queued_tasks()
        while self.started_tasks:
            task = self.started_tasks.pop(0)
            await task._promise
            self.finished_tasks.append(task)
            self.start_queued_tasks()

        time_b = time.perf_counter()

        #if Config.debug or Config.verbose:
        log("")
        log(f"Running {len(app.all_tasks)} tasks took {time_b-time_a:.3f} seconds")

        # Done, print status info if needed
        #if Config.debug:
        if Config.verbose:
            log(f"tasks total:     {len(self.all_tasks)}")
            log(f"tasks passed:    {self.tasks_pass}")
            log(f"tasks failed:    {self.tasks_fail}")
            log(f"tasks skipped:   {self.task_skipped}")
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

    def load_module(self, repo_path, repo_name, file_path, file_name, config):
        """Loads a Hancho module ***while chdir'd into its directory***"""

        assert not macro_regex.search(repo_path)
        assert not macro_regex.search(repo_name)
        assert not macro_regex.search(file_path)
        assert not macro_regex.search(file_name)

        assert path.isabs(repo_path)
        assert not path.isabs(repo_name)
        assert path.isabs(file_path)
        assert not path.isabs(file_name)

        file_pathname = join_path(file_path, file_name)

        #if config.debug or config.verbose:
        log(("┃ " * (len(app.modstack) - 1)), end="")
        log(color(128,255,128) + f"Loading module {file_pathname}" + color())

        with open(file_pathname, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, file_name, "exec", dont_inherit=True)

        mod_name = path.splitext(file_name)[0]
        module = type(sys)(mod_name)
        module.__file__ = file_pathname
        module.__builtins__ = builtins

        module.imports = config
        module.exports = Config()

        module.repo_path = repo_path
        module.repo_name = repo_name
        module.file_path = file_path
        module.file_name = file_name

        self.loaded_modules.append(module)

        try:
            # We must chdir()s into the .hancho file directory before running it so that
            # glob() can resolve files relative to the .hancho file itself. We are _not_ in an async
            # context here so there should be no other threads trying to change cwd.
            app.pushdir(file_path)
            self.modstack.append(module)

            old_repo_path = Config.repo_path
            old_repo_name = Config.repo_name

            Config.repo_path = repo_path
            Config.repo_name = repo_name

            # Why Pylint thinks this is not callable is a mystery.
            # pylint: disable=not-callable
            types.FunctionType(code, module.__dict__)()
        finally:

            Config.repo_path = old_repo_path
            Config.repo_name = old_repo_name

            self.modstack.pop()
            app.popdir()
        return module.exports

    ########################################

    async def acquire_jobs(self, count, desc):
        """Waits until 'count' jobs are available and then removes them from the job pool."""

        if count > Config.jobs:
            raise ValueError(f"Nedd {count} jobs, but pool is {Config.jobs}.")

        await self.jobs_lock.acquire()
        await self.jobs_lock.wait_for(lambda: self.jobs_available >= count)

        slots_remaining = count
        for i, val in enumerate(self.job_slots):
            if val == "[----]" and slots_remaining:
                self.job_slots[i] = desc
                slots_remaining -= 1

        self.jobs_available -= count
        self.jobs_lock.release()

    ########################################
    # NOTE: The notify_all here is required because we don't know in advance which tasks will
    # be capable of running after we return jobs to the pool. HOWEVER, this also creates an
    # O(N^2) slowdown when we have a very large number of pending tasks (>1000) due to the
    # "Thundering Herd" problem - all tasks will wake up, only a few will acquire jobs, the
    # rest will go back to sleep again, this will repeat for every call to release_jobs().

    async def release_jobs(self, count, desc):
        """Returns 'count' jobs back to the job pool."""

        await self.jobs_lock.acquire()
        self.jobs_available += count

        slots_remaining = count
        for i, val in enumerate(self.job_slots):
            if val == desc:
                self.job_slots[i] = "[----]"
                slots_remaining -= 1

        self.jobs_lock.notify_all()
        self.jobs_lock.release()

####################################################################################################
# Always create an App() object so we can use it for bookkeeping even if we loaded Hancho as a
# module instead of running it directly.

app = App()

if __name__ == "__main__":
    sys.exit(app.main())
