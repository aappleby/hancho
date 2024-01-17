>>> import hancho

>>> import tempfile
>>> tmpdirname = tempfile.TemporaryDirectory()

>>> blah = hancho.ProtoArgs()
>>> print(blah)                                              #doctest: +ELLIPSIS
<hancho.ProtoArgs object at 0x...>
>>> hancho.global_args.print = print
>>> print_hello = hancho.reduce(command = "echo {print('hello world')}")
>>> def my_task():
...   print_hello(files_in = ["test/foo.c"], files_out = ["obj/foo.o"])
>>> hancho.global_args.verbose = True

#>>> hancho.run(my_task)
#['test/foo.c'] -> ['obj/foo.o']
