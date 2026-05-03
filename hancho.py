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

# FIXME - should we be using mappingproxy to make Dicts immutable?

####################################################################################################
#region imports

import argparse
import asyncio
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
import typing
from typing import Any, cast, Type, overload
from collections import abc

type str_tree = str | list[str_tree]
_MISSING = object()

#if typing.TYPE_CHECKING:
#    from . import hancho
#else:
#    hancho = sys.modules[__name__]

#endregion
####################################################################################################
#region Job pool

class JobPool:
    jobs_available = os.cpu_count() or 1
    jobs_lock = asyncio.Condition()
    job_slots = [None] * jobs_available

    @classmethod
    def reset(cls, job_count):
        cls.jobs_available = job_count
        cls.job_slots = [None] * cls.jobs_available

    ########################################

    @classmethod
    async def acquire_jobs(cls, count, token):
        """Waits until 'count' jobs are available and then removes them from the job pool."""

        if count > root_config.jobs:
            raise ValueError(f"Need {count} jobs, but pool is {root_config.jobs}.")

        await cls.jobs_lock.acquire()
        await cls.jobs_lock.wait_for(lambda: cls.jobs_available >= count)

        slots_remaining = count
        for i, val in enumerate(cls.job_slots):
            if val is None and slots_remaining:
                cls.job_slots[i] = token
                slots_remaining -= 1

        cls.jobs_available -= count
        cls.jobs_lock.release()

    ########################################
    # NOTE: The notify_all here is required because we don't know in advance which tasks will
    # be capable of running after we return jobs to the pool. HOWEVER, this also creates an
    # O(N^2) slowdown when we have a very large number of pending tasks (>1000) due to the
    # "Thundering Herd" problem - all tasks will wake up, only a few will acquire jobs, the
    # rest will go back to sleep again, this will repeat for every call to release_jobs().

    @classmethod
    async def release_jobs(cls, count, token):
        """Returns 'count' jobs back to the job pool."""

        await cls.jobs_lock.acquire()
        cls.jobs_available += count

        slots_remaining = count
        for i, val in enumerate(cls.job_slots):
            if val == token:
                cls.job_slots[i] = None
                slots_remaining -= 1

        cls.jobs_lock.notify_all()
        cls.jobs_lock.release()

#endregion
####################################################################################################
#region Files

class Files:
    loaded_files : list[str] = []
    all_out_files : set = set()
    filename_to_fingerprint : dict[str, str] = {}

#endregion
####################################################################################################
#region Stats

class Stats:
    mtime_calls : int = 0

    time_load  : float = 0
    time_queue : float = 0
    time_build : float = 0

    tasks_started : int = 0
    tasks_running : int = 0
    tasks_finished : int = 0
    tasks_failed : int = 0
    tasks_skipped : int = 0
    tasks_cancelled : int = 0
    tasks_broken : int = 0

    @classmethod
    def print_build_stats(cls):
        # Done, print status info if needed

        Log.log(f"Running {cls.tasks_finished} tasks took {cls.time_build:.3f} seconds")

        if root_config.debug or root_config.verbose:
            Log.log(f"tasks started:   {cls.tasks_started}")
            Log.log(f"tasks finished:  {cls.tasks_finished}")
            Log.log(f"tasks failed:    {cls.tasks_failed}")
            Log.log(f"tasks skipped:   {cls.tasks_skipped}")
            Log.log(f"tasks cancelled: {cls.tasks_cancelled}")
            Log.log(f"tasks broken:    {cls.tasks_broken}")
            Log.log(f"mtime calls:     {cls.mtime_calls}")

        if cls.tasks_failed or cls.tasks_broken:
            Log.log(f"hancho: {Utils.color(255, 128, 128)}BUILD FAILED{Utils.color()}")
        elif cls.tasks_finished:
            Log.log(f"hancho: {Utils.color(128, 255, 128)}BUILD PASSED{Utils.color()}")
        else:
            Log.log(f"hancho: {Utils.color(128, 128, 255)}BUILD CLEAN{Utils.color()}")

#endregion
####################################################################################################
#region Log

class Log:
    buffer : str = ""
    line_dirty : bool = False

    @classmethod
    def log(cls, message : str, *, sameline : bool = False, **kwargs):
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
            cls.log_line(output)
        else:
            if cls.line_dirty:
                cls.log_line("\n")
            cls.log_line(output)

        cls.line_dirty = sameline

    @classmethod
    def log_line(cls, message : str):
        cls.buffer += message
        if not root_config.quiet:
            sys.stdout.write(message)
            sys.stdout.flush()



#endregion
####################################################################################################
#region Path

class Path:

    # FIXME this could use some cleanup, I don't think we need _all_ these methods.

    @staticmethod
    @overload
    def abs_path(raw_path : str) -> str: pass
    @staticmethod
    @overload
    def abs_path(raw_path : list[str_tree]) -> list[str_tree]: pass
    @staticmethod
    def abs_path(raw_path):
        if Utils.listlike(raw_path):
            return [Path.abs_path(p) for p in raw_path]
        elif isinstance(raw_path, str):
            return os.path.abspath(raw_path)
        else:
            assert False, f"abs_path() Don't know what to do with a {type(raw_path).__name__}"

    @staticmethod
    @overload
    def rel_path(path1 : str, path2 : str) -> str: pass
    @staticmethod
    @overload
    def rel_path(path1 : str_tree, path2 : str_tree) -> str_tree: pass
    @staticmethod
    def rel_path(path1, path2):
        if Utils.listlike(path1):
            return [Path.rel_path(p, path2) for p in path1]
        elif isinstance(path1, str):
            # Generating relative paths in the presence of symlinks doesn't work with either
            # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
            # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
            # should. What we really want is to just remove redundant cwd stuff off the beginning of the
            # path, which we can do with simple string manipulation.
            return path1.removeprefix(path2 + "/") if path1 != path2 else "."
        else:
            assert False, f"rel_path() Don't know what to do with a {type(path1).__name__}"

    @staticmethod
    @overload
    def join(lhs : str, rhs : str) -> str: pass
    @staticmethod
    @overload
    def join(lhs : str_tree, rhs : str_tree, *args : str_tree) -> str_tree: pass
    @staticmethod
    def join(lhs, rhs, *args) -> str_tree:
        if len(args) > 0:
            rhs = Path.join(rhs, *args)
        flat_lhs = Utils.flatten(lhs)
        flat_rhs = Utils.flatten(rhs)
        result = [os.path.join(l, r) for l in flat_lhs for r in flat_rhs]
        return result[0] if len(result) == 1 else result

    @staticmethod
    def isnorm(file_path : str) -> bool:
        return file_path == Path.norm(file_path)

    @staticmethod
    def isreal(file_path : str) -> bool:
        return file_path == Path.real(file_path)

    @staticmethod
    def norm(_path : str) -> str:
        assert not Utils.is_template(_path), f"Can't use a template as a path : {_path}"
        _path = os.path.join(os.getcwd(), _path)
        _path = os.path.normpath(_path)
        return _path

    @staticmethod
    def real(file_path : str) -> str:
        assert not Utils.is_template(file_path), f"Can't use a template as a path : {file_path}"
        file_path = Path.norm(file_path)
        file_path = os.path.realpath(file_path)
        return file_path

    @staticmethod
    def split(file_path : str) -> tuple[str, str]:
        result = os.path.split(file_path)
        return result

    @staticmethod
    @overload
    def normpath(val : str) -> str: pass
    @staticmethod
    @overload
    def normpath(val : str_tree) -> str_tree: pass
    @staticmethod
    def normpath(val):
        result : str_tree | None = None
        if Utils.listlike(val):
            return [Path.normpath(v) for v in val]
        elif isinstance(val, str):
            return os.path.normpath(val)
        else:
            assert False, f"normpath() Don't know what to do with a {type(val).__name__}"

    #@staticmethod
    #@overload
    #def prepend_dir(task_dir : str, val : str) -> str : pass
    #@staticmethod
    #@overload
    #def prepend_dir(task_dir : str, val : str_tree) -> str_tree: pass
    #@staticmethod
    #def prepend_dir(task_dir, val):
    #    if isinstance(val, list):
    #        return [Path.prepend_dir(task_dir, v) for v in val]
    #    elif isinstance(val, str):
    #        return Path.join_path(task_dir, val)
    #    else:
    #        assert False, f"prepend_dir() Don't know what to do with a {type(val).__name__}"

    @staticmethod
    @overload
    def ext(name : str, new_ext : str) -> str : pass
    @staticmethod
    @overload
    def ext(name : str_tree, new_ext : str) -> str_tree : pass
    @staticmethod
    def ext(name : str_tree, new_ext : str):
        """Replaces file extensions on either a single filename or a list of filenames."""
        if Utils.listlike(name):
            return [Path.ext(n, new_ext) for n in name]
        elif isinstance(name, str):
            return os.path.splitext(name)[0] + new_ext
        else:
            assert False, f"ext() Don't know what to do with a {type(name).__name__}"

    #FIXME shouldn't this do the dynamic dispatch thing like above?
    @staticmethod
    def stem(filename : str_tree) -> str:
        flat_names : list[str] = Utils.flatten(filename)
        flat_filename : str = flat_names[0]
        base_filename : str = os.path.basename(flat_filename)
        return os.path.splitext(base_filename)[0]

