#!/usr/bin/python3

import rules
import re
import pprint

from tinybuild import ProtoDict

"""
def obj_name(x):
  return "obj/" + tinybuild.swap_ext(x, ".o")

def compile_dir(dir):
  files = glob.glob(dir + "/*.cpp") + glob.glob(dir + "/*.c")
  objs  = [obj_name(x) for x in files]
  compile_cpp(files, objs)
  return objs
"""

def build_main():
  print("Building bin/main")
  print("=====BEGIN=====")
  rules.compile_cpp("src/test.cpp", "obj/test.o")
  rules.compile_cpp("src/main.cpp", "obj/main.o")
  rules.link_c_bin(["obj/main.o", "obj/test.o"], "bin/main")
  print("=====END=====")

rules.tinybuild.build(build_main)

"""
config = ProtoDict()

config.toolchain  = "x86_64-linux-gnu"
config.build_type = "-g -O0"
config.warnings   = "-Wunused-variable -Werror"
config.depfile    = "-MMD -MF {file_out}.d"
config.defines    = "-DCONFIG_DEBUG"
config.cpp_std    = "-std=gnu++2a"
config.includes   = "-I. -Isymlinks"
config.c_opts     = "{warnings} {depfile} {build_type}"
config.cpp_opts   = "{cpp_std} {c_opts}"
config.ld_opts    = "{build_type}"

rule = ProtoDict(config)
rule.libraries = ""

action = ProtoDict(rule)
action.desc       = "Compiling C++ {file_in} => {file_out}"
action.command    = "{toolchain}-g++ {cpp_opts} {includes} {defines} -c {file_in} -o {file_out}"

file = ProtoDict(action)
file.config = config
file.rule = rule
file.action = action
file.files_in = ["src/main.cpp", "src/test.cpp"]
file.files_out = ["obj/main.o", "obj/test.o"]

print("========================================")
file.dump()
print("========================================")
"""
