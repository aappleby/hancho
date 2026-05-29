#!/usr/bin/python3
# region header

"""
Hancho v0.4.0 @ 2024-11-01 - A simple, pleasant build system.

Hancho is a single-file build system that's designed to be dropped into your project folder - there
is no 'install' step.

Hancho's test suite can be found in 'test.hancho' in the root of the Hancho repo.
"""

# pylint: disable=too-many-lines
# pylint: disable=protected-access
# pylint: disable=unused-argument
# pylint: disable=bad-indentation

import asyncio
import contextvars
import os
import re
import sys
import time
import types
from collections import abc, ChainMap
from contextlib import chdir
from typing import Any, cast

hancho = sys.modules[__name__]

# endregion
####################################################################################################
# region Main

type Tree[T] = T | list[Tree[T]]

def __getattr__(name):
    # Any attribute read that's not global in this module gets redirected to the per-script context
    # dict.

    if name == "config":
        return Loader.cv_config.get()
    elif hasattr(aliases, name):
        return aliases[name]
    else:
        raise AttributeError(name)

def __dir__():
    # Not sure yet if we need to tweak the public dir of hancho.
    return [*hancho.__dict__.keys(), *aliases.keys(), "config"]

#----------------------------------------

def init(*args, **kwargs):
    """
    Re-initializes all of Hancho.
    If you are importing Hancho directly, you should call this as
    hancho.init(debug = true, quiet = false, ...)
    """
    reset(*args, **kwargs)

#----------------------------------------

def reset(*args, **kwargs):
    Loader.reset(*args, **kwargs)
    Stats.reset()
    Log.reset(hancho.config.verbose)
    Utils.reset()
    Tracer.reset()
    Runner.reset(hancho.config.core_max)

#----------------------------------------

def main():

    flags = Loader.parse_flags(sys.argv[1:])
    init(flags)

    #----------------------------------------
    # Load all build scripts

    time_a = time.perf_counter()

    script_path = os.path.join(hancho.config.root_dir, hancho.config.root_file)
    if not os.path.exists(script_path):
        path = os.path.relpath(script_path, os.getcwd())
        Log.log(f"Could not load build script {path}\n")
        sys.exit(-1)
    Loader.root_repo = Loader.load_file(script_path, True)

    Stats.time_load = time.perf_counter() - time_a
    Log.log(f"Loading .hancho files took {Stats.time_load:.3f} seconds\n")

    #----------------------------------------
    # Run tools if needed

    if hancho.config.tool:
        result = Runner.run_tool(hancho.config.tool)
        return result

    #----------------------------------------
    # Queue all tasks

    time_a = time.perf_counter()

    if hancho.config.target:
        target_regex = re.compile(hancho.config.target)
        Runner.queue_tasks_by_regex(target_regex)
    elif hancho.config.build_all:
        Runner.queue_all_tasks()
    else:
        Runner.queue_root_tasks()

    Stats.time_queue = time.perf_counter() - time_a
    Log.log(f"Queueing {len(Runner.queued_tasks)} tasks took {Stats.time_queue:.3f} seconds\n")

    #----------------------------------------
    # Run all tasks

    time_a = time.perf_counter()

    result = Runner.sync_run_tasks()

    Stats.time_build = time.perf_counter() - time_a
    Log.log(f"Running {Stats.tasks_finished} tasks took {Stats.time_build:.3f} seconds\n")

    #----------------------------------------
    # Done

    Stats.print_build_stats()
    return result

# endregion
####################################################################################################
# region Log

class Log:
    """Simple logger that can do same-line log messages like Ninja."""

    buffer : str
    verbose : bool

    @classmethod
    def reset(cls, verbose):
        cls.buffer = ""
        cls.verbose = verbose

    @classmethod
    def log(cls, message : str):
        lines = message.split('\n')
        for i, line in enumerate(lines):
            if ((i < len(lines) - 1) or cls.verbose) and line:
                cls.log_raw("\r" + line + "\n")
            else:
                cls.log_raw("\r" + line + "\x1B[K")

    @classmethod
    def log_raw(cls, message : str):
        cls.buffer += message
        if not hancho.config.quiet:
            sys.stdout.write(message)
            sys.stdout.flush()

    @staticmethod
    def dump(val):
        text = Log.dump_to_str(None, val)
        Log.log(text)
        pass

    # Pretty-printer for various types
    @staticmethod
    def dump_to_str(key, val, indent = 0, print_id = False, max_width = 80, tab = "  ", flat = False):
        # In "key : type = ", don't print these types.
        skip_type = isinstance(val, (str, bool, int, float, list, tuple, set, bytes, bytearray, range,
            type(None), types.FunctionType, types.BuiltinFunctionType, types.ModuleType))

        # Generate the "key : type = " prefix.
        prefix = ""
        if key is not None: prefix += str(key) + " "
        if not skip_type:   prefix += ": " + type(val).__name__ + " "
        if print_id:        prefix += ": " + hex(id(val)) + " "
        if prefix:          prefix += "= "

        # Unwrap a few types that we want to view as containers
        if   isinstance(val, Task):                val = val.__dict__
        elif isinstance(val, Expander):            val = val._context
        elif isinstance(val, contextvars.Context): val = list(val.keys())

        #
        if key == "__builtins__":
            return (tab * indent) + prefix + object.__repr__(val)

        # Non-containers are always emitted on one line. If they overflow, they overflow.
        if not (Utils.is_collection(val) or Utils.is_mapping(val)):
            return (tab * indent) + prefix + repr(val)

        # Extract key-value pairs and set delimiters for our container types.
        if isinstance(val, tuple):
            items = [(None, val2) for val2 in val]
            ld = "("; rd = ",)" if len(items) == 1 else ")"
        elif Utils.is_mapping(val):
            items = val.items() # type:ignore
            ld = "{"; rd = "}"
        elif Utils.is_collection(val):
            items = [(None, val2) for val2 in val] # type:ignore
            ld = "["; rd = "]"
        else:
            assert False, f"Don't know what to do with {type(val)}"

        # Iterate over our key-value pairs, converting them in to string chunks. If the resulting line
        # would be too wide and we're not trying to generate a flat string, fall back to multi-line.
        pad = (tab * indent)
        separator = ", "
        chunks = []
        width = len(pad) + len(prefix) + len(ld) + (len(separator) * (len(items) - 1)) + len(rd) + len(",")

        for k, v in items:
            chunk = Log.dump_to_str(k, v, 0, print_id, max_width, tab, True)
            if chunk is None or width + len(chunk) > max_width:
                if flat: return None
                separator = ",\n"
                chunks = (Log.dump_to_str(k, v, indent + 1, print_id, max_width, tab, False) for k, v in items)
                return pad + prefix + ld + "\n" + separator.join(chunks) + "\n" + pad + rd
            width += len(chunk)
            chunks.append(chunk)

        # Done, we can fit this dump on one line.
        return pad + prefix + ld + separator.join(chunks) + rd

# endregion
####################################################################################################
# region Utils