#endregion
####################################################################################################
#region Utils

class Utils:
    rand = random.Random()

    @staticmethod
    def hash(v):
        if isinstance(v, (int, float, bool, str, type(None))):
            pass
        elif isinstance(v, dict):
            v = frozenset(Utils.hash(kv) for kv in v.items())
        elif isinstance(v, (list, tuple)):
            v = tuple(Utils.hash(x) for x in v)
        elif isinstance(v, set):
            v = frozenset(Utils.hash(x) for x in v)
        else:
            raise TypeError(f"Don't know how to hash {v}")
        return hash(v)

    @staticmethod
    def check[T](type_: Type[T], t: object) -> T:
        assert isinstance(t, type_), f"Expected {type_.__name__}, got {type(t).__name__}"
        return t

    @staticmethod
    def listlike(variant : Any) -> bool:
        return isinstance(variant, abc.Sequence) and not isinstance(variant, (str, bytes))

    @staticmethod
    def dictlike(variant : Any) -> bool:
        return isinstance(variant, abc.Mapping)

    @staticmethod
    def is_template(variant : Any) -> bool:
        if not isinstance(variant, str):
            return False
        blocks = Expander.split(variant)
        return len(blocks) > 1

    @staticmethod
    def is_expr(variant : Any) -> bool:
        if not isinstance(variant, str):
            return False
        blocks = Expander.split(variant)
        return len(blocks) == 1 and type(blocks[0]) == Expander.Expr

    @staticmethod
    def is_lit(variant : Any) -> bool:
        if not isinstance(variant, str):
            return False
        blocks = Expander.split(variant)
        return len(blocks) == 1 and type(blocks[0]) == Expander.Lit

    @staticmethod
    def join(lhs : str_tree, rhs : str_tree, *args : str_tree) -> list[str]:
        lhs2 = Utils.flatten(lhs)
        rhs2 = Utils.join(rhs, *args) if len(args) > 0 else Utils.flatten(rhs)
        return [l + r for l in lhs2 for r in rhs2]

    ########################################

    @staticmethod
    def color(red : int = 0, green : int = 0, blue : int = 0) -> str:
        """Converts RGB color to ANSI format string."""
        # Color strings don't work in Windows console, so don't emit them.
        # if not flags.use_color or os.name == "nt":
        #    return ""
        if red == 0 and green == 0 and blue == 0:
            return "\x1B[0m"
        return f"\x1B[38;2;{red};{green};{blue}m"

    @classmethod
    def id_to_color(cls, obj):
        rand = cls.rand
        rand.seed(id(obj))
        return Utils.color(rand.randint(64, 255), rand.randint(64, 255), rand.randint(64, 255))

    ########################################

    @staticmethod
    def run_cmd(cmd : str):
        """Runs a console command synchronously and returns its stdout with whitespace stripped."""
        return subprocess.check_output(cmd, shell=True, text=True).strip()

    ########################################

    @staticmethod
    def mtime(filename : str):
        """Gets the file's mtime and tracks how many times we've called mtime()"""
        Stats.mtime_calls += 1
        return os.stat(filename).st_mtime_ns

    ########################################

    @staticmethod
    def flatten(variant : Any) -> list[Any]:
        if Utils.listlike(variant):
            return [x for element in variant for x in Utils.flatten(element)]
        if variant is None:
            return []
        return [variant]

    ########################################

    @classmethod
    def stringify_variant(cls, variant):
        """Converts any type into a template-compatible string."""
        if variant is None:
            return ""
        elif Utils.listlike(variant):
            variant = [cls.stringify_variant(val) for val in variant]
            return " ".join(variant)
        else:
            return str(variant)

    @staticmethod
    def map_variant(key, val, map):
        if Utils.dictlike(val):
            val = Dict({k: Utils.map_variant(k, v, map) for k, v in val.items()})
        elif Utils.listlike(val):
            val = tuple(Utils.map_variant(k, v, map) for k, v in enumerate(val))
        else:
            val = map(key, val)
        return val

    @staticmethod
    def apply_variant(key, val, apply):
        val = apply(key, val)
        if Utils.dictlike(val):
            for key2, val2 in val.items():
                Utils.apply_variant(key2, val2, apply)
        elif Utils.listlike(val):
            for key2, val2 in enumerate(val):
                Utils.apply_variant(key2, val2, apply)

    @staticmethod
    async def await_variant(variant):
        """Recursively replaces every awaitable in the variant with its awaited value."""

        if Utils.listlike(variant):
            for key, val in enumerate(variant):
                variant[key] = await Utils.await_variant(val)
            return variant

        if isinstance(variant, Promise):
            return await Utils.await_variant(await variant.get())

        if isinstance(variant, Task):
            await variant.await_done()
            return await Utils.await_variant(variant._out_files)

        if inspect.isawaitable(variant):
            return await Utils.await_variant(await variant)

        return variant



