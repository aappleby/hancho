#!/usr/bin/python3
#!/usr/bin/python3
# ruff: noqa: RUF012 C408
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

# FIXME do a template test with two nested dicts a.b and a.c where a.c contains a template referring to b.d
# FIXME we need tests that actually load .hancho files, repos, nested loads, etc.

from __future__ import annotations

import argparse
import ast
import asyncio
import colorsys
import contextvars
import copy
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
from collections import Counter, abc
from contextlib import chdir, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Any, TypeGuard, cast

# endregion
# ==================================================================================================
# region constants

hancho = sys.modules[__name__]
sys.modules["hancho"] = hancho

def new_vars(v) -> abc.MutableMapping:
    return v if Utils.is_mut_mapping(v) else vars(v)

def public(scope):
    yield from filter(is_public, scope)

def is_public(key):
    return not key.startswith("_")

def is_private(key):
    return key.startswith("_")

# endregion
# ==================================================================================================
# region Utils

class Utils:

    @classmethod
    def reset(cls, flags : Flags):
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
        if len(result) == 0:
            return []
        return result if len(result) > 1 else result[0]

    @staticmethod
    def weave(lhs, rhs, *args) -> list[str]:
        if not lhs or not rhs: return []
        return Utils.cross_join(lambda x, y: x + y, lhs, rhs, *args)

    @staticmethod
    def obj_to_float(obj) -> float:
        """
        Generates a 'random' float in the range [0.0,1.0) by hashing the object's ID.
        """
        temp = id(obj) & 0xFFFFFFFF
        temp = ((temp ^ (temp >> 19)) * 0x23456789) & 0xFFFFFFFF
        temp = ((temp ^ (temp >> 19)) * 0x23456789) & 0xFFFFFFFF
        temp = ((temp ^ (temp >> 19)) * 0x23456789) & 0xFFFFFFFF
        return (temp & 0xFFFFFFFF) / 0x100000000

    color_map = {}
    color_cursor = 0

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
    def flatten(variant):
        return list(Utils.yield_values(variant))

    @staticmethod
    def is_scalar(v):
        if type(v) is object: return True
        return not (Utils.is_sequence(v) or Utils.is_mapping(v) or Utils.is_set(v))

    @staticmethod
    def is_mut_scalar(v):
        if type(v) is object: return True
        return not (Utils.is_mut_sequence(v) or Utils.is_mut_mapping(v) or Utils.is_mut_set(v))

    @staticmethod
    def is_sequence(v) -> TypeGuard[abc.Sequence]:
        if isinstance(v, (str, bytes, bytearray)):
            return False
        return isinstance(v, abc.Sequence)

    @staticmethod
    def is_mut_sequence(v) -> TypeGuard[abc.MutableSequence]:
        if isinstance(v, (str, bytes, bytearray)):
            return False
        return isinstance(v, abc.MutableSequence)

    @staticmethod
    def is_set(v) -> TypeGuard[abc.Set]:
        return isinstance(v, abc.Set)

    @staticmethod
    def is_mut_set(v) -> TypeGuard[abc.MutableSet]:
        return isinstance(v, abc.MutableSet)

    @staticmethod
    def is_mapping(v) -> TypeGuard[abc.Mapping]:
        # FIXME Forcing tasks to _not_ be mappings so we don't recurse inside them in various
        # places.
        #if isinstance(v, Scope) and not isinstance(v, Dict):
        #    raise AssertionError("FIXME should we be catching this case?")
        if isinstance(v, Task):
            return False
        return isinstance(v, abc.Mapping)

    @staticmethod
    def is_mut_mapping(v) -> TypeGuard[abc.MutableMapping]:
        # FIXME Forcing tasks to _not_ be mappings so we don't recurse inside them in various
        # places.
        #if isinstance(v, Scope) and not isinstance(v, Dict):
        #    raise AssertionError("FIXME should we be catching this case?")
        if isinstance(v, Task):
            return False
        return isinstance(v, abc.MutableMapping)

    @staticmethod
    def is_scope(v) -> TypeGuard[Scope]:
        return isinstance(v, Scope)

    @staticmethod
    def yield_items(variant, seen : set | None = None) -> Any:
        if seen is None:
            seen = set()
        if (vid := hex(id(variant))) in seen:
            return
        seen.add(vid)

        if variant is None:
            return
        elif isinstance(variant, Task):
            # don't recurse into tasks, we want to collect them in _input_tasks
            yield (None, variant)
        elif Utils.is_mapping(variant):
            yield from variant.items()
        elif Utils.is_set(variant) or Utils.is_sequence(variant):
            yield from enumerate(variant)
        else:
            yield (None, variant)

    @staticmethod
    def yield_values(variant) -> Any:
        yield from [kv[1] for kv in Utils.yield_items(variant)]

    @staticmethod
    def yield_public_values(variant) -> Any:
        yield from [kv[1] for kv in Utils.yield_items(variant) if is_public(kv[0])]

    @staticmethod
    def hex_id(obj):
        return "0x" + hex(id(obj))[-4:].upper()

    @staticmethod
    def instance_tag(obj):
        if Utils.is_mapping(obj) and 'name' in obj:
            return obj['name'] + "@" + Utils.hex_id(obj)
        else:
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
            if Utils.is_mapping(obj):
                items = {k: wrapper(v, *args, **kwargs) for k, v in obj.items()}
                return type(obj)(**items)
            elif Utils.is_sequence(obj) or Utils.is_set(obj):
                items = [wrapper(v, *args, **kwargs) for v in obj]
                return type(obj)(*items)
            else:
                return func(obj, *args, **kwargs)

        return cast(abc.Callable[..., str], wrapper)

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
    def get_stats(cls, file : str, command = None) -> dict:
        cls.stat_calls += 1

        _hash = cls.hash_file(file)
        _stat = os.stat(file)

        command = Utils.commands_to_string(command)

        stat = dict(
            hash = _hash,
            st_size = _stat.st_size,
            st_mtime_ns = _stat.st_mtime_ns,
            command = command
        )

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
                deplines3 = [d for d in deplines2 if d[-1] != ':']
            else:
                raise Task.BROKEN(f"Invalid depfile format {format}") # pragma: no cover

        # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
        deplines4 : list[str] = [cast(str, Path.join(task_cwd, d)) for d in deplines3]

        deplines5 = [d for d in deplines4 if not d.startswith("/usr")]

        return deplines5

    class Missing:
        def __repr__(self): return "<field missing>"
        def __bool__(self): return False

    MISSING : Any = Missing()

# endregion
# ==================================================================================================
# region ContextGlobals

class ContextGlobals:
    def __init__(self):
        object.__setattr__(self, '_ctx', ContextVar('globals'))
        self._ctx.set(dict())

    def set(self, mapping):
        return self._ctx.set(mapping)

    def __getattr__(self, key):
        return self._ctx.get()[key]

    def __setattr__(self, key, val):
        self._ctx.get()[key] = val

