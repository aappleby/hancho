#!/usr/bin/python3

"""Hancho v0.1.0 @ 2024-03-25 - A simple, pleasant build system."""

import argparse
import asyncio
import builtins
import inspect
import io
import json
import os
import re
import subprocess
import sys
import traceback
import time
import types
import random
from os import path
import glob
import resource
from types import MappingProxyType

# If we were launched directly, a reference to this module is already in
# sys.modules[__name__]. Stash another reference in sys.modules["hancho"] so
# that build.hancho and descendants don't try to load a second copy of Hancho.
sys.modules["hancho"] = sys.modules[__name__]

def log_line(message):
    app.log += message
    if not Config.quiet:
        sys.stdout.write(message)
        sys.stdout.flush()


def log(message, *args, sameline=False, **kwargs):
    """Simple logger that can do same-line log messages like Ninja."""
    if not sys.stdout.isatty():
        sameline = False

    if sameline:
        kwargs.setdefault("end", "")

    output = io.StringIO()
    print(message, *args, file=output, **kwargs)
    output = output.getvalue()

    if not output:
        return

    if sameline:
        output = output[: os.get_terminal_size().columns - 1]
        output = "\r" + output + "\x1B[K"
        log_line(output)
    else:
        if app.line_dirty:
            log_line("\n")
        log_line(output)

    app.line_dirty = sameline


def _flatten(variant):
    if isinstance(variant, list):
        return [x for element in variant for x in _flatten(element)]
    return [variant]


def _abs_path(raw_path, strict=False):
    if isinstance(raw_path, list):
        return [_abs_path(p, strict) for p in raw_path]
    result = path.abspath(raw_path)
    if strict and not path.exists(result):
        raise FileNotFoundError(raw_path)
    return result


def _rel_path(path1, path2):
    if isinstance(path1, list):
        return [_rel_path(p, path2) for p in path1]
    # Generating relative paths in the presence of symlinks doesn't work with either
    # Path.relative_to or os.path.relpath - the former balks at generating ".." in paths, the
    # latter does generate them but "path/with/symlink/../foo" doesn't behave like you think it
    # should. What we really want is to just remove redundant cwd stuff off the beginning of the
    # path, which we can do with simple string manipulation.
    return path1.removeprefix(path2 + "/") if path1 != path2 else "."


def _join_path2(path1, path2, *args):
    if len(args):
        return [_join_path(path1, p) for p in _join_path(path2, *args)]
    if isinstance(path1, list):
        return [_join_path(p, path2) for p in _flatten(path1)]
    if isinstance(path2, list):
        return [_join_path(path1, p) for p in _flatten(path2)]
    return path.join(path1, path2)

def _join_path(path1, path2, *args):
    result = _join_path2(path1, path2, *args)
    return _flatten(result) if isinstance(result, list) else result

def _join_prefix(prefix, strings):
    return [prefix+str(s) for s in _flatten(strings)]

def _join_suffix(strings, suffix):
    return [str(s)+suffix for s in _flatten(strings)]

def _color(red=None, green=None, blue=None):
    """Converts RGB color to ANSI format string."""
    # Color strings don't work in Windows console, so don't emit them.
    if not Config.use_color or os.name == "nt":
        return ""
    if red is None:
        return "\x1B[0m"
    return f"\x1B[38;2;{red};{green};{blue}m"


def _run_cmd(cmd):
    """Runs a console command synchronously and returns its stdout with whitespace stripped."""
    return subprocess.check_output(cmd, shell=True, text=True).strip()


def _swap_ext(name, new_ext):
    """Replaces file extensions on either a single filename or a list of filenames."""
    if isinstance(name, list):
        return [_swap_ext(n, new_ext) for n in name]
    return path.splitext(name)[0] + new_ext


def _mtime(filename):
    """Gets the file's mtime and tracks how many times we've called mtime()"""
    app.mtime_calls += 1
    return os.stat(filename).st_mtime_ns


def _maybe_as_number(text):
    """Tries to convert a string to an int, then a float, then gives up. Used for ingesting
    unrecognized flag values."""
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


