#!/usr/bin/python3
# region header

"""
Hancho v0.4.0 @ 2024-11-01 - A simple, pleasant build system.

Hancho is a single-file build system that's designed to be dropped into your project folder - there
is no 'install' step.

Hancho's test suite can be found in 'test.hancho' in the root of the Hancho repo.
"""

from __future__ import annotations

import argparse
import asyncio
import colorsys
import contextvars
import enum
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import types
from collections import ChainMap, abc
from contextlib import chdir
from inspect import isawaitable
from typing import Any, cast

hancho = sys.modules[__name__]

# endregion
####################################################################################################
# region Main

type Tree[T] = T | list[Tree[T]] | dict[Any, Tree[T]]

def __getattr__(name):
    # Any attribute read that's not global in this module gets redirected to the per-script context
    # dict.

    if name == "config":
        return Loader.cv_config.get()
    elif name in aliases:
        return aliases[name]
    else:
        raise AttributeError(name)

def __dir__():
    return [*hancho.__dict__.keys(), *aliases.keys(), "config"]

# ----------------------------------------

def init(*args, **kwargs):
    """
    Re-initializes all of Hancho.
    If you are importing Hancho directly, you should call this as
    hancho.init(debug = True, quiet = False, myoption=1234)
    """
    reset(*args, **kwargs)

# ----------------------------------------

def reset(*args, **kwargs):
    Loader.reset(*args, **kwargs)
    Log.reset()
    Utils.reset()
    Task.reset()
    Tracer.reset()
    Runner.reset(hancho.config.core_max)

# ----------------------------------------

def main():

    flags = Loader.parse_flags(sys.argv[1:])
    init(flags)

    #----------------------------------------
    # Load all build scripts

    time_a = time.perf_counter()

    script_path = cast(str, Path.join(hancho.config.root_dir, hancho.config.root_file))
    if not Path.exists(script_path):
        path = Path.rel(script_path, os.getcwd())
        Log.log_fatal(f"Could not load build script {path}")
    Loader.root_repo = Loader.load_file(script_path, True)

    time_load = time.perf_counter() - time_a
    Log.log_v(f"Loading .hancho files took {time_load:.3f} seconds", 0x8080FF)

    #----------------------------------------
    # Run tools if needed

    if hancho.config.tool:
        result = Runner.run_tool(hancho.config.tool)
        return result

    #----------------------------------------
    # Start all tasks

    if hancho.config.target:
        target_regex = re.compile(hancho.config.target)
        Runner.enable_tasks_by_regex(target_regex)
    elif hancho.config.build_all:
        Runner.enable_all_tasks()
    else:
        Runner.enable_root_tasks()

    #----------------------------------------
    # Run all tasks

    time_a = time.perf_counter()
    result = Runner.sync_run_tasks()
    time_build = time.perf_counter() - time_a
    Log.log_v(f"Running {Task.tasks_enabled} tasks took {time_build:.3f} seconds", 0x8080FF)

    #----------------------------------------
    # Done

    return result

# endregion
####################################################################################################
# region Log

class Log:
    """Simple logger that can do same-line log messages like Ninja."""

    buffer : str
    con_w : int
    reset_color = "\x1B[0m"
    match_escapes = re.compile(r"\x1B.*?m")

    @staticmethod
    def reset():
        Log.buffer = ""
        Log.con_w = shutil.get_terminal_size().columns

    @staticmethod
    def clip_printable(text, width):
        result = ""
        accum = 0

        while text:
            match = Log.match_escapes.search(text)
            if not match:
                result += text[:width - accum]
                break
            chunk = text[:match.start()][:width - accum]
            result += chunk
            accum += len(chunk)
            if accum == width:
                return result
            result += match.group()
            text = text[match.end():]

        return result

    @staticmethod
    def log(message : str | list[str], color : int = 0):

        if isinstance(message, list):
            for m in message:
                Log.log(m, color)
            return

        lines = message.split('\n')
        for i, line in enumerate(lines):
            if not hancho.config.wrap:
                line = Log.clip_printable(line, Log.con_w)
            Log.buffer += line + "\n"

            use_newline = (i < len(lines) - 1) or hancho.config.debug or hancho.config.verbose

            if not hancho.config.quiet:
                sys.stdout.write("" if use_newline else "\r")
                sys.stdout.write(Utils.hex_to_ansi(color))
                sys.stdout.write(line)
                sys.stdout.write(Log.reset_color)
                sys.stdout.write("\n" if use_newline else "\x1B[K")
                sys.stdout.flush()

    @staticmethod
    def log_fatal(message):
        Log.log(message, 0xFF0000)
        sys.exit(-1)

    @staticmethod
    def log_d(message):
        if hancho.config.debug:
            Log.log(message, 0x606060)

    @staticmethod
    def log_v(message : str, color : int = 0x606060):
        if hancho.config.debug or hancho.config.verbose:
            Log.log(message, color)

    @staticmethod
    def dump_to_str(key, val, indent = 0, print_id = False, max_width = 80, tab = "  ", flat = False):
        """
        Hancho's pretty-printer for various types. Note that this is also used for script deduping:
        if you load "my/app/tools/stuff.hancho" multiple times but the configurations you gave it
        were identical, you should get one copy of the "stuff" module instead of two.
        Changing the way things are pretty-printed will _not_ break the deduper,
        """

        # In "key : type = ", don't print these types.
        basic_types = (str, bool, int, float, list, tuple, set, bytes, bytearray, range, type(None))

        skip_type = isinstance(val, basic_types)

        # Generate the "key : type = " prefix.
        prefix = ""
        if key is not None:
            prefix += str(key) + " "
        if not skip_type:
            prefix += ": " + type(val).__name__ + " "
        if print_id:
            prefix += ": " + hex(id(val)) + " "
        if prefix:
            prefix += "= "

        # Unwrap a few types that we want to view as containers
        if   isinstance(val, Task):
            val = val.__dict__
        elif isinstance(val, Expander):
            val = val._context
        elif isinstance(val, contextvars.Context):
            val = list(val.keys())

        # Non-containers are always emitted on one line. If they overflow, they overflow.
        if not (Utils.is_collection(val) or Utils.is_mapping(val)):
            # Objects that don't have a custom repr (and a few built-in types) just get printed as '<object>'
            if type(val).__repr__ is object.__repr__ or type(val) in [types.FunctionType, types.BuiltinFunctionType, types.ModuleType, types.GeneratorType, types.LambdaType]:
                return (tab * indent) + prefix + "<object>"
            else:
                return (tab * indent) + prefix + repr(val)

        # Extract key-value pairs and set delimiters for our container types.
        if isinstance(val, tuple):
            items = [(None, val2) for val2 in val]
            ld = "("
            rd = ",)" if len(items) == 1 else ")"
        elif Utils.is_mapping(val):
            val = cast(abc.Mapping, val)
            items = val.items()
            ld = "{"
            rd = "}"
        elif Utils.is_collection(val):
            items = [(None, val2) for val2 in cast(abc.Collection, val)]
            ld = "["
            rd = "]"
        else:
            raise AssertionError(f"Don't know what to do with {type(val)}")

        # Iterate over our key-value pairs, converting them in to string chunks. If the resulting line
        # would be too wide and we're not trying to generate a flat string, fall back to multi-line.
        pad = (tab * indent)
        separator = ", "
        chunks = []
        width = len(pad) + len(prefix) + len(ld) + (len(separator) * (len(items) - 1)) + len(rd)

        for k, v in items:
            chunk = Log.dump_to_str(k, v, 0, print_id, max_width, tab, True)
            if chunk is None or width + len(chunk) > max_width:
                if flat:
                    return None
                separator = ",\n"
                chunks = (Log.dump_to_str(k, v, indent + 1, print_id, max_width, tab, False) for k, v in items)
                return pad + prefix + ld + "\n" + separator.join(chunks) + "\n" + pad + rd
            width += len(chunk)
            chunks.append(chunk)

        # Done, we can fit this dump on one line.
        return pad + prefix + ld + separator.join(chunks) + rd

# endregion
####################################################################################################
# region Utils

