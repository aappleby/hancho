#!/usr/bin/python3

import sys
import glob
import os
import random
import concurrent
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import ThreadPoolExecutor
import asyncio
import time
from datetime import datetime

sys.path.append("symlinks/tinybuild")
import tinybuild

tinybuild.global_config["toolchain"]  = "x86_64-linux-gnu"
tinybuild.global_config["build_type"] = "-g -O0"
tinybuild.global_config["warnings"]   = "-Wunused-variable -Werror"
tinybuild.global_config["depfile"]    = "-MMD -MF {file_out}.d"
tinybuild.global_config["defines"]    = "-DCONFIG_DEBUG"
tinybuild.global_config["cpp_std"]    = "-std=gnu++2a"


"""
_print=print
def print(*args, **kw):
    _print("[%s]" % (datetime.now()),*args, **kw)

################################################################################

proc_sem = asyncio.Semaphore(os.cpu_count())

async def await_deps(input_deps):
    dep_filenames = []
    for f in input_deps:
       if type(f) is asyncio.Task:
        dep_filenames.append(await f)
       else:
        dep_filenames.append(f)
    return dep_filenames

#----------------------------------------

async def dummy_compile(filename, time, input_deps = []):
    dep_filenames = await await_deps(input_deps)

    async with proc_sem:
      print(f"start processing {filename} @ {time}")
      print(dep_filenames)
      proc = await asyncio.create_subprocess_shell(f"sleep {time}")
      result = await proc.wait()
      print(f"finished compiling {filename} @ {time} - result {result}")
      return filename

#----------------------------------------

async def dummy_link(filename, input_deps = []):
    print(f"    link {filename} waiting on futures")
    dep_filenames = await await_deps(input_deps)
    print(f"    link {filename} waiting on futures done")

    #time = round(random.random(), 2)
    time = 0
    async with proc_sem:
      print(f"    link {filename} starting")
      print(dep_filenames)
      proc = await asyncio.create_subprocess_shell(f"sleep {time}")
      result = await proc.wait()
      print(f"    finished linking {filename} @ {time} - result {result}")
      return filename

#----------------------------------------

async def main():
    futures1 = []
    for i in range(20):
      future = asyncio.create_task(dummy_compile(f"file{i}", round(random.random(), 2)))
      futures1.append(future)

    futures2 = []
    for i in range(20):
      future = asyncio.create_task(dummy_compile(f"slow file{i}", round(random.random() + 2, 2)))
      futures2.append(future)

    link1 = asyncio.create_task(dummy_link("bin1", futures1))
    link2 = asyncio.create_task(dummy_link("bin2", futures2))

    await asyncio.gather(link1, link2)

    print(await link1)
    print(await link2)

#----------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
"""

################################################################################

compile_cpp = tinybuild.map_async(
  desc      = "Compiling C++ {file_in} => {file_out}",
  command   = "{toolchain}-g++ {opts} {includes} {defines} -c {file_in} -o {file_out}",
  opts      = "{cpp_std} {warnings} {depfile} {build_type}",
  includes  = "-Isymlinks/metrolib -Isymlinks/metron -I. -Isymlinks",
)

link_c_bin  = tinybuild.reduce_async(
  desc      = "Linking {file_out}",
  command   = "{toolchain}-g++ {opts} {join(files_in)} {join(deps)} {libraries} -o {file_out}",
  opts      = "{build_type}",
  deps      = [],
  libraries = "",
)

async def build():
  obj_main_o_futures = compile_cpp("src/main.cpp", "obj/main.o")
  obj_test_o_futures = compile_cpp("src/test.cpp", "obj/test.o")

  bin_main_futures = link_c_bin([obj_main_o_futures, obj_test_o_futures], "bin/main")

  print(await obj_main_o_futures[0])
  print(await obj_test_o_futures[0])
  print(await bin_main_futures[0])

asyncio.run(build())

################################################################################

"""

compile_cpp = tinybuild.map(
  desc      = "Compiling C++ {file_in} => {file_out}",
  command   = "{toolchain}-g++ {opts} {includes} {defines} -c {file_in} -o {file_out}",
  opts      = "{cpp_std} {warnings} {depfile} {build_type}",
  includes  = "-Isymlinks/metrolib -Isymlinks/metron -I. -Isymlinks",
)

compile_c   = tinybuild.map(
  desc      = "Compiling C {file_in} => {file_out}",
  command   = "{toolchain}-gcc {opts} {includes} {defines} -c {file_in} -o {file_out}",
  opts      = "{warnings} {depfile} {build_type}",
  includes  = "-Isymlinks/metrolib -Isrc -I. -Isymlinks",
)

link_c_lib = tinybuild.reduce(
  desc      = "Bundling {file_out}",
  command   = "ar rcs {file_out} {join(files_in)}",
)

link_c_bin  = tinybuild.reduce(
  desc      = "Linking {file_out}",
  command   = "{toolchain}-g++ {opts} {join(files_in)} {join(deps)} {libraries} -o {file_out}",
  opts      = "{build_type}",
  deps      = [],
  libraries = "",
)

def obj_name(x):
  return "obj/" + tinybuild.swap_ext(x, ".o")

def compile_dir(dir):
  files = glob.glob(dir + "/*.cpp") + glob.glob(dir + "/*.c")
  objs  = [obj_name(x) for x in files]
  compile_cpp(files, objs)
  return objs

obj_main_o_futures = compile_cpp("src/main.cpp", "obj/main.o")
obj_test_o_futures = compile_cpp("src/test.cpp", "obj/test.o")
link_c_bin([obj_main_o_futures, obj_test_o_futures], "bin/main")

tinybuild.finish()
"""
