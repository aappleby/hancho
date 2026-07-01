#!/usr/bin/python3
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
import asyncio
import colorsys
import contextvars
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
import types
import zlib  # for crc32, adler32
from collections import Counter, abc
from contextlib import chdir, contextmanager, suppress
from enum import Enum
from inspect import isawaitable
from typing import Any, cast

hancho = sys.modules[__name__]
sys.modules["hancho"] = hancho


def get_defaults() -> Dict:

    # This is the config of the "default" script associated with hancho.py itself.

    # fmt: off
    hancho_defaults = Dict(

        # This stuff is generally per-repo

        hancho_dir   = "<sentinel>", # Where hancho.py lives. Used for hancho.load("{hancho_dir}/tools/tools_base.hancho") etcetera
        repo_dir     = "<sentinel>", # Directory for the repo we're currently building
        script_name  = "<sentinel>", # Name of the current script. Starts at 'hancho'
        script_path  = "<sentinel>", # Path to the current script. Starts at /abs/path/hancho.py
        script_cwd   = "<sentinel>", # Working directory for the current script, may not be the same as dirname(script_path)
        is_repo      = "<sentinel>",

        build_root   = "{repo_dir}/build",
        build_tag    = "",
        build_dir    = "{build_root}/{build_tag}/{rel(task_cwd, repo_dir)}",

        comp_db_path = "{build_root}/compile_commands.json",
        stat_db_path = "{build_root}/hancho.json",

        # This stuff is generally per-task

        name       = None,
        desc       = None,
        command    = None,
        task_cwd   = "{repo_dir}",
        enabled    = False,
        core_count = 1,
        dry_run    = False,
        depformat  = "gcc" if os.name == "posix" else "msvc",
    )
    # fmt: on

    return hancho_defaults


cv_script : contextvars.ContextVar[Script] = contextvars.ContextVar("script")

# endregion
# --------------------------------------------------------------------------------------------------
# region Log

class LogLevel(int, Enum):
    QUIET    = 0
    FATAL    = 10
    CRITICAL = 20
    ERROR    = 30
    WARNING  = 40
    NORMAL   = 50
    VERBOSE  = 60
    DEBUG    = 70
    TRACE    = 80

    def __enter__(self):
        self.old_verbosity_in = Log.verbosity_in
        Log.verbosity_in = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        Log.verbosity_in = self.old_verbosity_in
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

    # Log needs to be initialized immediately on startup so that if some other init step hits an
    # issue, we don't crash because we have no log.

    time_origin   = time.perf_counter()
    indent_stack  = []
    current_color = -1
    line_buffer   = ""
    match_escapes = re.compile(r"(\x1B.*?m)")
    verbosity_in  = LogLevel.NORMAL
    verbosity_out = LogLevel.NORMAL # verbosity level we want to appear in the log

    @classmethod
    def reset(cls, flags):
        cls.con_w = shutil.get_terminal_size().columns
        cls.log_wrap      = flags.pop("log_wrap", False)
        cls.log_color     = flags.pop("log_color", True)
        cls.log_timestamp = flags.pop("log_timestamp", True)

        cls.time_origin   = time.perf_counter()
        cls.indent_stack  = []
        cls.current_color = -1
        cls.line_buffer   = ""
        cls.match_escapes = re.compile(r"(\x1B.*?m)")
        cls.verbosity_in  = LogLevel.NORMAL

        # Handle all the verbosity-related flags

        verbosity = flags.pop("verbosity", None)
        trace     = flags.pop("trace", False)
        debug     = flags.pop("debug", False)
        verbose   = flags.pop("verbose", False)
        quiet     = flags.pop("quiet", False)

        if verbosity is not None:
            if isinstance(verbosity, str):
                verbosity = LogLevel[verbosity.upper()]
            elif isinstance(verbosity, int):
                verbosity = LogLevel(verbosity)
            else:
                raise ValueError(f"Got an unknown verbosity '{type(verbosity)} = {verbosity}'")

        elif trace:
            verbosity = LogLevel.TRACE
        elif debug:
            verbosity = LogLevel.DEBUG
        elif verbose:
            verbosity = LogLevel.VERBOSE
        elif quiet:
            verbosity = LogLevel.QUIET
        else:
            verbosity = LogLevel.NORMAL

        cls.verbosity_out = verbosity

    # ----------------------------------------------------------------------------------------------

    @staticmethod
    @contextmanager
    def color(new_color):
        old_color = Log.current_color
        try:
            Log.current_color = new_color
            yield
        finally:
            Log.current_color = old_color

    @classmethod
    def indent(cls, color):
        cls.indent_stack.append(Log.hex_to_ansi(color) + "│ " + Log.reset_color())

    @classmethod
    def dedent(cls):
        cls.indent_stack.pop()

    # ----------------------------------------------------------------------------------------------

    @staticmethod
    def hex_to_ansi(hex):
        if hex and Log.log_color:
            r, g, b = ((hex >> 16) & 0xFF, (hex >>  8) & 0xFF, (hex >>  0) & 0xFF)
            return f"\x1B[38;2;{r};{g};{b}m"
        else:
            return ""

    @classmethod
    def reset_color(cls):
        if Log.current_color != 0 and cls.log_color:
            return "\x1B[0m"
        else:
            return ""

    @classmethod
    def log(cls, text):
        if not isinstance(text, str) or len(text) == 0:
            return

        if Log.verbosity_in > Log.verbosity_out:
            return

        if cls.current_color >= 0 and cls.log_color:
            hex = cls.current_color
            color_prefix = Log.hex_to_ansi(hex)
            color_suffix = Log.reset_color()
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

            if not Log.log_wrap:
                cls.line_buffer = Log.clip_printable(cls.line_buffer, Log.con_w)

            assert Log.verbosity_in is not None

            if Log.verbosity_in <= Log.verbosity_out:
                sys.stdout.write(cls.line_buffer)

            cls.line_buffer = ""

    @classmethod
    def log_exception(cls, ex):
        tb = traceback.extract_tb(ex.__traceback__)
        if tb:
            frame = tb[-1]
            #Log.log(f"type      = {type(ex)}\n")
            Log.log("  text = ")
            with Log.color(0xFFFF00):
                Log.log(f"'{ex}'\n")
            Log.log(f"  file = {frame.filename}\n")
            Log.log(f"  func = {frame.name}\n")
            Log.log(f"  line = {frame.lineno}\n")
            # Printing the line isn't useful as it's always going to be "raise ..."
            #Log.log(f"line      = '{frame.line}'\n")
        else: # pragma: no cover
            Log.log(f"Could not extract traceback from {ex}!")

    @classmethod
    def get_timestamp(cls):
        """Returns the timestamp string that is placed at the left of log entries."""
        return f"[{time.perf_counter() - Log.time_origin:8.3f}] " if cls.log_timestamp else ""

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
        chunks = Log.match_escapes.split(text)

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
# region Utils

