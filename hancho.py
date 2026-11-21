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
import asyncio
import colorsys
import contextvars
import functools
import hashlib
import inspect
import io
import json
import os
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
from collections.abc import Generator, ItemsView, Iterator
from contextlib import chdir, contextmanager, suppress
from dataclasses import dataclass, field, replace
from itertools import product
from types import CodeType, FunctionType, GeneratorType, ModuleType, NoneType
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Concatenate,
    NamedTuple,
    NoReturn,
    Self,
    TextIO,
    TypeIs,
    cast,
)

# endregion
# ==================================================================================================
# region Constants and globals

sys.modules["hancho"] = sys.modules[__name__]
sys.modules["hancho"].path = __file__ # type: ignore
sys.modules["hancho"].root = os.path.dirname(__file__) # type: ignore

def dprint(*args : Any, **kwargs : Any) -> None: # pragma: no cover
    print("DBG: ", end="")
    print(*args, **kwargs)

# 'branch' must be sequence/set/mapping in order for this to typecheck:
# a : str = "foo"
# b : tree[str] = a
# c : list[str] = ["bar"]
# d : tree[str] = c

type branch[LEAF] = LEAF | abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]] | None
type tree[LEAF] = abc.Sequence[branch[LEAF]] | abc.Set[branch[LEAF]] | abc.Mapping[str, branch[LEAF]]

type task_callback = abc.Callable[[Task], Any]
type item[T] = tuple[int | str | None, T]

type command_type = branch[str | task_callback]

type key_type = str | int | None

# These types don't need a type annotation when dumped.
base_types: tuple[type, ...] = (int, float, complex, bool, str, tuple, NoneType, type)

# These types don't get dumped because they're not really dumpable.

opaque_types: tuple[type, ...] = (GeneratorType, contextvars.Context, asyncio.Task, ModuleType, type)

default_folds: list[Any] = ["_dedupe", "_repos", "_scripts", "_tasks", "builtins", asyncio.Task]

@contextmanager
def timeit(name : str) -> Generator[None]:
    builtins = cv_builtins.get()
    builtins = cv_builtins.get()
    log = builtins.log
    a: float = time.perf_counter()
    yield
    b: float = time.perf_counter()
    log.verbose(text=Log.DBLUE + f"{name} took {b - a:8.6f} seconds\n")

# endregion
# ==================================================================================================
# region Sentinel

class Sentinel: # pragma: no cover
    def __init__(self, name: str) -> None:
        self.name: str = name

    def __repr__(self) -> str:
        return self.name

    def __bool__(self) -> bool:
        raise TypeError("Can't convert sentinels to bools")

    def __copy__(self) -> Sentinel:
        raise TypeError("Can't copy sentinels")

    def __deepcopy__(self, _: dict[int, Any]) -> Sentinel:
        raise TypeError("Can't copy sentinels")

    def __reduce__(self) -> str:
        raise TypeError("Can't pickle sentinels")

# Marks a function argument or template parameter as "not present" for when None would be a valid
# value.
___ : Any = Sentinel("MISSING")

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

        if isinstance(variant, ScopeBase):
            variant = {k:v for k,v in variant.__dict__.items() if not k.startswith("_")}

        result : str = ""
        for v in Utils.leaves(variant):
            if v is ___:
                continue
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

    @staticmethod
    def yield_leaves[T](branch : branch[T]) -> Generator[T]:
        if branch is None:
            return
        elif Utils.is_sequence(branch) or Utils.is_set(branch):
            for b in branch:
                yield from Utils.yield_leaves(b)
        elif Utils.is_mapping(branch):
            for b in branch.values():
                yield from Utils.yield_leaves(b)
        else:
            yield cast(T, branch)

    @staticmethod
    def leaves[T](branch : branch[T]) -> list[T]:
        return list(Utils.yield_leaves(branch))

    @staticmethod
    def weave(*args : tree[str]) -> list[str]:
        return [
            functools.reduce(lambda x, y: x + y, t)
            for t in product(*(Utils.leaves(a) for a in args))
        ]

    @staticmethod
    def map_in_place(map : abc.Callable, key : key_type, val : Any) -> Any:
        if isinstance(val, dict):
            for k, v in val.items():
                val[k] = Utils.map_in_place(map, k, v)
        elif isinstance(val, list):
            for i, v in enumerate(val):
                val[i] = Utils.map_in_place(map, i, v)
        elif isinstance(val, set):
            temp = list(val)
            for i, v in enumerate(temp):
                temp[i] = Utils.map_in_place(map, i, v)
            val.clear()
            for v in temp:
                val.add(v)
        else:
            val = map(key, val)

        return val

    # ------------------------------------

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
    def is_sequence(v : Any) -> TypeIs[abc.Sequence[Any]]:
        return isinstance(v, abc.Sequence) and not isinstance(v, (str, bytes, bytearray))

    @staticmethod
    def is_set(v : Any) -> TypeIs[abc.Set]:
        return isinstance(v, abc.Set)

    @staticmethod
    def is_mapping(v : Any) -> TypeIs[abc.Mapping[Any, Any]]:
        return isinstance(v, abc.Mapping)

    @staticmethod
    def is_mut_mapping(v : Any) -> TypeIs[abc.MutableMapping[Any, Any]]:
        return isinstance(v, abc.MutableMapping)

    @classmethod
    def is_expandable(cls, text : str) -> bool:
        if not isinstance(text, str):
            return False
        counts: Expander.SplitCount = Expander.split_text(text)
        return counts.macros > 0

    # ------------------------------------

    @staticmethod
    def hex_tag(obj : Any) -> str:
        return "0x" + hex(id(obj))[-4:].upper()

    @staticmethod
    def instance_tag(obj : Any) -> str:
        result : str = ""

        if '__name__' in obj:
            result += obj['__name__']
            return result
        else:
            result += type(obj).__name__
            result += "@"
            result += Utils.hex_tag(obj)
            return result

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

    @classmethod
    @contextmanager
    def write_and_swap(cls, filename:Goodpath) -> Generator:
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
        lhs : dict,
        rhs : dict,
        *,
        merge_mappings: bool = True,
        merge_sequences: bool = True,
        merge_sets: bool = True,
        keep_lhs: bool = True,
        keep_rhs: bool = True,
    ) -> Any:
        assert type(lhs) is dict
        assert type(rhs) is dict

        lkeys = lhs.keys()
        rkeys = rhs.keys()

        # Prune lhs keys if needed
        for key in (k for k in lkeys if k not in rkeys and not keep_lhs):
            del lhs[key]

        # Merge all lhs+rhs pairs
        for key in (k for k in lkeys if k in rkeys):

            lhs2:Any = lhs.get(key)
            rhs2:Any = rhs.get(key)

            if lhs2 is rhs2:
                pass
            elif Utils.is_mut_mapping(lhs2) and Utils.is_mapping(rhs2) and merge_mappings:
                if not isinstance(lhs2, dict):
                    lhs2 = lhs2.__dict__
                if not isinstance(rhs2, dict):
                    rhs2 = rhs2.__dict__
                Utils.merge(lhs2, rhs2)
            elif Utils.is_sequence(lhs2) and Utils.is_sequence(rhs2) and merge_sequences:
                lhs[key] = [*lhs2, *rhs2]
            elif Utils.is_set(lhs2) and Utils.is_set(rhs2) and merge_sets:
                lhs[key] = lhs2 | rhs2
            elif rhs2 is not None:
                lhs[key] = rhs2

        # Save rhs keys if needed
        for key in (k for k in rkeys if k not in lkeys and keep_rhs):
            lhs[key] = rhs[key]

        return lhs

    @staticmethod
    def merge_all(lhs : dict[str, Any], *args : dict[str, Any], **kwargs : Any) -> None:
        for rhs in (*args, kwargs):
            Utils.merge(lhs, rhs)

    # Fill-in-the-blank (or override what's there): Merges lhs and args into a new Dict, keeping
    # only keys that were already in lhs. For example, if you have a Dict that contains
    # "out_bin" and you merge it with "compile_cpp", Hancho will complain that "out_bin" is missing
    # - it sees both "out_obj" and "out_bin" and assumes the task produces both. If you do
    # compile_cpp.fill(...), "out_bin" does not get added to compile_cpp.

    @staticmethod
    def fill_all(lhs : dict[str, Any], *args : dict[str, Any], **kwargs : Any) -> None:
        for rhs in (*args, kwargs):
            Utils.merge(lhs, rhs, keep_rhs = False)

