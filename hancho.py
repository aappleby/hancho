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
import functools
import hashlib
import inspect
import io
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
from abc import ABCMeta
from collections import Counter, abc, defaultdict
from collections.abc import Generator, Iterator
from contextlib import chdir, contextmanager, suppress
from dataclasses import dataclass, field, replace
from enum import Enum
from itertools import product
from types import CodeType, FunctionType, GeneratorType, ModuleType, NoneType
from typing import (
    IO,
    Any,
    ClassVar,
    Concatenate,
    NamedTuple,
    NoReturn,
    Self,
    TextIO,
    TypeIs,
    cast,
    overload,
)

# endregion
# ==================================================================================================
# region Constants and globals

def is_public(key : int | str | None) -> bool:
    if not isinstance(key, str) and key is not None:
        print(f"key is {type(key)}={key}")
        #breakpoint()
        raise ValueError(key)
    return key is None or not key.startswith("_")

def is_private(key : str) -> bool:
    if not isinstance(key, str) and key is not None:
        print(f"key is {type(key)}={key}")
        #breakpoint()
        raise ValueError(key)
    return key is not None and key.startswith("_")

def dprint(*args : Any, **kwargs : Any) -> None:
    print("DBG: ", end="")
    print(*args, **kwargs)

# 'branch' must be sequence/set/mapping in order for this to typecheck:
# a : str = "foo"
# b : tree[str] = a
# c : list[str] = ["bar"]
# d : tree[str] = c

type container[T] = abc.Sequence[T] | abc.Set[T] | abc.Mapping[str, T]
# type inference doesnt' like this
#type branch[LEAF] = LEAF | container[branch[LEAF]]
#type tree[LEAF]   = container[branch[LEAF]]
type branch[LEAF] = LEAF | abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]
type tree[LEAF] = abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]

type task_callback = abc.Callable[[Task], Any]
type item[T] = tuple[int | str | None, T]
type command_type = str | list[str] | task_callback | None

type dump_key = str | int | None

# These types don't need a type annotation when dumped.
base_types: tuple[type, ...] = (int, float, complex, bool, str, tuple, NoneType, type)

# These types don't get dumped because they're not really dumpable.

opaque_types: tuple[type, ...] = (GeneratorType, contextvars.Context, asyncio.Task, ModuleType, type)

default_folds: list[str] = ["_dedupe", "_repos", "_scripts", "_tasks", "Builtins"]

@contextmanager
def timeit(name : str) -> Generator[None]:
    a: float = time.perf_counter()
    yield
    b: float = time.perf_counter()
    cvg.log.verbose(text=Log.DBLUE + f"{name} took {b - a:8.6f} seconds\n")

@overload
def check[T](cls: type[T], obj: object) -> T: ...
@overload
def check[A, B](cls: tuple[type[A], type[B]], obj: object) -> A | B: ...
@overload
def check[A, B, C](cls: tuple[type[A], type[B], type[C]], obj: object) -> A | B | C: ...
def check(cls: Any, obj: object) -> Any:
    if not isinstance(obj, cls):
        raise TypeError(f"expected {cls}, got {type(obj).__name__}")
    return obj

# endregion
# ==================================================================================================
# region CVGlobals

class CVGlobals:
    """A 'globals()'-type object stored in a contextvar. All modifications replace its entire
    contents, so that if something is hold a reference to an old global scope it doesn't get
    modified indirectly."""

    def __init__(self) -> None:
        object.__setattr__(self, "_cv", contextvars.ContextVar("CVGlobals"))

        # These are just here so we get some type checking on the things that are usually in cvg.
        self.builtins : Builtins
        self.flags : Flags
        self.hancho : ModuleType
        self.log : Log
        self.project : Project
        self.repo : Repo
        self.script : Script
        self.stats : Stats

        self._cv.set({})

    def update(self, *args : Any, **kwargs : Any) -> contextvars.Token:
        new_dict: dict[Any, Any] = dict(self._cv.get())
        for d in [*args, kwargs]:
            new_dict |= d
        return self._cv.set(new_dict)

    def __setattr__(self, key : str, val : Any) -> None:
        #new_dict = dict(self._cv.get()) | {key:val}
        ##new_dict[key] = val
        #return self._cv.set(new_dict)
        self._cv.get()[key] = val

    def __getattr__(self, key : str) -> Any:
        return self._cv.get()[key]

cvg = CVGlobals()

# endregion
# ==================================================================================================
# region Sentinel

