#!/usr/bin/python3

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
from collections import Counter, abc, defaultdict
from contextlib import chdir, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from functools import partial, wraps
from typing import Any, ClassVar, Concatenate, TypeGuard, cast

# pyright: reportSelfClsParameterName=false

# endregion
# ==================================================================================================
# region Constants and globals

def is_public(key):
    if not isinstance(key, str) and key is not None:
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

type branch[LEAF] = LEAF | abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]
type tree[LEAF] = abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]
type task_callback = abc.Callable[[Task], Any]
type item[T] = tuple[int | str | None, T]

# endregion
# ==================================================================================================
# region Sentinel

class Sentinel:
    def __init__(sentinel, name):     sentinel.name = name
    def __repr__(sentinel):           return sentinel.name
    def __bool__(sentinel):           return False
    def __copy__(sentinel):           return sentinel
    def __deepcopy__(sentinel, memo): return sentinel
    def __reduce__(sentinel):         return sentinel.name
    def __dump__(sentinel, key, opts, seen):
        new_opts = dataclasses.replace(opts, print_id = False)
        return Dumper._dump_prefix(key, sentinel, new_opts) + Log.VIOLET + repr(sentinel) + Log.RESET

    MISSING : Any

# Marks a function argument as "not present" for when None would be a valid value.
Sentinel.MISSING = Sentinel("MISSING")

# endregion
# ==================================================================================================
# region Utils

