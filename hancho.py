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
from enum import Enum
from functools import wraps
from inspect import isawaitable
from typing import Any, cast

# endregion
# --------------------------------------------------------------------------------------------------

sys.modules["hancho"] = sys.modules[__name__]

cv_script : contextvars.ContextVar[Any] = contextvars.ContextVar("script", default = None)

sentinel = "<sentinel>"

# We treat the Hancho module itself as a repo, so that we have a place to put everything added to
# the build by tests or other code that doesn't load a .hancho script.
hancho : Any = sys.modules["hancho"]

options : Dict

config : Dict

# And when we _do_ have a root .hancho script, its components go here.
# I suppose this could be a dict but would require reshuffling.
#root : Any = object()

# --------------------------------------------------------------------------------------------------
# region Dict

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
            if not isinstance(arg, dict):
                raise ValueError(f"Argument #{i} was not a dict - {arg}")

        Dict.merge(self, *args, kwargs)

    @classmethod
    def merge(cls, dest, *args, **kwargs):
        for rhs in (*args, kwargs):
            Dict.generic_merge(
                dest, dest, rhs,
                merge_dicts=True, merge_lists=True,
                keep_a=True, keep_b=True)

    # Merges self and args into a new dict, keeping only keys that were already in self.
    # For example, if you have a config that contains "out_bin" and you merge it with "compile_cpp",
    # Hancho will complain that "out_bin" is missing - it sees both "out_obj" and "out_bin" and
    # assumes the task produces both. If you do compile_cpp.fill(...), "out_bin" does not get added
    # to compile_cpp.

    def fill2(self, *args, **kwargs):
        dest = Dict(self)
        Dict.fill(dest, *args, **kwargs)
        return dest

    @classmethod
    def fill(cls, dest, *args, **kwargs):
        for rhs in (*args, kwargs):
            Dict.generic_merge(
                dest, dest, rhs,
                merge_dicts=True, merge_lists=True,
                keep_a=True, keep_b=False)
            pass

    @classmethod
    def generic_merge(cls, dst, lhs, rhs, merge_dicts, merge_lists, keep_a, keep_b):
        try:
            keys = list(lhs) + [r for r in rhs if r not in lhs]
        except TypeError as err:
            print("-----------------------------------")
            print(lhs)
            print(rhs)
            print("-----------------------------------")
            sys.exit(0)
            raise
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

    # ----------------------------------------

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
        return Utils.dump_to_str(key = None, val = self)

    # ----------------------------------------

    def __getitem__(self, key : str):
        #return self._get_by_path(key)
        return dict.__getitem__(self, key)

    def __setitem__(self, key : str, val : Any):
        #return self.set_by_path(key, val)
        dict.__setitem__(self, key, val)

    def expand(self, template, onion = None):
        if onion is None:
            script = cv_script.get()
            onion = Onion()
            onion._layers['hancho_module'] = hancho.__dict__
            onion._layers['aliases'] = Aliases.__dict__
            if script is not None:
                onion._layers['script_module'] = script.module.__dict__
                onion._layers['script_options'] = script.options
            onion._layers['self'] = self
        return Expander.expand(template, onion)

# Tool is just an alias for Dict to make build scripts more readable.
class Tool(Dict):
    pass