async def _await_variant(variant):
    """Recursively replaces every awaitable in the variant with its awaited value."""
    match variant:
        case asyncio.CancelledError():
            raise variant
        case Task():
            # If the task hasn't been queued yet, queue it now before we await it.
            if variant._promise is None:
                app.queue_pending_tasks()

            # We don't recurse through subtasks because they should await themselves.
            if inspect.isawaitable(variant._promise):
                _promise = await variant._promise
                variant._promise = await _await_variant(_promise)
        case Config():
            await _await_variant(variant.__dict__)
        case dict():
            for key in variant:
                variant[key] = await _await_variant(variant[key])
        case list():
            for index, value in enumerate(variant):
                variant[index] = await _await_variant(value)
        case _ if inspect.isawaitable(variant):
            variant = await variant
    return variant

####################################################################################################

class Dumper:
    def __init__(self, max_depth = 2):
        self.tabs = 0
        self.depth = 0
        self.max_depth = max_depth

    def indent(self):
        return "  " * self.tabs

    def dump(self, variant):
        result = ""
        match variant:
            case Task():
                result = f"{type(variant).__name__} @ {hex(id(variant))} "
                if self.depth >= self.max_depth:
                    result += "{...}"
                else:
                    result += self.dump(variant.__dict__)
            case Config():
                result = f"{type(variant).__name__} @ {hex(id(variant))} "
                if self.depth >= self.max_depth:
                    result += "{...}"
                else:
                    result += self.dump(variant.__dict__)
            case list():
                result = self.dump_list(variant)
            case dict() | MappingProxyType():
                result = self.dump_dict(variant)
            case str():
                result = '"' + str(variant) + '"'
            case _:
                result = str(variant)
        return result

    def dump_list(self, l):
        result = "["
        self.depth += 1
        self.tabs += 1
        for val in l:
            if len(l) > 0:
                result += "\n" + self.indent()
            result += self.dump(val)
            result += ", "
        self.depth -= 1
        self.tabs -= 1
        if len(l) > 0:
            result += "\n" + self.indent()
        result += "]"
        return result

    def dump_dict(self, d):
        result = "{\n"
        self.depth += 1
        self.tabs += 1
        for key, val in d.items():
            result += self.indent() + f"{key} = {self.dump(val)},\n"
        self.tabs -= 1
        self.depth -= 1
        result += self.indent() + "}"
        return result


class Sentinel:
    pass

####################################################################################################

class Config:
    """A Config object is just a 'bag of fields'."""

    def __init__(self, *args, **kwargs):
        filtered_args = []
        source_files = Sentinel()
        build_files = Sentinel()

        for arg in args:
            if isinstance(arg, Config) and not isinstance(arg, Task):
                filtered_args.append(arg)
            else:
                if isinstance(source_files, Sentinel):
                    source_files = arg
                elif isinstance(build_files, Sentinel):
                    build_files = arg
                else:
                    raise ValueError("Too many non-config args")

        if not isinstance(source_files, Sentinel):
            kwargs.setdefault("source_files", source_files)
        if not isinstance(build_files, Sentinel):
            kwargs.setdefault("build_files", build_files)

        for arg in filtered_args:
            self.update(arg)
        self.update(kwargs)

    # required to use config as mapping in eval()
    def __getitem__(self, key):
        return getattr(self, key)

    def __repr__(self):
        return Dumper(1).dump(self)

    def __iadd__(self, other):
        self.update(other)
        return self

    def update(self, kwargs):
        for key, val in kwargs.items():
            if val is not None:
                setattr(self, key, val)

    # required to support "**config"
    def keys(self):
        return self.__dict__.keys()

    def pop(self, key, val = Sentinel()):
        if not isinstance(val, Sentinel):
            return self.__dict__.pop(key, val)
        else:
            return self.__dict__.pop(key)

    def items(self):
        return self.__dict__.items()

    def expand(self, variant):
        return expand(self, variant)

    def load(self, file_name, *args, **kwargs):
        return load_file(file_name, False, *args, **kwargs)

    def repo(self, file_name, *args, **kwargs):
        return load_file(file_name, True, *args, **kwargs)

    def reset(self):
        return app.reset()

    def build(self):
        return app.build()

    def get_log(self):
        return app.log

    def __call__(self, *args, **kwargs):
        return Task(self, *args, **kwargs)

#----------------------------------------

