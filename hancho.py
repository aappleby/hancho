#!/usr/bin/python3
# pylint: disable=too-many-lines

"""
Hancho is a simple, pleasant build system.

Hancho v0.0.5, 19-03-2024

- Special dir-related fields are now start_path, root_dir, leaf_dir, work_dir, and build_dir
- Hancho files in a submodule can be loaded via load(root="submodule/path", file="build.hancho)
- Each Hancho module now gets its own 'config' object extended from its parent module (or
  global_config). This prevents submodules from accidentally changing global fields that their
  parent modules use while still allowing sharing of configuration across files.
"""

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
from collections import UserDict

# If we were launched directly, a reference to this module is already in
# sys.modules[__name__]. Stash another reference in sys.modules["hancho"] so
# that build.hancho and descendants don't try to load a second copy of Hancho.
sys.modules["hancho"] = sys.modules[__name__]

# The maximum number of recursion levels we will do to expand a template
# Tests currently require MAX_EXPAND_DEPTH >= 6
MAX_EXPAND_DEPTH = 100

# Matches {} delimited regions inside a template string.
template_regex = re.compile("{[^}]*}")

single_template_regex = re.compile("^{[^}]*}$")

####################################################################################################


def log(message, *args, sameline=False, **kwargs):
    """Simple logger that can do same-line log messages like Ninja."""
    if global_config.quiet:
        return

    if not sys.stdout.isatty():
        sameline = False

    output = io.StringIO()
    if sameline:
        kwargs["end"] = ""
    print(message, *args, file=output, **kwargs)
    output = output.getvalue()

    if not sameline and app.line_dirty:
        sys.stdout.write("\n")
        app.line_dirty = False

    if not output:
        return

    if sameline:
        sys.stdout.write("\r")
        output = output[: os.get_terminal_size().columns - 1]
        sys.stdout.write(output)
        sys.stdout.write("\x1B[K")
    else:
        sys.stdout.write(output)

    sys.stdout.flush()
    app.line_dirty = output[-1] != "\n"


def abspath(path):
    """Pathlib's path.absolute() doesn't resolve "foo/../bar", so we use os.path.abspath."""
    if template_regex.search(str(path)):
        raise ValueError("Abspath can't operate on templated strings")
    # Hmm this acutally works now, am I forgetting a corner case?
    # return Path(path).absolute()
    return Path(os.path.abspath(path))


def relpath(path1, path2):
    return Path(str(path1).removeprefix(str(path2) + "/"))
    #"""Pathlib's path.relative_to() refuses to generate "../bar", so we use os.path.relpath."""
    #if template_regex.search(str(path1)) or template_regex.search(str(path2)):
    #    raise ValueError("Relpath can't operate on templated strings")
    ## This also works now, def need to check corner cases.
    ## if path2 is None: return path1
    ## return Path(path1).relative_to(path2)
    #return Path(os.path.relpath(path1, path2))


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
    if name is None:
        return None
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


####################################################################################################


async def await_variant(variant):
    """Recursively replaces every awaitable in the variant with its awaited value."""

    match variant:
        case Task():
            # We don't iterate through subtasks because they should await themselves except for
            # their own promise.
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


####################################################################################################


def load(hancho_file=None, **kwargs):
    """Module loader entry point for .hancho files."""
    return app.load_module(hancho_file, kwargs)


####################################################################################################


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


####################################################################################################