class Utils:

    @classmethod
    def reset(cls, flags):
        cls.stat_calls = 0
        cls.hash_calls = 0
        cls.hash_bytes = 0
        cls.hash_time = 0

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
        elif Utils.is_mapping(key):
            for k, v, in sorted(key.items()):
                h = cls.hash(k, h)
                h = cls.hash(v, h)
        elif Utils.is_collection(key):
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

    # These types are considered already "flat" and don't need to be turned into a list.
    flat_types = (str, bytes, bytearray, range, abc.Mapping)

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
    def dump_to_str1(cls, key, val, indent = 0, print_id = False, max_length = 80, tab = "    ", flat = False):
        """
        Hancho's pretty-printer for various types. Note that this is also used for script deduping:
        if you load "my/app/tools/stuff.hancho" multiple times but the configurations you gave it
        were identical, you should get one copy of the "stuff" script instead of two.

        As long as you're not doing something bizarre with configs or changing the dumper in the
        middle of a build, the resulting strings should be stable enough to use for deduping.
        """

        # Generate the "key : type = " prefix.
        prefix = ""
        if key is not None:
            prefix += str(key)
        if not isinstance(val, Utils.base_types):
            if key:
                prefix += ": "
            prefix += type(val).__name__
        if print_id:
            prefix += ": " + Utils.hex_id(val)
        if prefix:
            prefix += " = "


        # Non-containers are always emitted on one line. If they overflow, they overflow.
        if not (Utils.is_collection(val) or Utils.is_mapping(val)):
            if isinstance(val, Task):
                val = f"<Task {val.config.name}>" if indent > 0 else val.__dict__
            elif isinstance(val, contextvars.Context):
                val = "<Context>"
            elif isinstance(val, types.ModuleType):
                val = f"<Module {val.__name__}>"
            elif isinstance(val, types.FunctionType):
                val = f"<Function {val.__name__}>"
            elif isinstance(val, argparse.Namespace):
                val = val.__dict__
            # Objects that don't have a custom repr (and a few built-in types) just get printed as
            # '<object>'
            if type(val) in Utils.opaque_types:
                return (tab * indent) + prefix + Utils.opaque_types[type(val)] #type:ignore
            elif type(val).__repr__ is object.__repr__:
                return (tab * indent) + prefix + "<object>"
            else:
                return (tab * indent) + prefix + repr(val)

        # Extract key-value pairs and delimiters for our container types.
        if isinstance(val, tuple):
            items = [(None, val2) for val2 in val]
            ld = "("
            rd = ",)" if len(items) == 1 else ")"
        elif Utils.is_mapping(val):
            val = cast(abc.Mapping, val)
            items = sorted(val.items())
            ld = "{"
            rd = "}"
        elif Utils.is_collection(val):
            items = [(None, val2) for val2 in cast(abc.Collection, val)]
            ld = "["
            rd = "]"
        else:
            raise AssertionError(f"Don't know what to do with {type(val)}") # pragma: no cover

        # Iterate over our key-value pairs, converting them in to string chunks. If the resulting line
        # would be too wide and we're not trying to generate a flat string, fall back to multi-line.
        pad = (tab * indent)
        separator = ", "
        num_separators = len(items) - 1 if len(items) else 0
        length = len(pad) + len(prefix) + len(ld) + (len(separator) * num_separators) + len(rd)

        chunks = []
        for k, v in items:
            chunk = Utils.dump_to_str1(k, v, 0, print_id, max_length, tab, True)
            if chunk is None or length + len(chunk) > max_length:
                if flat:
                    return None
                chunks = (Utils.dump_to_str1(k, v, indent + 1, print_id, max_length - 1, tab, False) for k, v in items)
                return pad + prefix + ld + "\n" + ",\n".join(chunks) + "\n" + pad + rd
            length += len(chunk)
            chunks.append(chunk)

        # Done, we can fit this dump on one line.
        return pad + prefix + ld + separator.join(chunks) + rd

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def _dump_prefix(cls, key, val, print_id):
        prefix = ""
        if key is not None:
            prefix += str(key)
        if type(val) not in Utils.base_types:
            if key:
                prefix += ":"
            prefix += type(val).__name__
        if print_id:
            prefix += ": " + Utils.hex_id(val)
        if prefix:
            prefix += " = "
        return prefix

    @classmethod
    def _dump_scalar(cls, val):
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
            return repr(val)

    @classmethod
    def _unpack_container(cls, val):
        if isinstance(val, tuple):
            items = [(None, v) for v in val]
            return '(', items, ",)" if len(items) == 1 else ')'
        elif Utils.is_mapping(val):
            val = cast(abc.Mapping, val)
            return '{', sorted(val.items()), '}'
        elif Utils.is_collection(val):
            val = cast(abc.Collection, val)
            items = [(None, v) for v in val]
            return '[', items, ']'
        else:
            raise AssertionError(f"Don't know what to do with {type(val)}") # pragma: no cover

    @classmethod
    def _dump_container_to_flat_str(cls, val, print_id, max_length, tab):
        ld, items, rd = cls._unpack_container(val)
        separator = ", "
        num_separators = len(items) - 1 if len(items) else 0
        length = len(ld) + (len(separator) * num_separators) + len(rd)

        chunks = []
        for k, v in items:
            prefix = cls._dump_prefix(k, v, print_id)
            chunk = prefix + cls._dump_variant_to_flat_str(v, print_id, max_length - length - len(prefix), tab)
            length += len(chunk)
            if length > max_length:
                raise ValueError()
            chunks.append(chunk)

        return ld + ", ".join(chunks) + rd

    @classmethod
    def _dump_container_to_str(cls, val, indent, print_id, max_length, tab):
        ld, items, rd = cls._unpack_container(val)
        lines = [
            cls.dump_to_str(k, v, indent + 1, print_id, max_length - 1, tab)
            for k, v in items
        ]
        return ld + '\n' + ',\n'.join(lines) + '\n' + (tab * indent) + rd

    @classmethod
    def _dump_variant_to_flat_str(cls, val, print_id, max_length, tab):
        if Utils.is_collection(val) or Utils.is_mapping(val):
            return cls._dump_container_to_flat_str(val, print_id, max_length, tab)
        else:
            return cls._dump_scalar(val)

    @classmethod
    def _dump_variant_to_str(cls, key, val, indent, print_id, max_length, tab):
        prefix = (tab * indent) + cls._dump_prefix(key, val, print_id)

        if Utils.is_collection(val) or Utils.is_mapping(val):
            try:
                return prefix + cls._dump_container_to_flat_str(val, print_id, max_length - len(prefix), tab)
            except ValueError:
                return prefix + cls._dump_container_to_str(val, indent, print_id, max_length, tab)
        else:
            return prefix + cls._dump_scalar(val)

    @classmethod
    def dump_to_str(cls, key, val, indent = 0, print_id = False, max_length = 80, tab = "    "):
        # Unwrap tasks only if they're at the bottom level of indentation.
        if isinstance(val, Task) and indent == 0:
            val = val.__dict__

        return cls._dump_variant_to_str(key, val, indent, print_id, max_length, tab)

    # ----------------------------------------------------------------------------------------------

    @staticmethod
    def stringify(variant) -> str:
        """Converts any type into a template-compatible string."""
        if variant is None:
            return ""
        elif Utils.is_collection(variant):
            variant = [Utils.stringify(val) for val in variant]
            return " ".join(variant)
        else:
            result = str(variant)
            if result == Expander.sentinel:
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
    def is_collection(variant : Any) -> bool:
        """
        Mappings and non-array iterables are not considered Collections in Hancho so that
        we don't turn "foo" into ('f', 'o', 'o').
        """
        if isinstance(variant, Utils.flat_types):
            return False
        return isinstance(variant, abc.Collection)

    @staticmethod
    def is_mapping(variant : Any) -> bool:
        return isinstance(variant, abc.Mapping)

    @staticmethod
    def is_template(text) -> bool:
        # inefficient way to check for templates, but it's reliable
        blocks = Expander._split_template(text)
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
    def flatten(variant, out : list | None = None):
        if out is None:
            out = []
        if isinstance(variant, Utils.flat_types):
            out.append(variant)
        elif isinstance(variant, abc.Iterable):
            for element in variant:
                Utils.flatten(element, out)
        elif variant is not None:
            out.append(variant)
        return out

    @staticmethod
    def visit(variant, visitor):
        if Utils.is_collection(variant):
            for v in variant:
                Utils.visit(v, visitor)
        elif Utils.is_mapping(variant):
            for v in variant.values():
                Utils.visit(v, visitor)
        else:
            visitor(variant)

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
        return deplines


# endregion
# --------------------------------------------------------------------------------------------------
# region Path
# These functions wrap the os.path.* functions so that they work on Tree[str]

