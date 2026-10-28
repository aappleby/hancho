#!/usr/bin/python3
# ruff: noqa: F401
# region Header

"""
Hancho v1.0.0 @ 2026-06-05 - A simple, pleasant build system.

Hancho is a single-file build system that's designed to be dropped into your project folder - there
is no "install" step.

Hancho requires Python 3.12+, which should be fairly universal in 2026.

Hancho's test suite can be found in /tests and can be run via "python -m unittest" in the root of
the Hancho repo.

WARNING - Hancho is NOT A SANDBOX, your build scripts can evaluate arbitrary Python code which
could format your hard drive and email spam to your grandparents. Use responsibly.
"""

# FIXME do a template test with two nested dicts a.b and a.c where a.c contains a template referring to b.d
# FIXME we need tests that actually load .hancho files, repos, nested loads, etc.

from __future__ import annotations

import argparse
import ast
import asyncio
import colorsys
import contextvars
import dataclasses
import functools
import hashlib
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
import typing
from abc import ABCMeta
from collections import Counter, abc, defaultdict
from contextlib import chdir, contextmanager, suppress
from dataclasses import dataclass
from enum import Enum
from itertools import product
from typing import Any, ClassVar, Concatenate, TypeIs, cast

#from functools import partial, wraps

# pyright: reportSelfClsParameterName=false

# endregion
# ==================================================================================================
# region Constants and globals

def is_public(key):
    if not isinstance(key, str) and key is not None:
        print(f"key is {type(key)}={key}")
        breakpoint()
    return key is None or not key.startswith("_")

def is_private(key):
    if not isinstance(key, str) and key is not None:
        print(f"key is {type(key)}={key}")
        breakpoint()
    return key is not None and key.startswith("_")

def dprint(*args, **kwargs):
    print("DBG: ", end="")
    print(*args, **kwargs)

# 'branch' must be sequence/set/mapping in order for this to typecheck:
# a : str = "foo"
# b : tree[str] = a
# c : list[str] = ["bar"]
# d : tree[str] = c


type container[T] = abc.Sequence[T] | abc.Set[T] | abc.Mapping[str, T]
#type branch[LEAF] = LEAF | container[branch[LEAF]]
#type tree[LEAF]   = container[branch[LEAF]]
type branch[LEAF] = LEAF | abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]
type tree[LEAF] = abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]

type task_callback = abc.Callable[[Task], Any]
type item[T] = tuple[int | str | None, T]
type command_type = str | list[str] | task_callback

# endregion
# ==================================================================================================
# region Sentinel

class Sentinel:
    def __init__(sentinel, name):     sentinel.name = name
    def __repr__(sentinel):           return Log.VIOLET + sentinel.name + Log.RESET
    def __bool__(sentinel):           return False
    def __copy__(sentinel):           return sentinel
    def __deepcopy__(sentinel, memo): return sentinel
    def __reduce__(sentinel):         return sentinel.name
    def __dump__(sentinel, key, opts, seen):
        new_opts = dataclasses.replace(opts, print_id = False)
        return Dumper._dump_prefix(key, sentinel, new_opts) + repr(sentinel)

    MISSING : Any
    FOLDED : Any

# Marks a function argument as "not present" for when None would be a valid value.
Sentinel.MISSING = Sentinel("MISSING")
# Placeholder for items we don't want to show in a dump
Sentinel.FOLDED  = Sentinel("FOLDED")

# endregion
# ==================================================================================================
# region Utils

class Utils:

    @classmethod
    def reset(cls):
        pass

    @staticmethod
    def hex_to_ansi(hex):
        """Converts a color hex code into an ANSI escape sequence"""
        r, g, b = ((hex >> 16) & 0xFF, (hex >>  8) & 0xFF, (hex >>  0) & 0xFF)
        return f"\x1B[38;2;{r};{g};{b}m" if hex else "\x1B[0m"

    @staticmethod
    def stringify(variant : Any) -> str:
        """Converts any type into a template-compatible string."""
        result = ""
        for v in Utils.yield_leaves(variant):
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

    # ------------------------------------
    # yield leaves from a tree

    @staticmethod
    def yield_leaves[T](branch : branch[T]) -> abc.Iterator[T]:
        if Utils.is_sequence(branch) or Utils.is_set(branch):
            for v in branch:
                yield from Utils.yield_leaves(v)
        elif Utils.is_mapping(branch):
            for v in branch.values():
                yield from Utils.yield_leaves(v)
        else:
            yield cast(T, branch)

    @staticmethod
    def flatten_tree[T](tree : tree[T]) -> list[T]:
        result = list(Utils.yield_leaves(tree))
        return result

    # ------------------------------------
    # yield key-value pairs from a key-value pair

    @staticmethod
    def yield_items_from_item[T](item : item[T] | None, seen : set | None = None) -> abc.Iterator[item[T]]:
        if item is None:
            return

        if seen is None: seen = set()
        if (vid := hex(id(item[1]))) in seen: return
        seen.add(vid)

        if Utils.is_mapping(item[1]):
            for item2 in item[1].items():
                yield from Utils.yield_items_from_item(item2)
        elif Utils.is_sequence(item[1]) or Utils.is_set(item[1]):
            t = item[1]
            for item2 in enumerate(t):
                yield from Utils.yield_items_from_item(item2)
        else:
            yield item

    # yield key-any pairs from a variant
    @staticmethod
    def yield_items(variant : Any) -> abc.Iterator[item[Any]]:
        if variant is None:
            return
        yield from Utils.yield_items_from_item((None, variant))

    @staticmethod
    def yield_unique_values(variant : Any, seen : set | None = None) -> abc.Iterator[Any]:
        if variant is None:
            return

        if seen is None: seen = set()
        if (vid := hex(id(variant))) in seen: return
        seen.add(vid)

        if Utils.is_mapping(variant):
            for v2 in variant.values():
                yield from Utils.yield_unique_values(v2, seen)
        elif Utils.is_sequence(variant) or Utils.is_set(variant):
            for v2 in variant:
                yield from Utils.yield_unique_values(v2, seen)
        else:
            yield variant

    @staticmethod
    def yield_public_values(variant, seen = None) -> Any:
        if seen is None: seen = set()
        if (vid := hex(id(variant))) in seen: return
        seen.add(vid)

        if Utils.is_mapping(variant):
            for k, v  in variant.items():
                if is_public(k):
                    yield from Utils.yield_public_values(v, seen)
        elif Utils.is_sequence(variant) or Utils.is_set(variant):
            for v in variant:
                yield from Utils.yield_public_values(v, seen)
        else:
            yield variant

    @staticmethod
    def yield_cross_join[T](*args: tree[T]) -> abc.Iterator[tuple[T, ...]]:
        return product(*(Utils.yield_leaves(a) for a in args))

    @staticmethod
    def cross_join[T](lhs_t : tree[T], *args: tree[T]) -> list[tuple[T, ...]]:
        return list(Utils.yield_cross_join(lhs_t, *args))

    @staticmethod
    def yield_cross_join_reduce[T,U](reduce: abc.Callable[[T|U, T], U], *args: tree[T]) -> abc.Iterator[U]:
        yield from (functools.reduce(reduce, t) for t in Utils.yield_cross_join(*args)) # type: ignore

    @staticmethod
    def cross_join_reduce[T](reduce: abc.Callable[[T, T], T], *args: tree[T]) -> list[T]:
        return list(Utils.yield_cross_join_reduce(reduce, *args))

    @staticmethod
    def weave(lhs : tree[str], rhs : tree[str], *args : tree[str]) -> list[str]:
        return Utils.cross_join_reduce(lambda x, y: x + y, lhs, rhs, *args)

    @staticmethod
    def obj_to_float(obj) -> float:
        """
        Generates a "random" float in the range [0.0,1.0) by hashing the object's ID.
        """
        temp = id(obj) & 0xFFFFFFFF
        temp = ((temp ^ (temp >> 19)) * 0x23456789) & 0xFFFFFFFF
        temp = ((temp ^ (temp >> 19)) * 0x23456789) & 0xFFFFFFFF
        temp = ((temp ^ (temp >> 19)) * 0x23456789) & 0xFFFFFFFF
        return (temp & 0xFFFFFFFF) / 0x100000000

    color_map : ClassVar[dict] = {}
    color_cursor : ClassVar[int] = 0

    @staticmethod
    def obj_to_ansi_color(obj) -> str:
        oid = id(obj)
        if oid in Utils.color_map:
            return Utils.color_map[oid]
        phi = 1.61803398875
        hue = (0.3 + Utils.color_cursor * phi) % 1
        Utils.color_cursor += 1

        r, g, b = colorsys.hsv_to_rgb(hue, 0.6, 0.8)
        r, g, b = (int(r * 255), int(g * 255), int(b * 255))
        result = Utils.hex_to_ansi((r << 16) | (g << 8) | b)
        Utils.color_map[oid] = result
        return result

    @staticmethod
    def run_cmd(cmd : str):
        """Runs a console command synchronously and returns its stdout with whitespace stripped."""
        result = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        return result

    @staticmethod
    def is_scalar(v : Any):
        if type(v) is object: return True
        return not (Utils.is_sequence(v) or Utils.is_mapping(v) or Utils.is_set(v))

    @staticmethod
    def is_sequence(v : Any) -> typing.TypeIs[abc.Sequence[Any]]:
        return isinstance(v, abc.Sequence) and not isinstance(v, (str, bytes, bytearray))

    @staticmethod
    def is_mut_sequence(v) -> typing.TypeIs[abc.MutableSequence[Any]]:
        if isinstance(v, (str, bytes, bytearray)):
            return False
        return isinstance(v, abc.MutableSequence)

    @staticmethod
    def is_set(v) -> typing.TypeIs[abc.Set]:
        return isinstance(v, abc.Set)

    @staticmethod
    def is_mut_set(v) -> typing.TypeIs[abc.MutableSet[Any]]:
        return isinstance(v, abc.MutableSet)

    @staticmethod
    def is_mapping(v : Any) -> typing.TypeIs[abc.Mapping[Any, Any]]:
        return isinstance(v, abc.Mapping)

    @staticmethod
    def is_mut_mapping(v) -> typing.TypeIs[abc.MutableMapping[Any, Any]]:
        return isinstance(v, abc.MutableMapping)

    # ------------------------------------

    @staticmethod
    def hex_tag(obj):
        return "0x" + hex(id(obj))[-4:].upper()

    @staticmethod
    def instance_tag(obj):
        result = ""
        if hasattr(obj, "name") and obj.name is not None:
            result += obj.name
            result += ":"
        result += type(obj).__name__
        result += "@"
        result += Utils.hex_tag(obj)
        return result

    @classmethod
    def command_to_string(cls, command: command_type) -> str:
        if isinstance(command, str):
            return command
        elif isinstance(command, list):
            return "; ".join(command)
        elif callable(command):
            return command.__name__
        else:
            raise TypeError(type(command))

    @staticmethod
    def tree_map[X, Y, **P](func: abc.Callable[Concatenate[Any, P], Y]) -> abc.Callable[Concatenate[tree[X], P], tree[Y]]:

        """Turns a mapping function into one that can be applied to arbitrarily nested containers."""

        def repack(obj: tree[X], *args: Any, **kwargs: Any) -> tree[Y]:
            if hasattr(obj, "_make"):
                # Namedtuple special case
                constructor = obj.__dict__["_make"]
            elif isinstance(obj, defaultdict):
                # Default dict special case
                constructor = functools.partial(type(obj), obj.default_factory)
            else:
                # Everything else
                constructor = type(obj)
            return cast(tree[Y], constructor(*args, **kwargs))

        def apply1(obj : branch[X], seen : dict[str, Any], *args : P.args, **kwargs : P.kwargs) -> branch[Y]:
            if isinstance(obj, abc.Mapping):
                map_items = {k : apply1(v, seen, *args, **kwargs) for k, v in obj.items()}
                result = repack(obj, map_items)
            if isinstance(obj, (abc.Set, abc.Sequence)) and not isinstance(obj, (str, bytes, bytearray)):
                list_items = [apply1(v, seen, *args, **kwargs) for v in obj]
                result = repack(obj, list_items)
            else:
                result = func(obj, *args, **kwargs)
            return result

        @functools.wraps(func)
        def apply2(obj: tree[X], *args : P.args, **kwargs : P.kwargs) -> tree[Y]:
            result = apply1(obj, {}, *args, **kwargs)
            return cast(tree[Y], result)

        return staticmethod(apply2)

    @staticmethod
    def tree_all(func):
        """Turns a predicate into one that can be applied to arbitrarily nested containers."""

        @functools.wraps(func)
        def wrapper(variant, *args, **kwargs):
            return all(func(v, *args, **kwargs) for v in Utils.yield_leaves(variant))

        return staticmethod(wrapper)

    @classmethod
    @contextmanager
    def write_and_swap(cls, filename):
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
    def load_json(cls, path):
        with open(path) as contents:
            return json.load(contents)

    @classmethod
    def save_json(cls, variant, path):
        os.makedirs(os.path.dirname(path), exist_ok = True)
        with cls.write_and_swap(path) as file:
            json.dump(variant, file, indent=4, default=lambda x: x.__dict__)
            file.write("\n")

    # When merging Dicts, if two attributes have the same name:
    #     - If they are both dicts, we recursively merge them.
    #     - If they are both lists, we concatenate them.
    #     - Otherwise rightmost non-None wins.

    @staticmethod
    def merge_variant(
        lhs: Any,
        rhs: Any,
        *,
        merge_private: bool = False,
        merge_mappings: bool = True,
        merge_sequences: bool = True,
        merge_sets: bool = True,
        keep_lhs: bool = True,
        keep_rhs: bool = True,
    ):
        if rhs is None:
            return lhs

        ldict = lhs if isinstance(lhs, dict) else lhs.__dict__
        rdict = rhs if isinstance(rhs, dict) else rhs.__dict__
        lkeys = [k for k in ldict if is_public(k) or merge_private]
        rkeys = [k for k in rdict if is_public(k) or merge_private]

        # Prune lhs keys if needed
        for key in (k for k in lkeys if k not in rkeys and not keep_lhs):
            del ldict[key]

        # Merge all lhs+rhs pairs
        for key in (k for k in lkeys if k in rkeys):

            lhs2 = ldict.get(key)
            rhs2 = rdict.get(key)

            if lhs2 is rhs2:
                pass
            elif Utils.is_mut_mapping(lhs2) and Utils.is_mapping(rhs2) and merge_mappings:
                Utils.merge_variant(lhs2, rhs2)
            elif Utils.is_sequence(lhs2) and Utils.is_sequence(rhs2) and merge_sequences:
                ldict[key] = [*lhs2, *rhs2]
            elif Utils.is_set(lhs2) and Utils.is_set(rhs2) and merge_sets:
                ldict[key] = lhs2 | rhs2
            elif rhs2 is not None:
                ldict[key] = rhs2

        # Save rhs keys if needed
        for key in (k for k in rkeys if k not in lkeys and keep_rhs):
            ldict[key] = rdict[key]

        return lhs

    @staticmethod
    def merge_variants(lhs, *args, **kwargs):
        for rhs in [*args, kwargs]:
            Utils.merge_variant(lhs, rhs)

