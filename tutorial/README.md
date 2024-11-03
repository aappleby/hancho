Hancho is built out of a few simple pieces - Configs, text templates, and Tasks.

## The hancho.Config class is a dict, basically.

The ```hancho.Config``` class is just a fancy ```dict``` with a few additional methods. For example, it comes with a pretty-printer:

```
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

To clone a Config and optionally change its fields, use ```config.fork()```:
```
>>> foo = hancho.Config(a = 1)
>>> bar = foo.fork(b = 2)
>>> bar
Config @ 0x788c814aaee0 {
  a = 1,
  b = 2,
}
```

Configs can be merged together by wrapping them in another Config. The rule for merging two configs A and B via ```hancho.Config(A, B)``` is: ***If a field in B is not None, it overrides the corresponding field in A***.

```py
>>> hancho.Config(hancho.Config(a = 1), hancho.Config(a = 2))
Config @ 0x746cb87f3ed0 {
  a = 2,
}
>>> hancho.Config(hancho.Config(a = 1), hancho.Config(a = None))
Config @ 0x746cb87f3f20 {
  a = 1,
}
```

This works for nested Configs as well:

```
>>> a = hancho.Config(foo = hancho.Config(bar = 1, baz = 2))
>>> b = hancho.Config(foo = hancho.Config(baz = 3, cow = 4))
>>> hancho.Config(a, b)
Config @ 0x746cb87f3f70 {
  foo = Config @ 0x746cb8610640 {
    bar = 1,
    baz = 3,
    cow = 4,
  },
}
```

## Text {templates}

Hancho's text templates work a bit like Python's F-strings and a bit like its ```str.format()``` method:

Like Python's F-strings, Hancho's templates can contain ```{arbitrary_expressions}```, but the expressions are _not_ immediately evaluated. Instead, we call ```config.expand(template)``` and the values in ```config``` are used to fill in the blanks in ```template```.


```py
>>> foo = hancho.Config(a = 1, b = 2)
>>> foo.expand("The sum of a and b is {a+b}.")
'The sum of a and b is 3.'
```

If the result contains more templates, Hancho will keep expanding until the string stops changing.

```py
>>> foo = hancho.Config(a = "a{b}", b = "b{c}", c = "c{d}", d = "d{e}", e = 1000)
>>> foo.expand("{a}")
'abcd1000'
```

## Configs can contain functions, templates can call functions.

Any function attached to a ```Config``` can be used in a template. By default it contains all the methods from ```dict``` plus a set of built-in utility methods.

```py
>>> dir(foo)
[<snip...> 'abs_path', 'clear', 'color', 'copy', 'expand', 'flatten', 'fork', 'fromkeys', 'get', 'glob', 'hancho_dir', 'items', 'join_path', 'join_prefix', 'join_suffix', 'keys', 'len', 'log', 'merge', 'path', 'pop', 'popitem', 'print', 're', 'rel', 'rel_path', 'run_cmd', 'setdefault', 'stem', 'swap_ext', 'update', 'values']
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

## Nested configs and "fallthrough"

Expanding templates based on configs inside configs also works:

```py
>>> foo = hancho.Config(a = 1, b = 2)
>>> bar = hancho.Config(c = foo)
>>> baz = hancho.Config(d = bar)
>>> baz.expand("d.c.a = {d.c.a}, d.c.a = {d.c.b}")
'd.c.a = 1, d.c.a = 2'
```

Failure to expand a template is _not an error_, it just passes the unexpanded template through.

```py
>>> foo = hancho.Config(a = 1)
>>> foo.expand("A equals {a}, B equals {b}")
'A equals 1, B equals {b}'
```

While this might seem like a bad idea, it allows for Configs to hold templates that they can't expand which will be used later by a parent or grandparent config.

```py
>>> foo = hancho.Config(msg = "What's a {bar.thing}?")
>>> bar = hancho.Config(thing = "bear")
>>> baz = hancho.Config(foo = foo, bar = bar)
>>> baz.expand("{foo.msg}")
"What's a bear?"
```

