#!/bin/sh
# Compile a Cardinal program to a native binary using the CARDINAL-WRITTEN
# compiler pipeline (compiler/emitir.cardinal: lower -> IR -> backend_c), run by
# the bootstrap interpreter, then link against the C runtime and run it.
# Stage-0 of self-hosting: the bootstrap runs the Cardinal compiler; the Cardinal
# compiler emits the native artifact.
#
#   sh compiler/ccrun.sh <program.cardinal> [-o out] [--emit] [--no-run]
#
# Default: build to ./<name> and run it.
set -e
root="$(cd "$(dirname "$0")/.." && pwd)"
src="$1"; shift || true
# resolve src to an absolute path so imports resolve regardless of cwd
case "$src" in
  /*) abssrc="$src" ;;
  *)  abssrc="$(cd "$(dirname "$src")" && pwd)/$(basename "$src")" ;;
esac
base="$(basename "$src" .cardinal)"
mkdir -p "$root/build"
out="$root/build/$base"
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
errfile="$(mktemp /tmp/cardinal_XXXXXX.err)"
# emit C via the Cardinal compiler pipeline. The driver type-checks first and
# refuses to emit (non-zero exit) on a type error; surface diagnostics instead
# of feeding them to cc. Type errors land on stdout (cfile), panics on stderr.
if ! ( cd "$root/compiler" && python3 "$root/bootstrap/cardinal.py" emitir.cardinal "$abssrc" "$root/lib" ) > "$cfile" 2>"$errfile"; then
  echo "cardinal: compilation failed:" >&2
  cat "$cfile" "$errfile" >&2
  rm -f "$cfile" "$errfile"
  exit 1
fi
rm -f "$errfile"
if [ "$run" = emit ]; then cat "$cfile"; rm -f "$cfile"; exit 0; fi
cc -O2 -fwrapv -I "$root/bootstrap/runtime" -o "$out" "$cfile" \
   "$root/bootstrap/runtime/cardinal_rt.c" "$root/bootstrap/runtime/cardinal_gc.c"
rm -f "$cfile"
[ "$run" = 1 ] && exec "$out"
echo "$out"