# endregion
# ==================================================================================================
# region Log

class Log:

    """12 half-saturated, 80% value colors evenly spaced around the HSV wheel"""
    RED     = Utils.hex_to_ansi(0xCC6666)
    PINK    = Utils.hex_to_ansi(0xCC6699)
    MAGENTA = Utils.hex_to_ansi(0xCC66CC)
    VIOLET  = Utils.hex_to_ansi(0x9966CC)
    BLUE    = Utils.hex_to_ansi(0x6666CC)
    SKY     = Utils.hex_to_ansi(0x6699CC)
    TEAL    = Utils.hex_to_ansi(0x66CCCC)
    AQUA    = Utils.hex_to_ansi(0x66CC99)
    GREEN   = Utils.hex_to_ansi(0x66CC66)
    LIME    = Utils.hex_to_ansi(0x99CC66)
    YELLOW  = Utils.hex_to_ansi(0xCCCC66)
    ORANGE  = Utils.hex_to_ansi(0xCC9966)

    DBLUE   = Utils.hex_to_ansi(0x5555AA)
    DRED    = Utils.hex_to_ansi(0xAA5555)
    DGREEN  = Utils.hex_to_ansi(0x55AA55)

    GRAY1   = Utils.hex_to_ansi(0x333333)
    GRAY2   = Utils.hex_to_ansi(0x666666)
    GRAY3   = Utils.hex_to_ansi(0x999999)
    GRAY4   = Utils.hex_to_ansi(0xCCCCCC)
    RESET   = Utils.hex_to_ansi(0x000000)
    WHITE   = Utils.hex_to_ansi(0xFFFFFF)

    TRACE    = 0
    DEBUG    = 10
    VERBOSE  = 15
    INFO     = 20
    WARNING  = 30
    ERROR    = 40
    CRITICAL = 50

    def __init__(log, flags, stdout):

        log.level :int        = cast(int, getattr(Log, flags.log_level.upper()))
        log.wrap : bool       = flags.log_wrap
        log.color : bool      = flags.log_color
        log.timestamp : bool  = flags.log_timestamp
        log.width : int       = flags.log_width

        assert type(log.level) is int

        log._time_origin   = time.perf_counter()
        log._indent_stack  = []
        log._line_buffer   = ""
        log._match_escapes = re.compile(r"(\x1B.*?m)")

        if isinstance(stdout, str):
            log._stdout = open(stdout, "w")  # noqa: SIM115
        else:
            log._stdout = stdout

    def indent(log, ansi_color : str = RESET):
        log._indent_stack.append(f"{ansi_color}│{Log.RESET}")

    def dedent(log):
        log._indent_stack.pop()

    @contextmanager
    def indenter(log, level, ansi_color : str = RESET):
        log_level = log.level
        if level >= log_level:
            log.indent(ansi_color)
        yield
        if level >= log_level:
            log.dedent()

    def trace(log, text):
        # Traces do their own color coding
        log._log(Log.TRACE, "", text)

    def debug(log, text):
        log._log(Log.DEBUG, Log.GRAY3, text)

    def verbose(log, text):
        log._log(Log.VERBOSE, Log.GRAY4, text)

    def info(log, text):
        log._log(Log.INFO, "", text)

    def warning(log, text):
        log._log(Log.WARNING, Log.YELLOW, text)

    def error(log, text):
        log._log(Log.ERROR, Log.ORANGE, text)

    def critical(log, text):
        log._log(Log.CRITICAL, Log.RED, text)

    def _log(log, log_level : int, color : str, text : Any):
        if isinstance(text, list):
            for t in text:
                log._log(log_level, color, t)
            return
        elif not isinstance(text, str):
            text = repr(text) + "\n"

        if log_level < log.level:
            return
        if not isinstance(text, str) or len(text) == 0:
            return

        # FIXME str.partition might be easier here
        lines = text.splitlines(keepends=True)
        for line in lines:
            if log._line_buffer == "":
                log._line_buffer += f"[{time.perf_counter() - log._time_origin:8.3f}] " if log.timestamp else ""
                log._line_buffer += "".join(log._indent_stack)

            log._line_buffer += color

            if line[-1] == "\n":
                log._line_buffer += line[:-1]
                log._line_buffer += Log.RESET
                log._line_buffer += "\n"
                log._flush()
            else:
                log._line_buffer += line
                log._line_buffer += Log.RESET

    def _flush(log):
        # Dumps the line buffer to stdout and then clears it.
        if log._line_buffer:
            # If we want a non-colorized log, strip color codes from the line.
            if not log.color:
                log._line_buffer = re.sub(log._match_escapes, "", log._line_buffer)

            # If the line wasn't finished (because we're exiting the app), stick a newline on it.
            if log._line_buffer[-1] != "\n":
                log._line_buffer += "\n"

            if not log.wrap:
                _ = log.width
                log._line_buffer = log._clip_printable(log._line_buffer, log.width)

            log._stdout.write(log._line_buffer)
            log._line_buffer = ""

    def _clip_printable(log, text : str, width : int) -> str:
        """
        Clips a string with embedded escape codes (such as ANSI color codes) so that it fits in
        "width" without breaking the escape codes.

        If the printable portion exceeds width, it will be clipped and capped with "...".
        """
        if len(text) < 3:
            return text

        # We don't want to clip trailing newlines - if one is present, just remember it was there
        # and we'll stick it back on at the end.
        newline = text[-1] == "\n"
        if newline:
            text = text[:-1]

        # Split the text using the escape sequences as separators.
        chunks = log._match_escapes.split(text)

        # Even chunks are printable text, odd chunks are escape sequences.
        # If the printable characters fit on the line, we don't need to clip.

        print_len = sum(len(c) for c in chunks[::2])
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
            result += "\n"

        return result

# endregion
# ==================================================================================================
# region Path

