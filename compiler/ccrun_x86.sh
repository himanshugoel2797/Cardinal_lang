#!/bin/sh
# Compile a Cardinal program to a native binary via the x86_64 ASSEMBLER backend
# (compiler/emitx86.cardinal: lower -> IR -> x86_64 asm), run by the bootstrap
# interpreter, then assemble + link against the C runtime and run it.
#
#   sh compiler/ccrun_x86.sh <program.cardinal> [-o out] [--emit] [--no-run] [args...]
#
# Default: build to ./<name> and run it.
set -e
root="$(cd "$(dirname "$0")/.." && pwd)"
src="$1"; shift || true
case "$src" in
  /*) abssrc="$src" ;;
  *)  abssrc="$(cd "$(dirname "$src")" && pwd)/$(basename "$src")" ;;
esac
base="$(basename "$src" .cardinal)"
mkdir -p "$root/build"
out="$root/build/$base"
run=1
runargs=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2;;
    --emit) run=emit; shift;;
    --no-run) run=0; shift;;
    *) runargs="$runargs $1"; shift;;
  esac
done
sfile="$(mktemp /tmp/cardinal_XXXXXX.s)"
errfile="$(mktemp /tmp/cardinal_XXXXXX.err)"
if ! ( cd "$root/compiler" && python3 "$root/bootstrap/cardinal.py" emitx86.cardinal "$abssrc" "$root/lib" ) > "$sfile" 2>"$errfile"; then
  echo "cardinal: x86 compilation failed:" >&2
  cat "$sfile" "$errfile" >&2
  rm -f "$sfile" "$errfile"
  exit 1
fi
rm -f "$errfile"
if [ "$run" = emit ]; then cat "$sfile"; rm -f "$sfile"; exit 0; fi
cc -O2 -fwrapv -I "$root/bootstrap/runtime" -o "$out" "$sfile" \
   "$root/bootstrap/runtime/cardinal_rt.c" "$root/bootstrap/runtime/cardinal_gc.c"
rm -f "$sfile"
if [ "$run" = 1 ]; then exec "$out" $runargs; fi
echo "$out"
