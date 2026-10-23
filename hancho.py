
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

# endregion
# ==================================================================================================
# region Constants and globals

def is_public(key):
    if not isinstance(key, str):
        breakpoint()
    return key is None or not key.startswith("_")

def is_private(key):
    if not isinstance(key, str):
        breakpoint()
    return key is not None and key.startswith("_")

def dprint(*args, **kwargs):
    print("DBG: ", end="")
    print(*args, **kwargs)

type branch[LEAF] = LEAF | abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]
type tree[LEAF] = abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]
type task_callback = abc.Callable[[Task], Any]

# The above needs to be sequence/set/mapping in order for this to typecheck:
# a : str = "foo"
# b : tree[str] = a
# c : list[str] = ["bar"]
# d : tree[str] = c

type item[T] = tuple[int | str | None, T]

# endregion
# ==================================================================================================
# region Sentinel

class Sentinel:
    def __init__(self, name):     self.name = name
    def __repr__(self):           return self.name
    def __bool__(self):           return False
    def __copy__(self):           return self
    def __deepcopy__(self, memo): return self
    def __reduce__(self):         return self.name
    def __dump__(self, key, opts, seen):
        new_opts = dataclasses.replace(opts, print_id = False)
        return Dumper._dump_prefix(key, self, new_opts) + Log.VIOLET + repr(self)

    MISSING : Any
    INHERIT : Any

# Marks a function argument as "not present" for when None would be a valid value.
Sentinel.MISSING = Sentinel("MISSING")

