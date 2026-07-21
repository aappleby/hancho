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

# We treat the Hancho module itself as a repo, so that we have a place to put everything added to
# the build by tests or other code that doesn't load a .hancho script.

sentinel = "<sentinel>"

# endregion
# --------------------------------------------------------------------------------------------------
# region CVProxy

class CVProxy:
    def __init__(self, name : str):
        # Use object.__setattr__ to avoid triggering custom __setattr__ during init
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_cv", contextvars.ContextVar(name, default = None))

    def get(self):
        if obj := self._cv.get() is None:
            raise RuntimeError(f"ContextVar {self._name} is not set in this context.")
        return obj

    def set(self, val):
        return self._cv.set(val)

    def __getattr__(self, item):
        return getattr(self.get(), item)

    def __setattr__(self, item, value):
        setattr(self.get(), item, value)

    def __delattr__(self, item):
        delattr(self.get(), item)

    def __repr__(self):
        return repr(self.get())

cv_batch  : contextvars.ContextVar[Any] = contextvars.ContextVar("batch",  default = None)
cv_repo   : contextvars.ContextVar[Any] = contextvars.ContextVar("repo",   default = None)
cv_script : contextvars.ContextVar[Any] = contextvars.ContextVar("script", default = None)

# endregion
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

    # ----------------------------------------
    # If a key is in both the lhs and rhs, set the key+val in lhs and remove it from rhs.

    def pluck(self, src: Dict):
        for key, lhs in list(self.items()):
            self[key] = src.pop(key, lhs)
        return self

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
        return Dumper.dump_to_str(key = None, val = self)

    # ----------------------------------------

    def __getitem__(self, key : str):
        return dict.__getitem__(self, key)

    def __setitem__(self, key : str, val : Any):
        dict.__setitem__(self, key, val)

    def expand(self, template):
        with Tracer(self, "dict.expand", template) as trace:
            result = Expander._expand(template, self)
            trace.save_result(result)
            return result

    def eval(self, expr):
        return self.expand("{" + expr + "}")


# Tool is just an alias for Dict to make build scripts more readable.
class Tool(Dict):
    pass

# endregion
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

    @classmethod
    def reset(cls, flags):
        pass

    @classmethod
    def get(cls, context : abc.Mapping, key : str) -> Any:
        return cls._expand("{" + key + "}", context)

    @classmethod
    def _expand(cls, variant : Any, context : abc.Mapping) -> Any:
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
                result.append(Expander._expand(v, context))
                # Restore the budget so the next string in the list gets it.
                Expander.cv_evals.set(saved)
            return result

        if isinstance(variant, dict):
            result = {}
            for k, v in variant.items():
                # Remember how much budget was spent.
                saved = Expander.cv_evals.get()
                # Expand the list element.
                result[k] = Expander._expand(v, context)
                # Restore the budget so the next string in the list gets it.
                Expander.cv_evals.set(saved)
            return result

        # Bail out early if our variant isn't a string.
        if not isinstance(variant, str):
            return variant

        script : Script = cv_script.get()
        if script:
            onion = Onion(hancho.__dict__, script.flags, script.module.__dict__, context)
        else:
            onion = Onion(hancho.__dict__, context)

        template = cast(str, variant)
        delims = onion.raw_get("delims")

        # Bail out if we've gone through too many levels of recursion.
        depth = Expander.cv_depth.get()
        if depth >= Expander.MAX_DEPTH:
            raise RecursionError(f"Expansion failed to terminate after {depth} recursions: {template!r}")
        Expander.cv_depth.set(depth + 1)

        # OK, we have a string that could be a template. Keep expanding it until it stops changing
        # or it's no longer a template.
        try:
            old_template = None
            while old_template != template and Utils.is_template2(template, delims):
                old_template = template
                with Tracer(onion, "expand", template) as trace:
                    template = Expander._expand_pass(template, onion, delims)
                    trace.save_result(template)
        finally:
            # And then reset the depth/evals check vars when we're done.
            if depth == 0:
                Expander.cv_evals.set(0)
            Expander.cv_depth.set(depth)

        return template

    # ----------------------------------------------------------------------------------------------
    # IMPORTANT IMPORTANT IMPORTANT
    # If you can't eval a macro, you return it unchanged.
    # TEFINAE : Template Expansion Failure Is Not An Error. Same idea as SFINAE in C++ - we don't
    # fail on expansion failure so we can retry somewhere/somewhen else.

    @classmethod
    def _expand_pass(cls, template : str, onion : Onion, delims : dict[str, str]):
        """This inner expand function does one split-expand-rejoin pass on the template string."""

        # Split the string into literal and macro blocks.
        blocks = []
        Expander._split_template(template, blocks, delims)

        # Expand all macro blocks.
        for i, block in enumerate(blocks):

            # Skip literal blocks.
            if len(block) < 2 or block[0] not in delims or block[-1] not in delims[block[0]]:
                continue

            # Bail out if we've taken too many expansion steps already.
            if (steps := Expander.cv_evals.get()) >= Expander.MAX_EVALS:
                raise RecursionError(f"Expansion failed to terminate after {steps} evals: '{template!r}'")
            Expander.cv_evals.set(steps + 1)

            # Otherwise try and expand the macro. Failing is OK.
            # This should be the _only_ try/except block in the expansion code.

            try:
                with Tracer(onion, "eval", block) as trace:
                    result = eval(block[1:-1], {}, onion)
                    trace.save_result(result)

                # If there was only one block in the list, we're done early.
                if len(blocks) == 1:
                    return result

                blocks[i] = Utils.stringify(result)

            # Note that we do _not_ suppress any BaseExceptions - they _must_ be propagated up to
            # callers. As of Python 3.11, this includes asyncio.CancelledError.
            except RecursionError:
                raise
            except Exception as _:
                # Do NOT print stuff here or it'll spam like mad
                #traceback.print_exc()
                pass

        # Otherwise we stringify everything and join the blocks back together.
        return "".join(blocks)

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def _split_template(cls, text : str, out : list[str], delims):
        """
        Extracts all innermost single-brace-delimited spans from a block of text and produces a
        list of string literals and macros. Escaped braces don't count as delimiters.
        """
        assert isinstance(text, str)

        rdelim = ""
        cursor = 0
        lbrace = -1
        escaped = False
        chunk_count = 0
        for i, c in enumerate(text):
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c in delims:
                lbrace = i
                rdelim = delims[c]
            elif c in rdelim and lbrace >= 0:
                if cursor < lbrace:
                    out.append(text[cursor:lbrace])
                    chunk_count += 1
                out.append(text[lbrace:i+1])
                chunk_count += 1
                cursor = i + 1
                lbrace = -1
                rdelim = ""

        if cursor < len(text):
            out.append(text[cursor:])
            chunk_count += 1
        return chunk_count

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

    Onion fixes this by looking up the key in all 'layers' and returns a value only if it was the
    first non-Mapping match. If there are multiple Mapping matches, they form a new Onion.

    Onion layers are searched in right-to-left (i.e. reverse) order, to match the "right overrides
    left" behavior of Dict.

    Why the name 'Onion'?
    Well, 'stack' and 'deck' are overloaded and 'Onion' at least implies nested layers.
    """

    def __init__(self, *args, **kwargs):

        self._layers : list[types.MappingProxyType] = []
        for val in args:
            if isinstance(val, Onion):
                self._layers.extend(val._layers)
            elif isinstance(val, types.MappingProxyType):
                self._layers.append(val)
            elif isinstance(val, abc.Mapping):
                self._layers.append(types.MappingProxyType(val))
            else:
                raise TypeError(f"Can't use this as an onion layer: {type(val)} = {val}")

        if len(kwargs):
            self._layers.append(types.MappingProxyType(kwargs))

#    @classmethod
#    def _wrap(cls, *args, **kwargs):
#        """
#        Wrap the given dict so that when we expand things in it the macros can refer to stuff in
#        hancho or the script module that created it.
#        """
#        script = cv_script.get()
#
#        onion = Onion(
#            hancho.__dict__,
#            Main.flags,
#            cv_batch.get().flags,
#            cv_repo.get().flags,
#            cv_script.get().flags,
#            cv_script.get().module.__dict__,
#            *args,
#            **kwargs
#        )
#        return onion

    def __getattr__(self, key : str):
        try:
            return self.get(key)
        except KeyError as err:
            raise AttributeError from err

    def __getitem__(self, key):
        return self.get(key)

    def __iter__(self):
        seen = set()
        for layer in reversed(self._layers):
            for key in layer:
                if key not in seen:
                    seen.add(key)
                    yield key

    def __len__(self):
        return len(set().union(*self._layers))

    def __repr__(self):
        return Dumper.dump_to_str(key = None, val = self)

    def __contains__(self, key):
        return any(key in layer for layer in self._layers)

    def get(self, key, default : Any = sentinel) -> Any: # type: ignore
        """
        Searches through layers in reverse order (because we obey rightmost-not-None wins) for a
        key match. If we find it, we expand it before returning it. If we only found Mappings, we
        return a new Onion containing those mappings.
        """
        with Tracer(self, "get", key) as trace:
            # Return the rightmost non-None non-Mapping if present.
            saw_a_none = False
            for layer in reversed(self._layers):
                if key in layer:
                    val = layer[key]
                    if val is None:
                        saw_a_none = True
                    elif not isinstance(val, abc.Mapping):
                        result = Expander._expand(val, self)
                        trace.save_result(result)
                        return result

            # If the key was present but there was no value associated with it, return None.
            if saw_a_none:
                result = None if default == sentinel else default
                trace.save_result(result)
                return result

            # Nope, all mappings. Pull out the ones containing the key.
            new_layers = [layer[key] for layer in self._layers if key in layer]

            # No matches and no default? Bad key.
            if not new_layers and default == sentinel:
                raise KeyError(key)

            # No matches but we have a default? Return it.
            if default != sentinel:
                return default

            # Otherwise we make a new onion out of the mappings.
            result = Onion(*new_layers)
            trace.save_result(result)
            return result

    def raw_get(self, key, default : Any = sentinel) -> Any:
        """
        A simpler getter equivalent to ChainMap.get - doesn't expand the result.
        """
        for layer in reversed(self._layers):
            if key in layer:
                return layer[key]

        if default == sentinel:
            raise KeyError(key)

        return default

    def expand(self, template):
        with Tracer(self, "expand", template) as trace:
            result = Expander._expand(template, self)
            trace.save_result(result)
            return result

