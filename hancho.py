#!/usr/bin/python3
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
import copy
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
import types
from collections import ChainMap, abc
from contextlib import chdir, contextmanager, suppress
from enum import Enum
from inspect import isawaitable
from typing import Any, cast

hancho = sys.modules[__name__]
sys.modules["hancho"] = hancho

# Config fields often have arbitrarily nested lists of stuff due to things like
#
#     obj1 = [foo.o, bar.o]
#     link(in_objs = [objs1, ...])
#
# and so we define a 'Tree' type that is basically 'either a T, or arbitrarily nested list of T'
# This is only used as a type annotation, but be aware when reading the functions below that
# some of them look like they operate on Ts, but they've been 'recursified' to work on Tree[T]s.

type Tree[T] = T | list[Tree[T]]

# endregion
# --------------------------------------------------------------------------------------------------
# region Log

class Log:

    @classmethod
    def reset(cls):
        cls.start  : float = time.time( )
        cls.buffer : str  = ""
        cls.indent_depth : int  = 0
        cls.current_color  : int  = -1
        cls.line_buffer : str = ""
        cls.match_escapes = re.compile(r"(\x1B.*?m)")
        cls.reset_color = "\x1B[0m"

    # ----------------------------------------------------------------------------------------------

    @staticmethod
    @contextmanager
    def color(new_color):
        try:
            old_color = Log.current_color
            Log.current_color = new_color
            yield
        finally:
            Log.current_color = old_color

    @staticmethod
    @contextmanager
    def indent():
        # Not dead, used in test suites
        try:
            Log.indent_depth += 1
            yield
        finally:
            Log.indent_depth -= 1

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def log(cls, text):
        if not isinstance(text, str) or len(text) == 0:
            return

        color_hex = cls.current_color
        r, g, b = ((color_hex >> 16) & 0xFF, (color_hex >>  8) & 0xFF, (color_hex >>  0) & 0xFF)
        color_prefix = f"\x1B[38;2;{r};{g};{b}m" if color_hex >= 0 else ""
        color_suffix = cls.reset_color if color_hex >= 0 else ""

        for i, line in enumerate(text.split("\n")):
            if cls.line_buffer == "":
                cls.line_buffer += cls.get_timestamp() + " " + cls.get_indentation()

            if i > 0:
                cls.line_buffer += "\n"

            if line:
                cls.line_buffer += color_prefix + line + color_suffix

            if cls.line_buffer[-1] == '\n':
                if not Options.wrap:
                    cls.line_buffer = Log.clip_printable(cls.line_buffer, Options.con_w)
                cls.line_buffer += cls.reset_color
                # Ensure that QUIET mutes absolutely everything
                if Options.verbosity > LogLevel.QUIET:
                    sys.stdout.write(cls.line_buffer)
                Log.buffer += cls.line_buffer
                cls.line_buffer = ""

    @classmethod
    def log_exception(cls, ex):
        frame = traceback.extract_tb(ex.__traceback__)[-1]
        Log.log(f"type      = {type(ex)}\n")
        Log.log(f"message   = '{ex}'\n")
        Log.log(f"location  = {frame.filename} {frame.name} @ {frame.lineno}\n")
        Log.log(f"line      = '{frame.line}'\n")

    @classmethod
    def get_timestamp(cls):
        """Returns the timestamp string that is placed at the left of log entries."""
        return f"[{time.time() - Log.start:12.6f}]"

    @classmethod
    def get_indentation(cls):
        return "│ " * cls.indent_depth

    @classmethod
    def clip_printable(cls, text, width) -> str:
        """
        Clips a string with embedded escape codesf (such as ANSI color codes) so that it fits in
        'width' without breaking the escape codes.

        If the printable portion exceeds 'width', it will be clipped and capped with '...'.
        """
        if not text or not isinstance(text, str) or len(text) < 3:
            return text

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
        # clip the last chunk and add "..."
        accum = 0
        result = ""
        for i, chunk in enumerate(chunks):
            if i & 1:
                result += chunk
            else:
                accum += len(chunk)
                if accum > width - 3:
                    result += chunk[:-(accum - width + 3)] + "..."
                    break
                else:
                    result += chunk

        if newline:
            result += '\n'

        return result

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

    # WARNING - This __bool__ conversion does not do what you think. It's here because it's what
    # lets us say "if LogLevel.VERBOSE: <print stuff>".
    #
    # It's comparing the enum in the 'if' with the global verbosity setting in 'Options.verbosity',
    # which is _not_ what you might expect by default. It's a really useful bit of syntactic sugar
    # though, so it'll stay for now.

    def __bool__(self):
        return self.value <= Options.verbosity

#endregion
# --------------------------------------------------------------------------------------------------
#region Colors

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
    RESET   = -1  # The "go back to default" color :D

# endregion
# --------------------------------------------------------------------------------------------------
# region Utils

