### Tutorial 0: Running Hancho
---

Hancho is distributed as a single Python file with no dependencies, just download it to your working directory:

``` bash
wget https://raw.githubusercontent.com/aappleby/hancho/main/hancho.py
chmod +x hancho.py
./hancho.py
```

To start the tutorial, clone the Hancho repo and cd into hancho/tutorial:

```shell
user@host:~$ git clone https://github.com/aappleby/hancho
Cloning into 'hancho'...
<snip>

user@host:~$ cd hancho/tutorial
user@host:~/hancho/tutorial$
```

Inside the tutorial folder there's a ```src``` folder with a trivial "Hello World" application consisting of two files, ```main.cpp``` and ```util.cpp```:

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

Assuming we have GCC installed, compiling it from the command line is straightforward:

```shell
user@host:~/hancho/tutorial$ mkdir -p build/tut0
user@host:~/hancho/tutorial$ g++ src/main.cpp src/util.cpp -o build/tut0/app
user@host:~/hancho/tutorial$ build/tut0/app
Hello World 42
```

Here's how we run the same command in Hancho:

```py
# tutorial/tut0.hancho
import hancho

rule = hancho.Rule(
  command = "g++ {files_in} -o {files_out}",
)

rule(
  files_in = ["src/main.cpp", "src/util.cpp"],
  files_out = "build/tut0/app"
)

# The files_in and files_out keywords are optional: this is also valid
# rule(["src/main.cpp", "src/util.cpp"], "build/tut0/app")
```

Hancho build files are just Python modules ending in .hancho, with minor modifications. In this build file we define a ```Rule``` that contains a ```command``` with two template variables ```files_in``` and ```files_out```, and then we call the rule and give it our source files and our output filename. Hancho then does the fill-in-the-blanks for us and runs the command, which we can see with the ```--verbose``` flag:

```shell
user@host:~/hancho/tutorial$ rm -rf build
user@host:~/hancho/tutorial$ hancho tut0.hancho --verbose
[1/1] src/main.cpp src/util.cpp -> build/tut0/app
Reason: Rebuilding ['build/tut0/app'] because some are missing
g++ src/main.cpp src/util.cpp -o build/tut0/app
user@host:~/hancho/tutorial$ build/tut0/app
Hello World 42
```



If we run Hancho a second time, nothing will happen because nothing in ```files_in``` has changed.

```shell
user@host:~/hancho/tutorial$ hancho tut0.hancho --verbose
hancho: no work to do.
```

If we change a source file and run Hancho again, it will do a rebuild.

```shell
user@host:~/hancho/tutorial$ touch src/main.cpp
user@host:~/hancho/tutorial$ hancho tut0.hancho --verbose
[1/1] src/main.cpp src/util.cpp -> build/tut0/app
Reason: Rebuilding ['build/tut0/app'] because an input has changed
g++ src/main.cpp src/util.cpp -o build/tut0/app
```

The above example is not a particularly useful way to use Hancho, but it should check that your installation is working.

---
### Tutorial 1: Compiling a C binary


Now to build the same C binary the right-er way. Instead of running a single command to do both the compile and link steps, we can split that up into one compile command per source file and one final link command. We'll also add a description to each rule so we get a bit nicer feedback when running a build.

``` py
# tutorial/tut1.hancho
import hancho

compile = hancho.Rule(
  desc = "Compile {files_in} -> {files_out}",
  command = "g++ -c {files_in} -o {files_out}",
)

link = hancho.Rule(
  desc = "Link {files_in} -> {files_out}",
  command = "g++ {files_in} -o {files_out}",
)

main_o = compile("src/main.cpp", "build/tut1/src/main.o")
util_o = compile("src/util.cpp", "build/tut1/src/util.o")
link([main_o, util_o], "build/tut1/app")
```

If we run that, we'll see three commands instead of just one:

