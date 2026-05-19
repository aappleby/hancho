// This file should _not_ compile, but it also should not be built because it's not in the root
// Hancho repo and it's not required by anything.

#include <stdio.h>

int main(int argc, char** argv) {
  typo = "this line is an error";
  printf("Hello World");
  return 0;
}
