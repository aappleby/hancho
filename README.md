# Hancho

"班長, hanchō - "Squad leader”, from 19th c. Mandarin 班長 (bānzhǎng, “team leader”)"

Hancho is a simple, pleasant build system with few moving parts.

Hancho fits comfortably in 500 lines of Python and requires no installation, just copy-paste it into your source tree.

Hancho is inspired by Ninja and Bazel.

Like Ninja, it knows nothing about your build tools and is only trying to assemble and run commands as fast as possible.

Unlike Ninja, you don't need a separate build rule invocation for every single output file.

Like Bazel, you invoke build rules by calling them as if they were functions with keyword arguments.

Unlike Bazel, you can create build rules that call arbitary Python code (for better or worse).

Hancho should suffice for small to medium sized projects.

[Tutorial Here](tutorial)

[Some Additional Documentation Here](docs)

## Installation

``` bash
wget https://raw.githubusercontent.com/aappleby/hancho/main/hancho.py
chmod +x hancho.py
./hancho.py
```

## Simple Example
```py
# examples/hello_world/build.hancho

config.set(build_dir = "build")

compile = Rule(
  desc      = "Compile {files_in} -> {files_out}",
  command   = "g++ -MMD -c {files_in} -o {files_out}",
  files_out = "{swap_ext(files_in, '.o')}",
  depfile   = "{swap_ext(files_out, '.d')}",
)

link = Rule(
  desc      = "Link {files_in} -> {files_out}",
  command   = "g++ {files_in} -o {files_out}",
)

main_o = compile("main.cpp")
main_app = link(main_o, "app")
```
```cpp
// examples/hello_world/main.cpp
#include <stdio.h>

int main(int argc, char** argv) {
  printf("Hello World\n");
  return 0;
}
```
```sh
user@host:~/hancho/examples/hello_world$ ../../hancho.py --verbose
[1/2] Compile main.cpp -> build/main.o
Reason: Rebuilding ['build/main.o'] because some are missing
g++ -MMD -c main.cpp -o build/main.o
[2/2] Link build/main.o -> build/app
Reason: Rebuilding ['build/app'] because some are missing
g++ build/main.o -o build/app
hancho: BUILD PASSED

user@host:~/hancho/examples/hello_world$ build/app
Hello World

user@host:~/hancho/examples/hello_world$ ../../hancho.py --verbose
hancho: BUILD CLEAN
```