#endregion
# --------------------------------------------------------------------------------------------------
# region Dumper

class Dumper:

    # These types don't get dumped because they're not really dumpable.
    opaque_types = types.MappingProxyType({
        types.BuiltinFunctionType : "<builtin>",
        types.ModuleType          : "<module>",
        types.GeneratorType       : "<generator>",
    })

    # These types don't need a type annotation when dumped.
    base_types = (str, bool, int, float, list, tuple, set, dict, bytes, bytearray, range, type(None),
                  *opaque_types.keys())

    @classmethod
    def dump_to_str(cls, key, val, indent = 0, print_id = False, color_code = False, max_length = 80, tab = "    "):
        """
        Hancho's pretty-printer for various types. Note that this is also used for script deduping:
        if you load "my/app/tools/stuff.hancho" multiple times but the configurations you gave it
        were identical, you should get one copy of the "stuff" script instead of two.

        As long as you're not doing something bizarre with configs or changing the dumper in the
        middle of a build, the resulting strings should be stable enough to use for deduping.
        """

        # Unwrap tasks only if they're at the bottom level of indentation.
        if isinstance(val, Task) and indent == 0:
            val = val.__dict__

        return cls._dump_variant_to_str(key, val, indent, print_id, color_code, max_length, tab, memo = {})

    @classmethod
    def _dump_prefix(cls, key, val, print_id, color_code):
        prefix = ""
        if key is not None:
            prefix += str(key)
        if type(val) not in Dumper.base_types:
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
            val = f"<Task '{val.config.name}'>"
        elif isinstance(val, contextvars.Context):
            val = "<Context>"
        elif isinstance(val, types.ModuleType):
            val = f"<Module {val.__name__}>"
        elif isinstance(val, types.FunctionType):
            val = f"<Function {val.__name__}>"
        elif isinstance(val, argparse.Namespace):
            val = val.__dict__

        if type(val) in Dumper.opaque_types:
            return Dumper.opaque_types[type(val)] # type: ignore
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
        elif isinstance(val, (list, tuple, set)):
            items = [(None, v) for v in val]
            return '[', items, ']'
        elif isinstance(val, Onion):
            items = [(None, v) for v in reversed(val._layers)]
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

        if isinstance(val, (dict, list, tuple, set, Onion)):
            val = val
        elif isinstance(val, Script):
            # Don't print scripts if they're not at the top of the dump, otherwise our dumps get
            # massive
            if indent > 1:
                return prefix + f"'{val.script_path}'"
            else:
                val = val.__dict__
        else:
            return prefix + cls._dump_scalar(val, color_code) + suffix

        try:
            return prefix + cls._dump_container_to_flat_str(val, print_id, color_code, max_length - (len(prefix) + len(suffix)), tab, memo) + suffix
        except ValueError:
            return prefix + cls._dump_container_to_str(val, indent, print_id, color_code, max_length, tab, memo) + suffix

# endregion
# --------------------------------------------------------------------------------------------------
# region Utils

