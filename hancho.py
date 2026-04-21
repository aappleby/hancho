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

####################################################################################################
#region imports

from os import path
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
from typing import Any, cast, Type, overload
from collections import abc

type str_tree = str | list[str_tree]
_MISSING = object()
trace = False

#endregion
####################################################################################################
#region Path manipulation
class Path:
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
            return path.abspath(raw_path)
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
        result = [path.join(l, r) for l in flat_lhs for r in flat_rhs]
        return result[0] if len(result) == 1 else result

    @staticmethod
    def isnorm(file_path : str) -> bool:
        return file_path == Path.norm(file_path)

    @staticmethod
    def isreal(file_path : str) -> bool:
        return file_path == Path.real(file_path)

    @staticmethod
    def norm(file_path : str) -> str:
        assert not Utils.is_template(file_path), f"Can't use a template as a path : {file_path}"
        file_path = path.join(os.getcwd(), file_path)
        file_path = path.normpath(file_path)
        return file_path

    @staticmethod
    def real(file_path : str) -> str:
        assert not Utils.is_template(file_path), f"Can't use a template as a path : {file_path}"
        file_path = Path.norm(file_path)
        file_path = path.realpath(file_path)
        return file_path

    @staticmethod
    def split(file_path : str) -> tuple[str, str, str]:
        (file_dir, file_name) = path.split(file_path)
        (file_stem, file_ext) = path.splitext(file_name)
        return (file_dir, file_stem, file_ext)

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
            return path.normpath(val)
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
            return path.splitext(name)[0] + new_ext
        else:
            assert False, f"ext() Don't know what to do with a {type(name).__name__}"

    #FIXME shouldn't this do the dynamic dispatch thing like above?
    @staticmethod
    def stem(filename : str_tree) -> str:
        flat_names : list[str] = Utils.flatten(filename)
        flat_filename : str = flat_names[0]
        base_filename : str = path.basename(flat_filename)
        return path.splitext(base_filename)[0]

#endregion
####################################################################################################
#region Utils

class Utils:
    # fmt: off
    path        = path # path.dirname and path.basename used by makefile-related rules
    re          = re # why is sub() not working?
    glob        = staticmethod(glob.glob)
    ext         = staticmethod(Path.ext)
    rel_path    = staticmethod(Path.rel_path)  # used by build_path etc
    stem        = staticmethod(Path.stem)      # FIXME used by metron/tests?
    #hancho_dir  = path.dirname(path.realpath(__file__))
    # fmt: on

    @staticmethod
    def log(message : str, *, sameline : bool = False, **kwargs):
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

        def log_line(message : str):
            g_app.log += message
            if not g_app.flags.quiet:
                sys.stdout.write(message)
                sys.stdout.flush()

        if sameline:
            output = output[: os.get_terminal_size().columns - 1]
            output = "\r" + output + "\x1B[K"
            log_line(output)
        else:
            if g_app.line_dirty:
                log_line("\n")
            log_line(output)

        g_app.line_dirty = sameline

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

    @staticmethod
    def color(red=None, green=None, blue=None):
        """Converts RGB color to ANSI format string."""
        # Color strings don't work in Windows console, so don't emit them.
        # if not g_app.flags.use_color or os.name == "nt":
        #    return ""
        if red is None:
            return "\x1B[0m"
        return f"\x1B[38;2;{red};{green};{blue}m"

    @staticmethod
    def run_cmd(cmd : str):
        """Runs a console command synchronously and returns its stdout with whitespace stripped."""
        return subprocess.check_output(cmd, shell=True, text=True).strip()

    @staticmethod
    def mtime(filename : str):
        """Gets the file's mtime and tracks how many times we've called mtime()"""
        g_app.mtime_calls += 1
        return os.stat(filename).st_mtime_ns

    @staticmethod
    def flatten(variant : Any) -> list[Any]:
        if Utils.listlike(variant):
            return [x for element in variant for x in Utils.flatten(element)]
        if variant is None:
            return []
        return [variant]

    #@staticmethod
    #def to_dict():
    #    result = {}
    #    for (key, val) in Utils.__dict__.items():
    #        if not key.startswith("_"):
    #            result[key] = val
    #    return result

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
        return Dumper(2).dump(self)

    def dump(self, depth):
        return Dumper(depth).dump(self)

    ########################################
    # Expander stuff

    def get(self, key : str, default : Any = _MISSING) -> Any:
        key = Expander(self).expand(key)
        if key in self:
            result = dict.get(self, key)
        else:
            if default == _MISSING:
                raise AttributeError(f"Dict.get - did not have key {key} and no default provided")
            else:
                result = default
        return result

    def eval(self, expr : str) -> Any:
        return Expander(self).eval(expr)

    def expand(self, text : str):
        return Expander(self).expand(text)

########################################

class Tool(Dict):
    pass