#endregion
####################################################################################################
#region Dict

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
            assert Utils.dictlike(arg)
            for key, rval in arg.items():
                lval = dict.get(self, key, None)

                # Upgrade rval dict to Dict
                if isinstance(rval, abc.Mapping) and type(rval) != Dict:
                    rval = Dict(rval)

                # Recursively merge mapping-type attributes.
                if isinstance(lval, abc.Mapping) and isinstance(rval, abc.Mapping):
                    dict.__setitem__(self, key, Dict(lval, rval))

                # Deep copy all other attributes.
                elif lval is None or rval is not None:
                    dict.__setitem__(self, key, copy.deepcopy(rval))

    ########################################

    def __copy__(self):
        return Dict(self)

    def __deepcopy__(self, memo):
        return Dict(self)

    def __hash__(self):
        return Utils.hash(self)

    ########################################
    # Object

    def __getattr__(self, name : str):
        try:
            return dict.__getitem__(self, name)
        except KeyError as e:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'") from e

    def __setattr__(self, name : str, value : Any):
        raise TypeError("Hancho.Dict is immutable", name, value)

    def __delattr__(self, name : str):
        raise TypeError("Hancho.Dict is immutable", name)

    #######################################
    # abc.Mapping

    def __getitem__(self, name : str):
        try:
            return dict.__getitem__(self, name)
        except KeyError as e:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'") from e

    def __setitem__(self, name : str, value : Any):
        raise TypeError("Hancho.Dict is immutable", name, value)

    def __delitem__(self, name : str):
        raise TypeError("Hancho.Dict is immutable", name)

    ########################################
    # Debugging stuff

    def __repr__(self):
        if Expander.depth > 0:
            return Dumper(0).dump(self)
        else:
            return Dumper(2).dump(self)

    def dump(self, depth):
        return Dumper(depth).dump(self)

    ########################################
    # Expander stuff

    def eval(self, expr : str) -> Any:
        return Expander(self).eval(expr)

    def expand(self, text : str):
        return Expander(self).expand(text)

########################################

class Tool(Dict):
    pass

#endregion
####################################################################################################
#region Expander
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
# The depth checks are to prevent recursive runaway - the MAX_Expander.depth limit is arbitrary but
# should suffice.
#
# Also - TEFINAE - Text Expansion Failure Is Not An Error. Dicts can contain macros that are not
# expandable by that dict. This allows nested dicts to contain templates that can only be expanded
# an outer dict, and things will still Just Work.

class Expander(abc.Mapping):
    """
    This class is used to fetch and expand text templates from a dict during text expansion.
    It allows for both dictionary-like access (using `expander[key]`) and attribute-like access
    (using `expander.key`), making it versatile for accessing template variables and methods.
    """

    depth : int = 0

    _context: Dict

    # The maximum number of recursion levels we will do to expand a macro.
    # Tests currently require MAX_DEPTH >= 6
    MAX_DEPTH = 20

    # FIXME need tests for brace-delimited sections inside quote-delimited strings, etc
    # FIXME It feels slightly odd to have expansion_globals, should we just use the hancho.py
    # module itself?

    expansion_globals = dict(
        os   = os,
        sys  = sys,
        path = os.path,
        re   = re,
        glob = glob,

        #ext     = Utils.ext,
        #rel     = Utils.rel_path,
        #stem    = Utils.stem,
        #name    = Utils.name
        #log     = Log.log,
        flatten = Utils.flatten,
        run_cmd = Utils.run_cmd,
        color   = Utils.color,
        join    = Utils.join,
    )

    class Lit(str):
        def __repr__(self):
            return "L" + str.__repr__(self)

    class Expr(str):
        def __repr__(self):
            return "E" + str.__repr__(self)

    ########################################

    def __init__(self, context : Dict):
        object.__setattr__(self, "_context", context)
        # We save a copy of 'trace', otherwise we end up printing traces of reading trace.... :P
        object.__setattr__(self, "trace", context.trace)

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

    def __setattr__(self, name : str, value : Any):
        raise TypeError("Hancho.Expander is immutable", name, value)

    def __delattr__(self, name : str):
        raise TypeError("Hancho.Expander is immutable", name)

    def __setitem__(self, name : str, value : Any):
        raise TypeError("Hancho.Expander is immutable", name, value)

    def __delitem__(self, name : str):
        raise TypeError("Hancho.Expander is immutable", name)

    def __repr__(self):
        result = f"{self.__class__.__name__} @ {hex(id(self))}"
        return result

    ########################################
    # Returns a relative path from the task directory to the sub_path.

    def rel(self, sub_path):
        task_dir = self.eval("_task_dir")
        result = Path.rel_path(sub_path, task_dir)
        return result

    ########################################
    # FIXME we need full-loop test cases for escaped {}s.
    # Somewhere in the process we need to unescape them and I'm not sure where it goes.

    @classmethod
    def split(cls, text):
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

        for i, c in enumerate(text):
            if escaped:
                escaped = False
            elif squoted:
                if c == '\'':
                    squoted = False
            elif dquoted:
                if c == '"':
                    dquoted = False
            elif c == '\\':
                escaped = True
            elif c == '\'':
                squoted = True
            elif c == '"':
                dquoted = True
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
        orig_key = key
        if self.trace:
            Tracer.log(self, f"┏ get '{orig_key}'")
        Expander.depth += 1

        result = "<_get failed>"
        try:
            result = self._context[key]

            # If we fetched a mapping, wrap it in an Expander so we expand its sub-fields.
            if isinstance(result, Dict):
                result = Expander(result)

            Expander.depth -= 1
            if self.trace:
                if isinstance(result, str):
                    Tracer.log(self, f"┗ '{result}'")
                else:
                    Tracer.log(self, f"┗ {result}")

        except Exception as e:
            Expander.depth -= 1
            if self.trace:
                Tracer.log(self, f"┗ {type(e).__name__}: {e}")
            raise
            return result

        # If we fetched a string, expand it if needed
        if isinstance(result, str):
            result = self.expand(result)

        return result

    ########################################

    def eval(self, expr : str) -> Any: # , trace : bool
        """
        Expander.eval first expands the expression (to remove any templates) and then evaluates
        and returns the result.
        """

        if not isinstance(expr, str):
            return expr

        expr = self.expand(expr)

        orig_expr = expr
        if self.trace:
            Tracer.log(self, f"┏ eval '{orig_expr}'")
        Expander.depth += 1

        try:
            result = eval(expr, self.expansion_globals, self)
            Expander.depth -= 1
            if self.trace:
                if isinstance(result, str):
                    Tracer.log(self, f"┗ '{result}'")
                else:
                    Tracer.log(self, f"┗ {result}")
        except Exception as e:
            # If the expression was not valid Python, return it verbatim.
            # We can tag the failed evals if needed
            #result = "X" + expr
            result = expr

            Expander.depth -= 1
            if self.trace:
                Tracer.log(self, f"┗ {type(e).__name__}: {e}")
            raise

#        except Exception as e:
#            # If any other error happened while evaluating the expression, return the expression verbatim.
#            # We can tag the failed evals if needed
#            #result = "X" + expr
#            result = expr
#
#            Expander.depth -= 1
#            if self.trace:
#                Tracer.log_trace(self, f"┗ {type(e).__name__}: {e}")
#            # We can make this fatal instead of a no-op, not sure if that's more ergonomic...
#            raise

        return result

    ########################################

    def expand(self, template : str) -> str:
        """
        Expander.expand replaces all innermost {expressions} with the result of evaluating the
        expression and then recurses until either the expansion stops changing or we hit max
        recursion depth.
        Expand _always_ recurses until expansion does nothing.
        """

        if not isinstance(template, str):
            print(f"??? type of template is {type(template)}")
            return template

        if Expander.depth > Expander.MAX_DEPTH:
            raise RecursionError("TemplateRecursion: Text expansion failed to terminate")


        blocks = Expander.split(template)

        if len(blocks) == 1 and type(blocks[0]) == Expander.Lit:
            return template

        if self.trace:
            Tracer.log(self, f"┏ expand '{template}'")
        Expander.depth += 1


        for (i, block) in enumerate(blocks):
            if isinstance(block, Expander.Lit):
                continue
            try:
                block = self.eval(block)
                block = Utils.stringify_variant(block)
            except:
                block = "{" + block + "}"
            blocks[i] = block

        result = "".join(blocks)

        Expander.depth -= 1
        if self.trace:
            Tracer.log(self, f"┗ '{result}'")

        if result != template:
            result = self.expand(result)

        return result