# endregion
# ==================================================================================================
# region Scope

class ScopeBase:

    # Expand all expandables at the top level of this scope.
    def __expand__(self) -> None:
        ex = Expander(self)
        for key, old_val in self.__dict__.items():
            if Utils.is_expandable(old_val):
                new_val = ex.expand(old_val)
                assert not Utils.is_expandable(new_val)
                self.__dict__[key] = new_val

    def get(self, key : str, default : Any = ___) -> Any:
        result = self.__dict__.get(key, ___)
        if result is ___:
            raise KeyError(key)
        return result

    def merge(self, *args : Any, **kwargs : Any) -> None:
        for d in [*args, kwargs]:
            if not isinstance(d, dict):
                d = d.__dict__
            Utils.merge(self.__dict__, d)

    def fill(self, *args : dict[str, Any], **kwargs : Any) -> ScopeBase:
        result = type(self)()
        Utils.merge(result.__dict__, self.__dict__)
        for rhs in (*args, kwargs):
            Utils.merge(result.__dict__, rhs, keep_rhs = False)
        return result

    def expand(self, template : str) -> Any:
        ex = Expander(self)
        return ex.expand(template)

    # ----------------------------------------
    # abc.MutableMapping

    def __getitem__(self, key : str) -> Any:
        result = self.__dict__.get(key, ___)
        if result is ___:
            raise KeyError(key)
        return result

    def __setitem__(self, key : str, val : Any) -> None:
        self.__dict__[key] = val

    def __delitem__(self, key : str) -> None:
        del self.__dict__[key]

    def __iter__(self) -> abc.Iterator[Any]:
        return self.__dict__.__iter__()

    def __len__(self) -> int:
        return len(self.__dict__)

    def items(self) -> ItemsView[str, Any]:
        return self.__dict__.items()

    # ----------------------------------------
    # We don't want the type checker to see this or it will stop type-checking attributes

    if not TYPE_CHECKING:
        def __getattr__(self, key : str) -> Any:
            result = self.__dict__.get(key, ___)
            if result is ___:
                raise AttributeError(key)
            return result

    def __setattr__(self, key : str, val : Any) -> None:
        self.__dict__[key] = val

# endregion
# ==================================================================================================
# region Dict/Tool

class Dict(ScopeBase):

    def __init__(self, *args : Any, **kwargs : Any) -> None:
        super().__init__()
        self.merge(*args, **kwargs)

    def __getattr__(self, key : str) -> Any:
        # This is just to appease the type checker
        return super().__getattr__(key) # type:ignore

# ====================================
# A Tool is just a callable alias for Dict to make build scripts more readable.

class Tool(Dict):
    def __call__(self, *args : Any, **kwargs : Any) -> Task:
        return Task(self, *args, **kwargs)

# endregion
# ==================================================================================================
# region Log

class Log(ScopeBase):

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

    def __init__(self, *args : dict[str, Any], **kwargs : Any) -> None:
        super().__init__()
        self.level     : str    = "info"
        self.wrap      : bool   = False
        self.color     : bool   = True
        self.timestamp : bool   = True
        self.width     : int    = shutil.get_terminal_size().columns
        self.stdout    : TextIO = sys.stdout
        Utils.merge_all(self.__dict__, *args, **kwargs)

        self._level_int     : int        = getattr(Log, self.level.upper())
        self._time_origin   : float      = time.perf_counter()
        self._indent_stack  : list[str]  = []
        self._line_buffer   : str        = ""
        self._match_escapes : re.Pattern[str] = re.compile(r"(\x1B.*?m)")
        self._stdout : TextIO

        if isinstance(self.stdout, str):
            self._stdout = open(str(self.stdout), "w")  # noqa: SIM115
        elif isinstance(self.stdout, io.TextIOBase):
            self._stdout = self.stdout
        else:
            raise ValueError(f"Don't know how to use {self.stdout} as an output")

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
        if not isinstance(text, str):
            text = repr(text) + "\n"

        if log_level < self._level_int:
            return

        # FIXME str.partition might be easier here
        lines: list[str] = text.splitlines(keepends=True)
        for line in lines:
            if self._line_buffer == "":
                self._line_buffer += f"[{time.perf_counter() - self._time_origin:8.3f}] " if self.timestamp else ""
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
#    def tree_all(func : abc.Callable) -> abc.Callable:
#        """Turns a predicate into one that can be applied to arbitrarily nested containers."""
#
#        @functools.wraps(func)
#        def wrapper(variant : Any, *args : Any, **kwargs : Any) -> bool:
#            return all(func(v, *args, **kwargs) for v in Utils.leaves(variant))
#
#        return staticmethod(wrapper)


#    @staticmethod
#    def relpath_t(lhs, rhs) -> tree[str]:
#        if isinstance(rhs, (list, tuple, set)):
#            return [Path.relpath_t(lhs, rh) for rh in rhs]
#            #result = [Path.relpath(lhs, rh) for rh in Utils.leaves(rhs)]
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
# region Goodpath/Realpath/Literal/Macro/Blocks

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

class Blocks(list[Literal | Macro]):
    pass

# endregion
# ==================================================================================================
# region Stats - trivial build statistics and tracking vars for expansion to catch infinite
# loops/recursion

class Stats(ScopeBase):
    def __init__(self) -> None:
        super().__init__()
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
# region Flags