class Utils:

    @classmethod
    def reset(cls):
        cls.stat_calls : int = 0
        cls.hash_calls : int = 0
        cls.hash_bytes : int = 0
        cls.hash_time  : float = 0

    @staticmethod
    def hex_to_ansi(hex):
        """Converts a color hex code into an ANSI escape sequence"""
        r, g, b = ((hex >> 16) & 0xFF, (hex >>  8) & 0xFF, (hex >>  0) & 0xFF)
        return f"\x1B[38;2;{r};{g};{b}m" if hex else "\x1B[0m"

    @staticmethod
    def stringify(variant : Any) -> str:
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
    def yield_cross_join[T](*args: tree[T]) -> abc.Iterator[list[T]]:
        if len(args) == 0:
            return
        elif len(args) == 1:
            x = args[0]
            yield list(Utils.yield_leaves(x))
        else:
            lhs = list(Utils.yield_leaves(args[0]))
            rhs = list(Utils.yield_cross_join(*args[1:]))
            for lhs2 in lhs:
                for rhs2 in rhs:
                    yield [lhs2, *rhs2]

    @staticmethod
    def cross_join[T](
        reduce: abc.Callable[[T, T], T], lhs: tree[T], rhs: tree[T], *args: tree[T]
    ) -> list[T]:
        """
        This function does a "cross join" in the database sense, every line in lhs will be joined
        to every line in rhs (and this will be repeated with *args if present). This is useful for
        adding prefixes / suffixes to a bunch of strings, or generating all possible combinations
        of two sets of options, et cetera.
        """

        lhs2 = Utils.flatten_tree(lhs)
        rhs2 = Utils.cross_join(reduce, rhs, *args) if len(args) > 0 else Utils.flatten_tree(rhs)

        return [reduce(lh, rh) for lh in lhs2 for rh in rhs2]

    @staticmethod
    def weave(lhs : tree[str], rhs : tree[str], *args : tree[str]) -> list[str]:
        return Utils.cross_join(lambda x, y: x + y, lhs, rhs, *args)

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
    def is_sequence(v : Any) -> TypeGuard[abc.Sequence[Any]]:
        return isinstance(v, abc.Sequence) and not isinstance(v, (str, bytes, bytearray))

    @staticmethod
    def is_mut_sequence(v) -> TypeGuard[abc.MutableSequence[Any]]:
        if isinstance(v, (str, bytes, bytearray)):
            return False
        return isinstance(v, abc.MutableSequence)

    @staticmethod
    def is_set(v) -> TypeGuard[abc.Set]:
        return isinstance(v, abc.Set)

    @staticmethod
    def is_mut_set(v) -> TypeGuard[abc.MutableSet[Any]]:
        return isinstance(v, abc.MutableSet)

    @staticmethod
    def is_mapping(v : Any) -> TypeGuard[abc.Mapping[Any, Any]]:
        return isinstance(v, abc.Mapping)

    @staticmethod
    def is_mut_mapping(v) -> TypeGuard[abc.MutableMapping[Any, Any]]:
        return isinstance(v, abc.MutableMapping)

    # ------------------------------------
    # yield leaves from a tree

    @staticmethod
    def yield_leaves[T](branch : branch[T] | None, seen : set | None = None) -> abc.Iterator[T]:
        if branch is None:
            return
        if seen is None: seen = set()
        if (vid := hex(id(branch))) in seen: return
        seen.add(vid)

        if isinstance(branch, (abc.Sequence, abc.Set)):
            if isinstance(branch, (str, bytes, bytearray)):
                yield cast(T, branch)
            else:
                for v in branch:
                    yield from Utils.yield_leaves(v, seen)
        elif isinstance (branch, abc.Mapping):
            for v in branch.values():
                yield from Utils.yield_leaves(v, seen)
        else:
            yield branch

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
    def yield_values(variant : Any, seen : set | None = None) -> abc.Iterator[Any]:
        if variant is None:
            return

        if seen is None: seen = set()
        if (vid := hex(id(variant))) in seen: return
        seen.add(vid)

        if Utils.is_mapping(variant):
            for v2 in variant.values():
                yield from Utils.yield_values(v2)
        elif Utils.is_sequence(variant) or Utils.is_set(variant):
            for v2 in variant:
                yield from Utils.yield_values(v2)
        else:
            yield variant

        #for item in Utils.yield_items(variant):
        #    yield item[1]

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

    # ------------------------------------

    @staticmethod
    def hex_tag(obj):
        return "0x" + hex(id(obj))[-4:].upper()

    @staticmethod
    def instance_tag(obj):
        result = ""
        if name := obj.__dict__.get("name", None):
            result += name
            result += ":"
        result += type(obj).__name__
        result += "@"
        result += Utils.hex_tag(obj)
        return result

    @classmethod
    def commands_to_string_list(cls, commands: tree[str | abc.Callable] | None):
        if commands is None: return []
        commands = Utils.flatten_tree(commands)
        command_str = [c.__name__ if callable(c) else c for c in commands]
        return command_str

    @staticmethod
    def tree_map[X, Y, **P](func: abc.Callable[Concatenate[Any, P], Y]) -> abc.Callable[Concatenate[tree[X], P], tree[Y]]:

        """Turns a mapping function into one that can be applied to arbitrarily nested containers."""

        def repack(obj: tree[X], *args: Any, **kwargs: Any) -> tree[Y]:
            if hasattr(obj, "_make"):
                # Namedtuple special case
                constructor = obj.__dict__["_make"]
            elif isinstance(obj, defaultdict):
                # Default dict special case
                constructor = partial(type(obj), obj.default_factory)
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

        @wraps(func)
        def apply2(obj: tree[X], *args : P.args, **kwargs : P.kwargs) -> tree[Y]:
            result = apply1(obj, {}, *args, **kwargs)
            return cast(tree[Y], result)

        return apply2

    @staticmethod
    def tree_all(func):
        """Turns a predicate into one that can be applied to arbitrarily nested containers."""

        @wraps(func)
        def wrapper(variant, *args, **kwargs):
            return all(func(v, *args, **kwargs) for v in Utils.yield_values(variant))

        return wrapper

    @classmethod
    def hash_file(cls, abs_path, h = 0):
        Utils.hash_calls += 1
        time_a = time.perf_counter()
        with open(abs_path, "rb") as f:
            Utils.hash_bytes += os.fstat(f.fileno()).st_size
            digest = hashlib.file_digest(f, lambda: hashlib.blake2b(digest_size=8)).hexdigest()
        time_b = time.perf_counter()
        Utils.hash_time += time_b - time_a
        return digest

    @classmethod
    def get_stats(cls, file : str, command : tree[str | abc.Callable] | None = None) -> dict:
        cls.stat_calls += 1

        _hash = cls.hash_file(file)
        _stat = os.stat(file)

        command_str = cls.commands_to_string_list(command)

        stat = {
            "hash": _hash,
            "st_size": _stat.st_size,
            "st_mtime_ns": _stat.st_mtime_ns,
            "command": command_str
        }

        return stat

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
            deplines3 = None
            if format == "msvc":
                # MSVC /sourceDependencies
                deplines3 = json.load(depcontents)["Data"]["Includes"]
            elif format == "gcc":
                # GCC -MMD
                # NOTE: This does not handle filenames with escaped spaces in them, but I don't
                # want to write a whole .d parser yet.
                deplines0 = depcontents.read()
                deplines1 = re.sub(r"\\\s*\n", "", deplines0)
                deplines2 = deplines1.split()
                deplines3 = [d for d in deplines2 if d[-1] != ":"]
            else:
                raise Task.BROKEN(f"Invalid depfile format {format}")

        # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
        deplines4 : list[str] = [cast(str, Path.join(task_cwd, d)) for d in deplines3]

        deplines5 = [d for d in deplines4 if not d.startswith("/usr")]

        return deplines5

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
            return

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
            pass
            ldict[key] = rdict[key]
            pass

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

    GRAY1   = Utils.hex_to_ansi(0x333333)
    GRAY2   = Utils.hex_to_ansi(0x666666)
    GRAY3   = Utils.hex_to_ansi(0x999999)
    GRAY4   = Utils.hex_to_ansi(0xCCCCCC)
    RESET   = Utils.hex_to_ansi(0x000000)
    WHITE   = Utils.hex_to_ansi(0xFFFFFF)

    TRACE    = 0
    DEBUG    = 10
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
        log._indent_stack.append(f"{ansi_color}│{Log.RESET} ")

    def dedent(log):
        log._indent_stack.pop()

    @contextmanager
    def indenter(log, ansi_color : str = RESET):
        log.indent(ansi_color)
        yield
        log.dedent()

    def trace(log, text):
        # Traces do their own color coding
        log._log(Log.TRACE, text)

    def debug(log, text):
        log._log(Log.DEBUG, Log.GRAY3 + text)

    def info(log, text):
        log._log(Log.INFO, text)

    def warning(log, text):
        log._log(Log.WARNING, Log.YELLOW + text)

    def error(log, text):
        log._log(Log.ERROR, Log.ORANGE + text)

    def critical(log, text):
        log._log(Log.CRITICAL, Log.RED + text)

    def exception(log, ex):
        if ex is None:
            return
        tb = traceback.extract_tb(ex.__traceback__)
        if tb:
            frame = tb[-1]
            log.error("exception:\n")
            log.error(f"  type = {type(ex)}\n")
            log.error(f"  text = {ex}\n")
            log.error(f"  file = {frame.filename}\n")
            log.error(f"  func = {frame.name}\n")
            log.error(f"  line = {frame.lineno}\n")
            log.error(traceback.format_exc() + "\n")
        else:
            log.error(f"Could not extract traceback from {ex}!")

    def _log(log, log_level : int, text : str):
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
    # These functions wrap the os.path.* functions so that they work on arbitrary trees

    @staticmethod
    def _resolve(path, strict):
        """
        This tries to convert a path containing potential env variable references and stuff into a
        real path.
        """
        path = os.path.expandvars(path)
        path = pathlib.Path(path).expanduser()
        path = path.resolve(strict = strict)
        return str(path)

    resolve  = Utils.tree_map(lambda path : Path._resolve(path, strict = True))

    abspath  = Utils.tree_map(os.path.abspath)
    normpath = Utils.tree_map(os.path.normpath)
    basename = Utils.tree_map(os.path.basename)
    dirname  = Utils.tree_map(os.path.dirname)

    @staticmethod
    def swapext_scalar(p : str, new_ext : str):
        return os.path.splitext(p)[0] + new_ext
    swapext  = Utils.tree_map(swapext_scalar)

    isabs    = Utils.tree_all(os.path.isabs)
    isfile   = Utils.tree_all(os.path.isfile)
    isdir    = Utils.tree_all(os.path.isdir)
    exists   = Utils.tree_all(os.path.exists)

    # WARNING - Both "startswith" and "relpath" below can throw ValueError if there's a mix of
    # abs/rel paths, or if the paths are on different volumes in Windows. We don't handle this yet,
    # but we will need to eventually. If this occurs inside a macro you'll see the exception in the
    # macro expansion trace and the macro will be returned unexpanded. Using "commonpath" here is
    # probably worth it though, as it handles some annoying edge cases.

    #startswith = Utils.tree_all(lambda p, parent : os.path.commonpath([p, parent]) == parent)

    @staticmethod
    def _startswith(path : str, prefix : str):
        try:
            common = os.path.commonpath([path, prefix])
            return common == prefix
        except Exception as ex:
            raise ex

    @staticmethod
    def startswith(variant : tree[str], prefix : str):
        return all(Path._startswith(path, prefix) for path in Utils.yield_leaves(variant))

    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with "commonpath" and "removeprefix".

    @staticmethod
    def relpath(lhs, rhs):
        log = cg.log

        if isinstance(rhs, (list, tuple, set)):
            return [Path.relpath(lhs, rh) for rh in rhs]

        if not os.path.isabs(lhs):
            lhs = os.path.abspath(lhs)
        if not os.path.isabs(rhs):
            rhs = os.path.abspath(rhs)

        prefix = ""
        try:
            prefix = os.path.commonpath([lhs, rhs])
        except Exception:
            log.error(f"Commonpath failed for {lhs} and {rhs}\n")
            raise

        if lhs == rhs:
            result = "."
        elif prefix != rhs:
            result = lhs
        else:
            result = lhs.removeprefix(prefix + os.sep)

        return result

    @staticmethod
    def split(path):
        return os.path.split(path)

    @staticmethod
    def join(*args: tree[str]) -> list[str] | str:
        lists  = list(Utils.yield_cross_join(*args))
        joined = [os.path.join(*cross) for cross in lists]
        normed = [os.path.normpath(path) for path in joined]
        return normed if len(normed) > 1 else normed[0]


# endregion
# ==================================================================================================
# region Scope

