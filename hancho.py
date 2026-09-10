#!/usr/bin/python3
#!/usr/bin/python3
# ruff: noqa: RUF012
#region Header

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
# FIXME change 'env' to 'tree'
# FIXME param defaults should just be another layer in the onion
# FIXME onions should be able to contain other onions, like Onion(parent = Onion(some_layer = blee), some_layer = blah)

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
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Any, TypeVar, cast

# Just a sanity check that we haven't accidentally imported the 'real' hancho twice.
#assert "hancho" not in sys.modules

hancho = sys.modules[__name__]
K = TypeVar("K")
V = TypeVar("V")

#endregion
# ==================================================================================================

class Utils:

    @classmethod
    def reset(cls):
        cls.stat_calls : int = 0
        cls.hash_calls : int = 0
        cls.hash_bytes : int = 0
        cls.hash_time  : float = 0

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

    @staticmethod
    def obj_to_hex(obj) -> int:
        hue = Utils.obj_to_float(obj)
        r, g, b = colorsys.hsv_to_rgb(hue, 0.6, 0.8)
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
        return "0x" + hex(id(obj))[-4:].upper()

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
            if isinstance(obj, (dict, Dict)):
                wrapped_items = {k: wrapper(v, *args, **kwargs) for k, v in obj.items()}
                return type(obj)(**wrapped_items)
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

    @classmethod
    def hash_file(cls, abs_path, h = 0):
        Utils.hash_calls += 1
        time_a = time.perf_counter()
        with open(abs_path, "rb") as f:
            Utils.hash_bytes += os.fstat(f.fileno()).st_size
            digest = hashlib.file_digest(f, lambda: hashlib.blake2b(digest_size=8))
        time_b = time.perf_counter()
        Utils.hash_time += time_b - time_a
        return digest

    @classmethod
    def get_stats(cls, file : str, command = None) -> Dict:
        cls.stat_calls += 1

        _hash = cls.hash_file(file)
        _stat = os.stat(file)

        command = Utils.commands_to_string(command)

        stat = Dict(
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

# ==================================================================================================

class Log:

    class Color:
        """12 half-saturated, 80% value Log.Color evenly spaced around the HSV wheel"""

        def __init__(self, color):
            self.color = color

        def __enter__(self):
            self.old_color = Log.current_color
            Log.current_color = self.color
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            Log.current_color = self.old_color
            return False

    RED     = Color(0xCC6666)
    PINK    = Color(0xCC6699)
    MAGENTA = Color(0xCC66CC)
    VIOLET  = Color(0x9966CC)
    BLUE    = Color(0x6666CC)
    SKY     = Color(0x6699CC)
    TEAL    = Color(0x66CCCC)
    AQUA    = Color(0x66CC99)
    GREEN   = Color(0x66CC66)
    LIME    = Color(0x99CC66)
    YELLOW  = Color(0xCCCC66)
    ORANGE  = Color(0xCC9966)


    class Level:
        def __init__(self, level):
            self.level = level

        def __enter__(self):
            self.old_log_level = Log.log_level_in
            Log.log_level_in = self
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            Log.log_level_in = self.old_log_level
            return False

        def __bool__(self):
            return self.level <= Log.log_level_out

    FATAL    = Level(0 ) # Fatal always beats quiet
    QUIET    = Level(10)
    CRITICAL = Level(20) # Should critical/error beat quiet?
    ERROR    = Level(30)
    WARNING  = Level(40)
    NORMAL   = Level(50)
    VERBOSE  = Level(60)
    DEBUG    = Level(70)

    levels = {
        "FATAL": FATAL,
        "QUIET": QUIET,
        "CRITICAL": CRITICAL,
        "ERROR": ERROR,
        "WARNING": WARNING,
        "NORMAL": NORMAL,
        "VERBOSE": VERBOSE,
        "DEBUG": DEBUG,
    }

    config : Dict = Utils.MISSING
    con_w         = 80
    time_origin   = time.perf_counter()
    indent_stack  = []
    current_color = -1
    line_buffer   = ""
    match_escapes = re.compile(r"(\x1B.*?m)")
    log_level_in  = NORMAL
    log_level_out = NORMAL # log level we want to appear in the log

    @classmethod
    def reset(cls, env_log : Dict):
        cls.config        = env_log
        cls.con_w         = shutil.get_terminal_size().columns
        cls.time_origin   = time.perf_counter()
        cls.indent_stack  = []
        cls.current_color = -1
        cls.line_buffer   = ""
        cls.match_escapes = re.compile(r"(\x1B.*?m)")

        level = cls.config['level']

        if level is not None:
            if isinstance(level, int):
                cls.log_level = Log.Level(level)
            else:
                raise ValueError(f"Got an unknown log level '{type(level)} = {level}'")

        # The individual -T/-D/-V/-Q flags override --log_level, with the 'loudest' flag winning.

        if cls.config['debug']:
            cls.config['level'] = Log.DEBUG.level
        elif cls.config['verbose']:
            cls.config['level'] = Log.VERBOSE.level
        elif cls.config['quiet']:
            cls.config['level'] = Log.QUIET.level

        cls.log_level_in  = cls.Level(cls.config['level'])
        cls.log_level_out = cls.Level(cls.config['level'])

    @classmethod
    def indent(cls, color = 0):
        ansi = cls.hex_to_ansi(color) if cls.config['color'] else ""
        cls.indent_stack.append(ansi + "│ " + cls.reset_color())

    @classmethod
    def dedent(cls):
        if not cls.indent_stack:
            pass
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
        if cls.current_color != 0 and cls.config['color']:
            return "\x1B[0m"
        else:
            return ""

    @classmethod
    def log(cls, text):
        if not isinstance(text, str) or len(text) == 0:
            return

        if cls.log_level_in.level > cls.log_level_out.level:
            return

        if cls.current_color >= 0 and cls.config['color']:
            hex = cls.current_color
            color_prefix = cls.hex_to_ansi(hex) if cls.config['color'] else ""
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

            if not cls.config['wrap']:
                cls.line_buffer = cls.clip_printable(cls.line_buffer, cls.con_w)

            assert cls.log_level_in is not None

            if cls.log_level_in.level <= cls.log_level_out.level:
                sys.stdout.write(cls.line_buffer)

            cls.line_buffer = ""

    @classmethod
    def log_exception(cls, ex):
        tb = traceback.extract_tb(ex.__traceback__)
        if tb:
            frame = tb[-1]
            cls.log("  text = ")
            with cls.Color(0xFFFF00):
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
        return f"[{time.perf_counter() - cls.time_origin:8.3f}] " if cls.config['time'] else ""

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

    startswith2 = Utils.tree_all(lambda p, parent : os.path.commonpath([p, parent]) == parent)

    @classmethod
    def startswith(cls, p, parent):
        try:
            result = cls.startswith2(p, parent)
        except:
            raise
        return result

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

        if not os.path.isabs(lhs):
            lhs = os.path.abspath(lhs)
        if not os.path.isabs(rhs):
            rhs = os.path.abspath(rhs)

        prefix = ""
        try:
            prefix = os.path.commonpath([lhs, rhs])
        except Exception:
            with Log.ERROR, Log.RED:
                Log.log(f"Commonpath failed for '{lhs}' and '{rhs}'\n")
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

class ContextProxy[T]:
    # Helper that just wraps CVs so you can do "contextvar.foo".
    def __init__(self, name, default):
        self._cv : contextvars.ContextVar[T]
        object.__setattr__(self, "_cv", contextvars.ContextVar(name, default = default))

    def __getattr__(self, key) -> Any:
        return getattr(self._cv.get(), key)

    def __setattr__(self, key, val):
        setattr(self._cv.get(), key, val)

    def get(self) -> T:
        return self._cv.get()

    def set(self, val : T) -> contextvars.Token[T]:
        return self._cv.set(val)

    def reset(self, token : contextvars.Token[T]):
        return self._cv.reset(token)

    @contextmanager
    def enter(self, val : T):
        token = self.set(val)
        yield
        self.reset(token)

# ==================================================================================================
# region Dict merging

def generic_merge(
    dst: Dict,
    lhs: dict | Dict,
    rhs: dict | Dict,
    merge_dicts: bool,
    merge_lists: bool,
    keep_a: bool,
    keep_b: bool,
):
    assert type(dst) is Dict

    keys = list(lhs) + [r for r in rhs if r not in lhs]
    #keys = lhs.keys() | rhs.keys()
    #keys = {*lhs, *rhs}

    for key in keys:
        if key in lhs and key not in rhs and not keep_a:
            continue
        if key not in lhs and key in rhs and not keep_b:
            continue

        lhs2 = lhs.get(key)
        rhs2 = rhs.get(key)

        if isinstance(lhs2, (dict, Dict)) and isinstance(rhs2, (dict, Dict)) and merge_dicts:
            dst2 = Dict()
            generic_merge(dst2, lhs2, rhs2, merge_dicts, merge_lists, keep_a, keep_b)
        elif isinstance(lhs2, list) and isinstance(rhs2, list) and merge_lists:
            dst2 = copy.deepcopy(list(lhs2) + list(rhs2))
        elif rhs2 is not None:
            dst2 = Dict(rhs2) if type(rhs2) is Dict else copy.deepcopy(rhs2)
        else:
            dst2 = Dict(lhs2) if type(lhs2) is Dict else copy.deepcopy(lhs2)

        if isinstance(dst2, Dict):
            dst2.link(dst)

        dict.__setitem__(dst, key, dst2)

    # FIXME sanity checking
    dst.check_links()

    return dst

# Fill-in-the-blank (or override what's there): Merges lhs and args into a new Dict, keeping
# only keys that were already in lhs. For example, if you have a Dict that contains
# "out_bin" and you merge it with "compile_cpp", Hancho will complain that "out_bin" is missing
# - it sees both "out_obj" and "out_bin" and assumes the task produces both. If you do
# compile_cpp.fill(...), "out_bin" does not get added to compile_cpp.

def fill(lhs : Dict, *args : Dict, **kwargs):
    dest = copy.deepcopy(lhs)
    for rhs in (*args, kwargs):
        generic_merge(
            dest, dest, rhs,
            merge_dicts=True, merge_lists=True,
            keep_a=True, keep_b=False)
    return dest

def update(lhs : Dict, *args : dict, **kwargs):
    for rhs in [d for d in (*args, kwargs) if d]:
        generic_merge(
            lhs, lhs, rhs,
            merge_dicts=True, merge_lists=True,
            keep_a=True, keep_b=True)
    return lhs

def merge(*args : Dict, **kwargs):
    result = update(Dict(), *args, **kwargs)
    return result

def _walk(lhs : Dict, key : str, spawn = False):
    while True:
        key, _, rest = key.partition('.')
        if not rest:
            return (lhs, key)
        if key in lhs:
            key, lhs = rest, dict.__getitem__(lhs, key)
        elif spawn:
            dest = Dict()
            dict.__setitem__(lhs, key, dest)
            key, lhs = rest, dest
        else:
            raise KeyError(key)
# endregion
# ==================================================================================================

def parse_flags(argv, *args, **kwargs) -> Dict:

    desc = textwrap.dedent("""
    ================================================================================
                    Hancho is a simple, pleasant build system
    ================================================================================
    """)

    parser = argparse.ArgumentParser(
        description=desc,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    bool_opt = argparse.BooleanOptionalAction

    # ------------------------------------
    # fmt: off

    parser.add_argument(      "--hancho.root",        type=str.strip,     help="Hancho lives in this directory (so we can find hancho/tools, etc).")
    parser.add_argument('-o', "--hancho.opt_file",    type=str.strip,     help="File containing JSON that will be used as additional options")
    parser.add_argument(      "--hancho.run_tool",    type=str.strip,     help="Run a subtool.")
    parser.add_argument(      "--hancho.depformat",   type=str.strip,     help="Dependency file format (gcc or msvc)")
    parser.add_argument(      "--hancho.max_errors",  type=int,           help="The maximum number of task errors we tolerate before abandoning the build")
    parser.add_argument('-j', "--hancho.max_jobs",    type=int,           help="Run a maximum of N jobs in parallel.")

    levels = Log.levels.keys()

    parser.add_argument(      "--log.level",          choices = levels,   help="Manually select verbosity level. 'quiet' = none, 'trace' = maximal spam")
    parser.add_argument('-Q', "--log.quiet",          action = bool_opt,  help="(same as --log_level=quiet)")
    parser.add_argument('-V', "--log.verbose",        action = bool_opt,  help="(same as --log_level=verbose)")
    parser.add_argument('-D', "--log.debug",          action = bool_opt,  help="(same as --log_level=debug)")
    parser.add_argument('-T', "--log.trace",          action = bool_opt,  help="(same as --log_level=trace)")
    parser.add_argument('-w', "--log.wrap",           action = bool_opt,  help="Wrap lines around the console instead of clipping them")
    parser.add_argument('-c', "--log.color",          action = bool_opt,  help="Use color in the log for better readability")
    parser.add_argument(      "--log.time",           action = bool_opt,  help="Timestamp each log line")

    parser.add_argument(      "--repo.root",          type=str.strip,     help="The top repo lives in this directory.")
    parser.add_argument(      "--repo.build",         type=str.strip,     help="Build artifacts go in this directory.")
    parser.add_argument(      "--repo.tag",           type=str.strip,     help="Tagged builds will have separate subdirectories under the build directory.")
    parser.add_argument('-t', "--repo.target",        type=str.strip,     help="A regex that selects a subset of targets to build.")
    parser.add_argument(      "--repo.force",         action = bool_opt,  help="Rebuild targets even if they're clean.")
    parser.add_argument(      "--repo.all",           action = bool_opt,  help="Build every task in every repo.")
    parser.add_argument(      "--repo.dry_run",       action = bool_opt,  help="Dry run - Do everything except actually run commands.")
    parser.add_argument(      "--repo.strict",        action = bool_opt,  help="Strict mode, slightly more error checking to catch footguns.")

    parser.add_argument(      "--script.path",        type=str.strip,     help="Path to the .hancho file that starts the build.")
    parser.add_argument(      "--script.root",        type=str.strip,     help="The top script runs in this directory.")
    # fmt: on

    (argv_vars, unrecognized) = parser.parse_known_args(argv if argv else [])
    argv_vars = vars(argv_vars)


    def get_by_path(rhs : Dict, key : str):
        dest, key = _walk(rhs, key, spawn = False)
        return dest[key]

    def set_by_path(lhs : Dict, key : str, val : Any):
        dest, key = _walk(lhs, key, spawn = True)
        dest[key] = val

    argv_flags = Dict()
    for k, v in argv_vars.items():
        if v is not None:
            set_by_path(argv_flags, k, v)

    # ------------------------------------
    # Load flags from opt_file if present

    opt_file = argv_flags.pop("opt_file", None)
    if opt_file:
        #with Log.GREEN:
        #    Log.log(f"Loading options file {opt_file!r}\n")
        if os.path.exists(opt_file):
            with open(opt_file) as f:
                try:
                    opts = json.load(f)
                    argv_flags.update(opts)
                except Exception as _:
                    #with Log.RED:
                    #    Log.log(f"Opt file {opt_file!r} invalid!\n")
                    pass
        else:
            #with Log.RED:
            #    Log.log(f"Opt file {opt_file!r} not found!\n")
            pass

    # ------------------------------------
    # Unrecognized command line flags also become config fields if they are flag-like.
    # Naked flags become {'name':True}, number types become numbers, 'true' and 'false'
    # become bools (regardless of capitalization), everything else becomes a string.

    mystery_flags = Dict()
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
    merge(flags, argv_flags)

    for k, v in mystery_flags.items():
        set_by_path(flags, k, v)

    merge(flags, *args, Dict(kwargs))

    return flags

# ==================================================================================================



























class Dict(dict):
    """
    This class extends 'dict' in a couple ways -
    1. Dict supports "foo.bar" attribute access in addition to "foo['bar']"
    2. Dict supports "merging" instances by passing them (and any additional key-value pairs) in via the constructor.
    3. When merging Dicts, the rightmost not-None value of an attribute will be kept.
    4. If two attributes have the same name:
        If they are both dicts, we recursively merge them.
        If they are both lists, we concatenate them.
        Otherwise rightmost non-None wins.
    5. Dict's constructor makes copies of all basic container types (collections and mappings) in
    its inputs. I can't guarantee that everything you might put in a Dict will be deep-copied, but
    it should be close enough.

    FIXME - This _must_ have an _up pointer, otherwise we can't expand things like "hancho.somefunction(var_on_task)"
    because 'hancho' doesn't resolve inside task and 'var_on_task' doesn't resolve at the top level of the env

    """

    def __init__(self, *args : dict[str, Any] | Dict, **kwargs : Any):
        #assert all(isinstance(d, dict) for d in args)

        self._up : Dict | None
        object.__setattr__(self, "_up", None)

        dicts = [d for d in (*args, kwargs) if d]
        update(self, *dicts)
        self.check_links()

    # ==============================================================================================

    def link(self, up : Dict):
        object.__setattr__(self, "_up", up)

    def check_links(self):
        for v in self.values():
            if isinstance(v, Dict):
                if object.__getattribute__(v, "_up") != self:
                    raise AssertionError(f"Child dict not linked to parent - {v} -> {self}")
                v.check_links()

    # ==============================================================================================
    # region Dunders

    def __copy__(self):
        return copy.deepcopy(self)

    def __repr__(self):
        return Dumper.dump(self)

    def __dump__(self, key, opts, seen):
        prefix = ""
        if key:
            prefix += f"{key}"
            prefix += ": "
        prefix += f"Dict@{Utils.hex_id(self)}"
        if prefix:
            prefix += " = "

        if id(self) in seen:
            return prefix + "<ref loop>"
        seen.add(id(self))

        items = list(self.items())
        return Dumper._dump_items(key, prefix, "{", items, "}", opts, set(seen))

    # endregion
    # ==============================================================================================
    # region MutableMapping interface

    def __getitem__(self, key: str) -> Any:
        return self._get(key)

    def __setitem__(self, key: str, val: Any):
        return self._set(key, val)

    def __delitem__(self, key: str):
        return self._del(key)

    # endregion
    # ==============================================================================================
    # region Attribute interface

    def __getattr__(self, key: str) -> Any:
        try:
            return self._get(key)
        except KeyError as err:
            raise AttributeError from err

    def __setattr__(self, key: str, val: Any):
        try:
            return self._set(key, val)
        except KeyError as err:
            raise AttributeError from err

    def __delattr__(self, key: str):
        try:
            return self._del(key)
        except KeyError as err:
            raise AttributeError from err

    # endregion
    # ==============================================================================================

    def _get(self, key, default = Utils.MISSING):
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        elif default is not Utils.MISSING:
            return default
        else:
            raise KeyError(key)

    def _set(self, key, val):
        dest, key = _walk(self, key, spawn = True)
        dict.__setitem__(dest, key, val)
        if isinstance(val, Dict):
            val.link(self)

    def _del(self, key):
        dest, key = _walk(self, key, spawn = False)
        dict.__delitem__(dest, key)



class Tool(Dict):
    # Tool is just an alias for Dict to make build scripts more readable.
    pass

class Data(Dict):
    # Same thing
    pass

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

    #region instance methods

    def __init__(self, env : Dict):
        env.check_links()
        self.env : Dict
        object.__setattr__(self, "env", env)

    def __getattr__(self, key) -> Any:
        try:
            return self._get(key, check_up = False)
        except KeyError as ex:
            raise AttributeError from ex

    def __setattr__(self, key, val):
        self.env.__setattr__(key, val)

    def __delattr__(self, key):
        self.env.__delattr__(key)

    def __getitem__(self, key : str) -> Any:
        return self._get(key, check_up = True)

    def __setitem__(self, key, val):
        self.env.__setitem__(key, val)

    def __delitem__(self, key):
        self.env.__delitem__(key)

    def __iter__(self):
        return self.env.__iter__()

    def __len__(self):
        return self.env.__len__()

    def _get(self, key : str, check_up : bool):
        result = Utils.MISSING
        env = object.__getattribute__(self, "env")

        try:
            trace_start(env, "get", key)

            if key in env:
                result = env[key]
                if isinstance(result, Dict):
                    result = Expander(env = result)
                else:
                    assert type(result) is not dict
                    result = Expander.expand(result, env)

            elif check_up:
                up = object.__getattribute__(env, "_up")
                if up:
                    trace_up(env, up)
                    result = Expander(up)._get(key, check_up)

                else:
                    raise KeyError(key)
            else:
                raise KeyError(key)

            return result

        finally:
            trace_end(env, key, result)
            pass


    #endregion

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

    @staticmethod
    def is_terminal(var):
        pass

    @classmethod
    def expand(cls, var : Any, env : Dict | Expander) -> Any:
        old_depth = Expander.cv_depth.get()

        # Bail out if we've recursed too many times.
        if old_depth > Expander.MAX_DEPTH:
            raise RecursionError(f"Expansion failed to terminate after {old_depth} recursions: {var!r}")
        Expander.cv_depth.set(old_depth + 1)

        if isinstance(env, Dict):
            env.check_links()
            env = Expander(env = env)

        try:
            while True:
                if var is Utils.MISSING:
                    raise AssertionError("Tried to expand a sentinel value")

                if isinstance(var, (bytes, bytearray)):
                    return var

                if isinstance(var, abc.Mapping):
                    if isinstance(var, Expander):
                        var = var.env
                    #raise AssertionError("Do we really want to support expanding mappings?")
                    old_evals = Expander.cv_evals.get()
                    var = type(var)(**{k: Expander.expand(v, env) for k, v in var.items()})
                    Expander.cv_evals.set(old_evals)
                    return var

                if isinstance(var, abc.Collection) and not isinstance(var, str):
                    old_evals = Expander.cv_evals.get()
                    # the linter doesn't like this, but we know collection constructors can accept a
                    # generator
                    var = type(var)(Expander.expand(v, env) for v in var) # type: ignore
                    Expander.cv_evals.set(old_evals)
                    return var

                if not isinstance(var, str):
                    # If we don't know what to do with this type, just return it.
                    return var

                # Ok, we know 'var' is a string, split it.
                blocks = Expander.Blocks()
                blocks.clear()
                Expander._split_text(var, blocks)

                if len(blocks) == 0:
                    return var
                if len(blocks) == 1:
                    if isinstance(blocks[0], Expander.Literal):
                        break

                    if isinstance(blocks[0], Expander.Macro):
                        old_var = var
                        trace_start(env.env, "eval", var)
                        var = Expander._eval_macro(blocks[0], env) # type: ignore
                        trace_end(env.env, old_var, var)
                        if old_var == var:
                            break
                else:
                    old_var = None
                    try:
                        trace_start(env.env, "expand", var)
                        for i, b in enumerate(blocks):
                            if isinstance(b, Expander.Macro):
                                blocks[i] = Expander._eval_macro(b, env)
                        old_var, var = var, "".join(Utils.stringify(b) for b in blocks)
                    finally:
                        trace_end(env.env, old_var, var)
                        pass
                    if old_var == var:
                        break

        finally:
            Expander.cv_depth.set(old_depth)
            if old_depth == 0:
                # We just finished an expansion - reset the eval budget
                Expander.cv_evals.set(0)

        return var

    # ==============================================================================================
    # Note that we do _not_ suppress any BaseExceptions - they _must_ be propagated up to
    # callers. As of Python 3.11, this includes asyncio.CancelledError.

    # IMPORTANT IMPORTANT IMPORTANT
    # If you can't eval a macro, you return it unchanged.

    # TEFINAE : Template Expansion Failure Is Not An Error. Same idea as SFINAE in C++
    # - we don't fail on expansion failure so we can retry somewhere/somewhen else.


    @classmethod
    def _eval_macro(cls, var : Expander.Macro, env : Expander) -> Any:
        # Bail out if we've done too many evals already.
        old_evals = Expander.cv_evals.get()
        if old_evals >= Expander.MAX_EVALS:
            raise RecursionError(f"Expansion failed to terminate after {old_evals} evals: '{var!r}'")
        Expander.cv_evals.set(old_evals + 1)

        try:
            new_var = eval(var[1:-1], hancho_aliases, env)
            return new_var
        except RecursionError:
            raise
        except Exception as ex:
            Log.log(f"eval failed because >{ex}<\n")
            return var
        except BaseException:
            raise

    # ==============================================================================================

    @classmethod
    def _split_text(cls, text : str, out_blocks : Blocks):
        """
        Extracts all innermost delimited spans from a block of text and produces a list of string
        literals and macros. Note that we're not handling "escaped" delimiters, instead we allow
        the user to change the delimiter when required (default delimiters are {} and «»)
        """

        ldelim = '«' if '«' in text else '{'
        rdelim = '»' if '»' in text else '}'
        cursor = 0
        idelim = -1
        macros = 0

        for i, c in enumerate(text):
            if c == ldelim:
                idelim = i
            elif c == rdelim and idelim >= 0:
                if cursor < idelim:
                    out_blocks.append(Expander.Literal(text[cursor:idelim]))
                out_blocks.append(Expander.Macro(text[idelim:i+1]))
                macros += 1
                cursor = i + 1
                idelim = -1

        if cursor < len(text):
            out_blocks.append(Expander.Literal(text[cursor:]))

    # ==============================================================================================

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
        #list,
        #tuple,
        #set,
        #dict,
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
        indent : int = 0
        fold : list[str] = dataclasses.field(default_factory=list)  # Any container fields with these names will _not_ be recursively dumped
        print_id : bool = True
        color_code : bool = True
        tab : str = "    "
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
        indent=0,
        fold = None,
        print_id=True,
        color_code=False,
        width=80,
        len=0,
        tab="    ",
    ):
        opts = Dumper.Opts(depth, indent, fold or [], print_id, color_code, tab, len, width, flat = False)
        return cls._dump_to_str(None, val, opts, set())

    @classmethod
    def print(cls, *args, **kwargs):
        result = cls.dump(*args, **kwargs)
        print(result)

    @classmethod
    def _dump_to_str(cls, key, val : Any, opts, seen : set):
        if key == "hancho":
            pass

        if key == "__builtins__":
            return cls._dump_prefix(key, val, opts) + "<builtins>"
        elif hasattr(type(val), "__dump__"):
            return val.__dump__(key, opts, seen)
        elif inspect.isroutine(val) or inspect.isclass(val) or inspect.ismodule(val) or isinstance(val, Enum):  # noqa: SIM114
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

        return cls._dump_items(key, prefix, ld, items, rd, opts, set(seen))

    @classmethod
    def _dump_items(cls, key, prefix, ld, items, rd, opts, seen : set):
        # This slightly odd construct is so that when a deeply nested container doesn't fit on a
        # line, we rewind the callstack back to the topmost container that was not forced to be
        # flat.

        if key in opts.fold:
            return prefix + ld + "<folded>" + rd

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

        # len(pad) + 1 for the trailing comma
        pad = opts.tab * (opts.indent + 1)
        new_opts = dataclasses.replace(opts, len = len(pad) + 1, indent = opts.indent + 1)

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

def trace_start(env, action, arg):
    if not Log.config['trace']:
        return

    try:
        env_color = Utils.obj_to_hex(env)

        with Log.Color(env_color):
            Log.log(f"┌ {Utils.instance_tag(env)}")
        Log.log(f".{action}({arg!r})\n")
        Log.indent(env_color)
        pass
    except:
        raise

def trace_up(env, up):
    if not Log.config['trace']:
        return

    env_color = Utils.obj_to_hex(env)
    try:
        Log.dedent()
        with Log.Color(env_color):
            Log.log(f"├ {Utils.instance_tag(env)}")
        Log.log(" -> ")
        with Log.Color(Utils.obj_to_hex(up)):
            Log.log(f"{Utils.instance_tag(up)}\n")
    finally:
        Log.indent(env_color)

def trace_end(env, arg, result):
    if not Log.config['trace']:
        return

    try:
        Log.dedent()

        if isinstance(result, Expander):
            result = result.env

        env_color = Utils.obj_to_hex(env)
        result_color = 0
        result_type = type(result)
        if isinstance(result, (dict|Dict|Expander)):
            result_color = Utils.obj_to_hex(result)
            result = Utils.instance_tag(result)
        with Log.Color(env_color):
            Log.log("└ ")
        Log.log(f"{arg!r} : ")
        Log.log(f"{result_type.__name__} = ")
        with Log.Color(result_color):
            Log.log(f"{result!r}\n")
        pass
    except:
        raise

class Runner:

    @classmethod
    def reset(cls, max_jobs, max_errors):
        cls.max_jobs   = max_jobs
        cls.max_errors = max_errors

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

class Hancho:
    # Just a container for global stuff.

    real_filenames : set[str] = set()
    dedupe : Dict = Dict()
    repos : set[Repo] = set()

    @classmethod
    def init(cls, top_env):
        cls.real_filenames = set()
        cls.dedupe = Dict()
        cls.repos : set[Repo] = set()

        Log.reset(top_env.log)
        Utils.reset()
        Runner.reset(top_env.hancho.max_jobs, top_env.hancho.max_errors)

class Repo:
    def __init__(self, env : Dict):
        self._root  : str        = Expander.expand("{repo.root}", env)
        self._build_dir : str    = Expander.expand("{repo.build_dir}", env)
        self._build_tag  : str   = Expander.expand("{repo.build_tag}", env)
        self._target  : str      = Expander.expand("{repo.target}", env)
        self._build_force : bool = Expander.expand("{repo.build_force}", env)
        self._build_all : bool   = Expander.expand("{repo.build_all}", env)
        self._dry_run : bool     = Expander.expand("{repo.dry_run}", env)
        self._strict : bool      = Expander.expand("{repo.strict}", env)

        self.stat_db = {}
        self.build_reasons = Counter()
        self.root_script : Script = Utils.MISSING
        self.scripts = []

    def yield_tasks(self) -> abc.Iterator[Task]:
        for script in self.scripts:
            yield from script.yield_tasks()

def load_stat_db(repo : Repo):
    stat_db_path = os.path.join(repo._build_dir, 'hancho.json')

    if os.path.isfile(stat_db_path):
        with open(stat_db_path) as contents:
            with Log.VERBOSE, Log.ORANGE:
                Log.log(f"Loading stat_db {stat_db_path}\n")
            repo.stat_db = json.load(contents)
    else:
        with Log.VERBOSE, Log.ORANGE:
            Log.log(f"No stat db for {repo._root}\n")
        repo.stat_db = {}

def save_stat_db(repo : Repo):
    if repo._dry_run:
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

        if task.cfg['in_depfile']:
            deplines = Utils.load_depfile(task.cfg['in_depfile'], task.cfg['depformat'], task.cfg['cwd'])
            for file in deplines:
                stat_db[file] = Utils.get_stats(file) # type: ignore

    # We gather stats from output files in a second pass so that their .command fields
    # overwrite any blank ones from the first pass.

    for task in repo.yield_tasks():
        if not task._complete:
            continue

        for file in Utils.yield_values(task.out_files):
            stat_db[file] = Utils.get_stats(file, task.cfg['command'])

    stat_db_path = Path.join(repo._build_dir, 'hancho.json')
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
                "directory" : task.cfg['cwd'],
                "command"   : Utils.commands_to_string(task.cfg['command']),
                "file"      : file,
            }

    comp_db_path = Path.join(repo._build_dir, 'compile_commands.json')
    Utils.save_json(list(comp_db.values()), comp_db_path)

