### Tutorial 0: Running Hancho
---

To start the tutorial, clone the Hancho repo and cd into hancho/tutorial:

```shell
user@host:~$ git clone https://github.com/aappleby/hancho
Cloning into 'hancho'...
<snip>

user@host:~$ cd hancho/tutorial
user@host:~/hancho/tutorial$
```

Inside the tutorial folder there's a ```src``` folder with a trivial "Hello
World" application consisting of two files, ```main.cpp``` and ```util.cpp```:

``` cpp
// src/main.cpp
#include "main.hpp"
#include "util.hpp"
#include <stdio.h>

int main(int argc, char** argv) {
  printf("Hello World %d\n", get_value());
  return 0;
}
```
```cpp
// src/util.cpp
#include <stdint.h>

int32_t get_value() {
  return 42;
}
```

Assuming we have GCC installed, compiling it from the command line is
straightforward:

```shell
user@host:~/hancho/tutorial$ mkdir -p build
user@host:~/hancho/tutorial$ g++ src/main.cpp src/util.cpp -o build/app
user@host:~/hancho/tutorial$ build/app
Hello World 42
```

Here's how we run the same command using Hancho. First, we create ```build.hancho``` in the tutorial directory:

```py
hancho(
  command = [
    "mkdir -p build",
    "g++ src/main.cpp src/util.cpp -o build/app"
  ]
)
```

Hancho build files are just Python code in a file ending in .hancho, with a few minor differences. Hancho build files always have access to a global ```hancho``` object, which we can also call as if it's a function to tell Hancho to do some work. The absolute minimum we can pass to ```hancho()``` is just the command to run.


```shell
user@host:~/hancho/tutorial$ ../hancho.py
Loading /home/aappleby/repos/hancho/tutorial/build.hancho
Loading .hancho files took 0.000 seconds
Queueing 1 tasks took 0.000 seconds
[1/1] mkdir -p build g++ src/main.cpp src/util.cpp -o build/app
Running 1 tasks took 0.054 seconds
hancho: BUILD PASSED
```

Of course we don't actually want to hardcode the file names into the command, so let's use Hancho's text templates to fix that. Templates in Hancho work almost identically to Python F-strings, except that they're lazily-evaluated and can only read variables from the ```hancho()``` invocation they're in.

In addition, parameters named ```in_*``` or ```out_*``` are special - strings inside them are assumed to be filenames, and Hancho will check for changes to any ```in_``` file before deciding to re-run the command.

```py
hancho(
  command = "g++ {in_src} -o {out_bin}",
  in_src  = ["src/main.cpp", "src/util.cpp"],
  out_bin = "app",
)
```


Strings in Hancho tasks use Python-style f-string syntax, minus the 'f' prefix. The ```{}``` blocks can contain arbitrary Python expressions, with the limitation that the expressions can only refer to other fields inside the task or to Hancho's built-in functions (see documentation at ***FIXME***).

Let's see what's inside our task:

```py
# tutorial/tut00.hancho

task = hancho(
  desc    = "Compile {in_src} -> {out_bin}",
  command = "g++ {in_src} -o {out_bin}",
  in_src  = ["src/main.cpp", "src/util.cpp"],
  out_bin = "app",
)

print(task)
```