class Scope:
    def __init__(scope, *, _up = None):
        scope._up  = _up

    def expand(scope, template, recursive : bool = False) -> Any:
        return Expander.expand(template, scope, recursive)

    def update(scope, *args, **kwargs):
        Utils.merge_variants(scope, *args, **kwargs)

    # Fill-in-the-blank (or override what's there): Merges lhs and args into a new Dict, keeping
    # only keys that were already in lhs. For example, if you have a Dict that contains
    # "out_bin" and you merge it with "compile_cpp", Hancho will complain that "out_bin" is missing
    # - it sees both "out_obj" and "out_bin" and assumes the task produces both. If you do
    # compile_cpp.fill(...), "out_bin" does not get added to compile_cpp.

    def fill(scope, *args, **kwargs):
        for rhs in (*args, kwargs):
            Utils.merge_variant(scope, rhs, keep_rhs = False)

    # ----------------------------------------
    # These are here so that the linter doesn't complain about reading/writing unknown fields off a
    # scope.

    def __getattr__(scope, key) -> Any:
        result = object.__getattribute__(scope, key)
        return result

    def __setattr__(scope, key, val : Any):
        object.__setattr__(scope, key, val)

    # ----------------------------------------
    # Scope is _not_ a mapping, but it implements the core of MutableMapping so that it only
    # exposes public fields and traverses _up if a key is not in the current scope.

    def __contains__(scope, key : str) -> bool:
        return key in scope.__dict__

    def __getitem__(scope, key : str) -> Any:
        if is_private(key):
            raise KeyError(key)
        return scope.__dict__[key]

    def __setitem__(scope, key, val):
        if is_private(key):
            raise KeyError(key)
        scope.__dict__[key] = val

    def __delitem__(scope, key):
        if is_private(key):
            raise KeyError(key)
        del scope.__dict__[key]

    def __len__(scope):
        return sum(1 for _ in scope)

    def __iter__(scope):
        yield from filter(is_public, scope.__dict__)

# ====================================

class Dict(Scope, abc.Mapping):
    def __init__(self, *args, **kwargs):
        super().__init__(_up   = kwargs.pop("_up",   None))
        Utils.merge_variants(self, *args, **kwargs)

class Tool(Dict):
    # Tool is just a callable alias for Dict to make build scripts more readable.
    def __call__(self, *args, **kwargs):
        return Task(self, *args, **kwargs)

# endregion
# ==================================================================================================
# region Flags

class Flags:
    def __init__(flags, *args, **kwargs):
        flags.name        = "flags"
        flags.file        = "build.hancho"
        flags.name        = "{join(split(flags.root)[-1], basename(flags.file))}"
        flags.path        = "{resolve(flags.file)}"
        flags.root        = "{dirname(flags.path)}"
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

        Utils.merge_variants(flags, *args, **kwargs)

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

        levels = ["trace", "debug", "info", "warning", "error", "critical"]

        def str_to_bool(v):
            if v.lower() in ("yes", "true", "t", "y", "1"): return True
            if v.lower() in ("no", "false", "f", "n", "0"): return False
            raise argparse.ArgumentTypeError("Not a bool")

        parser.add_argument("project.targets", nargs="*", help="The names or partial names of targets to build")

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
# region ContextGlobals

class ContextGlobals:
    def __init__(cg, *args, **kwargs):
        object.__setattr__(cg, "name", "globals")
        object.__setattr__(cg, "_ctx",  ContextVar("globals"))
        cg._ctx.set(Dict(*args, **kwargs))

    def keys(cg):
        return cg._ctx.get().keys()

    def reset(cg, *args, **kwargs):
       return cg._ctx.set(Dict(*args, **kwargs))

    def update(cg, *args, **kwargs):
        return cg.reset(cg._ctx.get(), *args, **kwargs)

    def __contains__(cg, key):
        return cg._ctx.get().__contains__(key)

    def __getattr__(cg, key):
        try:
            return cg.__getitem__(key)
        except KeyError as err:
            raise AttributeError(key) from err

    def __setattr__(cg, key, val):
        raise AttributeError(f"can't write {key}:{val}")

    def __getitem__(cg, key):
        return cg._ctx.get()[key]

    # We do _not_ allow incrementally modifying a context, as doing so could allow inadvertent
    # modification of a context already captured by an earlier task.
    def __setitem__(cg, key, val):
        raise KeyError(f"can't write {key}:{val}")

# endregion
# ==================================================================================================
# region Expander

# Expander _is_ a mapping, but is _not_ a scope.

class Expander(abc.Mapping):
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
    def wrap(cls, scope : Scope, recursive : bool):
        assert not isinstance(scope, cls)
        return cls(scope, recursive)

    # ====================================

    def __getattr__(expander, key) -> Any:
        try:
            return expander.internal_get(key)
        except KeyError as ex:
            raise AttributeError from ex

    def __getitem__(expander, key : str) -> Any:
        return expander.internal_get(key)

    def __iter__(expander):
        return expander.scope.__iter__()

    def __len__(expander):
        return expander.scope.__len__()

    # ====================================

    @staticmethod
    def scope_walk(scope, key : str, default = Sentinel.MISSING) -> Any:
        """Walk the _up chain until we find the key or run out of scopes."""

        sdict = scope if isinstance(scope, dict) else scope.__dict__
        result = sdict.get(key, Sentinel.MISSING)

        if result is not Sentinel.MISSING:
            return result
        elif (up := sdict.get('_up', None)) is not None:
            return Expander.scope_walk(up, key, default)
        elif key in cg:
            return cg[key]
        elif key in aliases:
            return aliases[key]
        elif default is not Sentinel.MISSING:
            return default

        raise KeyError(key)

    def internal_get(expander, key : str):
        log = cg.log

        Tracer.start(log, expander.scope, "get", key)
        result = Expander.scope_walk(expander.scope, key)

        if isinstance(result, Scope):
            # have to do this so that "read nested c first" resolves in the dest dict first
            result = expander.wrap(result, object.__getattribute__(expander, "recursive"))
        else:
            result = Expander.expand(result, expander.scope, recursive = True)

        Tracer.end(log, expander.scope, "get", key, result)
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
    def expand(cls, var : Any, scope, /, recursive : bool = False) -> Any:
        result = cls._expand(var, scope, recursive)
        assert not isinstance(result, cls)
        return result

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
                log = cg.log
                try:
                    Tracer.start(log, scope, "expand", var)
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
                    Tracer.end(log, scope, "expand", old_var, var)

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
        log = cg.log

        # Bail out if we've done too many evals already.
        old_evals = cls.cv_evals.get()
        if old_evals >= cls.MAX_EVALS:
            raise RecursionError(f"Expansion failed to terminate after {old_evals} evals: {var!r}")
        cls.cv_evals.set(old_evals + 1)

        result = var
        try:
            Tracer.start(log, scope, "eval", var[1:-1])
            result = eval(var[1:-1], {}, cls.wrap(scope, recursive))
        except RecursionError:
            raise
        except Exception as ex:
            _ = ex
            # If you uncomment this line, things will be super spammy.
            #dprint(f"##########\neval failed because >{ex}<\n##########\n")
            pass
        finally:
            Tracer.end(log, scope, "eval", var[1:-1], result)
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
        #key,
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
        if opts.cur_depth > 0 and (key in opts.fold or type(val) in opts.fold):
                return f"{key}:{type(val).__name__} = {Log.BLUE}<folded>{Log.RESET}"

        if key is not None and is_private(key) and not opts.dump_private:
            return ""
        elif key == "__builtins__":
            return cls._dump_prefix(key, val, opts) + "<builtins>"
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
            breakpoint()

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

        items2 = []
        any_private = False

        for x in items:
            if x[0] == "_up" and isinstance(x[1], Scope):
                items2.append((x[0], "<parent scope>"))
            elif is_public(x[0]) or opts.dump_private:
                items2.append(x)
            else:
                any_private = True
        if any_private:
            items2.append(("<private fields are hidden>", Sentinel.MISSING))

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
                return prefix + cls._dump_items_deep(key, ld, items, rd, opts, set(seen))

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
            #return ld + "..." + rd
            return "<folded>"
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
        if key: prefix += f"{key}"
        if (type(val) not in Dumper.base_types) or force_type:
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
        return key


