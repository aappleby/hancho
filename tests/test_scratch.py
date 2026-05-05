import hancho
from hancho import Dict, Expander

print("<test_scratch.py>")

#d = hancho.Dumper(2)
#
#def gen_things():
#    yield "Foo"
#    yield "Bar"
#    yield "Baz"
#
#cfg = hancho.Dict(
#    message = ["Hello", "World", "Boop"],
#    range   = range(10),
#    gen     = gen_things(),
#    slkdjf = "lksdjflskj",
#    rhiweurie = b"kjfskdjlsf",
#)
#
#print(d.dump(cfg))

blah = hancho.task(
    desc    = "dummy task 1",
    command = "echo {message}",
    message = ["Hello", "World", "Boop"],
    #verbose = True,
    #trace   = True,
)

blee = hancho.task(
    desc    = "dummy task 2",
    command = "echo {message}",
    message = ["Goodbye", "Star", "Beep"],
    #verbose = True,
)

hancho.Runner.queue_all_tasks()
hancho.Runner.run_tasks()
hancho.reset([])