# Marks a name as resolvable _from_ a scope but not _in_ it. Mostly just used to document where
# fields come from during expansion.
Sentinel.INHERIT = Sentinel("INHERIT")

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

    @staticmethod
    def is_scope(v) -> TypeGuard[Scope]:
        return isinstance(v, Scope)

    @staticmethod
    def is_tree(variant : Any, leaftype : type):
        return all(isinstance(x, leaftype) for x in Utils.yield_values(variant))

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
        if isinstance(obj, Scope) and "_name" in obj and obj._name is not None:
            result += obj._name
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
    def merge_variants(
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
                Utils.merge_variants(lhs2, rhs2)
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

    def __init__(self, stdout):

        self.level :int        = cast(int, getattr(Log, cg.flags.find("log_level", "info").upper()))
        self.wrap : bool       = cg.flags.find("log_wrap", False)
        self.color : bool      = cg.flags.find("log_color", True)
        self.timestamp : bool  = cg.flags.find("log_timestamp", True)
        self.width : int       = cg.flags.find("log_width", shutil.get_terminal_size().columns)

        assert type(self.level) is int

        self._time_origin   = time.perf_counter()
        self._indent_stack  = []
        self._line_buffer   = ""
        self._match_escapes = re.compile(r"(\x1B.*?m)")

        if isinstance(stdout, str):
            self._stdout = open(stdout, "w")  # noqa: SIM115
        else:
            self._stdout = stdout

    def indent(self, ansi_color : str = RESET):
        self._indent_stack.append(f"{ansi_color}│{Log.RESET} ")

    def dedent(self):
        self._indent_stack.pop()

    @contextmanager
    def indenter(self, ansi_color : str = RESET):
        self.indent(ansi_color)
        yield
        self.dedent()

    def trace(self, text):
        # Traces do their own color coding
        self._log(Log.TRACE, text)

    def debug(self, text):
        self._log(Log.DEBUG, Log.GRAY3 + text)

    def info(self, text):
        self._log(Log.INFO, text)

    def warning(self, text):
        self._log(Log.WARNING, Log.YELLOW + text)

    def error(self, text):
        self._log(Log.ERROR, Log.ORANGE + text)

    def critical(self, text):
        self._log(Log.CRITICAL, Log.RED + text)

    def exception(self, ex):
        if ex is None:
            return
        tb = traceback.extract_tb(ex.__traceback__)
        if tb:
            frame = tb[-1]
            self.error("exception:\n")
            self.error(f"  type = {type(ex)}\n")
            self.error(f"  text = {ex}\n")
            self.error(f"  file = {frame.filename}\n")
            self.error(f"  func = {frame.name}\n")
            self.error(f"  line = {frame.lineno}\n")
            self.error(traceback.format_exc() + "\n")
        else:
            self.error(f"Could not extract traceback from {ex}!")

    def _log(self, log_level : int, text : str):
        if log_level < self.level:
            return
        if not isinstance(text, str) or len(text) == 0:
            return

        # FIXME str.partition might be easier here
        lines = text.splitlines(keepends=True)
        for line in lines:
            if self._line_buffer == "":
                self._line_buffer += f"[{time.perf_counter() - self._time_origin:8.3f}] " if self.timestamp else ""
                self._line_buffer += "".join(self._indent_stack)

            if line[-1] == "\n":
                self._line_buffer += line[:-1]
                self._line_buffer += Log.RESET
                self._line_buffer += "\n"
                self._flush()
            else:
                self._line_buffer += line
                self._line_buffer += Log.RESET

    def _flush(self):
        # Dumps the line buffer to stdout and then clears it.
        if self._line_buffer:
            # If we want a non-colorized log, strip color codes from the line.
            if not self.color:
                self._line_buffer = re.sub(self._match_escapes, "", self._line_buffer)

            # If the line wasn't finished (because we're exiting the app), stick a newline on it.
            if self._line_buffer[-1] != "\n":
                self._line_buffer += "\n"

            if not self.wrap:
                _ = self.width
                self._line_buffer = self._clip_printable(self._line_buffer, self.width)

            self._stdout.write(self._line_buffer)
            self._line_buffer = ""

    def _clip_printable(self, text : str, width : int) -> str:
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
        chunks = self._match_escapes.split(text)

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
            cg.log.error(f"Commonpath failed for {lhs} and {rhs}\n")
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

# The Scope object is the basic building block of our template variable resolution tree.
# It exposes all "public" attributes (that don't start with an underscore) through a MutableMapping
# interface, and adjusts __deepcopy__ to only deep-copy public attributes (private attributes are
# shallow-copied).

# FIXME Pretty sure at this point that a Scope should _not_ be a Mapping.

class Scope:
    def __init__(self, *, _name, _id, _up):
        # If you ask a scope to find _id, it returns _itself_.
        self._name = _name
        self._id   = _id
        self._up  = _up

    def find(self, key, default = Sentinel.MISSING) -> Any:
        """Walk the _up chain until we find the key or run out of scopes."""

        # If you ask a scope to find _id, it returns _itself_.
        if key == self._id:
            return self

        result = self.__dict__.get(key, Sentinel.INHERIT)
        if result is not Sentinel.INHERIT:
            return result
        if self._up is not None:
            return self._up.find(key, default)
        if default is not Sentinel.MISSING:
            return default
        raise KeyError(key)

    def update(self, *args, **kwargs):
        for rhs in [*args, kwargs]:
            Utils.merge_variants(self, rhs)



    def expand_field(self, key : Any, recursive : bool = False) -> Any:
        val = self.find(key)
        return Expander.expand(val, self, recursive)

    def eval(self, expr : str, recursive : bool = False):
        return Expander.expand("{" + expr + "}", self, recursive)

    def xip(self, key : str, /, recursive = True):
        """Expand-in-place. Replaces a field with its expanded version."""
        result = self.find(key)
        result = Expander.expand(result, self, recursive)
        self[key] = result
        return result

    # Fill-in-the-blank (or override what's there): Merges lhs and args into a new Dict, keeping
    # only keys that were already in lhs. For example, if you have a Dict that contains
    # "out_bin" and you merge it with "compile_cpp", Hancho will complain that "out_bin" is missing
    # - it sees both "out_obj" and "out_bin" and assumes the task produces both. If you do
    # compile_cpp.fill(...), "out_bin" does not get added to compile_cpp.

    def fill(self, *args, **kwargs):
        for rhs in (*args, kwargs):
            Utils.merge_variants(self, rhs, keep_rhs = False)

    # ----------------------------------------
    # These are here so that the linter doesn't complain about reading/writing unknown fields off a
    # scope.

    # I don't think we want to walk _up for attribute accesses, we need at least one "raw" access
    # method... UNLESS it is explicitly set to Sentinel.INHERIT

    def __getattr__(self, key) -> Any:
        result = object.__getattribute__(self, key)
        if result is Sentinel.INHERIT:
            return self.find(key)
        else:
            return result

    def __setattr__(self, key, val : Any):
        object.__setattr__(self, key, val)

    # ----------------------------------------
    # Scope is _not_ a mapping, but it implements the core of MutableMapping so that it only
    # exposes public fields and traverses _up if a key is not in the current scope.

    def __contains__(self, key : str) -> bool:
        return key in self.__dict__

    def __getitem__(self, key : str) -> Any:
        if is_private(key):
            raise KeyError(key)
        result = self.find(key)
        return result

    def __setitem__(self, key, val):
        if is_private(key):
            raise KeyError(key)
        self.__dict__[key] = val

    def __delitem__(self, key):
        if is_private(key):
            raise KeyError(key)
        del self.__dict__[key]

    def __len__(self):
        return sum(1 for _ in self)

    def __iter__(self):
        yield from filter(is_public, self.__dict__)

# ====================================

class Dict(Scope, abc.Mapping):
    def __init__(self, *args, **kwargs):
        super().__init__(
            _name = kwargs.pop("_name", "<none>"),
            _id   = kwargs.pop("_id",   "<none>"),
            _up   = kwargs.pop("_up",   None)
        )
        self.update(*args, **kwargs)

    def expand(self, template, recursive : bool = False) -> Any:
        return Expander.expand(template, self, recursive)


class Tool(Dict):
    # Tool is just an alias for Dict to make build scripts more readable.

    def __call__(self, *args, **kwargs):
        return cg.script.Task(self, *args, **kwargs)

    pass

class Data(Dict):
    # Same thing
    pass

# endregion
# ==================================================================================================
# region Aliases

class Aliases(Dict):
    def __init__(self):
        super().__init__(_name = "aliases", _id = "aliases", _up = None)

        self.getcwd   = os.getcwd

        self.dprint   = dprint
        self.dump     = Dumper.dprint

        self.flatten  = Utils.flatten_tree
        self.run_cmd  = Utils.run_cmd
        self.weave    = Utils.weave

        self.resolve  = Path.resolve
        self.abspath  = Path.abspath
        self.normpath = Path.normpath
        self.relpath  = Path.relpath
        self.basename = Path.basename
        self.dirname  = Path.dirname
        self.split    = Path.split
        self.join     = Path.join
        self.swapext  = Path.swapext

# endregion
# ==================================================================================================
# region Flags

class Flags(Dict):
    def __init__(self, *args, **kwargs):
        super().__init__(_name = "flags", _id = "flags", _up = Aliases())

        self.file        = "build.hancho"
        self.name        = "{join(split(flags.root)[-1], basename(flags.file))}"
        self.path        = "{resolve(flags.file)}"
        self.root        = "{dirname(flags.path)}"
        self.targets     = []

        self.build_all   = False
        self.build_dir   = "build"
        self.build_dry   = False
        self.build_force = False
        self.build_root  = "{join(repo.root, repo.build_dir, repo.build_tag)}"
        self.build_tag   = ""
        self.cwd         = "{script.root}"
        self.depformat   = "gcc" if os.name == "posix" else "msvc"
        self.in_depfile  = None
        self.job_size    = 1
        self.max_errors  = 0
        self.max_jobs    = os.cpu_count() or 1
        self.strict      = True
        self.tool        = None
        self.stdout      = sys.stdout

        self.log_level     = "info"
        self.log_wrap      = False
        self.log_color     = True
        self.log_timestamp = True
        self.log_width     = shutil.get_terminal_size().columns
        self.log_stdout    = self.stdout

        self.update(*args, **kwargs)

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
        parser.add_argument(      "--log_stdout",    type=str,         help="Redirect stdout to the given file")

        # fmt: on

        flag_dict = Dict()
        (argv_vars, unrecognized) = parser.parse_known_args(argv if argv else [])

        # ------------------------------------
        # Load flags from opt_file if present

        opt_file_name = argv_vars.__dict__.pop("project.opts", None)
        #loaded_opts = False
        if opt_file_name and os.path.exists(opt_file_name):
            with open(opt_file_name) as f:
                try:
                    opt_file = json.load(f)
                    #loaded_opts = True
                    flag_dict.update(opt_file)
                except Exception as _:
                    cg.log.error(f"Opt file {opt_file_name!r} invalid!\n")
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
# region Globals

class Globals(Scope):
    def __init__(self):
        #super().__init__(_name = "globals", _id = "globals", _up = None)
        object.__setattr__(self, "_name", "globals")
        object.__setattr__(self, "_id",   "globals")
        object.__setattr__(self, "_up",   None)
        object.__setattr__(self, "_ctx",  ContextVar("globals"))
        self.reset()

    def reset(self):
        return self._ctx.set(Dict())

    # Update creates a whole new Dict so that if anyone was holding a reference to the old Dict
    # their copy will remain unchanged.
    def update(self, *args, **kwargs):
        new_ctx = Dict(self._ctx.get(), *args, **kwargs)
        return self._ctx.set(new_ctx)

    def __getattr__(self, key):
        try:
            return self.__getitem__(key)
        except KeyError as err:
            raise AttributeError(key) from err

    def __setattr__(self, key, val):
        try:
            self.__setitem__(key, val)
        except KeyError as err:
            raise AttributeError(key) from err

    def __getitem__(self, key):
        return self._ctx.get()[key]

    # We do _not_ allow incrementally modifying a context, as doing so could allow inadvertent
    # modification of a context already captured by an earlier task.
    def __setitem__(self, key, val):
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

    def __init__(self, scope : Scope, recursive : bool):
        self.scope : Scope
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "recursive", recursive)

    @classmethod
    def wrap(cls, scope : Scope, recursive : bool):
        assert not isinstance(scope, cls)
        return cls(scope, recursive)

    # ====================================

    def __getattr__(self, key) -> Any:
        try:
            return self.internal_get(key, check_up = False)
        except KeyError as ex:
            raise AttributeError from ex

    def __getitem__(self, key : str) -> Any:
        return self.internal_get(key, check_up = True)

    def __iter__(self):
        return self.scope.__iter__()

    def __len__(self):
        return self.scope.__len__()

    # ====================================

    def internal_get(self, key : str, check_up : bool = False):

        Tracer.start(self.scope, "get", key)
        result = self.scope.find(key)

        if isinstance(result, Scope):
            # have to do this so that "read nested c first" resolves in the dest dict first
            result = self.wrap(result, object.__getattribute__(self, "recursive"))
        else:
            result = Expander.expand(result, self.scope, recursive = True)

        Tracer.end(self.scope, "get", key, result)
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
    def xip(cls, scope : Scope, *, recursive : bool):
        try:
            Tracer.start(scope, "range", "xip")
            for key in scope:
                assert is_public(key)
                ex = Expander(scope, recursive)
                scope[key] = ex[key]
            return scope
        finally:
            Tracer.end(scope, "range", "xip", None)

    @classmethod
    def expand(cls, var : Any, scope : Scope, /, recursive : bool = False) -> Any:
        result = cls._expand(var, scope, recursive)
        assert not isinstance(result, cls)
        return result

    @classmethod
    def _expand(cls, var : Any, scope : Scope, recursive : bool) -> Any:
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
                    Tracer.start(scope, "expand", var)
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
                    Tracer.end(scope, "expand", old_var, var)

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
    def _eval_macro(cls, var : str, scope : Scope, recursive : bool) -> Any:
        # Bail out if we've done too many evals already.
        old_evals = cls.cv_evals.get()
        if old_evals >= cls.MAX_EVALS:
            raise RecursionError(f"Expansion failed to terminate after {old_evals} evals: {var!r}")
        cls.cv_evals.set(old_evals + 1)

        result = var
        try:
            Tracer.start(scope, "eval", var[1:-1])
            result = eval(var[1:-1], {}, cls.wrap(scope, recursive))
        except RecursionError:
            raise
        except Exception as ex:
            _ = ex
            # If you uncomment this line, things will be super spammy.
            #dprint(f"##########\neval failed because >{ex}<\n##########\n")
            pass
        finally:
            Tracer.end(scope, "eval", var[1:-1], result)
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
                return f"{key}:{type(val).__name__} = {Log.BLUE}<folded>"

        if key is not None and is_private(key) and not opts.dump_private:
            return ""
        elif key == "__builtins__":
            return cls._dump_prefix(key, val, opts) + "<builtins>"
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

        if isinstance(val, Scope):
            items = [x for x in items if x[0] != "_up"]
        if not opts.dump_private:
            items = [x for x in items if is_public(x[0])]
            items.append(("<private fields are hidden>", Sentinel.MISSING))

        return cls._dump_items(key, prefix, ld, items, rd, opts, set(seen))

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
    def start(scope : Scope, action : str, arg):
        if isinstance(scope, Expander):
            scope = scope.scope

        scope_tag    = Utils.instance_tag(scope)
        scope_color  = Utils.obj_to_ansi_color(scope)
        indent_color = scope_color

        if action == "range":
            indent_color = Log.WHITE
            cg.log.trace(f"{indent_color}┌─── {scope_color}{scope_tag}{Log.RESET} begin {arg}\n")
            cg.log.indent(indent_color)
        else:
            cg.log.trace(f"{indent_color}┌ {scope_color}{scope_tag}{Log.RESET}.{action}({arg!r})\n")
            cg.log.indent(indent_color)

    @staticmethod
    def up(scope, up, action, arg):
        if isinstance(scope, Expander):
            scope = scope.scope

        scope_color = Utils.obj_to_ansi_color(scope)
        scope_tag   = Utils.instance_tag(scope)
        up_color   = Utils.obj_to_ansi_color(up)
        up_tag     = Utils.instance_tag(up)

        cg.log.trace(f"{scope_color}┌ {scope_tag}{Log.RESET}.{action}({arg!r}) -> {up_color}{up_tag}\n")

    @staticmethod
    def end(scope : Scope, action : str, arg, result):
        cg.log.dedent()
        if isinstance(result, Expander):
            result = result.scope

        scope_tag   = Utils.instance_tag(scope)
        scope_color   = Utils.obj_to_ansi_color(scope)
        indent_color = scope_color

        if action == "range":
            indent_color = Log.WHITE
            cg.log.trace(f"{indent_color}└─── {scope_color}{scope_tag}{Log.RESET} end {arg}\n")
        else:
            result_color = Log.RESET
            result_type  = type(result)

            if Utils.is_mapping(result):
                result_color = Utils.obj_to_ansi_color(result)

            cg.log.trace(f"{indent_color}└ {scope_color}{arg!r}:{result_type.__name__}{Log.RESET} = ")
            if not Utils.is_scalar(result):
                cg.log.trace(f"{result_color}{Utils.instance_tag(result)}\n")
            else:
                cg.log.trace(f"{result_color}{result!r}\n")