cv = ContextGlobals()

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

    TRACE    = 0
    DEBUG    = 10
    INFO     = 20
    WARNING  = 30
    ERROR    = 40
    CRITICAL = 50

    def __init__(self, flags : Flags):

        self.level      = cast(int, getattr(Log, flags.log.get('level', 'info').upper()))
        self.wrap       = flags.log.get('wrap', False)
        self.color      = flags.log.get('color', True)
        self.timestamp  = flags.log.get('timestamp', True)
        self.width      = flags.log.get('width', shutil.get_terminal_size().columns)

        assert type(self.level) is int

        self._time_origin   = time.perf_counter()
        self._indent_stack  = []
        self._line_buffer   = ""
        self._match_escapes = re.compile(r"(\x1B.*?m)")

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
        self._log(Log.DEBUG, Log.AQUA + text)

    def info(self, text):
        self._log(Log.INFO, Log.GREEN + text)

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
            self.error(f"  text = '{ex}'\n")
            self.error(f"  file = {frame.filename}\n")
            self.error(f"  func = {frame.name}\n")
            self.error(f"  line = {frame.lineno}\n")
            self.error(traceback.format_exc() + "\n")
        else: # pragma: no cover
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

            if line[-1] == '\n':
                self._line_buffer += line[:-1]
                self._line_buffer += Log.RESET
                self._line_buffer += '\n'
                self._flush()
            else:
                self._line_buffer += line
                self._line_buffer += Log.RESET

    def _flush(self):
        # Dumps the line buffer to stdout and then clears it.
        if self._line_buffer:
            # If we want a non-colorized log, strip color codes from the line.
            if not self.color:
                self._line_buffer = re.sub(self._match_escapes, '', self._line_buffer)

            # If the line wasn't finished (because we're exiting the app), stick a newline on it.
            if self._line_buffer[-1] != '\n':
                self._line_buffer += '\n'

            if not self.wrap:
                self._line_buffer = self._clip_printable(self._line_buffer, self.width)

            sys.stdout.write(self._line_buffer)
            self._line_buffer = ""

    def _clip_printable(self, text : str, width : int) -> str:
        """
        Clips a string with embedded escape codes (such as ANSI color codes) so that it fits in
        'width' without breaking the escape codes.

        If the printable portion exceeds 'width', it will be clipped and capped with '...'.
        """
        if len(text) < 3:
            return text #pragma: no cover

        # We don't want to clip trailing newlines - if one is present, just remember it was there
        # and we'll stick it back on at the end.
        newline = text[-1] == '\n'
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
            result += '\n'

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
            cv.log.error(f"Commonpath failed for '{lhs}' and '{rhs}'\n")
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
        def join(x, y):
            return os.path.normpath(os.path.join(x, y))
        return Utils.cross_join(join, lhs, rhs, *args)

# endregion
# ==================================================================================================
# region parse_flags