```shell
user@host:~/hancho/tutorial$ hancho tut1.hancho --verbose
[1/3] Compile src/main.cpp -> build/tut1/src/main.o
Reason: Rebuilding ['build/tut1/src/main.o'] because some are missing
g++ -c src/main.cpp -o build/tut1/src/main.o
[2/3] Compile src/util.cpp -> build/tut1/src/util.o
Reason: Rebuilding ['build/tut1/src/util.o'] because some are missing
g++ -c src/util.cpp -o build/tut1/src/util.o
[3/3] Link build/tut1/src/main.o build/tut1/src/util.o -> build/tut1/app
Reason: Rebuilding ['build/tut1/app'] because some are missing
g++ build/tut1/src/main.o build/tut1/src/util.o -o build/tut1/app
user@host:~/hancho/tutorial$
```

And if we modify the source file ```utils.cpp```, we should see that ```utils.cpp``` is recompiled and ```build/tut1/app``` is relinked, but ```main.cpp``` is _not_ recompiled:
```shell
user@host:~/hancho/tutorial$ touch src/util.cpp
user@host:~/hancho/tutorial$ hancho tut1.hancho --verbose
[1/2] Compile src/util.cpp -> build/tut1/src/util.o
Reason: Rebuilding ['build/tut1/src/util.o'] because an input has changed
g++ -c src/util.cpp -o build/tut1/src/util.o
[2/2] Link build/tut1/src/main.o build/tut1/src/util.o -> build/tut1/app
Reason: Rebuilding ['build/tut1/app'] because an input has changed
g++ build/tut1/src/main.o build/tut1/src/util.o -o build/tut1/app
user@host:~/hancho/tutorial$
```

However, if we modify the header file ```util.hpp``` the build is ***not*** updated, as Hancho is only checking the dependencies declared by ```files_in``` and ```files_out```. We'll fix that in a minute.

```
user@host:~/hancho/tutorial$ touch src/util.hpp
user@host:~/hancho/tutorial$ hancho tut1.hancho --verbose
hancho: no work to do.
```

So what exactly are ```main_o``` and ```util_o```? They are ***promises*** (well, technically they are ```asyncio.Task```s) that resolve to either a list of filenames that the rule generated, or None if the rule failed for some reason. Hancho will ```await``` all promises that are passed to ```files_in``` before running the rule. Hancho will also skip running a rule if everything in the rule's ```files_out``` is newer than the rule's ```files_in```.

You might have noticed that we seem to be inconsistent about whether ```files_in``` and ```files_out``` are single strings, arrays of strings, nested arrays of promises, or whatnot. Hancho doesn't actually care - it will ```await``` anything that needs awaiting and will flatten out nested lists or wrap single strings in ```[]```s as needed. By the time the rule runs everything will be a flat array of strings. Using that array in a ```{template}``` will do the equivalent of ```' '.join(array)```.

This works, but we're still missing some steps: we need to generate and handle GCC's dependency files so we can catch modified header files, and we need a better way to define our build directory - hardcoding it in ```files_out``` isn't going to work for larger projects.







































---
### Aside: Hancho ```Rule``` objects

Before we go into more detail with our toy app, we should probably explain what exactly a ```Rule``` is.

Hancho's ```Rule``` is basically just a Python ```dict``` with a text templating system tacked on.

We can put whatever key-value pairs we like inside a ```Rule``` and then use it to expand whatever text template we like:
```py
>>> import hancho
>>> rule = hancho.Rule(foo = "{bar}", bar = "{baz}", baz = "Hancho")
>>> rule.expand("One Hancho: {foo}")
'One Hancho: Hancho'
```

Basic expressions work inside templates as well:
```py
>>> rule = hancho.Rule(foo = "{bar*3}", bar = "{baz*3}", baz = "Hancho")
>>> rule.expand("Nine Hanchos: {foo}")
'Nine Hanchos: HanchoHanchoHanchoHanchoHanchoHanchoHanchoHanchoHancho'
```

Rule fields can also be functions or lambdas:
```py
>>> rule = hancho.Rule(foo = lambda x: "Hancho" * x)
>>> rule.expand("{foo(4)}")
'HanchoHanchoHanchoHancho'
```

