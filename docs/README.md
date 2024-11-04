# Hancho Quick Reference

Hancho is built out of a few simple pieces - the ```hancho``` object, Configs, Templates, and Tasks. This document is a quick overview of each of those pieces, along with a few examples of more complex usage.

For more detailed and up-to-date information, check out the examples folder and the '*_rules.hancho' files in the root directory of this repo.

## All built-ins:

Both ```hancho.Config``` and ```hancho.HanchoAPI``` (the class of the global ```hancho``` object) derive from ```hancho.Utils``` to pick up various built-in functions that you may want to use in your scripts or templates. Most functions can accept either single values or arrays of values as their params and will generally do the right thing.

| Built-in       | Description |
| --------       | ----------- |
|```log```        | Logs messages to the console and to Hancho's internal log. Also plays nicer with console output from parallel tasks than ```print()```|
|```print```      | Python's built-in ```print()```.|
|```len```        | Python's built-in ```len()```|
|```abs_path```   | Converts a relative path to an absolute, physical path.|
|```rel_path```   | Removes a common prefix from an absolute path to make a relative path. ```rel_path('/foo/bar/baz', '/foo')``` -> ```'bar/baz'```
|```join_path```  | Joins arbitrary arrays of paths together, combinatorially. ```join_path(['a','b'],['c','d'])``` -> ```['a/c', 'a/d', 'b/c', 'b/d']```|
|```join_prefix```| Attaches a prefix to a string or an array of strings.|
|```join_suffix```| Attaches a suffix to a string or an array of strings.|
|```stem```       | Returns the 'stem' of a path - ```/home/foo/bar.txt``` -> ```bar```|
|```swap_ext```   | Replaces a filename's extension.|
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



## The hancho.Config class is a dict, basically.

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
[<snip...> 'abs_path', 'clear', 'color', 'copy', 'expand', 'flatten',
'fromkeys', 'get', 'glob', 'hancho_dir', 'items', 'join_path', 'join_prefix',
'join_suffix', 'keys', 'len', 'log', 'merge', 'path', 'pop', 'popitem',
'print', 're', 'rel', 'rel_path', 'run_cmd', 'setdefault', 'stem', 'swap_ext',
'update', 'values']
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

