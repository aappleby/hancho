from hancho import Dict, Expander

d = Dict(a = Dict(b = "{c}", c = 10), c = 20)
print(d.eval("a.b"))
print(d.eval("{a.b}"))
e = Expander(d)
print(e.eval2("{a.b}"))
