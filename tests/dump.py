#!/usr/bin/python3
import hancho

class Merp:
    def __repr__(self):
        return f"merp@{hex(id(self))}"

test = hancho.Dict(
    a = 1,
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
    r = [Merp(), Merp()],
    s = dict(foo = 1, bar = 2, baz = 3),
    t = dict(a = "123456789123456789", b = "123456789123456789", c = "123456789123456789", d = "123456789123456789"),
)


result = hancho.Dumper(print_id = False).dump_to_str("test", test)
print(result)

#import pprint
#pprint.pprint(dict(test))