class Utils:

    rand : random.Random
    mtime_calls = 0

    @staticmethod
    def reset():
        Utils.rand = random.Random()
        Utils.mtime_calls = 0

    #----------------------------------------

    @staticmethod
    def recursify_all(func: abc.Callable[..., bool]):
        """Turns a function that maps scalars to bools into one that evaluates any([func(x) for x in v])."""

        def outer(v, *args, **kwargs):
            if Utils.is_collection(v):
                return all(outer(v2, *args, **kwargs) for v2 in v)
            elif Utils.is_mapping(v):
                return all(outer(v2, *args, **kwargs) for _, v2 in v.items())
            else:
                return func(v, *args, **kwargs)

        return outer

    @staticmethod
    def recursify_map(func: abc.Callable[..., Any]):
        """
        Creates a function that recursively applies 'func' to a Tree[T], creating a new Tree[T] in the process.
        """

        def outer(v, *args, **kwargs):
            if Utils.is_collection(v):
                return [outer(v2, *args, **kwargs) for v2 in v]
            elif Utils.is_mapping(v):
                return {k2: outer(v2, *args, **kwargs) for k2, v2 in v.items()}
            else:
                return func(v, *args, **kwargs)

        return outer

    @staticmethod
    def recursify_apply_mip(func):
        """
        MIP = Modify In-Place
        Creates a static function that recursively applies 'func' to a Tree[T], modifying the tree in-place.
        """

        def outer(v, *args, **kwargs):
            if Utils.is_collection(v):
                for k2, v2 in enumerate(v):
                    v[k2] = outer(v2, *args, **kwargs)
            elif Utils.is_mapping(v):
                for k2, v2 in v.items():
                    v[k2] = outer(v2, *args, **kwargs)
            else:
                v = func(v, *args, **kwargs)
            return v

        return outer

    @staticmethod
    def recursify_apply_mip_member(func):
        """
        MIP = Modify In-Place
        Creates a member function that recursively applies 'self.func' to a Tree[T], modifying the tree in-place.
        """

        def outer(self, v, *args, **kwargs):
            if Utils.is_collection(v):
                for k2, v2 in enumerate(v):
                    v[k2] = outer(self, v2, *args, **kwargs)
            elif Utils.is_mapping(v):
                for k2, v2 in v.items():
                    v[k2] = outer(self, v2, *args, **kwargs)
            else:
                v = func(self, v, *args, **kwargs)
            return v

        return outer

    @staticmethod
    def recursify_pairwise_map(func):
        """
        Creates a function with two args that effectively
        1. Flattens both arguments.
        2. Creates a list of all possible pairs using one element of each argument.
        3. Creates a list by applying 'func' to each element of the previous list
        4. Returns the list if len(list) > 1, otherwise returns the scalar in list[0].
        """

        def inner(accum, a, b, *args, **kwargs):
            if Utils.is_collection(a):
                for c in a:
                    inner(accum, c, b, *args, **kwargs)
            elif Utils.is_collection(b):
                for c in b:
                    inner(accum, a, c, *args, **kwargs)
            else:
                accum.append(func(a, b, *args, **kwargs))

        def outer(a, b, *args, **kwargs):
            accum = []
            inner(accum, a, b, *args, **kwargs)
            return accum[0] if len(accum) == 1 else accum

        return outer

    # ----------------------------------------

    @staticmethod
    def stringify(variant) -> str:
        """Converts any type into a template-compatible string."""
        if variant is None:
            return ""
        elif Utils.is_collection(variant):
            variant = [Utils.stringify(val) for val in variant]
            return " ".join(variant)
        else:
            return str(variant)

    # ----------------------------------------

    @staticmethod
    def in_event_loop() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    #----------------------------------------

    @staticmethod
    def is_scalar(v):
        return not Utils.is_collection(v) and not Utils.is_mapping(v)

    @staticmethod
    def is_flat_list_of[T](c : Any, as_type : type[T]):
        if Utils.is_collection(c):
            return all(isinstance(v, as_type) for v in c)
        elif Utils.is_mapping(c):
            return all(isinstance(v, as_type) for v in c.values())
        return isinstance(c, as_type)

    @staticmethod
    def is_collection(variant : Any) -> bool:
        """
        Mappings and non-array iterables are not considered Collections in Hancho so that
        we don't turn "foo" into ('f', 'o', 'o').
        """
        if isinstance(variant, (str, bytes, bytearray, range, abc.Mapping)):
            return False
        return isinstance(variant, abc.Collection)

    @staticmethod
    def is_iterable(variant : Any) -> bool:
        if isinstance(variant, (str, bytes, bytearray, abc.Mapping)):
            return False
        return isinstance(variant, abc.Iterable)

    @staticmethod
    def is_mapping(variant : Any) -> bool:
        return isinstance(variant, abc.Mapping)

    #----------------------------------------
    # Checks if a string needs template expansion. Empty strings are considered literals.

    #braced = re.compile(r"\{(\\.|[^\\}])*\}")
    braced = re.compile(r"(?<!\\)\{(\\.|[^\\}])*\}")

    @recursify_all
    def is_literal(v: Any) -> bool:
        return isinstance(v, str) and len(v) != 0 and Utils.braced.search(v) is None

    @recursify_all
    def is_braced(v: Any) -> bool:
        return isinstance(v, str) and len(v) != 0 and Utils.braced.search(v) is not None

    @recursify_all
    def is_macro(v: Any) -> bool:
        return (
            isinstance(v, str)
            and (len(v) != 0)
            and (m := Utils.braced.search(v)) is not None
            and (m.group() == v)
        )

    @recursify_all
    def is_template(v: Any) -> bool:
        return (
            isinstance(v, str)
            and (len(v) != 0)
            and (m := Utils.braced.search(v)) is not None
            and (m.group() != v)
        )

    #----------------------------------------

    @staticmethod
    def weave(lhs, rhs, *args) -> list[str]:
        """
        This function does a 'cross join' in the database sense, every line in lhs will be joined
        to every line in rhs (and this will be repeated with *args if present). This is useful for
        adding prefixes / suffixes to a bunch of strings, or generating all possible combinations
        of two sets of options, etecetera.
        """

        lhs2 = Utils.flatten(lhs)
        rhs2 = Utils.weave(rhs, *args) if len(args) > 0 else Utils.flatten(rhs)
        return [lh + rh for lh in lhs2 for rh in rhs2]

    #----------------------------------------

    @staticmethod
    def hex_to_rgb(hexcode : int) -> tuple[int, int, int]:
        return ((hexcode >> 16) & 0xFF, (hexcode >>  8) & 0xFF, (hexcode >>  0) & 0xFF)

    @staticmethod
    def rgb_to_hex(r : int, g : int, b : int) -> int:
        return (r << 16) | (g << 8) | (b << 0)

    #----------------------------------------

    @staticmethod
    def rgb_to_ansi(r : int, g : int, b : int) -> str:
        if r == 0 and g == 0 and b == 0:
            return "\x1B[0m"
        return f"\x1B[38;2;{r};{g};{b}m"

    @staticmethod
    def hex_to_ansi(hexcode : int = 0):
        return Utils.rgb_to_ansi(*Utils.hex_to_rgb(hexcode))

    @staticmethod
    def obj_to_ansi(obj):
        return Utils.rgb_to_ansi(*Utils.obj_to_rgb(obj))

    #----------------------------------------

    @staticmethod
    def obj_to_hex(obj) -> int:
        return Utils.rgb_to_hex(*Utils.obj_to_rgb(obj))

    @staticmethod
    def obj_to_rgb(obj) -> tuple[int, int, int]:
        Utils.rand.seed(id(obj))
        r, g, b = colorsys.hsv_to_rgb(Utils.rand.random(), 0.3, 1.0)
        return (int(r * 255), int(g * 255), int(b * 255))

    #----------------------------------------

    @staticmethod
    def run_cmd(cmd : str):
        """Runs a console command synchronously and returns its stdout with whitespace stripped."""
        result = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        return result

    #----------------------------------------

    @staticmethod
    def mtime(filename : str):
        """Gets the file's mtime and tracks how many times we've called mtime()"""
        Utils.mtime_calls += 1
        return os.stat(filename).st_mtime_ns

    #----------------------------------------

    @staticmethod
    def flatten(variant : Any) -> list[Any]:
        if Utils.is_iterable(variant):
            return [x for element in variant for x in Utils.flatten(element)]
        return [] if variant is None else [variant]

