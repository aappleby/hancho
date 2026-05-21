#!/usr/bin/python3
# region header

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

# endregion
####################################################################################################
# region imports

import argparse
import asyncio
import glob
import inspect
import io
import json
from math import e
import os
import random
import re
import shutil
import subprocess
import sys
from tabnanny import verbose
import time
import traceback
import types
import contextvars
from typing import Any, cast
from collections import abc
from enum import Enum
from contextlib import chdir

# endregion
####################################################################################################
# region globals, decls, etc.

hancho = sys.modules[__name__]
if __name__ == "__main__" and "hancho" not in sys.modules:
    sys.modules["hancho"] = hancho

type Tree[T] = T | list[Tree[T]]

cv_context = contextvars.ContextVar("context")
def __getattr__(name):
    return getattr(cv_context.get(), name)

def recursify(func):
    def result(val, *args, **kwargs):
        if Utils.is_iterable(val):
            return [result(v, *args, **kwargs) for v in val]
        else:
            return func(val, *args, **kwargs)
    return result

# endregion
####################################################################################################
# region Stats

class Stats:
    all_out_files : set
    filename_to_fingerprint : dict[str, str]

    mtime_calls : int

    time_load  : float
    time_queue : float
    time_build : float

    tasks_started : int
    tasks_running : int
    tasks_finished : int
    tasks_failed : int
    tasks_skipped : int
    tasks_cancelled : int
    tasks_broken : int
    tasks_shouldfail : int

    @classmethod
    def reset(cls):
        cls.all_out_files = set()
        cls.filename_to_fingerprint = dict()

        cls.mtime_calls = 0
        cls.time_load  = 0
        cls.time_queue = 0
        cls.time_build = 0
        cls.tasks_started = 0
        cls.tasks_running = 0
        cls.tasks_finished = 0
        cls.tasks_failed = 0
        cls.tasks_skipped = 0
        cls.tasks_cancelled = 0
        cls.tasks_broken = 0
        cls.tasks_shouldfail = 0

    @classmethod
    def print_build_stats(cls):
        # Done, print status info if needed

        Log.log(f"Running {cls.tasks_finished} tasks took {cls.time_build:.3f} seconds\n")

        if hancho.config.debug or hancho.config.verbose:
            Log.log(f"tasks started:    {cls.tasks_started}\n")
            Log.log(f"tasks finished:   {cls.tasks_finished}\n")
            Log.log(f"tasks failed:     {cls.tasks_failed}\n")
            Log.log(f"tasks skipped:    {cls.tasks_skipped}\n")
            Log.log(f"tasks cancelled:  {cls.tasks_cancelled}\n")
            Log.log(f"tasks broken:     {cls.tasks_broken}\n")
            Log.log(f"tasks shouldfail: {cls.tasks_shouldfail}\n")
            Log.log(f"mtime calls:      {cls.mtime_calls}\n")

        if cls.tasks_failed or cls.tasks_broken:
            Log.log(f"hancho: {Utils.color(255, 128, 128)}BUILD FAILED{Utils.color()}\n")
        elif cls.tasks_finished:
            Log.log(f"hancho: {Utils.color(128, 255, 128)}BUILD PASSED{Utils.color()}\n")
        else:
            Log.log(f"hancho: {Utils.color(128, 128, 255)}BUILD CLEAN{Utils.color()}\n")

# endregion
####################################################################################################
# region Log

class Log:
    """Simple logger that can do same-line log messages like Ninja."""

    buffer : str
    verbose : bool

    @classmethod
    def reset(cls, verbose):
        cls.buffer = ""
        cls.verbose = verbose

    @classmethod
    def log(cls, message : str):
        #cls.verbose = True

        lines = message.split('\n')

        x = 2

        for i, line in enumerate(lines):
            if ((i < len(lines) - 1) or cls.verbose) and line:
                cls.log_line("\r" + line + "\n")
            else:
                cls.log_line("\r" + line + "\x1B[K")

    @classmethod
    def log_line(cls, message : str):
        cls.buffer += message
        if not hancho.config.quiet:
            sys.stdout.write(message)
            sys.stdout.flush()

# endregion
####################################################################################################
# region Path
# These are just equivalents of the os.path.* functions that work on string trees.

class Path:

    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with simple string manipulation.

    @staticmethod
    def rel(path1, path2):
        if Utils.is_collection(path1):
            result = [Path.rel(p, path2) for p in path1]
        elif Utils.is_collection(path2):
            result = [Path.rel(path1, p) for p in path2]
        elif isinstance(path1, str) and isinstance(path2, str):
            path1 = os.path.normpath(path1)
            path2 = os.path.normpath(path2)
            result = path1.removeprefix(path2 + "/") if path1 != path2 else "."
        else:
            assert False, f"rel() Don't know how to join a {type(path1).__name__} with a {type(path2).__name__}"
        return result

    @staticmethod
    def join(lhs, rhs):
        result = [os.path.join(l, r) for l in Utils.flatten(lhs) for r in Utils.flatten(rhs)]
        return result[0] if len(result) == 1 else result

    abs  = recursify(os.path.abspath)
    base = recursify(os.path.basename)
    norm = recursify(os.path.normpath)
    real = recursify(os.path.realpath)
    ext  = recursify(lambda name, ext: os.path.splitext(name)[0] + ext)
    stem = recursify(lambda path: os.path.splitext(os.path.basename(path))[0])

# endregion
####################################################################################################
# region Utils

