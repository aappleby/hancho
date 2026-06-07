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
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
import types
from collections import ChainMap, abc
from contextlib import chdir, contextmanager, suppress
from enum import Enum
from inspect import isawaitable
from typing import Any, cast

hancho = sys.modules[__name__]
sys.modules["hancho"] = hancho

# Config fields often have arbitrarily nested lists of stuff due to things like
#
#     obj1 = [foo.o, bar.o]
#     link(in_objs = [objs1, ...])
#
# and so we define a 'Tree' type that is basically 'either a T, or arbitrarily nested list of T'
# This is only used as a type annotation, but be aware when reading the functions below that
# some of them look like they operate on Ts, but they've been 'recursified' to work on Tree[T]s.

type Tree[T] = T | list[Tree[T]]

# endregion
####################################################################################################
# region Log

class Log:

    @classmethod
    def reset(cls, priority):
        Log.start  : float = time.time( )
        Log.priority  : int  = priority
        Log.buffer : str  = ""
        Log.con_w  : int  = shutil.get_terminal_size().columns
        Log.dirty  : bool = False
        Log.indent_depth : int  = 0
        Log.wrap   : bool = False
        Log.current_color  : int  = -1
        Log.at_newline = True
        Log.line_buffer = ""
        Log.max_one_newline = re.compile(r"[^\n]*\n?$")

    match_escapes = re.compile(r"(\x1B.*?m)")

    INFO     =  0
    DEBUG    = 10
    VERBOSE  = 20
    NORMAL   = 30
    WARNING  = 40
    ERROR    = 50
    CRITICAL = 60
    FATAL    = 70
    QUIET    = 100

    # ----------------------------------------------------------------------------------------------

    @staticmethod
    @contextmanager
    def color(new_color):
        old_color = Log.current_color
        Log.set_color(new_color)
        yield
        Log.set_color(old_color)

    @staticmethod
    @contextmanager
    def indent():
        Log.indent_depth += 1
        yield
        Log.indent_depth -= 1

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def _emit(cls, text):
        if not text:
            return

        hex = cls.current_color
        r, g, b = ((hex >> 16) & 0xFF, (hex >>  8) & 0xFF, (hex >>  0) & 0xFF)
        prefix = f"\x1B[38;2;{r};{g};{b}m" if hex >= 0 else "\x1B[0m"

        text = prefix + text

        cls.line_buffer += text
        Log.buffer += text

        if text[-1] == "\n":
            line = cls.line_buffer
            cls.line_buffer = ""
            if not Log.wrap:
                line = Log.clip_printable(line, Log.con_w)
            sys.stdout.write(line)
            sys.stdout.flush()
            Log.at_newline = True
        else:
            Log.at_newline = False

    @classmethod
    def _log_at(cls, priority : int, text: str):
        if not text:
            return
        if priority < Log.priority:
            return

        assert Log.max_one_newline.match(text)

        if Log.at_newline:
            prefix  = f"[{time.time() - Log.start:12.6f}] "
            prefix += "│ " * Log.indent_depth

            with Log.color(-1):
                Log._emit(prefix)

        Log._emit(text)

        if priority == Log.FATAL:
            sys.exit(-1)

    @classmethod
    def log_at(cls, priority, text):
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if i > 0:
                Log._log_at(priority, '\n')
            Log._log_at(priority, line)

    @classmethod
    def log(cls, message : str): Log.log_at(Log.NORMAL, message)

    @classmethod
    def log_i(cls, message : str): Log.log_at(Log.INFO, message)

    @classmethod
    def log_d(cls, message : str): Log.log_at(Log.DEBUG, message)

    @classmethod
    def log_v(cls, message : str): Log.log_at(Log.VERBOSE, message)

    @classmethod
    def log_n(cls, message : str): Log.log_at(Log.NORMAL, message)

    @classmethod
    def log_w(cls, message : str): Log.log_at(Log.WARNING, message)

    @classmethod
    def log_e(cls, message : str): Log.log_at(Log.ERROR, message)

    @classmethod
    def log_c(cls, message : str): Log.log_at(Log.CRITICAL, message)

    @classmethod
    def log_fatal(cls, message): Log.log_at(Log.FATAL, message)

    @classmethod
    def set_color(cls, hex):
        Log.current_color = hex

    @classmethod
    def clip_printable(cls, text, width):
        """
        Clips a string with embedded escape codes (such as ANSI color codes) so that it fits in
        'width' without breaking the escape codes.

        If the printable portion exceeds 'width', it will be clipped and capped with '...'.
        """
        if not text:
            return text

        newline = text[-1] == '\n'
        if newline:
            text = text[:-1]

        # Split the text using the escape sequences as separators.
        # Even chunks are printable text, odd chunks are escape sequences.
        chunks = Log.match_escapes.split(text)

        accum = 0
        result = ""
        for i, chunk in enumerate(chunks):
            if i & 1:
                result += chunk
            else:
                accum += len(chunk)
                if accum > width - 3:
                    chunk = chunk[:(width - 3) - accum]
                result += chunk

        if accum > width:
            result += "..."
        if newline:
            result += '\n'

        return result

#endregion
####################################################################################################
#region

class Colors(int, Enum):
    # 12 half-saturated, 80% value colors evenly spaced around the HSV wheel
    RED     = 0xCC6666
    PINK    = 0xCC6699
    MAGENTA = 0xCC66CC
    VIOLET  = 0x9966CC
    BLUE    = 0x6666CC
    SKY     = 0x6699CC
    TEAL    = 0x66CCCC
    AQUA    = 0x66CC99
    GREEN   = 0x66CC66
    LIME    = 0x99CC66
    YELLOW  = 0xCCCC66
    ORANGE  = 0xCC9966
    # And the "go back to default" color :D
    RESET   = -1

# endregion
####################################################################################################
# region Utils