# ==================================================================================================

class Script:
    def __init__(
        self,
        repo: Repo,
        script_path: str | None,
        script_root: str | None,
        module: types.ModuleType,
        script_env: Dict,
        code: types.CodeType | None,
    ):

        self._repo       = repo
        self._path       = script_path
        self._root       = script_root
        self._module     = module
        self._script_env = script_env
        self._code       = code

        self._tasks : list[Task] =  []
        self._children : list[Script] = []

    def __repr__(self):
        return Dumper.dump(self)

    def yield_tasks(self) -> abc.Iterator[Task]:
        yield from self._tasks
        for child in self._children:
            yield from child.yield_tasks()

# ==================================================================================================

class Task:
    class FAILED(Exception):    pass
    class CANCELLED(Exception): pass
    class SKIPPED(Exception):   pass
    class BROKEN(Exception):    pass

    def __init__(self, repo : Repo, script : Script, env : Dict):
        self._repo   = repo
        self._script = script
        self._env    = env
        self.cfg     = {}

        # Build scripts also may need to see the complete list of inputs/outputs to a task in
        # addition to the individual in_/out_ fields, so these are public.

        self.in_files  = {}
        self.out_files = {}

        # ------------------------------------
        # Implementation details below this line

        self._enabled = False

        # This must be populated -before- the task starts, as we need it to queue up the task's
        # dependencies
        self.input_tasks = [v for v in Utils.yield_values(self._env) if isinstance(v, Task)]

        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._aio_task : asyncio.Task | None = None

        # We remember the aio context we were in when this task was created so that we can return
        # to it when the task starts.
        # FIXME do we even need this if we store our cv's in task and set them in task_top?
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