class Utils:
    rand : random.Random

    @classmethod
    def reset(cls):
        cls.rand = random.Random()

    @classmethod
    def check(cls, type_, t):
        if not isinstance(t, type_):
            assert isinstance(t, type_), f"Expected {type_.__name__}, got {type(t).__name__} = {t}"
        return t

    @classmethod
    def tuplify(cls, obj):
        if not Utils.is_collection(obj):
            return obj
        result = tuple(Utils.tuplify(x) for x in obj)
        return result

    @classmethod
    def listify(cls, obj):
        if not Utils.is_collection(obj):
            return obj
        result = [Utils.listify(x) for x in obj]
        return result

    # Mappings and non-array iterables are not considered Collections in Hancho so that
    # we don't turn "foo" into ('f', 'o', 'o').

    @classmethod
    def is_collection(cls, variant : Any) -> bool:
        """
        Mappings and non-array iterables are not considered Collections in Hancho so that
        we don't turn "foo" into ('f', 'o', 'o').
        """
        if isinstance(variant, (str, bytes, bytearray, abc.Mapping)): return False
        return isinstance(variant, abc.Collection)

    @classmethod
    def is_iterable(cls, variant : Any) -> bool:
        if isinstance(variant, (str, bytes, bytearray, abc.Mapping)): return False
        return isinstance(variant, abc.Iterable)

    @classmethod
    def is_mapping(cls, variant : Any) -> bool:
        return isinstance(variant, abc.Mapping)

    @classmethod
    def is_template(cls, variant : Any) -> bool:
        # This is kinda dumb as we split the string just to see how many blocks it has, and then
        # throw away the result. But whatev, this isn't performance-critical at the moment.
        if not isinstance(variant, str):
            return False
        blocks = Expander.split(variant)
        return len(blocks) > 1

    @classmethod
    def is_expr(cls, variant : Any) -> bool:
        if not isinstance(variant, str):
            return False
        blocks = Expander.split(variant)
        return len(blocks) == 1 and type(blocks[0]) == Expander.Expr

    @classmethod
    def is_lit(cls, variant : Any) -> bool:
        if not isinstance(variant, str):
            return False
        blocks = Expander.split(variant)
        return len(blocks) == 1 and type(blocks[0]) == Expander.Lit

    @classmethod
    def join(cls, lhs, rhs, *args) -> list[str]:
        lhs2 = Utils.flatten(lhs)
        rhs2 = Utils.join(rhs, *args) if len(args) > 0 else Utils.flatten(rhs)
        return [l + r for l in lhs2 for r in rhs2]

    ########################################

    @classmethod
    def color(cls, red : int = 0, green : int = 0, blue : int = 0) -> str:
        """Converts RGB color to ANSI format string."""
        # Color strings don't work in Windows console, so don't emit them.
        if not hancho.config.use_color or os.name == "nt":
            return ""
        if red == 0 and green == 0 and blue == 0:
            return "\x1B[0m"
        return f"\x1B[38;2;{red};{green};{blue}m"

    @classmethod
    def id_to_color(cls, obj):
        rand = cls.rand
        rand.seed(id(obj))
        return Utils.color(rand.randint(64, 255), rand.randint(64, 255), rand.randint(64, 255))

    ########################################

    @classmethod
    def run_cmd(cls, cmd : str):
        """Runs a console command synchronously and returns its stdout with whitespace stripped."""
        result = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        return result

    ########################################

    @classmethod
    def mtime(cls, filename : str):
        """Gets the file's mtime and tracks how many times we've called mtime()"""
        Stats.mtime_calls += 1
        return os.stat(filename).st_mtime_ns

    ########################################

    @classmethod
    def flatten(cls, variant : Any) -> list[Any]:
        if Utils.is_iterable(variant):
            return [x for element in variant for x in Utils.flatten(element)]
        return [] if variant is None else [variant]

    #--------------------------------------------------------------------------------

    @classmethod
    def is_scalar(cls, val):
        return not Utils.is_mapping(val) and not Utils.is_collection(val)

    @classmethod
    def _walk2(cls, c, k, v, func):
        if Utils.is_mapping(v):
            for k2, v2 in v.items():
                Utils._walk2(v, k2, v2, func)
        elif Utils.is_collection(v):
            for k2, v2 in enumerate(v):
                Utils._walk2(v, k2, v2, func)
        else:
            return func(c, k, v)

    @classmethod
    def walk2(cls, c, func):
        return cls._walk2(None, None, c, func)

    @staticmethod
    def _map(k, v, func):
        if Utils.is_scalar(v):
            return func(k, v)
        elif Utils.is_collection(v):
            return [Utils._map(k2, v2, func) for k2, v2 in enumerate(v)]
        elif Utils.is_mapping(v):
            return Dict({k2 : Utils._map(k2, v2, func) for k2, v2 in v.items()})
        else:
            assert False, f"Don't know what to do with a {type(v)}"

    @staticmethod
    def map(v, func):
        return Utils._map(None, v, func)

    #--------------------------------------------------------------------------------

    @staticmethod
    def stringify_variant(variant) -> str:
        """Converts any type into a template-compatible string."""
        if variant is None:
            return ""
        elif Utils.is_collection(variant):
            variant = [Utils.stringify_variant(val) for val in variant]
            return " ".join(variant)
        else:
            return str(variant)

    #--------------------------------------------------------------------------------

    @staticmethod
    async def await_scalar(v):
        if isinstance(v, Promise):
            return await Utils.await_variant(await v.get())
        elif isinstance(v, Task):
            return await Utils.await_variant(await v.await_done())
        elif inspect.isawaitable(v):
            return await Utils.await_variant(await v)
        else:
            return v

    @staticmethod
    async def await_variant(v):
        if Utils.is_scalar(v):
            return await Utils.await_scalar(v)
        elif Utils.is_collection(v):
            return [await Utils.await_variant(v2) for v2 in v]
        elif Utils.is_mapping(v):
            return Dict({k2 : await Utils.await_variant(v2) for k2, v2 in v.items()})
        else:
            assert False, f"Don't know what to do with a {type(v)}"


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
    5. Dict behaves like a value type, merging will make copies of all its inputs.
    """

    def __init__(self, *args, **kwargs):
        super().__init__()

        # Ignore Nones and empty dicts.
        for arg in filter(None, (*args, kwargs)):
            assert Utils.is_mapping(arg)
            for key, rval in arg.items():
                lval = dict.get(self, key, None)

                # Mappings get turned into Dicts.
                if Utils.is_mapping(rval) and type(rval) != Dict:
                    rval = Dict(rval)

                # Collections get turned into lists.
                if Utils.is_collection(rval) and type(rval) != list:
                    rval = Utils.listify(rval)

                # Pairs of mappings get merged together as needed.
                if Utils.is_mapping(lval) and Utils.is_mapping(rval):
                    rval = Dict(lval, rval)

                if lval is None or rval is not None:
                    dict.__setitem__(self, key, rval)

    ########################################
    # Object

    def __getattr__(self, key : str):
        try:
            return dict.__getitem__(self, key)
        except KeyError as e:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'") from e

    def __setattr__(self, key : str, val : Any):
        try:
            return dict.__setitem__(self, key, val)
        except KeyError as e:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'") from e

    def __delattr__(self, key : str):
        try:
            return dict.__delattr__(self, key)
        except KeyError as e:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'") from e

    def __or__(self, other):
        return Dict(self, other)

    ########################################
    # Debugging stuff

    def __repr__(self):
        return Dumper(999).dump(self)
        #if Expander.depth > 0:
        #    return Dumper(0).dump(self)
        #else:
        #    return Dumper(3).dump(self)

    def dump(self, depth, print_id = True):
        return Dumper(depth, print_id = print_id).dump(self)

    ########################################
    # Expander stuff

    def eval[T](self, expr : str, as_type: type[T] = object) -> T:
        result = Expander(self).eval(expr)
        assert isinstance(result, as_type)
        return result

    def expand[T](self, template : Tree[str], as_type : type[T] = object) -> T:
        result = Expander(self).expand(template)
        assert isinstance(result, as_type)
        return result

########################################

class Tool(Dict):
    pass

# endregion
####################################################################################################
# region Expander
# Hancho's text expansion system.
#
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
# The depth checks are to prevent recursive runaway - the MAX_DEPTH limit is arbitrary but should
# suffice.
#
# Also - TEFINAE - Text Expansion Failure Is Not An Error. Dicts can contain macros that are not
# expandable by that dict. This allows nested dicts to contain templates that can only be expanded
# an outer dict, and things will still Just Work.

class Expander(abc.Mapping[str, object]):
    """
    This class is used to fetch and expand text templates from a dict during text expansion.
    It allows for both dictionary-like access (using `expander[key]`) and attribute-like access
    (using `expander.key`), making it versatile for accessing template variables and methods.
    """

    @classmethod
    def reset(cls):
        cls.depth = 0


    class Lit(str):
        def __repr__(self):
            return "L" + str.__repr__(self)
        def __eq__(self, b):
            if type(b) == Expander.Expr:
                return False
            return str.__eq__(self, b)
        def __hash__(self):
            return str.__hash__(self)

    class Expr(str):
        def __repr__(self):
            return "E" + str.__repr__(self)
        def __eq__(self, b):
            if type(b) == Expander.Lit:
                return False
            return str.__eq__(self, b)
        def __hash__(self):
            return str.__hash__(self)

    ########################################

    def __init__(self, context : Dict):
        # We save a copy of 'trace', otherwise we end up printing traces of reading trace.... :P
        self._context = context
        self.trace = dict.get(context, "trace", False)

    def __contains__(self, key):
        return key in self._context

    def __getitem__(self, key):
        return self._get(key)

    def __getattr__(self, key):
        return self._get(key)

    def __iter__(self):
        raise TypeError("Hancho.Expander cannot be iter'd")

    def __len__(self):
        raise TypeError("Hancho.Expander cannot be len'd")

    def __repr__(self):
        result = f"{self.__class__.__name__} @ {hex(id(self))}"
        return result

    ########################################

    @classmethod
    def split(cls, text) -> list[str]:
        """
        Extracts all innermost single-brace-delimited spans from a block of text and produces a
        list of string literals and expressions. Escaped braces don't count as delimiters.
        """
        result = []
        cursor = 0
        lbrace = -1
        rbrace = -1
        escaped = False
        squoted = False
        dquoted = False

        # Turning off quote detection, because we want templates like "Run test suite '{test_mod}'"
        # to turn into "Run test suite 'my_tests'" - we _do_ want to expand inside quotes there.

        for i, c in enumerate(text):
            if escaped:
                escaped = False
            #elif squoted:
            #    if c == '\'':
            #        squoted = False
            #elif dquoted:
            #    if c == '"':
            #        dquoted = False
            elif c == '\\':
                escaped = True
            #elif c == '\'':
            #    squoted = True
            #elif c == '"':
            #    dquoted = True
            elif c == '{':
                lbrace = i
            elif c == '}' and lbrace >= 0:
                rbrace = i
                if cursor < lbrace:
                    result.append(cls.Lit(text[cursor:lbrace]))
                result.append(cls.Expr(text[lbrace+1:rbrace]))
                cursor = rbrace + 1
                lbrace = -1
                rbrace = -1

        if cursor < len(text):
            result.append(cls.Lit(text[cursor:]))

        return result

    ########################################

    def _get(self, key):
        with Tracer(self) as trace:
            trace.log(f" get '{key}'")
            result = self._context[key]

            # If we fetched a mapping, wrap it in an Expander so we expand its sub-fields.
            if isinstance(result, Dict):
                result = Expander(result)

            # If we fetched a string, expand it if needed
            if isinstance(result, str):
                result = self.expand(result)

            trace.log(f" '{result}'" if isinstance(result, str) else f" {result}")

        return result

    def get[T](self, key : str, as_type : type[T] = object) -> T:
        result = self._get(key)
        assert isinstance(result, as_type)
        return result

    ########################################

    def _eval(self, expr):
        """
        Expander.eval first expands the expression (to remove any templates) and then evaluates
        and returns the result.
        """

        with Tracer(self) as trace:
            trace.log(f"eval '{expr}'")
            try:
                expr = self.expand(expr, str)
                result = eval(expr, hancho.__dict__, self)
            except RecursionError as err:
                raise err
            except BaseException as err:
                trace.log(f" {type(err).__name__}: {err}")
                raise err
            trace.log(f" '{result}'" if isinstance(result, str) else f" {result}")

        return result

    def eval[T](self, key : str, as_type : type[T] = object) -> T:
        result = self._eval(key)
        assert isinstance(result, as_type)
        return result

    ########################################

    def _expand(self, template : str) -> str:
        """
        Expander.expand replaces all innermost {expressions} with the result of evaluating the
        expression and then recurses until either the expansion stops changing or we hit max
        recursion depth.
        Expand _always_ recurses until expansion does nothing.
        """

        blocks : list[str] = Expander.split(template)

        if len(blocks) == 0 or (len(blocks) == 1 and type(blocks[0]) == Expander.Lit):
            return template

        with Tracer(self) as trace:
            trace.log(f" expand '{template}'")
            for (i, block) in enumerate(blocks):
                if isinstance(block, Expander.Lit):
                    continue
                try:
                    value = self.eval(block)
                    block = Utils.stringify_variant(value)
                except RecursionError as e:
                    raise e
                except:
                    block = "{" + block + "}"
                blocks[i] = block
            result = "".join(blocks)
            trace.log(f" '{result}'")

        if result != template:
            result = self._expand(result)

        return result

    def expand[T](self, template : Tree[str], as_type : type[T] = object) -> T:
        if Utils.is_collection(template):
            result = [self.expand(v) for v in template]
        elif isinstance(template, str):
            result = self._expand(template)
        else:
            assert False, f"Don't know how to expand a {type(template)} = {template}"
        assert isinstance(result, as_type)
        return result

    # Expand-In-Place
    def xip(self, variant, key):
        pass

# endregion
####################################################################################################
# region Tracer
# Expansion tracing class used by Expander

class Tracer:
    # The maximum number of recursion levels we will do to expand a macro.
    # Tests currently require MAX_DEPTH >= 6
    MAX_DEPTH : int = 20
    trellis_stack : list[str] = []

    def __init__(self, expander : Expander):
        self.trace = expander.trace
        self.color = Utils.id_to_color(expander)

    def __enter__(self):
        if len(Tracer.trellis_stack) >= Tracer.MAX_DEPTH:
            raise RecursionError("Template expansion failed to terminate")
        Tracer.trellis_stack.append(self.color + "┃ ")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        Tracer.trellis_stack.pop()
        return False

    @classmethod
    def reset(cls):
        cls.trellis_stack = [Utils.color(0)]

    def log(self, text : str):
        """Prints a trace message to the log."""
        if not self.trace:
            return

        #source_id = id(source)

        #if trellis_bar[0] == '┗':
        #    Tracer.pop()

        buffer = ""
        #buffer += Utils.id_to_color(source_id)
        #buffer += hex(source_id)
        #buffer += Utils.color()
        #buffer += ": "

        buffer += "".join(Tracer.trellis_stack)
        #buffer += self.color + "┃ "
        #buffer += trellis_bar
        #buffer += Utils.color()

        buffer += text
        buffer += '\n'

        Log.log(buffer)

        #if trellis_bar[0] == '┏':
        #    Tracer.push(trellis_color)

        #if len(Tracer.trellis_bar) and Tracer.trellis_bar[0] == '┗' and Expander.depth == 0:
        #    Log.log("")

# endregion
####################################################################################################
# region Dumper
# Pretty-printer for various types

class Dumper:
    def __init__(self, max_depth=2, print_id = True):
        self.depth     = 0
        self.max_depth = max_depth
        self.print_id  = print_id

    def indent(self):
        return "  " * self.depth

    def dump(self, variant):
        if self.print_id:
            result = f"{type(variant).__name__} @ {hex(id(variant))} "
        else:
            result = f"{type(variant).__name__} "

        if isinstance(variant, Task):
            result += self.dump_dict(variant.__dict__)
        elif isinstance(variant, Dict):
            result += self.dump_dict(variant)
        elif isinstance(variant, Expander):
            result += self.dump_dict(variant.config)
        elif isinstance(variant, tuple):
            result += self.dump_list(variant, '(', ')')
        elif Utils.is_collection(variant):
            result += self.dump_list(variant, '[', ']')
        elif Utils.is_mapping(variant):
            result = ""
            result += self.dump_dict(variant)
        elif isinstance(variant, str):
            result = ""
            result += '"' + str(variant) + '"'
        else:
            result = ""
            result += str(variant)
        return result

    def dump_list(self, val, ld, rd):
        if len(val) == 0:
            return f"{ld}{rd}"

        if len(val) == 1:
            return f"{ld}{self.dump(val[0])}{rd}"

        if self.depth >= self.max_depth:
            return "[...]"

        result = f"{ld}\n"
        self.depth += 1
        for val in val:
            result += self.indent() + self.dump(val) + ",\n"
        self.depth -= 1
        result += f"{self.indent()}{rd}"
        return result

    def dump_dict(self, d):
        if self.depth >= self.max_depth:
            return "{...}"

        result = "{\n"
        self.depth += 1
        last_index = len(d) - 1
        for i, (key, val) in enumerate(d.items()):
            result += self.indent()
            result += f"{key} = "
            result += self.dump(val)
            if i != last_index:
                result += ","
            result += "\n"
        self.depth -= 1
        result += self.indent() + "}"
        return result

# endregion
####################################################################################################
# region Loader

class Loader:

    root_repo : types.ModuleType
    dedupe : dict[tuple[str, str], types.ModuleType]
    stack : list[types.ModuleType]
    loaded_files : list[str]

    @classmethod
    def reset(cls):
        cls.dedupe = {}
        cls.stack = []
        cls.loaded_files = []

    #-----------------------------------------------------------------------------------------------

    @classmethod
    def parse_flags(cls, args : list[str]):
        assert Utils.is_collection(args)

        d = defaults

        # pylint: disable=line-too-long
        # fmt: off
        parser = argparse.ArgumentParser()

        # These flags are in Ninja order
        parser.add_argument("target",             default=d.target,     nargs="?", type=str.strip,  help="A regex that selects the targets to build. Defaults to all targets.")
        parser.add_argument("-v", "--verbose",    default=d.verbose,    action="store_true",  help="Show verbose build info")
        parser.add_argument("-q", "--quiet",      default=d.quiet,      action="store_true",  help="Mute all output")

        parser.add_argument("-C", "--root_dir",   default=d.root_dir,   type=str.strip,       help="Change directory before starting the build")
        parser.add_argument("-f", "--root_file",  default=d.root_file,  type=str.strip,       help="Input .hancho file - defaults to 'build.hancho'")

        parser.add_argument("-j", "--job_max",    default=d.job_max,    type=int,             help="Run N jobs in parallel (default = cpu_count)")
        parser.add_argument("-k", "--keep_going", default=d.keep_going, type=int,             help="Keep going until N jobs fail (0 means infinity)")
        parser.add_argument("-n", "--dry_run",    default=d.dry_run,    action="store_true",  help="Do not run commands")

        parser.add_argument("-d", "--debug",      default=d.debug,      action="store_true",  help="Print debugging information")
        parser.add_argument("-t", "--tool",       default=d.tool,       type=str.strip,       help="Run a subtool.")

        # These are Hancho-specific
        parser.add_argument("--build_tag",        default=d.build_tag,  type=str.strip,       help="Set the build tag. Tagged builds will have separate subdirectories under the build directory.")
        parser.add_argument("--rebuild",          default=d.rebuild,    action="store_true",  help="Rebuild everything")
        parser.add_argument("--shuffle",          default=d.shuffle,    action="store_true",  help="Shuffle task order to shake out dependency issues")
        parser.add_argument("--trace",            default=d.trace,      action="store_true",  help="Trace all text expansion")
        parser.add_argument("--use_color",        default=d.use_color,  action="store_true",  help="Use color in the console output")
        # fmt: on

        # Ignore the name of the script that loaded Hancho
        (flags, unrecognized) = parser.parse_known_args(args)

        # Unrecognized command line parameters also become module config fields if they are
        # flag-like
        extra_flags = {}
        for span in unrecognized:
            if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
                key = match.group(1)
                val = match.group(2)

                if val is None:
                    val = True
                else:
                    for converter in (float, int, bool, str):
                        try:
                            val = converter(val)
                            break
                        except ValueError:
                            pass

                #val = maybe_as_number(val) if val is not None else True
                extra_flags[key] = val

        flags = Dict(vars(flags), extra_flags)
        return flags

    #-----------------------------------------------------------------------------------------------

    @classmethod
    def load(cls, script_path : str, is_repo : bool, *args, **kwargs) -> types.ModuleType:
        debug   = hancho.config.eval("debug")
        verbose = hancho.config.eval("verbose")

        script_path = cast(str, hancho.config.expand(script_path))
        script_path = os.path.abspath(script_path)

        if debug or verbose:
            script_type = "repo" if is_repo else "script"
            message  = Utils.color(128, 128, 255)
            message += f"Loading {script_type} {script_path}"
            message += Utils.color()
            message += "\n"
            Log.log(message)

        #----------------------------------------
        # Create the script-specific config that points the 'repo' and 'this' paths at the given
        # script.

        (script_dir, script_file) = os.path.split(script_path)

        tweaks = Dict(is_repo = False, script_dir = script_dir, script_file = script_file)
        if is_repo:
            tweaks.update(is_repo = True, repo_dir = script_dir, repo_file = script_file)

        new_config = Dict(hancho.config, tweaks, *args, kwargs)

        #----------------------------------------
        # Dedupe the load if needed. Modules are only deduped if their configurations are
        # _identical_, which may bite users.

        script_path_real = os.path.realpath(script_path)
        dedupe_key = (script_path_real, new_config.dump(2, print_id = False))
        dedupe = cls.dedupe.get(dedupe_key, None)
        if dedupe is not None:
            return dedupe

        #----------------------------------------
        # We didn't get deduped, so create a new module and add it to the dedupe cache.

        assert os.path.isfile(script_path)
        new_module = types.ModuleType(os.path.basename(script_path))
        cls.dedupe[dedupe_key] = new_module

        new_module.__dict__.update(
            __file__ = script_path,
            __code__ = None,
            hancho   = hancho,
        )

        #----------------------------------------
        # Compile the module's code.

        with open(script_path, encoding="utf-8") as file:
            Loader.loaded_files.append(script_path)
            source = file.read()
            code = compile(source, script_path, "exec", dont_inherit=True)
            new_module.__dict__.update(__code__ = code)

        #----------------------------------------
        # Create a new context and run the code.

        old_context = cv_context.get()
        new_context = Dict(
            old_context,
            config    = new_config,
            this_repo = new_module if is_repo else old_context.this_repo,
            this_mod  = new_module,
        )

        with (chdir(script_dir), cv_context.set(new_context)):
            exec(new_module.__code__, new_module.__dict__)

        #----------------------------------------

        return new_module

# endregion
####################################################################################################
# region Promise
# Promise selects subsets of _out_files

class Promise:
    def __init__(self, task : Task, field : str):
        self.task : Task = task
        self.field = field

    async def get(self):
        await self.task.await_done()
        result = self.task._config[self.field]
        # FIXME this is probably wrong? need a test.
        result = Path.join(self.task._task_cwd, result)
        return result

#    def __init__(self, task : Task, *args):
#        self.task : Task = task
#        self.args = args
#
#    async def get(self):
#        await self.task.await_done()
#        if len(self.args) == 0:
#            result = self.task._out_files
#            return result
#        elif len(self.args) == 1:
#            field = self.args[0]
#            result = self.task._config[field]
#            return result
#        else:
#            return [self.task._config[field] for field in self.args]

# endregion
####################################################################################################
# region Task
# Task object + bookkeeping

class TaskState(Enum):
    DECLARED = "DECLARED"
    QUEUED   = "QUEUED"
    STARTED  = "STARTED"
    WAITING  = "WAITING"
    INIT     = "INIT"
    GET_JOBS = "GET_JOBS"
    RUNNING  = "RUN"

    FINISHED  = "FINISHED"
    CANCELLED = "CANCELLED"
    FAILED    = "FAILED"
    SKIPPED   = "SKIPPED"
    BROKEN    = "BROKEN"

class Task:

    #--------------------------------------------------------------------------------

    def __init__(self, *args, **kwargs):

        # Save the context, we will use it when we create the asyncio.Task
        self._context     = contextvars.copy_context()

        self._parent_repo = hancho.this_repo
        self._parent_mod  = hancho.this_mod
        self._config      = Dict(hancho.config, *args, **kwargs)

        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._asyncio_task : asyncio.Task | None

        # Tasks depend on all .hancho files that were loaaded when the task was created.
        # This is probably too wide a net, but tracking dependencies between .hancho files is not
        # really possible.
        self._loaded_files : list[str] = list(Loader.loaded_files)

        # Expanded config options
        e = Expander(self._config)

        self._name = e.name
        self._desc = e.desc

        self._root_dir    : str = os.path.abspath(e.get("root_dir",    str))
        self._root_file   : str = os.path.abspath(e.get("root_file",   str))
        self._repo_dir    : str = os.path.abspath(e.get("repo_dir",    str))
        self._repo_file   : str = os.path.abspath(e.get("repo_file",   str))
        self._script_dir  : str = os.path.abspath(e.get("script_dir",  str))
        self._script_file : str = os.path.abspath(e.get("script_file", str))
        self._task_cwd    : str = os.path.abspath(e.get("task_cwd",    str))
        self._build_root  : str = os.path.abspath(e.get("build_root",  str))
        self._build_dir   : str = os.path.abspath(e.get("build_dir",   str))

        self._job_count   = e.get("job_count", int)
        self._keep_going  = e.get("keep_going", int)

        self._depformat   = e.get("depformat", str)
        self._build_tag   = e.get("build_tag", str)
        self._target      = e.get("target", str)
        self._tool        = e.get("tool", str)

        self._verbose     = e.get("verbose", bool)
        self._debug       = e.get("debug", bool)
        self._dry_run     = e.get("dry_run", bool)
        self._quiet       = e.get("quiet", bool)
        self._rebuild     = e.get("rebuild", bool)
        self._shuffle     = e.get("shuffle", bool)
        self._should_fail = e.get("should_fail", bool)

        # Command can't be expanded until inputs are ready
        self._command : Tree[str] | function = ""

        # Bookkeeping stuff

        self._task_index : int = 0
        self._state : TaskState = TaskState.DECLARED
        self._reason : str = ""
        self._stdout : str = ""
        self._stderr : str = ""

        self._in_files  = []
        self._out_files = []

        Runner.all_tasks.append(self)

    # ----------------------------------------

    transitions = {
        TaskState.DECLARED : [TaskState.QUEUED],
        TaskState.QUEUED   : [TaskState.STARTED],

        TaskState.STARTED  : [TaskState.WAITING],
        TaskState.WAITING  : [TaskState.INIT, TaskState.CANCELLED],

        TaskState.INIT     : [
            TaskState.CANCELLED,
            TaskState.BROKEN,
            TaskState.FINISHED,
            TaskState.SKIPPED,
            TaskState.GET_JOBS
        ],

        TaskState.GET_JOBS : [TaskState.RUNNING],
        TaskState.RUNNING  : [TaskState.FAILED, TaskState.FINISHED],
    }

    def to_state(self, new_state):
        if not self._state in Task.transitions:
            raise RuntimeError(f"State {self._state} has no edges in the transition table")
        edges = Task.transitions[self._state]
        if not new_state in edges:
            raise RuntimeError(f"Can't transition from {self._state} to {new_state}!")
        self._state = new_state

    # ----------------------------------------

    # WARNING: Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
    def __copy__(self):
        assert False, "Don't copy Tasks!"

    def __deepcopy__(self, memo):
        assert False, "Don't copy Tasks!"

    def __repr__(self):
        return Dumper(2).dump(self)

    # ----------------------------------------

    def queue(self):
        self.to_state(TaskState.QUEUED)

        # Queue all tasks referenced by this task's config.F
        def apply2(k, v):
            if isinstance(v, Task) and v._state is TaskState.DECLARED:
                v.queue()
            return v
        self._config = Utils.map(self._config, apply2)

        # And now queue this task.
        Runner.queued_tasks.append(self)


    def start(self):
        self.to_state(TaskState.STARTED)

        self._asyncio_task = asyncio.create_task(self.task_main2(), context = self._context)
        Stats.tasks_started += 1

    async def await_done(self):
        if self._state is TaskState.DECLARED:
            self.queue()
        if self._state is TaskState.QUEUED:
            self.start()
        assert self._asyncio_task is not None
        await self._asyncio_task
        return self._out_files

    def promise(self, field : str):
        return Promise(self, field)

    #--------------------------------------------------------------------------------

    async def task_main2(self):
        try:
            # Note that we chdir to task_cwd before initializing the task so that any path.abspath
            # or whatever happen from the right place.
            with chdir(self._task_cwd):
                await self.task_main()

        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # Both broken and failed tasks should end up here.
            self.log_task_failure(ex)
            self.to_state(TaskState.FAILED)
            if self._should_fail:
                Stats.tasks_shouldfail += 1
                return
            else:
                Stats.tasks_failed += 1
                raise ex

        #except BaseException as ex:  # pylint: disable=broad-exception-caught
        #    # Failure during run_command, task failed
        #    # If any command failed, we propagate the error to downstream tasks.

    #-----------------------------------------------------------------------------------------------

    async def task_main(self):

        #----------------------------------------
        # Await everything awaitable in this task's config. If any of this tasks's dependencies
        # were cancelled, we propagate the cancellation to downstream tasks.

        try:
            self.to_state(TaskState.WAITING)
            self._config = cast(Dict, await Utils.await_variant(self._config))
        except BaseException as ex:
            self.to_state(TaskState.CANCELLED)
            Stats.tasks_cancelled += 1
            raise asyncio.CancelledError() from ex

        # Now that all our inputs are ready, grab a _task_index that we'll use in our logging.
        Stats.tasks_running += 1
        self._task_index = Stats.tasks_running

        #----------------------------------------
        # Initialize the task, which means expanding everything else that needs expanding and
        # fixing up paths to point to task_cwd or build_dir.

        try:

            self.task_init()

        except asyncio.CancelledError as ex:
            # We discovered during init that we don't need to run this task.
            self.to_state(TaskState.CANCELLED)
            Stats.tasks_cancelled += 1
            raise asyncio.CancelledError() from ex

        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # Failure during task init because task is broken
            self.to_state(TaskState.BROKEN)
            if self._should_fail:
                Stats.tasks_shouldfail += 1
            else:
                Stats.tasks_broken += 1
            raise ex

        #----------------------------------------
        # Early-out if this is a no-op task

        if not self._command:
            Stats.tasks_finished += 1
            self.to_state(TaskState.FINISHED)
            return

        #----------------------------------------
        # Check if we need a rebuild

        self._reason = self.needs_rerun(self._rebuild)
        if not self._reason:
            Stats.tasks_skipped += 1
            self.to_state(TaskState.SKIPPED)
            return

        #----------------------------------------
        # Run the task!

        # Print the first status line for this task

        if self._verbose or self._debug:
            self.log_task_start()
            self.log_task_reason()

        # Wait for enough jobs to free up to run this task and then run the commands.
        self.to_state(TaskState.GET_JOBS)

        async with Runner.Jobs(self._job_count):
            self.to_state(TaskState.RUNNING)
            for command in Utils.flatten(self._command):
                await self.run_command(command)

        #----------------------------------------
        # Task finished successfully

        self.to_state(TaskState.FINISHED)
        Stats.tasks_finished += 1


    #--------------------------------------------------------------------------------

    def task_init(self):

        self.to_state(TaskState.INIT)

        # ----------------------------------------
        # Fix up all in/out paths and then expand the command.

        if self._debug:
            Log.log(f"Task before expand: {self}\n")

        def walk(c, func):
            if Utils.is_mapping(c):
                for key, val in c.items():
                    c[key] = func(key, val)
                    walk(val, func)
            elif Utils.is_collection(c):
                for key, val in enumerate(c):
                    c[key] = func(key, val)
                    walk(val, func)

        walk(self._config, self.fix_paths)

        if (callable(self._config.command)):
            self._command = cast(Any, self._config.command)
        else:
            self._command = cast(Tree[str], self._config.expand(self._config.command))

        if self._debug:
            Log.log(f"Task after expand: {self}\n")

        # ----------------------------------------
        # Check for missing paths

        if not os.path.exists(self._task_cwd):
            raise FileNotFoundError(self._task_cwd)

        if not self._build_dir.startswith(self._repo_dir):
            raise ValueError(
                f"Path error, build_dir {self._build_dir} is not under repo dir {self._repo_dir}"
            )

        # ----------------------------------------
        # Check for task collisions

        for file in self._out_files:
            real_file = os.path.realpath(file)
            if real_file in Stats.filename_to_fingerprint:
                raise ValueError(f"TaskCollision: Multiple tasks build {real_file}")
            Stats.filename_to_fingerprint[real_file] = real_file

        # ----------------------------------------
        # Check for missing inputs

        if not self._dry_run:
            for file in self._in_files:
                if file is None:
                    raise ValueError("_in_files contained a None")
                if not os.path.exists(file):
                    raise FileNotFoundError(file)

        # ----------------------------------------
        # Check that all build files would end up under build_dir

        for file in self._out_files:
            if file is None:
                raise ValueError("_out_files contained a None")
            file = os.path.abspath(file)
            if not file.startswith(self._build_dir):
                raise ValueError(
                    f"Path error, output file {file} is not under build_dir {self._build_dir}"
                )

        # ----------------------------------------
        # Check for duplicate task outputs

        if self._command:
            for file in self._out_files:
                file = os.path.abspath(file)
                if file in Stats.all_out_files:
                    raise NameError(f"Multiple rules build {file}!")
                Stats.all_out_files.add(file)

        # ----------------------------------------
        # Make sure our output directories exist

        if not self._dry_run:
            for file in self._out_files:
                os.makedirs(os.path.dirname(file), exist_ok=True)

    #--------------------------------------------------------------------------------

    def fix_paths(self, k, v):
        if not isinstance(k, str): return v
        if not k.startswith("in_") and not k.startswith("out_"): return v
        if v is None: return v

        if Utils.is_collection(v):
            return [self.fix_paths(k, v2) for v2 in v]

        if not isinstance(v, str):
            assert False, f"Value associated with key '{k}' is not a string or collection: '{v}'"

        if len(v) == 0: return v

        # Expand all in_ and out_ filenames
        # We _must_ expand these first before joining paths or the paths will be incorrect:
        # prefix + swap(abs_path) != abs(prefix + swap(path))

        v = cast(str, self._config.expand(v))
        v = os.path.normpath(v) # type: ignore

        # Make all in_ and out_ file paths absolute by joining build_dir to them

        if k == "in_depfile" or k.startswith("out_"):
            # Note this conditional needs to be first, as build_dir can itself be under task_cwd
            if v.startswith(self._build_dir):
                # Absolute path under build_dir, do nothing.
                pass
            elif v.startswith(self._task_cwd):
                # If an input source had an absolute path and we swap the extension on it to make the
                # output filename, we'll have a '.o' file or similar inside task_cwd. Move it so it
                # lives under build_dir.
                v = os.path.relpath(v, self._task_cwd)
                v = os.path.join(self._build_dir, v)
            elif os.path.isabs(v):
                raise ValueError(f"Output file has absolute path that is not under task_cwd or build_dir : {v}")
            else:
                # Relative path, add build_dir
                v = os.path.join(self._build_dir, v)

            v = os.path.abspath(v)

        elif k.startswith("in_"):
            v = os.path.join(self._task_cwd, v)

        # Gather all absolute file paths to _in/_out_files.
        # WARNING: These filenames _must_ be absolute as they may be read from other repos.
        if k == "in_depfile":
            if isinstance(v, str) and os.path.isfile(v):
                self._in_files.append(v)
        elif k.startswith("out_"):
            self._out_files.extend(Utils.flatten(v))
        elif k.startswith("in_"):
            self._in_files.extend(Utils.flatten(v))

        #print("**************")
        #print(v)
        #print(self._task_cwd)
        #print(os.path.relpath(v, self._task_cwd))
        #print(Path.rel(v, self._task_cwd))
        #print(Path.rel(v, self._build_dir))
        #print("**************")

        # But the path _inside_ the task can be relative to the task dir? I think this works...
        v = Path.rel(v, self._task_cwd)

        #print(f"************** {v}")


        return v

    #--------------------------------------------------------------------------------

    def needs_rerun(self, rebuild=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""

        if rebuild:
            return f"Files {self._out_files} forced to rebuild"
        if not self._in_files:
            return "Always rebuild a target with no inputs"
        if not self._out_files:
            return "Always rebuild a target with no outputs"

        # Check if any of our output files are missing.
        for file in self._out_files:
            if not os.path.exists(file):
                return f"Rebuilding because {file} is missing"

        # Check if any of our input files are newer than the output files.
        min_out = min(Utils.mtime(f) for f in self._out_files)

        if Utils.mtime(__file__) >= min_out:
            return "Rebuilding because hancho.py has changed"

        for file in self._in_files:
            if Utils.mtime(file) >= min_out:
                return f"Rebuilding because {file} has changed"

        for filename in self._loaded_files:
            if Utils.mtime(filename) >= min_out:
                return f"Rebuilding because {filename} has changed"

        # Check all dependencies in the C dependencies file, if present.
        depfile = self._config.in_depfile

        if depfile and os.path.exists(depfile):
            if self._debug:
                Log.log(f"Found C dependencies file {depfile}\n")
            with open(depfile, encoding="utf-8") as depfile:
                deplines = None
                if self._depformat == "msvc":
                    # MSVC /sourceDependencies
                    deplines = json.load(depfile)["Data"]["Includes"]
                elif self._depformat == "gcc":
                    # GCC -MMD
                    deplines = depfile.read().split()
                    deplines = [d for d in deplines[1:] if d != "\\"]
                else:
                    raise ValueError(f"Invalid dependency file format {self._depformat}")

                # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
                deplines = [os.path.join(self._task_cwd, d) for d in deplines]
                for abs_file in deplines:
                    if Utils.mtime(abs_file) >= min_out:
                        return f"Rebuilding because {abs_file} has changed"

        # All checks passed; we don't need to rebuild this output.
        # Empty string = no reason to rebuild
        return ""

    #--------------------------------------------------------------------------------

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        # Non-string non-callable commands are not valid
        if not isinstance(command, str) and not callable(command):
            raise ValueError(f"Don't know what to do with {command}")

        if self._verbose or self._debug:
            self.log_command_start(command)

        # Dry runs get early-out'ed before we do anything.
        if self._dry_run:
            return

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            try:
                result = command(self)
                while inspect.isawaitable(result):
                    result = await result
                self._stdout = ""
                self._stderr = ""
            except BaseException as e:
                self.log_command_failure(command, e)
                raise e
            return
        else:
            # Create the subprocess via asyncio and then await the result.
            #if debug: Log.log(f"Task {hex(id(self))} subprocess start '{command}'\n")
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd    = self._task_cwd,
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.PIPE,
            )
            (stdout_data, stderr_data) = await proc.communicate()
            self._stdout = stdout_data.decode()
            self._stderr = stderr_data.decode()
            #if debug: Log.log(f"Task {hex(id(self))} subprocess done '{command}'\n")

        if proc.returncode:
            e = ValueError(f"CommandFailure: Command exited with return code {proc.returncode}\n")
            self.log_command_failure(command, e)
            raise e
        elif self._verbose or self._debug:
            self.log_command_done(command)

    #----------------------------------------

    def dump(self):
        result = f"{type(self).__name__} @ {hex(id(self))} : '{self._name}'"
        print(result)

    #----------------------------------------

    def log_prefix(self):
        message  = Utils.color(128,255,196)
        message += f"[{self._task_index}/{Stats.tasks_started}] "
        message += Utils.color()
        return message

    def stdout_to_str(self):
        message = ""
        if self._stdout:
            message += f"========== Stdout ==========\n"
            message += self._stdout
        if self._stderr:
            message += f"========== Stderr ==========\n"
            message += self._stderr
        if self._stdout or self._stderr:
            message += f"============================\n"
        return message

    def log_task_start(self):
        #if isinstance(self._command, list) and len(self._command) > 1:
        #    pass
        #else:
        #    pass
        message  = self.log_prefix()
        message += f"Task started : '{self._name}'"
        ##if self._dry_run:     message += " (DRY RUN)"
        ##if self._config.desc: message += f" '{self._config.desc}'"
        Log.log(message)
        pass

    def log_task_reason(self):
        message  = self.log_prefix()
        message += Utils.color(128,128,128)
        message += f"Reason: {self._reason}"
        message += Utils.color()
        message += "\n"
        Log.log(message)


    def log_task_done(self):
        #message  = self.log_prefix()
        #message += f"Task '{self._name}' done"
        #Log.log(message)
        pass

    def log_task_failure(self, ex):
        script_path = os.path.join(self._script_dir, self._script_file)
        message  = self.log_prefix()
        message += Utils.color(255,0,0)
        message += f"Task failed!\n"
        message += f"From {rel(script_path, self._root_dir)}:\n"
        message += f"    Task '{self._name}' : '{self._desc}'\n"
        message += traceback.format_exc()
        message += Utils.color()
        Log.log(message)

    def log_command_start(self, command):
        message  = self.log_prefix()
        message += f"Command started : '{command}'"
        if self._dry_run: message += " (DRY RUN)"
        Log.log(message)

    def log_command_failure(self, command, ex):
        script_path = os.path.join(self._script_dir, self._script_file)
        message  = self.log_prefix()
        message += Utils.color(255,0,0)
        message += f"Task failed!\n"
        message += f"From {rel(script_path, self._root_dir)}:\n"
        message += f"    Task '{self._name}' : '{self._desc}'\n"
        message += f"    command = '{command}'\n"
        message += f"    error   = '{ex}'\n"
        if not callable(command):
            message += self.stdout_to_str()
        message += Utils.color()
        Log.log(message)

    def log_command_done(self, command):
        #message  = self.log_prefix()
        #message += f"Command done : '{command}'"
        #message += self.stdout_to_str()
        #Log.log(message)
        pass

# endregion
####################################################################################################
# region Runner

class Runner:

    all_tasks : list[Task]
    queued_tasks : list[Task]
    started_tasks : list[Task]
    finished_tasks : list[Task]
    job_max  : int
    job_sem  : asyncio.Semaphore
    job_lock : asyncio.Lock

    class Jobs:
        def __init__(self, count):
            self.count = count
        async def __aenter__(self):
            await Runner.acquire(self.count)
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            await Runner.release(self.count)
            return False

    @classmethod
    def reset(cls, job_max):
        cls.all_tasks = []
        cls.queued_tasks = []
        cls.started_tasks = []
        cls.finished_tasks = []
        cls.job_max  = job_max
        cls.job_sem  = asyncio.Semaphore(job_max)
        cls.job_lock = asyncio.Lock()

    #--------------------------------------------------------------------------------
    # Job pool

    @classmethod
    async def acquire(cls, count):
        async with cls.job_lock:
            for _ in range(count):
                await cls.job_sem.acquire()

    @classmethod
    async def release(cls, count):
        for _ in range(count):
            cls.job_sem.release()

    #--------------------------------------------------------------------------------

    @classmethod
    def queue_all_tasks(cls):
        for task in cls.all_tasks:
            task.queue()

    @classmethod
    def queue_root_tasks(cls):
        for task in cls.all_tasks:
            if task._parent_repo == Loader.root_repo:
                task.queue()

    @classmethod
    def queue_tasks_by_regex(cls, target_regex):
        for task in cls.all_tasks:
            if target_regex.search(task._name):
                #Log.log(f"Queueing task for '{task._name}'")
                task.queue()

    #--------------------------------------------------------------------------------

    @classmethod
    def sync_run_tasks(cls):
        """Synchronously run all queued tasks until we're done with all of them."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = asyncio.run(cls.async_run_tasks())
        loop.close()
        return result

    #--------------------------------------------------------------------------------

    @classmethod
    async def async_run_tasks(cls):
        """Run all tasks in the queue until we run out."""

        # Tasks can create other tasks, and we don't want to block waiting on a whole batch of
        # tasks to complete before queueing up more. Instead, we just keep queuing up any pending
        # tasks after awaiting each one. Because we're awaiting tasks in the order they were
        # created, this will effectively walk through all tasks in dependency order.

        while cls.queued_tasks or cls.started_tasks:
            if hancho.config.shuffle:
                Log.log(f"Shufflin' {len(cls.queued_tasks)} tasks")
                random.shuffle(cls.queued_tasks)

            while cls.queued_tasks:
                task = cls.queued_tasks.pop(0)
                task.start()
                cls.started_tasks.append(task)

            task = cls.started_tasks.pop(0)
            asyncio_task = Utils.check(asyncio.Task, task._asyncio_task)

            verbose = task._config.eval("verbose")
            debug   = task._config.eval("verbose")

            try:
                await asyncio_task
                cls.finished_tasks.append(task)
            except BaseException as ex:  # pylint: disable=broad-exception-caught
                # Both broken and failed tasks should end up here.
                #task.log_task_failure(ex)
                if task._should_fail:
                    cls.finished_tasks.append(task)

            fail_count = Stats.tasks_failed + Stats.tasks_cancelled + Stats.tasks_broken
            if hancho.config.keep_going and fail_count >= hancho.config.keep_going:
                Log.log("Too many failures, cancelling tasks and stopping build\n")
                cls.cancel_all_tasks()
                break

        return -1 if Stats.tasks_failed or Stats.tasks_broken else 0

    #--------------------------------------------------------------------------------

    @classmethod
    def cancel_all_tasks(cls):
        for task in cls.started_tasks:
            if task._asyncio_task is not None:
                task._asyncio_task.cancel()
                tasks_cancelled += 1

    #--------------------------------------------------------------------------------

    @classmethod
    def run_tool(cls, tool : str):
        if tool == "clean":
            for task in cls.all_tasks:
                build_root = os.path.realpath(task._config.eval("build_root", str))
                build_root = os.path.relpath(build_root, os.getcwd())
                if os.path.isdir(build_root):
                    Log.log(f"Wiping build_root {build_root}\n")
                    shutil.rmtree(build_root, ignore_errors=True)
            Log.log("Clean done\n")
            return 0
        else:
            assert False, f"Don't know how to run tool {tool}"
            return -1

