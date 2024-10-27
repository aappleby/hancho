







---
### Tutorial 1: Compiling a C binary

Now to build the same C binary the right-er way. Instead of running a single
command to do both the compile and link steps, we can split that up into one
compile command per source file and one final link command.

We'll also add a description to each rule so we get a bit nicer feedback when
running a build.

``` py
# tutorial/tut01.hancho

main_o = hancho(
  desc    = "Compile {in_src} -> {out_obj}",
  command = "g++ -MMD -c {in_src} -o {out_obj}",
  in_src  = "src/main.cpp",
  out_obj = "main.o",
  c_deps  = "main.d",
)

util_o = hancho(
  desc    = "Compile {in_src} -> {out_obj}",
  command = "g++ -MMD -c {in_src} -o {out_obj}",
  in_src  = "src/util.cpp",
  out_obj = "util.o",
  c_deps  = "util.d",
)

app = hancho(
  desc    = "Link {in_objs} -> {out_bin}",
  command = "g++ {in_objs} -o {out_bin}",
  in_objs = [main_o, util_o],
  out_bin = "app",
)
```

If we run that, we'll see three commands instead of just one:

```shell
user@host:~/hancho/tutorial$ ../hancho.py tut1.hancho --verbose
[1/3] Compile src/main.cpp -> build/tut1/src/main.o
Reason: Rebuilding ['build/tut1/src/main.o'] because some are missing
g++ -c src/main.cpp -o build/tut1/src/main.o
[2/3] Compile src/util.cpp -> build/tut1/src/util.o
Reason: Rebuilding ['build/tut1/src/util.o'] because some are missing
g++ -c src/util.cpp -o build/tut1/src/util.o
[3/3] Link build/tut1/src/main.o build/tut1/src/util.o -> build/tut1/app
Reason: Rebuilding ['build/tut1/app'] because some are missing
g++ build/tut1/src/main.o build/tut1/src/util.o -o build/tut1/app
```

And if we modify the source file ```utils.cpp```, we should see that
```utils.cpp``` is recompiled and ```build/tut1/app``` is relinked, but
```main.cpp``` is _not_ recompiled:

```shell
user@host:~/hancho/tutorial$ touch src/util.cpp

user@host:~/hancho/tutorial$ ../hancho.py tut1.hancho --verbose
[1/2] Compile src/util.cpp -> build/tut1/src/util.o
Reason: Rebuilding ['build/tut1/src/util.o'] because an input has changed
g++ -c src/util.cpp -o build/tut1/src/util.o
[2/2] Link build/tut1/src/main.o build/tut1/src/util.o -> build/tut1/app
Reason: Rebuilding ['build/tut1/app'] because an input has changed
g++ build/tut1/src/main.o build/tut1/src/util.o -o build/tut1/app
```

However, if we modify the header file ```util.hpp``` the build is ***not***
updated, as Hancho is only checking the dependencies declared by ```in_*```
and ```out_*```. We'll fix that in a minute.

```shell
user@host:~/hancho/tutorial$ touch src/util.hpp

user@host:~/hancho/tutorial$ ../hancho.py tut1.hancho --verbose
hancho: no work to do.
```

So what exactly are ```main_o``` and ```util_o```? They are ```hancho.Task``` objects that contains ***promises***
(well, technically they are ```asyncio.Task```s) which will resolve to either a list of filenames that the rule generated, or None if the rule failed for some
reason.

Before Hancho starts a task, it will ```await``` all promises in the task before
expanding all the  ```{template_strings}``` and then starting an ```asyncio.Task```.

Hancho will also skip running a rule if everything in the rule's ```out_*```
is newer than the rule's ```in_*```.

You might have noticed that we seem to be inconsistent about whether
```in_*``` and ```out_*``` are single strings, arrays of strings,
nested arrays of promises, or whatnot. Hancho doesn't actually care - it will
```await``` anything that needs awaiting and will ```flatten()``` nested lists or
wrap single strings in ```[]```s as needed. By the time the rule runs,
everything will be a flat array of strings. Using that array in a
```{template}``` will do the equivalent of ```' '.join(array)```.

At this point our build works, but we're still missing some steps we need to use
this for real: we need to generate and handle GCC's dependency files so we can
catch modified header files, and we need a better way to define our build
directory - hardcoding it in ```out_*``` isn't going to work for larger
projects.







---
### Aside: Hancho ```Rule``` objects

Before we go into more detail with our toy app, we should probably explain what
exactly a ```Rule``` is.

Hancho's ```Rule``` is basically just a Python ```dict``` with a text templating
system tacked on.

We can put whatever key-value pairs we like inside a ```Rule``` and then use it
to expand whatever text template we like:
```py
>>> from hancho import config, Rule
>>> rule = Rule(foo = "{bar}", bar = "{baz}", baz = "Hancho")
>>> expand("One Hancho: {foo}", rule)
'One Hancho: Hancho'
```