# This _must_ be a real 'dict' so we can pass it into the 'globals' arg to eval()
hancho_aliases = Dict(
    log      = Log.log,
    dump     = Dumper.print,

    Utils    = Utils,
    flatten  = Utils.flatten,
    run_cmd  = Utils.run_cmd,
    weave    = Utils.weave,

    Path     = Path,
    abspath  = Path.abspath,
    normpath = Path.normpath,
    basename = Path.basename,
    dirname  = Path.dirname,
    join     = Path.join,
    relpath  = Path.relpath,
    resolve  = Path.resolve,
    swapext  = Path.swapext,
)

# ==================================================================================================

hancho_defaults = Dict(
    hancho = Dict(
        root       = os.path.dirname(__file__),
        opt_file   = '',
        run_tool   = '',
        depformat  = "gcc" if os.name == "posix" else "msvc",
        max_errors = 0,
        max_jobs   = os.cpu_count() or 1
    ),
    log = Dict(
        level   = 50,
        quiet   = False,
        verbose = False,
        debug   = False,
        trace   = True, #False,
        wrap    = False,
        color   = True,
        time    = True
    ),
    repo = Dict(
        root        = '{dirname(script.path)}',
        build_dir   = "{join(root, 'build', build_tag)}",
        build_tag   = '',
        target      = '',
        build_force = False,
        build_all   = False,
        dry_run     = False,
        strict      = True
    ),
    script = Dict(
        path    = os.path.abspath("build.hancho"),
        root    = '{dirname(path)}',
        is_repo = True
    ),
    task = Dict(
        name       = '<no name>',
        desc       = '<no desc>',
        command    = 'echo {name} : {desc}',
        cwd        = '{repo.root}',
        in_depfile = '',
        depformat  = 'gcc',
        job_size   = 1,
        build_dir  = '{join(repo.build_dir, relpath(script.root, repo.root))}',
        dry_run    = '{repo.dry_run}',
        force      = '{repo.build_force}',
    ),
)