#endregion
####################################################################################################
# region Tracer
# Expansion tracing class used by Expander

class Tracer:

    @classmethod
    def log(cls, source : Any, text : str):
        """Prints a trace message to the log."""
        source_id = id(source)
        color  = Utils.id_to_color(source_id)
        prefix = hex(source_id) + Utils.color() + ": " + ("┃ " * Expander.depth)
        Log.log(color + prefix + text)

    #@classmethod
    #def prefix(cls, context):
    #    """Prints the left-side trellis of the expansion traces."""
    #    return hex(id(context)) + ": " + ("┃ " * Expander.depth)

    #@classmethod
    #def variant(cls, variant):
    #    """Prints the right-side values of the expansion traces."""
    #    if callable(variant):
    #        return f"Callable @ {hex(id(variant))}"
    #    elif isinstance(variant, Dict):
    #        return f"Dict @ {hex(id(variant))}'"
    #    elif isinstance(variant, Expander):
    #        return f"Expander @ {hex(id(variant._context))}'"
    #    else:
    #        return f"'{variant}'"

#endregion
####################################################################################################
#region Dumper
# Pretty-printer for various types

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
        #elif isinstance(variant, HanchoAPI):
        #    result += self.dump_dict(variant.__dict__)
        elif isinstance(variant, Dict):
            result += self.dump_dict(variant)
        elif isinstance(variant, Expander):
            result += self.dump_dict(variant.config)
        elif Utils.listlike(variant):
            result += self.dump_list(variant)
        elif Utils.dictlike(variant):
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

        #result = "{\n"
        #self.depth += 1
        #for key, val in d.items():
        #    result += self.indent() + f"{key} = " + self.dump(val) + ",\n"
        #self.depth -= 1
        #result += self.indent() + "}"
        #return result

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

#endregion
####################################################################################################
#region HanchoProxy
# Hancho build scripts don't get direct access to the Hancho module, they go through this proxy so
# that each build script can have its own hancho.config object without breaking the global
# hancho.config.

class HanchoProxy(types.ModuleType):
    hancho_ref = sys.modules[__name__]

    def __init__(self, config):
        super().__init__(__name__)

        load_lambda = lambda script_path, *args, **kwargs : Loader.load_script(script_path, config, *args, kwargs)
        repo_lambda = lambda script_path, *args, **kwargs : Loader.load_repo(script_path, config, *args, kwargs)
        task_lambda = lambda *args, **kwargs : Task(config, *args, **kwargs)

        self.__dict__.update(
            load = load_lambda,
            repo = repo_lambda,
            task = task_lambda,
            config = config
        )

    def __getattr__(self, key):
        return getattr(HanchoProxy.hancho_ref, key)

    def __setattr__(self, key, _):
        raise AttributeError(f"Can't set attribute {key!r} on a Hancho proxy")

    def __dir__(self):
        return dir(HanchoProxy.hancho_ref)


#endregion
####################################################################################################
#region Loader

class Loader:

    depth : int = 0
    script_to_repo : dict[tuple[str, Dict], types.ModuleType] = {}

#    @classmethod
#    def _load(cls, new_module : Hancho) -> Hancho:
#        this_path = Path.join(config.this_dir, config.this_file)
#        if True:
#            rel_path = Path.rel_path(this_path, config.root_dir)
#            Log.log(("┃ " * Loader.depth, end="")
#            if config.is_repo:
#                Log.log(Utils.color(128, 128, 255) + f"Loading repo {rel_path}" + Utils.color())
#            else:
#                Log.log(Utils.color(128, 255, 128) + f"Loading module {rel_path}" + Utils.color())
#
#        Files.loaded_files.append(this_path) # type:ignore
#
#        # We're using compile() and FunctionType()() here beause exec() doesn't preserve source
#        # code for debugging.
#        file = open(config.this_file, encoding="utf-8")
#        source = file.read()
#        code = compile(source, config.this_file, "exec", dont_inherit=True)
#
#        #----------------------------------------
#        # THIS IS WHERE WE EXEC THE SUBMODULE
#
#        try:
#            # We must chdir()s into the .hancho file directory before running it so that
#            # glob() can resolve files relative to the .hancho file itself. We are _not_ in an async
#            # context here so there should be no other threads trying to change cwd.
#            Path.pushdir(os.path.dirname(config.this_dir))
#            old_proxy = sys.modules.get("hancho", None)
#            sys.modules["hancho"] = new_proxy
#            types.FunctionType(code, new_module.__dict__)()
#
#        finally:
#            sys.modules["hancho"] = old_proxy # type: ignore
#            Path.popdir()
#
#        #----------------------------------------
#
#        return new_module

    ########################################

#    @classmethod
#    def create_mod_config(cls, parent_config : Dict, in_path : str, *args, **kwargs) -> Dict:
#        this_path = cast(str, parent_config.expand(in_path))
#        this_path = Path.real(this_path)
#        (this_dir, this_file) = Path.split(this_path)
#
#        this_config = Dict(
#            parent_config,
#            Dict(
#                is_repo  = False,
#                this_dir  = this_dir,
#                this_file = this_file,
#            ),
#            *args,
#            kwargs
#        )
#
#        return this_config

    ########################################

#    @classmethod
#    def create_mod(cls, parent : Hancho, in_path : str, *args, **kwargs):
#        new_config = cls.create_mod_config(parent.config, in_path, *args, **kwargs)
#        # FIXME redo this like repo()
#        assert False

    ########################################










