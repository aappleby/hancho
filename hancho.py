#!/usr/bin/python3
# ruff: noqa: RUF012
# region Header

"""
Hancho v1.0.0 @ 2026-06-05 - A simple, pleasant build system.

Hancho is a single-file build system that's designed to be dropped into your project folder - there
is no 'install' step.

Hancho requires Python 3.12+, which should be fairly universal in 2026.

Hancho's test suite can be found in /tests and can be run via "python -m unittest" in the root of
the Hancho repo.

WARNING - Hancho is NOT A SANDBOX, your build scripts can evaluate arbitrary Python code which
could format your hard drive and email spam to your grandparents. Use responsibly.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import colorsys
import contextvars
import copy
import inspect
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import traceback
import types
import zlib  # for crc32, adler32
from collections import Counter, abc
from contextlib import chdir, contextmanager, suppress
from dataclasses import dataclass, replace
from enum import Enum
from functools import wraps
from typing import Any, cast

MISSING = object()

#endregion

class Dict(dict):
    """
    This class extends 'dict' in a couple ways -
    1. Dict supports "foo.bar" attribute access in addition to "foo['bar']"
    2. Dict supports "merging" instances by passing them (and any additional key-value pairs) in via the constructor.
    3. When merging Dicts, the rightmost not-None value of an attribute will be kept.
    4. If two attributes with the same name are both Dicts, we will recursively merge them.
    5. Dict's constructor makes copies of all basic container types (collections and mappings) in
    its inputs. I can't guarantee that everything you might put in a Dict will be deep-copied, but
    it should be close enough.
    """

    def __init__(self, *args, **kwargs):
        super().__init__()

        for i, arg in enumerate(args):
            if not isinstance(arg, abc.Mapping) and arg is not None:
                raise ValueError(f"Argument #{i} was not a dict - {arg}")

        self.merge(*args, kwargs)

    def merge(self, *args, **kwargs):
        for rhs in (*args, kwargs):
            if rhs is None:
                continue
            Dict.generic_merge(
                self, self, rhs,
                merge_dicts=True, merge_lists=True,
                keep_a=True, keep_b=True)

    # Fill-in-the-blank (or override what's there): Merges self and args into a new dict, keeping
    # only keys that were already in self. For example, if you have a config that contains
    # "out_bin" and you merge it with "compile_cpp", Hancho will complain that "out_bin" is missing
    # - it sees both "out_obj" and "out_bin" and assumes the task produces both. If you do
    # compile_cpp.fill(...), "out_bin" does not get added to compile_cpp.

    def fill(self, *args, **kwargs):
        dest = Dict(self)
        for rhs in (*args, kwargs):
            Dict.generic_merge(
                dest, dest, rhs,
                merge_dicts=True, merge_lists=True,
                keep_a=True, keep_b=False)
        return dest

    @classmethod
    def generic_merge(cls, dst, lhs, rhs, merge_dicts, merge_lists, keep_a, keep_b):
        keys = list(lhs) + [r for r in rhs if r not in lhs]

        for key in keys:
            if key in lhs and key not in rhs and not keep_a: continue
            if key not in lhs and key in rhs and not keep_b: continue

            lhs2 = lhs.get(key, None)
            rhs2 = rhs.get(key, None)

            if isinstance(lhs2, dict) and isinstance(rhs2, dict) and merge_dicts:
                dst2 = Dict()
                cls.generic_merge(dst2, lhs2, rhs2, merge_dicts, merge_lists, keep_a, keep_b)
                dst[key] = dst2
            elif isinstance(lhs2, list) and isinstance(rhs2, list) and merge_lists:
                dst[key] = list(lhs2) + list(rhs2)
            elif isinstance(rhs2, abc.Mapping):
                dst[key] = Dict(rhs2)
            elif isinstance(rhs2, (list, set, tuple)):
                dst[key] = lhs2 if rhs2 is None else copy.copy(rhs2)
            else:
                dst[key] = lhs2 if rhs2 is None else rhs2

        return dst

    def __getattr__(self, key : str):
        try:
            return dict.__getitem__(self, key)
        except KeyError as err:
            raise AttributeError from err

    def __setattr__(self, key : str, val : Any):
        try:
            return dict.__setitem__(self, key, val)
        except KeyError as err:
            raise AttributeError from err

    def __delattr__(self, key : str):
        try:
            return dict.__delitem__(self, key)
        except KeyError as err:
            raise AttributeError from err

    def __or__(self, other):
        return Dict(self, other)

    def __repr__(self):
        return Dumper.dump(self)

    def __getitem__(self, key : str):
        return dict.__getitem__(self, key)

    def __setitem__(self, key : str, val : Any):
        dict.__setitem__(self, key, val)

    def expand(self, template):
        onion = Onion(Hancho.cv_script.onion, dict = self)
        return Expander.expand(onion, template)

    def eval(self, expr):
        onion = Onion(Hancho.cv_script.onion, dict = self)
        return eval(expr, {}, onion)

class Tool(Dict):
    # Tool is just an alias for Dict to make build scripts more readable.
    pass

class Script:

    def __init__(self, *, params : Dict, code : types.CodeType | None, is_repo : bool):
        self.params = params
        self.config = Dict()
        self.onion  = Onion(hancho = Hancho.proxy.__dict__, script_params = self.params, script_config = self.config)

        self.config.script_path  = self.onion.script_path
        self.config.script_cwd   = self.onion.script_cwd

        self.config.repo_root    = self.onion.repo_root
        self.config.build_root   = self.onion.build_root
        self.config.comp_db_path = self.onion.comp_db_path
        self.config.stat_db_path = self.onion.stat_db_path

        self.config.build_tag    = self.onion.build_tag
        self.config.build_target = self.onion.build_target
        self.config.build_all    = self.onion.build_all
        self.config.build_dry    = self.onion.build_dry
        self.config.build_strict = self.onion.build_strict

        self.code    = code
        self.is_repo = is_repo
        self.stats   = Stats(self.config.comp_db_path, self.config.stat_db_path, self.config.build_dry)

        self.repos : dict[str, Script] = {}
        self.scripts : dict[str, Script] = {}
        self.tasks : list[Task]=  []

        self.module = types.ModuleType(os.path.basename(self.config.script_path))

        self.module.__file__  = self.config.script_path
        self.module.hancho    = Hancho.proxy   # type: ignore
        self.module.params    = self.params     # type: ignore
        self.module.config    = self.config    # type: ignore
        self.module.onion     = self.onion     # type: ignore

    def exec(self):
        if self.code:
            with Hancho.cv_script.enter(self), chdir(self.config.script_cwd):
                exec(self.code, self.module.__dict__)

    def add(self, repo):
        self.repos[repo.repo_root] = repo

    def __repr__(self):
        return Dumper.dump(self)

    def yield_tasks(self):
        yield from self.tasks
        for script in self.scripts.values():
            yield from script.yield_tasks()

    def yield_repos(self):
        if self.is_repo:
            yield self
        yield from self.repos.values()
        for repo in self.repos.values():
            yield from repo.yield_repos()

    def yield_all_tasks(self):
        for repo in self.yield_repos():
            yield from repo.yield_tasks()

    class Abort(Exception): pass
    class EarlyOut(Exception): pass
    class Fail(Exception): pass

    @staticmethod
    def log_script_error(frame, condition, message):
        with Log.Level.ERROR, Log.Color.RED:
            Log.log(f"Script {condition}:\n")
            Log.log(f"  text = '{message}'\n")
            Log.log(f"  file = {frame.f_code.co_filename}\n")
            Log.log(f"  func = {frame.f_code.co_name}\n")
            Log.log(f"  line = {frame.f_lineno}\n")

    @staticmethod
    def fail(message):
        Script.log_script_error(sys._getframe(1), "failed", message)
        raise Script.Fail()

    @staticmethod
    def abort(message):
        Script.log_script_error(sys._getframe(1), "aborted", message)
        raise Script.Abort()

    @staticmethod
    def earlyout(message = ""):
        Script.log_script_error(sys._getframe(1), "exited early", message)
        raise Script.EarlyOut()

class ContextProxy:
    # Helper that just wraps CVs so you can do "contextvar.foo".
    def __init__(self, root_script):
        object.__setattr__(self, "_cv", contextvars.ContextVar("script", default = root_script))

    def __getattr__(self, key):
        return getattr(self._cv.get(), key)

    def __setattr__(self, key, val):
        setattr(self._cv.get(), key, val)

    def get(self) -> Script:
        return self._cv.get()

    def set(self, new_ctx : Script):
        return self._cv.set(new_ctx)

    @contextmanager
    def enter(self, new_ctx : Script):
        token = self._cv.set(new_ctx)
        yield
        self._cv.reset(token)

class Onion(abc.Mapping):
    """
    An Onion is like a ChainMap, except that it behaves as if you'd merged all the ChainMap maps
    together. This matters when you have something like
    c = ChainMap(
        dict(foo = dict(a = 1)),
        dict(foo = dict(b = 2))
    )
    because if you try to read both c['foo']['a'] and c['foo']['b'] it won't work - 'foo' always
    resolves to the first dict and never sees the second.

    Onion fixes this by looking up the key in all 'layers' and returns a value only if it was the
    first non-Mapping match. If there are multiple Mapping matches, they form a new Onion.

    Onion layers are searched in right-to-left (i.e. reverse) order, to match the "right overrides
    left" behavior of Dict.

    Why the name 'Onion'?
    Well, 'stack' and 'deck' are overloaded and 'Onion' at least implies nested layers.
    """

    def __init__(self, *args, **kwargs):

        self._layers2 : dict[str, abc.Mapping] = {}

        for val in args:
            if val is None:
                continue
            if isinstance(val, Onion):
                self._layers2.update(val._layers2)
            else:
                raise TypeError("You need to pass all Onion args as Onion(name=mapping, ...)")

        if kwargs:
            self._layers2.update(kwargs)

    def __getattr__(self, key : str):
        try:
            return self.get(key)
        except KeyError as err:
            raise AttributeError from err

    def __getitem__(self, key):
        return self.get(key)

    def __iter__(self):
        seen = set()
        for layer in reversed(self._layers2.values()):
            for key in layer:
                if key not in seen:
                    seen.add(key)
                    yield key

    def __len__(self):
        result = {key for layer in self._layers2.values() for key in layer}
        return len(result)

    def __repr__(self):
        return Dumper.dump(self)

    def __contains__(self, key):
        return any(key in layer for layer in self._layers2.values())

    def __dump__(self, key, opts, seen):
        trimmed_layers = dict(self._layers2)
        if hasattr(trimmed_layers, "hancho"):
            delattr(trimmed_layers, "hancho")
        return Dumper._dump_vector(key, self, trimmed_layers, opts, seen)

    def get(self, key, default : Any = MISSING) -> Any: # type: ignore
        """
        Searches through layers in reverse order (because we obey rightmost-not-None wins) for a
        key match. If we find it, we expand it before returning it. If we only found Mappings, we
        return a new Onion containing those mappings.
        """
        with Tracer(self, "get", key) as trace:

            # Return the rightmost non-None non-Mapping if present.
            saw_a_none = False
            for layer in reversed(self._layers2.values()):
                if key in layer:
                    val = layer[key]
                    if val is None:
                        saw_a_none = True
                    elif not isinstance(val, abc.Mapping):
                        result = Expander.expand(self, val)
                        trace.save_result(result)
                        return result

            # If the key was present but there was no value associated with it, return None.
            if saw_a_none:
                result = None if default is MISSING else default
                trace.save_result(result)
                return result

            # Nope, all mappings. Pull out the ones containing the key.
            new_layers = {name:layer[key] for name, layer in self._layers2.items() if key in layer}

            # No matches and no default? Bad key.
            if not new_layers and default is MISSING:
                raise KeyError(key)

            # No matches but we have a default? Return it.
            if default != MISSING:
                return default

            # Otherwise we make a new onion out of the mappings.
            result = Onion(**new_layers)
            trace.save_result(result)
            return result

#    def raw_get(self, key, default : Any = MISSING) -> Any:
#        """
#        A simpler getter equivalent to ChainMap.get - doesn't expand the result.
#        """
#        for layer in reversed(self._layers2.values()):
#            if key in layer:
#                return layer[key]
#
#        if default is MISSING:
#            raise KeyError(key)
#
#        return default

    def eval(self, expr):
        return eval(expr, {}, self)

    def expand(self, variant : Any):
        return Expander.expand(self, variant)