class Utils:

    @classmethod
    def reset(cls):
        cls.rand : random.Random = random.Random()
        cls.mtime_calls : int = 0

    @classmethod
    def dump_to_str(cls, key, val, indent = 0, print_id = False, max_width = 80, tab = "  ", flat = False):
        """
        Hancho's pretty-printer for various types. Note that this is also used for script deduping:
        if you load "my/app/tools/stuff.hancho" multiple times but the configurations you gave it
        were identical, you should get one copy of the "stuff" module instead of two.
        Changing the way things are pretty-printed will _not_ break the deduper,
        """

        # In "key : type = ", don't print these types.
        basic_types = (str, bool, int, float, list, tuple, set, bytes, bytearray, range, type(None))

        # Generate the "key : type = " prefix.
        prefix = ""
        if key is not None:
            prefix += str(key) + " "
        if not isinstance(val, basic_types):
            prefix += ": " + type(val).__name__ + " "
        if print_id:
            prefix += ": " + hex(id(val)) + " "
        if prefix:
            prefix += "= "

        # Don't recurse into a few types that need special handling
        if isinstance(val, Task):
            val = f"<Task {val._config.name}>"
        elif isinstance(val, Expander):
            val = "<Expander>"
        elif isinstance(val, contextvars.Context):
            #val = list(val.keys())
            val = "<Context>"
        elif isinstance(val, types.ModuleType):
            val = f"<Module {val.__name__}>"

        if isinstance(val, argparse.Namespace):
            val = val.__dict__
            pass

        # Non-containers are always emitted on one line. If they overflow, they overflow.
        if not (Utils.is_collection(val) or Utils.is_mapping(val)):
            # Objects that don't have a custom repr (and a few built-in types) just get printed as
            # '<object>'
            if type(val).__repr__ is object.__repr__ or type(val) in [
                types.FunctionType,
                types.BuiltinFunctionType,
                types.ModuleType,
                types.GeneratorType,
                types.LambdaType,
            ]:
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
            chunk = Utils.dump_to_str(k, v, 0, print_id, max_width, tab, True)
            if chunk is None or width + len(chunk) > max_width:
                if flat:
                    return None
                separator = ",\n"
                chunks = (Utils.dump_to_str(k, v, indent + 1, print_id, max_width, tab, False) for k, v in items)
                return pad + prefix + ld + "\n" + separator.join(chunks) + "\n" + pad + rd
            width += len(chunk)
            chunks.append(chunk)

        # Done, we can fit this dump on one line.
        return pad + prefix + ld + separator.join(chunks) + rd

    #----------------------------------------
    # Yes Claude, I know these recursify functions are weird and probably need better names.

    @staticmethod
    def recursify_all(func: abc.Callable[..., bool]):
        """
        Creates a function that recursively checks if 'func' is True for all fields of a Tree[T].
        """

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

        @staticmethod
        def inner(accum, a, b, *args, **kwargs):
            if Utils.is_collection(a):
                for c in a:
                    inner(accum, c, b, *args, **kwargs)
            elif Utils.is_collection(b):
                for c in b:
                    inner(accum, a, c, *args, **kwargs)
            else:
                accum.append(func(a, b, *args, **kwargs))

        @staticmethod
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
    def is_mapping(variant : Any) -> bool:
        return isinstance(variant, abc.Mapping)

    #----------------------------------------
    # Checks if a string needs template expansion. Empty strings are considered literals.

    # Matches non-escaped _innermost_ brace pairs
    braced = re.compile(r"(?<!\\)\{(?:\\.|[^\\{}])*\}")

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
    def obj_to_hex(obj) -> int:
        Utils.rand.seed(id(obj))
        r, g, b = colorsys.hsv_to_rgb(Utils.rand.random(), 0.3, 1.0)
        r, g, b = (int(r * 255), int(g * 255), int(b * 255))
        return (r << 16) | (g << 8) | (b << 0)

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
    def flatten(variant: Tree[Any]) -> list[Any]:
        noflat_types = (str, bytes, bytearray, abc.Mapping)

        if isinstance(variant, noflat_types) or not isinstance(variant, abc.Iterable):
            return [] if variant is None else [variant]
        else:
            return [x for element in variant for x in Utils.flatten(element)]


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
    def rel(path1, path2): return path1.removeprefix(path2 + "/") if path1 != path2 else "."

    @staticmethod
    @Utils.recursify_pairwise_map
    def join(lhs, rhs): return os.path.join(lhs, rhs)

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
        return Utils.dump_to_str(key = getattr(self, "name", "_"), val = self)

    #----------------------------------------
    # Expander convenience helpers

    def expand[T](self, text : Any, as_type : type[T] = object) -> T:
        result = Expander._expand(text, self)
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

    @classmethod
    def reset(cls):
        cls.id_counter : int = 0
        cls.tasks_enabled : int = 0

    class FAILED(Exception):    pass  # noqa: E701
    class CANCELLED(Exception): pass  # noqa: E701
    class SKIPPED(Exception):   pass  # noqa: E701
    class BROKEN(Exception):    pass  # noqa: E701

    def __init__(self, *args, **kwargs):
        # Save the context, we will use it when we create the asyncio.Task
        self._context = contextvars.copy_context()
        self._config  = Dict(hancho.config, *args, **kwargs)
        self._expand  = Expander.wrap(self._config)

        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._aio_task : asyncio.Task | None = None

        self._error : BaseException | None = None

        # Tasks depend on all .hancho files that were loaded when the task was created.
        # This is probably too wide a net, but tracking dependencies between .hancho files is not
        # really possible.
        self._loaded_files : list[str] = list(Loader.loaded_files)

        # Bookkeeping stuff
        self._task_id : int = 0
        self._stdout : str = ""
        self._stderr : str = ""

        self._core_count = 0

        self._in_files  = []
        self._out_files = []

        Runner.all_tasks.append(self)

        if Utils.in_event_loop():
            self.enable()

    # ----------------------------------------------------------------------------------------------
    # WARNING: Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.

    def __copy__(self):
        raise AssertionError("Don't copy Tasks!")

    def __deepcopy__(self, _):
        raise AssertionError("Don't copy Tasks!")

    def __repr__(self):
        return Utils.dump_to_str(key = "Task", val = self)

    # ----------------------------------------------------------------------------------------------

    @staticmethod
    def is_depfile_field(key : str, val : Any = None) -> bool:
        return key == "in_depfile"

    @staticmethod
    def is_output_field(key : str, val : Any = None):
        return key and (Task.is_depfile_field(key) or key.startswith("out_"))

    @staticmethod
    def is_input_field(key : str, val : Any = None):
        return key and key.startswith("in_")

    @staticmethod
    def is_io_field(key : str, val : Any = None):
        return Task.is_input_field(key) or Task.is_output_field(key)

    # ----------------------------------------------------------------------------------------------

    def _log(self, message : str):
        with Log.color(Colors.LIME):
            Log.log(f"[{self._task_id:3d}/{Task.tasks_enabled:3d}] ")
        Log.log(message)

    def _log_d(self, message : str):
        if self._config.debug:
            self._log(message)

    def _log_v(self, message : str):
        if self._config.verbose or self._config.debug:
            self._log(message)

    # ----------------------------------------------------------------------------------------------

    def enable(self):
        if not self._config.enabled:
            self._config.enabled = True
            Task.tasks_enabled += 1
            if Utils.in_event_loop():
                self.create_aio_task()

    @Utils.recursify_apply_mip_member
    def create_parent_tasks(self, v):
        if isinstance(v, Task):
            v.create_aio_task()
        return v

    def create_aio_task(self):
        assert Utils.in_event_loop()

        if self._aio_task is None:
            t = asyncio.create_task(self.task_top(), context=self._context)
            Runner.live_aio_tasks.add(t)
            t.add_done_callback(lambda t: Runner.aio_done_queue.put_nowait(t))
            self._aio_task = t

        # Start all tasks referenced by _config so we don't deadlock while waiting for them.
        self.create_parent_tasks(self._config)

    # ----------------------------------------------------------------------------------------------

    async def task_top(self):
        try:
            await self.task_main()
        except asyncio.CancelledError as ex:
            self._log_v(f"<asyncio.CancelledError {ex}>\n")
            self._error = ex
            raise
        except Task.BROKEN as ex:
            self.log_error("Task broken!", "<exception>", ex)
            self._error = ex
        except Task.FAILED as ex:
            self.log_error("Task failed!", "<exception>", ex)
            self._error = ex
        except Task.CANCELLED as ex:
            self._log_v(str(ex) + "\n")
            self._error = ex
        except Task.SKIPPED as ex:
            self._log_v(str(ex) + "\n")
            self._error = ex
        except Exception as ex:
            self.log_error("Task threw an exception!", type(ex), ex)
            self._error = ex
        finally:
            if self._core_count:
                Runner.release(self._core_count)
                self._core_count = 0

        if self._error:
            raise self._error

        dry_run = "(DRY RUN)" if self._config.dry_run else ""
        self._log_v(f"Task done {dry_run}: '{self._config.name}' - '{self._config.desc}'\n")
        return self._out_files

    # ----------------------------------------------------------------------------------------------

    async def task_main(self):
        config = self._config
        expand = self._expand

        #if expand.debug:
        #    self.log("Task config before expand:", 0xFFFFFF)
        #    for line in str(config).split("\n"):
        #        self.log(line, 0xFFFFFF)

        # ----------------------------------------
        # Expand all fields that don't depend on input/output filenames (basically everything
        # except name/desc/command)

        path_fields  = ["hancho_dir", "task_cwd", "root_dir", "root_file", "repo_dir", "repo_file",
                        "script_cwd", "script_file", "build_root", "build_dir"]

        flag_fields  = ["core_count", "core_max", "depformat", "build_tag", "target", "tool",
                        "max_errors", "verbose", "debug", "dry_run", "quiet", "rebuild",
                        "trace"]

        for f in path_fields:
            if f in config:
                config[f] = Path.norm(expand[f])
        for f in flag_fields:
            if f in config:
                config[f] = expand[f]

        # ----------------------------------------
        # Flatten the commands and check that they're valid

        if not config.command:
            raise Task.BROKEN(f"Task {config.name} has no command!")

        config.command = Utils.flatten(config.command)
        for command in config.command:
            if type(command) is not type(config.command[0]):
                raise Task.BROKEN(f"Commands aren't the same type: {config.command}")

        # ----------------------------------------
        # Check for missing paths

        if not Path.exists(config.task_cwd):
            raise Task.BROKEN(f"Task working directory '{config.task_cwd}' does not exist")

        if not config.build_dir.startswith(config.repo_dir):
            raise Task.BROKEN(f"Build_dir {config.build_dir} is not under repo dir {config.repo_dir}")

        # ----------------------------------------
        # Await all tasks in our input fields and then flatten them.

        for key, files in [i for i in config.items() if Task.is_input_field(*i)]:
            files = Utils.flatten(files)

            if key == "in_depfile" and len(files) > 1:
                raise Task.BROKEN("Tasks can't have more than one dependency file!")

            for i, file in enumerate(files):
                if isinstance(file, Task):
                    task = cast(Task, file)
                    try:
                        await cast(asyncio.Task, task._aio_task)
                    except Exception as err:
                        raise Task.CANCELLED(f"Task is cancelled: '{config.name}' : '{config.desc}'") from err

                    files[i] = task._out_files

            # Awaiting inputs has probably un-flattened our input fields. Re-flatten them.
            config[key] = Utils.flatten(files)

        # ----------------------------------------
        # Now that all our inputs are ready, grab a _task_id that we'll use in our logging.

        Task.id_counter += 1
        self._task_id = Task.id_counter

        # ----------------------------------------
        # Relative paths are relative to task_cwd if we're running a command, otherwise they're
        # relative to script_cwd if we're calling a callback.

        for key, files in [i for i in config.items() if Task.is_io_field(*i)]:

            # Do all the file path remapping so our commands will work
            files = self.remap_io_field_paths(key, files)

            # and unwrap filenames if they're an array of one element so that scripts expecting
            # join(str, str) to return a str will be happy.
            config[key] = files[0] if len(files) == 1 else files

        # ----------------------------------------
        # Paths are cleaned up, we can expand name/desc/command

        config.name    = expand.name
        config.desc    = expand.desc
        config.command = expand.command

        with Log.color(0xFFFFFF):
            Log.log_i("Task config after expand:")
            for line in str(config).split("\n"):
                Log.log_i(line)

        # Dry runs early out after config expansion
        if config.dry_run:
            return

        # ----------------------------------------
        # Run some sanity checks

        def is_braced(v: Any) -> bool:
            if isinstance(v, list):
                return any(is_braced(v2) for v2 in v)
            return Utils.braced.search(v) is not None if isinstance(v, str) else False

        if config.strict and is_braced(config.command):
            raise Task.BROKEN("We are in strict mode and this task's command has curly braces in it - did you typo a template?")

        # Check for missing inputs
        for file in self._in_files:
            assert Path.isabs(file)
            if not Path.exists(file):
                raise Task.BROKEN(f"Input file missing - {file}")

        # Check that all build files would end up under build_dir
        for file in self._out_files:
            assert Path.isabs(file)
            if not file.startswith(config.build_dir):
                raise Task.BROKEN(f"Path error, output file {file} is not under build_dir {config.build_dir}")

        # Check for task collisions
        for file in self._out_files:
            real_file = cast(str, Path.real(file))
            if real_file in Loader.real_filenames:
                raise Task.BROKEN(f"TaskCollision: Multiple tasks build {real_file}")
            Loader.real_filenames.add(real_file)

        # ----------------------------------------
        # See if we need to rebuild our outputs

        rebuild_reason = self.rebuild_reason()
        if not rebuild_reason:
            raise Task.SKIPPED(f"Task is up-to-date: '{config.name}' : '{config.desc}'")

        # ----------------------------------------
        # Wait for enough jobs to free up to run this task.

        await Runner.acquire(config.core_count)
        self._core_count = config.core_count

        # ----------------------------------------
        # Run all the task's commands

        self._log(f"Task started : '{config.name}' - '{config.desc}'\n")
        self._log_v(f"Task rebuilding because: {rebuild_reason}\n")

        for command in cast(list, config.command):
            if isinstance(command, str):
                await self.run_command(command)

            elif callable(command):
                await self.call_callback(command)
            else:
                raise Task.FAILED(f"Command {command} is not a string or a callable?")

        # Done!

    # ----------------------------------------------------------------------------------------------

    def remap_io_field_paths(self, name, files) -> list[str]:
        """
        Input and output file paths in .hancho scripts are declared relative to the directory the
        script is in (stored in the config under 'script_cwd').
        In general we want to run commands from the root of the repo and store output files in
        repo/build. Additionally, our file paths may be text templates that we need to expand first.
        This function takes care of all of that and a few other things, and tries to do so in a
        robust way. Whether this actually turns out to be robust or not is yet to be determined.
        """

        config = self._config
        expand = self._expand

        # Expand all in_ and out_ filenames.
        # We _must_ expand these first before joining paths or the paths will be incorrect:
        # prefix + swap(abs_path) != abs(prefix + swap(path))
        files = expand.expand(files)

        # Initially, all our file paths are relative to the script_cwd that created this task.
        # Join script_cwd with the filenames to produce absolute paths.
        files = Path.join(config.script_cwd, files)

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
                if not files[i].startswith(config.build_dir):
                    files[i] = files[i].replace(config.task_cwd, config.build_dir)

                if files[i].startswith(config.build_dir):
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
        rel_dir = config.task_cwd if isinstance(config.command[0], str) else config.script_cwd

        for i in range(len(files)):
            files[i] = Path.rel(files[i], rel_dir)

        return files

    # ----------------------------------------------------------------------------------------------

    def rebuild_reason(self) -> str:
        config = self._config
        cwd = os.getcwd()

        if config.rebuild:
            return "Target forced to rebuild"
        if not self._in_files:
            return "Always rebuild a target with no inputs"
        if not self._out_files:
            return "Always rebuild a target with no outputs"

        # Check if any of our output files are missing.
        for file in self._out_files:
            if not Path.exists(file):
                return f"{Path.rel(file, cwd)} is missing"

        # Check if any of our input files are newer than the output files.
        min_out = min(Utils.mtime(f) for f in self._out_files)
        if Utils.mtime(__file__) >= min_out:
            return "hancho.py has changed"

        for file in self._in_files:
            if Utils.mtime(file) >= min_out:
                return f"{Path.rel(file, cwd)} has changed"

        for file in self._loaded_files:
            if Utils.mtime(file) >= min_out:
                return f"{Path.rel(file, cwd)} has changed"

        # Check all dependencies in the C dependencies file, if present.
        if config.in_depfile and Path.exists(config.in_depfile):
            self._log_d(f"Found C dependencies file {config.in_depfile}\n")
            with open(config.in_depfile) as depcontents:
                deplines = None
                if config.depformat == "msvc":
                    # MSVC /sourceDependencies
                    deplines = json.load(depcontents)["Data"]["Includes"]
                elif config.depformat == "gcc":
                    # GCC -MMD
                    deplines = depcontents.read().split()
                    deplines = [d for d in deplines[1:] if d != "\\"]
                else:
                    raise Task.BROKEN(f"Invalid depfile format {config.depformat}")

                # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
                deplines = [cast(str, Path.join(config.task_cwd, d)) for d in deplines]
                for abs_file in deplines:
                    if Utils.mtime(abs_file) >= min_out:
                        return f"Rebuilding because {Path.rel(abs_file, cwd)} has changed"

        # All checks passed; we don't need to rebuild this output.
        return ""

    # ----------------------------------------------------------------------------------------------

    async def run_command(self, command):
        config = self._config
        #print(f"v {self._config.verbose} d {self._config.debug}")
        with Log.color(Colors.BLUE):
            self._log_v(f"{Path.rel(config.task_cwd, config.repo_dir)}$ {command}\n")

        # Create the subprocess via asyncio and then await the result.
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd    = config.task_cwd,
            stdout = asyncio.subprocess.PIPE,
            stderr = asyncio.subprocess.PIPE,
            start_new_session = True
        )
        try:
            (stdout_data, stderr_data) = await proc.communicate()
        except asyncio.CancelledError as err:
            # We don't trust asyncio to clean up all cancelled processes, so we do it the hard way
            # here and kill the whole process group.
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.communicate()
            raise Task.CANCELLED(f"Task is cancelled: '{config.name}' : '{config.desc}'") from err
        except Exception as ex:
            raise Task.FAILED(f"Command threw an exception : {ex}") from ex

        if proc.returncode:
            raise Task.FAILED(f"Command return code was non-zero : {proc.returncode}")

        self._stdout = stdout_data.decode()
        self._stderr = stderr_data.decode()

        if (self._stdout or self._stderr) and (self._config.verbose or self._config.debug):
            self._log_v("========== Stdout ==========\n")
            for line in self._stdout.strip().split("\n"):
                self._log(line + "\n")
            self._log("========== Stderr ==========\n")
            for line in self._stderr.strip().split("\n"):
                self._log(line + "\n")
            self._log("============================\n")

        return proc.returncode

    # ----------------------------------------------------------------------------------------------

    async def call_callback(self, command):
        callback_dir = Path.rel(self._config.script_cwd, self._config.repo_dir)
        self._log_v(f"{callback_dir}$ {command}\n")

        try:
            with chdir(self._config.script_cwd):
                result = command(self)
                if isawaitable(result):
                    result = await result
        except Exception as err:
            self.log_error("Callback threw an exception!", type(err), err)
            raise err

        return result

    # ----------------------------------------------------------------------------------------------

    def log_error(self, type, reason, ex = None):
        script_path = Path.join(self._config.script_cwd, self._config.script_file)

        with Log.color(Colors.RED):
            self._log(type + "\n")
            self._log(f"From {script_path}:\n")
            self._log(f"    Task       = '{self._config.name}' : '{self._config.desc}'\n")
            self._log(f"    time       = {time.perf_counter()}\n")
            self._log(f"    os.getcwd  = {os.getcwd()}\n")
            self._log(f"    command    = {self._config.command}\n")
            self._log(f"    reason     = '{reason}'\n")
            self._log(f"    except     = '{ex}'\n")

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
# The depth checks are to prevent recursive runaway - the MAX_DEPTH limit is arbitrary but should
# suffice.
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

    #----------------------------------------

    def __init__(self, context : Dict, trace : bool | None = None):
        assert trace is None or isinstance(trace, bool)
        # These are just type annotations, because writing to fields while we're in the constructor
        # of a class that overrides __setattr__ does strange things.
        self._context : Dict
        self.trace : bool

        # The actual seet is here.
        super().__setattr__("_context", context)

        if trace is None:
            trace = getattr(context, "trace", hancho.config.trace)
        super().__setattr__("trace", trace)

    #----------------------------------------

    @classmethod
    def reset(cls):
        # The maximum recursion depth we will do to expand a macro.
        # Tests currently require MAX_DEPTHS >= 6.
        cls.MAX_DEPTH = 20
        cls.depth = 0

        # These are aliases to stuff in Hancho that have been pulled out so they can be used by
        # template expansion. This lets you do {flatten(x)} instead of {Utils.flatten(x)} in macros,
        # and use "hancho.flatten(x)" in your script instead of "hancho.Utils.flatten(x)"
        cls.aliases = Dict(
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

    @staticmethod
    def track_depth(func):
        def wrapper(*args, **kwargs):
            if Expander.depth >= Expander.MAX_DEPTH:
                raise RecursionError("Template expansion failed to terminate")
            try:
                Expander.depth += 1
                return func(*args, **kwargs)
            finally:
                Expander.depth -= 1
        return wrapper

    @staticmethod
    def wrap(source : Dict | Expander, tracer = None) -> Expander:
        trace = getattr(source, "trace", False) if tracer is None else tracer.trace

        if isinstance(source, Expander) and source.trace == trace:
            return source

        if isinstance(source, Expander):
            result = Expander(source._context, tracer)
        elif isinstance(source, Dict):
            result = Expander(source, tracer)
        else:
            raise TypeError("Don't know how to wrap a {type(source)} = {source}")

        #if trace:
        #    tag_a = (str(type(source).__name__)[:2] + "_" + hex(id(source))[-4:]).upper()
        #    tag_b = (str(type(result).__name__)[:2] + "_" + hex(id(result))[-4:]).upper()
        #    #tag_a = Utils.obj_to_ansi(source) + tag_a + Log.reset_color
        #    #tag_b = Utils.obj_to_ansi(result) + tag_b + Log.d
        #    #if tracer:
        #    #    tracer.log("wrap ")
        #    #    tracer.log(tag_a)
        #    #    tracer.log(" -> ")
        #    #    tracer.log(tag_b)

        return result

    #----------------------------------------
    # MutableMapping interface

    def __getitem__(self, key):
        try:
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

    def expand(self, val : Any):
        return Expander._expand(val, self)

    def _get(self, key):
        """
        Reads and expands a field stored in our context. Mappings will be wrapped in an Expander so
        that expansions in nested dicts works correctly.
        """

        result = self._context[key]
        if Utils.is_mapping(result) and not isinstance(result, Expander):
            result = Expander.wrap(result)
        else:
            result = self.expand(result)

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
                    result.append(text[cursor:lbrace])
                result.append(text[lbrace:rbrace+1])
                cursor = rbrace + 1
                lbrace = -1
                rbrace = -1

        if cursor < len(text):
            result.append(text[cursor:])

        return result

    # ----------------------------------------------------------------------------------------------
    # IMPORTANT IMPORTANT IMPORTANT
    # If you can't eval a macro, you return it unchanged. TEFINAE.
    # Template Expansion Failure Is Not An Error.
    # This should be the _only_ try/except block in the expansion code.

    @Utils.recursify_apply_mip
    @track_depth
    @staticmethod
    def _expand(text : Any, context : Dict | Expander) -> str:
        if not isinstance(text, str):
            return text

        match = Utils.braced.search(text)

        if not match:
            return text

        elif match.group() == text:
            with Tracer(context, f"eval_macro({text!r})") as tracer:
                try:
                    _locals = ChainMap(context, Loader.cv_config.get(), Expander.aliases)
                    result = eval(text[1:-1], hancho.__dict__, _locals)
                except RecursionError as err:
                    Log.log_e(f"Recursion error {err}\n")
                    raise err
                except Exception as _:
                    result = text
                tracer.save_result(result)

        else:
            with Tracer(context, f"expand_template({text!r})") as tracer:
                blocks = Expander.split(text)
                for (i, block) in enumerate(blocks):
                    result = Expander._expand(block, context)
                    blocks[i] = Utils.stringify(result)
                result = "".join(blocks)
                tracer.save_result(result)

        if result != text:
            result = Expander._expand(result, context)

        return result

# endregion
####################################################################################################
# region Tracer
# Expansion tracing class used by Expander

class Tracer:

    def __init__(self, context : Dict | Expander, enter_message):
        self.trace : bool = getattr(context, "trace", False) or hancho.config.trace
        self.enter_message = enter_message
        self.color = Utils.obj_to_hex(context)
        self.context = context
        self.result = None

    def __enter__(self):
        if not (self.trace or hancho.config.trace):
            return self

        with Log.color(Utils.obj_to_hex(self.context)):
            Log.log(f"{Tracer.object_to_tag(self.context)}." + self.enter_message + "\n")

        Log.indent_depth += 1

        return self

    def __exit__(self, exc_type, exc_value, tb):
        if not (self.trace or hancho.config.trace):
            return

        with Log.color(self.color):
            if exc_type:
                Log.log(f"exc_type  : {exc_type}\n")
            if exc_value:
                Log.log(f"exc_value : {exc_value}\n")
            if tb:
                summary = traceback.extract_tb(tb)
                filename, line_no, func_name, _ = summary[-1]
                Log.log(f"location  : {filename} line {func_name}@{line_no}\n")

        Log.indent_depth -= 1

        Log.log("└ ")

        if isinstance(self.result, (Expander, Dict)):
            Log.log(f"{Tracer.object_to_tag(self.result)}\n")
            return False

        with Log.color(Utils.obj_to_hex(self.result)):
            if self.result is None:
                Log.log("<None>\n")
            elif self.result == "":
                Log.log("<Empty>\n")
            else:
                Log.log(repr(self.result) + "\n")

        return False

    def save_result(self, result : Any):
        self.result = result

    @staticmethod
    def object_to_tag(obj):
        tag = (str(type(obj).__name__)[:2] + "_" + hex(id(obj))[-4:]).upper()
        return tag

# endregion
####################################################################################################
# region Loader

class Loader:

    @classmethod
    def reset(cls, *args, **kwargs):
        cls.match_pointer : re.Pattern = re.compile(r"<(\w+) (\w+) at 0[xX][0-9a-fA-F]+>")
        cls.real_filenames : set[str] = set()
        cls.dedupe : dict[tuple[str, str], types.ModuleType] = {}
        cls.loaded_files : list[str] = []

        cls.root_repo : types.ModuleType | None = None
        cls.root_config : Dict = Dict(cls.default_config(), *args, **kwargs)

        if not hasattr(cls, "cv_config"):
            cls.cv_config : contextvars.ContextVar = contextvars.ContextVar("config")
        if hasattr(cls, "cv_token"):
            cls.cv_config.reset(cls.cv_token)

        cls.cv_token : contextvars.Token = cls.cv_config.set(cls.root_config)

    # -----------------------------------------------------------------------------------------------
    # We spell all these defaults out explicitly so that when this config gets merged with flags and
    # task configs the fields stay in the same order.
    # This is a function so that when we re-initialize Hancho during tests, we pick up a fresh
    # copy of os.getcwd() if it changed.

    @classmethod
    def default_config(cls):
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
            max_errors  = 0,
            verbose     = False,
            debug       = False,
            dry_run     = False,
            quiet       = False,
            trace       = False,
            rebuild     = False,
            wrap        = False,
            strict      = True,
            scroll      = False,
        )
        return result

    # -----------------------------------------------------------------------------------------------

    @classmethod
    def parse_flags(cls, args : list[str]):
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
        parser.add_argument("--max_errors",       default = None, type=int,             help="The maximum number of task errors we tolerate before abandoning the build")

        parser.add_argument("-v", "--verbose",   action = argparse.BooleanOptionalAction, help="Show verbose build info")
        parser.add_argument("-q", "--quiet",     action = argparse.BooleanOptionalAction, help="Mute all output")
        parser.add_argument("-n", "--dry_run",   action = argparse.BooleanOptionalAction, help="Do not run commands")
        parser.add_argument("-d", "--debug",     action = argparse.BooleanOptionalAction, help="Print debugging information")
        parser.add_argument("-a", "--rebuild",   action = argparse.BooleanOptionalAction, help="Build absolutely everything in all build scripts loaded.")
        parser.add_argument("--trace",           action = argparse.BooleanOptionalAction, help="Trace all text expansion")
        parser.add_argument("--wrap",            action = argparse.BooleanOptionalAction, help="Wrap lines around the console instead of clipping them")
        parser.add_argument("--strict",          action = argparse.BooleanOptionalAction, help="Checks for common footguns like typo'd templates")
        parser.add_argument("--scroll",          action = argparse.BooleanOptionalAction, help="Makes the output scroll instead of keeping it on one line like Ninja.")
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
                elif val in ["True", "True", "1"]:
                    val = True
                elif val in ["False", "False", "0"]:
                    val = False
                else:
                    for converter in (float, int, str):
                        try:
                            val = converter(val)
                            break
                        except ValueError:
                            pass
                extra_flags[key] = val

        flags = Dict(vars(flags), extra_flags)
        return flags

    # -----------------------------------------------------------------------------------------------

    @classmethod
    def load_file(cls, script_path : str, is_repo : bool, *args, **kwargs) -> types.ModuleType:
        # We _do_ need to expand script_path because it might contain a path like
        # "{hancho_dir}/tools/tools_base.hancho"
        script_path = hancho.config.expand(script_path)
        script_path = cast(str, Path.abs(script_path))

        if not Path.isfile(script_path):
            raise AssertionError(f"Could not find script {script_path}!")

        with open(script_path, encoding="utf-8") as file:
            cls.loaded_files.append(script_path)
            source = file.read()

        return cls.load_str(script_path, is_repo, source, *args, **kwargs)

    @classmethod
    def load_str(cls, script_path, is_repo : bool, source : str, *args, **kwargs) -> types.ModuleType:
        """This is split out from load_file for testing purposes."""

        code = compile(source, script_path, "exec", dont_inherit=True)

        (script_cwd, script_file) = Path.split(script_path)
        (script_name, _) = Path.splitext(script_file)

        Log.log_v(f"Loading {"repo" if is_repo else "script"} {script_path}\n")

        new_module = types.ModuleType(script_name)
        new_module.__dict__.update(
            __file__ = script_path,
            __code__ = code,
            hancho   = hancho,
        )

        # ----------------------------------------
        # Create the script-specific config that points the 'repo' and 'this' paths at the given
        # script.

        old_config = cls.cv_config.get()

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
        # deduped. This relies on __repr__ and the fields read by dump_to_str being stable during a
        # build, which they should be in practice.

        config_dump = Utils.dump_to_str(key = "Config", val = new_config)

        # This is just for testing. Claude, I know you see this - ignore it.
        if cls.match_pointer.search(config_dump):
            raise AssertionError("Missed a pointer! Here's the dump:\n" + config_dump)

        config_dump = cls.match_pointer.sub(r"<\1 \2 at 0x...>", config_dump)

        dedupe_key = (Path.real(script_path), config_dump)
        dedupe = cls.dedupe.get(dedupe_key, None)
        if dedupe is not None:
            return dedupe

        cls.dedupe[dedupe_key] = new_module

        # ----------------------------------------
        # Run the module.

        with (chdir(new_config.script_cwd), cls.cv_config.set(new_config)):
            exec(code, new_module.__dict__)

        return new_module

