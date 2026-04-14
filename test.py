#!/usr/bin/python3

import sys

print("Hello World")

import hancho

print("Hancho imported")

print(hancho)

foo = hancho.Dict(a = 1, b = 2, c = 3, d = hancho.Dict(e = 4, f = 5, g = "derp", h = [1,2,3]))

print(foo)

print(foo.dump(10))

try:
    foo.a = 2
except:
    print("Couldn't set foo.a")