class Expander:

    class Literal(str):
        pass

    class Macro(str):
        pass

    class Expr(str):
        pass

    ldelims : str = "{«"
    rdelims : str = "}»"

    cv_depth = contextvars.ContextVar("depth", default = 0)
    cv_evals = contextvars.ContextVar("evals", default = 0)
    MAX_DEPTH = 30
    MAX_EVALS = 300

    @classmethod
    def expand(cls, onion : Onion, variant : Any):
        if not variant:
            #return variant
            pass
        return cls._expand_variant(onion, variant)

    @classmethod
    def _expand_variant(cls, onion : Onion, variant : Any) -> Any:
        if variant is MISSING:
            raise AssertionError("Tried to expand a sentinel value")
        elif isinstance(variant, str):
            return cls._expand_text(onion, variant)
        elif isinstance(variant, abc.Collection):
            return [cls._expand_variant(onion, v) for v in variant]
        elif isinstance(variant, abc.Mapping):
            return {k: cls._expand_variant(onion, v) for k, v in variant.items()}
        else:
            return variant

    @classmethod
    def _expand_text(cls, onion : Onion, text : str) -> Any:
        old_text = ""
        blocks = []

        if not text or not cls._split_text(text, blocks):
            return text

        if len(blocks) == 1:
            return cls._eval_macro(onion, blocks[0])

        with Tracer(onion, "expand", text) as trace:
            while old_text != text:
                blocks.clear()

                if not text or not cls._split_text(text, blocks):
                    return text

                if len(blocks) == 1:
                    return cls._eval_macro(onion, blocks[0])

                for i in range(len(blocks)):
                    if isinstance(blocks[i], cls.Macro):
                        blocks[i] = cls._eval_macro(onion, blocks[i])
                        blocks[i] = Utils.stringify(blocks[i])

                old_text = text
                text = "".join(blocks)
            trace.save_result(text)

        return str(text)

    @classmethod
    def _eval_macro(cls, onion : Onion, macro : Expander.Macro):
        result = None
        with Tracer(onion, "eval", macro) as trace:
            try:
                result = eval(macro[1:-1], {}, onion)
                return result
            except RecursionError:
                raise
            except Exception as _:
                return macro
            finally:
                trace.save_result(result)

    @classmethod
    def _split_text(cls, text : str, out : list[str]) -> int:
        rdelim = None
        cursor = 0
        idelim = -1
        macros = 0

        for i, c in enumerate(text):
            if (pos := cls.ldelims.find(c)) != -1:
                idelim = i
                rdelim = cls.rdelims[pos]
            elif c == rdelim and idelim >= 0:
                if cursor < idelim:
                    out.append(cls.Literal(text[cursor:idelim]))
                out.append(cls.Macro(text[idelim:i+1]))
                macros += 1
                cursor = i + 1
                idelim = -1
                rdelim = None

        if cursor < len(text):
            out.append(cls.Literal(text[cursor:]))

        return macros

class Dumper:
    """
    Hancho's pretty-printer for various types. Note that this is also used for script deduping:
    if you load "my/app/tools/stuff.hancho" multiple times but the configurations you gave it
    were identical, you should get one copy of the "stuff" script instead of two.

    As long as you're not doing something bizarre with configs or changing the dumper in the
    middle of a build, the resulting strings should be stable enough to use for deduping.
    """

    # These types don't get dumped because they're not really dumpable.
    opaque_types = {
        types.BuiltinFunctionType : "<builtin>",
        #types.ModuleType          : "<module>",
        types.GeneratorType       : "<generator>",
    }

    # These types don't need a type annotation when dumped.
    base_types = (
        str,
        Expander.Literal,
        Expander.Macro,
        bool,
        int,
        float,
        list,
        tuple,
        set,
        dict,
        bytes,
        bytearray,
        range,
        type(None),
        *opaque_types.keys(),
    )

    match_pointer : re.Pattern = re.compile(r"<(\w+) (\w+) at 0[xX][0-9a-fA-F]+>")

    @dataclass
    class Opts:
        depth : int = 1
        indent : int = 0
        print_id : bool = True
        color_code : bool = True
        tab : str = "    "
        len : int = 80
        max : int = 80
        flat : bool = False

    class LineTooLong(Exception):
        pass

    @classmethod
    def dump(
        cls,
        #key,
        val,
        depth=1,
        indent=0,
        print_id=True,
        color_code=False,
        max=80,
        len=0,
        tab="    ",
    ):
        opts = Dumper.Opts(depth, indent, print_id, color_code, tab, len, max, flat = False)
        #return cls._dump_to_str(key, val, opts, set())
        return cls._dump_to_str(None, val, opts, set())

    @classmethod
    def _dump_to_str(cls, key, val : Any, opts, seen : set):
        if key == "__builtins__":
            return cls._dump_prefix(key, val, opts) + "<builtins>"
        elif hasattr(type(val), "__dump__"):
            return val.__dump__(key, opts, seen)
        elif inspect.isroutine(val) or inspect.isclass(val) or inspect.ismodule(val):  # noqa: SIM114
            return cls._dump_scalar(key, val, opts, seen)
        elif isinstance(val, (str, bytes, bytearray)):
            return cls._dump_scalar(key, val, opts, seen)
        elif isinstance(val, abc.Collection):
            return cls._dump_vector(key, val, val, opts, seen)
        elif hasattr(val, "__dict__"):
            return cls._dump_vector(key, val, val.__dict__, opts, seen)
        else:
            return cls._dump_scalar(key, val, opts, seen)

    @classmethod
    def _dump_scalar(cls, key, val : Any, opts, seen : set):
        return cls._dump_prefix(key, val, opts) + repr(val)

    @classmethod
    def _dump_vector(cls, key, val, contents, opts, seen : set):
        prefix = cls._dump_prefix(key, val, opts)

        if id(val) in seen:
            return prefix + "<ref loop>"
        seen.add(id(val))

        if isinstance(contents, tuple):
            items = [(None, v) for v in contents]
            ld, items, rd = '(', items, ",)" if len(items) == 1 else ')'
        elif isinstance(contents, abc.Mapping):
            ld, items, rd = '{', list(contents.items()), '}'
        elif isinstance(contents, abc.Collection):
            items = [(None, v) for v in contents]
            ld, items, rd = '[', items, ']'
        else:
            raise AssertionError(f"Don't know what to do with {type(val)}") # pragma: no cover

        # This slightly odd construct is so that when a deeply nested container doesn't fit on a
        # line, we rewind the callstack back to the topmost container that was not forced to be
        # flat.

        if opts.flat:
            return prefix + cls._dump_flat_vector(key, ld, items, rd, replace(opts, flat = True), set(seen))
        else:
            try:
                return prefix + cls._dump_flat_vector(key, ld, items, rd, replace(opts, flat = True), set(seen))
            except Dumper.LineTooLong:
                return prefix + cls._dump_deep_vector(key, ld, items, rd, opts, set(seen))


    @classmethod
    def _dump_flat_vector(cls, key, ld, items, rd, opts, seen : set):
        result = ld

        for i in range(len(items)):
            result += cls._dump_to_str(items[i][0], items[i][1], opts, set(seen))
            if i < len(items) - 1: result += ", "
            if opts.len + len(result) + len(rd) > opts.max:
                raise Dumper.LineTooLong()

        return result + rd

    @classmethod
    def _dump_deep_vector(cls, key, ld, items, rd, opts, seen : set):
        result = ld + '\n'

        if opts.depth == 0:
            return ld + "..." + rd
        opts = replace(opts, depth = opts.depth - 1)

        # len(pad) + 1 for the trailing comma
        pad = opts.tab * (opts.indent + 1)
        new_opts = replace(opts, len = len(pad) + 1, indent = opts.indent + 1)

        for i in range(len(items)):
            result += pad + cls._dump_to_str(items[i][0], items[i][1], new_opts, set(seen))
            if i < len(items) - 1: result += ','
            result += '\n'

        return result + (opts.tab * opts.indent) + rd

    @classmethod
    def _dump_prefix(cls, key, val, opts):
        prefix = ""
        if key: prefix += f"{key}"
        if type(val) not in Dumper.base_types:
            if prefix: prefix += ": "
            prefix += type(val).__name__
            if opts.print_id: prefix += "@" + Utils.hex_id(val)
        if prefix: prefix += " = "
        return prefix

