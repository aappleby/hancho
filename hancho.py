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
import builtins
import copy
import functools
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
from typing import Any, TypeVar
from collections import abc

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
        for arg in (*args, kwargs):
            if arg is None:
                continue
            if not isinstance(arg, abc.Mapping):
                raise TypeError(f"Argument {arg} is not a mapping")
            for key, rval in arg.items():
                lval = self.get(key, None)

                # Recursively merge mapping-type attributes.
                if isinstance(lval, abc.Mapping) and isinstance(rval, abc.Mapping):
                    self[key] = Dict(lval, rval)

                # Deep copy all other attributes.
                elif lval is None or rval is not None:
                    dict.__setitem__(self, key, rval)

    def to_dict(self):
        return {k: v.to_dict() if isinstance(v, Dict) else v for k, v in self.items()}

    #def copy(self):
    #    return Dict(self)

    #def __copy__(self):
    #    return self.copy()

    #def __deepcopy__(self, memo):
    #    return Dict(copy.deepcopy(dict(self), memo))

    #def __setitem__(self, key, value):
    #    # Upgrade all mappings to Dict, make deep copies of everything else.
    #    if isinstance(value, abc.Mapping) and not isinstance(value, Dict):
    #        value = Dict(value)
    #    else:
    #        value = copy.deepcopy(value)
    #    super().__setitem__(key, value)

    def __setitem__(self, name, value):
        raise NotImplementedError("Hancho.Dict is immutable", name, value)

    def __delitem__(self, name):
        raise NotImplementedError("Hancho.Dict is immutable", name)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'") from e

    def __setattr__(self, name, value):
        raise NotImplementedError("Hancho.Dict is immutable", name, value)

    def __delattr__(self, name):
        raise NotImplementedError("Hancho.Dict is immutable", name)

    #def __setattr__(self, name, value):
    #    if hasattr(type(self), name):
    #        raise AttributeError(f"Cannot set attribute '{name}' on dict")
    #    self[name] = value

    #def __delattr__(self, name):
    #    if hasattr(type(self), name):
    #        raise AttributeError(f"Cannot delete attribute '{name}' on dict")
    #    try:
    #        del self[name]
    #    except KeyError as e:
    #        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'") from e

    def __repr__(self):
        return Dumper(2).dump(self)

    def dump(self, depth):
        return Dumper(depth).dump(self)

    def expand(self, text):
        return expand_variant(self, text)

    def get_expanded(self, key, default=None):
        macro = f"{{{key}}}"
        expanded = expand_variant(self, macro)
        if macro == expanded and default is not None:
            return default
        else:
            return expanded

class Tool(Dict):
    pass

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

#endregion
####################################################################################################
#region Path manipulation

#RecursiveStrList = str | list[Any] | None

#TypeThing = TypeVar('TypeThing', str, list[Any], None)

def abs_path[T](raw_path):
    if raw_path is None:
        return None
    elif listlike(raw_path):
        return [abs_path(p) for p in raw_path]
    elif isinstance(raw_path, str):
        return path.abspath(raw_path)
    else:
        return None

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


def normalize_path(file_path):
    assert isinstance(file_path, str)
    assert not istemplate(file_path)

    file_path = path.abspath(path.join(os.getcwd(), file_path))
    file_path = path.normpath(file_path)

    assert path.isabs(file_path)
    return file_path

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

def istemplate(variant):
    return isinstance(variant, str) and macro_regex.search(variant)

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
    # if not g_app.flags.use_color or os.name == "nt":
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
    g_app.mtime_calls += 1
    return os.stat(filename).st_mtime_ns

#endregion
####################################################################################################
#region Helpers for managing variants

# FIXME this doesn't work with immutable dicts
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

    result = Dict()
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
        return await await_variant(variant._out_files)

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
        elif isinstance(variant, Dict):
            result += self.dump_dict(variant)
        elif isinstance(variant, Expander):
            result += self.dump_dict(variant.config)
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

# Matches macros inside a string.
macro_regex = re.compile("{[^{}]*}")

def id_to_color(obj):
    random.seed(id(obj))
    return color(random.randint(64, 255), random.randint(64, 255), random.randint(64, 255))