class Path:

    @staticmethod
    def resolve(path):
        """
        This tries to convert a path containing potential env variable references and stuff into a
        real path.
        """
        path = os.path.expandvars(path)
        path = pathlib.Path(path).expanduser()
        try:
            path = path.resolve(strict = True)
        except Exception as _:
            current_script.get().log.error(f"Path.resolve() failed for {path}")
            raise
        return str(path)

    @staticmethod
    def isresolved(p):
        try:
            return p == Path.resolve(p)
        except Exception:
            return False

    @staticmethod
    def swapext(p : str, new_ext : str):
        return os.path.splitext(p)[0] + new_ext

    # WARNING - Both "startswith" and "relpath" below can throw ValueError if there's a mix of
    # abs/rel paths, or if the paths are on different volumes in Windows. We don't handle this yet,
    # but we will need to eventually. If this occurs inside a macro you'll see the exception in the
    # macro expansion trace and the macro will be returned unexpanded. Using "commonpath" here is
    # probably worth it though, as it handles some annoying edge cases.

    @staticmethod
    def startswith(path : str, prefix : str):
        try:
            common = os.path.commonpath([path, prefix])
            return common == prefix
        except Exception as ex:
            raise ex

    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with "commonpath" and "removeprefix".

    @staticmethod
    def relpath(lhs : str, rhs : str) -> str:
        lhs = os.path.abspath(lhs)
        rhs = os.path.abspath(rhs)

        prefix = os.path.commonpath([lhs, rhs])

        if lhs == rhs:
            result = "."
        elif prefix != rhs:
            result = lhs
        else:
            result = lhs.removeprefix(prefix + os.sep)

        return result

    @staticmethod
    def isabs(p : str) -> bool : return os.path.isabs(p)

    @staticmethod
    def isfile(p : str) -> bool : return os.path.isfile(p)

    @staticmethod
    def isdir(p : str) -> bool : return os.path.isdir(p)

    @staticmethod
    def abspath(p : str) -> str : return os.path.abspath(p)

    @staticmethod
    def normpath(p : str) -> str : return os.path.normpath(p)

    @staticmethod
    def basename(p : str) -> str : return os.path.basename(p)

    @staticmethod
    def dirname(p : str) -> str : return os.path.dirname(p)

    @staticmethod
    def split(p : str) -> tuple[str, str] : return os.path.split(p)

    @staticmethod
    def join(*p : str) -> str : return os.path.join(*p)

    @staticmethod
    def exists(p : str) -> bool : return os.path.exists(p)



    # ==============================================================================================
    # These functions wrap the os.path.* functions so that they work on arbitrary trees

    @staticmethod
    def relpath_t(lhs, rhs) -> tree[str]:
        if isinstance(rhs, (list, tuple, set)):
            return [Path.relpath_t(lhs, rh) for rh in rhs]
            #result = [Path.relpath(lhs, rh) for rh in Utils.yield_leaves(rhs)]
            #return cast(list[str], result)

        if not os.path.isabs(lhs):
            lhs = os.path.abspath(lhs)
        if not os.path.isabs(rhs):
            rhs = os.path.abspath(rhs)

        prefix = os.path.commonpath([lhs, rhs])

        if lhs == rhs:
            result = "."
        elif prefix != rhs:
            result = lhs
        else:
            result = lhs.removeprefix(prefix + os.sep)

        return result

    startswith_t = Utils.tree_all(startswith)

    #@staticmethod
    #def startswith_t(variant : tree[str], prefix : str):
    #    return all(Path.startswith(path, prefix) for path in Utils.yield_leaves(variant))

    #@staticmethod
    #def join_t(*args: tree[str]) -> list[str] | str:
    #    lists  = list(Utils.yield_cross_join(*args))
    #    joined = [Path.join(*cross) for cross in lists]
    #    normed = [Path.normpath(path) for path in joined]
    #    return normed if len(normed) > 1 else normed[0]

    resolve_t = Utils.tree_map(lambda path : Path.resolve(path))

    abspath_t  = Utils.tree_map(abspath)
    normpath_t = Utils.tree_map(normpath)
    basename_t = Utils.tree_map(basename)
    dirname_t  = Utils.tree_map(dirname)

    swapext_t  = Utils.tree_map(swapext)

    exists_t   = Utils.tree_all(exists)

    isabs_t    = Utils.tree_all(isabs)
    isfile_t   = Utils.tree_all(isfile)
    isdir_t    = Utils.tree_all(isdir)
    isresolved_t = Utils.tree_all(isresolved)




# endregion
# ==================================================================================================
# region Scope/Dict/Tool

# A Scope is just some mapping-like object that can be used in template expansion.
# Note that it only exposes _public_ fields through iter/len

# FIXME this breaks if we do Scope(abc.Mapping)?

class Scope:
    def get(scope, key, default = None):
        return scope.__dict__.get(key, default)

    def expand(scope, template, recursive : bool = False) -> Any:
        return Expander._expand(template, scope, recursive)

    def __contains__(scope, key) -> bool:
        return is_public(key) and key in scope.__dict__

    def __getattr__(scope, key) -> Any:
        return object.__getattribute__(scope, key)

    def __getitem__(scope, key : str) -> Any:
        if is_private(key):
            raise KeyError(key)
        return scope.__dict__[key]

    def __len__(scope):
        return sum(1 for _ in scope)

    def __iter__(scope):
        yield from filter(is_public, scope.__dict__)

    def __dump__(self, key, opts : Dumper.Opts, seen):
        new_fold = [*opts.fold, contextvars.Context, asyncio.Task, Project, Repo, Script, Flags, Log, aliases]
        new_opts = dataclasses.replace(opts, fold = new_fold)
        return Dumper._dump_vector(key, self, self.__dict__, new_opts, seen)

    # This makes traces hard to read because we unfold _every_ scope that we read
    #def __repr__(self):
    #    return Dumper.dump(self)

# ====================================
# A Dict is a Scope that can be used as an abc.MutableMapping.

class Dict(Scope, abc.MutableMapping):

    def __init__(self, *args, **kwargs):
        Utils.merge_variants(self, *args, **kwargs)

    def __setitem__(scope, key, val):
        scope.__dict__[key] = val

    def __delitem__(scope, key):
        del scope.__dict__[key]

    # Fill-in-the-blank (or override what's there): Merges lhs and args into a new Dict, keeping
    # only keys that were already in lhs. For example, if you have a Dict that contains
    # "out_bin" and you merge it with "compile_cpp", Hancho will complain that "out_bin" is missing
    # - it sees both "out_obj" and "out_bin" and assumes the task produces both. If you do
    # compile_cpp.fill(...), "out_bin" does not get added to compile_cpp.

    def fill(self, *args, **kwargs):
        lhs = Dict(self)
        for rhs in (*args, kwargs):
            Utils.merge_variant(lhs, rhs, keep_rhs = False)



# ====================================
# A Tool is just a callable alias for Dict to make build scripts more readable.

class Tool(Dict):
    def __call__(self, *args, **kwargs):
        return Task(self, *args, **kwargs)

# endregion
# ==================================================================================================
# region Flags

class Flags(Scope):
    def __init__(flags, *args, **kwargs):
        flags.name        = "build"
        flags.file        = "build.hancho"
        flags.root : str  = "{dirname(path)}"
        flags.targets     = []

        flags.build_dir   = "build"
        flags.build_tag   = ""
        flags.build_force = False
        flags.build_all   = False
        flags.build_dry   = False

        flags.strict      = True
        flags.max_errors  = 0
        flags.max_jobs    = os.cpu_count() or 1
        flags.log_level     = "info"
        flags.log_wrap      = False
        flags.log_color     = True
        flags.log_timestamp = True
        flags.log_width     = shutil.get_terminal_size().columns
        #flags.log_width = 120

        Utils.merge_variants(flags, *args, **kwargs)

        flags.path = Path.resolve(flags.file)
        flags.name = flags.expand(flags.name)
        flags.file = flags.expand(flags.file)
        flags.root = flags.expand(flags.root)


        if flags.root is None:
            flags.root = Path.dirname(flags.path)
        else:
            flags.root = Path.resolve(flags.root)


    @staticmethod
    def parse(*argv) -> Flags:

        if len(argv) > 0 and "hancho.py" in argv[0]:
            argv = argv[1:]

        desc = textwrap.dedent("""
        ================================================================================
                        Hancho is a simple, pleasant build system
        ================================================================================
        """)

        class MyFormatter(argparse.RawDescriptionHelpFormatter, argparse.ArgumentDefaultsHelpFormatter):
            pass

        parser = argparse.ArgumentParser(
            description=desc,
            formatter_class=MyFormatter
        )

        # ------------------------------------
        # fmt: off

        levels = ["trace", "debug", "verbose", "info", "warning", "error", "critical"]

        def str_to_bool(v):
            if v.lower() in ("yes", "true", "t", "y", "1"): return True
            if v.lower() in ("no", "false", "f", "n", "0"): return False
            raise argparse.ArgumentTypeError("Not a bool")

        parser.add_argument("targets", nargs="*", help="The names or partial names of targets to build")

        parser.add_argument("-o", "--opts",          type=str,         help="File containing JSON that will be used as additional options")
        parser.add_argument(      "--name",          type=str,         help="Name of the whole build project, if you don't want it to be <project>")
        parser.add_argument("-f", "--file",          type=str,         help="Path to the .hancho file that starts the build.")
        parser.add_argument("-r", "--root",          type=str,         help="The top script runs in this directory.")
        parser.add_argument(      "--build_dir",     type=str,         help="Build artifacts go in this directory at the top of each repo.")
        parser.add_argument(      "--build_tag",     type=str,         help="Tagged builds will have separate subdirectories under the build directory.")
        parser.add_argument(      "--build_force",   type=str_to_bool, help="Rebuild targets even if they're clean.")
        parser.add_argument("-a", "--build_all",     type=str_to_bool, help="Build every task in every repo.")
        parser.add_argument("-n", "--build_dry",     type=str_to_bool, help="Dry run - Do everything except actually run commands.")
        parser.add_argument(      "--strict",        type=str_to_bool, help="Strict mode, slightly more error checking to catch footguns.")
        parser.add_argument(      "--max_errors",    type=int,         help="The maximum number of task errors we tolerate before abandoning the build")
        parser.add_argument("-j", "--max_jobs",      type=int,         help="Run a maximum of N jobs in parallel.")
        parser.add_argument("-l", "--log_level",     choices=levels,   help="Select verbosity level.")
        parser.add_argument("-w", "--log_wrap",      type=str_to_bool, help="Wrap lines around the console instead of clipping them")
        parser.add_argument("-c", "--log_color",     type=str_to_bool, help="Use color in the log for better readability")
        parser.add_argument(      "--log_timestamp", type=str_to_bool, help="Timestamp each log line")
        parser.add_argument(      "--log_width",     type=int,         help="Max width (in characters) of the log if not wrapping lines")

        # fmt: on

        flag_dict = Dict()
        (argv_vars, unrecognized) = parser.parse_known_args(argv)

        # ------------------------------------
        # Load flags from opt_file if present

        opt_file_name = argv_vars.__dict__.pop("project.opts", None)
        #loaded_opts = False
        if opt_file_name and os.path.exists(opt_file_name):
            with open(opt_file_name) as f:
                try:
                    opt_file = json.load(f)
                    Utils.merge_variant(flag_dict, opt_file)
                except Exception as _:
                    print(f"Opt file {opt_file_name!r} invalid!\n")
                    traceback.print_exc()

        # ------------------------------------
        # Add argv_vars

        def set_by_path(lhs, key, val):
            parts = key.split(".")
            for part in parts[:-1]:
                if part not in lhs:
                    lhs[part] = Dict()
                lhs = lhs[part]
            lhs[parts[-1]] = val

        for k, v in argv_vars.__dict__.items():
            if v is not None:
                set_by_path(flag_dict, k, v)

        # ------------------------------------
        # Unrecognized command line flags also become config fields if they are flag-like.
        # Naked flags become {"name":True}, number types become numbers, "true" and "false"
        # become bools (regardless of capitalization), everything else becomes a string.

        for chunk in unrecognized:
            if match := re.match(r"--([^=]+)=(.+)", chunk):
                key = match.group(1)
                val = match.group(2)
                val = val.lower()

                try:
                    val = str_to_bool(val)
                except argparse.ArgumentTypeError:
                    with suppress(NameError, ValueError, SyntaxError):
                        val = ast.literal_eval(val)

                set_by_path(flag_dict, key, val)

        return Flags(flag_dict)

# endregion
# ==================================================================================================
# region Expander

# Expander _must_ be a mapping in order to go in eval()

