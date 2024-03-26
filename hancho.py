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
import types
from pathlib import Path
from glob import glob

# If we were launched directly, a reference to this module is already in
# sys.modules[__name__]. Stash another reference in sys.modules["hancho"] so
# that build.hancho and descendants don't try to load a second copy of Hancho.
sys.modules["hancho"] = sys.modules[__name__]

# The maximum number of recursion levels we will do to expand a macro.
# Tests currently require MAX_EXPAND_DEPTH >= 6
MAX_EXPAND_DEPTH = 20

# Matches "{expression}" macros
macro_regex = re.compile("^{[^}]*}$")

# Matches macros inside a template string.
template_regex = re.compile("{[^}]*}")


def log(message, *args, sameline=False, **kwargs):
    """Simple logger that can do same-line log messages like Ninja."""
    if global_config.quiet:
        return

    if not sys.stdout.isatty():
        sameline = False

    output = io.StringIO()
    print(message, *args, file=output, **kwargs)
    output = output.getvalue()

    if not output:
        return

    if sameline:
        output = output[: os.get_terminal_size().columns - 1]
        sys.stdout.write("\r" + output + "\x1B[K")
    else:
        if app.line_dirty:
            sys.stdout.write("\n")
        sys.stdout.write(output)

    app.line_dirty = sameline
    sys.stdout.flush()


def abspath(path):
    """Pathlib's path.absolute() doesn't resolve "foo/../bar", so we use os.path.abspath."""
    if isinstance(path, list):
        return [abspath(p) for p in path]
    return Path(os.path.abspath(path))


def relpath(path1, path2):
    """We don't want to generate paths with '..' in them, so we just try and remove the prefix.
    If we can't remove the prefix we'll still have an absolute path."""
    if isinstance(path1, list):
        return [relpath(p, path2) for p in path1]
    if str(path1) == str(path2):
        return Path("")
    return Path(str(path1).removeprefix(str(path2) + "/"))


def joinpath(*args):
    """Produces all possible concatenated paths from the given paths (or arrays of paths)."""
    if len(args) > 2:
        return joinpath(args[0], joinpath(*args[1:]))
    if isinstance(args[0], list):
        return [joinpath(a, args[1]) for a in args[0]]
    if isinstance(args[1], list):
        return [joinpath(args[0], a) for a in args[1]]
    return Path(args[0]) / Path(args[1])


def color(red=None, green=None, blue=None):
    """Converts RGB color to ANSI format string."""
    # Color strings don't work in Windows console, so don't emit them.
    if os.name == "nt":
        return ""
    if red is None:
        return "\x1B[0m"
    return f"\x1B[38;2;{red};{green};{blue}m"


def run_cmd(cmd):
    """Runs a console command synchronously and returns its stdout with whitespace stripped."""
    return subprocess.check_output(cmd, shell=True, text=True).strip()


def swap_ext(name, new_ext):
    """Replaces file extensions on either a single filename or a list of filenames."""
    if isinstance(name, list):
        return [swap_ext(n, new_ext) for n in name]
    return Path(name).with_suffix(new_ext)


def mtime(filename):
    """Gets the file's mtime and tracks how many times we've called mtime()"""
    app.mtime_calls += 1
    return Path(filename).stat().st_mtime


def maybe_as_number(text):
    """Tries to convert a string to an int, then a float, then gives up. Used for ingesting
    unrecognized flag values."""
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def check_path(path, *, exists=False):
    """Sanity-checks an expanded path - it must be absolute and without '..'s."""
    if isinstance(path, list):
        for p in path:
            check_path(p)
        return
    path = str(path)
    if path[0] != "/":
        raise ValueError(f"Path '{path}' does not start with /")
    if ".." in path:
        raise ValueError(f"Path '{path}' contains '..'")
    if exists and not Path(path).exists():
        raise ValueError(f"Path '{path}' does not exist")


async def await_variant(variant):
    """Recursively replaces every awaitable in the variant with its awaited value."""
    match variant:
        case Task():
            # We don't recurse through subtasks because they should await themselves.
            if inspect.isawaitable(variant.promise):
                variant.promise = await variant.promise
        case Config():
            await await_variant(variant.__dict__["_data"])
        case dict():
            for key in variant:
                variant[key] = await await_variant(variant[key])
        case list():
            for index, value in enumerate(variant):
                variant[index] = await await_variant(value)
        case _ if inspect.isawaitable(variant):
            variant = await variant
    return variant