Basic expressions work inside templates as well:
```py
>>> rule = Rule(foo = "{bar*3}", bar = "{baz*3}", baz = "Hancho")
>>> expand("Nine Hanchos: {foo}", rule)
'Nine Hanchos: HanchoHanchoHanchoHanchoHanchoHanchoHanchoHanchoHancho'
```

Rule fields can also be functions or lambdas:
```py
>>> rule = Rule(foo = lambda x: "Hancho" * x)
>>> expand("{foo(4)}", rule)
'HanchoHanchoHanchoHancho'
```

but note that you do _not_ have access to any Python globals or builtins,

```py
>>> rule = Rule(foo = "{print(4)}")
>>> expand("{foo}", rule)
{print(4)}
Expanding '{print(4)}' is stuck in a loop
```

...unless you put them somewhere the rule has access to:

```py
>>> rule = Rule(foo = "{print(4)}", print = print)
>>> expand("{foo}", rule)
4
''
```

Arbitrarily-nested arrays of strings will be flattened out and joined with
spaces:
```py
>>> rule = Rule(foo = [1,2,3,[4,5],[[[6,7]]]])
>>> expand("{foo}", rule)
'1 2 3 4 5 6 7'
```

Fields that are never defined will turn into empty strings:
```py
>>> rule = Rule(foo = "{missing}")
>>> expand("?{foo}?", rule)
'??'
```

Fields that are used globally in multiple rules can be set on
```config```, which will make them visible in _every_ rule:
```py
>>> config.bar = "Hancho"
>>> rule = Rule(foo = "{bar}")
>>> expand("{foo}", rule)
'Hancho'
```

Rules can also 'inherit' fields from other rules via ```rule.fork()```, which
is a better option for common fields that shouldn't be globally visible:

```py
>>> base_rule = Rule(bar = "Hancho")
>>> rule = base_rule.rule(foo = "{bar}")
>>> expand("{foo}", rule)
'Hancho'
```

Text templates that cause infinite loops will fail:
```py
>>> rule = Rule(foo = "{bar}", bar = "{foo}")
>>> expand("{foo}", rule)
Expanding '{foo}...' failed to terminate
```

as will templates that create infinitely-long strings:
```py
>>> rule = Rule(foo = "!{foo}!")
>>> expand("{foo}", rule)
Expanding '!!!!!!!!!!!!!!!!!!!!...' failed to terminate
```






---
### Tutorial 2: Global configuration & calling builtin functions

Now that ```Rule``` is a bit less mysterious, we can use its powers to improve
our build further.

First things first - we want all our build output for each tutorial to go into a
separate directory so the output of each tutorial doesn't collide and we don't have
to specify it in every ```out_*```. Hancho defines a special rule field
 ```build_dir``` that is prepended to all output filenames if present and
 defaults to ```build```. To specify a custom ```build_dir``` for all rules in
 our build, we can set it on the global ```config``` object.

Next up, dependency files. If we pass GCC the ```-MMD``` flag, it will produce a
dependency file ```main.d``` alongside the compiled ```main.o``` that contains a
list of all the header files ```main.cpp``` depends on. We can use this in
Hancho to ensure that our source files are recompiled whenever a header file
they depend on changes. Like ```build_dir```, the special rule field
```c_deps``` accepts the name of the generated dependency file. If the dependency file exists
during the build, Hancho will use its contents when deciding if a rule
needs to be rebuilt.

It would be nice if we didn't have to specify ```out_*``` and ```c_deps```
every time we call ```compile```. To do that, we can use the ```swap_ext```
builtin to generically define ```out_*``` and ```c_deps``` in the
```compile``` rule. Then we don't need to specify them at all when calling
```compile```.


```py
# tutorial/tut2.hancho
from hancho import *

config.build_dir = "build/tut2"

compile = Rule(
  desc      = "Compile {in_*} -> {out_*}",
  command   = "g++ -MMD -c {in_*} -o {out_*}",
  out_* = "{swap_ext(in_*, '.o')}",
  c_deps   = "{swap_ext(out_*, '.d')}",
)

link = Rule(
  desc      = "Link {in_*} -> {out_*}",
  command   = "g++ {in_*} -o {out_*}",
)

main_o = compile("src/main.cpp")
util_o = compile("src/util.cpp")
link([main_o, util_o], "app")
```

The build still works as expected

```shell
user@host:~/hancho/tutorial$ ../hancho.py tut2.hancho --verbose
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
user@host:~/hancho/tutorial$ ../hancho.py tut2.hancho --verbose
hancho: no work to do.
```

but now modifying a header file _does_ cause a rebuild:

```shell
user@host:~/hancho/tutorial$ ../hancho.py tut2.hancho --verbose
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
hancho.build_dir = "build/tut3"

hancho.load("src/src.hancho")
```

That's because the actual build has moved to ```src/src.hancho``` so it can live alongside its source code.


