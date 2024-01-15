#!/usr/bin/python3

import rules
import tinybuild

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
  rules.link_c_bin(files_in = ["obj/main.o", "obj/tfest.o"], files_out = "bin/main")
  rules.compile_cpp(files_in = "src/test.cpp", files_out = "obj/test.o")
  rules.compile_cpp(files_in = "src/main.cpp", files_out = "obj/main.o")

tinybuild.run(build_main)