def log_trace(config, text):
    """Prints a trace message to the log."""
    prefix = id_to_color(config) + hex(id(config)) + color() + ": " + ("┃ " * g_app.expand_depth)
    log(prefix + text)

def trace_prefix(context):
    """Prints the left-side trellis of the expansion traces."""
    return hex(id(context)) + ": " + ("┃ " * g_app.expand_depth)

def trace_variant(variant):
    """Prints the right-side values of the expansion traces."""
    if callable(variant):
        return f"Callable @ {hex(id(variant))}"
    elif isinstance(variant, Dict):
        return f"Dict @ {hex(id(variant))}'"
    elif isinstance(variant, Expander):
        return f"Expander @ {hex(id(variant.context))}'"
    else:
        return f"'{variant}'"

def stringify_variant(variant):
    """Converts any type into a template-compatible string."""
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

    def __init__(self, context):
        assert isinstance(context, dict)
        self.context = context
        # We save a copy of 'trace', otherwise we end up printing traces of reading trace.... :P
        self.trace = context.get("trace", g_app.flags.trace)

    def __contains__(self, key):
        return hasattr(Expander, key) or hasattr(Utils, key) or key in self.context

    def __getitem__(self, key):
        return self.get(key)

    def __getattr__(self, key):
        return self.get(key)

    def get(self, key):
        # Check to see if we're fetching an Expander method. Note we getattr(self, key) so that the
        # method is called on the Expander instance, not the class.
        if hasattr(Expander, key):
            val = getattr(self, key)
        # Check to see if we're fetching a special method from the Utils class.
        elif hasattr(Utils, key):
            val = getattr(Utils, key)
        # Neither of those special cases apply, so we fetch the key from the context and expand it
        # immediately.
        elif key in self.context:
            val = expand_variant(self.context, self.context[key])
        # If the key is not found, raise an AttributeError.
        else:
            if self.trace:
                log(trace_prefix(self) + f"┃ Read '{key}' failed")
            raise AttributeError(key)

        if self.trace:
            log(trace_prefix(self) + f"┃ Read '{key}' = {trace_variant(val)}")

        # If we fetched a dict, wrap it in an Expander so we expand its sub-fields.
        if isinstance(val, dict):
            val = Expander(val)
        return val

    # Returns a relative path from the task directory to the sub_path.
    def rel(self, sub_path):
        result = rel_path(sub_path, expand_variant(self.context, self._task_dir))
        return result

    def __repr__(self):
        result = f"{self.__class__.__name__} @ {hex(id(self))} wraps "
        result += Dumper(2).dump(self.config)
        return result

# ----------------------------------------

class Macro(str):
    pass

def eval_macro(config, macro : Macro):
    """
    Evaluates the expression inside a {macro} and returns the result.
    Returns the full macro (with curly braces) unchanged if evaluation fails.
    """
    trace = config.get("trace", g_app.flags.trace)
    if trace:
        log_trace(config, f"┏ eval_macro {macro}")

    if g_app.expand_depth >= MAX_EXPAND_DEPTH:
        if trace:
            log_trace(config, f"┗ eval_macro {macro} failed due to recursion depth")
        raise RecursionError(f"eval_macro('{macro}') failed to terminate")

    failed = False
    g_app.expand_depth += 1

    try:
        result = eval(macro[1:-1], {}, Expander(config))  # type: ignore
    except BaseException:  # pylint: disable=broad-exception-caught
        # TEFINAE - Text Expansion Failure Is Not An Error, we return the original macro.
        failed = True
        result = macro

    g_app.expand_depth -= 1
    if trace:
        if failed:
            log_trace(config, f"┗ eval_macro {macro} failed")
        else:
            log_trace(config, f"┗ eval_macro {macro} = {result}")

    return result

# ----------------------------------------
# FIXME we need full-loop test cases for escaped {}s. Somewhere in the process we need to unescape
# them and I'm not sure where it goes.