#    @classmethod
#    def load_hancho(cls, parent_config, task_path : str, *args, **kwargs) -> Hancho:
#        task_path = config.expand(task_path)
#        task_path = Path.norm(task_path)
#        (task_dir, task_file) = Path.split(task_path)
#
#        if config.verbose:
#            rel_path = Path.rel_path(task_path, config.root_dir)
#            Log.log(("┃ " * (len(Path.dirstack) - 1)), end="")
#            Log.log(Utils.color(128, 255, 128) + f"Loading module {rel_path}" + Utils.color())
#
#        #----------------------------------------
#        # Create the new Hancho proxy
#
#        #def create_mod(parent : Hancho, in_task_path : str, *args, **kwargs):
#        new_module = cls.create_mod(hancho, task_path)
#
#        new_config = Dict(
#            parent_config,
#            Dict(
#                is_repo  = False,
#                this_dir  = task_dir,
#                this_file = task_name,
#            ),
#            *args,
#            kwargs,
#        )
#
#        return new_config






    #-----------------------------------------------------------------------------------------------

    @classmethod
    def create_mod(cls, script_real : str, config : Dict):
        """
        Creates a new module for the given script + config pair.
        """

        assert Path.isreal(script_real)
        mod = types.ModuleType(os.path.basename(script_real))
        mod.__dict__.update(
            __file__ = script_real,
            __code__ = None,
            hancho   = HanchoProxy(config)
        )
        return mod

    #----------------------------------------

    @classmethod
    def compile_mod(cls, mod : types.ModuleType):
        """
        Compiles a module's script and stores the result in 'mod.__code__'.
        """

        path_real = Utils.check(str, mod.__file__)
        Files.loaded_files.append(path_real)
        with open(path_real, encoding="utf-8") as file:
            source = file.read()
        code = compile(source, path_real, "exec", dont_inherit=True)
        mod.__dict__.update(__code__ = code)

    #----------------------------------------

    @classmethod
    def exec_mod(cls, mod : types.ModuleType):
        """
        Execs the module's compiled script, which is stored in 'mod.__code__'.
        """

        old_cwd = os.getcwd()
        old_hancho = sys.modules.get("hancho", None)

        try:
            sys.modules["hancho"] = mod.hancho
            Loader.depth += 1
            (dir_real, _) = os.path.split(Utils.check(str, mod.__file__))
            os.chdir(dir_real)
            exec(mod.__code__, mod.__dict__)
        finally:
            os.chdir(old_cwd)
            Loader.depth -= 1
            if old_hancho is None:
                sys.modules.pop("hancho", None)
            else:
                sys.modules["hancho"] = old_hancho

    #-----------------------------------------------------------------------------------------------

    @classmethod
    def create_repo_config(cls, script_path_real : str, parent_config : Dict, *args, **kwargs):
        """
        Creates a config object for the given script that points (repo|this)_(dir|file) at the
        given script.
        """
        (dir_real, script_file) = os.path.split(script_path_real)

        config = Dict(
            parent_config,
            Dict(
                is_repo   = True,
                repo_dir  = dir_real,
                repo_file = script_file,
                this_dir  = dir_real,
                this_file = script_file,
            ),
            *args,
            kwargs,
        )
        return config

    @classmethod
    def create_script_config(cls, script_path_real : str, parent_config : Dict, *args, **kwargs) -> Dict:
        (dir_real, script_file) = os.path.split(script_path_real)

        config = Dict(
            parent_config,
            Dict(
                is_repo  = False,
                this_dir  = dir_real,
                this_file = script_file,
            ),
            *args,
            kwargs
        )

        return config

    #-----------------------------------------------------------------------------------------------

    @classmethod
    def load_repo(cls, script_path : str, parent_config : Dict, *args, **kwargs) -> types.ModuleType:

        #----------------------------------------
        # Normalize the script path. It _must_ be a real path, otherwise repo dedupe will break if
        # there are symlinks in the path.

        script_path = parent_config.expand(script_path)
        script_path_real = os.path.realpath(script_path)

        assert os.path.isabs (script_path_real)
        assert os.path.isfile(script_path_real)

        #----------------------------------------
        # Create the repo-specific config that points the 'repo' and 'this' path at the given
        # script.

        repo_config = Loader.create_repo_config(script_path_real, parent_config, *args, **kwargs)

        #----------------------------------------
        # Dedupe the repo load if needed. Repos are only deduped if their configurations are
        # _identical_, which may bite users.

        dedupe_key = (script_path_real, repo_config)
        dedupe = cls.script_to_repo.get(dedupe_key, None)
        if dedupe is not None:
            return dedupe

        #----------------------------------------
        # Create the new module and run its script.

        if repo_config.verbose:
            script_path_rel = Path.rel_path(script_path_real, repo_config.root_dir)
            Log.log("┃ " * Loader.depth, end="")
            Log.log(Utils.color(128, 128, 255) + f"Loading repo {script_path_rel}" + Utils.color())

        repo_module = Loader.create_mod(script_path_real, repo_config)
        Loader.compile_mod(repo_module)
        Loader.exec_mod(repo_module)

        #----------------------------------------
        # Add the new module to the dedupe list now that we're done.

        cls.script_to_repo[dedupe_key] = repo_module

        return repo_module

    @classmethod
    def load_script(cls, script_path : str, parent_config : Dict, *args, **kwargs) -> types.ModuleType:

        #----------------------------------------
        # Normalize the script path. It _must_ be a real path, otherwise repo dedupe will break if
        # there are symlinks in the path.

        script_path = parent_config.expand(script_path)
        script_path_real = os.path.realpath(script_path)

        assert os.path.isabs (script_path_real)
        assert os.path.isfile(script_path_real)

        #----------------------------------------
        # Create the script-specific config that points the 'this' path at the given script.

        script_config = Loader.create_script_config(script_path_real, parent_config, *args, **kwargs)

        #----------------------------------------
        # Create the new module and run its script.

        if script_config.verbose:
            script_path_rel = Path.rel_path(script_path_real, script_config.root_dir)
            Log.log("┃ " * Loader.depth, end="")
            Log.log(Utils.color(128, 128, 255) + f"Loading repo {script_path_rel}" + Utils.color())

        script_module = Loader.create_mod(script_path_real, script_config)
        Loader.compile_mod(script_module)
        Loader.exec_mod(script_module)

        return script_module

#endregion
####################################################################################################
#region Promise
# Promise selects subsets of _out_files

class Promise:
    def __init__(self, task, *args):
        self.task = task
        self.args = args

    async def get(self):
        await self.task.await_done()
        if len(self.args) == 0:
            return self.task.out_files
        elif len(self.args) == 1:
            return self.task._context[self.args[0]]
        else:
            return [self.task._context[field] for field in self.args]

#endregion
####################################################################################################
#region Task
# Task object + bookkeeping

