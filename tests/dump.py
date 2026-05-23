#!/usr/bin/python3

import sys
sys.path.append("..")
import hancho

class Merp:
    def __repr__(self):
        return f"merp@{hex(id(self))}"

print("--------------------------------------------------------------------------------")
test = hancho.Dict(
    a = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0],
    b = "two",
    c = r"three",
    d = [4, 5, 6],
    e = dict(f = 7, g = 8, h = 9),
    f = ["foo"],
    g = dict(bar = 1),
    h = None,
    i = dict(foo = None),
    j = dict(foo = None, bar = None),
    k = [1, 2, 3, 4, 5, 6, 7, 8, 9],
    l = ["hello", [Merp(), Merp(), Merp(), Merp(), Merp()], "merp", "merp"],
    m = [],
    n = [[[[],[]],[{},{},{"a":{},"b":{}}]], "123456789123456789", "123456789123456789"],
    o = ("slkdjfslkdjf",),
    p = ("tu","p","le"),
    q = ((),(()),),
    r = {"merp1":Merp(), "merp2":Merp()},
    s = dict(foo = 1, bar = 2, baz = 3),
    t = dict(a = "123456789123456789", b = "123456789123456789", c = "123456789123456789", d = "123456789123456789"),
    u = [print, len, Merp.__repr__, lambda x : x + 1, lambda x,y,z : x * y * z],
    v = [True, False],
    w = [b"1234", "Hello World".encode(), bytearray("Hello", 'utf-8'), range(10)],
    x = hancho.Task(command = "echo hello world"),
)


#result = hancho.Dumper(print_id = False).dump_to_str("test", test)

#chunk = hancho.Dumper().dump_oneline("test", test)
#assert not '\n' in chunk
#print(chunk)

print("--------------------------------------------------------------------------------")

print(hancho.Dumper().dump_to_str(indent = 0, key = "foo", val = None))
print(hancho.Dumper().dump_to_str(indent = 0, key = None, val = "foo"))
print(hancho.Dumper().dump_to_str(indent = 0, key = None, val = None))

print(hancho.Dumper().dump_to_str(indent = 0, key = "foo", val = 12345))

class Wrapper:
    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f"<{self.value}>"

print(hancho.Dumper().dump_to_str(indent = 0, key = "foo", val = Wrapper(12345)))

print(hancho.Dumper(tab = "<->").dump_to_str(indent = 4, key = 17, val = Wrapper(12345)))

print("--------------------------------------------------------------------------------")

print(hancho.Dumper(tab = ". ").dump_to_str(indent = 0, key = "test", val = test))

#print(hancho.Dumper(max_width = 4).dump_to_str(indent = 0, key = "test", val = test))

#import pprint
#pprint.pprint(dict(test))