def load(hancho_file=None, build_config=None, **kwargs):
    """Module loader entry point for .hancho files."""
    return app.load_module(hancho_file, build_config, kwargs)


class Chdir:
    """Copied from Python 3.11 contextlib.py"""

    def __init__(self, path):
        self.path = path
        self._old_cwd = []

    def __enter__(self):
        self._old_cwd.append(os.getcwd())
        os.chdir(self.path)

    def __exit__(self, *excinfo):
        os.chdir(self._old_cwd.pop())


class Config:
    """Config is a 'bag of fields' that behaves sort of like a Javascript object."""

    def __init__(self, **kwargs):
        self.__dict__["_base"] = kwargs.pop("base", None)
        self.__dict__["_data"] = kwargs

    def __getitem__(self, key):
        val = self.get(key)
        if val is None:
            raise KeyError(f"Config key '{key}' was never defined")
        return val

    def __setitem__(self, key, val):
        if val is None:
            raise ValueError(f"Config key '{key}' cannot be set to None")
        self.__dict__["_data"][key] = val

    def __delitem__(self, key):
        del self.__dict__["_data"][key]

    def __getattr__(self, key):
        return self.__getitem__(key)

    def __setattr__(self, key, val):
        self.__setitem__(key, val)

    def __delattr__(self, key):
        self.__delitem__(key)

    def __repr__(self):
        class Encoder(json.JSONEncoder):
            """Types the encoder doesn't understand just get stringified."""

            def default(self, o):
                if isinstance(o, Task):
                    return f"task {Expander(o.rule).desc}"
                return str(o)

        base = self.__dict__["_base"]
        data = self.__dict__["_data"]
        result1 = json.dumps(data, indent=2, cls=Encoder)
        return result1 if not base else result1 + ",\n" + "base: " + str(base)

    def get(self, key, default=None):
        base = self.__dict__["_base"]
        data = self.__dict__["_data"]
        if key in data:
            val = data[key]
            if val is not None:
                return val
        if base is not None:
            return base.get(key, default)
        if self is not global_config:
            return global_config.get(key, default)
        return default

    def set(self, **kwargs):
        self.__dict__["_data"].update(kwargs)

    def defaults(self, **kwargs):
        """Sets key-val pairs in this config if the key does not already exist."""
        for key, val in kwargs.items():
            if self.get(key) is None:
                self[key] = val

    def extend(self, **kwargs):
        """Returns a 'subclass' of this config blob that can override its fields."""
        return self.__class__(base=self, **kwargs)

    def rule(self, **kwargs):
        """Returns a callable rule that uses this config blob (plus any kwargs)."""
        return Rule(base=self, **kwargs)


class Rule(Config):
    """Rules are callable Configs that create a Task when called."""

    def __call__(self, source_files=None, build_files=None, **kwargs):
        return Task(
            rule=self, source_files=source_files, build_files=build_files, **kwargs
        )


# Expander requires some explanation.
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
# The result of this is that the functions in Expander are mutually recursive in a way that can
# lead to confusing callstacks, but that should handle every possible case of stuff inside other
# stuff.
#
# The 'depth' checks are to prevent recursive runaway - the MAX_EXPAND_DEPTH limit is arbitrary but
# should suffice.


class Expander:
    """Expander does template expasion on read so that eval() always sees expanded templates."""

    def __init__(self, config):
        assert isinstance(config, Config)
        self.config = config
        self.depth = 0

    def __getitem__(self, key):
        """Defining __getitem__ is required to use this expander as a mapping in eval()."""
        return self.expand(self.config[key])

    def expand(self, variant):
        """Expands all templates anywhere inside 'variant'."""
        match variant:
            case BaseException():
                raise variant
            case Task():
                return self.expand(variant.promise)
            case Path():
                return Path(self.expand(str(variant)))
            case list():
                return [self.expand(s) for s in variant]
            case str() if macro_regex.search(variant):
                return self.eval_macro(variant)
            case str() if template_regex.search(variant):
                return self.expand_template(variant)
            case int() | bool() | float() | str():
                return variant
            case _ if inspect.isfunction(variant):
                return variant
            case _:
                raise ValueError(
                    f"Don't know how to expand {type(variant)}='{variant}'"
                )

    def flatten(self, variant):
        """Turns 'variant' into a flat array of expanded variants."""
        match variant:
            case Task():
                return self.flatten(variant.promise)
            case list():
                return [x for element in variant for x in self.flatten(element)]
            case _:
                return [self.expand(variant)]

    def expand_template(self, template):
        """Replaces all macros in template with their stringified values."""
        old_template = template
        result = ""
        while span := template_regex.search(template):
            result += template[0 : span.start()]
            try:
                macro = template[span.start() : span.end()]
                variant = self.eval_macro(macro)
                result += " ".join([str(s) for s in self.flatten(variant)])
            except BaseException as exc:
                log(color(255, 255, 0))
                log(f"Expanding template '{old_template}' failed!")
                log(color())
                raise exc
            template = template[span.end() :]
        result += template
        return result

    def eval_macro(self, macro):
        """Evaluates the contents of a "{macro}" string."""
        if self.depth > MAX_EXPAND_DEPTH:
            raise RecursionError(f"Expanding '{template}' failed to terminate")
        self.depth += 1
        # pylint: disable=eval-used
        result = eval(macro[1:-1], {}, self)
        self.depth -= 1
        return result