# endregion
# ==================================================================================================
# region Tracer

class Tracer:

    @staticmethod
    def start(log : Log, scope : Scope, action : str, arg):
        if log.level > Log.TRACE: return
        if isinstance(scope, Expander):
            scope = scope.scope

        scope_tag    = Utils.instance_tag(scope)
        scope_color  = Utils.obj_to_ansi_color(scope)
        indent_color = scope_color

        if action == "range":
            indent_color = Log.WHITE
            log.trace(f"{indent_color}┌─── {scope_color}{scope_tag}{Log.RESET} begin {arg}\n")
        else:
            log.trace(f"{indent_color}┌ {scope_color}{scope_tag}{Log.RESET}.{action}({arg!r})\n")
        log.indent(indent_color)

    @staticmethod
    def up(log, scope, up, action, arg):
        if log.level > Log.TRACE: return
        if isinstance(scope, Expander):
            scope = scope.scope

        scope_color = Utils.obj_to_ansi_color(scope)
        scope_tag   = Utils.instance_tag(scope)
        up_color   = Utils.obj_to_ansi_color(up)
        up_tag     = Utils.instance_tag(up)

        log.trace(f"{scope_color}┌ {scope_tag}{Log.RESET}.{action}({arg!r}) -> {up_color}{up_tag}\n")

    @staticmethod
    def end(log, scope : Scope, action : str, arg, result):
        if log.level > Log.TRACE: return
        if isinstance(result, Expander):
            result = result.scope

        log.dedent()
        scope_tag   = Utils.instance_tag(scope)
        scope_color   = Utils.obj_to_ansi_color(scope)
        indent_color = scope_color

        if action == "range":
            indent_color = Log.WHITE
            log.trace(f"{indent_color}└─── {scope_color}{scope_tag}{Log.RESET} end {arg}\n")
        else:
            result_color = Log.RESET
            result_type  = type(result)

            if Utils.is_mapping(result):
                result_color = Utils.obj_to_ansi_color(result)

            log.trace(f"{indent_color}└ {scope_color}{arg!r}:{result_type.__name__}{Log.RESET} = ")
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

        log.info(Log.BLUE + "Running tasks...\n")

        while Runner.live_aio_tasks and (Runner.tasks_broken + Runner.tasks_failed) <= Runner.max_errors:
            finished_aio_task : asyncio.Task | None = None

            done = await Runner.aio_done_queue.get()
            finished_aio_task = cast(asyncio.Task, done)
            task = finished_aio_task.hancho_task # type: ignore

            match type(task._error):
                case types.NoneType:
                    task._complete = True
                    Runner.tasks_finished += 1
                case asyncio.CancelledError:
                    Runner.tasks_cancelled += 1
                case Task.CANCELLED:
                    Runner.tasks_cancelled += 1
                case Task.BROKEN:
                    Runner.tasks_broken += 1
                case Task.FAILED:
                    Runner.tasks_failed += 1
                case Task.SKIPPED:
                    task._complete = True
                    Runner.tasks_skipped += 1
                case _:
                    log.warning(f"Weird exception {type(task._error)} >{task._error}< at {time.perf_counter()}\n")
                    log.exception(task._error)
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
    def __init__(project, name : str, path : str, root : str, targets : list[str], *args, **kwargs):
        super().__init__()

        project.name : str = name
        project.path : str = path
        project.root : str = root
        project.targets : list[str] = targets

        project.update(*args, **kwargs)

        assert project.path == Path.resolve(project.path)
        assert project.root == Path.resolve(project.root)

        project._real_filenames = set()
        project._dedupe = Dict()
        project._repos : list[Repo] = []
        project._build_reasons = Counter()
        project._runner = Runner()

    def build(project, log, flags) -> int:
        time_a3 = time.perf_counter()

        #result = hancho_build(project)

        # ------------------------------------
        # This must happen _after_ all repos are loaded (so that if they change repo_root we don't
        # get the old path), but _before_ we build any tasks.

        # Also this is here and not in hancho_main because tests also need to load stats.
        for repo in project._repos:
            repo.load_stat_db()

        # ------------------------------------
        # Select the set of tasks to run.

        if project.targets:
            for target in project.targets:
                # Enable all tasks whose name contains any of the targets
                for repo in project._repos:
                    for task in repo.yield_tasks():
                        if target in task.name:
                            task.queue_task()

        elif flags.build_all:
            for repo in project._repos:
                for task in repo.yield_tasks():
                    task.queue_task()

        else:
            # Enable all tasks in the top repo
            for task in project._repos[0].yield_tasks():
                task.queue_task()

        # ------------------------------------
        # Run the tasks.

        result = asyncio.run(Runner.async_run_tasks(log, project))

        # ------------------------------------
        # Update stat DBs.

        for repo in project._repos:
            repo.save_stat_db()

        time_b3 = time.perf_counter()
        log.info(Log.GREEN + f"Build took {time_b3 - time_a3:8.6f} seconds\n")
        project._result = result
        return result

    def stats(project, log):
        task_count = 0
        for repo in project._repos:
            task_count += len(list(repo.yield_tasks()))

        # FIXME all the stats should move to Project
        log.debug(f"Tasks created:    {task_count}\n")
        log.debug(f"Tasks enabled:    {Runner.tasks_enabled}\n")
        log.debug(f"Tasks started:    {Runner.tasks_started}\n")
        log.debug(f"Tasks finished:   {Runner.tasks_finished}\n")
        log.debug(f"Tasks broken:     {Runner.tasks_broken}\n")
        log.debug(f"Tasks failed:     {Runner.tasks_failed}\n")
        log.debug(f"Tasks cancelled:  {Runner.tasks_cancelled}\n")
        log.debug(f"Tasks skipped:    {Runner.tasks_skipped}\n")
        log.debug(f"Mtime calls:      {Utils.stat_calls}\n")
        log.debug(f"Hash calls:       {Utils.hash_calls}\n")
        log.debug(f"Hash bytes:       {Utils.hash_bytes}\n")
        log.debug(f"Hash time:        {Utils.hash_time:8.6f}\n")

        log.debug("Rebuild reasons:\n")
        for reason, count in project._build_reasons.items():
            log.debug(f"{reason:17} {count}\n")

    def passfail(project, log):
        if project._runner.tasks_failed or project._runner.tasks_broken:
            log.error(Log.RED + "BUILD FAILED\n")
        elif Runner.tasks_finished:
            log.info(Log.GREEN + "BUILD PASSED\n")
        else:
            log.info(Log.BLUE + "BUILD CLEAN\n")