class Utils:

    @staticmethod
    def stringify(variant) -> str:
        """Converts any type into a template-compatible string."""
        result = ""
        for v in Utils.yield_values(variant):
            if isinstance(v, Task):
                raise AssertionError("We should never be stringifying tasks, something is broken")
            if result:
                result += " "
            result += str(v) if v is not None else ""
        return result

    @staticmethod
    def in_event_loop() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    @staticmethod
    def cross_join(reduce, lhs, rhs, *args) -> list[str]:
        """
        This function does a 'cross join' in the database sense, every line in lhs will be joined
        to every line in rhs (and this will be repeated with *args if present). This is useful for
        adding prefixes / suffixes to a bunch of strings, or generating all possible combinations
        of two sets of options, et cetera.
        """

        lhs2 = Utils.flatten(lhs)
        rhs2 = Utils.flatten(Utils.cross_join(reduce, rhs, *args) if len(args) > 0 else rhs)
        result = [reduce(lh, rh) for lh in lhs2 for rh in rhs2]
        return result if len(result) > 1 else result[0]

    @staticmethod
    def weave(lhs, rhs, *args) -> list[str]:
        return Utils.cross_join(lambda x, y: x + y, lhs, rhs, *args)

    @staticmethod
    def obj_to_float(obj) -> float:
        """
        Generates a 'random' float in the range [0.0,1.0) by hashing the object's ID.
        """
        temp = Stats.hash(id(obj), 0)
        return (temp & 0xFFFFFFFF) / 0x100000000

    @staticmethod
    def obj_to_hex(obj) -> int:
        hue = Utils.obj_to_float(obj)
        r, g, b = colorsys.hsv_to_rgb(hue, 0.3, 1.0)
        r, g, b = (int(r * 255), int(g * 255), int(b * 255))
        return (r << 16) | (g << 8) | b

    @staticmethod
    def run_cmd(cmd : str):
        """Runs a console command synchronously and returns its stdout with whitespace stripped."""
        result = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        return result

    @staticmethod
    def flatten(variant):
        return list(Utils.yield_values(variant))

    @staticmethod
    def yield_values(variant) -> Any:
        if variant is None:
            return
        elif isinstance(variant, abc.Mapping):
            for v in variant.values():
                yield from Utils.yield_values(v)
        elif isinstance(variant, (list, tuple, set)):
            for v in variant:
                yield from Utils.yield_values(v)
        else:
            yield variant

    @staticmethod
    def hex_id(obj):
        return hex(id(obj))[-4:].upper()
        #return f"0x{id(obj):016x}"

    @staticmethod
    def instance_tag(obj):
        return type(obj).__name__ + "@" + Utils.hex_id(obj)

    @classmethod
    def commands_to_string(cls, commands):
        commands = Utils.flatten(commands)
        if len(commands) and callable(commands[0]):
            commands = [c.__name__ for c in commands]
        return "; ".join(commands)

    @staticmethod
    def tree_map(func) -> abc.Callable[..., str]:
        """Turns a function into one that can be applied to arbitrarily nested containers."""

        @wraps(func)
        def wrapper(obj, *args, **kwargs):
            if isinstance(obj, dict):
                return type(obj)((k, wrapper(v, *args, **kwargs)) for k, v in obj.items())
            if isinstance(obj, (list, tuple, set)):
                return type(obj)(wrapper(v, *args, **kwargs) for v in obj)
            return func(obj, *args, **kwargs)

        return cast(abc.Callable[..., str], wrapper)

    @staticmethod
    def tree_all(func):
        """Turns a predicate into one that can be applied to arbitrarily nested containers."""

        @wraps(func)
        def wrapper(variant, *args, **kwargs):
            return all(func(v, *args, **kwargs) for v in Utils.yield_values(variant))

        return wrapper

class Log:

    class Color(int, Enum):
        """12 half-saturated, 80% value Log.Color evenly spaced around the HSV wheel"""

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

        def __enter__(self):
            self.old_color = Log.current_color
            Log.current_color = self
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            Log.current_color = self.old_color
            return False

    class Level(int, Enum):
        FATAL    = 0  # Fatal always beats quiet
        QUIET    = 10
        CRITICAL = 20 # Should critical/error beat quiet?
        ERROR    = 30
        WARNING  = 40
        NORMAL   = 50
        VERBOSE  = 60
        DEBUG    = 70

        def __enter__(self):
            self.old_log_level = Log.log_level_in
            Log.log_level_in = self
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            Log.log_level_in = self.old_log_level
            return False

        def __bool__(self):
            return self.value <= Log.log_level_out

    log_level   : int  = Level.NORMAL
    log_quiet   : bool = False
    log_verbose : bool = False
    log_debug   : bool = False
    log_trace   : bool = False
    log_wrap    : bool = False
    log_color   : bool = True
    log_time    : bool = True

    con_w         = 80
    time_origin   = time.perf_counter()
    indent_stack  = []
    current_color = -1
    line_buffer   = ""
    match_escapes = re.compile(r"(\x1B.*?m)")
    log_level_in  = Level.NORMAL
    log_level_out = Level.NORMAL # log level we want to appear in the log

    @classmethod
    def reset(cls, params):

        log_params  = {k: params.pop(k) for k in list(params) if k.startswith("log_")}

        cls.log_level   : int  = cast(int,  log_params["log_level"])
        cls.log_quiet   : bool = cast(bool, log_params["log_quiet"])
        cls.log_verbose : bool = cast(bool, log_params["log_verbose"])
        cls.log_debug   : bool = cast(bool, log_params["log_debug"])
        cls.log_trace   : bool = cast(bool, log_params["log_trace"])
        cls.log_wrap    : bool = cast(bool, log_params["log_wrap"])
        cls.log_color   : bool = cast(bool, log_params["log_color"])
        cls.log_time    : bool = cast(bool, log_params["log_time"])

        cls.con_w         = shutil.get_terminal_size().columns
        cls.time_origin   = time.perf_counter()
        cls.indent_stack  = []
        cls.current_color = -1
        cls.line_buffer   = ""
        cls.match_escapes = re.compile(r"(\x1B.*?m)")

        if cls.log_level is not None:
            if isinstance(cls.log_level, str):
                cls.log_level = Log.Level[cls.log_level.upper()]
            elif isinstance(cls.log_level, int):
                cls.log_level = Log.Level(cls.log_level)
            else:
                raise ValueError(f"Got an unknown log_level '{type(cls.log_level)} = {cls.log_level}'")

        # The individual -T/-D/-V/-Q flags override --log_level, with the 'loudest' flag winning.

        if cls.log_debug:
            cls.log_level = Log.Level.DEBUG
        elif cls.log_verbose:
            cls.log_level = Log.Level.VERBOSE
        elif cls.log_quiet:
            cls.log_level = Log.Level.QUIET

        cls.log_level_in  : int = cls.log_level
        cls.log_level_out : int = cls.log_level

    @classmethod
    @contextmanager
    def color(cls, new_color):
        old_color = cls.current_color
        try:
            cls.current_color = new_color
            yield
        finally:
            cls.current_color = old_color

    @classmethod
    def indent(cls, color = 0):
        ansi = cls.hex_to_ansi(color) if cls.log_color else ""
        cls.indent_stack.append(ansi + "│ " + cls.reset_color())

    @classmethod
    def dedent(cls):
        cls.indent_stack.pop()

    @classmethod
    @contextmanager
    def indent2(cls, color = 0):
        cls.indent(color)
        yield
        cls.dedent()

    @classmethod
    def hex_to_ansi(cls, hex):
        if hex:
            r, g, b = ((hex >> 16) & 0xFF, (hex >>  8) & 0xFF, (hex >>  0) & 0xFF)
            return f"\x1B[38;2;{r};{g};{b}m"
        else:
            return ""

    @classmethod
    def reset_color(cls):
        if cls.current_color != 0 and cls.log_color:
            return "\x1B[0m"
        else:
            return ""

    @classmethod
    def log(cls, text):
        if not isinstance(text, str) or len(text) == 0:
            return

        if cls.log_level_in > cls.log_level_out:
            return

        if cls.current_color >= 0 and cls.log_color:
            hex = cls.current_color
            color_prefix = cls.hex_to_ansi(hex) if cls.log_color else ""
            color_suffix = cls.reset_color()
        else:
            color_prefix = ""
            color_suffix = ""

        lines = text.splitlines(keepends=True)

        for line in lines:
            if cls.line_buffer == "":
                cls.line_buffer += cls.get_timestamp() + cls.get_indentation()

            # Wrap the line in the color prefix/suffix, but don't lose newlines.
            if line[-1] == '\n':
                line = line[:-1]
                line = color_prefix + line + color_suffix + '\n'
            else:
                line = color_prefix + line + color_suffix

            cls.line_buffer += line
            if cls.line_buffer[-1] == '\n':
                cls.flush()

    @classmethod
    def flush(cls):
        # Dumps the line buffer to stdout (if we're not in quiet mode) and then clears it.
        if cls.line_buffer:
            # If the line wasn't finished (because we're exiting the app), stick a newline on it.
            if cls.line_buffer[-1] != '\n':
                cls.line_buffer += '\n'

            if not cls.log_wrap:
                cls.line_buffer = cls.clip_printable(cls.line_buffer, cls.con_w)

            assert cls.log_level_in is not None

            if cls.log_level_in <= cls.log_level_out:
                sys.stdout.write(cls.line_buffer)

            cls.line_buffer = ""

    @classmethod
    def log_exception(cls, ex):
        tb = traceback.extract_tb(ex.__traceback__)
        if tb:
            frame = tb[-1]
            cls.log("  text = ")
            with cls.color(0xFFFF00):
                cls.log(f"'{ex}'\n")
            cls.log(f"  file = {frame.filename}\n")
            cls.log(f"  func = {frame.name}\n")
            cls.log(f"  line = {frame.lineno}\n")
            cls.log(traceback.format_exc() + "\n")
        else: # pragma: no cover
            cls.log(f"Could not extract traceback from {ex}!")

    @classmethod
    def get_timestamp(cls):
        """Returns the timestamp string that is placed at the left of log entries."""
        return f"[{time.perf_counter() - cls.time_origin:8.3f}] " if cls.log_time else ""

    @classmethod
    def get_indentation(cls):
        return "".join(cls.indent_stack)

    @classmethod
    def clip_printable(cls, text, width) -> str:
        """
        Clips a string with embedded escape codes (such as ANSI color codes) so that it fits in
        'width' without breaking the escape codes.

        If the printable portion exceeds 'width', it will be clipped and capped with '...'.
        """
        if not text or not isinstance(text, str) or len(text) < 3:
            return text #pragma: no cover

        # We don't want to clip trailing newlines - if one is present, just remember it was there
        # and we'll stick it back on at the end.
        newline = text[-1] == '\n'
        if newline:
            text = text[:-1]

        # Split the text using the escape sequences as separators.
        chunks = cls.match_escapes.split(text)

        # Even chunks are printable text, odd chunks are escape sequences.
        # If the printable characters fit on the line, we don't need to clip.

        print_len = 0
        for i in range(0, len(chunks), 2):
            print_len += len(chunks[i])

        if print_len <= width:
            if newline:
                text += "\n"
            return text

        # If we do need to clip, stick the chunks back together until we exceed width-3, then
        # clip the last chunk and add "...". After clipping, emit all the remaining escape codes in
        # case they do something important.

        accum = 0
        result = ""
        clipped = False
        for i, chunk in enumerate(chunks):
            if i & 1:
                # Escape code
                result += chunk
            elif not clipped:
                # Printable text
                accum += len(chunk)
                if accum > width - 3:
                    result += chunk[:-(accum - width + 3)] + "..."
                    clipped = True
                else:
                    result += chunk

        # Stick that trailing newline back on.
        if newline:
            result += '\n'

        return result

