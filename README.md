# Hancho

"班長, hanchō - "Squad leader”, from 19th c. Mandarin 班長 (bānzhǎng, “team leader”)"

Hancho is the smallest build system I can make that fits my needs.

It focuses on these features:

1. Easy construction of commands via text templates, similar to Python f-strings.
2. Minimal, parallel, fast rebuilds.
3. Zero "magic" - you control every command run.
4. Single file with no dependencies outside python3 - just copy-paste it into your repo.

The resulting ```hancho.py``` is under 500 lines of code and should suffice for
small to medium sized projects.

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

hancho.config.set(build_dir = "build")

compile = hancho.Rule(
  desc      = "Compile {files_in} -> {files_out}",
  command   = "g++ -MMD -c {files_in} -o {files_out}",
  files_out = "{swap_ext(files_in, '.o')}",
  depfile   = "{swap_ext(files_in, '.d')}",
)

link = hancho.Rule(
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
[1/2] Compile ['main.cpp'] -> ['build/main.o']
Reason: Rebuilding ['build/main.o'] because some are missing
g++ -c main.cpp -o build/main.o
[2/2] Link ['build/main.o'] -> ['build/app']
Reason: Rebuilding ['build/app'] because some are missing
g++ build/main.o -o build/app

user@host:~/hancho/examples/hello_world$ build/app
Hello World

user@host:~/hancho/examples/hello_world$ ../../hancho.py --verbose
hancho: no work to do.
```
