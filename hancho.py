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

# FIXME do a template test with two nested dicts a.b and a.c where a.c contains a template referring to b.d

# FIXME param defaults should just be another layer in the onion

from __future__ import annotations

import argparse
import ast
import asyncio
import colorsys
import contextvars
import copy
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
import zlib  # for crc32, adler32
from collections import Counter, abc
from contextlib import chdir, contextmanager, suppress
from dataclasses import dataclass, replace
from enum import Enum
from functools import wraps
from typing import Any, cast


class Missing:
    def __repr__(self): return "<field missing>"
    def __bool__(self): return False

MISSING : Any = Missing()

#endregion

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
    """

    def __init__(self, *args, **kwargs):
        super().__init__()

        for i, arg in enumerate(args):
            if not isinstance(arg, abc.Mapping) and arg is not None:
                raise ValueError(f"Argument #{i} was not a dict - {arg}")

        self.merge(*args, kwargs)

    def merge(self, *args, **kwargs):
        for rhs in (*args, kwargs):
            if rhs is None:
                continue
            Dict.generic_merge(
                self, self, rhs,
                merge_dicts=True, merge_lists=True,
                keep_a=True, keep_b=True)
        return self

    # Fill-in-the-blank (or override what's there): Merges self and args into a new dict, keeping
    # only keys that were already in self. For example, if you have a config that contains
    # "out_bin" and you merge it with "compile_cpp", Hancho will complain that "out_bin" is missing
    # - it sees both "out_obj" and "out_bin" and assumes the task produces both. If you do
    # compile_cpp.fill(...), "out_bin" does not get added to compile_cpp.

    def fill(self, *args, **kwargs):
        dest = Dict(self)
        for rhs in (*args, kwargs):
            Dict.generic_merge(
                dest, dest, rhs,
                merge_dicts=True, merge_lists=True,
                keep_a=True, keep_b=False)
        return dest

    @classmethod
    def generic_merge(cls, dst, lhs, rhs, merge_dicts, merge_lists, keep_a, keep_b):
        keys = list(lhs) + [r for r in rhs if r not in lhs]

        for key in keys:
            if key in lhs and key not in rhs and not keep_a: continue
            if key not in lhs and key in rhs and not keep_b: continue

            lhs2 = lhs.get(key, None)
            rhs2 = rhs.get(key, None)

            if isinstance(lhs2, dict) and isinstance(rhs2, dict) and merge_dicts:
                dst2 = Dict()
                cls.generic_merge(dst2, lhs2, rhs2, merge_dicts, merge_lists, keep_a, keep_b)
                dst[key] = dst2
            elif isinstance(lhs2, list) and isinstance(rhs2, list) and merge_lists:
                dst[key] = list(lhs2) + list(rhs2)
            elif isinstance(rhs2, abc.Mapping):
                dst[key] = Dict(rhs2)
            elif isinstance(rhs2, (list, set, tuple)):
                dst[key] = lhs2 if rhs2 is None else copy.copy(rhs2)
            else:
                dst[key] = lhs2 if rhs2 is None else rhs2

        return dst

    def _walk(self, key):
        while True:
            key, _, rest = key.partition('.')
            if not rest:
                return (self, key)
            if key not in self:
                dict.__setitem__(self, key, Dict())
            self = dict.__getitem__(self, key)
            key = rest

    # ------------------------------------

    def _get(self, key):
        dest, key = self._walk(key)
        return dict.__getitem__(dest, key)

    def _set(self, key, val):
        dest, key = self._walk(key)
        dict.__setitem__(dest, key, val)

    def _del(self, key):
        dest, key = self._walk(key)
        return dict.__delitem__(dest, key)

    # ------------------------------------

    def __getitem__(self, key : str):
        return self._get(key)

    def __setitem__(self, key : str, val : Any):
        return self._set(key, val)

    def __delitem__(self, key : str):
        return self._del(key)

    # ------------------------------------

    def __getattr__(self, key : str):
        try:
            return self._get(key)
        except KeyError as err:
            raise AttributeError from err

    def __setattr__(self, key : str, val : Any):
        try:
            return self._set(key, val)
        except KeyError as err:
            raise AttributeError from err

    def __delattr__(self, key : str):
        try:
            return self._del(key)
        except KeyError as err:
            raise AttributeError from err

    # ------------------------------------

    def __or__(self, other):
        return Dict(self, other)

    def __repr__(self):
        return Dumper.dump(self)

    def expand(self, template):
        return Expander.expand(Onion(dict = self), template)

class Tool(Dict):
    # Tool is just an alias for Dict to make build scripts more readable.
    pass
















class HanchoConfig:
    def __init__(self, env):
        self.root       = env.hancho.root
        self.run_tool   = env.hancho.run_tool
        self.delims     = env.hancho.delims
        self.depformat  = env.hancho.depformat
        self.max_errors = env.hancho.max_errors
        self.max_jobs   = env.hancho.max_jobs

class LogConfig:
    def __init__(self, env):
        self.level      = env.log.level
        self.quiet      = env.log.quiet
        self.verbose    = env.log.verbose
        self.debug      = env.log.debug
        self.trace      = env.log.trace
        self.wrap       = env.log.wrap
        self.color      = env.log.color
        self.time       = env.log.time

class BuildConfig:
    def __init__(self, env):
        self.root       = env.build.root
        self.tag        = env.build.tag
        self.target     = env.build.target
        self.force      = env.build.force
        self.all        = env.build.all
        self.dry        = env.build.dry
        self.strict     = env.build.strict

class ScriptConfig:
    def __init__(self, env):
        self.path       = env.script.path
        self.root       = env.script.root

class TaskConfig:
    def __init__(self):
        self.name       = MISSING
        self.desc       = MISSING
        self.command    = MISSING
        self.cwd        = MISSING
        self.in_depfile = MISSING
        self.depformat  = MISSING
        self.job_size   = MISSING
        self.build_dir  = MISSING

    def fill(self, env):
        t = env.task

        self.name       = t.name
        self.desc       = t.desc
        self.command    = t.command
        self.cwd        = t.cwd
        self.in_depfile = t.in_depfile
        self.depformat  = t.depformat
        self.job_size   = t.job_size
        self.build_dir  = t.build_dir




hancho_defaults = Dict(
    hancho = Dict(
        root       = os.path.dirname(__file__),
        opt_file   = '',
        run_tool   = '',
        delims     = '{}«»',
        depformat  = "gcc" if os.name == "posix" else "msvc",
        max_errors = 0,
        max_jobs   = os.cpu_count() or 1
    ),
    repo = Dict(
        root = '{dirname(script.path)}'
    ),
    build = Dict(
        root   = "{join(repo.root, 'build')}",
        tag    = '',
        target = '',
        force  = False,
        all    = False,
        dry    = False,
        strict = True
    ),
    script = Dict(
        path = os.path.abspath("build.hancho"),
        root = '{dirname(script.path)}'
    ),
    log = Dict(
        level   = 50,
        quiet   = False,
        verbose = False,
        debug   = False,
        trace   = False,
        wrap    = False,
        color   = True,
        time    = True
    ),
    task = Dict(
        name       = '<no name>',
        desc       = '<no desc>',
        command    = '',
        cwd        = '{repo.root}',
        in_depfile = '',
        depformat  = 'gcc',
        job_size   = 1,
        build_dir  = '{abspath(join(build.root, build.tag, relpath(script.root, repo.root)))}'
    )
)















class Repo:
    def __init__(self, env):
        self.build_config = BuildConfig(env)
        self.repo_root = env.repo.root
        self.stat_db = Dict()
        self.build_reasons = Counter()
        self.root_script : Script = MISSING
        self.scripts = []
        Hancho.add_repo(self)

    def add_script(self, script : Script):
        if self.root_script is MISSING:
            self.root_script = script
        self.scripts.append(script)
        script.repo = self

    def yield_tasks(self):
        for script in self.scripts:
            yield from script.yield_tasks()

    def load_stat_db(self):
        stat_db_path = os.path.join(self.build_config.root, 'hancho.json')

        if os.path.isfile(stat_db_path):
            with open(stat_db_path) as contents:
                with Log.Level.VERBOSE, Log.Color.ORANGE:
                    Log.log(f"Loading stat_db {stat_db_path}\n")
                self.stat_db = Dict(json.load(contents))
        else:
            with Log.Level.VERBOSE, Log.Color.ORANGE:
                Log.log(f"No stat db for {self.repo_root}\n")
            self.stat_db = Dict()

    def save_stat_db(self):
        if self.build_config.dry:
            return

        stat_db = {}

        # FIXME we could probably save a little work if we didn't always re-stat every input and
        # output, but this is safe for now.

        # ------------------------------------
        # Gather stats for all input files in all tasks.

        for task in self.yield_tasks():
            if not task._complete:
                continue

            for file in Utils.yield_values(task.in_files):
                stat_db[file] = Utils.get_stats(file)

            if task.config.in_depfile:
                deplines = Utils.load_depfile(task.config.in_depfile, task.config.depformat, task.config.cwd)
                for file in deplines:
                    stat_db[file] = self.get_stats(file) # type: ignore

        # We gather stats from output files in a second pass so that their .command fields
        # overwrite any blank ones from the first pass.

        for task in self.yield_tasks():
            if not task._complete:
                continue

            for file in Utils.yield_values(task.out_files):
                stat_db[file] = Utils.get_stats(file, task.config.command)

        stat_db_path = Path.join(self.build_config.root, 'hancho.json')
        Utils.save_json(stat_db, stat_db_path)

        # ------------------------------------
        # And do the same for compile_commands.json with a slightly different format.

        comp_db = {}

        for task in self.yield_tasks():
            if not task._complete:
                continue

            for file in Utils.yield_values(task.in_files):
                # Haven't tested this in an IDE, but I think it matches the spec.
                comp_db[file] = {
                    "directory" : task.config.cwd,
                    "command"   : Utils.commands_to_string(task.config.command),
                    "file"      : file,
                }

        comp_db_path = Path.join(self.build_config.root, 'compile_commands.json')
        Utils.save_json(list(comp_db.values()), comp_db_path)

    def rebuild_reason(self, task) -> str:
        """
        Figures out why we have to run a Task, or returns "" if we don't.
        """

        # ------------------------------------
        # Check the trivial reasons to rebuild

        if task.config.build_force:
            self.build_reasons["forced"] += 1
            return "Target forced to rebuild"

        has_input = any(Utils.yield_values(task.in_files))
        if not has_input:
            self.build_reasons["no inputs"] += 1
            return "Always rebuild a target with no inputs"

        has_output = any(Utils.yield_values(task.out_files))
        if not has_output:
            self.build_reasons["no outputs"] += 1
            return "Always rebuild a target with no outputs"

        # ------------------------------------

        for filename in Utils.yield_values(task.in_files):
            if reason := self.check_stat(filename):
                return reason

        for filename in task._old_deplines:
            if reason := self.check_stat(filename):
                return reason

        for filename in Utils.yield_values(task.out_files):
            if reason := self.check_stat(filename, task.config.command):
                return reason

        self.build_reasons["*task clean"] += 1
        return ""

    def check_stat(self, filename, command = None):
        if not Path.exists(filename):
            self.build_reasons["file missing"] += 1
            return f"File missing: {filename}"

        if filename not in self.stat_db:
            self.build_reasons["stat missing"] += 1
            return f"Stat missing: {filename}"

        old_stat = self.stat_db[filename]
        new_stat = Utils.get_stats(filename, command)

        if old_stat.st_mtime_ns != new_stat.st_mtime_ns:
            self.build_reasons["mtime mismatch"] += 1
            return f"Mtime mismatch {old_stat.st_mtime_ns} != {new_stat.st_mtime_ns} for : {filename}"

        if old_stat.st_size != new_stat.st_size:
            self.build_reasons["size mismatch"] += 1
            return f"Size mismatch {old_stat.st_size} != {new_stat.st_size} for : {filename}"

        if old_stat.hash != new_stat.hash:
            self.build_reasons["hash mismatch"] += 1
            return f"Hash mismatch {old_stat.hash} -> {new_stat.hash} for : {filename}"

        if command is not None and old_stat.command != new_stat.command:
            self.build_reasons["command changed"] += 1
            return f"Command used to generate file has changed : {filename!r} : {old_stat.command!r} : {new_stat.command!r}"

        # Does not need to rebuild based on file stats / hash
        self.build_reasons["*hash match"] += 1
        return ""

    def update_stat(self, filename, command = None):
        pass


























class Script:

    class Abort(Exception): pass
    class EarlyOut(Exception): pass
    class Fail(Exception): pass

    def __init__(self, *, script_env : Onion, script_code : types.CodeType | None):
        #self.script_params = script_params
        self.script_env    = script_env
        self.script_code   = script_code

        self.script_config = ScriptConfig(self.script_env)
        self.script_env.layers["script_config"] = Dict(script = self.script_config)

        self.script_module           = types.ModuleType(os.path.basename(self.script_config.path)) # type: ignore
        self.script_module.__file__  = self.script_config.path
        self.script_module.hancho    = HanchoProxy("hancho")   # type: ignore
        self.script_module.env       = self.script_env    # type: ignore

        # If we put the entire module in the env, we end up with reference loops that break stuff.
        #self.script_env.layers["script_module"] = self.script_module.__dict__

        self.repo = MISSING
        self.parent_script = MISSING
        self.tasks : list[Task] =  []
        self.child_scripts : list[Script] = []
        Hancho.add_script(self)


    def add_child(self, script : Script):
        self.child_scripts.append(script)
        script.parent_script = self

    def is_repo(self):
        return self.repo.root_script is self

    def exec(self):
        if self.script_code:
            with Hancho.cv_script.enter(self), chdir(self.script_config.root):  # type: ignore
                exec(self.script_code, self.script_module.__dict__)

    def __repr__(self):
        return Dumper.dump(self)

    def yield_tasks(self):
        yield from self.tasks
        for script in self.child_scripts:
            yield from script.yield_tasks()

    @staticmethod
    def log_script_error(frame, condition, message):
        with Log.Level.ERROR, Log.Color.RED:
            Log.log(f"Script {condition}:\n")
            Log.log(f"  text = '{message}'\n")
            Log.log(f"  file = {frame.f_code.co_filename}\n")
            Log.log(f"  func = {frame.f_code.co_name}\n")
            Log.log(f"  line = {frame.f_lineno}\n")

    @staticmethod
    def fail(message):
        Script.log_script_error(sys._getframe(1), "failed", message)
        raise Script.Fail()

    @staticmethod
    def abort(message):
        Script.log_script_error(sys._getframe(1), "aborted", message)
        raise Script.Abort()

    @staticmethod
    def earlyout(message = ""):
        Script.log_script_error(sys._getframe(1), "exited early", message)
        raise Script.EarlyOut()















class ContextProxy:
    # Helper that just wraps CVs so you can do "contextvar.foo".
    def __init__(self, default):
        object.__setattr__(self, "_cv", contextvars.ContextVar("script", default = default))

    def __getattr__(self, key):
        return getattr(self._cv.get(), key)

    def __setattr__(self, key, val):
        setattr(self._cv.get(), key, val)

    def get(self) -> Script:
        return self._cv.get()

    def set(self, new_ctx : Script):
        return self._cv.set(new_ctx)

    @contextmanager
    def enter(self, new_ctx : Script):
        token = self._cv.set(new_ctx)
        yield
        self._cv.reset(token)














class Onion(abc.Mapping):
    """
    An Onion is like a ChainMap, except that it behaves as if you'd merged all the ChainMap maps
    together. This matters when you have something like
    c = ChainMap(
        dict(foo = dict(a = 1)),
        dict(foo = dict(b = 2))
    )
    because if you try to read both c['foo']['a'] and c['foo']['b'] it won't work - 'foo' always
    resolves to the first dict and never sees the second.

    Onion fixes this by looking up the key in all 'layers' and returns a value only if it was the
    first non-Mapping match. If there are multiple Mapping matches, they form a new Onion.

    Onion layers are searched in right-to-left (i.e. reverse) order, to match the "right overrides
    left" behavior of Dict. The full lookup rules are:

    - If the rightmost non-None match is a value, we expand it and return it.
    - If the rightmost non-None match is a mapping, we return a new Onion from all matches that are mappings.
    - If all matches are None, we return None.
    - If there were no matches but there is a default, we expand and return it.
    - If there were no matches and no default, we raise KeyError.

    Why the name 'Onion'? Well, 'stack' and 'deck' are overloaded and 'Onion' at least implies
    nested layers.
    """

    def __init__(self, *args, **kwargs):

        self.parent = kwargs.pop("parent", None)
        self.layers : Dict = Dict()

        for val in args:
            if isinstance(val, Onion):
                self.layers.update(val.layers)
            elif val is not None:
                raise TypeError("You need to pass all non-Onion args as Onion(name=mapping, ...)")

        for k, v in kwargs.items():
            assert isinstance(v, Dict)
            self.layers[k] = v

    def __getattr__(self, key : str):
        try:
            return self.get(key)
        except KeyError as err:
            raise AttributeError from err

#    def __setattr__(self, key : str, val : Dict):
#        self.set(key, val)

    def __getitem__(self, key):
        return self.get(key)

#    def __setitem__(self, key, val):
#        self.set(key, val)

    def __iter__(self):
        seen = set()
        for _, layer in reversed(self.layers.items()):
            for key in layer:
                if key not in seen:
                    seen.add(key)
                    yield key

    def __len__(self):
        result = {key for _, layer in self.layers.items() for key in layer}
        return len(result)

    def __repr__(self):
        return Dumper.dump(self)

    def __contains__(self, key):
        return any(key in layer for _, layer in self.layers.items())

    def __dump__(self, key, opts, seen):
        #trimmed_layers = dict(self.layers)
        #if hasattr(trimmed_layers, "hancho"):
        #    delattr(trimmed_layers, "hancho")
        #return Dumper._dump_vector(key, self, trimmed_layers, opts, seen)
        #return Dumper.dump(self)
        return Dumper._dump_vector(key, self, self.layers, opts, seen)

    def get(self, key, default : Any = MISSING) -> Any:
        # Allow direct access to layers
        layers = object.__getattribute__(self, "layers")
        if key in layers:
            return layers[key]

        # Otherwise walk the key path until we get a match.
        while True:
            key, _, rest = key.partition('.')
            result = self._get(key, default)
            if not rest:
                return result
            assert isinstance(result, Onion)
            self = result
            key = rest

    def set(self, key, val):
        assert '.' not in key
        self.layers[key] = val

    def _get(self, key, default : Any = MISSING) -> Any:
        """
        Searches through layers in reverse order (because we obey rightmost-not-None wins) for a
        key match. If we find it, we expand it before returning it. If we only found Mappings, we
        return a new Onion containing those mappings.

        """

        with Tracer(self, "get", key) as trace:

            def on_result(result):
                result = Expander.expand(self, result)
                trace.save_result(result)
                return result

            matches = []
            saw_none = False

            for name, layer in reversed(self.layers.items()):
                if key not in layer:
                    continue

                key2 = f"{name}->{key}"
                val2 = layer[key]

                if isinstance(val2, abc.Mapping):
                    matches.append((key2, val2))
                elif matches:
                    pass
                elif val2 is not None:
                    return on_result(val2)
                else:
                    saw_none = True

            if not matches:
                if saw_none:
                    return on_result(None)
                elif default is MISSING:
                    raise KeyError(key)
                else:
                    return on_result(default)

            result = Onion(self, **dict(reversed(matches)))
            trace.save_result(result)
            return result


    def raw_get(self, key, default : Any = MISSING) -> Any:
        """
        A simpler getter equivalent to ChainMap.get - doesn't expand the result.
        """
        for _, layer in reversed(self.layers.items()):
            if key in layer:
                return layer[key]

        if default is MISSING:
            raise KeyError(key)

        return default

    def expand(self, variant : Any):
        return Expander.expand(self, variant)

    def flat(self):
        result = Dict()
        for _name, layer in self.layers.items():
            result.merge(layer)
        return result
















class Expander:
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
    # not expandable by that dict. This allows nested dicts to contain templates that can only be
    # expanded an outer dict, and things will still Just Work.

    # Trivial classes just so we can distinguish between literal strings and macro strings without
    # having to do regex stuff every time.
    class Literal(str): pass
    class Macro(str):   pass
    class Expr(str):    pass

    # Delims: Normally you'd use '{' and '}' as macro delimiters, but you can also use '«' and '»'.
    # On Linux, you can type those using control-shift-u a b <enter> and control-shift-u b b <enter>
    # On Windows, use alt-0171 and alt-0187 with the numbers being typed on the numpad while numlock
    # is on.

    ldelims : str = "{«"
    rdelims : str = "}»"

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

    @classmethod
    def reset(cls, delims):
        Expander.delims  = delims
        Expander.ldelims = delims[::2]
        Expander.rdelims = delims[1::2]

    @classmethod
    def expand(cls, env : Onion, variant : Any, ldelims : str | None = None, rdelims : str | None = None):
        if not ldelims or not rdelims:
            delims = env.raw_get("delims", None)
            if delims:
                ldelims = delims[::2]
                rdelims = delims[1::2]
            else:
                ldelims = ldelims or Expander.ldelims
                rdelims = rdelims or Expander.rdelims

        if variant is MISSING:
            raise AssertionError("Tried to expand a sentinel value")
        elif isinstance(variant, str):
            # We have to catch this case before the 'is collection' below because strings _are_
            # collections, alas.
            pass
        elif isinstance(variant, abc.Mapping):
            return {k: Expander.expand(env, v, ldelims, rdelims) for k, v in variant.items()}
        elif isinstance(variant, abc.Collection):
            return [Expander.expand(env, v, ldelims, rdelims) for v in variant]
        elif not isinstance(variant, str):
            return variant

        # If old_depth = 0, then this is the start of a new expand().
        new_expand = Expander.cv_depth.get() == 0
        try:
            result = cls._expand_text(env, variant, ldelims, rdelims)
            return result
        finally:
            # And when that expand() is done, we reset the eval budget.
            if new_expand:
                Expander.cv_evals.set(0)

    @classmethod
    def _expand_text(cls, env : Onion, text : str, ldelims, rdelims) -> Any:
        old_text = ""
        blocks : list[str] = []

        result = None

        while old_text != text:

            blocks.clear()
            if not Expander._split_text(text, blocks, ldelims, rdelims):
                result = text
                return result

            if len(blocks) == 1 and isinstance(blocks[0], Expander.Macro):
                return Expander._eval_macro(env, blocks[0])

            # ----------

            trace = Tracer(env, "expand", text)
            trace.__enter__()

            old_text = text
            text = ""

            for block in blocks:
                if isinstance(block, Expander.Macro):
                    block = Expander._eval_macro(env, block)
                    block = Utils.stringify(block)
                text += block

            trace.save_result(text)
            trace.__exit__()


        result = text
        return result


    @classmethod
    def _eval_macro(cls, env : Onion, macro : Expander.Macro):

        # Bail out if we've done too many evals already.
        old_evals = Expander.cv_evals.get()
        if old_evals >= Expander.MAX_EVALS:
            raise RecursionError(f"Expansion failed to terminate after {old_evals} evals: '{macro!r}'")

        # Bail out if we've recursed through eval() too many times.
        old_depth = Expander.cv_depth.get()
        if old_depth >= Expander.MAX_DEPTH:
            raise RecursionError(f"Expansion failed to terminate after {old_depth} recursions: {macro!r}")

        # Note that we do _not_ suppress any BaseExceptions - they _must_ be propagated up to
        # callers. As of Python 3.11, this includes asyncio.CancelledError.

        result = None
        trace = Tracer(env, "eval", macro)

        try:
            trace.__enter__()
            Expander.cv_evals.set(old_evals + 1)
            Expander.cv_depth.set(old_depth + 1)
            result = eval(macro[1:-1], {}, env)
            trace.save_result(result)
        except RecursionError:
            raise
        except Exception as _:
            # IMPORTANT IMPORTANT IMPORTANT
            # If you can't eval a macro, you return it unchanged.
            # TEFINAE : Template Expansion Failure Is Not An Error. Same idea as SFINAE in C++
            # - we don't fail on expansion failure so we can retry somewhere/somewhen else.
            result = macro
            trace.save_result(result)
        finally:
            trace.__exit__()
            Expander.cv_depth.set(old_depth)

        return result


    @classmethod
    def _split_text(cls, text : str, out : list[str], ldelims, rdelims) -> int:
        """
        Extracts all innermost delimited spans from a block of text and produces a list of string
        literals and macros. Note that we're not handling "escaped" delimiters, instead we allow
        the user to change the delimiter when required (default delimiters are {} and «»)
        """

        rdelim = None
        cursor = 0
        idelim = -1
        macros = 0

        for i, c in enumerate(text):
            if (pos := ldelims.find(c)) != -1:
                idelim = i
                rdelim = rdelims[pos]
            elif c == rdelim and idelim >= 0:
                if cursor < idelim:
                    out.append(Expander.Literal(text[cursor:idelim]))
                out.append(Expander.Macro(text[idelim:i+1]))
                macros += 1
                cursor = i + 1
                idelim = -1
                rdelim = None

        if cursor < len(text):
            out.append(Expander.Literal(text[cursor:]))

        return macros

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
        types.BuiltinFunctionType : "<builtin>",
        #types.ModuleType          : "<module>",
        types.GeneratorType       : "<generator>",
    }

    # These types don't need a type annotation when dumped.
    base_types = (
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
        bytes,
        bytearray,
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
        print_id=True,
        color_code=False,
        width=80,
        len=0,
        tab="    ",
    ):
        opts = Dumper.Opts(depth, indent, print_id, color_code, tab, len, width, flat = False)
        #return cls._dump_to_str(key, val, opts, set())
        return cls._dump_to_str(None, val, opts, set())

    @classmethod
    def print(cls, *args, **kwargs):
        result = cls.dump(*args, **kwargs)
        print(result)

    @classmethod
    def _dump_to_str(cls, key, val : Any, opts, seen : set):
        if key == "__builtins__":
            return cls._dump_prefix(key, val, opts) + "<builtins>"
        elif hasattr(type(val), "__dump__"):
            return val.__dump__(key, opts, seen)
        elif inspect.isroutine(val) or inspect.isclass(val) or inspect.ismodule(val):  # noqa: SIM114
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

        # This slightly odd construct is so that when a deeply nested container doesn't fit on a
        # line, we rewind the callstack back to the topmost container that was not forced to be
        # flat.

        if opts.flat:
            return prefix + cls._dump_flat_vector(key, ld, items, rd, replace(opts, flat = True), set(seen))
        else:
            try:
                return prefix + cls._dump_flat_vector(key, ld, items, rd, replace(opts, flat = True), set(seen))
            except Dumper.LineTooLong:
                return prefix + cls._dump_deep_vector(key, ld, items, rd, opts, set(seen))


    @classmethod
    def _dump_flat_vector(cls, key, ld, items, rd, opts, seen : set):
        result = ld

        for i in range(len(items)):
            result += cls._dump_to_str(items[i][0], items[i][1], opts, set(seen))
            if i < len(items) - 1: result += ", "
            if opts.len + len(result) + len(rd) > opts.width:
                raise Dumper.LineTooLong()

        return result + rd

    @classmethod
    def _dump_deep_vector(cls, key, ld, items, rd, opts, seen : set):
        result = ld + '\n'

        if opts.depth == 0:
            return ld + "..." + rd
        opts = replace(opts, depth = opts.depth - 1)

        # len(pad) + 1 for the trailing comma
        pad = opts.tab * (opts.indent + 1)
        new_opts = replace(opts, len = len(pad) + 1, indent = opts.indent + 1)

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
        temp = Utils.hash(id(obj), 0)
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
        #return f"0x{id(obj):016x}"

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
            if isinstance(obj, dict):
                return type(obj)((k, wrapper(v, *args, **kwargs)) for k, v in obj.items())
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
        elif isinstance(key, dict):
            for k, v, in sorted(key.items()):
                h = cls.hash(k, h)
                h = cls.hash(v, h)
        elif isinstance(key, (list, tuple, set)):
            for k in key:
                h = cls.hash(k, h)
        elif key is None:
            h = join(*mix(*split(h)))
        else:
            raise TypeError(f"Don't know how to hash a {type(key)} = {key}")
        return h

    @classmethod
    def hash_file(cls, abs_path, h = 0):
        Utils.hash_calls += 1
        time_a = time.perf_counter()
        with open(abs_path, "rb") as f:
            blob = f.read()
            Utils.hash_bytes += len(blob)
        result = cls.hash(blob, h)
        time_b = time.perf_counter()
        Utils.hash_time += time_b - time_a
        return result

    @classmethod
    def get_stats(cls, file : str, command = None):
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













class Log:

    class Color(int, Enum):
        """12 half-saturated, 80% value Log.Color evenly spaced around the HSV wheel"""

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

    class Level(int, Enum):
        FATAL    = 0  # Fatal always beats quiet
        QUIET    = 10
        CRITICAL = 20 # Should critical/error beat quiet?
        ERROR    = 30
        WARNING  = 40
        NORMAL   = 50
        VERBOSE  = 60
        DEBUG    = 70

        def __enter__(self):
            self.old_log_level = Log.log_level_in
            Log.log_level_in = self
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            Log.log_level_in = self.old_log_level
            return False

        def __bool__(self):
            return self.value <= Log.log_level_out

    config : LogConfig = MISSING
    con_w         = 80
    time_origin   = time.perf_counter()
    indent_stack  = []
    current_color = -1
    line_buffer   = ""
    match_escapes = re.compile(r"(\x1B.*?m)")
    log_level_in  = Level.NORMAL
    log_level_out = Level.NORMAL # log level we want to appear in the log

    @classmethod
    def reset(cls, config : LogConfig):
        cls.config        = config
        cls.con_w         = shutil.get_terminal_size().columns
        cls.time_origin   = time.perf_counter()
        cls.indent_stack  = []
        cls.current_color = -1
        cls.line_buffer   = ""
        cls.match_escapes = re.compile(r"(\x1B.*?m)")

        if cls.config.level is not None:
            if isinstance(cls.config.level, str):
                cls.log_level = Log.Level[cls.config.level.upper()]
            elif isinstance(cls.config.level, int):
                cls.log_level = Log.Level(cls.config.level)
            else:
                raise ValueError(f"Got an unknown log level '{type(cls.config.level)} = {cls.config.level}'")

        # The individual -T/-D/-V/-Q flags override --log_level, with the 'loudest' flag winning.

        if cls.config.debug:
            cls.config.level = Log.Level.DEBUG
        elif cls.config.verbose:
            cls.config.level = Log.Level.VERBOSE
        elif cls.config.quiet:
            cls.config.level = Log.Level.QUIET

        cls.log_level_in  : int = cast(int, cls.config.level)
        cls.log_level_out : int = cast(int, cls.config.level)

    @classmethod
    @contextmanager
    def color(cls, new_color):
        old_color = cls.current_color
        try:
            cls.current_color = new_color
            yield
        finally:
            cls.current_color = old_color

    @classmethod
    def indent(cls, color = 0):
        ansi = cls.hex_to_ansi(color) if cls.config.color else ""
        cls.indent_stack.append(ansi + "│ " + cls.reset_color())

    @classmethod
    def dedent(cls):
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
        if cls.current_color != 0 and cls.config.color:
            return "\x1B[0m"
        else:
            return ""

    @classmethod
    def log(cls, text):
        if not isinstance(text, str) or len(text) == 0:
            return

        if cls.log_level_in > cls.log_level_out:
            return

        if cls.current_color >= 0 and cls.config.color:
            hex = cls.current_color
            color_prefix = cls.hex_to_ansi(hex) if cls.config.color else ""
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

            if not cls.config.wrap:
                cls.line_buffer = cls.clip_printable(cls.line_buffer, cls.con_w)

            assert cls.log_level_in is not None

            if cls.log_level_in <= cls.log_level_out:
                sys.stdout.write(cls.line_buffer)

            cls.line_buffer = ""

    @classmethod
    def log_exception(cls, ex):
        tb = traceback.extract_tb(ex.__traceback__)
        if tb:
            frame = tb[-1]
            cls.log("  text = ")
            with cls.color(0xFFFF00):
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
        return f"[{time.perf_counter() - cls.time_origin:8.3f}] " if cls.config.time else ""

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
    def resolve_path(path, strict):
        """
        This tries to convert a path containing potential env variable references and stuff into a
        real path.
        """
        path = os.path.expandvars(path)
        path = pathlib.Path(path).expanduser()
        path = path.resolve(strict = strict)
        return str(path)

    resolve  = Utils.tree_map(lambda path : Path.resolve_path(path, strict = True))

    abspath  = Utils.tree_map(os.path.abspath)
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
            with Log.Level.ERROR, Log.Color.RED:
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
        #print(f"{type(lhs)} = {lhs}")
        #print(f"{type(rhs)} = {rhs}")
        return Utils.cross_join(os.path.join, lhs, rhs, *args)



















class Task:
    # Task object + bookkeeping

    class FAILED(Exception):    pass
    class CANCELLED(Exception): pass
    class SKIPPED(Exception):   pass
    class BROKEN(Exception):    pass

    def __init__(self, *args, **kwargs):
        # The task's 'raw' flags contain everything passed in to hancho.Task(), but no templates
        # are expanded.

        params = Dict(*args, **kwargs)

        self.script = Hancho.cv_script.get()

        self.env = Onion(
            self.script.script_env,
            task_params = Dict(task = params),
            task_config = Dict(task = Dict())
        )

        Dumper.print(self.env, width = 0)

        # The task's 'cooked' config contains only the mandatory fields needed to run the command.
        # It is expected that build scripts will need to read task.env/confing
        # in order to implement task callbacks, so this field is not underscore-prefixed.
        # We can't create it until our input tasks are complete as we need those to expand stuff.

        self.config : TaskConfig = MISSING

        # Build scripts also may need to see the complete list of inputs/outputs to a task in
        # addition to the individual in_/out_ fields, so these are public.

        self.in_files  = {}
        self.out_files = {}
        self.in_depfile : str = ""

        # ------------------------------------
        # Implementation details below this line

        self._enabled = False

        # This must be populated -before- the task starts, as we need it to queue up the task's
        # dependencies
        self.input_tasks = [v for v in Utils.yield_values(params) if isinstance(v, Task)]

        # We don't immediately create an asyncio.Task here because we may not
        # actually need to run this task if its outputs are up to date.
        self._aio_task : asyncio.Task | None = None

        # We remember the aio context we were in when this task was created so that we can return
        # to it when the task starts.
        self._aio_context = contextvars.copy_context()

        # Input dependencies read from the pre-existing source.o.d file.
        self._old_deplines = []

        # Input dependencies read after compilation from the new source.o.d file.
        self._new_deplines = []

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

        self.script.tasks.append(self)

        # Auto-start the task if it was created dynamically during the build.
        if Utils.in_event_loop():
            self.enable_task()

    def __repr__(self):
        return Dumper.dump(self)

    # Tasks must _not_ be copied or we'll hit the "Multiple tasks generate file X" checks.
    # Dicts make deep copies and we want dicts to store Tasks, so we work around it by making
    # Tasks just return themselves when copied.

    def __copy__(self):
        return self

    def __deepcopy__(self, _):
        return self

    def log(self, message : str):
        # Log helper that adds the [ NN/ XX] tag before the log line.
        for line in message.splitlines(keepends=True):
            with Log.Color.LIME:
                if not Log.line_buffer:
                    Log.log(f"[{self._task_id:3d}/{Runner.tasks_enabled:3d}] ")
            Log.log(line)

    def enable_task(self):
        if not self._enabled:
            Runner.tasks_enabled += 1
            self._enabled = True
            if Utils.in_event_loop():
                Runner.create_aio_task(self)

    async def task_top(self):

        task   = self
        script = self.script
        repo   = script.repo

        try:
            # Await all tasks in our input fields and then flatten them.
            await task.await_inputs()

            # We're ready to run
            Runner.tasks_started += 1
            task._task_id = Runner.tasks_started
            with Log.Level.VERBOSE:
                self.log(Utils.instance_tag(self) + " starting\n")

            # Expand all mandatory fields in the raw config and fix raw file paths.
            task.expand_task()

            # Update mtime/hash for all input and output files in this task if they exist.

            # If there's a depfile from a previous build, load it so we can use it below.
            if self.in_depfile:
                self._old_deplines = Utils.load_depfile(
                    self.in_depfile, self.config.depformat, self.config.cwd
                )

            # Inputs are ready, templates are expanded, time to run the task.
            task.sanity_check()

            # Dry runs early out after the task is initialized but before we do .exists() checks or
            # run any commands.
            if repo.build_config.dry:
                return

            # Paths updated. See if we need to rebuild our outputs.
            task._reason = repo.rebuild_reason(task)
            if not task._reason:
                raise Task.SKIPPED(f"Task is up-to-date: '{self.config.name}' : '{self.config.desc}'")

            # Wait for enough jobs to free up to run this task.
            task._cores = await Runner.acquire(self.config.job_size)

            # OK, let's go!
            await task.task_main()

            # And we're done
            return task.out_files

        except asyncio.CancelledError as ex:
            with Log.Level.VERBOSE:
                task.log(f"<asyncio.CancelledError {ex}>\n")
            task._error = ex
        except Task.BROKEN as ex:
            task.log_exception("Task broken!", ex)
            task._error = ex
        except Task.FAILED as ex:
            task.log_exception("Task failed!", ex)
            task._error = ex
        except Task.SKIPPED as ex:
            with Log.Level.VERBOSE:
                task.log(str(ex) + "\n")
            task._error = ex
        except Exception as ex:
            task.log_exception("Task threw an exception!", ex)
            #with Log.Level.ERROR:
            #    Log.log(traceback.format_exc() + "\n")
            task._error = ex
        finally:
            Runner.release(task._cores)

        raise task._error

    async def await_inputs(self):
        # NOTE: Hancho _cannot_ have dependency cycles unless you do something really sketchy via
        # modifying tasks after they're created but before they're started. If you point task B's
        # inputs at task A and task A's inputs at task B and it blows up, that's on you.

        for input_task in self.input_tasks:
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
        task   = self
        script = self.script
        repo   = script.repo
        env    = task.env

        params = env.layers["task_params"].task

        with Log.Level.DEBUG:
            task.log("Task config before expand:\n")
            task.log(Dumper.dump(params) + "\n")

        # We need to expand the build dir first so we can use it in fix_paths.
        build_dir = env.task.build_dir

        # Then we expand all io fields and fix their paths.
        for _field, _files in params.items():
            if not _field.startswith("in_") and not _field.startswith("out_") and _field != "in_depfile":
                continue

            files = [
                val.out_files if isinstance(val, Task) else val
                for val in Utils.yield_values(_files)
            ]

            files = Utils.flatten(files)
            files = Expander.expand(task.env, files)
            files = task.fix_paths(_field, files, build_dir)

            #setattr(self.config, _field, files[0] if len(files) == 1 else files)
            #task.expanded_files[_field] = files[0] if len(files) == 1 else files
            params[_field] = files[0] if len(files) == 1 else files

            # FIXME did the config objects break depfile?

            if _field == "in_depfile":
                task.in_depfile = cast(str, files[0])
            elif _field.startswith("in_"):
                task.in_files[_field] = files
            elif _field.startswith("out_"):
                task.out_files[_field] = files

        print(params)

        # And finally we use the env to fill the config.
        task.config = TaskConfig()
        task.config.fill(task.env)

        Dumper.print(task.config)

        # And now we can do stuff that needs to read self.config

        for _field in params:
            if (_field.startswith("out_") or _field == "in_depfile") and not repo.build_config.dry:
                file = params[_field]
                os.makedirs(Path.dirname(file), exist_ok=True)

        # FIXME commenting this out is gonna break something
        #if len(self.config.command) == 1:
        #    self.config.command = self.config.command[0]

        with Log.Level.DEBUG:
            task.log("Task config after expand:\n")
            task.log(Dumper.dump(task.config) + "\n")

    async def task_main(self):
        task   = self
        config = task.config

        # Run all the task's commands

        text  = repr(config.name) if config.name else ""
        text += " : " if config.name and config.desc else ""
        text += repr(config.desc) if config.desc else ""

        with Log.Level.NORMAL, Log.Color.TEAL:
            task.log(f"Task {text}\n")

        with Log.Level.VERBOSE, Log.color(0x606060):
            task.log(f"Task rebuilding because: {task._reason}\n")

        time_a = time.perf_counter()

        flat_commands = Utils.flatten(config.command)
        for command in flat_commands:
            if command is None:
                continue
            elif callable(command):
                await task.call_callback(command)
            else:
                await task.run_command(command)

        time_b = time.perf_counter()

        with Log.Level.VERBOSE, Log.color(0x606060):
            message  = f"Task took {time_b-time_a:8.6f} sec: {text}\n"
            task.log(message)

        # See if the task wrote all its output files

        for file in Utils.yield_values(task.out_files):
            if not os.path.exists(file):
                raise Task.FAILED(f"Task ran, but output file still missing: {file}")

        # Done!

    def fix_paths(self, field, file, build_dir):
        """
        Input and output file paths in .hancho scripts are declared relative to the directory the
        script is in (stored in the config under 'script_cwd').
        In general we want to run commands from the root of the repo and store output files in
        repo/build, so we need to fix up the paths to match.
        """
        if isinstance(file, (list, set, tuple)):
            return [self.fix_paths(field, f, build_dir) for f in file]
        if isinstance(file, abc.Mapping):
            return {k:self.fix_paths(field, f, build_dir) for k, f in file}

        # Join script_cwd with the filename to produce an absolute path.
        file = Path.join(self.script.script_config.root, file)

        # File paths _must_ be abs'd after joining, otherwise they might look like they're under
        # script_dir, but they're not because the paths could have "../../../../.." in them.
        file = Path.abspath(file)

        # Move all outputs under build_dir and ensure their directories exist.
        # Note - This will also move "in_depfile" under build_dir - this is _intentional_ as
        # it's an _output_ from the compiler and is not checked in to the source tree.
        if (field.startswith("out_") or field == "in_depfile") and not Path.startswith(file, build_dir):
            file = Path.relpath(file, self.script.script_config.root)
            file = Path.join(build_dir, file)

        return file

    def sanity_check(self):
        # Check for all task issues that break the build
        task   = self
        script = self.script
        repo   = script.repo

        if not Path.exists(task.config.cwd):
            raise Task.BROKEN(f"Task working directory '{task.config.cwd}' does not exist")

        if not Path.startswith(task.config.build_dir, script.repo.repo_root):
            raise Task.BROKEN(f"The build dir {task.config.build_dir} is not under repo.root {script.repo.repo_root}")

        # In order to provide the least amount of bafflement to users, CLI commands execute
        # from task_cwd (which is usually the root of the repo, the most common cwd)
        # and callbacks execute from dir(script_path) (because you expect to be in the same
        # directory as the script when the callback is firing).

        # This means that pre-rel-ified paths can only be rel'd to one of the two cwds, not both.
        # And that means we disallow mixed cli/callback command lists.

        if isinstance(task.config.command, list):
            for command in task.config.command:
                if type(command) is not type(task.config.command[0]):
                    raise Task.BROKEN(f"Commands aren't the same type: {task.config.command}")

                # Check that task's commands are either strings or callables.
                if not isinstance(command, str) and not callable(command) and command is not None:
                    raise Task.BROKEN(f"Command {command} is not a string or a callable?")

        # In strict mode, we mark a task broken if its command still has delimiters in it.
        if repo.build_config.strict:
            delims = task.env.raw_get("delims", Expander.delims)
            for command in cast(list, Utils.flatten(task.config.command)):
                if isinstance(command, str) and any(d in command for d in delims):
                    raise Task.BROKEN("STRICT: Command has delimiters in it")

        # Check that all build files would end up under build_dir
        for file in Utils.yield_values(task.out_files):
            assert Path.isabs(file)
            if not Path.startswith(file, task.config.build_dir):
                raise Task.BROKEN(f"Path error, output file {file} is not under build dir {task.config.build_dir}")

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
            if not Path.exists(file) and not repo.build_config.dry:
                raise Task.BROKEN(f"Input file missing - {file}")

        # Tasks should have at most one depfile.
        if isinstance(task.config.in_depfile, list):
            raise Task.BROKEN(f"Tasks can't have more than one dependency file! - {task.config.in_depfile}")

    async def run_command(self, command):
        task   = self
        script = self.script

        with Log.Level.VERBOSE, Log.Color.BLUE:
            task.log(f"{Path.relpath(task.config.cwd, script.repo.repo_root)}$ {command}\n")

        proc = None
        try:
            # Create the subprocess via asyncio and then await the result.
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd    = task.config.cwd,
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
            with Log.Level.VERBOSE, Log.color(0x666666):
                task.log(task.dump_stdout())

    async def call_callback(self, command):
        script = self.script

        with Log.Level.VERBOSE, Log.Color.BLUE:
            callback_dir = Path.relpath(script.script_config.root, script.repo.repo_root)
            self.log(f"{callback_dir}$ {command}\n")

        # Callbacks run from the script_dir where they were defined so that relative paths used
        # in the callback will be correct.
        with chdir(script.script_config.cwd): # type: ignore
            result = command(self)

        # It would seem like we wouldn't have to explicitly unwrap one level of await-ness here,
        # but apparently that's just how Python waitables work.
        if inspect.isawaitable(result):
            result = await result

        return result

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

    def log_exception(self, message, ex = None):
        task = self
        script = self.script

        with Log.Level.ERROR, Log.Color.RED:
            Log.log("========================================\n")
            Log.log(message + "\n")
            Log.log("========================================\n")

            Log.log(f"Script    = {script.script_config.path}:\n")
            Log.log(f"Task      = '{task.config.name}' : '{task.config.desc}'\n")
            Log.log(f"os.getcwd = {os.getcwd()}\n")
            Log.log(f"task cwd  = {task.config.cwd}\n")
            Log.log(f"command   = {task.config.command}\n")
            if ex:
                Log.log_exception(ex)
            Log.log(task.dump_stdout())

            Log.log("========================================\n")

class Tracer:
    # Expansion tracing class used by Expander
    #
    # The traces generated look like this -
    #
    # [   0.024] ┌ Onion@94F0.get('name')
    # [   0.024] └ 'name' : NoneType = None
    # [   0.024] ┌ Onion@94F0.get('desc')
    # [   0.024] │ ┌ Onion@94F0.expand('Linking {basename(out_bin)}')
    # [   0.024] │ │ ┌ Onion@94F0.eval('{basename(out_bin)}')
    # [   0.024] │ │ │ ┌ Onion@94F0.get('basename')
    # [   0.024] │ │ │ └ 'basename' : function = <function basename at 0x79b4121f2770>
    # [   0.024] │ │ │ ┌ Onion@94F0.get('out_bin')
    # [   0.024] │ │ │ └ 'out_bin' : str = '/home/aappleby/repos/hancho/build/examples/hello_worl...
    # [   0.024] │ │ └ '{basename(out_bin)}' : str = 'hello_world'
    # [   0.024] │ └ 'Linking {basename(out_bin)}' : NoneType = None
    # [   0.024] └ 'desc' : str = 'Linking hello_world'
    # [   0.024] ┌ Onion@94F0.get('command')
    # [   0.024] │ ┌ Onion@94F0.expand('g++ {in_objs} -o {out_bin}')
    # [   0.024] │ │ ┌ Onion@94F0.eval('{in_objs}')
    # [   0.024] │ │ │ ┌ Onion@94F0.get('in_objs')
    # [   0.024] │ │ │ └ 'in_objs' : list = ['/home/aappleby/repos/hancho/build/examples/hello_wo...
    # [   0.024] │ │ └ '{in_objs}' : list = ['/home/aappleby/repos/hancho/build/examples/hello_wo...
    # [   0.024] │ │ ┌ Onion@94F0.eval('{out_bin}')
    # [   0.024] │ │ │ ┌ Onion@94F0.get('out_bin')
    # [   0.024] │ │ │ └ 'out_bin' : str = '/home/aappleby/repos/hancho/build/examples/hello_worl...
    # [   0.024] │ │ └ '{out_bin}' : str = '/home/aappleby/repos/hancho/build/examples/hello_worl...
    # [   0.024] │ └ 'g++ {in_objs} -o {out_bin}' : NoneType = None
    # [   0.024] └ 'command' : str = 'g++ /home/aappleby/repos/hancho/build/examples/hello_world/...

    def __init__(self, env : Dict | Onion, enter_message, name):
        self.enter_message = f"{enter_message}({name!r})"
        self.name = name
        self.env = env
        self.result = None
        self.trace = Log.config.trace if Log.config is not MISSING else False

    def save_result(self, result : Any):
        self.result = result
        return result

    def __enter__(self): # pragma: no cover
        if not self.trace:
            return self

        env_color = Utils.obj_to_hex(self.env)

        with Log.color(env_color):
            Log.log(f"┌ {Utils.instance_tag(self.env)}." + self.enter_message + "\n")
            Log.indent(env_color)

        return self

    def __exit__(self, exc_type = None, exc_value = None, tb = None): # pragma: no cover
        if not self.trace:
            return False

        env_color = Utils.obj_to_hex(self.env)

        if exc_type:
            Log.dedent()
            with Log.color(env_color):
                Log.log(f"└ {exc_value!r}\n")
            return

        type = self.result.__class__.__name__
        env_color = Utils.obj_to_hex(self.env)
        result_color = Utils.obj_to_hex(self.result)

        Log.dedent()

        if isinstance(self.result, (Dict, Onion)):
            with Log.color(env_color):
                Log.log(f"└ {self.name!r} : ")
            with Log.color(result_color):
                Log.log(f"{type} = {Utils.instance_tag(self.result)}\n")
        else:
            with Log.color(env_color):
                Log.log(f"└ {self.name!r}")
            Log.log(f" : {type} = {self.result!r}\n")


        return False

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

    @classmethod
    def create_aio_task(cls, task):
        assert Utils.in_event_loop()

        if task._aio_task is None:
            t = asyncio.create_task(task.task_top(), context=task._aio_context)
            t.hancho_task = task # type: ignore
            cls.live_aio_tasks.add(t)
            t.add_done_callback(lambda t: cls.aio_done_queue.put_nowait(t))
            task._aio_task = t

        # Start all tasks referenced by the config so we don't deadlock while waiting for them.
        for v in task.input_tasks:
            v.enable_task()

    @classmethod
    async def run_tasks(cls, tasks_to_run):
        """Run all tasks until we run out."""

        # ------------------------------------
        # Create asyncio tasks for all enabled Hancho tasks.

        for task in tasks_to_run:
            task.enable_task()
            Runner.create_aio_task(task)

        # ------------------------------------
        # Await tasks in the asyncio queue until the queue is empty, or we hit too many failures.

        with Log.Level.VERBOSE, Log.Color.BLUE:
            Log.log("Running tasks...\n")

        while cls.live_aio_tasks and (Runner.tasks_broken + Runner.tasks_failed) <= Runner.max_errors:
            finished_aio_task = None

            try:
                finished_aio_task = cast(asyncio.Task, await cls.aio_done_queue.get())
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
                with Log.Level.DEBUG:
                    Log.log(f"Weird exception {type(ex)} >{ex}< at {time.perf_counter()}\n")
                    Log.log_exception(ex)
                Runner.tasks_failed += 1
            else:
                # If _none_ of the above exceptions fired, we mark the task as complete.
                finished_aio_task.hancho_task._complete = True #type:ignore
            finally:
                if finished_aio_task is not None:
                    cls.live_aio_tasks.discard(finished_aio_task)

        failures = Runner.tasks_broken + Runner.tasks_failed
        if failures > Runner.max_errors:
            with Log.Level.ERROR:
                Log.log(f"Too many failures after {failures}, cancelling tasks and stopping build\n")

            # Cancel all the asyncio.Tasks that haven't completed yet
            with Log.Level.VERBOSE:
                Log.log(f"Cancelling {len(cls.live_aio_tasks)} tasks\n")

            # This tasks_cancelled count may be off by one or two due to in-flight tasks not being
            # accounted for in live_aio_tasks, but it doesn't matter - we're about to bail out due
            # to failures or someone ctrl-c'ing the build, this is purely cosmetic.

            Runner.tasks_cancelled += len(cls.live_aio_tasks)
            for t in cls.live_aio_tasks:
                t.cancel()

            # and then wait on their cancellations to complete (it isn't instantaneous)
            await asyncio.gather(*cls.live_aio_tasks, return_exceptions=True)

        return 1 if Runner.tasks_failed or Runner.tasks_broken else 0

    @classmethod
    def run_tool(cls, tool : str): # pragma: no cover
        if tool == "clean":
            for repo in Hancho.repos:
                build_root = repo.build_config.root

                # Tiny bit of sanity checking so we don't inadvertently delete a repo.
                assert build_root.starts_with(repo.repo_root) and build_root != repo.repo_root

                if Path.isdir(build_root):
                    Log.log(f"Wiping build_root {build_root}\n")
                    shutil.rmtree(build_root, ignore_errors=True)
            Log.log("Clean done\n")
            return 0
        else:
            raise AssertionError(f"Don't know how to run tool {tool}")

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
    levels = [v.lower() for v in Log.Level.__members__]

    bool_opt = argparse.BooleanOptionalAction

    # ------------------------------------
    # fmt: off

    #default_script_path = os.path.abspath("build.hancho")
    #default_hancho_root = os.path.dirname(__file__)
    #default_depformat   = "gcc" if os.name == "posix" else "msvc"
    #default_build_root  = "{join(repo.root, 'build')}"

    #default=default_hancho_root,
    #default = "",
    #default = "",
    #default = "{}«»",
    #default = default_depformat,
    #default = 0,
    #default = os.cpu_count() or 1,
    #default="{dirname(script.path)}",
    #default = default_build_root,
    #default = "",
    #default = "",
    #default = False,
    #default = False,
    #default = False,
    #default = True,
    #default=default_script_path,
    #default="{dirname(script.path)}",
    #default = int(Log.Level.NORMAL),
    #default = False,
    #default = False,
    #default = False,
    #default = False,
    #default = False,
    #default = True,
    #default = True,

    parser.add_argument(      "--hancho.root",        type=str.strip,     help="Hancho lives in this directory (so we can find hancho/tools, etc).")
    parser.add_argument('-o', "--hancho.opt_file",    type=str.strip,     help="File containing JSON that will be used as additional options")
    parser.add_argument(      "--hancho.run_tool",    type=str.strip,     help="Run a subtool.")
    parser.add_argument(      "--hancho.delims",      type=str.strip,     help="The characters to use as macro delimiters.")
    parser.add_argument(      "--hancho.depformat",   type=str.strip,     help="Dependency file format (gcc or msvc)")
    parser.add_argument(      "--hancho.max_errors",  type=int,           help="The maximum number of task errors we tolerate before abandoning the build")
    parser.add_argument('-j', "--hancho.max_jobs",    type=int,           help="Run a maximum of N jobs in parallel.")
    parser.add_argument(      "--repo.root",          type=str.strip,     help="The top repo lives in this directory.")
    parser.add_argument(      "--build.root",         type=str.strip,     help="Build artifacts go in this directory.")
    parser.add_argument(      "--build.tag",          type=str.strip,     help="Tagged builds will have separate subdirectories under the build directory.")
    parser.add_argument('-t', "--build.target",       type=str.strip,     help="A regex that selects a subset of targets to build.")
    parser.add_argument(      "--build.force",        action = bool_opt,  help="Rebuild targets even if they're clean.")
    parser.add_argument(      "--build.all",          action = bool_opt,  help="Build every task in every repo.")
    parser.add_argument(      "--build.dry",          action = bool_opt,  help="Dry run - Do everything except actually run commands.")
    parser.add_argument(      "--build.strict",       action = bool_opt,  help="Strict mode, slightly more error checking to catch footguns.")
    parser.add_argument(      "--script.path",        type=str.strip,     help="Path to the .hancho file that starts the build.")
    parser.add_argument(      "--script.root",        type=str.strip,     help="The top script runs in this directory.")
    parser.add_argument(      "--log.level",          choices = levels,   help="Manually select verbosity level. 'quiet' = none, 'trace' = maximal spam")
    parser.add_argument('-Q', "--log.quiet",          action = bool_opt,  help="(same as --log_level=quiet)")
    parser.add_argument('-V', "--log.verbose",        action = bool_opt,  help="(same as --log_level=verbose)")
    parser.add_argument('-D', "--log.debug",          action = bool_opt,  help="(same as --log_level=debug)")
    parser.add_argument('-T', "--log.trace",          action = bool_opt,  help="(same as --log_level=trace)")
    parser.add_argument('-w', "--log.wrap",           action = bool_opt,  help="Wrap lines around the console instead of clipping them")
    parser.add_argument('-c', "--log.color",          action = bool_opt,  help="Use color in the log for better readability")
    parser.add_argument(      "--log.time",           action = bool_opt,  help="Timestamp each log line")
    # fmt: on

    (argv_vars, unrecognized) = parser.parse_known_args(argv if argv else [])
    argv_vars = vars(argv_vars)

    argv_flags = Dict()
    for k, v in argv_vars.items():
        if v is not None:
            argv_flags[k] = v

#    task_params = Dict(
#        name = "<no name>",
#        desc = "<no desc>",
#        command = "",
#        cwd = "{repo.root}",
#        in_depfile = "",
#        depformat = "gcc",
#        job_size = 1,
#        build_dir = "{abspath(join(build.root, build.tag, relpath(script.root, repo.root)))}"
#    )
#
#    argv_flags.task = task_params

    # ------------------------------------
    # Load flags from opt_file if present

    opt_file = argv_flags.pop("opt_file", None)
    if opt_file:
        #with Log.Color.GREEN:
        #    Log.log(f"Loading options file {opt_file!r}\n")
        if os.path.exists(opt_file):
            with open(opt_file) as f:
                try:
                    opts = json.load(f)
                    argv_flags.update(opts)
                except Exception as _:
                    #with Log.Color.RED:
                    #    Log.log(f"Opt file {opt_file!r} invalid!\n")
                    pass
        else:
            #with Log.Color.RED:
            #    Log.log(f"Opt file {opt_file!r} not found!\n")
            pass

    # ------------------------------------
    # Unrecognized command line flags also become config fields if they are flag-like.
    # Naked flags become {'name':True}, number types become numbers, 'true' and 'false'
    # become bools (regardless of capitalization), everything else becomes a string.

    mystery_flags = {}
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
    flags.merge(argv_flags)

    flags.merge(mystery_flags)
    flags.merge(*args, kwargs)

    return flags

class Hancho:
    # Just a container for global functions and stuff.

    repos : list[Repo] = []
    scripts : list[Script] = []

    @classmethod
    def add_repo(cls, repo : Repo):
        cls.repos.append(repo)

    @classmethod
    def add_script(cls, script):
        cls.scripts.append(script)

    @classmethod
    def yield_tasks(cls):
        for repo in cls.repos:
            yield from repo.yield_tasks()

    @classmethod
    def init(cls, params):
        log_params = hancho_defaults.pop("log").merge(params.pop("log", Dict()))
        log_config = LogConfig(Dict(log = log_params))
        Log.reset(log_config)

        env  = Onion(
            hancho_aliases = hancho_aliases,
            hancho_defaults = hancho_defaults,
            hancho_params = params
        )

        cls.hancho_env = env

        cls.real_filenames = set()
        cls.dedupe = {}
        cls.cv_script = ContextProxy(None)


        #cls.hancho_params = params
        #cls.config = HanchoConfig(cls.hancho_env)
        #cls.hancho_env.layers["hancho_config"] = Dict(hancho = cls.config)

        env_hancho = env.hancho

        Utils.reset()
        Expander.reset(env_hancho.delims)
        Runner.reset(env_hancho.max_jobs, env_hancho.max_errors)

        # ---------

        root_params = Dict(
            script = Dict(
                path = __file__,
                root = os.path.dirname(__file__),
            )
        )

        root_env = Onion(cls.hancho_env, script_params = root_params)

        print(root_env)
        print(root_env.flat())

        root_script = Script(script_env = root_env, script_code = None)

        root_repo = Repo(root_env)
        root_repo.add_script(root_script)

        cls.cv_script.set(root_script)

    @classmethod
    def load_path(cls, parent_script : Script, script_path : str, is_repo : bool, *args, **kwargs) -> Script:
        script_path = parent_script.script_env.expand(script_path)
        script_path = Path.resolve(script_path)

        with open(script_path, encoding="utf-8") as file:
            source = file.read()
            return cls.load_source(parent_script, script_path, source, is_repo, *args, **kwargs)

    @classmethod
    def load_source(cls, parent_script : Script, script_path : str, source : str, is_repo : bool, *args, **kwargs) -> Script:

        child_params = Dict(
            *args,
            **kwargs,
            script = Dict(
                path = script_path,
                root = os.path.dirname(script_path)
            )
        )

        # --------------------------------
        # Dedupe the load - only scripts with identical real paths and identical configs are
        # deduped. This relies on __repr__ and the fields read by Dumper.dump being stable during a
        # build, which they should be in practice.

        dedupe_key = Hancho.dict_to_key(child_params)
        deduped_script = Hancho.dedupe.get(dedupe_key)
        if deduped_script:
            with Log.Level.VERBOSE, Log.Color.SKY:
                Log.log(f"Deduped load of {script_path}\n")
            return deduped_script

        # --------------------------------
        # Not deduped, create a new script.

        with Log.Level.VERBOSE, Log.Color.ORANGE:
            Log.log(f"Loading {script_path}\n")

        try:
            Log.indent(Log.Color.ORANGE)

            child_env = Onion(parent_script.script_env)

            # FIXME this is gross
            child_env.script_params.merge(child_params)
            child_env.layers.script_config = Dict()

            child_code   = compile(source, script_path, "exec", dont_inherit=True)
            child_script = Script(script_env = child_env, script_code = child_code)

            if is_repo:
                new_repo = Repo(child_env) if is_repo else parent_script.repo
                new_repo.add_script(child_script)
            else:
                parent_script.add_child(child_script)
            Hancho.add_script(child_script)
            Hancho.dedupe[dedupe_key] = child_script

            child_script.exec()

        finally:
            Log.dedent()

        return child_script

    @classmethod
    def main(cls) -> int:

        with Log.Level.VERBOSE, Log.Color.LIME:
            Log.log(f"Command line : {" ".join(sys.argv)}\n")
        if Log.config.trace:
            Log.log("Trace mode on\n")
        if Log.log_level_out >= Log.Level.DEBUG:
            Log.log("Debug mode on\n")
        if Log.log_level_out >= Log.Level.VERBOSE:
            Log.log("Verbose mode on\n")

        # ------------------------------------
        # Load and exec top script

        time_a1 = time.perf_counter()
        Log.indent(Log.Color.ORANGE)

        parent_script = Hancho.cv_script.get()
        script_path = Hancho.hancho_env.script.path
        top_script = Hancho.load_path(parent_script, script_path, is_repo = True)
        Hancho.cv_script.set(top_script)

        Log.dedent()
        time_b1 = time.perf_counter()
        with Log.Level.VERBOSE, Log.Color.BLUE:
            Log.log(f"Loading scripts took {time_b1 - time_a1:8.6f} seconds\n")

        # ------------------------------------
        # Sanity-check the repo/script hierarchies

        for repo in cls.repos:
            assert repo.root_script in repo.scripts
            for script in repo.scripts:
                assert script.repo == repo

        for parent in cls.scripts:
            for child in parent.child_scripts:
                assert child in cls.scripts
                assert child.parent_script == parent

        # ------------------------------------
        # If we're running a tool, run it and we're done.

        tool = Hancho.hancho_env.hancho.run_tool

        if tool:
            time_a2 = time.perf_counter()
            result = Runner.run_tool(tool)
            time_b2 = time.perf_counter()

            with Log.Level.VERBOSE, Log.Color.GREEN:
                Log.log(f"Tool took {time_b2 - time_a2:8.6f} seconds\n")
            return result

        # ------------------------------------
        # Start the build

        time_a3 = time.perf_counter()
        result = Hancho.build()
        time_b3 = time.perf_counter()
        with Log.Level.VERBOSE, Log.Color.GREEN:
            Log.log(f"Build took {time_b3 - time_a3:8.6f} seconds\n")

        # ------------------------------------
        # Done

        Hancho.banner_end(top_script)
        return result

    @classmethod
    def build(cls) -> int:

        top_script = cls.cv_script.get()
        top_repo   = top_script.repo

        for repo in cls.repos:
            Log.log(f"Repo {repo.repo_root}\n")
            Log.indent()
            for script in repo.scripts:
                Log.log(f"Script {script.script_config.path}{' (root)' if script.is_repo() else ''}\n")
            Log.dedent()

        # ------------------------------------
        # This must happen _after_ all repos are loaded (so that if they change repo_root we don't
        # get the old path), but _before_ we build any tasks.

        for repo in cls.repos:
            repo.load_stat_db()

        # ------------------------------------
        # Select the set of tasks to run.

        tasks_to_run = []

        if top_repo.build_config.target:
            # Enable all tasks whose name matches the target regex
            # NOTE - We match task.env.name, _not_ the expanded task.config.name.
            # This is because the task _has not initialized yet_, so we have no config.name.
            target_regex = re.compile(top_repo.build_config.target)

            for task in Hancho.yield_tasks():
                if target_regex.search(task.env.name):
                    tasks_to_run.append(task)

        elif top_repo.build_config.all:
            for task in Hancho.yield_tasks():
                tasks_to_run.append(task)

        else:
            # Enable all tasks in the top repo
            for task in top_repo.yield_tasks():
                tasks_to_run.append(task)

        # ------------------------------------
        # Run the tasks.

        result = asyncio.run(Runner.run_tasks(tasks_to_run))

        # ------------------------------------
        # Update stat DBs.

        for repo in cls.repos:
            repo.save_stat_db()

        return result

    @classmethod
    def banner_end(cls, script):
        task_count = len(list(Hancho.yield_tasks()))

        with Log.Level.VERBOSE:
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
            with Log.Level.ERROR, Log.Color.RED:
                Log.log("BUILD FAILED\n")
        elif Runner.tasks_finished:
            with Log.Color.GREEN:
                Log.log("BUILD PASSED\n")
        else:
            with Log.Color.BLUE:
                Log.log("BUILD CLEAN\n")

        #with Log.Level.DEBUG, Log.Color.BLUE:
        #    for repo in cls.repos.items():
        #        Log.log(f"Stats for {script.repo_root}\n")
        #        Log.indent(Log.Color.BLUE)
        #        for k, v in script.reasons.items():
        #            Log.log(f"Rebuild reasons {k:13} = {v}\n")
        #        Log.dedent()

    @classmethod
    def path_to_code(cls, source_path) -> types.CodeType:
        source_path = Path.resolve(source_path)
        with open(source_path, encoding="utf-8") as file:
            source = file.read()
            code = compile(source, source_path, "exec", dont_inherit=True)
            return code

    @classmethod
    def dict_to_key(cls, _dict) -> str:
        dedupe_key = Dumper.dump(_dict, print_id = False, tab = "", color_code = False, depth = 999, width = 999)
        dedupe_key = Dumper.depointer(dedupe_key)
        dedupe_key = "".join(dedupe_key.split())
        return dedupe_key

class HanchoProxy(types.ModuleType):

    _module = sys.modules[__name__]

    Dict     = Dict
    Tool     = Tool
    Task     = Task
    Path     = Path

    log      = Log.log
    #dump     = Dumper.dump
    dump     = Dumper.print
    flatten  = Utils.flatten
    run_cmd  = Utils.run_cmd
    weave    = Utils.weave
    hash     = Utils.hash

    abspath  = Path.abspath
    basename = Path.basename
    dirname  = Path.dirname
    join     = Path.join
    relpath  = Path.relpath
    resolve  = Path.resolve
    swapext  = Path.swapext

    fail     = Script.Fail
    abort    = Script.Abort
    earlyout = Script.EarlyOut

    @staticmethod
    def init(params):
        Hancho.init(params)

    @staticmethod
    def load(path, *args, **kwargs) -> types.ModuleType:
        return Hancho.load_path(Hancho.cv_script.get(), path, False, *args, **kwargs).script_module

    @staticmethod
    def repo(path, *args, **kwargs) -> types.ModuleType:
        return Hancho.load_path(Hancho.cv_script.get(), path, True, *args, **kwargs).script_module

    @staticmethod
    def build() -> int:
        return Hancho.build()

    @staticmethod
    def as_dict():
        return {
            k:v
            # linter thinks __dict__ is not a Mapping, even though it's a mappingproxy
            for k, v in cast(abc.Mapping, HanchoProxy.__dict__).items()
            if not k.startswith("_")
        }

    @staticmethod
    def init_for_testing(argv, *args, **kwargs):
        params = parse_flags(argv, *args, **kwargs)
        Hancho.init(params)
        return HanchoProxy._module


hancho_aliases = Dict(
    flatten  = Utils.flatten,
    run_cmd  = Utils.run_cmd,
    weave    = Utils.weave,
    hash     = Utils.hash,

    abspath  = Path.abspath,
    basename = Path.basename,
    dirname  = Path.dirname,
    join     = Path.join,
    relpath  = Path.relpath,
    resolve  = Path.resolve,
    swapext  = Path.swapext,
)




sys.modules["hancho"] = HanchoProxy("hancho")


def _start():

    #params = parse_flags(sys.argv)
    #Hancho.init(params)
    #print(Dumper.dump(Dict(params), depth=999))
    #sys.exit(0)

    if __name__ == "__main__":

        # Top-level exception handler just so we can print a big red "SOMETHING BROKE" message if
        # we failed to catch an exception during load/build. The 'except' clause should catch
        # Exception and not BaseException so ctrl-c doesn't get misinterpreted as a Hancho bug.

        try:
            params = parse_flags(sys.argv)
            Hancho.init(params)
            result = Hancho.main()
            sys.exit(result)

        except Exception:
            print(Log.hex_to_ansi(0xFF3030), end="")
            print("Hancho hit an unhandled exception:")
            traceback.print_exc()
            print("\x1B[0m", end="")
            sys.exit(1)

        finally:
            # Don't leave the last line of the log sitting in line_buffer!
            Log.flush()

_start()