class Flags:

    def __init__(self) -> None:
        self.project = {}
        self.build   = {}
        self.log     = {}
        self.extra   = {}

    def parse_flags(self, *argv : str) -> None:

        if len(argv) > 0 and "hancho.py" in argv[0]:
            argv = argv[1:]

        # ------------------------------------
        # fmt: off

        desc = textwrap.dedent("""
        ================================================================================
                        Hancho is a simple, pleasant build system
        ================================================================================
        """)

        class MyFormatter(argparse.RawDescriptionHelpFormatter, argparse.ArgumentDefaultsHelpFormatter):
            pass

        levels: list[str] = ["trace", "debug", "verbose", "info", "warning", "error", "critical", "quiet"]

        def str_to_bool(v : str) -> bool:
            if v.lower() in ("yes", "true", "t", "y"): return True
            if v.lower() in ("no", "false", "f", "n"): return False
            raise argparse.ArgumentTypeError("Not a bool")

        default_root  = "{dirname(resolve(project.file))}"
        default_jobs  = os.cpu_count() or 1
        default_width = shutil.get_terminal_size().columns

        parser = argparse.ArgumentParser(description = desc, formatter_class = MyFormatter)

        parser.add_argument(        "project.targets",   nargs="*",                                   help="The names or partial names of targets to build")
        parser.add_argument("-o", "--project.opts",      type=str,         default = None,            help="File containing JSON that will be used as additional options")
        parser.add_argument(      "--project.name",      type=str,         default = "project",       help="Name of the whole build project, if you don't want it to be 'project'")
        parser.add_argument("-f", "--project.file",      type=str,         default = "build.hancho",  help="Path to the .hancho file that starts the build.")
        parser.add_argument("-r", "--project.root",      type=str,         default = default_root,    help="The top script runs in this directory.")

        parser.add_argument(      "--build.dir",         type=str,         default = "build",         help="The name of the directory to put output files in.")
        parser.add_argument(      "--build.tag",         type=str,         default = "",              help="Tagged builds will have separate subdirectories under the build directory.")
        parser.add_argument(      "--build.force",       type=str_to_bool, default = False,           help="Rebuild targets even if they're clean.")
        parser.add_argument("-a", "--build.all_targets", type=str_to_bool, default = False,           help="Build every task in every repo.")
        parser.add_argument("-n", "--build.dry_run",     type=str_to_bool, default = False,           help="Dry run - Do everything except actually run commands.")
        parser.add_argument(      "--build.strict",      type=str_to_bool, default = True,            help="Strict mode, slightly more error checking to catch footguns.")
        parser.add_argument(      "--build.max_errors",  type=int,         default = 0,               help="The maximum number of task errors we tolerate before abandoning the build")
        parser.add_argument("-j", "--build.max_jobs",    type=int,         default = default_jobs,    help="Run a maximum of N jobs in parallel.")

        parser.add_argument("-l", "--log.level",         choices=levels,   default = "info",          help="Select verbosity level.")
        parser.add_argument("-w", "--log.wrap",          type=str_to_bool, default = False,           help="Wrap lines around the console instead of clipping them")
        parser.add_argument("-c", "--log.color",         type=str_to_bool, default = True,            help="Use color in the log for better readability")
        parser.add_argument(      "--log.timestamp",     type=str_to_bool, default = True,            help="Timestamp each log line")
        parser.add_argument(      "--log.width",         type=int,         default = default_width,   help="Max width (in characters) of the log if not wrapping lines")
        parser.add_argument(      "--log.stdout",        type=str,         default = sys.stdout,      help="Redirect the log's output to this filename")

        (argv_namespace, unrecognized) = parser.parse_known_args(args=argv)
        argv_vars = vars(argv_namespace)

        opt_file_name = argv_vars.pop("project.opts", None)

        # fmt: on
        # ------------------------------------

        def set_by_path(lhs : Any, key : str, val : Any) -> None:
            if val is None:
                return

            parts: list[str] = key.split(".")

            # Flags we don't know about get shunted to flags.extra
            if parts[0] not in self.__dict__:
                parts = ['extra', *parts]

            for part in parts[:-1]:
                if part not in lhs:
                    lhs[part] = Dict()
                lhs = lhs[part]
            lhs[parts[-1]] = val


        # ------------------------------------
        # Load flags from opt_file if present

        if opt_file_name and os.path.exists(opt_file_name):
            with open(str(opt_file_name)) as f:
                try:
                    opt_file = json.load(f)
                    for k, v in opt_file.items():
                        set_by_path(self.__dict__, k, v)
                except Exception as _:
                    print(f"Opt file {opt_file_name!r} invalid!\n")
                    traceback.print_exc()

        # ------------------------------------
        # Add argv_vars

        for k, v in argv_vars.items():
            set_by_path(self.__dict__, k, v)

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

                set_by_path(self.__dict__, key, convert(val))

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

    def __init__(self, object : Any) -> None:
        assert not isinstance(object, dict)

        self.expand_scope: dict = object if isinstance(object, dict) else object.__dict__
        self.expand_object: Any = object

    # ====================================
    # Expander _must_ implement __getattr__ so that macros like "{script.foo}" work.

    def __getattr__(self, key : str) -> Any:
        try:
            return self.internal_get(key)
        except KeyError as ex:
            raise AttributeError(key) from ex

    def __getitem__(self, key : str) -> Any:
        result = self.internal_get(key)
        pass
        return result

    def __iter__(self) -> NoReturn:
        raise AssertionError()

    def __len__(self) -> NoReturn:
        raise AssertionError()

    # ------------------------------------

    def find(self, key : str) -> Any:
        if key == "comp_db_path":
            pass

        cursor : (dict | Dict | ScopeBase) = self.expand_object
        result = ___
        try:
            self.trace_start(cursor, "get", key)

            # "local" and "enclosing" scope
            while cursor is not None:
                if key in cursor:
                    result = cursor[key]
                    return result
                elif '__up__' in cursor:
                    next = cursor["__up__"]
                    if next is None:
                        break
                    elif isinstance(next, (dict, Dict)):
                        self.trace_up(cursor, next, "get", key)
                        cursor = next
                    else:
                        raise TypeError(f"Dict has an __up__ chain that is not a dict - {next}")
                else:
                    break

            builtins = cv_builtins.get()

            # "global" scope
            if builtins.script is not None:
                self.trace_up(cursor, builtins.script, "get", key)
                cursor = builtins.script
                if key in builtins.script.__dict__:
                    result = builtins.script.__dict__[key]
                    return result

            # "builtins" scope
            self.trace_up(cursor, builtins, "get", key)
            cursor = builtins

            if key in builtins.__dict__:
                result = builtins.__dict__[key]
                return result

            if result is ___:
                raise KeyError(key)

        finally:
            self.trace_end(cursor, "get", key, result)

    # ------------------------------------

    def internal_get(self, key : str) -> Any:
        result = self.find(key)
        if isinstance(result, (dict, ScopeBase)):
            return Expander(result)
        elif isinstance(result, str):
            return self.expand(result)
        else:
            return result

    # ==============================================================================================
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

    # Note that we do _not_ suppress any BaseExceptions - they _must_ be propagated up to
    # callers. As of Python 3.11, this includes asyncio.CancelledError.

    # IMPORTANT IMPORTANT IMPORTANT
    # If you can't eval a macro, you return it unchanged.

    # TEFINAE : Template Expansion Failure Is Not An Error. Same idea as SFINAE in C++
    # - we don't fail on expansion failure so we can retry somewhere/somewhen else.

    def expand(self, text : str) -> Any:
        builtins = cv_builtins.get()

        log = builtins.log

        if not isinstance(text, str):
            raise TypeError(f"_expand - can't expand a {type(text)}")

        stats = builtins.stats

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
                blocks = Blocks()
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
                        self.trace_start(self.expand_object, "eval", block)
                        exp = block[1:-1]
                        var = eval(exp, {}, self)
                    except RecursionError:
                        raise
                    except Exception as _:
                        log.trace("\n")
                        log.trace(f"Eval failed because {_!r}\n")
                        log.trace("\n")
                        pass
                    finally:
                        self.trace_end(self.expand_object, "eval", block, var)

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
                        self.trace_start(self.expand_object, "expand", var)
                    for i, block in enumerate(blocks):
                        if isinstance(block, Macro):
                            blocks[i] = self.expand(block)
                    var = "".join(Utils.stringify(block) for block in blocks)

        finally:
            if trace_started:
                self.trace_end(self.expand_object, "expand", text, var)
            stats.expand_depth = old_depth
            if old_depth == 0:
                # We just finished an expansion - reset the eval budget
                stats.expand_evals = 0

        var = var.expand_object if isinstance(var, Expander) else var
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

    def trace_start(self, scope : (dict | Dict | ScopeBase), action : str, arg : Any) -> None: #pragma: no cover
        builtins = cv_builtins.get(None)
        if builtins is None or 'log' not in builtins.__dict__: return
        log = builtins.log
        if log._level_int > Log.TRACE: return

        scope_tag    = Utils.instance_tag(scope)
        scope_color  = Utils.obj_to_ansi_color(scope)
        action_color = Log.RESET
        indent_color = Log.RESET

        if action == "expand":
            arg = repr(arg)
            action_color = Utils.hex_to_ansi(0xFFAAAA)
            indent_color = Utils.hex_to_ansi(0xFFAAAA)
        elif action == "eval":
            arg = repr(arg)
            action_color = Utils.hex_to_ansi(0xAAFFAA)
            indent_color = Utils.hex_to_ansi(0xAAFFAA)
        elif action == "get":
            action_color = Utils.hex_to_ansi(0xAAAAFF)
            indent_color = Utils.hex_to_ansi(0xAAAAFF)
        elif action == "range":
            action_color = Utils.hex_to_ansi(0xFFFFFF)
            indent_color = Utils.hex_to_ansi(0xFFFFFF)

        if action == "range":
            log.trace(f"{Log.WHITE}┌── {scope_color}{scope_tag}{Log.RESET} begin {arg}\n")
            log.indent(Log.WHITE)
        else:
            #log.trace(f"{indent_color}V {scope_color}{scope_tag}{Log.RESET}.{action_color}{action}({arg}){Log.RESET}\n")
            log.trace(f"{indent_color}{scope_color}{scope_tag}{Log.RESET}.{action_color}{action}({arg}){Log.RESET}\n")
            log.indent(indent_color)

    def trace_up(self, scope : (dict | Dict | ScopeBase), up : (dict | Dict | ScopeBase), action : str, arg : str) -> None: #pragma: no cover
        builtins = cv_builtins.get(None)
        if builtins is None or 'log' not in builtins.__dict__: return
        log = builtins.log
        if log._level_int > Log.TRACE: return

        up_tag    = Utils.instance_tag(up)
        up_color  = Utils.obj_to_ansi_color(up)
        action_color = Utils.hex_to_ansi(0xAAAAFF)

        log.trace(f"{up_color}^{up_tag}.{action_color}{action}({arg}){Log.RESET}\n")

    def trace_end(self, scope : (dict | Dict | ScopeBase), action : str, arg : Any, result : Any) -> None: #pragma: no cover
        builtins = cv_builtins.get(None)
        if builtins is None or 'log' not in builtins.__dict__: return
        log = builtins.log
        if log._level_int > Log.TRACE: return

        scope_tag    = Utils.instance_tag(scope)
        scope_color  = Utils.obj_to_ansi_color(scope)
        action_color = Log.RESET
        indent_color = log._indent_stack[-1]

        log.dedent()

        if action == "expand":
            arg = repr(arg)
            action_color = Utils.hex_to_ansi(0xFFAAAA)
        elif action == "eval":
            arg = repr(arg)
            action_color = Utils.hex_to_ansi(0xAAFFAA)
        elif action == "get":
            action_color = Utils.hex_to_ansi(0xAAAAFF)
        elif action == "range":
            action_color = Utils.hex_to_ansi(0xFFFFFF)

        if isinstance(result, Expander):
            result = result.expand_object

        if action == "range":
            log.trace(f"{Log.WHITE}└── {scope_color}{scope_tag}{Log.RESET} end {arg}\n")
        else:
            result_color = Utils.obj_to_ansi_color(result)
            #result_type  = type(result)

            if Utils.is_mapping(result):
                result_color = Utils.obj_to_ansi_color(result)

            #log.trace(f"{indent_color}└─>{scope_color}{scope_tag}{Log.RESET}.{action_color}{action}({arg}) = {Log.RESET}")

            if not Utils.is_scalar(result):
                log.trace(f"{indent_color}└─> {scope_color}{scope_tag}{Log.RESET}.{action_color}{action}({arg}){Log.RESET} = {result_color}{Utils.instance_tag(result)}\n")
            else:
                log.trace(f"{indent_color}└─> {scope_color}{scope_tag}{Log.RESET}.{action_color}{action}({arg}){Log.RESET} = {result_color}{result!r}\n")

