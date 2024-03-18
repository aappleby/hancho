#!/usr/bin/python3

"""Hancho is a simple, pleasant build system."""

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

# The maximum number of recursion levels we will do to expand a template
MAX_EXPAND_DEPTH = 100

# Matches {} delimited regions inside a template string.
template_regex = re.compile("{[^}]*}")


def log(message, *args, sameline=False, **kwargs):
    """Simple logger that can do same-line log messages like Ninja."""
    if config.quiet:
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
    return Path(os.path.abspath(path))
    # Hmm this acutally works now, am I forgetting a corner case?
    # return Path(path).absolute()


def relpath(path1, path2):
    """Pathlib's path.relative_to() refuses to generate "../bar", so we use os.path.relpath."""
    return Path(os.path.relpath(path1, path2))
    # This also works now, def need to check corner cases.
    # if path2 is None: return path1
    # return Path(path1).relative_to(path2)


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


# The next three functions require some explanation.
#
# We do not necessarily know in advance how the users will nest strings, templates, callbacks,
# etcetera. So, when we need to produce a flat list of files from whatever was passed to files_in,
# we need to do a bunch of dynamic-dispatch-type stuff to ensure that we can always turn that thing
# into a flat list of files.
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


def flatten(variant, rule=None, depth=0):
    """Turns 'variant' into a flat array of non-templated strings, paths, and callbacks."""
    # pylint: disable=too-many-return-statements

    if depth > MAX_EXPAND_DEPTH:
        raise ValueError(f"Flattening '{variant}' failed to terminate")

    match variant:
        case None:
            return []
        case asyncio.CancelledError():
            raise variant
        case Task():
            return flatten(variant.promise, rule, depth + 1)
        case Path():
            return [Path(stringize(str(variant), rule, depth + 1))]
        case list():
            result = []
            for element in variant:
                result.extend(flatten(element, rule, depth + 1))
            return result
        case _ if inspect.isfunction(variant):
            return [variant]
        case _:
            return [stringize(variant, rule, depth + 1)]


def stringize(variant, rule=None, depth=0):
    """Turns 'variant' into a non-templated string."""
    # pylint: disable=too-many-return-statements

    if depth > MAX_EXPAND_DEPTH:
        raise ValueError(f"Stringizing '{variant}' failed to terminate")

    match variant:
        case None:
            return ""
        case asyncio.CancelledError():
            raise variant
        case Task():
            return stringize(variant.promise, rule, depth + 1)
        case Path():
            return stringize(str(variant), rule, depth + 1)
        case list():
            variant = flatten(variant, rule, depth + 1)
            variant = [str(s) for s in variant if s is not None]
            variant = " ".join(variant)
            return variant
        case str():
            if template_regex.search(variant):
                return expand(variant, rule, depth + 1)
            return variant
        case _:
            return str(variant)


def expand(template, rule=None, depth=0):
    """Expands all templates to produce a non-templated string."""

    if depth > MAX_EXPAND_DEPTH:
        raise ValueError(f"Expanding '{template}' failed to terminate")

    if not isinstance(template, str):
        raise ValueError(f"Don't know how to expand {type(template)}")

    if rule is None:
        rule = config

    result = ""
    while span := template_regex.search(template):
        result += template[0 : span.start()]
        exp = template[span.start() : span.end()]

        # Evaluate the template contents.
        replacement = ""
        try:
            # pylint: disable=eval-used
            replacement = eval(exp[1:-1], globals(), rule)
        except Exception as exc:  # pylint: disable=broad-except
            raise ValueError(f"Template '{exp}' failed to eval") from exc

        result += stringize(replacement, rule, depth + 1)
        template = template[span.end() :]

    result += template
    return result


async def await_variant(variant):
    """Recursively replaces every awaitable in the variant with its awaited value."""

    match variant:
        case Task():
            # We don't iterate through subtasks because they should await themselves except for
            # their own promise.
            if inspect.isawaitable(variant.promise):
                variant.promise = await variant.promise
        case dict():
            for key in variant:
                variant[key] = await await_variant(variant[key])
        case list():
            for index, value in enumerate(variant):
                variant[index] = await await_variant(value)
        case _ if inspect.isawaitable(variant):
            variant = await variant
    return variant


def load(file=None, root=None):
    """Module loader entry point for .hancho files. Searches the loaded Hancho module stack for a
    module whose directory contains 'mod_path', then loads the module relative to that path.
    """

    if file is None:
        raise FileNotFoundError("No .hancho filename given")

    file = Path(stringize(file, config))

    if root is not None:
        file = Path(stringize(root, config)) / file
    else:
        file = Path(app.mod_stack[-1].__file__).parent / file

    if not file.exists():
        raise FileNotFoundError(f"Could not load module {file}")

    return app.load_module(file, root)


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