# endregion
####################################################################################################
# region Path
# These are just equivalents of the os.path.* functions that work on Tree[str].

class Path:

    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with simple string manipulation.

    @staticmethod
    @Utils.recursify_pairwise_map
    def rel(path1, path2):
        return path1.removeprefix(path2 + "/") if path1 != path2 else "."

    @staticmethod
    @Utils.recursify_pairwise_map
    def join(lhs, rhs):
        return os.path.join(lhs, rhs)

    #----------------------------------------
    # We want these functions to work on Tree[str], so we run them through recursify.

    @staticmethod
    @Utils.recursify_map
    def abs(p): return os.path.abspath(p) if p else ""

    @staticmethod
    @Utils.recursify_map
    def real(p): return os.path.realpath(p) if p else ""

    @staticmethod
    @Utils.recursify_map
    def norm(p): return os.path.normpath(p) if p else ""

    #----------------------------------------

    @staticmethod
    @Utils.recursify_map
    def base(p): return os.path.basename(p)

    @staticmethod
    @Utils.recursify_map
    def ext(p, new_ext): return os.path.splitext(p)[0] + new_ext

    @staticmethod
    @Utils.recursify_map
    def stem(p): return os.path.splitext(os.path.basename(p))[0]

    @staticmethod
    @Utils.recursify_map
    def dirname(path): return os.path.dirname(path)

    @staticmethod
    @Utils.recursify_map
    def split(path): return os.path.split(path)

    @staticmethod
    @Utils.recursify_map
    def splitext(path): return os.path.splitext(path)

    #----------------------------------------

    @staticmethod
    @Utils.recursify_all
    def isabs(v): return isinstance(v, str) and len(v) > 0 and os.path.isabs(v)

    @staticmethod
    @Utils.recursify_all
    def isfile(path): return isinstance(path, str) and os.path.isfile(path)

    @staticmethod
    @Utils.recursify_all
    def isdir(path): return isinstance(path, str) and os.path.isdir(path)

    @staticmethod
    @Utils.recursify_all
    def exists(path): return isinstance(path, str) and os.path.exists(path)

# endregion
####################################################################################################
# region Dict

class Dict(dict):
    """
    This class extends 'dict' in a couple ways -
    1. Dict supports "foo.bar" attribute access in addition to "foo['bar']"
    2. Dict supports "merging" instances by passing them (and any additional key-value pairs) in via the constructor.
    3. When merging Dicts, the rightmost not-None value of an attribute will be kept.
    4. If two attributes with the same name are both Dicts, we will recursively merge them.
    5. Copying dicts deep-copies all nested mappings, other containers (list, tuple) are shallow-copied.
    """

    def __init__(self, *args, **kwargs):
        super().__init__()

        # Ignore Nones and empty dicts.
        for arg in filter(None, (*args, kwargs)):
            assert Utils.is_mapping(arg)
            for key, rval in arg.items():
                lval = dict.get(self, key, None)

                # Mappings get turned into Dicts.
                if Utils.is_mapping(rval) and type(rval) is not Dict:
                    rval = Dict(rval)

                # Pairs of mappings get merged together as needed.
                if Utils.is_mapping(lval) and Utils.is_mapping(rval):
                    rval = Dict(lval, rval)

                if lval is None or rval is not None:
                    dict.__setitem__(self, key, rval)

    #----------------------------------------
    # Object

    def on_keyerror(self, key):
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'")

    def __getattr__(self, key : str):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            self.on_keyerror(key)

    def __setattr__(self, key : str, val : Any):
        try:
            return dict.__setitem__(self, key, val)
        except KeyError:
            self.on_keyerror(key)

    def __delattr__(self, key : str):
        try:
            return dict.__delitem__(self, key)
        except KeyError:
            self.on_keyerror(key)

    def __or__(self, other):
        return Dict(self, other)

    def __repr__(self):
        return Log.dump_to_str(key = getattr(self, "name", "_"), val = self)

    #----------------------------------------
    # Expander convenience helpers

    def eval[T](self, expr : str, as_type: type[T] = object) -> T:
        result = Expander.eval(expr, self)
        assert isinstance(result, as_type)
        return result

    def expand_once[T](self, text : str, as_type : type[T] = object) -> T:
        result = Expander.expand_once(text, self)
        assert isinstance(result, as_type)
        return result

    def expand_all[T](self, text : Tree[str], as_type : type[T] = object) -> T:
        result = Expander.expand_all(text, self)
        assert isinstance(result, as_type)
        return result

# Tool is just an alias for Dict to make build scripts more readable.
class Tool(Dict):
    pass

# endregion
####################################################################################################
# region Task
# Task object + bookkeeping