class Utils:

    @classmethod
    def reset(cls):
        cls.mtime_calls : int = 0

    # These types are considered already "flat" and don't need to be turned into a list.
    flat_types = (str, bytes, bytearray, range, abc.Mapping)

    # These types don't get dumped because they're either uninteresting or not really dumpable.
    opaque_types = types.MappingProxyType({
        types.FunctionType        : "<function>",
        types.BuiltinFunctionType : "<builtin>",
        types.ModuleType          : "<module>",
        types.GeneratorType       : "<generator>",
    })

    # These types don't need a type annotation when dumped.
    base_types = (str, bool, int, float, list, tuple, set, bytes, bytearray, range, type(None),
                  *opaque_types.keys())

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def dump_to_str(cls, key, val, indent = 0, print_id = False, max_width = 80, tab = "  ", flat = False):
        """
        Hancho's pretty-printer for various types. Note that this is also used for script deduping:
        if you load "my/app/tools/stuff.hancho" multiple times but the configurations you gave it
        were identical, you should get one copy of the "stuff" module instead of two.

        As long as you're not doing something bizarre with configs or changing the dumper in the
        middle of a build, the resulting strings should be stable enough to use for deduping.
        """

        # Generate the "key : type = " prefix.
        prefix = ""
        if key is not None:
            prefix += str(key) + " "
        if not isinstance(val, Utils.base_types):
            prefix += ": " + type(val).__name__ + " "
        if print_id:
            prefix += ": " + hex(id(val)) + " "
        if prefix:
            prefix += "= "

        # Don't recurse into a few types that need special handling
        if isinstance(val, Task):
            val = f"<Task {val._config.name}>"
        elif isinstance(val, Expander):
            val = "<Expander>"
        elif isinstance(val, contextvars.Context):
            val = "<Context>"
        elif isinstance(val, types.ModuleType):
            val = f"<Module {val.__name__}>"

        if isinstance(val, argparse.Namespace):
            val = val.__dict__

        # Non-containers are always emitted on one line. If they overflow, they overflow.
        if not (Utils.is_collection(val) or Utils.is_mapping(val)):
            # Objects that don't have a custom repr (and a few built-in types) just get printed as
            # '<object>'
            if type(val) in Utils.opaque_types:
                return (tab * indent) + prefix + Utils.opaque_types[type(val)] # type: ignore
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
            items = val.items()
            ld = "{"
            rd = "}"
        elif Utils.is_collection(val):
            items = [(None, val2) for val2 in cast(abc.Collection, val)]
            ld = "["
            rd = "]"
        else:
            raise AssertionError(f"Don't know what to do with {type(val)}")

        # Iterate over our key-value pairs, converting them in to string chunks. If the resulting line
        # would be too wide and we're not trying to generate a flat string, fall back to multi-line.
        pad = (tab * indent)
        separator = ", "
        chunks = []
        num_separators = len(items) - 1 if len(items) else 0
        width = len(pad) + len(prefix) + len(ld) + (len(separator) * num_separators) + len(rd)

        for k, v in items:
            chunk = Utils.dump_to_str(k, v, 0, print_id, max_width, tab, True)
            if chunk is None or width + len(chunk) > max_width:
                if flat:
                    return None
                separator = ",\n"
                chunks = (Utils.dump_to_str(k, v, indent + 1, print_id, max_width, tab, False) for k, v in items)
                return pad + prefix + ld + "\n" + separator.join(chunks) + "\n" + pad + rd
            width += len(chunk)
            chunks.append(chunk)

        # Done, we can fit this dump on one line.
        return pad + prefix + ld + separator.join(chunks) + rd

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
            return str(variant)

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
        Generates a 'random' float in the range [0.0,1.0) out of the object's ID. Multiplying by
        an irrational constant (phi in this case) helps ensure that colors don't collide
        unnecessarily in obj_to_hex below.
        """
        return (id(obj) * 0.618033988749895) % 1.0

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
    def mtime(filename : str):
        """Gets the file's mtime and tracks how many times we've called mtime()"""
        Utils.mtime_calls += 1
        return os.stat(filename).st_mtime_ns

    @staticmethod
    def flatten(variant: Tree[Any], out : list | None = None):
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

# endregion
# --------------------------------------------------------------------------------------------------
# region Path
# These functions wrap the os.path.* functions so that they work on Tree[str]

class Path:

    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with simple string manipulation.

    @staticmethod
    def rel(lhs, rhs):
        if Utils.is_collection(lhs):
            return [Path.rel(lh, rhs) for lh in lhs]
        if Utils.is_collection(rhs):
            return [Path.rel(lhs, rh) for rh in rhs]
        return lhs.removeprefix(rhs + "/") if lhs != rhs else "."

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

    @staticmethod
    def startswith(path, parent):
        # WARNING - 'startswith' can throw ValueError if there's a mix of abs/rel paths, or if the
        # paths are on different volumes in Windows. We don't handle this yet, but we will need to
        # eventually.
        if Utils.is_collection(path):
            return all(Path.startswith(p, parent) for p in path)
        return os.path.commonpath([path, parent]) == parent

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
        self.merge(*args, **kwargs)

    # ----------------------------------------

    def merge(self, *args, **kwargs):
        # Ignore Nones and empty dicts.
        for arg in filter(None, (*args, kwargs)):
            assert Utils.is_mapping(arg)
            for key, rval in arg.items():
                lval = dict.get(self, key, None)

                # Mappings get turned into Dicts. If they're already Dicts, this just makes a copy
                # of them. Pairs of mappings get merged together.
                if Utils.is_mapping(rval):
                    rval = Dict(lval, rval) if Utils.is_mapping(lval) else Dict(rval)

                # Collections get turned into lists. Same as above.
                if Utils.is_collection(rval):
                    rval = copy.deepcopy(rval)

                if lval is None or rval is not None:
                    dict.__setitem__(self, key, rval)

    # ----------------------------------------
    # Object

    def on_keyerror(self, key):
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
        return Utils.dump_to_str(key = getattr(self, "name", "_"), val = self)

    # ----------------------------------------
    # Expander convenience helpers

    def expand[T](self, text : Any, as_type : type[T] = object) -> T:
        result = Expander._expand(text, self)
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
    def reset(cls, root_config):
        cls.con_w       = shutil.get_terminal_size().columns

        # Pull options that aren't task-specific off the root config.

        cls.core_max    = root_config.pop("core_max", os.cpu_count() or 1)
        cls.max_errors  = root_config.pop("max_errors", 0)
        cls.rebuild     = root_config.pop("rebuild", False)
        cls.strict      = root_config.pop("strict", True)
        cls.target      = root_config.pop("target", None)
        cls.tool        = root_config.pop("tool", None)
        cls.wrap        = root_config.pop("wrap", False)

        # Handle all the verbosity-related flags

        verbosity = root_config.pop("verbosity", None)
        trace     = root_config.pop("trace", False)
        debug     = root_config.pop("debug", False)
        verbose   = root_config.pop("verbose", False)
        quiet     = root_config.pop("quiet", False)

        if isinstance(verbosity, str):
            verbosity = LogLevel[verbosity.upper()]
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

        cls.verbosity = verbosity

        # Set up our config contextvar

        if not hasattr(cls, "_cv_config"):
            cls._cv_config : contextvars.ContextVar = contextvars.ContextVar("config")
        if hasattr(cls, "_cv_token"):
            cls._cv_config.reset(cls._cv_token)

        cls._cv_token : contextvars.Token = cls._cv_config.set(root_config)

    @classmethod
    def cv_config(cls):
        return cls._cv_config.get()

    # ----------------------------------------------------------------------------------------------
    # We spell all these defaults out explicitly so that when this config gets merged with flags and
    # task configs the fields stay in the same order.
    # This is a function so that when we re-initialize Hancho during tests, we pick up a fresh
    # copy of os.getcwd() if it changed.

    @classmethod
    def default_config(cls):
        result = Dict(
            name        = "_",
            desc        = "_",
            command     = None,

            this_repo   = hancho,
            this_module = hancho,

            hancho_dir  = os.path.dirname(__file__),
            root_dir    = os.getcwd(),
            root_file   = "build.hancho",
            repo_dir    = "{root_dir}",
            repo_file   = "{root_file}",
            script_cwd  = "{repo_dir}",
            script_file = "{root_file}",
            task_cwd    = "{repo_dir}",
            build_root  = "{repo_dir}/build",
            build_tag   = "",
            build_dir   = "{build_root}/{build_tag}/{rel(task_cwd, repo_dir)}",

            depformat   = "gcc" if sys.platform != "win32" else "msvc",
            in_depfile  = [],

            core_count  = 1,
            enabled     = False,
            dry_run     = False
        )
        return result

    @classmethod
    def parse_flags(cls, args : list[str]):
        assert Utils.is_collection(args)

        parser = argparse.ArgumentParser()

        # pylint: disable=line-too-long
        # fmt: off
        parser.add_argument("target",  nargs="?", default=argparse.SUPPRESS, type=str.strip,       help="A regex that selects the targets to build. Defaults to all targets in the root repo.")
        parser.add_argument("-C", "--root_dir",   default=argparse.SUPPRESS, type=str.strip,       help="Change directory before starting the build")
        parser.add_argument("-f", "--root_file",  default=argparse.SUPPRESS, type=str.strip,       help="Input .hancho file - defaults to 'build.hancho'")
        parser.add_argument("-t", "--tool",       default=argparse.SUPPRESS, type=str.strip,       help="Run a subtool.")
        parser.add_argument("--build_tag",        default=argparse.SUPPRESS, type=str.strip,       help="Set the build tag. Tagged builds will have separate subdirectories under the build directory.")
        parser.add_argument("-j", "--core_max",   default=argparse.SUPPRESS, type=int,             help="Run jobs on N cores in parallel (default = cpu_count)")
        parser.add_argument("--max_errors",       default=argparse.SUPPRESS, type=int,             help="The maximum number of task errors we tolerate before abandoning the build")
        parser.add_argument("-n", "--dry_run",    default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Do not run commands")
        parser.add_argument("-a", "--rebuild",    default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Build absolutely everything in all build scripts loaded.")
        parser.add_argument("--wrap",             default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Wrap lines around the console instead of clipping them")
        parser.add_argument("--strict",           default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Checks for common footguns like typo'd templates")
        parser.add_argument("-q", "--quiet",      default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Shortcut for --verbosity=quiet. Mute all output")
        parser.add_argument("-d", "--debug",      default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Shortcut for --verbosity=debug. Print debugging information")
        parser.add_argument("--trace",            default=argparse.SUPPRESS, action = argparse.BooleanOptionalAction, help="Shortcut for --verbosity=trace. Trace all text expansion")

        choices = [v.lower() for v in LogLevel.__members__]
        parser.add_argument("-v", "--verbosity", choices=choices, help="Select verbosity level. Quiet = none, Trace = maximal spam")


        # fmt: on

        (flags, unrecognized) = parser.parse_known_args(args)

        # Unrecognized command line parameters also become module config fields if they are
        # flag-like.
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
# region Task
# Task object + bookkeeping

class Task:

    @classmethod
    def reset(cls):
        cls.id_counter : int = 0
        cls.tasks_enabled : int = 0

    class FAILED(Exception):    pass  # noqa: E701
    class CANCELLED(Exception): pass  # noqa: E701
    class SKIPPED(Exception):   pass  # noqa: E701
    class BROKEN(Exception):    pass  # noqa: E701

    def __init__(self, *args, **kwargs):
        # Save the context, we will use it when we create the asyncio.Task
        self._context = contextvars.copy_context()
        self._config  = Dict(Options.cv_config(), *args, **kwargs)
        self._expander  = Expander.wrap(self._config)

        # Why this task rebuilt, or "" if it did not need to rebuilds.
        self._reason = ""

        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._aio_task : asyncio.Task | None = None

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

        self._in_files  = []
        self._out_files = []

        Runner.all_tasks.append(self)

        if Utils.in_event_loop():
            self.enable()

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
        for i, line in enumerate(message.split("\n")):
            if i > 0:
                Log.log("\n")
            if line:
                with Log.color(Colors.LIME):
                    Log.log(f"[{self._task_id:3d}/{Task.tasks_enabled:3d}] ")
                Log.log(line)

    # ----------------------------------------------------------------------------------------------

    def enable(self):
        if not self._config.enabled:
            self._config.enabled = True
            Task.tasks_enabled += 1
            if Utils.in_event_loop():
                self.create_aio_task()

    def create_aio_task(self):
        assert Utils.in_event_loop()

        if self._aio_task is None:
            t = asyncio.create_task(self.task_top(), context=self._context)
            Runner.live_aio_tasks.add(t)
            t.add_done_callback(lambda t: Runner.aio_done_queue.put_nowait(t))
            self._aio_task = t

        # Start all tasks referenced by _config so we don't deadlock while waiting for them.
        Utils.visit(self._config, lambda task: isinstance(task, Task) and task.enable())

    # ----------------------------------------------------------------------------------------------

    async def task_top(self):
        try:
            await self.task_main()
        except asyncio.CancelledError as ex:
            if LogLevel.VERBOSE:
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
            if LogLevel.VERBOSE:
                self.log(str(ex) + "\n")
            self._error = ex
        except Task.SKIPPED as ex:
            if LogLevel.VERBOSE:
                self.log(str(ex) + "\n")
            self._error = ex
        except Exception as ex:
            self.log_task_exception("Task threw an exception!", ex)
            self._error = ex
        finally:
            if self._core_count:
                Runner.release(self._core_count)
                self._core_count = 0

        if self._error:
            raise self._error

        dry_run = " (DRY RUN)" if self._config.dry_run else ""
        if LogLevel.VERBOSE:
            self.log(f"Task done{dry_run}: '{self._config.name}' - '{self._config.desc}'\n")
        return self._out_files

    # ----------------------------------------------------------------------------------------------

    async def task_main(self):
        config = self._config
        expand = self._expander

        Task.id_counter += 1
        self._task_id = Task.id_counter

        if LogLevel.DEBUG:
            self.log("Task config before expand:\n")
            self.log(str(config) + "\n")

        # ----------------------------------------
        # Expand all fields that don't depend on input/output filenames (basically everything
        # except name/desc/command). To prevent expansion-order issues, we expand to a temp Dict
        # and then copy them back into config.

        # We _can't_ expand input/output paths here as they may refer to output paths for tasks
        # that haven't executed yet - that has to happen _after_ awaiting our dependencies, so
        # you'll find it in task_init below.

        path_fields  = ["build_dir", "build_root", "hancho_dir", "repo_dir", "repo_file",
                        "root_dir", "root_file", "script_cwd", "script_file", "task_cwd"]

        flag_fields = [ "build_tag", "core_count", "depformat", "dry_run", "enabled", ]

        temp = Dict()

        for f in path_fields:
            if f in config:
                temp[f] = Path.norm(expand[f])
        for f in flag_fields:
            if f in config:
                temp[f] = expand[f]

        for k, v in temp.items():
            config[k] = v

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

        # ----------------------------------------
        # Dry runs early out after all the task checks but before we allocate cores and run
        # commands.

        if config.dry_run:
            return

        # ----------------------------------------
        # Wait for enough jobs to free up to run this task.

        await Runner.acquire(config.core_count)
        self._core_count = config.core_count

        # ----------------------------------------
        # Run all the task's commands

        self.log(f"Task started : '{config.name}' - '{config.desc}'\n")
        if LogLevel.VERBOSE:
            self.log(f"Task rebuilding because: {self._reason}\n")

        for command in cast(list, config.command):
            if isinstance(command, str):
                await self.run_command(command)

            elif callable(command):
                await self.call_callback(command)
            else:
                raise Task.FAILED(f"Command {command} is not a string or a callable?")

        # Done!

    # ----------------------------------------------------------------------------------------------

    async def await_inputs(self):

        # Copy the dict key-values, as it's generally a bad idea to modify a container you're
        # iterating over - _especially_ if it has an await in the middle of it.
        items = list(self._config.items())
        temp = Dict()

        for key, files in items:
            if not Task.is_input_field(key):
                continue

            # Our file list has never been flattened, so do it now.
            files = Utils.flatten(files)

            if key == "in_depfile" and len(files) > 1:
                raise Task.BROKEN("Tasks can't have more than one dependency file!")

            for i, file in enumerate(files):
                if isinstance(file, Task):
                    task = cast(Task, file)
                    if task._aio_task is None:
                        raise AssertionError("One of a task's input sub-tasks was not started")
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
                        raise Task.CANCELLED(f"Task is cancelled: '{self._config.name}' : '{self._config.desc}'") from ex

                    files[i] = task._out_files

            # Awaiting inputs has probably un-flattened our input fields. Re-flatten them.
            temp[key] = Utils.flatten(files)

        # We've awaited everything, copy the file lists back into the config.
        for k, v in temp.items():
            self._config[k] = v

    # ----------------------------------------------------------------------------------------------

    def task_init(self):
        config = self._config
        expand = self._expander

        if os.getcwd() != config.task_cwd:
            raise AssertionError("Running task_init while we're not in task's cwd")

        # ----------------------------------------
        # Flatten the commands and check that they're valid

        config.command = Utils.flatten(config.command)

        if not config.command:
            raise Task.BROKEN(f"Task {config.name} has no command!")

        for command in config.command:
            if command == "":
                raise Task.BROKEN("Command is an empty string")

            # In order to provide the least amount of bafflement to users, CLI commands execute
            # from task_cwd (which is usually the root of the repo, the most common cwd)
            # and callbacks execute from script_cwd (because you expect to be in the same directory
            # as the script when the callback is firing).

            # This means that rel-ified paths can only be  rel'd to one of the two cwds, not both.
            # And that means we disallow mixed cli/callback command lists.

            if type(command) is not type(config.command[0]):
                raise Task.BROKEN(f"Commands aren't the same type: {config.command}")

        # ----------------------------------------
        # Check for missing paths

        if not Path.exists(config.task_cwd):
            raise Task.BROKEN(f"Task working directory '{config.task_cwd}' does not exist")

        if not Path.startswith(config.build_dir, config.repo_dir):
            raise Task.BROKEN(f"The build_dir {config.build_dir} is not under repo dir {config.repo_dir}")

        # ----------------------------------------
        # Expand all in_ and out_ filenames.

        # We _must_ expand _all_ of these first before joining paths or the paths will be incorrect:
        # prefix + swap(abs_path) != abs(prefix + swap(path)). And just out of paranoia, we expand
        # to a temp Dict, norm them (because who knows how the user may have written the template)
        # and then copy them back into the config.

        temp = Dict()

        for key, files in config.items():
            if Task.is_io_field(key):
                temp[key] = Path.norm(self._expander.expand(files))

        for k, v in temp.items():
            config[k] = v

        # ----------------------------------------
        # Do all the file path remapping so our commands will work

        for key, files in [i for i in config.items() if Task.is_io_field(i[0])]:

            files = self.remap_io_field_paths(key, files)

            # and unwrap filenames if they're an array of one element so that scripts expecting
            # join(str, str) to return a str will be happy.
            config[key] = files[0] if len(files) == 1 else files

        # ----------------------------------------
        # Paths are cleaned up, we can expand name/desc/command

        config.name    = expand.name
        config.desc    = expand.desc
        config.command = expand.command

        if LogLevel.DEBUG:
            self.log("Task config after expand:\n")
            self.log(str(config) + "\n")

        # ----------------------------------------
        # Run some sanity checks

        if Options.strict:
            for command in config.command:
                if not isinstance(command, str):
                    continue
                blocks = Expander.split(command)
                if len(blocks) > 1 or (len(blocks) == 1 and blocks[0][0] == "{"):
                    raise Task.BROKEN("STRICT: Command has curly braces in it")

        # Check that all build files would end up under build_dir
        for file in self._out_files:
            assert Path.isabs(file)
            if not Path.startswith(file, config.build_dir):
                raise Task.BROKEN(f"Path error, output file {file} is not under build_dir {config.build_dir}")

        # Check for task collisions
        for file in self._out_files:
            real_file = cast(str, Path.real(file))
            if real_file in Loader.real_filenames:
                raise Task.BROKEN(f"TaskCollision: Multiple tasks build {real_file}")
            Loader.real_filenames.add(real_file)

        # ----------------------------------------
        # Check for missing inputs. We have to check dry_run, as the input files may only exist if
        # we're really running tasks.

        if not config.dry_run:
            for file in self._in_files:
                assert Path.isabs(file)
                if not Path.exists(file):
                    raise Task.BROKEN(f"Input file missing - {file}")

        # ----------------------------------------
        # See if we need to rebuild our outputs

        self._reason = self.rebuild_reason()
        if not self._reason:
            raise Task.SKIPPED(f"Task is up-to-date: '{config.name}' : '{config.desc}'")


    # ----------------------------------------------------------------------------------------------

    def remap_io_field_paths(self, name, files) -> list[str]:
        """
        Input and output file paths in .hancho scripts are declared relative to the directory the
        script is in (stored in the config under 'script_cwd').
        In general we want to run commands from the root of the repo and store output files in
        repo/build.
        This function takes care of all of that and a few other things, and tries to do so in a
        robust way. Whether this actually turns out to be robust or not is yet to be determined.
        """

        config = self._config

        # Initially, all our file paths are relative to the script_cwd that created this task.
        # Join script_cwd with the filenames to produce absolute paths.
        files = Path.join(config.script_cwd, files)

        # Expanding may have made our files array non-flat, but all of its contents should be
        # absolute paths now.
        files = Utils.flatten(files)
        assert Path.isabs(files)

        # Path _must_ be normed after joining, otherwise it might look like it's under script_cwd
        # but it's not because the path could have "../../../../.." in it.
        files = cast(list[str], Path.norm(files))

        # Move all outputs under build_dir and ensure their directories exist.
        if Task.is_output_field(name):
            for i, file in enumerate(files):
                # Note these conditionals are _NOT_ an if/elif pair!
                if not Path.startswith(file, config.build_dir):  # noqa: SIM102
                    if Path.startswith(file, config.task_cwd):
                        file = file.removeprefix(config.task_cwd)
                        file = config.build_dir + file
                        files[i] = file

                if Path.startswith(file, config.build_dir):
                    dirname = Path.dirname(file)
                    if dirname is not None and not config.dry_run:
                        os.makedirs(dirname, exist_ok=True) #type:ignore


        # Gather all absolute file paths to _in_files/_out_files.

        # The check for is_depfile_field must come first, as it's a special case of a file that
        # is technically _both_ an input and an output file, even though its name starts with "in".

        for i in range(len(files)):
            if Task.is_depfile_field(name):
                if Path.isfile(files[i]):
                    self._in_files.append(files[i])
            elif Task.is_output_field(name):
                self._out_files.append(files[i])
            elif Task.is_input_field(name):
                self._in_files.append(files[i])

        # Convert the fixed paths back to relative so our command lines aren't enormous.
        # Relative paths are relative to task_cwd if we're running a command, otherwise they're
        # relative to script_cwd if we're calling a callback.
        rel_dir = config.task_cwd if isinstance(config.command[0], str) else config.script_cwd

        for i in range(len(files)):
            files[i] = Path.rel(files[i], rel_dir)

        return files

    # ----------------------------------------------------------------------------------------------

    def rebuild_reason(self) -> str:
        """
        Figures out why we have to run a Task, or returns "" if we don't.
        """
        config = self._config
        cwd = os.getcwd()

        if Options.rebuild or getattr(config, "rebuild", False):
            return "Target forced to rebuild"
        if not self._in_files:
            return "Always rebuild a target with no inputs"
        if not self._out_files:
            return "Always rebuild a target with no outputs"

        # Check if any of our output files are missing.
        for file in self._out_files:
            if not Path.exists(file):
                return f"{Path.rel(file, cwd)} is missing"

        # Check if any of our input files are newer than the output files.
        min_out = min(Utils.mtime(f) for f in self._out_files)
        if Utils.mtime(__file__) >= min_out:
            return "hancho.py has changed"

        for file in self._in_files:
            if Utils.mtime(file) >= min_out:
                return f"{Path.rel(file, cwd)} has changed"

        for file in self._loaded_files:
            if Utils.mtime(file) >= min_out:
                return f"{Path.rel(file, cwd)} has changed"

        # Check all dependencies in the C dependencies file, if present.
        if config.in_depfile and Path.exists(config.in_depfile):
            if LogLevel.DEBUG:
                self.log(f"Found C dependencies file {config.in_depfile}\n")
            with open(config.in_depfile) as depcontents:
                deplines = None
                if config.depformat == "msvc":
                    # MSVC /sourceDependencies
                    deplines = json.load(depcontents)["Data"]["Includes"]
                elif config.depformat == "gcc":
                    # GCC -MMD
                    deplines = depcontents.read().split()
                    deplines = [d for d in deplines[1:] if d != "\\"]
                else:
                    raise Task.BROKEN(f"Invalid depfile format {config.depformat}")

                # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
                deplines = [cast(str, Path.join(config.task_cwd, d)) for d in deplines]
                for abs_file in deplines:
                    if not Path.exists(abs_file):
                        return f"Rebuilding because {Path.rel(abs_file, cwd)} from the depfile is missing"
                    if Utils.mtime(abs_file) >= min_out:
                        return f"Rebuilding because {Path.rel(abs_file, cwd)} from the depfile has changed"

        # All checks passed; we don't need to rebuild this output.
        return ""

    # ----------------------------------------------------------------------------------------------

    async def run_command(self, command):
        config = self._config

        if LogLevel.VERBOSE:
            with Log.color(Colors.BLUE):
                self.log(f"{Path.rel(config.task_cwd, config.repo_dir)}$ {command}\n")

        # Create the subprocess via asyncio and then await the result.
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd    = config.task_cwd,
            stdout = asyncio.subprocess.PIPE,
            stderr = asyncio.subprocess.PIPE,
            start_new_session = True
        )
        try:
            (stdout_data, stderr_data) = await proc.communicate()
        except asyncio.CancelledError as ex:
            # We don't trust asyncio to clean up all cancelled processes, so we do it the hard way
            # here and kill the whole process group.
            # Note - this only works on Linux. We will need a slightly different implementation for
            # Windows, which is out of scope until Hancho is shippable.
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
            raise Task.CANCELLED(f"Task is cancelled: '{config.name}' : '{config.desc}'") from ex
        except Exception as ex:
            raise Task.FAILED(f"Command threw an exception : {ex}") from ex

        self._stdout = stdout_data.decode(errors="replace")
        self._stderr = stderr_data.decode(errors="replace")

        if proc.returncode:
            raise Task.FAILED(f"Command return code was non-zero : {proc.returncode}")

        if LogLevel.VERBOSE and (self._stdout or self._stderr):
            self.log_stdout()

        return proc.returncode

    # ----------------------------------------------------------------------------------------------

    def log_stdout(self):
        Log.log("---------------- Stdout ----------------\n")
        if self._stdout:
            for line in self._stdout.strip().split("\n"):
                Log.log(line + "\n")
        Log.log("---------------- Stderr ----------------\n")
        if self._stderr:
            for line in self._stderr.strip().split("\n"):
                Log.log(line + "\n")

    # ----------------------------------------------------------------------------------------------

    async def call_callback(self, command):
        callback_dir = Path.rel(self._config.script_cwd, self._config.repo_dir)
        if LogLevel.VERBOSE:
            self.log(f"{callback_dir}$ {command}\n")

        try:
            result = command(self)
            if isawaitable(result):
                result = await result
        except Exception as ex:
            self.log_task_exception("Callback threw an exception!", ex)
            raise

        return result

    # ----------------------------------------------------------------------------------------------

    def log_task_exception(self, message, ex = None):
        if LogLevel.ERROR:
            script_path = Path.join(self._config.script_cwd, self._config.script_file)

            with Log.color(0xFF0000):
                Log.log("========================================\n")
                Log.log(message + "\n")
                Log.log("========================================\n")

            with Log.color(Colors.RED):
                Log.log(f"Script    = {script_path}:\n")
                Log.log(f"Task      = '{self._config.name}' : '{self._config.desc}'\n")
                Log.log(f"os.getcwd = {os.getcwd()}\n")
                Log.log(f"task cwd  = {self._config.task_cwd}\n")
                Log.log(f"command   = {self._config.command}\n")
                if ex:
                    Log.log_exception(ex)
                self.log_stdout()

            with Log.color(0xFF0000):
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
class Expander(abc.MutableMapping[str, object]):
    """
    This class is used to fetch and expand text templates from a dict during text expansion.
    It allows for both dictionary-like access (using `expander[key]`) and attribute-like access
    (using `expander.key`), making it versatile for accessing template variables and methods.
    """

    def __init__(self, context : Dict):
        # These are just type annotations, because writing to fields while we're in the constructor
        # of a class that overrides __setattr__ does strange things.
        self._context : Dict

        # The actual set is here.
        super().__setattr__("_context", context)

    @staticmethod
    def wrap(source : Dict | Expander) -> Expander:
        return Expander(source) if isinstance(source, (Dict, dict)) else source

    # ----------------------------------------

    @classmethod
    def reset(cls):
        # These are aliases for methods in Hancho that have been pulled out so they can be used by
        # template expansion. This lets you do {flatten(x)} instead of {Utils.flatten(x)} in macros.
        # It's also read by the module-level __getattr__ so you can use "hancho.flatten(x)" instead
        # of "hancho.Utils.flatten(x)"
        cls.aliases = Dict(
            path = os.path,
            abs  = Path.abs,
            base = Path.base,
            ext  = Path.ext,
            norm = Path.norm,
            real = Path.real,
            rel  = Path.rel,
            stem = Path.stem,
            load = lambda file, *args, **kwargs : Loader.load_file(file, False, *args, **kwargs),
            repo = lambda file, *args, **kwargs : Loader.load_file(file, True, *args, **kwargs),

            flatten = Utils.flatten,
            run_cmd = Utils.run_cmd,
            weave   = Utils.weave,
        )

    # ----------------------------------------
    # MutableMapping interface

    def __getitem__(self, key):
        try:
            return self._get(key)
        except AttributeError as ex:
            raise KeyError from ex

    def __setitem__(self, key, val):
        cast(Dict, self._context).__setitem__(key, val)

    def __delitem__(self, key):
        cast(Dict, self._context).__delitem__(key)

    def __iter__(self):
        yield from cast(Dict, self._context)

    def __len__(self):
        return cast(Dict, self._context).__len__()

    # ----------------------------------------
    # object interface

    def __repr__(self):
        result = f"{self.__class__.__name__} @ {hex(id(self))}"
        return result

    def __getattr__(self, key):
        try:
            return self._get(key)
        except KeyError as ex:
            raise AttributeError from ex

    def __setattr__(self, key, val):
        self._context.__setattr__(key, val)

    def __delattr__(self, key):
        self._context.__delattr__(key)

    # ----------------------------------------

    def expand(self, val : Any):
        return Expander._expand(val, self)

    def _get(self, key):
        """
        Reads and expands a field stored in our context. Mappings will be wrapped in an Expander so
        that expansions in nested dicts works correctly.
        """

        # The "trace" key is a special case, as we don't want to trace reading trace...
        if key == "trace":
            return self._context[key]

        with Tracer(self, f"_get({key})") as tracer:
            result = self._context[key]
            result = Expander.wrap(result) if Utils.is_mapping(result) else self.expand(result)
            tracer.save_result(result)

        return result

    # ----------------------------------------

    @staticmethod
    def split(text : str) -> list[str]:
        """
        Extracts all innermost single-brace-delimited spans from a block of text and produces a
        list of string literals and macros. Escaped braces don't count as delimiters.
        """
        result = []
        cursor = 0
        lbrace = -1
        escaped = False

        for i, c in enumerate(text):
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '{':
                lbrace = i
            elif c == '}' and lbrace >= 0:
                if cursor < lbrace:
                    result.append(text[cursor:lbrace])
                result.append(text[lbrace:i+1])
                cursor = i + 1
                lbrace = -1

        if cursor < len(text):
            result.append(text[cursor:])

        return result

    # ----------------------------------------------------------------------------------------------
    # IMPORTANT IMPORTANT IMPORTANT
    # If you can't eval a macro, you return it unchanged. TEFINAE.
    # Template Expansion Failure Is Not An Error.
    # This should be the _only_ try/except block in the expansion code.

    @staticmethod
    def _eval_macro(macro, context):
        with Tracer(context, f"_eval_macro({macro!r})") as tracer:
            try:
                _locals = ChainMap(context, Options.cv_config(), Expander.aliases)
                result = eval(macro[1:-1], hancho.__dict__, _locals)
            except Exception:
                result = macro
            tracer.save_result(result)
        return result

    @staticmethod
    def _expand_blocks(blocks, context):
        with Tracer(context, f"_expand_blocks({blocks!r})") as tracer:
            result = "".join(Utils.stringify(Expander._expand(b, context)) for b in blocks)
            tracer.save_result(result)
        return result

    # Hancho's template expansions can contain infinite loops, so we need some simple recursion
    # depth tracking here. Hancho's test suites currently pass with MAX_DEPTH = 7, but we set it to
    # 20 just for future growth.

    # The expand_depth is global mutable state, but it's only ever modified inside _expand, which
    # is synchronous and should only be touched by one thread at a time.
    expand_depth : int = 0
    MAX_DEPTH : int = 20

    @staticmethod
    def _expand(variant, context):
        if isinstance(variant, list):
            return [Expander._expand(t, context) for t in variant]
        if not isinstance(variant, str) or not variant:
            return variant

        blocks = Expander.split(variant)
        if len(blocks) == 1 and blocks[0][0] != '{':
            return variant

        with Tracer(context, f"_expand({variant!r})") as tracer:
            if len(blocks) == 1:
                result = Expander._eval_macro(variant, context)
            else:
                result = Expander._expand_blocks(blocks, context)

            if result != variant:
                if Expander.expand_depth >= Expander.MAX_DEPTH:
                    raise RecursionError("Template expansion failed to terminate")
                try:
                    Expander.expand_depth += 1
                    result = Expander._expand(result, context)
                finally:
                    Expander.expand_depth -= 1

            tracer.save_result(result)
        return result


# endregion
# --------------------------------------------------------------------------------------------------
# region Tracer
# Expansion tracing class used by Expander
#
# The traces generated look like this - the EX_XXXX prefix is an identifier for the Expander being
# used so you can tell when the expand context changes, the rest are the call arguments and the
# return values.
#
# [    0.916905] EX_BE50._get(desc)
# [    0.917224] │ EX_BE50._expand('Linking {name}')
# [    0.917305] │ │ EX_BE50._expand_blocks(['Linking ', '{name}'])
# [    0.917569] │ │ │ EX_BE50._expand('{name}')
# [    0.917880] │ │ │ │ EX_BE50._eval_macro('{name}')
# [    0.918016] │ │ │ │ │ EX_BE50._get(name)
# [    0.918284] │ │ │ │ │ └ 'hello-world'
# [    0.918592] │ │ │ │ └ 'hello-world'
# [    0.918706] │ │ │ └ 'hello-world'
# [    0.918977] │ │ └ 'Linking hello-world'
# [    0.919282] │ └ 'Linking hello-world'
# [    0.919394] └ 'Linking hello-world'

class Tracer:

    def __init__(self, context : Dict | Expander, enter_message):
        self.trace = getattr(context, "trace", False)
        self.enter_message = enter_message
        self.color = None
        self.context = context
        self.result = None

    def __enter__(self):
        if not (LogLevel.TRACE or self.trace):
            return self

        self.color = Utils.obj_to_hex(self.context)

        with Log.color(self.color):
            Log.log(f"{Tracer.object_to_tag(self.context)}." + self.enter_message + "\n")

        Log.indent_depth += 1

        return self

    def __exit__(self, exc_type, exc_value, tb):
        if not (LogLevel.TRACE or self.trace):
            return False

        with Log.color(self.color):
            if exc_type:
                Log.log(f"exc_type  : {exc_type}\n")
            if exc_value:
                Log.log(f"exc_value : {exc_value}\n")
            if tb:
                summary = traceback.extract_tb(tb)
                filename, line_no, func_name, _ = summary[-1]
                Log.log(f"location  : {filename} line {func_name}@{line_no}\n")

        Log.indent_depth -= 1

        Log.log("└ ")

        if isinstance(self.result, (Expander, Dict)):
            Log.log(f"{Tracer.object_to_tag(self.result)}\n")
            return False

        with Log.color(Utils.obj_to_hex(self.result)):
            if self.result is None:
                Log.log("<None>\n")
            elif self.result == "":
                Log.log("<Empty>\n")
            else:
                Log.log(repr(self.result) + "\n")

        return False

    def save_result(self, result : Any):
        self.result = result

    @staticmethod
    def object_to_tag(obj):
        tag = (str(type(obj).__name__)[:2] + "_" + hex(id(obj))[-4:]).upper()
        return tag

# endregion
# --------------------------------------------------------------------------------------------------
# region Loader

class Loader:

    @classmethod
    def reset(cls):
        cls.match_pointer : re.Pattern = re.compile(r"<(\w+) (\w+) at 0[xX][0-9a-fA-F]+>")
        cls.real_filenames : set[str] = set()
        cls.dedupe : dict[tuple[str, str], types.ModuleType] = {}
        cls.loaded_files : list[str] = []
        cls.root_repo : types.ModuleType | None = None

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def load_file(cls, script_path : str, is_repo : bool, *args, **kwargs) -> types.ModuleType:
        # We _do_ need to expand script_path because it might contain a path like
        # "{hancho_dir}/tools/tools_base.hancho"
        script_path = Options.cv_config().expand(script_path)
        script_path = cast(str, Path.abs(script_path))

        if not Path.isfile(script_path):
            raise AssertionError(f"Could not find script {script_path}!")

        with open(script_path, encoding="utf-8") as file:
            source = file.read()

        return cls.load_str(script_path, is_repo, source, *args, **kwargs)

    @classmethod
    def load_str(cls, script_path, is_repo : bool, source : str, *args, **kwargs) -> types.ModuleType:
        """This is split out from load_file for testing purposes."""

        code = compile(source, script_path, "exec", dont_inherit=True)

        (script_cwd, script_file) = Path.split(script_path)

        if LogLevel.VERBOSE:
            Log.log(f"Loading {"repo" if is_repo else "script"} {script_path}\n")

        new_module = types.ModuleType(script_path)
        new_module.__dict__.update(
            __file__ = script_path,
            hancho   = hancho,
        )

        # ----------------------------------------
        # Create the script-specific config that points the 'repo' and 'this' paths at the given
        # script.

        old_config = Options.cv_config()

        new_config = Dict(
            old_config,
            Dict(
                script_cwd  = script_cwd,
                script_file = script_file,
                repo_dir    = script_cwd  if is_repo else old_config.repo_dir,
                repo_file   = script_file if is_repo else old_config.repo_file,
                this_repo   = new_module  if is_repo else old_config.this_repo,
                this_module = new_module,
            ),
            *args,
            **kwargs
        )

        # ----------------------------------------
        # Dedupe the load - only scripts with identical real paths and identical module configs are
        # deduped. This relies on __repr__ and the fields read by dump_to_str being stable during a
        # build, which they should be in practice.

        config_dump = Utils.dump_to_str(key = "Config", val = new_config)
        config_dump = cls.match_pointer.sub(r"<\1 \2 at 0x...>", config_dump)

        dedupe_key = (Path.real(script_path), config_dump)
        dedupe = cls.dedupe.get(dedupe_key, None) #type:ignore
        if dedupe is not None:
            return dedupe

        # Not deduped, record this module for future deduping and dependency checking.

        cls.dedupe[dedupe_key] = new_module #type:ignore
        cls.loaded_files.append(script_path)

        # ----------------------------------------
        # Run the module.

        with chdir(new_config.script_cwd):
            try:
                old_token = Options._cv_config.set(new_config)
                exec(code, new_module.__dict__)
            finally:
                Options._cv_config.reset(old_token)

        return new_module

# endregion
# --------------------------------------------------------------------------------------------------
# region Runner

class Runner:

    @classmethod
    def reset(cls):
        cls.all_tasks : list[Task] = []
        cls.core_sem : asyncio.Semaphore = asyncio.Semaphore(Options.core_max)
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

        if count > Options.core_max:
            raise ValueError(f"Tried to acquire {count} cores, which exceeds the max {Options.core_max}")
        async with cls.core_lock:
            acquired = 0
            try:
                while acquired < count:
                    await cls.core_sem.acquire()
                    acquired += 1
            except BaseException:
                cls.release(acquired)
                raise

    @classmethod
    def release(cls, count):
        for _ in range(count):
            cls.core_sem.release()

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def select_root_tasks(cls):
        if Options.target:
            # Enable all tasks whose name matches the target regex
            # NOTE - We have to expand "name" _before_ the task has initialized, which means some
            # of its input fields may be Task references and the resulting name may be wonky if it
            # includes those names via template. Maybe don't do that.
            target_regex = re.compile(Options.target)
            for task in cls.all_tasks:
                name = task._config.expand("{name}")
                if target_regex.search(name):
                    task.enable()
        elif Options.rebuild:
            # Enable _everything_
            for task in cls.all_tasks:
                task.enable()
        else:
            # Enable all tasks that were generated by the root repo.
            for task in cls.all_tasks:
                if task._config.this_repo == Loader.root_repo:
                    task.enable()

    # ----------------------------------------------------------------------------------------------

    @classmethod
    def sync_run_tasks(cls):
        """Synchronously run all tasks until we're done with all of them."""
        return asyncio.run(cls.async_run_tasks())

    # ----------------------------------------------------------------------------------------------

    @classmethod
    async def async_run_tasks(cls):
        """Run all tasks until we run out."""

        # Create asyncio tasks for all enabled Hancho tasks.
        time_a = time.perf_counter()
        for task in cls.all_tasks:
            if task._config.enabled:
                task.create_aio_task()
        time_start = time.perf_counter() - time_a
        if LogLevel.VERBOSE:
            Log.log(f"Starting {Task.tasks_enabled} tasks took {time_start:.3f} seconds\n")

        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.
        time_a = time.perf_counter()
        while cls.live_aio_tasks and cls.count_failures() <= Options.max_errors:
            finished_aio_task = None

            try:
                finished_aio_task = await cls.aio_done_queue.get()
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
                cls.tasks_skipped += 1
            except BaseException as ex:
                if LogLevel.DEBUG:
                    Log.log(f"Weird exception {type(ex)} >{ex}< at {time.perf_counter()}\n")
                cls.tasks_failed += 1

            finally:
                if finished_aio_task is not None:
                    cls.live_aio_tasks.discard(finished_aio_task)
                cls.tasks_awaited += 1
        time_build = time.perf_counter() - time_a

        if LogLevel.VERBOSE:
            Log.log(f"Running {cls.tasks_finished} tasks took {time_build:.3f} seconds\n")

        if cls.count_failures() > Options.max_errors:
            Log.log(f"Too many failures after {cls.tasks_awaited}, cancelling tasks and stopping build\n")

            # Cancel all the asyncio.Tasks that haven't completed yet
            if LogLevel.VERBOSE:
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

    @classmethod
    def run_tool(cls, tool : str):
        if tool == "clean":
            for task in cls.all_tasks:
                build_root = Path.real(task._expander.expand("build_root"))
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
# region init/reset/main