class Config:
    """Config is a 'bag of fields' that behaves sort of like a Javascript object."""

    def __init__(self, **kwargs):
        self.__dict__["_base"] = kwargs.pop("base", None)
        self.__dict__["_data"] = dict(kwargs)

    def __getitem__(self, key):
        base = self.__dict__["_base"]
        data = self.__dict__["_data"]
        if key in data:
            val = data[key]
            if val is Required:
                raise ValueError(f"Required config '{key}' was not defined")
            elif val is Optional:
                return None
            elif val is not None:
                return val
        if base is not None:
            return base[key]
        if self is not global_config:
            return global_config[key]
        raise ValueError(f"Config '{key}' was never defined")

    def __setitem__(self, key, val):
        self.__dict__["_data"][key] = val

    def __getattr__(self, key):
        return self.__getitem__(key)

    def __setattr__(self, key, val):
        self.__setitem__(key, val)

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

    def get(self, key, default):
        val = self.__getitem__(key)
        return val if val is not None else default

    def set(self, **kwargs):
        self.update(kwargs)

    def update(self, values):
        self.__dict__["_data"].update(values)

    def extend(self, **kwargs):
        """Returns a 'subclass' of this config blob that can override its fields."""
        return self.__class__(base=self, **kwargs)

    def rule(self, **kwargs):
        """Returns a 'subclass' of this config blob that can override its fields."""
        return Rule(base=self, **kwargs)


class Rule(Config):
    """Rules are callable Configs that create a Task when called."""

    def __call__(self, source_files=None, build_files=None, **kwargs):
        return Task(
            rule=self, source_files=source_files, build_files=build_files, **kwargs
        )


####################################################################################################

Required = {}
Optional = {}

####################################################################################################
# The next three functions require some explanation.
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
# The result of this is that the following three functions are mutually recursive in a way that can
# lead to confusing callstacks, but that should handle every possible case of stuff inside other
# stuff.
#
# The 'depth' checks are to prevent recursive runaway - 100 is an arbitrary limit but it should
# suffice.


class Expander:
    """Expander does template expasion on read so that eval() always sees expanded templates."""

    def __init__(self, config):
        assert isinstance(config, Config)
        self.__dict__["config"] = config
        self.__dict__["depth"] = 0

    def __getitem__(self, key):
        val = self.__dict__["config"][key]
        # FIXME need a match or something
        if isinstance(val, list):
            val = self.flatten(val)
        if isinstance(val, str):
            val = self.stringize(val)
        if isinstance(val, Path):
            val = Path(self.stringize(str(val)))
        return val

    def __getattr__(self, key):
        return self.__getitem__(key)

    def get(self, key, default=None):
        val = self.__getitem__(key)
        if val is None:
            val = default
        return val

    def flatten(self, variant):
        """Turns 'variant' into a flat array of non-templated strings, paths, and callbacks."""
        # pylint: disable=too-many-return-statements

        if self.__dict__["depth"] > MAX_EXPAND_DEPTH:
            raise ValueError(f"Flattening '{variant}' failed to terminate")

        match variant:
            case None:
                return []
            case asyncio.CancelledError():
                raise variant
            case Task():
                return self.flatten(variant.promise)
            case Path():
                return [Path(self.stringize(str(variant)))]
            case list():
                result = []
                for element in variant:
                    result.extend(self.flatten(element))
                return result
            case _ if inspect.isfunction(variant):
                return [variant]
            case _:
                return [self.stringize(variant)]

    def stringize(self, variant):
        """Turns 'variant' into a non-templated string."""
        # pylint: disable=too-many-return-statements

        match variant:
            case None:
                return ""
            case asyncio.CancelledError():
                raise variant
            case Task():
                return self.stringize(variant.promise)
            case Path():
                return self.stringize(str(variant))
            case list():
                variant = self.flatten(variant)
                variant = [str(s) for s in variant if s is not None]
                variant = " ".join(variant)
                return variant
            case str():
                if template_regex.search(variant):
                    # print(variant)
                    return self.stringize(self.expand(variant))
                return variant
            case _:
                return str(variant)

    def expand(self, template):
        """Expands all templates to produce a non-templated string."""

        if self.__dict__["depth"] > MAX_EXPAND_DEPTH:
            raise ValueError(f"Expanding '{template}' failed to terminate")

        if isinstance(template, Path):
            return Path(self.expand(str(template)))

        if isinstance(template, list):
            return [self.expand(t) for t in template]

        if not isinstance(template, str):
            # print(template)
            raise ValueError(f"Don't know how to expand {type(template)}")

        if single_template_regex.search(template):
            return self.expand(eval(template[1:-1], {}, self))

        # Evaluate the template contents.
        old_template = template
        try:
            self.__dict__["depth"] += 1
            result = ""

            while span := template_regex.search(template):
                result += template[0 : span.start()]
                exp = template[span.start() : span.end()]
                try:
                    # pylint: disable=eval-used
                    code = exp[1:-1]
                    replacement = eval(exp[1:-1], {}, self)
                    result += self.stringize(replacement)
                except Exception as exc:  # pylint: disable=broad-except
                    log(
                        f"{color(255,255,0)}Expanding template '{old_template}' failed!{color()}"
                    )
                    raise exc

                template = template[span.end() :]

            result += template
        finally:
            self.__dict__["depth"] -= 1

        return result