```py
# tutorial/src/src.hancho
import glob

rules = hancho.load("base_rules.hancho")

hancho(rules.c_binary, in_srcs = glob.glob("*.cpp"), out_bin = "app")
```

Some things to note here -

1. We've moved the actual build rules to ```rules.hancho```
2. We're compiling all .cpp files in src/ by passing the result of
  ```glob.glob("*.cpp")``` to the ```c_binary``` function we got from
  ```rules.hancho```.
3. We're globbing ```*.cpp``` in the ```tutorial/src``` directory, ***not***
  the ```tutorial``` directory. This is ***not*** how Python module loading
  works by default - Hancho ```chdir()```s into the build script's directory
  before running it so that file matching patterns like this work regardless of
  where Hancho was launched from.

But what is ```rules.c_binary```? It's a helper function in ```rules.hancho```:

```py
# tutorial/rules.hancho
from hancho import *

compile = Rule(
  desc      = "Compile {in_*} -> {out_*}",
  command   = "g++ -MMD -c {in_*} -o {out_*}",
  out_* = "{swap_ext(in_*, '.o')}",
  c_deps = "{swap_ext(out_*, '.d')}",
)

link = Rule(
  desc      = "Link {in_*} -> {out_*}",
  command   = "g++ {in_*} -o {out_*}",
)

def c_binary(in_*, out_*, **kwargs):
  objs = [compile(file, **kwargs) for file in in_*]
  return link(objs, out_*, **kwargs)
```

So overall we have the same rules and commands as before, but now they're split
up into
1. A top-level build file ```tut3.hancho``` that loads build files for its
  sub-components
2. A ```src.hancho``` sub-component build file that actually compiles stuff
3. A ```rules.hancho``` file that contains our reusable rules and helper
  functions

This basic setup (top-level build, component-level build, and rules file) works
well for most small to medium size projects.





---
### Tutorial 4 - Async functions and custom commands

Hancho's use of promises as part of its dependency graph means there are a few
odd things you can do that aren't easily specified in other build systems.

For example, you can call asynchronous functions and pass their return values to
```in_*```:

```py
# tutorial/tut4.hancho - Async/await and custom commands
from hancho import *
import asyncio
import os

async def do_slow_thing():
  print("Doing slow thing")
  await asyncio.sleep(0.1)
  print("Slow thing done")
  return ["src/main.cpp"]

echo = Rule(
  desc = "Consuming a promise as in_*",
  command = "echo {in_*}",
)
echo(do_slow_thing(), [])
```

You can also replace ```command``` with an asynchronous function instead of a
command line to run arbitrary Python code as part of the build graph:

```py
async def custom_command(task):
  for f in task.out_*:
    print(f"Touching {f}")
    os.system(f"touch {f}")
  return task.out_*

custom_rule = Rule(
  desc    = "Custom rule: {in_*} -> {out_*}",
  command = custom_command
)

custom_rule("src/main.cpp", ["tut4/custom1", "tut4/custom2"])
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
expand "{in_*} -> {out_*}"
expand "src/main.cpp src/util.cpp -> build/tut0/app"
[1/1] src/main.cpp src/util.cpp -> build/tut0/app
Reason: Rebuilding ['build/tut0/app'] because some are missing
expand "g++ {in_*} -o {out_*}"
expand "g++ src/main.cpp src/util.cpp -o build/tut0/app"
g++ src/main.cpp src/util.cpp -o build/tut0/app
{
  "base": {
    "command": "g++ {in_*} -o {out_*}",
    "base": {
      "jobs": 16,
      "verbose": false,
      "quiet": false,
      "dryrun": false,
      "debug": true,
      "force": false,
      "desc": "{in_*} -> {out_*}",
      "out_*": [],
      "expand": "<function>",
      "join": "<function>",
      "len": "<function>",
      "run_cmd": "<function>",
      "swap_ext": "<function>",
      "color": "<function>",
      "base": null
    }
  },
  "in_*": [
    "src/main.cpp",
    "src/util.cpp"
  ],
  "out_*": [
    "build/tut0/app"
  ],
  "meta_deps": [
    "/home/aappleby/hancho/tutorial/tut0.hancho"
  ],
  "cwd": "/home/aappleby/hancho/tutorial",
  "deps": [],
  "abs_in_*": [
    "/home/aappleby/hancho/tutorial/src/main.cpp",
    "/home/aappleby/hancho/tutorial/src/util.cpp"
  ],
  "abs_out_*": [
    "/home/aappleby/hancho/tutorial/build/tut0/app"
  ],
  "abs_deps": [],
  "reason": "Rebuilding ['build/tut0/app'] because some are missing"
}
expand "g++ {in_*} -o {out_*}"
expand "g++ src/main.cpp src/util.cpp -o build/tut0/app"
Files ['build/tut0/app'] are up to date
tasks total:   1
tasks skipped: 0
tasks passed:  1
tasks failed:  0
```