def split_template(text):
    """
    Extracts all innermost single-brace-delimited spans from a block of text and produces a list of
    strings and macros. Escaped braces don't count as delimiters.
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
            result.append(Macro(text[lbrace:rbrace + 1]))
            cursor = rbrace + 1
            lbrace = -1
            rbrace = -1

    if cursor < len(text):
        result.append(text[cursor:])

    return result

# ----------------------------------------

def expand_blocks(config, blocks):
    trace = config.get("trace", g_app.flags.trace)
    if trace:
        log_trace(config, f"┏ expand_blocks {blocks}")
    g_app.expand_depth += 1

    result = ""
    for block in blocks:
        if isinstance(block, Macro):
            value = eval_macro(config, block)
            result += stringify_variant(value)
        else:
            result += block

    g_app.expand_depth -= 1
    if trace:
        log_trace(config, f"┗ expand_blocks {blocks} = '{result}'")
    return result

# ----------------------------------------

#def expand_variant(context, variant):
#    """Expands all macros anywhere inside 'variant', making deep copies where needed so we don't
#    expand someone else's data."""
#
#    if listlike(variant):
#        result = [expand_variant(context, val) for val in variant]
#    elif isinstance(variant, str):
#        g_app.expand_depth += 1
#        result = expand_text(context, variant)
#        g_app.expand_depth -= 1
#    else:
#        result = variant
#
#    return result

def expand_variant(config, variant):
    """Expands single templates and nested lists of templates. Returns non-templates unchanged."""

    trace = config.get("trace", g_app.flags.trace)

    if listlike(variant):
        return [config.expand(val) for val in variant]

    if not isinstance(variant, str):
        return variant

    blocks = split_template(variant)
    if len(blocks) == 0 or (len(blocks) == 1 and not isinstance(blocks[0], Macro)):
        # Empty string or plain string
        return variant

    if trace:
        log_trace(config, f"┏ expand_variant '{variant}'")
    g_app.expand_depth += 1

    if len(blocks) == 1:
        result = eval_macro(config, blocks[0])
    else:
        result = expand_blocks(config, blocks)

    if result != variant:
        result = config.expand(result)

    g_app.expand_depth -= 1
    if trace:
        log_trace(config, f"┗ expand_variant '{variant}' = '{result}'")

    return result

def expand_text(context, text):
    """Replaces all macros in 'text' with their expanded, stringified values."""

    if not istemplate(text):
        return text

    trace = context.get("trace", g_app.flags.trace)

    if trace:
        log(trace_prefix(context) + f"┏ expand_text '{text}'")

    g_app.expand_depth += 1
    if g_app.expand_depth > MAX_EXPAND_DEPTH:
        raise RecursionError("TemplateRecursion: Text expansion failed to terminate")

    # ==========

    old_text = text
    temp = old_text
    result = ""
    while span := macro_regex.search(temp):
        result += temp[0 : span.start()]
        macro = temp[span.start() : span.end()]

        if trace:
            log(trace_prefix(context) + f"┏ expand_macro '{macro}'")

        # ==========

        result2 = macro
        failed = False

        try:
            result2 = eval(macro[1:-1], {}, Expander(context))  # type: ignore # pylint: disable=eval-used
        except RecursionError:
            raise
        except BaseException:  # pylint: disable=broad-exception-caught
            failed = True

        # ==========

        if trace:
            if failed:
                log(trace_prefix(context) + f"┗ expand_macro '{macro}' failed")
            else:
                log(trace_prefix(context) + f"┗ expand_macro '{macro}' = {result2}")

        variant = result2

        result += stringify_variant(variant)
        temp = temp[span.end() :]
    result += temp

    # ==========

    # If expansion changed the text, try to expand it again.
    if result != old_text:
        result = expand_text(context, result)

    g_app.expand_depth -= 1
    if g_app.expand_depth < 0:
        raise RecursionError("Text expand_inc/dec unbalanced")

    if trace:
        log(trace_prefix(context) + f"┗ expand_text '{old_text}' = '{result}'")

    return result

# ----------------------------------------
# FIXME all should go through this

def expand(template, config):
    return expand_variant(config, template)

#endregion
####################################################################################################
#region Utils
# FIXME we should just merge these into the config the moment we wrap it in an Expander or something.