# endregion
# ==================================================================================================
# region Runner

class Runner:

    @classmethod
    def reset(cls):
        cls.max_jobs   = cg.flags.find("max_jobs")
        cls.max_errors = cg.flags.find("max_errors")

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
    async def async_run_tasks(project : Project):
        """Run all tasks until we run out."""

        # ------------------------------------
        # Create asyncio tasks for all enabled Hancho tasks.

        for repo in project._repos:
            for task in repo.yield_tasks():
                if task._enabled:
                    task.create_aio_task()

        # ------------------------------------
        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.

        cg.log.info(Log.BLUE + "Running tasks...\n")

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
                    cg.log.warning(f"Weird exception {type(task._error)} >{task._error}< at {time.perf_counter()}\n")
                    cg.log.exception(task._error)
                    Runner.tasks_failed += 1

            if finished_aio_task is not None:
                Runner.live_aio_tasks.discard(finished_aio_task)

        failures = Runner.tasks_broken + Runner.tasks_failed
        if failures > Runner.max_errors:
            cg.log.error(f"Too many failures after {failures}, cancelling tasks and stopping build\n")

            # Cancel all the asyncio.Tasks that haven't completed yet
            cg.log.warning(f"Cancelling {len(Runner.live_aio_tasks)} tasks\n")

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
    def __init__(self, name : str, path : str, root : str, *args, **kwargs):
        super().__init__(
            _name = name,
            _id   = "project",
            _up  = cg.flags
        )

        self.name : str = name
        self.path : str = path
        self.root : str = root
        self.targets : list[str] = Sentinel.INHERIT

        self.update(*args, **kwargs)
        Expander.xip(self, recursive = True)

        assert self.path == Path.resolve(self.path)
        assert self.root == Path.resolve(self.root)

        self._real_filenames = set()
        self._dedupe = Dict()
        self._repos : list[Repo] = []
        self._build_reasons = Counter()
        self._runner = Runner()

    def build(self) -> int:
        time_a3 = time.perf_counter()

        #result = hancho_build(project)

        # ------------------------------------
        # This must happen _after_ all repos are loaded (so that if they change repo_root we don't
        # get the old path), but _before_ we build any tasks.

        # Also this is here and not in hancho_main because tests also need to load stats.
        for repo in self._repos:
            repo.load_stat_db()

        # ------------------------------------
        # Select the set of tasks to run.

        if self.targets:
            for target in self.targets:
                # Enable all tasks whose name contains any of the targets
                for repo in self._repos:
                    for task in repo.yield_tasks():
                        if target in task.name:
                            task.queue_task()

        elif self.find("build_all"):
            for repo in self._repos:
                for task in repo.yield_tasks():
                    task.queue_task()

        else:
            # Enable all tasks in the top repo
            for task in self._repos[0].yield_tasks():
                task.queue_task()

        # ------------------------------------
        # Run the tasks.

        result = asyncio.run(Runner.async_run_tasks(self))

        # ------------------------------------
        # Update stat DBs.

        for repo in self._repos:
            repo.save_stat_db()

        time_b3 = time.perf_counter()
        cg.log.info(Log.GREEN + f"Build took {time_b3 - time_a3:8.6f} seconds\n")
        self._result = result
        return result

    def stats(self):
        task_count = 0
        for repo in self._repos:
            task_count += len(list(repo.yield_tasks()))

        # FIXME all the stats should move to Project
        cg.log.debug(f"Tasks created:    {task_count}\n")
        cg.log.debug(f"Tasks enabled:    {Runner.tasks_enabled}\n")
        cg.log.debug(f"Tasks started:    {Runner.tasks_started}\n")
        cg.log.debug(f"Tasks finished:   {Runner.tasks_finished}\n")
        cg.log.debug(f"Tasks broken:     {Runner.tasks_broken}\n")
        cg.log.debug(f"Tasks failed:     {Runner.tasks_failed}\n")
        cg.log.debug(f"Tasks cancelled:  {Runner.tasks_cancelled}\n")
        cg.log.debug(f"Tasks skipped:    {Runner.tasks_skipped}\n")
        cg.log.debug(f"Mtime calls:      {Utils.stat_calls}\n")
        cg.log.debug(f"Hash calls:       {Utils.hash_calls}\n")
        cg.log.debug(f"Hash bytes:       {Utils.hash_bytes}\n")
        cg.log.debug(f"Hash time:        {Utils.hash_time:8.6f}\n")

    def passfail(self):
        if self._runner.tasks_failed or self._runner.tasks_broken:
            cg.log.error(Log.RED + "BUILD FAILED\n")
        elif Runner.tasks_finished:
            cg.log.info(Log.GREEN + "BUILD PASSED\n")
        else:
            cg.log.info(Log.BLUE + "BUILD CLEAN\n")