####################################################################################################


class Task:
    """Calling a Rule creates a Task."""

    # pylint: disable=too-many-instance-attributes
    # pylint: disable=attribute-defined-outside-init
    # pylint: disable=super-init-not-called

    def __init__(self, *, rule=None, **kwargs):
        app.tasks_total += 1

        self.desc = None
        self.reason = None
        self.task_index = None

        self.command = None
        self.command_files = None
        self.command_path = None
        self.command_stdout = None
        self.command_stderr = None

        self.source_files = None
        self.source_path = None

        self.build_files = None
        self.build_deps = None
        self.build_path = None

        self.abs_command_files = None
        self.abs_source_files = None
        self.abs_build_files = None
        self.abs_build_deps = None

        if rule is None:
            self.rule = Rule(**kwargs)
        elif len(kwargs):
            self.rule = rule.extend(**kwargs)
        else:
            self.rule = rule

        self.promise = None

        coroutine = self.run_async()
        self.promise = asyncio.create_task(coroutine)

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
        """Entry point for async task stuff, handles exceptions generated
        during task execution."""

        rule = self.rule

        try:
            # Await everything awaitable in this task's rule.
            await await_variant(rule)

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

        # If this task failed, we print the error and propagate a cancellation
        # to downstream tasks.
        except Exception:  # pylint: disable=broad-except
            if not self.rule.quiet:
                log(color(255, 128, 128))
                traceback.print_exception(*sys.exc_info())
                log(color())
            app.tasks_fail += 1
            return asyncio.CancelledError()

        # If any of this tasks's dependencies were cancelled, we propagate the
        # cancellation to downstream tasks.
        except asyncio.CancelledError as cancel:
            app.tasks_cancel += 1
            return cancel

        finally:
            if self.rule.debug:
                log("")

    # pylint: disable=too-many-branches
    def task_init(self):
        """All the setup steps needed before we run a task."""

        # Expand everything
        expander = Expander(self.rule)

        self.desc = expander.stringize(self.rule.desc)

        self.command = expander.flatten(self.rule.command)
        self.command_files = expander.flatten(self.rule.command_files)
        self.command_path = expander.expand(self.rule.command_path)

        self.source_files = expander.flatten(self.rule.source_files)
        self.source_path = expander.expand(self.rule.source_path)

        self.build_files = expander.flatten(self.rule.build_files)
        self.build_deps = expander.flatten(self.rule.build_deps)
        self.build_path = expander.expand(self.rule.build_path)

        # 'build_path' may not exist yet and that's OK, we will create it.
        check_path(self.source_path, exists = True)
        check_path(self.command_path, exists = True)
        check_path(self.build_path, exists = False)

        # Prepend directories to filenames and then normalize + absolute them.
        # If they're already absolute, this does nothing.
        self.abs_command_files = [self.command_path / f for f in self.command_files]
        self.abs_source_files = [self.source_path / f for f in self.source_files]
        self.abs_build_files = [self.build_path / f for f in self.build_files]
        self.abs_build_deps = [self.build_path / f for f in self.build_deps]

        check_path(self.abs_command_files, exists = True)
        check_path(self.abs_source_files, exists = True)
        check_path(self.abs_build_files, exists = False)

        # Check for duplicate task outputs
        for file in self.abs_build_files:
            if file in app.all_build_files:
                raise NameError(f"Multiple rules build {file}!")
            app.all_build_files.add(file)

        # Make sure our output directories exist
        if not self.rule.dry_run:
            for build_file in self.abs_build_files:
                #print(f"mkdir for {build_file}")
                build_file.parent.mkdir(parents=True, exist_ok=True)

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

        # Tasks with missing outputs always run.
        for file_out in self.abs_build_files:
            if not file_out.exists():
                return f"Rebuilding {self.abs_build_files} because some are missing"

        # Check if any task inputs are newer than any outputs.
        min_out = min(mtime(f) for f in self.abs_build_files)
        if (
            self.abs_source_files
            and max(mtime(f) for f in self.abs_source_files) >= min_out
        ):
            return f"Rebuilding {self.abs_build_files} because an input has changed"

        # Check if the hancho file(s) that generated the task have changed.
        if max(mtime(f.__file__) for f in app.hancho_mods.values()) >= min_out:
            return f"Rebuilding {self.abs_build_files} because its .hancho files have changed"

        # Check if any files the command needs have changed.
        if (
            self.abs_command_files
            and max(mtime(f) for f in self.abs_command_files) >= min_out
        ):
            return f"Rebuilding {self.abs_build_files} because a dependency has changed"

        # Check all dependencies in the depfile, if present.
        if self.build_deps:
            for file in self.abs_build_deps:
                abs_depfile = file
                check_path(abs_depfile)
                if abs_depfile.exists():
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
                        if deplines and max(mtime(f) for f in deplines) >= min_out:
                            return (
                                f"Rebuilding {self.abs_build_files} because a dependency in "
                                + f"{abs_depfile} has changed"
                            )

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

            # Deps fulfilled and jobs acquired, we are now runnable so grab a task index.
            app.task_counter += 1
            self.task_index = app.task_counter

            # Print the "[1/N] Foo foo.foo foo.o" status line and debug information
            log(
                f"{color(128,255,196)}[{self.task_index}/{app.tasks_total}]{color()} {self.desc}",
                sameline=not self.rule.verbose,
            )

            command_path = "."
            if self.command_path != self.rule.start_path:
                command_path = str(self.command_path).removeprefix(
                    str(self.rule.start_path) + "/"
                )
            dry_run = "(DRY RUN) " if self.rule.dry_run else ""

            if self.rule.verbose or self.rule.debug:
                log(f"{color(128,128,128)}Reason: {self.reason}{color()}")

            if self.rule.debug:
                log(self)

            result = []
            for command in self.command:
                if self.rule.verbose or self.rule.debug:
                    log(
                        f"{color(128,128,255)}{command_path}$ {color()}{dry_run}{command}"
                    )
                result = await self.run_command(command)
        finally:
            await app.release_jobs(self.rule.job_count)

        # After the build, the deps files should exist if specified.
        if not self.rule.dry_run:
            for file in self.abs_build_deps:
                if not file.exists():
                    raise NameError(f"Dep file wasn't created {file}")

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
            return command(self)

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
                log(self.stderr, end="")
            if self.stdout:
                log(self.stdout, end="")

        # Task complete, check the task return code
        if self.returncode:
            raise ValueError(
                f"Command '{command}' exited with return code {self.returncode}"
            )

        # Task passed, return the output file list
        return self.abs_build_files


