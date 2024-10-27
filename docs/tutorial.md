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

Here's how we run the same command in Hancho:

```py
# tutorial/tut00.hancho

hancho(
  command = "g++ src/main.cpp src/util.cpp -o build/app",
)
```

```shell
aappleby@Neurotron:~/repos/hancho/tutorial$ ../hancho.py -f tut00.hancho --verbose
Loading /home/aappleby/repos/hancho/tutorial/tut00.hancho
Loading .hancho files took 0.000 seconds
[1/1]  ->
Reason: Always rebuild a target with no inputs
.$ g++ src/main.cpp src/util.cpp -o build/app
[1/1] Task passed - ' -> '
```

The slightly odd ```' -> '``` is because Hancho tried to print ```<inputs> -> <outputs>```, but we didn't tell it what the inputs and outputs are. Let's fix that:


```py
# tutorial/tut00.hancho

hancho(
  desc    = "Compile {in_src} -> {out_bin}",
  command = "g++ {in_src} -o {out_bin}",
  in_src  = ["src/main.cpp", "src/util.cpp"],
  out_bin = "app",
)
```

Hancho build files are just Python modules ending in .hancho, with minor
modifications. One modification is that there's always a global ```hancho``` object, which we can call as if it's a function to tell Hancho to start up an asynchronous task that will do some work.

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
aappleby@Neurotron:~/repos/hancho/tutorial$ ../hancho.py -f tut00.hancho --verbose
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
  log_path = None,
  _task_index = 0,
  _in_files = [],
  _out_files = [],
  _state = 0,
  _reason = None,
  _promise = None,
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
can see with the ```--verbose``` flag:

```shell
user@host:~/hancho/tutorial$ rm -rf build
user@host:~/hancho/tutorial$ ../hancho.py -f tut00.hancho --verbose
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
aappleby@Neurotron:~/repos/hancho/tutorial$ ../hancho.py -f tut00.hancho --verbose
Loading /home/aappleby/repos/hancho/tutorial/tut00.hancho
Loading .hancho files took 0.000 seconds
```

If we change a source file and run Hancho again, it will do a rebuild.

```shell
aappleby@Neurotron:~/repos/hancho/tutorial$ touch src/main.cpp
aappleby@Neurotron:~/repos/hancho/tutorial$ ../hancho.py -f tut00.hancho --verbose
Loading /home/aappleby/repos/hancho/tutorial/tut00.hancho
Loading .hancho files took 0.000 seconds
[1/1] Compile /home/aappleby/repos/hancho/tutorial/src/main.cpp /home/aappleby/repos/hancho/tutorial/src/util.cpp -> /home/aappleby/repos/hancho/tutorial/build/app
Reason: Rebuilding because /home/aappleby/repos/hancho/tutorial/src/main.cpp has changed
.$ g++ /home/aappleby/repos/hancho/tutorial/src/main.cpp /home/aappleby/repos/hancho/tutorial/src/util.cpp -o /home/aappleby/repos/hancho/tutorial/build/app
[1/1] Task passed - 'Compile /home/aappleby/repos/hancho/tutorial/src/main.cpp /home/aappleby/repos/hancho/tutorial/src/util.cpp -> /home/aappleby/repos/hancho/tutorial/build/app'
```

The above example is not a particularly useful way to use Hancho, but it should
check that your installation is working.