class Expander(Scope, abc.Mapping):
    # Hancho's text expansion system.
    #
    # WARNING - Again, Hancho is NOT A SANDBOX. Expander is the part that evaluates the arbitrary
    # Python code that then formats your hard drive and sends spam to all your coworkers.
    #
    # Expander works similarly to Python's F-strings, but with quite a bit more power. The code
    # here requires some explanation.
    #
    # We do not necessarily know in advance how the users will nest strings, macros, callbacks,
    # etcetera. Text expansion therefore requires dynamic-dispatch-type stuff to ensure that we
    # always end up with flat strings.
    #
    # The result of this is that the functions here are mutually recursive in a way that can lead
    # to confusing callstacks, but that should handle every possible case of stuff inside other
    # stuff.
    #
    # Also - TEFINAE - Text Expansion Failure Is Not An Error. Dicts can contain macros that are
    # not expandable by that Dict. This allows nested dicts to contain templates that can only be
    # expanded an outer Dict, and things will still Just Work.

    def __init__(expander, scope : Scope, recursive : bool):
        expander.scope = scope
        expander.recursive = recursive

    @classmethod
    def wrap(cls, scope, recursive : bool):
        assert not isinstance(scope, cls)
        return cls(scope, recursive)

    # ====================================
    # Expander _must_ implement __getattr__ so that macros like {script.foo} work.

    def __getattr__(expander, key) -> Any:
        try:
            return expander.internal_get(key)
        except KeyError as ex:
            raise AttributeError from ex

    # ====================================

    def __getitem__(expander, key : str) -> Any:
        return expander.internal_get(key)

    def __iter__(expander):
        raise AssertionError()

    def __len__(expander):
        raise AssertionError()

    # ====================================

    @staticmethod
    def scope_walk(scope, key : str, default = Sentinel.MISSING) -> Any:
        if (result := scope.get(key, Sentinel.MISSING)) is not Sentinel.MISSING:
            return result
        elif key in current_script.get():
            return current_script.get()[key]
        elif key in aliases:
            return aliases[key]
        elif default is not Sentinel.MISSING:
            return default

        raise KeyError(key)

    def internal_get(expander, key : str):
        Tracer.trace_start(expander.scope, "get", key)
        result = Expander.scope_walk(expander.scope, key)

        if isinstance(result, Scope):
            # have to do this so that "read nested c first" resolves in the dest dict first
            result = expander.wrap(result, object.__getattribute__(expander, "recursive"))
        else:
            result = expander.scope.expand(result, recursive = True)

        Tracer.trace_end(expander.scope, "get", key, result)
        return result

    # Trivial classes just so we can distinguish between literal strings and macro strings without
    # having to do regex stuff every time.
    class Literal(str): pass
    class Macro(str):   pass
    class Expr(str):    pass

    class Blocks(list[Literal | Macro]): pass

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

    # ==============================================================================================

    @classmethod
    def _expand(cls, var : Any, scope, recursive : bool) -> Any:
        # Bail out if we've recursed too many times.
        old_depth = cls.cv_depth.get()
        if old_depth > cls.MAX_DEPTH:
            raise RecursionError(f"Expansion failed to terminate after {old_depth} recursions: {var!r}")
        cls.cv_depth.set(old_depth + 1)

        try:
            while True:

                # Filter out everything we can't expand directly.
                # This _must_ go inside the while True loop, as we can have macros that expand into
                # containers that we then need to recurse into.
                if Utils.is_mut_mapping(var) and recursive:
                    for k, v in var.items():
                        var[k] = cls._expand(v, scope, recursive)
                    return var
                elif Utils.is_mut_sequence(var) and recursive:
                    for i, v in enumerate(var):
                        var[i] = cls._expand(v, scope, recursive)
                    return var
                elif Utils.is_mut_set(var) and recursive:
                    var = list(var)
                    for i, v in enumerate(var):
                        var[i] = cls._expand(v, scope, recursive)
                    return set(var)
                elif isinstance(var, Sentinel):
                    # Trying to expand a sentinel is a failure.
                    raise AssertionError(f"Tried to expand sentinel {var}")
                elif not isinstance(var, str):
                    return var


                old_var = var
                blocks = cls._split_text(var)
                # Stop if we have nothing in the string to expand.
                if len(blocks) == 0 or (len(blocks) == 1 and isinstance(blocks[0], cls.Literal)):
                    return var

                # If there's only one macro block, eval it.
                if len(blocks) == 1 and isinstance(block := blocks[0], cls.Macro):
                    var = cls._eval_macro(block, scope, recursive)
                    # Stop if we're at a fixed point.
                    if old_var == var:
                        return var
                    continue

                # Otherwise we're doing a template expansion.
                try:
                    Tracer.trace_start(scope, "expand", var)
                    for i, b in enumerate(blocks):
                        if isinstance(b, cls.Macro):
                            blocks[i] = cls._expand(b, scope, recursive)
                    var = "".join(Utils.stringify(b) for b in blocks)

                    # Stop if we're at a fixed point.
                    if old_var == var:
                        return var

                    # Stop if we hit a non-string
                    if not isinstance(var, str):
                        return var
                finally:
                    Tracer.trace_end(scope, "expand", old_var, var)

        finally:
            cls.cv_depth.set(old_depth)
            if old_depth == 0:
                # We just finished an expansion - reset the eval budget
                cls.cv_evals.set(0)

        return var

    # ==============================================================================================
    # Note that we do _not_ suppress any BaseExceptions - they _must_ be propagated up to
    # callers. As of Python 3.11, this includes asyncio.CancelledError.

    # IMPORTANT IMPORTANT IMPORTANT
    # If you can't eval a macro, you return it unchanged.

    # TEFINAE : Template Expansion Failure Is Not An Error. Same idea as SFINAE in C++
    # - we don't fail on expansion failure so we can retry somewhere/somewhen else.

    @classmethod
    def _eval_macro(cls, var : str, scope, recursive : bool) -> Any:
        # Bail out if we've done too many evals already.
        old_evals = cls.cv_evals.get()
        if old_evals >= cls.MAX_EVALS:
            raise RecursionError(f"Expansion failed to terminate after {old_evals} evals: {var!r}")
        cls.cv_evals.set(old_evals + 1)

        result = var
        try:
            Tracer.trace_start(scope, "eval", var[1:-1])
            result = eval(var[1:-1], {}, cls.wrap(scope, recursive))
        except RecursionError:
            raise
        except Exception as ex:
            _ = ex
            # If you uncomment this line, things will be super spammy.
            #dprint(f"##########\neval failed because >{ex}<\n##########\n")
            pass
        finally:
            Tracer.trace_end(scope, "eval", var[1:-1], result)
            pass

        if isinstance(result, Expander):
            result = result.scope

        return result

    # ==============================================================================================

    @classmethod
    def is_macro(cls, text):
        if not isinstance(text, str):
            return False
        blocks = cls._split_text(text)
        return len(blocks) == 1 and isinstance(blocks[0], cls.Macro)

    @classmethod
    def is_template(cls, text):
        if not isinstance(text, str):
            return False
        blocks = cls._split_text(text)
        return len(blocks) > 1

    @classmethod
    def _split_text(cls, text : str) -> Blocks:
        """
        Extracts all innermost delimited spans from a block of text and produces a list of string
        literals and macros. Note that we're not handling "escaped" delimiters, instead we just
        translate "«»" into "{}" right before we run a command
        """

        out_blocks = cls.Blocks()
        cursor = 0
        idelim = -1
        macros = 0

        if "«" in text:
            ldelim = "«"
            rdelim = "»"
        else:
            ldelim = "{"
            rdelim = "}"

        for i, c in enumerate(text):
            if c == ldelim:
                idelim = i
            elif c == rdelim and idelim >= 0:
                if cursor < idelim:
                    out_blocks.append(cls.Literal(text[cursor:idelim]))
                out_blocks.append(cls.Macro(text[idelim:i+1]))
                macros += 1
                cursor = i + 1
                idelim = -1

        if cursor < len(text):
            out_blocks.append(cls.Literal(text[cursor:]))

        return out_blocks

# endregion
# ==================================================================================================
# region Dumper