def parse_flags(*argv) -> Flags:

    if len(argv) > 0 and "hancho.py" in argv[0]:
        argv = argv[1:]

    desc = textwrap.dedent("""
    ================================================================================
                    Hancho is a simple, pleasant build system
    ================================================================================
    """)

    parser = argparse.ArgumentParser(
        description=desc,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # ------------------------------------
    # fmt: off

    levels = ["trace", "debug", "info", "warning", "error", "critical"]

    parser.add_argument('-o', "--opt_file",                                     help="File containing JSON that will be used as additional options")

    parser.add_argument(        "project.targets",     nargs="*",               help="The names or partial names of targets to build")
    parser.add_argument(      "--project.name",                                 help="Name of the whole build project, if you don't want it to be <project>")
    parser.add_argument('-s', "--project.path",                                 help="Path to the .hancho file that starts the build.")
    parser.add_argument(      "--project.root",                                 help="The top script runs in this directory.")
    parser.add_argument(      "--project.build_dir",                            help="Build artifacts go in this directory at the top of each repo.")
    parser.add_argument(      "--project.build_tag",                            help="Tagged builds will have separate subdirectories under the build directory.")
    parser.add_argument('-f', "--project.build_force", action = 'store_true',   help="Rebuild targets even if they're clean.")
    parser.add_argument('-a', "--project.build_all",   action = 'store_true',   help="Build every task in every repo.")
    parser.add_argument(      "--project.dry_run",     action = 'store_true',   help="Dry run - Do everything except actually run commands.")
    parser.add_argument(      "--project.strict",      action = 'store_true',   help="Strict mode, slightly more error checking to catch footguns.")

    parser.add_argument(      "--runner.max_errors",                            help="The maximum number of task errors we tolerate before abandoning the build")
    parser.add_argument('-j', "--runner.max_jobs",                              help="Run a maximum of N jobs in parallel.")

    parser.add_argument('-l', "--log.level",           choices = levels,        help="Select verbosity level.")
    parser.add_argument('-w', "--log.wrap",            action = 'store_false',  help="Wrap lines around the console instead of clipping them")
    parser.add_argument('-c', "--log.color",           action = 'store_false',  help="Use color in the log for better readability")
    parser.add_argument(      "--log.timestamp",       action = 'store_false',  help="Timestamp each log line")
    parser.add_argument(      "--log.width",           type = int,              help="Max width (in characters) of the log if not wrapping lines")

    # fmt: on

    (argv_vars, unrecognized) = parser.parse_known_args(argv if argv else [])

    # ------------------------------------
    # Turn the flags into a tree.

    def set_by_path(lhs, key, val):
        parts = key.split('.')
        for part in parts[:-1]:
            lhs = lhs.setdefault(part, Dict())
        lhs[parts[-1]] = val

    argv_flags = Dict()
    for k, v in argv_vars.__dict__.items():
        if v is not None:
            set_by_path(argv_flags, k, v)

    # ------------------------------------
    # Load flags from opt_file if present

    opt_file = {}
    opt_file_name = argv_flags.pop("opt_file", None)
    if opt_file_name:
        cv.cv.log.info(f"Loading options file {opt_file_name!r}\n")
        if os.path.exists(opt_file_name):
            with open(opt_file_name) as f:
                try:
                    opt_file = json.load(f)
                except Exception as _:
                    cv.log.error(f"Opt file {opt_file_name!r} invalid!\n")
                    traceback.print_exc()
        else:
            cv.log.error(f"Opt file {opt_file_name!r} not found!\n")

    # ------------------------------------
    # Unrecognized command line flags also become config fields if they are flag-like.
    # Naked flags become {'name':True}, number types become numbers, 'true' and 'false'
    # become bools (regardless of capitalization), everything else becomes a string.

    mystery_flags = Dict()
    for chunk in unrecognized:
        if match := re.match(r"--([^=]+)=(.+)", chunk):
            key = match.group(1)
            val = match.group(2)
            val = val.lower()

            if val == "true" or val == "1":
                val = True
            elif val == "false" or val == "0":
                val = False
            else:
                with suppress(NameError, ValueError, SyntaxError):
                    val = ast.literal_eval(val)

            set_by_path(mystery_flags, key, val)

    # ------------------------------------
    # Merge 'em all and we're done.

    flags = Flags()

    flags.update(argv_flags, opt_file, mystery_flags)

    return flags

# endregion
# ==================================================================================================
# region mergey stuffs

# When merging Dicts, if two attributes have the same name:
#     - If they are both dicts, we recursively merge them.
#     - If they are both lists, we concatenate them.
#     - Otherwise rightmost non-None wins.

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
            merge_variants(lhs2, rhs2)
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

def merge_args(lhs, *args, **kwargs):
    for rhs in filter(None, [*args, kwargs]):
        merge_variants(lhs, rhs)

# endregion
# ==================================================================================================
# region Scope

# The Scope object is the basic building block of our template variable resolution tree.
# It exposes all "public" attributes (that don't start with an underscore) through a MutableMapping
# interface, and adjusts __deepcopy__ to only deep-copy public attributes (private attributes are
# shallow-copied).

class Scope(abc.MutableMapping):
    def __init__(self, up):
        self._up = up

    # FIXME I had an infinite loop when printing scopes or something but i forgot how I triggered
    # it :/
    #def __repr__(self):
    #    return Dumper.dump(self)
    #
    #def __dump__(self, opts, seen):
    #    pass

    def update(self, *args, **kwargs):
        merge_args(self, *args, **kwargs)

    def expand(self, template, recursive : bool = False):
        return Expander.expand(template, self, recursive)

    def eval(self, expr, recursive : bool = False):
        return Expander.expand('{' + expr + '}', self, recursive)

    def xip(self, key, /, recursive = True):
        """Expand-in-place. Replaces a field with its expanded version."""
        result = self[key]
        result = self.expand(result, recursive)
        self[key] = result
        return result

    # Fill-in-the-blank (or override what's there): Merges lhs and args into a new Dict, keeping
    # only keys that were already in lhs. For example, if you have a Dict that contains
    # "out_bin" and you merge it with "compile_cpp", Hancho will complain that "out_bin" is missing
    # - it sees both "out_obj" and "out_bin" and assumes the task produces both. If you do
    # compile_cpp.fill(...), "out_bin" does not get added to compile_cpp.

    def fill(self, *args : Scope, **kwargs):
        for rhs in (*args, kwargs):
            merge_variants(self, rhs, keep_rhs = False)

    # ----------------------------------------

    def __deepcopy__(self, memo):
        if id(self) in memo:
            return memo[id(self)]

        result = type(self).__new__(type(self))

        for k, v in self.__dict__.items():
            v2 = v if is_private(k) else copy.deepcopy(v)
            result.__dict__[k] = v2

        return result

    # ----------------------------------------
    # These are here so that the linter doesn't complain about reading/writing unknown fields off a
    # scope.

    def __getattr__(self, key) -> Any:
        return object.__getattribute__(self, key)

    def __setattr__(self, key, val : Any):
        object.__setattr__(self, key, val)

    # ----------------------------------------
    # Our MutableMapping interface only exposes public attributes.

    def __getitem__(self, key):
        if is_private(key): raise KeyError(key)
        return self.__dict__.__getitem__(key)

    def __setitem__(self, key, val):
        if is_private(key): raise KeyError(key)
        return self.__dict__.__setitem__(key, val)

    def __delitem__(self, key):
        if is_private(key): raise KeyError(key)
        self.__dict__.__delitem__(key)

    def __len__(self):
        return sum(1 for _ in self)

    def __iter__(self):
        yield from filter(is_public, self.__dict__)

class Dict(Scope):
    def __init__(self, *args, **kwargs):
        super().__init__(kwargs.pop('_up', None))
        self.update(*args, **kwargs)

class Tool(Dict):
    # Tool is just an alias for Dict to make build scripts more readable.
    pass

class Data(Dict):
    # Same thing
    pass

class Flags(Dict):
    def __init__(self):
        self.hancho  = Dict(root = Path.dirname(__file__))
        self.utils   = Dict()
        self.runner  = Dict()
        self.project = Dict()
        self.log     = Dict()

# endregion
# ==================================================================================================
# region Expander

# FIXME this should probably inherit from Scope
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

    # region instance methods

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

        cursor = self.scope

        while key not in cursor:
            if check_up and (up := cursor._up): # type: ignore
                trace_up(cursor, up, "get", key)
                cursor = up
            else:
                break

        if key not in cursor:
            raise KeyError(key)

        trace_start(cursor, "get", key)
        result = getattr(cursor, key)
        trace_end(cursor, key, result)

        if isinstance(result, Scope):
            # have to do this so that "read nested c first" resolves in the dest dict first
            result = self.wrap(result, object.__getattribute__(self, 'recursive'))
        else:
            result = self.expand(result, cursor)
        return result

    # endregion

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
    def xip(cls, scope : Scope, /, recursive : bool):
        # eXpand-In-Place
        for key, val in scope.__dict__.items():
            if is_public(key):
                scope.__dict__[key] = cls.expand(val, scope, recursive)
        return scope

    @classmethod
    def expand(cls, var : Any, scope : Scope, /, recursive : bool = False) -> Any:
        result = cls._expand(var, scope, recursive)
        assert not isinstance(result, cls)
        return result

    @classmethod
    def _expand(cls, var : Any, scope : Scope, recursive : bool) -> Any:
        if isinstance(scope, cls):
            raise AssertionError()

        # Bail out if we've recursed too many times.
        old_depth = cls.cv_depth.get()
        if old_depth > cls.MAX_DEPTH:
            raise RecursionError(f"Expansion failed to terminate after {old_depth} recursions: {var!r}")
        cls.cv_depth.set(old_depth + 1)

        try:
            old_var = None
            while old_var != var:
                old_var = var

                if var is Utils.MISSING:
                    raise AssertionError("Tried to expand a sentinel value")

                # Don't recurse into other scopes
                if isinstance(var, Scope):
                    return var

                if recursive:
                    if Utils.is_mut_mapping(var):
                        for k, v in var.items():
                            var[k] = cls._expand(v, scope, recursive)
                        return var
                    elif Utils.is_mut_sequence(var):
                        for i, v in enumerate(var):
                            var[i] = cls._expand(v, scope, recursive)
                        return var
                    elif Utils.is_mut_set(var):
                        var = list(var)
                        for i, v in enumerate(var):
                            var[i] = cls._expand(v, scope, recursive)
                        return set(var)

                # Don't expand non-strings
                if not isinstance(var, str):
                    return var

                blocks = cls._split_text(var)

                if len(blocks) == 0 or (len(blocks) == 1 and isinstance(blocks[0], cls.Literal)):
                    return var

                if len(blocks) == 1 and isinstance(blocks[0], cls.Macro):
                    var = cls._eval_macro(blocks[0], scope, recursive) # type: ignore
                else:
                    try:
                        trace_start(scope, "expand", var)
                        for i, b in enumerate(blocks):
                            if isinstance(b, cls.Macro):
                                blocks[i] = cls._expand(b, scope, recursive)
                        var = "".join(Utils.stringify(b) for b in blocks)
                    finally:
                        trace_end(scope, old_var, var)


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
    def _eval_macro(cls, var : Expander.Macro, scope : Scope, recursive : bool) -> Any:
        # Bail out if we've done too many evals already.
        old_evals = cls.cv_evals.get()
        if old_evals >= cls.MAX_EVALS:
            raise RecursionError(f"Expansion failed to terminate after {old_evals} evals: '{var!r}'")
        cls.cv_evals.set(old_evals + 1)

        old_var = var
        result = var

        try:
            trace_start(scope, "eval", var)
            result = eval(var[1:-1], {}, cls.wrap(scope, recursive))
        except RecursionError:
            raise
        except Exception as ex:
            _ = ex
            # Anything you put here will be super spammy
            print(f"##########\neval failed because >{ex}<\n##########\n")
            pass
        except BaseException:
            raise
        finally:
            trace_end(scope, old_var, result)

        if isinstance(result, cls):
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

        for i, c in enumerate(text):
            if c == '{':
                idelim = i
            elif c == '}' and idelim >= 0:
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
    opaque_types = {
        #types.BuiltinFunctionType : "<builtin>",
        #types.ModuleType          : "<module>",
        types.GeneratorType       : "<generator>",
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
        *opaque_types.keys(),
    )

    match_pointer : re.Pattern = re.compile(r"0[xX][0-9a-fA-F]{4,16}")

    @classmethod
    def depointer(cls, text):
        text = Dumper.match_pointer.sub("0x...", text)
        return text

    @dataclass
    class Opts:
        depth : int = 3
        indent_stack2 : list[str] = dataclasses.field(default_factory=list)  # Any container fields with these names will _not_ be recursively dumped
        fold : list[str] = dataclasses.field(default_factory=list)  # Any container fields with these names will _not_ be recursively dumped
        print_id : bool = True
        print_prefix : bool = True
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
        depth=3,
        fold = None,
        print_id=True,
        print_prefix=True,
        color_code=False,
        dump_private=True,
        width=80,
        len=0,
        tab=4,
        indent_level = 0
    ):
        fold = fold or []
        indent_stack2 = [" "] * indent_level
        opts = Dumper.Opts(depth, indent_stack2, fold, print_id, print_prefix, color_code, dump_private, tab, len, width, False)
        return cls._dump_to_str(None, val, opts, set())

    #@classmethod

    @staticmethod
    def print(*args, **kwargs):
        result = Dumper.dump(*args, **kwargs)
        print(result)

    @classmethod
    def _dump_to_str(cls, key, val : Any, opts, seen : set):
        if key is not None and is_private(key) and not opts.dump_private:
            return ""
        elif key == "__builtins__":
            return cls._dump_prefix(key, val, opts) + "<builtins>"
        elif hasattr(type(val), "__dump__"):
            return val.__dump__(key, opts, seen)
        elif Utils.is_sequence(val) or Utils.is_mapping(val) or Utils.is_set(val):
            return cls._dump_vector(key, val, val, opts, seen)
        elif hasattr(val, "__dict__"):
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

        if id(val) in seen:
            return cls._dump_prefix(key, val, opts) + "<ref loop>"
        seen.add(id(val))
        # FIXME should we always be using repr here?
        return cls._dump_prefix(key, val, opts) + repr(val)

    @classmethod
    def _dump_vector(cls, key, val, contents, opts, seen : set):
        prefix = cls._dump_prefix(key, val, opts, force_type = False)

        if id(val) in seen:
            return prefix + "<ref loop>"
        seen.add(id(val))

        if Utils.is_mapping(contents):
            ld, items, rd = '{', list(contents.items()), '}'
        elif Utils.is_sequence(contents):
            items = [(None, v) for v in contents]
            if isinstance(contents, tuple):
                ld, items, rd = '(', items, ",)" if len(items) == 1 else ')'
            else:
                ld, items, rd = '[', items, ']'
        elif Utils.is_set(contents):
            items = [(None, v) for v in contents]
            ld, items, rd = '{', items, "}"

        else:
            raise AssertionError(f"Don't know what to do with {type(val)}") # pragma: no cover

        public_items = [x for x in items if is_public(x[0])]

        return cls._dump_items(key, prefix, ld, public_items, rd, opts, set(seen))

    @classmethod
    def _dump_items(cls, key, prefix, ld, items, rd, opts, seen : set):
        # This slightly odd construct is so that when a deeply nested container doesn't fit on a
        # line, we rewind the callstack back to the topmost container that was not forced to be
        # flat.

        if key in opts.fold:
            #return prefix + ld + "<folded>" + rd
            return prefix + "<folded>"

        if opts.flat:
            return prefix + cls._dump_items_flat(key, ld, items, rd, opts, set(seen))
        else:
            try:
                return prefix + cls._dump_items_flat(key, ld, items, rd, dataclasses.replace(opts, flat = True), set(seen))
            except Dumper.LineTooLong:
                return prefix + cls._dump_items_deep(key, ld, items, rd, opts, set(seen))

    @classmethod
    def _dump_items_flat(cls, key, ld, items, rd, opts, seen : set):
        result = ld

        for i in range(len(items)):
            result += cls._dump_to_str(items[i][0], items[i][1], opts, set(seen))
            if i < len(items) - 1: result += ", "
            if opts.len + len(result) + len(rd) > opts.width:
                raise Dumper.LineTooLong()

        return result + rd

    @classmethod
    def _dump_items_deep(cls, key, ld, items, rd, opts, seen : set):
        result = ld + '\n'

        if opts.depth == 0:
            return ld + "..." + rd
        opts = dataclasses.replace(opts, depth = opts.depth - 1)

        opts.indent_stack2.append(" ")

        # len(pad) + 1 for the trailing comma
        pad = (" " * opts.tab) * len(opts.indent_stack2)
        new_opts = dataclasses.replace(opts, len = len(pad) + 1)

        for i in range(len(items)):
            result += pad + cls._dump_to_str(items[i][0], items[i][1], new_opts, set(seen))
            if i < len(items) - 1: result += ','
            result += '\n'
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
            if opts.print_id: prefix += "@" + Utils.hex_id(val)
        if prefix: prefix += " = "
        return prefix

