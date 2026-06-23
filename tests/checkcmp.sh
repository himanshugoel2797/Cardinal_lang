#!/bin/sh
# Checker differential harness for a single Cardinal program (NEGATIVE tests).
#
#   sh tests/checkcmp.sh <program.cardinal>
#
# Runs the Python reference checker (--check-only) and the Cardinal checker
# (checkmod.cardinal) over the SAME file and reports whether both AGREE on the
# ok/err verdict. Used for programs that SHOULD fail type-checking (and a few
# that should pass): the two checkers must reach the same verdict.
#
# Prints AGREE/DIFF and the verdicts. Exit 0 on AGREE, 1 on DIFF.
root="$(cd "$(dirname "$0")/.." && pwd)"
prog="$1"
[ -z "$prog" ] && { echo "usage: checkcmp.sh <prog.cardinal>" >&2; exit 2; }
case "$prog" in /*) ;; *) prog="$(pwd)/$prog" ;; esac

py=$(python3 "$root/bootstrap/cardinal.py" "$prog" --check-only >/dev/null 2>&1 && echo ok || echo err)
card=$(cd "$root/compiler" && python3 "$root/bootstrap/cardinal.py" checkmod.cardinal "$prog" "$root/lib" "$root/examples" "$root/compiler" 2>&1 | head -1 | grep -q '^ok' && echo ok || echo err)

if [ "$py" = "$card" ]; then
  echo "AGREE  $prog  py=$py card=$card"
  exit 0
else
  echo "DIFF   $prog  py=$py card=$card"
  echo "--- python checker output ---"
  python3 "$root/bootstrap/cardinal.py" "$prog" --check-only 2>&1 | head -10
  echo "--- cardinal checker output ---"
  (cd "$root/compiler" && python3 "$root/bootstrap/cardinal.py" checkmod.cardinal "$prog" "$root/lib" "$root/examples" "$root/compiler" 2>&1 | head -10)
  exit 1
fi