class Utils:
    @classmethod
    def reset(cls):
        import random
        cls.rand = random.Random()

    @classmethod
    def check(cls, type_, t):
        if not isinstance(t, type_):
            assert isinstance(t, type_), f"Expected {type_.__name__}, got {type(t).__name__} = {t}"
        return t

    @classmethod
    def listify(cls, obj):
        if not Utils.is_collection(obj):
            return obj
        result = [Utils.listify(x) for x in obj]
        return result

    @staticmethod
    def recursify(func):
        """Turns a function that maps scalars into one that maps Tree[str]"""
        def result(val, *args, **kwargs):
            if Utils.is_iterable(val):
                return [result(v, *args, **kwargs) for v in val]
            else:
                return func(val, *args, **kwargs)
        return result

    #----------------------------------------

    @classmethod
    def is_collection(cls, variant : Any) -> bool:
        """
        Mappings and non-array iterables are not considered Collections in Hancho so that
        we don't turn "foo" into ('f', 'o', 'o').
        """
        if isinstance(variant, (str, bytes, bytearray, range, abc.Mapping)): return False
        return isinstance(variant, abc.Collection)

    @classmethod
    def is_iterable(cls, variant : Any) -> bool:
        if isinstance(variant, (str, bytes, bytearray, abc.Mapping)): return False
        return isinstance(variant, abc.Iterable)

    @classmethod
    def is_mapping(cls, variant : Any) -> bool:
        return isinstance(variant, abc.Mapping)

    @classmethod
    def is_scalar(cls, variant : Any) -> bool:
        import numbers
        return isinstance(variant, (numbers.Number, str, bytes, bool, type(None)))

    #----------------------------------------
    # Checks if a string needs template expansion. Empty strings are considered literals.

    braced = re.compile(r"\{(\\.|[^\\}])*\}")

    @staticmethod
    def is_literal(variant : Any) -> bool:
        if not isinstance(variant, str): return False
        m = Utils.braced.search(variant)
        return m is None

    @staticmethod
    def is_braced(variant : Any) -> bool:
        # this is just is_macro or is_template
        if not isinstance(variant, str) or len(variant) == 0: return False
        m = Utils.braced.search(variant)
        return m is not None

    @staticmethod
    def is_macro(variant : Any) -> bool:
        if not isinstance(variant, str) or len(variant) == 0: return False
        m = Utils.braced.search(variant)
        return m is not None and m.group() == variant

    @staticmethod
    def is_template(variant) -> bool:
        if not isinstance(variant, str) or len(variant) == 0: return False
        m = Utils.braced.search(variant)
        return m is not None and m.group() != variant

    #----------------------------------------

    @classmethod
    def join(cls, lhs, rhs, *args) -> list[str]:
        lhs2 = Utils.flatten(lhs)
        rhs2 = Utils.join(rhs, *args) if len(args) > 0 else Utils.flatten(rhs)
        return [l + r for l in lhs2 for r in rhs2]

    #----------------------------------------

    @classmethod
    def color_hsv(cls, h : float = 0, s : float = 0, v : float = 0) -> str:
        import colorsys
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        return Utils.color(int(r * 255), int(g * 255), int(b * 255))

    @classmethod
    def color(cls, red : int = 0, green : int = 0, blue : int = 0) -> str:
        """Converts RGB color to ANSI format string."""
        # Color strings don't work in Windows console, so don't emit them.
        if not hancho.config.use_color or os.name == "nt":
            return ""
        if red == 0 and green == 0 and blue == 0:
            return "\x1B[0m"
        return f"\x1B[38;2;{red};{green};{blue}m"

    @classmethod
    def obj_to_color(cls, obj):
        rand = cls.rand
        rand.seed(id(obj))
        return Utils.color_hsv(rand.random(), 0.3, 1.0)

    #----------------------------------------

    @classmethod
    def run_cmd(cls, cmd : str):
        """Runs a console command synchronously and returns its stdout with whitespace stripped."""
        import subprocess
        result = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
        return result

    #----------------------------------------

    @classmethod
    def mtime(cls, filename : str):
        """Gets the file's mtime and tracks how many times we've called mtime()"""
        Stats.mtime_calls += 1
        return os.stat(filename).st_mtime_ns

    #----------------------------------------

    @classmethod
    def flatten(cls, variant : Any) -> list[Any]:
        if Utils.is_iterable(variant):
            return [x for element in variant for x in Utils.flatten(element)]
        return [] if variant is None else [variant]

    #----------------------------------------

    @staticmethod
    def _map(k, v, func):
        if Utils.is_collection(v):
            return [Utils._map(k2, v2, func) for k2, v2 in enumerate(v)]
        elif Utils.is_mapping(v):
            return Dict({k2 : Utils._map(k2, v2, func) for k2, v2 in v.items()})
        else:
            return func(k, v)

    @staticmethod
    def map(v, func):
        return Utils._map(None, v, func)

    #----------------------------------------

    @staticmethod
    def stringify_variant(variant) -> str:
        """Converts any type into a template-compatible string."""
        if variant is None:
            return ""
        elif Utils.is_collection(variant):
            variant = [Utils.stringify_variant(val) for val in variant]
            return " ".join(variant)
        else:
            return str(variant)

    #----------------------------------------

    @staticmethod
    async def await_scalar(v):
        import inspect
        if isinstance(v, Promise):
            return await Utils.await_variant(await v.get())
        elif isinstance(v, Task):
            return await Utils.await_variant(await v.await_done())
        elif inspect.isawaitable(v):
            return await Utils.await_variant(await v)
        else:
            return v

    @staticmethod
    async def await_variant(v):
        if Utils.is_collection(v):
            return [await Utils.await_variant(v2) for v2 in v]
        elif Utils.is_mapping(v):
            return Dict({k2 : await Utils.await_variant(v2) for k2, v2 in v.items()})
        else:
            return await Utils.await_scalar(v)
            assert False, f"Don't know what to do with a {type(v)}"

# endregion
####################################################################################################
# region Path
# These are just equivalents of the os.path.* functions that work on Tree[str].

class Path:

    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with simple string manipulation.

    @staticmethod
    def rel(path1, path2):
        if Utils.is_collection(path1):
            result = [Path.rel(p, path2) for p in path1]
        elif Utils.is_collection(path2):
            result = [Path.rel(path1, p) for p in path2]
        elif isinstance(path1, str) and isinstance(path2, str):
            path1 = os.path.normpath(path1)
            path2 = os.path.normpath(path2)
            result = path1.removeprefix(path2 + "/") if path1 != path2 else "."
        else:
            assert False, f"rel() Don't know how to join a {type(path1).__name__} with a {type(path2).__name__}"
        return result

    @staticmethod
    def join(lhs, rhs):
        result = [os.path.join(l, r) for l in Utils.flatten(lhs) for r in Utils.flatten(rhs)]
        return result[0] if len(result) == 1 else result

    # We want these functions to work on Tree[str], so we run them through recursify.
    _abs  = Utils.recursify(os.path.abspath)

    abs = lambda path : Path._abs(path) if path else ""

    base = Utils.recursify(os.path.basename)
    norm = Utils.recursify(os.path.normpath)
    real = Utils.recursify(os.path.realpath)
    ext  = Utils.recursify(lambda name, new_ext: os.path.splitext(name)[0] + new_ext)
    stem = Utils.recursify(lambda path: os.path.splitext(os.path.basename(path))[0])

# endregion
####################################################################################################
# region Dict

class Dict(dict):
    """
    This class extends 'dict' in a couple ways -
    1. Dict supports "foo.bar" attribute access in addition to "foo['bar']"
    2. Dict supports "merging" instances by passing them (and any additional key-value pairs) in via the constructor.
    3. When merging Dicts, the rightmost not-None value of an attribute will be kept.
    4. If two attributes with the same name are both Dicts, we will recursively merge them.
    5. Dict behaves like a value type, merging will make copies of all its inputs.
    """

    def __init__(self, *args, **kwargs):
        super().__init__()

        # Ignore Nones and empty dicts.
        for arg in filter(None, (*args, kwargs)):
            assert Utils.is_mapping(arg)
            for key, rval in arg.items():
                lval = dict.get(self, key, None)

                # Mappings get turned into Dicts.
                if Utils.is_mapping(rval) and type(rval) != Dict:
                    rval = Dict(rval)

                # Pairs of mappings get merged together as needed.
                if Utils.is_mapping(lval) and Utils.is_mapping(rval):
                    rval = Dict(lval, rval)

                if lval is None or rval is not None:
                    dict.__setitem__(self, key, rval)

    #----------------------------------------
    # Object

    def __getattr__(self, key : str):
        try:
            return dict.__getitem__(self, key)
        except KeyError as e:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'") from e

    def __setattr__(self, key : str, val : Any):
        try:
            return dict.__setitem__(self, key, val)
        except KeyError as e:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'") from e

    def __delattr__(self, key : str):
        try:
            return dict.__delattr__(self, key)
        except KeyError as e:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'") from e

    def __or__(self, other):
        return Dict(self, other)

    def __repr__(self):
        return Log.dump_to_str(key = None, val = self)

    #----------------------------------------
    # Expander convenience helpers

    def eval[T](self, expr : str, as_type: type[T] = object) -> T:
        result = Expander.eval(self, expr)
        assert isinstance(result, as_type)
        return result

    def expand_once[T](self, text : str, as_type : type[T] = object) -> T:
        result = Expander.expand_once(self, text)
        assert isinstance(result, as_type)
        return result

    def expand_all[T](self, text : Tree[str], as_type : type[T] = object) -> T:
        result = Expander.expand_all(self, text)
        assert isinstance(result, as_type)
        return result

