# tutorial/rules.hancho - Reusable rules for tutorial 3
import hancho
import os

# We extend base_rule just like the previous example
hancho.config.set(
  build_dir = "build/tut3"
)

# And these rules are the same as the previous example
rule_compile = hancho.Rule(
  desc      = "Compile {files_in} -> {files_out}",
  command   = "g++ -c {files_in[0]} -o {files_out[0]}",
  files_out = "{swap_ext(files_in[0], '.o')}",
  depfile   = "{build_dir}/{swap_ext(files_in[0], '.d')}",
)

rule_link = hancho.Rule(
  desc      = "Link {files_in} -> {files_out}",
  command   = "g++ {join(files_in)} -o {files_out[0]}",
)

# But since we're in Python, we can make helper functions to call rules for us
def compile(files):
  objs = []
  for file in files:
    objs.append(rule_compile(files_in = file))
  return objs

# And now compiling a bunch of files into a binary is just one call.
def c_binary(name, files):
  return rule_link(
    files_in = compile(files),
    files_out = name
  )