# endregion
# --------------------------------------------------------------------------------------------------
#region Onion

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

    Onion fixes this by looking up the key in all 'layers' and returns a value only if there was a
    single match. If there are multiple Mapping-type matches, they form a new Onion.
    """

    __slots__ = ("_layers",)

    def __init__(self, *args, **kwargs):
        #print(args)
        #print(kwargs)

        self._layers = {}
        for val in args:
            if val is None:
                continue
            if isinstance(val, Onion):
                self._layers.update(val._layers)
            else:
                raise TypeError(f"You can only merge onions, not this: {val}")

        for key, val in kwargs.items():
            if val is None:
                continue
            elif isinstance(val, Onion):
                for key2, val2 in val._layers.items():
                    self._layers[key + "." + key2] = val2
            elif isinstance(val, abc.Mapping):
                self._layers[key] = val
            else:
                raise TypeError(f"Onion layers must be mappings, not this: {val}")

    def __getattr__(self, key : str):
        try:
            return self._get(key)
        except KeyError as err:
            raise AttributeError from err

    def __getitem__(self, key):
        return self._get(key)

    # FIXME - Do we really need these iter/len impls? Do they make onions more debuggable?
    def __iter__(self):
        seen = set()
        for layer in reversed(self._layers.items()):
            for key, _ in layer[1].items():
                if key not in seen:
                    seen.add(key)
                    yield key

    def __len__(self):
        return len(set().union(*self._layers.values()))

    def __repr__(self):
        #return repr(self._layers) #Utils.dump_to_str("Onion", self._layers)
        return Utils.dump_to_str(key = None, val = self)

    def __contains__(self, key):
        return any(key in layer for layer in self._layers.values())

    def _get(self, key, default = sentinel) -> Any:
        # Pull out non-None layer[key]s for all layers containing the key.
        values = [
            (layer_name + "." + key, layer[key])
            for layer_name, layer in self._layers.items()
            if isinstance(layer, abc.Mapping) and key in layer
        ]

        # No values? Bad key.
        if not values:
            if default is sentinel:
                raise KeyError(key)
            else:
                return default

        # Is the last value _not_ a mapping? Then it's our result - attempt to expand it in this
        # onion's context before we give it back.
        if not isinstance(values[-1][1], abc.Mapping):
            result = values[-1][1]
            return Expander.expand(result, onion = self)

        # The last value _is_ a mapping. Is it the only value? Then it's our result.
        if len(values) == 1:
            return Onion(layer = values[-1][1])

        # Otherwise we split off the mappings at the _end_ of the list. This is easier to do if we
        # reverse the list first.
        new_layers : list[tuple[str, abc.Mapping]] = []
        for layer_name, layer in reversed(values):
            if not isinstance(layer, abc.Mapping):
                break
            new_layers.append((layer_name, layer))

        # If there was only one mapping, it's our result.
        if len(new_layers) == 1:
            return Onion(layer = new_layers[0][1])

        # Otherwise we make a new onion out of the new (un-reversed) mappings.
        return Onion(**dict(reversed(new_layers)))

    def expand(self, template):
        return Expander.expand(template, self)

#endregion
# --------------------------------------------------------------------------------------------------
# region Expander
# Hancho's text expansion system.
#
# WARNING - Again, Hancho is NOT A SANDBOX. Expander is the part that evaluates the arbitrary
# Python code that then formats your drive and spams your grandmother.
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
# Also - TEFINAE - Text Expansion Failure Is Not An Error. Dicts can contain macros that are not
# expandable by that dict. This allows nested dicts to contain templates that can only be expanded
# an outer dict, and things will still Just Work.

class Expander:
    """
    This class is used to fetch and expand text templates from a dict during text expansion.
    It allows for both dictionary-like access (using `expander[key]`) and attribute-like access
    (using `expander.key`), making it versatile for accessing template variables and methods.
    """

    @classmethod
    def reset(cls, config):
        pass

    # ----------------------------------------------------------------------------------------------
    # Hancho's template expansions can cause infinite loops, so we need some simple complexity
    # tracking here. This is _not_ some precise thing, it's just a tripwire to keep us from blowing
    # up the whole Python stack.
    # If you do weird things like load scripts from inside macros and you hit MAX_STEPS, that's a
    # you problem.
    #
    # The evals and depth limits are arbitrary, but should be plenty - Hancho's test suites
    # currently pass with MAX_DEPTH = 3 and MAX_EVALS = 12.

    cv_depth = contextvars.ContextVar("depth", default = 0)
    cv_evals = contextvars.ContextVar("evals", default = 0)
    MAX_DEPTH = 30
    MAX_EVALS = 300

    sentinel = sentinel

    # ----------------------------------------

    @classmethod
    def expand(cls, variant : Any, onion : Onion):
        """
        The outer expand function handles setting/resetting the depth/evals-check vars and repeats
        expansion until we reach a non-string or the string stops changing.
        """

        if variant == sentinel:
            raise AssertionError("Tried to expand a sentinel value")

        # Recurse early if we're trying to expand a list of strings.
        # This ensures that every template gets its own independent depth and evals check.
        if isinstance(variant, list):
            result = []
            for v in variant:
                # Remember how much budget was spent.
                saved = Expander.cv_evals.get()
                # Expand the list element.
                result.append(Expander.expand(v, onion))
                # Restore the budget so the next string in the list gets it.
                Expander.cv_evals.set(saved)
            return result

        # Bail out early if our variant isn't a string (a common case if we're expanding {debug} or
        # something) or if it's a string with no macros in it.
        if not (isinstance(variant, str) and '{' in variant):
            return variant
        template = cast(str, variant)

        # Bail out if we've gone through too many levels of recursion.
        depth = Expander.cv_depth.get()
        if depth >= Expander.MAX_DEPTH:
            raise RecursionError(f"Expansion failed to terminate after {depth} recursions: {template!r}")
        Expander.cv_depth.set(depth + 1)

        # OK, we have a string that could be a template. Keep expanding it until it stops changing
        # or it's not a template.

        Log.indent()
        try:
            old_template = None
            while old_template != template and isinstance(template, str) and '{' in template:
                old_template = template
                #Log.log(f"Expand {template!r} with merged_onion 0x{hex(id(merged_onion))[-4:]} {len(merged_onion._layers)}\n")

                template = Expander._expand_pass(template, onion)
                #Log.log(f" = {template}\n")

        finally:
            Log.dedent()
            # And then reset the depth/evals check vars when we're done.
            if depth == 0:
                Expander.cv_evals.set(0)
            Expander.cv_depth.set(depth)

        return template

    # ----------------------------------------
    # IMPORTANT IMPORTANT IMPORTANT
    # If you can't eval a macro, you return it unchanged.
    # TEFINAE : Template Expansion Failure Is Not An Error. Same idea as SFINAE in C++ - we don't
    # fail on expansion failure so we can retry somewhere/somewhen else.

    @classmethod
    def _expand_pass(cls, template : str, onion : Onion):
        """The inner expand function does one split-expand-rejoin pass on the template string."""

        # Split the string into literal and macro blocks.
        blocks = []
        Expander._split_template(template, blocks)

        # Expand all macro blocks.
        for i, block in enumerate(blocks):

            # Skip literal blocks.
            if len(block) < 2 or block[0] != "{" or block[-1] != "}":
                continue

            # Bail out if we've taken too many expansion steps already.
            if (steps := Expander.cv_evals.get()) >= Expander.MAX_EVALS:
                raise RecursionError(f"Expansion failed to terminate after {steps} evals: '{template!r}'")
            Expander.cv_evals.set(steps + 1)

            # Otherwise try and expand the macro. Failing is OK.
            # This should be the _only_ try/except block in the expansion code.

            try:
                blocks[i] = eval(block[1:-1], {}, onion)

            # Note that we do _not_ suppress any BaseExceptions - they _must_ be propagated up to
            # callers. As of Python 3.11, this includes asyncio.CancelledError.
            except RecursionError:
                raise
            except Exception as _:
                # Do NOT print stuff here or it'll spam like mad
                #traceback.print_exc()
                pass

        # If there was only one block in the list, unwrap it.
        if len(blocks) == 1:
            return blocks[0]

        # Otherwise we stringify everything and join the blocks back together.
        return "".join(Utils.stringify(b) for b in blocks)

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def _split_template(cls, text : str, out : list[str]):
        """
        Extracts all innermost single-brace-delimited spans from a block of text and produces a
        list of string literals and macros. Escaped braces don't count as delimiters.
        """
        assert isinstance(text, str)

        cursor = 0
        lbrace = -1
        escaped = False
        chunk_count = 0
        for i, c in enumerate(text):
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '{':
                lbrace = i
            elif c == '}' and lbrace >= 0:
                if cursor < lbrace:
                    out.append(text[cursor:lbrace])
                    chunk_count += 1
                out.append(text[lbrace:i+1])
                chunk_count += 1
                cursor = i + 1
                lbrace = -1

        if cursor < len(text):
            out.append(text[cursor:])
            chunk_count += 1
        return chunk_count

# endregion
# --------------------------------------------------------------------------------------------------
# region Utils

class Utils:

    @classmethod
    def reset(cls, config):
        cls.stat_calls = 0
        cls.hash_calls = 0
        cls.hash_bytes = 0
        cls.hash_time  = 0

    # ----------------------------------------------------------------------------------------------

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
        cls.hash_calls += 1
        time_a = time.perf_counter()
        with open(abs_path, "rb") as f:
            blob = f.read()
            cls.hash_bytes += len(blob)
        result = cls.hash(blob, h)
        time_b = time.perf_counter()
        cls.hash_time += time_b - time_a
        return result

    # ----------------------------------------------------------------------------------------------

    # These types don't get dumped because they're not really dumpable.
    opaque_types = types.MappingProxyType({
        types.BuiltinFunctionType : "<builtin>",
        types.ModuleType          : "<module>",
        types.GeneratorType       : "<generator>",
    })

    # These types don't need a type annotation when dumped.
    base_types = (str, bool, int, float, list, tuple, set, dict, bytes, bytearray, range, type(None),
                  *opaque_types.keys())

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def _dump_prefix(cls, key, val, print_id, color_code):
        prefix = ""
        if key is not None:
            prefix += str(key)
        if type(val) not in Utils.base_types:
            if key:
                prefix += ":"
            prefix += type(val).__name__
        if prefix:
            prefix += " = "
        return prefix

    @classmethod
    def _dump_suffix(cls, val, print_id):
        if isinstance(val, (str, bool, int, float)) or not print_id:
            return ""
        suffix = " @ 0x" + Utils.hex_id(val).upper()[-4:]
        return suffix

    @classmethod
    def _dump_scalar(cls, val, color_code):
        # Non-containers are always emitted on one line. If they overflow, they overflow.
        if isinstance(val, Task):
            val = f"<Task {val.config.name}>"
        elif isinstance(val, contextvars.Context):
            val = "<Context>"
        elif isinstance(val, types.ModuleType):
            val = f"<Module {val.__name__}>"
        elif isinstance(val, types.FunctionType):
            val = f"<Function {val.__name__}>"
        elif isinstance(val, argparse.Namespace):
            val = val.__dict__

        if type(val) in Utils.opaque_types:
            return Utils.opaque_types[type(val)] # type: ignore
        elif type(val).__repr__ is object.__repr__:
            # Objects that don't have a custom repr (and a few built-in types) just get printed
            # as '<object>'
            return "<object>"
        else:
            result = repr(val)
            return result

    @classmethod
    def _unpack_container(cls, val):
        if isinstance(val, tuple):
            items = [(None, v) for v in val]
            return '(', items, ",)" if len(items) == 1 else ')'
        elif isinstance(val, (dict, types.MappingProxyType)):
            return '{', val.items(), '}'
            #return '{', sorted(val.items()), '}'
        elif isinstance(val, (list, tuple, set)):
            items = [(None, v) for v in val]
            return '[', items, ']'
        elif isinstance(val, Onion):
            items = [(f"layer {k}", v) for k, v in reversed(val._layers.items())]
            return '[', items, ']'
        else:
            raise AssertionError(f"Don't know what to do with {type(val)}") # pragma: no cover

    @classmethod
    def _dump_container_to_flat_str(cls, val, print_id, color_code, max_length, tab, memo):
        ld, items, rd = cls._unpack_container(val)
        separator = ", "
        num_separators = len(items) - 1 if len(items) else 0
        length = len(ld) + (len(separator) * num_separators) + len(rd)

        chunks = []
        for k, v in items:
            prefix = cls._dump_prefix(k, v, print_id, color_code)
            suffix = cls._dump_suffix(v, print_id)

            chunk = prefix + cls._dump_variant_to_flat_str(v, print_id, color_code, max_length - length - len(prefix), tab, memo) + suffix

            length += len(chunk)
            if length > max_length:
                raise ValueError()
            chunks.append(chunk)

        return ld + ", ".join(chunks) + rd

    @classmethod
    def _dump_container_to_str(cls, val, indent, print_id, color_code, max_length, tab, memo):
        ld, items, rd = cls._unpack_container(val)
        lines = [
            cls.dump_to_str(k, v, indent + 1, print_id, color_code, max_length - 1, tab)
            for k, v in items
        ]
        return ld + '\n' + ',\n'.join(lines) + '\n' + (tab * indent) + rd

    @classmethod
    def _dump_variant_to_flat_str(cls, val, print_id, color_code, max_length, tab, memo):
        if isinstance(val, (dict, list, tuple, set)):
            return cls._dump_container_to_flat_str(val, print_id, color_code, max_length, tab, memo)
        elif isinstance(val, Script):
            return cls._dump_container_to_flat_str(val.__dict__, print_id, color_code, max_length, tab, memo)
        else:
            return cls._dump_scalar(val, color_code)

    @classmethod
    def _dump_variant_to_str(cls, key, val, indent, print_id, color_code, max_length, tab, memo):
        prefix = (tab * indent) + cls._dump_prefix(key, val, print_id, color_code)
        suffix = cls._dump_suffix(val, print_id)

        # FIXME duplication

        #if isinstance(val, dict):
        #    return prefix + "<dict>"

        #if key == "__builtins__":
        #    return prefix + "<builtins>"

        if isinstance(val, (dict, list, tuple, set, Onion)):
            try:
                return prefix + cls._dump_container_to_flat_str(val, print_id, color_code, max_length - len(prefix), tab, memo) + suffix
            except ValueError:
                return prefix + cls._dump_container_to_str(val, indent, print_id, color_code, max_length, tab, memo) + suffix
        elif isinstance(val, Script):
            if indent > 1:
                return prefix + f"'{val.options.script_path}'"
            try:
                return prefix + cls._dump_container_to_flat_str(val.__dict__, print_id, color_code, max_length - len(prefix), tab, memo) + suffix
            except ValueError:
                return prefix + cls._dump_container_to_str(val.__dict__, indent, print_id, color_code, max_length, tab, memo) + suffix
        else:
            return prefix + cls._dump_scalar(val, color_code) + suffix

    @classmethod
    def dump_to_str(cls, key, val, indent = 0, print_id = False, color_code = False, max_length = 80, tab = "    "):
        """
        Hancho's pretty-printer for various types. Note that this is also used for script deduping:
        if you load "my/app/tools/stuff.hancho" multiple times but the configurations you gave it
        were identical, you should get one copy of the "stuff" script instead of two.

        As long as you're not doing something bizarre with configs or changing the dumper in the
        middle of a build, the resulting strings should be stable enough to use for deduping.
        """

#        if key == "__builtins__":
#            prefix = (tab * indent) + cls._dump_prefix(key, val, print_id, color_code)
#            return prefix + "<builtins>"

#        if val is hancho.__dict__:
#            prefix = (tab * indent) + cls._dump_prefix(key, val, print_id, color_code)
#            return prefix + "<hancho.__dict__>"

#        if val is cv_script.get().module.__dict__:
#            prefix = (tab * indent) + cls._dump_prefix(key, val, print_id, color_code)
#            return prefix + "<script.__dict__>"

        #if key == "__builtins__":
        #    return "<builtins>"
        #if val.__class__ is dict:
        #    prefix = (tab * indent) + cls._dump_prefix(key, val, print_id, color_code)
        #    return prefix + "<dict>"


        # Unwrap tasks only if they're at the bottom level of indentation.
        if isinstance(val, Task) and indent == 0:
            val = val.__dict__

        return cls._dump_variant_to_str(key, val, indent, print_id, color_code, max_length, tab, memo = {})

    # ----------------------------------------------------------------------------------------------

    @staticmethod
    def stringify(variant) -> str:
        """Converts any type into a template-compatible string."""
        result = " ".join(str(v) for v in Utils.yield_values(variant) if v is not None)
        if Expander.sentinel in result:
            raise AssertionError("Tried to stringify a sentinel value")
        return result

    @staticmethod
    def in_event_loop() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    @staticmethod
    def is_template(text) -> bool:
        # inefficient way to check for templates, but it's reliable
        blocks = []
        Expander._split_template(text, blocks)
        return len(blocks) > 1 or (len(blocks) == 1 and blocks[0][0] == "{")

    @staticmethod
    def weave(lhs, rhs, *args) -> list[str]:
        """
        This function does a 'cross join' in the database sense, every line in lhs will be joined
        to every line in rhs (and this will be repeated with *args if present). This is useful for
        adding prefixes / suffixes to a bunch of strings, or generating all possible combinations
        of two sets of options, et cetera.
        """

        lhs2 = Utils.flatten(lhs)
        rhs2 = Utils.weave(rhs, *args) if len(args) > 0 else Utils.flatten(rhs)
        return [lh + rh for lh in lhs2 for rh in rhs2]

    @staticmethod
    def obj_to_float(obj) -> float:
        """
        Generates a 'random' float in the range [0.0,1.0) by hashing the object's ID.
        """
        temp = id(obj)
        temp *= 0x4F9B2A1D # doesn't matter what this constant is as long as it's odd.
        temp ^= temp >> 17
        temp *= 0x4F9B2A1D
        temp ^= temp >> 17

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
        return f"0x{id(obj):016x}"

    # ----------------------------------------------------------------------------------------------

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
    def load_json(cls, filename : str) -> dict:
        if os.path.isfile(filename):
            with open(filename) as contents:
                return json.load(contents)
        else:
            return {} # pragma: no cover

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

        with LogLevel.DEBUG:
            Log.log(f"Depfile {filename} contained {len(deplines)} deplines.\n")

        return deplines

# endregion
# --------------------------------------------------------------------------------------------------
# region Log

class LogLevel(int, Enum):
    FATAL    = 0  # Fatal always beats quiet
    QUIET    = 10
    CRITICAL = 20 # Should critical/error beat quiet?
    ERROR    = 30
    WARNING  = 40
    NORMAL   = 50
    VERBOSE  = 60
    DEBUG    = 70
    TRACE    = 80

    def __enter__(self):
        self.old_log_level = Log.log_level_in
        Log.log_level_in = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        Log.log_level_in = self.old_log_level
        return False

# --------------------------------------------------------------------------------------------------

class Colors(int, Enum):
    """12 half-saturated, 80% value colors evenly spaced around the HSV wheel"""

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

# --------------------------------------------------------------------------------------------------

class Log:

#    options : Dict = Dict(
#        wrap   = False,
#        color  = True,
#        time   = True,
#    )

    con_w         = 80
    time_origin   = time.perf_counter()
    indent_stack  = []
    current_color = -1
    line_buffer   = ""
    match_escapes = re.compile(r"(\x1B.*?m)")
    log_level_in  = LogLevel.NORMAL
    log_level_out = LogLevel.NORMAL # log level we want to appear in the log

    @classmethod
    def reset(cls, options : Dict):
        cls.options = options

        cls.con_w         = shutil.get_terminal_size().columns
        cls.time_origin   = time.perf_counter()
        cls.indent_stack  = []
        cls.current_color = -1
        cls.line_buffer   = ""
        cls.match_escapes = re.compile(r"(\x1B.*?m)")

        if options.log_level is not None:
            if isinstance(options.log_level, str):
                options.log_level = LogLevel[options.log_level.upper()]
            elif isinstance(options.log_level, int):
                options.log_level = LogLevel(options.log_level)
            else:
                raise ValueError(f"Got an unknown log_level '{type(options.log_level)} = {options.log_level}'")

        # The individual -T/-D/-V/-Q flags override --log_level, with the 'loudest' flag winning.

        if options.log_trace:
            options.log_level = LogLevel.TRACE
        elif options.log_debug:
            options.log_level = LogLevel.DEBUG
        elif options.log_verbose:
            options.log_level = LogLevel.VERBOSE
        elif options.log_quiet:
            options.log_level = LogLevel.QUIET

        cls.log_level_in  = options.log_level
        cls.log_level_out = options.log_level

    # ----------------------------------------------------------------------------------------------

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
        ansi = cls.hex_to_ansi(color) if cls.options.log_color else ""
        cls.indent_stack.append(ansi + "│ " + cls.reset_color())

    @classmethod
    def dedent(cls):
        cls.indent_stack.pop()

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def hex_to_ansi(cls, hex):
        if hex:
            r, g, b = ((hex >> 16) & 0xFF, (hex >>  8) & 0xFF, (hex >>  0) & 0xFF)
            return f"\x1B[38;2;{r};{g};{b}m"
        else:
            return ""

    @classmethod
    def reset_color(cls):
        if cls.current_color != 0 and cls.options.log_color:
            return "\x1B[0m"
        else:
            return ""

    @classmethod
    def log(cls, text):
        if not isinstance(text, str) or len(text) == 0:
            return

        if cls.log_level_in > cls.log_level_out:
            return

        if cls.current_color >= 0 and cls.options.log_color:
            hex = cls.current_color
            color_prefix = cls.hex_to_ansi(hex) if cls.options.log_color else ""
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

            if not cls.options.log_wrap:
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
            #Log.log(f"type      = {type(ex)}\n")
            cls.log("  text = ")
            with cls.color(0xFFFF00):
                cls.log(f"'{ex}'\n")
            cls.log(f"  file = {frame.filename}\n")
            cls.log(f"  func = {frame.name}\n")
            cls.log(f"  line = {frame.lineno}\n")
            cls.log(traceback.format_exc() + "\n")
            # Printing the line isn't useful as it's always going to be "raise ..."
            #cls.log(f"line      = '{frame.line}'\n")
        else: # pragma: no cover
            cls.log(f"Could not extract traceback from {ex}!")

    @classmethod
    def get_timestamp(cls):
        """Returns the timestamp string that is placed at the left of log entries."""
        return f"[{time.perf_counter() - cls.time_origin:8.3f}] " if cls.options.log_time else ""

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

#endregion
# --------------------------------------------------------------------------------------------------
# region Path
# These functions wrap the os.path.* functions so that they work on arbitrary trees

class Path:

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

    @staticmethod
    def resolve_path(path, strict):
        path = os.path.expandvars(path)
        path = pathlib.Path(path).expanduser()
        path = path.resolve(strict = strict)
        return str(path)

    @staticmethod
    def new_resolve(path):
        path = os.path.expandvars(path)
        path = pathlib.Path(path).expanduser()
        path = path.resolve(strict = True)
        return str(path)


    resolve  = tree_map(lambda path : Path.resolve_path(path, strict = True))

    # This doesn't work, abs'ing a path
    #normpath = tree_map(lambda path : Path.resolve_path(path, strict = False))

    normpath = tree_map(os.path.normpath)

    basename = tree_map(os.path.basename)
    dirname  = tree_map(os.path.dirname)
    swapext  = tree_map(lambda p, new_ext : os.path.splitext(p)[0] + new_ext)

    isabs    = tree_all(os.path.isabs)
    isfile   = tree_all(os.path.isfile)
    isdir    = tree_all(os.path.isdir)
    exists   = tree_all(os.path.exists)

    # WARNING - Both 'startswith' and 'rel' below can throw ValueError if there's a mix of abs/rel
    # paths, or if the paths are on different volumes in Windows. We don't handle this yet, but we
    # will need to eventually. If this occurs inside a macro you'll see the exception in the macro
    # expansion trace and the macro will be returned unexpanded. Using 'commonpath' here is
    # probably worth it though, as it handles some annoying edge cases.

    startswith = tree_all(lambda p, parent : os.path.commonpath([p, parent]) == parent)

    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with 'commonpath' and 'removeprefix'.

    @staticmethod
    def relpath(lhs, rhs):
        if isinstance(lhs, (list, tuple, set)):
            return [Path.relpath(lh, rhs) for lh in lhs]
        if isinstance(rhs, (list, tuple, set)):
            return [Path.relpath(lhs, rh) for rh in rhs]

        prefix = os.path.commonpath([lhs, rhs])

        if lhs == rhs:
            result = "."
        elif prefix != rhs:
            result = lhs
        else:
            result = lhs.removeprefix(prefix + os.sep)

        return result

    @staticmethod
    def join(lhs, rhs):
        if isinstance(lhs, (list, tuple, set)):
            return [Path.join(lh, rhs) for lh in lhs]
        if isinstance(rhs, (list, tuple, set)):
            return [Path.join(lhs, rh) for rh in rhs]
        return os.path.join(lhs, rhs)

# endregion
# --------------------------------------------------------------------------------------------------
# region Script

class Script:

    @classmethod
    def reset(cls, config):
        pass

    def __init__(self, options : Dict, module : types.ModuleType, code : types.CodeType):
        self.options = options

        self.onion = Onion(
            hancho_module  = hancho.__dict__,
            aliases        = hancho.Aliases.__dict__,
            script_module  = module.__dict__,
            script_options = options,
            task_config    = None,
        )

        self.module  = module
        self.code    = code

        self.comp_db_path = "{build_root}/compile_commands.json"
        self.stat_db_path = "{build_root}/hancho.json"

        # Tally up the rebuild reasons for debugging
        self.reasons = Counter()

        # Hash, size, mtime, command for each file in the previous build.
        # Command is only set for output files.
        self.old_stat_db : dict[str, Dict] = {}

        # Stats accumulated during the build after a task is initialized but before it has run.
        # Compared with old_stat_db entries to determine if a task needs a rebuild.
        self.mid_stat_db : dict[str, Dict] = {}

        self.loaded   = False  # true once the script has finished its exec()
        self.children = []     # child scripts (not repos)
        self.tasks    = []     # all tasks created by this script


    def __repr__(self):
        return Utils.dump_to_str("Script", self.__dict__, print_id = True, color_code = True)

    # ----------------------------------------------------------------------------------------------

    def load_stat_db(self) -> Dict:
        script = self
        result = {}

        stat_db = Expander.expand(self.stat_db_path, script.onion)
        stat_db = cast(str, Path.normpath(stat_db))

        comp_db = Expander.expand(script.comp_db_path, script.onion)
        comp_db = cast(str, Path.normpath(comp_db))

        with LogLevel.VERBOSE, Colors.ORANGE:
            Log.log(f"Loading stat db '{stat_db}'\n")

            if not os.path.isfile(stat_db):
                Log.log(f"Stat db '{stat_db}' not found\n")
                return Dict()

            time_a = time.perf_counter()
            result = Utils.load_json(cast(str, stat_db))
            time_b = time.perf_counter()

            Log.log(f"Loading {len(result)} stat db entries took {time_b - time_a:8.6f} seconds\n")

        # Turn the serialized stats back into a Dict.
        for k, v in list(result.items()):
            result[k] = Dict(v)

        return Dict(result)

    # ----------------------------------------------------------------------------------------------

    def update_stat_db(self, out_db, file, command = None):
        Utils.stat_calls += 1

        _hash = Utils.hash_file(file)
        _stat = os.stat(file)

        stat = out_db.get(file, Dict(command = None))
        stat.hash = _hash
        stat.st_size = _stat.st_size
        stat.st_mtime_ns = _stat.st_mtime_ns

        if command:
            stat.command = command

        out_db[file] = stat

    @classmethod
    def commands_to_string(cls, commands):
        commands = Utils.flatten(commands)
        if len(commands) and callable(commands[0]):
            commands = [c.__name__ for c in commands]
        return "; ".join(commands)

    # ----------------------------------------------------------------------------------------------

    def pre_task(self, task):
        script = self

        if script.options.build_dry:
            return

        # Tasks should have at most one depfile.
        for key, files in list(task.config.items()):
            if Task.is_depfile_field(key) and len(Utils.flatten(files)) > 1:
                # Why isn't this being hit by code coverage? We do have a test for it.
                raise Task.BROKEN("Tasks can't have more than one dependency file!")

        # If there's a depfile from a previous build, load it so we can use it in rebuild_reason.
        if "in_depfile" in task.config:
            task._old_deplines = Utils.load_depfile(
                task.config.in_depfile, task.config.depformat, task.config.task_cwd
            )
            for file in task._old_deplines:
                if os.path.exists(file):
                    self.update_stat_db(self.mid_stat_db, file)
                else:
                    raise AssertionError(f"Could not find {file}")

        for file in Utils.yield_values(task.in_files):
            assert os.path.exists(file)
            if os.path.exists(file):
                self.update_stat_db(self.mid_stat_db, file)

        for file in Utils.yield_values(task.out_files):
            if os.path.exists(file):
                str_command = Script.commands_to_string(task.config.command)
                self.update_stat_db(self.mid_stat_db, file, str_command)

    # ----------------------------------------------------------------------------------------------

    def post_task(self, task):
        if "in_depfile" in task.config:
            task.new_deplines = Utils.load_depfile(
                    task.config.in_depfile, task.config.depformat, task.config.task_cwd
                )

    # ----------------------------------------------------------------------------------------------

    def rebuild_reason(self, task) -> str:
        """
        Figures out why we have to run a Task, or returns "" if we don't.
        """

        # FIXME we should be expanding force_build etc. so we pick up either the task value or the
        # script value

        # ------------------------------------
        # Check the trivial reasons to rebuild

        if task.onion.build_force:
            self.reasons["forced"] += 1
            return "Target forced to rebuild"

        if not task.in_files:
            self.reasons["no inputs"] += 1
            return "Always rebuild a target with no inputs"

        if not task.out_files:
            self.reasons["no outputs"] += 1
            return "Always rebuild a target with no outputs"

        # ------------------------------------

        for filename in Utils.yield_values(task.out_files):
            if not Path.exists(filename):
                self.reasons["output missing"] += 1
                return f"Output file missing: {filename}"

            if filename not in self.old_stat_db:
                # I'm not sure we can test this, we probably get hit by other checks before we get
                # here.
                self.reasons["output stat missing"] += 1 # pragma: no cover
                return f"Output stat missing: {filename}"

            old_stat = self.old_stat_db[filename]
            mid_stat = self.mid_stat_db[filename]

            assert old_stat is not None
            assert mid_stat is not None

            if old_stat.command != mid_stat.command:
                self.reasons["command changed"] += 1
                return f"Command used to generate file has changed : {filename} : {old_stat.command} : {mid_stat.command}"

        # ------------------------------------

        all_files = task._old_deplines + list(Utils.yield_values(task.in_files))

        for filename in all_files:
            old_stat = self.old_stat_db[filename]
            mid_stat = self.mid_stat_db[filename]

            assert old_stat is not None
            assert mid_stat is not None

            if old_stat.st_mtime_ns != mid_stat.st_mtime_ns:
                self.reasons["mtime mismatch"] += 1
                return f"Mtime mismatch {old_stat.st_mtime_ns} != {mid_stat.st_mtime_ns} for : {filename}"

            if old_stat.st_size != mid_stat.st_size:
                self.reasons["size mismatch"] += 1
                return f"Size mismatch {old_stat.st_size} != {mid_stat.st_size} for : {filename}"

            if old_stat.hash != mid_stat.hash:
                self.reasons["hash mismatch"] += 1
                return f"Hash mismatch {old_stat.hash} -> {mid_stat.hash} for : {filename}"

            # Does not need to rebuild based on file stats / hash
            self.reasons["*hash match"] += 1

        self.reasons["*task clean"] += 1
        return ""

# endregion
# --------------------------------------------------------------------------------------------------
# region Task
# Task object + bookkeeping

class Task:

    @classmethod
    def reset(cls, config):
        cls.id_counter : int = 0
        cls.tasks_enabled : int = 0

    class FAILED(Exception):    pass
    class CANCELLED(Exception): pass
    class SKIPPED(Exception):   pass
    class BROKEN(Exception):    pass

    default_config = Dict(
        name         = None,
        desc         = None,
        command      = None,
        enabled      = False,
    )

    # ----------------------------------------------------------------------------------------------

    def __init__(self, *args, **kwargs):

        # The task's config contains all the commands, paths, options, inputs, dependent Tasks, and
        # anything else needed to assemble and run the task's commands. It is expected that build
        # scripts will need to read task.config in order to implement task callbacks, so the field
        # is not underscore-prefixed like the later ones.

        script = cv_script.get()

        self.script = script
        self.config = Dict(
            Task.default_config,
            *args, **kwargs
        )
        self.onion = Onion(script.onion, task_config = self.config)

        # Similarly, build scripts may need to see the complete list of inputs/outputs to a task
        # in addition to the individual in_/out_ fields, so these are public.
        self.in_files  = {}
        self.out_files = {}

        # ------------------------------------
        # Implementation details below this line

        self._aio_context = contextvars.copy_context()

        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._aio_task : asyncio.Task | None = None

        # Input dependencies read from the pre-existing source.o.d file.
        self._old_deplines = []

        # Input dependencies read after compilation from the new source.o.d file.
        self._new_deplines = []

        # Why this task rebuilt, or "" if it did not need to rebuild.
        self._reason = ""

        # True if this task is going to be built.
        self._enabled = False

        # The "return value" for the task as a whole, or "None" if the task was successful.
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
        self._complete = False

        script.tasks.append(self)

        # Auto-start the task if it was created dynamically during the build.
        if Utils.in_event_loop():
            self.enable_task()

    # ----------------------------------------------------------------------------------------------
    # Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
    # Dicts make deep copies and we want dicts to store Tasks, so we work around it by making
    # Tasks just return themselves when copied.

    def __copy__(self):
        return self

    def __deepcopy__(self, _):
        return self

    def __repr__(self):
        return Utils.dump_to_str(key = "Task", val = self)

    # ----------------------------------------------------------------------------------------------

    @staticmethod
    def is_depfile_field(key : str) -> bool:
        return key == "in_depfile"

    @staticmethod
    def is_output_field(key : str):
        return (key != "") and (Task.is_depfile_field(key) or key.startswith("out_"))

    @staticmethod
    def is_input_field(key : str):
        return (key != "") and key.startswith("in_")

    @staticmethod
    def is_io_field(key : str):
        return Task.is_input_field(key) or Task.is_output_field(key)

    # ----------------------------------------------------------------------------------------------

    def log(self, message : str):
        for line in message.splitlines(keepends=True):
            with Colors.LIME:
                if not Log.line_buffer:
                    Log.log(f"[{self._task_id:3d}/{Task.tasks_enabled:3d}] ")
            Log.log(line)

    # ----------------------------------------------------------------------------------------------

    def enable_task(self):
        if not self.config.enabled:
            self.config.enabled = True
            Task.tasks_enabled += 1
            if Utils.in_event_loop():
                self.create_aio_task()

    # ----------------------------------------------------------------------------------------------

    def create_aio_task(self):
        assert Utils.in_event_loop()

        if self._aio_task is None:
            t = asyncio.create_task(self.task_top(), context=self._aio_context)
            t.hancho_task = self # type: ignore
            Runner.live_aio_tasks.add(t)
            t.add_done_callback(lambda t: Runner.aio_done_queue.put_nowait(t))
            self._aio_task = t

        # Start all tasks referenced by the config so we don't deadlock while waiting for them.
        for v in Utils.yield_values(self.config):
            if isinstance(v, Task):
                v.enable_task()

    # ----------------------------------------------------------------------------------------------

    async def task_top(self):
        task = self
        try:
            await task.task_main()
            task._error = None
        except asyncio.CancelledError as ex:
            with LogLevel.VERBOSE:
                task.log(f"<asyncio.CancelledError {ex}>\n")
            task._error = ex
            raise
        except Task.BROKEN as ex:
            task.log_exception("Task broken!", ex)
            task._error = ex
        except Task.FAILED as ex:
            task.log_exception("Task failed!", ex)
            task._error = ex
        except Task.CANCELLED as ex:
            with LogLevel.VERBOSE:
                task.log(str(ex) + "\n")
            task._error = ex
        except Task.SKIPPED as ex:
            with LogLevel.VERBOSE:
                task.log(str(ex) + "\n")
            task._error = ex
        except Exception as ex:
            task.log_exception("Task threw an exception!", ex)
            with LogLevel.ERROR:
                Log.log(traceback.format_exc() + "\n")
            task._error = ex
        finally:
            if task._core_count:
                Runner.release(task._core_count)
                task._core_count = 0

        if task._error:
            raise task._error

        return task.out_files

    # ----------------------------------------------------------------------------------------------

    async def task_main(self):
        task = self
        script = cv_script.get()

        assert task.script is script

        time_a = time.perf_counter()

        Task.id_counter += 1
        task._task_id = Task.id_counter

        with LogLevel.DEBUG:
            task.log("Task config before expand:\n")
            task.log(str(task.config) + "\n")

        # ----------------------------------------
        # Expand all fields that don't depend on input/output filenames (basically everything
        # except name/desc/command). To prevent expansion-order issues, we expand to a temp Dict
        # and then copy them back into config.

        # We _can't_ expand input/output paths here as they may refer to output paths for tasks
        # that haven't executed yet - that has to happen _after_ awaiting our dependencies, so
        # you'll find it in task_init below.

        # FIXME yeah we should expand almost everything

        expanded = Dict()
        expanded.task_cwd   = Path.normpath(Expander.expand("{task_cwd}", task.onion))
        expanded.build_dir  = Path.normpath(Expander.expand("{build_dir}", task.onion))

        expanded.build_tag  = Expander.expand("{build_tag}", task.onion)
        expanded.core_count = Expander.expand("{cpu_cores}", task.onion)
        expanded.depformat  = Expander.expand("{depformat}", task.onion)
        expanded.enabled    = Expander.expand("{enabled}",   task.onion)

        Dict.merge(task.config, expanded)

        # ----------------------------------------
        # Await all tasks in our input fields and then flatten them.

        await task.await_inputs()

        # ----------------------------------------
        # Do all our task setup while chdir'd into the task's cwd so that relative paths will be
        # correct while we're checking input file existence. The task_init function is synchronous,
        # so there can be no await'ed points that could interrupt us - os.getcwd() should be stable
        # while we're doing this.

        with chdir(task.config.task_cwd):
            task.task_init()

        task.sanity_check()

        # ----------------------------------------
        # Paths updated. See if we need to rebuild our outputs.

        script.pre_task(task)

        task._reason = script.rebuild_reason(task)
        if not task._reason:
            raise Task.SKIPPED(f"Task is up-to-date: '{task.config.name}' : '{task.config.desc}'")

        # ----------------------------------------
        # Dry runs early out after all the task checks but before we allocate cores and run
        # commands.

        if script.options.build_dry:
            return

        # ----------------------------------------
        # Wait for enough jobs to free up to run this task.

        task._core_count = await Runner.acquire(task.config.core_count)

        # ----------------------------------------
        # Run all the task's commands

        with LogLevel.NORMAL:
            if task.config.name:
                task.log(f"{task.config.name}: ")
            task.log(f"{task.config.desc}\n")

        with LogLevel.VERBOSE, Log.color(0x606060):
            task.log(f"Task rebuilding because: {task._reason}\n")

        for command in cast(list, task.config.command):
            if command is None:
                continue
            elif callable(command):
                await task.call_callback(command)
            else:
                await task.run_command(command)

        # ----------------------------------------
        # See if the task wrote all its output files

        for file in Utils.yield_values(task.out_files):
            if not os.path.exists(file):
                raise Task.FAILED(f"Task ran, but output file still missing: {file}")

        if "in_depfile" in task.config:
            deplines = Utils.load_depfile(task.config.in_depfile, task.config.depformat, task.config.task_cwd)
            for file in deplines:
                script.update_stat_db(script.mid_stat_db, file)

        # ----------------------------------------
        # Done!

        time_b = time.perf_counter()

        task.script.post_task(task)

        with LogLevel.VERBOSE, Log.color(0x606060):
            message  = f"Task took {time_b-time_a:8.6f} sec: "
            if task.config.name:
                message += f"'{task.config.name}' - "
            message += f"'{task.config.desc}'\n"
            task.log(message)

    # ----------------------------------------------------------------------------------------------
    # NOTE: Hancho _cannot_ have dependency cycles unless you do something really sketchy via
    # modifying tasks after they're created but before they're started. If you point task B's
    # inputs at task A and task A's inputs at task B and it blows up, that's on you.

    async def await_inputs(self):

        # Copy the dict key-values, as it's generally a bad idea to modify a container you're
        # iterating over - _especially_ if it has an await in the middle of it.

        for key, files in list(self.config.items()):
            if not Task.is_input_field(key):
                continue

            # Our file list has never been flattened, so do it now.
            files = Utils.flatten(files)

            for i, file in enumerate(files):
                if isinstance(file, Task):
                    task = cast(Task, file)
                    if task._aio_task is None:
                        raise AssertionError("One of a task's input sub-tasks was not started") # pragma: no cover
                    try:
                        await task._aio_task
                    except asyncio.CancelledError:
                        # _This_ task was cancelled while waiting for inputs. We need to ensure
                        # the exception makes it back to asyncio.
                        raise
                    except Task.SKIPPED:
                        # This input was clean and didn't need to rebuild.
                        pass
                    except BaseException as ex:
                        raise Task.CANCELLED(f"Task is cancelled: '{self.config.name}' : '{self.config.desc}'") from ex

                    files[i] = task.out_files

            # Awaiting inputs has probably un-flattened our input fields. Re-flatten them.
            self.config[key] = Utils.flatten(files)

    # ----------------------------------------------------------------------------------------------

    def task_init(self):
        task = self
        #script = cv_script.get()

        if os.getcwd() != Path.resolve(task.config.task_cwd):
            raise AssertionError(f"Running task_init while we're not in the real path of task's cwd '{task.config.task_cwd}' - we are in {os.getcwd()}")  # pragma: no cover

        # ----------------------------------------
        # Flatten the commands and check that they're valid

        task.config.command = Utils.flatten(task.config.command)

        # ----------------------------------------
        # Expand all in_ and out_ filenames.

        # We _must_ expand _all_ of these first before joining paths or the paths will be incorrect:
        # prefix + swap(abs_path) != normpath(prefix + swap(path)).

        # FIXME can we merge these two loops now?
        # FIXME we need to move this loop into remap_io_fields or something.

        for key, val in list(task.config.items()):
            if not Task.is_io_field(key):
                continue

            temp = val
            temp = Expander.expand(temp, task.onion)
            temp = Path.join(task.script.options.script_cwd, temp)
            temp = Path.normpath(temp)

            task.config[key] = temp

        # ----------------------------------------
        # Do all the file path remapping so our commands will work

        for key, files in list(task.config.items()):
            if not Task.is_io_field(key):
                continue

            files = task.remap_io_field_paths(key, files)

            # and unwrap filenames if they're an array of one element so that scripts expecting
            # join(str, str) to return a str will be happy.
            task.config[key] = files[0] if len(files) == 1 else files

        # ----------------------------------------
        # Paths are cleaned up, we can expand name/desc/command

        task.config.name    = Expander.expand(task.config.name, task.onion)
        task.config.desc    = Expander.expand(task.config.desc, task.onion)
        task.config.command = Expander.expand(task.config.command, task.onion)

        with LogLevel.DEBUG:
            task.log("Task config after expand:\n")
            task.log(str(task.config) + "\n")

    # ----------------------------------------------------------------------------------------------

    def sanity_check(self):
        """
        Checks for various ways that a task can be broken and raises exceptions for them.
        All of our 'raise Task.BROKEN's should be here (except for 'Invalid depfile format' above)
        """
        task = self
        script = cv_script.get()
        assert task.script is script

        if not Path.exists(task.config.task_cwd):
            raise Task.BROKEN(f"Task working directory '{task.config.task_cwd}' does not exist")

        if not Path.startswith(task.config.build_dir, script.options.repo_root):
            raise Task.BROKEN(f"The build.dir {task.config.build_dir} is not under repo.root {task.config.repo_root}")

        # In order to provide the least amount of bafflement to users, CLI commands execute
        # from task_cwd (which is usually the root of the repo, the most common cwd)
        # and callbacks execute from dir(script_path) (because you expect to be in the same
        # directory as the script when the callback is firing).

        # This means that rel-ified paths can only be rel'd to one of the two cwds, not both.
        # And that means we disallow mixed cli/callback command lists.

        for command in task.config.command:
            if type(command) is not type(task.config.command[0]):
                raise Task.BROKEN(f"Commands aren't the same type: {task.config.command}")

        # In strict mode, we mark a task broken if its command still has curly braces.
        if script.options.build_strict:
            for command in cast(list, task.config.command):
                if not isinstance(command, str):
                    continue
                blocks = []
                Expander._split_template(command, blocks)
                if len(blocks) > 1 or (len(blocks) == 1 and blocks[0][0] == "{"):
                    raise Task.BROKEN("STRICT: Command has curly braces in it")

        # Check that all build files would end up under build.dir
        for file in Utils.yield_values(task.out_files):
            assert Path.isabs(file)
            if not Path.startswith(file, task.config.build_dir):
                raise Task.BROKEN(f"Path error, output file {file} is not under build.dir {task.config.build_dir}")

        # Check for task collisions
        for file in Utils.yield_values(task.out_files):
            real_file = cast(str, Path.normpath(file))
            if real_file in Loader.real_filenames:
                raise Task.BROKEN(f"TaskCollision: Multiple tasks build {real_file}")
            Loader.real_filenames.add(real_file)

        # Check for missing inputs. We have to check build_dry, as the input files may only exist if
        # we're really running tasks.
        for file in Utils.yield_values(task.in_files):
            if not Path.isabs(file):
                raise Task.BROKEN(f"Somehow we got a non-abs path for an input file - {file}")  # pragma: no cover
            if not Path.exists(file) and not script.options.build_dry:
                raise Task.BROKEN(f"Input file missing - {file}")

        # Check that task's commands are either strings or callables.
        for command in cast(list, task.config.command):
            if not isinstance(command, str) and not callable(command) and command is not None:
                raise Task.BROKEN(f"Command {command} is not a string or a callable?")

        # Tasks should have at most one depfile.
        for key, files in list(task.config.items()):
            if Task.is_depfile_field(key) and len(Utils.flatten(files)) > 1:
                raise Task.BROKEN("Tasks can't have more than one dependency file!")

    # ----------------------------------------------------------------------------------------------

    def remap_io_field_paths(self, name, files) -> list[str]:
        """
        Input and output file paths in .hancho scripts are declared relative to the directory the
        script is in (stored in the config under 'script_path').
        In general we want to run commands from the root of the repo and store output files in
        repo/build.
        This function takes care of all of that and a few other things, and tries to do so in a
        robust way. Whether this actually turns out to be robust or not is yet to be determined.
        """
        task = self
        script = cv_script.get()
        assert task.script is script
        script_dir = Path.dirname(script.options.script_path)

        # Initially, all our file paths are relative to the script that created this task.
        # Join script_dir with the filenames to produce absolute paths.
        files = Path.join(script_dir, files)

        # Expanding may have made our files array non-flat, but all of its contents should be
        # absolute paths now.
        files = Utils.flatten(files)

        # File paths _must_ be normed after joining, otherwise they might look like they're under
        # script_dir, but they're not because the paths could have "../../../../.." in them.
        files = cast(list[str], Path.normpath(files))

        assert Path.isabs(files)

        # Move all outputs under build.dir and ensure their directories exist.
        # Note - This will also move "in_depfile" under build.dir - this is _intentional_ as it's
        # an _output_ from the compiler.
        if Task.is_output_field(name):
            for i, file in enumerate(files):
                # Note that this conditional and the one below are _NOT_ an if/elif pair!
                if not Path.startswith(file, task.config.build_dir):  # noqa: SIM102
                    if Path.startswith(file, script.options.script_cwd):
                        #file = file.removeprefix(script.options.script_cwd)
                        file = Path.relpath(file, script.options.script_cwd)
                        file = Path.join(task.config.build_dir, file)
                        files[i] = file

                if not script.options.build_dry and Path.startswith(file, task.config.build_dir):
                    dirname = Path.dirname(file)
                    os.makedirs(dirname, exist_ok=True) #type:ignore

        # Gather all absolute file paths to in_files/out_files.
        # The check for is_depfile_field must come first, as it's a special case of a file that
        # is technically _both_ an input and an output file, even though its name starts with "in".
        for i in range(len(files)):
            if Task.is_depfile_field(name):
                pass
            elif Task.is_output_field(name):
                task.out_files[name] = files[i]
            elif Task.is_input_field(name):
                task.in_files[name] = files[i]

        # Convert the fixed paths back to relative so our command lines aren't enormous.
        # Relative paths are relative to task_cwd if we're running a command, otherwise they're
        # relative to script_dir if we're calling a callback.

        # actually this may not be worth it...

        #rel_dir = task.config.task_cwd if isinstance(config.command[0], str) else script.options.script_cwd
        #for i in range(len(files)):
        #    files[i] = Path.relpath(files[i], rel_dir)

        return files

    # ----------------------------------------------------------------------------------------------

    async def run_command(self, command):
        script = cv_script.get()

        task = self
        with LogLevel.VERBOSE, Colors.BLUE:
            task.log(f"{Path.relpath(task.config.task_cwd, script.options.repo_root)}$ {command}\n")

        proc = None
        try:
            # Create the subprocess via asyncio and then await the result.
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd    = task.config.task_cwd,
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
            with LogLevel.VERBOSE, Log.color(0x666666):
                task.log(task.dump_stdout())

    # ----------------------------------------------------------------------------------------------

    async def call_callback(self, command):
        script_dir = Path.dirname(self.script.options.script_path)

        callback_dir = Path.relpath(script_dir, self.script.options.repo_root)

        with LogLevel.VERBOSE, Colors.BLUE:
            self.log(f"{callback_dir}$ {command}\n")

        # Callbacks run from the script_dir where they were defined so that relative paths used
        # in the callback will be correct.
        with chdir(script_dir):
            result = command(self)
        if isawaitable(result):
            result = await result

        return result

    # ----------------------------------------------------------------------------------------------

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

    # ----------------------------------------------------------------------------------------------

    def log_exception(self, message, ex = None):
        script = cv_script.get()
        task = self
        with LogLevel.ERROR, Colors.RED:
            Log.log("========================================\n")
            Log.log(message + "\n")
            Log.log("========================================\n")

            Log.log(f"Script    = {script.options.script_path}:\n")
            Log.log(f"Task      = '{task.config.name}' : '{task.config.desc}'\n")
            Log.log(f"os.getcwd = {os.getcwd()}\n")
            Log.log(f"task cwd  = {task.config.task_cwd}\n")
            Log.log(f"command   = {task.config.command}\n")
            if ex:
                Log.log_exception(ex)
            Log.log(task.dump_stdout())

            Log.log("========================================\n")

# endregion
# --------------------------------------------------------------------------------------------------
# region Tracer
# Expansion tracing class used by Expander
#
# The traces generated look like this - the EX_XXXX prefix is an identifier for the Expander being
# used so you can tell when the expander changes, the rest are the call arguments and the return
# values.
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

class Tracer:

    @classmethod
    def reset(cls, config):
        cls.config = config
        pass

    def __init__(self, onion : Onion, enter_message, name):
        self.enter_message = f"{enter_message}({name!r})"
        self.name = name
        self.color = None
        self.onion = onion
        self.result = None
        self.trace = True

        #if len(self.name) > 40:
        #    self.name = self.name[:34] + "<snip>"

    def get_tag(self, obj):
        tag = (str(type(obj).__name__)[:2] + "_" + Utils.hex_id(obj)[-4:]).upper()
        return tag

    def save_result(self, result : Any):
        self.result = result

    def __enter__(self): # pragma: no cover
        if not self.trace:
            return self

        self.color = Utils.obj_to_hex(self.onion)

        with LogLevel.TRACE, Log.color(self.color):
            Log.log(f"{self.get_tag(self.onion)}." + self.enter_message + "\n")
            Log.indent(self.color)

        return self

    def __exit__(self, exc_type, exc_value, tb): # pragma: no cover
        if not self.trace:
            return False

        with LogLevel.TRACE, Log.color(self.color):
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
                    message = f"{self.name!r} : {type} = {self.get_tag(self.result)}\n"
                else:
                    message = f"{self.name!r} : {type} = {self.result!r}\n"

            Log.dedent()
            Log.log(message)

        return False

# endregion
# --------------------------------------------------------------------------------------------------
# region Loader

class Loader:

    # FIXME we should dedupe module compilation even if the config args differ

    class Abort(Exception):    pass # Raised by hancho scripts when they need to stop running due to some error.
    class EarlyOut(Exception): pass # Raised by hancho scripts when they are successful but don't need to do anything else.
    class Fail(Exception): pass     # Script has hit a fatal error

    @classmethod
    def reset(cls, config : Dict):
        cls.config : Dict= config
        cls.match_pointer : re.Pattern = re.compile(r"<(\w+) (\w+) at 0[xX][0-9a-fA-F]+>")
        cls.real_filenames : set[str] = set()
        cls.dedupe : dict[tuple[str, str], Script] = {}
        cls.loaded_files : list[str] = []
        cls.all_scripts : list[Script] = []
        cls.load_started = False

    # ----------------------------------------------------------------------------------------------

    @staticmethod
    def yield_tasks():
        for script in Loader.all_scripts:
            yield from script.tasks

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def load_script(cls, options) -> Script:

        parent_script = cv_script.get()

        # ----------------------------------------

        onion = Onion(parent_script.onion, options = options)

        path = options.script_path
        path = Expander.expand(path, onion)
        assert not Utils.is_template(path)
        path = Path.resolve(path)

        options.script_path = path

        with open(path, encoding="utf-8") as file:
            source = file.read()

        return Loader.load_str(options, source)

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def load_str(cls, options, source : str) -> Script:
        """This is split out from load_file for testing purposes."""

        assert Path.resolve(options.script_path) == options.script_path

        # ----------------------------------------
        # Dedupe the load - only scripts with identical real paths and identical configs are
        # deduped. This relies on __repr__ and the fields read by dump_to_str being stable during a
        # build, which they should be in practice.

        config_dump = Utils.dump_to_str(key = "options", val = options)
        config_dump = cls.match_pointer.sub(r"<\1 \2 at 0x...>", config_dump)

        dedupe_key = (options.script_path, config_dump)
        dedupe = cls.dedupe.get(dedupe_key, None) #type:ignore
        if dedupe is not None:
            with LogLevel.VERBOSE, Colors.SKY:
                Log.log(f"Deduped load of {options.script_path}\n")
            return dedupe

        # ----------------------------------------
        # Not deduped, create a new Script+Module and also a Repo+BuildDB if this script is the
        # root of a new repo.

        module = types.ModuleType(os.path.basename(options.script_path))
        module.__file__ = options.script_path
        module.hancho  = hancho  # type: ignore
        module.options = options # type: ignore

        code = compile(source, options.script_path, "exec", dont_inherit=True)

        new_script = Script(options, module, code)

        # ----------------------------------------
        # Script created, save to dedupe dict.

        cls.dedupe[dedupe_key] = new_script #type:ignore
        cls.loaded_files.append(options.script_path)

        return new_script

    # ----------------------------------------------------------------------------------------------

# endregion
# --------------------------------------------------------------------------------------------------
# region Runner

class Runner:

    @classmethod
    def reset(cls, options):
        cls.options = options
        cls.core_sem  : asyncio.Semaphore = asyncio.Semaphore(options.cpu_count)
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

    # ----------------------------------------------------------------------------------------------

    @classmethod
    async def acquire(cls, count):
        # A task that requires a lot of cores can block tasks behind it in the queue. This is
        # intended behavior.

        if count > cls.options.cpu_count: # pragma: no cover
            raise ValueError(f"Tried to acquire {count} cores, which exceeds the max {cls.options.cpu_count}")
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

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def enable_all_tasks(cls):
        # Enable _everything_
        for task in Loader.yield_tasks():
            task.enable_task()

    @classmethod
    def select_root_tasks(cls):
        script  = cv_script.get()

        if script.options.build_target:
            # Enable all tasks whose name matches the target regex
            # NOTE - We have to expand "name" _before_ the task has initialized, which means some
            # of its input fields may be Task references and the resulting name may be wonky if it
            # includes those names via template. Maybe don't do that.
            target_regex = re.compile(script.options.target)

            for task in Loader.yield_tasks():
                name = Expander.expand("{name}", task.onion)
                if target_regex.search(name):
                    task.enable_task()

        elif script.options.build_all:
            cls.enable_all_tasks()

        else:
            # Enable all tasks that were generated by the top script
            for task in Loader.yield_tasks():
                if task.script.options.repo_root == script.options.repo_root:
                #if task.script is script:
                    task.enable_task()

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def sync_run_tasks(cls):
        """Synchronously run all tasks until we're done with all of them."""
        return asyncio.run(cls.async_run_tasks())

    # ----------------------------------------------------------------------------------------------

    @classmethod
    async def async_run_tasks(cls):
        """Run all tasks until we run out."""

        script = cv_script.get()

        # ------------------------------------
        # Create asyncio tasks for all enabled Hancho tasks.

        for task in Loader.yield_tasks():
            if task.config.enabled:
                task.create_aio_task()

        # ------------------------------------
        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.

        with LogLevel.VERBOSE, Colors.BLUE:
            Log.log("Running tasks...\n")

        time_a = time.perf_counter()
        while cls.live_aio_tasks and cls.count_failures() <= Runner.options.max_errors:
            finished_aio_task = None

            try:
                finished_aio_task = cast(asyncio.Task, await cls.aio_done_queue.get())
                _ = finished_aio_task.result()
                cls.tasks_finished += 1
            except asyncio.CancelledError:
                cls.tasks_cancelled += 1
            except Task.CANCELLED:
                cls.tasks_cancelled += 1
            except Task.BROKEN:
                cls.tasks_broken += 1
            except Task.FAILED:
                cls.tasks_failed += 1
            except Task.SKIPPED:
                finished_aio_task.hancho_task._complete = True #type:ignore
                cls.tasks_skipped += 1
            except BaseException as ex:
                with LogLevel.DEBUG:
                    Log.log(f"Weird exception {type(ex)} >{ex}< at {time.perf_counter()}\n")
                    Log.log_exception(ex)
                cls.tasks_failed += 1
            else:
                # If _none_ of the above exceptions fired, we mark the task as complete.
                finished_aio_task.hancho_task._complete = True #type:ignore
            finally:
                if finished_aio_task is not None:
                    cls.live_aio_tasks.discard(finished_aio_task)
                cls.tasks_awaited += 1
        time_b = time.perf_counter()

        with LogLevel.VERBOSE, Colors.BLUE:
            Log.log(f"Running {cls.tasks_awaited} tasks took {time_b - time_a:8.6f} seconds\n")

        if cls.count_failures() > script.options.max_errors:
            with LogLevel.ERROR:
                Log.log(f"Too many failures after {cls.tasks_awaited}, cancelling tasks and stopping build\n")

            # Cancel all the asyncio.Tasks that haven't completed yet
            with LogLevel.VERBOSE:
                Log.log(f"Cancelling {len(cls.live_aio_tasks)} tasks\n")

            # This tasks_cancelled count may be off by one or two due to in-flight tasks not being
            # accounted for in live_aio_tasks, but it doesn't matter - we're about to bail out due
            # to failures or someone ctrl-c'ing the build, this is purely cosmetic.

            cls.tasks_cancelled += len(cls.live_aio_tasks)
            for t in cls.live_aio_tasks:
                t.cancel()

            # and then wait on their cancellations to complete (it isn't instantaneous)
            await asyncio.gather(*cls.live_aio_tasks, return_exceptions=True)

        return 1 if cls.tasks_failed or cls.tasks_broken else 0

    # ----------------------------------------------------------------------------------------------
    # not worth coverage checking this when we only have one tool and we know it works.

    @classmethod
    def run_tool(cls, tool : str): # pragma: no cover
        if tool == "clean":
            for task in Loader.yield_tasks():
                root = Path.normpath(Expander.expand("{build.root}", task.onion))
                root = Path.relpath(root, os.getcwd())
                if Path.isdir(root):
                    Log.log(f"Wiping build_root {root}\n")
                    shutil.rmtree(root, ignore_errors=True)
            Log.log("Clean done\n")
            return 0
        else:
            raise AssertionError(f"Don't know how to run tool {tool}")