class Sentinel:
    def __init__(self, name : str) -> None: self.name: str = name
    def __repr__(self) -> str:              return self.name
    def __bool__(self) -> bool:             return False
    def __copy__(self)-> Sentinel:          return self
    def __deepcopy__(self, _ : dict[int, Any])->Sentinel: return self
    def __reduce__(self)-> str:             return self.name

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

    @staticmethod
    def hex_to_ansi(hex : int) -> str:
        """Converts a color hex code into an ANSI escape sequence"""
        r : int = (hex >> 16) & 0xFF
        g : int = (hex >>  8) & 0xFF
        b : int = (hex >>  0) & 0xFF
        return f"\x1B[38;2;{r};{g};{b}m" if hex else "\x1B[0m"

    @staticmethod
    def stringify(variant : Any) -> str:
        """Converts any type into a template-compatible string."""
        result : str = ""
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
    def yield_leaves[T](branch : branch[T]) -> Iterator[T]:
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
        result : list[T] = list(Utils.yield_leaves(tree))
        return result

    # ------------------------------------
    # yield key-value pairs from a key-value pair

    @staticmethod
    def yield_items_from_item[T](item : item[T] | None, seen : set | None = None) -> Iterator[item[T]]:
        if item is None:
            return

        #key = item[0]
        val : T = item[1]

        if seen is None:
            seen = set()

        if id(val) in seen:
            return
        elif not isinstance(val, base_types):
            seen.add(id(val))

        if Utils.is_mapping(val):
            for item2 in val.items():
                yield from Utils.yield_items_from_item(item2)
        elif Utils.is_sequence(item[1]) or Utils.is_set(val):
            for item2 in enumerate(cast(Any, val)):
                yield from Utils.yield_items_from_item(item2, seen)
        else:
            yield item

    # yield key-any pairs from a variant
    @staticmethod
    def yield_items(variant : Any) -> Iterator[item[Any]]:
        if variant is None:
            return
        yield from Utils.yield_items_from_item((None, variant))

    @staticmethod
    def yield_unique_values(variant : Any, seen : set | None = None) -> Iterator[Any]:
        if variant is None:
            return

        if seen is None: seen = set()

        if id(variant) in seen:
            return
        elif not isinstance(variant, base_types):
            seen.add(id(variant))

        if Utils.is_mapping(variant):
            for v2 in variant.values():
                yield from Utils.yield_unique_values(v2, seen)
        elif Utils.is_sequence(variant) or Utils.is_set(variant):
            for v2 in variant:
                yield from Utils.yield_unique_values(v2, seen)
        else:
            yield variant

    @staticmethod
    def yield_public_values(variant : tree[Any], seen : set[Any] | None = None) -> Any:
        if seen is None: seen = set()

        if id(variant) in seen:
            return
        elif not isinstance(variant, base_types):
            seen.add(id(variant))

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
    def yield_cross_join[T](*args: tree[T]) -> Iterator[tuple[T, ...]]:
        return product(*(Utils.yield_leaves(a) for a in args))

    @staticmethod
    def cross_join[T](lhs_t : tree[T], *args: tree[T]) -> list[tuple[T, ...]]:
        return list(Utils.yield_cross_join(lhs_t, *args))

    @staticmethod
    def yield_cross_join_reduce[T,U](reduce: abc.Callable[[T|U, T], U], *args: tree[T]) -> Iterator[U]:
        yield from (functools.reduce(reduce, t) for t in Utils.yield_cross_join(*args)) # type: ignore

    @staticmethod
    def cross_join_reduce[T](reduce: abc.Callable[[T, T], T], *args: tree[T]) -> list[T]:
        return list(Utils.yield_cross_join_reduce(reduce, *args))

    @staticmethod
    def weave(lhs : tree[str] | None, rhs : tree[str] | None, *args : tree[str]) -> list[str]:
        if lhs is None or rhs is None:
            return []
        return Utils.cross_join_reduce(lambda x, y: x + y, lhs, rhs, *args)

    @staticmethod
    def obj_to_float(obj : object) -> float:
        """
        Generates a "random" float in the range [0.0,1.0) by hashing the object's ID.
        """
        temp : int
        temp = id(obj) & 0xFFFFFFFF
        temp = ((temp ^ (temp >> 19)) * 0x23456789) & 0xFFFFFFFF
        temp = ((temp ^ (temp >> 19)) * 0x23456789) & 0xFFFFFFFF
        temp = ((temp ^ (temp >> 19)) * 0x23456789) & 0xFFFFFFFF
        return (temp & 0xFFFFFFFF) / 0x100000000

    color_map : ClassVar[dict] = {}
    color_cursor : ClassVar[int] = 0

    @staticmethod
    def obj_to_ansi_color(obj : object) -> str:
        oid : int = id(obj)
        if oid in Utils.color_map:
            return Utils.color_map[oid]
        phi : float = 1.61803398875
        hue : float = (0.3 + Utils.color_cursor * phi) % 1
        Utils.color_cursor += 1

        r : float
        g : float
        b : float
        r, g, b = colorsys.hsv_to_rgb(hue, 0.6, 0.8)
        r, g, b = (int(r * 255), int(g * 255), int(b * 255))
        result : str = Utils.hex_to_ansi((r << 16) | (g << 8) | b)
        Utils.color_map[oid] = result
        return result

    @staticmethod
    def run_cmd(cmd : str) -> str:
        """Runs a console command synchronously and returns its stdout with whitespace stripped."""
        result: str = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        return result

    @staticmethod
    def is_scalar(v : Any) -> bool:
        if type(v) is object: return True
        return not (Utils.is_sequence(v) or Utils.is_mapping(v) or Utils.is_set(v))

    @staticmethod
    def is_collection(v : Any) -> TypeIs[abc.Sequence[Any]] | TypeIs[abc.Set[Any]] | TypeIs[abc.Mapping[Any, Any]]:
        if type(v) is object: return False
        return (Utils.is_sequence(v) or Utils.is_mapping(v) or Utils.is_set(v))

    @staticmethod
    def is_sequence(v : Any) -> TypeIs[abc.Sequence[Any]]:
        return isinstance(v, abc.Sequence) and not isinstance(v, (str, bytes, bytearray))

    @staticmethod
    def is_mut_sequence(v : Any) -> TypeIs[abc.MutableSequence[Any]]:
        if isinstance(v, (str, bytes, bytearray)):
            return False
        return isinstance(v, abc.MutableSequence)

    @staticmethod
    def is_set(v : Any) -> TypeIs[abc.Set]:
        return isinstance(v, abc.Set)

    @staticmethod
    def is_mut_set(v : Any) -> TypeIs[abc.MutableSet[Any]]:
        return isinstance(v, abc.MutableSet)

    @staticmethod
    def is_mapping(v : Any) -> TypeIs[abc.Mapping[Any, Any]]:
        return isinstance(v, abc.Mapping)

    @staticmethod
    def is_mut_mapping(v : Any) -> TypeIs[abc.MutableMapping[Any, Any]]:
        return isinstance(v, abc.MutableMapping)

    @classmethod
    def is_macro(cls, text : str) -> bool:
        if not isinstance(text, str):
            return False
        counts: Expander.SplitCount = Expander.split_text(text)
        return counts.literals == 0 and counts.macros == 1

    @classmethod
    def is_template(cls, text : str) -> bool:
        if not isinstance(text, str):
            return False
        counts: Expander.SplitCount = Expander.split_text(text)
        return (counts.literals + counts.macros) > 1

    # ------------------------------------

    @staticmethod
    def hex_tag(obj : Any) -> str:
        return "0x" + hex(id(obj))[-4:].upper()

    @staticmethod
    def instance_tag(obj : Any) -> str:
        result : str = ""
        if hasattr(obj, "_self") and obj._self is not None:
            result += obj._self
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
    def tree_all(func : abc.Callable) -> abc.Callable:
        """Turns a predicate into one that can be applied to arbitrarily nested containers."""

        @functools.wraps(func)
        def wrapper(variant : Any, *args : Any, **kwargs : Any) -> bool:
            return all(func(v, *args, **kwargs) for v in Utils.yield_leaves(variant))

        return staticmethod(wrapper)

    @classmethod
    @contextmanager
    def write_and_swap(cls, filename:Goodpath) -> Generator[io.IOBase]:
        """
        Works like "with open(...) as f:", except we first write to filename.temp and then
        atomically swap it with the original filename once the user is done with it.
        """
        temp_filename:str = filename + ".temp"
        try:
            with open(str(temp_filename), "w") as temp_file:
                yield temp_file
                temp_file.flush()
                os.fsync(temp_file.fileno())
        finally:
            os.replace(temp_filename, filename)

    @classmethod
    def load_json(cls, path : Realpath) -> Any:
        with open(str(path)) as contents:
            return json.load(contents)

    @classmethod
    def save_json(cls, variant : Any, path : Goodpath) -> None:
        os.makedirs(os.path.dirname(path), exist_ok = True)
        with cls.write_and_swap(path) as file:
            json.dump(variant, file, indent=4, default=lambda x: x.__dict__)
            file.write("\n")

    # When merging Dicts, if two attributes have the same name:
    #     - If they are both dicts, we recursively merge them.
    #     - If they are both lists, we concatenate them.
    #     - Otherwise rightmost non-None wins.

    @staticmethod
    def merge(
        lhs: object|abc.Mapping[str,Any],
        rhs: object|abc.Mapping[str,Any],
        *,
        merge_private: bool = False,
        merge_mappings: bool = True,
        merge_sequences: bool = True,
        merge_sets: bool = True,
        keep_lhs: bool = True,
        keep_rhs: bool = True,
    ) -> Any:
        if rhs is None:
            return lhs

        ldict:abc.Mapping[str,Any] = lhs if isinstance(lhs, dict) else lhs.__dict__
        rdict:abc.Mapping[str,Any] = rhs if isinstance(rhs, dict) else rhs.__dict__
        lkeys:list[str] = [k for k in ldict if is_public(k) or merge_private]
        rkeys:list[str] = [k for k in rdict if is_public(k) or merge_private]

        # Prune lhs keys if needed
        for key in (k for k in lkeys if k not in rkeys and not keep_lhs):
            del ldict[key]

        # Merge all lhs+rhs pairs
        for key in (k for k in lkeys if k in rkeys):

            lhs2:Any = ldict.get(key)
            rhs2:Any = rdict.get(key)

            if lhs2 is rhs2:
                pass
            elif Utils.is_mut_mapping(lhs2) and Utils.is_mapping(rhs2) and merge_mappings:
                Utils.merge(lhs2, rhs2)
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
    QUIET    = 60

    def __init__(self, *args : Any, **kwargs : Any) -> None:

        self.log_level     = "info"
        self.log_wrap      = False
        self.log_color     = True
        self.log_timestamp = True
        self.log_width: int     = shutil.get_terminal_size().columns
        self.log_stdout: TextIO = sys.stdout

        for rhs in [*args, kwargs]:
            Utils.merge(self, rhs, keep_rhs = False)

        self._level_int:int     = getattr(Log, self.log_level.upper())
        self._time_origin:float   = time.perf_counter()
        self._indent_stack:list[str]  = []
        self._line_buffer:str   = ""
        self._match_escapes: re.Pattern[str] = re.compile(r"(\x1B.*?m)")

        self._stdout : TextIO
        if isinstance(self.log_stdout, str):
            self._stdout = open(str(self.log_stdout), "w")  # noqa: SIM115
        elif isinstance(self.log_stdout, io.TextIOBase):
            self._stdout = self.log_stdout
        else:
            raise ValueError(f"Don't know how to use {self.log_stdout} as an output")

    def indent(self, ansi_color : str = RESET) -> None:
        #self._indent_stack.append(f"{ansi_color}│{Log.RESET}")
        self._indent_stack.append(ansi_color)

    def dedent(self) -> None:
        self._indent_stack.pop()

    @contextmanager
    def indenter(self, level:int, ansi_color : str = RESET) -> Generator[None]:
        log_level = self._level_int
        if level >= log_level:
            self.indent(ansi_color)
        yield
        if level >= log_level:
            self.dedent()

    def trace(self, text : str) -> None:
        # Traces do their own color coding
        self._log(Log.TRACE, "", text)

    def debug(self, text : str) -> None:
        self._log(Log.DEBUG, Log.GRAY3, text)

    def verbose(self, text : str) -> None:
        self._log(Log.VERBOSE, Log.GRAY4, text)

    def info(self, text : str) -> None:
        self._log(Log.INFO, "", text)

    def warning(self, text : str) -> None:
        self._log(Log.WARNING, Log.YELLOW, text)

    def error(self, text : str) -> None:
        self._log(Log.ERROR, Log.ORANGE, text)

    def critical(self, text : str) -> None:
        self._log(Log.CRITICAL, Log.RED, text)

    def _log(self, log_level : int, color : str, text : Any) -> None:
        if isinstance(text, list):
            for t in text:
                self._log(log_level, color, t)
            return
        elif not isinstance(text, str):
            text = repr(text) + "\n"

        if log_level < self._level_int:
            return
        if not isinstance(text, str) or len(text) == 0:
            return

        # FIXME str.partition might be easier here
        lines: list[str] = text.splitlines(keepends=True)
        for line in lines:
            if self._line_buffer == "":
                self._line_buffer += f"[{time.perf_counter() - self._time_origin:8.3f}] " if self.log_timestamp else ""
                self._line_buffer += f"│{Log.RESET} ".join([*self._indent_stack, ""])

            self._line_buffer += color

            if line[-1] == "\n":
                self._line_buffer += line[:-1]
                self._line_buffer += Log.RESET
                self._line_buffer += "\n"
                self._flush()
            else:
                self._line_buffer += line
                self._line_buffer += Log.RESET

    def _flush(self) -> None:
        # Dumps the line buffer to stdout and then clears it.
        if self._line_buffer:
            # If we want a non-colorized log, strip color codes from the line.
            if not self.log_color:
                self._line_buffer = re.sub(self._match_escapes, "", self._line_buffer)

            # If the line wasn't finished (because we're exiting the app), stick a newline on it.
            if self._line_buffer[-1] != "\n":
                self._line_buffer += "\n"

            if not self.log_wrap:
                _ = self.log_width
                self._line_buffer = self._clip_printable(self._line_buffer, self.log_width)

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
        newline: bool = text[-1] == "\n"
        if newline:
            text = text[:-1]

        # Split the text using the escape sequences as separators.
        chunks: list[str] = self._match_escapes.split(text)

        # Even chunks are printable text, odd chunks are escape sequences.
        # If the printable characters fit on the line, we don't need to clip.

        print_len: int = sum(len(c) for c in chunks[::2])
        if print_len <= width:
            if newline:
                text += "\n"
            return text

        # If we do need to clip, stick the chunks back together until we exceed width-3, then
        # clip the last chunk and add "...". After clipping, emit all the remaining escape codes in
        # case they do something important.

        accum = 0
        result:str = ""
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
    def swapext(p : str, new_ext : str) -> str:
        return os.path.splitext(p)[0] + new_ext

    # WARNING - Both "startswith" and "relpath" below can throw ValueError if there's a mix of
    # abs/rel paths, or if the paths are on different volumes in Windows. We don't handle this yet,
    # but we will need to eventually. If this occurs inside a macro you'll see the exception in the
    # macro expansion trace and the macro will be returned unexpanded. Using "commonpath" here is
    # probably worth it though, as it handles some annoying edge cases.

    @staticmethod
    def startswith(path : str, prefix : str) -> bool:
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
    def relpath(lhs : Goodpath, rhs : Goodpath) -> str:
        prefix : str = os.path.commonpath([lhs, rhs])
        result : str

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
    def isfile(p : str) -> TypeIs[Realpath] : return os.path.isfile(p)

    @staticmethod
    def isdir(p : str) -> TypeIs[Realpath] : return os.path.isdir(p)

    @staticmethod
    def basename(p : str) -> str : return os.path.basename(p)

    @staticmethod
    def dirname(p : Goodpath) -> Goodpath : return Goodpath(os.path.dirname(p))

    @staticmethod
    def split(p : Goodpath) -> tuple[Goodpath, str] :
        (head, tail) = os.path.split(p)
        return (Goodpath(head), tail)

    @staticmethod
    def stem(p : str) -> str: return os.path.splitext(os.path.basename(p))[0]

    @staticmethod
    def topdir(p : Goodpath) -> str: return os.path.split(os.path.dirname(p))[1]

    @staticmethod
    def join(head : Goodpath, *tail : str) -> Goodpath:
        result: str = os.path.join(head, *tail)
        return Goodpath(result)

    @staticmethod
    def exists(p : str) -> TypeIs[Realpath] : return os.path.exists(p)

    # ==============================================================================================
    # These functions wrap the os.path.* functions so that they work on arbitrary trees

#    @staticmethod
#    def relpath_t(lhs, rhs) -> tree[str]:
#        if isinstance(rhs, (list, tuple, set)):
#            return [Path.relpath_t(lhs, rh) for rh in rhs]
#            #result = [Path.relpath(lhs, rh) for rh in Utils.yield_leaves(rhs)]
#            #return cast(list[str], result)
#
#        if not os.path.isabs(lhs):
#            lhs = os.path.abspath(lhs)
#        if not os.path.isabs(rhs):
#            rhs = os.path.abspath(rhs)
#
#        prefix = os.path.commonpath([lhs, rhs])
#
#        if lhs == rhs:
#            result = "."
#        elif prefix != rhs:
#            result = lhs
#        else:
#            result = lhs.removeprefix(prefix + os.sep)
#
#        return result