# ==================================================================================================

class HanchoProxy(types.ModuleType):

    def __init__(self, repo : Repo, script : Script, env : Dict):
        super().__init__("hancho_proxy")
        self._repo   = repo
        self._script = script
        self._env    = env

    def __getattr__(self, key):
        # Delegate to hancho_aliases so we don't have to duplicate it.
        return getattr(hancho_aliases, key)

    Dict = Dict
    Expander = Expander
    Tool = Tool

    def Task(self, *args, **kwargs):

        task_env = copy.deepcopy(self._env)

        update(task_env['task'], *args, Dict(kwargs))

        task = Task(repo = self._repo, script = self._script, env = task_env)
        self._script._tasks.append(task)

        # Auto-start the task if it was created dynamically during the build.
        if Utils.in_event_loop():
            queue_task(task)

        return task

    def load(self, path, root = None, *args, **kwargs) -> types.ModuleType:
        return load_script(self._env, self._repo, path, root, *args, **kwargs)._script._module

    def repo(self, path, root = None, *args, **kwargs) -> types.ModuleType:
        return load_script(self._env, None, path, root, *args, **kwargs)._script._module

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
        with Log.ERROR, Log.RED:
            Log.log(f"Script {condition}:\n")
            Log.log(f"  text = '{message}'\n")
            Log.log(f"  file = {frame.f_code.co_filename}\n")
            Log.log(f"  func = {frame.f_code.co_name}\n")
            Log.log(f"  line = {frame.f_lineno}\n")

    def build(self) -> int:
        return hancho_build(self._repo)

    @staticmethod
    def init_for_testing(argv : list[str], *args, **kwargs):
        return init_lib(argv, *args, **kwargs)