class Utils:

    @classmethod
    def reset(cls):
        cls.stat_calls = 0
        cls.hash_calls = 0
        cls.hash_bytes = 0
        cls.hash_time  = 0

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
    def is_template2(text, delims) -> bool:
        if not isinstance(text, str):
            return False
        return any(c in text and delims[c] in text for c in delims)

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
        temp = Utils.hash(id(obj), 0)
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

        #with LogLevel.DEBUG:
        #    Log.log(f"Depfile {filename} contained {len(deplines)} deplines.\n")

        return deplines

    @classmethod
    def commands_to_string(cls, commands):
        commands = Utils.flatten(commands)
        if len(commands) and callable(commands[0]):
            commands = [c.__name__ for c in commands]
        return "; ".join(commands)

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

    log_level   : int  = LogLevel.NORMAL
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
    log_level_in  = LogLevel.NORMAL
    log_level_out = LogLevel.NORMAL # log level we want to appear in the log

    @classmethod
    def reset(cls, flags : Dict):
        cls.log_level   = Expander.get(flags, "log_level")
        cls.log_quiet   = Expander.get(flags, "log_quiet")
        cls.log_verbose = Expander.get(flags, "log_verbose")
        cls.log_debug   = Expander.get(flags, "log_debug")
        cls.log_trace   = Expander.get(flags, "log_trace")
        cls.log_wrap    = Expander.get(flags, "log_wrap")
        cls.log_color   = Expander.get(flags, "log_color")
        cls.log_time    = Expander.get(flags, "log_time")

        cls.con_w         = shutil.get_terminal_size().columns
        cls.time_origin   = time.perf_counter()
        cls.indent_stack  = []
        cls.current_color = -1
        cls.line_buffer   = ""
        cls.match_escapes = re.compile(r"(\x1B.*?m)")


        if cls.log_level is not None:
            if isinstance(cls.log_level, str):
                cls.log_level = LogLevel[cls.log_level.upper()]
            elif isinstance(cls.log_level, int):
                cls.log_level = LogLevel(cls.log_level)
            else:
                raise ValueError(f"Got an unknown log_level '{type(cls.log_level)} = {cls.log_level}'")

        # The individual -T/-D/-V/-Q flags override --log_level, with the 'loudest' flag winning.

        if cls.log_debug:
            cls.log_level = LogLevel.DEBUG
        elif cls.log_verbose:
            cls.log_level = LogLevel.VERBOSE
        elif cls.log_quiet:
            cls.log_level = LogLevel.QUIET

        cls.log_level_in  = cls.log_level
        cls.log_level_out = cls.log_level

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
        ansi = cls.hex_to_ansi(color) if cls.log_color else ""
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
        """
        This tries to convert a path containing potential env variable references and stuff into a
        real path.
        """
        path = os.path.expandvars(path)
        path = pathlib.Path(path).expanduser()
        path = path.resolve(strict = strict)
        return str(path)

    resolve  = tree_map(lambda path : Path.resolve_path(path, strict = True))
    abspath  = tree_map(os.path.abspath)
    basename = tree_map(os.path.basename)
    dirname  = tree_map(os.path.dirname)
    swapext  = tree_map(lambda p, new_ext : os.path.splitext(p)[0] + new_ext)

    isabs    = tree_all(os.path.isabs)
    isfile   = tree_all(os.path.isfile)
    isdir    = tree_all(os.path.isdir)
    exists   = tree_all(os.path.exists)

    # WARNING - Both 'startswith' and 'relpath' below can throw ValueError if there's a mix of
    # abs/rel paths, or if the paths are on different volumes in Windows. We don't handle this yet,
    # but we will need to eventually. If this occurs inside a macro you'll see the exception in the
    # macro expansion trace and the macro will be returned unexpanded. Using 'commonpath' here is
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

        prefix = ""
        try:
            prefix = os.path.commonpath([lhs, rhs])
        except Exception:
            with LogLevel.ERROR, Colors.RED:
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
    def join(lhs, rhs):
        if isinstance(lhs, (list, tuple, set)):
            return [Path.join(lh, rhs) for lh in lhs]
        if isinstance(rhs, (list, tuple, set)):
            return [Path.join(lhs, rh) for rh in rhs]
        return os.path.join(lhs, rhs)

# endregion
# --------------------------------------------------------------------------------------------------
# region Batch

class Batch:

    def __init__(self, flags, top_repo : Repo):
        self.flags        = flags
        self.top_repo     = top_repo
        self.build_tag    = Expander.get(flags, "build_tag")
        self.build_target = Expander.get(flags, "build_target")
        self.build_force  = Expander.get(flags, "build_force")
        self.build_all    = Expander.get(flags, "build_all")
        self.build_dry    = Expander.get(flags, "build_dry")
        self.build_strict = Expander.get(flags, "build_strict")

        self.repos : dict[str, Repo]  = {top_repo.repo_root : top_repo}

    def yield_tasks(self):
        for r in self.repos.values():
            yield from r.yield_tasks()


# endregion
# --------------------------------------------------------------------------------------------------
# region Repo

class Repo:

    def __init__(self, flags : abc.Mapping, top_script : Script):
        self.top_script   = top_script
        self.repo_root    = Expander.get(flags, "repo_root")
        self.build_root   = Expander.get(flags, "build_root")
        self.comp_db_path = Expander.get(flags, "comp_db_path")
        self.stat_db_path = Expander.get(flags, "stat_db_path")

        # Tally up the rebuild reasons for debugging
        self.reasons = Counter()

        # Hash, size, mtime, command for each file in the previous build.
        # Command is only set for output files.
        self.old_stat_db : dict[str, Dict] = {}

        # Stats accumulated during the build after a task is initialized but before it has run.
        # Compared with old_stat_db entries to determine if a task needs a rebuild.
        self.mid_stat_db : dict[str, Dict] = {}

        self.scripts : dict[str, Script]  = {top_script.script_path : top_script}

    # ----------------------------------------------------------------------------------------------

    def yield_tasks(self):
        for s in self.scripts.values():
            yield from s.yield_tasks()

    # ----------------------------------------------------------------------------------------------

    def load_stat_db(self) -> Dict:
        result = {}

        with LogLevel.VERBOSE, Colors.ORANGE:
            Log.log(f"Loading stat db '{self.stat_db_path}'\n")

            if not os.path.isfile(self.stat_db_path):
                Log.log(f"Stat db '{self.stat_db_path}' not found\n")
                return Dict()

            time_a = time.perf_counter()
            result = Utils.load_json(cast(str, self.stat_db_path))
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

    # ----------------------------------------------------------------------------------------------

    def save_stat_db(self):
        if cv_batch.get().build_dry:
            return

        # Gather stats from all completed tasks
        stat_db = {}
        comp_db = {}

        for task in self.top_script.yield_tasks():
            if not task._complete:
                continue

            config = task.config

            for file in Utils.yield_values(task.in_files):
                self.update_stat_db(stat_db, file)

                # Haven't tested this in an IDE, but I think it matches the spec.
                comp_db[file] = {
                    "directory" : config.task_cwd,
                    "command"   : Utils.commands_to_string(config.command),
                    "file"      : file,
                }

            if task.in_depfile:
                deplines = Utils.load_depfile(task.in_depfile, config.depformat, config.task_cwd)
                for file in deplines:
                    self.update_stat_db(stat_db, file)

            for file in Utils.yield_values(task.out_files):
                str_command = Utils.commands_to_string(config.command)
                self.update_stat_db(stat_db, file, str_command)

        # ------------------------------------

        with LogLevel.DEBUG, Colors.ORANGE:
            Log.log(f"┌ Repo {self.repo_root} post-build\n")
            Log.indent(Colors.ORANGE)

        # Dump the stats as JSON.
        time_a = time.perf_counter()
        Utils.save_json(stat_db, self.stat_db_path)
        time_b = time.perf_counter()
        with LogLevel.DEBUG, Colors.ORANGE:
            Log.log(f"Saved {len(stat_db)} stats to {self.stat_db_path}\n")
        with LogLevel.DEBUG, Colors.BLUE:
            Log.log(f"Saving stat db took {time_b - time_a:8.6f} seconds\n")

        time_a = time.perf_counter()
        Utils.save_json(list(comp_db.values()), self.comp_db_path)
        time_b = time.perf_counter()
        with LogLevel.DEBUG, Colors.ORANGE:
            Log.log(f"Saved {len(comp_db)} stats to {self.comp_db_path}\n")
        with LogLevel.DEBUG, Colors.BLUE:
            Log.log(f"Saving comp_db took {time_b - time_a:8.6f} seconds\n")

        with LogLevel.DEBUG, Colors.ORANGE:
            Log.dedent()
            Log.log(f"└ Repo {self.repo_root} done\n")

    # ----------------------------------------------------------------------------------------------

    def rebuild_reason(self, task) -> str:
        """
        Figures out why we have to run a Task, or returns "" if we don't.
        """

        config      = task.confiog
        reasons     = self.reasons
        old_stat_db = self.old_stat_db
        mid_stat_db = self.mid_stat_db

        # ------------------------------------
        # Check the trivial reasons to rebuild

        if config.build_force:
            reasons["forced"] += 1
            return "Target forced to rebuild"

        has_input = any(Utils.yield_values(task.in_files))
        if not has_input:
            reasons["no inputs"] += 1
            return "Always rebuild a target with no inputs"

        has_output = any(Utils.yield_values(task.out_files))
        if not has_output:
            reasons["no outputs"] += 1
            return "Always rebuild a target with no outputs"

        # ------------------------------------

        for filename in Utils.yield_values(task.out_files):
            if not Path.exists(filename):
                reasons["output missing"] += 1
                return f"Output file missing: {filename}"

            if filename not in old_stat_db:
                # I'm not sure we can test this, we probably get hit by other checks before we get
                # here.
                reasons["output stat missing"] += 1 # pragma: no cover
                return f"Output stat missing: {filename}"

            old_stat = old_stat_db[filename]
            mid_stat = mid_stat_db[filename]

            assert old_stat is not None
            assert mid_stat is not None

            if old_stat.command != mid_stat.command:
                reasons["command changed"] += 1
                return f"Command used to generate file has changed : {filename} : {old_stat.command} : {mid_stat.command}"

        # ------------------------------------

        all_files = task._old_deplines + list(Utils.yield_values(task.in_files))

        for filename in all_files:
            old_stat = old_stat_db[filename]
            mid_stat = mid_stat_db[filename]

            assert old_stat is not None
            assert mid_stat is not None

            if old_stat.st_mtime_ns != mid_stat.st_mtime_ns:
                reasons["mtime mismatch"] += 1
                return f"Mtime mismatch {old_stat.st_mtime_ns} != {mid_stat.st_mtime_ns} for : {filename}"

            if old_stat.st_size != mid_stat.st_size:
                reasons["size mismatch"] += 1
                return f"Size mismatch {old_stat.st_size} != {mid_stat.st_size} for : {filename}"

            if old_stat.hash != mid_stat.hash:
                reasons["hash mismatch"] += 1
                return f"Hash mismatch {old_stat.hash} -> {mid_stat.hash} for : {filename}"

            # Does not need to rebuild based on file stats / hash
            reasons["*hash match"] += 1

        reasons["*task clean"] += 1
        return ""


