#!/usr/bin/env python3
"""
Cardinal compiler driver: type-check -> lower to IR -> backend -> (build/run).

  cardinalc.py [options] <program.cardinal>

  --backend NAME   target backend (default: c)
  --emit           write the backend artifact (e.g. .c) next to the source and stop
  -o PATH          output executable path (default: ./<name>)
  --run            build and run, propagating the exit code
"""

import os
import sys
import tempfile

from interpreter import lex, Parser, CardinalError
from typecheck import Checker
from backend import get_backend
from lower import Lowerer

HERE = os.path.dirname(os.path.abspath(__file__))
STDLIB = os.path.normpath(os.path.join(HERE, "..", "lib"))


def compile_module(path, backend_name="c"):
    search = [os.path.dirname(os.path.abspath(path)) or os.getcwd(), STDLIB, os.getcwd()]
    checker = Checker(list(search))
    errors = checker.check_main(path)
    if errors:
        raise CardinalError("type errors:\n  " + "\n  ".join(errors))
    irmod = Lowerer(checker).lower_all()
    backend = get_backend(backend_name)
    return backend, backend.emit(irmod)


def main(argv):
    args, flags, kw = [], set(), {}
    it = iter(argv[1:])
    for a in it:
        if a == "-o":
            kw["o"] = next(it)
        elif a == "--backend":
            kw["backend"] = next(it)
        elif a.startswith("--"):
            flags.add(a[2:])
        else:
            args.append(a)
    if len(args) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    path = args[0]
    backend_name = kw.get("backend", "c")

    try:
        backend, artifact = compile_module(path, backend_name)
    except CardinalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    base = os.path.splitext(os.path.basename(path))[0]

    if "emit" in flags:
        out = os.path.join(os.path.dirname(os.path.abspath(path)),
                           base + backend.output_suffix)
        with open(out, "w") as f:
            f.write(artifact)
        print(out)
        return 0

    exe = kw.get("o", os.path.join(os.getcwd(), base))
    with tempfile.TemporaryDirectory() as d:
        cpath = os.path.join(d, base + backend.output_suffix)
        with open(cpath, "w") as f:
            f.write(artifact)
        try:
            backend.build(cpath, exe)
        except Exception as e:
            print(f"build error: {e}", file=sys.stderr)
            return 1

    if "run" in flags:
        import subprocess
        return subprocess.run([exe]).returncode
    print(exe)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