####################################################################################################


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
        global global_config
        # pylint: disable=attribute-defined-outside-init
        global_config.update(flags.__dict__)

        # Unrecognized command line parameters also become global config fields if
        # they are flag-like
        for span in unrecognized:
            if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
                global_config[match.group(1)] = (
                    maybe_as_number(match.group(2))
                    if match.group(2) is not None
                    else True
                )

        # Change directory if needed and kick off the build.
        with Chdir(global_config.chdir):
            # For some reason "result = asyncio.run(self.async_main())" might be breaking actions
            # in Github, so I'm gonna try this. Seems to fix the issue.
            result = asyncio.get_event_loop().run_until_complete(self.async_main())

        return result

    async def async_main(self):
        """All the actual Hancho stuff runs in an async context so that clients can schedule their
        own async tasks as needed."""

        self.jobs_available = global_config.jobs

        # Load the root .hancho files.
        for file in global_config.start_files:
            file = global_config.start_path / file
            if not file.exists():
                raise FileNotFoundError(f"Could not find {file}")
            self.load_module(file)

        # Root module(s) loaded. Run all tasks in the queue until we run out.
        while True:
            pending_tasks = asyncio.all_tasks() - {asyncio.current_task()}
            if not pending_tasks:
                break
            await asyncio.wait(pending_tasks)

        # Done, print status info if needed
        if global_config.debug or global_config.verbose:
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

    def load_module(self, mod_filename, kwargs={}):
        """Loads a Hancho module ***while chdir'd into its directory***"""

        mod_path = abspath(mod_filename)
        if not mod_path.exists():
            raise FileNotFoundError(f"Could not load module {file}")

        phys_path = Path(mod_path).resolve()
        module_key = f"{phys_path} : params {sorted(kwargs.items())}"
        #print(f"Module key {module_key}")
        if module_key in self.hancho_mods:
            return self.hancho_mods[module_key]

        with open(mod_path, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, mod_path, "exec", dont_inherit=True)

        module = type(sys)(mod_path.stem)
        module.__file__ = mod_path
        module.__builtins__ = builtins
        module.build_params = kwargs
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
            raise ValueError(
                f"Tried to acquire {count} jobs, but we only have {global_config.jobs} in the pool."
            )

        await self.jobs_lock.acquire()
        await self.jobs_lock.wait_for(lambda: self.jobs_available >= count)
        self.jobs_available -= count
        self.jobs_lock.release()

    async def release_jobs(self, count):
        """Returns 'count' jobs back to the job pool."""

        await self.jobs_lock.acquire()
        self.jobs_available += count

        # NOTE: The notify_all here is required because we don't know in advance which tasks will
        # be capable of running after we return jobs to the pool. HOWEVER, this also creates an
        # O(N^2) slowdown when we have a very large number of pending tasks (>1000) due to the
        # "Thundering Herd" problem - all tasks will wake up, only a few will acquire jobs, the
        # rest will go back to sleep again, this will repeat for every call to release_jobs().
        self.jobs_lock.notify_all()
        self.jobs_lock.release()