class Config(dict):
    """Config is a 'bag of fields' that behaves sort of like a Javascript object."""

    def __init__(self, base=None, **kwargs):
        self.base = base
        self |= kwargs

    def __missing__(self, key):
        return None if self.base is None else self.base[key]

    def __setattr__(self, key, value):
        self.__setitem__(key, value)

    def __getattr__(self, key):
        return self.__getitem__(key)

    def __repr__(self):
        """Turns this config blob into a JSON doc for debugging."""

        class Encoder(json.JSONEncoder):
            """Types the encoder doesn't understand just get stringified."""

            def default(self, o):
                if isinstance(o, Path):
                    return f"Path {o}"
                return str(o)

        return json.dumps(self, indent=2, cls=Encoder)

    def extend(self, **kwargs):
        """Returns a 'subclass' of this config blob that can override its fields."""
        return type(self)(base=self, **kwargs)


class Rule(Config):
    """Rules are callable Configs that create a Task when called. Rules also delegate attribute
    lookups to the global 'config' object if they are missing a field."""

    # pylint: disable=attribute-defined-outside-init
    # pyglint: disable=too-many-instance-attributes

    def __init__(self, base=None, **kwargs):
        super().__init__(base, **kwargs)
        # pylint: disable=access-member-before-definition
        if self.rule_dir is None:
            self.rule_dir = Path(inspect.stack(context=0)[1].filename).parent

    def __missing__(self, key):
        """Rules delegate to config[key] if a key is missing."""

        result = super().__missing__(key)
        return result if result is not None else config[key]

    def __call__(self, files_in, files_out=None, **kwargs):
        task = Task(base=self, **kwargs)
        task.files_in = files_in
        if files_out is not None:
            task.files_out = files_out

        task.root_dir = app.root_stack[-1]
        task.call_dir = Path(inspect.stack(context=0)[1].filename).parent

        # A task that's created during task execution instead of module loading will have no mod
        # stack entry to pull load_dir from, so it just inherits its parent's cwd.
        #if "load_dir" not in kwargs:
        if self.load_dir is None:
            if app.mod_stack:
                task.load_dir = Path(app.mod_stack[-1].__file__).parent
            else:
                task.load_dir = Path.cwd()

        if task.job_count > config.jobs:
            raise ValueError("Task requires too many cores!")

        coroutine = task.run_async()
        task.promise = asyncio.create_task(coroutine)
        return task


