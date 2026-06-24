#!/bin/sh
# Self-host fixed-point check (DESIGN §11).
#
# Stage A: the Python-hosted Cardinal compiler (bootstrap interpreter running
#          compiler/emitir.cardinal) emits C for the WHOLE compiler -> cc1.c,
#          which is compiled to the native compiler `cc1`.
# Stage B: `cc1` recompiles the same compiler source -> cc2.c.
# Fixed point: cc1.c and cc2.c must be byte-identical (so cc1 == cc2). The
# emitted C is the meaningful artifact; native binaries built from it are
# identical too, modulo the C toolchain embedding the input file PATH.
#
#   sh compiler/selfhost.sh [--keep]
set -e
root="$(cd "$(dirname "$0")/.." && pwd)"
comp="$root/compiler"
rt="$root/bootstrap/runtime"
tmp="$(mktemp -d /tmp/cardinal_selfhost_XXXXXX)"
keep=0
[ "$1" = "--keep" ] && keep=1

echo "[1/4] Python-hosted compiler emits the compiler -> cc1.c"
( cd "$comp" && python3 "$root/bootstrap/cardinal.py" emitir.cardinal "$comp/emitir.cardinal" "$root/lib" ) > "$tmp/cc1.c"

echo "[2/4] build native cc1 from cc1.c"
cc -O2 -fwrapv -I "$rt" -o "$tmp/cc1" "$tmp/cc1.c" "$rt/cardinal_rt.c" "$rt/cardinal_gc.c"

echo "[3/4] cc1 recompiles the compiler -> cc2.c"
( cd "$comp" && "$tmp/cc1" "$comp/emitir.cardinal" "$root/lib" ) > "$tmp/cc2.c"

echo "[4/4] compare cc1.c vs cc2.c"
if diff -q "$tmp/cc1.c" "$tmp/cc2.c" >/dev/null; then
  echo "SELF-HOST OK: cc1.c == cc2.c byte-identical ($(wc -l < "$tmp/cc1.c") lines of C)"
  [ "$keep" = 1 ] && echo "artifacts kept in $tmp" || rm -rf "$tmp"
  exit 0
else
  echo "SELF-HOST FAILED: cc1.c != cc2.c"
  echo "artifacts in $tmp"
  exit 1
fi