but note that you do _not_ have access to any Python globals or builtins,

```py
>>> rule = hancho.Rule(foo = "{print(4)}")
>>> rule.expand("{foo}")
{print(4)}
Expanding '{print(4)}' is stuck in a loop
```

...unless you put them somewhere the rule has access to:

```py
>>> rule = hancho.Rule(foo = "{print(4)}", print = print)
>>> rule.expand("{foo}")
4
''
```

Arbitrarily-nested arrays of strings will be flattened out and joined with spaces:
```py
>>> rule = hancho.Rule(foo = [1,2,3,[4,5],[[[6,7]]]])
>>> rule.expand("{foo}")
'1 2 3 4 5 6 7'
```

Fields that are never defined will turn into empty strings:
```py
>>> rule = hancho.Rule(foo = "{missing}")
>>> rule.expand("?{foo}?")
'??'
```

Fields that are used globally in multiple rules can be set on ```hancho.config```, which will make them visible in _every_ rule:
```py
>>> hancho.config.set(bar = "Hancho")
>>> rule = hancho.Rule(foo = "{bar}")
>>> rule.expand("{foo}")
'Hancho'
```

Rules can also 'inherit' fields from other rules via ```rule.extend()```, which is a better option for common fields that shouldn't be globally visible:

```py
>>> base_rule = hancho.Rule(bar = "Hancho")
>>> rule = base_rule.extend(foo = "{bar}")
>>> rule.expand("{foo}")
'Hancho'
```

Text templates that cause infinite loops will fail:
```py
>>> rule = hancho.Rule(foo = "{bar}", bar = "{foo}")
>>> rule.expand("{foo}")
Expanding '{foo}...' failed to terminate
```

as will templates that create infinitely-long strings:
```py
>>> rule = hancho.Rule(foo = "!{foo}!")
>>> rule.expand("{foo}")
Expanding '!!!!!!!!!!!!!!!!!!!!...' failed to terminate
```












---
### Tutorial 2: Global configuration & calling builtin functions

Now that ```Rule``` is a bit less mysterious, we can use its powers to improve
our build further.

First things first - we want all our build output to go into a separate
directory so we don't clutter up our source tree and so we don't have to specify
it in every ```files_out```. Hancho defines a special rule field ```build_dir```
that is prepended to all output filenames if present. To specify ```build_dir```
for all rules in our build, we can set it on the global ```hancho.config```
object.

Next up, dependency files. If we pass GCC the ```-MMD``` flag, it will produce a
dependency file ```main.d``` alongside the compiled ```main.o``` that contains a
list of all the header files ```main.cpp``` depends on. We can use this in
Hancho to ensure that our source files are recompiled whenever a header file
they depend on changes. Like ```build_dir```, the special rule field
```depfile``` accepts the name of the generated depfile. If the depfile exists
during the build, Hancho will use its contents when deciding if a rule
needs to be rebuilt.

It would be nice if we didn't have to specify ```files_out``` and ```depfile```
every time we call ```compile```. To do that, we can use the ```swap_ext```
builtin to generically define ```files_out``` and ```depfile``` in the
```compile``` rule. Then we don't need to specify them at all when calling
```compile```.


```py
# tutorial/tut2.hancho
import hancho

hancho.config.set(build_dir = "build/tut2")

compile = hancho.Rule(
  desc      = "Compile {files_in} -> {files_out}",
  command   = "g++ -MMD -c {files_in} -o {files_out}",
  files_out = "{swap_ext(files_in, '.o')}",
  depfile   = "{swap_ext(files_in, '.d')}",
)

link = hancho.Rule(
  desc      = "Link {files_in} -> {files_out}",
  command   = "g++ {files_in} -o {files_out}",
)

main_o = compile("src/main.cpp")
util_o = compile("src/util.cpp")
link([main_o, util_o], "app")
```

The build still works as expected