class Task:

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

    def __init__(self, *args, **kwargs):

        self._config : Dict = Dict(*args, **kwargs)

        self._desc : str = ""
        self._command : str = ""
        self._in_files : list[Any] = []
        self._out_files : list[Any] = []
        self._task_index : int = 0
        self._state : str = Task.DECLARED
        self._reason : str = ""
        self._asyncio_task : asyncio.Task | None = None
        self._loaded_files : list[str] = list(Files.loaded_files)
        self._stdout : str = ""
        self._stderr : str = ""
        self._returncode : int = -1

        self._repo_dir : str = ""
        self._task_dir : str = ""
        self._build_dir : str = ""

        Runner.all_tasks.append(self)

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
        if self._state is Task.DECLARED:
            # Queue all tasks referenced by this task's config.
            def apply(_, val):
                if isinstance(val, Task):
                    val.queue()
            Utils.apply_variant(None, self._config, apply)

            # And now queue this task.
            Runner.queued_tasks.append(self)
            self._state = Task.QUEUED

    def start(self):
        self.queue()
        if self._state is Task.QUEUED:
            self._asyncio_task = asyncio.create_task(self.task_main())
            self._state = Task.STARTED
            Stats.tasks_started += 1

    async def await_done(self):
        self.start()
        assert self._asyncio_task is not None
        await self._asyncio_task

    def promise(self, *args):
        return Promise(self, *args)

    def print_status(self):
        """Print the "[1/N] Compiling foo.cpp -> foo.o" status line and debug information"""

        verbose = self._config.eval("verbose")
        Log.log(
            f"{Utils.color(128,255,196)}[{self._task_index}/{Stats.tasks_started}]{Utils.color()} {self._config.desc}",
            sameline = verbose,
        )

    async def task_main(self):
        """Entry point for async task stuff, handles exceptions generated during task execution."""

        #_config   = self._config
        verbose   = self._config.eval("verbose")
        debug     = self._config.eval("debug")
        rebuild   = self._config.eval("rebuild")

        # Await everything awaitable in this task's config.
        # If any of this tasks's dependencies were cancelled, we propagate the cancellation to
        # downstream tasks.

        def apply_await(_, val):
            return Utils.await_variant(val)
        self._config = Utils.map_variant(None, self._config, apply_await)

        try:
            assert self._state is Task.STARTED
            self._state = Task.AWAITING_INPUTS
            for key, val in self._config.items():
                # FIXME this isn't going to work with immutable Dicts
                self._config[key] = await Utils.await_variant(val)
        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # Exceptions during awaiting inputs means that this task cannot proceed, cancel it.
            self._state = Task.CANCELLED
            Stats.tasks_cancelled += 1
            raise asyncio.CancelledError() from ex

        # Everything awaited, task_init runs synchronously.
        try:
            self._state = Task.TASK_INIT

            # Note that we chdir to task_dir before initializing the task so that any path.abspath
            # or whatever happen from the right place

            task_dir = self._config.eval("task_dir")
            assert isinstance(task_dir, str)
            old_cwd = os.getcwd()
            try:
                os.chdir(task_dir)
                self.task_init()
            finally:
                os.chdir(old_cwd)

        except asyncio.CancelledError as ex:
            # We discovered during init that we don't need to run this task.
            self._state = Task.CANCELLED
            Stats.tasks_cancelled += 1
            raise asyncio.CancelledError() from ex
        except BaseException as ex:  # pylint: disable=broad-exception-caught
            self._state = Task.BROKEN
            Stats.tasks_broken += 1
            raise ex

        # Early-out if this is a no-op task
        if self._command is None:
            Stats.tasks_finished += 1
            self._state = Task.FINISHED
            return

        # Check if we need a rebuild
        self._reason = self.needs_rerun(rebuild)
        if not self._reason:
            Stats.tasks_skipped += 1
            self._state = Task.SKIPPED
            return

        try:
            # Wait for enough jobs to free up to run this task.
            job_count = self._config.eval("job_count")
            self._state = Task.AWAITING_JOBS
            await JobPool.acquire_jobs(job_count, self)

            # Run the commands.
            self._state = Task.RUNNING_COMMANDS
            Stats.tasks_running += 1
            self._task_index = Stats.tasks_running

            self.print_status()
            if verbose or debug:
                Log.log(f"{Utils.color(128,128,128)}Reason: {self._reason}{Utils.color()}")

            for command in Utils.flatten(self._command):
                await self.run_command(command)
                if self._returncode != 0:
                    break

        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # If any command failed, we print the error and propagate it to downstream tasks.
            self._state = Task.FAILED
            Stats.tasks_failed += 1
            raise ex
        finally:
            await JobPool.release_jobs(job_count, self)

        # Task finished successfully
        self._state = Task.FINISHED
        Stats.tasks_finished += 1

    def move_to_builddir(self, val):
        if not isinstance(val, str):
            return val
        # Note this conditional needs to be first, as build_dir can itself be under
        # task_dir
        if val.startswith(self._config.build_dir):
            # Absolute path under build_dir, do nothing.
            pass
        elif val.startswith(self._config.task_dir):
            # Absolute path under task_dir, move to build_dir
            val = Path.rel_path(val, self._config.task_dir)
            val = Path.join(self._config.build_dir, val)
        elif os.path.isabs(val):
            raise ValueError(f"Output file has absolute path that is not under task_dir or build_dir : {val}")
        else:
            # Relative path, add build_dir
            val = Path.join(self._config.build_dir, val)
        return val

    def move_to_taskdir(self, val):
        if not isinstance(val, str):
            return val
        if not os.path.isabs(val):
            val = Path.join(self._config.task_dir, val)
        return val

    def task_init(self):
        """All the setup steps needed before we run a task."""

        # FIXME _all_ paths should be rel'd before running command. If you want abs, you can abs() it.

        _config = self._config
        debug = _config.eval("debug")
        if debug:
            Log.log(f"\nTask before expand: {self}")

        # ----------------------------------------
        # Expand task_dir and build_dir

        # pylint: disable=attribute-defined-outside-init

        self._repo_dir   = Path.abs_path(_config.eval("repo_dir"))
        self._task_dir   = Path.abs_path(_config.eval("task_dir"))
        self._build_dir  = Path.abs_path(_config.eval("build_dir"))

        # Check for missing input files/paths
        if not os.path.exists(self._task_dir):
            raise FileNotFoundError(self._task_dir)

        if not self._build_dir.startswith(self._repo_dir):
            raise ValueError(
                f"Path error, build_dir {self._build_dir} is not under repo dir {self._repo_dir}"
            )

        # ----------------------------------------
        # Expand all in_ and out_ filenames
        # We _must_ expand these first before joining paths or the paths will be incorrect:
        # prefix + swap(abs_path) != abs(prefix + swap(path))

        def expand_path(key, val):
            if key.startswith("in_") or key.startswith("out_"):
                if not isinstance(val, str):
                    return val
                val = self._config.expand(val)
                val = path.normpath(val) # type: ignore
            return val

        self._config = Utils.map_variant(None, self._config, expand_path)

        # ----------------------------------------
        # Make all in_ and out_ file paths absolute

        def move_stuff(key, val):
            if key.startswith("out_") or key == "in_depfile":
                return self.move_to_builddir(val)
            elif key.startswith("in_"):
                return self.move_to_taskdir(val)

        self._config = Utils.map_variant(None, self._config, move_stuff)

        # ----------------------------------------
        # Gather all inputs to task.in_files and outputs to task.out_files

        def collect_stuff(key, val):
            # Note - we only add the depfile to in_files _if_it_exists_, otherwise we will fail a
            # check that all our inputs are present.
            if key == "in_depfile":
                if os.path.isfile(val):
                    self._in_files.append(val)
            elif key.startswith("out_"):
                self._out_files.append(val)
            elif key.startswith("in_"):
                self._in_files.append(val)

        Utils.apply_variant(None, self._config, collect_stuff)

        # ----------------------------------------
        # And now we can expand the command.

        self._desc    = cast(str, self._config.expand(self._config.desc))
        self._command = cast(str, self._config.expand(self._config.command))

        if debug:
            Log.log(f"\nTask after expand: {self}")

        # ----------------------------------------
        # Check for task collisions

        # FIXME need a test for this that uses symlinks

        #if self._out_files and self._context.command is not None:
        for file in self._out_files:
            real_file = os.path.realpath(file)
            if real_file in Files.filename_to_fingerprint:
                raise ValueError(f"TaskCollision: Multiple tasks build {real_file}")
            Files.filename_to_fingerprint[real_file] = real_file

        # ----------------------------------------
        # Sanity checks

        # Check for missing input files/paths
        if not os.path.exists(self._config.task_dir):
            raise FileNotFoundError(self._config.task_dir)

        for file in self._in_files:
            if file is None:
                raise ValueError("_in_files contained a None")
            if not os.path.exists(file):
                raise FileNotFoundError(file)

        # Check that all build files would end up under build_dir
        for file in self._out_files:
            if file is None:
                raise ValueError("_out_files contained a None")
            if not file.startswith(self._config.build_dir):
                raise ValueError(
                    f"Path error, output file {file} is not under build_dir {self._config.build_dir}"
                )

        # Check for duplicate task outputs
        if self._config.command:
            for file in self._out_files:
                if file in Files.all_out_files:
                    raise NameError(f"Multiple rules build {file}!")
                Files.all_out_files.add(file)

        # Make sure our output directories exist
        if not self._config.dry_run:
            for file in self._out_files:
                os.makedirs(os.path.dirname(file), exist_ok=True)

        if debug:
            Log.log(f"\nTask after expand: {self}")

    def needs_rerun(self, rebuild=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""

        debug = self._config.eval("debug")

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
        if (in_depfile := self._config.eval("in_depfile")) and os.path.exists(in_depfile):
            depformat = self._config.eval("depformat")
            if debug:
                Log.log(f"Found C dependencies file {in_depfile}")
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
                deplines = [os.path.join(self._config.task_dir, d) for d in deplines]
                for abs_file in deplines:
                    if Utils.mtime(abs_file) >= min_out:
                        return f"Rebuilding because {abs_file} has changed"

        # All checks passed; we don't need to rebuild this output.
        # Empty string = no reason to rebuild
        return ""

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        _config   = self._config
        verbose   = _config.eval("verbose")
        debug     = _config.eval("debug")

        if verbose or debug:
            Log.log(Utils.color(128, 128, 255), end="")
            if _config.dry_run:
                Log.log("(DRY RUN) ", end="")
            Log.log(f"{Path.rel_path(_config.task_dir, _config.repo_dir)}$ ", end="")
            Log.log(Utils.color(), end="")
            Log.log(command)

        # Dry runs get early-out'ed before we do anything.
        if _config.dry_run:
            return

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            old_cwd = os.getcwd()
            try:
                os.chdir(_config.task_dir)
                await Utils.await_variant(command(self))
            finally:
                os.chdir(old_cwd)
                self._returncode = 0
            return

        # Non-string non-callable commands are not valid
        if not isinstance(command, str):
            raise ValueError(f"Don't know what to do with {command}")

        # Create the subprocess via asyncio and then await the result.
        if debug:
            Log.log(f"Task {hex(id(self))} subprocess start '{command}'")

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd    = _config.task_dir,
            stdout = asyncio.subprocess.PIPE,
            stderr = asyncio.subprocess.PIPE,
        )
        (stdout_data, stderr_data) = await proc.communicate()

        if debug:
            Log.log(f"Task {hex(id(self))} subprocess done '{command}'")

        self._stdout = stdout_data.decode()
        self._stderr = stderr_data.decode()
        self._returncode = Utils.check(int, proc.returncode)

        # We need a better way to handle "should fail" so we don't constantly keep rerunning
        # intentionally-failing tests every build
        command_pass = (self._returncode == 0) != _config.eval("should_fail")

        if not command_pass:
            message = f"CommandFailure: Command exited with return code {self._returncode}\n"
            if self._stdout:
                message += "Stdout:\n"
                message += self._stdout
            if self._stderr:
                message += "Stderr:\n"
                message += self._stderr
            raise ValueError(message)

        if debug or verbose:
            Log.log(
                f"{Utils.color(128,255,196)}[{self._task_index}/{Stats.tasks_started}]{Utils.color()} Task passed - '{self._desc}'"
            )
            if self._stdout:
                Log.log("Stdout:")
                Log.log(self._stdout, end="")
            if self._stderr:
                Log.log("Stderr:")
                Log.log(self._stderr, end="")

