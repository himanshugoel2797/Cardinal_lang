#!/bin/sh
# Differential test: compare the Cardinal type checker (checker.cardinal, run via
# the bootstrap interpreter) against the Python reference checker (--check-only)
# on every example and every compiler module. Run from the repo root.
#
#   sh compiler/difftest.sh
#
# Prints AGREE/DIFF per file and a summary; exits non-zero if any verdict differs.
set -e
cd "$(dirname "$0")/.."
pass=0; fail=0
for f in examples/demo examples/features examples/closures examples/sumtest \
         examples/strtest examples/vectest examples/maptest examples/gcstress \
         examples/cdemo examples/usestd \
         compiler/lexer compiler/parser compiler/checker; do
  py=$(python3 bootstrap/cardinal.py "$f.cardinal" --check-only >/dev/null 2>&1 && echo ok || echo err)
  card=$(cd compiler && python3 ../bootstrap/cardinal.py checkmod.cardinal "../$f.cardinal" ../lib ../examples ../compiler 2>&1 | head -1 | grep -q '^ok' && echo ok || echo err)
  if [ "$py" = "$card" ]; then verdict=AGREE; pass=$((pass+1)); else verdict="DIFF"; fail=$((fail+1)); fi
  printf '%-26s py=%-3s card=%-3s  %s\n' "$f" "$py" "$card" "$verdict"
done
echo "AGREE=$pass DIFF=$fail"
[ "$fail" -eq 0 ]
