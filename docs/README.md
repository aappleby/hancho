# Hancho Quick Reference

Hancho is built out of a few simple pieces - the ```hancho``` object, Configs, Templates, and Tasks. This document is a quick overview of each of those pieces, along with a few examples of more complex usage.

For more detailed and up-to-date information, check out the examples folder and the '*_rules.hancho' files in the root directory of this repo.

## The hancho.Config class is a dict, basically

The ```hancho.Config``` class is just a fancy ```dict``` with a few additional methods. For example, it comes with a pretty-printer:

```py
>>> foo = hancho.Config(a = 1, b = "two", c = ['th','ree'])
>>> foo
Config @ 0x788c818610e0 {
  a = 1,
  b = "two",
  c = list @ 0x788c8147db40 [
    "th",
    "ree",
  ],
}
```

## Hancho comes with some built-in functions

Both ```hancho.Config``` and ```hancho.HanchoAPI``` (the class of the global ```hancho``` object) derive from ```hancho.Utils``` to pick up various built-in functions that you may want to use in your scripts or templates. Most functions can accept either single values or arrays of values as their params and will generally do the right thing.

| Built-in       | Description |
| --------       | ----------- |
|```log```        | Logs messages to the console and to Hancho's internal log. Also plays nicer with console output from parallel tasks than ```print()```|
|```print```      | Python's built-in ```print()```.|
|```len```        | Python's built-in ```len()```|
|```abs_path```   | Converts a relative path to an absolute, physical path.|
|```rel_path```   | Removes a common prefix from an absolute path to make a relative path. ```rel_path('/foo/bar/baz', '/foo')``` -> ```'bar/baz'```
|```join```       | Joins arbitrary arrays of strings together, combinatorially. ```join(['a','b'],['c','d'])``` -> ```['ac', 'ad', 'bc', 'bd']```|
|```join_path```  | Joins arbitrary arrays of paths together, combinatorially. ```join_path(['a','b'],['c','d'])``` -> ```['a/c', 'a/d', 'b/c', 'b/d']```|
|```stem```       | Returns the 'stem' of a path - ```/home/foo/bar.txt``` -> ```bar```|
|```ext```        | Replaces a filename's extension.|
|```color```      | Returns escape codes that change the terminal's text color. Used for color-coding Hancho output.|
|```flatten```    | Converts nested arrays to a single flat array, non-array arguments to a one-element array, and ```None```s to an empty array. Used all over the place to normalize inputs.|
|```hancho_dir``` | The physical path to ```hancho.py```. Useful if you've cloned the Hancho repo and want to call ```hancho.load("{hancho_dir}/base_rules.hancho")```|
|```glob```       | Python's ```glob.glob```|
|```re```         | Python's ```re``` regular expression module|
|```path```       | Python's ```os.path``` module|
|```run_cmd```    | Runs a CLI command and returns the command's ```stdout```.|
|```rel```        | Only usable by ```Config```s. Removes ```task_dir``` from a file path if present. Makes descriptions and commands a bit more readable.|
|```merge```      | Only usable by ```Config```s. Merges additional ```Config```s or key-value pairs into this one.|
|```expand```     | Only usable by ```Config```s. Expands a text template.|

## Splitting your build into multiple ```.hancho``` files

Hancho is explicitly designed to allow for build scripts that span multiple files, multiple directories, and multiple repos.

To load the contents of another ```.hancho``` file into the current one, use ```hancho.load(filename)```. The return value from ```load()``` will be a Config containing all the global variables defined in the file, minus imported modules and 'private' variables prefixed with an underscore.

```py
# stuff.hancho
_private_constant = 42

def helper_function():
    return _private_constant
```

```py
# build.hancho
stuff = hancho.load("stuff.hancho")
print(stuff)
```

```sh
user@host:~/temp$ hancho
Loading /home/user/temp/build.hancho
Config @ 0x7cda59023480 {
  helper_function = <function helper_function at 0x7cda5902f100>,
}
hancho: BUILD CLEAN
```

Build scripts loaded this way get a _deep copy_ of the loader's ```hancho``` object, which can be used to pass arbitrary data into another build script.