class Path:
    # These functions wrap the os.path.* functions so that they work on arbitrary trees

    @staticmethod
    def resolve_path(path, strict):
        """
        This tries to convert a path containing potential env variable references and stuff into a
        real path.
        """
        path = os.path.expandvars(path)
        path = pathlib.Path(path).expanduser()
        path = path.resolve(strict = strict)
        return str(path)

    resolve  = Utils.tree_map(lambda path : Path.resolve_path(path, strict = True))
    abspath  = Utils.tree_map(os.path.abspath)
    basename = Utils.tree_map(os.path.basename)
    dirname  = Utils.tree_map(os.path.dirname)
    swapext  = Utils.tree_map(lambda p, new_ext : os.path.splitext(p)[0] + new_ext)

    isabs    = Utils.tree_all(os.path.isabs)
    isfile   = Utils.tree_all(os.path.isfile)
    isdir    = Utils.tree_all(os.path.isdir)
    exists   = Utils.tree_all(os.path.exists)

    # WARNING - Both 'startswith' and 'relpath' below can throw ValueError if there's a mix of
    # abs/rel paths, or if the paths are on different volumes in Windows. We don't handle this yet,
    # but we will need to eventually. If this occurs inside a macro you'll see the exception in the
    # macro expansion trace and the macro will be returned unexpanded. Using 'commonpath' here is
    # probably worth it though, as it handles some annoying edge cases.

    startswith = Utils.tree_all(lambda p, parent : os.path.commonpath([p, parent]) == parent)

    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with 'commonpath' and 'removeprefix'.

    @staticmethod
    def relpath(lhs, rhs):
        # FIXME should this use cross_join?
        if isinstance(lhs, (list, tuple, set)):
            return [Path.relpath(lh, rhs) for lh in lhs]
        if isinstance(rhs, (list, tuple, set)):
            return [Path.relpath(lhs, rh) for rh in rhs]

        prefix = ""
        try:
            prefix = os.path.commonpath([lhs, rhs])
        except Exception:
            with Log.Level.ERROR, Log.Color.RED:
                Log.log("Commonpath failed for '{lhs}' and '{rhs}'\n")
            raise

        if lhs == rhs:
            result = "."
        elif prefix != rhs:
            result = lhs
        else:
            result = lhs.removeprefix(prefix + os.sep)

        return result

    @staticmethod
    def join(lhs, rhs, *args) -> str | list[str]:
        return Utils.cross_join(os.path.join, lhs, rhs, *args)

class Stats:

    @classmethod
    def reset(cls):
        cls.stat_calls : int = 0
        cls.hash_calls : int = 0
        cls.hash_bytes : int = 0
        cls.hash_time  : float = 0

    def __init__(self, comp_db_path, stat_db_path, build_dry):

        #self.config = config
        self.comp_db_path = comp_db_path
        self.stat_db_path = stat_db_path
        self.build_dry    = build_dry

        # Tally up the rebuild reasons for debugging
        self.reasons = Counter()

        # Hash, size, mtime, command for each file in the previous build.
        # Command is only set for output files.
        self.old_stat_db : Dict = Dict({})
        self.new_stat_db : Dict = Dict({})

    @classmethod
    def hash(cls, key, h):
        # For some reason Python's stdlib does not have a fast non-crypto 64-bit hash, so we
        # improvise one here from two 32-bit hashes that are implemented in C. This is not as good
        # as a real 64-bit hash, but it'll do.

        def split(h):
            return (h & 0xFFFFFFFF, (h >> 32) & 0xFFFFFFFF)

        def join(h0, h1):
            return (h1 << 32) | h0

        # Feistel-ish mix to tangle up the two 32-bit hashes.
        def mix(h0, h1):
            assert isinstance(h0, int) and h0 <= 0xFFFFFFFF
            assert isinstance(h1, int) and h1 <= 0xFFFFFFFF

            c = 0x58949537 # meaningless odd constant
            j = 0x90678F0D # another meaningless constant
            k = 0x48728717 # another meaningless constant

            h0 = j ^ h1 ^ ((h0 * c) & 0xFFFFFFFF)
            h1 = k ^ h0 ^ (h0 >> 16)
            return (h0, h1)

        if isinstance(key, bytes):
            h0, h1 = split(h)
            h0 = zlib.crc32(key, h0)
            h1 = zlib.adler32(key, h1)
            h0, h1 = mix(h0, h1)
            h0, h1 = mix(h0, h1)
            h0, h1 = mix(h0, h1)
            h = join(h0, h1)
        elif isinstance(key, int):
            h0, h1 = mix(*split(h))
            k0, k1 = mix(*split(key))
            h0, h1 = mix(k0 ^ h0, k1 ^ h1)
            h = join(h0, h1)
        elif isinstance(key, str):
            h = cls.hash(key.encode(), h)
        elif callable(key):
            h = cls.hash(key.__name__, h)
            h = cls.hash(key.__defaults__, h)
            h = cls.hash(key.__code__.co_code, h)
            h = cls.hash(key.__code__.co_consts, h)
        elif isinstance(key, dict):
            for k, v, in sorted(key.items()):
                h = cls.hash(k, h)
                h = cls.hash(v, h)
        elif isinstance(key, (list, tuple, set)):
            for k in key:
                h = cls.hash(k, h)
        elif key is None:
            h = join(*mix(*split(h)))
        else:
            raise TypeError(f"Don't know how to hash a {type(key)} = {key}")
        return h

    @classmethod
    def hash_file(cls, abs_path, h = 0):
        Stats.hash_calls += 1
        time_a = time.perf_counter()
        with open(abs_path, "rb") as f:
            blob = f.read()
            Stats.hash_bytes += len(blob)
        result = cls.hash(blob, h)
        time_b = time.perf_counter()
        Stats.hash_time += time_b - time_a
        return result

    def get_stats(self, file : str, command = None):
        Stats.stat_calls += 1

        _hash = Stats.hash_file(file)
        _stat = os.stat(file)

        command = Utils.commands_to_string(command)

        stat = Dict(
            hash = _hash,
            st_size = _stat.st_size,
            st_mtime_ns = _stat.st_mtime_ns,
            command = command
        )

        return stat

    @staticmethod
    @contextmanager
    def write_and_swap(filename):
        """
        Works like "with open(...) as f:", except we first write to filename.temp and then
        atomically swap it with the original filename once the user is done with it.
        """
        temp_filename = filename + ".temp"
        try:
            with open(temp_filename, "w") as temp_file:
                yield temp_file
                temp_file.flush()
                os.fsync(temp_file.fileno())
        finally:
            os.replace(temp_filename, filename)

    @classmethod
    def save_json(cls, variant, path):
        os.makedirs(os.path.dirname(path), exist_ok = True)
        with cls.write_and_swap(path) as file:
            json.dump(variant, file, indent=4, default=lambda x: x.__dict__)
            file.write("\n")

    @classmethod
    def load_depfile(cls, filename : str, format : str, task_cwd : str) -> list[str]:
        if not os.path.isfile(filename):
            return []

        with open(filename, encoding="utf-8") as depcontents:
            deplines = None
            if format == "msvc":
                # MSVC /sourceDependencies
                deplines = json.load(depcontents)["Data"]["Includes"]
            elif format == "gcc":
                # GCC -MMD
                # NOTE: This does not handle filenames with escaped spaces in them, but I don't
                # want to write a whole .d parser yet.
                deplines = depcontents.read()
                deplines = re.sub(r"\\\s*\n", "", deplines)
                deplines = deplines.split()
                deplines = [d for d in deplines if d[-1] != ':']
            else:
                raise Task.BROKEN(f"Invalid depfile format {format}") # pragma: no cover

        # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
        deplines = [Path.join(task_cwd, d) for d in deplines]

        return cast(list[str], deplines)

    def save_stat_db(self, script : Script):
        if self.build_dry:
            return

        stat_db = {}

        # ------------------------------------
        # Gather stats for all input files in the task.

        for task in script.yield_tasks():
            if not task._complete:
                continue

            for file in Utils.yield_values(task.in_files):
                stat_db[file] = self.get_stats(file)

            if task.in_depfile:
                deplines = Stats.load_depfile(task.in_depfile, task.config.depformat, task.config.task_cwd)
                for file in deplines:
                    stat_db[file] = self.get_stats(file) # type: ignore

        # We gather stats from output files in a second pass so that their .command fields
        # overwrite any blank ones from the first pass.
        for task in script.yield_tasks():
            if not task._complete:
                continue

            for file in Utils.yield_values(task.out_files):
                stat_db[file] = self.get_stats(file, task.config.command)

        Stats.save_json(stat_db, self.stat_db_path)

        # ------------------------------------
        # And do the same for compile_commands.json with a slightly different format.

        comp_db = {}

        for task in script.yield_tasks():
            if not task._complete:
                continue

            for file in Utils.yield_values(task.in_files):
                # Haven't tested this in an IDE, but I think it matches the spec.
                comp_db[file] = {
                    "directory" : task.config.task_cwd,
                    "command"   : Utils.commands_to_string(task.config.command),
                    "file"      : file,
                }

        Stats.save_json(list(comp_db.values()), self.comp_db_path)

    def rebuild_reason(self, task) -> str:
        """
        Figures out why we have to run a Task, or returns "" if we don't.
        """

        # ------------------------------------
        # Check the trivial reasons to rebuild

        if task.config.build_force:
            self.reasons["forced"] += 1
            return "Target forced to rebuild"

        has_input = any(Utils.yield_values(task.in_files))
        if not has_input:
            self.reasons["no inputs"] += 1
            return "Always rebuild a target with no inputs"

        has_output = any(Utils.yield_values(task.out_files))
        if not has_output:
            self.reasons["no outputs"] += 1
            return "Always rebuild a target with no outputs"

        # ------------------------------------

        for filename in Utils.yield_values(task.in_files):
            if reason := self.check_stat(filename):
                return reason

        for filename in task._old_deplines:
            if reason := self.check_stat(filename):
                return reason

        for filename in Utils.yield_values(task.out_files):
            if reason := self.check_stat(filename, task.config.command):
                return reason

        self.reasons["*task clean"] += 1
        return ""

    def check_stat(self, filename, command = None):
        if not Path.exists(filename):
            self.reasons["file missing"] += 1
            return f"File missing: {filename}"

        if filename not in self.old_stat_db:
            self.reasons["stat missing"] += 1
            return f"Stat missing: {filename}"

        old_stat = self.old_stat_db[filename]
        new_stat = self.get_stats(filename, command)

        if old_stat.st_mtime_ns != new_stat.st_mtime_ns:
            self.reasons["mtime mismatch"] += 1
            return f"Mtime mismatch {old_stat.st_mtime_ns} != {new_stat.st_mtime_ns} for : {filename}"

        if old_stat.st_size != new_stat.st_size:
            self.reasons["size mismatch"] += 1
            return f"Size mismatch {old_stat.st_size} != {new_stat.st_size} for : {filename}"

        if old_stat.hash != new_stat.hash:
            self.reasons["hash mismatch"] += 1
            return f"Hash mismatch {old_stat.hash} -> {new_stat.hash} for : {filename}"

        if command is not None and old_stat.command != new_stat.command:
            self.reasons["command changed"] += 1
            return f"Command used to generate file has changed : {filename!r} : {old_stat.command!r} : {new_stat.command!r}"

        # Does not need to rebuild based on file stats / hash
        self.reasons["*hash match"] += 1
        return ""

