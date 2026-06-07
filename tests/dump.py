#!/usr/bin/python3

import timeit

import hancho
from hancho import Log


class Merp:
    def __repr__(self):
        return f"merp@{hex(id(self))}"

test = hancho.Dict(
    s0 = ["123456789012345678901234567890123456789012345678901234567890123456"],
    s1 = ["1234567890123456789012345678901234567890123456789012345678901234567"],
    s2 = ["12345678901234567890123456789012345678901234567890123456789012345678"],
    s3 = ["123456789012345678901234567890123456789012345678901234567890123456789"],
    s4 = ["1234567890123456789012345678901234567890123456789012345678901234567890"],
    a = [[1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0]],
    b = "two\ntwo\ntwo",
    c = r"three",
    d = [4, 5, 6],
    e = {"f": 7, "g": 8, "h": 9},
    f = ["foo"],
    g = {"bar": 1},
    h = None,
    i = {"foo": None},
    j = {"foo": None, "bar": None},
    k = [1, 2, 3, 4, 5, 6, 7, 8, 9],
    l = ["hello", [Merp(), Merp(), Merp(), Merp(), Merp()], "merp", "merp"],
    m = [],
    n = [[[[],[]],[{},{},{"a":{},"b":{}}]], "123456789123456789", "123456789123456789"],
    o = ("slkdjfslkdjf",),
    p = ("tu","p","le"),
    q = ((),(()),),
    r = {"merp1":Merp(), "merp2":Merp()},
    s = {"foo": 1, "bar": 2, "baz": 3},
    t = {"a": "123456789123456789", "b": "123456789123456789", "c": "123456789123456789", "d": "123456789123456789"},
    u = [print, len, Merp.__repr__, lambda x : x + 1, lambda x,y,z : x * y * z],
    v = [True, False],
    w = [b"1234", b"Hello World", bytearray("Hello", 'utf-8'), range(10)],
    x = hancho.Task(command = "echo tests/dump.py"),
)

print('-' * 80)

print(Log.dump_to_str(key = "foo", val = None))
print(Log.dump_to_str(key = None,  val = "foo"))
print(Log.dump_to_str(key = None,  val = None))
print(Log.dump_to_str(key = "foo", val = 12345))

class Wrapper:
    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f"<{self.value}>"

print(Log.dump_to_str(key = "foo", val = Wrapper(12345)))

print(Log.dump_to_str(key = 17, val = Wrapper(12345), tab = "<->"))

print('-' * 80)

result = Log.dump_to_str(key = "test", val = test, tab = ". ", max_width = 80)
print(result)
print()

#print(Log.dump_to_str(key = "test", val = test, tab = ". ", max_width = 9999999))
#print()


if True:
    def blah1():
        return Log.dump_to_str(key = "test", val = test, tab = ". ", max_width = 80)
    print(f"max_width = 80 -> {timeit.timeit(blah1, number = 1000)} msec")

    def blah2():
        return Log.dump_to_str(key = "test", val = test, tab = ". ", max_width = 9999999)
    print(f"max_width = inf -> {timeit.timeit(blah2, number = 1000)} msec")

#import pprint
#pprint.pprint(dict(test))

print(Log.dump_to_str("hancho", hancho.__dict__))

print(dir(hancho))

print(hancho.__dict__.keys())