# endregion
# ==================================================================================================
# region Tracer

def trace_start(scope, action, arg):
    if isinstance(scope, Expander):
        scope = scope.scope

    scope_color = Utils.obj_to_ansi_color(scope)
    scope_tag   = Utils.instance_tag(scope)

    cv.log.trace(f"{scope_color}┌ {scope_tag}{Log.RESET}.{action}({arg!r})\n")
    cv.log.indent(scope_color)

# ==================================================================================================

def trace_up(scope, up, action, arg):
    if isinstance(scope, Expander):
        scope = scope.scope

    scope_color = Utils.obj_to_ansi_color(scope)
    scope_tag   = Utils.instance_tag(scope)
    up_color   = Utils.obj_to_ansi_color(up)
    up_tag     = Utils.instance_tag(up)

    cv.log.info(f"{scope_color}┌ {scope_tag}{Log.RESET}.{action}({arg!r}) -> {up_color}{up_tag}\n")

# ==================================================================================================

def trace_end(scope, arg, result):
    cv.log.dedent()

    if isinstance(result, Expander):
        result = result.scope

    scope_color   = Utils.obj_to_ansi_color(scope)
    result_color = Log.RESET
    result_type  = type(result)

    if Utils.is_mapping(result):
        result_color = Utils.obj_to_ansi_color(result)

    cv.log.trace(f"{scope_color}└ {arg!r} : {result_type.__name__}{Log.RESET} = ")
    if not Utils.is_scalar(result):
        cv.log.trace(f"{result_color}{Utils.instance_tag(result)}\n")
    else:
        cv.log.trace(f"{result_color}{result!r}\n")

# endregion
# ==================================================================================================
# region Runner

class Runner:

    @classmethod
    def reset(cls, flags : Flags):
        cls.max_jobs   = flags.runner.get('max_jobs', os.cpu_count() or 1)
        cls.max_errors = flags.runner.get('max_errors', 0)

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

# endregion
# ==================================================================================================
# region Project