class Task:
    # Task object + bookkeeping

    class FAILED(Exception):    pass
    class CANCELLED(Exception): pass
    class SKIPPED(Exception):   pass
    class BROKEN(Exception):    pass

    def __init__(self, *args, **kwargs):
        # The task's 'raw' flags contain everything passed in to hancho.Task(), but no templates
        # are expanded.

        self.script = Hancho.cv_script.get()
        self.params = Dict(*args, **kwargs)
        self.params.setdefault("job_size", 1)

        # The task's 'cooked' config contains only the mandatory fields needed to run the command.
        # It is expected that build scripts will need to read task.params/confing
        # in order to implement task callbacks, so this field is not underscore-prefixed.

        self.config = Dict()

        self.onion = Onion(
            self.script.onion,
            task_params = self.params,
            task_config = self.config,
        )

        # Build scripts also may need to see the complete list of inputs/outputs to a task in
        # addition to the individual in_/out_ fields, so these are public.

        self.in_files  = {}
        self.out_files = {}
        self.in_depfile : str = ""

        # ------------------------------------
        # Implementation details below this line

        self._enabled = False

        # This must be populated -before- the task starts, as we need it to queue up the task's
        # dependencies
        self.input_tasks = [v for v in Utils.yield_values(self.params) if isinstance(v, Task)]

        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._aio_task : asyncio.Task | None = None

        # We remember the aio context we were in when this task was created so that we can return
        # to it when the task starts.
        self._aio_context = contextvars.copy_context()

        # Input dependencies read from the pre-existing source.o.d file.
        self._old_deplines = []

        # Input dependencies read after compilation from the new source.o.d file.
        self._new_deplines = []

        # Why this task rebuilt, or "" if it did not need to rebuild.
        self._reason = ""

        # The "return value" for the task as a whole, or "None" if the task was successful.
        self._error : BaseException | None = None

        # Bookkeeping stuff
        self._task_id : int = 0
        self._stdout : str = ""
        self._stderr : str = ""
        self._cores = 0
        self._complete = False

        self.script.tasks.append(self)

        # Auto-start the task if it was created dynamically during the build.
        if Utils.in_event_loop():
            self.enable_task()

    def __repr__(self):
        return Dumper.dump(self)

    # Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
    # Dicts make deep copies and we want dicts to store Tasks, so we work around it by making
    # Tasks just return themselves when copied.

    def __copy__(self):
        return self

    def __deepcopy__(self, _):
        return self

    def log(self, message : str):
        # Log helper that adds the [ NN/ XX] tag before the log line.
        for line in message.splitlines(keepends=True):
            with Log.Color.LIME:
                if not Log.line_buffer:
                    Log.log(f"[{self._task_id:3d}/{Runner.tasks_started:3d}] ")
            Log.log(line)

    def enable_task(self):
        if not self._enabled:
            self._enabled = True
            if Utils.in_event_loop():
                Runner.create_aio_task(self)

    async def task_top(self):
        with Log.Level.VERBOSE:
            self.log(Utils.instance_tag(self) + " starting\n")

        task   = self
        config = self.config
        script = self.script

        try:
            # Await all tasks in our input fields and then flatten them.
            await task.await_inputs()

            # Expand all mandatory fields in the raw config and fix raw file paths.
            task.expand_task()

            # Update mtime/hash for all input and output files in this task if they exist.

            # If there's a depfile from a previous build, load it so we can use it below.
            if self.in_depfile:
                self._old_deplines = Stats.load_depfile(
                    self.in_depfile, config.depformat, config.task_cwd
                )

            # Inputs are ready, templates are expanded, time to run the task.
            task.sanity_check()

            # Dry runs early out after the task is initialized but before we do .exists() checks or
            # run any commands.
            if script.config.build_dry:
                return

            # Paths updated. See if we need to rebuild our outputs.
            task._reason = script.stats.rebuild_reason(task)
            if not task._reason:
                raise Task.SKIPPED(f"Task is up-to-date: '{config.name}' : '{config.desc}'")

            # Wait for enough jobs to free up to run this task.
            task._cores = await Runner.acquire(config.job_size)

            # OK, let's go!
            await task.task_main()

            # And we're done
            return task.out_files

        except asyncio.CancelledError as ex:
            with Log.Level.VERBOSE:
                task.log(f"<asyncio.CancelledError {ex}>\n")
            task._error = ex
        except Task.BROKEN as ex:
            task.log_exception("Task broken!", ex)
            task._error = ex
        except Task.FAILED as ex:
            task.log_exception("Task failed!", ex)
            task._error = ex
        except Task.SKIPPED as ex:
            with Log.Level.VERBOSE:
                task.log(str(ex) + "\n")
            task._error = ex
        except Exception as ex:
            task.log_exception("Task threw an exception!", ex)
            with Log.Level.ERROR:
                Log.log(traceback.format_exc() + "\n")
            task._error = ex
        finally:
            Runner.release(task._cores)

        raise task._error

    async def await_inputs(self):
        # NOTE: Hancho _cannot_ have dependency cycles unless you do something really sketchy via
        # modifying tasks after they're created but before they're started. If you point task B's
        # inputs at task A and task A's inputs at task B and it blows up, that's on you.

        for input_task in self.input_tasks:
            if input_task._aio_task is None:
                raise AssertionError("One of a task's input sub-tasks was not started") # pragma: no cover
            try:
                await input_task._aio_task
            except Task.SKIPPED:
                # This input task didn't need to rebuild.
                pass
            except Exception as ex:
                self._error = Task.CANCELLED(f"Task is cancelled: '{self.config['name']}' : '{self.config['desc']}'")
                raise self._error from ex

    def expand_task(self):
        with Log.Level.DEBUG:
            self.log("Task config before expand:\n")
            self.log(Dumper.dump(self.params) + "\n")

        task_keys = ['name', 'desc', 'command', 'repo_root', 'build_root', 'script_cwd',
                     'build_dir', 'task_cwd', 'build_force', 'depformat', 'job_size']

        for key in task_keys:
            self.config[key] = None

        # We need to expand the build dir first so we can use it in fix_paths.
        build_dir = self.onion.build_dir

        # Then we expand all io fields and fix their paths.
        for _field in self.params:
            if not _field.startswith("in_") and not _field.startswith("out_"):
                continue

            files = [
                val.out_files if isinstance(val, Task) else val
                for val in Utils.yield_values(self.params[_field])
            ]

            files = Utils.flatten(files)
            files = Expander.expand(self.onion, files)
            files = self.fix_paths(_field, files, build_dir)

            self.config[_field] = files[0] if len(files) == 1 else files

            if _field == "in_depfile":
                self.in_depfile = cast(str, files[0])
            elif _field.startswith("in_"):
                self.in_files[_field] = files
            elif _field.startswith("out_"):
                self.out_files[_field] = files

        # And finally we expand the onion to fill the config.
        for key in task_keys:
            self.config[key] = self.onion[key]

        self.config.command     = Utils.flatten(self.config.command)

        with Log.Level.DEBUG:
            self.log("Task config after expand:\n")
            self.log(Dumper.dump(self.config) + "\n")

    async def task_main(self):
        task   = self
        config = task.config

        # Run all the task's commands

        text  = repr(config.name) if config.name else ""
        text += " : " if config.name and config.desc else ""
        text += repr(config.desc) if config.desc else ""

        with Log.Level.NORMAL, Log.Color.TEAL:
            task.log(f"Task {text}\n")

        with Log.Level.VERBOSE, Log.color(0x606060):
            task.log(f"Task rebuilding because: {task._reason}\n")

        time_a = time.perf_counter()

        for command in cast(list, config.command):
            if command is None:
                continue
            elif callable(command):
                await task.call_callback(command)
            else:
                await task.run_command(command)

        time_b = time.perf_counter()

        with Log.Level.VERBOSE, Log.color(0x606060):
            message  = f"Task took {time_b-time_a:8.6f} sec: {text}\n"
            task.log(message)

        # See if the task wrote all its output files

        for file in Utils.yield_values(task.out_files):
            if not os.path.exists(file):
                raise Task.FAILED(f"Task ran, but output file still missing: {file}")

        # Done!

    def fix_paths(self, field, file, build_dir):
        """
        Input and output file paths in .hancho scripts are declared relative to the directory the
        script is in (stored in the config under 'script_cwd').
        In general we want to run commands from the root of the repo and store output files in
        repo/build, so we need to fix up the paths to match.
        """
        if isinstance(file, (list, set, tuple)):
            return [self.fix_paths(field, f, build_dir) for f in file]
        if isinstance(file, abc.Mapping):
            return {k:self.fix_paths(field, f, build_dir) for k, f in file}

        # Join script_cwd with the filename to produce an absolute path.
        file = Path.join(self.script.config.script_cwd, file)

        # File paths _must_ be abs'd after joining, otherwise they might look like they're under
        # script_dir, but they're not because the paths could have "../../../../.." in them.
        file = Path.abspath(file)

        # Move all outputs under build_dir and ensure their directories exist.
        # Note - This will also move "in_depfile" under build_dir - this is _intentional_ as
        # it's an _output_ from the compiler and is not checked in to the source tree.
        if field.startswith("out_") or field == "in_depfile":
            if not Path.startswith(file, build_dir):
                file = Path.relpath(file, self.script.config.script_cwd)
                file = Path.join(build_dir, file)

            if not self.script.config.build_dry:
                os.makedirs(Path.dirname(file), exist_ok=True)

        return file

    def sanity_check(self):
        # Check for all task issues that break the build
        task = self
        task_config = self.config
        script_config = self.script.config

        if not Path.exists(task_config.task_cwd):
            raise Task.BROKEN(f"Task working directory '{task_config.task_cwd}' does not exist")

        if not Path.startswith(task_config.build_dir, script_config.repo_root):
            raise Task.BROKEN(f"The build dir {task_config.build_dir} is not under repo.root {task_config.repo_root}")

        # In order to provide the least amount of bafflement to users, CLI commands execute
        # from task_cwd (which is usually the root of the repo, the most common cwd)
        # and callbacks execute from dir(script_path) (because you expect to be in the same
        # directory as the script when the callback is firing).

        # This means that pre-rel-ified paths can only be rel'd to one of the two cwds, not both.
        # And that means we disallow mixed cli/callback command lists.

        if isinstance(task_config.command, list):
            for command in task_config.command:
                if type(command) is not type(task_config.command[0]):
                    raise Task.BROKEN(f"Commands aren't the same type: {task_config.command}")

                # Check that task's commands are either strings or callables.
                if not isinstance(command, str) and not callable(command) and command is not None:
                    raise Task.BROKEN(f"Command {command} is not a string or a callable?")

        # In strict mode, we mark a task broken if its command still has curly braces.
        if script_config.build_strict:
            for command in cast(list, task_config.command):
                if not isinstance(command, str):
                    continue
                blocks = []
                if Expander._split_text(command, blocks):
                    raise Task.BROKEN("STRICT: Command has curly braces in it")

        # Check that all build files would end up under build_dir
        for file in Utils.yield_values(task.out_files):
            assert Path.isabs(file)
            if not Path.startswith(file, task_config.build_dir):
                raise Task.BROKEN(f"Path error, output file {file} is not under build dir {task_config.build_dir}")

        # Check for task collisions
        for file in Utils.yield_values(task.out_files):
            real_file = cast(str, Path.abspath(file))
            if real_file in Hancho.real_filenames:
                raise Task.BROKEN(f"TaskCollision: Multiple tasks build {real_file}")
            Hancho.real_filenames.add(real_file)

         # Check for missing inputs. We have to check build_dry, as the input files may only exist if
        # we're really running tasks.
        for file in Utils.yield_values(task.in_files):
            if not Path.isabs(file):
                raise Task.BROKEN(f"Somehow we got a non-abs path for an input file - {file}")  # pragma: no cover
            if not Path.exists(file) and not script_config.build_dry:
                raise Task.BROKEN(f"Input file missing - {file}")

        # Tasks should have at most one depfile.
        if "in_depfile" in self.config and isinstance(self.config.in_depfile, list):
            raise Task.BROKEN(f"Tasks can't have more than one dependency file! - {self.config.in_depfile}")

    async def run_command(self, command):
        task          = self
        task_config   = self.config
        script_config = self.script.config

        with Log.Level.VERBOSE, Log.Color.BLUE:
            task.log(f"{Path.relpath(task_config.task_cwd, script_config.repo_root)}$ {command}\n")

        proc = None
        try:
            # Create the subprocess via asyncio and then await the result.
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd    = task_config.task_cwd,
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.PIPE,
                start_new_session = True
            )

            (stdout_data, stderr_data) = await proc.communicate()

        except asyncio.CancelledError as ex: # pragma: no cover
            # The 'asyncio.CancelledError' exception is _special_. It's not an Exception, and it
            # usually (but not always) arises from hitting ctrl-c while the build is running.
            #
            # If we see a CancelledError while running a command, we can't trust asyncio to clean
            # up all cancelled processes, so we do it the hard way here and kill the whole process
            # group.
            #
            # Note - this only works on Linux. We may need a slightly different implementation for
            # Windows.
            if os.name == "posix" and proc is not None:
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL) #type:ignore
                await proc.wait()
            # Re-raise so that dependent tasks and the top-level except can see the error.
            raise ex
        except Exception as ex:
            # All other exceptions are treated as a task failure.
            raise Task.FAILED(f"Command threw an exception : {ex}") from ex

        task._stdout = stdout_data.decode(errors="replace")
        task._stderr = stderr_data.decode(errors="replace")

        if proc.returncode == 2:
            raise Task.BROKEN("Command return code was 2 : bash error")
        elif proc.returncode:
            raise Task.FAILED(f"Command return code was non-zero : {proc.returncode}")

        if task._stdout or task._stderr:
            with Log.Level.VERBOSE, Log.color(0x666666):
                task.log(task.dump_stdout())

    async def call_callback(self, command):
        script_config = self.script.config

        with Log.Level.VERBOSE, Log.Color.BLUE:
            callback_dir = Path.relpath(script_config.script_cwd, script_config.repo_root)
            self.log(f"{callback_dir}$ {command}\n")

        # Callbacks run from the script_dir where they were defined so that relative paths used
        # in the callback will be correct.
        with chdir(script_config.script_cwd):
            result = command(self)

        # It would seem like we wouldn't have to explicitly unwrap one level of await-ness here,
        # but apparently that's just how Python waitables work.
        if inspect.isawaitable(result):
            result = await result

        return result

    def dump_stdout(self) -> str:
        result = ""

        if self._stdout:
            result += "---------------- Stdout ----------------\n"
            result += self._stdout.strip() + "\n"

        if self._stderr:
            result += "---------------- Stderr ----------------\n"
            result += self._stderr.strip() + "\n"

        if self._stdout or self._stderr:
            result += "----------------------------------------\n"

        return result

    def log_exception(self, message, ex = None):
        task = self
        task_config = task.config
        script_config = self.script.config

        with Log.Level.ERROR, Log.Color.RED:
            Log.log("========================================\n")
            Log.log(message + "\n")
            Log.log("========================================\n")

            Log.log(f"Script    = {script_config.script_path}:\n")
            Log.log(f"Task      = '{task_config.name}' : '{task_config.desc}'\n")
            Log.log(f"os.getcwd = {os.getcwd()}\n")
            Log.log(f"task cwd  = {task_config.task_cwd}\n")
            Log.log(f"command   = {task_config.command}\n")
            if ex:
                Log.log_exception(ex)
            Log.log(task.dump_stdout())

            Log.log("========================================\n")