# Tool is just an alias for Dict to make build scripts more readable.
class Tool(Dict): pass

# endregion
####################################################################################################
# region Task
# Task object + bookkeeping

class Task:

    DECLARED  = "DECLARED"
    QUEUED    =  "QUEUED"
    STARTED   = "STARTED"
    WAITING   = "WAITING"
    INIT      = "INIT"
    GET_CORES = "GET_CORES"
    RUNNING   = "RUN"
    FINISHED  = "FINISHED"
    CANCELLED = "CANCELLED"
    FAILED    = "FAILED"
    SKIPPED   = "SKIPPED"
    BROKEN    = "BROKEN"

    #--------------------------------------------------------------------------------

    def __init__(self, *args, **kwargs):

        # Save the context, we will use it when we create the asyncio.Task
        self._context = contextvars.copy_context()
        self._config  = Dict(hancho.config, *args, **kwargs)

        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._asyncio_task : asyncio.Task

        # Tasks depend on all .hancho files that were loaded when the task was created.
        # This is probably too wide a net, but tracking dependencies between .hancho files is not
        # really possible.
        self._loaded_files : list[str] = list(Loader.loaded_files)

        # Bookkeeping stuff
        self._task_index : int = 0
        self._state : str = Task.DECLARED
        self._reason : str = ""
        self._stdout : str = ""
        self._stderr : str = ""

        self._in_files  = []
        self._out_files = []

        Runner.all_tasks.append(self)

    # ----------------------------------------

    def to_state(self, new_state):
        transitions = {
            Task.DECLARED : [Task.QUEUED],
            Task.QUEUED   : [Task.STARTED],

            Task.STARTED  : [Task.WAITING],
            Task.WAITING  : [Task.INIT, Task.CANCELLED],

            Task.INIT     : [
                Task.CANCELLED,
                Task.BROKEN,
                Task.FINISHED,
                Task.SKIPPED,
                Task.GET_CORES
            ],

            Task.GET_CORES : [Task.RUNNING],
            Task.RUNNING  : [Task.FAILED, Task.FINISHED],
        }

        if not self._state in transitions:
            message = f"State {self._state} -> {new_state} has no edges in the transition table"
            raise RuntimeError(message)
        edges = transitions[self._state]
        if not new_state in edges:
            message = f"Can't transition from {self._state} to {new_state}!"
            raise RuntimeError(message)
        self._state = new_state

    # ----------------------------------------
    # WARNING: Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.

    def __copy__(self):
        assert False, "Don't copy Tasks!"

    def __deepcopy__(self, memo):
        assert False, "Don't copy Tasks!"

    def __repr__(self):
        return Log.dump_to_str(key = "Task", val = self)

    # ----------------------------------------

    def queue(self):
        self.to_state(Task.QUEUED)

        # Queue all tasks referenced by this task's config.
        def apply2(k, v):
            if isinstance(v, Task) and v._state is Task.DECLARED:
                v.queue()
            return v
        self._config = Utils.map(self._config, apply2)

        # And now queue this task.
        Runner.queued_tasks.append(self)

    def start(self):
        self.to_state(Task.STARTED)

        self._asyncio_task = asyncio.create_task(self.task_main(), context = self._context)
        # FIXME should this be in log_task_start? Needs cleanup.
        Stats.tasks_started += 1

    async def await_done(self):
        if self._state is Task.DECLARED:
            self.queue()
        if self._state is Task.QUEUED:
            self.start()
        assert self._asyncio_task is not None
        await self._asyncio_task
        return self._out_files

    def promise(self, field : str):
        return Promise(self, field)

    #--------------------------------------------------------------------------------
    # FIXME We're gonna merge task_init into this and then break it back out into smaller pieces

    async def task_main(self):
        #----------------------------------------

        c = self._config
        e = Expander.wrap(c, c.trace)

        path_fields  = ["hancho_dir", "task_cwd", "root_dir", "root_file", "repo_dir", "repo_file",
                        "script_dir", "script_file", "build_root", "build_dir"]

        flag_fields  = ["core_count", "core_max", "depformat", "build_tag", "target", "tool",
                        "keep_going", "verbose", "debug", "dry_run", "quiet", "rebuild", "shuffle",
                        "trace", "use_color", "should_fail", "build_all"]

        for f in path_fields:   c[f] = os.path.normpath(e[f]) #type:ignore
        for f in flag_fields:   c[f] = e[f]

        #----------------------------------------
        # Await everything awaitable in this task's config. If any of this tasks's dependencies
        # were cancelled, we propagate the cancellation to downstream tasks.

        try:
            self.to_state(Task.WAITING)
            self._config = cast(Dict, await Utils.await_variant(self._config))
        except BaseException as ex:  # pylint: disable=broad-exception-caught
            self.to_state(Task.CANCELLED)
            self.log_task_cancelled(ex)
            raise asyncio.CancelledError() from ex

        # Now that all our inputs are ready, grab a _task_index that we'll use in our logging.
        Stats.tasks_running += 1
        self._task_index = Stats.tasks_running

        #----------------------------------------
        # Task init

        try:
            self.to_state(Task.INIT)
            if self._config.debug:
                Log.log(f"Task config before expand: {self._config}\n")

            # Initialize the task, which means expanding everything else that needs expanding
            # and fixing up paths to point to task_cwd or build_dir.
            with chdir(self._config.script_dir):
                # ----------------------------------------
                # First, flatten all inputs and outputs.

                for k, v in self._config.items():
                    if not v:
                        continue
                    if isinstance(k, str) and (k.startswith("in_") or k.startswith("out_")):
                        result = Utils.flatten(v)
                        if len(result) == 1:
                            self._config[k] = result[0]
                        else:
                            self._config[k] = result

                # ----------------------------------------
                # All our inputs and outputs are now flat arrays. Expand all in_ and out_ filenames.
                # We _must_ expand these first before joining paths or the paths will be incorrect:
                # prefix + swap(abs_path) != abs(prefix + swap(path))

                for k, v in self._config.items():
                    if not v:
                        continue
                    if isinstance(k, str) and (k.startswith("in_") or k.startswith("out_")):
                        v = self._config.expand_all(v)
                        v = Path.abs(v)
                        self._config[k] = v

                #----------------------------------------
                # Make all paths absolute and move all output files so they're under build_dir.

                def fix(k, v):
                    if k == "in_depfile" or k.startswith("out_"):
                        # Note this conditional needs to be first, as build_dir can itself be under task_cwd
                        if v.startswith(self._config.build_dir):
                            # Absolute path under build_dir, do nothing.
                            pass
                        elif v.startswith(self._config.task_cwd):
                            # If an input source had an absolute path and we swap the extension on it to make the
                            # output filename, we'll have a '.o' file or similar inside task_cwd. Move it so it
                            # lives under build_dir.
                            v = v.replace(self._config.task_cwd, self._config.build_dir)
                        else:
                            raise ValueError(f"Output file has absolute path that is not under task_cwd or build_dir : {v}")
                    elif k.startswith("in_"):
                        v = Path.join(self._config.task_cwd, v)
                    return v

                for k, v in self._config.items():
                    if not v:
                        continue
                    if isinstance(k, str) and (k.startswith("in_") or k.startswith("out_")):
                        if Utils.is_collection(v):
                            for i, v2 in enumerate(v):
                                v[i] = fix(k, v2)
                        else:
                            self._config[k] = fix(k, v)

                #----------------------------------------
                # Paths are cleaned up, we can expand name/desc/command

                self._config.name    = self._config.expand_all("{name}")
                self._config.desc    = self._config.expand_all("{desc}")
                self._config.command = self._config.expand_all("{command}")

                #----------------------------------------
                # Gather all absolute file paths to _in_files/_out_files.
                # WARNING: These filenames _must_ be absolute as they may be read from other repos.

                for k, v in self._config.items():
                    if k == "in_depfile":
                        if isinstance(v, str) and os.path.isfile(v):
                            self._in_files.append(v)
                    elif k.startswith("out_"):
                        self._out_files.extend(Utils.flatten(v))
                    elif k.startswith("in_"):
                        self._in_files.extend(Utils.flatten(v))

                # ----------------------------------------
                # Check for missing paths

                if not os.path.exists(self._config.task_cwd):
                    raise FileNotFoundError(self._config.task_cwd)

                if not self._config.build_dir.startswith(self._config.repo_dir):
                    raise ValueError(
                        f"Path error, build_dir {self._config.build_dir} is not under repo dir {self._config.repo_dir}"
                    )

                # Make sure our output directories exist
                if not self._config.dry_run:
                    for file in self._out_files:
                        os.makedirs(os.path.dirname(file), exist_ok=True)

                # ----------------------------------------
                # Check for task collisions

                for file in self._out_files:
                    real_file = os.path.realpath(file)
                    if real_file in Loader.filename_to_fingerprint:
                        raise ValueError(f"TaskCollision: Multiple tasks build {real_file}")
                    Loader.filename_to_fingerprint[real_file] = real_file

                # Check for duplicate task outputs

                # FIXME all_out_files and filename_to_fingerprint should probably be sets

                if self._config.command:
                    for file in self._out_files:
                        file = os.path.abspath(file)
                        if file in Loader.all_out_files:
                            raise NameError(f"Multiple rules build {file}!")
                        Loader.all_out_files.add(file)

                # ----------------------------------------
                # Check for missing inputs

                if not self._config.dry_run:
                    for file in self._in_files:
                        if file is None:
                            # FIXME I don't think we care about inputs having a none. We should test for that.
                            raise ValueError("_in_files contained a None")
                        if not os.path.exists(file):
                            raise FileNotFoundError(file)

                # ----------------------------------------
                # Check that all build files would end up under build_dir

                for file in self._out_files:
                    # FIXME same here
                    if file is None:
                        raise ValueError("_out_files contained a None")
                    file = os.path.abspath(file)
                    if not file.startswith(self._config.build_dir):
                        raise ValueError(
                            f"Path error, output file {file} is not under build_dir {self._config.build_dir}"
                        )

            if self._config.debug:
                Log.log(f"Task config after expand: {self._config}\n")

        except asyncio.CancelledError as ex:
            # We discovered during init that we don't need to run this task.
            self.to_state(Task.CANCELLED)
            self.log_task_cancelled(ex)
            raise ex

        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # Failure during task init because task is broken
            self.to_state(Task.BROKEN)
            self.log_task_broken(ex)
            if self._config.should_fail:
                return
            else:
                raise ex

        #----------------------------------------
        # Early-out if this is a no-op task

        if not self._config.command:
            self.log_task_done()
            self.to_state(Task.FINISHED)
            return

        #----------------------------------------
        # Check if we need a rebuild

        self._reason = self.needs_rerun(self._config.rebuild)
        if not self._reason:
            self.log_task_uptodate()
            self.to_state(Task.SKIPPED)
            return

        #----------------------------------------
        # TASK START

        self.log_task_start()

        try:

            #----------------------------------------
            # Wait for enough jobs to free up to run this task and then run the commands.

            self.to_state(Task.GET_CORES)
            async with Runner.Cores(self._config.core_count):

                # Run the task's commands!
                self.to_state(Task.RUNNING)
                for command in Utils.flatten(self._config.command):
                    await self.run_command(command)

            #----------------------------------------
            # Task finished successfully

            if self._config.verbose or self._config.debug:
                self.log_task_done()

            self.to_state(Task.FINISHED)
            Stats.tasks_finished += 1

        except BaseException as ex:  # pylint: disable=broad-exception-caught
            # Both broken and failed tasks should end up here.
            self.log_task_failed(ex)
            self.to_state(Task.FAILED)
            if self._config.should_fail:
                return
            else:
                raise ex

    #--------------------------------------------------------------------------------

    def needs_rerun(self, rebuild=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""

        cwd = os.getcwd()

        if rebuild:
            return f"Files {Path.rel(self._out_files, cwd)} forced to rebuild"
        if not self._in_files:
            return "Always rebuild a target with no inputs"
        if not self._out_files:
            return "Always rebuild a target with no outputs"

        # Check if any of our output files are missing.
        for file in self._out_files:
            if not os.path.exists(file):
                return f"Rebuilding because {Path.rel(file, cwd)} is missing"

        # Check if any of our input files are newer than the output files.
        min_out = min(Utils.mtime(f) for f in self._out_files)

        if Utils.mtime(__file__) >= min_out:
            return "Rebuilding because hancho.py has changed"

        for file in self._in_files:
            if Utils.mtime(file) >= min_out:
                return f"Rebuilding because {Path.rel(file, cwd)} has changed"

        for file in self._loaded_files:
            if Utils.mtime(file) >= min_out:
                return f"Rebuilding because {Path.rel(file, cwd)} has changed"

        # Check all dependencies in the C dependencies file, if present.
        depfile = self._config.in_depfile

        if depfile and os.path.exists(depfile):
            if self._config.debug:
                Log.log(f"Found C dependencies file {depfile}\n")
            with open(depfile, encoding="utf-8") as depfile:
                deplines = None
                if self._config.depformat == "msvc":
                    # MSVC /sourceDependencies
                    import json
                    deplines = json.load(depfile)["Data"]["Includes"]
                elif self._config.depformat == "gcc":
                    # GCC -MMD
                    deplines = depfile.read().split()
                    deplines = [d for d in deplines[1:] if d != "\\"]
                else:
                    raise ValueError(f"Invalid dependency file format {self._config.depformat}")

                # The contents of the C dependencies file are RELATIVE TO THE WORKING DIRECTORY
                deplines = [os.path.join(self._config.task_cwd, d) for d in deplines]
                for abs_file in deplines:
                    if Utils.mtime(abs_file) >= min_out:
                        return f"Rebuilding because {Path.rel(abs_file, cwd)} has changed"

        # All checks passed; we don't need to rebuild this output.
        # Empty string = no reason to rebuild
        return ""

    #--------------------------------------------------------------------------------

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        # Non-string non-callable commands are not valid
        if not isinstance(command, str) and not callable(command):
            raise ValueError(f"Don't know what to do with {command}")

        if self._config.verbose or self._config.debug:
            self.log_command_start(command)

        # Dry runs get early-out'ed before we do anything.
        if self._config.dry_run:
            return

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            import inspect
            try:
                with chdir(self._config.task_cwd):
                    result = command(self)
                    while inspect.isawaitable(result):
                        result = await result
                self._stdout = ""
                self._stderr = ""
            except BaseException as e:
                script_path = os.path.join(self._config.script_dir, self._config.script_file)
                self.log_command_failure(script_path, command, e)
                raise e
            return
        else:
            # Create the subprocess via asyncio and then await the result.
            #if debug: Log.log(f"Task {hex(id(self))} subprocess start '{command}'\n")
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd    = self._config.task_cwd,
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.PIPE,
            )
            (stdout_data, stderr_data) = await proc.communicate()
            self._stdout = stdout_data.decode()
            self._stderr = stderr_data.decode()
            #if debug: Log.log(f"Task {hex(id(self))} subprocess done '{command}'\n")

        if proc.returncode:
            e = ValueError(f"CommandFailure: Command exited with return code {proc.returncode}\n")
            script_path = os.path.join(self._config.script_dir, self._config.script_file)
            self.log_command_failure(script_path, command, e)
            raise e
        elif self._config.verbose or self._config.debug:
            self.log_command_done(command)

    #----------------------------------------

    def dump(self):
        result = f"{type(self).__name__} @ {hex(id(self))} : '{self._config.name}'"
        return result

    #----------------------------------------
    # FIXME Clean this up, yuck

    def log_prefix(self):
        """Prints the [1/N] prefix before a log"""
        message  = Utils.color(128,255,196)
        message += f"[{self._task_index}/{Stats.tasks_started}] "
        message += Utils.color()
        return message

    def stdout_to_str(self):
        message = ""
        if self._stdout:
            message += f"========== Stdout ==========\n"
            message += self._stdout
        if self._stderr:
            message += f"========== Stderr ==========\n"
            message += self._stderr
        if self._stdout or self._stderr:
            message += f"============================\n"
        return message

    def log_task_start(self):
        if self._config.verbose or self._config.debug:
            message  = self.log_prefix()
            message += f"Task started"
            if self._config.dry_run: message += " (DRY RUN)"
            message += f" : '{self._config.name}' - '{self._config.desc}'"
            Log.log(message)
            self.log_task_reason(self._reason)

    def log_task_reason(self, reason):
        if self._config.verbose or self._config.debug:
            message  = self.log_prefix()
            message += Utils.color(128,128,128)
            message += f"Reason: {reason}"
            message += Utils.color()
            message += "\n"
            Log.log(message)

    def log_task_done(self):
        Stats.tasks_finished += 1
        if self._config.verbose or self._config.debug:
            message  = self.log_prefix()
            message += f"Task done"
            if self._config.dry_run: message += " (DRY RUN)"
            message += f" : '{self._config.name}' - '{self._config.desc}'"
            Log.log(message)

    def log_task_failed(self, ex):
        if self._config.should_fail:
            Stats.tasks_shouldfail += 1
        else:
            Stats.tasks_failed += 1

        if True:
            script_path = os.path.join(self._config.script_dir, self._config.script_file)
            import traceback
            message  = self.log_prefix()
            message += Utils.color(255,0,0)
            message += f"Task failed!\n"
            message += f"From {script_path}:\n"
            message += f"    Task '{self._config.name}' : '{self._config.desc}'\n"
            message += traceback.format_exc()
            message += Utils.color()
            Log.log(message)


    def log_task_broken(self, ex):
        if self._config.should_fail:
            Stats.tasks_shouldfail += 1
        else:
            Stats.tasks_broken += 1

        if True:
            script_path = os.path.join(self._config.script_dir, self._config.script_file)
            import traceback
            message  = self.log_prefix()
            message += Utils.color(255,0,0)
            message += f"Task broken!\n"
            message += f"From {script_path}:\n"
            message += f"    Task '{self._config.name}' : '{self._config.desc}'\n"
            message += traceback.format_exc()
            message += Utils.color()
            Log.log(message)

    def log_task_cancelled(self, ex):
        Stats.tasks_cancelled += 1

        if self._config.verbose or self._config.debug:
            message  = self.log_prefix()
            message += Utils.color(64,64,64)
            message += f"Task is cancelled: '{self._config.name}' : '{self._config.desc}'\n"
            message += Utils.color()
            Log.log(message)

    def log_task_uptodate(self):
        Stats.tasks_skipped += 1

        if self._config.verbose or self._config.debug:
            message  = self.log_prefix()
            message += Utils.color(64,64,64)
            message += f"Task is up-to-date: '{self._config.name}' : '{self._config.desc}'\n"
            message += Utils.color()
            Log.log(message)


    def log_command_start(self, command):
        if self._config.verbose or self._config.debug:
            assert self._config.task_cwd == os.getcwd()
            message  = self.log_prefix()
            message += Utils.color(128, 128, 255)
            message += f"{Path.rel(self._config.task_cwd, self._config.repo_dir)}$ '{command}'"
            message += " (DRY RUN)" if self._config.dry_run else ""
            message += Utils.color()
            Log.log(message)

    def log_command_failure(self, script_path, command, ex):
        #if self._config.verbose or self._config.debug:
        if True:
            message  = self.log_prefix()
            message += Utils.color(255,0,0)
            message += f"Command failed!\n"
            message += f"From {script_path}:\n"
            message += f"    Task '{self._config.name}' : '{self._config.desc}'\n"
            message += f"    task_cwd = '{self._config.task_cwd}'\n"
            message += f"    getcwd   = '{os.getcwd()}'\n"
            message += f"    command  = '{command}'\n"
            message += f"    error    = '{ex}'\n"
            if not callable(command):
                message += self.stdout_to_str()
            message += Utils.color()
            Log.log(message)

    def log_command_done(self, command):
        if self._config.verbose or self._config.debug:
            message  = self.log_prefix()
            message += f"Command done : '{command}'"
            if not callable(command):
                message += self.stdout_to_str()
            Log.log(message)