class Project(Scope):
    def __init__(self, *args, flags : Flags, scope : Scope, **kwargs):
        super().__init__(scope)

        self.name        = "<project>"
        self.path        = "{resolve('build.hancho')}"
        self.root        = "{dirname(path)}"
        self.targets     = []
        self.build_dir   = "build"
        self.build_tag   = ""
        self.build_force = False
        self.build_all   = False
        self.dry_run     = False
        self.strict      = True
        self.trace       = False

        self.update(flags.project, *args, **kwargs)
        Expander.xip(self, True)

        self._real_filenames = set()
        self._dedupe = Dict()
        self._repos : list[Repo] = []
        self._build_reasons = Counter()
        self._log = Log(flags)
        self._runner = Runner()

        self._globals = dict(
            project = self,
            log = self._log
        )


    @contextmanager
    def activate(self):
        token = cv._ctx.set(self._globals)
        yield
        cv._ctx.reset(token)

# endregion
# ==================================================================================================
# region Task

# FIXME not actually using this as a scope yet

class Task(Scope):

    class FAILED(Exception):    pass
    class CANCELLED(Exception): pass
    class SKIPPED(Exception):   pass
    class BROKEN(Exception):    pass

    def __init__(self, scope, *args, **kwargs):
        super().__init__(scope)

        self.name       = '<no name>'
        self.desc       = '<no desc>'
        self.command    = None
        self.cwd        = '{repo.root}'
        self.in_depfile = ''
        self.depformat  = "gcc" if os.name == "posix" else "msvc"
        self.job_size   = 1
        self.build_dir  = '{join(repo.build_dir, relpath(script.root, repo.root))}'
        self.dry_run    = '{project.dry_run}'
        self.force      = '{project.build_force}'
        self.in_files   = {}
        self.out_files  = {}

        #self.script  = script
        #self.repo    = script.repo
        #self.project = script.repo.project

        self.update(*args, **kwargs)

        # ----------------------------------------

        self._commands = []
        self._enabled = False

        self._input_tasks = [t for t in Utils.yield_public_values(self.__dict__) if isinstance(t, Task)]

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

    def __repr__(self):
        return Dumper.dump(self)

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
            t.hancho_task = self # type: ignore
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

    async def task_top(self):
        # Entry point for tasks, just so we can keep all the task-level exception handling together.

        try:
            return await self.task_main()

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

        raise self._error

    async def task_main(self):
        # Await all tasks in our input fields and then flatten them.
        await self.await_inputs()

        # We're ready to run
        Runner.tasks_started += 1
        self._task_id = Runner.tasks_started
        self.log_task(Log.DEBUG, Utils.instance_tag(self) + " starting\n")

        # Expand all mandatory fields in the raw config and fix raw file paths.
        self.expand_task()

        # If there's a depfile from a previous build, load it so we can use it below.
        if self.in_depfile:
            self._old_deplines = Utils.load_depfile(self.in_depfile, self.depformat, self.cwd)

        # Inputs are ready, templates are expanded, see if everything's sane before we try running
        # commands.
        self.sanity_check()

        # Dry runs early out after the task is initialized but before we do .exists() checks or
        # run any commands.
        if self.dry_run:
            return

        # Paths updated. See if we need to rebuild our outputs.
        self._reason = self.rebuild_reason()
        if not self._reason:
            raise Task.SKIPPED(f"Task is up-to-date: '{self.name}' : '{self.desc}'")

        # Wait for enough jobs to free up to run this task.
        self._cores = await Runner.acquire(self.job_size)

        # Run all the task's commands
        text  = repr(self.name) if self.name else ""
        text += " : " if self.name and self.desc else ""
        text += repr(self.desc) if self.desc else ""
        self.log_task(cv.log.info,  Log.TEAL + f"Task {text}\n")
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

        for file in Utils.yield_values(self.out_files):
            if not os.path.exists(file):
                raise Task.FAILED(f"Task ran, but output file still missing: {file}")

        # And we're done
        return self.out_files

    async def await_inputs(self):
        # NOTE: Hancho _cannot_ have dependency cycles unless you do something really sketchy via
        # modifying tasks after they're created but before they're started. If you point task B's
        # inputs at task A and task A's inputs at task B and it blows up, that's on you.

        for input_task in self._input_tasks:
            if not isinstance(input_task, Task):
                continue
            if input_task._aio_task is None:
                raise AssertionError("One of a task's input sub-tasks was not started") # pragma: no cover
            try:
                await input_task._aio_task
            except Task.SKIPPED:
                # This input task didn't need to rebuild.
                pass
            except Exception as ex:
                self._error = Task.CANCELLED(f"Task {hex(id(self))} is cancelled")
                raise self._error from ex

    def expand_task(self):

        if cv.log.level <= Log.DEBUG:
            self.log_task(Log.DEBUG, "Task before expand:\n")
            #self.log_task(Log.DEBUG, Dumper.dump(self, dump_private=False, fold = ["hancho", "log", "in_objs", "_up", "script", "_aio_context"]) + "\n")
            self.log_task(Log.DEBUG, Dumper.dump(self, dump_private=False) + "\n")

        build_dir = self.eval("build_dir")

        # Then we expand all io fields and fix their paths.
        io_fields = [k for k in self if k.startswith("in_") or k.startswith("out_")]

        for _field in io_fields:
            # if task is a scope, then flattening in_files produces an array of the _attributes_
            # of the task instead of the out_files.

            files = Utils.flatten(self[_field])
            files = [v for v in files if v != ""]
            for i, f in enumerate(files):
                if isinstance(f, Task):
                    files[i] = f.out_files

            if files:
                files = self.expand(files, recursive = True)
                files = Utils.flatten(files)
                files = self.fix_paths(_field, files, build_dir)
                files = files[0] if len(files) == 1 else files

                self[_field] = files

                if _field == "in_depfile":
                    if len(files) != 1:
                        raise Task.BROKEN("in_depfile can't be a list")
                    self.in_depfile = files[0]
                elif _field.startswith("in_"):
                    self.in_files[_field] = files
                elif _field.startswith("out_"):
                    self.out_files[_field] = files

        # Fields all expanded, we can expand the rest of the task now.
        Expander.xip(self, recursive = True)
        self._commands = Utils.flatten(self.command)

        if not self.dry_run:
            for file in filter(None, self.out_files.values()):
                os.makedirs(Path.dirname(file), exist_ok=True)
            if self.in_depfile:
                os.makedirs(Path.dirname(self.in_depfile), exist_ok=True)

        if cv.log.level <= Log.DEBUG:
            self.log_task(Log.DEBUG, "Task after expand:\n")
            self.log_task(Log.DEBUG, Dumper.dump(self) + "\n")

    def fix_paths(self : Task, field : str, file : (str | list | set | tuple | abc.Mapping), build_dir : str):
        """
        Input and output file paths in .hancho scripts are declared relative to the directory the
        script is in (stored in the config under 'script_cwd').
        In general we want to run commands from the root of the repo and store output files in
        repo/build, so we need to fix up the paths to match.
        """
        if Utils.is_sequence(file) or Utils.is_set(file):
            result = [self.fix_paths(field, f, build_dir) for f in file]
            return result
        if Utils.is_mapping(file):
            result = {k:self.fix_paths(field, f, build_dir) for k, f in file}
            return result

        # Join script_cwd with the filename to produce an absolute path.
        script_root = self.eval("script.root")
        file = Path.join(script_root, file)

        # File paths _must_ be abs'd after joining, otherwise they might look like they're under
        # script_dir, but they're not because the paths could have "../../../../.." in them.
        file = Path.abspath(file)

        # Move all outputs under build_dir and ensure their directories exist.
        # Note - This will also move "in_depfile" under build_dir - this is _intentional_ as
        # it's an _output_ from the compiler and is not checked in to the source tree.
        if (field.startswith("out_") or field == "in_depfile") and not Path.startswith(file, build_dir):
            file = Path.relpath(file, self._up.script.root)
            file = Path.join(build_dir, file)

        return file

    def sanity_check(self : Task):
        repo = self._repo
        project = self.project

        # Check for all task issues that break the build

        if not Path.exists(self.cwd):
            raise Task.BROKEN(f"Task working directory '{self.cwd}' does not exist")

        if not Path.startswith(self.build_dir, repo.root):
            raise Task.BROKEN(f"The build dir {self.build_dir} is not under repo.root {repo.root}")

        # In order to provide the least amount of bafflement to users, CLI commands execute
        # from task_cwd (which is usually the root of the repo, the most common cwd)
        # and callbacks execute from dir(script_path) (because you expect to be in the same
        # directory as the script when the callback is firing).

        # This means that pre-relative-ified paths can only be rel'd to one of the two cwds, not both.
        # And that means we disallow mixed cli/callback command lists.

        if isinstance(self.command, list):
            for command in self._commands:
                if type(command) is not type(self._commands[0]):
                    raise Task.BROKEN(f"Commands aren't the same type: {self.command}")

                # Check that task's commands are either strings or callables.
                if not isinstance(command, str) and not callable(command) and command is not None:
                    raise Task.BROKEN(f"Command {command} is not a string or a callable?")

        # In strict mode, we mark a task broken if its command still has delimiters in it.
        if repo.strict:
            for command in Utils.flatten(self.command):
                if Expander.is_macro(command) or Expander.is_template(command):
                    raise Task.BROKEN("STRICT: Command not fully expanded")

        # Check that all build files would end up under build_dir
        for file in Utils.yield_values(self.out_files):
            assert Path.isabs(file)
            if not Path.startswith(file, self.build_dir):
                raise Task.BROKEN(f"Path error, output file {file} is not under build dir {self.build_dir}")

        # Check for task collisions
        for file in Utils.yield_values(self.out_files):
            real_file = cast(str, Path.abspath(file))
            if real_file in project._real_filenames:
                raise Task.BROKEN(f"TaskCollision: Multiple tasks build {real_file}")
            project._real_filenames.add(real_file)

            # Check for missing inputs. We have to check build_dry, as the input files may only exist if
        # we're really running tasks.
        for file in Utils.yield_values(self.in_files):
            if not Path.isabs(file):
                raise Task.BROKEN(f"Somehow we got a non-abs path for an input file - {file}")  # pragma: no cover
            if not Path.exists(file) and not repo.dry_run:
                raise Task.BROKEN(f"Input file missing - {file}")

        # Tasks should have at most one depfile.
        if isinstance(self.in_depfile, list) and len(self.in_depfile) > 1:
            raise Task.BROKEN(f"Tasks can't have more than one dependency file! - {self.in_depfile}")

    async def run_command(self : Task, command : str):
        repo = self._up.repo

        self.log_task(cv.log.info, f"{Path.relpath(self.cwd, repo.root)}$ {command}\n")

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

        self._stdout = stdout_data.decode(errors="replace")
        self._stderr = stderr_data.decode(errors="replace")

        if proc.returncode == 2:
            raise Task.BROKEN("Command return code was 2 : bash error")
        elif proc.returncode:
            raise Task.FAILED(f"Command return code was non-zero : {proc.returncode}")

        if (self._stdout or self._stderr):
            self.log_task(Log.DEBUG, self.dump_stdout())

    async def call_callback(self : Task, command : abc.Callable):
        callback_dir = Path.relpath(self.get_up().script.root, self.get_up().repo.root)
        self.log_task(cv.log.info, f"{callback_dir}$ {command}\n")

        # Callbacks run from the script dir where they were defined so that relative paths used
        # in the callback will be correct.
        up = self.get_up()
        assert up is not None
        script_root = up.script.root

        with chdir(script_root): # type: ignore
            result = command(self)

        # It would seem like we wouldn't have to explicitly unwrap one level of await-ness here,
        # but apparently that's just how Python waitables work.
        if inspect.isawaitable(result):
            result = await result

        return result

    def rebuild_reason(self : Task) -> str:
        """
        Figures out why we have to run a Task, or returns "" if we don't.
        """
        repo = self.repo
        project = self.project

        # ------------------------------------
        # Check the trivial reasons to rebuild

        if self.force:
            project._build_reasons["forced"] += 1
            return "Target forced to rebuild due to task.force"

        if self.get_up().repo.build_force:
            project._build_reasons["forced"] += 1
            return "Target forced to rebuild due to repo.build_force"

        has_input = any(Utils.yield_values(self.in_files))
        if not has_input:
            project._build_reasons["no inputs"] += 1
            return "Always rebuild a target with no inputs"

        has_output = any(Utils.yield_values(self.out_files))
        if not has_output:
            project._build_reasons["no outputs"] += 1
            return "Always rebuild a target with no outputs"

        # ------------------------------------

        for filename in Utils.yield_values(self.in_files):
            if reason := repo.check_stat(filename):
                return reason

        for filename in self._old_deplines:
            if reason := repo.check_stat(filename):
                return reason

        for filename in Utils.yield_values(self.out_files):
            if reason := repo.check_stat(filename, self.command):
                return reason

        if self.in_depfile:  # noqa: SIM102
            if reason := repo.check_stat(self.in_depfile):
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
            if not cv.log._line_buffer:
                cv.log._log(level, f"[{self._task_id:3d}/{Runner.tasks_enabled:3d}] ")
            cv.log._log(level, line)

    def log_task_exception(self : Task, message, ex = None):
        cv.log.error("========================================\n")
        cv.log.error(message + "\n")
        cv.log.error("========================================\n")
        cv.log.error(f"Script    = {self._up.script.path}:\n")
        cv.log.error(f"Task      = {self.name!r} : {self.desc!r}\n")
        cv.log.error(f"os.getcwd = {os.getcwd()}\n")
        cv.log.error(f"task cwd  = {self.cwd}\n")
        cv.log.error(f"command   = {self.command}\n")
        cv.log.exception(ex)
        cv.log.error(self.dump_stdout())
        cv.log.error("========================================\n")

