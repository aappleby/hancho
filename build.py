#!/usr/bin/python3

import argparse
import hancho
import asyncio
import atexit

print("================================================================================")

parser = argparse.ArgumentParser(
  prog = "Test Build",
  description = "Test Build for Hancho",
  epilog = "Test Build Done!",
)

parser.add_argument('--verbose',  default=False, action='store_true', help='Print verbose build info')
parser.add_argument('--clean',    default=False, action='store_true', help='Delete intermediate files')
parser.add_argument('--serial',   default=False, action='store_true', help='Do not parallelize commands')
parser.add_argument('--dry_run',  default=False, action='store_true', help='Do not run commands')
parser.add_argument('--debug',    default=False, action='store_true', help='Dump debugging information')
parser.add_argument('--dotty',    default=False, action='store_true', help='Dump dependency graph as dotty')
(flags, unrecognized) = parser.parse_known_args()

hancho.config.verbose    = flags.verbose
hancho.config.clean      = flags.clean
hancho.config.serial     = flags.serial
hancho.config.dry_run    = flags.dry_run
hancho.config.debug      = flags.debug
hancho.config.dotty      = flags.dotty

#hancho.config.toolchain  = "x86_64-linux-gnu"
#hancho.config.build_opt  = "-g -O0"
#hancho.config.warnings   = "-Wunused-variable -Werror"
#hancho.config.depfile    = "-MMD -MF {file_out}.d"
#hancho.config.defines    = "-DCONFIG_DEBUG"
#hancho.config.cpp_std    = "-std=gnu++2a"
#hancho.config.includes   = "-I. -Isymlinks"
#hancho.config.c_opts     = "{warnings} {depfile} {build_opt}"
#hancho.config.cpp_opts   = "{cpp_std} {c_opts}"
#hancho.config.ld_opts    = "{build_opt}"

compile_cpp = hancho.rule(
  #desccription = "Compiling C++ {file_in} => {file_out}",
  command  = "{toolchain}-g++ {cpp_opts} {includes} {defines} -c {file_in} -o {file_out}",
  parallel = True
)

compile_c = hancho.rule(
  #desccription = "Compiling C {file_in} => {file_out}",
  command  = "{toolchain}-gcc {c_opts} {includes} {defines} -c {file_in} -o {file_out}",
  parallel = True
)

link_c_lib = hancho.rule(
  #desccription = "Bundling {file_out}",
  command = "ar rcs {file_out} {join(files_in)}",
)

link_c_bin = hancho.rule(
  #desccription = "Linking {file_out}",
  command = "{toolchain}-g++ {ld_opts} {join(files_in)} {libraries} -o {file_out}",
)

compile_cpp(files_in = "src/test.cpp", files_out = "obj/test.o")
compile_cpp(files_in = "src/main.cpp", files_out = "obj/main.o")
link_c_bin(files_in = ["obj/main.o", "obj/test.o"], files_out = "bin/main")




"""
def obj_name(x):
  return "obj/" + hancho.swap_ext(x, ".o")

def compile_dir(dir):
  files = glob.glob(dir + "/*.cpp") + glob.glob(dir + "/*.c")
  objs  = [obj_name(x) for x in files]
  compile_cpp(files, objs)
  return objs
"""
