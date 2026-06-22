#!/bin/sh
# Compile a Cardinal program to a native binary using the CARDINAL-WRITTEN code
# generator (compiler/codegen.cardinal), run by the bootstrap interpreter, then
# link against the C runtime and run it. Stage-0 of self-hosting: the bootstrap
# runs the Cardinal compiler; the Cardinal compiler emits the native artifact.
#
#   sh compiler/ccrun.sh <program.cardinal> [-o out] [--run]
#
# Default: build to ./<name> and run it.
set -e
root="$(cd "$(dirname "$0")/.." && pwd)"
src="$1"; shift || true
base="$(basename "$src" .cardinal)"
out="./$base"
run=1
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2;;
    --emit) run=emit; shift;;
    --no-run) run=0; shift;;
    *) shift;;
  esac
done
cfile="$(mktemp /tmp/cardinal_XXXXXX.c)"
# emit C via the Cardinal codegen
( cd "$root/compiler" && python3 "$root/bootstrap/cardinal.py" emitc.cardinal "$src" ) > "$cfile"
if [ "$run" = emit ]; then cat "$cfile"; rm -f "$cfile"; exit 0; fi
cc -I "$root/bootstrap/runtime" -o "$out" "$cfile" \
   "$root/bootstrap/runtime/cardinal_rt.c" "$root/bootstrap/runtime/cardinal_gc.c"
rm -f "$cfile"
[ "$run" = 1 ] && exec "$out"
echo "$out"