# endregion
# --------------------------------------------------------------------------------------------------
# region Script

class Script:

    def __init__(self, flags : Dict, code : types.CodeType | None):
        self.flags       = flags
        self.code        = code
        self.script_path = Expander.get(flags, "script_path")
        self.script_cwd  = Expander.get(flags, "script_cwd")
        self.task_cwd    = Expander.get(flags, "task_cwd")
        self.build_dir   = Expander.get(flags, "build_dir")

        self.script_path = Path.resolve(self.script_path)
        self.script_cwd  = Path.resolve(self.script_cwd)

        self.module = types.ModuleType(os.path.basename(self.script_path))
        self.module.__file__ = self.script_path
        self.module.hancho   = hancho  # type: ignore
        self.module.flags    = flags # type: ignore

        self.loaded   = False  # true once the script has finished its exec()
        self.tasks    = []     # all tasks created by this script

    # ------------------------------------

    def yield_tasks(self):
        yield from self.tasks

    # ------------------------------------

    def exec(self, repo, build):
        if self.loaded or self.code is None:
            return
        with chdir(self.script_cwd):
            token_build  = cv_batch.set(build)
            token_repo   = cv_repo.set(repo)
            token_script = cv_script.set(self)
            try:
                Log.indent(Colors.ORANGE)
                exec(self.code, self.module.__dict__)
                self.loaded = True
            except (Loader.Abort, Loader.EarlyOut):
                pass
            except Loader.Fail as fail:
                raise RuntimeError(f"Script failed : {self.script_path}") from fail
            finally:
                Log.dedent()
                cv_batch.reset(token_build)
                cv_repo.reset(token_repo)
                cv_script.reset(token_script)

    # ----------------------------------------------------------------------------------------------

    def __repr__(self):
        return Dumper.dump_to_str("Script", self.__dict__, print_id = True, color_code = True)

# endregion
# --------------------------------------------------------------------------------------------------
# region Task
# Task object + bookkeeping