class Task:

    id_counter : int = 0
    tasks_enabled : int = 0

    @staticmethod
    def reset():
        Task.id_counter = 0
        Task.tasks_enabled = 0

    class Status(enum.Enum):
        PENDING   = enum.auto()
        FINISHED  = enum.auto()
        FAILED    = enum.auto()
        CANCELLED = enum.auto()
        SKIPPED   = enum.auto()
        BROKEN    = enum.auto()

    def __init__(self, *args, **kwargs):
        # Save the context, we will use it when we create the asyncio.Task
        self._context = contextvars.copy_context()
        self._config  = Dict(hancho.config, *args, **kwargs)
        self._expand  = Expander.wrap(self._config, self._config.trace)

        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._asyncio_task : asyncio.Task | None = None
        self._status : Task.Status =  Task.Status.PENDING

        # Tasks depend on all .hancho files that were loaded when the task was created.
        # This is probably too wide a net, but tracking dependencies between .hancho files is not
        # really possible.
        self._loaded_files : list[str] = list(Loader.loaded_files)

        # State machine, holds a pending coroutine for the next state (or None)
        self._state = None

        # Bookkeeping stuff
        self._task_id : int = 0
        self._stdout : str = ""
        self._stderr : str = ""

        self._core_count = 0

        self._in_files  = []
        self._out_files = []

        # ----------------------------------------
        # Expand all fields that don't depend on input/output filenames (basically everything)
        # except name/desc/command

        path_fields  = ["hancho_dir", "task_cwd", "root_dir", "root_file", "repo_dir", "repo_file",
                        "script_cwd", "script_file", "build_root", "build_dir"]

        flag_fields  = ["core_count", "core_max", "depformat", "build_tag", "target", "tool",
                        "keep_going", "verbose", "debug", "dry_run", "quiet", "rebuild",
                        "trace", "build_all"]

        for f in path_fields:
            if f in self._config:
                self._config[f] = Path.norm(self._expand[f])
        for f in flag_fields:
            if f in self._config:
                self._config[f] = self._expand[f]

        # ----------------------------------------
        # Flatten all inputs/outputs and the command

        for k, v in self._config.items():
            if Task.is_io_field(k) or k == "command":
                v = Utils.flatten(v)
                self._config[k] = v
            if Task.is_depfile_field(k) and len(v) > 1:
                raise AssertionError("Tasks can't have more than one dependency file!")

        if not self._config.command:
            raise ValueError(f"Task {self._config.name} has no command! >{self._config.command}<")

        # ----------------------------------------
        # Check that all commands are valid

        for command in self._config.command:
            if type(command) is not type(self._config.command[0]):
                self.log(f"Commands aren't the same type: {self._config.command}", 0xFF0000)
                raise ValueError(f"Commands aren't the same type: {self._config.command}")

            if not isinstance(command, str) and not callable(command):
                raise ValueError(f"Don't know what to do with command '{command}'")

        # ----------------------------------------
        # Check for missing paths

        if not Path.exists(self._config.task_cwd):
            raise ValueError(f"Task working directory '{self._config.task_cwd}' does not exist")

        if not self._config.build_dir.startswith(self._config.repo_dir):
            raise ValueError(f"Build_dir {self._config.build_dir} is not under repo dir {self._config.repo_dir}")

        # ----------------------------------------

        Runner.all_tasks.append(self)

        if Utils.in_event_loop():
            self.enable()

    # -----------------------------------------------------------------------------------------------
    # WARNING: Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.

    def __copy__(self):
        raise AssertionError("Don't copy Tasks!")

    def __deepcopy__(self, _):
        raise AssertionError("Don't copy Tasks!")

    def __repr__(self):
        return Log.dump_to_str(key = "Task", val = self)

    # -----------------------------------------------------------------------------------------------

    @staticmethod
    def is_depfile_field(name : str) -> bool:
        return name == "in_depfile"

    @staticmethod
    def is_output_field(name : str):
        return name and (Task.is_depfile_field(name) or name.startswith("out_"))

    @staticmethod
    def is_input_field(name : str):
        return name and name.startswith("in_")

    @staticmethod
    def is_io_field(name : str):
        return Task.is_input_field(name) or Task.is_output_field(name)

    # -----------------------------------------------------------------------------------------------

    def log(self, message : str, color : int = 0):
        prefix  = ""
        prefix += Utils.hex_to_ansi(0x80FF80)
        prefix += f"[{self._task_id}/{Task.tasks_enabled}] "
        prefix += Utils.hex_to_ansi(color)
        prefix += message
        prefix += Log.reset_color
        Log.log(prefix)

    def log_d(self, message : str, color : int = 0):
        if self._config.debug:
            self.log(message, color)

    def log_v(self, message : str, color : int = 0):
        if self._config.verbose or self._config.debug:
            self.log(message, color)

    # -----------------------------------------------------------------------------------------------

    def enable(self):
        if not self._config.enabled:
            self._config.enabled = True
            Task.tasks_enabled += 1
            if Utils.in_event_loop():
                self.create_asyncio_task()

    @Utils.recursify_apply_mip_member
    def create_parent_tasks(self, v):
        if isinstance(v, Task):
            v.create_asyncio_task()
        return v

    def create_asyncio_task(self):
        assert Utils.in_event_loop()

        if self._state is None:
            self._state = self.WAITING()
            self._asyncio_task = asyncio.create_task(self.task_top(), context=self._context)

        # Recurse through all tasks referenced by _config so we don't deadlock while waiting for
        # them.

        self.create_parent_tasks(self._config)

    # -----------------------------------------------------------------------------------------------

    async def task_top(self):
        """
        Hancho is using async member functions as both the names of states in a state machine and
        as the implementation of the states themselves. This is slightly weird, but it allows for
        a really nice system where states can run asynchronously and can pass parameters to each
        other.

        To dispatch each state function, we await the coroutine in self._state - this will return
        either the next coroutine to run, or None if the task is complete. The self._state field
        will stay at the last awaited coroutine (FINISHED, FAILED, etc) so that other tasks can
        check on this one's status. Both the coroutine and the corresponding async function have
        '__name__' fields that we can use for comparing and pretty-printing states.
        """

        try:
            while self._state:
                if not isawaitable(self._state):
                    raise AssertionError("Task._state is not awaitable, it should be")
                next_state = await self._state
                if next_state is None:
                    break
                self._state = next_state

        finally:
            if self._core_count:
                Runner.release(self._core_count)
                self._core_count = 0

        return self._status

    # -----------------------------------------------------------------------------------------------

    async def WAITING(self):
        """Await everything awaitable in this task's config. If any of this tasks's dependencies
        failed, we propagate a cancellation to downstream tasks."""

        # Await our dependencies. If any of our dependencies failed, we are cancelled.
        for name, files in self._config.items():
            if Task.is_input_field(name):
                for i, file in enumerate(files):
                    if isinstance(file, Task):
                        task = cast(Task, file)
                        task_status = await cast(asyncio.Task, task._asyncio_task)
                        if task_status == Task.Status.FAILED:
                            return self.CANCELLED()
                        files[i] = task._out_files
                self._config[name] = Utils.flatten(files)

        return self.SETUP()

    # -----------------------------------------------------------------------------------------------

    def remap_io_field_paths(self, name, files) -> list[str]:
        """
        Input and output file paths in .hancho scripts are declared relative to the directory the
        script is in (stored in the config under 'script_cwd').
        In general we want to run commands from the root of the repo and store output files in
        repo/build. Additionally, our file paths may be text templates that we need to expand first.
        This function takes care of all of that and a few other things, and tries to do so in a
        robust way. Whether this actually turns out to be robust or not is yet to be determined.
        """

        c = self._config
        e = self._expand

        # Expand all in_ and out_ filenames.
        # We _must_ expand these first before joining paths or the paths will be incorrect:
        # prefix + swap(abs_path) != abs(prefix + swap(path))
        files = Expander.expand_all(files, e)

        # Initially, all our file paths are relative to the script_cwd that created this task.
        # Join script_cwd with the filenames to produce absolute paths.
        files = Path.join(c.script_cwd, files)

        # Expanding may have made our files array non-flat, but its contents should be all
        # absolute paths now.
        files = Utils.flatten(files)
        assert Path.isabs(files)

        # Path _must_ be normed after expansion and joining, otherwise it might look like it's
        # under script_cwd but it's not because the path could have "../../../../.." in it.
        files = cast(list[str], Path.norm(files))

        # Move all outputs under build_dir and ensure their directories exist.
        if Task.is_output_field(name):
            for i in range(len(files)):
                # Note these conditionals are _NOT_ an if/elif pair!
                if not files[i].startswith(c.build_dir):
                    files[i] = files[i].replace(c.task_cwd, c.build_dir)

                if files[i].startswith(c.build_dir):
                    dirname = Path.dirname(files[i])
                    if dirname is not None:
                        os.makedirs(dirname, exist_ok=True)

        # Gather all absolute file paths to _in_files/_out_files.
        for i in range(len(files)):
            # The check for is_depfile_field must come first, as it's a special case of a file that
            # is technically an _output_ file, but also counts as an input file.
            if Task.is_depfile_field(name):
                if Path.isfile(files[i]):
                    self._in_files.append(files[i])
            elif Task.is_output_field(name):
                self._out_files.append(files[i])
            elif Task.is_input_field(name):
                self._in_files.append(files[i])

        # Convert the fixed paths back to relative so our command lines aren't enormous.
        # Relative paths are relative to task_cwd if we're running a command, otherwise they're
        # relative to script_cwd if we're calling a callback.
        rel_dir = c.task_cwd if isinstance(c.command[0], str) else c.script_cwd

        for i in range(len(files)):
            files[i] = Path.rel(files[i], rel_dir)

        return files

    # -----------------------------------------------------------------------------------------------

    async def SETUP(self):
        # Now that all our inputs are ready, grab a _task_id that we'll use in our logging.
        Task.id_counter += 1
        self._task_id = Task.id_counter

        c = self._config
        e = self._expand

        self.log_d("Task config before expand:", 0xFFFFFF)
        for line in str(c).strip().split("\n"):
            self.log_d(line, 0xFFFFFF)

        # ----------------------------------------
        # Path cleanup

        # Relative paths are relative to task_cwd if we're running a command, otherwise they're
        # relative to script_cwd if we're calling a callback.
        #rel_dir = c.task_cwd if isinstance(c.command[0], str) else c.script_cwd

        for name, files in c.items():
            if not Task.is_io_field(name):
                continue

            # First, flatten our list of files so we don't have to deal with weird nested
            # structures.
            files = Utils.flatten(files)

            # All our input and output fields should contain flat arrays of strings now.
            if not Utils.is_flat_list_of(files, str):
                raise AssertionError(
                    "SETUP got a task without flattened input/output fields, or some of the " +
                    "fields were non-strings"
                )

            # Do all the file path remapping so our commands will work
            files = self.remap_io_field_paths(name, files)

            # and unwrap filenames if they're an array of one element so that scripts expecting
            # join(str, str) to return a str will be happy.
            c[name] = files[0] if len(files) == 1 else files

        # ----------------------------------------
        # Paths are cleaned up, we can expand name/desc/command

        c.name    = Expander.expand_all("{name}", e)
        c.desc    = Expander.expand_all("{desc}", e)
        c.command = Expander.expand_all("{command}", e)

        if c.strict and Utils.is_braced(c.command):
            return self.BROKEN("We are in strict mode and this task's command has curly braces in it - did you typo a template?")

        self.log_d("Task config after expand:", 0xFFFFFF)
        for line in str(c).strip().split("\n"):
            self.log_d(line, 0xFFFFFF)

        if c.dry_run:
            return self.FINISHED()
        else:
            return self.SANITY_CHECK()

    # -----------------------------------------------------------------------------------------------

    async def SANITY_CHECK(self):
        # Check for missing inputs
        for file in self._in_files:
            assert Path.isabs(file)
            if not Path.exists(file):
                return self.BROKEN(f"Input file missing - {file}")

        # Check that all build files would end up under build_dir
        for file in self._out_files:
            assert Path.isabs(file)
            if not file.startswith(self._config.build_dir):
                return self.BROKEN(f"Path error, output file {file} is not under build_dir {self._config.build_dir}")

        # Check for task collisions
        for file in self._out_files:
            real_file = cast(str, Path.real(file))
            if real_file in Loader.real_filenames:
                return self.BROKEN(f"TaskCollision: Multiple tasks build {real_file}")
            Loader.real_filenames.add(real_file)

        return self.CHECK_DEPS()

    # -----------------------------------------------------------------------------------------------

    async def CHECK_DEPS(self):
        # Check if we need a rebuild

        c = self._config
        cwd = os.getcwd()

        if self._config.rebuild:
            return self.RUNNING("Target forced to rebuild")
        if not self._in_files:
            return self.RUNNING("Always rebuild a target with no inputs")
        if not self._out_files:
            return self.RUNNING("Always rebuild a target with no outputs")

        # Check if any of our output files are missing.
        for file in self._out_files:
            if not Path.exists(file):
                return self.RUNNING(f"{Path.rel(file, cwd)} is missing")

        # Check if any of our input files are newer than the output files.
        min_out = min(Utils.mtime(f) for f in self._out_files)
        if Utils.mtime(__file__) >= min_out:
            return self.RUNNING("hancho.py has changed")

        for file in self._in_files:
            if Utils.mtime(file) >= min_out:
                return self.RUNNING(f"{Path.rel(file, cwd)} has changed")

        for file in self._loaded_files:
            if Utils.mtime(file) >= min_out:
                return self.RUNNING(f"{Path.rel(file, cwd)} has changed")

        # Check all dependencies in the C dependencies file, if present.

        if c.in_depfile and Path.exists(c.in_depfile):
            self.log_d(f"Found C dependencies file {c.in_depfile}")
            with open(c.in_depfile) as depcontents:
                deplines = None
                if c.depformat == "msvc":
                    # MSVC /sourceDependencies
                    deplines = json.load(depcontents)["Data"]["Includes"]
                elif c.depformat == "gcc":
                    # GCC -MMD
                    deplines = depcontents.read().split()
                    deplines = [d for d in deplines[1:] if d != "\\"]
                else:
                    return self.BROKEN(f"Invalid dependency file format {c.depformat}")

                # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
                deplines = [cast(str, Path.join(c.task_cwd, d)) for d in deplines]
                for abs_file in deplines:
                    if Utils.mtime(abs_file) >= min_out:
                        return self.RUNNING(f"Rebuilding because {Path.rel(abs_file, cwd)} has changed")


        # All checks passed; we don't need to rebuild this output.
        return self.SKIPPED()

    # -----------------------------------------------------------------------------------------------

    async def RUNNING(self, reason):
        """Wait for enough jobs to free up to run this task and then run the commands."""

        await Runner.acquire(self._config.core_count)
        self._core_count = self._config.core_count

        self.log(f"Task started : '{self._config.name}' - '{self._config.desc}'")
        self.log_v(f"Task rebuilding because: {reason}")

        return self.RUN_COMMAND(0)

    # -----------------------------------------------------------------------------------------------

    async def RUN_COMMAND(self, index):
        c = self._config
        command = c.command[index]

        if isinstance(command, str):
            self.log_v(f"{Path.rel(c.task_cwd, c.repo_dir)}$ {command}", 0x8080FF)

            # Create the subprocess via asyncio and then await the result.
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd    = c.task_cwd,
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.PIPE,
            )
            (stdout_data, stderr_data) = await proc.communicate()

            self._stdout = stdout_data.decode()
            self._stderr = stderr_data.decode()

            if proc.returncode:
                return self.FAILED("Command return code was non-zero")

        elif callable(command):
            self.log_v(f"{Path.rel(c.script_cwd, c.repo_dir)}$ {command}", 0x8080FF)

            try:
                with chdir(c.script_cwd):
                    result = command(self)
                    while isawaitable(result):
                        result = await result
            except Exception as ex:
                return self.FAILED("Callback threw an exception", ex)

        else:
            return self.BROKEN("Command is not a string or a callable?")

        if index == len(c.command) - 1:
            return self.FINISHED()
        else:
            return self.RUN_COMMAND(index + 1)


    # -----------------------------------------------------------------------------------------------

    async def FINISHED(self):
        self._status = Task.Status.FINISHED
        self.log_v(f"Task done : '{self._config.name}' - '{self._config.desc}'")
        return None

    # -----------------------------------------------------------------------------------------------

    async def CANCELLED(self):
        self._status = Task.Status.CANCELLED
        self.log_v(f"Task is cancelled: '{self._config.name}' : '{self._config.desc}'\n", 0x404040)
        return None

    # -----------------------------------------------------------------------------------------------

    async def FAILED(self, reason, ex = None):
        self._status = Task.Status.FAILED
        script_path = Path.join(self._config.script_cwd, self._config.script_file)

        self.log("Command failed!", 0xFF0000)
        self.log(f"From {script_path}:", 0xFF0000)
        self.log(f"    Task     = '{self._config.name}' : '{self._config.desc}'", 0xFF0000)
        self.log(f"    task_cwd = '{self._config.task_cwd}'", 0xFF0000)
        self.log(f"    getcwd   = '{os.getcwd()}'", 0xFF0000)
        self.log(f"    command  = '{self._config.command}'", 0xFF0000)
        self.log(f"    reason   = '{reason}'", 0xFF0000)
        self.log(f"    except   = '{ex}'", 0xFF0000)
        self.log_stdout()

        return None

    # -----------------------------------------------------------------------------------------------

    async def SKIPPED(self):
        self._status = Task.Status.SKIPPED
        self.log_v(f"Task is up-to-date: '{self._config.name}' : '{self._config.desc}'\n", 0x404040)
        return None

    # -----------------------------------------------------------------------------------------------

    async def BROKEN(self, reason):
        self._status = Task.Status.BROKEN
        script_path = Path.join(self._config.script_cwd, self._config.script_file)

        self.log("Task broken!", 0xFF0000)
        self.log(f"From {script_path}:", 0xFF0000)
        self.log(f"    Task     = '{self._config.name}' : '{self._config.desc}'", 0xFF0000)
        self.log(f"    task_cwd = '{self._config.task_cwd}'", 0xFF0000)
        self.log(f"    getcwd   = '{os.getcwd()}'", 0xFF0000)
        self.log(f"    command  = '{self._config.command}'", 0xFF0000)
        self.log(f"    reason   = '{reason}'", 0xFF0000)

        return None

    # -----------------------------------------------------------------------------------------------

    def log_stdout(self):
        self.log("========== Stdout ==========")
        for line in self._stdout.strip().split("\n"):
            self.log(line)
        self.log("========== Stderr ==========")
        for line in self._stderr.strip().split("\n"):
            self.log(line)
        self.log("============================")

    # -----------------------------------------------------------------------------------------------