class Dumper:
    """
    Hancho's pretty-printer for various types. Note that this is also used for script deduping:
    if you load "my/app/tools/stuff.hancho" multiple times but the configurations you gave it
    were identical, you should get one copy of the "stuff" script instead of two.

    As long as you're not doing something bizarre with configs or changing the dumper in the
    middle of a build, the resulting strings should be stable enough to use for deduping.
    """

    # These types don't get dumped because they're not really dumpable.
    opaque_types : ClassVar[dict[Any, str]] = {
        #types.BuiltinFunctionType : "<builtin>",
        #types.ModuleType          : "<module>",
        types.GeneratorType       : "<generator>",
        type                      : "type",
    }

    # These types don't need a type annotation when dumped.
    base_types = (
        Enum,
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
        #bytes,
        #bytearray,
        range,
        type(None),
        Sentinel,
        *opaque_types.keys(),
    )

    match_pointer : re.Pattern = re.compile(r"0[xX][0-9a-fA-F]{4,16}")

    @classmethod
    def depointer(cls, text):
        text = Dumper.match_pointer.sub("0x...", text)
        return text

    @dataclass
    class Opts:
        cur_depth : int = 0
        max_depth : int = 3
        indent_stack2 : list[str] = dataclasses.field(default_factory=list)  # Any container fields with these names will _not_ be recursively dumped
        fold : list[str] = dataclasses.field(default_factory=list)  # Any container fields with these names will _not_ be recursively dumped
        print_id : bool = True
        color_code : bool = True
        dump_private : bool = True
        tab : int = 4
        len : int = 80
        width : int = 80
        flat : bool = False

    class LineTooLong(Exception):
        pass

    @classmethod
    def dump(
        cls,
        val,
        cur_depth=0,
        max_depth=3,
        fold = None,
        print_id=True,
        color_code=False,
        dump_private=True,
        width=80,
        len=0,
        tab=4,
        indent_level = 0
    ):
        fold = fold or []
        indent_stack2 = [" "] * indent_level
        opts = Dumper.Opts(cur_depth, max_depth, indent_stack2, fold, print_id, color_code, dump_private, tab, len, width, False)
        return cls._dump_to_str(None, val, opts, set())

    @staticmethod
    def dprint(*args, **kwargs):
        result = Dumper.dump(*args, **kwargs)
        dprint(result)

    @classmethod
    def _dump_to_str(cls, key, val : Any, opts, seen : set):
        if opts.cur_depth > 0 and (key in opts.fold or val in opts.fold or type(val) in opts.fold):
            return cls._dump_prefix(key, val, opts) + str(Sentinel.FOLDED)
        elif key == "__builtins__":
            return cls._dump_prefix(key, val, opts) + "<builtins>"
        elif isinstance(val, (type, types.FunctionType)):
            return cls._dump_scalar(key, val, opts, seen)
        elif val is sys.modules[__name__]:
            return cls._dump_prefix(key, val, opts) + "<hancho module>"
        elif hasattr(type(val), "__dump__"):
            return val.__dump__(key, opts, seen)
        elif Utils.is_sequence(val) or Utils.is_mapping(val) or Utils.is_set(val):
            return cls._dump_vector(key, val, val, opts, seen)
        elif hasattr(val, "__dict__") and type(val) not in Dumper.opaque_types:
            return cls._dump_vector(key, val, val.__dict__, opts, seen)
        else:
            return cls._dump_scalar(key, val, opts, seen)

    @classmethod
    def _dump_scalar(cls, key, val : Any, opts, seen : set):
        # FIXME how else can we verify that this is really a scalar?
        #scalars = (int, float, str, bool, types.NoneType, object, types.FunctionType)
        #if type(val) not in scalars:
        #    msg = f"Trying to dump {key}:{type(val)}={val}, but it's not a scalar"
        #    raise TypeError(msg)

        if isinstance(val, Sentinel):
            raise TypeError("Sentinels are not scalars")

        if (vid := hex(id(val))) in seen:
            return cls._dump_prefix(key, val, opts) + "<ref loop>"
        seen.add(vid)
        # FIXME should we always be using repr here?
        return cls._dump_prefix(key, val, opts) + repr(val)

    @classmethod
    def _dump_vector(cls, key, val, contents, opts, seen : set):
        prefix = cls._dump_prefix(key, val, opts, force_type = False)

        if key == "Dumper":
            pass

        if (vid := hex(id(val))) in seen:
            return prefix + "<ref loop>"
        seen.add(vid)

        items : list[item[Any]]
        if Utils.is_mapping(contents):
            ld, items, rd = "{", list(contents.items()), "}"
        elif Utils.is_sequence(contents):
            items = [(None, v) for v in contents]
            if isinstance(contents, tuple):
                ld, items, rd = "(", items, ",)" if len(items) == 1 else ")"
            else:
                ld, items, rd = "[", items, "]"
        elif Utils.is_set(contents):
            items = [(None, v) for v in contents]
            ld, items, rd = "{", items, "}"

        else:
            raise AssertionError(f"Don't know what to do with {type(val)}")

        dump_private = opts.dump_private
        items2 = []
        any_private = False

        for x in items:
            if is_public(x[0]) or dump_private:
                items2.append(x)
            else:
                any_private = True
        if any_private:
            items2.append(("<private fields>", Sentinel.FOLDED))

        return cls._dump_items(key, prefix, ld, items2, rd, opts, set(seen))

    @classmethod
    def _dump_items(cls, key, prefix, ld, items : list[item[Any]], rd, opts, seen : set):
        # This slightly odd construct is so that when a deeply nested container doesn't fit on a
        # line, we rewind the callstack back to the topmost container that was not forced to be
        # flat.

        if key in opts.fold:
            raise AssertionError()

        if opts.flat:
            return prefix + cls._dump_items_flat(key, ld, items, rd, opts, set(seen))
        else:
            try:
                return prefix + cls._dump_items_flat(key, ld, items, rd, dataclasses.replace(opts, flat = True), set(seen))
            except Dumper.LineTooLong:
                rhs = cls._dump_items_deep(key, ld, items, rd, opts, set(seen))
                return prefix + rhs

    @classmethod
    def _dump_items_flat(cls, key, ld, items : list[item[Any]], rd, opts, seen : set):
        result = ld

        for i in range(len(items)):
            result += cls._dump_to_str(items[i][0], items[i][1], opts, set(seen))
            if i < len(items) - 1: result += ", "
            if opts.len + len(result) + len(rd) > opts.width:
                raise Dumper.LineTooLong()

        return result + rd

    @classmethod
    def _dump_items_deep(cls, key, ld, items, rd, opts, seen : set):
        result = ld + "\n"

        if opts.cur_depth == opts.max_depth:
            return str(Sentinel.FOLDED)
        opts = dataclasses.replace(opts, cur_depth = opts.cur_depth + 1)

        opts.indent_stack2.append(" ")

        # len(pad) + 1 for the trailing comma
        pad = (" " * opts.tab) * len(opts.indent_stack2)
        new_opts = dataclasses.replace(opts, len = len(pad) + 1)

        for i in range(len(items)):
            result += pad + cls._dump_to_str(items[i][0], items[i][1], new_opts, set(seen))
            if i < len(items) - 1: result += ","
            result += "\n"
        opts.indent_stack2.pop()

        pad = (" " * opts.tab) * len(opts.indent_stack2)
        return result + pad + rd

    @classmethod
    def _dump_prefix(cls, key, val, opts, force_type = False):
        prefix = ""
        if key:
            prefix += f"{key}"
        elif isinstance(val, (ABCMeta, abc.Callable, types.ModuleType)):
            prefix += val.__name__
        elif hasattr(val, 'name') and val.name is not None:
            prefix += val.name
        elif isinstance(val, abc.Mapping) and 'name' in val and val['name'] is not None:
            prefix += val['name']

        if isinstance(val, type):
            prefix += ":type"
        elif (type(val) not in Dumper.base_types) or force_type:
            prefix += ":"
            prefix += type(val).__name__
            if opts.print_id: prefix += "@" + Utils.hex_tag(val)
        if prefix: prefix += " = "
        return prefix

    @classmethod
    def any_to_key(cls, var):
        key = Dumper.dump(
            var,
            color_code=False,
            dump_private=False,
            max_depth=999,
            print_id=False,
            tab=0,
            width=999,
        )

        key = Dumper.depointer(key)
        key = "".join(key.split())
        key = repr(key)
        return key


# endregion
# ==================================================================================================
# region Tracer

# ┌─
# └─

class Tracer:

    @staticmethod
    def trace_start(scope, action : str, arg):
        log = current_script.get().log
        if log.level > Log.TRACE: return
        if isinstance(scope, Expander):
            scope = scope.scope

        scope_tag    = Utils.instance_tag(scope)
        scope_color  = Utils.obj_to_ansi_color(scope)
        indent_color = scope_color

        if action == "range":
            indent_color = Log.WHITE
            log.trace(f"{indent_color}┌── {scope_color}{scope_tag}{Log.RESET} begin {arg}\n")
        else:
            log.trace(f"{indent_color}┌ {scope_color}{scope_tag}{Log.RESET}.{action}({arg})\n")
        log.indent(indent_color)

    @staticmethod
    def trace_up(scope, up, action, arg):
        log = current_script.get().log
        if log.level > Log.TRACE: return
        if isinstance(scope, Expander):
            scope = scope.scope

        scope_color = Utils.obj_to_ansi_color(scope)
        scope_tag   = Utils.instance_tag(scope)
        up_color   = Utils.obj_to_ansi_color(up)
        up_tag     = Utils.instance_tag(up)

        log.trace(f"{scope_color}┌ {scope_tag}{Log.RESET}.{action}({arg}) -> {up_color}{up_tag}\n")

    @staticmethod
    def trace_end(scope, action : str, arg, result):
        log = current_script.get().log
        if log.level > Log.TRACE: return
        if isinstance(result, Expander):
            result = result.scope

        log.dedent()
        scope_tag   = Utils.instance_tag(scope)
        scope_color   = Utils.obj_to_ansi_color(scope)
        indent_color = scope_color

        if action == "range":
            indent_color = Log.WHITE
            log.trace(f"{indent_color}└── {scope_color}{scope_tag}{Log.RESET} end {arg}\n")
        else:
            result_color = Log.RESET
            result_type  = type(result)

            if Utils.is_mapping(result):
                result_color = Utils.obj_to_ansi_color(result)

            log.trace(f"{indent_color}└ {scope_color}{arg}:{result_type.__name__}{Log.RESET} = ")
            if not Utils.is_scalar(result):
                log.trace(f"{result_color}{Utils.instance_tag(result)}\n")
            else:
                log.trace(f"{result_color}{result!r}\n")

# endregion
# ==================================================================================================
# region Runner

class Runner:

    @classmethod
    def reset(cls, flags):
        cls.max_jobs   = flags.max_jobs
        cls.max_errors = flags.max_errors

        cls.core_sem  : asyncio.Semaphore = asyncio.Semaphore(cls.max_jobs)
        cls.core_lock : asyncio.Lock = asyncio.Lock()

        cls.aio_done_queue : asyncio.Queue = asyncio.Queue()
        cls.live_aio_tasks : set[asyncio.Task] = set()

        cls.tasks_enabled : int = 0
        cls.tasks_started : int = 0
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

        if count > cls.max_jobs:
            raise ValueError(f"Tried to acquire {count} cores, which exceeds the max {cls.max_jobs}")
        async with cls.core_lock:
            acquired = 0
            try:
                while acquired < count:
                    await cls.core_sem.acquire()
                    acquired += 1
                return count
            except BaseException:
                cls.release(acquired)
                raise

    @classmethod
    def release(cls, count):
        for _ in range(count):
            cls.core_sem.release()

    @staticmethod
    async def async_run_tasks(log : Log, project : Project):
        """Run all tasks until we run out."""

        # ------------------------------------
        # Create asyncio tasks for all enabled Hancho tasks.

        for repo in project._repos:
            for task in repo.yield_tasks():
                if task._enabled:
                    task.create_aio_task()

        # ------------------------------------
        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.

        log.verbose("Running tasks...\n")

        while Runner.live_aio_tasks and (Runner.tasks_broken + Runner.tasks_failed) <= Runner.max_errors:
            finished_aio_task : asyncio.Task | None = None

            done = await Runner.aio_done_queue.get()
            finished_aio_task = cast(asyncio.Task, done)
            task = finished_aio_task.hancho_task # type: ignore

            match type(task._error):

                case types.NoneType:
                    task._complete = True
                    if task._skipped:
                        Runner.tasks_skipped += 1
                    else:
                        Runner.tasks_finished += 1

                case asyncio.CancelledError:
                    Runner.tasks_cancelled += 1
                case Task.CANCELLED:
                    Runner.tasks_cancelled += 1
                case Task.BROKEN:
                    Runner.tasks_broken += 1
                case Task.FAILED:
                    Runner.tasks_failed += 1
                case _:
                    log.warning(f"Weird exception {type(task._error)} >{task._error}< at {time.perf_counter()}\n")
                    Runner.tasks_failed += 1

            if finished_aio_task is not None:
                Runner.live_aio_tasks.discard(finished_aio_task)

        failures = Runner.tasks_broken + Runner.tasks_failed
        if failures > Runner.max_errors:
            log.error(f"Too many failures after {failures}, cancelling tasks and stopping build\n")

            # Cancel all the asyncio.Tasks that haven't completed yet
            log.warning(f"Cancelling {len(Runner.live_aio_tasks)} tasks\n")

            # This tasks_cancelled count may be off by one or two due to in-flight tasks not being
            # accounted for in live_aio_tasks, but it doesn't matter - we're about to bail out due
            # to failures or someone ctrl-c'ing the build, this is purely cosmetic.

            Runner.tasks_cancelled += len(Runner.live_aio_tasks)
            for t in Runner.live_aio_tasks:
                t.cancel()

            # and then wait on their cancellations to complete (it isn't instantaneous)
            await asyncio.gather(*Runner.live_aio_tasks, return_exceptions=True)

        return 1 if Runner.tasks_failed or Runner.tasks_broken else 0

# endregion
# ==================================================================================================
# region Project