```py
# stuff.hancho
print(f"hancho.options = {hancho.options}")
print(f"hancho.config.thing = {hancho.config.thing}")
```

```py
# build.hancho
hancho.options = 42
hancho.config.thing = "cat"
stuff = hancho.load("stuff.hancho")
```

```sh
aappleby@Neurotron:~/temp$ hancho
Loading /home/aappleby/temp/build.hancho
hancho.options = 42
hancho.config.thing = cat
hancho: BUILD CLEAN
```

If your project uses Git subrepos and your subrepo also builds with Hancho, you can load the subrepo's build script via ```hancho.repo()``` - this will ensure that all of its build targets go in ```{build_root}/{build_tag}/subrepo/path-relative-to-subrepo``` instead of getting mixed in with the rest of your build files.

```py
base_rules = hancho.load("{hancho_path}/base_rules.hancho")
awesomelib = hancho.repo("subrepos/awesomelib/build.hancho")

hancho(
  base_rules.cpp_binary,
  in_srcs = "main.cpp",
  in_libs = awesomelib.lib,
  out_bin = "main"
)
```

## The global 'hancho' object you use when writing a script has some other stuff in it.

In particular, there's a hancho.Config object named 'hancho.config' (note the lowercase) that gets merged into all tasks when you call ```hancho()```. This config object contains default paths that Hancho uses for bookkeeping. You can also set your own fields on hancho.config - they will then be visible to all tasks in your build script.

```py
HanchoAPI @ 0x7cb6c8d0b110 {
  config = Config @ 0x7cb6c8b223f0 {
    root_dir = "/home/user/temp",
    root_path = "/home/user/temp/build.hancho",
    repo_name = "",
    repo_dir = "/home/user/temp",
    mod_name = "build",
    mod_dir = "/home/user/temp",
    mod_path = "/home/user/temp/build.hancho",
    build_root = "{root_dir}/build",
    build_tag = "",
  },
  Config = <class '__main__.Config'>,
  Task = <class '__main__.Task'>,
}
```

Special fields and methods in ```hancho```
'Config',
'Task',
'__call__',
'config',
'hancho_dir',
'load',
'load_module',
'repo',
'root'

Fields automatically added to ```hancho.config```:
|Field name | Description |
| -----    | ----- |
|root_dir  | The directory Hancho was started in.|
|root_path | The build script Hancho read first|
|repo_name | The name of the repo or subrepo we're currently in. Empty string for the root repo, directory name for subrepos. Used to keep repos from colliding in ```build```|
|repo_dir  | The directory of the repo we're currently in.|
|mod_name  | The name of the Hancho script currently being processed |
|mod_dir   | The directory of the Hancho script currently being processed |
|mod_path  | The absolute path of the Hancho script currently being processed|
|build_root| The place where all ```out_*``` files should go. Defaults to ```{root_dir}/build```|
|build_tag | A descriptive tag such as ```debug```, ```release```, etcetera that can be used to divide your ```build``` directory up into ```build/debug```. Defaults to empty string.|


## Merging Configs together combines their fields.

The rule for merging two configs A and B is: ***If a field in B is not None, it overrides the corresponding field in A***.

```py
>>> foo = hancho.Config(a = 1)
>>> bar = hancho.Config(a = 2)
>>> hancho.Config(foo, bar)
Config @ 0x746cb87f3ed0 {
  a = 2,
}
>>> bar = hancho.Config(a = None)
>>> hancho.Config(foo, bar)
Config @ 0x746cb87f3f20 {
  a = 1,
}
```

This works for nested Configs as well:

```py
>>> foo = hancho.Config(child = hancho.Config(bar = 1, baz = 2))
>>> bar = hancho.Config(child = hancho.Config(baz = 3, cow = 4))
>>> hancho.Config(foo, bar)
Config @ 0x746cb87f3f70 {
  child = Config @ 0x746cb8610640 {
    bar = 1,
    baz = 3,
    cow = 4,
  },
}
```

## Templates work like a mix of F-strings and ```str.format()```

Like Python's F-strings, Hancho's templates can contain ```{arbi + trary * express - ions}```, but the expressions are _not_ immediately evaluated.