class Tracer:
    # Expansion tracing class used by Expander
    #
    # The traces generated look like this - the EX_XXXX prefix is an identifier for the Expander being
    # used so you can tell when the expander changes, the rest are the call arguments and the return
    # values.
    #
    # FIXME update this trace
    #
    # [    0.443765] EX_53B0.get('name')
    # [    0.443795] └ 'name' : str = '_'
    # [    0.443838] EX_53B0.get('desc')
    # [    0.443865] │ EX_53B0.expand('Linking C++ bin {out_bin}')
    # [    0.443894] │ │ EX_53B0.eval('{out_bin}')
    # [    0.443973] │ │ │ EX_53B0.get('out_bin')
    # [    0.443999] │ │ │ └ 'out_bin' : str = 'build/examples/hello_gtk/hello_gtk'
    # [    0.444028] │ │ └ '{out_bin}' : str = 'build/examples/hello_gtk/hello_gtk'
    # [    0.444054] │ └ 'Linking C++ bin {out_bin}' : str = 'Linking C++ bin build/examples/hello_gtk/hello_gtk'
    # [    0.444077] └ 'desc' : str = 'Linking C++ bin build/examples/hello_gtk/hello_gtk'
    # [    0.444112] EX_53B0.get('command')
    # [    0.444135] │ EX_53B0.expand('{toolchain.linker} {flags} -Wl,--start-group {in_objs} {in_libs} {sys_libs} -Wl,--end-group -o {out_bin}')
    # [    0.444166] │ │ EX_53B0.eval('{toolchain.linker}')
    # [    0.444211] │ │ │ EX_53B0.get('toolchain')
    # [    0.444233] │ │ │ └ 'toolchain' : Expander = EX_7AC0
    # [    0.444259] │ │ │ EX_7AC0.get('linker')
    # [    0.444273] │ │ │ └ 'linker' : str = 'x86_64-linux-gnu-g++'
    # [    0.444295] │ │ └ '{toolchain.linker}' : str = 'x86_64-linux-gnu-g++'
    # [    0.444320] │ │ EX_53B0.eval('{flags}')
    # [    0.444356] │ │ │ EX_53B0.get('flags')
    # [    0.444373] │ │ │ └ 'flags' : list = [None]
    # [    0.444398] │ │ └ '{flags}' : list = [None]

    def __init__(self, context : Dict | Onion, enter_message, name):
        self.enter_message = f"{enter_message}({name!r})"
        self.name = name
        self.color = None
        self.context = context
        self.result = None
        self.trace = Log.log_trace

    def save_result(self, result : Any):
        self.result = result

    def __enter__(self): # pragma: no cover
        if not self.trace:
            return self

        self.color = Utils.obj_to_hex(self.context)

        with Log.color(self.color):
            Log.log(f"┌ {Utils.instance_tag(self.context)}." + self.enter_message + "\n")
            Log.indent(self.color)

        return self

    def __exit__(self, exc_type, exc_value, tb): # pragma: no cover
        if not self.trace:
            return False

        with Log.color(self.color):
            if exc_type:
                Log.log(f"{exc_type.__name__}\n")
                Log.log(f"{exc_value}\n")
                Log.dedent()
                return

            type = self.result.__class__.__name__
            color = Utils.obj_to_hex(self.result)

            message = ""
            with Log.color(color):
                if isinstance(self.result, (Dict, Onion)):
                    message = f"└ {self.name!r} : {type} = {Utils.instance_tag(self.result)}\n"
                else:
                    message = f"└ {self.name!r} : {type} = {self.result!r}\n"

            Log.dedent()
            Log.log(message)

        return False