# endregion
# ==================================================================================================
# region Dumper

class Dumper:  #pragma: no cover
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
        dump_private : bool = False
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
        print_id : bool = True,
        color_code : bool = False,
        dump_private : bool = False,
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
    def _dump_to_str(cls, key : key_type, val : Any, opts : Opts)-> str:
        if opts.cur_depth > 0 and (key in opts.fold or val in opts.fold or type(val) in opts.fold):
            return cls._dump_prefix(key, val, opts) + "<folded>"
        elif isinstance(val, ScopeBase):
            return cls._dump_scope(key, val, opts)
        elif isinstance(val, opaque_types):
            return cls._dump_scalar(key, val, opts)
        elif key == "__up__": # ugh hardcoding. do we ever hit this?
            #breakpoint()
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
        elif isinstance(val, str):
            return cls._dump_scalar(key, val, opts)
        #elif hasattr(val, "__dict__"):
        #    return cls._dump_vector(key, val, val.__dict__, opts)
        else:
            return cls._dump_scalar(key, val, opts)

    @classmethod
    def _dump_scalar(cls, key : key_type, val : Any, opts : Opts) -> str:
        if val is ___:
            return f"{key} = ___"

        seen: set[Any] = Dumper.seen.get()
        if id(val) in seen:
            return cls._dump_prefix(key, val, opts) + "<deduped>"
        elif not isinstance(val, base_types):
            seen.add(id(val))

        if isinstance(val, base_types):
            return cls._dump_prefix(key, val, opts) + repr(val)
        else:
            val_cls  = type(val)
            text = repr(val)
            text = text.replace(val_cls.__module__ + ".", "", 1)
            return cls._dump_prefix(key, val, opts) + text

    @classmethod
    def _dump_vector(cls, key : key_type, val : Any, contents : Any, opts : Opts) -> str:
        prefix : str = cls._dump_prefix(key, val, opts)

        if opts.cur_depth == opts.max_depth:
            return prefix + "<too deep>"

        seen : set[Any] = Dumper.seen.get()
        if id(val) in seen:
            return prefix + "<deduped>"
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

        return cls._dump_items(key, prefix, ld, items, rd, opts)

    @classmethod
    def _dump_scope(cls, key : key_type, val : ScopeBase, opts : Opts) -> str:
        return cls._dump_vector(key, val, val.__dict__, opts)

    @classmethod
    def _dump_items(cls, key : key_type, prefix : str, ld : str, items : list[item[Any]], rd : str, opts : Opts) -> str:
        # This slightly odd construct is so that when a deeply nested container doesn't fit on a
        # line, we rewind the callstack back to the topmost container that was not forced to be
        # flat.

        if opts.cur_depth == opts.max_depth:
            return prefix + "<too deep>"

        if key in opts.fold:
            raise AssertionError()

        if opts.flat:
            return prefix + cls._dump_items_flat(ld, items, rd, opts)
        else:
            # If the dump_items_flat fails, we _must_ roll back the seen set to its
            # old value.
            old_contents :list[Any]= list(Dumper.seen.get())
            try:
                return prefix + cls._dump_items_flat(ld, items, rd, replace(opts, flat = True))
            except Dumper.LineTooLong:
                seen:set[Any] = Dumper.seen.get()
                seen.clear()
                seen.update(old_contents)
                rhs:str = cls._dump_items_deep(ld, items, rd, opts)
                return prefix + rhs

    @classmethod
    def _dump_items_flat(cls, ld : str, items : list[item[Any]], rd : str, opts : Opts) -> str:
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
    def _dump_items_deep(cls, ld : str, items : list[item[Any]], rd : str, opts : Opts) -> str:
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
    def _dump_prefix(cls, key : key_type, val : Any, opts : Opts) -> str:
        prefix: str = ""
        if key:
            prefix += f"{key}"
        elif isinstance(val, ScopeBase):
            prefix += type(val).__name__.lower()
        elif hasattr(val, 'name') and val.name is not None:
            prefix += val.name
        elif isinstance(val, (ABCMeta, abc.Callable, ModuleType)):
            if '__name__' in val.__dict__:
                prefix += val.__name__
            else:
                prefix = type(val).__name__

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
    @staticmethod
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
        #elif any(v is f for f in folded) or any(type(v) is f for f in folded):
        elif v in folded or type(v) in folded:
            print(fold + str(k))
        else:
            print(open + str(k))
            for i, (k2, v2) in enumerate(v.items()):
                tab1, tab2 = (hook, gap) if i == len(v) - 1 else (tee, pipe)
                print(lhs + tab1, end="")
                Dumper.print_tree(k2, v2, lhs + tab2)


# endregion
# ==================================================================================================
# region Runner