# endregion
####################################################################################################
# region Stats

class Stats:
    mtime_calls : int

    time_load  : float
    time_queue : float
    time_build : float

    tasks_started : int
    tasks_running : int
    tasks_finished : int
    tasks_failed : int
    tasks_skipped : int
    tasks_cancelled : int
    tasks_broken : int
    tasks_shouldfail : int

    @classmethod
    def reset(cls):
        cls.mtime_calls = 0
        cls.time_load  = 0
        cls.time_queue = 0
        cls.time_build = 0
        cls.tasks_started = 0
        cls.tasks_running = 0
        cls.tasks_finished = 0
        cls.tasks_failed = 0
        cls.tasks_skipped = 0
        cls.tasks_cancelled = 0
        cls.tasks_broken = 0
        cls.tasks_shouldfail = 0

    @classmethod
    def print_build_stats(cls):
        # Done, print status info if needed

        if hancho.config.debug or hancho.config.verbose:
            Log.log(f"tasks started:    {cls.tasks_started}\n")
            Log.log(f"tasks finished:   {cls.tasks_finished}\n")
            Log.log(f"tasks failed:     {cls.tasks_failed}\n")
            Log.log(f"tasks skipped:    {cls.tasks_skipped}\n")
            Log.log(f"tasks cancelled:  {cls.tasks_cancelled}\n")
            Log.log(f"tasks broken:     {cls.tasks_broken}\n")
            Log.log(f"tasks shouldfail: {cls.tasks_shouldfail}\n")
            Log.log(f"mtime calls:      {cls.mtime_calls}\n")

        if cls.tasks_failed or cls.tasks_broken:
            Log.log(f"hancho: {Utils.color(255, 128, 128)}BUILD FAILED{Utils.color()}\n")
        elif cls.tasks_finished:
            Log.log(f"hancho: {Utils.color(128, 255, 128)}BUILD PASSED{Utils.color()}\n")
        else:
            Log.log(f"hancho: {Utils.color(128, 128, 255)}BUILD CLEAN{Utils.color()}\n")

