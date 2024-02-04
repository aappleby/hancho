"""
Special action args
  description: Description of the rule printed every time it runs
  command:     Command to run for the rule
  files_in:    Either a single filename or a list of filenames
  files_out:   Either a single filename or a list of filenames
  force:       Makes the rule always run even if dependencies are up to date
"""


"""
Hancho is a minimal build system that focuses only on doing two things:
1 - Only rebuild files that need rebuilding
2 - Make generating build commands simple

Build parameters can be specified globally, at rule scope, or at action scope.

>>> import tempfile
>>> tmpdirname = tempfile.TemporaryDirectory()
>>> print(tmpdirname)                                        #doctest: +ELLIPSIS
<TemporaryDirectory '/tmp/tmp...'>
>>> os.chdir(tmpdirname.name)
>>> print(os.getcwd())                                       #doctest: +ELLIPSIS
/tmp/tmp...

>>> import hancho
>>> print_hello = hancho.rule(command = "echo hello world")
>>> def my_task():
...   #print_hello(input_files = ["foo.c"], output_files = ["foo.o"])
...   pass
>>> hancho.run(my_task)
"""


  """
  Swaps the extension of a filename.

    >>> filename = "src/foo.cpp"
    >>> swap_ext(filename, ".hpp")
    'src/foo.hpp'
  """

  """
  Sticks strings together with a space.

     >>> filenames = ["foo.cpp", "bar.cpp", "baz.cpp"]
     >>> join(filenames)
     'foo.cpp bar.cpp baz.cpp'
  """

  """
  Wraps scalars in a list, flattens nested lists into a single list.

    >>> listify(None)
    []
    >>> listify("asdf")
    ['asdf']
    >>> listify([[[1]],[[[[2]]]],[[3],[4],[[5]]]])
    [1, 2, 3, 4, 5]
  """

  """
  Config is a Javascript-style prototypal-inheritance text-expansion tool.
  It allows you to create objects with trees of attributes (and attribute
  inheritance) and use those trees to repeatedly expand Python strings ala
  f-strings until they no longer contain {}s.

  Config instances behave like Javascript objects. String fields can
  contain Python expressions in curly braces, which will be evaluated when
  the args are used to "expand" a template string.

    >>> args1 = Config()
    >>> args1.foo = "foo_option1"
    >>> args1.bar = "bar_option77"
    >>> args1.message = "Foo is {foo}, bar is {bar}, undefined is {undefined}."

  Config can use prototype-style inheritance. This "args2" instance will
  appear to contain all the fields of args1, but can override them.

    >>> args2 = Config(args1)
    >>> args2.bar = "bar_override"

  Config can be used to expand a string containing {}s. Variable lookup
  will happen using the arg object itself as a context, with lookup
  proceeding up the prototype chain until a match is found (or "" if there
  was no match).

    >>> print(expand(args2.message, args2))
    Foo is foo_option1, bar is bar_override, undefined is .

"""


>>> import hancho

>>> import tempfile
>>> tmpdirname = tempfile.TemporaryDirectory()

>>> blah = hancho.Config()
>>> print(blah)                                              #doctest: +ELLIPSIS
<hancho.Config object at 0x...>
>>> hancho.config.print = print
>>> print_hello = hancho.rule(command = "echo {print('hello world')}")
>>> def my_task():
...   print_hello(files_in = ["test/foo.c"], files_out = ["obj/foo.o"])
>>> hancho.config.verbose = True

#>>> hancho.run(my_task)
#['test/foo.c'] -> ['obj/foo.o']
