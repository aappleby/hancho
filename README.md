# tinybuild
Tinybuild is the smallest build system I can make that fits my needs.
It focuses on a small set of features:
1. Easy construction of commands via Python F-strings
2. Minimal rebuilds
3. Zero "magic"

It does _not_ build dependency graphs - dependencies are explicit via declaration order.

Tinybuild should suffice for small to medium sized projects.