class Project(Scope):
    def __init__(self, flags, log, name : str, path : str, root : str, targets : list[str], *args, **kwargs):
        self.name : str = name
        self.path : str = path
        self.root : str = root
        self.targets : list[str] = targets

        self.flags = flags
        self.log   = log

        Utils.merge_variants(self, *args, **kwargs)

        self._real_filenames = set()
        self._dedupe = Dict()
        self._repos : list[Repo] = []
        #self._build_reasons = Counter()
        self._runner = Runner()

    def build(self) -> int:
        time_a3 = time.perf_counter()

        # ------------------------------------
        # Select the set of tasks to run.

        if self.targets:
            for target in self.targets:
                # Enable all tasks whose name contains any of the targets
                for repo in self._repos:
                    for task in repo.yield_tasks():
                        if task.name is not None and target in task.name:
                            task.queue_task()

        elif self.flags.build_all:
            for repo in self._repos:
                for task in repo.yield_tasks():
                    task.queue_task()

        else:
            # Enable all tasks in the top repo
            for task in self._repos[0].yield_tasks():
                task.queue_task()

        # ------------------------------------
        # Run the tasks.

        result = asyncio.run(Runner.async_run_tasks(self.log, self))

        # ------------------------------------
        # Update stat DBs.

        if not self.flags.build_dry:
            for repo in self._repos:
                for task in repo.yield_tasks():
                    if task._complete:
                        task.update_repo_stat_db()
                repo.save_stat_db()

        time_b3 = time.perf_counter()
        self.log.verbose(Log.DBLUE + f"Build took {time_b3 - time_a3:8.6f} seconds\n")
        self._result = result
        return result

    def stats(self):
        task_count = 0
        for repo in self._repos:
            task_count += len(list(repo.yield_tasks()))

        # FIXME all the stats should move to Project
        self.log.verbose(Log.DBLUE + f"Tasks created:    {task_count}\n")
        self.log.verbose(Log.DBLUE + f"Tasks enabled:    {Runner.tasks_enabled}\n")
        self.log.verbose(Log.DBLUE + f"Tasks started:    {Runner.tasks_started}\n")
        self.log.verbose(Log.DBLUE + f"Tasks finished:   {Runner.tasks_finished}\n")
        self.log.verbose(Log.DBLUE + f"Tasks broken:     {Runner.tasks_broken}\n")
        self.log.verbose(Log.DBLUE + f"Tasks failed:     {Runner.tasks_failed}\n")
        self.log.verbose(Log.DBLUE + f"Tasks cancelled:  {Runner.tasks_cancelled}\n")
        self.log.verbose(Log.DBLUE + f"Tasks skipped:    {Runner.tasks_skipped}\n")
        self.log.verbose(Log.DBLUE + f"Mtime calls:      {Repo.stat_calls}\n")
        self.log.verbose(Log.DBLUE + f"Hash calls:       {Repo.hash_calls}\n")
        self.log.verbose(Log.DBLUE + f"Hash bytes:       {Repo.hash_bytes}\n")
        self.log.verbose(Log.DBLUE + f"Hash time:        {Repo.hash_time:8.6f}\n")

        #log.verbose("Rebuild reasons:\n")
        #for reason, count in project._build_reasons.items():
        #    log.verbose(f"{reason:17} {count}\n")

    def passfail(self):
        if self._runner.tasks_failed or self._runner.tasks_broken:
            self.log.error("BUILD FAILED\n")
        elif Runner.tasks_finished:
            self.log.info(Log.GREEN + "BUILD PASSED\n")
        else:
            self.log.info(Log.BLUE + "BUILD CLEAN\n")

# endregion
# ==================================================================================================
# region Stat

class Stat:
    def __init__(self):
        self.cwd : str
        self.directory : str
        self.file : str
        self.arguments : list[str]
        self.command : str | list[str] | abc.Callable
        self.output : str
        self.hash : str
        self.st_size : int
        self.st_mtime_ns : int

# endregion
# ==================================================================================================
# region Repo

class Repo(Scope):

    @classmethod
    def reset(cls):
        cls.stat_calls : int = 0
        cls.hash_calls : int = 0
        cls.hash_bytes : int = 0
        cls.hash_time  : float = 0


    def __init__(self, flags, log, project, name : str, path : str, root : str, *args, **kwargs):
        self.name = name
        self.path = path
        self.root = root
        self.log  = log
        self.build_root = "{join(root, flags.build_dir, flags.build_tag)}"

        self.flags   = flags
        self.log     = log
        self.project = project

        Utils.merge_variants(self, *args, **kwargs)

        self._scripts = []
        self._stat_db : dict[str, dict] = {}

        stat_db_path = self.expand("{join(build_root, 'hancho.json')}")

        if os.path.isfile(stat_db_path):
            log.verbose(f"Loading stat_db {stat_db_path}\n")
            json_stats = Utils.load_json(stat_db_path)
            for file, json_stat in json_stats.items():
                self._stat_db[file] = json_stat
        else:
            log.verbose(Log.DRED + f"Stat db not found: {stat_db_path}\n")

    @classmethod
    def load_depfile(cls, filename : str, format : str, task_cwd : str) -> list[str]:
        # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
        # NOTE: This does not handle filenames with escaped spaces in them, but I don't want to
        # write a whole .d parser yet.

        if not os.path.isfile(filename):
            return []

        with open(filename, encoding="utf-8") as depcontents:
            if format == "msvc":
                raw_deplines = json.load(depcontents)["Data"]["Includes"]
            elif format == "gcc":
                raw_deplines = re.sub(r"\\\s*\n", "", depcontents.read()).split()
            else:
                raise Task.BROKEN(f"Invalid depfile format {format}")

        deplines = []
        for d in raw_deplines:
            d = os.path.join(task_cwd, d)
            if not d.startswith(task_cwd) or d.endswith(":"):
                continue
            deplines.append(d)

        return deplines


    def save_stat_db(self):
        stat_db_path = self.expand("{join(build_root, 'hancho.json')}")
        self.log.verbose(Log.DGREEN + f"Saving stat_db {stat_db_path}\n")
        Utils.save_json(self._stat_db, stat_db_path)

#    def save_comp_db(self):
#        # Haven't tested this in an IDE, but I think it matches the spec.
#        comp_db_path = self.expand("{join(build_root, 'compile_commands.json')}")
#        self.log.verbose(f"Saving comp_db {comp_db_path}\n")
#
#        comp_db = []
#        for file, stat in self._stat_db.items():
#            comp_db.append({
#                "directory" : stat.cwd,
#                "command"   : Utils.command_to_string(stat.command),
#                "file"      : Path.relpath(file, stat.cwd),
#            })
#        Utils.save_json(comp_db, comp_db_path)

    def yield_tasks(self) -> abc.Iterator[Task]:
        for script in self._scripts:
            yield from script._tasks

    @classmethod
    def hash_file(cls, abs_path, h = 0):
        Repo.hash_calls += 1
        time_a = time.perf_counter()
        with open(abs_path, "rb") as f:
            Repo.hash_bytes += os.fstat(f.fileno()).st_size
            digest = hashlib.file_digest(f, lambda: hashlib.blake2b(digest_size=16)).hexdigest()
        time_b = time.perf_counter()
        Repo.hash_time += time_b - time_a
        return digest

    def get_stat(self, file : str) -> dict:
        self.stat_calls += 1

        _hash = self.hash_file(file)
        _stat = os.stat(file)

        stat = {
            "hash": _hash,
            "st_size": _stat.st_size,
            "st_mtime_ns": _stat.st_mtime_ns,
        }

        return stat

    def update_stat(self, file, command):
        new_stat = self.get_stat(file)
        new_stat['command'] = Dumper.depointer(repr(command)) if command is not None else None
        if file in self._stat_db:
            old_stat = self._stat_db[file]
            self._stat_db[file] = Utils.merge_variant(old_stat, new_stat)
        else:
            self._stat_db[file] = new_stat

    def stat_changed2(self, file) -> str | None:
        if file not in self._stat_db:
            return f"Stat missing for {file}"

        old_stat = self._stat_db[file]
        new_stat = self.get_stat(file)

        if old_stat["st_mtime_ns"] != new_stat["st_mtime_ns"]:
            return f"Mtime mismatch for {file}"

        if old_stat["st_size"] != new_stat["st_size"]:
            return f"Size mismatch for {file}"

        if old_stat["hash"] != new_stat["hash"]:
            return f"Hash mismatch for {file}"

        return None

        #return (
        #    (old_stat["st_mtime_ns"] != new_stat["st_mtime_ns"])
        #    or (old_stat["st_size"] != new_stat["st_size"])
        #    or (old_stat["hash"] != new_stat["hash"])
        #)

    def command_changed(self, file, command):
        old_command = self._stat_db[file]['command']
        new_command = Dumper.depointer(repr(command))
        return old_command != new_command

# endregion
# ==================================================================================================
# region Task

# FIXME get() should call eval or something? a lot of these task fields should delegate to script

