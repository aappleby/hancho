#!/usr/bin/python3

import argparse
import tinybuild
from tinybuild import global_args as globals

parser = argparse.ArgumentParser(
  prog = "Test Build",
  description = "Test Build for tinybuild",
  epilog = "Test Build Done!",
)

parser.add_argument('--verbose',  default=False, action='store_true', help='Print verbose build info')
parser.add_argument('--clean',    default=False, action='store_true', help='Delete intermediate files')
parser.add_argument('--serial',   default=False, action='store_true', help='Do not parallelize commands')
parser.add_argument('--dry_run',  default=False, action='store_true', help='Do not run commands')
parser.add_argument('--debug',    default=False, action='store_true', help='Dump debugging information')
parser.add_argument('--dotty',    default=False, action='store_true', help='Dump dependency graph as dotty')
(flags, unrecognized) = parser.parse_known_args()

globals.toolchain  = "x86_64-linux-gnu"
globals.build_type = "-g -O0"
globals.warnings   = "-Wunused-variable -Werror"
globals.depfile    = "-MMD -MF {file_out}.d"
globals.defines    = "-DCONFIG_DEBUG"
globals.cpp_std    = "-std=gnu++2a"
globals.includes   = "-I. -Isymlinks"
globals.c_opts     = "{warnings} {depfile} {build_type}"
globals.cpp_opts   = "{cpp_std} {c_opts}"
globals.ld_opts    = "{build_type}"

globals.verbose    = flags.verbose
globals.clean      = flags.clean
globals.serial     = flags.serial
globals.dry_run    = flags.dry_run
globals.debug      = flags.debug
globals.dotty      = flags.dotty

compile_cpp = tinybuild.map(
  desc    = "Compiling C++ {file_in} => {file_out}",
  command = "{toolchain}-g++ {cpp_opts} {includes} {defines} -c {file_in} -o {file_out}",
)

compile_c = tinybuild.map(
  desc    = "Compiling C {file_in} => {file_out}",
  command = "{toolchain}-gcc {c_opts} {includes} {defines} -c {file_in} -o {file_out}",
)

link_c_lib = tinybuild.reduce(
  desc    = "Bundling {file_out}",
  command = "ar rcs {file_out} {join(files_in)}",
)

link_c_bin = tinybuild.reduce(
  desc    = "Linking {file_out}",
  command = "{toolchain}-g++ {ld_opts} {join(files_in)} {libraries} -o {file_out}",
)

def build_main():
  compile_cpp(files_in = "src/test.cpp", files_out = "obj/test.o")
  compile_cpp(files_in = "src/main.cpp", files_out = "obj/main.o")
  link_c_bin(files_in = ["obj/main.o", "obj/test.o"], files_out = "bin/main")

tinybuild.run(build_main)





"""
def obj_name(x):
  return "obj/" + tinybuild.swap_ext(x, ".o")

def compile_dir(dir):
  files = glob.glob(dir + "/*.cpp") + glob.glob(dir + "/*.c")
  objs  = [obj_name(x) for x in files]
  compile_cpp(files, objs)
  return objs
"""