# endregion
# ==================================================================================================
# region Aliases

aliases = Dict(
    name     = "<aliases>",
    dump     = Dumper.print,
    flatten  = Utils.flatten,
    run_cmd  = Utils.run_cmd,
    weave    = Utils.weave,
    abspath  = Path.abspath,
    normpath = Path.normpath,
    basename = Path.basename,
    dirname  = Path.dirname,
    join     = Path.join,
    relpath  = Path.relpath,
    resolve  = Path.resolve,
    swapext  = Path.swapext,
)

# endregion
# ==================================================================================================
# region Script

# FIXME does this _have_ to be moduletype?
#class Script(Scope, types.ModuleType):
class Script(Scope):

    def __init__(self, *args, name : str, path : str, root : str, scope : Scope, **kwargs):
        super().__init__(scope)
        self.name  = name
        self.path  = path
        self.root  = root

        # The Python module loading machinery looks for this field, but it can be None.
        self.__spec__ = None

        self.update(*args, **kwargs)
        Expander.xip(self, True)

        self._tasks = []

    def __getattr__(self, key):
        result = hancho.__dict__[key]
        return result

    def dump(self, *args, **kwargs):
        print(Dumper.dump(*args, **kwargs))

    # We intercept hancho.Task so we can add it to this script's task list and start it if it was
    # dynamically created.
    def Task(self, *args, **kwargs):
        script = self

        scope = Dict(_up = script._up, script = script)
        task = Task(scope, *args, **kwargs)

        self._tasks.append(task)

        # Auto-start the task if it was created dynamically during the build.
        if Utils.in_event_loop():
            task.queue_task()
        return task

    def load_script(self, path, root = None, *args, **kwargs):
        name = os.path.basename(path) if path else "<no path>"
        if root is None:
            root = Path.dirname(path)
        return load_script(name, path, root, self.project, self.repo, *args, **kwargs)

    def load_repo(self, path, root = None, *args, **kwargs) -> Script:
        name = os.path.basename(path) if path else "<no path>"
        if root is None:
            root = Path.dirname(path)
        return load_script(name, path, root, self.project, self.repo, True, *args, **kwargs)

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
        cv.log.error(f"Script {condition}:\n")
        cv.log.error(f"  text = '{message}'\n")
        cv.log.error(f"  file = {frame.f_code.co_filename}\n")
        cv.log.error(f"  func = {frame.f_code.co_name}\n")
        cv.log.error(f"  line = {frame.f_lineno}\n")