There's quite a lot of stuff in there:
```shell
aappleby@Neurotron:~/repos/hancho/tutorial$ ../hancho.py -f tut00.hancho -v
Loading /home/aappleby/repos/hancho/tutorial/tut00.hancho
Task @ 0x727f0371d6a0 {
  root_dir = "/home/aappleby/repos/hancho/tutorial",
  root_path = "/home/aappleby/repos/hancho/tutorial/tut00.hancho",
  repo_name = "",
  repo_dir = "/home/aappleby/repos/hancho/tutorial",
  build_root = "{root_dir}/build",
  build_tag = "",
  mod_name = "tut00",
  mod_dir = "/home/aappleby/repos/hancho/tutorial",
  mod_path = "/home/aappleby/repos/hancho/tutorial/tut00.hancho",
  desc = "Compile {in_src} -> {out_bin}",
  command = "g++ {in_src} -o {out_bin}",
  in_src = [
    "src/main.cpp",
    "src/util.cpp",
  ],
  out_bin = "app",
  task_dir = "{mod_dir}",
  build_dir = "{build_root}/{build_tag}/{repo_name}/{rel_path(task_dir, repo_dir)}",
  _task_index = 0,
  _in_files = [],
  _out_files = [],
  _state = 0,
  _reason = None,
  _asyncio_task = None,
  _loaded_files = [
    "/home/aappleby/repos/hancho/tutorial/tut00.hancho",
  ],
  _stdout = "",
  _stderr = "",
  _returncode = -1,
}
```
At the top you can see the global paths that Hancho uses internally, followed by the arguments we passed to ```hancho()```, followed by the task-specific ```task_dir``` and ```build_dir```, and finally some private Hancho bookkeeping fields.


In this build file we define a ```Rule``` that contains a
```command``` with two template variables ```in_*``` and ```out_*```,
and then we call the rule and give it our source files and our output filename.

Hancho then does the fill-in-the-blanks for us and runs the command, which we
can see with the ```-v``` (verbosity) flag:

```shell
user@host:~/hancho/tutorial$ rm -rf build
user@host:~/hancho/tutorial$ ../hancho.py -f tut00.hancho -v
Loading /home/user/hancho/tutorial/tut00.hancho
Loading .hancho files took 0.000 seconds
[1/1] Compile /home/user/hancho/tutorial/src/main.cpp /home/user/hancho/tutorial/src/util.cpp -> /home/user/hancho/tutorial/build/app
Reason: Rebuilding because /home/user/hancho/tutorial/build/app is missing
.$ g++ /home/user/hancho/tutorial/src/main.cpp /home/user/hancho/tutorial/src/util.cpp -o /home/user/hancho/tutorial/build/app
[1/1] Task passed - 'Compile /home/user/hancho/tutorial/src/main.cpp /home/user/hancho/tutorial/src/util.cpp -> /home/user/hancho/tutorial/build/app'

user@host:~/hancho/tutorial$ build/app
Hello World 42
```

If we run Hancho a second time, nothing will happen because nothing in
```in_*``` has changed.

```shell
aappleby@Neurotron:~/repos/hancho/tutorial$ ../hancho.py -f tut00.hancho -v
Loading /home/aappleby/repos/hancho/tutorial/tut00.hancho
Loading .hancho files took 0.000 seconds
```

If we change a source file and run Hancho again, it will do a rebuild.

```shell
aappleby@Neurotron:~/repos/hancho/tutorial$ touch src/main.cpp
aappleby@Neurotron:~/repos/hancho/tutorial$ ../hancho.py -f tut00.hancho -v
Loading /home/aappleby/repos/hancho/tutorial/tut00.hancho
Loading .hancho files took 0.000 seconds
[1/1] Compile /home/aappleby/repos/hancho/tutorial/src/main.cpp /home/aappleby/repos/hancho/tutorial/src/util.cpp -> /home/aappleby/repos/hancho/tutorial/build/app
Reason: Rebuilding because /home/aappleby/repos/hancho/tutorial/src/main.cpp has changed
.$ g++ /home/aappleby/repos/hancho/tutorial/src/main.cpp /home/aappleby/repos/hancho/tutorial/src/util.cpp -o /home/aappleby/repos/hancho/tutorial/build/app
[1/1] Task passed - 'Compile /home/aappleby/repos/hancho/tutorial/src/main.cpp /home/aappleby/repos/hancho/tutorial/src/util.cpp -> /home/aappleby/repos/hancho/tutorial/build/app'
```

The above example is not a particularly useful way to use Hancho, but it should
check that your installation is working.