class Utils:
    # fmt: off
    #path        = path # path.dirname and path.basename used by makefile-related rules
    #re          = re # why is sub() not working?

    #color       = staticmethod(color)
    #flatten     = staticmethod(flatten)
    #glob        = staticmethod(glob.glob)
    #join        = staticmethod(join)
    #ext         = staticmethod(ext)
    #log         = staticmethod(log)
    #rel_path    = staticmethod(rel_path)  # used by build_path etc
    #run_cmd     = staticmethod(run_cmd)   # FIXME rename to run? cmd?
    #stem        = staticmethod(stem)      # FIXME used by metron/tests?

    #expand      = staticmethod(expand_variant)

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

    hancho_dir  = path.dirname(path.realpath(__file__))
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
            return self.task.context[self.args[0]]
        else:
            return [self.task.context[field] for field in self.args]

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

        self._config = Dict(default_context, *args, **kwargs)

        self._desc = None
        self._command = None
        self._in_files  = []
        self._out_files = []
        self._task_index = 0
        self._state = TaskState.DECLARED
        self._reason = None
        self._asyncio_task = None
        self._loaded_files = list(g_app.loaded_files)
        self._stdout = ""
        self._stderr = ""
        self._returncode = -1

        g_app.all_tasks.append(self)

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
            # Queue all tasks referenced by this task's config.
            def apply(_, val):
                if isinstance(val, Task):
                    val.queue()
                return val
            map_variant(None, self._config, apply)

            # And now queue this task.
            g_app.queued_tasks.append(self)
            self._state = TaskState.QUEUED

    def start(self):
        self.queue()
        if self._state is TaskState.QUEUED:
            self._asyncio_task = asyncio.create_task(self.task_main())
            self._state = TaskState.STARTED
            g_app.tasks_started += 1

    async def await_done(self):
        self.start()
        assert self._asyncio_task is not None
        await self._asyncio_task

    def promise(self, *args):
        return Promise(self, *args)

    # -----------------------------------------------------------------------------------------------

    def print_status(self):
        """Print the "[1/N] Compiling foo.cpp -> foo.o" status line and debug information"""

        verbosity = self._config.get_expanded("verbosity", g_app.flags.verbosity)
        log(
            f"{color(128,255,196)}[{self._task_index}/{g_app.tasks_started}]{color()} {self._config.desc}",
            sameline=verbosity == 0,
        )

    # -----------------------------------------------------------------------------------------------

    async def task_main(self):
        """Entry point for async task stuff, handles exceptions generated during task execution."""

        verbosity = self._config.get_expanded("verbosity", g_app.flags.verbosity)
        debug     = self._config.get_expanded("debug",     g_app.flags.debug)
        rebuild   = self._config.get_expanded("rebuild",   g_app.flags.rebuild)

        assert isinstance(verbosity, bool)
        assert isinstance(debug,     bool)
        assert isinstance(rebuild,   bool)


        # Await everything awaitable in this task's config.
        # If any of this tasks's dependencies were cancelled, we propagate the cancellation to
        # downstream tasks.
        try:
            assert self._state is TaskState.STARTED
            self._state = TaskState.AWAITING_INPUTS
            for key, val in self._config.items():
                self._config[key] = await await_variant(val)
        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # Exceptions during awaiting inputs means that this task cannot proceed, cancel it.
            self._state = TaskState.CANCELLED
            g_app.tasks_cancelled += 1
            raise asyncio.CancelledError() from ex

        # Everything awaited, task_init runs synchronously.
        try:
            self._state = TaskState.TASK_INIT

            # Note that we chdir to task_dir before initializing the task so that any path.abspath
            # or whatever happen from the right place

            task_dir = self._config.get_expanded("task_dir")
            assert isinstance(task_dir, str)
            try:
                g_app.pushdir(task_dir)
                self.task_init()
            finally:
                g_app.popdir()

        except asyncio.CancelledError as ex:
            # We discovered during init that we don't need to run this task.
            self._state = TaskState.CANCELLED
            g_app.tasks_cancelled += 1
            raise asyncio.CancelledError() from ex
        except BaseException as ex:  # pylint: disable=broad-exception-caught
            self._state = TaskState.BROKEN
            g_app.tasks_broken += 1
            raise ex

        # Early-out if this is a no-op task
        if self._command is None:
            g_app.tasks_finished += 1
            self._state = TaskState.FINISHED
            return

        # Check if we need a rebuild
        self._reason = self.needs_rerun(rebuild)
        if not self._reason:
            g_app.tasks_skipped += 1
            self._state = TaskState.SKIPPED
            return

        try:
            # Wait for enough jobs to free up to run this task.
            job_count = self._config.get("job_count", 1)
            self._state = TaskState.AWAITING_JOBS
            await g_app.job_pool.acquire_jobs(job_count, self)

            # Run the commands.
            self._state = TaskState.RUNNING_COMMANDS
            g_app.tasks_running += 1
            self._task_index = g_app.tasks_running

            self.print_status()
            if verbosity or debug:
                log(f"{color(128,128,128)}Reason: {self._reason}{color()}")

            for command in flatten(self._command):
                await self.run_command(command)
                if self._returncode != 0:
                    break

        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # If any command failed, we print the error and propagate it to downstream tasks.
            self._state = TaskState.FAILED
            g_app.tasks_failed += 1
            raise ex
        finally:
            await g_app.job_pool.release_jobs(job_count, self)

        # Task finished successfully
        self._state = TaskState.FINISHED
        g_app.tasks_finished += 1

    # -----------------------------------------------------------------------------------------------
    # FIXME _all_ paths should be rel'd before running command. If you want abs, you can abs() it.

    def task_init(self):
        """All the setup steps needed before we run a task."""

        debug = self._config.get("debug", g_app.flags.debug)
        if debug:
            log(f"\nTask before expand: {self}")

        # ----------------------------------------
        # Expand task_dir and build_dir

        # pylint: disable=attribute-defined-outside-init

        self._repo_dir   = abs_path(expand_variant(self._config, self._config.repo_dir))
        self._task_dir   = abs_path(expand_variant(self._config, self._config.task_dir))
        self._build_dir  = abs_path(expand_variant(self._config, self._config.build_dir))

        assert isinstance(self._repo_dir, str)
        assert isinstance(self._task_dir, str)
        assert isinstance(self._build_dir, str)

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
                    val = expand_variant(self._config, val)
                    val = path.normpath(val) # type: ignore
                    return val
                self._config[key] = map_variant(key, val, expand_path)

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
                        val = rel_path(val, self._config.task_dir)
                        val = join_path(self._config.build_dir, val)
                    elif path.isabs(val):
                        raise ValueError(f"Output file has absolute path that is not under task_dir or build_dir : {val}")
                    else:
                        # Relative path, add build_dir
                        val = join_path(self._config.build_dir, val)
                    return val
                self._config[key] = map_variant(key, val, move_to_builddir)
            elif key.startswith("in_"):
                def move_to_taskdir(key, val):
                    if not isinstance(val, str):
                        return val
                    if not path.isabs(val):
                        val = join_path(self._config.task_dir, val)
                    return val
                self._config[key] = map_variant(key, val, move_to_taskdir)

        # Gather all inputs to task.in_files and outputs to task.out_files

        for key, val in self._config.items():
            # Note - we only add the depfile to in_files _if_it_exists_, otherwise we will fail a check
            # that all our inputs are present.
            if key == "in_depfile":
                if path.isfile(val):
                    self._in_files.append(val)
            elif key.startswith("out_"):
                self._out_files.extend(flatten(val))
            elif key.startswith("in_"):
                self._in_files.extend(flatten(val))


        # Make all in_ and out_ file paths absolute

        # FIXME I dislike all this "move_to" stuff

        # Gather all inputs to task._in_files and outputs to task._out_files

        def move_to_builddir2(file):
            if isinstance(file, list):
                return [move_to_builddir2(f) for f in file]

            # needed for test_bad_build_path
            file = path.normpath(file)

            # Note this conditional needs to be first, as build_dir can itself be under
            # task_dir
            if file.startswith(self._build_dir):
                # Absolute path under build_dir.
                pass
            elif file.startswith(self._task_dir):
                # Absolute path under task_dir, move to build_dir
                file = rel_path(file, self._task_dir)
            elif path.isabs(file):
                raise ValueError(f"Output file has absolute path that is not under task_dir or build_dir : {file}")

            file = join_path(self._build_dir, file)
            return file

        # pylint: disable=consider-using-dict-items
        for key in self._config.keys():

            if key.startswith("in_"):
                file = self._config[key]
                file = self._config.expand(file)
                file = join_path(self._task_dir, normpath(file))
                self._in_files.extend(flatten(file))
                self._config[key] = file

            if key.startswith("out_"):
                file = self._config[key]
                file = self._config.expand(file)
                file = move_to_builddir2(file)
                self._out_files.extend(flatten(file))
                self._config[key] = file

            if key == "depfile":
                file = self._config[key]
                file = self._config.expand(file)
                file = move_to_builddir2(file)
                self._config[key] = file

        # ----------------------------------------
        # And now we can expand the command.

        self._desc    = expand_variant(self._config, self._config.desc)
        self._command = expand_variant(self._config, self._config.command)

        if debug:
            log(f"\nTask after expand: {self}")

        # ----------------------------------------
        # Check for task collisions

        # FIXME need a test for this that uses symlinks

        #if self._out_files and self.context.command is not None:
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
                #if file in app.all_out_files:
                #    raise NameError(f"Multiple rules build {file}!")
                g_app.all_out_files.add(file)

        # Make sure our output directories exist
        if not g_app.flags.dry_run:
            for file in self._out_files:
                os.makedirs(path.dirname(file), exist_ok=True)

        if debug:
            log(f"\nTask after expand: {self}")

    # -----------------------------------------------------------------------------------------------

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
        if (in_depfile := self._config.get("in_depfile", None)) and path.exists(in_depfile):
            depformat = self._config.get("depformat", "gcc")
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
                deplines = [path.join(self._config.task_dir, d) for d in deplines]
                for abs_file in deplines:
                    if mtime(abs_file) >= min_out:
                        return f"Rebuilding because {abs_file} has changed"

        # All checks passed; we don't need to rebuild this output.
        # Empty string = no reason to rebuild
        return ""

    # -----------------------------------------------------------------------------------------------

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        verbosity = self._config.get_expanded("verbosity", g_app.flags.verbosity)
        debug     = self._config.get_expanded("debug", g_app.flags.debug)

        if verbosity or debug:
            log(color(128, 128, 255), end="")
            if g_app.flags.dry_run:
                log("(DRY RUN) ", end="")
            log(f"{rel_path(self._config.task_dir, self._config.repo_dir)}$ ", end="")
            log(color(), end="")
            log(command)

        # Dry runs get early-out'ed before we do anything.
        if g_app.flags.dry_run:
            return

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            g_app.pushdir(self._config.task_dir)
            await await_variant(command(self))
            g_app.popdir()
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
            cwd=self._config.task_dir,
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
            log(
                f"{color(128,255,196)}[{self._task_index}/{g_app.tasks_started}]{color()} Task passed - '{self._desc}'"
            )
            if self._stdout:
                log("Stdout:")
                log(self._stdout, end="")
            if self._stderr:
                log("Stderr:")
                log(self._stderr, end="")