#    startswith_t = Utils.tree_all(startswith)
#    resolve_t    = Utils.tree_map(lambda path : Path.resolve(path))
#    swapext_t    = Utils.tree_map(swapext)
#    exists_t     = Utils.tree_all(exists)
#    isabs_t      = Utils.tree_all(isabs)
#    isfile_t     = Utils.tree_all(isfile)
#    isdir_t      = Utils.tree_all(isdir)

#    basename_t   = Utils.tree_map(basename)
#    dirname_t    = Utils.tree_map(dirname)
#    isresolved_t = Utils.tree_all(isresolved)

# endregion
# ==================================================================================================
# region Goodpath/Realpath/Literal/Macro/Template/Expression

# Strings are immutable, if we want to normalize the path we have to do it in __new__.

class Goodpath(str):
    """A path string that we know is bash-expanded, home-expanded, and normalized."""
    def __new__(cls, path : str) -> Self:
        if isinstance(path, cls): return path
        path = os.path.expanduser(path)
        path = os.path.expandvars(path)
        path = os.path.abspath(path)
        return super().__new__(cls, path)

class Realpath(Goodpath):
    """Same as the above, but also resolved. Must refer to an actual file."""
    def __new__(cls, path : str) -> Self:
        if isinstance(path, cls): return path
        path = os.path.expanduser(path)
        path = os.path.expandvars(path)
        path = os.path.abspath(path)
        path = os.path.realpath(path, strict = True)
        return super().__new__(cls, path)

class Identifier(str):
    def __new__(cls, text : str) -> Self:
        if not isinstance(text, str) or not text.isidentifier():
            raise ValueError(f"String {text!r} is not a valid identifier")
        return super().__new__(cls, text)

class Literal(str):
    def __new__(cls, text : str) -> Self:
        (literals, macros) = Expander.split_text(text)
        if literals == 1 and macros == 0:
            return super().__new__(cls, text)
        else:
            raise ValueError(f"String {text!r} is not a valid literal")

class Macro(str):
    def __new__(cls, text : str) -> Self:
        (literals, macros) = Expander.split_text(text)
        if literals == 0 and macros == 1:
            return super().__new__(cls, text)
        else:
            raise ValueError(f"String {text!r} is not a valid macro")

class Template(str):
    def __new__(cls, text : str) -> Self:
        (literals, macros) = Expander.split_text(text)
        if (literals + macros) > 1:
            return super().__new__(cls, text)
        else:
            raise ValueError(f"String {text!r} is not a valid template")

# FIXME Can we use this in Expander?
class Expression(str):
    def __new__(cls, text : str) -> Self:
        try:
            ast.parse(text, mode='eval')
            return super().__new__(cls, text)
        except Exception as err:
            raise ValueError(f"String {text!r} is not a valid template") from err

# endregion
# ==================================================================================================
# region Scope/Dict/Tool

# A Scope is just some mapping-like object that can be used in template expansion.
# Note that it only exposes _public_ fields through iter/len

# FIXME this breaks if we do Scope(abc.Mapping)?

class Scope:
    def __init__(self, *, _self : str | None, _up : object | None) -> None:
        # The _self field is the name of _this_ scope while inside a template.

        # This is for stuff like "{script.root_s}" - the script is not _named_ 'script', but its
        # _self is 'script' so it matches.

        if _self is not None:
            self.__dict__['_self'] = _self

        # If a scope does _not_ contain a variable, we look for it in _up next.
        if _up is not None:
            self.__dict__['_up'] = _up

    # OK while maybe expand shouldn't really be in here, it's annoying to use otherwise.
    def expand(self, var : str) -> Any:
        return Expander(self).expand(var)

    def xip(self, var : Any) -> Any:
        return Expander(self).xip(var)

    # Fill-in-the-blank (or override what's there): Merges lhs and args into a new Dict, keeping
    # only keys that were already in lhs. For example, if you have a Dict that contains
    # "out_bin" and you merge it with "compile_cpp", Hancho will complain that "out_bin" is missing
    # - it sees both "out_obj" and "out_bin" and assumes the task produces both. If you do
    # compile_cpp.fill(...), "out_bin" does not get added to compile_cpp.

    def fill(self, *args : Any, **kwargs : Any) -> None:
        for rhs in (*args, kwargs):
            Utils.merge(self, rhs, keep_rhs = False)

    def merge(self, *args : Any, **kwargs : Any) -> None:
        for rhs in (*args, kwargs):
            Utils.merge(self, rhs)

    def get(self, key : str, default : Any | None = None) -> Any | None:
        return self.__dict__.get(key, default)

    def __contains__(self, key : str) -> bool:
        return is_public(key) and key in self.__dict__

    def __getitem__(self, key : str) -> Any:
        if is_private(key):
            raise KeyError(key)
        return self.__dict__[key]

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __iter__(self) -> abc.Iterator[Any]:
        yield from filter(is_public, self.__dict__)

# ====================================
# A Dict is a Scope that can be used as an abc.MutableMapping.

class Dict(Scope, abc.MutableMapping):

    # FIXME why this no werk
    #def __init__(self, *args : Dict[V], **kwargs : dict[str, V]):
    def __init__(self, *args : Any, **kwargs : Any) -> None:
        _self = kwargs.pop('_self', None)
        if _self is not None:
            _self = Identifier(_self)
        _up   = kwargs.pop('_up', None)

        super().__init__(_self = _self, _up = _up)
        for rhs in [*args, kwargs]:
            self.merge(rhs)

    def __getattr__(self, key : str) -> Any:
        try:
            return self.__dict__[key]
        except KeyError as ex:
            raise AttributeError from ex

    def __setattr__(self, key : str, val : Any) -> None:
        super().__setattr__(key, val)

    def __setitem__(self, key : str, val : Any) -> None:
        self.__dict__[key] = val

    def __delitem__(self, key : str) -> None:
        del self.__dict__[key]

# ====================================
# A Tool is just a callable alias for Dict to make build scripts more readable.

class Tool(Dict):
    def __call__(self, *args : Any, **kwargs : Any) -> Task:
        return Task(self, *args, **kwargs)

# endregion
# ==================================================================================================
# region Stats - trivial build statistics and tracking vars for expansion to catch infinite
# loops/recursion

class Stats(Scope):
    def __init__(self) -> None:
        self.stat_calls = 0
        self.hash_calls = 0
        self.hash_bytes = 0
        self.hash_time : float = 0
        self.expand_depth = 0
        self.expand_evals = 0
        self.tasks_created = 0
        self.tasks_enabled = 0
        self.tasks_started = 0
        self.tasks_finished = 0
        self.tasks_broken = 0
        self.tasks_failed = 0
        self.tasks_cancelled = 0
        self.tasks_skipped = 0
        self.build_reasons = Counter()

# endregion
# ==================================================================================================
# region Defaults

@dataclass
class Defaults:
    name : str   = "{topdir(path)}_{stem(path)}"
    file : str   = "build.hancho"
    path : str   = "{resolve(file)}"
    root : str   = "{dirname(path)}"
    root_b : str = "{join(repo.root_b, relpath(script.root_s, repo.root_r))}"

    targets: list[str] = field(default_factory=list)
    build_dir: str = "build"
    build_tag: str = ""
    build_force: bool = False
    build_all: bool = False
    build_dry: bool = False
    strict: bool = True
    max_errors: int = 0
    max_jobs: int = os.cpu_count() or 1
    log_level: str = "info"
    log_wrap: bool = False
    log_color: bool = True
    log_timestamp = True
    log_width: int = shutil.get_terminal_size().columns
    log_stdout: TextIO | str = sys.stdout


# endregion
# ==================================================================================================
# region Flags

class Flags(Scope):
    def __init__(self, *, raw_flags : Dict) -> None:
        super().__init__(_self = "flags", _up = cvg.builtins)

        self.name: str = raw_flags.pop("name", "{topdir(path)}")
        self.file: str = raw_flags.pop("file", "build.hancho")
        self.path: str = raw_flags.pop("path", "{resolve(file)}")
        self.root: str = raw_flags.pop("root", "{dirname(path)}")

        self.root_p: str = self.root
        self.root_r: str = self.root
        self.root_s: str = self.root
        self.root_t: str = self.root
        self.root_b: str = raw_flags.pop("build_root", "{join(repo.root_b, relpath(script.root_s, repo.root_r))}")

        self.targets : list[str]  = raw_flags.pop("targets",       [])
        self.build_dir : str      = raw_flags.pop("build_dir",     "build")
        self.build_tag : str      = raw_flags.pop("build_tag",     "")
        self.build_force : bool   = raw_flags.pop("build_force",   False)
        self.build_all : bool     = raw_flags.pop("build_all",     False)
        self.build_dry : bool     = raw_flags.pop("build_dry",     False)
        self.strict : bool        = raw_flags.pop("strict",        True)
        self.max_errors : int     = raw_flags.pop("max_errors",    0)
        self.max_jobs : int       = raw_flags.pop("max_jobs",      os.cpu_count() or 1)
        self.log_level : str      = raw_flags.pop("log_level",     "info")
        self.log_wrap : bool      = raw_flags.pop("log_wrap",      False)
        self.log_color : str      = raw_flags.pop("log_color",     True)
        self.log_timestamp : bool = raw_flags.pop("log_timestamp", True)
        self.log_width : int      = raw_flags.pop("log_width",     shutil.get_terminal_size().columns)
        self.log_stdout : IO[str] = raw_flags.pop("log_stdout",    sys.stdout)

        for k, v in raw_flags.items():
            if v is not None:
                self.__dict__[k] = v

    @classmethod
    def parse_flags(cls, *argv : str) -> Flags:

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

        levels: list[str] = ["trace", "debug", "verbose", "info", "warning", "error", "critical", "quiet"]

        def str_to_bool(v : str) -> bool:
            if v.lower() in ("yes", "true", "t", "y"): return True
            if v.lower() in ("no", "false", "f", "n"): return False
            raise argparse.ArgumentTypeError("Not a bool")

        parser.add_argument("targets", nargs="*", help="The names or partial names of targets to build")

        parser.add_argument("-o", "--opts",          type=str,         help="File containing JSON that will be used as additional options")
        parser.add_argument(      "--name",          type=str,         help="Name of the whole build project, if you don't want it to be <project>")
        parser.add_argument("-f", "--file",          type=str,         help="Path to the .hancho file that starts the build.")
        parser.add_argument("-r", "--root",          type=str,         help="The top script runs in this directory.")
        parser.add_argument(      "--build_root",    type=str,         help="Overrides the absolute path of the build directory.")
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
        parser.add_argument(      "--log_stdout",    type=str,         help="Redirect the log's output to this filename")

        # fmt: on

        flag_dict = Dict()
        (argv_vars, unrecognized) = parser.parse_known_args(args=argv)

        # ------------------------------------
        # Load flags from opt_file if present

        opt_file_name = argv_vars.__dict__.pop("project.opts", None)
        #loaded_opts = False
        if opt_file_name and os.path.exists(opt_file_name):
            with open(str(opt_file_name)) as f:
                try:
                    opt_file = json.load(f)
                    flag_dict.merge(opt_file)
                except Exception as _:
                    print(f"Opt file {opt_file_name!r} invalid!\n")
                    traceback.print_exc()

        # ------------------------------------
        # Add argv_vars

        def set_by_path(lhs : Any, key : str, val : Any) -> None:
            parts: list[str] = key.split(".")
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
            if match := re.match(pattern=r"--([^=]+)=(.+)", string=chunk):
                key:str = match.group(1)
                val:str = match.group(2)
                val:str = val.lower()

                def convert(val : Any) -> Any:
                    converters = [str_to_bool, int, float, lambda x : x]
                    for c in converters:
                        try:
                            return c(val)
                        except Exception as _:
                            pass
                    return None

                val2 = convert(val)
                #print(f"unrecognized flag - key {key!r} : value {val2!r}")

                set_by_path(flag_dict, key, val2)

        return cls(raw_flags = flag_dict)