class Runner:

    @classmethod
    def reset(cls, params):
        cls.max_jobs = params.pop("max_jobs")
        cls.max_errors = params.pop("max_errors")

        cls.core_sem  : asyncio.Semaphore = asyncio.Semaphore(cls.max_jobs)
        cls.core_lock : asyncio.Lock = asyncio.Lock()

        cls.aio_done_queue : asyncio.Queue = asyncio.Queue()
        cls.live_aio_tasks : set[asyncio.Task] = set()

        cls.tasks_started : int = 0
        cls.tasks_awaited : int = 0
        cls.tasks_finished : int = 0
        cls.tasks_broken : int = 0
        cls.tasks_failed : int = 0
        cls.tasks_cancelled : int = 0
        cls.tasks_skipped : int = 0

    @classmethod
    async def acquire(cls, count):
        # A task that requires a lot of cores can block tasks behind it in the queue. This is
        # intended behavior.

        if not isinstance(count, int):
            pass

        if count > cls.max_jobs: # pragma: no cover
            raise ValueError(f"Tried to acquire {count} cores, which exceeds the max {cls.max_jobs}")
        async with cls.core_lock:
            acquired = 0
            try:
                while acquired < count:
                    await cls.core_sem.acquire()
                    acquired += 1
                return count
            except BaseException: # pragma: no cover
                cls.release(acquired)
                raise

    @classmethod
    def release(cls, count):
        for _ in range(count):
            cls.core_sem.release()

    @classmethod
    def create_aio_task(cls, task):
        assert Utils.in_event_loop()

        if task._aio_task is None:
            t = asyncio.create_task(task.task_top(), context=task._aio_context)
            t.hancho_task = task # type: ignore
            cls.live_aio_tasks.add(t)
            t.add_done_callback(lambda t: cls.aio_done_queue.put_nowait(t))
            task._aio_task = t
            Runner.tasks_started += 1
            task._task_id = Runner.tasks_started

        # Start all tasks referenced by the config so we don't deadlock while waiting for them.
        for v in task.input_tasks:
            v.enable_task()

    @classmethod
    async def run_tasks(cls, script):
        """Run all tasks until we run out."""

        # ------------------------------------
        # Create asyncio tasks for all enabled Hancho tasks.

        for task in script.yield_all_tasks():
            if task._enabled:
                Runner.create_aio_task(task)

        # ------------------------------------
        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.

        with Log.Level.VERBOSE, Log.Color.BLUE:
            Log.log("Running tasks...\n")

        while cls.live_aio_tasks and (Runner.tasks_broken + Runner.tasks_failed) <= Runner.max_errors:
            finished_aio_task = None

            try:
                finished_aio_task = cast(asyncio.Task, await cls.aio_done_queue.get())
                _ = finished_aio_task.result()
                Runner.tasks_finished += 1
            except asyncio.CancelledError:
                Runner.tasks_cancelled += 1
            except Task.CANCELLED:
                Runner.tasks_cancelled += 1
            except Task.BROKEN:
                Runner.tasks_broken += 1
            except Task.FAILED:
                Runner.tasks_failed += 1
            except Task.SKIPPED:
                finished_aio_task.hancho_task._complete = True #type:ignore
                Runner.tasks_skipped += 1
            except BaseException as ex:
                with Log.Level.DEBUG:
                    Log.log(f"Weird exception {type(ex)} >{ex}< at {time.perf_counter()}\n")
                    Log.log_exception(ex)
                Runner.tasks_failed += 1
            else:
                # If _none_ of the above exceptions fired, we mark the task as complete.
                finished_aio_task.hancho_task._complete = True #type:ignore
            finally:
                if finished_aio_task is not None:
                    cls.live_aio_tasks.discard(finished_aio_task)
                Runner.tasks_awaited += 1

        if Runner.tasks_broken + Runner.tasks_failed > Runner.max_errors:
            with Log.Level.ERROR:
                Log.log(f"Too many failures after {Runner.tasks_awaited}, cancelling tasks and stopping build\n")

            # Cancel all the asyncio.Tasks that haven't completed yet
            with Log.Level.VERBOSE:
                Log.log(f"Cancelling {len(cls.live_aio_tasks)} tasks\n")

            # This tasks_cancelled count may be off by one or two due to in-flight tasks not being
            # accounted for in live_aio_tasks, but it doesn't matter - we're about to bail out due
            # to failures or someone ctrl-c'ing the build, this is purely cosmetic.

            Runner.tasks_cancelled += len(cls.live_aio_tasks)
            for t in cls.live_aio_tasks:
                t.cancel()

            # and then wait on their cancellations to complete (it isn't instantaneous)
            await asyncio.gather(*cls.live_aio_tasks, return_exceptions=True)

        return 1 if Runner.tasks_failed or Runner.tasks_broken else 0

    @classmethod
    def run_tool(cls, script, tool : str): # pragma: no cover
        if tool == "clean":
            for task in script.yield_all_tasks():
                root = Path.relpath(task.config.build_root, os.getcwd())
                if Path.isdir(root):
                    Log.log(f"Wiping build_root {root}\n")
                    shutil.rmtree(root, ignore_errors=True)
            Log.log("Clean done\n")
            return 0
        else:
            raise AssertionError(f"Don't know how to run tool {tool}")

class HanchoProxy(types.ModuleType):

    def __init__(self):
        # When a HanchoProxy is created, it only exposes an 'init' method - so users can't forget
        # to initialize it.
        super().__init__("HanchoProxy")
        self.init     = self._init

    def _init(self, argv = None, *args, **kwargs):
        # And then when the user calls hancho.init(), we do the rest of the initialization.
        params = Hancho.parse_flags(argv or [], *args, **kwargs)

        self.params   = params
        self.Dict     = Dict
        self.Tool     = Tool
        self.Task     = Task
        self.Path     = Path

        self.log      = Log.log
        self.dump     = Dumper.dump

        self.flatten  = Utils.flatten
        self.run_cmd  = Utils.run_cmd
        self.weave    = Utils.weave

        self.abspath  = Path.abspath
        self.basename = Path.basename
        self.dirname  = Path.dirname
        self.join     = Path.join
        self.relpath  = Path.relpath
        self.resolve  = Path.resolve
        self.swapext  = Path.swapext

        self.fail     = Script.Fail
        self.abort    = Script.Abort
        self.earlyout = Script.EarlyOut

        self.load  = self._load
        self.repo  = self._repo
        self.build = self._build

        Hancho.init(params)

    def _load(self, path, *args, **kwargs) -> types.ModuleType:
        return Hancho.load_path(Hancho.cv_script.get(), path, False, *args, **kwargs).module

    def _repo(self, path, *args, **kwargs) -> types.ModuleType:
        return Hancho.load_path(Hancho.cv_script.get(), path, True, *args, **kwargs).module

    def _build(self) -> int:
        return Hancho.build(Hancho.cv_script.get())