class Task(Scope):

    class FAILED(Exception):    pass
    class CANCELLED(Exception): pass
    class BROKEN(Exception):    pass

    def __init__(self, *args, **kwargs):
        self.name        = None
        self.desc        = "<no desc>"
        self.command : str | list[str] | task_callback = ""
        self.build_force = "{flags.build_force}"
        self.build_root  = "{script.build_root}"
        self.cwd         = "{script.root}"
        self.depformat   = "gcc" if os.name == "posix" else "msvc"
        self.in_depfile  = None
        self.job_size    = 1

        script = current_script.get()

        self.flags   = script.flags
        self.log     = script.log
        self.project = script.project
        self.repo    = script.repo
        self.script  = script.script

        Utils.merge_variants(self, *args, **kwargs)

        # ----------------------------------------
        # Private fields

        self._enabled = False

        # These two have to be dicts so that downstream tasks can pick named outputs from it if
        # needed.
        self._in_files :  dict[str, list[str]] = {}
        self._out_files : dict[str, list[str]] = {}

        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._aio_task : asyncio.Task | None = None

        # We remember the aio context we were in when this task was created so that we can return
        # to it when the task starts.
        self._aio_context = contextvars.copy_context()

        # The "return value" for the task as a whole, or "None" if the task was successful.
        self._error : BaseException | None = None

        self._skipped = False

        # Bookkeeping stuff
        self._task_id : int = -1
        self._stdout : str = ""
        self._stderr : str = ""
        self._cores = 0
        self._complete = False
        self._deplines = []

        self.script._tasks.append(self)

        # ----------------------------------------
        # Expand all input fields. These _must_ be expandable into filenames or tasks right now, as
        # we need to queue up input tasks before this task starts (and then expands everything else)

        for k, v in self.__dict__.items():
            if k.startswith("in_"):  # noqa: SIM102
                if isinstance(v, str):
                    v = Expander._expand(v, self, recursive = True)
                    self.__dict__[k] = v

        # ----------------------------------------
        # Add all input fields that are tasks to _input_tasks so we can await them later.

        input_tasks = []
        for v in Utils.yield_leaves(self.__dict__):
            if isinstance(v, Task):
                assert v is not self
                input_tasks.append(v)
        self._input_tasks = input_tasks

        # ----------------------------------------

        # Auto-start the task if it was created dynamically during the build.
        if Utils.in_event_loop():
            self.queue_task()

    def __copy__(self):
        # Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
        raise AssertionError()

    def __deepcopy__(self, _):
        raise AssertionError()

    def create_aio_task(self):
        assert Utils.in_event_loop()

        if self._aio_task is None:
            t = asyncio.create_task(self.task_top(), context=self._aio_context)
            setattr(t, "hancho_task", self)  # noqa: B010
            Runner.live_aio_tasks.add(t)
            t.add_done_callback(lambda t: Runner.aio_done_queue.put_nowait(t))
            self._aio_task = t

    def queue_task(self):
        if self._enabled:
            return

        self._enabled = True
        Runner.tasks_enabled += 1

        # If this task was dynamically created during the build, add it to asyncio immediately.
        if Utils.in_event_loop():
            self.create_aio_task()

        # Start all tasks referenced by the config so we don't deadlock while waiting for them.
        # FIXME we should pull all the upstream tasks out earlier or something
        for input_task in self._input_tasks:
            input_task.queue_task()

    async def task_top(self) -> dict | None:
        # Entry point for tasks, just so we can keep all the task-level exception handling together.

        try:
            await self.task_main()
        except (Exception, asyncio.CancelledError) as ex:
            self._error = ex
        finally:
            Runner.release(self._cores)

        if self._error:
            #print("================")
            self.log_task(Log.ERROR, traceback.format_exception(self._error))
            if (self._stdout or self._stderr):
                self.log_task(Log.ERROR, self.dump_stdout())
            #print("================")
            #self.log_task(Log.ERROR, traceback.format_exception(self._error))

        return self._out_files

    async def task_main(self):
        # ------------------------------------
        # Await all tasks in our input fields and then flatten them.

        await self.await_inputs()

        # ------------------------------------
        # We're ready to run

        Runner.tasks_started += 1
        self._task_id = Runner.tasks_started

        # ------------------------------------
        # Expand all mandatory fields in the raw config and fix raw file paths.

        Tracer.trace_start(self, "range", "expand_task")
        self.expand_task()
        Tracer.trace_end(self, "range", "expand_task", None)

        # ------------------------------------
        # Inputs are ready, templates are expanded, see if everything's sane before we try running
        # commands.

        self.sanity_check()

        # ------------------------------------
        # Dry runs early out after the task is initialized but before we do .exists() checks or
        # run any commands.

        if self.flags.build_dry:
            return

        # ------------------------------------
        # Paths updated. See if we need to rebuild our outputs.

        if self.in_depfile:
            self._deplines = Repo.load_depfile(self.in_depfile, self.depformat, self.cwd)

        text  = repr(self.name) if self.name else ""
        text += " : " if self.name and self.desc else ""
        text += repr(self.desc) if self.desc else ""

        reason = self.rebuild_reason()
        if not reason:
            self._skipped = True
            self.log_task(Log.VERBOSE, Log.YELLOW + f"Task {text}: {Log.DGREEN}Clean!\n")
            return

        # ------------------------------------
        # Wait for enough jobs to free up to run this task.

        self._cores = await Runner.acquire(self.job_size)

        # ------------------------------------
        # Print what we're about to run

        self.log_task(Log.INFO, Log.YELLOW + f"Task {text}: {Log.DBLUE + reason}\n")
        #self.log_task(Log.VERBOSE, Log.DBLUE + f"{reason}\n")

        # ------------------------------------
        # Run all the commands

        time_a = time.perf_counter()


        def fix_cmd(cmd):
            curlify = str.maketrans({"«" : "{", "»" : "}"})
            cmd = cmd.translate(curlify)
            # Hacky way to rel-ify all paths in the command, but bleh
            #cmd = cmd.replace(self.cwd + "/", "")
            return cmd

        if isinstance(self.command, str):
            command = fix_cmd(self.command)
            await self.run_command(command)
        elif isinstance(self.command, list):
            for command in self.command:
                command = fix_cmd(command)
                await self.run_command(command)
        elif isinstance(self.command, abc.Callable):
            await self.call_callback(self.command)
        elif self.command is not None:
            raise TypeError(type(self.command))

        time_b = time.perf_counter()

        self.log_task(Log.VERBOSE, Log.VIOLET + f"Task took {time_b-time_a:8.6f} sec: {text}\n")

        # ------------------------------------
        # See if the task wrote all its output files

        for file in Utils.yield_leaves(self._out_files):
            if not os.path.exists(file):
                raise Task.FAILED(f"Task ran, but output file still missing: {file}")

        # ------------------------------------
        # Update the repo stat db

        # FIXME _don't_ do this, we don't want to update anything until _everything_ is done.
        # And we're done

    def update_repo_stat_db(self):
        with chdir(self.cwd):
            # Reload the depfile post-build so we pick up new deps
            if self.in_depfile:
                self._deplines = Repo.load_depfile(self.in_depfile, self.depformat, self.cwd)

            for file in Utils.yield_leaves([self._in_files, self._deplines]):
                self.repo.update_stat(file, None)

            for file in Utils.yield_leaves(self._out_files):
                self.repo.update_stat(file, self.command)

    async def await_inputs(self):
        # NOTE: Hancho _cannot_ have dependency cycles unless you do something really sketchy via
        # modifying tasks after they're created but before they're started. If you point task B's
        # inputs at task A and task A's inputs at task B and it blows up, that's on you.

        for input_task in self._input_tasks:
            if not isinstance(input_task, Task):
                raise TypeError(f"Input task is not a task, it's a {type(input_task)!r}")

            if input_task._aio_task is None:
                raise AssertionError("One of a task's input sub-tasks was not started")

            await input_task._aio_task

            if input_task._error is not None:
                raise Task.CANCELLED(f"Task {hex(id(self))} is cancelled due to {input_task._error}")

    def expand_task(self):
        if self.log.level <= Log.DEBUG:
            self.log_task(Log.DEBUG, "Task before expand:\n")
            self.log_task(Log.DEBUG, Dumper.dump(self, fold=[Task], dump_private = False) + "\n")

        # We eval these first because we need them for filename expansion

        script_root = self.expand("{script.root}")
        build_root  = self.expand("{build_root}")
        self_cwd    = self.expand("{cwd}")

        Tracer.trace_start(self, "range", "expand io fields")
        # Then we expand all io fields and fix their paths.
        io_fields = [k for k in self.__dict__ if k.startswith("in_") or k.startswith("out_")]

        for field_name in io_fields:
            # if task is a scope, then flattening in_files produces an array of the _attributes_
            # of the task instead of the out_files.

            def yield_files(f : branch[str | Task] | None) -> abc.Iterator[str]:
                if f is None:
                    return
                for x in Utils.yield_leaves(f):
                    if isinstance(x, Task):
                        yield from Utils.yield_leaves(x._out_files)
                    else:
                        yield x

            file_tree = cast(branch[str | Task], getattr(self, field_name))
            files : list[str] = list(yield_files(file_tree))

            if files:
                files = self.expand(files, recursive = True)
                files = self.fix_paths(field_name, files, script_root, build_root)
                setattr(self, field_name, files[0] if len(files) == 1 else files)

                if field_name == "in_depfile":
                    if len(files) > 1:
                        raise Task.BROKEN("in_depfile can't be a list")
                elif field_name.startswith("in_"):
                    self._in_files[field_name] = [Path.resolve(Path.join(self_cwd, file)) for file in files]
                elif field_name.startswith("out_"):
                    self._out_files[field_name] = [os.path.abspath(file) for file in files]

        Tracer.trace_end(self, "range", "expand io fields", None)

        # IO fields all expanded, we can expand the rest of the task now.

        Tracer.trace_start(self, "range", "expand everything else")
        for key, val in self.__dict__.items():
            if is_public(key):
                setattr(self, key, Expander._expand(val, self, recursive = True))
        Tracer.trace_end(self, "range", "expand everything else", None)

        if not self.flags.build_dry:
            blah = list(self._out_files.values())
            for file in Utils.yield_leaves(blah):
                os.makedirs(os.path.dirname(file), exist_ok=True)
            if self.in_depfile:
                os.makedirs(os.path.dirname(self.in_depfile), exist_ok=True)

        if self.log.level <= Log.DEBUG:
            self.log_task(Log.DEBUG, "Task after expand:\n")
            self.log_task(Log.DEBUG, Dumper.dump(self, fold=[Task], dump_private = False) + "\n")

    def fix_paths(self, field : str, files : list[str], script_root : str, build_root : str) -> list[str]:
        return [self.fix_path(field, f, script_root, build_root) for f in files]

    def fix_path(self, field : str, file : str, script_root : str, build_root : str) -> str:
        """
        Input and output file paths in .hancho scripts are declared relative to the directory the
        script is in (stored in the config under 'script_cwd').
        In general we want to run commands from the root of the repo and store output files in
        repo/build, so we need to fix up the paths to match.
        """

        if not isinstance(script_root, str) or not isinstance(file, str):
            breakpoint()

        # File paths _must_ be abs'd after joining, otherwise they might look like they're under
        # script_dir, but they're not because the paths could have "../../../../.." in them.
        file = os.path.abspath(os.path.join(script_root, file))

        # Move all outputs under build_root and ensure their directories exist.
        # Note - This will also move "in_depfile" under build_root - this is _intentional_ as
        # it's an _output_ from the compiler and is not checked in to the source tree.

        if (field.startswith("out_") or field == "in_depfile") and not Path.startswith(file, build_root):
            file = Path.relpath(file, script_root)
            file = os.path.join(build_root, file)
            file = os.path.abspath(file)

        cwd = cast(str, self.expand("{cwd}"))

        #print()
        #print(file)
        #print()
        file = Path.relpath(file, cwd)

        return file

    def sanity_check(self):
        # Check for all task issues that break the build

        if not Path.exists(self.cwd):
            raise Task.BROKEN(f"Task working directory '{self.cwd}' does not exist")

        if not Path.startswith(self.build_root, self.repo.root):
            raise Task.BROKEN(f"The build root {self.build_root} is not under repo.root {self.repo.root}")

        if isinstance(self.command, list):
            for command in Utils.yield_leaves(self.command):
                if not isinstance(command, str):
                    raise Task.BROKEN(f"Command is not a list of strings: {self.command}")
        elif not isinstance(self.command, (str, abc.Callable)):
            raise Task.BROKEN(f"Command is not a string or callable: {self.command}")

        # In strict mode, we mark a task broken if its command still has delimiters in it.
        if self.flags.strict:
            for command in Utils.yield_leaves(self.command):
                if Expander.is_macro(command) or Expander.is_template(command):
                    raise Task.BROKEN(f"STRICT: Command not fully expanded: {command!r}")

