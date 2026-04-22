import hancho
from hancho import Dict, Expander

#d = Dict(sum = "{a} + {b} + {c.d}", a = 1, b = 2, c = Dict(d = 3))
#d.eval("{sum}")

#d = Dict(a = "{b}", b = "{c}", c = "{d}", d = 10)
#d.eval("a")


#d = Dict(part_a1 = '{f', part_b1 = 'o', part_c1 = 'o}',
#  foo = "{part_a2}{part_b2}{part_c2}",
#  part_a2 = '{b', part_b2 = 'a', part_c2 = 'r}',
#  bar = 12)
#d.expand("{part_a1}{part_b1}{part_c1}")

d = Dict(a = Dict(b = Dict(c = "{d}", d = 10)))
d.expand("{a.b.c}")

d = Dict(a = Dict(b = Dict(c = "{d}")), d = 10)
d.expand("{a.b.c}")