#!/bin/sh
# Run every *.cardinal under the given test dirs through run3.sh (e2e three-way)
# and print a compact PASS/FAIL summary. Usage: sh tests/sweep.sh <dir>...
root="$(cd "$(dirname "$0")/.." && pwd)"
pass=0; fail=0; faillist=""
for d in "$@"; do
  for f in "$root/$d"/*.cardinal; do
    [ -e "$f" ] || continue
    if sh "$root/tests/run3.sh" "$f" >/dev/null 2>&1; then
      pass=$((pass+1))
    else
      fail=$((fail+1)); faillist="$faillist $f"
    fi
  done
done
echo "SWEEP: PASS=$pass FAIL=$fail"
[ -n "$faillist" ] && { echo "FAILURES:"; for x in $faillist; do echo "  $x"; done; }
[ "$fail" -eq 0 ]