# endregion
# --------------------------------------------------------------------------------------------------
# region Main

class Main:

    root_repo = None
    root_script = None
    build_started = False

    # fmt: off
    default_options = Dict(
        hancho_dir   = os.path.dirname(__file__),
        opt_file     = "hancho.opts",
        script_path  = "build.hancho",
        script_cwd   = "{dirname(script_path)}",
        task_cwd     = "{repo_root}",
        repo_root    = "{dirname(script_path)}",

        run_tool     = None,
        max_errors   = 0,
        cpu_count    = os.cpu_count() or 1,
        cpu_cores    = 1,
        depformat    = "gcc" if os.name == "posix" else "msvc",

        build_tag    = "",
        build_root   = "{repo_root}/build",
        build_dir    = "{build_root}/{build_tag}/{relpath(script_cwd, repo_root)}",
        build_target = None,
        build_force  = False,
        build_all    = False,
        build_dry    = False,
        build_strict = True,

        log_level    = LogLevel.NORMAL,
        log_quiet    = False,
        log_verbose  = False,
        log_debug    = False,
        log_trace    = False,
        log_wrap     = False,
        log_color    = True,
        log_time     = True,
    )

    # fmt: on

    hancho_script : Script

    @classmethod
    def reset(cls, config):
        pass


    # ----------------------------------------------------------------------------------------------
    # INIT

    @classmethod
    def init(cls, *args, **kwargs):

        flags = Dict(*args, kwargs)

        # --------------------------------------------------------------------------------------

        Log.reset     (flags)
        Expander.reset(flags)
        Utils.reset   (flags)
        Log.reset     (flags)
        Script.reset  (flags)
        Task.reset    (flags)
        Tracer.reset  (flags)
        Loader.reset  (flags)
        Runner.reset  (flags)
        Main.reset    (flags)

        onion = Onion(aliases = Aliases.__dict__, hancho = hancho.__dict__, flags = flags)

        flags.hancho_dir  = Expander.expand("{hancho_dir}",  onion)
        flags.script_path = Expander.expand("{script_path}", onion)
        flags.script_cwd  = Expander.expand("{script_cwd}",  onion)
        flags.repo_root   = Expander.expand("{repo_root}",   onion)

        flags.hancho_dir  = Path.normpath(flags.hancho_dir)
        flags.script_path = Path.normpath(flags.script_path)
        flags.script_cwd  = Path.normpath(flags.script_cwd)
        flags.repo_root   = Path.normpath(flags.repo_root)

        # ------------------------------------

        hancho_script = Script(
            Dict(flags, script_path =__file__),
            hancho,
            sys._getframe().f_code,
        )

        cv_script.set(hancho_script)
        Loader.all_scripts.append(hancho_script)
        cls.hancho_script = hancho_script

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def main(cls, flags):
        # Top-level exception handler just so we can print a big red "SOMETHING BROKE ALL BAD"
        # message if we failed to catch an exception during load/build.
        # The 'except' clause should catch Exception and not BaseException so ctrl-c doesn't get
        # misinterpreted as a Hancho bug.

        cv_token = None

        try:
            Main.banner_start(
                flags.script_path,
                flags.repo_root,
                flags.opt_file,
            )

            # LOAD

            Loader.load_started = True
            time_a = time.perf_counter()
            top_script = load2(flags.script_path, True)
            time_b = time.perf_counter()

            cv_token = cv_script.set(top_script)

            with LogLevel.VERBOSE, Colors.BLUE:
                Log.log(f"Loading scripts took {time_b - time_a} seconds\n")

            # BUILD

            time_a = time.perf_counter()
            result = Main.build()
            time_b = time.perf_counter()

            with LogLevel.VERBOSE, Colors.GREEN:
                Log.log(f"Build took {time_b - time_a} seconds\n")

            # DONE

            Main.banner_end()
            return result

        except Exception:
            print(Log.hex_to_ansi(0xFF3030), end="")
            print("Hancho hit an exception during startup:")
            traceback.print_exc()
            print("\x1B[0m", end="")
            return 1
        finally:
            if cv_token:
                cv_script.reset(cv_token)
            # Don't leave the last line of the log sitting in line_buffer!
            Log.flush()

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def parse_flags(cls, argv):

        desc = textwrap.dedent("""
        ================================================================================
                        Hancho is a simple, pleasant build system
        ================================================================================
        """)

        parser = argparse.ArgumentParser(
            description=desc,
            #formatter_class=argparse.RawDescriptionHelpFormatter
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
        verbosities = [v.lower() for v in LogLevel.__members__]

        d = Main.default_options
        bool_opt = argparse.BooleanOptionalAction

        # Flags
        # fmt: off
        parser.add_argument(      "--hancho_dir",   default = d.hancho_dir,   metavar = "(path)",    type=str,       help="Override the directory that we think hancho.py is in.")
        parser.add_argument('-o', "--opt_file",     default = d.opt_file,     metavar = "(path)",    type=str,       help="File containing a Python literal that will be used as additional options")
        parser.add_argument('-s', "--script_path",  default = d.script_path,  metavar = "(path)",    type=str.strip, help="The .hancho file that starts the build.")
        parser.add_argument(      "--script_cwd",   default = d.script_cwd,   metavar = "(path)",    type=str.strip, help="Change to this directory before running the top build script.")
        parser.add_argument(      "--task_cwd",     default = d.task_cwd,     metavar = "(path)",    type=str.strip, help="Directory to run commands in.")
        parser.add_argument('-r', "--repo_root",    default = d.script_cwd,   metavar = "(path)",    type=str.strip, help="The location of the repo we're building.")

        parser.add_argument(      "--run_tool",     default = d.run_tool,     metavar = "(tool)",    type=str.strip, help="Run a subtool.")
        parser.add_argument(      "--max_errors",   default = d.max_errors,   metavar = "(count)",   type=int,       help="The maximum number of task errors we tolerate before abandoning the build")
        parser.add_argument(      "--cpu_count",    default = d.cpu_count,    metavar = "(count)",   type=int,       help="Run jobs on N cores in parallel.")
        parser.add_argument(      "--cpu_cores",    default = d.cpu_cores,    metavar = "(count)",   type=int,       help="How many CPU cores to reserve per task.")
        parser.add_argument(      "--depformat",    default = d.depformat,    metavar = "(format)",  type=str.strip, help="Dependency file format (gcc or msvc)")

        parser.add_argument(      "--build_tag",    default = d.build_tag,    metavar = "(name)",    type=str.strip, help="Set the build tag. Tagged builds will have separate subdirectories under the build directory.")
        parser.add_argument(      "--build_root",   default = d.build_root,   metavar = "(path)",    type=str.strip, help="Directory to put build artifacts in.")
        parser.add_argument(      "--build_dir",    default = d.build_dir,    metavar = "(path)",    type=str.strip, help="Per-task build artifact directory. Directory to put build artifacts in.")
        parser.add_argument('-t', "--build_target", default = d.build_target, metavar = "(name)",    type=str.strip, help="A regex that selects the targets to build. Defaults to all targets in the top repo.")
        parser.add_argument(      "--build_force",  default = d.build_force,  action = bool_opt,                     help="Rebuild targets even if they're clean.")
        parser.add_argument(      "--build_all",    default = d.build_all,    action = bool_opt,                     help="Build absolutely everything in all build scripts loaded.")
        parser.add_argument(      "--build_dry",    default = d.build_dry,    action = bool_opt,                     help="Dry run - Do everything except actually run commands.")
        parser.add_argument(      "--build_strict", default = d.build_strict, action = bool_opt,                     help="Strict mode, slightly more error checking to catch footguns.")

        parser.add_argument(      "--log_level",    default = d.log_level,    choices = verbosities,                 help="Manually select verbosity level. 'quiet' = none, 'trace' = maximal spam")
        parser.add_argument('-Q', "--log_quiet",    default = d.log_quiet,    action = bool_opt,                     help="(same as --log_level=quiet)")
        parser.add_argument('-V', "--log_verbose",  default = d.log_verbose,  action = bool_opt,                     help="(same as --log_level=verbose)")
        parser.add_argument('-D', "--log_debug",    default = d.log_debug,    action = bool_opt,                     help="(same as --log_level=debug)")
        parser.add_argument('-T', "--log_trace",    default = d.log_trace,    action = bool_opt,                     help="(same as --log_level=trace)")
        parser.add_argument('-w', "--log_wrap",     default = d.log_wrap,     action = bool_opt,                     help="Wrap lines around the console instead of clipping them")
        parser.add_argument('-c', "--log_color",    default = d.log_color,    action = bool_opt,                     help="Use color in the log for better readability")
        parser.add_argument(      "--log_time",     default = d.log_time,     action = bool_opt,                     help="Timestamp each log line")
        # fmt: on


        (raw_flags, unrecognized) = parser.parse_known_args(argv)

        flags = Dict()

        opt_file = Path.normpath(raw_flags.opt_file)
        if os.path.exists(opt_file):
            with open(opt_file) as f:
                opts = json.load(f)
                Dict.merge(flags, flags, opts)

        for key, val in vars(raw_flags).items():
            flags[key] = val

        # Unrecognized command line parameters also become config fields if they are flag-like.
        # Naked flags become {'name':True}, number types become numbers, 'true' and 'false'
        # become bools (regardless of capitalization), everything else becomes a string.

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

                flags[key] = val

        return flags

    # ----------------------------------------------------------------------------------------------
    # Startup banner

    @classmethod
    def banner_start(cls, script_path, repo_root, opt_file):


        with LogLevel.VERBOSE, Colors.LIME:
            Log.log(f"Command line : {" ".join(sys.argv)}\n")
            Log.log(f"Script path  : {script_path}\n")
            Log.log(f"Repo root    : {repo_root}\n")
            Log.log(f"Opt file     : {opt_file}\n")
            if not os.path.exists(opt_file):
                Log.log("Opt file not found!\n")

            if Log.log_level_out >= LogLevel.TRACE:
                Log.log("Trace mode on\n")
            if Log.log_level_out >= LogLevel.DEBUG:
                Log.log("Debug mode on\n")
            if Log.log_level_out >= LogLevel.VERBOSE:
                Log.log("Verbose mode on\n")


    # ----------------------------------------------------------------------------------------------
    # This must happen _after_ all repos are loaded (so that if they change repo_root we don't get
    # the old path), but _before_ we build any tasks.

    @classmethod
    def pre_build(cls, build_db):
        build_db.old_stat_db = build_db.load_stat_db()

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def build(cls):
        top_script = cv_script.get()

        # Do _not_ update stats after running a tool, just early out.
        if top_script.options.run_tool:
            result = Runner.run_tool(top_script.options.run_tool)
            return

        # ------------------------------------

        time_a = time.perf_counter()
        for script in Loader.all_scripts:
            cls.pre_build(script)
        time_b = time.perf_counter()

        #print("pre_build done")
        with LogLevel.DEBUG, Colors.BLUE:
            Log.log(f"Loading stats took {time_b - time_a:8.6f} seconds\n")

        # ------------------------------------

        Runner.select_root_tasks()
        cls.build_started = True
        result = Runner.sync_run_tasks()
        #print("build done")

        # ------------------------------------

        time_a = time.perf_counter()
        for script in Loader.all_scripts:
            cls.post_build(script)
        time_b = time.perf_counter()

        #print("post_build done")
        with LogLevel.DEBUG, Colors.BLUE:
            Log.log(f"Saving stats took {time_b - time_a:8.6f} seconds\n")

        return result

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def post_build(cls, script : Script):
        if script.options.build_dry:
            return

        stat_db_path = Expander.expand(script.stat_db_path, script.onion)
        stat_db_path = cast(str, Path.normpath(stat_db_path))
        comp_db_path = Expander.expand(script.comp_db_path, script.onion)
        comp_db_path = cast(str, Path.normpath(comp_db_path))

        # Gather stats from all completed tasks
        stat_db = {}
        comp_db = {}

        for task in script.tasks:
            if isinstance(task._error, (Task.CANCELLED, Task.BROKEN, Task.FAILED)) or not task._complete:
                continue

            for file in Utils.yield_values(task.in_files):
                script.update_stat_db(stat_db, file)

                # Haven't tested this in an IDE, but I think it matches the spec.
                comp_db[file] = {
                    "directory" : task.config.task_cwd,
                    "command"   : script.commands_to_string(task.config.command),
                    "file"      : file,
                }

            if "in_depfile" in task.config:
                deplines = Utils.load_depfile(task.config.in_depfile, task.config.depformat, task.config.task_cwd)
                for file in deplines:
                    script.update_stat_db(stat_db, file)

            for file in Utils.yield_values(task.out_files):
                str_command = script.commands_to_string(task.config.command)
                script.update_stat_db(stat_db, file, str_command)

        with LogLevel.DEBUG, Colors.ORANGE:
            Log.log(f"┌ Repo {script.options.repo_root} post-build\n")
            Log.indent(Colors.ORANGE)

        # Dump the stats as JSON.
        if stat_db_path is not None:
            time_a = time.perf_counter()
            Utils.save_json(stat_db, stat_db_path)
            time_b = time.perf_counter()
            with LogLevel.DEBUG, Colors.ORANGE:
                Log.log(f"Saved {len(stat_db)} stats to {stat_db_path}\n")
            with LogLevel.DEBUG, Colors.BLUE:
                Log.log(f"Saving stat db took {time_b - time_a:8.6f} seconds\n")

        if comp_db_path is not None:
            time_a = time.perf_counter()
            Utils.save_json(list(comp_db.values()), comp_db_path)
            time_b = time.perf_counter()
            with LogLevel.DEBUG, Colors.ORANGE:
                Log.log(f"Saved {len(comp_db)} stats to {comp_db_path}\n")
            with LogLevel.DEBUG, Colors.BLUE:
                Log.log(f"Saving comp_db took {time_b - time_a:8.6f} seconds\n")

        with LogLevel.DEBUG, Colors.ORANGE:
            Log.dedent()
            Log.log(f"└ Repo {script.options.repo_root} done\n")

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def banner_end(cls):
        task_count = len(list(Loader.yield_tasks()))

        with LogLevel.VERBOSE:
            Log.log(f"Tasks created:    {task_count}\n")
            Log.log(f"Tasks awaited:    {Runner.tasks_awaited}\n")
            Log.log(f"Tasks finished:   {Runner.tasks_finished}\n")
            Log.log(f"Tasks broken:     {Runner.tasks_broken}\n")
            Log.log(f"Tasks failed:     {Runner.tasks_failed}\n")
            Log.log(f"Tasks cancelled:  {Runner.tasks_cancelled}\n")
            Log.log(f"Tasks skipped:    {Runner.tasks_skipped}\n")
            Log.log(f"Mtime calls:      {Utils.stat_calls}\n")
            Log.log(f"Hash calls:       {Utils.hash_calls}\n")
            Log.log(f"Hash bytes:       {Utils.hash_bytes}\n")
            Log.log(f"Hash time:        {Utils.hash_time:8.6f}\n")

        if Runner.tasks_failed or Runner.tasks_broken:
            with LogLevel.ERROR, Colors.RED:
                Log.log("BUILD FAILED\n")
        elif Runner.tasks_finished:
            with Colors.GREEN:
                Log.log("BUILD PASSED\n")
        else:
            with Colors.BLUE:
                Log.log("BUILD CLEAN\n")

        with LogLevel.DEBUG, Colors.BLUE:
            for script in Loader.all_scripts:
                Log.log(f"Stats for {script.options.script_path}\n")
                Log.indent(Colors.BLUE)
                for k, v in script.reasons.items():
                    Log.log(f"Rebuild reasons {k:13} = {v}\n")
                Log.dedent()

# endregion
# --------------------------------------------------------------------------------------------------
# region aliases

# These are aliases for methods in Hancho that have been pulled out so they can be used by
# template expansion. This lets you do {flatten(x)} instead of {Utils.flatten(x)} in macros.
# FIXME - maybe just put these in the top level globals? type checking might work better idk.

class Aliases:
    path     = Path
    basename = Path.basename
    swapext  = Path.swapext
    resolve  = Path.resolve
    normpath = Path.normpath
    relpath  = Path.relpath
    dirname  = Path.dirname
    cwd      = os.getcwd
    flatten  = Utils.flatten
    run_cmd  = Utils.run_cmd
    weave    = Utils.weave

# ----------------------------------------

def load2(script_path, is_repo, *args, **kwargs):
    parent_script = cv_script.get()

    script_path = Expander.expand(script_path, parent_script.onion)
    script_path = Path.resolve(script_path)

    child_options = Dict(
        parent_script.options,
        *args,
        kwargs,
        Dict(
            repo_root = Path.dirname(script_path) if is_repo else parent_script.options.repo_root,
            script_path = script_path,
            script_cwd  = Path.dirname(script_path),
        )
    )

    with LogLevel.VERBOSE, Colors.ORANGE:
        type = "repo" if is_repo else "script"
        Log.log(f"Loading {type} {script_path}\n")

    child_script = Loader.load_script(child_options)
    parent_script.children.append(child_script)

    with chdir(child_script.options.script_cwd):
        token = cv_script.set(child_script)
        try:
            Log.indent(Colors.ORANGE)
            exec(child_script.code, child_script.module.__dict__)
        except (Loader.Abort, Loader.EarlyOut):
            pass
        except Loader.Fail as fail:
            raise RuntimeError(f"Script failed : {script_path}") from fail
        finally:
            Log.dedent()
            cv_script.reset(token)

    Loader.all_scripts.append(child_script)

    return child_script

def build():
    return Main.build()

def load(script_path, *args, **kwargs):
    return load2(script_path, False, *args, **kwargs).module

def repo(script_path, *args, **kwargs):
    return load2(script_path, True, *args, **kwargs).module

# ----------------------------------------

def log(*args, **kwargs):
    return Log.log(*args, **kwargs)

# ----------------------------------------

def task(*args, **kwargs):
    if len(args) and callable(args[0]):
        # Take hancho.task(callable, ...) and instead of creating a task, collect all the args into
        # a dict and then splat it into the callback.
        merged_config = Dict(
            Task.default_config,
            *args[1:],
            kwargs
        )
        return args[0](**merged_config)
    else:
        return Task(*args, **kwargs)

# ----------------------------------------

def fail(message):
    with LogLevel.ERROR, Colors.RED:
        frame = sys._getframe(1)
        Log.log("Script failed:\n")
        Log.log(f"  text = '{message}'\n")
        Log.log(f"  file = {frame.f_code.co_filename}\n")
        Log.log(f"  func = {frame.f_code.co_name}\n")
        Log.log(f"  line = {frame.f_lineno}\n")
    raise Loader.Fail()

# ----------------------------------------

def abort(message):
    with LogLevel.WARNING, Colors.YELLOW:
        frame = sys._getframe(1)
        Log.log("Script aborted:\n")
        Log.log(f"  text = '{message}'\n")
        Log.log(f"  file = {frame.f_code.co_filename}\n")
        Log.log(f"  func = {frame.f_code.co_name}\n")
        Log.log(f"  line = {frame.f_lineno}\n")
    raise Loader.Abort()

# ----------------------------------------

def earlyout(message = ""):
    with LogLevel.VERBOSE, Colors.LIME:
        frame = sys._getframe(1)
        Log.log("Script exited early:\n")
        Log.log(f"  text = '{message}'\n")
        Log.log(f"  file = {frame.f_code.co_filename}\n")
        Log.log(f"  func = {frame.f_code.co_name}\n")
        Log.log(f"  line = {frame.f_lineno}\n")
    raise Loader.EarlyOut()

# ----------------------------------------

def init(*args, **kwargs):
    flags = Main.parse_flags([])
    Dict.merge(flags, *args, kwargs)
    Main.init(flags)

# endregion
# --------------------------------------------------------------------------------------------------
# region __main__

def _start():
    if __name__ == "__main__":
        flags = Main.parse_flags(sys.argv[1:])
        Main.init(flags)
        result = Main.main(flags)
        sys.exit(result)

_start()

# endregion
# --------------------------------------------------------------------------------------------------