#endregion
####################################################################################################
#region Hancho API object
# This is what gets passed into .hancho files

class HanchoAPI:

    def __init__(self, config, is_repo):
        #self.app    = app
        self.config  = config
        self.is_repo = is_repo
        #self.Task   = Task

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
        mod_path = expand(mod_path, self.config)
        assert isinstance(mod_path, str)
        mod_path = normalize_path(mod_path)
        mod_path = path.realpath(mod_path)
        #real_path = path.realpath(mod_path)

        dedupe = g_app.realpath_to_repo.get(mod_path, None)
        if dedupe is not None:
            return dedupe

        new_api = create_repo(mod_path, *args, **kwargs)

        result = new_api._load()
        g_app.realpath_to_repo[mod_path] = result
        return result

    def load(self, mod_path):
        mod_path = expand(mod_path, self.config)
        assert isinstance(mod_path, str)
        mod_path = normalize_path(mod_path)
        new_module = create_mod(self, mod_path)
        return new_module._load()

    def _load(self):
        #if len(app.dirstack) == 1 or app.flags.verbosity or app.flags.debug:
        if True:
            #mod_path = rel_path(self.config.mod_path, self.config.repo_dir)
            mod_path = rel_path(self.config.mod_path, g_app.flags.root_dir)
            log(("┃ " * (len(g_app.dirstack) - 1)), end="")
            if self.is_repo:
                log(color(128, 128, 255) + f"Loading repo {self.config.mod_path}" + color())
            else:
                log(color(128, 255, 128) + f"Loading file {self.config.mod_path}" + color())

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
        self.flatten = flatten

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
#region Helper stuff that needs to go somewhere else