class Task(Rule):
    """Calling a Rule creates a Task."""

    # pylint: disable=too-many-instance-attributes
    # pylint: disable=attribute-defined-outside-init

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        app.tasks_total += 1

    async def run_async(self):
        """Entry point for async task stuff, handles exceptions generated
        during task execution."""

        try:
            # Await everything awaitable in this task and replace the promises with the awaited
            # values.
            for key in self:
                if key != "promise":
                    self[key] = await await_variant(self[key])

            # Everything awaited, task_init runs synchronously.
            self.task_init()

            # Run the commands if we need to.
            if self.reason:
                result = await self.run_commands()
                app.tasks_pass += 1
            else:
                result = self.abs_files_out
                app.tasks_skip += 1

            return result

        # If this task failed, we print the error and propagate a cancellation
        # to downstream tasks.
        except Exception:  # pylint: disable=broad-except
            if not self.quiet:
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
            if self.debug:
                log("")

    # pylint: disable=too-many-branches
    def task_init(self):
        """All the setup steps needed before we run a task."""

        # Check for missing fields
        if self.command is None:  # pylint: disable=access-member-before-definition
            raise ValueError("Task missing command")
        if self.files_in is None:
            raise ValueError("Task missing files_in")
        if self.files_out is None:
            raise ValueError("Task missing files_out")

        # Stringize our directories
        self.work_dir = Path(stringize(self.work_dir, self))
        self.in_dir = Path(stringize(self.in_dir, self))
        self.deps_dir = Path(stringize(self.deps_dir, self))
        self.out_dir = Path(stringize(self.out_dir, self))

        assert self.work_dir.is_absolute() and self.work_dir.exists()
        assert self.in_dir.is_absolute() and self.in_dir.exists()
        assert self.deps_dir.is_absolute() and self.deps_dir.exists()

        # 'out_dir' may not exist yet and that's OK, we will create it.
        assert self.out_dir.is_absolute()

        # Flatten our file lists
        self.files_in = flatten(self.files_in, self)
        self.deps = flatten(self.deps, self)
        self.files_out = flatten(self.files_out, self)

        for key in self.named_deps:
            self.named_deps[key] = stringize(self.named_deps[key], self)

        # Prepend directories to filenames and then normalize + absolute them.
        # If they're already absolute, this does nothing.
        self.abs_files_in = [abspath(self.in_dir / f) for f in self.files_in]
        self.abs_deps = [abspath(self.deps_dir / f) for f in self.deps]
        self.abs_files_out = [abspath(self.out_dir / f) for f in self.files_out]

        self.abs_named_deps = {}
        for key in self.named_deps:
            self.abs_named_deps[key] = abspath(self.deps_dir / self.named_deps[key])

        # Strip the working directory off all our file paths to make our command lines less bulky.
        # Note that we _don't_ want relpath() here as it could add "../../.." that would go up
        # through a symlink to the wrong directory.
        def strip(f):
            work_dir_prefix = str(self.work_dir) + "/"
            return Path(str(f).removeprefix(work_dir_prefix))

        self.files_in = [strip(f) for f in self.abs_files_in]
        self.deps = [strip(f) for f in self.abs_deps]
        self.files_out = [strip(f) for f in self.abs_files_out]
        for key in self.named_deps:
            self.named_deps[key] = strip(self.abs_named_deps[key])

        # Now that files_in/files_out/deps are flat, we can expand our description and command
        # list.
        self.command = flatten(self.command, self)

        # pylint: disable=access-member-before-definition
        self.desc = stringize(self.desc, self)
        self.depfile = stringize(self.depfile, self)

        # Check for missing inputs
        if not self.dryrun:
            for file in self.abs_files_in:
                if not file.exists():
                    raise NameError(f"Input file doesn't exist - {file}")
            for file in self.abs_deps:
                if not file.exists():
                    raise NameError(f"Dependency doesn't exist - {file}")
            for kv in self.abs_named_deps.items():
                if not kv[1].exists():
                    raise NameError(f"Named dependency doesn't exist - {kv[0]}:{kv[1]}")

        # Check for duplicate task outputs
        for file in self.abs_files_out:
            if file in app.all_files_out:
                raise NameError(f"Multiple rules build {file}!")
            app.all_files_out.add(file)

        # Make sure our output directories exist
        if not self.dryrun:
            for file_out in self.abs_files_out:
                file_out.parent.mkdir(parents=True, exist_ok=True)

        # Check if we need a rebuild
        self.reason = self.needs_rerun(self.force)

    def needs_rerun(self, force=False):
        """Checks if a task needs to be re-run, and returns a non-empty reason if so."""

        # Pylint really doesn't like this function, lol.
        # pylint: disable=too-many-return-statements
        # pylint: disable=too-many-branches

        files_in = self.abs_files_in
        files_out = self.abs_files_out

        if force:
            return f"Files {self.files_out} forced to rebuild"
        if not files_in:
            return "Always rebuild a target with no inputs"
        if not files_out:
            return "Always rebuild a target with no outputs"

        # Tasks with missing outputs always run.
        for file_out in files_out:
            if not file_out.exists():
                return f"Rebuilding {self.files_out} because some are missing"

        # Check if any task inputs are newer than any outputs.
        min_out = min(mtime(f) for f in files_out)
        if files_in and max(mtime(f) for f in files_in) >= min_out:
            return f"Rebuilding {self.files_out} because an input has changed"

        # Check if the hancho file(s) that generated the task have changed.
        if max(mtime(f) for f in app.hancho_mods) >= min_out:
            return f"Rebuilding {self.files_out} because its .hancho files have changed"

        # Check if any user-specified deps have changed.
        if self.deps and max(mtime(f) for f in self.deps) >= min_out:
            return f"Rebuilding {self.files_out} because a dependency has changed"

        for key in self.named_deps:
            if mtime(self.named_deps[key]) >= min_out:
                return f"Rebuilding {self.files_out} because a named dependency has changed"

        # Check all dependencies in the depfile, if present.
        if self.depfile:
            abs_depfile = abspath(self.root_dir / self.depfile)
            if abs_depfile.exists():
                if self.debug:
                    log(f"Found depfile {abs_depfile}")
                with open(abs_depfile, encoding="utf-8") as depfile:
                    deplines = None
                    if self.depformat == "msvc":
                        # MSVC /sourceDependencies json depfile
                        deplines = json.load(depfile)["Data"]["Includes"]
                    elif self.depformat == "gcc":
                        # GCC .d depfile
                        deplines = depfile.read().split()
                        deplines = [d for d in deplines[1:] if d != "\\"]
                    else:
                        raise ValueError(f"Invalid depformat {self.depformat}")

                    # The contents of the depfile are RELATIVE TO THE WORKING DIRECTORY
                    deplines = [self.work_dir / Path(d) for d in deplines]
                    if deplines and max(mtime(f) for f in deplines) >= min_out:
                        return (
                            f"Rebuilding {self.files_out} because a dependency in "
                            + f"{abs_depfile} has changed"
                        )

        # All checks passed; we don't need to rebuild this output.
        if self.debug:
            log(f"Files {self.files_out} are up to date")

        return None

    async def run_commands(self):
        """Grabs a lock on the jobs needed to run this task's commands, then runs all of them."""

        try:
            # Wait for enough jobs to free up to run this task.
            await app.acquire_jobs(self.job_count)

            # Deps fulfilled and jobs acquired, we are now runnable so grab a task index.
            app.task_counter += 1
            self.task_index = app.task_counter

            # Print the "[1/N] Foo foo.foo foo.o" status line and debug information
            log(
                f"{color(128,255,196)}[{self.task_index}/{app.tasks_total}]{color()} {self.desc}",
                sameline=not self.verbose,
            )

            if self.work_dir == self.start_dir:
                work_dir = "."
            else:
                work_dir = str(self.work_dir).removeprefix(str(self.start_dir) + "/")
            dryrun = "(DRY RUN) " if self.dryrun else ""

            if self.verbose or self.debug:
                log(f"{color(128,128,128)}Reason: {self.reason}{color()}")

            if self.debug:
                log(self)

            result = []
            for command in self.command:
                if self.verbose or self.debug:
                    log(f"{color(128,128,255)}{work_dir}$ {color()}{dryrun}{command}")
                result = await self.run_command(command)
        finally:
            await app.release_jobs(self.job_count)

        # Check if the commands actually updated all the output files
        if self.files_in and self.files_out and not self.dryrun:
            if second_reason := self.needs_rerun():
                raise ValueError(
                    f"Task '{self.desc}' still needs rerun after running!\n"
                    + f"Reason: {second_reason}"
                )

        return result

    async def run_command(self, command):
        """Runs a single command, either by calling it or running it in a subprocess."""

        # Early exit if this is just a dry run
        if self.dryrun:
            return self.abs_files_out

        # Custom commands just get called and then early-out'ed.
        if callable(command):
            return command(self)

        # Non-string non-callable commands are not valid
        if not isinstance(command, str):
            raise ValueError(f"Don't know what to do with {command}")

        # Create the subprocess via asyncio and then await the result.
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=self.work_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        (stdout_data, stderr_data) = await proc.communicate()

        self.stdout = stdout_data.decode()
        self.stderr = stderr_data.decode()
        self.returncode = proc.returncode

        # Print command output if needed
        if not self.quiet and (self.stdout or self.stderr):
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
        return self.abs_files_out