class Runner(ScopeBase):

    def __init__(self, *args : dict[str, Any], **kwargs : Any) -> None:
        super().__init__()
        self.name : str = "runner"
        self.max_jobs : int   = "{build.max_jobs}"   #type:ignore
        self.max_errors : int = "{build.max_errors}" #type:ignore
        Utils.merge_all(self.__dict__, *args, **kwargs)

    def __expand__(self) -> None:
        super().__expand__()
        self._core_sem  : asyncio.Semaphore = asyncio.Semaphore(self.max_jobs)
        self._core_lock : asyncio.Lock = asyncio.Lock()
        self._aio_done_queue : asyncio.Queue = asyncio.Queue()
        self._live_aio_tasks : set[asyncio.Task] = set()

    async def acquire(self, count : int) -> int:
        # A task that requires a lot of cores can block tasks behind it in the queue. This is
        # intended behavior.

        if count > self.max_jobs:
            raise ValueError(f"Tried to acquire {count} cores, which exceeds the max {self.max_jobs}")
        async with self._core_lock:
            acquired = 0
            try:
                while acquired < count:
                    await self._core_sem.acquire()
                    acquired += 1
                return count
            except BaseException: #pragma: no cover
                self.release(acquired)
                raise

    def release(self, count : int) -> None:
        for _ in range(count):
            self._core_sem.release()

    def run_tasks(self) -> int:
        result = asyncio.run(self._async_run_tasks())
        return result

    async def _async_run_tasks(self) -> int:
        """Run all tasks until we run out."""
        builtins = cv_builtins.get()
        log     = builtins.log
        project = builtins.project
        stats   = builtins.stats

        # ------------------------------------
        # Create asyncio tasks for all enabled Hancho tasks.

        for repo in project._repos:
            for task in repo.yield_tasks():
                if task._enabled:
                    task.create_aio_task()

        # ------------------------------------
        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.

        log.verbose("Running tasks...\n")

        while self._live_aio_tasks and (stats.tasks_broken + stats.tasks_failed) <= self.max_errors:
            finished_aio_task:asyncio.Task = await self._aio_done_queue.get()
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
                self._live_aio_tasks.discard(finished_aio_task)

        failures: int = stats.tasks_broken + stats.tasks_failed
        if failures > self.max_errors:
            log.error(f"Too many failures after {failures}, cancelling tasks and stopping build\n")

            # Cancel all the asyncio.Tasks that haven't completed yet
            log.warning(f"Cancelling {len(self._live_aio_tasks)} tasks\n")

            # This tasks_cancelled count may be off by one or two due to in-flight tasks not being
            # accounted for in live_aio_tasks, but it doesn't matter - we're about to bail out due
            # to failures or someone ctrl-c'ing the build, this is purely cosmetic.

            stats.tasks_cancelled += len(self._live_aio_tasks)
            for t in self._live_aio_tasks:
                t.cancel()

            # and then wait on their cancellations to complete (it isn't instantaneous)
            await asyncio.gather(*self._live_aio_tasks, return_exceptions=True)

        return 1 if stats.tasks_failed or stats.tasks_broken else 0

# endregion
# ==================================================================================================
# region Project

class Project(ScopeBase):
    def __init__(self, *args : dict[str, Any], file : str, **kwargs : Any) -> None:
        super().__init__()
        self.name : str          = "project@" + Path.basename(file)
        self.file : str          = file
        self.path : Realpath     = "{realpath(file)}"  # type: ignore
        self.root : Realpath     = ___
        self.targets : list[str] = ___

        self._all_outputs : set[Goodpath] = set()
        self._dedupe = {}
        self._repos : list[Repo] = []

        Utils.merge_all(self.__dict__, *args, **kwargs)

# endregion
# ==================================================================================================
# region Build

class Build(ScopeBase):
    def __init__(self, *args : dict[str, Any], **kwargs : Any) -> None:
        super().__init__()

        self.name : str         = "build"
        self.dir : str          = "build"
        self.dest : str         = "{goodpath(join(repo.root, dir))}"
        self.tag : str          = ""
        self.force : bool       = False
        self.all_targets : bool = False
        self.dry_run : bool     = False
        self.strict : bool      = True
        self.max_jobs : int     = os.cpu_count() or 1
        self.max_errors : int   = 0

        Utils.merge_all(self.__dict__, *args, **kwargs)

# endregion
# ==================================================================================================
# region Repo

class Repo(ScopeBase):

    def __init__(self, file : str, *args : dict[str, Any], **kwargs : Any) -> None:
        super().__init__()
        self.name : str      = "repo@" + Path.basename(file)
        self.file : str      = file
        self.path : Realpath = "{realpath(file)}"         #type:ignore
        self.root : Realpath = "{dirname(file)}"          #type:ignore
        self.dest : Goodpath = "{join(root, build.dir)}"  #type:ignore

        self.stat_db_path : Goodpath = "{goodpath(join(dest, 'hancho.json'))}" #type:ignore
        self.comp_db_path : Goodpath = "{goodpath(join(dest, 'compile_commands.json'))}" #type:ignore
        Utils.merge_all(self.__dict__, *args, **kwargs)

        self._scripts : list[Script] = []
        self._stat_db : dict[str, dict] = {}

    def load_stat_db(self) -> None:
        builtins = cv_builtins.get()

        if Path.isfile(self.stat_db_path):
            log = builtins.log
            log.verbose(f"Loading stat_db {self.stat_db_path}\n")
            json_stats = Utils.load_json(self.stat_db_path)
            for file, json_stat in json_stats.items():
                self._stat_db[file] = json_stat
        else:
            log = builtins.log
            log.verbose(Log.DRED + f"Stat db not found: {self.stat_db_path}\n")

    # FIXME this should go to Utils
    @classmethod
    def load_depfile(cls, filename : Realpath, format : str, task_cwd : Realpath) -> list[Realpath]:
        # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
        # NOTE: This does not handle filenames with escaped spaces in them, but I don't want to
        # write a whole .d parser yet.

        with open(str(filename), encoding="utf-8") as depcontents:
            if format == "msvc":
                raw_deplines = json.load(depcontents)["Data"]["Includes"] #pragma: no cover
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
        builtins = cv_builtins.get()
        log = builtins.log
        log.verbose(Log.DGREEN + f"Saving stat_db {self.stat_db_path}\n")
        Utils.save_json(self._stat_db, self.stat_db_path)

    def save_comp_db(self) -> None:
        pass
        ## Haven't tested this in an IDE, but I think it matches the spec.
        #script = cvg.get()
        #builtins.log.verbose(f"Saving comp_db {comp_db_path}\n")
        #comp_db = []
        #for file, stat in self._stat_db.items():
        #    comp_db.append({
        #        "directory" : stat.cwd,
        #        "command"   : Dumper.depointer(str(command)),
        #        "file"      : Path.relpath(file, stat.cwd),
        #    })
        #Utils.save_json(comp_db, comp_db_path)

    def yield_tasks(self) -> abc.Iterator[Task]:
        for script in self._scripts:
            yield from script._tasks

    # FIXME move to Utils
    @classmethod
    def hash_file(cls, abs_path : Realpath) -> str:
        builtins = cv_builtins.get()
        stats = builtins.stats
        stats.hash_calls += 1

        time_a = time.perf_counter()
        with open(str(abs_path), "rb") as f:
            stats.hash_bytes += os.fstat(f.fileno()).st_size
            digest = hashlib.file_digest(f, lambda: hashlib.blake2b(digest_size=16)).hexdigest()
        time_b = time.perf_counter()

        stats.hash_time += time_b - time_a
        return digest

    # FIXME move to Utils
    def get_stat(self, file : Realpath) -> dict:
        builtins = cv_builtins.get()
        stats = builtins.stats
        stats.stat_calls += 1

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
            new_stat['command'] = Dumper.depointer(str(command))
        if file in self._stat_db:
            Utils.merge(self._stat_db[file], new_stat)
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
        new_command : str = Dumper.depointer(str(command))
        return old_command != new_command

# endregion
# ==================================================================================================
# region Task

# FIXME get() should call eval or something? a lot of these task fields should delegate to script

