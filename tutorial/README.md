Hancho is built out of a few simple pieces


## hancho.Config

The ```hancho.Config``` class is just a fancy ```dict``` with a few additional methods.

Configs can be merged together by wrapping them in another Config. The rule for merging two configs A and B via ```hancho.Config(A, B)``` is: If a field in B is not None, it overrides the matching field in A.

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

```
>>> foo = hancho.Config(bar = 2)
>>> foo.expand("The value of bar is {bar}")
'The value of bar is 2'
```

Like Python's F-strings, Hancho's templates can contain ```{arbitrary_expressions}```, but the expressions are _not_ immediately evaluated. Instead, we call ```config.expand(template)``` and the values in ```config``` are used to fill in the blanks in ```template```.

```py
>>> foo = hancho.Config(a = 1, b = 2)
>>> foo.expand("The sum of a and b is {a+b}.")
'The sum of a and b is 3.'
```

Because a ```Config``` is a dict, it inherits dict methods in addition to providing a few of its own. All of these methods can be used in a template, in addition to whatever other methods you attach to the ```Config```.

```py
>>> dir(foo)
[<snip...> 'abs_path', 'clear', 'color', 'copy', 'expand', 'flatten', 'fork', 'fromkeys', 'get', 'glob', 'hancho_dir', 'items', 'join_path', 'join_prefix', 'join_suffix', 'keys', 'len', 'log', 'merge', 'path', 'pop', 'popitem', 'print', 're', 'rel', 'rel_path', 'run_cmd', 'setdefault', 'stem', 'swap_ext', 'update', 'values']
```


Any of these methods can be used in a template. For example, ```color(r,g,b)``` produces escape codes to change the terminal color. Printing the expanded template should change your Python repl prompt to red:

```py
>>> foo.expand("{color(255,0,0)}")
'\x1b[38;2;255;0;0m'
>>> print(foo.expand("{color(255,0,0)}"))
<span style="color:red"> >>> </span>
```


Expanding templates based on configs inside configs also works:

```py
>>> foo = hancho.Config(a = 1, b = 2)
>>> bar = hancho.Config(c = foo)
>>> baz = hancho.Config(d = bar)
>>> baz.expand("d.c.a = {d.c.a}, d.c.a = {d.c.b}")
'd.c.a = 1, d.c.a = 2'
```

If a template can't be expanded inside its parent config, Hancho will try to expand it inside its grandparent config (if present), etcetera:
```py
>>> foo = hancho.Config(msg = "What's a {bar.thing}?")
>>> bar = hancho.Config(thing = "bear")
>>> baz = hancho.Config(foo = foo, bar = bar)
>>> baz.expand("{foo.msg}")
"What's a bear?"
```