def load_file(file_name, as_repo, *args, **kwargs):
    mod_config = Config(*args, **kwargs)

    file_name = mod_config.expand(file_name)
    abs_file_path = _join_path(app.topmod().base_path, file_name)

    repo_path = path.dirname(abs_file_path) if as_repo else app.topmod().repo_path
    repo_name = path.basename(repo_path) if as_repo else app.topmod().repo_name
    file_path = path.dirname(abs_file_path)
    file_name = path.basename(abs_file_path)

    return app.load_module(repo_path, repo_name, file_path, file_name, mod_config)

####################################################################################################
# The template expansion / macro evaluation code requires some explanation.
#
# We do not necessarily know in advance how the users will nest strings, templates, callbacks,
# etcetera. So, when we need to produce a flat list of files from whatever was passed to
# source_files, we need to do a bunch of dynamic-dispatch-type stuff to ensure that we can always
# turn that thing into a flat list of files.
#
# We also need to ensure that if anything in this process throws an exception (or if an exception
# was passed into a rule due to a previous rule failing) that we always propagate the exception up
# to Task.run_async, where it will be handled and propagated to other Tasks.
#
# The result of this is that the functions here are mutually recursive in a way that can lead to
# confusing callstacks, but that should handle every possible case of stuff inside other stuff.
#
# The depth checks are to prevent recursive runaway - the MAX_EXPAND_DEPTH limit is arbitrary but
# should suffice.

# The maximum number of recursion levels we will do to expand a macro.
# Tests currently require MAX_EXPAND_DEPTH >= 6
MAX_EXPAND_DEPTH = 20

# Matches "{expression}" macros
macro_regex = re.compile("^{[^}]*}$")

# Matches macros inside a template string.
template_regex = re.compile("{[^}]*}")

def expand(config, variant, fail_ok=False):
    """Expands all templates anywhere inside 'variant'."""
    match variant:
        case str() if macro_regex.search(variant):
            return eval_macro(config, variant, fail_ok)
        case str() if template_regex.search(variant):
            return expand_template(config, variant, fail_ok)
        case list():
            return [expand(config, s) for s in variant]
        case Task():
            return expand(config, variant._promise, fail_ok)
            #return variant
        case Config():
            return Expander(variant)
        case dict():
            return Expander(Config(variant))
        case BaseException():
            raise variant
        case Sentinel():
            raise ValueError("Tried to expand a Sentinel")
        case _:
            return variant


def expand_template(config, template, fail_ok=False):
    """Replaces all macros in template with their stringified values."""
    if config.trace:
        log(("┃" * app.expand_depth) + f"┏ Expand '{template}'")

    result = ""
    try:
        app.expand_depth += 1
        old_template = template
        while span := template_regex.search(template):
            result += template[0 : span.start()]
            try:
                macro = template[span.start() : span.end()]
                variant = eval_macro(config, macro, fail_ok)
                if isinstance(variant, Task):
                    variant = expand(config, variant._promise, fail_ok)
                result += " ".join([str(s) for s in _flatten(variant)])
            except BaseException as err:
                log(f"{_color(255, 255, 0)}Expanding template '{old_template}' failed!{_color()}")
                raise err
            template = template[span.end() :]
        result += template
    finally:
        app.expand_depth -= 1

    if config.trace:
        log(("┃" * app.expand_depth) + f"┗ '{result}'")
    return result

class Expander:
    """JIT template expansion for use in eval()."""

    def __init__(self, config):
        self.config = config
        self.trace = config.trace

    __getitem__ = lambda self, key: self.get(key)
    __getattr__ = lambda self, key: self.get(key)

    def get(self, key):
        val = getattr(self.config, key)
        if self.trace:
            log(("┃" * app.expand_depth) + f" Read '{key}' = '{val}'")
        return expand(self.config, val)


def eval_macro(config, macro, fail_ok=False):
    """Evaluates the contents of a "{macro}" string."""
    if app.expand_depth > MAX_EXPAND_DEPTH:
        raise RecursionError(f"Expanding '{macro}' failed to terminate")
    if config.trace:
        log(("┃" * app.expand_depth) + f"┏ Eval '{macro}'")

    if not isinstance(config, Expander):
        config = Expander(config)

    app.expand_depth += 1
    # pylint: disable=eval-used
    result = ""
    try:
        # We must pass the JIT expanded config to eval() otherwise we'll try and join unexpanded
        # paths and stuff, which will break.
        result = eval(macro[1:-1], {}, config)
    except BaseException as err:
        if not fail_ok:
            log(f"{_color(255, 255, 0)}Expanding macro '{macro}' failed! - {err}{_color()}")
            raise err
    finally:
        app.expand_depth -= 1

    if config.trace:
        log(("┃" * app.expand_depth) + f"┗ {result}")
    return result

