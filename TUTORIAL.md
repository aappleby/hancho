>>> import tinybuild

>>> import tempfile
>>> tmpdirname = tempfile.TemporaryDirectory()

>>> blah = tinybuild.ProtoArgs()
>>> print(blah)                                              #doctest: +ELLIPSIS
<tinybuild.ProtoArgs object at 0x...>
>>> tinybuild.global_args.print = print
>>> print_hello = tinybuild.reduce(command = "echo {print('hello world')}")
>>> def my_task():
...   print_hello(files_in = ["test/foo.c"], files_out = ["obj/foo.o"])
>>> tinybuild.global_args.verbose = True

#>>> tinybuild.run(my_task)
#['test/foo.c'] -> ['obj/foo.o']