#endregion
####################################################################################################
#region Runner

class Runner:

    all_tasks      : list[Task] = []
    queued_tasks   : list[Task] = []
    started_tasks  : list[Task] = []
    finished_tasks : list[Task] = []

    @classmethod
    def run_tool(cls, tool : str):
        print(f"Running tool {tool}")

        if tool == "clean":
            print("Deleting build directories")
            build_roots = set()
            for task in cls.all_tasks:
                build_root = Path.real(task._config.eval("build_root"))
                if os.path.isdir(build_root):
                    build_roots.add(build_root)
            for root in build_roots:
                print(f"Deleting build root {root}")
                shutil.rmtree(root, ignore_errors=True)
            return 0

        assert False, f"Don't know how to run tool {tool}"

    ########################################
    # FIXME selecting targets by regex needs revisiting

    @classmethod
    def select_tasks_by_regex(cls, target_regex : re.Pattern[str]):
        for task in cls.all_tasks:
            queue_task = False
            task_name = None
            # This doesn't work because we haven't expanded output filenames yet
            # for out_file in flatten(task._out_files):
            #    if self.target_regex.search(out_file):
            #        queue_task = True
            #        task_name = out_file
            #        break
            if name := task._config.eval("name"):
                if target_regex.search(name):
                    queue_task = True
                    task_name = name
            if queue_task:
                Log.log(f"Queueing task for '{task_name}'")
                task.queue()

    ########################################
    # If no target was specified, we queue up all tasks that build stuff in the root repo
    # FIXME we are not currently doing that....

    @classmethod
    def select_root_tasks(cls, _root_mod):
        for task in cls.all_tasks:
            # build_dir = expand_variant(task._context, task._context.build_dir)
            # build_dir = normalize_path(build_dir)
            # repo_dir  = expand_variant(app.root_context._context, "{build_dir}")
            # repo_dir  = normalize_path(repo_dir)
            # print(build_dir)
            # print(repo_dir)
            # if build_dir.startswith(repo_dir):
            #    task.queue()
            task.queue()

    ########################################

    @classmethod
    def run_tasks(cls):
        """Run tasks until we're done with all of them."""
        JobPool.reset(root_config.jobs)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = asyncio.run(cls._async_run_tasks())
        loop.close()
        return result

    ########################################

    @classmethod
    def cancel_all_tasks(cls):
        for task in cls.started_tasks:
            if task._asyncio_task is not None:
                task._asyncio_task.cancel()
                tasks_cancelled += 1

    ########################################

    @classmethod
    def log_task_failure(cls, task):
        Log.log(Utils.color(255, 128, 0), end="")
        Log.log(f"Task failed: {task._desc}")
        Log.log(Utils.color(), end="")
        Log.log(str(task))
        Log.log(Utils.color(255, 128, 128), end="")
        Log.log(traceback.format_exc())
        Log.log(Utils.color(), end="")

    ########################################

    @classmethod
    async def _async_run_tasks(cls):
        """Run all tasks in the queue until we run out."""

        # Tasks can create other tasks, and we don't want to block waiting on a whole batch of
        # tasks to complete before queueing up more. Instead, we just keep queuing up any pending
        # tasks after awaiting each one. Because we're awaiting tasks in the order they were
        # created, this will effectively walk through all tasks in dependency order.

        while cls.queued_tasks or cls.started_tasks:
            if root_config.shuffle:
                Log.log(f"Shufflin' {len(cls.queued_tasks)} tasks")
                random.shuffle(cls.queued_tasks)

            while cls.queued_tasks:
                task = cls.queued_tasks.pop(0)
                task.start()
                cls.started_tasks.append(task)

            task = cls.started_tasks.pop(0)
            asyncio_task = Utils.check(asyncio.Task, task._asyncio_task)

            try:
                await asyncio_task
                cls.finished_tasks.append(task)
            except BaseException:  # pylint: disable=broad-exception-caught
                cls.log_task_failure(task)
                fail_count = Stats.tasks_failed + Stats.tasks_cancelled + Stats.tasks_broken
                if root_config.keep_going and fail_count >= root_config.keep_going:
                    Log.log("Too many failures, cancelling tasks and stopping build")
                    cls.cancel_all_tasks()
                    break

        return -1 if Stats.tasks_failed or Stats.tasks_broken else 0