class Task:
    """Calling a Rule creates a Task."""

    # pylint: disable=too-many-instance-attributes
    # pylint: disable=attribute-defined-outside-init

    def __init__(self, *, rule=None, **kwargs):
        app.tasks_total += 1
        if rule is None:
            self.rule = Rule(**kwargs)
        elif len(kwargs):
            self.rule = rule.extend(**kwargs)
        else:
            self.rule = rule
        self.promise = asyncio.create_task(self.run_async())

    def __repr__(self):
        class Encoder(json.JSONEncoder):
            """Types the encoder doesn't understand just get stringified."""

            def default(self, o):
                if isinstance(o, Config):
                    return "<config>"
                return str(o)

        base = json.dumps(self.__dict__, indent=2, cls=Encoder)
        config = str(self.rule)
        return "task: " + base + ",\nrule: " + config

    async def run_async(self):
        """Entry point for async task stuff, handles exceptions generated during task execution."""
        try:
            # Await everything awaitable in this task's rule.
            await await_variant(self.rule)

            # Everything awaited, task_init runs synchronously.
            self.task_init()

            # Run the commands if we need to.
            if self.reason:
                result = await self.run_commands()
                app.tasks_pass += 1
            else:
                result = self.abs_build_files
                app.tasks_skip += 1

            return result

        # If this task failed, we print the error and propagate a cancellation to downstream tasks.
        except Exception:  # pylint: disable=broad-except
            if not (global_config.quiet or self.rule.quiet):
                log(color(255, 128, 128))
                traceback.print_exception(*sys.exc_info())
                log(color())
            app.tasks_fail += 1
            return asyncio.CancelledError()

        # If any of this tasks's dependencies were cancelled, we propagate the cancellation to
        # downstream tasks.
        except asyncio.CancelledError as cancel:
            app.tasks_cancel += 1
            return cancel

        finally:
            if self.rule.debug:
                log("")

    def task_init(self):
        """All the setup steps needed before we run a task."""

        # Expand everything
        expander = Expander(self.rule)
        self.desc = expander.expand(self.rule.desc)
        self.command = expander.flatten(self.rule.command)
        self.command_files = expander.flatten(self.rule.get("command_files", []))
        self.command_path = expander.expand(self.rule.command_path)
        self.source_files = expander.flatten(self.rule.source_files)
        self.source_path = expander.expand(self.rule.source_path)
        self.build_files = expander.flatten(self.rule.build_files)
        self.build_deps = expander.flatten(self.rule.get("build_deps", []))
        self.build_path = expander.expand(self.rule.build_path)

        # Sanity-check expanded paths. It's OK if 'build_path' doesn't exist yet.
        check_path(self.source_path, exists=True)
        check_path(self.command_path, exists=True)
        check_path(self.build_path, exists=False)

        # Prepend expanded absolute paths to expanded filenames. If the filenames are already
        # absolute, this does nothing.
        self.abs_command_files = [self.command_path / f for f in self.command_files]
        self.abs_source_files = [self.source_path / f for f in self.source_files]
        self.abs_build_files = [self.build_path / f for f in self.build_files]
        self.abs_build_deps = [self.build_path / f for f in self.build_deps]

        # Sanity-check file paths.
        check_path(self.abs_command_files, exists=True)
        check_path(self.abs_source_files, exists=True)
        check_path(self.abs_build_files, exists=False)
        check_path(self.abs_build_deps, exists=False)

        # Check for duplicate task outputs
        for abs_file in self.abs_build_files:
            if abs_file in app.all_build_files:
                raise NameError(f"Multiple rules build {abs_file}!")
            app.all_build_files.add(abs_file)

        # Make sure our output directories exist
        if not self.rule.dry_run:
            for abs_file in self.abs_build_files:
                abs_file.parent.mkdir(parents=True, exist_ok=True)

        # Check if we need a rebuild
        self.reason = self.needs_rerun(self.rule.force)

    def needs_rerun(self, force=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""
        # Pylint really doesn't like this function, lol.
        # pylint: disable=too-many-return-statements
        # pylint: disable=too-many-branches

        if force:
            return f"Files {self.abs_build_files} forced to rebuild"
        if not self.abs_source_files:
            return "Always rebuild a target with no inputs"
        if not self.abs_build_files:
            return "Always rebuild a target with no outputs"

        # Check if any of our output files are missing.
        for abs_file in self.abs_build_files:
            if not abs_file.exists():
                return f"Rebuilding because {abs_file} is missing"

        # Check if any of our input files are newer than the output files.
        min_out = min(mtime(f) for f in self.abs_build_files)

        for abs_file in self.abs_source_files:
            if mtime(abs_file) >= min_out:
                return f"Rebuilding because {abs_file} has changed"

        for abs_file in self.abs_command_files:
            if mtime(abs_file) >= min_out:
                return f"Rebuilding because {abs_file} has changed"

        for mod in app.hancho_mods.values():
            if mtime(mod.__file__) >= min_out:
                return f"Rebuilding because {mod.__file__} has changed"

        # Check all dependencies in the depfile, if present.
        for abs_depfile in self.abs_build_deps:
            if not abs_depfile.exists():
                continue
            if self.rule.debug:
                log(f"Found depfile {abs_depfile}")
            with open(abs_depfile, encoding="utf-8") as depfile:
                deplines = None
                if self.rule.depformat == "msvc":
                    # MSVC /sourceDependencies json depfile
                    deplines = json.load(depfile)["Data"]["Includes"]
                elif self.rule.depformat == "gcc":
                    # GCC .d depfile
                    deplines = depfile.read().split()
                    deplines = [d for d in deplines[1:] if d != "\\"]
                else:
                    raise ValueError(f"Invalid depformat {self.rule.depformat}")

                # The contents of the depfile are RELATIVE TO THE WORKING DIRECTORY
                deplines = [self.command_path / d for d in deplines]
                for abs_file in deplines:
                    if mtime(abs_file) >= min_out:
                        return f"Rebuilding because {abs_file} has changed"

        # All checks passed; we don't need to rebuild this output.
        if self.rule.debug:
            log(f"Files {self.abs_build_files} are up to date")

        # Empty string = no reason to rebuild
        return ""

    async def run_commands(self):
        """Grabs a lock on the jobs needed to run this task's commands, then runs all of them."""

        try:
            # Wait for enough jobs to free up to run this task.
            await app.acquire_jobs(self.rule.job_count)

            # Jobs acquired, we are now runnable so grab a task index.
            app.task_counter += 1
            self.task_index = app.task_counter

            # Print the "[1/N] Compiling foo.cpp -> foo.o" status line and debug information
            log(
                f"{color(128,255,196)}[{self.task_index}/{app.tasks_total}]{color()} {self.desc}",
                sameline=not self.rule.verbose,
            )

            if self.rule.verbose or self.rule.debug:
                log(f"{color(128,128,128)}Reason: {self.reason}{color()}")

            if self.rule.debug:
                log(self)

            result = []
            for command in self.command:
                if self.rule.verbose or self.rule.debug:
                    command_path = relpath(self.command_path, self.rule.start_path)
                    log(f"{color(128,128,255)}{command_path}$ {color()}", end="")
                    log("(DRY RUN) " if self.rule.dry_run else "", end="")
                    log(command)
                result = await self.run_command(command)
        finally:
            await app.release_jobs(self.rule.job_count)

        # After the build, the deps files should exist if specified.
        for abs_file in self.abs_build_deps:
            if not abs_file.exists() and not self.rule.dry_run:
                raise NameError(f"Dep file {abs_file} wasn't created")

        # Check if the commands actually updated all the output files.
        # _Don't_ do this if this task represents a call to an external build system, as that
        # system might not actually write to the output files.
        if (
            self.source_files
            and self.build_files
            and not (self.rule.dry_run or self.rule.ext_build)
        ):
            if second_reason := self.needs_rerun():
                raise ValueError(
                    f"Task '{self.desc}' still needs rerun after running!\n"
                    + f"Reason: {second_reason}"
                )

        return result

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        # Early exit if this is just a dry run
        if self.rule.dry_run:
            return self.abs_build_files

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            result = command(self)
            if inspect.isawaitable(result):
                result = await(result)
            return result

        # Non-string non-callable commands are not valid
        if not isinstance(command, str):
            raise ValueError(f"Don't know what to do with {command}")

        # Create the subprocess via asyncio and then await the result.
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=self.command_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        (stdout_data, stderr_data) = await proc.communicate()

        self.stdout = stdout_data.decode()
        self.stderr = stderr_data.decode()
        self.returncode = proc.returncode

        # Print command output if needed
        if not self.rule.quiet and (self.stdout or self.stderr):
            if self.stderr:
                log(f"stderr: {self.stderr}", end="")
            if self.stdout:
                log(f"stdout: {self.stdout}", end="")

        # Task complete, check the task return code
        if self.returncode:
            raise ValueError(
                f"Command '{command}' exited with return code {self.returncode}"
            )

        # Task passed, return the output file list
        return self.abs_build_files


class App:
    """The application state. Mostly here so that the linter will stop complaining about my use of
    global variables. :D"""

    # pylint: disable=too-many-instance-attributes
    def __init__(self):
        self.hancho_mods = {}
        self.all_build_files = set()
        self.tasks_total = 0
        self.tasks_pass = 0
        self.tasks_fail = 0
        self.tasks_skip = 0
        self.tasks_cancel = 0
        self.task_counter = 0
        self.mtime_calls = 0
        self.line_dirty = False
        self.jobs_available = os.cpu_count()
        self.jobs_lock = asyncio.Condition()
        # The global config object. All fields here can be used in any template.
        # fmt: off
        self.global_config = Config(
            name="<Global Config>",

            # Config flags
            start_path=Path.cwd(),
            start_files="build.hancho",
            chdir=".",
            jobs=os.cpu_count(),
            verbose=False,
            quiet=False,
            dry_run=False,
            debug=False,
            force=False,

            # Rule defaults
            desc = "{source_files} -> {build_files}",
            job_count=1,
            depformat="gcc",
            ext_build=False,

            # Helper functions
            abspath=abspath,
            relpath=relpath,
            joinpath=joinpath,
            color=color,
            glob=glob,
            len=len,
            Path=Path,
            run_cmd=run_cmd,
            swap_ext=swap_ext,

            # Helper macros
            abs_source_files  = "{joinpath(source_path, source_files)}",
            abs_build_files   = "{joinpath(build_path, build_files)}",
            abs_command_files = "{joinpath(command_path, command_files)}",
            rel_source_files  = "{relpath(abs_source_files, command_path)}",
            rel_build_files   = "{relpath(abs_build_files, command_path)}",
            rel_command_files = "{relpath(abs_command_files, command_path)}",

            # Global config has no base.
            base=None,
        )
        # fmt: on

    def main(self):
        """Our main() just handles command line args and delegates to async_main()"""

        # pylint: disable=line-too-long
        # fmt: off
        parser = argparse.ArgumentParser()
        parser.add_argument("start_files",     default=["build.hancho"], type=str, nargs="*", help="The name of the .hancho file to build")
        parser.add_argument("-C", "--chdir",   default=".",              type=str,            help="Change directory before starting the build")
        parser.add_argument("-j", "--jobs",    default=os.cpu_count(),   type=int,            help="Run N jobs in parallel (default = cpu_count)")
        parser.add_argument("-v", "--verbose", default=False,            action="store_true", help="Print verbose build info")
        parser.add_argument("-q", "--quiet",   default=False,            action="store_true", help="Mute all output")
        parser.add_argument("-n", "--dry_run", default=False,            action="store_true", help="Do not run commands")
        parser.add_argument("-d", "--debug",   default=False,            action="store_true", help="Print debugging information")
        parser.add_argument("-f", "--force",   default=False,            action="store_true", help="Force rebuild of everything")
        # fmt: on

        # Parse the command line
        (flags, unrecognized) = parser.parse_known_args()

        # Merge all known command line flags into our global config object.
        # pylint: disable=global-statement
        # pylint: disable=attribute-defined-outside-init
        self.global_config.set(**flags.__dict__)

        # Unrecognized command line parameters also become global config fields if
        # they are flag-like
        for span in unrecognized:
            if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
                key = match.group(1)
                val = match.group(2)
                val = maybe_as_number(val) if val is not None else True
                global_config[key] = val

        # Change directory if needed and kick off the build.
        with Chdir(global_config.chdir):
            # For some reason "result = asyncio.run(self.async_main())" might be breaking actions
            # in Github, so I'm gonna try this. Seems to fix the issue.
            result = asyncio.get_event_loop().run_until_complete(self.async_main())

        return result

    async def async_main(self):
        """All the actual Hancho stuff runs in an async context so that clients can schedule their
        own async tasks as needed."""

        # Load the root .hancho files.
        for file in global_config.start_files:
            abs_file = global_config.start_path / file
            if not abs_file.exists():
                raise FileNotFoundError(f"Could not find {abs_file}")
            self.load_module(abs_file, None)

        # Root module(s) loaded. Run all tasks in the queue until we run out.
        self.jobs_available = global_config.jobs
        while True:
            pending_tasks = asyncio.all_tasks() - {asyncio.current_task()}
            if not pending_tasks:
                break
            await asyncio.wait(pending_tasks)

        # Done, print status info if needed
        if global_config.debug:
            log(f"tasks total:     {self.tasks_total}")
            log(f"tasks passed:    {self.tasks_pass}")
            log(f"tasks failed:    {self.tasks_fail}")
            log(f"tasks skipped:   {self.tasks_skip}")
            log(f"tasks cancelled: {self.tasks_cancel}")
            log(f"mtime calls:     {self.mtime_calls}")

        if self.tasks_fail:
            log(f"hancho: {color(255, 128, 128)}BUILD FAILED{color()}")
        elif self.tasks_pass:
            log(f"hancho: {color(128, 255, 128)}BUILD PASSED{color()}")
        else:
            log(f"hancho: {color(128, 128, 255)}BUILD CLEAN{color()}")

        return -1 if self.tasks_fail else 0

    def load_module(self, mod_filename, build_config=None, kwargs={}):
        """Loads a Hancho module ***while chdir'd into its directory***"""

        mod_path = abspath(mod_filename)
        if not mod_path.exists():
            raise FileNotFoundError(f"Could not load module {file}")

        # We dedupe module loads based on the physical path to the .hancho file and the contents
        # of the arguments passed to it.
        phys_path = Path(mod_path).resolve()
        module_key = f"{phys_path} : params {sorted(kwargs.items())}"
        if module_key in self.hancho_mods:
            return self.hancho_mods[module_key]

        with open(mod_path, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, mod_path, "exec", dont_inherit=True)

        module = type(sys)(mod_path.stem)
        module.__file__ = mod_path
        module.__builtins__ = builtins
        if build_config is not None:
            module.build_config = build_config.extend(**kwargs)
        else:
            module.build_config = Config(**kwargs)
        self.hancho_mods[module_key] = module

        # We must chdir()s into the .hancho file directory before running it so that
        # glob() can resolve files relative to the .hancho file itself. We are _not_ in an async
        # context here so there should be no other threads trying to change cwd.
        with Chdir(mod_path.parent):
            # Why Pylint thinks this is not callable is a mystery.
            # pylint: disable=not-callable
            types.FunctionType(code, module.__dict__)()

        return module

    async def acquire_jobs(self, count):
        """Waits until 'count' jobs are available and then removes them from the job pool."""

        if count > global_config.jobs:
            raise ValueError(f"Nedd {count} jobs, but pool is {global_config.jobs}.")

        await self.jobs_lock.acquire()
        await self.jobs_lock.wait_for(lambda: self.jobs_available >= count)
        self.jobs_available -= count
        self.jobs_lock.release()

    async def release_jobs(self, count):
        """Returns 'count' jobs back to the job pool."""

        # NOTE: The notify_all here is required because we don't know in advance which tasks will
        # be capable of running after we return jobs to the pool. HOWEVER, this also creates an
        # O(N^2) slowdown when we have a very large number of pending tasks (>1000) due to the
        # "Thundering Herd" problem - all tasks will wake up, only a few will acquire jobs, the
        # rest will go back to sleep again, this will repeat for every call to release_jobs().
        await self.jobs_lock.acquire()
        self.jobs_available += count
        self.jobs_lock.notify_all()
        self.jobs_lock.release()


if __name__ == "__main__":
    app = App()
    global_config = app.global_config
    sys.exit(app.main())