#        io_fields = [k for k in self.__dict__ if k.startswith("in_") or k.startswith("out_")]
#        for field_name in io_fields:
#            if field_name.startswith("out_"):
#                files = getattr(self, field_name)
#                for file in Utils.yield_leaves(files):
#                    print()
#                    print(file)
#                    print(self.build_root)
#
#                    if not Path._startswith(Path.abspath(file), self.build_root):
#                        raise Task.BROKEN(f"Path error, output file {field_name}:{file} is not under build root {self.build_root}")


        # Check that all build files would end up under build_root
        for file in Utils.yield_leaves(self._out_files):
            #assert Path.isabs(file)
            if not Path.startswith(Path.abspath(file), self.build_root):
                raise Task.BROKEN(f"Path error, output file _out_files:{file} is not under build root {self.build_root}")

        # Check for task collisions
        for file in Utils.yield_leaves(self._out_files):
            real_file = cast(str, Path.abspath(file))
            if real_file in self.project._real_filenames:
                raise Task.BROKEN(f"TaskCollision: Multiple tasks build {real_file}")
            self.project._real_filenames.add(real_file)

        # Check for missing inputs. We have to check build_dry, as the input files may only exist if
        # we're really running tasks.
        for file in Utils.yield_leaves(self._in_files):
            #if not Path.isabs(file):
            #    raise Task.BROKEN(f"Somehow we got a non-abs path for an input file - {file}")
            if not Path.exists(os.path.join(self.cwd, file)) and not self.flags.build_dry:
                raise Task.BROKEN(f"Input file missing - {file}")

        # Tasks should have at most one depfile.
        if isinstance(self.in_depfile, list) and len(self.in_depfile) > 1:
            raise Task.BROKEN(f"Tasks can't have more than one dependency file! - {self.in_depfile}")

    def rebuild_reason(self) -> str:
        """Figures out why we have to run a Task, or returns "" if we don't."""

        # ------------------------------------
        # Check the trivial reasons to rebuild

        if self.build_force:
            return "Task forced to rebuild"

        if not any(Utils.yield_leaves(self._in_files)):
            return "Always rebuild a task with no inputs"

        if not any(Utils.yield_leaves(self._out_files)):
            return "Always rebuild a task with no outputs"

        # ------------------------------------
        # Check for file modifications

        all_files : tree[str] = [self._in_files, self._out_files, self._deplines]


        for file in Utils.yield_leaves(all_files):
            if not os.path.isfile(file):
                rel_file = Path.relpath(file, self.repo.root)
                return f"File missing: {rel_file!r}"
            if reason := self.repo.stat_changed2 (file):
                return reason

        for file in Utils.yield_leaves(self._out_files):
            if self.repo.command_changed(file, self.command):
                rel_file = Path.relpath(file, self.repo.root)
                return f"Command mismatch for {rel_file!r}"

        return ""

    async def run_command(self : Task, command : str):

        # Prints the cwd and command about to run
        # some/folder$ g++ blah blah blah
        self.log_task(Log.INFO, Log.GRAY3 + f"{Path.relpath(self.cwd, self.repo.root)}$ {command}\n")

        proc = None
        try:
            # Create the subprocess via asyncio and then await the result.
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd    = self.cwd,
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.PIPE,
                start_new_session = True
            )

            (stdout_data, stderr_data) = await proc.communicate()

        except asyncio.CancelledError as ex:
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
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
            # Re-raise so that dependent tasks and the top-level except can see the error.
            raise ex
        except Exception as ex:
            # All other exceptions are treated as a task failure.
            raise Task.FAILED(f"Command threw an exception : {ex}") from ex

        self._stdout = stdout_data.decode(errors="replace")
        self._stderr = stderr_data.decode(errors="replace")

        if proc.returncode == 2:
            raise Task.BROKEN("Command return code was 2 : bash error")
        elif proc.returncode:
            raise Task.FAILED(f"Command return code was non-zero : {proc.returncode}")

        if (self._stdout or self._stderr):
            self.log_task(Log.DEBUG, self.dump_stdout())

    async def call_callback(self : Task, command : task_callback):
        callback_dir = Path.relpath(self.script.root, self.repo.root)
        self.log_task(Log.INFO, f"{callback_dir}$ {command}\n")

        # Callbacks run from the script dir where they were defined so that relative paths used
        # in the callback will be correct.

        with chdir(self.script.root):
            result = command(self)

        # It would seem like we wouldn't have to explicitly unwrap one level of await-ness here,
        # but apparently that's just how Python waitables work.
        if inspect.isawaitable(result):
            result = await result

    def dump_stdout(self : Task) -> str:
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

    def log_task(self : Task, level : int, message : str | list[str]):
        # Log helper that adds the [ NN/ XX] tag before the log line.

        color = Log.YELLOW if level > Log.INFO else ""

        all_lines = []
        for chunk in Utils.yield_leaves(message):
            all_lines.extend(chunk.splitlines(keepends = True))

        for line in all_lines:
            if not self.log._line_buffer:
                self.log._log(level, "", f"[{self._task_id:3d}/{Runner.tasks_enabled:3d}] ")
            self.log._log(level, color, line)

# endregion
# ==================================================================================================
# region Script

# This is not a types.ModuleType, but it still works fine as one. Making it a ModuleType is a bit
# annoying to work with.

class Script(Scope):

    def __init__(self : Script, flags : Flags, log : Log, project : Project, repo : Repo, name : str, path : str, root : str, *args, **kwargs):
        super().__init__()

        # FIXME - do we want to force users to import hancho? so they get type
        # checking and stuff?

        self.name       = name
        self.path       = path
        self.root       = root
        self.build_root = "{join(repo.build_root, relpath(root, repo.root))}"

        self.hancho  = sys.modules[__name__]
        self.flags   = flags
        self.log     = log
        self.project = project
        self.repo    = repo
        self.script  = self
        self.aliases = aliases

        Utils.merge_variants(self, *args, **kwargs)

        self.name       = self.expand(self.name)
        self.path       = Path.resolve(self.expand(self.path)) # this should be before resolve/expand root
        self.root       = Path.resolve(self.expand(self.root))
        self.build_root = self.expand(self.build_root) # build_root doesn't exist yet, don't resolve

        # We add __file__ because some scripts might expect it (see dynamic_dependencies/filelist/build.hancho)
        self.__file__ = self.path

        self.__spec__    = None # The Python module loading machinery looks for this field, but it can be None.
        self._tasks      = []

    # Script exposes all globals that were set while exec'ing the scripts modules
    # through getitem/getattr

    def __getitem__(self, key):
        return self.__dict__[key]

    def __getattr__(self, key) -> Any:
        return object.__getattribute__(self, key)

    def dump(self, *args, **kwargs):
        dprint(Dumper.dump(*args, **kwargs))

    def exec(self : Script):
        assert self.path.endswith(".hancho")

        self.log.verbose(Log.ORANGE + f"script {self.path}\n")

        with self.log.indenter(Log.VERBOSE, Log.ORANGE):
            code = None
            with open(self.path, encoding="utf-8") as file:
                source = file.read()
                code = compile(source, self.path, "exec", dont_inherit=True)

            with chdir(self.root), current_script.set(self):
                exec(code, self.__dict__)

            if "__builtins__" in self.__dict__:
                del self.__dict__["__builtins__"]

# endregion
# ==================================================================================================
# region create_test_script

def create_test_script(*, name, file : str, root : str, stdout : str | typing.TextIO, extra_flags) -> Script:
    flags = Flags.parse(
        f"--name={name}",
        f"--file={file}",
        f"--root={root}",
        *extra_flags
    )
    Utils.reset()
    Repo.reset()
    Runner.reset(flags)

    log = Log(flags, stdout)
    log.verbose(Log.ORANGE + f"create_test_script {flags.path}\n")

    args = ()
    kwargs = {}

    current_script.set(Dict(flags = flags, log = log))

    # FIXME need to make sure private fields aren't in the dump, but i'll deal with it properly later.
    dedupe_key = Dumper.any_to_key([flags.path, flags.root, *args, kwargs])
    assert " _" not in dedupe_key

    project = Project(flags, log, flags.name, flags.path, flags.root, flags.targets)

    repo = Repo(flags, log, project, flags.name, flags.path, flags.root)
    project._repos.append(repo)

    script = Script(flags, log, project, repo, flags.name, flags.path, flags.root)
    repo._scripts.append(script)
    project._dedupe[dedupe_key] = script

    current_script.set(script)

    # do _not_ exec the dummy script

    return script

# endregion
# ==================================================================================================
# region main

def main():
    flags = Flags.parse(*sys.argv)

    Utils.reset()
    Repo.reset()
    Runner.reset(flags)

    # ┌─
    # └─

    log = Log(flags, sys.stdout)
    log.verbose(Log.GRAY3 + f"Command line : {" ".join(sys.argv)}\n")
    log.verbose(Log.GRAY3 + "Verbose mode on\n")
    log.debug(Log.GRAY3 + "Debug mode on\n")
    log.trace(Log.GRAY3 + "Trace mode on\n")
    args = ()
    kwargs = {}

    current_script.set(Dict(flags = flags, log = log))

    # FIXME need to make sure private fields aren't in the dump, but i'll deal with it properly later.
    dedupe_key = Dumper.any_to_key([flags.path, flags.root, *args, kwargs])
    assert " _" not in dedupe_key

    # ------------------------------------

    time_a1 = time.perf_counter()

    project = Project(flags, log, flags.name, flags.path, flags.root, flags.targets)

    log.verbose(Log.ORANGE + f"project {project.path}\n")
    with log.indenter(Log.VERBOSE, Log.ORANGE):

        repo = Repo(flags, log, project, flags.name, flags.path, flags.root)
        project._repos.append(repo)

        log.verbose(Log.ORANGE + f"repo {repo.path}\n")
        with log.indenter(Log.VERBOSE, Log.ORANGE):
            script = Script(flags, log, project, repo, flags.name, flags.path, flags.root)
            project._dedupe[dedupe_key] = script
            repo._scripts.append(script)
            script.exec()

        time_b1 = time.perf_counter()

    log.verbose(Log.DBLUE + f"Loading took {time_b1 - time_a1:8.6f} seconds\n")

    # ------------------------------------

    project.build()
    project.stats()
    project.passfail()

    log._flush()
    return project._result


# ----------------------------------------

def _start():
    # Top-level exception handler just so we can print a big red "SOMETHING BROKE" message if
    # we failed to catch an exception during load/build. The 'except' clause should catch
    # Exception and not BaseException so ctrl-c doesn't get misinterpreted as a Hancho bug.
    try:
        return main()
    except Exception:
        print(f"{Log.RED}Hancho hit an unhandled exception:{Log.RESET}\n")
        print(traceback.format_exc())
        print(Log.RESET)

        # Re-raise in debug builds so the debugger will stop execution.
        raise

#endregion
# ==================================================================================================
# region User API

aliases = Dict(
    name     = "aliases",
    Dict     = Dict,
    Task     = Task,
    Tool     = Tool,
    Dumper   = Dumper,
    getcwd   = os.getcwd,
    dprint   = dprint,
    dump     = Dumper.dprint,
    flatten  = Utils.flatten_tree,
    run_cmd  = Utils.run_cmd,
    weave    = Utils.weave,
    resolve  = Path.resolve,
    abspath  = Path.abspath,
    normpath = Path.normpath,
    relpath  = Path.relpath,
    basename = Path.basename,
    dirname  = Path.dirname,
    split    = Path.split,
    join     = Path.join,
    swapext  = Path.swapext,
)

current_script = contextvars.ContextVar("current_script")

path = __file__
root = os.path.dirname(path)

sys.modules["hancho"] = sys.modules[__name__]

def script(file, root = None, *args, **kwargs):
    old_script = current_script.get()
    flags   = old_script.flags
    log     = old_script.log
    project = old_script.project
    repo    = old_script.repo

    path = Path.resolve(old_script.expand(file))
    root = Path.resolve(old_script.expand(root or os.path.dirname(path)))
    name = os.path.basename(path)

    dedupe_key = Dumper.any_to_key([path, root, *args, kwargs])
    assert " _" not in dedupe_key

    if dupe := project._dedupe.get(dedupe_key, None):
        log.verbose(Log.DGREEN + f"Deduped script('{path}')\n")
        return dupe

    script = Script(flags, log, project, repo, name, path, root, *args, **kwargs)
    project._dedupe[dedupe_key] = script
    repo._scripts.append(script)

    script.exec()
    return script

def include(file):
    script = current_script.get()
    path = Path.resolve(script.expand(file))

    code = None
    with open(path, encoding="utf-8") as file:
        source = file.read()
        code = compile(source, path, "exec", dont_inherit=True)

        script.log.verbose(Log.ORANGE + f"include {path}\n")
        with script.log.indenter(Log.VERBOSE, Log.ORANGE):
            exec(code, script.__dict__)

        if "__builtins__" in script.__dict__:
            del script.__dict__["__builtins__"]

def repo(file, root = None, *args, **kwargs) -> Script:
    old_script = current_script.get()

    flags   = old_script.flags
    log     = old_script.log
    project = old_script.project

    path = Path.resolve(old_script.expand(file))
    root = Path.resolve(old_script.expand(root or os.path.dirname(path)))
    name = os.path.basename(path)

    dedupe_key = Dumper.any_to_key([path, root, *args, kwargs])
    assert " _" not in dedupe_key

    if dupe := project._dedupe.get(dedupe_key, None):
        log.verbose(Log.LIME + f"Deduped repo('{path}')\n")
        return dupe

    repo = Repo(flags, log, project, name, path, root)
    project._repos.append(repo)

    script = Script(flags, log, project, repo, name, path, root, *args, **kwargs)
    project._dedupe[dedupe_key] = script
    repo._scripts.append(script)

    script.exec()
    return script

def build():
    return current_script.get().project.build()

# endregion
# ==================================================================================================

if __name__ == "__main__":
    sys.exit(_start())
