#!/usr/bin/env python3
"""
Cardinal driver: type-check, then interpret.

  cardinal.py [--no-check] [--check-only] <program.cardinal>

The standard library under ../lib is always on the module search path, so
programs can `import math`, `import array`, etc.
"""

import os
import sys

from interpreter import Interp, CardinalError, Panic
from typecheck import Checker

HERE = os.path.dirname(os.path.abspath(__file__))
STDLIB = os.path.normpath(os.path.join(HERE, "..", "lib"))


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    if len(args) < 1:
        print("usage: cardinal.py [--no-check] [--check-only] <program.cardinal> [args...]",
              file=sys.stderr)
        return 2
    path = args[0]
    program_args = args[1:]        # exposed to the program via sys::args()
    search = [os.getcwd(), STDLIB]

    if "--no-check" not in flags:
        try:
            errors = Checker(list(search)).check_main(path)
        except CardinalError as e:
            print(f"type error: {e}", file=sys.stderr)
            return 1
        if errors:
            for msg in errors:
                print(f"type error: {msg}", file=sys.stderr)
            return 1

    if "--check-only" in flags:
        print("ok")
        return 0

    interp = Interp(search_dirs=list(search))
    interp.program_args = program_args
    try:
        return interp.run(path)
    except CardinalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Panic as e:
        print(f"panic: {e.msg}", file=sys.stderr)
        return 101


if __name__ == "__main__":
    sys.exit(main(sys.argv))