```shell
user@host:~/hancho/tutorial$ hancho tut2.hancho --verbose
[1/3] Compile src/main.cpp -> build/tut2/src/main.o
Reason: Rebuilding ['build/tut2/src/main.o'] because some are missing
g++ -MMD -c src/main.cpp -o build/tut2/src/main.o
[2/3] Compile src/util.cpp -> build/tut2/src/util.o
Reason: Rebuilding ['build/tut2/src/util.o'] because some are missing
g++ -MMD -c src/util.cpp -o build/tut2/src/util.o
[3/3] Link build/tut2/src/main.o build/tut2/src/util.o -> build/tut2/app
Reason: Rebuilding ['build/tut2/app'] because some are missing
g++ build/tut2/src/main.o build/tut2/src/util.o -o build/tut2/app
```

and rerunning the build does nothing as expected

```shell
user@host:~/hancho/tutorial$ hancho tut2.hancho --verbose
hancho: no work to do.
```

but now modifying a header file _does_ cause a rebuild:

```shell
user@host:~/hancho/tutorial$ hancho tut2.hancho --verbose
[1/3] Compile src/main.cpp -> build/tut2/src/main.o
Reason: Rebuilding ['build/tut2/src/main.o'] because a dependency in build/tut2/src/main.d has changed
g++ -MMD -c src/main.cpp -o build/tut2/src/main.o
[2/2] Link build/tut2/src/main.o build/tut2/src/util.o -> build/tut2/app
Reason: Rebuilding ['build/tut2/app'] because an input has changed
g++ build/tut2/src/main.o build/tut2/src/util.o -o build/tut2/app
```

and all our output files are in ```build/tut2``` as they should be.

```shell
user@host:~/hancho/tutorial$ tree build
build
└── tut2
    ├── app
    └── src
        ├── main.d
        ├── main.o
        ├── util.d
        └── util.o

2 directories, 5 files
```


















---
### Tutorial 3 - Rule Files, Globs, and Helper Functions

In a large project, you may not want your whole Hancho build configuration in a
single .hancho file. That's fine - you can move things around pretty easily.
You'll notice that tut3.hancho is mostly empty now:

```py
# tutorial/tut3.hancho
import hancho

hancho.config.set(build_dir = "build/tut3")

hancho.load("src/src.hancho")
```

That's because the actual build has moved to ```src/src.hancho``` so it can live alongside its source code.


```py
# tutorial/src/src.hancho
import glob
import hancho

rules = hancho.load("rules.hancho")

rules.c_binary("app", glob.glob("*.cpp"))
```

Some things to note here -

1. We've moved the actual build rules to ```rules.hancho```
2. We're compiling all .cpp files in src/ by passing the result of ```glob.glob("*.cpp")``` to the ```c_binary``` function we got from ```rules.hancho```.
2. We're globbing ```*.cpp``` in the ```tutorial/src``` directory, ***not*** the ```tutorial``` directory. This is ***not*** how Python module loading works by default - Hancho ```chdir()```s into the build script's directory before running it so that file matching patterns like this work regardless of where Hancho was launched from.

But what is ```rules.c_binary```? It's a helper function in ```rules.hancho```:

```py
# tutorial/rules.hancho
import hancho
import os

compile = hancho.Rule(
  desc      = "Compile {files_in} -> {files_out}",
  command   = "g++ -MMD -c {files_in} -o {files_out}",
  files_out = "{swap_ext(files_in, '.o')}",
  depfile   = "{swap_ext(files_in, '.d')}",
)

link = hancho.Rule(
  desc      = "Link {files_in} -> {files_out}",
  command   = "g++ {files_in} -o {files_out}",
)

def c_binary(name, files):
  objs = [compile(file) for file in files]
  return link(objs, name)
```

So overall we have the same rules and commands as before, but now they're split up into
1. A top-level build file ```tut3.hancho``` that loads build files for its sub-components
2. A ```src.hancho``` sub-component build file that actually compiles stuff
3. A ```rules.hancho``` file that contains our reusable rules and helper functions