@staticmethod
def init_for_testing(path : str, *args) -> Project:
    path  = Path.resolve(path)
    root  = Path.dirname(path)
    flags = parse_flags(*args)

    project = Project(flags = flags, scope = aliases)
    Utils.reset(flags)
    Runner.reset(flags)

    # don't need the dummy script
    _ = load_script("<testing>", path, root, project, repo = None, is_repo = True)

    return project


# endregion
# ==================================================================================================
# region Repo

#class Repo(Script):
class Repo(Scope):

    def __init__(self, *args, name : str, path : str, root : str, scope : Scope, **kwargs):
        super().__init__(scope)

        self.name      = name
        self.path      = path
        self.root      = root
        self.build_dir = "{join(root, project.build_dir, build_tag)}"
        self.build_tag = "{project.build_tag}"
        self.dry_run   = "{project.dry_run}"

        self.update(*args, **kwargs)
        Expander.xip(self, True)

        self._stat_db = {}
        self._scripts = []

    def yield_tasks(self) -> abc.Iterator[Task]:
        for script in self._scripts:
            yield from script._tasks

    def check_stat(self, filename : str, command = None):
        repo = self
        project = self.project

        if not Path.exists(filename):
            project._build_reasons["file missing"] += 1
            return f"File missing: {filename}"

        if filename not in repo._stat_db:
            project._build_reasons["stat missing"] += 1
            return f"Stat missing: {filename}"

        old_stat = repo._stat_db[filename]
        new_stat = Utils.get_stats(filename, command)

        try:
            old_ns = old_stat['st_mtime_ns']
            new_ns = new_stat['st_mtime_ns']

            if old_ns != new_ns:
                project._build_reasons["mtime mismatch"] += 1
                return f"Mtime mismatch {old_ns} != {new_ns} for : {filename}"
        except Exception as err:
            print(err)
            traceback.print_exc()
            raise err

        if old_stat['st_size'] != new_stat['st_size']:
            project._build_reasons["size mismatch"] += 1
            return f"Size mismatch {old_stat['st_size']} != {new_stat['st_size']} for : {filename}"

        if old_stat['hash'] != new_stat['hash']:
            project._build_reasons["hash mismatch"] += 1
            return f"Hash mismatch {old_stat['hash']} -> {new_stat['hash']} for : {filename}"

        if command is not None and old_stat['command'] != new_stat['command']:
            project._build_reasons["command changed"] += 1
            return f"Command used to generate file has changed : {filename!r} : {old_stat['command']!r} : {new_stat['command']!r}"

        # Does not need to rebuild based on file stats / hash
        project._build_reasons["*hash match"] += 1
        return ""

    def load_stat_db(self):
        repo = self
        stat_db_path = os.path.join(repo.build_dir, 'hancho.json')

        if os.path.isfile(stat_db_path):
            with open(stat_db_path) as contents:
                cv.log.info(Log.ORANGE + f"Loading stat_db {stat_db_path}\n")
                repo._stat_db = json.load(contents)
        else:
            cv.log.info(Log.ORANGE + f"No stat db for {repo.root}\n")
            repo._stat_db = {}

    def save_stat_db(self):
        repo = self
        if repo.dry_run:
            return

        stat_db = {}

        # FIXME we could probably save a little work if we didn't always re-stat every input and
        # output, but this is safe for now.

        # ------------------------------------
        # Gather stats for all input files in all tasks.

        for task in repo.yield_tasks():
            if not task._complete:
                continue

            for file in Utils.yield_values(task.in_files):
                stat_db[file] = Utils.get_stats(file)

            in_depfile = task.in_depfile
            if in_depfile:
                stat_db[in_depfile] = Utils.get_stats(in_depfile)
                deplines = Utils.load_depfile(task.in_depfile, task.depformat, task.cwd)
                for file in deplines:
                    stat_db[file] = Utils.get_stats(file) # type: ignore

        # We gather stats from output files in a second pass so that their .command fields
        # overwrite any blank ones from the first pass.

        for task in repo.yield_tasks():
            if not task._complete:
                continue

            for file in Utils.yield_values(task.out_files):
                stat_db[file] = Utils.get_stats(file, task.command)

        stat_db_path = Path.join(repo.build_dir, 'hancho.json')
        Utils.save_json(stat_db, stat_db_path)

        # ------------------------------------
        # And do the same for compile_commands.json with a slightly different format.

        comp_db = {}

        for task in repo.yield_tasks():
            if not task._complete:
                continue

            for file in Utils.yield_values(task.in_files):
                # Haven't tested this in an IDE, but I think it matches the spec.
                comp_db[file] = {
                    "directory" : task.cwd,
                    "command"   : Utils.commands_to_string(task.command),
                    "file"      : file,
                }

        comp_db_path = Path.join(repo.build_dir, 'compile_commands.json')
        Utils.save_json(list(comp_db.values()), comp_db_path)

# endregion
# ==================================================================================================
# region _start

def _start():

    # Parse flags and set up initial global log so we can print messages while starting the
    # project.
    flags = parse_flags(*sys.argv)
    cv.log = Log(flags)

    # Top-level exception handler just so we can print a big red "SOMETHING BROKE" message if
    # we failed to catch an exception during load/build. The 'except' clause should catch
    # Exception and not BaseException so ctrl-c doesn't get misinterpreted as a Hancho bug.

    try:
        sys.modules["hancho"] = sys.modules[__name__]
        if __name__ == "__main__":
            sys.exit(hancho_main(flags))
    except Exception:
        cv.log.critical("Hancho hit an unhandled exception:\n")
        cv.log.critical(traceback.format_exc())
        cv.log.critical(Log.RESET)

        # Re-raise in debug builds so the debugger will stop execution.
        raise
    finally:
        # Don't leave the last line of the log sitting in line_buffer!
        cv.log._flush()

#endregion
# ==================================================================================================
# region load_script