class App:
    """The application state. Mostly here so that the linter will stop complaining about my use of
    global variables. :D"""

    # pylint: disable=too-many-instance-attributes
    def __init__(self):
        self.hancho_mods = {}
        self.mod_stack = []
        self.all_files_out = set()
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
        self.root_stack = [Path.cwd()]

    def main(self):
        """Our main() just handles command line args and delegates to async_main()"""

        # pylint: disable=line-too-long
        # fmt: off
        parser = argparse.ArgumentParser()
        parser.add_argument("filename",        default="build.hancho", type=str, nargs="?", help="The name of the .hancho file to build")
        parser.add_argument("-C", "--chdir",   default=".",            type=str,            help="Change directory before starting the build")
        parser.add_argument("-j", "--jobs",    default=os.cpu_count(), type=int,            help="Run N jobs in parallel (default = cpu_count)")
        parser.add_argument("-v", "--verbose", default=False,          action="store_true", help="Print verbose build info")
        parser.add_argument("-q", "--quiet",   default=False,          action="store_true", help="Mute all output")
        parser.add_argument("-n", "--dryrun",  default=False,          action="store_true", help="Do not run commands")
        parser.add_argument("-d", "--debug",   default=False,          action="store_true", help="Print debugging information")
        parser.add_argument("-f", "--force",   default=False,          action="store_true", help="Force rebuild of everything")
        # fmt: on

        # Parse the command line
        (flags, unrecognized) = parser.parse_known_args()

        # Merge all known command line flags into our global config object.
        global config  # pylint: disable=global-statement
        config |= flags.__dict__

        # Unrecognized command line parameters also become global config fields if
        # they are flag-like
        for span in unrecognized:
            if match := re.match(r"-+([^=\s]+)(?:=(\S+))?", span):
                config[match.group(1)] = (
                    maybe_as_number(match.group(2))
                    if match.group(2) is not None
                    else True
                )

        # Change directory if needed and kick off the build.
        with Chdir(config.chdir):
            # For some reason "result = asyncio.run(self.async_main())" might be breaking actions
            # in Github, so I'm gonna try this.
            result = asyncio.get_event_loop().run_until_complete(self.async_main())

        return result

    async def async_main(self):
        """All the actual Hancho stuff runs in an async context."""

        self.jobs_available = config.jobs

        # Load the root build.hancho file.
        root_filename = abspath(config.filename)
        if not root_filename.exists():
            raise FileNotFoundError(f"Could not find {root_filename}")
        self.load_module(root_filename, Path.cwd())

        # Root module(s) loaded. Run all tasks in the queue until we run out.
        while True:
            pending_tasks = asyncio.all_tasks() - {asyncio.current_task()}
            if not pending_tasks:
                break
            await asyncio.wait(pending_tasks)

        # Print a copy of the global config after all tasks are done if we're in
        # debug mode
        if config.debug:
            log(f"Hancho global config: {config}")
            log("")

        # Done, print status info if needed
        if config.debug or config.verbose:
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

    def load_module(self, abs_path, root=None):
        """Loads a Hancho module ***while chdir'd into its directory***"""

        phys_path = Path(abs_path).resolve()
        if phys_path in self.hancho_mods:
            return self.hancho_mods[phys_path]

        with open(abs_path, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, abs_path, "exec", dont_inherit=True)

        module = type(sys)(abs_path.stem)
        module.__file__ = abs_path
        module.__builtins__ = builtins
        self.hancho_mods[phys_path] = module

        # The directory the module is in gets added to the global path so we can
        # import .py modules in the same directory as it if needed. This may not
        # be necessary.
        sys.path.insert(0, str(abs_path.parent))

        root = self.root_stack[-1] if root is None else abspath(root)

        self.mod_stack.append(module)
        self.root_stack.append(root)

        # We must chdir()s into the .hancho file directory before running it so that
        # glob() can resolve files relative to the .hancho file itself. We are _not_ in an async
        # context here so there should be no other threads trying to change cwd.
        with Chdir(abs_path.parent):
            # Why Pylint thinks this is not callable is a mystery.
            # pylint: disable=not-callable
            types.FunctionType(code, module.__dict__)()

        self.mod_stack.pop()
        self.root_stack.pop()

        return module

    async def acquire_jobs(self, count):
        """Waits until 'count' jobs are available and then removes them from the job pool."""

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