This basic setup (top-level build, component-level build, and rules file) works well for most small to medium size projects.


---
### Tutorial 4 - Async functions and custom commands

Hancho's use of promises as part of its dependency graph means there are a few
odd things you can do that aren't easily specified in other build systems.

For example, you can call asynchronous functions and pass their return values to
```files_in```:

```py
# tutorial/tut4.hancho - Async/await and custom commands
import asyncio
import hancho
import os

async def do_slow_thing():
  print("Doing slow thing")
  await asyncio.sleep(0.1)
  print("Slow thing done")
  return ["src/main.cpp"]

echo = hancho.Rule(
  desc = "Consuming a promise as files_in",
  command = "echo {files_in}",
)
echo(do_slow_thing(), [])
```

You can also replace ```command``` with an asynchronous function instead of a
command line to run arbitrary Python code as part of the build graph:

```py
async def custom_command(task):
  for f in task.files_out:
    hancho.log(f"Touching {f}")
    os.system(f"touch {f}")
  return task.files_out

custom_rule = hancho.Rule(
  desc    = "Custom rule: {files_in} -> {files_out}",
  command = custom_command
)

custom_rule("src/main.cpp", ["build/tut4/custom1", "build/tut4/custom2"])
```

Whether these features are useful or not is yet to be determined. I think they
may be helpful for integrating test frameworks into the build graph.

---
### Addendum - Debugging Hancho

The ```--debug``` flag will print very verbose internal info about the Hancho
rules. The debug dumps are very verbose but should be sufficient to track down
template problems and incorrect command lines.

The components of the debug output are:
 - "expand ..." messages for all templates
 - The description for each rule evaluated
 - The reason the rule was (or was not) executed
 - The command executed
 - A JSON representation of the rule object

```shell
user@host:~/hancho/tutorial$ rm -rf build
user@host:~/hancho/tutorial$ ../hancho.py tut0.hancho --debug
expand "None"
expand ""
expand "src/main.cpp"
expand "src/util.cpp"
expand "build/tut0/app"
expand "{files_in} -> {files_out}"
expand "src/main.cpp src/util.cpp -> build/tut0/app"
[1/1] src/main.cpp src/util.cpp -> build/tut0/app
Reason: Rebuilding ['build/tut0/app'] because some are missing
expand "g++ {files_in} -o {files_out}"
expand "g++ src/main.cpp src/util.cpp -o build/tut0/app"
g++ src/main.cpp src/util.cpp -o build/tut0/app
{
  "base": {
    "command": "g++ {files_in} -o {files_out}",
    "base": {
      "jobs": 16,
      "verbose": false,
      "quiet": false,
      "dryrun": false,
      "debug": true,
      "force": false,
      "desc": "{files_in} -> {files_out}",
      "files_out": [],
      "expand": "<function>",
      "join": "<function>",
      "len": "<function>",
      "run_cmd": "<function>",
      "swap_ext": "<function>",
      "color": "<function>",
      "base": null
    }
  },
  "files_in": [
    "src/main.cpp",
    "src/util.cpp"
  ],
  "files_out": [
    "build/tut0/app"
  ],
  "meta_deps": [
    "/home/aappleby/hancho/tutorial/tut0.hancho"
  ],
  "cwd": "/home/aappleby/hancho/tutorial",
  "deps": [],
  "abs_files_in": [
    "/home/aappleby/hancho/tutorial/src/main.cpp",
    "/home/aappleby/hancho/tutorial/src/util.cpp"
  ],
  "abs_files_out": [
    "/home/aappleby/hancho/tutorial/build/tut0/app"
  ],
  "abs_deps": [],
  "reason": "Rebuilding ['build/tut0/app'] because some are missing"
}
expand "g++ {files_in} -o {files_out}"
expand "g++ src/main.cpp src/util.cpp -o build/tut0/app"
Files ['build/tut0/app'] are up to date
tasks total:   1
tasks skipped: 0
tasks passed:  1
tasks failed:  0
```