def load_script(name : str, path : str, root : str, project : Project, repo : Repo | None, is_repo : bool, *args, **kwargs) -> Script:
    assert path == Path.resolve(path)
    assert root == Path.resolve(root)

    if is_repo or repo is None:
        repo_scope = Dict(_up = project._up, project = project)
        repo = Repo(name = name, path = path, root = root, scope = repo_scope)
        project._repos.append(repo)

    script_scope = Dict(_up = repo._up, repo = repo)
    script = Script(*args, name = name, path = path, root = root, scope = script_scope, **kwargs)
    repo._scripts.append(script)

    # Dedupe the load - only scripts with identical real paths and identical configs are
    # deduped. This relies on __repr__ and the fields read by Dumper.dump being stable during a
    # build, which they should be in practice.

    Dumper.print(script)

    dupe_key = Dumper.dump(script, print_id = False, tab = 0, color_code = False, depth = 999, width = 999)

    # FIXME need to make sure private fields aren't in the dump, but i'll deal with it properly later.
    assert " _" not in dupe_key

    dupe_key = Dumper.depointer(dupe_key)
    dupe_key = "".join(dupe_key.split())

    script_type = 'repo' if is_repo else 'script'

    if dupe := project._dedupe.get(dupe_key, None):
        cv.log.info(Log.LIME + f"Deduped {script_type} {script.path}\n")
        return dupe
    else:
        cv.log.info(Log.ORANGE + f"Loading {script_type} {script.path}\n")

    code = None
    with open(script.path, encoding="utf-8") as file:
        source = file.read()
        code = compile(source, script.path, "exec", dont_inherit=True)

    project._dedupe[dupe_key] = script

    try:
        with chdir(script.root), cv.log.indenter(Log.ORANGE):
            # FIXME does this _really_ need to be moduletype?
            sys.modules["hancho"] = cast(types.ModuleType, script)
            exec(code, script.__dict__)
            return script
    finally:
        sys.modules["hancho"] = hancho

# endregion
# ==================================================================================================
# region hancho_main

def hancho_main(flags) -> int:

    project = Project(flags = flags, scope = aliases)
    Utils.reset(flags)
    Runner.reset(flags)

    with project.activate():
        project_intro(project)
        project_load(project)
        project_build(project)
        project_stats(project)
        project_passfail(project)

    return project._result

# ========================================

def project_intro(project):
    cv.log.info(Log.LIME + f"Command line : {" ".join(sys.argv)}\n")
    cv.log.info(f"Trace mode {'on' if project.trace else 'off'}\n")
    cv.log.debug("Debug mode on\n")

def project_load(project, *args, **kwargs):
    time_a1 = time.perf_counter()

    name = os.path.basename(project.path) if project.path else "<no path>"
    _ = load_script(name, project.path, project.root, project, None, True, *args, **kwargs)

    time_b1 = time.perf_counter()
    cv.log.info(Log.BLUE + f"Loading project took {time_b1 - time_a1:8.6f} seconds\n")

def project_build(project):
    time_a3 = time.perf_counter()
    result = hancho_build(project)
    time_b3 = time.perf_counter()
    cv.log.info(Log.GREEN + f"Build took {time_b3 - time_a3:8.6f} seconds\n")
    project._result = result

def project_stats(project):
    task_count = 0
    for repo in project._repos:
        task_count += len(list(repo.yield_tasks()))

    # FIXME all the stats should move to Project
    cv.log.debug(f"Tasks created:    {task_count}\n")
    cv.log.debug(f"Tasks enabled:    {Runner.tasks_enabled}\n")
    cv.log.debug(f"Tasks started:    {Runner.tasks_started}\n")
    cv.log.debug(f"Tasks finished:   {Runner.tasks_finished}\n")
    cv.log.debug(f"Tasks broken:     {Runner.tasks_broken}\n")
    cv.log.debug(f"Tasks failed:     {Runner.tasks_failed}\n")
    cv.log.debug(f"Tasks cancelled:  {Runner.tasks_cancelled}\n")
    cv.log.debug(f"Tasks skipped:    {Runner.tasks_skipped}\n")
    cv.log.debug(f"Mtime calls:      {Utils.stat_calls}\n")
    cv.log.debug(f"Hash calls:       {Utils.hash_calls}\n")
    cv.log.debug(f"Hash bytes:       {Utils.hash_bytes}\n")
    cv.log.debug(f"Hash time:        {Utils.hash_time:8.6f}\n")

def project_passfail(project):
    if project._runner.tasks_failed or project._runner.tasks_broken:
        cv.log.error(Log.RED + "BUILD FAILED\n")
    elif Runner.tasks_finished:
        cv.log.info(Log.GREEN + "BUILD PASSED\n")
    else:
        cv.log.info(Log.BLUE + "BUILD CLEAN\n")

# endregion
# ==================================================================================================
# region hancho_build

def hancho_build(project) -> int:

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
            # NOTE - We match task.task_params.name, _not_ the expanded task._name.
            # This is because the task _has not initialized yet_, so we have no config.name.

            for repo in project._repos:
                for task in repo.yield_tasks():
                    if target in task._task_scope.name:
                        task.queue_task()

    elif project.build_all:
        for repo in project._repos:
            for task in repo.yield_tasks():
                task.queue_task()

    else:
        # Enable all tasks in the top repo
        for task in project._repos[0].yield_tasks():
            task.queue_task()

    # ------------------------------------
    # Run the tasks.

    result = asyncio.run(async_run_tasks(project))

    # ------------------------------------
    # Update stat DBs.

    for repo in project._repos:
        repo.save_stat_db()

    return result

# endregion
# ==================================================================================================
# region async_run_tasks

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

    cv.log.info(Log.BLUE + "Running tasks...\n")

    while Runner.live_aio_tasks and (Runner.tasks_broken + Runner.tasks_failed) <= Runner.max_errors:
        finished_aio_task = None

        try:
            finished_aio_task = cast(asyncio.Task, await Runner.aio_done_queue.get())
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
            cv.log.warning(f"Weird exception {type(ex)} >{ex}< at {time.perf_counter()}\n")
            cv.log.exception(ex)
            Runner.tasks_failed += 1
        else:
            # If _none_ of the above exceptions fired, we mark the task as complete.
            finished_aio_task.hancho_task._complete = True #type:ignore
        finally:
            if finished_aio_task is not None:
                Runner.live_aio_tasks.discard(finished_aio_task)

    failures = Runner.tasks_broken + Runner.tasks_failed
    if failures > Runner.max_errors:
        cv.log.error(f"Too many failures after {failures}, cancelling tasks and stopping build\n")

        # Cancel all the asyncio.Tasks that haven't completed yet
        cv.log.warning(f"Cancelling {len(Runner.live_aio_tasks)} tasks\n")

        # This tasks_cancelled count may be off by one or two due to in-flight tasks not being
        # accounted for in live_aio_tasks, but it doesn't matter - we're about to bail out due
        # to failures or someone ctrl-c'ing the build, this is purely cosmetic.

        Runner.tasks_cancelled += len(Runner.live_aio_tasks)
        for t in Runner.live_aio_tasks:
            t.cancel()

        # and then wait on their cancellations to complete (it isn't instantaneous)
        await asyncio.gather(*Runner.live_aio_tasks, return_exceptions=True)

    return 1 if Runner.tasks_failed or Runner.tasks_broken else 0

#endregion
# ==================================================================================================

if 'scratch' not in sys.argv:
    _start()
else:
    cv.log = Log(parse_flags())
    cv.log.info("hello world")
    cv.log._flush()
    print()