def create_repo(mod_path : str, *args, **kwargs):
    assert path.isabs(mod_path)
    assert mod_path == normalize_path(mod_path)
    assert mod_path == path.realpath(mod_path)
    assert mod_path not in g_app.realpath_to_repo

    mod_dir  = path.split(mod_path)[0]
    mod_file = path.split(mod_path)[1]
    mod_name = path.splitext(mod_file)[0]

    mod_config = Dict(
        hancho_dir  = path.dirname(path.realpath(__file__)),
        root_dir    = g_app.flags.root_dir,

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

    mod_config = Dict(mod_config, *args, **kwargs)

    mod_api = HanchoAPI(mod_config, True)
    return mod_api

####################################################################################################

def create_mod(parent_api : HanchoAPI, mod_path : str, *args, **kwargs):
    assert isinstance(parent_api, HanchoAPI)

    mod_path = normalize_path(expand_variant(parent_api.config, mod_path))
    assert isinstance(mod_path, str)
    mod_path = path.abspath(mod_path) # FIXME not needed?
    mod_dir  = path.split(mod_path)[0]
    mod_file = path.split(mod_path)[1]
    mod_name = path.splitext(mod_file)[0]

    mod_api = copy.deepcopy(parent_api)
    mod_api.is_repo = False

    mod_api.config.mod_name = mod_name
    mod_api.config.mod_dir  = mod_dir
    mod_api.config.mod_path = mod_path

    mod_api.config = Dict(mod_api.config, *args, kwargs)

    return mod_api

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
#region Global app object.
# There's probably a better way to handle global state...

class App:

    def __init__(self):
        self.flags = argparse.Namespace()
        self.extra_flags = Dict()
        self.target_regex = None

        self.root_mod = None
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
                    for converter in (int, float):
                        try:
                            val = converter(val)
                            break
                        except ValueError:
                            pass

                #val = maybe_as_number(val) if val is not None else True
                extra_flags[key] = val

        self.flags = flags
        self.extra_flags = extra_flags

    ########################################

    def create_root_mod(self):
        """ Needs to be its own function, used by run_tests.py """

        root_file = self.flags.root_file
        root_dir  = path.abspath(self.flags.root_dir)  # Root path must be absolute.
        root_path = path.normpath(path.join(root_dir, root_file))
        root_path = path.realpath(root_path)
        root_mod  = create_repo(root_path)

        # All the unrecognized flags get stuck on the root module's config.
        for key, val in self.extra_flags.items():
            setattr(root_mod.config, key, val)

        return root_mod

    ########################################

    def main(self):
        g_app.root_mod = self.create_root_mod()

        if g_app.root_mod.config.get_expanded("debug", None):
            log(f"root_mod = {Dumper(2).dump(g_app.root_mod)}")

        if not path.isfile(g_app.root_mod.config.repo_path):
            print(
                f"Could not find Hancho file {g_app.root_mod.config.repo_path}!"
            )
            sys.exit(-1)

        assert path.isabs (g_app.root_mod.config.repo_path)
        assert path.isfile(g_app.root_mod.config.repo_path)
        assert path.isabs (g_app.root_mod.config.repo_dir)
        assert path.isdir (g_app.root_mod.config.repo_dir)

        os.chdir(g_app.root_mod.config.repo_dir)
        time_a = time.perf_counter()
        g_app.root_mod._load()
        time_b = time.perf_counter()

        if g_app.flags.debug or g_app.flags.verbosity:
            log(f"Loading .hancho files took {time_b-time_a:.3f} seconds")

        if g_app.flags.tool:
            print(f"Running tool {g_app.flags.tool}")
            if g_app.flags.tool == "clean":
                print("Deleting build directories")
                build_roots = set()
                for task in g_app.all_tasks:
                    build_root = expand("{build_root}", task.config)
                    assert isinstance(build_root, str)
                    build_root = normalize_path(build_root)
                    build_root = path.realpath(build_root)
                    if path.isdir(build_root):
                        build_roots.add(build_root)
                for root in build_roots:
                    print(f"Deleting build root {root}")
                    shutil.rmtree(root, ignore_errors=True)
            return 0

        time_a = time.perf_counter()

        # FIXME selecting targets by regex needs revisiting
        if g_app.flags.target:
            g_app.target_regex = re.compile(g_app.flags.target)
            for task in g_app.all_tasks:
                queue_task = False
                task_name = None
                # This doesn't work because we haven't expanded output filenames yet
                # for out_file in flatten(task._out_files):
                #    if g_app.target_regex.search(out_file):
                #        queue_task = True
                #        task_name = out_file
                #        break
                if name := task._config.get_expanded("name", None):
                    if g_app.target_regex.search(name):
                        queue_task = True
                        task_name = name
                if queue_task:
                    log(f"Queueing task for '{task_name}'")
                    task.queue()
        else:
            for task in g_app.all_tasks:
                # If no target was specified, we queue up all tasks that build stuff in the root
                # repo

                # FIXME we are not currently doing that....

                # build_dir = expand_variant(task.context, task.context.build_dir)
                # build_dir = normalize_path(build_dir)
                # repo_dir  = expand_variant(app.root_context.context, "{build_dir}")
                # repo_dir  = normalize_path(repo_dir)
                # print(build_dir)
                # print(repo_dir)
                # if build_dir.startswith(repo_dir):
                #    task.queue()
                task.queue()

        time_b = time.perf_counter()

        # if g_app.flags.debug or g_app.flags.verbosity:
        log(f"Queueing {len(g_app.queued_tasks)} tasks took {time_b-time_a:.3f} seconds")

        result = self.build()
        return result

    ########################################

    def pushdir(self, new_dir: str):
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
            if g_app.shuffle:
                log(f"Shufflin' {len(self.queued_tasks)} tasks")
                random.shuffle(self.queued_tasks)

            while self.queued_tasks:
                task = self.queued_tasks.pop(0)
                task.start()
                self.started_tasks.append(task)

            task = self.started_tasks.pop(0)
            try:
                await task._asyncio_task
            except BaseException:  # pylint: disable=broad-exception-caught
                log(color(255, 128, 0), end="")
                log(f"Task failed: {task._desc}")
                log(color(), end="")
                log(str(task))
                log(color(255, 128, 128), end="")
                log(traceback.format_exc())
                log(color(), end="")
                fail_count = g_app.tasks_failed + g_app.tasks_cancelled + g_app.tasks_broken
                if g_app.flags.keep_going and fail_count >= g_app.flags.keep_going:
                    log("Too many failures, cancelling tasks and stopping build")
                    for task in self.started_tasks:
                        task._asyncio_task.cancel()
                        g_app.tasks_cancelled += 1
                    break
            self.finished_tasks.append(task)

        time_b = time.perf_counter()

        # if app.flags.debug or app.flags.verbosity:
        log(f"Running {g_app.tasks_finished} tasks took {time_b-time_a:.3f} seconds")

        # Done, print status info if needed
        if g_app.flags.debug or g_app.flags.verbosity:
            log(f"tasks started:   {g_app.tasks_started}")
            log(f"tasks finished:  {g_app.tasks_finished}")
            log(f"tasks failed:    {g_app.tasks_failed}")
            log(f"tasks skipped:   {g_app.tasks_skipped}")
            log(f"tasks cancelled: {g_app.tasks_cancelled}")
            log(f"tasks broken:    {g_app.tasks_broken}")
            log(f"mtime calls:     {g_app.mtime_calls}")

        if self.tasks_failed or self.tasks_broken:
            log(f"hancho: {color(255, 128, 128)}BUILD FAILED{color()}")
        elif self.tasks_finished:
            log(f"hancho: {color(128, 255, 128)}BUILD PASSED{color()}")
        else:
            log(f"hancho: {color(128, 128, 255)}BUILD CLEAN{color()}")

        return -1 if self.tasks_failed or self.tasks_broken else 0

#endregion
####################################################################################################
# region Main
# Always create an App() object so we can use it for bookkeeping even if we loaded Hancho as a
# module instead of running it directly.

g_app = App()

if __name__ == "__main__":
    g_app.parse_flags(sys.argv[1:])
    sys.exit(g_app.main())

# endregion
####################################################################################################

#import doctest
#doctest.testmod(verbose=True)
#doctest.testmod()