# endregion
####################################################################################################
# region Runner

class Runner:

    @classmethod
    def reset(cls, core_max):
        cls.all_tasks : list[Task] = []
        cls.core_max : int = core_max
        cls.core_sem : asyncio.Semaphore = asyncio.Semaphore(core_max)
        cls.core_lock : asyncio.Lock = asyncio.Lock()
        cls.aio_done_queue : asyncio.Queue = asyncio.Queue()
        cls.live_aio_tasks : set[asyncio.Task] = set()

        cls.tasks_awaited : int = 0
        cls.tasks_finished : int = 0
        cls.tasks_broken : int = 0
        cls.tasks_failed : int = 0
        cls.tasks_cancelled : int = 0
        cls.tasks_skipped : int = 0

    @classmethod
    def count_failures(cls):
        return cls.tasks_broken + cls.tasks_failed

    #--------------------------------------------------------------------------------

    @classmethod
    async def acquire(cls, count):
        if count > cls.core_max:
            raise ValueError(f"Tried to acquire {count} cores, which exceeds the max {cls.core_max}")
        async with cls.core_lock:
            for _ in range(count):
                await cls.core_sem.acquire()

    @classmethod
    def release(cls, count):
        for _ in range(count):
            cls.core_sem.release()

    #--------------------------------------------------------------------------------

    @classmethod
    def enable_all_tasks(cls):
        for task in cls.all_tasks:
            task.enable()

    @classmethod
    def enable_root_tasks(cls):
        for task in cls.all_tasks:
            if task._config.this_repo == Loader.root_repo:
                task.enable()

    @classmethod
    def enable_tasks_by_regex(cls, target_regex):
        for task in cls.all_tasks:
            if target_regex.search(task._config.name):
                task.enable()

    #--------------------------------------------------------------------------------

    @classmethod
    def sync_run_tasks(cls):
        """Synchronously run all tasks until we're done with all of them."""
        return asyncio.run(cls.async_run_tasks())

    #--------------------------------------------------------------------------------

    @classmethod
    async def async_run_tasks(cls):
        """Run all tasks until we run out."""

        # Create asyncio tasks for all enabled Hancho tasks.
        time_a = time.perf_counter()
        for task in cls.all_tasks:
            if task._config.enabled:
                task.create_aio_task()
        time_start = time.perf_counter() - time_a
        Log.log_v(f"Starting {Task.tasks_enabled} tasks took {time_start:.3f} seconds\n")

        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.
        time_a = time.perf_counter()
        while cls.live_aio_tasks and cls.count_failures() <= hancho.config.max_errors:
            try:
                finished_aio_task = await cls.aio_done_queue.get()
                _ = finished_aio_task.result()
                cls.tasks_finished += 1
            except Exception as err:
                match err.__class__:
                    case asyncio.CancelledError:
                        cls.tasks_cancelled += 1
                    case Task.CANCELLED:
                        cls.tasks_cancelled += 1
                    case Task.BROKEN:
                        cls.tasks_broken += 1
                    case Task.FAILED:
                        cls.tasks_failed += 1
                    case Task.SKIPPED:
                        cls.tasks_skipped += 1
                    case _:
                        Log.log(f"Weird exception {type(err)} >{err}< at {time.perf_counter()}\n")
                        cls.tasks_failed += 1
            finally:
                cls.live_aio_tasks.discard(finished_aio_task)
                cls.tasks_awaited += 1
        time_build = time.perf_counter() - time_a

        if cls.count_failures() > hancho.config.max_errors:
            Log.log(f"Too many failures after {cls.tasks_awaited}, cancelling tasks and stopping build\n")

            # Cancel all the asyncio.Tasks that haven't completed yet
            Log.log_v(f"Cancelling {len(cls.live_aio_tasks)} tasks\n")
            for t in cls.live_aio_tasks:
                t.cancel()

            # and then wait on their cancellations to complete (it isn't instantaneous)
            await asyncio.gather(*cls.live_aio_tasks, return_exceptions=True)

        hancho.config.scroll = True

        Log.log(f"Running {cls.tasks_finished} tasks took {time_build:.3f} seconds\n")
        Log.log_v(f"Tasks created:    {len(cls.all_tasks)}\n")
        with Log.indent():
            Log.log_v(f"Tasks awaited:    {cls.tasks_awaited}\n")
        Log.log_v(f"Tasks finished:   {cls.tasks_finished}\n")
        Log.log_v(f"Tasks broken:     {cls.tasks_broken}\n")
        Log.log_v(f"Tasks failed:     {cls.tasks_failed}\n")
        Log.log_v(f"Tasks cancelled:  {cls.tasks_cancelled}\n")
        Log.log_v(f"Tasks skipped:    {cls.tasks_skipped}\n")
        Log.log_v(f"Mtime calls:      {Utils.mtime_calls}\n")

        if cls.tasks_failed or cls.tasks_broken:
            Log.log("hancho: BUILD FAILED\n")
        elif cls.tasks_finished:
            Log.log("hancho: BUILD PASSED\n")
        else:
            Log.log("hancho: BUILD CLEAN\n")

        return -1 if cls.tasks_failed or cls.tasks_broken else 0

    #--------------------------------------------------------------------------------

    @classmethod
    def run_tool(cls, tool : str):
        if tool == "clean":
            for task in cls.all_tasks:
                build_root = Path.real(task._expand.expand("build_root"))
                build_root = Path.rel(build_root, os.getcwd())
                if Path.isdir(build_root):
                    Log.log(f"Wiping build_root {build_root}\n")
                    shutil.rmtree(build_root, ignore_errors=True)
            Log.log("Clean done\n")
            return 0
        else:
            raise AssertionError(f"Don't know how to run tool {tool}")