# endregion
# ==================================================================================================
# region Repo

class Repo(Scope):
    def __init__(repo, name : str, path : str, root : str, *args, **kwargs):
        super().__init__()

        repo.name = name
        repo.path = path
        repo.root = root
        repo.build_root = "{join(root, flags.build_dir, flags.build_tag)}"

        Utils.merge_variants(repo, *args, **kwargs)

        repo.build_root = Expander.expand(repo.build_root, repo, True)

        repo._stat_db = {}
        repo._scripts = []

    def yield_tasks(repo) -> abc.Iterator[Task]:
        for script in repo._scripts:
            yield from script._tasks

    def check_stats(repo, filename : str, command : list, build_reasons):
        if not Path.exists(filename):
            build_reasons["file missing"] += 1
            return f"File missing: {filename}"

        if filename not in repo._stat_db:
            build_reasons["stat missing"] += 1
            return f"Stat missing: {filename}"

        old_stat = repo._stat_db[filename]
        new_stat = Utils.get_stats(filename, command)

        try:
            old_ns = old_stat["st_mtime_ns"]
            new_ns = new_stat["st_mtime_ns"]

            if old_ns != new_ns:
                build_reasons["mtime mismatch"] += 1
                return f"Mtime mismatch {old_ns} != {new_ns} for : {filename}"
        except Exception as err:
            traceback.print_exc()
            raise err

        if old_stat["st_size"] != new_stat["st_size"]:
            build_reasons["size mismatch"] += 1
            return f"Size mismatch {old_stat["st_size"]} != {new_stat["st_size"]} for : {filename}"

        if old_stat["hash"] != new_stat["hash"]:
            build_reasons["hash mismatch"] += 1
            return f"Hash mismatch {old_stat["hash"]} -> {new_stat["hash"]} for : {filename}"

        old_command = old_stat["command"]
        new_command = Utils.commands_to_string_list(command)

        if old_command != new_command:
            build_reasons["command changed"] += 1
            return f"Command used to generate file has changed : {filename!r}\n : {old_command!r}n : {command!r}"

        # Does not need to rebuild based on file stats / hash
        build_reasons["*hash match"] += 1
        return ""

    def load_stat_db(repo):
        log = cg.log
        stat_db_path = os.path.join(repo.build_root, 'hancho.json')

        if os.path.isfile(stat_db_path):
            with open(stat_db_path) as contents:
                log.debug(Log.ORANGE + f"Loading stat_db {stat_db_path}\n")
                repo._stat_db = json.load(contents)
        else:
            log.debug(Log.ORANGE + f"No stat db for {repo.root}\n")
            repo._stat_db = {}

    def save_stat_db(repo):
        log = cg.log
        flags = cg.flags

        if flags.build_dry:
            return

        stat_db_path = Path.join(repo.build_root, "hancho.json")
        log.debug(Log.ORANGE + f"Saving stat_db {stat_db_path}\n")

        stat_db = {}

        # FIXME we could probably save a little work if we didn't always re-stat every input and
        # output, but this is safe for now.

        # ------------------------------------
        # Gather stats for all _input_ files in all tasks.

        for task in repo.yield_tasks():
            if not task._complete:
                continue

            for file in Utils.yield_values(task._in_files):
                stat_db[file] = Utils.get_stats(file)

            for file in Utils.yield_values(task._out_files):
                stat_db[file] = Utils.get_stats(file)

            in_depfile = cast(str, task.in_depfile)
            if in_depfile:
                stat_db[in_depfile] = Utils.get_stats(in_depfile)
                deplines = Utils.load_depfile(in_depfile, task.depformat, task.cwd)
                for file in deplines:
                    stat_db[file] = Utils.get_stats(file)

        # ------------------------------------
        # Fill in the .command field for all our _output_ files.
        # We do this in a second pass because if a file is both an input and an output, we don't
        # want to accidentally overwrite the command with []
        # during "stat_db[file] = Utils.get_stats(file)"

        for task in repo.yield_tasks():
            if not task._complete:
                continue

            command_list = Utils.commands_to_string_list(task.command)

            for file in Utils.yield_values(task._out_files):
                if file in stat_db:
                    stat_db[file]["command"] = command_list

        Utils.save_json(stat_db, stat_db_path)

        # ------------------------------------
        # And do the same for compile_commands.json with a slightly different format.

        comp_db = {}

        for task in repo.yield_tasks():
            if not task._complete:
                continue

            command_list = Utils.commands_to_string_list(task.command)

            for file in Utils.yield_values(task._in_files):
                # Haven't tested this in an IDE, but I think it matches the spec.
                comp_db[file] = {
                    "directory" : task.cwd,
                    "command"   : command_list,
                    "file"      : file,
                }

        comp_db_path = Path.join(repo.build_root, "compile_commands.json")
        Utils.save_json(list(comp_db.values()), comp_db_path)

# endregion
# ==================================================================================================
# region Task

# FIXME get() should call eval or something? a lot of these task fields should delegate to script