# endregion
# ==================================================================================================
# region Expander

# Expander _must_ be a mapping in order to go in eval()

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

    def __init__(self, scope : Scope) -> None:
        self.expand_scope: Scope = scope

        self.scope_tag: str    = Utils.instance_tag(scope)
        self.scope_color: str  = Utils.obj_to_ansi_color(scope)
        self.action_color: str = Log.RESET

    # ====================================
    # Expander _must_ implement __getattr__ so that macros like "{script.foo}" work.

    def __getattr__(self, key : str) -> Any:
        try:
            return self.internal_get(key)
        except KeyError as ex:
            raise AttributeError from ex

    def __getitem__(self, key : str) -> Any:
        return self.internal_get(key)

    def __iter__(self) -> NoReturn:
        raise AssertionError()

    def __len__(self) -> NoReturn:
        raise AssertionError()

    # ====================================
    # This is the main "find a variable referenced by a template" lookup function.

    def internal_get(self, key : str) -> Any:
        result : Any = Sentinel.MISSING
        cursor : Scope = self.expand_scope

        try:
            self.trace_start("get", key)

            while cursor is not None:
                if type(cursor).__name__ == key:
                    result = cursor
                    break
                if cursor.get("_self", Sentinel.MISSING) == key:
                    result = cursor
                    break
                elif key in cursor:
                    result = cursor[key]
                    break

                new_cursor: Any | None =  cursor.get("_up", None)
                if new_cursor is None:
                    break
                else:
                    cursor = new_cursor

            if isinstance(result, Scope):
                result = Expander(result)
            elif isinstance(result, str):
                result = self.expand(result)
            elif result is Sentinel.MISSING:
                raise KeyError(key)
            return result

        finally:
            self.trace_end("get", key, result)

    # ====================================
    # Trivial classes just so we can distinguish between literal strings and macro strings without
    # having to do regex stuff every time.

    class Blocks(list[Literal | Macro]): pass

    # Hancho's template expansions can cause infinite loops, so we need some simple complexity
    # tracking here. This is _not_ some precise thing, it's just a tripwire to keep us from blowing
    # up the whole Python stack.
    # If you do weird things like load scripts from inside macros and you hit MAX_STEPS, that's a
    # you problem.
    #
    # The evals and depth limits are arbitrary, but should be plenty - Hancho's test suites
    # currently pass with MAX_DEPTH = 3 and MAX_EVALS = 12.

    MAX_DEPTH : int = 30
    MAX_EVALS : int = 300

    # ==============================================================================================
    # Expand-in-place

    def xip(self, var : abc.Collection) -> Any:
        result : Any = var
        if Utils.is_mut_sequence(var):
            for i, v in enumerate(result):
                result[i] = self.xip(v)
        elif Utils.is_mut_mapping(var):
            for k, v in result.items():
                result[k] = self.xip(v)
        elif Utils.is_mut_set(var):
            temp = list(var)
            result.clear()
            for v in temp:
                result.add(self.xip(v))
        elif isinstance(var, str):
            result = self.expand(var)

        return result

    # ==============================================================================================
    # Note that we do _not_ suppress any BaseExceptions - they _must_ be propagated up to
    # callers. As of Python 3.11, this includes asyncio.CancelledError.

    # IMPORTANT IMPORTANT IMPORTANT
    # If you can't eval a macro, you return it unchanged.

    # TEFINAE : Template Expansion Failure Is Not An Error. Same idea as SFINAE in C++
    # - we don't fail on expansion failure so we can retry somewhere/somewhen else.

    def expand(self, text : str) -> Any:
        if not isinstance(text, str):
            raise TypeError(f"_expand - can't expand a {type(text)}")

        stats = check(Stats, cvg.stats)

        var : Any = text

        # Bail out if we've recursed too many times.
        old_depth = stats.expand_depth
        if old_depth > Expander.MAX_DEPTH:
            raise RecursionError(f"Expansion failed to terminate after {old_depth} recursions: {var!r}")
        stats.expand_depth = old_depth + 1

        trace_started = False
        old_var = None
        try:
            while isinstance(var, str) and var != old_var:
                old_var = var
                blocks = Expander.Blocks()
                (literals, macros) = self.split_text(var, blocks)

                # Stop if we have nothing in the string to expand.
                if literals + macros == 0:
                    return ""

                # If there's only one literal block, return var instead of the block as var is a
                # str and blocks[0] is a Literal
                elif literals == 1 and macros == 0:
                    return var

                # If there's only one macro block, eval it unless we've done too many evals already.
                elif literals == 0 and macros == 1:
                    block = blocks[0]
                    old_evals = stats.expand_evals
                    if old_evals >= self.MAX_EVALS:
                        raise RecursionError(f"Expansion failed to terminate after {old_evals} evals: {block!r}")
                    stats.expand_evals = old_evals + 1

                    try:
                        self.trace_start("eval", block)
                        exp = Expression(block[1:-1])
                        var = eval(exp, {}, self)
                    except RecursionError:
                        raise
                    except Exception as ex:
                        #print(f"Eval failed because {ex!r}")
                        pass
                    finally:
                        self.trace_end("eval", block, var)

                # Otherwise we're doing a template expansion.
                # FIXME Note that we want to _expand_ each macro in the template and not _eval_
                # them - the former keps expanding and eval'ing until we reach a fixed point, the
                # latter only does one pass.
                # Otherwise we could accidentally put partially-expanded templates from a sub-Dict
                # into the parent Dict's expansion where they would not be expandable.
                # We need a test case for this.

                else:
                    if not trace_started:
                        trace_started = True
                        self.trace_start("expand", var)
                    for i, block in enumerate(blocks):
                        if isinstance(block, Macro):
                            blocks[i] = self.expand(block)
                    var = "".join(Utils.stringify(block) for block in blocks)

        finally:
            if trace_started:
                self.trace_end("expand", text, var)
            stats.expand_depth = old_depth
            if old_depth == 0:
                # We just finished an expansion - reset the eval budget
                stats.expand_evals = 0

        var = var.expand_scope if isinstance(var, Expander) else var
        return var

    # ==============================================================================================
    # Turns "{foo} bar {baz} blep" int [Macro("{foo}"), Literal("bar"), ...]

    class SplitCount(NamedTuple):
        literals : int
        macros : int

    @classmethod
    def split_text(cls, text : str, out_blocks : Blocks | None = None) -> SplitCount:
        """
        Extracts all innermost delimited spans from a block of text and produces a list of string
        literals and macros. Note that we're not handling "escaped" delimiters, instead we just
        translate "«»" into "{}" right before we run a command
        """

        cursor = 0
        idelim = -1
        macros = 0
        literals = 0

        ldelim = "{"
        rdelim = "}"

        for i, c in enumerate(text):
            if c == ldelim:
                idelim = i
            elif c == rdelim and idelim >= 0:
                if cursor < idelim:
                    if out_blocks is not None:
                        out_blocks.append(Literal(text[cursor:idelim]))
                    literals += 1
                if out_blocks is not None:
                    out_blocks.append(Macro(text[idelim:i+1]))
                macros += 1
                cursor = i + 1
                idelim = -1

        if cursor < len(text):
            if out_blocks is not None:
                out_blocks.append(Literal(text[cursor:]))
            literals += 1

        if out_blocks:
            assert len(out_blocks) == (literals + macros)

        return Expander.SplitCount(literals = literals, macros = macros)

    # ====================================
    # a copy of our box chars for copy-pasting
    # ┌─
    # └─

    def trace_start(self, action : str, arg : Any) -> None:
        log = cvg.log
        if log._level_int > Log.TRACE: return

        indent_color = Log.RESET
        if action == "expand":
            self.action_color = Utils.hex_to_ansi(0xFFAAAA)
            indent_color = Utils.hex_to_ansi(0xFFAAAA)
        elif action == "eval":
            self.action_color = Utils.hex_to_ansi(0xAAFFAA)
            indent_color = Utils.hex_to_ansi(0xAAFFAA)
        elif action == "get":
            self.action_color = Utils.hex_to_ansi(0xAAAAFF)
            indent_color = Utils.hex_to_ansi(0xAAAAFF)
        elif action == "range":
            self.action_color = Utils.hex_to_ansi(0xFFFFFF)
            indent_color = Utils.hex_to_ansi(0xFFFFFF)

        if action == "range":
            log.trace(f"{Log.WHITE}┌── {self.scope_color}{self.scope_tag}{Log.RESET} begin {arg}\n")
            log.indent(Log.WHITE)
        else:
            #log.trace(f"{indent_color}┌ {scope_color}{scope_tag}{Log.RESET}.{action}({arg})\n")
            log.trace(f"{indent_color}V {self.scope_color}{self.scope_tag}{Log.RESET}.{self.action_color}{action}{Log.RESET}({arg})\n")
            log.indent(indent_color)

    def trace_up(self, up : Scope, action : str, arg : Any) -> None:
        # This can be used to trace "traverse an _up link", but it was spammy so it's not used at
        # the moment.
        if cvg.log._level_int > Log.TRACE: return
        up_color   = Utils.obj_to_ansi_color(up)
        up_tag     = Utils.instance_tag(up)
        cvg.log.trace(f"{self.scope_color}┌ {self.scope_tag}{Log.RESET}.{action}({arg}) -> {up_color}{up_tag}\n")

    def trace_end(self, action : str, arg : Any, result : Any) -> None:
        log = cvg.log
        if log._level_int > Log.TRACE: return
        indent_color = log._indent_stack[-1]
        log.dedent()

        if isinstance(result, Expander):
            result = result.expand_scope

        if action == "range":
            log.trace(f"{Log.WHITE}└── {self.scope_color}{self.
            scope_tag}{Log.RESET} end {arg}\n")
        else:
            result_color = Log.RESET
            result_type  = type(result)

            if Utils.is_mapping(result):
                result_color = Utils.obj_to_ansi_color(result)

            log.trace(f"{indent_color}└─>{self.scope_color}{arg}:{result_type.__name__}{Log.RESET} = ")
            #log.trace(f"{indent_color}{self.scope_color}{arg}:{result_type.__name__}{Log.RESET} = ")
            if not Utils.is_scalar(result):
                log.trace(f"{result_color}{Utils.instance_tag(result)}\n")
            else:
                log.trace(f"{result_color}{result!r}\n")

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

    seen : contextvars.ContextVar[set[Any]] = contextvars.ContextVar("seen")
    match_pointer : re.Pattern = re.compile(r"0[xX][0-9a-fA-F]{4,16}")

    @classmethod
    def depointer(cls, text : str) -> str:
        text = Dumper.match_pointer.sub("0x...", text)
        return text

    @dataclass
    class Opts:
        cur_depth : int = 0
        max_depth : int = 5
        indent_stack2 : list[str] = field(default_factory=list)
        # Any container fields with these names , values, or types will _not_ be dumped
        fold : list[Any] = field(default_factory=list)
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
        val : Any,
        cur_depth : int =0,
        max_depth : int =3,
        fold : list[Any] | None = None,
        print_id : bool =True,
        color_code : bool =False,
        dump_private : bool =True,
        width:int=80,
        len:int=0,
        tab:int=4,
        indent_level:int = 0
    ) -> str:
        old_seen : set[Any] | None = Dumper.seen.get(None)
        new_seen : set[Any] = set() if old_seen is None else old_seen

        with Dumper.seen.set(new_seen):
            fold = fold or default_folds
            indent_stack2: list[str] = [" "] * indent_level
            opts = Dumper.Opts(cur_depth, max_depth, indent_stack2, fold, print_id, color_code, dump_private, tab, len, width, False)
            return cls._dump_to_str(None, val, opts)

    @staticmethod
    def dprint(*args : Any, **kwargs : Any) -> None:
        result: str = Dumper.dump(*args, **kwargs)
        dprint(result)

    @classmethod
    def _dump_to_str(cls, key : dump_key, val : Any, opts : Opts)-> str:
        if opts.cur_depth > 0 and (key in opts.fold or val in opts.fold or type(val) in opts.fold):
            return cls._dump_prefix(key, val, opts) + str(Sentinel.FOLDED)
        elif isinstance(val, opaque_types):
            return cls._dump_scalar(key, val, opts)
        elif key == "_up":
            return cls._dump_prefix(key, val, opts) + "<up>"
        elif key == "__builtins__":
            return cls._dump_prefix(key, val, opts) + "<builtins>"
        elif isinstance(val, (type, FunctionType)):
            return cls._dump_scalar(key, val, opts)
        elif val is sys.modules[__name__]:
            return cls._dump_prefix(key, val, opts) + "<hancho module>"
        elif hasattr(type(val), "__dump__"):
            return val.__dump__(key, opts)
        elif Utils.is_sequence(val) or Utils.is_mapping(val) or Utils.is_set(val):
            return cls._dump_vector(key, val, val, opts)
        elif hasattr(val, "__dict__"):
            return cls._dump_vector(key, val, val.__dict__, opts)
        else:
            return cls._dump_scalar(key, val, opts)

    @classmethod
    def _dump_scalar(cls, key : dump_key, val : Any, opts : Opts) -> str:
        # FIXME how else can we verify that this is really a scalar?
        #scalars = (int, float, str, bool, types.NoneType, object, types.FunctionType)
        #if type(val) not in scalars:
        #    msg = f"Trying to dump {key}:{type(val)}={val}, but it's not a scalar"
        #    raise TypeError(msg)

        if isinstance(val, Sentinel):
            raise TypeError("Sentinels are not scalars")

        seen: set[Any] = Dumper.seen.get()
        if id(val) in seen:
            return cls._dump_prefix(key, val, opts) + "<deduped>"
        elif not isinstance(val, base_types):
            seen.add(id(val))

        # FIXME should we always be using repr here?
        return cls._dump_prefix(key, val, opts) + repr(val)

    @classmethod
    def _dump_vector(cls, key : dump_key, val : Any, contents : Any, opts : Opts) -> str:
        prefix : str= cls._dump_prefix(key, val, opts)

        if opts.cur_depth == opts.max_depth:
            return prefix + "<too deep>"

        seen : set[Any] = Dumper.seen.get()
        if id(val) in seen:
            return cls._dump_prefix(key, val, opts) + "<deduped>"
        elif not isinstance(val, base_types):
            seen.add(id(val))

        items : list[item[Any]]
        if Utils.is_mapping(contents):
            ld, items, rd = "{", list(contents.items()), "}"
        elif Utils.is_sequence(contents):
            items = [(None, v) for v in contents]
            if isinstance(contents, tuple):
                ld : str = "("
                rd : str = ",)" if len(items) == 1 else ")"
            else:
                ld : str = "["
                rd : str = "]"
        elif Utils.is_set(contents):
            items = [(None, v) for v in contents]
            ld : str = "{"
            rd : str = "}"

        else:
            raise AssertionError(f"Don't know what to do with {type(val)}")

        dump_private: bool = opts.dump_private
        items2:list[Any] = []
        any_private = False

        for x in items:
            if is_public(x[0]) or dump_private:
                items2.append(x)
            else:
                any_private = True
        if any_private:
            items2.append(("<private fields>", Sentinel.FOLDED))

        return cls._dump_items(key, prefix, ld, items2, rd, opts)

    @classmethod
    def _dump_items(cls, key : dump_key, prefix : str, ld : str, items : list[item[Any]], rd : str, opts : Opts) -> str:
        # This slightly odd construct is so that when a deeply nested container doesn't fit on a
        # line, we rewind the callstack back to the topmost container that was not forced to be
        # flat.

        if opts.cur_depth == opts.max_depth:
            return prefix + "<too deep>"

        if key in opts.fold:
            raise AssertionError()

        if opts.flat:
            return prefix + cls._dump_items_flat(key, ld, items, rd, opts)
        else:
            # If the dump_items_flat fails, we _must_ roll back the seen set to its
            # old value.
            old_contents :list[Any]= list(Dumper.seen.get())
            try:
                return prefix + cls._dump_items_flat(key, ld, items, rd, replace(opts, flat = True))
            except Dumper.LineTooLong:
                seen:set[Any] = Dumper.seen.get()
                seen.clear()
                seen.update(old_contents)
                rhs:str = cls._dump_items_deep(key, ld, items, rd, opts)
                return prefix + rhs

    @classmethod
    def _dump_items_flat(cls, key : dump_key, ld : str, items : list[item[Any]], rd : str, opts : Opts) -> str:
        result : str = ld

        for i in range(len(items)):
            key2 : int | str | None = items[i][0]
            val2 : Any = items[i][1]
            result += cls._dump_to_str(key2, val2, opts)
            if i < len(items) - 1: result += ", "
            if opts.len + len(result) + len(rd) > opts.width:
                raise Dumper.LineTooLong()

        return result + rd

    @classmethod
    def _dump_items_deep(cls, key : dump_key, ld : str, items : list[item[Any]], rd : str, opts : Opts) -> str:
        result = ld + "\n"

        opts = replace(opts, cur_depth = opts.cur_depth + 1)

        opts.indent_stack2.append(" ")

        # len(pad) + 1 for the trailing comma
        pad : str = (" " * opts.tab) * len(opts.indent_stack2)
        new_opts : Dumper.Opts = replace(opts, len = len(pad) + 1)

        for i in range(len(items)):
            key2 : int|str|None= items[i][0]
            val2 : Any = items[i][1]
            result += pad + cls._dump_to_str(key2, val2, new_opts)
            if i < len(items) - 1: result += ","
            result += "\n"
        opts.indent_stack2.pop()

        pad = (" " * opts.tab) * len(opts.indent_stack2)
        return result + pad + rd

    @classmethod
    def _dump_prefix(cls, key : dump_key, val : Any, opts : Opts) -> str:
        prefix: str = ""
        if key:
            prefix += f"{key}"
        elif isinstance(val, (ABCMeta, abc.Callable, ModuleType)):
            prefix += val.__name__
        elif hasattr(val, 'name') and val.name is not None:
            prefix += val.name
        elif hasattr(val, '_self') and val._self is not None:
            prefix += val._self
        elif isinstance(val, abc.Mapping) and '_self' in val and val['_self'] is not None:
            prefix += val['_self']

        if isinstance(val, type):
            prefix += ":type"
        elif not isinstance(val, base_types):
            prefix += ":"
            prefix += type(val).__name__
            if opts.print_id: prefix += "@" + Utils.hex_tag(val)
        if prefix: prefix += " = "
        return prefix

    @classmethod
    def any_to_key(cls, var: Any) -> str:
        # FIXME this needs a test case
        result : str = Dumper.dump(
            val=var,
            color_code=False,
            dump_private=False,
            max_depth=999,
            print_id=False,
            tab=0,
            width=999,
        )

        result = Dumper.depointer(result)
        result = "".join(result.split())
        return result