# endregion
####################################################################################################
# region Promise
# Promise selects subsets of _out_files

class Promise:
    def __init__(self, task : Task, field : str):
        self.task : Task = task
        self.field = field

    async def get(self):
        await self.task.await_done()
        result = self.task._config[self.field]
        # FIXME this is probably wrong? need a test.
        result = Path.join(self.task._config.task_cwd, result)
        return result

# endregion
####################################################################################################
# region Expander
# Hancho's text expansion system.
#
# Works similarly to Python's F-strings, but with quite a bit more power.
#
# The code here requires some explanation.
#
# We do not necessarily know in advance how the users will nest strings, macros, callbacks,
# etcetera. Text expansion therefore requires dynamic-dispatch-type stuff to ensure that we always
# end up with flat strings.
#
# The result of this is that the functions here are mutually recursive in a way that can lead to
# confusing callstacks, but that should handle every possible case of stuff inside other stuff.
#
# The depth checks are to prevent recursive runaway - the MAX_DEPTH limit is arbitrary but should
# suffice.
#
# Also - TEFINAE - Text Expansion Failure Is Not An Error. Dicts can contain macros that are not
# expandable by that dict. This allows nested dicts to contain templates that can only be expanded
# an outer dict, and things will still Just Work.

# FIXME Look into making Expander overwrite dict entries after expansion so we don't re-expand
# things constantly. I don't know if this is a good idea. Configs should be const by the time
# we start expanding them, so maybe it's OK?