#endregion
####################################################################################################
#region flags

def parse_flags(argv):
    assert Utils.listlike(argv)

    # pylint: disable=line-too-long
    # fmt: off
    parser = argparse.ArgumentParser()
    parser.add_argument("target",             default=None, nargs="?", type=str,   help="A regex that selects the targets to build. Defaults to all targets.")
    parser.add_argument("-v", "--verbose",    default=False, action="store_true",  help="Show verbose build info")
    parser.add_argument("-q", "--quiet",      default=False, action="store_true",  help="Mute all output")

    parser.add_argument("-C", "--root_dir",   default=os.getcwd(),     type=str,   help="Change directory before starting the build")
    parser.add_argument("-f", "--root_file",  default="build.hancho",  type=str,   help="Input .hancho file - defaults to 'build.hancho'")

    parser.add_argument("-j", "--jobs",       default=os.cpu_count(),  type=int,   help="Run N jobs in parallel (default = cpu_count)")
    parser.add_argument("-k", "--keep_going", default=1,     type=int,             help="Keep going until N jobs fail (0 means infinity)")
    parser.add_argument("-n", "--dry_run",    default=False, action="store_true",  help="Do not run commands")

    parser.add_argument("-d", "--debug",      default=False, action="store_true",  help="Print debugging information")
    parser.add_argument("-t", "--tool",       default=None,  type=str,             help="Run a subtool.")

    parser.add_argument("--build_tag",        default=None,  type=str,             help="Set the build tag. Tagged builds will have separate subdirectories under the build directory.")
    parser.add_argument("--rebuild",          default=False, action="store_true",  help="Rebuild everything")
    parser.add_argument("--shuffle",          default=False, action="store_true",  help="Shuffle task order to shake out dependency issues")
    parser.add_argument("--trace",            default=False, action="store_true",  help="Trace all text expansion")
    parser.add_argument("--use_color",        default=True,  action="store_true",  help="Use color in the console output")
    # fmt: on

    (flags, unrecognized) = parser.parse_known_args(argv)

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

    return (Dict(vars(flags)), Dict(extra_flags))

#endregion
####################################################################################################
#region API
# Everything in here is what the client's .hancho files should be using.

# This is explicitly set by the top-level Hancho module, and is set by parent modules for each
# child module.

#config = Dict()
# load
# repo
# Dict
# Task
# Tool
# flatten, other stuff from utils

# fmt: off
#path        = path # path.dirname and path.basename used by makefile-related rules
#re          = re # why is sub() not working?
#glob        = staticmethod(glob.glob)
#ext         = staticmethod(Path.ext)
#rel_path    = staticmethod(Path.rel_path)  # used by build_path etc
#stem        = staticmethod(Path.stem)      # FIXME used by metron/tests?
# fmt: on


#endregion
####################################################################################################
#region Main

def queue_tasks(root_mod):
    time_a = time.perf_counter()
    if root_mod.hancho.config.target:
        target_regex = re.compile(root_mod.hancho.config.target)
        Runner.select_tasks_by_regex(target_regex)
    else:
        Runner.select_root_tasks(root_mod)
    Stats.time_queue = time.perf_counter() - time_a
    # if flags.debug or flags.verbose:
    Log.log(f"Queueing {len(Runner.queued_tasks)} tasks took {Stats.time_queue:.3f} seconds")

####################################################################################################

def run_tasks(root_mod):
    time_a = time.perf_counter()
    result = Runner.run_tasks()
    Stats.time_build = time.perf_counter() - time_a
    Stats.print_build_stats()
    return result

####################################################################################################

def main():
    (flags, extra_flags) = parse_flags(sys.argv[1:])

    root_path = os.path.join(flags.root_dir, flags.root_file)
    root_path_real = os.path.realpath(root_path)
    (root_dir_real, root_file) = os.path.split(root_path_real)

    assert os.path.isabs (root_path_real)
    assert os.path.isfile(root_path_real)
    assert os.path.isabs (root_dir_real)
    assert os.path.isdir (root_dir_real)

    # We spell all these defaults out explicitly so that when this config gets merged with task
    # configs the fields stay in the same order.

    config_defaults = dict(
        root_dir   = root_dir_real,
        root_file  = root_file,

        repo_dir   = root_dir_real,
        repo_file  = root_file,

        this_dir   = root_dir_real,
        this_file  = root_file,

        task_dir   = "{this_dir}",

        build_root = "{repo_dir}/build",
        build_tag  = flags.build_tag,
        build_dir  = "{build_root}/{build_tag}/{rel_path(task_dir, repo_dir)}",

        target     = flags.target,
        jobs       = flags.jobs,
        keep_going = flags.keep_going,
        tool       = flags.tool,
        verbose    = flags.verbose,
        debug      = flags.debug,
        dry_run    = flags.dry_run,
        quiet      = flags.quiet,
        rebuild    = flags.rebuild,
        shuffle    = flags.shuffle,
        trace      = flags.trace,
        use_color  = flags.use_color,
    )

    # FIXME forcing verbose on here
    global root_config
    root_config = Dict(config_defaults, extra_flags, verbose = True)

#    ########################################
#    ### XXX REMOVE ME
#
#    print(config)
#
#    if True:
#        sys.exit(0)
#
#    ### XXX REMOVE ME
#    ########################################

    time_a = time.perf_counter()
    root_script_path =os.path.join(root_config.root_dir, root_config.root_file)
    root_mod = Loader.load_repo(root_script_path, root_config)
    Stats.time_load = time.perf_counter() - time_a
    if root_config.debug or root_config.verbose:
        Log.log(f"Loading .hancho files took {Stats.time_load:.3f} seconds")

    if root_mod.hancho.config.tool:
        result = Runner.run_tool(root_mod.hancho.config.tool)
    else:
        queue_tasks(root_mod)
        result = run_tasks(root_mod)

    return result

#endregion
####################################################################################################

if __name__ == "__main__":
    a = Dict(a = 1, b = 2, c = 3, d = [4, 5, 6], e = Dict(f = 5, g = 6))
    print(a)
    b = Utils.map_variant(None, a, lambda key, val: val + 1)
    print(b)
    sys.exit(0)

    #sys.exit(main())

# This is here to make the type checker not complain about references to "hancho.config" in build
# scripts, even though it's not declared in this module. The script loader will inject a script-
# specific config object via hancho.config before the script runs, so it will resolve correctly
# at runtime.
config : Dict

# These two functions are to make the type checker not complain about load/repo, which are actually
# lambdas bound to the current script's config in HanchoProxy.
def load(script_path, *args, **kwargs):
    assert False, "Nothing should be using the top-level hancho.load stub!"

def repo(script_path, *args, **kwargs):
    assert False, "Nothing should be using the top-level hancho.repo stub!"

def task(*args, **kwargs):
    assert False, "Nothing should be using the top-level hancho.task stub!"