class Task(ScopeBase):

    class FAILED(Exception):    pass
    class CANCELLED(Exception): pass
    class BROKEN(Exception):    pass

    def __init__(self, *args : Any, **kwargs : Any) -> None:
        super().__init__()
        self.command: command_type  = []
        self.name: str              = "task"
        self.desc: str              = "<no desc>"
        self.command: command_type  = ""
        self.cwd: Realpath          = "{script.root}"  # type:ignore
        self.dest: Goodpath         = "{script.dest}"  # type:ignore
        self.depformat: str         = "gcc" if os.name == "posix" else "msvc"
        self.dry_run: str           = "{build.dry_run}"
        self.in_depfile: str | None = None
        self.job_size: int          = 1
        self.force: bool            = "{build.force}"    # type:ignore
        self.strict: bool           = "{build.strict}"   # type:ignore

        for m in [*args, kwargs]:
            if not isinstance(m, dict):
                m = m.__dict__
            Utils.merge(self.__dict__, m)

        # Expand all input fields. These _must_ be expandable into filenames or tasks right now, as
        # we need to queue up input tasks before this task starts.

        # We can't expand the rest yet as commands or paths may depend on the paths of the output
        # files from the input tasks, which we don't know until the input tasks are complete.

        self._public_fields = [x for x in self.__dict__ if not x.startswith("_")]

        ex = Expander(self)
        self.cwd = Realpath(ex.expand(self.cwd))

        for k, v in self.__dict__.items():
            if k.startswith("in_") and isinstance(v, str):
                self.__dict__[k] = ex.expand(v)

        # ----------------------------------------
        # Private fields

        # Add all input fields that are tasks to _in_tasks so we can await them later.
        in_tasks = []
        for v in self.__dict__.values():
            for x in Utils.yield_leaves(v):
                if isinstance(x, Task):
                    in_tasks.append(x)
        self._in_tasks = in_tasks

        # These two have to be dicts so that downstream tasks can pick named outputs from it if
        # needed.
        self._in_files :  dict[str, list[Goodpath]] = {}
        self._out_files : dict[str, list[Goodpath]] = {}

        self._enabled = False
        self._complete = False
        self._skipped = False
        self._task_id : int = -1
        self._cores = 0
        self._error : BaseException | None = None # The "return value" for the task as a whole, or "None" if the task was successful.

        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._aio_task : asyncio.Task | None = None

        # We remember the aio context we were in when this task was created so that we can return
        # to it when the task starts.
        self._aio_context = contextvars.copy_context()

        self._stdout_text : str = ""
        self._stderr_text : str = ""
        self._deplines : list[Realpath] = []

        # ----------------------------------------

        script = cv_builtins.get().script
        script._tasks.append(self)

        # Auto-start the task if it was created dynamically during the build.
        if Utils.in_event_loop():
            self.queue_task()

    # Silence unknown attribute accesses on Task
    def __getattr__(self, name: str) -> Any:
        # __getattr__ is there, it's just hidden for type checking
        return super().__getattr__(name) # type: ignore

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)

    def __copy__(self) -> NoReturn:
        # Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
        raise AssertionError()

    def __deepcopy__(self, _ : dict[int, Any]) -> NoReturn:
        raise AssertionError()

    def create_aio_task(self) -> None:
        builtins = cv_builtins.get()
        runner = builtins.runner
        assert Utils.in_event_loop()

        if self._aio_task is None:
            t = asyncio.create_task(self.task_top(), context=self._aio_context)
            setattr(t, "hancho_task", self)  # noqa: B010
            runner._live_aio_tasks.add(t)
            t.add_done_callback(lambda t: runner._aio_done_queue.put_nowait(t))
            self._aio_task = t

    def queue_task(self) -> None:
        builtins = cv_builtins.get()
        stats = builtins.stats
        if self._enabled:
            return

        self._enabled = True
        stats.tasks_enabled += 1

        # If this task was dynamically created during the build, add it to asyncio immediately.
        if Utils.in_event_loop():
            self.create_aio_task()

        # Start all tasks referenced by the config so we don't deadlock while waiting for them.
        # FIXME we should pull all the upstream tasks out earlier or something
        for input_task in self._in_tasks:
            input_task.queue_task()

    async def task_top(self) -> dict | None:
        # Entry point for tasks, just so we can keep all the task-level exception handling together.

        builtins = cv_builtins.get()
        runner = builtins.runner

        try:
            await self.task_main()
        except (Exception, asyncio.CancelledError) as ex:
            self._error = ex
        finally:
            runner.release(self._cores)

        if self._error:
            self.log_task(Log.ERROR, traceback.format_exception(self._error))
            if (self._stdout_text or self._stderr_text):
                self.log_task(Log.ERROR, self.dump_stdout())

        return self._out_files

    async def task_main(self) -> None:
        builtins = cv_builtins.get()
        stats = builtins.stats
        runner = builtins.runner

        # ------------------------------------
        # Await all tasks in our input fields and then flatten them.

        await self.await_inputs()

        # ------------------------------------
        # We're ready to run

        stats.tasks_started += 1
        self._task_id = stats.tasks_started

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

        if self.dry_run:
            return

        # ------------------------------------
        # Paths updated. See if we need to rebuild our outputs.

        if self.in_depfile and Path.isfile(self.in_depfile):
            real_depfile = Realpath(self.in_depfile)
            self._deplines = Repo.load_depfile(real_depfile, self.depformat, self.cwd)

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

        self._cores = await runner.acquire(self.job_size)

        # ------------------------------------
        # Print what we're about to run

        self.log_task(Log.INFO, Log.YELLOW + f"Task {text}: {Log.DBLUE + reason}\n")

        # ------------------------------------
        # Run all the commands

        curlify = str.maketrans({"«" : "{", "»" : "}"})

        with timeit("Task"):
            for c in Utils.leaves(self.command):
                if isinstance(c, str):
                    c = c.translate(curlify)
                    await self.run_command(c)
                elif isinstance(c, abc.Callable):
                    await self.call_callback(c)
                else:
                    raise TypeError(type(c))

        # ------------------------------------
        # See if the task wrote all its output files

        for file in Utils.leaves(self._out_files):
            if not os.path.exists(file):
                raise Task.FAILED(f"Task ran, but output file still missing: {file}")

        # And we're done

    def update_repo_stat_db(self) -> None:
        builtins = cv_builtins.get()
        repo = builtins.repo

        with chdir(self.cwd):
            # Reload the depfile post-build so we pick up new deps
            if self.in_depfile and Path.isfile(self.in_depfile):
                self._deplines = Repo.load_depfile(Realpath(self.in_depfile), self.depformat, self.cwd)

            for file in Utils.leaves([self._in_files, self._deplines]):
                if Path.exists(file):
                    repo.update_stat(file, None)

            for file in Utils.leaves(self._out_files):
                if Path.exists(file):
                    repo.update_stat(file, self.command)

    async def await_inputs(self) -> None:
        # NOTE: Hancho _cannot_ have dependency cycles unless you do something really sketchy via
        # modifying tasks after they're created but before they're started. If you point task B's
        # inputs at task A and task A's inputs at task B and it blows up, that's on you.

        # Await all input tasks and check if any of them had errors.
        for field_name in self._public_fields:
            for leaf in Utils.yield_leaves(self.__dict__[field_name]):
                if isinstance(leaf, Task):
                    if leaf._aio_task is None:
                        raise AssertionError("One of a task's input sub-tasks was not started") #pragma: no cover
                    await leaf._aio_task
                    if leaf._error is not None:
                        raise Task.CANCELLED(f"Task {hex(id(self))} is cancelled due to {leaf._error}")

        # Replace all tasks in the dict with their _out_files.
        Utils.map_in_place(
            lambda k, v : v._out_files if isinstance(v, Task) else v,
            ___,
            self.__dict__
        )


    def expand_task(self) -> None:
        ex = Expander(self)

        self.log_task(Log.DEBUG, "Task before expand:\n")
        self.log_task(Log.DEBUG, Dumper.dump(self, fold=[Task, asyncio.Task, contextvars.Context], dump_private = True) + "\n")

        # We eval these first because we need them for filename expansion

        script_root = Realpath(ex.expand("{script.root}"))
        script_dest = Goodpath(ex.expand("{script.dest}"))

        # Then we expand all io fields and fix their paths.
        try:
            ex.trace_start(ex.expand_object, "range", "expand io fields")
            io_fields = [k for k in self.__dict__ if k.startswith("in_") or k.startswith("out_")]

            for field_name in io_fields:
                files = getattr(self, field_name)
                if isinstance(files, Task):
                    files = files._out_files
                files = [ex.expand(file) for file in Utils.leaves(files)]
                goodfiles = self.fix_paths(field_name, files, script_root, script_dest)
                setattr(self, field_name, goodfiles[0] if len(goodfiles) == 1 else goodfiles)

                if field_name == "in_depfile":
                    if len(goodfiles) > 1:
                        raise Task.BROKEN("in_depfile can't be a list")
                elif field_name.startswith("in_"):
                    self._in_files[field_name]  = goodfiles
                elif field_name.startswith("out_"):
                    self._out_files[field_name] = goodfiles
        finally:
            ex.trace_end(ex.expand_object, "range", "expand io fields", None)

        # IO fields all expanded, we can expand the rest of the task now.

        # We must NOT expand everything in the task - it could contain references to things like
        # shared arrays of flags that contain templates, and if we expand it using _this_ task as
        # a context, all other tasks using the shared data will get incorrectly-expanded values

        self.command = [(ex.expand(c) if isinstance(c, str) else c) for c in Utils.leaves(self.command)]

        if len(self.command) == 1:
            self.command = self.command[0]

        required_keys = ['command', 'cwd', 'depformat', 'desc', 'dest', 'dry_run', 'force', 'job_size', 'name']

        for key in required_keys:
            val = self.__dict__[key]
            if isinstance(val, str):
                self.__dict__[key] = ex.expand(val)

        if not self.dry_run:
            for file in Utils.leaves(self._out_files):
                os.makedirs(os.path.dirname(file), exist_ok=True)
            if self.in_depfile:
                os.makedirs(os.path.dirname(self.in_depfile), exist_ok=True)

        self.log_task(Log.DEBUG, "Task after expand:\n")
        self.log_task(Log.DEBUG, Dumper.dump(self, fold=[*default_folds, Task, asyncio.Task, contextvars.Context], dump_private = True) + "\n")

    def fix_paths(self, field : str, raw_files : list[str], script_root : Realpath, script_dest : Goodpath) -> list[Goodpath]:
        return [self.fix_path(field, raw_file, script_root, script_dest) for raw_file in raw_files]

    def fix_path(self, field : str, raw_file : str, script_root : Realpath, script_dest : Goodpath) -> Goodpath:
        # File paths _must_ be abs'd after joining, otherwise they might look like they're under
        # script_dir, but they're not because the paths could have "../../../../.." in them.
        goodfile = Goodpath(os.path.join(script_root, raw_file))

        # Move all outputs under build.root and ensure their directories exist.
        # Note - This will also move "in_depfile" under build.root - this is _intentional_ as
        # it's an _output_ from the compiler and is not checked in to the source tree.

        if (field.startswith("out_") or field == "in_depfile") and not Path.startswith(goodfile, script_dest):
            chunk = Path.relpath(goodfile, script_root)
            file = os.path.join(script_dest, chunk)
            goodfile = Goodpath(file)

        return goodfile

    def sanity_check(self) -> None:
        builtins = cv_builtins.get()
        repo = builtins.repo
        project = builtins.project

        # Check for all task issues that break the build

        if not Path.startswith(self.dest, repo.root):
            raise Task.BROKEN(f"The build destination {self.dest} is not under the repo root {repo.root}")

        has_str      = any(isinstance(x, str)                     for x in Utils.leaves(self.command))
        has_callable = any(isinstance(x, abc.Callable)            for x in Utils.leaves(self.command))
        has_other    = any(not isinstance(x, (str, abc.Callable)) for x in Utils.leaves(self.command))

        if has_str and has_callable:
            raise Task.BROKEN(f"Command list is a mix of strings and callables: {self.command}")
        if has_other:
            raise Task.BROKEN(f"Command contains a non-string/callable: {self.command}")

        # In strict mode, we mark a task broken if its command still has delimiters in it.
        if self.strict:
            for command in Utils.leaves(self.command):
                if isinstance(command, str) and Utils.is_expandable(command):
                    raise Task.BROKEN(f"STRICT: Command not fully expanded: {command!r}")

        # Check that all build files would end up under dest
        for files in self._out_files.values():
            for file in files:
                if not Path.startswith(file, self.dest):
                    raise Task.BROKEN(f"Path error, output file {file} is not under task destination {self.dest}")

        # Check for task collisions
        for files in self._out_files.values():
            for file in files:
                if file in project._all_outputs:
                    raise Task.BROKEN(f"TaskCollision: Multiple tasks build {file}")
                project._all_outputs.add(file)

        # Check for missing inputs. We have to check dry_run, as the input files may only exist if
        # we're really running tasks.
        if not self.dry_run:
            for files in self._in_files.values():
                for file in files:
                    if not Path.exists(file):
                        raise Task.BROKEN(f"Input file missing - {file}")

    def rebuild_reason(self) -> str:
        """Figures out why we have to run a Task, or returns "" if we don't."""
        builtins = cv_builtins.get()
        repo = builtins.repo

        # ------------------------------------
        # Check the trivial reasons to rebuild

        if self.force:
            return "Task forced to rebuild"

        if not any(Utils.leaves(self._in_files)):
            return "Always rebuild a task with no inputs"

        if not any(Utils.leaves(self._out_files)):
            return "Always rebuild a task with no outputs"

        # ------------------------------------
        # Check for file modifications

        all_files : tree[str] = [self._in_files, self._out_files, self._deplines]

        for file in Utils.leaves(all_files):
            if not Path.isfile(file):
                return f"File missing: {file!r}"
            file = Realpath(file)
            if reason := repo.stat_changed2(file):
                return reason

        for file in Utils.leaves(self._out_files):
            file = Realpath(file)
            if repo.command_changed(file, self.command):
                return f"Command mismatch for {file!r}"

        # No changes, no reason to rebuild.
        return ""

    async def run_command(self : Task, command : str) -> None:
        builtins = cv_builtins.get()
        repo = builtins.repo

        # Print the cwd and command about to run
        # some/folder$ g++ blah blah blah
        self.log_task(Log.INFO, Log.GRAY3 + f"{Path.relpath(self.cwd, repo.root)}$ {command}\n")

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

        except asyncio.CancelledError as ex: #pragma: no cover
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
        except Exception as ex: #pragma: no cover
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
        builtins = cv_builtins.get()
        script   = builtins.script
        repo     = builtins.repo

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

        builtins = cv_builtins.get()
        log      = builtins.log
        stats    = builtins.stats
        color    = Log.YELLOW if level > Log.INFO else ""

        all_lines = []
        for chunk in Utils.leaves(message):
            all_lines.extend(chunk.splitlines(keepends = True))

        for line in all_lines:
            if not log._line_buffer:
                log._log(level, "", f"[{self._task_id:3d}/{stats.tasks_enabled:3d}] ")
            log._log(level, color, line)

