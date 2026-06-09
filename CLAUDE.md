# Working with Claude on Hancho

## Project Overview
Hancho is a single-file Python build system (similar to Make/Ninja/Bazel) that uses
`.hancho` files as build scripts. The entire implementation lives in `hancho.py` (~2200
lines, no third-party dependencies, Python 3.12+).

The pitch: Ninja's speed and simplicity combined with Bazel's expressive Python-like
syntax, but lightweight and install-free. You drop `hancho.py` into your repo and run it;
there is no install step. Build scripts are plain Python, so they can run arbitrary code.
(Consequently Hancho is **not a sandbox** - a build script can do anything Python can do.)

## Interaction Guidelines

### Autonomous Work Is Confined to `sandbox/`
- The **only** filesystem changes Claude may make without explicit, specific permission are
  inside the `sandbox/` folder (gitignored). Experiments, scratch scripts, generated test
  builds, and trial `.hancho` files all go there.
- **Anywhere outside `sandbox/`** - `hancho.py`, `examples/`, `tests/`, `tutorial/`, `docs/`,
  `CLAUDE.md`, etc. - requires an explicit request before editing or creating files.
- This is a hard boundary, not a default. "Go ahead" on a sandbox task does not extend to
  touching the rest of the repo.

### Don't Be Proactive
- **Don't** automatically run commands, tests, or investigations unless explicitly asked
- **Don't** explore branches, check diffs, or analyze code without a request
- **Wait** for explicit requests before taking action
- Focus on discussion and answering questions until given a specific task

### When Running Tests
- Run 'python -m unittest' in the root of the repo.
- Always check that tests pass after changes

### Code Style
- The codebase uses pylint with some disabled checks (see top of hancho.py)
- No emojis in code or output unless explicitly requested
- Keep changes minimal and focused
- `hancho.py` is organized into `# region` / `# endregion` sections (Log, Colors, Utils,
  Path, Dict, Options, Task, Expander, Tracer, Loader, Runner, init/main).

## How Hancho Works

### The mental model
A build script creates **Tasks**. Each Task is a config (a `Dict`) describing a command to
run, its input files, and its output files. Passing one Task as another Task's input field
creates a dependency edge. Hancho assembles all Tasks into a dependency graph and runs them
in parallel via asyncio, skipping any whose outputs are already up to date.

### Build scripts (`.hancho` files)
- A `.hancho` file is just a Python module that is `exec`'d with a special module dict that
  has `hancho` and `__file__` pre-injected. The `.hancho` suffix is conventional, not
  required.
- To use Hancho you either run `hancho.py` in a directory containing `build.hancho`, or
  `import hancho` from your own Python and drive it directly.
- The root script is loaded as a **repo** (see below). It can pull in other scripts with
  `hancho.load(...)` (same repo) or `hancho.repo(...)` (new subrepo).

### Config, the `Dict` class, and merging
Almost everything in Hancho is a `Dict` - a `dict` subclass with three important behaviors:
1. **Dot access**: `config.command` is the same as `config["command"]`.
2. **Recursive merge on construction**: `Dict(a, b, c, key=val)` merges left-to-right. The
   **rightmost non-None value wins**; two Dicts at the same key are merged recursively;
   collections/mappings are deep-copied so Dicts don't alias each other.
3. `Tool` is just an alias subclass of `Dict` - identical behavior, different name for
   readability/debugging. By convention `Tool` holds a reusable command template and `Task`
   instances specialize it with concrete inputs/outputs.

Every script context has its own config. `hancho.config` (resolved via a module-level
`__getattr__` that reads a `contextvars.ContextVar`) returns the config for the currently
executing script. When you call `hancho.Task(...)`, the new task's config is
`Dict(current_script_config, *args, **kwargs)` - i.e. the script's config is automatically
folded in. This is why fields like `gcc_flags` defined once in a script are visible to every
Task in it.

### Fields with special prefixes (`in_` / `out_`)
Field naming drives dependency tracking:
- `in_*` fields are **input files**. If a value is a `Task` (or list containing Tasks),
  Hancho awaits that task and substitutes its output files - this is what wires the graph.