class GlobalConfig(Config):
    """The global config object. All fields here can be used in any template."""

    # pylint: disable=too-many-instance-attributes
    # fmt: off
    def __init__(self):
        super().__init__()
        self.filename  = "build.hancho"

        self.desc      = "{files_in} -> {files_out}"
        self.chdir     = "."
        self.jobs      = os.cpu_count()
        self.verbose   = False
        self.quiet     = False
        self.dryrun    = False
        self.debug     = False
        self.force     = False
        self.depformat = "gcc"

        # The directory we started hancho.py from.
        self.start_dir  = Path.cwd()

        self.root_dir   = Path.cwd()

        # The working directory that we run commands in. For single projects it's the same as
        # start_dir, for stuff we're building from submodules it's the submodule's root directory.
        self.work_dir   = Path("{root_dir}")


        # Input filenames are resolved relative to in_dir.
        self.in_dir     = Path("{load_dir}")

        # Dependency filenames are resolved relative to deps_dir.
        self.deps_dir   = Path("{load_dir}")

        # All output files from all tasks go under build_dir.
        self.build_dir  = Path("build")

        # Use build_tag to split outputs into separate debug/profile/release/etc folders.
        self.build_tag  = ""

        # Each .hancho file gets a separate directory under build_dir for its output files.
        self.out_dir    = Path("{start_dir / build_dir / build_tag / relpath(load_dir, start_dir)}")

        self.files_out = []
        self.deps      = []
        self.named_deps   = {}

        # The default number of parallel jobs a task consumes.
        self.job_count = 1

        self.len       = len
        self.run_cmd   = run_cmd
        self.swap_ext  = swap_ext
        self.color     = color
        self.glob      = glob
        self.abspath   = abspath
        self.relpath   = relpath
        self.flatten   = flatten
        self.stringize = stringize
        self.expand    = expand
    # fmt: on


config = GlobalConfig()
app = App()

if __name__ == "__main__":
    sys.exit(app.main())
