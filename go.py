#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import time


def clean(root="."):
    dirs = []
    for dirpath, dirnames, _ in os.walk(root):
        for name in dirnames:
          if name in {"build", "__pycache__", ".pytest_cache"}:
              dirs.append(os.path.join(dirpath, name))
    for path in dirs:
        shutil.rmtree(path, ignore_errors=True)


def run(*cmd):
    sys.stdout.flush()  # flush headers before the child writes to the same fd
    start = time.perf_counter()
    subprocess.run(cmd)
    print(f"Command took {time.perf_counter() - start:.3f} seconds")


def main():
    args = sys.argv[1:]
    clean()
    os.system("cls" if os.name == "nt" else "clear")

    print("\nClean run")
    run(sys.executable, "hancho.py", *args)

    print("\nDirty run")
    run(sys.executable, "hancho.py", *args)

    print()
    run(sys.executable, "-m", "unittest")


if __name__ == "__main__":
    main()