# this has to be a MutableMapping if we want to put it in the ChainMap for locals()
class Expander(abc.MutableMapping[str, object]):
    """
    This class is used to fetch and expand text templates from a dict during text expansion.
    It allows for both dictionary-like access (using `expander[key]`) and attribute-like access
    (using `expander.key`), making it versatile for accessing template variables and methods.
    """

    class Literal(str):
        def __repr__(self):
            return "L" + str.__repr__(self)
        def __eq__(self, b):
            if type(b) == Expander.Macro:
                return False
            return str.__eq__(self, b)
        def __hash__(self):
            return str.__hash__(self)

    class Macro(str):
        def __init__(self, str):
            if not Utils.is_macro(str):
                assert Utils.is_macro(str)
        def __repr__(self):
            return "M" + str.__repr__(self)
        def __eq__(self, b):
            if type(b) == Expander.Literal:
                return False
            return str.__eq__(self, b)
        def __hash__(self):
            return str.__hash__(self)

    #----------------------------------------
    # region

    def __init__(self, context : Dict | Expander, trace : bool):
        # We save a copy of 'trace', otherwise we end up printing traces of reading trace.... :P
        super().__setattr__("_context", context)
        super().__setattr__("trace", trace)

    @classmethod
    def wrap(cls, context : Dict | Expander, trace : bool):
        if isinstance(context, Expander):
            return context

        result = Expander(context, trace)

        tag_a = (str(type(context).__name__)[:2] + "_" + hex(id(context))[-4:]).upper()
        tag_b = (str(type(result).__name__)[:2] + "_" + hex(id(result))[-4:]).upper()
        tag_a = Utils.obj_to_color(context) + tag_a + Utils.color()
        tag_b = Utils.obj_to_color(result) + tag_b + Utils.color()

        Tracer.log(trace, f"wrap {tag_a} -> {tag_b}")
        return result

    #----------------------------------------

    def __contains__(self, key):
        return key in self._context

    def __iter__(self):
        for key in self._context:
            yield key

    def __len__(self):
        return self._context.__len__()

    def __repr__(self):
        result = f"{self.__class__.__name__} @ {hex(id(self))}"
        return result

    #----------------------------------------

    # FIXME do I need the exception translation here? I think I do, because these happen inside eval()
    def __getitem__(self, key):
        try:
            return self._get(key)
        except AttributeError as ex:
            raise KeyError from ex

    def __setitem__(self, key, val):
        self._context.__setitem__(key, val)

    def __delitem__(self, key):
        self._context.__delitem__(key)

    #----------------------------------------

    def __getattr__(self, key):
        try:
            return self._get(key)
        except KeyError as ex:
            raise AttributeError from ex

    def __setattr__(self, key, val):
        self._context.__setattr__(key, val)

    def __delattr__(self, key):
        self._context.__delattr__(key)

    #endregion
    #----------------------------------------

    def _get(self, key):
        assert Utils.is_literal(key)

        with Tracer(self, f"_get('{key}')") as trace:
            result = self._context[key]
            if isinstance(result, Expander):  pass
            elif Utils.is_mapping(result):    result = Expander.wrap(result, self.trace)
            elif Utils.is_collection(result): result = [Expander.expand_all(self, v) for v in cast(list, result)]
            elif Utils.is_template(result):   result = Expander.expand_all(self, result)
            elif Utils.is_macro(result):      result = Expander.expand_all(self, result)
            trace.log_result(result)

        # MAGIC EXPANDY THING IS HERE
        # this breaks some doctest_read_nested_c_first, doctest_template_nones because they don't
        # expect expansion to change a Dict.
        # So, this is probably a bad idea...
        #self._context[key] = result

        return result

    #----------------------------------------

    @staticmethod
    def split(text) -> list[str]:
        """
        Extracts all innermost single-brace-delimited spans from a block of text and produces a
        list of string literals and expressions. Escaped braces don't count as delimiters.
        """
        result = []
        cursor = 0
        lbrace = -1
        rbrace = -1
        escaped = False
        squoted = False
        dquoted = False

        # Turning off quote detection, because we want templates like "Run test suite '{test_mod}'"
        # to turn into "Run test suite 'my_tests'" - we _do_ want to expand inside quotes there.

        for i, c in enumerate(text):
            if escaped:
                escaped = False
            #elif squoted:
            #    if c == '\'':
            #        squoted = False
            #elif dquoted:
            #    if c == '"':
            #        dquoted = False
            elif c == '\\':
                escaped = True
            #elif c == '\'':
            #    squoted = True
            #elif c == '"':
            #    dquoted = True
            elif c == '{':
                lbrace = i
            elif c == '}' and lbrace >= 0:
                rbrace = i
                if cursor < lbrace:
                    result.append(Expander.Literal(text[cursor:lbrace]))
                result.append(Expander.Macro(text[lbrace:rbrace+1]))
                cursor = rbrace + 1
                lbrace = -1
                rbrace = -1

        if cursor < len(text):
            result.append(Expander.Literal(text[cursor:]))

        return result

    #--------------------------------------------------------------------------------
    # Template variable lookup order:
    # 1. The config we're expanding
    # 2. The script-local hancho.config
    # 3. Convenience aliases
    # 4. The global hancho module

    @staticmethod
    def _eval(context : Dict | Expander, expr : str):
        assert Utils.is_literal(expr)
        with Tracer(context, f"_eval('{expr}')") as tracer:
            try:
                # FIXME This should've broken something, but it didn't - why not? Add test?
                #_locals = ChainMap(context)
                # Because the context was built on top of the cv_config if it came from a Task.
                _locals = ChainMap(context, Loader.cv_config.get(), aliases)
                _globals = hancho.__dict__
                result = eval(expr, _globals, _locals)
            except RecursionError as err:
                raise err
            except BaseException as err:
                Tracer.log(cast(bool, context.trace), f"{type(err).__name__}: {err}")
                raise err
            tracer.log_result(result)
        return result

    @staticmethod
    def _expand_macro(context : Dict | Expander, macro : str) -> Any:
        assert Utils.is_macro(macro)
        with Tracer(context, f"_expand_macro('{macro}')") as tracer:
            try:
                result = Expander.eval(context, macro[1:-1])
            except RecursionError as e:
                raise e
            except:
                result = macro
            tracer.log_result(result)
        return result

    @staticmethod
    def _expand_template(context : Dict | Expander, template: str) -> str:
        assert Utils.is_template(template)
        with Tracer(context, f"_expand_template('{template}')") as tracer:
            blocks = Expander.split(template)
            for (i, block) in enumerate(blocks):
                try:
                    if isinstance(block, Expander.Macro):
                        value = Expander._expand_macro(context, block)
                        block = Utils.stringify_variant(value)
                except RecursionError as e:
                    raise e
                blocks[i] = block
            result = "".join(blocks)
            tracer.log_result(result)
        return result

    #----------------------------------------

    @staticmethod
    def get[T](context : Dict | Expander, key : str, as_type : type[T] = object) -> T:
        context = Expander.wrap(context, cast(bool, context.trace))
        result = context._get(key)
        assert isinstance(result, as_type)
        return result

    @staticmethod
    def eval[T](context : Dict | Expander, expr : str, as_type : type[T] = object) -> T:
        assert Utils.is_literal(expr)
        result = Expander._eval(context, expr)
        assert isinstance(result, as_type)
        return result

    @staticmethod
    def expand_once[T](context : Dict | Expander, variant : str, as_type : type[T] = object):
        if Utils.is_collection(variant):
            return [Expander.expand_once(context, v) for v in cast(list, variant)]

        if Utils.is_mapping(variant):
            return {k: Expander.expand_once(context, v) for k, v in cast(dict, variant)}

        if not Utils.is_braced(variant):
            return variant

        if Utils.is_macro(variant):
            result = Expander._expand_macro(context, variant)
        elif Utils.is_template(variant):
            result = Expander._expand_template(context, variant)
        else:
            result = variant
        assert isinstance(result, as_type)
        return result

    @staticmethod
    def expand_all[T](context : Dict | Expander, variant : Any, as_type : type[T] = object):
        if Utils.is_collection(variant):
            return [Expander.expand_all(context, v) for v in cast(list, variant)]

        if Utils.is_mapping(variant):
            return {k: Expander.expand_all(context, v) for k, v in cast(dict, variant)}

        if not Utils.is_braced(variant):
            return variant

        econtext = Expander.wrap(context, trace = getattr(context, "trace", False))

        # Keep expanding the template until it's no longer a template or it's no
        # longer changing.
        for _ in range(Tracer.MAX_DEPTH):
            with Tracer(econtext, f"expand_all('{variant}')") as tracer:
                result = Expander.expand_once(econtext, variant)
                tracer.log_result(result)
            if not Utils.is_braced(result) or result == variant:
                assert isinstance(result, as_type)
                return result
            variant = result
        raise RecursionError("expand_all() - Template expansion failed to terminate")

