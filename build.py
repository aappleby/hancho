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
parser.add_argument('--serial',   default=False, action='store_true', help='Do not parallelize commands')
parser.add_argument('--dryrun',   default=False, action='store_true', help='Do not run commands')
parser.add_argument('--debug',    default=False, action='store_true', help='Dump debugging information')
parser.add_argument('--dotty',    default=False, action='store_true', help='Dump dependency graph as dotty')
(flags, unrecognized) = parser.parse_known_args()

base_config = hancho.Config(
  prototype = hancho.config,
  verbose   = flags.verbose,
  serial    = flags.serial,
  dryrun    = flags.dryrun,
  debug     = flags.debug,
  dotty     = flags.dotty,
  toolchain = "x86_64-linux-gnu",
  build_opt = "-g -O0",
  warnings  = "-Wunused-variable -Werror",
  depfile   = "-MMD -MF {file_out}.d",
  defines   = "-DCONFIG_DEBUG",
  includes  = "-I. -Isymlinks",
)

rule_compile_cpp = hancho.Config(
  prototype = base_config,
  command   = "{toolchain}-g++ {cpp_std} {warnings} {depfile} {build_opt} {includes} {defines} -c {file_in} -o {file_out}",
  cpp_std   = "-std=gnu++2a",
)

compile_c = hancho.Config(
  prototype = base_config,
  command   = "{toolchain}-gcc {warnings} {depfile} {build_opt} {includes} {defines} -c {file_in} -o {file_out}",
)

link_c_lib = hancho.Config(
  prototype = base_config,
  command   = "ar rcs {file_out} {join(files_in)}",
)

link_c_bin = hancho.Config(
  prototype = base_config,
  command   = "{toolchain}-g++ {build_opt} {join(files_in)} {libs} -o {file_out}",
  libs      = "",
)

rule_compile_cpp(files_in = "src/test.cpp", files_out = "obj/test.o")
rule_compile_cpp(files_in = "src/main.cpp", files_out = "obj/main.o")
link_c_bin(files_in = ["obj/main.o", "obj/test.o"], files_out = "bin/main")