# endregion
# ==================================================================================================
# region Repo

class Repo(Scope):
    def __init__(self, name : str, path : str, root : str, *args, **kwargs):
        super().__init__(
            _name = name,
            _id   = "repo",
            _up  = cg.project
        )

        self.name = name
        self.path = path
        self.root = root
        self.build_root = Sentinel.INHERIT

        self.update(*args, **kwargs)
        Expander.xip(self, recursive = True)

        self._stat_db = {}
        self._scripts = []

    def yield_tasks(self) -> abc.Iterator[Task]:
        for script in self._scripts:
            yield from script._tasks

    def check_stats(self, filename : str, command : list):
        repo = self
        project = self.find("project")

        if not Path.exists(filename):
            project._build_reasons["file missing"] += 1
            return f"File missing: {filename}"

        if filename not in repo._stat_db:
            project._build_reasons["stat missing"] += 1
            return f"Stat missing: {filename}"

        old_stat = repo._stat_db[filename]
        new_stat = Utils.get_stats(filename, command)

        try:
            old_ns = old_stat["st_mtime_ns"]
            new_ns = new_stat["st_mtime_ns"]

            if old_ns != new_ns:
                project._build_reasons["mtime mismatch"] += 1
                return f"Mtime mismatch {old_ns} != {new_ns} for : {filename}"
        except Exception as err:
            traceback.print_exc()
            raise err

        if old_stat["st_size"] != new_stat["st_size"]:
            project._build_reasons["size mismatch"] += 1
            return f"Size mismatch {old_stat["st_size"]} != {new_stat["st_size"]} for : {filename}"

        if old_stat["hash"] != new_stat["hash"]:
            project._build_reasons["hash mismatch"] += 1
            return f"Hash mismatch {old_stat["hash"]} -> {new_stat["hash"]} for : {filename}"

        old_command = old_stat["command"]
        new_command = Utils.commands_to_string_list(command)

        if old_command != new_command:
            project._build_reasons["command changed"] += 1
            return f"Command used to generate file has changed : {filename!r}\n : {old_command!r}n : {command!r}"

        # Does not need to rebuild based on file stats / hash
        project._build_reasons["*hash match"] += 1
        return ""

    def load_stat_db(self):
        repo = self
        stat_db_path = os.path.join(repo.build_root, 'hancho.json')

        if os.path.isfile(stat_db_path):
            with open(stat_db_path) as contents:
                cg.log.debug(Log.ORANGE + f"Loading stat_db {stat_db_path}\n")
                repo._stat_db = json.load(contents)
        else:
            cg.log.debug(Log.ORANGE + f"No stat db for {repo.root}\n")
            repo._stat_db = {}

    def save_stat_db(self):
        repo = self
        if repo.find("build_dry"):
            return

        stat_db_path = Path.join(repo.build_root, "hancho.json")
        cg.log.debug(Log.ORANGE + f"Saving stat_db {stat_db_path}\n")

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

    def __init__(self, *args, **kwargs):
        super().__init__(
            _name = "<unexpanded task>",
            _id   = "task",
            _up  = cg.script
        )

        self.name    : str       = "<no name>"
        self.name    : str       = "<no name>"
        self.desc    : str       = "<no desc>"
        self.command : tree[str | task_callback] = []

        self.build_dry   : bool  = Sentinel.INHERIT
        self.build_force : bool  = Sentinel.INHERIT
        self.build_root  : str   = Sentinel.INHERIT
        self.cwd         : str   = Sentinel.INHERIT
        self.depformat   : str   = Sentinel.INHERIT
        self.in_depfile  : str   = Sentinel.INHERIT
        self.job_size    : int   = Sentinel.INHERIT

        self.update(*args, **kwargs)

        if not isinstance(self.command, list):
            self.command = [self.command]

        # We can't expand here like Repo/Script/Project because we have to wait on our input tasks
        # once the build is started.

        # ----------------------------------------
        # These two have to be dicts so that downstream tasks can pick named outputs from it if
        # needed.
        self._in_files : dict[str, list[str]]  = {}
        self._out_files : dict[str, list[str]] = {}

        self._commands : list[str | task_callback] = []
        self._enabled = False

        #self._input_tasks = [t for t in Utils.yield_public_values(self.__dict__) if isinstance(t, Task) and t is not self]
        self._input_tasks = []
        public_values = list(Utils.yield_public_values(self.__dict__))
        for t in public_values:
            if isinstance(t, Task):
                if t is not self:
                    self._input_tasks.append(t)
                else:
                    pass


        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._aio_task : asyncio.Task | None = None

        # We remember the aio context we were in when this task was created so that we can return
        # to it when the task starts.
        self._aio_context = contextvars.copy_context()

        # Input dependencies read from the source.o.d file.
        self._old_deplines = []

        # Why this task rebuilt, or "" if it did not need to rebuild.
        self._reason = ""

        # The "return value" for the task as a whole, or "None" if the task was successful.
        self._error : BaseException | None = None

        # Bookkeeping stuff
        self._task_id : int = -1
        self._stdout : str = ""
        self._stderr : str = ""
        self._cores = 0
        self._complete = False

        cg.script._tasks.append(self)
        # Auto-start the task if it was created dynamically during the build.
        if Utils.in_event_loop():
            self.queue_task()

    def __dump__(self, key, opts : Dumper.Opts, seen):
        new_fold = [*opts.fold, contextvars.Context, asyncio.Task]
        new_opts = dataclasses.replace(opts, fold = new_fold)
        return Dumper._dump_vector(key, self, self.__dict__, new_opts, seen)


    # Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
    # Dicts make deep copies and we want dicts to store Tasks, so we work around it by making
    # Tasks just return themselves when copied.

    def __copy__(self):
        return self

    def __deepcopy__(self, _):
        return self

    # ==================================================================================================

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

        except asyncio.CancelledError as ex:
            self.log_task(Log.DEBUG, f"<asyncio.CancelledError {ex}>\n")
            self._error = ex

        except Task.BROKEN as ex:
            self.log_task_exception("Task broken!", ex)
            self._error = ex

        except Task.FAILED as ex:
            self.log_task_exception("Task failed!", ex)
            self._error = ex

        except Task.SKIPPED as ex:
            self.log_task(Log.DEBUG, str(ex) + "\n")
            self._error = ex

        except Exception as ex:
            self.log_task_exception("Task threw an exception!", ex)
            self._error = ex

        finally:
            Runner.release(self._cores)

        return self._out_files

    async def task_main(self):
        # Await all tasks in our input fields and then flatten them.
        await self.await_inputs()

        # We're ready to run
        Runner.tasks_started += 1
        self._task_id = Runner.tasks_started
        self.log_task(Log.DEBUG, Utils.instance_tag(self) + " starting\n")

        # Expand all mandatory fields in the raw config and fix raw file paths.
        Tracer.start(self, "range", "expand_task")
        self.expand_task()
        Tracer.end(self, "range", "expand_task", None)

        # If there's a depfile from a previous build, load it so we can use it below.
        in_depfile = cast(str, self.in_depfile)
        if in_depfile:
            self._old_deplines = Utils.load_depfile(in_depfile, self.depformat, self.cwd)

        # Inputs are ready, templates are expanded, see if everything's sane before we try running
        # commands.
        self.sanity_check()

        # Dry runs early out after the task is initialized but before we do .exists() checks or
        # run any commands.
        if self.build_dry:
            return

        # Paths updated. See if we need to rebuild our outputs.
        self._reason = self.rebuild_reason()
        if not self._reason:
            raise Task.SKIPPED(f"Task is up-to-date: {self.name} : {self.desc}")

        # Wait for enough jobs to free up to run this task.
        self._cores = await Runner.acquire(self.job_size)

        # Run all the task's commands
        text  = repr(self.name) if self.name else ""
        text += " : " if self.name and self.desc else ""
        text += repr(self.desc) if self.desc else ""
        self.log_task(Log.INFO,  Log.TEAL + f"Task {text}\n")
        self.log_task(Log.DEBUG, Log.GRAY2 + f"Task rebuilding because: {self._reason}\n")

        time_a = time.perf_counter()

        for command in self._commands:
            if command is None:
                continue
            elif callable(command):
                await self.call_callback(command)
            else:
                await self.run_command(command)

        time_b = time.perf_counter()

        self.log_task(Log.DEBUG, Log.GRAY2 + f"Task took {time_b-time_a:8.6f} sec: {text}\n")

        # See if the task wrote all its output files
        for file in Utils.yield_values(self._out_files):
            if not os.path.exists(file):
                raise Task.FAILED(f"Task ran, but output file still missing: {file}")

        # And we're done

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

            if not isinstance(input_task._error, (Task.SKIPPED, types.NoneType)):
                raise Task.CANCELLED(f"Task {hex(id(self))} is cancelled due to {input_task._error}")

    def expand_task(self):

        if cg.log.level <= Log.DEBUG:
            self.log_task(Log.DEBUG, "Task before expand:\n")
            #self.log_task(Log.DEBUG, Dumper.dump(self, dump_private=False, fold = ["hancho", "log", "in_objs", "_up", "script", "_aio_context"]) + "\n")
            #self.log_task(Log.DEBUG, Dumper.dump(self, dump_private=False) + "\n")
            self.log_task(Log.DEBUG, Dumper.dump(self, fold=[Task], dump_private = False) + "\n")

        script_root = self.eval("script.root")
        build_root = self.eval("build_root")

        Tracer.start(self, "range", "expand io fields")
        # Then we expand all io fields and fix their paths.
        io_fields = [k for k in self if k.startswith("in_") or k.startswith("out_")]

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

            file_tree = cast(branch[str | Task], self[field_name])
            files : list[str] = list(yield_files(file_tree))

            if files:
                files = Expander.expand(files, self, recursive = True)
                files = self.fix_paths(field_name, files, script_root, build_root)
                self[field_name] = files[0] if len(files) == 1 else files

                if field_name == "in_depfile":
                    if len(files) > 1:
                        raise Task.BROKEN("in_depfile can't be a list")
                elif field_name.startswith("in_"):
                    self._in_files[field_name] = files
                elif field_name.startswith("out_"):
                    self._out_files[field_name] = files
        Tracer.end(self, "range", "expand io fields", None)

        # IO fields all expanded, we can expand the rest of the task now.
        Expander.xip(self, recursive = True)
        self._commands = Utils.flatten_tree(self.command)

        # Now that expansion is done, update our scope's _name/_id so we are more readable in
        # dumps.
        self._name = self.name
        self._id   = "task"

        if not self.build_dry:
            for files in self._out_files.values():
                for file in files:
                    os.makedirs(os.path.dirname(file), exist_ok=True)
            if self.in_depfile:
                os.makedirs(os.path.dirname(self.in_depfile), exist_ok=True)

        if cg.log.level <= Log.DEBUG:
            self.log_task(Log.DEBUG, "Task after expand:\n")
            #self.log_task(Log.DEBUG, Dumper.dump(self, fold=[Task]) + "\n")
            self.log_task(Log.DEBUG, Dumper.dump(self, fold=[Task], dump_private = False) + "\n")




    def fix_paths(self : Task, field : str, files : list[str], script_root : str, build_root : str) -> list[str]:
        return [self.fix_path(field, f, script_root, build_root) for f in files]

    def fix_path(self : Task, field : str, file : str, script_root : str, build_root : str) -> str:
        """
        Input and output file paths in .hancho scripts are declared relative to the directory the
        script is in (stored in the config under 'script_cwd').
        In general we want to run commands from the root of the repo and store output files in
        repo/build, so we need to fix up the paths to match.
        """
        #if Utils.is_sequence(file) or Utils.is_set(file):
        #    result = [self.fix_paths(field, f, build_root) for f in file]
        #    return result
        #if Utils.is_mapping(file):
        #    result = {k:self.fix_paths(field, f, build_root) for k, f in file}
        #    return result

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

    def sanity_check(self : Task):
        repo = self.find('repo')
        project = self.find("project")

        # Check for all task issues that break the build

        if not Path.exists(self.cwd):
            raise Task.BROKEN(f"Task working directory '{self.cwd}' does not exist")

        if not Path._startswith(self.build_root, repo.root):
            raise Task.BROKEN(f"The build root {self.build_root} is not under repo.root {repo.root}")

        # Check that task's commands are either strings or callables.
        for command in self._commands:
            if not isinstance(command, str) and not callable(command) and command is not None:
                raise Task.BROKEN(f"Command {command} is not a string or a callable?")

        # In order to provide the least amount of bafflement to users, CLI commands execute
        # from task_cwd (which is usually the root of the repo, the most common cwd)
        # and callbacks execute from dir(script_path) (because you expect to be in the same
        # directory as the script when the callback is firing).

        # This means that pre-relative-ified paths can only be rel'd to one of the two cwds, not both.
        # And that means we disallow mixed cli/callback command lists.

        for command in self._commands:
            if type(command) is not type(self._commands[0]):
                raise Task.BROKEN(f"Commands aren't the same type: {self.command}")

        # In strict mode, we mark a task broken if its command still has delimiters in it.
        if project.find('strict'):
            for command in self._commands:
                if Expander.is_macro(command) or Expander.is_template(command):
                    raise Task.BROKEN(f"STRICT: Command not fully expanded: {command!r}")

        io_fields = [k for k in self if k.startswith("in_") or k.startswith("out_")]
        for field_name in io_fields:
            if field_name.startswith("out_"):
                files = self[field_name]
                for file in Utils.yield_values(files):
                    if not Path._startswith(file, self.build_root):
                        raise Task.BROKEN(f"Path error, output file {field_name}:{file} is not under build root {self.build_root}")


        # Check that all build files would end up under build_root
        for file in Utils.yield_values(self._out_files):
            assert Path.isabs(file)
            if not Path._startswith(file, self.build_root):
                raise Task.BROKEN(f"Path error, output file _out_files:{file} is not under build root {self.build_root}")

        # Check for task collisions
        for file in Utils.yield_values(self._out_files):
            real_file = cast(str, Path.abspath(file))
            if real_file in project._real_filenames:
                raise Task.BROKEN(f"TaskCollision: Multiple tasks build {real_file}")
            project._real_filenames.add(real_file)

        # Check for missing inputs. We have to check build_dry, as the input files may only exist if
        # we're really running tasks.
        for file in Utils.yield_values(self._in_files):
            if not Path.isabs(file):
                raise Task.BROKEN(f"Somehow we got a non-abs path for an input file - {file}")
            if not Path.exists(file) and not self.find("build_dry"):
                raise Task.BROKEN(f"Input file missing - {file}")

        # Tasks should have at most one depfile.
        if isinstance(self.in_depfile, list) and len(self.in_depfile) > 1:
            raise Task.BROKEN(f"Tasks can't have more than one dependency file! - {self.in_depfile}")

    async def run_command(self : Task, command : str):
        repo = self.find('repo')

        self.log_task(Log.INFO, f"{Path.relpath(self.cwd, repo.root)}$ {command}\n")

        proc = None
        try:
            # Convert any alt delims into curly braces
            curlify = str.maketrans({"«" : "{", "»" : "}"})
            curly_command = command.translate(curlify)

            # Create the subprocess via asyncio and then await the result.
            proc = await asyncio.create_subprocess_shell(
                curly_command,
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
        script = self.find('script')
        repo   = self.find('repo')

        callback_dir = Path.relpath(script.root, repo.root)
        self.log_task(Log.INFO, f"{callback_dir}$ {command}\n")

        # Callbacks run from the script dir where they were defined so that relative paths used
        # in the callback will be correct.

        with chdir(script.root):
            result = command(self)

        # It would seem like we wouldn't have to explicitly unwrap one level of await-ness here,
        # but apparently that's just how Python waitables work.
        if inspect.isawaitable(result):
            result = await result

    def rebuild_reason(self : Task) -> str:
        """
        Figures out why we have to run a Task, or returns "" if we don't.
        """
        repo = self.find('repo')
        project = self.find("project")

        # ------------------------------------
        # Check the trivial reasons to rebuild

        if self.build_force:
            project._build_reasons["forced"] += 1
            return "Target forced to rebuild"

        has_input = any(Utils.yield_values(self._in_files))
        if not has_input:
            project._build_reasons["no inputs"] += 1
            return "Always rebuild a target with no inputs"

        has_output = any(Utils.yield_values(self._out_files))
        if not has_output:
            project._build_reasons["no outputs"] += 1
            return "Always rebuild a target with no outputs"

        # ------------------------------------

        for filename in Utils.yield_values(self._in_files):
            if reason := repo.check_stats(filename, []):
                return reason

        for filename in self._old_deplines:
            if reason := repo.check_stats(filename, []):
                return reason

        for filename in Utils.yield_values(self._out_files):
            if reason := repo.check_stats(filename, self.command):
                return reason

        if self.in_depfile and (reason := repo.check_stats(self.in_depfile, [])):
            return reason

        project._build_reasons["*task clean"] += 1
        return ""

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

    def log_task(self : Task, level : int, message : str):
        # Log helper that adds the [ NN/ XX] tag before the log line.
        for line in message.splitlines(keepends=True):
            if not cg.log._line_buffer:
                cg.log._log(level, f"[{self._task_id:3d}/{Runner.tasks_enabled:3d}] ")
            cg.log._log(level, line)

    def log_task_exception(self : Task, message, ex = None):
        cg.log.error("========================================\n")
        cg.log.error(message + "\n")
        cg.log.error("========================================\n")
        cg.log.error(f"Script    = {self.find("script").path}:\n")
        cg.log.error(f"Task      = {self.name!r} : {self.desc!r}\n")
        cg.log.error(f"os.getcwd = {os.getcwd()}\n")
        cg.log.error(f"task cwd  = {self.cwd}\n")
        cg.log.error(f"command   = {self.command}\n")
        cg.log.exception(ex)
        cg.log.error(self.dump_stdout())
        cg.log.error("========================================\n")

# endregion
# ==================================================================================================
# region Script

# This is not a types.ModuleType, but it still works fine as one. Making it a ModuleType is a bit
# annoying to work with.

class Script(Scope):

    def __init__(self, name, path, root, repo : Repo, *args, **kwargs):
        super().__init__(
            _name = name,
            _id   = "script",
            _up  = repo
        )

        self.name = name
        self.path = path
        self.root = root
        self.build_root = "{join(repo.build_root, relpath(script.root, repo.root))}"

        self.update(*args, **kwargs)
        Expander.xip(self, recursive = True)

        # The Python module loading machinery looks for this field, but it can be None.
        self.__spec__ = None
        self._tasks = []

        # If we want something to appear in a script's global scope, it _must_ be in this object's
        # dict
        self.Dict = Dict
        self.Task = Task
        self.Tool = Tool
        self.Dumper = Dumper

    def dump(self, *args, **kwargs):
        dprint(Dumper.dump(*args, **kwargs))

    def load(self, file, root, is_repo, *args, **kwargs):
        path = Path._resolve(file, strict = True)
        if root is None:
            root = os.path.dirname(path)
        name = os.path.join(os.path.split(root)[-1], os.path.basename(file))
        root = Path._resolve(root, strict = True)
        return Script._load(name, path, root, *args, is_repo = is_repo, **kwargs)

    def script(self, file, root = None, *args, **kwargs):
        return self.load(file, root, False, *args, **kwargs)

    def include(self, file, root = None, *args, **kwargs):
        return self.load(file, root, False, *args, **kwargs)

    def repo(self, file, root = None, *args, **kwargs) -> Script:
        return self.load(file, root, True, *args, **kwargs)

    def build(self):
        project = self.find("project")
        return project.build()

    # ----------------------------------------

    class EarlyOut(Exception): pass
    class Fail(Exception): pass
    class Abort(Exception): pass

    def fail(self, message):
        self._log_script_error(sys._getframe(1), "failed", message)
        raise self.Fail()

    def abort(self, message):
        self._log_script_error(sys._getframe(1), "aborted", message)
        raise self.Abort()

    def earlyout(self, message = ""):
        self._log_script_error(sys._getframe(1), "exited early", message)
        raise self.EarlyOut()

    def _log_script_error(self, frame, condition, message):
        cg.log.error(f"Script {condition}:\n")
        cg.log.error(f"  text = '{message}'\n")
        cg.log.error(f"  file = {frame.f_code.co_filename}\n")
        cg.log.error(f"  func = {frame.f_code.co_name}\n")
        cg.log.error(f"  line = {frame.f_lineno}\n")

    # ----------------------------------------

    @staticmethod
    def _load(name : str, path : str, root : str, is_repo : bool, *args, **kwargs) -> Script:
        assert path == Path.resolve(path)
        assert root == Path.resolve(root)

        if is_repo:
            repo = Repo(name, path, root, cg.project)
            script_type = "repo"
            cg.project._repos.append(repo)
        else:
            repo = cg.repo
            script_type = "script"

        script = Script(name, path, root, repo, *args, **kwargs)
        repo._scripts.append(script)

        # Dedupe the load - only scripts with identical real paths and identical configs are
        # deduped. This relies on the fields read by Dumper.dump being stable during a build,
        # which they should be in practice.
        dupe_key = Dumper.any_to_key(script)

        # FIXME need to make sure private fields aren't in the dump, but i'll deal with it properly later.
        assert " _" not in dupe_key

        if dupe := cg.project._dedupe.find(dupe_key, None):
            cg.log.debug(Log.LIME + f"Deduped {script_type} {script.path}\n")
            return dupe
        else:
            cg.log.debug(Log.ORANGE + f"Loading {script_type} {script.path}\n")

        cg.project._dedupe[dupe_key] = script

        if not script.path.endswith(".hancho"):
            return script

        code = None
        with open(script.path, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, script.path, "exec", dont_inherit=True)

        old_mod = sys.modules.get("hancho", None)

        try:
            with chdir(script.root), cg.update(repo = repo, script = script), cg.log.indenter(Log.ORANGE):
                # FIXME does this _really_ need to be moduletype?
                sys.modules["hancho"] = cast(types.ModuleType, script)
                exec(code, script.__dict__)
                return script
        finally:
            if old_mod is None:
                del sys.modules["hancho"]
            else:
                sys.modules["hancho"] = cast(types.ModuleType, old_mod)

    @staticmethod
    def init_for_testing(*, name, file : str, root : str, stdout : str | typing.TextIO, args) -> Script:
        flags = Flags.parse(
            f"--name={name}",
            f"--file={file}",
            f"--root={root}",
            *args
        )

        cg.update(flags = flags)
        # This can't happen until after cg.flags is set.
        cg.update(log = Log(stdout))

        Utils.reset()
        Runner.reset()

        # ------------------------------------

        test_name = cg.flags.name
        test_path = Path._resolve(cg.flags.file, strict = True)
        test_root = Path._resolve(cg.flags.root, strict = True)

        # ------------------------------------

        project = Project(test_name, test_path, test_root)
        cg.update(project = project)
        script  = Script._load(test_name, test_path, test_root, is_repo = True)
        cg.update(repo = script, script = script)

        return cg.script


# endregion
# ==================================================================================================
# region main


def main():

    # Top-level exception handler just so we can print a big red "SOMETHING BROKE" message if
    # we failed to catch an exception during load/build. The 'except' clause should catch
    # Exception and not BaseException so ctrl-c doesn't get misinterpreted as a Hancho bug.

    flags = Flags.parse(*sys.argv)

    cg.reset()
    cg.update(flags = flags)
    cg.update(
        hancho = Dict(
            _name  = "hancho",
            _id    = "hancho",
            _up    = sys.modules[__name__],
            path   = Path.resolve(__file__),
            root   = os.path.dirname(__file__)
        )
    )
    cg.update(log = Log(sys.stdout))

    try:
        cg.log.info(f"Command line : {" ".join(sys.argv)}\n")
        cg.log.trace("Trace mode on\n")
        cg.log.debug("Debug mode on\n")

        # We expand these manually because we need to *resolve* 'path' _before_ we *expand* 'root'.

        cg.flags.xip('name')
        cg.flags.xip('path')
        cg.flags.xip('root')

        cg.flags.path = Path._resolve(cg.flags.path, strict = True)
        cg.flags.root = Path._resolve(cg.flags.root, strict = True)

        project = Project(cg.flags.name, cg.flags.path, cg.flags.root)

        Utils.reset()
        Runner.reset()

        with cg.update(project = project):
            time_a1 = time.perf_counter()
            Script._load(project.name, project.path, project.root, is_repo = True)
            time_b1 = time.perf_counter()
            cg.log.info(Log.BLUE + f"Loading project took {time_b1 - time_a1:8.6f} seconds\n")

            project.build()
            project.stats()
            project.passfail()

        return project._result

    except Exception:
        cg.log.critical("Hancho hit an unhandled exception:\n")
        cg.log.critical(traceback.format_exc())
        cg.log.critical(Log.RESET)

        # Re-raise in debug builds so the debugger will stop execution.
        raise
    finally:
        # Don't leave the last line of the log sitting in line_buffer!
        cg.log._flush()

#endregion
# ==================================================================================================

cg = Globals()

if __name__ == "__main__":
    sys.exit(main())