# Not using this for dumper yet, but I might. Or I could use it for tracing.
def print_tree(k : str, v : Any, lhs : str = "") -> None:
    leaf = '⊡ '
    open = '⊟ '
    fold = '⊞ '
    tee  = '├──'
    pipe = '│  '
    hook = '└──'
    gap  = '   '
    folded = []

    if not isinstance(v, dict):
        print(leaf + str(k) + ": " + repr(v))
    elif any(v is f for f in folded):
        print(fold + str(k))
    else:
        print(open + str(k))
        for i, (k2, v2) in enumerate(v.items()):
            tab1, tab2 = (hook, gap) if i == len(v) - 1 else (tee, pipe)
            print(lhs + tab1, end="")
            print_tree(k2, v2, lhs + tab2)


# endregion
# ==================================================================================================
# region Runner

class Runner:

    @classmethod
    def reset(cls) -> None:
        cls.max_jobs:int   = cvg.flags.max_jobs
        cls.max_errors:int = cvg.flags.max_errors

        cls.core_sem  : asyncio.Semaphore = asyncio.Semaphore(cls.max_jobs)
        cls.core_lock : asyncio.Lock = asyncio.Lock()

        cls.aio_done_queue : asyncio.Queue = asyncio.Queue()
        cls.live_aio_tasks : set[asyncio.Task] = set()

    @classmethod
    async def acquire(cls, count : int) -> int:
        # A task that requires a lot of cores can block tasks behind it in the queue. This is
        # intended behavior.

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
    def release(cls, count : int) -> None:
        for _ in range(count):
            cls.core_sem.release()

    @classmethod
    async def async_run_tasks(cls) -> int:
        """Run all tasks until we run out."""
        log: Log = cvg.log
        project: Project = cvg.project
        stats: Stats = cvg.stats

        # ------------------------------------
        # Create asyncio tasks for all enabled Hancho tasks.

        for repo in project._repos:
            for task in repo.yield_tasks():
                if task._enabled:
                    task.create_aio_task()

        # ------------------------------------
        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.

        log.verbose("Running tasks...\n")

        while Runner.live_aio_tasks and (cvg.stats.tasks_broken + cvg.stats.tasks_failed) <= Runner.max_errors:
            finished_aio_task:asyncio.Task = await Runner.aio_done_queue.get()
            task:Task = finished_aio_task.hancho_task # type: ignore

            if task._error is None:
                task._complete = True
                if task._skipped:
                    stats.tasks_skipped += 1
                else:
                    stats.tasks_finished += 1
            else:
                match type(task._error):
                    case asyncio.CancelledError:
                        stats.tasks_cancelled += 1
                    case Task.CANCELLED:
                        stats.tasks_cancelled += 1
                    case Task.BROKEN:
                        stats.tasks_broken += 1
                    case Task.FAILED:
                        stats.tasks_failed += 1
                    case _:
                        log.warning(f"Weird exception {type(task._error)} >{task._error}< at {time.perf_counter()}\n")
                        stats.tasks_failed += 1

            if finished_aio_task is not None:
                Runner.live_aio_tasks.discard(finished_aio_task)

        failures: int = stats.tasks_broken + stats.tasks_failed
        if failures > Runner.max_errors:
            log.error(f"Too many failures after {failures}, cancelling tasks and stopping build\n")

            # Cancel all the asyncio.Tasks that haven't completed yet
            log.warning(f"Cancelling {len(Runner.live_aio_tasks)} tasks\n")

            # This tasks_cancelled count may be off by one or two due to in-flight tasks not being
            # accounted for in live_aio_tasks, but it doesn't matter - we're about to bail out due
            # to failures or someone ctrl-c'ing the build, this is purely cosmetic.

            stats.tasks_cancelled += len(Runner.live_aio_tasks)
            for t in Runner.live_aio_tasks:
                t.cancel()

            # and then wait on their cancellations to complete (it isn't instantaneous)
            await asyncio.gather(*Runner.live_aio_tasks, return_exceptions=True)

        return 1 if stats.tasks_failed or stats.tasks_broken else 0

