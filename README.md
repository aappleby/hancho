# ![Logo](assets/hancho_small.png) Hancho v0.4.0

"班長, hanchō - "Squad leader”, from 19th c. Mandarin 班長 (bānzhǎng, “team leader”)"

Hancho is a simple, pleasant build system that fits in a single Python file with no dependencies and no installation required - copy it into your repo and you're ready to go.

Hancho combines Ninja's speed and simplicity with Bazel's expressive Python-like syntax. Like Ninja, it focuses purely on executing your build commands as fast as possible. Like Bazel, it makes build scripts easy to write and extend. Unlike Bazel, it's lightweight (no 200+ meg installation) and lets you use arbitrary Python code in your build scripts.

Hancho should suffice for small to medium sized projects.

## Getting Started

Grab a copy of ```hancho.py``` and put it somewhere in your path. That's it.

``` bash
user@host:~$ wget https://raw.githubusercontent.com/user/hancho/main/hancho.py
user@host:~$ chmod +x hancho.py
user@host:~$ ./hancho.py --help
usage: hancho.py [-h] [-f ROOT_FILE] [-C ROOT_DIR] [-v] [-d] [--force] [--trace] [-j JOBS] [-q] [-n] [-s]
                 [--use_color]
                 [target]
<snip>
```

## Example usage

```py
# Hancho is a Python-native build system that lets you write build scripts
# using regular Python code. Build scripts use a global 'hancho' object to
# create build configurations and start build tasks.

# In this example, the 'compile_cpp' object below tells Hancho how to compile
# C++ source code.

# Hancho templates use {brackets} like Python f-strings with a few differences:
#   - Templates are lazily-evaluated
#   - Templates can only reference fields in a Config object
#   - Templates can use built-in functions like ext() for common filename
#     operations

# Config fields named 'in_*' and 'out_*' are special - they define the input
# and output filenames for a task. Hancho uses these fields to track
# dependencies between tasks.

compile_cpp = hancho.Config(
    desc = "Compiling C++ {in_src} -> {out_obj}",
    command = "g++ -c {in_src} -o {out_obj}",
    out_obj = "{ext(in_src, '.o')}",
)

# To make Hancho do some work, we pass configs and key-value pairs to hancho().
# It merges configs, expands templates, and queues an asynchronous task to run
# the command.

# The hancho() function returns a Task object, which is like a promise that
# resolves to a list of output files when the task is complete.

main_o = hancho(compile_cpp, in_src = "main.cpp")
util_o = hancho(compile_cpp, in_src = "util.cpp")

# This config object defines how to link objects into a binary file. Instead of
# passing filenames to 'in_objs', we can provide the task objects created above.
# Using a task object in place of a filename creates a dependency. Hancho uses
# these dependencies to build a task graph and schedule parallel task execution.

link_cpp_bin = hancho.Config(
    desc = "Linking C++ bin {out_bin}",
    command = "g++ {in_objs} -o {out_bin}",
)

# Hancho will automatically parallelize independent tasks. Here, main.cpp and
# util.cpp will be compiled in parallel before the link task starts.

main_app = hancho(
    link_cpp_bin,
    in_objs = [main_o, util_o],
    out_bin = "hello_world",
)

# To run a build script, save it as 'build.hancho' and run 'hancho.py' in the
# same directory.
```

More documentation (still a work in progress) can be found at [docs/README.md](docs/README.md). The currently-broken step-by-step tutorial is in [tutorial](tutorial). Working examples are in [examples](examples). There are also sample build rules for [C++](base_rules.hancho), [WASM](wasm_rules.hancho), and [FPGA synthesis](fpga_rules.hancho).

## Updates
 - 2024-11-03 - I'm stripping out obsolete documentation and trimming the tutorials down to the essentials.
 - 2024-11-02 - We're now on version v040 and the API has (hopefully) stabilized. Working on docs and tutorials now.