class Path:

    # WARNING - Both 'startswith' and 'rel' below can throw ValueError if there's a mix of abs/rel
    # paths, or if the paths are on different volumes in Windows. We don't handle this yet, but we
    # will need to eventually. If this occurs inside a macro you'll see the exception in the macro
    # expansion trace and the macro will be returned unexpanded. Using 'commonpath' here is
    # probably worth it though, as it handles some annoying edge cases.

    @staticmethod
    def startswith(path, parent):
        if Utils.is_collection(path):
            return all(Path.startswith(p, parent) for p in path)
        return os.path.commonpath([path, parent]) == parent

    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with 'commonpath' and 'removeprefix'.

    @staticmethod
    def rel(lhs, rhs):
        if Utils.is_collection(lhs):
            return [Path.rel(lh, rhs) for lh in lhs]
        if Utils.is_collection(rhs):
            return [Path.rel(lhs, rh) for rh in rhs]

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
        if Utils.is_collection(lhs):
            return [Path.join(lh, rhs) for lh in lhs]
        if Utils.is_collection(rhs):
            return [Path.join(lhs, rh) for rh in rhs]
        return os.path.join(lhs, rhs)

    @staticmethod
    def abs(path):
        if Utils.is_collection(path):
            return [Path.abs(p) for p in path]
        return os.path.abspath(path) if path else ""

    @staticmethod
    def real(path):
        if Utils.is_collection(path):
            return [Path.real(p) for p in path]
        return os.path.realpath(path) if path else ""

    @staticmethod
    def norm(path):
        if Utils.is_collection(path):
            return [Path.norm(p) for p in path]
        return os.path.normpath(path) if path else ""

    @staticmethod
    def base(path):
        if Utils.is_collection(path):
            return [Path.base(p) for p in path]
        return os.path.basename(path)

    @staticmethod
    def ext(path, new_ext):
        if Utils.is_collection(path):
            return [Path.ext(p, new_ext) for p in path]
        return os.path.splitext(path)[0] + new_ext

    @staticmethod
    def stem(path):
        if Utils.is_collection(path):
            return [Path.stem(p) for p in path]
        return os.path.splitext(os.path.basename(path))[0]

    @staticmethod
    def dirname(path):
        if Utils.is_collection(path):
            return [Path.dirname(p) for p in path]
        return os.path.dirname(path)

    @staticmethod
    def split(path):
        if Utils.is_collection(path):
            return [Path.split(p) for p in path]
        return os.path.split(path)

    @staticmethod
    def splitext(path):
        if Utils.is_collection(path):
            return [Path.splitext(p) for p in path]
        return os.path.splitext(path)

    @staticmethod
    def isabs(path):
        if Utils.is_collection(path):
            return all(Path.isabs(p) for p in path)
        return isinstance(path, str) and len(path) > 0 and os.path.isabs(path)

    @staticmethod
    def isfile(path):
        if Utils.is_collection(path):
            return all(Path.isfile(p) for p in path)
        return isinstance(path, str) and os.path.isfile(path)

    @staticmethod
    def isdir(path):
        if Utils.is_collection(path):
            return all(Path.isdir(p) for p in path)
        return isinstance(path, str) and os.path.isdir(path)

    @staticmethod
    def exists(path):
        if Utils.is_collection(path):
            return all(Path.exists(p) for p in path)
        return isinstance(path, str) and os.path.exists(path)

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
            if not Utils.is_mapping(arg):
                raise ValueError(f"Argument #{i} was not a Mapping - {arg}")

        self.merge(*args, **kwargs)
        object.__setattr__(self, "_expander", Expander(self))


    # ----------------------------------------

    def merge(self, *args, **kwargs):
        for rhs in (*args, kwargs):
            Dict.generic_merge(
                self, rhs, self,
                merge_dicts=True, merge_lists=True,
                keep_a=True, keep_b=True)
        return self

    # Merges self and args into a new dict, keeping only keys that were already in self.
    # For example, if you have a config that contains "out_bin" and you merge it with "compile_cpp",
    # Hancho will complain that "out_bin" is missing - it sees both "out_obj" and "out_bin" and
    # assumes the task produces both. If you do compile_cpp.fill(...), "out_bin" does not get added
    # to compile_cpp.
    def fill(self, *args, **kwargs):
        result = Dict()
        for i, rhs in enumerate((*args, kwargs)):
            Dict.generic_merge(
                self if i == 0 else result, rhs, result,
                merge_dicts=True, merge_lists=True,
                keep_a=True, keep_b=False)
        return result

    @classmethod
    def generic_merge(cls, lhs, rhs, dst, merge_dicts, merge_lists, keep_a, keep_b):
        keys = lhs.keys() | rhs.keys()
        for key in keys:
            if key in lhs and key not in rhs and not keep_a: continue
            if key not in lhs and key in rhs and not keep_b: continue

            lhs2 = lhs.get(key, None)
            rhs2 = rhs.get(key, None)

            if Utils.is_mapping(lhs2) and Utils.is_mapping(rhs2) and merge_dicts:
                dst2 = dst.get(key, Dict())
                cls.generic_merge(lhs2, rhs2, dst2, merge_dicts, merge_lists, keep_a, keep_b)
            elif Utils.is_mapping(rhs2):
                dst[key] = Dict(rhs2)
            elif Utils.is_collection(lhs2) and Utils.is_collection(rhs2) and merge_lists:
                dst[key] = lhs2 + rhs2
            elif Utils.is_collection(rhs2):
                dst[key] = list(rhs2)
            else:
                dst[key] = rhs2 if rhs2 is not None else lhs2
        return dst

    # ----------------------------------------
    # Object

    def on_keyerror(self, key):
        if key != "trace":
            pass
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'")

    def __getattr__(self, key : str):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            self.on_keyerror(key)

    def __setattr__(self, key : str, val : Any):
        try:
            return dict.__setitem__(self, key, val)
        except KeyError:
            self.on_keyerror(key)

    def __delattr__(self, key : str):
        try:
            return dict.__delitem__(self, key)
        except KeyError:
            self.on_keyerror(key)

    def __or__(self, other):
        return Dict(self, other)

    def __repr__(self):
        return Utils.dump_to_str(key = getattr(self, "name", None), val = self)

    # ----------------------------------------
    # Expander convenience helpers

    def expand[T](self, text : Any, as_type : type[T] = object) -> T:
        # Expander-mode _must_ be the default, otherwise things like
        # config.expand("{rel(task_cwd, repo_dir)}", str)
        # doesn't work because we try and call rel using {macros}, and macros are not paths.

        result = self._expander.expand(text)
        assert isinstance(result, as_type)
        return result

# Tool is just an alias for Dict to make build scripts more readable.
class Tool(Dict):
    pass

# endregion
# --------------------------------------------------------------------------------------------------
# region Options
# Handles global configuration options