# endregion
####################################################################################################
# region Declarations that should only be seen by client scripts. This has to go before the
# 'if __name__ == __main__' below.

def init(*args, **kwargs):
    context = Dict(
        config    = Dict(defaults, *args, kwargs),

        # These are here so that tasks have access to hancho.this_repo and hancho.this_mod, which
        # they store in parent_repo/mod
        this_repo = hancho,
        this_mod  = hancho,
    )
    cv_context.set(context)
    reset()

def reset():
    Loader.reset()
    Stats.reset()
    Log.reset(hancho.config.verbose)
    Utils.reset()
    Expander.reset()
    Tracer.reset()
    Runner.reset(hancho.config.job_max)

def load(script_path, *args, **kwargs) -> types.ModuleType:
    return Loader.load(script_path, False, *args, kwargs)

def repo(script_path, *args, **kwargs) -> types.ModuleType:
    return Loader.load(script_path, True, *args, kwargs)

path    = os.path # path.dirname and path.basename used by makefile-related rules
re      = re # why is sub() not working?
glob    = staticmethod(glob.glob)
flatten = Utils.flatten
run_cmd = Utils.run_cmd
color   = Utils.color
join    = Utils.join

abs     = Path.abs
base    = Path.base
ext     = Path.ext
norm    = Path.norm
real    = Path.real
rel     = Path.rel
stem    = Path.stem