# ==================================================================================================

def _start():

    # Top-level exception handler just so we can print a big red "SOMETHING BROKE" message if
    # we failed to catch an exception during load/build. The 'except' clause should catch
    # Exception and not BaseException so ctrl-c doesn't get misinterpreted as a Hancho bug.
    try:
        if __name__ == "__main__":
            sys.exit(hancho_main())
        else:
            sys.modules["hancho"] = init_lib(sys.argv)

    except Exception:
        print(Log.hex_to_ansi(0xFF3030), end="")
        print("Hancho hit an unhandled exception:")
        traceback.print_exc()
        print("\x1B[0m", end="")
        sys.exit(1)

    finally:
        # Don't leave the last line of the log sitting in line_buffer!
        Log.flush()

# ==================================================================================================

def init_lib(argv, *args, **kwargs) -> HanchoProxy:
    flags = parse_flags(argv, *args, **kwargs)
    top_env = merge(hancho_defaults, flags)
    top_env.link(hancho_aliases)
    Hancho.init(top_env)
    root_proxy = load_script(top_env, None, None, None)
    return root_proxy

# ==================================================================================================

def load_script(old_env : Dict, parent_repo : Repo | None, path : str | None, root : str | None, *args, **kwargs) -> HanchoProxy:

    if path:
        root = root or "{script.root}"
        path = Expander.expand(path, old_env)
        root = Expander.expand(root, old_env)

        path = Path.resolve(path)
        root = Path.resolve(root)
    else:
        assert root is None

    with Log.VERBOSE, Log.ORANGE:
        Log.log(f"Loading {"repo" if not parent_repo else "script"} {path}\n")

    new_env = copy.deepcopy(old_env)
    update(new_env['script'], *args, Dict(kwargs), path = path, root = root)

    # Dedupe the load - only scripts with identical real paths and identical configs are
    # deduped. This relies on __repr__ and the fields read by Dumper.dump being stable during a
    # build, which they should be in practice.
    dupe_key = Dumper.dump(new_env, print_id = False, tab = "", color_code = False, depth = 999, width = 999)
    dupe_key = Dumper.depointer(dupe_key)
    dupe_key = "".join(dupe_key.split())

    if dupe := Hancho.dedupe.get(dupe_key, None):
        return dupe

    if path:
        with open(path, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, path, "exec", dont_inherit=True)
    else:
        code = None

    repo   = parent_repo or Repo(new_env)
    module = types.ModuleType(os.path.basename(path) if path else "<no path>")
    script = Script(repo, path, root, module, new_env, code)
    proxy  = HanchoProxy(repo, script, new_env)

    module.__file__ = path
    module.hancho   = proxy   # type: ignore
    module.env      = new_env     # type: ignore

    Hancho.dedupe[dupe_key] = proxy
    Hancho.repos.add(proxy._repo)

    repo.scripts.append(script)
    if not repo.root_script:
        repo.root_script = script

    # Run the script
    if code and root:
        with chdir(root):
            try:
                Log.indent(Log.ORANGE.color)
                sys.modules["hancho"] = proxy
                exec(code, module.__dict__, {})
            finally:
                Log.dedent()

    return proxy