####################################################################################################
# The global config object. All fields here can be used in any template.


def join(*args):
    if len(args) > 2:
        return join(args[0], join(*args[1:]))
    if isinstance(args[0], list):
        return [join(a, args[1]) for a in args[0]]
    if isinstance(args[1], list):
        return [join(args[0], a) for a in args[1]]
    return Path(args[0]) / Path(args[1])


def trim(path, prefix):
    if isinstance(path, list):
        return [trim(p, prefix) for p in path]
    path = str(path)
    prefix = str(prefix)
    if path == prefix:
        return Path("")
    result = path.removeprefix(prefix + "/")
    result = Path(result)
    return result


# fmt: off
global_config = Config(
    name="<Global Config>",
    start_path=Path.cwd(),
    start_files="build.hancho",

    desc          = "{source_files} -> {build_files}",
    source_files  = Required,
    source_path   = Required,
    command       = Required,
    command_files = Optional,
    command_path  = Required,
    build_files   = Required,
    build_path    = Required,
    build_deps    = Optional,

    job_count=1,
    depformat="gcc",
    chdir=".",
    jobs=os.cpu_count(),

    verbose=False,
    quiet=False,
    dry_run=False,
    debug=False,
    force=False,
    ext_build=False,

    abspath=abspath,
    relpath=relpath,
    color=color,
    glob=glob,
    len=len,
    Path=Path,
    run_cmd=run_cmd,
    swap_ext=swap_ext,
    join=join,
    trim=trim,

    base=None,
)
# fmt: on

####################################################################################################


app = App()

if __name__ == "__main__":
    sys.exit(app.main())