# endregion
# ==================================================================================================
# region Script

# This is not a types.ModuleType, but it still works fine as one. Making it a ModuleType is a bit
# annoying to work with.

# If a function in script A calls a function in script B, the function in script B always sees B's
# globals.

# That's not what we want - we always want it to see "the script currently being loaded".
# And we want that to be true _after_ the script is loaded as well - expanding fields in a task
# should always see the script that was being loaded when that task was created.
# So, we put the script in a contextvar and have the task capture it, then when the tasks's
# asyncio.task runs it sets the contextvar back to that script.

class Script(ScopeBase):

    def __init__(self : Script, *args : dict[str, Any], file : str, **kwargs : Any) -> None:
        super().__init__()

        self.name : str      = "script@" + Path.basename(file)
        self.file : str      = file
        self.path : Realpath = "{realpath(file)}" #type:ignore
        self.root : Realpath = "{realpath(dirname(path))}" #type:ignore
        self.dest : Goodpath = "{join(repo.dest, relpath(script.root, repo.root))}" #type:ignore

        Utils.merge_all(self.__dict__, *args, **kwargs)

#        self._builtins = {}
#        self._locals   = {}
#        self._globals  = {}

        self._tasks = []

#    def __getattr__(self, key : str) -> Any:
#        if key in self._locals:
#            return self._locals[key]
#        if key in self:
#            return self[key]
#        if key in self._globals:
#            return self[key]
#        if key in self._builtins:
#            return self._builtins[key]
#        raise AttributeError(key)

    def exec(self, builtins : Builtins) -> None:

        with (
            cv_builtins.set(builtins),
            builtins.log.indenter(Log.VERBOSE, Log.ORANGE),
            chdir(self.root),
            open(str(self.path), encoding="utf-8") as file,
        ):
            self.__dict__['__builtins__'] = builtins.__dict__

            source: str = file.read()
            code: CodeType = compile(source, str(self.path), "exec", dont_inherit=True)
            exec(code, self.__dict__)