# endregion
# ==================================================================================================
# region Project

class Project(Scope):
    def __init__(self, *, name : Identifier, root_p : Realpath, targets : list[str]) -> None:
        super().__init__(_self = "project", _up = cvg.flags)
        self.name: Identifier  = name
        self.root_p: Realpath = root_p
        self.targets: list[str] = targets or []

        self.out_files : set[Goodpath] = set()
        self._dedupe = Dict()
        self._repos : list[Repo] = []

    def build(self) -> int:
        # ------------------------------------
        # Select the set of tasks to run.

        if self.targets:
            for target in self.targets:
                # Enable all tasks whose name contains any of the targets
                for repo in self._repos:
                    for task in repo.yield_tasks():
                        if task.name is not None and target in task.name:
                            task.queue_task()

        elif cvg.flags.build_all:
            for repo in self._repos:
                for task in repo.yield_tasks():
                    task.queue_task()

        else:
            # Enable all tasks in the top repo
            for task in self._repos[0].yield_tasks():
                task.queue_task()

        # ------------------------------------
        # Run the tasks.

        result: int = asyncio.run(Runner.async_run_tasks())

        # ------------------------------------
        # Update stat DBs.

        if not cvg.flags.build_dry:
            for repo in cvg.project._repos:
                for task in repo.yield_tasks():
                    if task._complete:
                        task.update_repo_stat_db()
                repo.save_stat_db()

        return result

# endregion
# ==================================================================================================
# region Repo

class Repo(Scope):

    def __init__(self, *, name : Identifier, path : Realpath, root_r : Realpath, root_b : Goodpath) -> None:
        super().__init__(_self = "repo", _up = cvg.project)

        self.name: Identifier  = name
        self.path: Realpath  = path
        self.root_r: Realpath = root_r
        self.root_b: Goodpath = root_b

        ex: Expander = Expander(self)
        self.stat_db_path = Goodpath(ex.expand("{join(repo.root_b, 'hancho.json')}"))
        self.comp_db_path = Goodpath(ex.expand("{join(repo.root_b, 'compile_commands.json')}"))

        self._scripts : list[Script] = []
        self._stat_db : dict[str, dict] = {}

        if Path.isfile(self.stat_db_path):
            cvg.log.verbose(f"Loading stat_db {self.stat_db_path}\n")
            json_stats = Utils.load_json(self.stat_db_path)
            for file, json_stat in json_stats.items():
                self._stat_db[file] = json_stat
        else:
            cvg.log.verbose(Log.DRED + f"Stat db not found: {self.stat_db_path}\n")

    @classmethod
    def load_depfile(cls, filename : Realpath, format : str, task_cwd : Realpath) -> list[Realpath]:
        # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
        # NOTE: This does not handle filenames with escaped spaces in them, but I don't want to
        # write a whole .d parser yet.

        if not Path.isfile(filename):
            return []

        filename = Realpath(filename)
        task_cwd = Realpath(task_cwd)

        with open(str(filename), encoding="utf-8") as depcontents:
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
            deplines.append(Realpath(d))

        return deplines

    def save_stat_db(self) -> None:
        cvg.log.verbose(Log.DGREEN + f"Saving stat_db {self.stat_db_path}\n")
        Utils.save_json(self._stat_db, self.stat_db_path)

    def save_comp_db(self) -> None:
        pass
        ## Haven't tested this in an IDE, but I think it matches the spec.
        #cvg.log.verbose(f"Saving comp_db {comp_db_path}\n")
        #comp_db = []
        #for file, stat in self._stat_db.items():
        #    comp_db.append({
        #        "directory" : stat.cwd,
        #        "command"   : Utils.command_to_string(stat.command),
        #        "file"      : Path.relpath(file, stat.cwd),
        #    })
        #Utils.save_json(comp_db, comp_db_path)

    def yield_tasks(self) -> abc.Iterator[Task]:
        for script in self._scripts:
            yield from script._tasks

    @classmethod
    def hash_file(cls, abs_path : Realpath) -> str:
        cvg.stats.hash_calls += 1

        time_a = time.perf_counter()
        with open(str(abs_path), "rb") as f:
            cvg.stats.hash_bytes += os.fstat(f.fileno()).st_size
            digest = hashlib.file_digest(f, lambda: hashlib.blake2b(digest_size=16)).hexdigest()
        time_b = time.perf_counter()

        cvg.stats.hash_time += time_b - time_a
        return digest

    def get_stat(self, file : Realpath) -> dict:
        cvg.stats.stat_calls += 1

        _hash = self.hash_file(file)
        _stat = os.stat(file)

        stat = {
            "hash": _hash,
            "st_size": _stat.st_size,
            "st_mtime_ns": _stat.st_mtime_ns,
        }

        return stat

    def update_stat(self, file : Realpath, command : command_type) -> None:
        new_stat = self.get_stat(file)
        if command is not None:
            new_stat['command'] = Dumper.depointer(repr(command))
        if file in self._stat_db:
            old_stat = self._stat_db[file]
            self._stat_db[file] = Utils.merge(old_stat, new_stat)
        else:
            self._stat_db[file] = new_stat

    def stat_changed2(self, file : Realpath) -> str | None:
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

    def command_changed(self, file : Realpath, command : command_type) -> bool:
        old_command : str = self._stat_db[file]['command']
        new_command : str = Dumper.depointer(repr(command))
        return old_command != new_command

# endregion
# ==================================================================================================
# region Task

# FIXME get() should call eval or something? a lot of these task fields should delegate to script