# ==================================================================================================

def hancho_main() -> int:

    flags = parse_flags(sys.argv)
    top_env = merge(hancho_defaults, flags)
    top_env.link(hancho_aliases)

    Hancho.init(top_env)

    with Log.VERBOSE, Log.LIME:
        Log.log(f"Command line : {" ".join(sys.argv)}\n")
    if Log.config['trace']:
        Log.log("Trace mode on\n")
    if Log.log_level_out.level >= Log.DEBUG.level:
        Log.log("Debug mode on\n")
    if Log.log_level_out.level >= Log.VERBOSE.level:
        Log.log("Verbose mode on\n")

    # ------------------------------------
    # Load and exec top script

    time_a1 = time.perf_counter()
    try:
        Log.indent(Log.ORANGE.color)

        top_repo  = Repo(top_env)
        top_proxy = load_script(top_env, top_repo, "{script.path}", "{script.root}")
    finally:
        Log.dedent()
    time_b1 = time.perf_counter()
    with Log.VERBOSE, Log.BLUE:
        Log.log(f"Loading scripts took {time_b1 - time_a1:8.6f} seconds\n")

    # ------------------------------------
    # If we're running a tool, run it and we're done.

    tool = top_env['hancho']['run_tool']

    if tool:
        time_a2 = time.perf_counter()
        result = run_tool(tool)
        time_b2 = time.perf_counter()

        with Log.VERBOSE, Log.GREEN:
            Log.log(f"Tool took {time_b2 - time_a2:8.6f} seconds\n")
        return result

    # ------------------------------------
    # Start the build

    time_a3 = time.perf_counter()
    result = hancho_build(top_proxy._repo)
    time_b3 = time.perf_counter()
    with Log.VERBOSE, Log.GREEN:
        Log.log(f"Build took {time_b3 - time_a3:8.6f} seconds\n")

    # ------------------------------------
    # Done

    task_count = 0
    for repo in Hancho.repos:
        task_count += len(list(repo.yield_tasks()))

    with Log.VERBOSE:
        Log.log(f"Tasks created:    {task_count}\n")

        Log.log(f"Tasks enabled:    {Runner.tasks_enabled}\n")
        Log.log(f"Tasks started:    {Runner.tasks_started}\n")
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
        with Log.ERROR, Log.RED:
            Log.log("BUILD FAILED\n")
    elif Runner.tasks_finished:
        with Log.GREEN:
            Log.log("BUILD PASSED\n")
    else:
        with Log.BLUE:
            Log.log("BUILD CLEAN\n")

    #with Log.DEBUG, Log.BLUE:
    #    for repo in cls.repos.items():
    #        Log.log(f"Stats for {script.repo_root}\n")
    #        Log.indent(Log.BLUE)
    #        for k, v in script.reasons.items():
    #            Log.log(f"Rebuild reasons {k:13} = {v}\n")
    #        Log.dedent()

    return result

# ==================================================================================================

def hancho_build(top_repo : Repo) -> int:

#    for repo in Hancho.repos:
#        Log.log(f"Repo {repo._root}\n")
#        Log.indent()
#        for script in repo.scripts:
#            Log.log(f"Script {script.script_config.path}\n")
#        Log.dedent()

    # ------------------------------------
    # Sanity-check the repo/script hierarchies

    for repo in Hancho.repos:
        assert repo.root_script in repo.scripts
        for script in repo.scripts:
            assert script._repo == repo

    # ------------------------------------
    # This must happen _after_ all repos are loaded (so that if they change repo_root we don't
    # get the old path), but _before_ we build any tasks.

    # Also this is here and not in hancho_main because tests also need to load stats.
    for repo in Hancho.repos:
        load_stat_db(repo)

    # ------------------------------------
    # Select the set of tasks to run.

    if top_repo._target:
        # Enable all tasks whose name matches the target regex
        # NOTE - We match task.task_params.name, _not_ the expanded task._name.
        # This is because the task _has not initialized yet_, so we have no config.name.
        target_regex = re.compile(top_repo._target)

        for repo in Hancho.repos:
            for task in repo.yield_tasks():
                if target_regex.search(task._env['task']['name']):
                    queue_task(task)

    elif top_repo._build_all:
        for repo in Hancho.repos:
            for task in repo.yield_tasks():
                queue_task(task)

    else:
        # Enable all tasks in the top repo
        for task in top_repo.yield_tasks():
            queue_task(task)

    # ------------------------------------
    # Run the tasks.

    result = asyncio.run(async_run_tasks())

    # ------------------------------------
    # Update stat DBs.

    for repo in Hancho.repos:
        save_stat_db(repo)

    return result

# ==================================================================================================

def create_aio_task(task : Task):
    assert Utils.in_event_loop()

    if task._aio_task is None:
        t = asyncio.create_task(task_top(task), context=task._aio_context)
        t.hancho_task = task # type: ignore
        Runner.live_aio_tasks.add(t)
        t.add_done_callback(lambda t: Runner.aio_done_queue.put_nowait(t))
        task._aio_task = t

# ==================================================================================================

def queue_task(task : Task):
    if not task._enabled:
        Runner.tasks_enabled += 1
        task._enabled = True

    if Utils.in_event_loop():
        create_aio_task(task)

    # Start all tasks referenced by the config so we don't deadlock while waiting for them.
    for v in task.input_tasks:
        queue_task(v)

# ==================================================================================================

async def async_run_tasks():
    """Run all tasks until we run out."""

    # ------------------------------------
    # Create asyncio tasks for all enabled Hancho tasks.

    for repo in Hancho.repos:
        for task in repo.yield_tasks():
            if task._enabled:
                create_aio_task(task)

    # ------------------------------------
    # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.

    with Log.VERBOSE, Log.BLUE:
        Log.log("Running tasks...\n")

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
            with Log.DEBUG:
                Log.log(f"Weird exception {type(ex)} >{ex}< at {time.perf_counter()}\n")
                Log.log_exception(ex)
            Runner.tasks_failed += 1
        else:
            # If _none_ of the above exceptions fired, we mark the task as complete.
            finished_aio_task.hancho_task._complete = True #type:ignore
        finally:
            if finished_aio_task is not None:
                Runner.live_aio_tasks.discard(finished_aio_task)

    failures = Runner.tasks_broken + Runner.tasks_failed
    if failures > Runner.max_errors:
        with Log.ERROR:
            Log.log(f"Too many failures after {failures}, cancelling tasks and stopping build\n")

        # Cancel all the asyncio.Tasks that haven't completed yet
        with Log.VERBOSE:
            Log.log(f"Cancelling {len(Runner.live_aio_tasks)} tasks\n")

        # This tasks_cancelled count may be off by one or two due to in-flight tasks not being
        # accounted for in live_aio_tasks, but it doesn't matter - we're about to bail out due
        # to failures or someone ctrl-c'ing the build, this is purely cosmetic.

        Runner.tasks_cancelled += len(Runner.live_aio_tasks)
        for t in Runner.live_aio_tasks:
            t.cancel()

        # and then wait on their cancellations to complete (it isn't instantaneous)
        await asyncio.gather(*Runner.live_aio_tasks, return_exceptions=True)

    return 1 if Runner.tasks_failed or Runner.tasks_broken else 0