# We spell all these defaults out explicitly so that when this config gets merged with flags and
# task configs the fields stay in the same order.

defaults = Dict(

    name       = "",
    desc       = "",
    command    = "",

    hancho_dir = os.path.dirname(__file__),
    task_cwd   = "{script_dir}",
    root_dir   = os.getcwd(),
    root_file  = "build.hancho",
    repo_dir   = "{root_dir}",
    repo_file  = "{root_file}",
    script_dir  = "{root_dir}",
    script_file = "{root_file}",

    build_root = "{repo_dir}/build",
    build_dir  = "{build_root}/{build_tag}/{rel(task_cwd, repo_dir)}",

    job_count   = 1,
    job_max     = os.cpu_count(),

    depformat   = "gcc" if sys.platform.startswith("linux") else "msvc",
    in_depfile  = "",

    build_tag   = "",
    target      = "",
    tool        = "",

    keep_going  = False,
    verbose     = False,
    debug       = False,
    dry_run     = False,
    quiet       = False,
    rebuild     = False,
    shuffle     = False,
    trace       = False,
    use_color   = True,
    should_fail = False
)

# endregion
####################################################################################################
# region Main

def main():

    init(Loader.parse_flags(sys.argv[1:]))

    #----------------------------------------
    # Load all build scripts

    time_a = time.perf_counter()
    script_path = os.path.join(hancho.config.root_dir, hancho.config.root_file)
    if not os.path.exists(script_path):
        path = os.path.relpath(script_path, os.getcwd())
        Log.log(f"Could not load build script {path}\n")
        sys.exit(-1)
    Loader.root_repo = Loader.load(script_path, True)
    Stats.time_load = time.perf_counter() - time_a

    #if hancho.config.debug or hancho.config.verbose:
    if True:
        Log.log(f"Loading .hancho files took {Stats.time_load:.3f} seconds\n")

    #----------------------------------------
    # Run tools if needed

    if hancho.config.tool:
        result = Runner.run_tool(hancho.config.tool)
        return result

    #----------------------------------------
    # Queue all tasks

    time_a = time.perf_counter()
    if hancho.config.target:
        target_regex = re.compile(hancho.config.target)
        Runner.queue_tasks_by_regex(target_regex)
    else:
        Runner.queue_root_tasks()
    Stats.time_queue = time.perf_counter() - time_a

    if hancho.config.debug or hancho.config.verbose:
        Log.log(f"Queueing {len(Runner.queued_tasks)} tasks took {Stats.time_queue:.3f} seconds\n")

    #----------------------------------------
    # Run all tasks

    time_a = time.perf_counter()
    result = Runner.sync_run_tasks()
    Stats.time_build = time.perf_counter() - time_a

    #----------------------------------------
    # Done

    Stats.print_build_stats()
    return result

# endregion
####################################################################################################
# region __name__ == __main__

if __name__ == "__main__":
    sys.exit(main())
else:
    init()

# endregion
####################################################################################################
