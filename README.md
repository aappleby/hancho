# ![Logo](assets/hancho_small.png) Hancho

"班長, hanchō - "Squad leader”, from 19th c. Mandarin 班長 (bānzhǎng, “team leader”)"

Hancho is a simple, pleasant build system with few moving parts.

Hancho fits comfortably in a single Python file and requires no installation, just copy-paste it into your source tree.

Hancho is inspired by Ninja (for speed and simplicity) and Bazel (for syntax and extensibility).

Like Ninja, it knows nothing about your build tools and is only trying to assemble and run commands as fast as possible.

Unlike Ninja, Hancho's build scripts are vastly less verbose.

Like Bazel, the build synax is simple and Pythonesque.

Unlike Bazel, your build can call arbitrary Python code (for better or worse).

Hancho should suffice for small to medium sized projects.

## Updates
 - 2024-11-02 - We're now on version v040 and the API has (hopefully) stabilized. Working on docs and tutorials now.
 - 2024-10-06 - The main branch has been updated to v020, which is what I've been using for personal projects all year. It changes a _lot_ of stuff compared to v010 and previous, and the documentation and tutorials are currently outdated.

## Installation

``` bash
user@host:~$ wget https://raw.githubusercontent.com/aappleby/hancho/main/hancho.py
user@host:~$ chmod +x hancho.py
user@host:~$ ./hancho.py --help
usage: hancho.py [-h] [-f ROOT_FILE] [-C ROOT_DIR] [-v] [-d] [--force] [--trace] [-j JOBS] [-q] [-n] [-s]
                 [--use_color]
                 [target]

positional arguments:
  target                A regex that selects the targets to build. Defaults to all targets.

options:
  -h, --help            show this help message and exit
  -f ROOT_FILE, --root_file ROOT_FILE
                        The name of the .hancho file(s) to build
  -C ROOT_DIR, --root_dir ROOT_DIR
                        Change directory before starting the build
  -v, --verbose         Print verbose build info
  -d, --debug           Print debugging information
  --force               Force rebuild of everything
  --trace               Trace all text expansion
  -j JOBS, --jobs JOBS  Run N jobs in parallel (default = cpu_count)
  -q, --quiet           Mute all output
  -n, --dry_run         Do not run commands
  -s, --shuffle         Shuffle task order to shake out dependency issues
  --use_color           Use color in the console output
```

## Simple Example

```python
# examples/hello_world/build.hancho

compile_cpp = hancho.Config(
    desc       = "Compiling C++ {in_src} -> {out_obj}",
    command    = "g++ -c {in_src} -o {out_obj}",
    out_obj    = "{swap_ext(in_src, '.o')}",
    in_depfile = "{swap_ext(in_src, '.d')}",
)

main_o = hancho(compile_cpp, in_src = "main.cpp")
util_o = hancho(compile_cpp, in_src = "util.cpp")

link_cpp_bin = hancho.Config(
    desc    = "Linking C++ bin {out_bin}",
    command = "g++ {in_objs} -o {out_bin}",
)

main_app = hancho(
    link_cpp_bin,
    in_objs = [main_o, util_o],
    out_bin = "hello_world",
)
```

```sh
user@host:~/hancho/examples/hello_world$ ../../hancho.py --verbose
Loading /home/user/hancho/examples/hello_world/build.hancho
Loading .hancho files took 0.000 seconds
Queueing 3 tasks took 0.000 seconds
Reason: Rebuilding because /home/user/hancho/examples/hello_world/build/main.o is missing
[1/3] Compiling C++ /home/user/hancho/examples/hello_world/main.cpp -> /home/user/hancho/examples/hello_world/build/main.o
.$ g++ -c /home/user/hancho/examples/hello_world/main.cpp -o /home/user/hancho/examples/hello_world/build/main.o
Reason: Rebuilding because /home/user/hancho/examples/hello_world/build/util.o is missing
[2/3] Compiling C++ /home/user/hancho/examples/hello_world/util.cpp -> /home/user/hancho/examples/hello_world/build/util.o
.$ g++ -c /home/user/hancho/examples/hello_world/util.cpp -o /home/user/hancho/examples/hello_world/build/util.o
[2/3] Task passed - 'Compiling C++ /home/user/hancho/examples/hello_world/util.cpp -> /home/user/hancho/examples/hello_world/build/util.o'
[1/3] Task passed - 'Compiling C++ /home/user/hancho/examples/hello_world/main.cpp -> /home/user/hancho/examples/hello_world/build/main.o'
Reason: Rebuilding because /home/user/hancho/examples/hello_world/build/hello_world is missing
[3/3] Linking C++ bin /home/user/hancho/examples/hello_world/build/hello_world
.$ g++ /home/user/hancho/examples/hello_world/build/main.o /home/user/hancho/examples/hello_world/build/util.o -o /home/user/hancho/examples/hello_world/build/hello_world
[3/3] Task passed - 'Linking C++ bin /home/user/hancho/examples/hello_world/build/hello_world'
Running 3 tasks took 0.043 seconds
tasks started:   3
tasks finished:  3
tasks failed:    0
tasks skipped:   0
tasks cancelled: 0
tasks broken:    0
mtime calls:     0
hancho: BUILD PASSED
```

## Old Updates
 - 2024-03-28 - The v010 branch now has visualization of template and macro expansion which you can enable via ```--debug_expansion```
 - 2024-03-28 - WIP tutorial for the redesigned Hancho is in the v010 branch here - https://github.com/aappleby/hancho/tree/v010/docs/tutorial
 - 2024-03-22
   - I'm working on a v0.1.0 branch that will rework the way paths/files/directories and template expansion works.
   - The current setup is fine for my personal projects, but I've gotten feedback that it's unintuitive for other use cases - for example, moving a Rule invocation from top-level into a function and then calling that function from another file can change how file paths are interpreted.
   - Similarly, template expansion is currently order-dependent in a few cases - expanding {"a": {"print(b)"}, "b": "{c}", "c": "foo"} can print either "{c}" or "foo" depending on whether "a" or "b" are expanded first.
   - The revised version will fix both those issues but will probably break some existing builds, hence the version bump.
 - 2024-03-19 - Hancho v0.0.5
   - Special dir-related fields are now start_dir, root_dir, leaf_dir, work_dir, and build_dir
   - Hancho files in a submodule can be loaded via load(root="submodule/path", file="build.hancho")
   - Each Hancho module now gets its own 'config' object extended from its parent module (or global_config). This prevents submodules from accidentally changing global fields that their parent modules use while still allowing sharing of configuration across files.
 - 2024-03-13 - Tasks can now 'reserve' jobs so that commands that themselves use many jobs (like Ninja) can block until the jobs are free. See the [job_count](tests/job_count.hancho) test for details.
 - 2024-03-13 - Code cleaned up to be more standard Python style and reduce linter complaints. Added 'rule_dir' field to each Rule that stores the directory of the file that created the rule.
 - 2024-03-12 - Handling of paths is more flexible now (and will be documented shortly). Calling a Rule now returns a Task object. All the task-running code is now in Task instead of Rule.
 - 2024-03-07 - Tests should run on Windows now. Added a Windows build example. Promises are now valid as inputs to any template.
 - 2024-03-04 - Cleaned up pylint & formatting issues in hancho.py and test.py. Hancho.py is now over 500 lines if you include whitespace and comments :D.
 - 2024-03-04 - Unrecognized '--key=value' command line flags are now merged into the global config object. This allows you to do things like "hancho.py --build_dir=some/other/dir" which could be annoying otherwise.
 - 2024-03-02 - Initial release. Some test cases yet to be written.