# ==================================================================================================

async def task_top(task : Task):
    # Entry point for tasks, just so we can keep all the task-levle exception handling together.

    try:
        return await task_main(task)

    except asyncio.CancelledError as ex:
        with Log.VERBOSE:
            log_task(task, f"<asyncio.CancelledError {ex}>\n")
        task._error = ex

    except Task.BROKEN as ex:
        log_exception(task, "Task broken!", ex)
        task._error = ex

    except Task.FAILED as ex:
        log_exception(task, "Task failed!", ex)
        task._error = ex

    except Task.SKIPPED as ex:
        with Log.VERBOSE:
            log_task(task, str(ex) + "\n")
        task._error = ex

    except Exception as ex:
        with Log.ERROR:
            Log.log(traceback.format_exc() + "\n")
        log_exception(task, "Task threw an exception!", ex)
        task._error = ex

    finally:
        Runner.release(task._cores)

    raise task._error

# ==================================================================================================

async def task_main(task : Task):
    # Await all tasks in our input fields and then flatten them.
    await await_inputs(task)

    # We're ready to run
    Runner.tasks_started += 1
    task._task_id = Runner.tasks_started
    with Log.VERBOSE:
        log_task(task, Utils.instance_tag(task) + " starting\n")

    # Expand all mandatory fields in the raw config and fix raw file paths.
    expand_task(task)

    # If there's a depfile from a previous build, load it so we can use it below.
    if task.cfg['in_depfile']:
        task._old_deplines = Utils.load_depfile(
            task.cfg['in_depfile'], task.cfg['depformat'], task.cfg['cwd']
        )

    # Inputs are ready, templates are expanded, see if everything's sane before we try running
    # commands.
    sanity_check(task)

    # Dry runs early out after the task is initialized but before we do .exists() checks or
    # run any commands.
    if task._repo._dry_run:
        return

    # Paths updated. See if we need to rebuild our outputs.
    task._reason = rebuild_reason(task)
    if not task._reason:
        raise Task.SKIPPED(f"Task is up-to-date: '{task.cfg['name']}' : '{task.cfg['desc']}'")

    # Wait for enough jobs to free up to run this task.
    task._cores = await Runner.acquire(task.cfg['job_size'])

    # Run all the task's commands

    with Log.NORMAL, Log.TEAL:
        text  = repr(task.cfg['name']) if task.cfg['name'] else ""
        text += " : " if task.cfg['name'] and task.cfg['desc'] else ""
        text += repr(task.cfg['desc']) if task.cfg['desc'] else ""
        log_task(task, f"Task {text}\n")

    with Log.VERBOSE, Log.Color(0x606060):
        log_task(task, f"Task rebuilding because: {task._reason}\n")

    time_a = time.perf_counter()

    flat_commands = Utils.flatten(task.cfg['command'])
    for command in flat_commands:
        if command is None:
            continue
        elif callable(command):
            await call_callback(task, command)
        else:
            await run_command(task, command)

    time_b = time.perf_counter()

    with Log.VERBOSE, Log.Color(0x606060):
        message  = f"Task took {time_b-time_a:8.6f} sec: {text}\n"
        log_task(task, message)

    # See if the task wrote all its output files

    for file in Utils.yield_values(task.out_files):
        if not os.path.exists(file):
            raise Task.FAILED(f"Task ran, but output file still missing: {file}")

    # And we're done
    return task.out_files

# ==================================================================================================

async def await_inputs(task : Task):
    # NOTE: Hancho _cannot_ have dependency cycles unless you do something really sketchy via
    # modifying tasks after they're created but before they're started. If you point task B's
    # inputs at task A and task A's inputs at task B and it blows up, that's on you.

    for input_task in task.input_tasks:
        if input_task._aio_task is None:
            raise AssertionError("One of a task's input sub-tasks was not started") # pragma: no cover
        try:
            await input_task._aio_task
        except Task.SKIPPED:
            # This input task didn't need to rebuild.
            pass
        except Exception as ex:
            task._error = Task.CANCELLED(f"Task {hex(id(task))} is cancelled")
            raise task._error from ex

# ==================================================================================================

def expand_task(task : Task):
    with Log.DEBUG:
        log_task(task, "Task env:\n")
        log_task(task, Dumper.dump(task._env, fold = ["hancho", "log", "in_objs"]) + "\n")

    # We need to expand the build dir first so we can use it in fix_paths.
    build_dir = Expander.expand("{build_dir}", task._env)

    # Then we expand all io fields and fix their paths.
    for _field, _files in task._env['task'].items():
        if not _field.startswith("in_") and not _field.startswith("out_"): # and _field != "in_depfile":
            continue

        files = [
            val.out_files if isinstance(val, Task) else val
            for val in Utils.yield_values(_files)
        ]

        files = Expander.expand(files, task._env)
        files = Utils.flatten(files)
        files = fix_paths(task, _field, files, build_dir)
        files = files[0] if len(files) == 1 else files

        task.cfg[_field] = files

        if _field == "in_depfile":
            task.cfg['in_depfile'] = cast(str, files)
        elif _field.startswith("in_"):
            task.in_files[_field] = files
        elif _field.startswith("out_"):
            task.out_files[_field] = files

    for key in ['name', 'desc', 'command', 'cwd', 'build_dir', 'in_depfile', 'depformat', 'job_size', 'dry_run']:
        task.cfg['key'] = Expander.expand("{" + key + "}", task._env.task)

    for _field in task.cfg:
        if (_field.startswith("out_") or _field == "in_depfile") and not task.cfg['dry_run']:
            file = task.cfg[_field]
            os.makedirs(Path.dirname(file), exist_ok=True)

    if len(task.cfg['command']) == 1:
        task.cfg['command'] = task.cfg['command'][0]

    with Log.DEBUG:
        log_task(task, "Task after expand:\n")
        log_task(task, Dumper.dump(task.cfg) + "\n")

# ==================================================================================================

def fix_paths(task : Task, field : str, file : (str | list | set | tuple | abc.Mapping), build_dir : str):
    """
    Input and output file paths in .hancho scripts are declared relative to the directory the
    script is in (stored in the config under 'script_cwd').
    In general we want to run commands from the root of the repo and store output files in
    repo/build, so we need to fix up the paths to match.
    """
    if isinstance(file, (list, set, tuple)):
        return [fix_paths(task, field, f, build_dir) for f in file]
    if isinstance(file, abc.Mapping):
        return {k:fix_paths(task, field, f, build_dir) for k, f in file}

    # Join script_cwd with the filename to produce an absolute path.
    file = Path.join(task._script._root, file)

    # File paths _must_ be abs'd after joining, otherwise they might look like they're under
    # script_dir, but they're not because the paths could have "../../../../.." in them.
    file = Path.abspath(file)

    # Move all outputs under build_dir and ensure their directories exist.
    # Note - This will also move "in_depfile" under build_dir - this is _intentional_ as
    # it's an _output_ from the compiler and is not checked in to the source tree.
    if (field.startswith("out_") or field == "in_depfile") and not Path.startswith(file, build_dir):
        file = Path.relpath(file, task._script._root)
        file = Path.join(build_dir, file)

    return file