#            #temp_locals = {}
#
#            exec(
#                code,
#                #self.__dict__,
#                #globals = {"iamthewalrus":self.file, "__builtins__":builtins.__dict__},
#                #locals = self.__dict__,
#
#                globals = self.__dict__,
#                locals  = self._locals, # type: ignore
#            )
#
#            #exec(
#            #    code,
#            #    globals = {"__builtins__":builtins.__dict__},
#            #    #globals = self.__dict__,
#            #    locals  = self.__dict__,
#            #)

            del self.__dict__['__builtins__']

    def dump(self, *args : Any, **kwargs : Any) -> None:
        dprint(Dumper.dump(*args, **kwargs))

# endregion
# ==================================================================================================
# region builtins

cv_builtins : contextvars.ContextVar[Builtins] = contextvars.ContextVar("builtins")

class Builtins(Dict):
    # FIXME turn this back into a class?
    def __init__(self, *args : dict[str, Any], **kwargs : Any) -> None:
        # Copy all the existing builtins over
        import builtins as _builtins
        for key, val in _builtins.__dict__.items():
            self[key] = val

        self.name     = "builtins"
        self.hancho   = sys.modules[__name__]
        self.___      = ___
        self.log      = ___
        self.runner   = ___
        self.project  = ___
        self.build    = ___
        self.repo     = ___
        self.script   = ___
        self.stats    = ___

        self.Dict     = Dict
        self.Task     = Task
        self.Tool     = Tool
        self.Dumper   = Dumper
        self.Expander = Expander

        self.getcwd   = os.getcwd
        self.dprint   = dprint
        self.dump     = Dumper.dprint
        self.Goodpath = Goodpath
        self.Realpath = Realpath

        self.run_cmd  = Utils.run_cmd
        self.weave    = Utils.weave
        self.leaves   = Utils.leaves

        # FIXME probably want tree versions of all of these?

        self.join     = Path.join
        self.split    = Path.split
        self.swapext  = Path.swapext
        self.topdir   = Path.topdir
        self.relpath  = Path.relpath
        self.stem     = Path.stem
        #self.basename = Utils.tree_map(Path.basename)
        self.basename = Path.basename

        self.dirname  = Path.dirname
        self.resolve  = lambda x : Realpath(x)
        self.abspath  = lambda x : Goodpath(x)
        self.realpath = lambda x : Realpath(x)
        self.goodpath = lambda x : Goodpath(x)

        # These have to be directly in the builtins object, as client code only sees
        # builtins.__dict__
        self.load_script = Builtins.load_script
        self.load_repo   = Builtins.load_repo
        self.start_build = Builtins.start_build

        Utils.merge_all(self.__dict__, *args, **kwargs)


    @staticmethod
    def _load(file : str, is_repo : bool, *args : Any, **kwargs : Any) -> Script:
        old_builtins = cv_builtins.get()

        file   = Realpath(Expander(old_builtins.script).expand(file))
        repo   = Repo(file) if is_repo else old_builtins.repo
        script = Script(*args, file = file, **kwargs)

        new_builtins = Builtins(old_builtins.__dict__, repo = repo, script = script)

        # Expand repo/script - should be done under new_builtins.
        with cv_builtins.set(new_builtins):
            script.__dict__['__builtins__'] = new_builtins.__dict__
            if is_repo:
                repo.__expand__()
                repo.load_stat_db()
            script.__expand__()
            del script.__dict__['__builtins__']

        # Check for duplicates
        public_stuff = {k:v for k, v in script.__dict__.items() if not k.startswith("_")}
        dedupe_key = Dumper.any_to_key(public_stuff)
        if dupe := new_builtins.project._dedupe.get(dedupe_key, None):
            new_builtins.log.verbose(Log.LIME + f"Deduped {script.path}\n")
            return dupe

        # File was _not_ deduped.
        new_builtins.project._dedupe[dedupe_key] = script
        repo._scripts.append(script)
        if is_repo:
            new_builtins.project._repos.append(repo)

        old_builtins.log.verbose(Log.ORANGE + f"┌─── Running script {script.path}\n")
        script.exec(new_builtins)

        return new_builtins.script

    @staticmethod
    def load_script(file : str, *args : Any, **kwargs : Any) -> Script:
        # FIXME handle args/kwargs
        return Builtins._load(file, False, *args, **kwargs)

    @staticmethod
    def load_repo(file : str, *args : Any, **kwargs : Any) -> Script:
        # FIXME handle args/kwargs
        return Builtins._load(file, True, *args, **kwargs)

    @staticmethod
    def start_build() -> int:
        builtins = cv_builtins.get()
        project = builtins.project

        # ------------------------------------
        # Select the set of tasks to run.

        if project.targets:
            for target in project.targets:
                # Enable all tasks whose name contains any of the targets
                for repo in project._repos:
                    for task in repo.yield_tasks():
                        if task.name is not None and target in task.name:
                            task.queue_task()

        elif builtins.build.all_targets:
            for repo in project._repos:
                for task in repo.yield_tasks():
                    task.queue_task()

        else:
            # Enable all tasks in the top repo
            for task in project._repos[0].yield_tasks():
                task.queue_task()

        # ------------------------------------
        # Run the tasks.

        result: int = builtins.runner.run_tasks()

        # ------------------------------------
        # Update stat DBs.

        if not builtins.build.dry_run:
            for repo in builtins.project._repos:
                for task in repo.yield_tasks():
                    if task._complete:
                        task.update_repo_stat_db()
                repo.save_stat_db()

        return result

# endregion
# ==================================================================================================
# region _start/main

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

# ----------------------------------------

def main() -> int:
    builtins = init(sys.argv, sys.stdout)

    builtins.log.verbose(Log.ORANGE + f"┌─── Running top script {builtins.script.path}\n")
    builtins.script.exec(builtins)

    # ------------------------------------
    # Build everything

    with timeit("Build"):
        result = builtins.start_build()

    # ------------------------------------
    # Print build stats

    builtins.stats.tasks_created = 0
    for repo in builtins.project._repos:
        builtins.stats.tasks_created += len(list(repo.yield_tasks()))

    builtins.log._log(
        Log.VERBOSE,
        Log.DBLUE,
        "Stats: " + Dumper.dump(builtins.stats.__dict__, print_id=False) + "\n",
    )

    # ------------------------------------
    # Print pass/fail

    if builtins.stats.tasks_failed or builtins.stats.tasks_broken:
        builtins.log.error("BUILD FAILED\n")
    elif builtins.stats.tasks_finished:
        builtins.log.info(Log.GREEN + "BUILD PASSED\n")
    else:
        builtins.log.info(Log.BLUE + "BUILD CLEAN\n")

    # ------------------------------------
    # Done

    builtins.log._flush()

    return result

# ----------------------------------------

def init(argv : list[str], stdout : TextIO) -> Dict:
    flags = Flags()
    flags.parse_flags(*argv)
    flags.log['stdout'] = stdout

    temp_log = Log(flags.log)
    temp_log.verbose(Log.GRAY3 + f"Command line : {" ".join(sys.argv)}\n")
    temp_log.verbose(Log.GRAY3 + "Verbose mode on\n")
    temp_log.debug  (Log.GRAY3 + "Debug mode on\n")
    temp_log.trace  (Log.GRAY3 + "Trace mode on\n")

    builtins = Builtins()
    cv_builtins.set(builtins)

    file = flags.project['file']

    builtins.build    = Build(flags.build)
    builtins.log      = Log(flags.log)
    builtins.project  = Project(flags.project, file = file)
    builtins.repo     = Repo(file = file)
    builtins.runner   = Runner()
    builtins.stats    = Stats()
    builtins.script   = Script(file = file, **flags.extra)

    builtins.runner.__expand__()
    builtins.project.__expand__()
    builtins.build.__expand__()
    builtins.repo.__expand__()
    builtins.script.__expand__()

    builtins.repo.load_stat_db()
    builtins.repo._scripts.append(builtins.script)
    builtins.project._repos.append(builtins.repo)

    return builtins

# ----------------------------------------

def create_test_script(
    in_file: str, stdout: TextIO, extra_flags: list[str]) -> Dict:
    argv = [f"--project.file={in_file}", *(extra_flags or [])]
    builtins = init(argv, stdout)
    return builtins


#endregion
# ==================================================================================================

if __name__ == "__main__":
    sys.exit(_start())