####################################################################################################

class Task(Config):
    """Calling a Rule creates a Task."""

    # pylint: disable=too-many-instance-attributes
    # pylint: disable=attribute-defined-outside-init

    def __init__(self, *args, **kwargs):

        path_config = Config(
            repo_path = app.topmod().repo_path,
            repo_name = app.topmod().repo_name,
            base_path = app.topmod().base_path,
        )

        task_config = Config(*args, **kwargs)

        # Note - We can't set _promise = asyncio.create_task() here, as we're not guaranteed to be
        # in an event loop yet

        self._loaded_modules = list(app.loaded_modules)

        self.update(default_task_config)
        self.update(path_config)
        self.update(task_config)

        self._reason = None
        self._promise = None

        app.tasks_total += 1
        app.pending_tasks.append(self)

    def __repr__(self):
        return Dumper(3).dump(self)

    async def run_async(self):
        """Entry point for async task stuff, handles exceptions generated during task execution."""
        try:
            # Await everything awaitable in this task's rule.
            _promise = self._promise
            self._promise = None
            await _await_variant(self.__dict__)
            self._promise = _promise

            # Everything awaited, task_init runs synchronously.
            self.task_init()

            # Check if we need a rebuild
            self._reason = self.needs_rerun(self.force)

            if self.debug:
                log(self)

            # Run the commands if we need to.
            if self._reason:
                result = await self.run_commands()
                app.tasks_pass += 1
            else:
                #if self.verbose or self.debug:
                #    log(
                #        f"{_color(128,196,255)}[{self.task_index}/{app.tasks_total}]{_color()} {self.desc}",
                #        sameline=not self.verbose,
                #    )
                #    log(f"{_color(128,128,128)}Files {self._build_files} are up to date{_color()}")
                result = self._build_files
                app.tasks_skip += 1

            return result

        # If this task failed, we print the error and propagate a cancellation to downstream tasks.
        except BaseException as err:
            log(f"{_color(255, 128, 128)}{traceback.format_exc()}{_color()}")
            app.tasks_fail += 1
            return asyncio.CancelledError()

        # If any of this tasks's dependencies were cancelled, we propagate the cancellation to
        # downstream tasks.
        except asyncio.CancelledError as cancel:
            app.tasks_cancel += 1
            return cancel

    def task_init(self):
        """All the setup steps needed before we run a task."""

        app.task_counter += 1
        if app.task_counter > 1000:
            sys.exit(-1)
        self.task_index = app.task_counter

        # Expand all the critical fields
        self._desc          = self.expand(self.desc)
        self._command       = self.expand(self.command)
        self._command_path  = self.expand(self.abs_command_path)
        self._command_files = self.expand(self.abs_command_files)
        self._source_files  = self.expand(self.abs_source_files)
        self._build_files   = self.expand(self.abs_build_files)
        self._build_deps    = self.expand(self.abs_build_deps)

        # Check for missing input files/paths
        if not path.exists(self._command_path):
            raise FileNotFoundError(self._command_path)

        for file in self._command_files:
            if not path.exists(file):
                raise FileNotFoundError(file)

        for file in self._source_files:
            if not path.exists(file):
                raise FileNotFoundError(file)

        # Check that all build files would end up under root_path
        for file in self._build_files:
            if not file.startswith(Config.root_path):
                raise ValueError(f"Path error, build_path {file} is not under root_path {Config.root_path}")

        # Check for duplicate task outputs
        for abs_file in self._build_files:
            if abs_file in app.all_build_files:
                raise NameError(f"Multiple rules build {abs_file}!")
            app.all_build_files.add(abs_file)

        # Make sure our output directories exist
        if not self.dry_run:
            for abs_file in self._build_files:
                os.makedirs(path.dirname(abs_file), exist_ok=True)


    def needs_rerun(self, force=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""
        # Pylint really doesn't like this function, lol.
        # pylint: disable=too-many-return-statements
        # pylint: disable=too-many-branches

        if force:
            return f"Files {self._build_files} forced to rebuild"
        if not self._source_files:
            return "Always rebuild a target with no inputs"
        if not self._build_files:
            return "Always rebuild a target with no outputs"

        # Check if any of our output files are missing.
        for abs_file in self._build_files:
            if not path.exists(abs_file):
                return f"Rebuilding because {abs_file} is missing"

        # Check if any of our input files are newer than the output files.
        min_out = min(_mtime(f) for f in self._build_files)

        for abs_file in self._source_files:
            if _mtime(abs_file) >= min_out:
                return f"Rebuilding because {abs_file} has changed"

        for abs_file in self._command_files:
            if _mtime(abs_file) >= min_out:
                return f"Rebuilding because {abs_file} has changed"

        for mod in self._loaded_modules:
            if _mtime(mod.__file__) >= min_out:
                return f"Rebuilding because {mod.__file__} has changed"

        # Check all dependencies in the depfile, if present.
        depformat = getattr(self, "depformat", "gcc")

        for abs_depfile in self._build_deps:
            if not path.exists(abs_depfile):
                continue
            if self.debug:
                log(f"Found depfile {abs_depfile}")
            with open(abs_depfile, encoding="utf-8") as depfile:
                deplines = None
                if depformat == "msvc":
                    # MSVC /sourceDependencies json depfile
                    deplines = json.load(depfile)["Data"]["Includes"]
                elif depformat == "gcc":
                    # GCC .d depfile
                    deplines = depfile.read().split()
                    deplines = [d for d in deplines[1:] if d != "\\"]
                else:
                    raise ValueError(f"Invalid depformat {depformat}")

                # The contents of the depfile are RELATIVE TO THE WORKING DIRECTORY
                deplines = [path.join(self._command_path, d) for d in deplines]
                for abs_file in deplines:
                    if _mtime(abs_file) >= min_out:
                        return f"Rebuilding because {abs_file} has changed"

        # All checks passed; we don't need to rebuild this output.
        # Empty string = no reason to rebuild
        return ""

    async def run_commands(self):
        """Grabs a lock on the jobs needed to run this task's commands, then runs all of them."""

        result = []
        job_count = getattr(self, "job_count", 1)
        try:
            # Wait for enough jobs to free up to run this task.
            await app.acquire_jobs(job_count)

            # Print the "[1/N] Compiling foo.cpp -> foo.o" status line and debug information
            log(
                f"{_color(128,255,196)}[{self.task_index}/{app.tasks_total}]{_color()} {self._desc}",
                sameline=not self.verbose,
            )

            if self.verbose or self.debug:
                log(f"{_color(128,128,128)}Reason: {self._reason}{_color()}")

            commands = _flatten(self._command)
            #print(commands)
            for _command in commands:
                if self.verbose or self.debug:
                    rel_command_path = _rel_path(self._command_path, Config.root_path)
                    log(f"{_color(128,128,255)}{rel_command_path}$ {_color()}", end="")
                    log("(DRY RUN) " if self.dry_run else "", end="")
                    log(_command)
                result = await self.run_command(_command)
        finally:
            await app.release_jobs(job_count)

        return result

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        # Early exit if this is just a dry run
        if self.dry_run:
            return self._build_files

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            result = command(self)
            if inspect.isawaitable(result):
                result = await result
            return result

        # Non-string non-callable commands are not valid
        if not isinstance(command, str):
            raise ValueError(f"Don't know what to do with {command}")

        # Create the subprocess via asyncio and then await the result.
        try:
            #print("Creating thread")
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=self._command_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            (stdout_data, stderr_data) = await proc.communicate()
        except RuntimeError:
            sys.exit(-1)

        self.stdout = stdout_data.decode()
        self.stderr = stderr_data.decode()
        self.returncode = proc.returncode

        # Print command output if needed
        if (self.stdout or self.stderr) and not self.quiet:
            if self.stderr:
                log("-----stderr-----")
                log(self.stderr, end="")
            if self.stdout:
                log("-----stdout-----")
                log(self.stdout, end="")

        # Task complete, check the task return code
        if self.returncode:
            raise ValueError(
                f"Command '{command}' exited with return code {self.returncode}"
            )

        # Task passed, return the output file list
        return self._build_files

####################################################################################################

class App:
    """The application state. Mostly here so that the linter will stop complaining about my use of
    global variables. :D"""

    # pylint: disable=too-many-instance-attributes
    def __init__(self):
        self.loaded_modules = []
        self.dirstack = [os.getcwd()]
        self.modstack = []

        # We're adding a 'fake' module to the top of the mod stack so that applications that import
        # Hancho directly don't try to read modstack[-1] from an empty stack
        fake_hancho = Config()

        fake_module = type(sys)("fake_module")
        fake_module.hancho    = Config()
        fake_module.repo_path = os.getcwd()
        fake_module.repo_name = ""
        fake_module.base_path = os.getcwd()
        self.modstack.append(fake_module)

        self.all_build_files = set()

        self.tasks_total = 0
        self.tasks_pass = 0
        self.tasks_fail = 0
        self.tasks_skip = 0
        self.tasks_cancel = 0
        self.task_counter = 0

        self.mtime_calls = 0
        self.line_dirty = False
        self.expand_depth = 0

        self.pending_tasks = []
        self.queued_tasks = []
        self.jobs_available = os.cpu_count()
        self.jobs_lock = asyncio.Condition()
        self.log = ""

    ########################################

    def pushdir(self, path):
        path = _abs_path(path, strict=True)
        self.dirstack.append(path)
        os.chdir(path)

    def popdir(self):
        self.dirstack.pop()
        os.chdir(self.dirstack[-1])

    def topdir(self):
        return self.dirstack[-1]

    def topmod(self):
        return self.modstack[-1]

    ########################################

    def reset(self):
        self.__init__()

    ########################################

    def main(self):
        result = -1
        try:
            self.parse_args()
            self.pushdir(Config.root_path)
            self.load_hanchos()
            result = self.build()
        finally:
            self.popdir()
        return result

    ########################################

    def parse_args(self):
        # pylint: disable=line-too-long
        # fmt: off
        parser = argparse.ArgumentParser()
        parser.add_argument("root_name",       default="build.hancho", type=str, nargs="?", help="The name of the .hancho file(s) to build")
        parser.add_argument("-C", "--chdir",   default=".", dest="root_path", type=str,     help="Change directory before starting the build")
        parser.add_argument("-j", "--jobs",    default=os.cpu_count(), type=int,            help="Run N jobs in parallel (default = cpu_count)")
        parser.add_argument("-v", "--verbose", default=False, action="store_true",          help="Print verbose build info")
        parser.add_argument("-q", "--quiet",   default=False, action="store_true",          help="Mute all output")
        parser.add_argument("-n", "--dry_run", default=False, action="store_true",          help="Do not run commands")
        parser.add_argument("-d", "--debug",   default=False, action="store_true",          help="Print debugging information")
        parser.add_argument("-f", "--force",   default=False, action="store_true",          help="Force rebuild of everything")
        parser.add_argument("-s", "--shuffle", default=False, action="store_true",          help="Shuffle task order to shake out dependency issues")
        parser.add_argument("-t", "--trace",   default=False, action="store_true",          help="Trace template & macro expansion")
        # fmt: on

        (flags, unrecognized) = parser.parse_known_args()
        flags = flags.__dict__
        # Root path must be absolute.
        flags["root_path"] = _abs_path(flags["root_path"])

        # Unrecognized command line parameters also become global Config fields if they are
        # flag-like
        unrecognized_flags = {}
        for span in unrecognized:
            if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
                key = match.group(1)
                val = match.group(2)
                val = _maybe_as_number(val) if val is not None else True
                unrecognized_flags[key] = val

        for key, val in flags.items():
            setattr(Config, key, val)

        for key, val in unrecognized_flags.items():
            setattr(Config, key, val)

    ########################################

    def load_hanchos(self):
        time_a = time.perf_counter()

        if Config.debug:
            log(f"global_config = {Dumper().dump(Config.__dict__)}")

        root_config = Config()
        self.load_module(
            repo_path=root_config.root_path,
            repo_name=path.basename(root_config.root_path),
            file_path=root_config.root_path,
            file_name=root_config.root_name,
            config=root_config
        )
        time_b = time.perf_counter()

        #if Config.debug or Config.verbose:
        #    log(f"Loading .hancho files took {time_b-time_a:.3f} seconds")
        log(f"Loading .hancho files took {time_b-time_a:.3f} seconds")

    ########################################

    def build(self):
        """Run tasks until we're done with all of them."""
        result = -1
        try:
            # For some reason "result = asyncio.run(self.async_main())" might be breaking actions
            # in Github, so I'm using get_event_loop().run_until_complete().
            # Seems to fix the issue.
            result = asyncio.get_event_loop().run_until_complete(self.async_run_tasks())
        except BaseException as err:
            log(f"{_color(255, 128, 128)}{traceback.format_exc()}{_color()}")
        return result

    ########################################

    def queue_pending_tasks(self):
        """Creates an asyncio.Task for each task in the pending list and clears the pending list."""

        if self.pending_tasks:
            if Config.shuffle:
                log(f"Shufflin' {len(self.pending_tasks)} tasks")
                random.shuffle(self.pending_tasks)

            for task in self.pending_tasks:
                task._promise = asyncio.create_task(task.run_async())
                self.queued_tasks.append(task)
            self.pending_tasks = []

    ########################################

    async def async_run_tasks(self):
        # Run all tasks in the queue until we run out.

        self.jobs_available = Config.jobs

        # Tasks can create other tasks, and we don't want to block waiting on a whole batch of
        # tasks to complete before queueing up more. Instead, we just keep queuing up any pending
        # tasks after awaiting each one. Because we're awaiting tasks in the order they were
        # created, this will effectively walk through all tasks in dependency order.

        time_a = time.perf_counter()

        self.queue_pending_tasks()
        while self.queued_tasks:
            task = self.queued_tasks.pop(0)
            if inspect.isawaitable(task._promise):
                await task._promise
            self.queue_pending_tasks()

        time_b = time.perf_counter()

        if Config.debug or Config.verbose:
            log(f"Running tasks took {time_b-time_a:.3f} seconds")

        # Done, print status info if needed
        if Config.debug:
            log(f"tasks total:     {self.tasks_total}")
            log(f"tasks passed:    {self.tasks_pass}")
            log(f"tasks failed:    {self.tasks_fail}")
            log(f"tasks skipped:   {self.tasks_skip}")
            log(f"tasks cancelled: {self.tasks_cancel}")
            log(f"mtime calls:     {self.mtime_calls}")

        if self.tasks_fail:
            log(f"hancho: {_color(255, 128, 128)}BUILD FAILED{_color()}")
        elif self.tasks_pass:
            log(f"hancho: {_color(128, 255, 128)}BUILD PASSED{_color()}")
        else:
            log(f"hancho: {_color(128, 128, 255)}BUILD CLEAN{_color()}")

        return -1 if self.tasks_fail else 0

    ########################################

    def load_module(self, repo_path, repo_name, file_path, file_name, config):
        """Loads a Hancho module ***while chdir'd into its directory***"""

        assert not template_regex.search(repo_path)
        assert not template_regex.search(repo_name)
        assert not template_regex.search(file_path)
        assert not template_regex.search(file_name)

        assert path.isabs(repo_path)
        assert not path.isabs(repo_name)
        assert path.isabs(file_path)
        assert not path.isabs(file_name)

        file_pathname = _join_path(file_path, file_name)

        #if config.debug or config.verbose:
        #    log(_color(128,255,128) + f"Loading module {file_pathname}" + _color())
        log(("┃ " * (len(app.modstack) - 1)), end="")
        log(_color(128,255,128) + f"Loading module {file_pathname}" + _color())

        with open(file_pathname, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, file_name, "exec", dont_inherit=True)

        mod_name = path.splitext(file_name)[0]
        module = type(sys)(mod_name)
        module.__file__ = file_pathname
        module.__builtins__ = builtins

        module.hancho = config
        module.export = Config()
        module.repo_path = repo_path
        module.repo_name = repo_name
        module.base_path = file_path

        self.loaded_modules.append(module)

        try:
            # We must chdir()s into the .hancho file directory before running it so that
            # glob() can resolve files relative to the .hancho file itself. We are _not_ in an async
            # context here so there should be no other threads trying to change cwd.
            app.pushdir(file_path)
            self.modstack.append(module)

            # Why Pylint thinks this is not callable is a mystery.
            # pylint: disable=not-callable
            types.FunctionType(code, module.__dict__)()
        finally:
            self.modstack.pop()
            app.popdir()
        return module.export

    ########################################

    async def acquire_jobs(self, count):
        """Waits until 'count' jobs are available and then removes them from the job pool."""

        if count > Config.jobs:
            raise ValueError(f"Nedd {count} jobs, but pool is {Config.jobs}.")

        await self.jobs_lock.acquire()
        await self.jobs_lock.wait_for(lambda: self.jobs_available >= count)
        self.jobs_available -= count
        self.jobs_lock.release()

    ########################################
    # NOTE: The notify_all here is required because we don't know in advance which tasks will
    # be capable of running after we return jobs to the pool. HOWEVER, this also creates an
    # O(N^2) slowdown when we have a very large number of pending tasks (>1000) due to the
    # "Thundering Herd" problem - all tasks will wake up, only a few will acquire jobs, the
    # rest will go back to sleep again, this will repeat for every call to release_jobs().

    async def release_jobs(self, count):
        """Returns 'count' jobs back to the job pool."""

        await self.jobs_lock.acquire()
        self.jobs_available += count
        self.jobs_lock.notify_all()
        self.jobs_lock.release()

####################################################################################################
# All static methods and fields are available to use in any template string.

# fmt: off

default_task_config = Config(
    desc          = "{source_files} -> {build_files}",
    command       = Sentinel(),
    command_path  = "{base_path}",
    command_files = [],
    source_path   = "{base_path}",
    source_files  = [],
    build_dir     = "build",
    build_tag     = "",
    build_path    = "{root_path}/{build_dir}/{build_tag}/{repo_name}/{rel_path(abs_source_path, repo_path)}",
    build_files   = [],
    build_deps    = [],
    other_files   = [],
)

Config.Config    = Config
Config.Task      = Task

Config.abs_path  = staticmethod(_abs_path)
Config.rel_path  = staticmethod(_rel_path)
Config.join_path = staticmethod(_join_path)
Config.color     = staticmethod(_color)
Config.glob      = staticmethod(glob.glob)
Config.len       = staticmethod(len)
Config.run_cmd   = staticmethod(_run_cmd)
Config.swap_ext  = staticmethod(_swap_ext)
Config.flatten   = staticmethod(_flatten)
Config.print     = staticmethod(print)
Config.log       = staticmethod(log)
Config.path      = path
Config.re        = re

Config.join_prefix = staticmethod(_join_prefix)
Config.join_suffix = staticmethod(_join_suffix)


Config.jobs      = os.cpu_count()
Config.verbose   = False
Config.quiet     = False
Config.dry_run   = False
Config.debug     = False
Config.force     = False
Config.shuffle   = False
Config.trace     = False
Config.use_color = True

Config.first_source = "{flatten(source_files)[0]}"

Config.abs_command_path  = "{abs_path(join_path(base_path,   command_path))}"
Config.abs_source_path   = "{abs_path(join_path(base_path,   source_path))}"
Config.abs_build_path    = "{abs_path(join_path(base_path,   build_path))}"

Config.abs_command_files = "{flatten(join_path(abs_command_path, command_files))}"
Config.abs_source_files  = "{flatten(join_path(abs_source_path,  source_files))}"
Config.abs_build_files   = "{flatten(join_path(abs_build_path,   build_files))}"
Config.abs_build_deps    = "{flatten(join_path(abs_build_path,   build_deps))}"

Config.rel_source_path   = "{rel_path(abs_source_path,   abs_command_path)}"
Config.rel_build_path    = "{rel_path(abs_build_path,    abs_command_path)}"

Config.rel_command_files = "{rel_path(abs_command_files, abs_command_path)}"
Config.rel_source_files  = "{rel_path(abs_source_files,  abs_command_path)}"
Config.rel_build_files   = "{rel_path(abs_build_files,   abs_command_path)}"
Config.rel_build_deps    = "{rel_path(abs_build_deps,    abs_command_path)}"
# fmt: on

####################################################################################################
# Always create an App() object so we can use it for bookkeeping even if we loaded Hancho as a
# module instead of running it directly.

app = App()

if __name__ == "__main__":
    sys.exit(app.main())
