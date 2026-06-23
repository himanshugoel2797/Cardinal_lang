#!/bin/sh
# Three-way differential harness for a single Cardinal program.
#
#   sh tests/run3.sh <program.cardinal>
#
# Runs the program through (1) the Python interpreter ORACLE, (2) the C backend,
# (3) the x86_64 backend. Compares stdout and a coarse exit-status class
# (0=ok, 1=panic/nonzero). Prints PASS if all three agree on stdout AND status
# class, otherwise FAIL with the divergence. Exit 0 on PASS, 1 on any divergence,
# 2 on a harness/compile error that is NOT a real divergence (e.g. the program
# legitimately fails to type-check in ALL paths — caller should use a checker
# harness for negative tests instead).
#
# stderr (panic message text) is intentionally NOT compared: it is documented to
# differ across backends. Only stdout + ok/panic class are the correctness gate.
root="$(cd "$(dirname "$0")/.." && pwd)"
prog="$1"
[ -z "$prog" ] && { echo "usage: run3.sh <prog.cardinal>" >&2; exit 2; }
case "$prog" in /*) ;; *) prog="$(pwd)/$prog" ;; esac

tmp="$(mktemp -d /tmp/run3_XXXXXX)"
trap 'rm -rf "$tmp"' EXIT

# class: 0 if exit 0, else 1 (panic / error). We also record raw code.
run_one() {
  # $1 = label, $2... = command
  label="$1"; shift
  "$@" > "$tmp/$label.out" 2> "$tmp/$label.err"
  code=$?
  echo "$code" > "$tmp/$label.code"
  if [ "$code" -eq 0 ]; then echo 0 > "$tmp/$label.class"; else echo 1 > "$tmp/$label.class"; fi
}

run_one interp timeout 180 python3 "$root/bootstrap/cardinal.py" "$prog"
run_one c      timeout 240 sh "$root/compiler/ccrun.sh" "$prog"
run_one x86    timeout 240 sh "$root/compiler/ccrun_x86.sh" "$prog"

ic=$(cat "$tmp/interp.class"); cc=$(cat "$tmp/c.class"); xc=$(cat "$tmp/x86.class")

fail=0
msg=""

# Compare stdout: interp vs c, interp vs x86
if ! diff -q "$tmp/interp.out" "$tmp/c.out" >/dev/null; then
  fail=1; msg="$msg\n[STDOUT DIFFERS: interp vs C]"
fi
if ! diff -q "$tmp/interp.out" "$tmp/x86.out" >/dev/null; then
  fail=1; msg="$msg\n[STDOUT DIFFERS: interp vs x86]"
fi
# Compare ok/panic class
if [ "$ic" != "$cc" ] || [ "$ic" != "$xc" ]; then
  fail=1; msg="$msg\n[STATUS CLASS DIFFERS: interp=$ic C=$cc x86=$xc]"
fi

if [ "$fail" -eq 0 ]; then
  echo "PASS  $prog  (status class=$ic)"
  exit 0
fi

echo "FAIL  $prog"
printf '%b\n' "$msg"
echo "--- interp (code $(cat $tmp/interp.code)) ---"; cat "$tmp/interp.out"
echo "--- C      (code $(cat $tmp/c.code)) ---";      cat "$tmp/c.out"
echo "--- x86    (code $(cat $tmp/x86.code)) ---";    cat "$tmp/x86.out"
echo "=== interp stderr (tail) ==="; tail -5 "$tmp/interp.err"
echo "=== C stderr (tail) ===";      tail -5 "$tmp/c.err"
echo "=== x86 stderr (tail) ===";    tail -5 "$tmp/x86.err"
echo "--- diff interp->C ---";   diff "$tmp/interp.out" "$tmp/c.out"   | head -40
echo "--- diff interp->x86 ---"; diff "$tmp/interp.out" "$tmp/x86.out" | head -40
exit 1