class Task(Scope):

    class FAILED(Exception):    pass
    class CANCELLED(Exception): pass
    class SKIPPED(Exception):   pass
    class BROKEN(Exception):    pass

    def __init__(task, *args, **kwargs):
        super().__init__()
        script = cg.script

        task.name        = "<no name>"
        task.name        = "<no name>"
        task.desc        = "<no desc>"
        task.command     = []
        task.build_force = "{flags.build_force}"
        task.build_root  = "{script.build_root}"
        task.cwd         = "{script.root}"
        task.depformat   = "gcc" if os.name == "posix" else "msvc"
        task.in_depfile  = None
        task.job_size    = 1

        Utils.merge_variants(task, *args, **kwargs)

        if not isinstance(task.command, list):
            task.command = [task.command]

        # We can't expand here like Repo/Script/Project because we have to wait on our input tasks
        # once the build is started.

        # ----------------------------------------
        # These two have to be dicts so that downstream tasks can pick named outputs from it if
        # needed.
        task._in_files : dict[str, list[str]]  = {}
        task._out_files : dict[str, list[str]] = {}

        task._commands : list[str | task_callback] = []
        task._enabled = False

        task._input_tasks = []
        public_values = list(Utils.yield_public_values(task.__dict__))
        for t in public_values:
            if isinstance(t, Task):
                if t is not task:
                    task._input_tasks.append(t)
                else:
                    pass


        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        task._aio_task : asyncio.Task | None = None

        # We remember the aio context we were in when this task was created so that we can return
        # to it when the task starts.
        task._aio_context = contextvars.copy_context()

        # Input dependencies read from the source.o.d file.
        task._old_deplines = []

        # Why this task rebuilt, or "" if it did not need to rebuild.
        task._reason = ""

        # The "return value" for the task as a whole, or "None" if the task was successful.
        task._error : BaseException | None = None

        # Bookkeeping stuff
        task._task_id : int = -1
        task._stdout : str = ""
        task._stderr : str = ""
        task._cores = 0
        task._complete = False

        script._tasks.append(task)
        # Auto-start the task if it was created dynamically during the build.
        if Utils.in_event_loop():
            task.queue_task()

    def __dump__(task, key, opts : Dumper.Opts, seen):
        new_fold = [*opts.fold, contextvars.Context, asyncio.Task]
        new_opts = dataclasses.replace(opts, fold = new_fold)
        return Dumper._dump_vector(key, task, task.__dict__, new_opts, seen)

    # Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
    # Dicts make deep copies and we want dicts to store Tasks, so we work around it by making
    # Tasks just return themselves when copied.

    def __copy__(task):
        raise AssertionError()

    def __deepcopy__(task, _):
        raise AssertionError()

    # ==================================================================================================

    def create_aio_task(task):
        assert Utils.in_event_loop()

        if task._aio_task is None:
            t = asyncio.create_task(task.task_top(), context=task._aio_context)
            setattr(t, "hancho_task", task)  # noqa: B010
            Runner.live_aio_tasks.add(t)
            t.add_done_callback(lambda t: Runner.aio_done_queue.put_nowait(t))
            task._aio_task = t

    def queue_task(task):
        if task._enabled:
            return

        task._enabled = True
        Runner.tasks_enabled += 1

        # If this task was dynamically created during the build, add it to asyncio immediately.
        if Utils.in_event_loop():
            task.create_aio_task()

        # Start all tasks referenced by the config so we don't deadlock while waiting for them.
        # FIXME we should pull all the upstream tasks out earlier or something
        for input_task in task._input_tasks:
                input_task.queue_task()

    async def task_top(task) -> dict | None:
        # Entry point for tasks, just so we can keep all the task-level exception handling together.

        script = cg.script
        log = cg.log

        try:
            await task.task_main()

        except asyncio.CancelledError as ex:
            task.log_task(log, Log.DEBUG, f"<asyncio.CancelledError {ex}>\n")
            task._error = ex

        except Task.BROKEN as ex:
            task.log_task_exception(script, log, "Task broken!", ex)
            task._error = ex

        except Task.FAILED as ex:
            task.log_task_exception(script, log, "Task failed!", ex)
            task._error = ex

        except Task.SKIPPED as ex:
            task.log_task(log, Log.DEBUG, str(ex) + "\n")
            task._error = ex

        except Exception as ex:
            task.log_task_exception(script, log, "Task threw an exception!", ex)
            task._error = ex

        finally:
            Runner.release(task._cores)

        return task._out_files

    async def task_main(task):
        log = cg.log
        flags = cg.flags
        project = cg.project

        # Await all tasks in our input fields and then flatten them.
        await task.await_inputs()

        # We're ready to run
        Runner.tasks_started += 1
        task._task_id = Runner.tasks_started
        task.log_task(log, Log.DEBUG, Utils.instance_tag(task) + " starting\n")

        # Expand all mandatory fields in the raw config and fix raw file paths.
        Tracer.start(log, task, "range", "expand_task")
        task.expand_task()
        Tracer.end(log, task, "range", "expand_task", None)

        # If there's a depfile from a previous build, load it so we can use it below.
        if task.in_depfile:
            task._old_deplines = Utils.load_depfile(task.in_depfile, task.depformat, task.cwd)

        # Inputs are ready, templates are expanded, see if everything's sane before we try running
        # commands.
        task.sanity_check()

        # Dry runs early out after the task is initialized but before we do .exists() checks or
        # run any commands.
        if flags.build_dry:
            return

        # Paths updated. See if we need to rebuild our outputs.
        task._reason = task.rebuild_reason(project._build_reasons)
        if not task._reason:
            raise Task.SKIPPED(f"Task is up-to-date: {task.name} : {task.desc}")

        # Wait for enough jobs to free up to run this task.
        task._cores = await Runner.acquire(task.job_size)

        # Run all the task's commands
        text  = repr(task.name) if task.name else ""
        text += " : " if task.name and task.desc else ""
        text += repr(task.desc) if task.desc else ""
        task.log_task(log, Log.INFO,  Log.TEAL + f"Task {text}\n")
        task.log_task(log, Log.DEBUG, Log.GRAY2 + f"Task rebuilding because: {task._reason}\n")

        time_a = time.perf_counter()

        for command in task._commands:
            if command is None:
                continue
            elif callable(command):
                await task.call_callback(command)
            else:
                await task.run_command(command)

        time_b = time.perf_counter()

        task.log_task(log, Log.DEBUG, Log.GRAY2 + f"Task took {time_b-time_a:8.6f} sec: {text}\n")

        # See if the task wrote all its output files
        for file in Utils.yield_values(task._out_files):
            if not os.path.exists(file):
                raise Task.FAILED(f"Task ran, but output file still missing: {file}")

        # And we're done

    async def await_inputs(task):
        # NOTE: Hancho _cannot_ have dependency cycles unless you do something really sketchy via
        # modifying tasks after they're created but before they're started. If you point task B's
        # inputs at task A and task A's inputs at task B and it blows up, that's on you.

        for input_task in task._input_tasks:
            if not isinstance(input_task, Task):
                raise TypeError(f"Input task is not a task, it's a {type(input_task)!r}")

            if input_task._aio_task is None:
                raise AssertionError("One of a task's input sub-tasks was not started")

            await input_task._aio_task

            if not isinstance(input_task._error, (Task.SKIPPED, types.NoneType)):
                raise Task.CANCELLED(f"Task {hex(id(task))} is cancelled due to {input_task._error}")

    def expand_task(task):
        log = cg.log
        flags = cg.flags

        if log.level <= Log.DEBUG:
            task.log_task(log, Log.DEBUG, "Task before expand:\n")
            task.log_task(log, Log.DEBUG, Dumper.dump(task, fold=[Task], dump_private = False) + "\n")

        # We eval these first because we need them for filename expansion

        script_root = Expander.expand("{script.root}", task)
        build_root  = Expander.expand("{build_root}", task)

        Tracer.start(log, task, "range", "expand io fields")
        # Then we expand all io fields and fix their paths.
        io_fields = [k for k in task if k.startswith("in_") or k.startswith("out_")]

        for field_name in io_fields:
            # if task is a scope, then flattening in_files produces an array of the _attributes_
            # of the task instead of the out_files.

            def yield_files(f : branch[str | Task] | None) -> abc.Iterator[str]:
                if f is None:
                    return
                for x in Utils.yield_leaves(f):
                    if isinstance(x, Task):
                        for v in x._out_files.values():
                            yield from v
                    else:
                        yield x

            file_tree = cast(branch[str | Task], task[field_name])
            files : list[str] = list(yield_files(file_tree))

            if files:
                files = Expander.expand(files, task, recursive = True)
                files = task.fix_paths(field_name, files, script_root, build_root)
                task[field_name] = files[0] if len(files) == 1 else files

                if field_name == "in_depfile":
                    if len(files) > 1:
                        raise Task.BROKEN("in_depfile can't be a list")
                elif field_name.startswith("in_"):
                    task._in_files[field_name] = files
                elif field_name.startswith("out_"):
                    task._out_files[field_name] = files
        Tracer.end(log, task, "range", "expand io fields", None)

        # IO fields all expanded, we can expand the rest of the task now.

        Tracer.start(log, task, "range", "expand_task")
        ex = Expander(task, True)
        for key in task:
            task[key] = ex[key]
        Tracer.end(log, task, "range", "expand_task", None)

        task._commands = Utils.flatten_tree(task.command)

        if not flags.build_dry:
            for files in task._out_files.values():
                for file in files:
                    os.makedirs(os.path.dirname(file), exist_ok=True)
            if task.in_depfile:
                os.makedirs(os.path.dirname(task.in_depfile), exist_ok=True)

        if log.level <= Log.DEBUG:
            task.log_task(log, Log.DEBUG, "Task after expand:\n")
            task.log_task(log, Log.DEBUG, Dumper.dump(task, fold=[Task], dump_private = False) + "\n")

    def fix_paths(task : Task, field : str, files : list[str], script_root : str, build_root : str) -> list[str]:
        return [task.fix_path(field, f, script_root, build_root) for f in files]

    def fix_path(task : Task, field : str, file : str, script_root : str, build_root : str) -> str:
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

        if (field.startswith("out_") or field == "in_depfile") and not Path._startswith(file, build_root):
            file = os.path.relpath(file, script_root)
            file = os.path.join(build_root, file)
            file = os.path.abspath(file)

        return file

    def sanity_check(task):
        repo = cg.repo
        flags = cg.flags
        project = cg.project

        # Check for all task issues that break the build

        if not Path.exists(task.cwd):
            raise Task.BROKEN(f"Task working directory '{task.cwd}' does not exist")

        if not Path._startswith(task.build_root, repo.root):
            raise Task.BROKEN(f"The build root {task.build_root} is not under repo.root {repo.root}")

        # Check that task's commands are either strings or callables.
        for command in task._commands:
            if not isinstance(command, str) and not callable(command) and command is not None:
                raise Task.BROKEN(f"Command {command} is not a string or a callable?")

        # In order to provide the least amount of bafflement to users, CLI commands execute
        # from task_cwd (which is usually the root of the repo, the most common cwd)
        # and callbacks execute from dir(script_path) (because you expect to be in the same
        # directory as the script when the callback is firing).

        # This means that pre-relative-ified paths can only be rel'd to one of the two cwds, not both.
        # And that means we disallow mixed cli/callback command lists.

        for command in task._commands:
            if type(command) is not type(task._commands[0]):
                raise Task.BROKEN(f"Commands aren't the same type: {task.command}")

        # In strict mode, we mark a task broken if its command still has delimiters in it.
        if flags.strict:
            for command in task._commands:
                if Expander.is_macro(command) or Expander.is_template(command):
                    raise Task.BROKEN(f"STRICT: Command not fully expanded: {command!r}")

        io_fields = [k for k in task if k.startswith("in_") or k.startswith("out_")]
        for field_name in io_fields:
            if field_name.startswith("out_"):
                files = task[field_name]
                for file in Utils.yield_values(files):
                    if not Path._startswith(file, task.build_root):
                        raise Task.BROKEN(f"Path error, output file {field_name}:{file} is not under build root {task.build_root}")


        # Check that all build files would end up under build_root
        for file in Utils.yield_values(task._out_files):
            assert Path.isabs(file)
            if not Path._startswith(file, task.build_root):
                raise Task.BROKEN(f"Path error, output file _out_files:{file} is not under build root {task.build_root}")

        # Check for task collisions
        for file in Utils.yield_values(task._out_files):
            real_file = cast(str, Path.abspath(file))
            if real_file in project._real_filenames:
                raise Task.BROKEN(f"TaskCollision: Multiple tasks build {real_file}")
            project._real_filenames.add(real_file)

        # Check for missing inputs. We have to check build_dry, as the input files may only exist if
        # we're really running tasks.
        for file in Utils.yield_values(task._in_files):
            if not Path.isabs(file):
                raise Task.BROKEN(f"Somehow we got a non-abs path for an input file - {file}")
            if not Path.exists(file) and not flags.build_dry:
                raise Task.BROKEN(f"Input file missing - {file}")

        # Tasks should have at most one depfile.
        if isinstance(task.in_depfile, list) and len(task.in_depfile) > 1:
            raise Task.BROKEN(f"Tasks can't have more than one dependency file! - {task.in_depfile}")

    async def run_command(task : Task, command : str):
        repo = cg.repo
        log = cg.log

        task.log_task(log, Log.INFO, f"{Path.relpath(task.cwd, repo.root)}$ {command}\n")

        proc = None
        try:
            # Convert any alt delims into curly braces
            curlify = str.maketrans({"«" : "{", "»" : "}"})
            curly_command = command.translate(curlify)

            # Create the subprocess via asyncio and then await the result.
            proc = await asyncio.create_subprocess_shell(
                curly_command,
                cwd    = task.cwd,
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

        task._stdout = stdout_data.decode(errors="replace")
        task._stderr = stderr_data.decode(errors="replace")

        if proc.returncode == 2:
            raise Task.BROKEN("Command return code was 2 : bash error")
        elif proc.returncode:
            raise Task.FAILED(f"Command return code was non-zero : {proc.returncode}")

        if (task._stdout or task._stderr):
            task.log_task(log, Log.DEBUG, task.dump_stdout())

    async def call_callback(task : Task, command : task_callback):
        script = cg.script
        repo = cg.repo
        log = cg.log

        callback_dir = Path.relpath(script.root, repo.root)
        task.log_task(log, Log.INFO, f"{callback_dir}$ {command}\n")

        # Callbacks run from the script dir where they were defined so that relative paths used
        # in the callback will be correct.

        with chdir(script.root):
            result = command(task)

        # It would seem like we wouldn't have to explicitly unwrap one level of await-ness here,
        # but apparently that's just how Python waitables work.
        if inspect.isawaitable(result):
            result = await result

    def rebuild_reason(task, build_reasons) -> str:
        """Figures out why we have to run a Task, or returns "" if we don't."""

        repo = cg.repo

        # ------------------------------------
        # Check the trivial reasons to rebuild

        if task.build_force:
            build_reasons["forced"] += 1
            return "Target forced to rebuild"

        has_input = any(Utils.yield_values(task._in_files))
        if not has_input:
            build_reasons["no inputs"] += 1
            return "Always rebuild a target with no inputs"

        has_output = any(Utils.yield_values(task._out_files))
        if not has_output:
            build_reasons["no outputs"] += 1
            return "Always rebuild a target with no outputs"

        # ------------------------------------

        for filename in Utils.yield_values(task._in_files):
            if reason := repo.check_stats(filename, [], build_reasons):
                return reason

        for filename in task._old_deplines:
            if reason := repo.check_stats(filename, [], build_reasons):
                return reason

        for filename in Utils.yield_values(task._out_files):
            if reason := repo.check_stats(filename, task.command, build_reasons):
                return reason

        if task.in_depfile and (reason := repo.check_stats(task.in_depfile, [], build_reasons)):
            return reason

        build_reasons["*task clean"] += 1
        return ""

    def dump_stdout(task : Task) -> str:
        result = ""
        if task._stdout:
            result += "---------------- Stdout ----------------\n"
            result += task._stdout.strip() + "\n"
        if task._stderr:
            result += "---------------- Stderr ----------------\n"
            result += task._stderr.strip() + "\n"
        if task._stdout or task._stderr:
            result += "----------------------------------------\n"
        return result

    def log_task(task : Task, log : Log, level : int, message : str):
        # Log helper that adds the [ NN/ XX] tag before the log line.
        for line in message.splitlines(keepends=True):
            if not log._line_buffer:
                log._log(level, f"[{task._task_id:3d}/{Runner.tasks_enabled:3d}] ")
            log._log(level, line)

    def log_task_exception(task : Task, script, log : Log, message, ex = None):
        log.error("========================================\n")
        log.error(message + "\n")
        log.error("========================================\n")
        log.error(f"Script    = {script.path}:\n")
        log.error(f"Task      = {task.name!r} : {task.desc!r}\n")
        log.error(f"os.getcwd = {os.getcwd()}\n")
        log.error(f"task cwd  = {task.cwd}\n")
        log.error(f"command   = {task.command}\n")
        log.exception(ex)
        log.error(task.dump_stdout())
        log.error("========================================\n")

# endregion
# ==================================================================================================
# region Script

# This is not a types.ModuleType, but it still works fine as one. Making it a ModuleType is a bit
# annoying to work with.

class Script(Scope):

    def __init__(script, name, path, root, *args, **kwargs):
        super().__init__()

        script.name = name
        script.path = path
        script.root = root
        script.build_root = "{join(repo.build_root, relpath(root, repo.root))}"
        script.hancho = sys.modules[__name__]

        script.update(*args, **kwargs)

        # The Python module loading machinery looks for this field, but it can be None.
        script.__spec__ = None
        script._tasks = []

    def dump(script, *args, **kwargs):
        dprint(Dumper.dump(*args, **kwargs))

    def load(script, file, root, is_repo, *args, **kwargs):
        project = cg.project
        repo = cg.repo

        file = Expander.expand(file, script, True)

        path = Path._resolve(file, strict = True)
        if root is None:
            root = os.path.dirname(path)
        root = Path._resolve(root, strict = True)
        name = os.path.join(os.path.split(root)[-1], os.path.basename(file))

        if is_repo:
            repo = Repo(name, path, root)
            project._repos.append(repo)

        return Script._load(name, path, root, project, repo, *args, **kwargs)

    # ----------------------------------------

    def _log_script_error(script, log, frame, condition, message):
        log.error(f"Script {condition}:\n")
        log.error(f"  text = '{message}'\n")
        log.error(f"  file = {frame.f_code.co_filename}\n")
        log.error(f"  func = {frame.f_code.co_name}\n")
        log.error(f"  line = {frame.f_lineno}\n")

    @staticmethod
    def _load(name : str, path : str, root : str, project : Project, repo : Repo, *args, **kwargs) -> Script:
        assert path == Path.resolve(path)
        assert root == Path.resolve(root)

        log = cg.log
        script = Script(name, path, root, *args, **kwargs)

        # Dedupe the load - only scripts with identical real paths and identical configs are
        # deduped. This relies on the fields read by Dumper.dump being stable during a build,
        # which they should be in practice.
        dupe_key = Dumper.any_to_key(script)

        # FIXME need to make sure private fields aren't in the dump, but i'll deal with it properly later.
        assert " _" not in dupe_key

        if dupe := project._dedupe.get(dupe_key, None):
            log.debug(Log.LIME + f"Deduped {script.path}\n")
            return dupe
        else:
            log.debug(Log.ORANGE + f"Loading {script.path}\n")

        repo._scripts.append(script)
        project._dedupe[dupe_key] = script

        if not script.path.endswith(".hancho"):
            return script

        code = None
        with open(script.path, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, script.path, "exec", dont_inherit=True)

        with chdir(script.root), cg.update(repo = repo, script = script), log.indenter(Log.ORANGE):
            exec(code, script.__dict__)
            del script.__dict__['__builtins__']
            return script

    @staticmethod
    def init_for_testing(*, name, file : str, root : str, stdout : str | typing.TextIO, args) -> Script:
        flags = Flags.parse(
            f"--name={name}",
            f"--file={file}",
            f"--root={root}",
            *args
        )

        log = Log(flags, stdout)
        cg.reset(flags = flags, log = log)

        Utils.reset()
        Runner.reset(flags)

        # ------------------------------------

        test_name = flags.name
        test_path = Path._resolve(flags.file, strict = True)
        test_root = Path._resolve(flags.root, strict = True)

        # ------------------------------------

        project = Project(test_name, test_path, test_root, [])
        cg.update(project = project)

        repo = Repo(test_name, test_path, test_root)
        project._repos.append(repo)
        cg.update(repo = repo)

        script  = Script._load(test_name, test_path, test_root, project, repo)
        cg.update(script = script)

        return script

# endregion
# ==================================================================================================
# region main

def main():
    flags = Flags.parse(*sys.argv)
    log = Log(flags, sys.stdout)

    log.info(f"Command line : {" ".join(sys.argv)}\n")
    log.trace("Trace mode on\n")
    log.debug("Debug mode on\n")

    cg.reset(flags = flags, log = log)

    # We expand these manually because we need to *resolve* 'path' _before_ we *expand* 'root'.

    flags.name = Expander.expand(flags.name, flags)
    flags.path = Expander.expand(flags.path, flags)
    flags.path = Path._resolve(flags.path, strict = True)
    flags.root = Expander.expand(flags.root, flags)
    flags.root = Path._resolve(flags.root, strict = True)

    project = Project(flags.name, flags.path, flags.root, flags.targets)
    repo = Repo(project.name, project.path, project.root)
    project._repos.append(repo)

    Utils.reset()
    Runner.reset(flags)

    cg.update(project = project, repo = repo)

    time_a1 = time.perf_counter()
    Script._load(project.name, project.path, project.root, project, repo)
    time_b1 = time.perf_counter()
    log.info(Log.BLUE + f"Loading project took {time_b1 - time_a1:8.6f} seconds\n")

    project.build(log, flags)
    project.stats(log)
    project.passfail(log)

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

cg = ContextGlobals()

path = __file__
root = os.path.dirname(path)

sys.modules["hancho"] = sys.modules[__name__]

class EarlyOut(Exception): pass
class Fail(Exception): pass
class Abort(Exception): pass

def script(file, root = None, *args, **kwargs):
    return cg.script.load(file, root, False, *args, **kwargs)

def include(file, root = None, *args, **kwargs):
    return cg.script.load(file, root, False, *args, **kwargs)

def repo(file, root = None, *args, **kwargs) -> Script:
    return cg.script.load(file, root, True, *args, **kwargs)

def build():
    return cg.project.build(cg.log, cg.flags)

def fail(message):
    cg.script._log_script_error(cg.log, sys._getframe(1), "failed", message)
    raise Fail(message)

def abort(message):
    cg.script._log_script_error(cg.log, sys._getframe(1), "aborted", message)
    raise Abort(message)

def earlyout(message = ""):
    cg.script._log_script_error(cg.log, sys._getframe(1), "exited early", message)
    raise EarlyOut(message)

# endregion
# ==================================================================================================

if __name__ == "__main__":
    sys.exit(_start())