# ==================================================================================================

def sanity_check(task : Task):
    repo = task._repo

    # Check for all task issues that break the build

    if not Path.exists(task.cfg['cwd']):
        raise Task.BROKEN(f"Task working directory '{task.cfg['cwd']}' does not exist")

    if not Path.startswith(task.cfg['build_dir'], repo._root):
        raise Task.BROKEN(f"The build dir {task.cfg['build_dir']} is not under repo.root {repo._root}")

    # In order to provide the least amount of bafflement to users, CLI commands execute
    # from task_cwd (which is usually the root of the repo, the most common cwd)
    # and callbacks execute from dir(script_path) (because you expect to be in the same
    # directory as the script when the callback is firing).

    # This means that pre-rel-ified paths can only be rel'd to one of the two cwds, not both.
    # And that means we disallow mixed cli/callback command lists.

    if isinstance(task.cfg['command'], list):
        for command in task.cfg['command']:
            if type(command) is not type(task.cfg['command'][0]):
                raise Task.BROKEN(f"Commands aren't the same type: {task.cfg['command']}")

            # Check that task's commands are either strings or callables.
            if not isinstance(command, str) and not callable(command) and command is not None:
                raise Task.BROKEN(f"Command {command} is not a string or a callable?")

    # In strict mode, we mark a task broken if its command still has delimiters in it.
    if repo._strict:
        for command in cast(list, Utils.flatten(task.cfg['command'])):
            out = Expander.Blocks()
            Expander._split_text(command, out)
            if (len(out) > 1) or (len(out) == 1 and isinstance(out[0], Expander.Macro)):
                raise Task.BROKEN("STRICT: Command has delimiters in it")

    # Check that all build files would end up under build_dir
    for file in Utils.yield_values(task.out_files):
        assert Path.isabs(file)
        if not Path.startswith(file, task.cfg['build_dir']):
            raise Task.BROKEN(f"Path error, output file {file} is not under build dir {task.cfg['build_dir']}")

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
        if not Path.exists(file) and not repo._dry_run:
            raise Task.BROKEN(f"Input file missing - {file}")

    # Tasks should have at most one depfile.
    if isinstance(task.cfg['in_depfile'], list):
        raise Task.BROKEN(f"Tasks can't have more than one dependency file! - {task.cfg['in_depfile']}")

# ==================================================================================================

async def run_command(task : Task, command : str):
    with Log.VERBOSE, Log.BLUE:
        log_task(task, f"{Path.relpath(task.cfg['cwd'], task._repo._root)}$ {command}\n")

    proc = None
    try:
        # Create the subprocess via asyncio and then await the result.
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd    = task.cfg['cwd'],
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
        with Log.VERBOSE, Log.Color(0x666666):
            log_task(task, dump_stdout(task))

# ==================================================================================================

async def call_callback(task : Task, command : abc.Callable):
    with Log.VERBOSE, Log.BLUE:
        callback_dir = Path.relpath(task._script._root, task._repo._root)
        log_task(task, f"{callback_dir}$ {command}\n")

    # Callbacks run from the script dir where they were defined so that relative paths used
    # in the callback will be correct.
    with chdir(task._script._root): # type: ignore
        result = command(task)

    # It would seem like we wouldn't have to explicitly unwrap one level of await-ness here,
    # but apparently that's just how Python waitables work.
    if inspect.isawaitable(result):
        result = await result

    return result

# ==================================================================================================

def rebuild_reason(task : Task) -> str:
    """
    Figures out why we have to run a Task, or returns "" if we don't.
    """

    repo = task._repo

    # ------------------------------------
    # Check the trivial reasons to rebuild

    if repo._build_force:
        repo.build_reasons["forced"] += 1
        return "Target forced to rebuild"

    has_input = any(Utils.yield_values(task.in_files))
    if not has_input:
        repo.build_reasons["no inputs"] += 1
        return "Always rebuild a target with no inputs"

    has_output = any(Utils.yield_values(task.out_files))
    if not has_output:
        repo.build_reasons["no outputs"] += 1
        return "Always rebuild a target with no outputs"

    # ------------------------------------

    for filename in Utils.yield_values(task.in_files):
        if reason := check_stat(repo, filename):
            return reason

    for filename in task._old_deplines:
        if reason := check_stat(repo, filename):
            return reason

    for filename in Utils.yield_values(task.out_files):
        if reason := check_stat(repo, filename, task.cfg['command']):
            return reason

    repo.build_reasons["*task clean"] += 1
    return ""

# ==================================================================================================

def check_stat(repo : Repo, filename : str, command = None):
    if not Path.exists(filename):
        repo.build_reasons["file missing"] += 1
        return f"File missing: {filename}"

    if filename not in repo.stat_db:
        repo.build_reasons["stat missing"] += 1
        return f"Stat missing: {filename}"

    old_stat = repo.stat_db[filename]
    new_stat = Utils.get_stats(filename, command)

    if old_stat['st_mtime_ns'] != new_stat['st_mtime_ns']:
        repo.build_reasons["mtime mismatch"] += 1
        return f"Mtime mismatch {old_stat['st_mtime_ns']} != {new_stat['st_mtime_ns']} for : {filename}"

    if old_stat['st_size'] != new_stat['st_size']:
        repo.build_reasons["size mismatch"] += 1
        return f"Size mismatch {old_stat['st_size']} != {new_stat['st_size']} for : {filename}"

    if old_stat['hash'] != new_stat['hash']:
        repo.build_reasons["hash mismatch"] += 1
        return f"Hash mismatch {old_stat['hash']} -> {new_stat['hash']} for : {filename}"

    if command is not None and old_stat['command'] != new_stat['command']:
        repo.build_reasons["command changed"] += 1
        return f"Command used to generate file has changed : {filename!r} : {old_stat['command']!r} : {new_stat['command']!r}"

    # Does not need to rebuild based on file stats / hash
    repo.build_reasons["*hash match"] += 1
    return ""

# ==================================================================================================

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

# ==================================================================================================

def log_task(task : Task, message : str):
    # Log helper that adds the [ NN/ XX] tag before the log line.
    for line in message.splitlines(keepends=True):
        with Log.LIME:
            if not Log.line_buffer:
                Log.log(f"[{task._task_id:3d}/{Runner.tasks_enabled:3d}] ")
        Log.log(line)

# ==================================================================================================

def log_exception(task : Task, message, ex = None):
    with Log.ERROR, Log.RED:
        Log.log("========================================\n")
        Log.log(message + "\n")
        Log.log("========================================\n")

        Log.log(f"Script    = {task._script._path}:\n")
        Log.log(f"Task      = '{task.cfg['name']}' : '{task.cfg['desc']}'\n")
        Log.log(f"os.getcwd = {os.getcwd()}\n")
        Log.log(f"task cwd  = {task.cfg['cwd']}\n")
        Log.log(f"command   = {task.cfg['command']}\n")
        if ex:
            Log.log_exception(ex)
        Log.log(dump_stdout(task))

        Log.log("========================================\n")

# ==================================================================================================

def run_tool(tool : str): # pragma: no cover
    if tool == "clean":
        for repo in Hancho.repos:
            build_root = repo._build_dir

            # Tiny bit of sanity checking so we don't inadvertently delete a repo.
            assert build_root.startswith(repo._root) and build_root != repo._root

            if Path.isdir(build_root):
                Log.log(f"Wiping build_root {build_root}\n")
                shutil.rmtree(build_root, ignore_errors=True)
        Log.log("Clean done\n")
        return 0
    else:
        raise AssertionError(f"Don't know how to run tool {tool}")

# ==================================================================================================

_start()