#endregion
####################################################################################################
#region Hancho's text expansion system.
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
# The depth checks are to prevent recursive runaway - the MAX_EXPAND_DEPTH limit is arbitrary but
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

    # The maximum number of recursion levels we will do to expand a macro.
    # Tests currently require MAX_EXPAND_DEPTH >= 6
    MAX_EXPAND_DEPTH = 20

    # FIXME need tests for brace-delimited sections inside quote-delimited strings, etc

    expansion_globals = dict(
        os   = os,
        sys  = sys,
        path = path,
        re   = re,
        glob = glob,

        ext     = Utils.ext,
        rel     = Utils.rel_path,
        stem    = Utils.stem,
        #name    = Utils.name
        log     = Utils.log,
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

    def __init__(self, context : abc.Mapping):
        object.__setattr__(self, "_context", context)
        # We save a copy of 'trace', otherwise we end up printing traces of reading trace.... :P
        #self.trace = context.get("trace", g_app.flags.trace)

    def __contains__(self, key):
        return key in self._context

    def __getitem__(self, key):
        return self.get(key)

    def __getattr__(self, key):
        return self.get(key)

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

    ########################################

    def get(self, key : str, default = _MISSING) -> Any:
        key = self.expand(key)

        if key in self._context:
            val = self._context[key]
            if isinstance(val, str):
                val = self.expand(val)
        elif default is not _MISSING:
            val = default
        else:
            # If the key is not found, raise an AttributeError.
            #if self.trace:
            #    Utils.log(Tracer.trace_prefix(self) + f"┃ Read '{key}' failed")
            raise AttributeError(key)

        #if self.trace:
        #    Utils.log(Tracer.trace_prefix(self) + f"┃ Read '{key}' = {Tracer.trace_variant(val)}")

        # If we fetched a mapping, wrap it in an Expander so we expand its sub-fields.
        if isinstance(val, abc.Mapping):
            val = Expander(val)
        return val

    ########################################
    # Returns a relative path from the task directory to the sub_path.

    def rel(self, sub_path):
        task_dir = self.eval("_task_dir")
        result = Path.rel_path(sub_path, task_dir)
        return result

    ########################################

    def __repr__(self):
        result = f"{self.__class__.__name__} @ {hex(id(self))} wraps "
        result += Dumper(2).dump(self.config)
        return result

    ########################################

    @staticmethod
    def stringify_variant(variant):
        """Converts any type into a template-compatible string."""
        if variant is None:
            return ""
        elif Utils.listlike(variant):
            variant = [Expander.stringify_variant(val) for val in variant]
            return " ".join(variant)
        else:
            return str(variant)

    ########################################
    # FIXME we need full-loop test cases for escaped {}s.
    # Somewhere in the process we need to unescape them and I'm not sure where it goes.

    @staticmethod
    def split(text):
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
                    result.append(Expander.Lit(text[cursor:lbrace]))
                result.append(Expander.Expr(text[lbrace+1:rbrace]))
                cursor = rbrace + 1
                lbrace = -1
                rbrace = -1

        if cursor < len(text):
            result.append(Expander.Lit(text[cursor:]))

        return result

    ########################################

    def eval(self, expr : str) -> Any: # , trace : bool
        """
        Expander.eval first expands the expression (to remove any templates) and then evaluates
        and returns the result.
        """

        expr = self.expand(expr)

        if trace:
            Tracer.log_trace(self, f"┏ eval {expr}")

        try:
            result = eval(expr, Expander.expansion_globals, self)
            if trace:
                Tracer.log_trace(self, f"┗ eval {expr} = {result}")
        except SyntaxError:
            # If the expression was not valid Python, return it verbatim.
            if trace:
                Tracer.log_trace(self, f"┗ eval failed, Python could not parse '{expr}'")
            # We can tag the failed evals if needed
            #result = "X" + expr
            result = expr
        except Exception as e:
            # If any other error happened while evaluating the expression, return the expression verbatim.
            if trace:
                Tracer.log_trace(self, f"┗ eval failed, evaluating '{expr}' generated {type(e).__name__}: {e}")
            # We can tag the failed evals if needed
            #result = "X" + expr
            result = expr

            # We can make this fatal instead of a no-op, not sure if that's more ergonomic...
            #raise

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

        g_app.expand_depth += 1
        if g_app.expand_depth > Expander.MAX_EXPAND_DEPTH:
            raise RecursionError("TemplateRecursion: Text expansion failed to terminate")

        if trace:
            Tracer.log_trace(self, f"┏ expand '{template}'")

        blocks = Expander.split(template)
        for (i, block) in enumerate(blocks):
            if isinstance(block, Expander.Lit):
                continue
            try:
                block = eval(block, Expander.expansion_globals, self)
                block = Expander.stringify_variant(block)
            except:
                block = "{" + block + "}"
            blocks[i] = block

        result = "".join(blocks)
        if result != template:
            result = self.expand(result)

        if trace:
            Tracer.log_trace(self, f"┗ expand '{template}' = '{result}'")

        g_app.expand_depth -= 1
        return result

#endregion
####################################################################################################
# region Expansion tracing class used by Expander

class Tracer:
    @staticmethod
    def id_to_color(obj):
        random.seed(id(obj))
        return Utils.color(random.randint(64, 255), random.randint(64, 255), random.randint(64, 255))

    @staticmethod
    def log_trace(config, text):
        """Prints a trace message to the log."""
        prefix = Tracer.id_to_color(config) + hex(id(config)) + Utils.color() + ": " + ("┃ " * g_app.expand_depth)
        Utils.log(prefix + text)

    @staticmethod
    def trace_prefix(context):
        """Prints the left-side trellis of the expansion traces."""
        return hex(id(context)) + ": " + ("┃ " * g_app.expand_depth)

    @staticmethod
    def trace_variant(variant):
        """Prints the right-side values of the expansion traces."""
        if callable(variant):
            return f"Callable @ {hex(id(variant))}"
        elif isinstance(variant, Dict):
            return f"Dict @ {hex(id(variant))}'"
        elif isinstance(variant, Expander):
            return f"Expander @ {hex(id(variant._context))}'"
        else:
            return f"'{variant}'"

#endregion
####################################################################################################
#region Pretty-printer for various types

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
#
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
            return self.task._context[self.args[0]]
        else:
            return [self.task._context[field] for field in self.args]

#endregion
####################################################################################################
# region Task object + bookkeeping

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

    default_desc = "{command}"
    default_command = None
    default_task_dir = "{mod_dir}"
    default_build_dir = "{build_root}/{build_tag}/{rel_path(task_dir, repo_dir)}"
    default_build_root = "{repo_dir}/build"
    default_build_tag = ""

    def __init__(self, *args, **kwargs):

        default_context = Dict(
            desc = Task.default_desc,
            command = Task.default_command,
        )

        self._config : Dict = Dict(default_context, *args, **kwargs)

        self._desc : str = ""
        self._command : str = ""
        self._in_files : list[Any] = []
        self._out_files : list[Any] = []
        self._task_index : int = 0
        self._state : str = Task.DECLARED
        self._reason : str = ""
        self._asyncio_task : asyncio.Task | None = None
        self._loaded_files : list[str] = list(g_app.loaded_files)
        self._stdout : str = ""
        self._stderr : str = ""
        self._returncode : int = -1

        self._repo_dir : str = ""
        self._task_dir : str = ""
        self._build_dir : str = ""

        g_app.all_tasks.append(self)

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
                return val
            Task.map_variant(None, self._config, apply)

            # And now queue this task.
            g_app.queued_tasks.append(self)
            self._state = Task.QUEUED

    def start(self):
        self.queue()
        if self._state is Task.QUEUED:
            self._asyncio_task = asyncio.create_task(self.task_main())
            self._state = Task.STARTED
            g_app.tasks_started += 1

    async def await_done(self):
        self.start()
        assert self._asyncio_task is not None
        await self._asyncio_task

    def promise(self, *args):
        return Promise(self, *args)

    def print_status(self):
        """Print the "[1/N] Compiling foo.cpp -> foo.o" status line and debug information"""

        verbosity = self._config.get_expanded(bool, "verbosity", Utils.check(g_app.flags.verbosity, bool))
        Utils.log(
            f"{Utils.color(128,255,196)}[{self._task_index}/{g_app.tasks_started}]{Utils.color()} {self._config.desc}",
            sameline=verbosity == 0,
        )

    async def task_main(self):
        """Entry point for async task stuff, handles exceptions generated during task execution."""

        verbosity = self._config.get("{verbosity}", g_app.flags.verbosity)
        debug     = self._config.get("{debug}",     g_app.flags.debug)
        rebuild   = self._config.get("{rebuild}",   g_app.flags.rebuild)

        # Await everything awaitable in this task's config.
        # If any of this tasks's dependencies were cancelled, we propagate the cancellation to
        # downstream tasks.
        try:
            assert self._state is Task.STARTED
            self._state = Task.AWAITING_INPUTS
            for key, val in self._config.items():
                # FIXME this isn't going to work with immutable Dicts
                self._config[key] = await Task.await_variant(val)
        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # Exceptions during awaiting inputs means that this task cannot proceed, cancel it.
            self._state = Task.CANCELLED
            g_app.tasks_cancelled += 1
            raise asyncio.CancelledError() from ex

        # Everything awaited, task_init runs synchronously.
        try:
            self._state = Task.TASK_INIT

            # Note that we chdir to task_dir before initializing the task so that any path.abspath
            # or whatever happen from the right place

            task_dir = self._config.get_expanded(str, "task_dir")
            assert isinstance(task_dir, str)
            try:
                g_app.pushdir(task_dir)
                self.task_init()
            finally:
                g_app.popdir()

        except asyncio.CancelledError as ex:
            # We discovered during init that we don't need to run this task.
            self._state = Task.CANCELLED
            g_app.tasks_cancelled += 1
            raise asyncio.CancelledError() from ex
        except BaseException as ex:  # pylint: disable=broad-exception-caught
            self._state = Task.BROKEN
            g_app.tasks_broken += 1
            raise ex

        # Early-out if this is a no-op task
        if self._command is None:
            g_app.tasks_finished += 1
            self._state = Task.FINISHED
            return

        # Check if we need a rebuild
        self._reason = self.needs_rerun(rebuild)
        if not self._reason:
            g_app.tasks_skipped += 1
            self._state = Task.SKIPPED
            return

        try:
            # Wait for enough jobs to free up to run this task.
            job_count = self._config.get("job_count", 1)
            self._state = Task.AWAITING_JOBS
            await g_app.job_pool.acquire_jobs(job_count, self)

            # Run the commands.
            self._state = Task.RUNNING_COMMANDS
            g_app.tasks_running += 1
            self._task_index = g_app.tasks_running

            self.print_status()
            if verbosity or debug:
                Utils.log(f"{Utils.color(128,128,128)}Reason: {self._reason}{Utils.color()}")

            for command in Utils.flatten(self._command):
                await self.run_command(command)
                if self._returncode != 0:
                    break

        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # If any command failed, we print the error and propagate it to downstream tasks.
            self._state = Task.FAILED
            g_app.tasks_failed += 1
            raise ex
        finally:
            await g_app.job_pool.release_jobs(job_count, self)

        # Task finished successfully
        self._state = Task.FINISHED
        g_app.tasks_finished += 1

    def task_init(self):
        """All the setup steps needed before we run a task."""

        # FIXME _all_ paths should be rel'd before running command. If you want abs, you can abs() it.

        debug = self._config.get("debug", g_app.flags.debug)
        if debug:
            Utils.log(f"\nTask before expand: {self}")

        # ----------------------------------------
        # Expand task_dir and build_dir

        # pylint: disable=attribute-defined-outside-init

        self._repo_dir   = Path.abs_path(self._config.get_expanded(str, "repo_dir"))
        self._task_dir   = Path.abs_path(self._config.get_expanded(str, "task_dir"))
        self._build_dir  = Path.abs_path(self._config.get_expanded(str, "build_dir"))

        # Check for missing input files/paths
        if not path.exists(self._task_dir):
            raise FileNotFoundError(self._task_dir)

        if not self._build_dir.startswith(self._repo_dir):
            raise ValueError(
                f"Path error, build_dir {self._build_dir} is not under repo dir {self._repo_dir}"
            )

        # ----------------------------------------
        # Expand all in_ and out_ filenames
        # We _must_ expand these first before joining paths or the paths will be incorrect:
        # prefix + swap(abs_path) != abs(prefix + swap(path))

        for key, val in self._config.items():
            if key.startswith("in_") or key.startswith("out_"):
                def expand_path(_, val):
                    if not isinstance(val, str):
                        return val
                    val = self._config.expand(val)
                    val = path.normpath(val) # type: ignore
                    return val
                self._config[key] = Task.map_variant(key, val, expand_path)

        # Make all in_ and out_ file paths absolute
        # FIXME feeling like in_depfile should really be io_depfile...

        # FIXME this did not merge cleanly and is broken

        for key, val in self._config.items():
            if key.startswith("out_") or key == "in_depfile":
                def move_to_builddir(_, val):
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
                    elif path.isabs(val):
                        raise ValueError(f"Output file has absolute path that is not under task_dir or build_dir : {val}")
                    else:
                        # Relative path, add build_dir
                        val = Path.join(self._config.build_dir, val)
                    return val
                self._config[key] = Task.map_variant(key, val, move_to_builddir)
            elif key.startswith("in_"):
                def move_to_taskdir(key, val):
                    if not isinstance(val, str):
                        return val
                    if not path.isabs(val):
                        val = Path.join(self._config.task_dir, val)
                    return val
                self._config[key] = Task.map_variant(key, val, move_to_taskdir)

        # Gather all inputs to task.in_files and outputs to task.out_files

        for key, val in self._config.items():
            # Note - we only add the depfile to in_files _if_it_exists_, otherwise we will fail a check
            # that all our inputs are present.
            if key == "in_depfile":
                if path.isfile(val):
                    self._in_files.append(val)
            elif key.startswith("out_"):
                self._out_files.extend(Utils.flatten(val))
            elif key.startswith("in_"):
                self._in_files.extend(Utils.flatten(val))


        # Make all in_ and out_ file paths absolute

        # FIXME I dislike all this "move_to" stuff

        # Gather all inputs to task._in_files and outputs to task._out_files

        def move_to_builddir2(file : str_tree) -> str_tree:
            build_dir = Utils.check(str, self._build_dir)

            if isinstance(file, list):
                return [move_to_builddir2(f) for f in file]

            # needed for test_bad_build_path
            file = path.normpath(file)

            # Note this conditional needs to be first, as build_dir can itself be under
            # task_dir
            if file.startswith(build_dir):
                # Absolute path under build_dir.
                pass
            elif file.startswith(build_dir):
                # Absolute path under task_dir, move to build_dir
                file = Path.rel_path(file, build_dir)
            elif path.isabs(file):
                raise ValueError(f"Output file has absolute path that is not under task_dir or build_dir : {file}")

            file = Path.join(Utils.check(str, build_dir), file)
            return file

        # pylint: disable=consider-using-dict-items
        for key in self._config.keys():

            file1 : str = self._config[key]
            file2 : str = self._config.expand(file1)

            if key.startswith("in_"):
                file3 : str = Path.normpath(file2)
                file3 : str = Path.join(self._task_dir, file3)
                self._in_files.extend(Utils.flatten(file3))
                self._config[key] = file3

            if key.startswith("out_"):
                file3 : str = Utils.check(str, move_to_builddir2(file2))
                self._out_files.extend(Utils.flatten(file3))
                self._config[key] = file3

            if key == "depfile":
                file3 : str = Utils.check(str, move_to_builddir2(file2))
                self._config[key] = file3

        # ----------------------------------------
        # And now we can expand the command.

        self._desc    = cast(str, self._config.expand(self._config.desc))
        self._command = cast(str, self._config.expand(self._config.command))

        if debug:
            Utils.log(f"\nTask after expand: {self}")

        # ----------------------------------------
        # Check for task collisions

        # FIXME need a test for this that uses symlinks

        #if self._out_files and self._context.command is not None:
        for file in self._out_files:
            real_file = path.realpath(file)
            if real_file in g_app.filename_to_fingerprint:
                raise ValueError(f"TaskCollision: Multiple tasks build {real_file}")
            g_app.filename_to_fingerprint[real_file] = real_file

        # ----------------------------------------
        # Sanity checks

        # Check for missing input files/paths
        if not path.exists(self._config.task_dir):
            raise FileNotFoundError(self._config.task_dir)

        for file in self._in_files:
            if file is None:
                raise ValueError("_in_files contained a None")
            if not path.exists(file):
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
                if file in g_app.all_out_files:
                    raise NameError(f"Multiple rules build {file}!")
                g_app.all_out_files.add(file)

        # Make sure our output directories exist
        if not g_app.flags.dry_run:
            for file in self._out_files:
                os.makedirs(path.dirname(file), exist_ok=True)

        if debug:
            Utils.log(f"\nTask after expand: {self}")

    def needs_rerun(self, rebuild=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""

        debug = self._config.get("debug", g_app.flags.debug)

        if rebuild:
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
        min_out = min(Utils.mtime(f) for f in self._out_files)

        if Utils.mtime(__file__) >= min_out:
            return "Rebuilding because hancho.py has changed"

        for file in self._in_files:
            if Utils.mtime(file) >= min_out:
                return f"Rebuilding because {file} has changed"

        for mod_filename in self._loaded_files:
            if Utils.mtime(mod_filename) >= min_out:
                return f"Rebuilding because {mod_filename} has changed"

        # Check all dependencies in the C dependencies file, if present.
        if (in_depfile := self._config.get("in_depfile", None)) and path.exists(in_depfile):
            depformat = self._config.get("depformat", "gcc")
            if debug:
                Utils.log(f"Found C dependencies file {in_depfile}")
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
                deplines = [path.join(self._config.task_dir, d) for d in deplines]
                for abs_file in deplines:
                    if Utils.mtime(abs_file) >= min_out:
                        return f"Rebuilding because {abs_file} has changed"

        # All checks passed; we don't need to rebuild this output.
        # Empty string = no reason to rebuild
        return ""

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        verbosity = self._config.get_expanded(bool, "verbosity", g_app.flags.verbosity)
        debug     = self._config.get_expanded(bool, "debug", g_app.flags.debug)

        if verbosity or debug:
            Utils.log(Utils.color(128, 128, 255), end="")
            if g_app.flags.dry_run:
                Utils.log("(DRY RUN) ", end="")
            Utils.log(f"{Path.rel_path(self._config.task_dir, self._config.repo_dir)}$ ", end="")
            Utils.log(Utils.color(), end="")
            Utils.log(command)

        # Dry runs get early-out'ed before we do anything.
        if g_app.flags.dry_run:
            return

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            g_app.pushdir(self._config.task_dir)
            await Task.await_variant(command(self))
            g_app.popdir()
            self._returncode = 0
            return

        # Non-string non-callable commands are not valid
        if not isinstance(command, str):
            raise ValueError(f"Don't know what to do with {command}")

        # Create the subprocess via asyncio and then await the result.
        if debug:
            Utils.log(f"Task {hex(id(self))} subprocess start '{command}'")

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=self._config.task_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        (stdout_data, stderr_data) = await proc.communicate()

        if debug:
            Utils.log(f"Task {hex(id(self))} subprocess done '{command}'")

        self._stdout = stdout_data.decode()
        self._stderr = stderr_data.decode()
        self._returncode = Utils.check(int, proc.returncode)

        # We need a better way to handle "should fail" so we don't constantly keep rerunning
        # intentionally-failing tests every build
        command_pass = (self._returncode == 0) != self._config.get("should_fail", False)

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
            Utils.log(
                f"{Utils.color(128,255,196)}[{self._task_index}/{g_app.tasks_started}]{Utils.color()} Task passed - '{self._desc}'"
            )
            if self._stdout:
                Utils.log("Stdout:")
                Utils.log(self._stdout, end="")
            if self._stderr:
                Utils.log("Stderr:")
                Utils.log(self._stderr, end="")

    @staticmethod
    def map_variant(key, val, apply):
        val = apply(key, val)
        if Utils.dictlike(val):
            for key2, val2 in val.items():
                val[key2] = Task.map_variant(key2, val2, apply)
        elif Utils.listlike(val):
            for key2, val2 in enumerate(val):
                val[key2] = Task.map_variant(key2, val2, apply)
        return val

    @staticmethod
    async def await_variant(variant):
        """Recursively replaces every awaitable in the variant with its awaited value."""

        if Utils.listlike(variant):
            for key, val in enumerate(variant):
                variant[key] = await Task.await_variant(val)
            return variant

        if isinstance(variant, Promise):
            return await Task.await_variant(await variant.get())

        if isinstance(variant, Task):
            await variant.await_done()
            return await Task.await_variant(variant._out_files)

        if inspect.isawaitable(variant):
            return await Task.await_variant(await variant)

        return variant


#endregion
####################################################################################################
#region Hancho API object
# This is what gets passed into .hancho files

class HanchoAPI:

    def __init__(self, config, is_repo):
        self.config  = config
        self.is_repo = is_repo

    def __repr__(self):
        return Dumper(2).dump(self)

    def __contains__(self, key):
        return key in self.__dict__

    def __call__(self, arg1=None, /, *args, **kwargs):
        if callable(arg1):
            temp_config = Dict(*args, **kwargs)
            # Note that we spread temp_config so that we can take advantage of parameter list
            # checking when we call the callback.
            return arg1(self, **temp_config)
        return Task(self.config, arg1, *args, **kwargs)

    def repo(self, mod_path, *args, **kwargs):
        mod_path = self.config.expand(str, mod_path)
        mod_path = Path.real(mod_path)
        #real_path = path.realpath(mod_path)

        dedupe = g_app.realpath_to_repo.get(mod_path, None)
        if dedupe is not None:
            return dedupe

        new_api = create_repo(mod_path, *args, **kwargs)

        result = new_api._load()
        g_app.realpath_to_repo[mod_path] = result
        return result

    def load(self, mod_path : str):
        mod_path = self.config.expand(str, mod_path)
        mod_path = Path.norm(mod_path)
        new_module = create_mod(self, mod_path)
        return new_module._load()

    def _load(self):
        #if len(app.dirstack) == 1 or app.flags.verbosity or app.flags.debug:
        if True:
            #mod_path = Path.rel_path(self.config.mod_path, self.config.repo_dir)
            mod_path = Path.rel_path(self.config.mod_path, g_app.flags.root_dir)
            Utils.log(("┃ " * (len(g_app.dirstack) - 1)), end="")
            if self.is_repo:
                Utils.log(Utils.color(128, 128, 255) + f"Loading repo {self.config.mod_path}" + Utils.color())
            else:
                Utils.log(Utils.color(128, 255, 128) + f"Loading file {self.config.mod_path}" + Utils.color())

        g_app.loaded_files.append(self.config.mod_path)

        # We're using compile() and FunctionType()() here beause exec() doesn't preserve source
        # code for debugging.
        file = open(self.config.mod_path, encoding="utf-8")
        source = file.read()
        code = compile(source, self.config.mod_path, "exec", dont_inherit=True)

        # We must chdir()s into the .hancho file directory before running it so that
        # glob() can resolve files relative to the .hancho file itself. We are _not_ in an async
        # context here so there should be no other threads trying to change cwd.
        g_app.pushdir(path.dirname(self.config.mod_path))

        #{
        #  '__annotations__': {},
        #  '__builtins__': <module 'builtins' (built-in)>,
        #  '__cached__': None,
        #  '__doc__': None,
        #  '__file__': '/home/aappleby/bin/hancho',
        #  '__loader__': <_frozen_importlib_external.SourceFileLoader object at 0x7ebe818004a0>,
        #  '__name__': '__main__',
        #  '__package__': None,
        #  '__spec__': None,
        #}

        self.Dict = Dict
        self.Tool = Tool
        self.flatten = Utils.flatten

        #self.Task = lambda()

        temp_globals = {
            "hancho"  : self,
            #"glob"    : glob.glob,
            #"run_cmd" : run_cmd,
            #"flatten" : flatten,
        }

        module_globals = dict(temp_globals)

        # Pylint is just wrong here
        # pylint: disable=not-callable
        types.FunctionType(code, module_globals)()
        g_app.popdir()

        # Module loaded, turn the module's globals into a dict that doesn't include __builtins__,
        # hancho, imports, and private fields so we don't have files that end up transitively
        # containing the universe
        new_module = Dict()
        for key, val in module_globals.items():
            #if key.startswith("_") or key == "hancho" or key == "config" or key == "task" or isinstance(val, type(sys)):
            if key.startswith("_") or key in temp_globals or isinstance(val, type(sys)):
                continue
            new_module[key] = val

        # Tack the config onto the module so people who load it can see the paths it was built with, etc.
        new_module['config'] = Dict(self.config)

        return new_module

#endregion
####################################################################################################
#region Job pool

class JobPool:
    def __init__(self):
        self.jobs_available = os.cpu_count() or 1
        self.jobs_lock = asyncio.Condition()
        self.job_slots = [None] * self.jobs_available

    def reset(self, job_count):
        self.jobs_available = job_count
        self.job_slots = [None] * self.jobs_available

    ########################################

    async def acquire_jobs(self, count, token):
        """Waits until 'count' jobs are available and then removes them from the job pool."""

        if count > g_app.flags.jobs:
            raise ValueError(f"Need {count} jobs, but pool is {g_app.flags.jobs}.")

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
#region Helper stuff that needs to go somewhere else

def create_repo(mod_path : str, *args, **kwargs) -> HanchoAPI:
    assert Path.isreal(mod_path)
    assert mod_path not in g_app.realpath_to_repo

    (hancho_dir, hancho_name, hancho_ext) = Path.split(__file__)
    (mod_dir, mod_name, mod_ext) = Path.split(mod_path)

    mod_config = Dict(
        hancho_dir  = hancho_dir,
        root_dir    = g_app.flags.root_dir,

        repo_path  = mod_path,
        repo_name  = mod_name,
        repo_dir   = mod_dir,
        repo_ext   = mod_ext,

        mod_path   = mod_path,
        mod_dir    = mod_dir,
        mod_name   = mod_name,
        mod_ext    = mod_ext,

        # These have to be here so that expand_variant(hancho._context, "{build_dir}") works.
        build_root = Task.default_build_root,
        build_tag  = Task.default_build_tag,
        build_dir  = Task.default_build_dir,

        task_dir   = Task.default_task_dir,

        *args, **kwargs
    )

    mod_api = HanchoAPI(mod_config, True)
    return mod_api

####################################################################################################

def create_mod(parent_api : HanchoAPI, in_mod_path : str, *args, **kwargs):
    assert isinstance(parent_api, HanchoAPI)

    mod_path = cast(str, parent_api.config.expand(str, in_mod_path))
    mod_path = Path.real(mod_path)
    (mod_dir, mod_name, mod_ext) = Path.split(mod_path)

    mod_api = copy.deepcopy(parent_api)
    mod_api.is_repo = False

    mod_config = Dict(
        mod_path = mod_path,
        mod_dir  = mod_dir,
        mod_name = mod_name,
        mod_ext  = mod_ext,
    )

    mod_api.config = Dict(mod_api.config, mod_config, *args, kwargs)

    return mod_api

########################################

def create_root_mod(flags, extra_flags):
    """ Needs to be its own function, used by run_tests.py """

    root_dir  = cast(str, flags.root_dir)
    root_file = cast(str, flags.root_file)
    root_path = Path.real(Path.join(root_dir, root_file))
    root_mod  = create_repo(root_path)

    # All the unrecognized flags get stuck on the root module's config.
    for key, val in extra_flags.items():
        setattr(root_mod.config, key, val)

    if root_mod.config.get("debug", False):
        Utils.log(f"root_mod = {Dumper(2).dump(root_mod)}")

    return root_mod

#endregion
####################################################################################################
#region Global app object.
# There's probably a better way to handle global state...

class App:

    def __init__(self):
        self.flags = argparse.Namespace()
        self.extra_flags : dict[str, Any] = {}

        self.root_mod : HanchoAPI | None = None
        self.loaded_files : list[str] = []
        self.dirstack : list[str] = [os.getcwd()]

        self.all_out_files : set = set()
        self.filename_to_fingerprint : dict[str, str] = {}

        self.realpath_to_repo : dict[str, Dict] = {}

        self.mtime_calls : int = 0
        self.line_dirty : bool = False
        self.expand_depth : int = 0
        self.shuffle : bool = False

        self.time_load  : float = 0
        self.time_queue : float = 0
        self.time_build : float = 0

        self.tasks_started : int = 0
        self.tasks_running : int = 0
        self.tasks_finished : int = 0
        self.tasks_failed : int = 0
        self.tasks_skipped : int = 0
        self.tasks_cancelled : int = 0
        self.tasks_broken : int = 0

        self.all_tasks : list[Task] = []
        self.queued_tasks : list[Task]  = []
        self.started_tasks : list[Task]  = []
        self.finished_tasks : list[Task]  = []
        self.log : str = ""

        self.job_pool : JobPool = JobPool()

    ########################################

    def reset(self):
        self.__init__()  # pylint: disable=unnecessary-dunder-call

    ########################################

    def load_root_mod(self):
        assert self.root_mod is not None

        if not path.isfile(self.root_mod.config.repo_path):
            print(
                f"Could not find Hancho file {self.root_mod.config.repo_path}!"
            )
            sys.exit(-1)

        os.chdir(self.root_mod.config.repo_dir)
        self.root_mod._load()

    ########################################

    def run_tool(self, tool : str):
        print(f"Running tool {tool}")

        if tool == "clean":
            print("Deleting build directories")
            build_roots = set()
            for task in self.all_tasks:
                build_root = Path.real(task._config.expand("{build_root}"))
                if path.isdir(build_root):
                    build_roots.add(build_root)
            for root in build_roots:
                print(f"Deleting build root {root}")
                shutil.rmtree(root, ignore_errors=True)
            return 0

        assert False, f"Don't know how to run tool {tool}"

    ########################################
    # FIXME selecting targets by regex needs revisiting

    def select_tasks_by_regex(self, target_regex : re.Pattern[str]):
        for task in self.all_tasks:
            queue_task = False
            task_name = None
            # This doesn't work because we haven't expanded output filenames yet
            # for out_file in flatten(task._out_files):
            #    if self.target_regex.search(out_file):
            #        queue_task = True
            #        task_name = out_file
            #        break
            if name := task._config.get_expanded(str, "name", None):
                if target_regex.search(name):
                    queue_task = True
                    task_name = name
            if queue_task:
                Utils.log(f"Queueing task for '{task_name}'")
                task.queue()

    ########################################
    # If no target was specified, we queue up all tasks that build stuff in the root repo
    # FIXME we are not currently doing that....

    def select_root_tasks(self):
        for task in self.all_tasks:
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

    def pushdir(self, new_dir : str):
        new_dir = abs_path(new_dir) # type: ignore
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
        self.job_pool.reset(self.flags.jobs)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = asyncio.run(self.async_run_tasks())
        loop.close()
        return result

    ########################################

    def print_build_stats(self):
        # Done, print status info if needed

        Utils.log(f"Running {self.tasks_finished} tasks took {self.time_build:.3f} seconds")

        if self.flags.debug or self.flags.verbosity:
            Utils.log(f"tasks started:   {self.tasks_started}")
            Utils.log(f"tasks finished:  {self.tasks_finished}")
            Utils.log(f"tasks failed:    {self.tasks_failed}")
            Utils.log(f"tasks skipped:   {self.tasks_skipped}")
            Utils.log(f"tasks cancelled: {self.tasks_cancelled}")
            Utils.log(f"tasks broken:    {self.tasks_broken}")
            Utils.log(f"mtime calls:     {self.mtime_calls}")

        if self.tasks_failed or self.tasks_broken:
            Utils.log(f"hancho: {Utils.color(255, 128, 128)}BUILD FAILED{Utils.color()}")
        elif self.tasks_finished:
            Utils.log(f"hancho: {Utils.color(128, 255, 128)}BUILD PASSED{Utils.color()}")
        else:
            Utils.log(f"hancho: {Utils.color(128, 128, 255)}BUILD CLEAN{Utils.color()}")

    ########################################

    async def async_run_tasks(self):
        """Run all tasks in the queue until we run out."""

        # Tasks can create other tasks, and we don't want to block waiting on a whole batch of
        # tasks to complete before queueing up more. Instead, we just keep queuing up any pending
        # tasks after awaiting each one. Because we're awaiting tasks in the order they were
        # created, this will effectively walk through all tasks in dependency order.

        while self.queued_tasks or self.started_tasks:
            if g_app.shuffle:
                Utils.log(f"Shufflin' {len(self.queued_tasks)} tasks")
                random.shuffle(self.queued_tasks)

            while self.queued_tasks:
                task = self.queued_tasks.pop(0)
                task.start()
                self.started_tasks.append(task)

            task = self.started_tasks.pop(0)
            asyncio_task = Utils.check(asyncio.Task, task._asyncio_task)

            try:
                await asyncio_task
                self.finished_tasks.append(task)
            except BaseException:  # pylint: disable=broad-exception-caught
                self.log_task_failure(task)
                fail_count = g_app.tasks_failed + g_app.tasks_cancelled + g_app.tasks_broken
                if g_app.flags.keep_going and fail_count >= g_app.flags.keep_going:
                    Utils.log("Too many failures, cancelling tasks and stopping build")
                    self.cancel_all_tasks()
                    break

        return -1 if self.tasks_failed or self.tasks_broken else 0

    ########################################

    def log_task_failure(self, task):
        Utils.log(Utils.color(255, 128, 0), end="")
        Utils.log(f"Task failed: {task._desc}")
        Utils.log(Utils.color(), end="")
        Utils.log(str(task))
        Utils.log(Utils.color(255, 128, 128), end="")
        Utils.log(traceback.format_exc())
        Utils.log(Utils.color(), end="")

    ########################################

    def cancel_all_tasks(self):
        for task in self.started_tasks:
            if task._asyncio_task is not None:
                task._asyncio_task.cancel()
                g_app.tasks_cancelled += 1

# Always create an App() object so we can use it for bookkeeping even if we loaded Hancho as a
# module instead of running it directly.

g_app = App()

#endregion
####################################################################################################
#region Main

def parse_flags(argv):
    assert Utils.listlike(argv)

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

    return (flags, extra_flags)

########################################

def main():
    (g_app.flags, g_app.extra_flags) = parse_flags(sys.argv[1:])

    g_app.root_mod = create_root_mod(g_app.flags, g_app.extra_flags)

    assert path.isabs (g_app.root_mod.config.repo_path)
    assert path.isfile(g_app.root_mod.config.repo_path)
    assert path.isabs (g_app.root_mod.config.repo_dir)
    assert path.isdir (g_app.root_mod.config.repo_dir)

    time_a = time.perf_counter()
    g_app.load_root_mod()
    g_app.time_load = time.perf_counter() - time_a
    if g_app.flags.debug or g_app.flags.verbosity:
        Utils.log(f"Loading .hancho files took {g_app.time_load:.3f} seconds")

    if g_app.flags.tool:
        result = g_app.run_tool(g_app.flags.tool)
        sys.exit(0)
    else:

        time_a = time.perf_counter()

        if g_app.flags.target:
            target_regex = re.compile(g_app.flags.target)
            g_app.select_tasks_by_regex(target_regex)
        else:
            g_app.select_root_tasks()

        g_app.time_queue = time.perf_counter() - time_a

        # if g_app.flags.debug or g_app.flags.verbosity:
        Utils.log(f"Queueing {len(g_app.queued_tasks)} tasks took {g_app.time_queue:.3f} seconds")

        time_a = time.perf_counter()
        result = g_app.build()
        g_app.time_build = time.perf_counter() - time_a
        g_app.print_build_stats()

        sys.exit(result)

# endregion
####################################################################################################
#region end

manual_test = False

if manual_test:
    print("manual test")

    d = Dict(a = "b", b = 1)
    e = d.eval("{{a}}")
    print(e)

    sys.exit(0)

if __name__ == "__main__":
    main()
else:
    (g_app.flags, g_app.extra_flags) = parse_flags([])

#import doctest
#doctest.testmod(verbose=True)
#doctest.testmod()

#endregion