class Hancho:
    # Just a container for global functions and stuff.

    proxy : HanchoProxy = HanchoProxy()
    #params : Dict = Dict()
    real_filenames : set # for catching multiple targets building the same output
    dedupe : dict

    cv_script : ContextProxy

    @classmethod
    def init(cls, params):

        cls.real_filenames = set()
        cls.dedupe = {}
        cls.onion = Onion(hancho = cls.proxy.__dict__, params = params)

        delims = params.pop("delims")
        Expander.ldelims = delims[::2]
        Expander.rdelims = delims[1::2]

        Log.reset(params)
        Runner.reset(params)
        Stats.reset()

        root_script = Script(
            params=Dict(params, script_path=__file__, script_cwd=os.getcwd()),
            code=None,
            is_repo=True,
        )
        cls.cv_script = ContextProxy(root_script)

    @classmethod
    def parse_flags(cls, argv, *args, **kwargs) -> Dict:

        desc = textwrap.dedent("""
        ================================================================================
                        Hancho is a simple, pleasant build system
        ================================================================================
        """)

        parser = argparse.ArgumentParser(
            description=desc,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
        verbosities = [v.lower() for v in Log.Level.__members__]

        bool_opt = argparse.BooleanOptionalAction

        # ------------------------------------
        # fmt: off

        depformat    = "gcc" if os.name == "posix" else "msvc"
        max_jobs     = os.cpu_count() or 1

        repo_root    = "{script_cwd}"
        build_root   = "{join(repo_root, 'build')}"
        comp_db_path = "{join(build_root, 'compile_commands.json')}"
        stat_db_path = "{join(build_root, 'hancho.json')}"

        script_path  = os.path.abspath("build.hancho")
        script_cwd   = "{dirname(script_path)}"

        build_dir    = "{abspath(join(build_root, build_tag, relpath(script_cwd, repo_root)))}"
        task_cwd     = "{repo_root}"

        # global
        parser.add_argument('-o', "--opt_file",     default = "",              metavar = "(path)",    type=str,       help="File containing a Python literal that will be used as additional options")
        parser.add_argument(      "--run_tool",     default = "",              metavar = "(tool)",    type=str.strip, help="Run a subtool.")
        parser.add_argument(      "--delims",       default = "{}«»",          metavar = "(delims)",  type=str.strip, help="The characters to use as macro delimiters.")
        parser.add_argument(      "--depformat",    default = depformat,       metavar = "(format)",  type=str.strip, help="Dependency file format (gcc or msvc)")

        # runner
        parser.add_argument(      "--max_errors",   default = 0,               metavar = "(count)",   type=int,       help="The maximum number of task errors we tolerate before abandoning the build")
        parser.add_argument('-j', "--max_jobs",     default = max_jobs,        metavar = "(count)",   type=int,       help="Run a maximum of N jobs in parallel.")

        # build
        parser.add_argument(      "--build_tag",    default = "",              metavar = "(name)",    type=str.strip, help="Tagged builds will have separate subdirectories under the build directory.")
        parser.add_argument('-t', "--build_target", default = "",              metavar = "(name)",    type=str.strip, help="A regex that selects a subset of targets to build.")
        parser.add_argument(      "--build_force",  default = False,           action = bool_opt,                     help="Rebuild targets even if they're clean.")
        parser.add_argument(      "--build_all",    default = False,           action = bool_opt,                     help="Build every task in every repo.")
        parser.add_argument(      "--build_dry",    default = False,           action = bool_opt,                     help="Dry run - Do everything except actually run commands.")
        parser.add_argument(      "--build_strict", default = True,            action = bool_opt,                     help="Strict mode, slightly more error checking to catch footguns.")

        # repo
        parser.add_argument('-r', "--repo_root",    default = repo_root,       metavar = "(path)",    type=str.strip, help="The location of the repo we're building.")
        parser.add_argument(      "--build_root",   default = build_root,      metavar = "(path)",    type=str.strip, help="Directory to put build artifacts in.")
        parser.add_argument(      "--comp_db_path", default = comp_db_path,    metavar = "(path)",    type=str.strip, help="Where to put the compilation database")
        parser.add_argument(      "--stat_db_path", default = stat_db_path,    metavar = "(path)",    type=str.strip, help="Where to put the stat database")

        # script
        parser.add_argument(      "--script_path",  default = script_path,     metavar = "(path)",    type=str.strip, help="Path to the .hancho file that starts the build.")
        parser.add_argument(      "--script_cwd",   default = script_cwd,      metavar = "(path)",    type=str.strip, help="If this is an out-of-tree build, change to this directory before running the top build script.")
        parser.add_argument(      "--task_cwd",     default = task_cwd,        metavar = "(path)",    type=str.strip, help="Directory to run commands in.")
        parser.add_argument(      "--build_dir",    default = build_dir,       metavar = "(path)",    type=str.strip, help="Per-task build artifact directory.")

        # log
        parser.add_argument(      "--log_level",    default = Log.Level.NORMAL, choices = verbosities,                 help="Manually select verbosity level. 'quiet' = none, 'trace' = maximal spam")
        parser.add_argument('-Q', "--log_quiet",    default = False,           action = bool_opt,                     help="(same as --log_level=quiet)")
        parser.add_argument('-V', "--log_verbose",  default = False,           action = bool_opt,                     help="(same as --log_level=verbose)")
        parser.add_argument('-D', "--log_debug",    default = False,           action = bool_opt,                     help="(same as --log_level=debug)")
        parser.add_argument('-T', "--log_trace",    default = False,           action = bool_opt,                     help="(same as --log_level=trace)")
        parser.add_argument('-w', "--log_wrap",     default = False,           action = bool_opt,                     help="Wrap lines around the console instead of clipping them")
        parser.add_argument('-c', "--log_color",    default = True,            action = bool_opt,                     help="Use color in the log for better readability")
        parser.add_argument(      "--log_time",     default = True,            action = bool_opt,                     help="Timestamp each log line")
        # fmt: on

        (argv_flags, unrecognized) = parser.parse_known_args(argv if argv else [])
        argv_flags = vars(argv_flags)
        argv_flags = {k:v for k, v in argv_flags.items() if v is not None}

        # ------------------------------------
        # Load flags from opt_file if present

        opt_file = argv_flags.pop("opt_file")
        if opt_file:
            #with Log.Color.GREEN:
            #    Log.log(f"Loading options file {opt_file!r}\n")
            if os.path.exists(opt_file):
                with open(opt_file) as f:
                    try:
                        opts = json.load(f)
                        argv_flags.update(opts)
                    except Exception as _:
                        #with Log.Color.RED:
                        #    Log.log(f"Opt file {opt_file!r} invalid!\n")
                        pass
            else:
                #with Log.Color.RED:
                #    Log.log(f"Opt file {opt_file!r} not found!\n")
                pass

        # ------------------------------------
        # Unrecognized command line parameters also become config fields if they are flag-like.
        # Naked flags become {'name':True}, number types become numbers, 'true' and 'false'
        # become bools (regardless of capitalization), everything else becomes a string.

        mystery_flags = {}
        for chunk in unrecognized:
            if match := re.match(r"--([^=]+)=(.+)", chunk):
                key = match.group(1)
                val = match.group(2)

                if val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                else:
                    with suppress(NameError, ValueError, SyntaxError):
                        val = ast.literal_eval(val)

                mystery_flags[key] = val

        flags = Dict()
        flags.hancho_dir = os.path.dirname(__file__)
        flags.merge(argv_flags)

        flags.merge(mystery_flags)
        flags.merge(*args, kwargs)

        return flags

    @classmethod
    def load_path(cls, parent_script : Script, script_path : str, is_repo : bool, *args, **kwargs) -> Script:
        script_path = parent_script.onion.expand(script_path)
        script_path = Path.resolve(script_path)
        with open(script_path, encoding="utf-8") as file:
            source = file.read()
            return cls.load_source(parent_script, script_path, source, is_repo, *args, **kwargs)

    @classmethod
    def load_source(cls, parent_script : Script, script_path : str, source : str, is_repo : bool, *args, **kwargs) -> Script:
        code = compile(source, script_path, "exec", dont_inherit=True)
        return cls.load_code(parent_script, script_path, code, is_repo, *args, **kwargs)

    @classmethod
    def load_code(cls, parent_script : Script, script_path : str, code : types.CodeType, is_repo : bool, *args, **kwargs) -> Script:

        # --------------------------------
        # Merge the parent script's params with whatever was passed in to hancho.load()

        params = Dict(
            parent_script.params,
            Dict(
                script_path = script_path,
                script_cwd  = Path.dirname(script_path)
            ),
            *args,
            **kwargs
        )

        return cls.load_code2(script_path, code, params, is_repo)

    @classmethod
    def load_code2(cls, script_path : str, code : types.CodeType, params : Dict, is_repo : bool) -> Script:

        # --------------------------------
        # Dedupe the load - only scripts with identical real paths and identical configs are
        # deduped. This relies on __repr__ and the fields read by Dumper.dump being stable during a
        # build, which they should be in practice.

        dedupe_key = Hancho.dict_to_key(params)
        deduped_script = Hancho.dedupe.get(dedupe_key)
        if deduped_script:
            with Log.Level.VERBOSE, Log.Color.SKY:
                Log.log(f"Deduped load of {script_path} - {deduped_script}\n")
            return deduped_script

        # --------------------------------
        # Not deduped, create a new script.

        new_script = Script(params = params, code = code, is_repo = is_repo)
        Hancho.dedupe[dedupe_key] = new_script
        new_script.exec()

        return new_script

    @classmethod
    def main(cls) -> int:

        with Log.Level.VERBOSE, Log.Color.LIME:
            Log.log(f"Command line : {" ".join(sys.argv)}\n")
        if Log.log_trace:
            Log.log("Trace mode on\n")
        if Log.log_level_out >= Log.Level.DEBUG:
            Log.log("Debug mode on\n")
        if Log.log_level_out >= Log.Level.VERBOSE:
            Log.log("Verbose mode on\n")

        # ------------------------------------
        # Load and exec top script

        time_a1 = time.perf_counter()

        Log.indent(Log.Color.ORANGE)
        parent_script = Hancho.cv_script.get()
        script_path = Hancho.onion.script_path
        script = Hancho.load_path(parent_script, script_path, is_repo = True)
        Log.dedent()

        time_b1 = time.perf_counter()
        with Log.Level.VERBOSE, Log.Color.BLUE:
            Log.log(f"Loading scripts took {time_b1 - time_a1:8.6f} seconds\n")

        # ------------------------------------
        # If we're running a tool, run it and we're done.

        if script.onion.run_tool:
            time_a2 = time.perf_counter()
            result = Runner.run_tool(script, script.onion.run_tool)
            time_b2 = time.perf_counter()

            with Log.Level.VERBOSE, Log.Color.GREEN:
                Log.log(f"Tool took {time_b2 - time_a2:8.6f} seconds\n")
            return result

        # ------------------------------------
        # Start the build

        time_a3 = time.perf_counter()
        result = Hancho.build(script)
        time_b3 = time.perf_counter()
        with Log.Level.VERBOSE, Log.Color.GREEN:
            Log.log(f"Build took {time_b3 - time_a3:8.6f} seconds\n")

        # ------------------------------------
        # Done

        Hancho.banner_end(script)
        return result

    @classmethod
    def build(cls, script) -> int:

        # ------------------------------------
        # This must happen _after_ all repos are loaded (so that if they change repo_root we don't
        # get the old path), but _before_ we build any tasks.

        for repo_script in script.yield_repos():
            if os.path.isfile(repo_script.config.stat_db_path):
                with open(repo_script.config.stat_db_path) as contents:
                    repo_script.stats.old_stat_db = Dict(json.load(contents))
            else:
                repo_script.stats.old_stat_db = Dict()

        # ------------------------------------
        # Select the set of tasks to run.

        if script.config.build_target:
            # Enable all tasks whose name matches the target regex
            # NOTE - We match task.params.name, _not_ the expanded task.config.name.
            # This is because the task _has not initialized yet_, so we have no config.name.
            target_regex = re.compile(script.config.build_target)

            for task in script.yield_all_tasks():
                if target_regex.search(task.params.name):
                    task.enable_task()

        elif script.config.build_all:
            for task in script.yield_all_tasks():
                task.enable_task()

        else:
            # Enable all tasks in the top repo
            for task in script.yield_tasks():
                task.enable_task()

        # ------------------------------------
        # Run the tasks.

        result = asyncio.run(Runner.run_tasks(script))

        # ------------------------------------
        # Update stat DBs.

        for repo_script in script.yield_repos():
            repo_script.stats.save_stat_db(repo_script)

        return result

    @classmethod
    def banner_end(cls, script):
        task_count = len(list(script.yield_all_tasks()))

        with Log.Level.VERBOSE:
            Log.log(f"Tasks created:    {task_count}\n")

            Log.log(f"Tasks started:    {Runner.tasks_started}\n")
            Log.log(f"Tasks awaited:    {Runner.tasks_awaited}\n")
            Log.log(f"Tasks finished:   {Runner.tasks_finished}\n")
            Log.log(f"Tasks broken:     {Runner.tasks_broken}\n")
            Log.log(f"Tasks failed:     {Runner.tasks_failed}\n")
            Log.log(f"Tasks cancelled:  {Runner.tasks_cancelled}\n")
            Log.log(f"Tasks skipped:    {Runner.tasks_skipped}\n")

            Log.log(f"Mtime calls:      {Stats.stat_calls}\n")
            Log.log(f"Hash calls:       {Stats.hash_calls}\n")
            Log.log(f"Hash bytes:       {Stats.hash_bytes}\n")
            Log.log(f"Hash time:        {Stats.hash_time:8.6f}\n")

        if Runner.tasks_failed or Runner.tasks_broken:
            with Log.Level.ERROR, Log.Color.RED:
                Log.log("BUILD FAILED\n")
        elif Runner.tasks_finished:
            with Log.Color.GREEN:
                Log.log("BUILD PASSED\n")
        else:
            with Log.Color.BLUE:
                Log.log("BUILD CLEAN\n")

        with Log.Level.DEBUG, Log.Color.BLUE:
            for repo in script.yield_repos():
                Log.log(f"Stats for {repo.config.repo_root}\n")
                Log.indent(Log.Color.BLUE)
                for k, v in repo.stats.reasons.items():
                    Log.log(f"Rebuild reasons {k:13} = {v}\n")
                Log.dedent()

    @classmethod
    def path_to_code(cls, source_path) -> types.CodeType:
        source_path = Path.resolve(source_path)
        with open(source_path, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, source_path, "exec", dont_inherit=True)
            return code

    @classmethod
    def dict_to_key(cls, params) -> str:
        dedupe_key = Dumper.dump(params)
        dedupe_key = Dumper.match_pointer.sub(r"<\1 \2 at 0x...>", dedupe_key)
        return dedupe_key

def _start():

    sys.modules["hancho"] = Hancho.proxy

    if __name__ == "__main__":

        # Top-level exception handler just so we can print a big red "SOMETHING BROKE" message if
        # we failed to catch an exception during load/build. The 'except' clause should catch
        # Exception and not BaseException so ctrl-c doesn't get misinterpreted as a Hancho bug.

        try:
            Hancho.proxy.init(argv = sys.argv[1:])
            result = Hancho.main()
            sys.exit(result)

        except Exception:
            print(Log.hex_to_ansi(0xFF3030), end="")
            print("Hancho hit an unhandled exception:")
            traceback.print_exc()
            print("\x1B[0m", end="")
            sys.exit(1)

        finally:
            # Don't leave the last line of the log sitting in line_buffer!
            Log.flush()

_start()
