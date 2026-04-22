import hancho
from hancho import Dict, Expander

d = Dict(a = "1 + 1")
d.expand("{a}")