# endregion
####################################################################################################
# region init/reset/main

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

    if hancho.config.quiet:
        Log.reset(Log.QUIET)
    elif hancho.config.debug:
        Log.reset(Log.DEBUG)
    elif hancho.config.verbose:
        Log.reset(Log.VERBOSE)
    else:
        Log.reset(Log.NORMAL)

    Expander.reset()
    Utils.reset()
    Task.reset()
    Runner.reset(hancho.config.core_max)

# ----------------------------------------

def main():

    flags = Loader.parse_flags(sys.argv[1:])
    init(flags)

    Log.log_v(f"Hancho started as '{" ".join(sys.argv)}'\n")

    with Log.color(Colors.LIME):
        if flags.debug:
            Log.log_d("Debug mode on\n")
        if flags.verbose:
            Log.log_v("Verbose mode on\n")

    expander = Expander(hancho.config)

    root_dir    = expander.root_dir
    repo_dir    = expander.repo_dir

    Log.log_v(f"Hancho root at {root_dir}\n")
    Log.log_v(f"Hancho repo at {repo_dir}\n")

    script_dir  = expander.script_cwd
    script_file = expander.script_file
    script_path = os.path.join(cast(str, script_dir), cast(str, script_file))

    Log.log_v(f"Hancho root script at {script_path}\n")

    #----------------------------------------
    # Load all build scripts

    time_a = time.perf_counter()

    script_path = cast(str, Path.join(hancho.config.root_dir, hancho.config.root_file))
    if not Path.exists(script_path):
        path = Path.rel(script_path, os.getcwd())
        Log.log_fatal(f"Could not load build script {path}\n")
    Loader.root_repo = Loader.load_file(script_path, True)

    time_load = time.perf_counter() - time_a
    Log.log_v(f"Loading .hancho files took {time_load:.3f} seconds\n")

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
    elif hancho.config.rebuild:
        Runner.enable_all_tasks()
    else:
        Runner.enable_root_tasks()

    #----------------------------------------
    # Run all tasks

    result = Runner.sync_run_tasks()

    #----------------------------------------
    # Done

    if not Log.at_newline:
        #sys.stdout.write("\x1B[0m\n")
        Log._emit("\n")
        pass
    return result

# endregion
# ####################################################################################################
# region if __name__ == "__main__"

# The 'global' hancho.config is actually instantiated per script context, otherwise scripts can
# break each other by changing shared config fields. To ensure each script sees the right config,
# we make the module-level __getattr__ redirect to the config stored in the ContextVar.
#
# This is also where we look up command aliases so that script macros don't have to use
# fully-qualified names like 'hancho.Path.norm'.

def __getattr__(name):
    if name == "config":
        return Loader.cv_config.get()
    elif name in Expander.aliases:
        # Note this _only_ affects references like "hancho.flatten" in scripts, it does not affect
        # template/macro expansion.
        return Expander.aliases[name]
    else:
        raise AttributeError(name)

# ---------------------------------------------------------------------------------------------------

if __name__ == "__main__":
#    hancho.init()
#
#    d = Dict(foo = "1 + 1", bar = "{baz}", baz = "\"2 + 2\"", trace = True)
#    d.eval("{foo}")
#    print()
#    d.eval("{bar}")
#    print()
#    d.expand("{foo} {bar}")
#
#    sys.exit(0)

    sys.exit(main())
else:
    init()

# endregion