- `out_*` fields are **output files**. They are relocated under `build_dir`.
- `in_depfile` is a special case: a single compiler-generated dependency file (`.d`) that is
  treated as *both* an output (it's written under `build_dir`) and an input (its listed
  headers are checked for the rebuild decision). Supports `gcc` (`-MMD`) and `msvc`
  (`/sourceDependencies`) formats via the `depformat` field.
- Helper predicates in `Task`: `is_input_field`, `is_output_field`, `is_depfile_field`,
  `is_io_field`.

### Text expansion (the `{macro}` system)
Templates look like Python f-strings but are lazier and more powerful (see the `Expander`
region):
- A `{...}` span is `eval`'d as a Python expression. The namespace is a `ChainMap` of the
  task's own config, the global config, and a set of **aliases**.
- Expansion is **recursive** - the result of one expansion is itself expanded, up to
  `MAX_DEPTH` (20) as an infinite-loop tripwire.
- **TEFINAE - "Template Expansion Failure Is Not An Error."** If a macro can't be evaluated
  (e.g. it references a field that doesn't exist *in this context yet*), it is returned
  **unchanged** rather than throwing. This is deliberate: nested Dicts can carry templates
  that only resolve once merged into an outer context.
- Aliases available inside `{...}` (and as `hancho.<name>`): path helpers `abs`, `base`,
  `ext`, `norm`, `real`, `rel`, `stem`, plus `path` (= `os.path`), `flatten`, `weave`,
  `run_cmd`, and the loaders `load` / `repo`. Example: `out_obj = "{ext(in_src, '.o')}"`.
- `Tree[T]` (`T | list[Tree[T]]`): many fields accept arbitrarily nested lists. Most
  Path/Utils helpers are "recursified" to map over these, and `Utils.flatten` collapses
  them. `weave` does a cross-join (e.g. prefix/suffix every item in a list).
- The `Tracer` class produces the indented expansion traces you see under
  `--trace`/`verbosity=trace`.

### Repos vs. scripts; deduplication
- `hancho.load(file)` runs a script **in the same repo** (inherits `repo_dir`).
- `hancho.repo(file)` runs a script as a **new subrepo** - `repo_dir`/`repo_file`/`this_repo`
  are rebased to that script's directory. Outputs land in that subrepo's own `build/`.
- Both return the module object, so you can reference another script's tasks:
  `myrepo = hancho.repo("myrepo/build.hancho"); ... in_obj = [main_o, myrepo.util_o]`.
- **Loading is deduped**: the `Loader` keys modules on `(real path, config dump)`. Loading
  the same script with an identical config returns the same module instead of re-running it.
  This relies on `Utils.dump_to_str` producing stable output (it's also Hancho's
  pretty-printer).

### Which tasks actually run
By default, only tasks created by the **root repo** are built (subrepo tasks build only if
something in the root depends on them - note `broken_o` in `examples/subrepos/myrepo` exists
but is never built). This is controlled by `Runner.select_root_tasks`:
- A positional `target` regex on the CLI selects tasks by `name`.
- `--rebuild` / `-a` enables **everything** in every loaded script.

### Commands: shell strings vs. Python callbacks
A task's `command` can be a string, a list of strings, or a callable (or a list of those,
but not mixed types in one list):
- **String commands** run as shell subprocesses from `task_cwd` (usually the repo root).
- **Callbacks** are Python functions `f(task)` (may be async). They run from `script_cwd`
  (the script's own directory) so relative paths behave intuitively. A callback can read
  files and even create *new* Tasks at build time - this is how dynamic dependencies work
  (see `examples/dynamic_dependencies`, e.g. read a generated filelist, then spawn a task to
  concatenate those files).

### The build lifecycle (per task)
Roughly, in `Task.task_main` / `task_init`:
1. Expand non-IO path/flag fields (`build_dir`, `task_cwd`, etc.).
2. `await` all `in_*` fields, replacing Task references with their output file lists.
3. `chdir` into `task_cwd` and run `task_init` synchronously: flatten/validate the command,
   expand all `in_`/`out_` filenames, relocate outputs under `build_dir` (creating dirs),
   then expand `name`/`desc`/`command`.
4. Sanity checks: outputs must be under `build_dir`; no two tasks may build the same real
   file (**TaskCollision**); inputs must exist; `--strict` rejects leftover `{}` in a
   command (a typo guard).
5. Decide whether to rebuild (`rebuild_reason`): forced rebuild; no inputs or no outputs
   (always rebuild); a missing output; any input/loaded-script/`hancho.py`/depfile entry
   newer than the oldest output. Empty reason -> `Task.SKIPPED`.
6. Acquire cores from the job pool, then run each command in order.

Task outcome exceptions: `SKIPPED` (up to date), `CANCELLED` (a dependency failed or build
aborted), `FAILED` (command/exception at runtime), `BROKEN` (misconfigured task, caught
during init).

### The job pool / Runner
- `Runner` owns all tasks and an asyncio `Semaphore` sized to `core_max`
  (`-j`, default `os.cpu_count()`). A task acquires `core_count` cores before running, so a
  heavy task can intentionally block lighter ones behind it.
- Tasks are created eagerly but only turned into `asyncio.Task`s when enabled; dependencies
  are enabled transitively so the graph can't deadlock.
- The build stops early once failures exceed `max_errors` (default 0), cancelling in-flight
  tasks - on Linux it kills the whole process group on Ctrl-C.
- Final status banner: `BUILD PASSED` / `BUILD FAILED` / `BUILD CLEAN` (nothing needed doing).

### Important config fields (defaults in `Options.default_config`)
- `root_dir` / `root_file` - where the build starts (`build.hancho` by default).
- `repo_dir` / `repo_file` / `this_repo` - the current repo (rebased by `hancho.repo`).
- `script_cwd` / `script_file` / `this_module` - the currently executing script.
- `task_cwd` - where shell commands run (defaults to `repo_dir`).
- `build_root` (`{repo_dir}/build`), `build_tag`, `build_dir`
  (`{build_root}/{build_tag}/{rel(task_cwd, repo_dir)}`) - where outputs go. `--build_tag`
  gives a build its own subtree (e.g. debug vs. release).
- `name` / `desc` / `command`, `core_count`, `depformat`, `dry_run`, `enabled`.

### CLI flags (`Options.parse_flags`)
`target` (regex), `-C/--root_dir`, `-f/--root_file`, `-t/--tool` (e.g. `clean` wipes
`build_root`), `--build_tag`, `-j/--core_max`, `--max_errors`, `-n/--dry_run`,
`-a/--rebuild`, `--wrap`, `--strict`, and verbosity shortcuts `-q/-v/-d/--trace` or
`--verbosity=LEVEL` (QUIET..TRACE). **Unrecognized `--flags` become config fields**
(`--foo` -> `foo=True`, `--foo=3` -> `foo=3`), so scripts can read arbitrary CLI options.

### Embedding Hancho
When imported rather than run as `__main__`, the module auto-calls `init()`. Call
`hancho.init(verbosity="debug", myoption=1234, ...)` to (re)initialize with custom config -
this is also how the test suite resets global state between cases.

## Repo layout
- `hancho.py` - the whole implementation.
- `examples/` - runnable sample builds. Good reference patterns:
  - `hello_world/` - minimal compile+link with a `Tool`.
  - `subrepos/` - cross-repo dependencies via `hancho.repo`.
  - `bazel-cpp-tutorial/` - reusable `cc_library`/`cc_binary` rules in a `tools_*.hancho`.
  - `dynamic_dependencies/` - callbacks that generate tasks at build time.
- `tools/` - sample reusable build tools: `tools_base.hancho` (C++), `tools_wasm.hancho`,
  `tools_fpga.hancho`, `tools_riscv.hancho`.
- `tests/` - unittest suite. `docs/`, `tutorial/` - work-in-progress documentation.

### Current State
- `main` branch: Stable, all tests passing. All other branches have been merged in to get ready for 1.0.

### Common Tasks
- Run tests: `python -m unittest` in the repo root.
- Run hancho: `python hancho.py` or `./hancho.py`
- Test suite uses unittest framework
- Build artifacts go in `build/` directories (gitignored)
