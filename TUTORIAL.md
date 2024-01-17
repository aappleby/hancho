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