class Task(Scope):

    class FAILED(Exception):    pass
    class CANCELLED(Exception): pass
    class BROKEN(Exception):    pass

    def __init__(self, *args : Any, **kwargs : Any) -> None:
        # Ideally, this should be the only reference to current_script we actually need
        # (well, apart from the global load/repo/include)
        super().__init__(_self = "task", _up = cvg.script)

        script = cvg.script

        self.name        = "task"
        self.desc        = "<no desc>"
        self.command : str | task_callback | list[str]    = ""
        self.root_b      = script.root_b
        self.cwd         = script.root_s
        self.depformat   = "gcc" if os.name == "posix" else "msvc"
        self.in_depfile : Goodpath | None = None
        self.job_size    = 1
        self.build_force = "{flags.build_force}"

        # This can't be self.fill, users may have added stuff via args that's needed for expansion.
        self.merge(*args, **kwargs)

        # ----------------------------------------
        # Private fields

        self._enabled = False

        self.cwd   : Realpath
        self.root_s : Realpath
        self.root_b : Goodpath

        # These two have to be dicts so that downstream tasks can pick named outputs from it if
        # needed.
        self._in_files :  dict[str, list[Goodpath]] = {}
        self._out_files : dict[str, list[Goodpath]] = {}

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
        self._stdout_text : str = ""
        self._stderr_text : str = ""
        self._cores = 0
        self._complete = False
        self._deplines : list[Realpath] = []

        cvg.script._tasks.append(self)

        # ----------------------------------------
        # Expand all input fields. These _must_ be expandable into filenames or tasks right now, as
        # we need to queue up input tasks before this task starts (and then expands everything else)

        ex = Expander(self)
        for k, v in self.__dict__.items():
            if k.startswith("in_"):
                ex.xip(v)

        # ----------------------------------------
        # Add all input fields that are tasks to _input_tasks so we can await them later.

        input_tasks = []
        leaves = list(Utils.yield_leaves(self.__dict__))
        for v in leaves:
            if isinstance(v, Task):
                if v is self:
                    assert v is not self
                input_tasks.append(v)
        self._input_tasks = input_tasks

        # ----------------------------------------

        # Auto-start the task if it was created dynamically during the build.
        if Utils.in_event_loop():
            self.queue_task()

    # These are here so the linter doesn't complain about arbitrary attribute access
    def __getattr__(self, name: str) -> Any:
        return self.__dict__.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)

    def __copy__(self) -> NoReturn:
        # Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
        raise AssertionError()

    def __deepcopy__(self, _ : dict[int, Any]) -> NoReturn:
        raise AssertionError()

    def create_aio_task(self) -> None:
        assert Utils.in_event_loop()

        if self._aio_task is None:
            t = asyncio.create_task(self.task_top(), context=self._aio_context)
            setattr(t, "hancho_task", self)  # noqa: B010
            Runner.live_aio_tasks.add(t)
            t.add_done_callback(lambda t: Runner.aio_done_queue.put_nowait(t))
            self._aio_task = t

    def queue_task(self) -> None:
        if self._enabled:
            return

        self._enabled = True
        cvg.stats.tasks_enabled += 1

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
            self.log_task(Log.ERROR, traceback.format_exception(self._error))
            if (self._stdout_text or self._stderr_text):
                self.log_task(Log.ERROR, self.dump_stdout())

        return self._out_files

    async def task_main(self) -> None:

        # ------------------------------------
        # Await all tasks in our input fields and then flatten them.

        await self.await_inputs()

        # ------------------------------------
        # We're ready to run

        cvg.stats.tasks_started += 1
        self._task_id = cvg.stats.tasks_started

        # ------------------------------------
        # Expand all mandatory fields in the raw config and fix raw file paths.

        # with Expander(self).trace_range("expand_task")):
        self.expand_task()

        # ------------------------------------
        # Inputs are ready, templates are expanded, see if everything's sane before we try running
        # commands.

        self.sanity_check()

        # ------------------------------------
        # Dry runs early out after the task is initialized but before we do .exists() checks or
        # run any commands.

        if cvg.flags.build_dry:
            return

        # ------------------------------------
        # Paths updated. See if we need to rebuild our outputs.

        if self.in_depfile and Path.isfile(self.in_depfile):
            real_depfile = Realpath(self.in_depfile)
            self._deplines = Repo.load_depfile(real_depfile, self.depformat, self._cwd)

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

        curlify = str.maketrans({"«" : "{", "»" : "}"})

        with timeit("Task"):
            if isinstance(self.command, str):
                command = self.command.translate(curlify)
                await self.run_command(command)
            elif isinstance(self.command, list):
                for command in self.command:
                    command = command.translate(curlify)
                    await self.run_command(command)
            elif isinstance(self.command, abc.Callable):
                await self.call_callback(self.command)
            elif self.command is not None:
                raise TypeError(type(self.command))

        # ------------------------------------
        # See if the task wrote all its output files

        for file in Utils.yield_leaves(self._out_files):
            if not os.path.exists(file):
                raise Task.FAILED(f"Task ran, but output file still missing: {file}")

        # ------------------------------------
        # Update the repo stat db

        # FIXME _don't_ do this, we don't want to update anything until _everything_ is done.
        # And we're done

    def update_repo_stat_db(self) -> None:
        with chdir(self._cwd):
            # Reload the depfile post-build so we pick up new deps
            if self.in_depfile and Path.isfile(self.in_depfile):
                self._deplines = Repo.load_depfile(Realpath(self.in_depfile), self.depformat, self._cwd)

            for file in Utils.yield_leaves([self._in_files, self._deplines]):
                if Path.exists(file):
                    cvg.repo.update_stat(file, None)

            for file in Utils.yield_leaves(self._out_files):
                if Path.exists(file):
                    cvg.repo.update_stat(file, self.command)

    async def await_inputs(self) -> None:
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

    def expand_task(self) -> None:
        ex = Expander(self)

        if cvg.log._level_int <= Log.DEBUG:
            self.log_task(Log.DEBUG, "Task before expand:\n")
            self.log_task(Log.DEBUG, Dumper.dump(self, fold=[Task], dump_private = True) + "\n")

        # We eval these first because we need them for filename expansion

        self._root_s = Realpath(ex.expand("{script.root_s}"))
        self._root_b = Goodpath(ex.expand("{task.root_b}"))
        self._cwd   = Realpath(ex.expand("{task.cwd}"))

        try:
            ex.trace_start("range", "expand io fields")
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
                    ex.xip(files)
                    goodfiles = self.fix_paths(field_name, files, self._root_s, self._root_b)
                    setattr(self, field_name, goodfiles[0] if len(goodfiles) == 1 else goodfiles)

                    if field_name == "in_depfile":
                        if len(goodfiles) > 1:
                            raise Task.BROKEN("iF_depfile can't be a list")
                    elif field_name.startswith("in_"):
                        self._in_files[field_name]  = goodfiles
                    elif field_name.startswith("out_"):
                        self._out_files[field_name] = goodfiles
                    #elif field_name.startswith("in_"):
                    #    self._in_files[field_name]  = [Path.join(self_cwd, file) for file in goodfiles]
                    #elif field_name.startswith("out_"):
                    #    self._out_files[field_name] = [Path.join(self_cwd, file) for file in goodfiles]
        finally:
            ex.trace_end("range", "expand io fields", None)

        # IO fields all expanded, we can expand the rest of the task now.

        # We must NOT expand everything in the task - it could contain references to things like
        # shared arrays of flags that contain templates, and if we expand it using _this_ task as
        # a context, all other tasks using the shared data will get incorrectly-expanded values

        if isinstance(self.command, list):
            ex.xip(self.command)
        elif isinstance(self.command, str):
            self.command = ex.expand(self.command)
        elif not isinstance(self.command, abc.Callable):
            raise TypeError(self.command)

        for key in ["name", "desc", "build_force", "root_b", "cwd", "depformat", "job_size"]:
            val = self.__dict__[key]
            if isinstance(val, str):
                setattr(self, key, ex.expand(val))

        if not cvg.flags.build_dry:
            temp = list(self._out_files.values())
            for file in Utils.yield_leaves(temp):
                os.makedirs(os.path.dirname(file), exist_ok=True)
            if self.in_depfile:
                os.makedirs(os.path.dirname(self.in_depfile), exist_ok=True)

        if cvg.log._level_int <= Log.DEBUG:
            self.log_task(Log.DEBUG, "Task after expand:\n")
            self.log_task(Log.DEBUG, Dumper.dump(self, fold=[Task], dump_private = True) + "\n")

    def fix_paths(self, field : str, raw_files : list[str], root_s : Realpath, root_b : Goodpath) -> list[Goodpath]:
        return [self.fix_path(field, raw_file, root_s, root_b) for raw_file in raw_files]

    def fix_path(self, field : str, raw_file : str, root_s : Realpath, root_b : Goodpath) -> Goodpath:
        """
        Input and output file paths in .hancho scripts are declared relative to the directory the
        script is in (stored in the config under 'script_cwd').
        In general we want to run commands from the root of the repo and store output files in
        repo/build, so we need to fix up the paths to match.
        """

        # File paths _must_ be abs'd after joining, otherwise they might look like they're under
        # script_dir, but they're not because the paths could have "../../../../.." in them.
        goodfile = Goodpath(os.path.join(root_s, raw_file))

        # Move all outputs under root_b and ensure their directories exist.
        # Note - This will also move "in_depfile" under root_b - this is _intentional_ as
        # it's an _output_ from the compiler and is not checked in to the source tree.

        if (field.startswith("out_") or field == "in_depfile") and not Path.startswith(goodfile, root_b):
            chunk = Path.relpath(goodfile, root_s)
            file = os.path.join(root_b, chunk)
            goodfile = Goodpath(file)

        if Utils.is_template(goodfile):
            print("**********bad file")
            print(goodfile)
            assert not Utils.is_template(goodfile)

        return goodfile

    def sanity_check(self) -> None:
        # Check for all task issues that break the build

        if not Path.startswith(self.root_b, cvg.repo.root_r):
            raise Task.BROKEN(f"The build root {self.root_b} is not under the repo root {cvg.repo.root_r}")

        if isinstance(self.command, list):
            for command in self.command:
                if not isinstance(command, str):
                    raise Task.BROKEN(f"Command is not a list of strings: {self.command}")
        elif not isinstance(self.command, (str, abc.Callable)):
            raise Task.BROKEN(f"Command is not a string or callable: {self.command}")

        # In strict mode, we mark a task broken if its command still has delimiters in it.
        if cvg.flags.strict:
            for command in Utils.yield_leaves(self.command):
                if isinstance(command, str) and (
                    Utils.is_macro(command) or Utils.is_template(command)
                ):
                    raise Task.BROKEN(f"STRICT: Command not fully expanded: {command!r}")

        # Check that all build files would end up under root_b
        for files in self._out_files.values():
            for file in files:
                if not Path.startswith(file, self.root_b):
                    raise Task.BROKEN(f"Path error, output file _out_files:{file} is not under build root {self.root_b}")

        # Check for task collisions
        for files in self._out_files.values():
            for file in files:
                if file in cvg.project.out_files:
                    raise Task.BROKEN(f"TaskCollision: Multiple tasks build {file}")
                cvg.project.out_files.add(file)

        # Check for missing inputs. We have to check build_dry, as the input files may only exist if
        # we're really running tasks.
        if not cvg.flags.build_dry:
            for files in self._in_files.values():
                for file in files:
                    if not Path.exists(file):
                        raise Task.BROKEN(f"Input file missing - {file}")

        # Tasks should have at most one depfile.
        if isinstance(self.in_depfile, list) and len(self.in_depfile) > 1:
            raise Task.BROKEN(f"Tasks can't have more than one dependency file! - {self.in_depfile}")

    def rebuild_reason(self) -> str:
        """Figures out why we have to run a Task, or returns "" if we don't."""

        repo = check(Repo, cvg.repo)

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
            if not Path.isfile(file):
                return f"File missing: {file!r}"
            file = Realpath(file)
            if reason := repo.stat_changed2(file):
                return reason

        for file in Utils.yield_leaves(self._out_files):
            file = Realpath(file)
            if repo.command_changed(file, self.command):
                return f"Command mismatch for {file!r}"

        # No changes, no reason to rebuild.
        return ""

    async def run_command(self : Task, command : str) -> None:
        repo = check(Repo, cvg.repo)

        # Print the cwd and command about to run
        # some/folder$ g++ blah blah blah
        self.log_task(Log.INFO, Log.GRAY3 + f"{Path.relpath(self.cwd, repo.root_r)}$ {command}\n")

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

            (stdout_text, stderr_text) = await proc.communicate()

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

        self._stdout_text = stdout_text.decode(errors="replace")
        self._stderr_text = stderr_text.decode(errors="replace")

        if proc.returncode == 2:
            raise Task.BROKEN("Command return code was 2 : bash error")
        elif proc.returncode:
            raise Task.FAILED(f"Command return code was non-zero : {proc.returncode}")

        if (self._stdout_text or self._stderr_text):
            self.log_task(Log.DEBUG, self.dump_stdout())

    async def call_callback(self : Task, command : task_callback) -> None:
        callback_dir = Path.relpath(cvg.script.root_s, cvg.repo.root_r)
        self.log_task(Log.INFO, f"{callback_dir}$ {command}\n")

        # Callbacks run from the script dir where they were defined so that relative paths used
        # in the callback will be correct.

        with chdir(cvg.script.root_s):
            result = command(self)

        # It would seem like we wouldn't have to explicitly unwrap one level of await-ness here,
        # but apparently that's just how Python waitables work.
        if inspect.isawaitable(result):
            result = await result

    def dump_stdout(self : Task) -> str:
        result = ""
        if self._stdout_text:
            result += "---------------- Stdout ----------------\n"
            result += self._stdout_text.strip() + "\n"
        if self._stderr_text:
            result += "---------------- Stderr ----------------\n"
            result += self._stderr_text.strip() + "\n"
        if self._stdout_text or self._stderr_text:
            result += "----------------------------------------\n"
        return result

    def log_task(self : Task, level : int, message : str | list[str]) -> None:
        # Log helper that adds the [ NN/ XX] tag before the log line.

        color = Log.YELLOW if level > Log.INFO else ""

        all_lines = []
        for chunk in Utils.yield_leaves(message):
            all_lines.extend(chunk.splitlines(keepends = True))

        for line in all_lines:
            if not cvg.log._line_buffer:
                cvg.log._log(level, "", f"[{self._task_id:3d}/{cvg.stats.tasks_enabled:3d}] ")
            cvg.log._log(level, color, line)

# endregion
# ==================================================================================================
# region Script

# This is not a types.ModuleType, but it still works fine as one. Making it a ModuleType is a bit
# annoying to work with.