Instead, we call ```config.expand(template)``` and the values in ```config``` are used to fill in the blanks in ```template```.
```py
>>> foo = hancho.Config(a = 1, b = 2)
>>> foo.expand("The sum of a and b is {a+b}.")
'The sum of a and b is 3.'
```

A template that evaluates to an array will have each element stringified and then joined with spaces
```py
>>> foo = hancho.Config(a = [1, 2, 3])
>>> foo.expand("These are numbers - {a}")
'These are numbers - 1 2 3'
```

Nested arrays get flattened before joining
```py
>>> foo = hancho.Config(a = [[1, [2]], [[3]]])
>>> foo.expand("These are numbers - {a}")
'These are numbers - 1 2 3'
```

And a ```None``` will turn into an empty string.
```py
>>> foo = hancho.Config(a = None, b = None, c = None)
>>> foo.expand("a=({a}), b=({b}), c=({c})")
'a=(), b=(), c=()'
```

If the result of a template expansion contains more templates, Hancho will keep expanding until the string stops changing.
```py
>>> foo = hancho.Config(a = "a{b}", b = "b{c}", c = "c{d}", d = "d{e}", e = 1000)
>>> foo.expand("{a}")
'abcd1000'
```

Expanding templates based on configs inside configs also works:
```py
>>> foo = hancho.Config(a = 1, b = 2)
>>> bar = hancho.Config(c = foo)
>>> baz = hancho.Config(d = bar)
>>> baz.expand("d.c.a = {d.c.a}, d.c.a = {d.c.b}")
'd.c.a = 1, d.c.a = 2'
```

## Configs can contain functions, templates can call functions.

Any function attached to a ```Config``` can be used in a template. By default it contains all the methods from ```dict``` plus a set of built-in utility methods.

```py
>>> dir(foo)
[<snip...> 'abs_path', 'clear', 'color', 'copy', 'expand', 'ext', 'flatten', 'fromkeys', 'get', 'glob', 'hancho_dir', 'items', 'join', 'join_path', 'keys', 'len', 'log', 'merge', 'path', 'pop', 'popitem',  'print', 're', 'rel', 'rel_path', 'run_cmd', 'setdefault', 'stem', 'update', 'values']
```

Any of these methods can be used in a template. For example, ```color(r,g,b)``` produces escape codes to change the terminal color. Printing the expanded template should change your Python repl prompt to red:

```py
>>> foo = hancho.Config()
>>> foo.expand("{color(255,0,0)}")
'\x1b[38;2;255;0;0m'
>>> print(foo.expand("The color is now {color(255,0,0)}RED"))
The color is now RED
>>> (or it would be if this wasn't a Markdown file)
```

You can also attach your own functions to a config:

```py
>>> def get_number(): return 7
>>> a = hancho.Config(get_number = get_number)
>>> a.expand("Calling get_number equals {get_number()}")
'Calling get_number equals 7'
```

## Configs can contain templates they can't expand.

Failure to expand a template is _not an error_, it just passes the unexpanded template through.

```py
>>> foo = hancho.Config(a = 1)
>>> foo.expand("A equals {a}, B equals {b}")
'A equals 1, B equals {b}'
```

While this might seem like a bad idea, it allows for Configs to hold templates that they can't expand until they're needed later by a parent or grandparent config.
```py
>>> foo = hancho.Config(msg = "What's a {bar.thing}?")
>>> bar = hancho.Config(thing = "bear")
>>> baz = hancho.Config(foo = foo, bar = bar)
>>> baz.expand("{foo.msg}")
"What's a bear?"
```

Hancho comes with a simple text-expansion tracing tool for debugging your build scripts. It can be enabled by setting ```trace=True``` on a Config, or via ```--trace``` on the command line.

Here's what the tracer generates for the above example:

```py
Python 3.12.3 (main, Sep 11 2024, 14:17:37) [GCC 13.2.0] on linux
Type "help", "copyright", "credits" or "license" for more information.
>>> import hancho
>>> foo = hancho.Config(msg = "What's a {bar.thing}?")
>>> bar = hancho.Config(thing = "bear")
>>> baz = hancho.Config(foo = foo, bar = bar, trace=True)
>>> baz.expand("{foo.msg}")
0x76beaa7eebc0: ┏ expand_text '{foo.msg}'
0x76beaa7eebc0: ┃ ┏ expand_macro '{foo.msg}'
0x76beaa7eebc0: ┃ ┃ Read 'foo' = Config @ 0x76beaa7eec60'
0x76beaa7eebc0: ┃ ┗ expand_macro '{foo.msg}' = What's a {bar.thing}?
0x76beaa7eebc0: ┗ expand_text '{foo.msg}' = 'What's a {bar.thing}?'
0x76beaa7eebc0: ┏ expand_text 'What's a {bar.thing}?'
0x76beaa7eebc0: ┃ ┏ expand_macro '{bar.thing}'
0x76beaa7eebc0: ┃ ┃ Read 'bar' = Config @ 0x76beaa7eecb0'
0x76beaa7eebc0: ┃ ┗ expand_macro '{bar.thing}' = bear
0x76beaa7eebc0: ┗ expand_text 'What's a {bar.thing}?' = 'What's a bear?'
"What's a bear?"
```

FIXME - there should be a ```Read 'msg'``` line and a ```Read 'thing'``` line in that trace - where did they go?



## Tasks are nodes in Hancho's build graph.

Tasks take a Config that completely defines the input files, output files, and directories needed to run a command and adds it to Hancho's build graph.

Tasks are lazily executed - only tasks that are needed to build the selected outputs are executed. By default, all Tasks that originate from the repo we started the build in will be queued up for execution.

## Calling ```hancho(...)``` merges ```hancho.config``` with all the parameters passed to ```hancho()``` and creates a task from it.

```py
echo_stuff = hancho.Config(
    command = "echo {in_file}",
)
hancho(echo_stuff, in_file = "foo.txt")
```
## Tasks can be used as inputs to other tasks anywhere you'd use a filename.
```py
foo_txt = hancho(
    command = "echo I like turtles > {out_file}",
    out_file = "foo.txt"
)
hancho(
    command = "cat {in_file}",
    in_file = foo_txt
)
```

## Raw tasks for corner cases

Normally Hancho will inject ```hancho.config``` into your Tasks to provide the path information
needed for the build.

If you'd rather control all the paths yourself, you can create a Task directly. You'll need to
supply ```task_dir``` and ```build_dir``` so that Hancho knows where to look for input and output
files.

```py
hancho.Task(
  command = "echo hello world",
  task_dir = ".",
  build_dir = "."
)
```

## Using task-generating functions to simplify your build

Sometimes you may need to create multiple small tasks to accomplish a larger task. For example,
this function from ```base_rules.hancho``` compiles a list of source files and then links them
along with other object files or libraries into a larger C++ library.

```py
def cpp_lib(hancho, *, in_srcs=None, in_objs=None, in_libs=None, out_lib, **kwargs):
    in_objs = hancho.flatten(in_objs)
    for file in hancho.flatten(in_srcs):
        obj = hancho(compile_cpp, in_src=file, **kwargs)
        in_objs.append(obj)
    return hancho(link_cpp_lib, in_objs=[in_objs, in_libs], out_lib=out_lib, **kwargs)
```

You can of course call this function directly, but for easier integration with larger build scripts
you can also pass ```cpp_lib``` as the first argument to ```hancho()```:

```
hancho(
  cpp_lib,
  in_srcs = glob.glob("src/*.cpp")
  out_lib = "foo.a"
)
```

Doing this is is exactly equivalent to the following:

```py
temp_config = hancho.Config(
  hancho.config,
  in_srcs = glob.glob("src/*.cpp"),
  out_lib = "foo.a"
)
cpp_lib(hancho, **temp_config)
```

## Using callbacks as Hancho commands

If you pass a function as the ```command``` field for a task, Hancho will call it with the task as
an argument. The callbacks can be synchronous or asynchronous - both work fine. If you need to do
some custom Python stuff during a build, this is the easiest way to do it.

```py
import asyncio

async def my_callback(task):
  await asyncio.sleep(0.1)
  print(f"Hello from an asynchronous callback, my task is {task}")

hancho(
  command = my_callback,
)
```