# endregion
####################################################################################################
# region Expander
# Hancho's text expansion system.
#
# WARNING - Hancho is NOT A SANDBOX, Expander can evaluate arbitrary Python code which could format
# your hard drive and email spam to your grandparents. Use responsibly.
#
# Expander works similarly to Python's F-strings, but with quite a bit more power. The code here
# requires some explanation.
#
# We do not necessarily know in advance how the users will nest strings, macros, callbacks,
# etcetera. Text expansion therefore requires dynamic-dispatch-type stuff to ensure that we always
# end up with flat strings.
#
# The result of this is that the functions here are mutually recursive in a way that can lead to
# confusing callstacks, but that should handle every possible case of stuff inside other stuff.
#
# The depth checks are to prevent recursive runaway - the MAX_RECURSION limit is arbitrary but
# should suffice.
#
# Also - TEFINAE - Text Expansion Failure Is Not An Error. Dicts can contain macros that are not
# expandable by that dict. This allows nested dicts to contain templates that can only be expanded
# an outer dict, and things will still Just Work.

# this has to be a MutableMapping if we want to put it in the ChainMap for locals()
class Expander(abc.MutableMapping[str, object]):
    """
    This class is used to fetch and expand text templates from a dict during text expansion.
    It allows for both dictionary-like access (using `expander[key]`) and attribute-like access
    (using `expander.key`), making it versatile for accessing template variables and methods.
    """

    # Trivial string wrappers that let us differentiate "literal" strings and "{macro}" strings.
    class Literal(str):
        def __repr__(self):
            return "L" + str.__repr__(self)

    class Macro(str):
        def __repr__(self):
            return "M" + str.__repr__(self)

    #----------------------------------------

    def __init__(self, context : Dict, trace : bool):
        # These are just type annotations, because writing to fields while we're in the constructor
        # of a class that overrides __setattr__ does strange things.
        self._context : Dict
        self.trace : bool

        # The actual seet is here.
        super().__setattr__("_context", context)
        super().__setattr__("trace", trace)

    @staticmethod
    def wrap(context : Dict | Expander, trace : bool):
        if isinstance(context, Expander):
            return context

        result = Expander(context, trace)

        if trace:
            tag_a = (str(type(context).__name__)[:2] + "_" + hex(id(context))[-4:]).upper()
            tag_b = (str(type(result).__name__)[:2] + "_" + hex(id(result))[-4:]).upper()
            tag_a = Utils.obj_to_ansi(context) + tag_a + Log.reset_color
            tag_b = Utils.obj_to_ansi(result) + tag_b + Log.reset_color
            Tracer.log2(f"wrap {tag_a} -> {tag_b}")

        return result

    #----------------------------------------
    # MutableMapping interface

    def __getitem__(self, key):
        try: # Expander.__getitem__
            return self._get(key)
        except AttributeError as ex:
            raise KeyError from ex

    def __setitem__(self, key, val):
        cast(Dict, self._context).__setitem__(key, val)

    def __delitem__(self, key):
        cast(Dict, self._context).__delitem__(key)

    def __iter__(self):
        yield from cast(Dict, self._context)

    def __len__(self):
        return cast(Dict, self._context).__len__()

    #----------------------------------------
    # object interface

    def __repr__(self):
        result = f"{self.__class__.__name__} @ {hex(id(self))}"
        return result

    def __getattr__(self, key):
        try:
            return self._get(key)
        except KeyError as ex:
            raise AttributeError from ex

    def __setattr__(self, key, val):
        self._context.__setattr__(key, val)

    def __delattr__(self, key):
        self._context.__delattr__(key)

    #----------------------------------------

    def _get(self, key):
        """
        Reads and expands a field stored in our context. Mappings will be wrapped in an Expander so
        that expansions in nested dicts works correctly.
        """
        assert Utils.is_literal(key)

        with Tracer(self, f"_get('{key}')") as trace:
            result = self._context[key]

            if isinstance(result, Expander):
                pass
            elif Utils.is_mapping(result):
                result = Expander.wrap(result, self.trace)
            elif Utils.is_collection(result):
                result = [Expander.expand_all(v, self) for v in cast(list, result)]
            elif Utils.is_template(result) or Utils.is_macro(result):
                result = Expander.expand_all(result, self)

            trace.log_result(result)

        return result

    #----------------------------------------

    @staticmethod
    def split(text) -> list[str]:
        """
        Extracts all innermost single-brace-delimited spans from a block of text and produces a
        list of string literals and expressions. Escaped braces don't count as delimiters.
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
                result.append(Expander.Macro(text[lbrace:rbrace+1]))
                cursor = rbrace + 1
                lbrace = -1
                rbrace = -1

        if cursor < len(text):
            result.append(Expander.Literal(text[cursor:]))

        return result

    #--------------------------------------------------------------------------------

    @staticmethod
    def _eval(expr : str, context : Dict | Expander):
        """
        Evaluates (and optionally traces) an expression using four sources of symbol lookup:
        1. The config we're expanding
        2. The script-local hancho.config
        3. Convenience aliases
        4. The global hancho module
        """
        assert Utils.is_literal(expr)
        with Tracer(context, f"_eval('{expr}')") as tracer:
            _locals = ChainMap(context, Loader.cv_config.get(), aliases)
            _globals = hancho.__dict__
            result = eval(expr, _globals, _locals)
            tracer.log_result(result)
        return result

    @staticmethod
    def _expand_macro(macro : str, context : Dict | Expander) -> Any:
        """
        Expands a macro like "{len(options)}" into 20 or whatever.
        If a 'normal' exception is raised during eval, we return the macro unchanged (see TEFINAE
        in the docs). If it's RecursionError or one of the "special" exceptions that don't inherit
        from an exception, propagate that back out so we don't break ctrl-c and such.
        """
        assert Utils.is_macro(macro)
        with Tracer(context, f"_expand_macro('{macro}')") as tracer:
            try:
                result = Expander.eval(macro[1:-1], context)
            except RecursionError:
                raise
            except Exception:
                result = macro
            except BaseException:
                raise
            tracer.log_result(result)
        return result

    @staticmethod
    def _expand_template(template: str, context : Dict | Expander) -> str:
        """
        Expands a template by splitting it into Literal and Macro chunks, replacing each macro with
        their eval'd-and-stringized contents, and then gluing evrything back together again.
        """
        assert Utils.is_template(template)
        with Tracer(context, f"_expand_template('{template}')") as tracer:
            blocks = Expander.split(template)
            for (i, block) in enumerate(blocks):
                if isinstance(block, Expander.Macro):
                    value = Expander._expand_macro(block, context)
                    block = Utils.stringify(value)
                blocks[i] = block
            result = "".join(blocks)
            tracer.log_result(result)
        return result

    #----------------------------------------

    @staticmethod
    @Utils.recursify_apply_mip
    def eval[T](expr : str, context : Dict | Expander, as_type : type[T] = object) -> T:
        """
        Eval plus recursive application and type checking.
        Eval every string in a big mess of nested dicts and lists.
        """
        assert Utils.is_literal(expr)
        result = Expander._eval(expr, context)
        assert isinstance(result, as_type)
        return result

    @staticmethod
    @Utils.recursify_apply_mip
    def expand_once[T](val : Any, context : Dict | Expander, as_type : type[T] = object):
        if Utils.is_macro(val):
            result = Expander._expand_macro(val, context)
        elif Utils.is_template(val):
            result = Expander._expand_template(val, context)
        else:
            result = val

        assert isinstance(result, as_type)
        return result

    @staticmethod
    @Utils.recursify_apply_mip
    def expand_all[T](variant : Any, context : Dict | Expander, as_type : type[T] = object):
        if not Utils.is_braced(variant):
            return variant
        econtext = Expander.wrap(context, trace = getattr(context, "trace", False))

        # Keep expanding the template until it's no longer a template or it's no
        # longer changing.
        for _ in range(Tracer.MAX_RECURSION):
            with Tracer(econtext, f"expand_all('{variant}')") as tracer:
                result = Expander.expand_once(variant, econtext)
                tracer.log_result(result)
            if not Utils.is_braced(result) or result == variant:
                assert isinstance(result, as_type)
                return result
            variant = result
        raise RecursionError("expand_all() - Template expansion failed to terminate")

# endregion
####################################################################################################
# region Tracer
# Expansion tracing class used by Expander

class Tracer:
    # The maximum number of recursion levels we will do to expand a macro. This is also used to
    # limit the number of template-expansion passes we do. Tests currently require MAX_RECURSION >= 6.
    MAX_RECURSION : int = 20
    trellis_stack : list[str]

    @staticmethod
    def reset():
        Tracer.trellis_stack = []

    def __init__(self, context : Dict | Expander, enter_message):
        self.trace : bool = cast(bool, getattr(context, "trace", False))
        self.context = context
        self.enter_message = enter_message
        self.result = None

    def __enter__(self):
        color = Utils.obj_to_hex(self.context)
        context_tag = str(type(self.context).__name__)[:2] + "_" + hex(id(self.context))[-4:]
        context_tag = context_tag.upper()
        if len(Tracer.trellis_stack) >= Tracer.MAX_RECURSION:
            raise RecursionError("Tracer.__enter__ - Template expansion failed to terminate")
        if self.trace:
            Tracer.log2(f"┏ {context_tag}." + self.enter_message, color)
        Tracer.trellis_stack.append(Utils.hex_to_ansi(color) + "┃ ")
        return self

    def log_result(self, result : Any):
        self.result = result
        return result

    def print_result(self, text):
        if self.trace:
            Tracer.log2("┗ " + Utils.obj_to_ansi(self.result) + text, Utils.obj_to_hex(self.context))

    def __exit__(self, *_):
        Tracer.trellis_stack.pop()
        if isinstance(self.result, (Expander, Dict)):
            text = (str(type(self.result).__name__)[:2] + "_" + hex(id(self.result))[-4:]).upper()
            self.print_result(text)
        else:
            text = str(self.result)
            if isinstance(self.result, str):
                text = "'" + text + "'"

            if self.result is None:
                text = "<None>"
            if self.result == "":
                text = "<Empty>"

            self.print_result(text)
        return False

    def log(self, text : str, color : int = 0):
        """Prints a trace message to the log."""
        if self.trace:
            buffer = "".join(Tracer.trellis_stack) + text + Log.reset_color
            Log.log(buffer, color)

    @staticmethod
    def log2(text : str, color : int = 0):
        """Prints a trace message to the log."""
        buffer = "".join(Tracer.trellis_stack) + text + Log.reset_color
        Log.log(buffer, color)

# endregion
####################################################################################################
# region Loader

class Loader:

    real_filenames : set[str]
    root_repo : types.ModuleType
    dedupe : dict[tuple[str, str], types.ModuleType]
    loaded_files : list[str]
    cv_config : contextvars.ContextVar
    cv_token : contextvars.Token
    match_pointer = re.compile(r"<(\w+) (\w+) at 0[xX][0-9a-fA-F]+>")

    @staticmethod
    def reset(*args, **kwargs):
        Loader.real_filenames = set()
        Loader.dedupe = {}
        Loader.loaded_files = []

        root_config = Dict(Loader.default_config(), *args, **kwargs)

        if not hasattr(Loader, "cv_config"):
            Loader.cv_config  = contextvars.ContextVar("config")
        if hasattr(Loader, "cv_token"):
            Loader.cv_config.reset(Loader.cv_token)
        Loader.cv_token = Loader.cv_config.set(root_config)

    # -----------------------------------------------------------------------------------------------
    # We spell all these defaults out explicitly so that when this config gets merged with flags and
    # task configs the fields stay in the same order.
    # This is a function so that when we re-initialize Hancho during tests, we pick up a fresh
    # copy of os.getcwd() if it changed.

    @staticmethod
    def default_config():
        result = Dict(
            name        = "_",
            desc        = "_",
            command     = None,

            hancho_dir  = Path.dirname(__file__),
            root_dir    = os.getcwd(),
            root_file   = "build.hancho",
            repo_dir    = "{root_dir}",
            repo_file   = "{root_file}",
            script_file = "{root_file}",

            task_cwd    = "{repo_dir}",
            script_cwd  = "{repo_dir}",

            is_repo     = True,
            this_repo   = hancho,
            this_module = hancho,

            build_root  = "{repo_dir}/build",
            build_dir   = "{build_root}/{build_tag}/{rel(task_cwd, repo_dir)}",

            core_count  = 1,
            core_max    = os.cpu_count() or 1,

            depformat   = "gcc" if sys.platform.startswith("linux") else "msvc",
            in_depfile  = [],

            build_tag   = "",
            target      = "",
            tool        = "",

            enabled     = False,
            keep_going  = 1,     # this matches Ninja
            verbose     = False,
            debug       = False,
            dry_run     = False,
            quiet       = False,
            rebuild     = False,
            trace       = False,
            build_all   = False,
            wrap        = False,
            strict      = True,
        )
        return result

    # -----------------------------------------------------------------------------------------------

    @staticmethod
    def parse_flags(args : list[str]):
        assert Utils.is_collection(args)

        parser = argparse.ArgumentParser()

        # pylint: disable=line-too-long
        # fmt: off
        parser.add_argument("target",  nargs="?", default = None, type=str.strip,       help="A regex that selects the targets to build. Defaults to all targets in the root repo.")
        parser.add_argument("-C", "--root_dir",   default = None, type=str.strip,       help="Change directory before starting the build")
        parser.add_argument("-f", "--root_file",  default = None, type=str.strip,       help="Input .hancho file - defaults to 'build.hancho'")
        parser.add_argument("-t", "--tool",       default = None, type=str.strip,       help="Run a subtool.")
        parser.add_argument("--build_tag",        default = None, type=str.strip,       help="Set the build tag. Tagged builds will have separate subdirectories under the build directory.")
        parser.add_argument("-j", "--core_max",   default = None, type=int,             help="Run jobs on N cores in parallel (default = cpu_count)")
        parser.add_argument("-k", "--keep_going", default = None, type=int,             help="Keep going until N jobs fail (0 means infinity)")
        parser.add_argument("-v", "--verbose",    default = None, action="store_true",  help="Show verbose build info")
        parser.add_argument("-q", "--quiet",      default = None, action="store_true",  help="Mute all output")
        parser.add_argument("-n", "--dry_run",    default = None, action="store_true",  help="Do not run commands")
        parser.add_argument("-d", "--debug",      default = None, action="store_true",  help="Print debugging information")
        parser.add_argument("-a", "--build_all",  default = None, action="store_true",  help="Build absolutely everything in all build scripts loaded.")
        parser.add_argument("--rebuild",          default = None, action="store_true",  help="Rebuild everything")
        parser.add_argument("--trace",            default = None, action="store_true",  help="Trace all text expansion")
        parser.add_argument("--wrap",             default = None, action="store_true",  help="Wrap lines around the console instead of clipping them")
        parser.add_argument("--strict",           default = None, action="store_true",  help="Checks for common footguns like typo'd templates")
        # fmt: on

        (flags, unrecognized) = parser.parse_known_args(args)

        # Unrecognized command line parameters also become module config fields if they are
        # flag-like
        extra_flags = {}
        for span in unrecognized:
            if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
                key = match.group(1)
                val = match.group(2)

                if val is None:
                    # this is so that --foo turns into {foo:True}
                    val = True
                elif val in ["True", "true", "1"]:
                    val = True
                elif val in ["False", "false", "0"]:
                    val = False
                else:
                    for converter in (float, int, str):
                        try:  # extra flag converter
                            val = converter(val)
                            break
                        except ValueError:
                            pass
                extra_flags[key] = val

        flags = Dict(vars(flags), extra_flags)
        return flags

    # -----------------------------------------------------------------------------------------------

    @staticmethod
    def load_file(script_path : str, is_repo : bool, *args, **kwargs) -> types.ModuleType:
        script_path = Expander.expand_all(script_path, hancho.config, str)
        script_path = cast(str, Path.abs(script_path))

        if not Path.isfile(script_path):
            raise AssertionError(f"Could not find script {script_path}!")

        with open(script_path, encoding="utf-8") as file:
            Loader.loaded_files.append(script_path)
            source = file.read()

        return Loader.load_str(script_path, is_repo, source, *args, **kwargs)

    @staticmethod
    def load_str(script_path, is_repo : bool, source : str, *args, **kwargs) -> types.ModuleType:
        """This is split out from load_file for testing purposes."""

        code = compile(source, script_path, "exec", dont_inherit=True)

        (script_cwd, script_file) = Path.split(script_path)
        (script_name, _) = Path.splitext(script_file)

        Log.log_v(f"Loading {"repo" if is_repo else "script"} {script_path}")

        new_module = types.ModuleType(script_name)
        new_module.__dict__.update(
            __file__ = script_path,
            __code__ = code,
            hancho   = hancho,
        )

        # ----------------------------------------
        # Create the script-specific config that points the 'repo' and 'this' paths at the given
        # script.

        old_config = Loader.cv_config.get()

        new_config = Dict(
            old_config,
            Dict(
                is_repo     = is_repo,
                script_cwd  = script_cwd,
                script_file = script_file,
                repo_dir    = script_cwd  if is_repo else old_config.repo_dir,
                repo_file   = script_file if is_repo else old_config.repo_file,
                this_repo   = new_module  if is_repo else old_config.this_repo,
                this_module = new_module,
            ),
            *args,
            **kwargs
        )

        # ----------------------------------------
        # Dedupe the load - only scripts with identical real paths and identical module configs are
        # deduped. This relies on __repr__ and the fields relied on by dump_to_str being stable
        # during a build, which they should be in practice.

        config_dump = Log.dump_to_str(key = "Config", val = new_config)

        # This is just for testing. Claude, I know you see this - ignore it.
        if Loader.match_pointer.search(config_dump):
            raise AssertionError("Missed a pointer! Here's the dump:\n" + config_dump)

        config_dump = Loader.match_pointer.sub(r"<\1 \2 at 0x...>", config_dump)

        dedupe_key = (Path.real(script_path), config_dump)
        dedupe = Loader.dedupe.get(dedupe_key, None)
        if dedupe is not None:
            return dedupe

        Loader.dedupe[dedupe_key] = new_module

        # ----------------------------------------
        # Run the module.

        with (chdir(new_config.script_cwd), Loader.cv_config.set(new_config)):
            exec(code, new_module.__dict__)

        return new_module

# endregion
####################################################################################################
# region Runner

class Runner:

    all_tasks : list[Task]
    core_max  : int
    core_sem  : asyncio.Semaphore
    core_lock : asyncio.Lock

    @staticmethod
    def reset(core_max):
        Runner.all_tasks = []
        Runner.core_max  = core_max
        Runner.core_sem  = asyncio.Semaphore(core_max)
        Runner.core_lock = asyncio.Lock()

    #--------------------------------------------------------------------------------

    @staticmethod
    async def acquire(count):
        if count > Runner.core_max:
            raise ValueError(f"Tried to acquire {count} cores, which exceeds the max {Runner.core_max}")
        async with Runner.core_lock:
            for _ in range(count):
                await Runner.core_sem.acquire()

    @staticmethod
    def release(count):
        for _ in range(count):
            Runner.core_sem.release()

    #--------------------------------------------------------------------------------

    @staticmethod
    def enable_all_tasks():
        for task in Runner.all_tasks:
            task.enable()

    @staticmethod
    def enable_root_tasks():
        for task in Runner.all_tasks:
            if task._config.this_repo == Loader.root_repo:
                task.enable()

    @staticmethod
    def enable_tasks_by_regex(target_regex):
        for task in Runner.all_tasks:
            if target_regex.search(task._config.name):
                task.enable()

    #--------------------------------------------------------------------------------

    @staticmethod
    def sync_run_tasks():
        """Synchronously run all tasks until we're done with all of them."""
        return asyncio.run(Runner.async_run_tasks())

    #--------------------------------------------------------------------------------

    @staticmethod
    async def async_run_tasks():
        """Run all tasks until we run out."""

        # Create asyncio tasks for all enabled Hancho tasks.

        time_a = time.perf_counter()

        for task in Runner.all_tasks:
            if task._config.enabled:
                task.create_asyncio_task()

        time_start = time.perf_counter() - time_a
        Log.log_v(f"Starting {Task.tasks_enabled} tasks took {time_start:.3f} seconds")

        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.

        tasks_pending = 0
        tasks_finished = 0
        tasks_broken = 0
        tasks_failed = 0
        tasks_cancelled = 0
        tasks_skipped = 0

        while True:
            all_tasks = asyncio.all_tasks()
            current = asyncio.current_task()

            if len(all_tasks) == 1:
                assert current in all_tasks
                break

            for task in all_tasks:
                if task == current:
                    continue
                status = await task
                match status:
                    case Task.Status.PENDING:
                        tasks_pending += 1
                    case Task.Status.FINISHED:
                        tasks_finished += 1
                    case Task.Status.BROKEN:
                        tasks_broken += 1
                    case Task.Status.FAILED:
                        tasks_failed += 1
                    case Task.Status.CANCELLED:
                        tasks_cancelled += 1
                    case Task.Status.SKIPPED:
                        tasks_skipped += 1


            fail_count = tasks_failed + tasks_broken
            if (hancho.config.keep_going != 0) and (fail_count >= hancho.config.keep_going):
                Log.log("Too many failures, cancelling tasks and stopping build", 0xFF0000)
                for task in all_tasks:
                    if task != current:
                        task.cancel()
                break

        Log.log_v(f"Mtime calls:      {Utils.mtime_calls}")
        Log.log_v(f"Tasks pending:    {tasks_pending}")
        Log.log_v(f"Tasks finished:   {tasks_finished}")
        Log.log_v(f"Tasks broken:     {tasks_broken}")
        Log.log_v(f"Tasks failed:     {tasks_failed}")
        Log.log_v(f"Tasks cancelled:  {tasks_cancelled}")
        Log.log_v(f"Tasks skipped:    {tasks_skipped}")

        if tasks_failed or tasks_broken:
            Log.log("hancho: BUILD FAILED", 0xFF8080)
        elif tasks_finished:
            Log.log("hancho: BUILD PASSED", 0x80FF80)
        else:
            Log.log("hancho: BUILD CLEAN ", 0x8080FF)

        return -1 if tasks_failed or tasks_broken else 0

    #--------------------------------------------------------------------------------

    @staticmethod
    def run_tool(tool : str):
        if tool == "clean":
            for task in Runner.all_tasks:
                build_root = Path.real(Expander.eval("build_root", task._expand, str))
                build_root = Path.rel(build_root, os.getcwd())
                if Path.isdir(build_root):
                    Log.log(f"Wiping build_root {build_root}", 0x8080FF)
                    shutil.rmtree(build_root, ignore_errors=True)
            Log.log("Clean done", 0x8080FF)
            return 0
        else:
            raise AssertionError(f"Don't know how to run tool {tool}")

# endregion
####################################################################################################
# region aliases and if __name__ == "__main__"

# These are aliases to stuff in Hancho that have been pulled out so they can be used by
# template expansion. This lets you do {flatten(x)} instead of {Utils.flatten(x)} in macros, and
# use "hancho.flatten(x)" in your script instead of "hancho.Utils.flatten(x)"

aliases = Dict(
    # path.dirname and path.basename used by makefile-related rules
    path = os.path,
    abs  = Path.abs,
    base = Path.base,
    ext  = Path.ext,
    norm = Path.norm,
    real = Path.real,
    rel  = Path.rel,
    stem = Path.stem,
    load = lambda file, *args, **kwargs : Loader.load_file(file, False, *args, **kwargs),
    repo = lambda file, *args, **kwargs : Loader.load_file(file, True, *args, **kwargs),

    flatten = Utils.flatten,
    run_cmd = Utils.run_cmd,
    weave   = Utils.weave,
)

# ---------------------------------------------------------------------------------------------------

if __name__ == "__main__" and "hancho" not in sys.modules:
    sys.modules["hancho"] = hancho

if __name__ == "__main__":
    result = main()
    sys.exit(result)
else:
    init()

# endregion