class Script(Scope):

    def __init__(self : Script, name : Identifier, path : Realpath, root_s : Realpath, root_b : Goodpath) -> None:
        super().__init__(_self = "script", _up = cvg.repo)
        self.name   = name
        self.path   = path
        self.root_s  = root_s
        self.root_b  = root_b
        self._tasks = []

    def dump(self, *args : Any, **kwargs : Any) -> None:
        dprint(Dumper.dump(*args, **kwargs))

    @classmethod
    def load(
        cls,
        *args : Any,
        name : Identifier,
        path : Realpath,
        root_s : Realpath,
        root_b : Goodpath,
        is_repo : bool,
        is_dummy : bool,
        **kwargs : Any,
    ) -> Script:
        log     = cvg.log
        project = cvg.project

        repo = cvg.repo
        if is_repo:
            repo = Repo(name = name, path = path, root_r = root_s, root_b = root_b)

        script = Script(name = name, path = path, root_s = root_s, root_b = root_b)
        script.merge(*args, **kwargs)

        dedupe_key = Dumper.any_to_key(script)
        if dupe := project._dedupe.get(dedupe_key, None):
            log.verbose(Log.LIME + f"Deduped {script.path}\n")
            return dupe

        # File was _not_ deduped.
        project._dedupe[dedupe_key] = script
        repo._scripts.append(script)

        if is_repo:
            cvg.repo = repo
            project._repos.append(repo)

        if is_dummy:
            return script

        with open(str(script.path), encoding="utf-8") as file:
            source: str = file.read()
            code: CodeType = compile(source, str(script.path), "exec", dont_inherit=True)

        with cvg.log.indenter(Log.VERBOSE, Log.ORANGE), chdir(script.root_s), cvg.update(script = script):
            Utils.merge(script.__dict__, cvg._cv.get())

            cvg.log.verbose(Log.ORANGE + f"Running script {script.path}\n")
            exec(code, script.__dict__)

        if "__builtins__" in script.__dict__:
            del script.__dict__["__builtins__"]

        return script

# endregion
# ==================================================================================================
# region create_test_script

def create_test_script(raw_file : str, *, raw_name : str | None = None, raw_root : str | None = None, stdout : TextIO = sys.stdout, extra_flags : list[str] | None = None) -> Script:

    cvg.hancho = sys.modules[__name__]
    cvg.builtins = Builtins()
    cvg.flags = Flags.parse_flags(
        f"--name={raw_name}",
        f"--file={raw_file}",
        f"--root={raw_root}",
        *(extra_flags or [])
    )
    cvg.stats = Stats()
    cvg.log = Log(cvg.flags, log_stdout = stdout)
    cvg.log.verbose(Log.ORANGE + f"create_test_script {cvg.flags.path}\n")

    name  = Identifier(raw_name or "script")
    path = Realpath(raw_file)
    raw_root = raw_root or Path.dirname(path)
    root_r = Realpath(raw_root)
    root_s = Realpath(raw_root)
    root_b = Goodpath(Path.join(root_r, cvg.flags.build_dir))

    Runner.reset()

    cvg.project = Project(name = name, root_p = root_r, targets = [])
    cvg.repo    = Repo(
        name  = name,
        path  = path,
        root_r = root_r,
        root_b = root_b
    )

    cvg.script = Script.load(
        name = name,
        path = path,
        root_s = root_s,
        root_b = root_b,
        is_repo = True,
        is_dummy = True
    )

    return cvg.script

# endregion
# ==================================================================================================
# region main

class Builtins(Scope):
    def __init__(self) -> None:
        super().__init__(_up = None, _self = "builtins")
        self.hancho   = sys.modules[__name__]
        self.log      = Log()
        self.Dict     = Dict
        self.Task     = Task
        self.Tool     = Tool
        self.Dumper   = Dumper
        self.getcwd   = os.getcwd
        self.dprint   = dprint
        self.dump     = Dumper.dprint
        self.Goodpath = Goodpath
        self.Realpath = Realpath

        self.flatten  = Utils.flatten_tree
        self.run_cmd  = Utils.run_cmd
        self.weave    = Utils.weave

        self.join     = Path.join
        self.split    = Path.split
        self.swapext  = Path.swapext
        self.topdir   = Path.topdir
        self.relpath  = Path.relpath
        self.stem     = Path.stem

        self.resolve  = lambda x : Realpath(x)
        self.abspath  = lambda x : Goodpath(x)

        self.realpath = lambda x : Realpath(x)
        self.goodpath = lambda x : Goodpath(x)

        self.basename = Path.basename
        self.dirname  = Path.dirname


def main() -> int:
    cvg.hancho   = sys.modules[__name__]
    cvg.stats    = Stats()
    cvg.builtins = Builtins()

    #cvg.flags    = Flags(_up = cvg.builtins)
    #cvg.flags.parse_flags(*sys.argv)
    flags = Flags.parse_flags(*sys.argv)
    cvg.flags = flags

    log = Log(flags)
    cvg.log = log

    temp = Dict(
        _self  = "temp",
        _up    = flags,
        #file   = flags.file,
        #name   = flags.name,
        #path   = flags.path,
        #root_s = flags.root_s,
        #root_r = flags.root_r,
        #root_b = flags.root_b,
    )

    ex = Expander(temp)


    name   = Identifier(ex.name)
    path   = Realpath(ex.path)
    root_p = Realpath(ex.root)
    root_r = Realpath(ex.root)
    root_s = Realpath(ex.root)
    root_b = Goodpath(Path.join(ex.root_r, cvg.flags.build_dir))

    flags.name   = name
    flags.path   = path
    flags.root_p = root_p
    flags.root_r = root_r
    flags.root_s = root_s
    flags.root_b = root_b

    cvg.project  = Project(
        name    = name,
        root_p  = root_p,
        targets = flags.targets
    )

    cvg.repo = Repo(
        name = name,
        path = path,
        root_r = root_r,
        root_b = root_b
    )

    Runner.reset()

    log.verbose(Log.ORANGE + f"Script {cvg.flags.path}\n")
    log.verbose(Log.GRAY3 + f"Command line : {" ".join(sys.argv)}\n")
    log.verbose(Log.GRAY3 + "Verbose mode on\n")
    log.debug(Log.GRAY3 + "Debug mode on\n")
    log.trace(Log.GRAY3 + "Trace mode on\n")

    # ------------------------------------
    # Load the root script, which will transitively load everything else.

    with timeit("Loading scripts"):
        cvg.script = Script.load(
            name     = name,
            path     = path,
            root_s   = root_s,
            root_b   = root_b,
            is_repo  = True,
            is_dummy = False
        )

    # ------------------------------------
    # Build everything

    with timeit("Build"):
        result = cvg.project.build()

    # ------------------------------------
    # Print build stats

    cvg.stats.tasks_created = 0
    for repo in cvg.project._repos:
        cvg.stats.tasks_created += len(list(repo.yield_tasks()))

    cvg.log._log(Log.VERBOSE, Log.DBLUE, Dumper.dump(cvg.stats) + "\n")

    # ------------------------------------
    # Print pass/fail

    if cvg.stats.tasks_failed or cvg.stats.tasks_broken:
        cvg.log.error("BUILD FAILED\n")
    elif cvg.stats.tasks_finished:
        cvg.log.info(Log.GREEN + "BUILD PASSED\n")
    else:
        cvg.log.info(Log.BLUE + "BUILD CLEAN\n")

    # ------------------------------------
    # Done

    cvg.log._flush()

    return result

# ----------------------------------------

def _start() -> int:
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

#def _load(file, *args, name, path, root, build_root, **kwargs):
#    assert Path.isresolved(path)
#    assert Path.isresolved(root)
#    assert Path.isabs(build_root) # may not exist yet
#    return Script.load(*args, name = name, file = file, path = path, root_s = root, root_b = build_root, is_repo = False, is_dummy = False, **kwargs)

def load(raw_file : str, *args : Any, raw_name : str | None = None, raw_root : str | None = None, raw_root_b : str | None = None, **kwargs : Any) -> Script:

    raw_name   = raw_name   or Defaults.name
    raw_root   = raw_root   or Defaults.root
    raw_root_b = raw_root_b or Defaults.root_b

    temp = Dict(
        _self    = "temp",
        _up      = cvg.script,
        file     = raw_file,
        path     = "{resolve(file)}",
        name     = raw_name,
        root     = raw_root,
        root_b   = raw_root_b,
        is_repo  = False,
        is_dummy = False,
        hancho = sys.modules[__name__]
    )

    ex = Expander(temp)

    name   = Identifier(ex.name)
    path   = Realpath(ex.file)
    root_s = Realpath(ex.root)
    root_b = Goodpath(ex.root_b)

    return Script.load(
        *args,
        name     = name,
        path     = path,
        root_s   = root_s,
        root_b   = root_b,
        is_repo  = False,
        is_dummy = False,
        **kwargs,
    )


def include(raw_file : str) -> None:

    temp = Dict(
        _self    = "temp",
        _up      = cvg.script,
        file     = raw_file,
        is_repo  = False,
        is_dummy = False
    )

    ex = Expander(temp)
    path  = Realpath(ex.file)

    with open(str(path), encoding="utf-8") as file:
        source = file.read()
        code = compile(source, path, "exec", dont_inherit=True)

    cvg.log.verbose(Log.ORANGE + f"include {path}\n")

    exec(code, cvg._cv.get())
    Utils.merge(cvg.script, cvg._cv.get())

    if "__builtins__" in cvg.script.__dict__:
        del cvg.script.__dict__["__builtins__"]

def load_repo(raw_file : str, *args : Any, raw_name : str | None = None, raw_root : str | None = None, raw_root_b : str | None = None, **kwargs : Any) -> Script:
    # FIXME we should be able to collapse this down a lot.

    temp = Dict(
        *args,
        _self    = "temp",
        _up      = cvg.script,
        file     = raw_file,
        name     = raw_name,
        root     = raw_root,
        root_b   = raw_root_b,
        is_repo  = False,
        is_dummy = False,
        **kwargs
    )
    ex = Expander(temp)

    name   = Identifier(ex.name)
    path   = Realpath(ex.file)
    root_s = Realpath(ex.root or cvg.repo.root_r)
    root_b = Goodpath(ex.root_b or Path.join(root_s, cvg.flags.build_dir))

    #script = Script(name = name, path = path, root_s = root_s, root_b = root_b)
    #script.merge(*args, **kwargs)


    return Script.load(
        *args,
        name     = name,
        path     = path,
        root_s   = root_s,
        root_b   = root_b,
        is_repo  = True,
        is_dummy = False,
        **kwargs
    )

def build() -> int:
    return cvg.project.build()


sys.modules["hancho"] = sys.modules[__name__]

def blah() -> None:
    sys.modules["hancho"].path = __file__ # type: ignore
    sys.modules["hancho"].root = os.path.dirname(__file__) # type: ignore
blah()

# endregion
# ==================================================================================================

if __name__ == "__main__":
    sys.exit(_start())