def init(*args, **kwargs):
    """
    Re-initializes all of Hancho.
    If you are importing Hancho directly, you should call this as
    hancho.init(verbosity = "debug", myoption=1234)
    """
    reset(*args, **kwargs)

# ----------------------------------------

def reset(*args, **kwargs):
    root_config : Dict = Dict(Options.default_config(), *args, **kwargs)

    Options.reset(root_config)
    Loader.reset()
    Log.reset()
    Expander.reset()
    Utils.reset()
    Task.reset()
    Runner.reset()

# --------------------------------------------------------------------------------------------------

def main():

    flags = Options.parse_flags(sys.argv[1:])
    init(flags)

    expander = Expander(Options.cv_config())

    # ------------------------------------
    # Startup banner

    root_dir    = expander.root_dir
    root_file   = expander.root_file

    if LogLevel.VERBOSE:
        repo_dir    = expander.repo_dir
        script_dir  = expander.script_cwd
        script_file = expander.script_file
        script_path = os.path.join(cast(str, script_dir), cast(str, script_file))

        Log.log(f"Hancho started as '{" ".join(sys.argv)}'\n")
        Log.log(f"Verbosity is {Options.verbosity}\n")

        with Log.color(Colors.LIME):
            Log.log("Verbose mode on\n")
            if LogLevel.DEBUG:
                Log.log("Debug mode on\n")

        Log.log(f"Hancho root at {root_dir}\n")
        Log.log(f"Hancho repo at {repo_dir}\n")
        Log.log(f"Hancho root script at {script_path}\n")

    # ------------------------------------
    # Load all build scripts

    time_a = time.perf_counter()

    script_path = cast(str, Path.join(root_dir, root_file))
    if not Path.exists(script_path):
        path = Path.rel(script_path, os.getcwd())
        if LogLevel.FATAL:
            Log.log(f"Could not load build script {path}\n")
        sys.exit(1)
    Loader.root_repo = Loader.load_file(script_path, True)

    time_load = time.perf_counter() - time_a

    if LogLevel.VERBOSE:
        Log.log(f"Loading .hancho files took {time_load:.3f} seconds\n")

    # ------------------------------------
    # Run all tasks and tools

    if Options.tool:
        result = Runner.run_tool(Options.tool)
    else:
        Runner.select_root_tasks()
        result = Runner.sync_run_tasks()

    # ------------------------------------
    # Done

    if LogLevel.VERBOSE:
        Log.log(f"Tasks created:    {len(Runner.all_tasks)}\n")
        Log.log(f"Tasks awaited:    {Runner.tasks_awaited}\n")
        Log.log(f"Tasks finished:   {Runner.tasks_finished}\n")
        Log.log(f"Tasks broken:     {Runner.tasks_broken}\n")
        Log.log(f"Tasks failed:     {Runner.tasks_failed}\n")
        Log.log(f"Tasks cancelled:  {Runner.tasks_cancelled}\n")
        Log.log(f"Tasks skipped:    {Runner.tasks_skipped}\n")
        Log.log(f"Mtime calls:      {Utils.mtime_calls}\n")

    if Runner.tasks_failed or Runner.tasks_broken:
        with Log.color(Colors.RED):
            Log.log("BUILD FAILED\n")
    elif Runner.tasks_finished:
        with Log.color(Colors.GREEN):
            Log.log("BUILD PASSED\n")
    else:
        with Log.color(Colors.BLUE):
            Log.log("BUILD CLEAN\n")

    return result

# endregion
# --------------------------------------------------------------------------------------------------
# region if __name__ == "__main__"

# The 'global' config is actually instantiated per script context, otherwise scripts can break each
# other by changing shared config fields. To ensure each script sees the right config, we make the
# module-level __getattr__ redirect to the config stored in the ContextVar in Options.
#
# This is also where we look up command aliases so that script macros don't have to use
# fully-qualified names like 'hancho.Path.norm'.

def __getattr__(name):
    if name == "config":
        return Options.cv_config()
    elif name in Expander.aliases:
        # Note this _only_ affects references like "hancho.flatten" in scripts, it does not affect
        # template/macro expansion. That's handled in Expander._eval_macro above.
        return Expander.aliases[name]
    else:
        raise AttributeError(name)

# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":

    # Top-level exception handler just so we can print a big red "SOMETHING BROKE ALL BAD" message
    # if we failed to catch an exception in run_tasks.
    result = None
    try:
        result = main()
    except BaseException as ex:
        with Log.color(Colors.RED):
            Log.log("Hancho hit an exception during startup:\n")
            Log.log_exception(ex)

            Log.log("BUILD FAILED\n")
            sys.exit(1)
    sys.exit(result)
else:
    init()

# endregion