# endregion
####################################################################################################
# region Tracer
# Expansion tracing class used by Expander

class Tracer:
    # The maximum number of recursion levels we will do to expand a macro.
    # Tests currently require MAX_DEPTH >= 6
    MAX_DEPTH : int = 20
    trellis_stack : list[str]

    @classmethod
    def reset(cls):
        cls.trellis_stack = []

    def __init__(self, context : Dict | Expander, enter_message):
        self.trace = getattr(context, "trace", False)
        self.context = context
        self.result = None

        color = Utils.obj_to_color(self.context)
        context_tag = str(type(self.context).__name__)[:2] + "_" + hex(id(self.context))[-4:]
        context_tag = context_tag.upper()

        Tracer.log(self.trace, color + f"┏ {context_tag}." + enter_message)
        Tracer.trellis_stack.append(color + "┃ ")

    def __enter__(self):
        if len(Tracer.trellis_stack) >= Tracer.MAX_DEPTH:
            raise RecursionError("Tracer.__enter__ - Template expansion failed to terminate")
        return self

    def log_result(self, result : Any):
        self.result = result
        return result

    def print_result(self, text):
        result_color = Utils.color()
        if not isinstance(self.result, (Expander, Dict)):
            result_color = Utils.obj_to_color(self.result)
        Tracer.log(self.trace, f"{Utils.obj_to_color(self.context)}┗ {result_color}{text}{Utils.color()}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        Tracer.trellis_stack.pop()
        if isinstance(self.result, (Expander, Dict)):
            text = (str(type(self.result).__name__)[:2] + "_" + hex(id(self.result))[-4:]).upper()
            self.print_result(text)
        else:
            text = f"{self.result}"
            if self.result is None: text = "<None>"
            if self.result == "":   text = "<Empty>"
            self.print_result(text)
        return False

    @staticmethod
    def log(trace : bool, text : str):
        """Prints a trace message to the log."""
        if not trace:
            return
        buffer = "".join(Tracer.trellis_stack) + text + "\x1B[0m" + '\n'
        Log.log(buffer)



# endregion
####################################################################################################
# region Loader

class Loader:

    all_out_files : set
    filename_to_fingerprint : dict[str, str]
    root_repo : types.ModuleType
    dedupe : dict[int, types.ModuleType]
    stack : list[types.ModuleType]
    loaded_files : list[str]
    cv_config : contextvars.ContextVar
    cv_token : contextvars.Token

    @classmethod
    def reset(cls, *args, **kwargs):
        cls.all_out_files = set()
        cls.filename_to_fingerprint = dict()
        cls.dedupe = {}
        cls.stack = []
        cls.loaded_files = []

        root_config = Dict(Loader.default_config(), *args, **kwargs)

        if not hasattr(cls, "cv_config"):
            cls.cv_config  = contextvars.ContextVar("config")
        if hasattr(cls, "cv_token"):
            cls.cv_config.reset(cls.cv_token)
        cls.cv_token = cls.cv_config.set(root_config)

    #-----------------------------------------------------------------------------------------------
    # We spell all these defaults out explicitly so that when this config gets merged with flags and
    # task configs the fields stay in the same order.
    # This is a function so that when we re-initialize Hancho during tests, we pick up a fresh
    # copy of os.getcwd() if it changed.

    @staticmethod
    def default_config():
        result = Dict(
            name        = "",
            desc        = "",
            command     = "",

            hancho_dir  = os.path.dirname(__file__),
            task_cwd    = "{repo_dir}",
            root_dir    = os.getcwd(),
            root_file   = "build.hancho",
            repo_dir    = "{root_dir}",
            repo_file   = "{root_file}",
            script_dir  = "{root_dir}",
            script_file = "{root_file}",

            is_repo     = True,
            this_repo   = hancho,
            this_module = hancho,

            build_root  = "{repo_dir}/build",
            build_dir   = "{build_root}/{build_tag}/{rel(task_cwd, repo_dir)}",

            core_count  = 1,
            core_max    = os.cpu_count(),

            depformat   = "gcc" if sys.platform.startswith("linux") else "msvc",
            in_depfile  = "",

            build_tag   = "",
            target      = "",
            tool        = "",

            keep_going  = False,
            verbose     = False,
            debug       = False,
            dry_run     = False,
            quiet       = False,
            rebuild     = False,
            shuffle     = False,
            trace       = False,
            use_color   = True,
            should_fail = False,
            build_all   = False,
        )
        return result

    #-----------------------------------------------------------------------------------------------

    @classmethod
    def parse_flags(cls, args : list[str]):
        assert Utils.is_collection(args)

        import argparse
        parser = argparse.ArgumentParser()

        # pylint: disable=line-too-long
        # fmt: off

        parser.add_argument("target",  nargs="?", default = None, type=str.strip,       help="A regex that selects the targets to build. Defaults to all targets in the root repo.")
        parser.add_argument("-C", "--root_dir",   default = None, type=str.strip,       help="Change directory before starting the build")
        parser.add_argument("-f", "--root_file",  default = None, type=str.strip,       help="Input .hancho file - defaults to 'build.hancho'")
        parser.add_argument("-t", "--tool",       default = None, type=str.strip,       help="Run a subtool.")
        parser.add_argument("--build_tag",        default = None, type=str.strip,       help="Set the build tag. Tagged builds will have separate subdirectories under the build directory.")
        parser.add_argument("-c", "--core_max",   default = None, type=int,             help="Run jobs on N cores in parallel (default = cpu_count)")
        parser.add_argument("-k", "--keep_going", default = None, type=int,             help="Keep going until N jobs fail (0 means infinity)")
        parser.add_argument("-v", "--verbose",    default = None, action="store_true",  help="Show verbose build info")
        parser.add_argument("-q", "--quiet",      default = None, action="store_true",  help="Mute all output")
        parser.add_argument("-n", "--dry_run",    default = None, action="store_true",  help="Do not run commands")
        parser.add_argument("-d", "--debug",      default = None, action="store_true",  help="Print debugging information")
        parser.add_argument("-a", "--build_all",  default = None, action="store_true",  help="Build absolutely everything in all build scripts loaded.")
        parser.add_argument("--rebuild",          default = None, action="store_true",  help="Rebuild everything")
        parser.add_argument("--shuffle",          default = None, action="store_true",  help="Shuffle task order to shake out dependency issues")
        parser.add_argument("--trace",            default = None, action="store_true",  help="Trace all text expansion")
        parser.add_argument("--use_color",        default = None, action="store_true",  help="Use color in the console output")

        # fmt: on

        # Ignore the name of the script that loaded Hancho
        (flags, unrecognized) = parser.parse_known_args(args)

        # Unrecognized command line parameters also become module config fields if they are
        # flag-like
        extra_flags = {}
        for span in unrecognized:
            import re
            if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
                key = match.group(1)
                val = match.group(2)

                # FIXME add a test for this converter thinger

                if val is None:
                    # this is so that --foo turns into {foo:True}
                    val = True
                elif val in ["True", "true", "1"]:
                    val = True
                elif val in ["False", "false", "0"]:
                    val = False
                else:
                    for converter in (float, int, str):
                        try:
                            val = converter(val)
                            break
                        except ValueError:
                            pass
                extra_flags[key] = val

        flags = Dict(vars(flags), extra_flags)
        return flags

    #-----------------------------------------------------------------------------------------------

    match_pointer = re.compile(r"<(\w+) (\w+) at 0[xX][0-9a-fA-F]+>")

    @classmethod
    def load_file(cls, script_path : str, is_repo : bool, *args, **kwargs) -> types.ModuleType:
        script_path = hancho.config.expand_all(script_path, str)
        script_path = os.path.abspath(script_path)

        assert os.path.isfile(script_path)
        with open(script_path, encoding="utf-8") as file:
            Loader.loaded_files.append(script_path)
            source = file.read()

        return cls.load_str(script_path, is_repo, source, *args, **kwargs)

    @classmethod
    def load_str(cls, script_path, is_repo : bool, source : str, *args, **kwargs) -> types.ModuleType:
        """This is split out from load_script for testing purposes."""

        code = compile(source, script_path, "exec", dont_inherit=True)

        (script_dir, script_file) = os.path.split(script_path)
        (script_name, script_ext) = os.path.splitext(script_file)

        cls.log_load(script_path, is_repo)

        code = compile(source, script_path, "exec", dont_inherit=True)
        new_module = types.ModuleType(script_name)
        new_module.__dict__.update(
            __file__ = script_path,
            __code__ = code,
            hancho   = hancho,
        )

        #----------------------------------------
        # Create the script-specific config that points the 'repo' and 'this' paths at the given
        # script.

        old_config = Loader.cv_config.get()

        new_config = Dict(
            old_config,
            is_repo     = is_repo,
            script_dir  = script_dir,
            script_file = script_file,
            repo_dir    = script_dir  if is_repo else old_config.repo_dir,
            repo_file   = script_file if is_repo else old_config.repo_file,
            this_repo   = new_module  if is_repo else old_config.this_repo,
            this_module = new_module,
            *args,
            **kwargs
        )

        #----------------------------------------
        # Dedupe the load - only scripts with identical real paths and identical module configs are
        # deduped.

        config_dump = Log.dump_to_str(key = None, val = new_config)
        config_dump = Loader.match_pointer.sub(r"<\1 \2 at 0x...>", config_dump)

        script_path_real = os.path.realpath(script_path)
        dedupe_key = hash((script_path_real, config_dump))
        dedupe = cls.dedupe.get(dedupe_key, None)
        if dedupe is not None:
            return dedupe

        cls.dedupe[dedupe_key] = new_module

        #----------------------------------------
        # Run the module.

        with (chdir(new_config.script_dir), Loader.cv_config.set(new_config)):
            exec(code, new_module.__dict__)

        return new_module

    #----------------------------------------

    @classmethod
    def log_load(cls, script_path, is_repo):
        debug   = hancho.config.eval("debug", bool)
        verbose = hancho.config.eval("verbose", bool)
        script_type = "repo" if is_repo else "script"

        if debug or verbose:
            message  = Utils.color(128, 128, 255)
            message += f"Loading {script_type} {script_path}"
            message += Utils.color()
            message += "\n"
            Log.log(message)

# endregion
####################################################################################################
# region Runner

class Runner:

    all_tasks : list[Task]
    queued_tasks : list[Task]
    started_tasks : list[Task]
    finished_tasks : list[Task]
    core_max  : int
    core_sem  : asyncio.Semaphore
    core_lock : asyncio.Lock

    @classmethod
    def reset(cls, core_max):
        cls.all_tasks = []
        cls.queued_tasks = []
        cls.started_tasks = []
        cls.finished_tasks = []
        cls.core_max  = core_max
        cls.core_sem  = asyncio.Semaphore(core_max)
        cls.core_lock = asyncio.Lock()

    #--------------------------------------------------------------------------------

    class Cores:
        def __init__(self, count):
            self.count = count

        async def __aenter__(self):
            async with Runner.core_lock:
                for _ in range(self.count):
                    await Runner.core_sem.acquire()
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            for _ in range(self.count):
                Runner.core_sem.release()
            return False

    #--------------------------------------------------------------------------------

    @classmethod
    def queue_all_tasks(cls):
        for task in cls.all_tasks:
            task.queue()

    @classmethod
    def queue_root_tasks(cls):
        for task in cls.all_tasks:
            if task._config.this_repo == Loader.root_repo:
                task.queue()

    @classmethod
    def queue_tasks_by_regex(cls, target_regex):
        for task in cls.all_tasks:
            if target_regex.search(task._config.name):
                task.queue()

    #--------------------------------------------------------------------------------

    @classmethod
    def sync_run_tasks(cls):
        """Synchronously run all queued tasks until we're done with all of them."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = asyncio.run(cls.async_run_tasks())
        loop.close()
        return result

    #--------------------------------------------------------------------------------

    @classmethod
    async def async_run_tasks(cls):
        """Run all tasks in the queue until we run out."""

        # Tasks can create other tasks, and we don't want to block waiting on a whole batch of
        # tasks to complete before queueing up more. Instead, we just keep queuing up any pending
        # tasks after awaiting each one. Because we're awaiting tasks in the order they were
        # created, this will effectively walk through all tasks in dependency order.

        while cls.queued_tasks or cls.started_tasks:
            if hancho.config.shuffle:
                Log.log(f"Shufflin' {len(cls.queued_tasks)} tasks")
                import random
                random.shuffle(cls.queued_tasks)

            while cls.queued_tasks:
                task = cls.queued_tasks.pop(0)
                task.start()
                cls.started_tasks.append(task)

            task = cls.started_tasks.pop(0)
            asyncio_task = Utils.check(asyncio.Task, task._asyncio_task)

            try:
                await asyncio_task
                cls.finished_tasks.append(task)
            except BaseException as ex:  # pylint: disable=broad-exception-caught
                # Both broken and failed tasks should end up here.
                #task.log_task_failure(ex)
                if task._config.should_fail:
                    cls.finished_tasks.append(task)

            fail_count = Stats.tasks_failed + Stats.tasks_cancelled + Stats.tasks_broken
            if hancho.config.keep_going and fail_count >= hancho.config.keep_going:
                Log.log("Too many failures, cancelling tasks and stopping build\n")
                cls.cancel_all_tasks()
                break

        return -1 if Stats.tasks_failed or Stats.tasks_broken else 0

    #--------------------------------------------------------------------------------

    @classmethod
    def cancel_all_tasks(cls):
        for task in cls.started_tasks:
            if task._asyncio_task is not None:
                task._asyncio_task.cancel()
                tasks_cancelled += 1

    #--------------------------------------------------------------------------------

    @classmethod
    def run_tool(cls, tool : str):
        if tool == "clean":
            for task in cls.all_tasks:
                build_root = os.path.realpath(task._config.eval("build_root", str))
                build_root = os.path.relpath(build_root, os.getcwd())
                if os.path.isdir(build_root):
                    Log.log(f"Wiping build_root {build_root}\n")
                    import shutil
                    shutil.rmtree(build_root, ignore_errors=True)
            Log.log("Clean done\n")
            return 0
        else:
            assert False, f"Don't know how to run tool {tool}"

# endregion
#---------------------------------------------------------------------------------------------------
# These are aliases to stuff in Hancho that have been pulled out so they can be used by
# template expansion so you can do {flatten(x)} instead of {Utils.flatten(x)} in macros, and
# use "hancho.flatten(x)" in your script instead of "hancho.Utils.flatten(x)"

aliases = Dict(
    # path.dirname and path.basename used by makefile-related rules
    path = os.path,
    abs  = Path.abs,
    base = Path.base,
    ext  = Path.ext,
    norm = Path.norm,
    real = Path.real,
    rel  = Path.rel,
    stem = Path.stem,
    load = lambda file, *args, **kwargs : Loader.load_file(file, False, *args, kwargs),
    repo = lambda file, *args, **kwargs : Loader.load_file(file, True, *args, kwargs),

    flatten = Utils.flatten,
    run_cmd = Utils.run_cmd,
    color   = Utils.color,
    join    = Utils.join,
)

#---------------------------------------------------------------------------------------------------

if __name__ == "__main__" and "hancho" not in sys.modules:
    sys.modules["hancho"] = hancho

if __name__ == "__main__":
    #print(sys.argv)
    sys.exit(main())
else:
    init()