class Options:

    @classmethod
    def reset(cls, flags):
        # Pull options that aren't task-specific off the root config.

        cls.max_errors  = flags.pop("max_errors", 0)
        cls.build_all   = flags.pop("build_all", False)
        cls.force_rebuild = flags.pop("force", False)
        cls.strict      = flags.pop("strict", True)
        cls.target      = flags.pop("target", None)
        cls.tool        = flags.pop("tool", None)

    @classmethod
    def parse_flags(cls, args : list[str]):
        assert Utils.is_collection(args)

        desc = textwrap.dedent("""
        ================================================================================
        Hancho is a simple, pleasant build system
        ================================================================================
        """)

        parser = argparse.ArgumentParser(description=desc, formatter_class=argparse.RawDescriptionHelpFormatter)

        # pylint: disable=line-too-long
        # fmt: off
        parser.add_argument("target",  nargs="?",  default=argparse.SUPPRESS, type=str.strip,       help="A regex that selects the targets to build. Defaults to all targets in the root repo.")

        # These two flags control where the root repo and root script are
        parser.add_argument("-C", "--repo-dir",    default=os.getcwd(),       type=str.strip,       help="Change directory before starting the build")
        parser.add_argument("-f", "--script-path", default="build.hancho",    type=str.strip,       help="Input .hancho file - defaults to 'build.hancho'")

        parser.add_argument("-t", "--tool",        default=argparse.SUPPRESS, type=str.strip,       help="Run a subtool.")
        parser.add_argument("--build-tag",         default=argparse.SUPPRESS, type=str.strip,       help="Set the build tag. Tagged builds will have separate subdirectories under the build directory.")
        parser.add_argument("-j", "--core-max",    default=argparse.SUPPRESS, type=int,             help="Run jobs on N cores in parallel (default = cpu_count)")
        parser.add_argument("--max-errors",        default=argparse.SUPPRESS, type=int,             help="The maximum number of task errors we tolerate before abandoning the build")
        parser.add_argument("-n", "--dry-run",     default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Do not run commands")
        parser.add_argument("-a", "--rebuild",     default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Build absolutely everything in all build scripts loaded.")
        parser.add_argument("--log-wrap",          default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Wrap lines around the console instead of clipping them")
        parser.add_argument("--log-color",         default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Use color in the log for better readability")
        parser.add_argument("--log-timestamp",     default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Timestamp each log line")
        parser.add_argument("--strict",            default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Checks for common footguns like typo'd templates")
        parser.add_argument("-q", "--quiet",       default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Shortcut for --verbosity=quiet. Mutes all output")
        parser.add_argument("-v", "--verbose",     default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Shortcut for --verbosity=verbose. Prints extra info")
        parser.add_argument("-d", "--debug",       default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Shortcut for --verbosity=debug. Prints debugging information")
        parser.add_argument("--trace",             default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Shortcut for --verbosity=trace. Traces all text expansion")
        # fmt: on

        choices = [v.lower() for v in LogLevel.__members__]
        parser.add_argument(
            "--verbosity",
            default=argparse.SUPPRESS,
            choices=choices,
            help="Manually select verbosity level. Quiet = none, Trace = maximal spam",
        )

        (flags, unrecognized) = parser.parse_known_args(args)

        # Unrecognized command line parameters also become config fields if they are flag-like.
        # Naked flags become {'name':True}, number types become numbers, 'true' and 'false'
        # become bools (regardless of capitalization), everything else becomes a string.

        extra_flags = {}
        for span in unrecognized:
            if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
                key = match.group(1)
                val = match.group(2)


                if val is None:
                    # this is so that --foo turns into {foo:True}
                    val = True
                elif val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                else:
                    for converter in (int, float, str):
                        try:
                            val = converter(val)
                            break
                        except ValueError:
                            pass
                extra_flags[key] = val

        flags = Dict(vars(flags), extra_flags)

        return flags

# endregion
# --------------------------------------------------------------------------------------------------
# region Repo

class Repo:
    def __init__(self, *, name : str, config : Dict):
        self.name : str = name
        self.config = config
        self.build_db : BuildDB = BuildDB(config)
        self.scripts = []

# endregion
# --------------------------------------------------------------------------------------------------
# region BuildDB

class BuildDB:

    def __init__(self, config):
        self.config = config
        self.reasons = Counter()

        # Hash, size, mtime, command for each file in the previous build.
        # Command is only set for output files.
        self.old_stat_db : dict[str, Dict] = {}

        # Stats accumulated during the build after a task is initialized but before it has run.
        # Compared with old_stat_db entries to determine if a task needs a rebuild.
        self.mid_stat_db : dict[str, Dict] = {}

        self.stat_db_path2 = config.stat_db_path # these are unexpanded
        self.comp_db_path2 = config.comp_db_path
        #self.old_stat_db   = self.load_stat_db()

    # ----------------------------------------------------------------------------------------------

    def load_stat_db(self) -> Dict:
        result = {}

        stat_db_path = self.config.expand(self.stat_db_path2, str)
        stat_db_path = cast(str, Path.abs(stat_db_path))

        #with LogLevel.VERBOSE, Colors.ORANGE:
        #    Log.log(f"Loading stat db '{stat_db_path}'\n")

        if not os.path.isfile(stat_db_path):
            #Log.log_dedent(Colors.ORANGE, f"Stat db '{stat_db_path}' not found\n")
            return Dict()

        #time_a = time.perf_counter()
        result = Utils.load_json(cast(str, stat_db_path))
        #time_b = time.perf_counter()

        #with LogLevel.VERBOSE, Colors.ORANGE:
        #    Log.log(f"Loading {len(result)} stat db entries took {time_b - time_a:8.6f} seconds\n")

        # Turn the serialized stats back into a Dict.
        for k, v in list(result.items()):
            result[k] = Dict(v)

        return Dict(result)

    # ----------------------------------------------------------------------------------------------

    def update_stat_db(self, out_db, file, command = None):
        Utils.stat_calls += 1

        if file in out_db:
            stat = out_db.get(file)
        else:
            stat = Dict()
            out_db[file] = stat

        _stat = os.stat(file)
        stat.merge(
            hash = Utils.hash_file(file),
            st_size = _stat.st_size,
            st_mtime_ns = _stat.st_mtime_ns,
            command = command
        )

    @classmethod
    def commands_to_string(cls, commands):
        commands = Utils.flatten(commands)
        if len(commands) and callable(commands[0]):
            commands = [c.__name__ for c in commands]
        return "; ".join(commands)

    # ----------------------------------------------------------------------------------------------

    def pre_task(self, task):
        if task.config.dry_run:
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
                    assert os.path.exists(file)


        for file in task.in_files:
            assert os.path.exists(file)
            if os.path.exists(file):
                self.update_stat_db(self.mid_stat_db, file)

        for file in task.out_files:
            if os.path.exists(file):
                str_command = BuildDB.commands_to_string(task.config.command)
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
        config = task.config

        # ------------------------------------
        # Check the trivial reasons to rebuild

        if Options.force_rebuild or config.get("rebuild", False):
            self.reasons["forced"] += 1
            return "Target forced to rebuild"

        if not task.in_files:
            self.reasons["no inputs"] += 1
            return "Always rebuild a target with no inputs"

        if not task.out_files:
            self.reasons["no outputs"] += 1
            return "Always rebuild a target with no outputs"

        # ------------------------------------

        for filename in task.out_files:
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

        all_files = task._old_deplines + task.in_files

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
# region Script

class Script:
    def __init__(self, *, name, config : Dict, module : types.ModuleType, repo : Repo):
        self.script_name   = name
        self.script_config = config
        self.script_module = module
        self.script_repo   = repo
        self.script_tasks  = []

# endregion
# --------------------------------------------------------------------------------------------------
# region Task
# Task object + bookkeeping

class Task:

    @classmethod
    def reset(cls, flags):
        cls.id_counter : int = 0
        cls.tasks_enabled : int = 0

    class FAILED(Exception):    pass
    class CANCELLED(Exception): pass
    class SKIPPED(Exception):   pass
    class BROKEN(Exception):    pass

    # ----------------------------------------------------------------------------------------------

    def __init__(self, *args, **kwargs):
        # The task's config contains all the commands, paths, options, inputs, dependent Tasks, and
        # anything else needed to assemble and run the task's commands. It is expected that build
        # scripts will need to read task.config in order to implement task callbacks, so the field
        # is not underscore-prefixed like the later ones.

        self.script  = cv_script.get()
        self.config  = Dict(self.script.script_config, *args, **kwargs)

        # Similarly, build scripts may need to see the complete list of inputs/outputs to a task
        # in addition to the individual in_/out_ fields, so these are public.
        self.in_files  = []
        self.out_files = []

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

        self.script.script_tasks.append(self)

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

        # Start all tasks referenced by _config so we don't deadlock while waiting for them.
        Utils.visit(self.config, lambda task: isinstance(task, Task) and task.enable_task())

    # ----------------------------------------------------------------------------------------------

    async def task_top(self):
        try:
            await self.task_main()
            self._error = None
        except asyncio.CancelledError as ex:
            with LogLevel.VERBOSE:
                self.log(f"<asyncio.CancelledError {ex}>\n")
            self._error = ex
            raise
        except Task.BROKEN as ex:
            self.log_task_exception("Task broken!", ex)
            self._error = ex
        except Task.FAILED as ex:
            self.log_task_exception("Task failed!", ex)
            self._error = ex
        except Task.CANCELLED as ex:
            with LogLevel.VERBOSE:
                self.log(str(ex) + "\n")
            self._error = ex
        except Task.SKIPPED as ex:
            with LogLevel.VERBOSE:
                self.log(str(ex) + "\n")
            self._error = ex
        except Exception as ex:
            self.log_task_exception("Task threw an exception!", ex)
            with LogLevel.ERROR:
                Log.log(traceback.format_exc() + "\n")
            self._error = ex
        finally:
            if self._core_count:
                Runner.release(self._core_count)
                self._core_count = 0

        if self._error:
            raise self._error

        return self.out_files

    # ----------------------------------------------------------------------------------------------

    async def task_main(self):
        config = self.config
        build_db : BuildDB = cv_script.get().script_repo.build_db

        time_a = time.perf_counter()

        Task.id_counter += 1
        self._task_id = Task.id_counter

        with LogLevel.DEBUG:
            self.log("Task config before expand:\n")
            self.log(str(config) + "\n")

        # ----------------------------------------
        # Expand all fields that don't depend on input/output filenames (basically everything
        # except name/desc/command). To prevent expansion-order issues, we expand to a temp Dict
        # and then copy them back into config.

        # We _can't_ expand input/output paths here as they may refer to output paths for tasks
        # that haven't executed yet - that has to happen _after_ awaiting our dependencies, so
        # you'll find it in task_init below.

        path_fields  = [
            "hancho_dir",
            "repo_dir",
            "script_path",
            "build_root",
            "build_dir",
            "task_cwd"
        ]

        flag_fields = [ "build_tag", "core_count", "depformat", "dry_run", "enabled", ]

        for f in path_fields:
            if f in config:
                config[f] = Path.norm(config.expand('{' + f + '}'))
        for f in flag_fields:
            if f in config:
                config[f] = config.expand('{' + f + '}')

        # ----------------------------------------
        # Await all tasks in our input fields and then flatten them.

        await self.await_inputs()

        # ----------------------------------------
        # Do all our task setup while chdir'd into the task's cwd so that relative paths will be
        # correct while we're checking input file existence. The task_init function is synchronous,
        # so there can be no await'ed points that could interrupt us - os.getcwd() should be stable
        # while we're doing this.

        with chdir(config.task_cwd):
            self.task_init()

        self.sanity_check()

        # ----------------------------------------
        # Paths updated. See if we need to rebuild our outputs.

        build_db.pre_task(self)

        self._reason = build_db.rebuild_reason(self)
        if not self._reason:
            raise Task.SKIPPED(f"Task is up-to-date: '{config.name}' : '{config.desc}'")

        # ----------------------------------------
        # Dry runs early out after all the task checks but before we allocate cores and run
        # commands.

        if config.dry_run:
            return

        # ----------------------------------------
        # Wait for enough jobs to free up to run this task.

        self._core_count = await Runner.acquire(config.core_count)

        # ----------------------------------------
        # Run all the task's commands

        with LogLevel.NORMAL:
            if config.name:
                self.log(f"{config.name}: ")
            self.log(f"{config.desc}\n")

        with LogLevel.VERBOSE, Log.color(0x606060):
            self.log(f"Task rebuilding because: {self._reason}\n")

        for command in cast(list, config.command):
            if callable(command):
                await self.call_callback(command)
            else:
                await self.run_command(command)

        # ----------------------------------------
        # See if the task wrote all its output files

        for file in self.out_files:
            if not os.path.exists(file):
                raise Task.FAILED(f"Task ran, but output file still missing: {file}")

        if "in_depfile" in self.config:
            deplines = Utils.load_depfile(self.config.in_depfile, self.config.depformat, self.config.task_cwd)
            for file in deplines:
                build_db.update_stat_db(build_db.mid_stat_db, file)

        # ----------------------------------------
        # Done!

        time_b = time.perf_counter()

        build_db.post_task(self)

        with LogLevel.VERBOSE, Log.color(0x606060):
            message  = f"Task took {time_b-time_a:8.6f} sec: "
            if self.config.name:
                message += f"'{self.config.name}' - "
            message += f"'{self.config.desc}'\n"
            self.log(message)

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
        config = self.config

        if os.getcwd() != Path.real(config.task_cwd):
            raise AssertionError(f"Running task_init while we're not in the realpath of task's cwd '{config.task_cwd}' - we are in {os.getcwd()}")  # pragma: no cover

        # ----------------------------------------
        # Flatten the commands and check that they're valid

        config.command = Utils.flatten(config.command)

        # ----------------------------------------
        # Expand all in_ and out_ filenames.

        # We _must_ expand _all_ of these first before joining paths or the paths will be incorrect:
        # prefix + swap(abs_path) != abs(prefix + swap(path)). And just out of paranoia, we expand
        # to a temp Dict, norm them (because who knows how the user may have written the template)
        # and then copy them back into the config.

        temp = {}
        for key, files in list(config.items()):
            if Task.is_io_field(key):
                temp[key] = Path.norm(self.config.expand(files))

        # We can't use merge here because that'll give us things like [unexpanded_depfile,
        # expanded_depfile] which we don't want - we just want to replace all the templates with
        # their expanded version.
        config.update(temp)

        # ----------------------------------------
        # Do all the file path remapping so our commands will work

        for key, files in list(config.items()):
            if not Task.is_io_field(key):
                continue

            files = self.remap_io_field_paths(key, files)

            # and unwrap filenames if they're an array of one element so that scripts expecting
            # join(str, str) to return a str will be happy.
            config[key] = files[0] if len(files) == 1 else files

        # ----------------------------------------
        # Paths are cleaned up, we can expand name/desc/command

        config.name    = config._expander.name
        config.desc    = config._expander.desc
        config.command = config._expander.command

        with LogLevel.DEBUG:
            self.log("Task config after expand:\n")
            self.log(str(config) + "\n")

    # ----------------------------------------------------------------------------------------------

    def sanity_check(self):
        """
        Checks for various ways that a task can be broken and raises exceptions for them.
        All of our 'raise Task.BROKEN's should be here (except for 'Invalid depfile format' above)
        """
        config = self.config

        if not Path.exists(config.task_cwd):
            raise Task.BROKEN(f"Task working directory '{config.task_cwd}' does not exist")

        if not Path.startswith(config.build_dir, config.repo_dir):
            raise Task.BROKEN(f"The build_dir {config.build_dir} is not under repo dir {config.repo_dir}")

        # In order to provide the least amount of bafflement to users, CLI commands execute
        # from task_cwd (which is usually the root of the repo, the most common cwd)
        # and callbacks execute from dir(script_path) (because you expect to be in the same
        # directory as the script when the callback is firing).

        # This means that rel-ified paths can only be rel'd to one of the two cwds, not both.
        # And that means we disallow mixed cli/callback command lists.

        for command in config.command:
            if type(command) is not type(config.command[0]):
                raise Task.BROKEN(f"Commands aren't the same type: {config.command}")

        # In strict mode, we mark a task broken if its command still has curly braces.
        if Options.strict:
            for command in cast(list, config.command):
                if not isinstance(command, str):
                    continue
                blocks = Expander._split_template(command)
                if len(blocks) > 1 or (len(blocks) == 1 and blocks[0][0] == "{"):
                    raise Task.BROKEN("STRICT: Command has curly braces in it")

        # Check that all build files would end up under build_dir
        for file in self.out_files:
            assert Path.isabs(file)
            if not Path.startswith(file, config.build_dir):
                raise Task.BROKEN(f"Path error, output file {file} is not under build_dir {config.build_dir}")

        # Check for task collisions
        for file in self.out_files:
            real_file = cast(str, Path.real(file))
            if real_file in Loader.real_filenames:
                raise Task.BROKEN(f"TaskCollision: Multiple tasks build {real_file}")
            Loader.real_filenames.add(real_file)

        # Check for missing inputs. We have to check dry_run, as the input files may only exist if
        # we're really running tasks.
        for file in self.in_files:
            if not Path.isabs(file):
                raise Task.BROKEN(f"Somehow we got a non-abs path for an input file - {file}")  # pragma: no cover
            if not Path.exists(file) and not config.dry_run:
                raise Task.BROKEN(f"Input file missing - {file}")

        # Check that task's commands are either strings or callables.
        for command in cast(list, config.command):
            if not isinstance(command, str) and not callable(command):
                raise Task.BROKEN(f"Command {command} is not a string or a callable?")

        # Tasks should have at most one depfile.
        for key, files in list(self.config.items()):
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

        config = self.config

        # Initially, all our file paths are relative to the script that created this task.
        # Join script_dir with the filenames to produce absolute paths.
        script_dir = Path.dirname(config.script_path)
        files = Path.join(script_dir, files)

        # Expanding may have made our files array non-flat, but all of its contents should be
        # absolute paths now.
        files = Utils.flatten(files)
        assert Path.isabs(files)

        # File paths _must_ be normed after joining, otherwise they might look like they're under
        # script_dir, but they're not because the paths could have "../../../../.." in them.
        files = cast(list[str], Path.norm(files))

        # Move all outputs under build_dir and ensure their directories exist.
        # Note - This will also move "in_depfile" under build_dir - this is _intentional_ as it's
        # an _output_ from the compiler.
        if Task.is_output_field(name):
            for i, file in enumerate(files):
                # Note that this conditional and the one below are _NOT_ an if/elif pair!
                if not Path.startswith(file, config.build_dir):  # noqa: SIM102
                    if Path.startswith(file, config.task_cwd):
                        file = file.removeprefix(config.task_cwd)
                        file = config.build_dir + file
                        files[i] = file

                if not config.dry_run and Path.startswith(file, config.build_dir):
                    dirname = Path.dirname(file)
                    os.makedirs(dirname, exist_ok=True) #type:ignore

        # Gather all absolute file paths to in_files/out_files.
        # The check for is_depfile_field must come first, as it's a special case of a file that
        # is technically _both_ an input and an output file, even though its name starts with "in".
        for i in range(len(files)):
            if Task.is_depfile_field(name):
                pass
            elif Task.is_output_field(name):
                self.out_files.append(files[i])
            elif Task.is_input_field(name):
                self.in_files.append(files[i])

        # Convert the fixed paths back to relative so our command lines aren't enormous.
        # Relative paths are relative to task_cwd if we're running a command, otherwise they're
        # relative to script_dir if we're calling a callback.

        #rel_dir = config.task_cwd if isinstance(config.command[0], str) else config.script_dir
        #for i in range(len(files)):
        #    files[i] = Path.rel(files[i], rel_dir)

        return files

    # ----------------------------------------------------------------------------------------------

    async def run_command(self, command):
        config = self.config

        with LogLevel.VERBOSE, Colors.BLUE:
            self.log(f"{Path.rel(config.task_cwd, config.repo_dir)}$ {command}\n")

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
            # Note - this only works on Linux. We will need a slightly different implementation for
            # Windows, which is out of scope until Hancho is shippable.
            if proc is not None:
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

        if proc.returncode:
            raise Task.FAILED(f"Command return code was non-zero : {proc.returncode}")

        if self._stdout or self._stderr:
            with LogLevel.VERBOSE, Log.color(0x666666):
                self.log(self.dump_stdout())

    # ----------------------------------------------------------------------------------------------

    async def call_callback(self, command):
        script_dir = os.path.dirname(self.config.script_path)
        callback_dir = Path.rel(script_dir, self.config.repo_dir)

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

    def log_task_exception(self, message, ex = None):
        with LogLevel.ERROR, Colors.RED:
            Log.log("========================================\n")
            Log.log(message + "\n")
            Log.log("========================================\n")

            Log.log(f"Script    = {self.config.script_path}:\n")
            Log.log(f"Task      = '{self.config.name}' : '{self.config.desc}'\n")
            Log.log(f"os.getcwd = {os.getcwd()}\n")
            Log.log(f"task cwd  = {self.config.task_cwd}\n")
            Log.log(f"command   = {self.config.command}\n")
            if ex:
                Log.log_exception(ex)
            Log.log(self.dump_stdout())

            Log.log("========================================\n")

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

# this has to be a MutableMapping if we want to put it in the ChainMap for locals()
class Expander(abc.MutableMapping[str, Any]):
    """
    This class is used to fetch and expand text templates from a dict during text expansion.
    It allows for both dictionary-like access (using `expander[key]`) and attribute-like access
    (using `expander.key`), making it versatile for accessing template variables and methods.
    """

    def __init__(self, context : Dict):
        self._dict : Dict
        # Don't use our __setattr__, as it's set to raise an assertion if used.
        object.__setattr__(self, "_dict", context)

    # ----------------------------------------
    # MutableMapping interface

    def __getitem__(self, key):
        if key == "_dict":
            return object.__getattribute__(self, "_dict")
        try:
            return self._get(key)
        except AttributeError as ex:
            raise KeyError from ex

    def __setitem__(self, key, val):
        raise AssertionError("Expander.__setitem__ should not be used")

    def __delitem__(self, key):
        raise AssertionError("Expander.__delitem__ should not be used")

    def __iter__(self):
        yield from cast(Dict, self._dict)

    def __len__(self):
        return self._dict.__len__()

    # ----------------------------------------
    # object interface

    def __repr__(self):
        result = f"{self.__class__.__name__} @ {Utils.hex_id(self)}"
        return result

    def __getattr__(self, key):
        if key == "_dict":
            return object.__getattribute__(self, "_dict")
        try:
            return self._get(key)
        except KeyError as ex:
            with LogLevel.ERROR:
                Log.log(traceback.format_exc() + "\n")
            raise AttributeError from ex

    def __setattr__(self, key, val):
        raise AssertionError("Expander.__setattr__ should not be used")

    def __delattr__(self, key):
        raise AssertionError("Expander.__delattr__ should not be used")

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

    sentinel = "<sentinel>"

    # ----------------------------------------

    def expand(self, variant):
        """
        The outer expand function handles setting/resetting the depth/evals-check vars and repeats
        expansion until we reach a non-string or the string stops changing.
        """

        if not Loader.load_started:
            raise AssertionError("Tried to expand {variant} before we've even reached script loading state")

        if variant == Expander.sentinel:
            raise AssertionError("Tried to expand a sentinel value")

        # Recurse early if we're trying to expand a list of strings.
        # This ensures that every template gets its own independent depth and evals check.
        if isinstance(variant, list):
            result = []
            for v in variant:
                # Remember how much budget was spent.
                saved = Expander.cv_evals.get()
                # Expand the list element.
                result.append(self.expand(v))
                # Restore the budget so the next string in the list gets it.
                Expander.cv_evals.set(saved)
            return result

        # Bail out early if our variant isn't a string (a common case if we're expanding {debug} or
        # something) or if it's a string with no macros in it.
        if not (isinstance(variant, str) and '{' in variant):
            return variant

        # Bail out if we've gone through too many levels of recursion.
        if (depth := Expander.cv_depth.get()) >= Expander.MAX_DEPTH:
            raise RecursionError(f"Expansion failed to terminate after {depth} recursions: {variant!r}")
        Expander.cv_depth.set(depth + 1)

        # OK, we have a string that could be a template. Keep expanding it until it stops changing
        # or it's not a template.
        try:
            old_variant = None
            while old_variant != variant and isinstance(variant, str) and '{' in variant:
                old_variant = variant
                with Tracer(self, "expand", variant) as tracer:
                    variant = self._expand_pass(variant)
                    tracer.save_result(variant)
        finally:
            # And then reset the depth/evals check vars when we're done.
            if depth == 0:
                Expander.cv_evals.set(0)
            Expander.cv_depth.set(depth)

        return variant

    # ----------------------------------------
    # IMPORTANT IMPORTANT IMPORTANT
    # If you can't eval a macro, you return it unchanged.
    # TEFINAE : Template Expansion Failure Is Not An Error. Same idea as SFINAE in C++ - we don't
    # fail on expansion failure so we can retry somewhere/somewhen else.

    def _expand_pass(self, template : str):
        """The inner expand function does one split-expand-rejoin pass on the template string."""

        # Split the string into literal and macro blocks.
        blocks = Expander._split_template(template)

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
            with Tracer(self, "eval", block) as tracer:
                try:
                    # None = use this module (hancho) as the global context
                    blocks[i] = eval(block[1:-1], None, self)

                # Note that we do _not_ suppress any BaseExceptions - they _must_ be propagated up to
                # callers. As of Python 3.11, this includes asyncio.CancelledError.
                except RecursionError:
                    raise
                except Exception:
                    # Do NOT print stuff here or it'll spam like mad
                    #traceback.print_exc()
                    pass
                tracer.save_result(blocks[i])

        # If there was only one block in the list, unwrap it.
        if len(blocks) == 1:
            return blocks[0]

        # Otherwise we stringify everything and join the blocks back together.
        return "".join(Utils.stringify(b) for b in blocks)

    # ----------------------------------------

    def _get(self, key):
        """
        Reads and expands a field stored in our context.
        """

        if key == "trace":
            return getattr(self._dict, "trace", False)

        with Tracer(self, "get", key) as tracer:
            result = self._dict[key]
            # We want the expander to show up as the result of _get...
            if isinstance(result, Dict):
                result = result._expander
            tracer.save_result(result)

        # ...but if there's a nested expansion it should show up _under_ the _get part of the trace
        # and not inside it.
        if not isinstance(result, Expander):
            result = self.expand(result)

        return result

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def _split_template(cls, text : str):
        out = []
        cls._split_template2(text, out)
        return out

    @classmethod
    def _split_template2(cls, text : str, out : list[str]):
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
# region Tracer
# Expansion tracing class used by Expander
#
# The traces generated look like this - the EX_XXXX prefix is an identifier for the Expander being
# used so you can tell when the expand context changes, the rest are the call arguments and the
# return values.
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

    def __init__(self, context : Expander, enter_message, name):
        self.enter_message = f"{enter_message}({name!r})"
        self.name = name
        self.color = None
        self.context = context
        self.result = None
        self.trace = getattr(context._dict, "trace", False) or Log.verbosity_out >= LogLevel.TRACE
        if len(self.name) > 40:
            self.name = self.name[:34] + "<snip>"

    def __enter__(self): # pragma: no cover
        if not self.trace:
            return self

        self.color = Utils.obj_to_hex(self.context)

        with LogLevel.TRACE, Log.color(self.color):
            Log.log(f"{Tracer.object_to_tag(self.context)}." + self.enter_message + "\n")
            Log.indent(self.color)

        return self

    def __exit__(self, exc_type, exc_value, tb): # pragma: no cover
        if not self.trace:
            return False

        with LogLevel.TRACE, Log.color(self.color):
            if exc_type:
                Log.log(f"exc_type  : {exc_type}\n")
            if exc_value:
                Log.log(f"exc_value : {exc_value}\n")
            if tb:
                summary = traceback.extract_tb(tb)
                filename, line_no, func_name, _ = summary[-1]
                Log.log(f"location  : {filename} line {func_name}@{line_no}\n")

            type = self.result.__class__.__name__
            color = Utils.obj_to_hex(self.result)

            message = ""
            with Log.color(color):
                if Utils.is_mapping(self.result):
                    message = f"{self.name!r} : {type} = {Tracer.object_to_tag(self.result)}\n"
                elif self.result is None:
                    message = "<None>\n"
                elif self.result == "":
                    message = "<Empty>\n"
                else:
                    message = f"{self.name!r} : {type} = {self.result!r}\n"

            Log.dedent()
            Log.log(message)

        return False

    def save_result(self, result : Any):
        self.result = result

    @staticmethod
    def object_to_tag(obj):
        tag = (str(type(obj).__name__)[:2] + "_" + Utils.hex_id(obj)[-4:]).upper()
        return tag

# endregion
# --------------------------------------------------------------------------------------------------
# region Loader

class Loader:

    class Abort(Exception):    pass # Raised by hancho scripts when they need to stop running due to some error.
    class EarlyOut(Exception): pass # Raised by hancho scripts when they are successful but don't need to do anything else.
    class Fail(Exception): pass     # Script has hit a fatal error

    @classmethod
    def reset(cls, flags):
        cls.match_pointer : re.Pattern = re.compile(r"<(\w+) (\w+) at 0[xX][0-9a-fA-F]+>")
        cls.real_filenames : set[str] = set()
        cls.dedupe : dict[tuple[str, str], Script] = {}
        cls.loaded_files : list[str] = []
        cls.all_repos : list[Repo] = []
        cls.load_started = False

    # ----------------------------------------------------------------------------------------------

    @staticmethod
    def yield_tasks():
        for repo in Loader.all_repos:
            for script in repo.scripts:
                yield from script.tasks

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def load_script(cls, config):

        config.script_name = config.expand("{script_name}")
        config.script_path = config.expand("{script_path}")
        config.script_cwd  = config.expand("{script_cwd}")
        config.is_repo     = config.expand("{is_repo}")

        assert not Utils.is_template(config.script_path)

        config.script_path = os.path.realpath(config.script_path)

        with open(config.script_path, encoding="utf-8") as file:
            source = file.read()

        return Loader.load_str(config = config, source = source)

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def load_str(cls, *, config, source) -> Script:
        """This is split out from load_file for testing purposes."""

        assert os.path.realpath(config.script_path) == config.script_path

        # ----------------------------------------
        # Dedupe the load - only scripts with identical real paths and identical configs are
        # deduped. This relies on __repr__ and the fields read by dump_to_str being stable during a
        # build, which they should be in practice.

        config_dump = Utils.dump_to_str(key = "Config", val = config)
        config_dump = cls.match_pointer.sub(r"<\1 \2 at 0x...>", config_dump)

        dedupe_key = (Path.real(config.script_path), config_dump)
        dedupe = cls.dedupe.get(dedupe_key, None) #type:ignore
        if dedupe is not None:
            with LogLevel.VERBOSE, Colors.SKY:
                Log.log(f"Deduped load of {config.script_path}\n")
            return dedupe

        # ----------------------------------------
        # Not deduped, create a new Script+Module and also a Repo+BuildDB if this script is the
        # root of a new repo.

        with LogLevel.VERBOSE, Colors.ORANGE:
            Log.log(f"Loading {"repo" if config.is_repo else "script"} {config.script_path}\n")

        script_dir, script_name = os.path.split(config.script_path)

        new_module = types.ModuleType(script_name)
        new_module.__file__ = config.script_path
        new_module.hancho = hancho     # type: ignore
        new_module.config = config # type: ignore

        if config.is_repo:
            #new_repo = Repo(script_path, new_config)
            new_repo = Repo(
                name   = config.script_name,
                config = config,
            )
            Loader.all_repos.append(new_repo)
        else:
            new_repo = cv_script.get().script_repo

        new_script = Script(
            name   = config.script_name,
            config = config,
            module = new_module,
            repo   = new_repo,
        )

        new_repo.scripts.append(new_script)

        # ----------------------------------------
        # Exec the module while in script_dir with cv_script pointed at the new script.

        with chdir(script_dir):
            token = cv_script.set(new_script)
            try:
                Log.indent(Colors.ORANGE)
                code = compile(source, config.script_path, "exec", dont_inherit=True)
                exec(code, new_module.__dict__)
            except (Loader.Abort, Loader.EarlyOut):
                pass
            except Loader.Fail as fail:
                raise RuntimeError(f"Script failed : {config.script_path}") from fail
            finally:
                Log.dedent()
                cv_script.reset(token)

        # ----------------------------------------
        # Script created, save to dedupe dict.

        cls.dedupe[dedupe_key] = new_script #type:ignore
        cls.loaded_files.append(config.script_path)

        return new_script

# endregion
# --------------------------------------------------------------------------------------------------
# region Runner

class Runner:

    @classmethod
    def reset(cls, flags):
        cls.core_max  : int = flags.pop("core_max", os.cpu_count() or 1)
        cls.core_sem  : asyncio.Semaphore = asyncio.Semaphore(cls.core_max)
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

        if count > cls.core_max: # pragma: no cover
            raise ValueError(f"Tried to acquire {count} cores, which exceeds the max {cls.core_max}")
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
        if Options.target:
            # Enable all tasks whose name matches the target regex
            # NOTE - We have to expand "name" _before_ the task has initialized, which means some
            # of its input fields may be Task references and the resulting name may be wonky if it
            # includes those names via template. Maybe don't do that.
            target_regex = re.compile(Options.target)

            for task in Loader.yield_tasks():
                name = task.config.expand("{name}", str)
                if target_regex.search(name):
                    task.enable_task()

        elif Options.build_all:
            cls.enable_all_tasks()

        else:
            # Enable all tasks that were generated by the root repo or the first loaded repo.
            for task in Loader.yield_tasks():

                if Main.hancho_repo and task.script.repo == Main.hancho_repo:
                    task.enable_task()

                if Main.root_repo and task.script.repo == Main.root_repo:
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
            if task.config.enabled:
                task.create_aio_task()

        # ------------------------------------
        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.

        with LogLevel.VERBOSE, Colors.BLUE:
            Log.log("Running tasks...\n")

        time_a = time.perf_counter()
        while cls.live_aio_tasks and cls.count_failures() <= Options.max_errors:
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

        if cls.count_failures() > Options.max_errors:
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
                build_root = Path.real(task.config.expand("{build_root}", str))
                build_root = Path.rel(build_root, os.getcwd())
                if Path.isdir(build_root):
                    Log.log(f"Wiping build_root {build_root}\n")
                    shutil.rmtree(build_root, ignore_errors=True)
            Log.log("Clean done\n")
            return 0
        else:
            raise AssertionError(f"Don't know how to run tool {tool}")

# endregion
# --------------------------------------------------------------------------------------------------
# region Main

class Main:

    # This is the "script" associated with hancho.py itself - used if you're driving hancho
    # directly instead of going through .hancho scripts.
    hancho_repo   = None
    hancho_script = None

    # And this is the very first script .load()ed.
    root_repo = None
    root_script = None

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def main(cls, flags):
        # Top-level exception handler just so we can print a big red "SOMETHING BROKE ALL BAD"
        # message if we failed to catch an exception during load/build.
        # The 'except' clause should catch Exception and not BaseException so ctrl-c doesn't get
        # misinterpreted as a Hancho bug.
        try:

            # ----------------------------------------
            # INIT

            root_repo_dir    = flags.pop("repo_dir")
            root_script_path = flags.pop("script_path")

            Main.init(flags)

            assert cls.hancho_script
            assert cls.root_script

            Main.banner_start(
                cls.hancho_script.script_config.script_path,
                root_script_path
            )

            # ----------------------------------------
            # LOAD

            time_a = time.perf_counter()
            #cls.load_top_script(root_script_dir, root_script_file, root_repo_dir)
            #cls.load_top_script(root_script_path = root_script_path, root_repo_dir = root_repo_dir)
            assert Main.hancho_script is not None

            root_config = Dict(
                repo_dir    = root_repo_dir,
                script_name = os.path.basename(root_script_path),
                script_path = root_script_path,
                script_cwd  = root_repo_dir,
                is_repo     = True
            )

            root_config.merge(flags)

            Loader.load_started = True
            cls.root_script = Loader.load_script(root_config)
            cls.root_repo   = cls.root_script.script_repo

            time_b = time.perf_counter()

            with LogLevel.VERBOSE, Colors.BLUE:
                Log.log(f"Loading scripts took {time_b - time_a} seconds\n")

            # ----------------------------------------
            # BUILD

            time_a = time.perf_counter()
            result = Main.build()
            time_b = time.perf_counter()

            with LogLevel.VERBOSE, Colors.GREEN:
                Log.log(f"Build took {time_b - time_a} seconds\n")

            # ----------------------------------------
            # DONE

            Main.banner_end()
            return result

        except Exception as ex:
            with LogLevel.ERROR:
                Log.log("\n")
                with Log.color(0xFFFF00):
                    Log.log("Hancho hit an exception during startup:\n")
                Log.log(f"  cwd  = {os.getcwd()}\n")
                Log.log_exception(ex)
                Log.log("\n")
                Log.log("BUILD FAILED\n")
            return 1
        finally:
            # Don't leave the last line of the log sitting in line_buffer!
            Log.flush()

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def init(cls, *args, **kwargs):
        """
        (Re-)initializes all of Hancho.
        If you are importing Hancho directly, you should call this as
        hancho.init(verbosity = "debug", myoption=1234)
        """

        flags = Dict(*args, kwargs)

        Log.reset(flags)
        Utils.reset(flags)
        Options.reset(flags)
        Task.reset(flags)
        Runner.reset(flags)
        Loader.reset(flags)

        # Whatever flags were not consumed by our classes go into root_config

        hancho_config = get_defaults()

        hancho_config.merge(
            hancho_dir  = os.path.dirname(__file__),
            repo_dir    = os.path.dirname(__file__),
            script_name = "hancho",
            script_path = __file__,
            script_cwd  = os.getcwd(),
            is_repo     = True
        )

        hancho_config.merge(flags)

        # def __init__(self, *, name : str, config : Dict):
        hancho_repo   = Repo(
            name   = hancho_config.script_name,
            config = hancho_config,
        )

        hancho_module = sys.modules[__name__]

        # def __init__(self, *, config : Dict, module : types.ModuleType, repo : Repo):
        hancho_script = Script(
            name   = hancho_config.script_name,
            config = hancho_config,
            module = hancho_module,
            repo   = hancho_repo,
        )
        hancho_repo.scripts.append(hancho_script)

        # A real script module has 'hancho' and 'config' pre-set. I don't know that we _need_ to do
        # that here, but might as well.
        hancho_module.hancho = hancho      # type: ignore
        hancho_module.config = hancho_config # type: ignore

        cls.hancho_script = hancho_script
        cls.hancho_repo   = hancho_repo

        cv_script.set(hancho_script)

        # ----------------------------------------

        Loader.all_repos.append(hancho_repo)

        assert not Utils.is_template(cls.hancho_repo.config.repo_dir)


    # ----------------------------------------------------------------------------------------------
    # Startup banner

    @classmethod
    def banner_start(cls, root_script_path, top_script_path):

        with LogLevel.VERBOSE, Colors.LIME:
            Log.log(f"Hancho started as '{" ".join(sys.argv)}'\n")
            Log.log(f"Verbosity is {Log.verbosity_out}\n")

            if Log.verbosity_out >= LogLevel.TRACE:
                Log.log("Trace mode on\n")
            if Log.verbosity_out >= LogLevel.DEBUG:
                Log.log("Debug mode on\n")
            if Log.verbosity_out >= LogLevel.VERBOSE:
                Log.log("Verbose mode on\n")

            Log.log(f"Hancho root at {root_script_path}\n")
            Log.log(f"Hancho repo at {top_script_path}\n")
            Log.log(f"Hancho top script at {top_script_path}")

    # ----------------------------------------------------------------------------------------------

    build_started = False

    @classmethod
    def build(cls):

        # Do _not_ update stats after running a tool, just early out.
        if Options.tool:
            result = Runner.run_tool(Options.tool)
            return

        # ------------------------------------

        time_a = time.perf_counter()
        for repo in Loader.all_repos:
            cls.pre_build(repo)
        time_b = time.perf_counter()

        print("pre_build done")
        with LogLevel.DEBUG, Colors.BLUE:
            Log.log(f"Loading stats took {time_b - time_a:8.6f} seconds\n")

        # ------------------------------------

        Runner.select_root_tasks()
        cls.build_started = True
        result = Runner.sync_run_tasks()
        print("build done")

        # ------------------------------------

        time_a = time.perf_counter()
        for repo in Loader.all_repos:
            cls.post_build(repo)
        time_b = time.perf_counter()

        print("post_build done")
        with LogLevel.DEBUG, Colors.BLUE:
            Log.log(f"Saving stats took {time_b - time_a:8.6f} seconds\n")

        return result

    # ----------------------------------------------------------------------------------------------
    # This must happen _after_ all repos are loaded (so that if they change repo_dir we don't get
    # the old path), but _before_ we build any tasks.

    @classmethod
    def pre_build(cls, repo):
        build_db = repo.build_db
        build_db.old_stat_db = build_db.load_stat_db()
        print("---------")
        print(repo.config)

        pass

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def post_build(cls, repo):
        if repo.config.dry_run:
            return

        build_db : BuildDB = repo.build_db

        stat_db_path = build_db.config.expand(build_db.stat_db_path2, str)
        stat_db_path = cast(str, Path.abs(stat_db_path))
        comp_db_path = build_db.config.expand(build_db.comp_db_path2, str)
        comp_db_path = cast(str, Path.abs(comp_db_path))

        # Gather stats from all completed tasks
        stat_db = {}
        comp_db = {}

        tasks = [t for script in repo.scripts for t in script.tasks]

        for task in tasks:
            if isinstance(task._error, (Task.CANCELLED, Task.BROKEN, Task.FAILED)) or not task._complete:
                continue

            for file in task.in_files:
                build_db.update_stat_db(stat_db, file)

                # Haven't tested this in an IDE, but I think it matches the spec.
                comp_db[file] = {
                    "directory" : task.config.task_cwd,
                    "command"   : BuildDB.commands_to_string(task.config.command),
                    "file"      : file,
                }

            if "in_depfile" in task.config:
                deplines = Utils.load_depfile(task.config.in_depfile, task.config.depformat, task.config.task_cwd)
                for file in deplines:
                    build_db.update_stat_db(stat_db, file)

            for file in task.out_files:
                str_command = BuildDB.commands_to_string(task.config.command)
                build_db.update_stat_db(stat_db, file, str_command)

        with LogLevel.DEBUG, Colors.ORANGE:
            Log.log(f"┌ Repo {repo.name} post-build\n")
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
            Log.log(f"└ Repo {repo.name} done\n")

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
            for repo in Loader.all_repos:
                Log.log(f"Repo stats for {repo.name}\n")
                Log.indent(Colors.BLUE)
                for k, v in repo.build_db.reasons.items():
                    Log.log(f"Rebuild reasons {k:13} = {v}\n")
                Log.dedent()

# endregion
# --------------------------------------------------------------------------------------------------
# region aliases

# These are aliases for methods in Hancho that have been pulled out so they can be used by
# template expansion. This lets you do {flatten(x)} instead of {Utils.flatten(x)} in macros.

init  = Main.init
build = Main.build

path = os.path
abs  = Path.abs
base = Path.base
ext  = Path.ext
norm = Path.norm
real = Path.real
rel  = Path.rel
stem = Path.stem
dirname = Path.dirname
def load(script_path, *args, **kwargs):
    new_config = Dict(
        cv_script.get().script_config,
        script_name = "{base(script_path)}",
        script_path = script_path,
        script_cwd  = "{dir(script_path)}",
        is_repo     = False,
    )

    new_config.merge(*args, kwargs)

    result = Loader.load_script(new_config)

    return result.script_module

def repo(script_path, *args, **kwargs):
    new_config = Dict(
        cv_script.get().script_config,
        script_name = "{base(script_path)}",
        script_path = script_path,
        script_cwd  = "{dir(script_path)}",
        is_repo     = True,
    )

    new_config.merge(*args, kwargs)

    result = Loader.load_script(new_config)

    return result.script_module

def log(*args, **kwargs):
    return Log.log(*args, **kwargs)

cwd = os.getcwd
flatten = Utils.flatten
run_cmd = Utils.run_cmd
weave   = Utils.weave

def task(*args, **kwargs):
    if len(args) and callable(args[0]):
        # Take hancho.task(callable, ...) and instead of creating a task, collect all the args into
        # a dict and then splat it into the callback.
        return args[0](**Dict(cv_script.get().script_config, *args[1:], kwargs))
    else:
        return Task(*args, **kwargs)

def fail(message):
    with LogLevel.ERROR, Colors.RED:
        frame = sys._getframe(1)
        Log.log("Script failed:\n")
        Log.log(f"  text = '{message}'\n")
        Log.log(f"  file = {frame.f_code.co_filename}\n")
        Log.log(f"  func = {frame.f_code.co_name}\n")
        Log.log(f"  line = {frame.f_lineno}\n")
    raise Loader.Fail()

def abort(message):
    with LogLevel.WARNING, Colors.YELLOW:
        frame = sys._getframe(1)
        Log.log("Script aborted:\n")
        Log.log(f"  text = '{message}'\n")
        Log.log(f"  file = {frame.f_code.co_filename}\n")
        Log.log(f"  func = {frame.f_code.co_name}\n")
        Log.log(f"  line = {frame.f_lineno}\n")
    raise Loader.Abort()

def earlyout(message = ""):
    with LogLevel.VERBOSE, Colors.LIME:
        frame = sys._getframe(1)
        Log.log("Script exited early:\n")
        Log.log(f"  text = '{message}'\n")
        Log.log(f"  file = {frame.f_code.co_filename}\n")
        Log.log(f"  func = {frame.f_code.co_name}\n")
        Log.log(f"  line = {frame.f_lineno}\n")
    raise Loader.EarlyOut()

# endregion
# --------------------------------------------------------------------------------------------------
# region __main__

def _start():
    flags = Options.parse_flags(sys.argv[1:])
    if __name__ == "__main__":
        result = Main.main(flags)
        sys.exit(result)
    else:
        Main.init(flags)
        Loader.load_started = True

_start()

# endregion
