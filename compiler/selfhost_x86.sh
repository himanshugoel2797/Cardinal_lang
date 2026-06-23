#!/bin/sh
# x86_64 self-host check.
#
# The Python-hosted Cardinal compiler emits x86_64 ASSEMBLY for the WHOLE compiler
# (compiler/emitx86.cardinal driving the x86 backend over compiler/emitir.cardinal),
# which is assembled into a NATIVE x86 compiler `xcc`. `xcc` then compiles the
# compiler to C and must produce byte-identical C to the Python-hosted compiler —
# i.e. the x86 backend compiled the entire compiler correctly.
#
#   sh compiler/selfhost_x86.sh [--keep]
set -e
root="$(cd "$(dirname "$0")/.." && pwd)"
comp="$root/compiler"
rt="$root/bootstrap/runtime"
tmp="$(mktemp -d /tmp/cardinal_x86host_XXXXXX)"
keep=0
[ "$1" = "--keep" ] && keep=1

echo "[1/4] Python-hosted compiler emits x86_64 asm of the compiler"
( cd "$comp" && python3 "$root/bootstrap/cardinal.py" emitx86.cardinal "$comp/emitir.cardinal" ) > "$tmp/xcc.s"

echo "[2/4] assemble the native x86 compiler (xcc)"
cc -O2 -fwrapv -I "$rt" -o "$tmp/xcc" "$tmp/xcc.s" "$rt/cardinal_rt.c" "$rt/cardinal_gc.c"

echo "[3/4] xcc compiles the compiler -> C; Python-hosted -> C"
( cd "$comp" && "$tmp/xcc" "$comp/emitir.cardinal" ) > "$tmp/by_x86.c"
( cd "$comp" && python3 "$root/bootstrap/cardinal.py" emitir.cardinal "$comp/emitir.cardinal" ) > "$tmp/by_py.c"

echo "[4/4] compare"
if diff -q "$tmp/by_x86.c" "$tmp/by_py.c" >/dev/null; then
  echo "X86 SELF-HOST OK: the native x86 compiler emits identical C to the"
  echo "Python-hosted compiler ($(wc -l < "$tmp/by_x86.c") lines of C)."
  [ "$keep" = 1 ] && echo "artifacts kept in $tmp" || rm -rf "$tmp"
  exit 0
else
  echo "X86 SELF-HOST FAILED: emitted C differs. artifacts in $tmp"
  exit 1
fi