class Task:

    @classmethod
    def reset(cls):
        cls.id_counter : int = 0
        cls.tasks_enabled : int = 0

    class FAILED(Exception):    pass
    class CANCELLED(Exception): pass
    class SKIPPED(Exception):   pass
    class BROKEN(Exception):    pass

    # ----------------------------------------------------------------------------------------------

    def __init__(self, *args, **kwargs):

        self.batch  = cv_batch.get()
        self.repo   = cv_repo.get()
        self.script = cv_script.get()

        self.script.tasks.append(self)

        # The task's 'raw' config contains everything passed in to hancho.Task(), but no templates
        # are expanded.

        self.raw_config = types.MappingProxyType(Dict(*args, **kwargs))

        # The task's 'cooked' config contains only the mandatory fields needed to run the command,
        # all fully expanded. It is expected that build scripts will need to read task.(raw_)config
        # in order to implement task callbacks, so this field is not underscore-prefixed.

        self.config = Dict(
            name=None,
            desc=None,
            command=None,
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
        self.input_tasks = [v for v in Utils.yield_values(self.raw_config) if isinstance(v, Task)]

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
        self._job_size = 0
        self._complete = False

        # Auto-start the task if it was created dynamically during the build.
        if Utils.in_event_loop():
            self._enabled = True
            Task.tasks_enabled += 1
            self.create_aio_task()

    # ----------------------------------------------------------------------------------------------
    # Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
    # Dicts make deep copies and we want dicts to store Tasks, so we work around it by making
    # Tasks just return themselves when copied.

    def __copy__(self):
        return self

    def __deepcopy__(self, _):
        return self

    def __repr__(self):
        return Dumper.dump_to_str(key = "Task", val = self)

    # ----------------------------------------------------------------------------------------------
    # Log helper that adds the [ NN/ XX] tag before the log line.

    def log(self, message : str):
        for line in message.splitlines(keepends=True):
            with Colors.LIME:
                if not Log.line_buffer:
                    Log.log(f"[{self._task_id:3d}/{Task.tasks_enabled:3d}] ")
            Log.log(line)

    # ----------------------------------------------------------------------------------------------

    def enable_task(self):
        if not self._enabled:
            self._enabled = True
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
        for v in self.input_tasks:
            v.enable_task()

    # ----------------------------------------------------------------------------------------------

    def update_stats(self):
        script = cv_script.get()
        repo   = script.repo
        config = self.config

        # If there's a depfile from a previous build, load it so we can use it below.
        if self.in_depfile:
            self._old_deplines = Utils.load_depfile(
                self.in_depfile, config.depformat, config.task_cwd
            )
            for file in self._old_deplines:
                if os.path.exists(file):
                    repo.update_stat_db(file)
                else:
                    raise AssertionError(f"Could not find {file}")

        for file in Utils.yield_values(self.in_files):
            assert os.path.exists(file)
            if os.path.exists(file):
                repo.update_stat_db(file)

        for file in Utils.yield_values(self.out_files):
            if os.path.exists(file):
                str_command = Utils.commands_to_string(config.command)
                repo.update_stat_db(file, str_command)

    # ----------------------------------------------------------------------------------------------
    # Async task entry point

    async def task_top(self):
        Task.id_counter += 1
        self._task_id = Task.id_counter

        task   = self
        config = task.config
        repo   = cv_repo.get()
        batch  = cv_batch.get()

        try:
            # Await all tasks in our input fields and then flatten them.
            await task.await_inputs()

            # Update mtime/hash for all input and output files in this task if they exist.
            task.update_stats()

            # Expand all mandatory fields in the raw config and fix raw file paths.
            task.expand_task()

            # Inputs are ready, templates are expanded, time to run the task.
            task.sanity_check()

            # Dry runs early out after the task is initialized but before we do .exists() checks or
            # run any commands.
            if batch.build_dry:
                return

            # Paths updated. See if we need to rebuild our outputs.
            task._reason = repo.rebuild_reason(task)
            if not task._reason:
                raise Task.SKIPPED(f"Task is up-to-date: '{config.name}' : '{config.desc}'")

            # Wait for enough jobs to free up to run this task.
            task._job_size = await Runner.acquire(config.job_size)

            # OK, let's go!
            await task.task_main()

            # And
            return task.out_files

        except asyncio.CancelledError as ex:
            with LogLevel.VERBOSE:
                task.log(f"<asyncio.CancelledError {ex}>\n")
            task._error = ex
        except Task.BROKEN as ex:
            task.log_exception("Task broken!", ex)
            task._error = ex
        except Task.FAILED as ex:
            task.log_exception("Task failed!", ex)
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
            Runner.release(task._job_size)

        raise task._error

    # ----------------------------------------------------------------------------------------------

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
                self._error = Task.CANCELLED(f"Task is cancelled: '{self.raw_config['name']}' : '{self.raw_config['desc']}'")
                raise self._error from ex

    # ----------------------------------------------------------------------------------------------

    def expand_task(self):
        config = self.config
        with LogLevel.DEBUG:
            self.log("Task config before expand:\n")
            self.log(str(self.raw_config) + "\n")


        # We wrap the task config in an onion and then tack the 'expanded' dict onto it. Then we
        # expand all the mandatory fields into 'expanded', which makes onion lookups during
        # expansion check 'expanded' first to see if it contains an already-expanded copy of the
        # field.

        config.task_cwd    = Expander.get(self.raw_config, "task_cwd")
        config.build_force = Expander.get(self.raw_config, "build_force")
        config.depformat   = Expander.get(self.raw_config, "depformat")
        config.job_size    = Expander.get(self.raw_config, "job_size")
        config.build_dir   = Expander.get(self.raw_config, "build_dir")
        config.build_dir   = Path.abspath(config.build_dir)

        # Build_dir must be expanded _before_ any io fields.

        # Then we expand all io fields (which could contain build_dir) and fix their paths.
        for field in self.raw_config:
            if not field.startswith("in_") and not field.startswith("out_"):
                continue

            files = [
                val.out_files
                  if isinstance(val, Task)
                    else val
                for val in
                  Utils.yield_values(self.raw_config[field])
            ]

            files = Utils.flatten(files)
            files = Expander._expand(files, self.raw_config)
            files = self.fix_paths(field, files)

            if field == "in_depfile":
                # Tasks should have at most one depfile.
                if len(files) > 1:
                    ex = Task.BROKEN(f"Tasks can't have more than one dependency file! - {files}")
                    self.log_exception("Task broken!", ex)
                    self._error = ex
                    raise ex
                self.in_depfile = cast(str, files[0])
            elif field.startswith("in_"):
                self.in_files[field] = files
            elif field.startswith("out_"):
                self.out_files[field] = files

            self.config[field] = files[0] if len(files) == 1 else files

        # And finally we expand name/desc/command, which can contain file paths.
        config.command = Utils.flatten(Expander.get(self.raw_config, "command"))
        config.desc    = Expander.get(self.raw_config, "desc")
        config.name    = Expander.get(self.raw_config, "name")

        with LogLevel.DEBUG:
            self.log("Task config after expand:\n")
            self.log(str(self.config) + "\n")

    # ----------------------------------------------------------------------------------------------

    async def task_main(self):
        task   = self
        config = task.config

        # ----------------------------------------
        # Run all the task's commands

        with LogLevel.NORMAL:
            if config.name:
                task.log(f"{config.name}: ")
            task.log(f"{config.desc}\n")

        with LogLevel.VERBOSE, Log.color(0x606060):
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

        with LogLevel.VERBOSE, Log.color(0x606060):
            message  = f"Task took {time_b-time_a:8.6f} sec: "
            if config.name:
                message += f"'{config.name}' - "
            message += f"'{config.desc}'\n"
            task.log(message)

        # ----------------------------------------
        # See if the task wrote all its output files

        for file in Utils.yield_values(task.out_files):
            if not os.path.exists(file):
                raise Task.FAILED(f"Task ran, but output file still missing: {file}")

        # ----------------------------------------
        # Done!

        if task.in_depfile:
            # FIXME why are there two of these now?
            deplines = Utils.load_depfile(task.in_depfile, cast(str, config.depformat), config.task_cwd)
            script = cv_script.get()
            for file in deplines:
                script.update_stat_db(script.mid_stat_db, file)

            task._new_deplines = Utils.load_depfile(
                task.in_depfile, cast(str, config.depformat), config.task_cwd
            )

    # ----------------------------------------------------------------------------------------------

    def fix_paths(self, field, file):
        """
        Input and output file paths in .hancho scripts are declared relative to the directory the
        script is in (stored in the config under 'script_cwd').
        In general we want to run commands from the root of the repo and store output files in
        repo/build, so we need to fix up the paths to match.
        """
        if isinstance(file, (list, set, tuple)):
            return [self.fix_paths(field, f) for f in file]
        if isinstance(file, abc.Mapping):
            return {k:self.fix_paths(field, f) for k, f in file}

        script = cv_script.get()

        # Join script_cwd with the filename to produce an absolute path.
        file = Path.join(script.script_cwd, file)

        # File paths _must_ be abs'd after joining, otherwise they might look like they're under
        # script_dir, but they're not because the paths could have "../../../../.." in them.
        file = Path.abspath(file)

        # Move all outputs under build_dir and ensure their directories exist.
        # Note - This will also move "in_depfile" under build_dir - this is _intentional_ as
        # it's an _output_ from the compiler and is not checked in to the source tree.
        if field.startswith("out_") or field == "in_depfile":
            if not Path.startswith(file, self.config.build_dir):
                file = Path.relpath(file, script.script_cwd)
                file = Path.join(self.config.build_dir, file)

            if not script.repo.build.build_dry:
                os.makedirs(Path.dirname(file), exist_ok=True)

        return file

    # ----------------------------------------------------------------------------------------------
    # Check for all task issues that break the build

    def sanity_check(self):
        task   = self
        script = cv_script.get()
        config = task.config
        batch  = cv_batch.get()

        if not Path.exists(config.task_cwd):
            raise Task.BROKEN(f"Task working directory '{config.task_cwd}' does not exist")

        if not Path.startswith(config.build_dir, script.repo.repo_root):
            raise Task.BROKEN(f"The build dir {config.build_dir} is not under repo.root {config.repo_root}")

        # In order to provide the least amount of bafflement to users, CLI commands execute
        # from task_cwd (which is usually the root of the repo, the most common cwd)
        # and callbacks execute from dir(script_path) (because you expect to be in the same
        # directory as the script when the callback is firing).

        # This means that pre-rel-ified paths can only be rel'd to one of the two cwds, not both.
        # And that means we disallow mixed cli/callback command lists.

        if isinstance(config.command, list):
            for command in config.command:
                if type(command) is not type(config.command[0]):
                    raise Task.BROKEN(f"Commands aren't the same type: {config.command}")

                # Check that task's commands are either strings or callables.
                if not isinstance(command, str) and not callable(command) and command is not None:
                    raise Task.BROKEN(f"Command {command} is not a string or a callable?")

        # In strict mode, we mark a task broken if its command still has curly braces.
        if script.repo.build.build_strict:
            for command in cast(list, config.command):
                if not isinstance(command, str):
                    continue
                blocks = []
                delims = Main.flags['delims']
                Expander._split_template(command, blocks, delims)
                if len(blocks) > 1 or (len(blocks) == 1 and blocks[0][0] == "{"):
                    raise Task.BROKEN("STRICT: Command has curly braces in it")

        # Check that all build files would end up under build_dir
        for file in Utils.yield_values(task.out_files):
            assert Path.isabs(file)
            if not Path.startswith(file, config.build_dir):
                raise Task.BROKEN(f"Path error, output file {file} is not under build dir {config.build_dir}")

        # Check for task collisions
        for file in Utils.yield_values(task.out_files):
            real_file = cast(str, Path.abspath(file))
            if real_file in Loader.real_filenames:
                raise Task.BROKEN(f"TaskCollision: Multiple tasks build {real_file}")
            Loader.real_filenames.add(real_file)

        # Check for missing inputs. We have to check build_dry, as the input files may only exist if
        # we're really running tasks.
        for file in Utils.yield_values(task.in_files):
            if not Path.isabs(file):
                raise Task.BROKEN(f"Somehow we got a non-abs path for an input file - {file}")  # pragma: no cover
            if not Path.exists(file) and not batch.build_dry:
                raise Task.BROKEN(f"Input file missing - {file}")

    # ----------------------------------------------------------------------------------------------

    async def run_command(self, command):
        task   = self
        repo   = cv_repo.get()
        config = task.config

        with LogLevel.VERBOSE, Colors.BLUE:
            task.log(f"{Path.relpath(config.task_cwd, repo.repo_root)}$ {command}\n")

        proc = None
        try:
            # Create the subprocess via asyncio and then await the result.
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd    = config.task_cwd,
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
        script = cv_script.get()
        script_dir = Path.dirname(script.script_path)

        callback_dir = Path.relpath(script_dir, script.repo_root)

        with LogLevel.VERBOSE, Colors.BLUE:
            self.log(f"{callback_dir}$ {command}\n")

        # Callbacks run from the script_dir where they were defined so that relative paths used
        # in the callback will be correct.
        with chdir(script_dir):
            result = command(self)

        # It would seem like we wouldn't have to explicitly unwrap one level of await-ness here,
        # but apparently that's just how Python waitables work.
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
        config = task.config

        with LogLevel.ERROR, Colors.RED:
            Log.log("========================================\n")
            Log.log(message + "\n")
            Log.log("========================================\n")

            Log.log(f"Script    = {script.script_path}:\n")
            Log.log(f"Task      = '{config.name}' : '{config.desc}'\n")
            Log.log(f"os.getcwd = {os.getcwd()}\n")
            Log.log(f"task cwd  = {config.task_cwd}\n")
            Log.log(f"command   = {config.command}\n")
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
# FIXME we broke tracer when changing how expansion works with Onion
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

    def __init__(self, context : Dict | Onion, enter_message, name):
        self.enter_message = f"{enter_message}({name!r})"
        self.name = name
        self.color = None
        self.context = context
        self.result = None
        self.trace = Log.log_trace

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

        self.color = Utils.obj_to_hex(self.context)

        with Log.color(self.color):
            Log.log(f"┌ {self.get_tag(self.context)}." + self.enter_message + "\n")
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
                    message = f"└ {self.name!r} : {type} = {self.get_tag(self.result)}\n"
                else:
                    message = f"└ {self.name!r} : {type} = {self.result!r}\n"

            Log.dedent()
            Log.log(message)

        return False

# endregion
# --------------------------------------------------------------------------------------------------
# region Loader

class Loader:

    class Abort(Exception):    pass # Raised by hancho scripts when they need to stop running due to some error.
    class EarlyOut(Exception): pass # Raised by hancho scripts when they are successful but don't need to do anything else.
    class Fail(Exception):     pass # Script has hit a fatal error

    @classmethod
    def reset(cls):
        cls.match_pointer : re.Pattern = re.compile(r"<(\w+) (\w+) at 0[xX][0-9a-fA-F]+>")
        cls.real_filenames : set[str] = set()
        cls.dedupe : dict[tuple[str, str], Script] = {}
        cls.batches : list[Batch] = []
        #cls.all_repos : dict[str, Repo] = {}
        cls.all_code : dict[str, types.CodeType] = {}
        cls.load_started = False

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def load(cls, flags : Dict, source = None, code = None):

        is_repo     = Expander.get(flags, "is_repo")
        script_path = Expander.get(flags, "script_path")
        script_path = Path.resolve(script_path)

        # --------------------------------
        # Dedupe the load - only scripts with identical real paths and identical configs are
        # deduped. This relies on __repr__ and the fields read by dump_to_str being stable during a
        # build, which they should be in practice.

        config_dump = Dumper.dump_to_str(key = "flags", val = flags)
        config_dump = cls.match_pointer.sub(r"<\1 \2 at 0x...>", config_dump)

        dedupe_key = (script_path, config_dump)
        deduped_script = cls.dedupe.get(dedupe_key, None) #type:ignore

        with LogLevel.VERBOSE:
            if deduped_script:
                with Colors.SKY:
                    Log.log(f"Deduped load of {script_path}\n")
                return deduped_script
            elif is_repo:
                with Colors.TEAL:
                    Log.log(f"REPO : Loading {script_path}\n")
            else:
                with Colors.AQUA:
                    Log.log(f"SCRIPT : Loading {script_path}\n")

        # --------------------------------
        # Not deduped, create a new Script+Module and also a Repo+BuildDB if this script is the
        # root of a new repo.

        parent_repo   = cv_repo.get()
        parent_build  = cv_batch.get()

        delims = Expander.get(flags, "delims")
        assert Path.isabs(script_path) and not Utils.is_template2(script_path, delims)

        if source is None:
            with open(script_path, encoding="utf-8") as file:
                source = file.read()

        if code is None:
            code = compile(source, script_path, "exec", dont_inherit=True)

        child_script = Script(flags, code)

        if is_repo:
            parent_repo = Repo(flags, child_script)
            parent_build.repos[script_path] = parent_repo

        parent_repo.scripts[script_path] = child_script

        # --------------------------------
        # Script created, save to dedupe dict.

        cls.dedupe[dedupe_key] = child_script #type:ignore

        # --------------------------------
        # And run the actual script code

        child_script.exec(parent_repo, parent_build)

        return child_script

    # ----------------------------------------------------------------------------------------------
    # FIXME we should probably not be yielding _all_ tasks, it should probably be per-build at the
    # highest

    @classmethod
    def yield_tasks(cls):
        for batch in cls.batches:
            yield from batch.yield_tasks()

# endregion
# --------------------------------------------------------------------------------------------------
# region Runner

class Runner:

    @classmethod
    def reset(cls, flags):
        cls.flags = flags

        cls.max_jobs   = Expander.get(flags, "max_jobs")
        cls.max_errors = Expander.get(flags, "max_errors")

        cls.core_sem  : asyncio.Semaphore = asyncio.Semaphore(cls.max_jobs)
        cls.core_lock : asyncio.Lock = asyncio.Lock()

        cls.aio_done_queue : asyncio.Queue = asyncio.Queue()
        cls.live_aio_tasks : set[asyncio.Task] = set()

        cls.tasks_awaited : int = 0
        cls.tasks_finished : int = 0
        cls.tasks_broken : int = 0
        cls.tasks_failed : int = 0
        cls.tasks_cancelled : int = 0
        cls.tasks_skipped : int = 0

    # ----------------------------------------------------------------------------------------------

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

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def select_root_tasks(cls):
        batch   = cv_batch.get()
        repo    = cv_repo.get()

        if batch.build_target:
            # Enable all tasks whose name matches the target regex
            # NOTE - We match task.raw_config.name, _not_ the expanded task.config.name.
            # This is because the task _has not initialized yet_, so we have no config.name.
            target_regex = re.compile(batch.build_target)

            for task in Loader.yield_tasks():
                if target_regex.search(task.raw_config.name):
                    task.enable_task()

        elif batch.build_all:
            for task in Loader.yield_tasks():
                task.enable_task()

        else:
            # Enable all tasks that were generated by the top script
            for task in Loader.yield_tasks():
                if task.repo == repo:
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

        # ------------------------------------
        # Create asyncio tasks for all enabled Hancho tasks.

        for task in Loader.yield_tasks():
            if task._enabled:
                task.create_aio_task()

        # ------------------------------------
        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.

        with LogLevel.VERBOSE, Colors.BLUE:
            Log.log("Running tasks...\n")

        while cls.live_aio_tasks and (cls.tasks_broken + cls.tasks_failed) <= Runner.max_errors:
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

        if cls.tasks_broken + cls.tasks_failed > Runner.max_errors:
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
                root = Path.relpath(task.config.build_root, os.getcwd())
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

    flags : abc.Mapping

    # ----------------------------------------------------------------------------------------------
    # INIT

    @classmethod
    def init(cls, flags):
        cls.flags = flags

        Log.reset(flags)
        Expander.reset(flags)
        Utils.reset()
        Task.reset()
        Loader.reset()
        Runner.reset(flags)

        internal_flags  = Dict(flags, script_path = __file__)
        internal_script = Script(internal_flags, code = None)
        internal_repo   = Repo(internal_flags, internal_script)
        internal_build  = Batch(internal_flags, internal_repo)

        Loader.batches.append(internal_build)
        internal_build.repos[__file__] = internal_repo
        internal_repo.top_script = internal_script

        cv_batch.set(internal_build)
        cv_repo.set(internal_repo)
        cv_script.set(internal_script)

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def main(cls):
        # Top-level exception handler just so we can print a big red "SOMETHING BROKE ALL BAD"
        # message if we failed to catch an exception during load/build.
        # The 'except' clause should catch Exception and not BaseException so ctrl-c doesn't get
        # misinterpreted as a Hancho bug.

        Main.banner_start(
            Main.flags['script_path'],
            Main.flags['repo_root'],
        )

        cv_token = None

        try:

            Loader.load_started = True

            # ------------------------------------
            # LOAD

            time_a = time.perf_counter()
            top_options = Dict(Main.flags, is_repo = True)
            top_script = Loader.load(top_options)
            time_b = time.perf_counter()

            cv_token = cv_script.set(top_script)

            with LogLevel.VERBOSE, Colors.BLUE:
                Log.log(f"Loading scripts took {time_b - time_a} seconds\n")

            # ------------------------------------
            # BUILD

            time_a = time.perf_counter()
            run_tool = Main.flags['run_tool']
            result = Runner.run_tool(run_tool) if run_tool else Main.build()
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
    def parse_flags(cls, argv, *args, **kwargs) -> abc.Mapping:

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

        bool_opt = argparse.BooleanOptionalAction

        # Flags
        # fmt: off

        # global
        parser.add_argument('-o', "--opt_file",     metavar = "(path)",    type=str,       help="File containing a Python literal that will be used as additional options")
        parser.add_argument(      "--run_tool",     metavar = "(tool)",    type=str.strip, help="Run a subtool.")
        parser.add_argument(      "--delims",       metavar = "(delims)",  type=str.strip, help="What characters are used as macro delimiters. Defaults to '{}«»' (don't forget the single quotes)")
        parser.add_argument(      "--depformat",    metavar = "(format)",  type=str.strip, help="Dependency file format (gcc or msvc)")
        parser.add_argument(      "--job_size",     metavar = "(path)",    type=str.strip, help="The default number of jobs (cores) to allocate to each task.")

        # runner
        parser.add_argument(      "--max_errors",   metavar = "(count)",   type=int,       help="The maximum number of task errors we tolerate before abandoning the build")
        parser.add_argument('-j', "--max_jobs",     metavar = "(count)",   type=int,       help="Run a maximum of N jobs in parallel (default=cpu_count).")

        # build ok
        parser.add_argument(      "--build_tag",    metavar = "(name)",    type=str.strip, help="Set the build tag. Tagged builds will have separate subdirectories under the build directory.")
        parser.add_argument('-t', "--build_target", metavar = "(name)",    type=str.strip, help="A regex that selects the targets to build. Defaults to all targets in the top repo.")
        parser.add_argument(      "--build_force",  action = bool_opt,                     help="Rebuild targets even if they're clean.")
        parser.add_argument(      "--build_all",    action = bool_opt,                     help="Build absolutely everything in all build scripts loaded.")
        parser.add_argument(      "--build_dry",    action = bool_opt,                     help="Dry run - Do everything except actually run commands.")
        parser.add_argument(      "--build_strict", action = bool_opt,                     help="Strict mode, slightly more error checking to catch footguns.")

        # repo ok
        parser.add_argument('-r', "--repo_root",    metavar = "(path)",    type=str.strip, help="The location of the repo we're building.")
        parser.add_argument(      "--build_root",   metavar = "(path)",    type=str.strip, help="Directory to put build artifacts in.")
        parser.add_argument(      "--comp_db_path", metavar = "(path)",    type=str.strip, help="Where to put the compilation database (default = {build_dir}/compile_commands.json)")
        parser.add_argument(      "--stat_db_path", metavar = "(path)",    type=str.strip, help="Where to put the stat database (default = {build_dir}/hancho.json)")

        # script ok
        parser.add_argument('-s', "--script_path",  metavar = "(path)",    type=str.strip, help="The .hancho file that starts the build.")
        parser.add_argument(      "--script_cwd",   metavar = "(path)",    type=str.strip, help="Change to this directory before running the top build script.")
        parser.add_argument(      "--task_cwd",     metavar = "(path)",    type=str.strip, help="Directory to run commands in.")
        parser.add_argument(      "--build_dir",    metavar = "(path)",    type=str.strip, help="Per-task build artifact directory. Directory to put build artifacts in.")

        # log ok
        parser.add_argument(      "--log_level",    choices = verbosities,                 help="Manually select verbosity level. 'quiet' = none, 'trace' = maximal spam")
        parser.add_argument('-Q', "--log_quiet",    action = bool_opt,                     help="(same as --log_level=quiet)")
        parser.add_argument('-V', "--log_verbose",  action = bool_opt,                     help="(same as --log_level=verbose)")
        parser.add_argument('-D', "--log_debug",    action = bool_opt,                     help="(same as --log_level=debug)")
        parser.add_argument('-T', "--log_trace",    action = bool_opt,                     help="(same as --log_level=trace)")
        parser.add_argument('-w', "--log_wrap",     action = bool_opt,                     help="Wrap lines around the console instead of clipping them")
        parser.add_argument('-c', "--log_color",    action = bool_opt,                     help="Use color in the log for better readability")
        parser.add_argument(      "--log_time",     action = bool_opt,                     help="Timestamp each log line")
        # fmt: on

        (raw_flags, unrecognized) = parser.parse_known_args(argv)
        raw_flags = vars(raw_flags)
        raw_flags = {k:v for k, v in raw_flags.items() if v is not None}

        # ------------------------------------

        opt_file = raw_flags.get("opt_file")
        if opt_file and os.path.exists(opt_file):
            with open(opt_file) as f:
                opts = json.load(f)
                raw_flags.update(opts)

        delims = raw_flags.get("delims")
        if delims:
            new_delims = {}
            for i in range(0, len(delims), 2):
                new_delims[delims[i]] = delims[i+1]
            raw_flags['delims'] = new_delims

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

        # ------------------------------------

        # fmt: off

        # Delims: Normally you'd use '{' and '}' as macro delimiters, but you can also use '«' and '»'.
        # On Linux, you can type those using control-shift-u a b <enter> and control-shift-u b b <enter>
        # On Windows, use alt-0171 and alt-0187 with the numbers being typed on the numpad while numlock
        # is on.

        defaults = types.MappingProxyType(Dict(
            hancho_path  = __file__,
            hancho_dir   = os.path.dirname(__file__),
            opt_file     = None,
            run_tool     = None,
            delims       = {"{": "}", "«": "»"},
            depformat    = "gcc" if os.name == "posix" else "msvc",
            job_size     = 1,
            max_errors   = 0,
            max_jobs     = os.cpu_count() or 1,
            build_tag    = "",
            build_target = None,
            build_force  = False,
            build_all    = False,
            build_dry    = False,
            build_strict = True,
            repo_root    = "{dirname(abspath(script_path))}",
            build_root   = "{repo_root}/build",
            comp_db_path = "{build_root}/compile_commands.json",
            stat_db_path = "{build_root}/hancho.json",
            script_path  = "build.hancho",
            script_cwd   = "{repo_root}",
            task_cwd     = "{repo_root}",
            build_dir    = "{build_root}/{build_tag}/{relpath(script_cwd, repo_root)}",
            log_level    = LogLevel.NORMAL,
            log_quiet    = False,
            log_verbose  = False,
            log_debug    = False,
            log_trace    = False,
            log_wrap     = False,
            log_color    = True,
            log_time     = True,
        ))

        # fmt: on

        flags = Dict(defaults)
        flags.merge(raw_flags)
        flags.merge(mystery_flags)
        flags.merge(*args, **kwargs)

        return types.MappingProxyType(flags)

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def banner_start(cls, script_path, repo_root):
        with LogLevel.VERBOSE, Colors.LIME:
            Log.log(f"Command line : {" ".join(sys.argv)}\n")
            Log.log(f"Script path  : {script_path}\n")
            Log.log(f"Repo root    : {repo_root}\n")
            #Log.log(f"Opt file     : {opt_file}")
            #if opt_file and not os.path.exists(opt_file):
            #    Log.log(" (not found!)")
            Log.log("\n")

            if Log.log_trace:
                Log.log("Trace mode on\n")
            if Log.log_level_out >= LogLevel.DEBUG:
                Log.log("Debug mode on\n")
            if Log.log_level_out >= LogLevel.VERBOSE:
                Log.log("Verbose mode on\n")

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def build(cls):

        # ------------------------------------
        # This must happen _after_ all repos are loaded (so that if they change repo_root we don't
        # get the old path), but _before_ we build any tasks.

        time_a = time.perf_counter()
        for batch in Loader.batches:
            for repo in batch.repos.values():
                repo.load_stat_db()
        time_b = time.perf_counter()

        with LogLevel.DEBUG, Colors.BLUE:
            Log.log(f"Loading stats took {time_b - time_a:8.6f} seconds\n")

        # ------------------------------------

        Runner.select_root_tasks()

        time_a = time.perf_counter()
        result = Runner.sync_run_tasks()
        time_b = time.perf_counter()

        with LogLevel.VERBOSE, Colors.BLUE:
            Log.log(f"Running {Runner.tasks_awaited} tasks took {time_b - time_a:8.6f} seconds\n")

        # ------------------------------------

        time_a = time.perf_counter()
        for batch in Loader.batches:
            for repo in batch.repos.values():
                repo.save_stat_db()
        time_b = time.perf_counter()

        with LogLevel.DEBUG, Colors.BLUE:
            Log.log(f"Saving stats took {time_b - time_a:8.6f} seconds\n")

        return result

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
            for batch in Loader.batches:
                for repo in batch.repos.values():
                    Log.log(f"Stats for {repo.repo_root}\n")
                    Log.indent(Colors.BLUE)
                    for k, v in repo.reasons.items():
                        Log.log(f"Rebuild reasons {k:13} = {v}\n")
                    Log.dedent()

# endregion
# --------------------------------------------------------------------------------------------------
# region aliases

# These are aliases for methods in Hancho that have been pulled out so they can be used by
# template expansion. This lets you do {flatten(x)} instead of {Utils.flatten(x)} in macros.

path     = Path
abspath  = Path.abspath
basename = Path.basename
swapext  = Path.swapext
resolve  = Path.resolve
relpath  = Path.relpath
dirname  = Path.dirname
cwd      = os.getcwd
flatten  = Utils.flatten
run_cmd  = Utils.run_cmd
weave    = Utils.weave

def build():
    return Main.build()

def load(script_path, *args, **kwargs):
    parent = cv_script.get()
    flags = Dict(parent.flags, *args, kwargs, script_path = script_path, is_repo = False)
    script = Loader.load(flags)
    return script.module

def repo(script_path, *args, **kwargs):
    parent = cv_script.get()
    flags = Dict(parent.flags, *args, kwargs, script_path = script_path, is_repo = True)
    script = Loader.load(flags)
    return script.module

# ----------------------------------------

def log(*args, **kwargs):
    return Log.log(*args, **kwargs)

# ----------------------------------------

def task(*args, **kwargs):
    if len(args) and callable(args[0]):
        # Take hancho.task(callable, ...) and instead of creating a task, collect all the args into
        # a dict and then splat it into the callback.
        merged_config = Dict(*args[1:], kwargs)
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
    flags = Main.parse_flags([], *args, **kwargs)
    Main.init(flags)

# endregion
# --------------------------------------------------------------------------------------------------
# region __main__

hancho = sys.modules[__name__]
sys.modules["hancho"] = hancho

def _start():
    if __name__ == "__main__":
        flags = Main.parse_flags(sys.argv[1:])
        Main.init(flags)
        result = Main.main()
        sys.exit(result)

_start()

# endregion
# --------------------------------------------------------------------------------------------------
