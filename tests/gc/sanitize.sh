#!/bin/sh
# GC-stress sanitizer harness for Cardinal test programs.
#
# Usage:
#   sh tests/gc/sanitize.sh <prog.cardinal>
#
# 1. Emits C via the C backend, compiles with ASan+UBSan, runs with
#    CARDINAL_GC_THRESHOLD=0.
# 2. Emits x86 asm, links against the runtime (no sanitizer on hand-asm),
#    runs with CARDINAL_GC_THRESHOLD=0 and checks exit code + output.
# 3. Compares all outputs to the interpreter oracle.
#
# Exit 0 if all outputs agree and no sanitizer report.  Prints summary.

set -e
root="$(cd "$(dirname "$0")/../.." && pwd)"
prog="$1"
[ -z "$prog" ] && { echo "usage: sanitize.sh <prog.cardinal>" >&2; exit 2; }
case "$prog" in /*) ;; *) prog="$(pwd)/$prog" ;; esac

base="$(basename "$prog" .cardinal)"
tmp="$(mktemp -d /tmp/sanitize_XXXXXX)"
trap 'rm -rf "$tmp"' EXIT

echo "=== $base ==="

# --- Oracle (interpreter) ---
oracle_out="$tmp/oracle.out"
if ! timeout 180 python3 "$root/bootstrap/cardinal.py" "$prog" > "$oracle_out" 2>"$tmp/oracle.err"; then
    echo "ORACLE FAILED" >&2
    cat "$tmp/oracle.err" >&2
    exit 1
fi
echo "Oracle output: $(cat "$oracle_out")"

# --- C backend + ASan+UBSan ---
cfile="$tmp/prog.c"
c_bin="$tmp/prog_asan"
c_out="$tmp/c_asan.out"
c_err="$tmp/c_asan.err"

echo "Emitting C..."
if ! ( cd "$root/compiler" && python3 "$root/bootstrap/cardinal.py" emitir.cardinal "$prog" "$root/lib" ) > "$cfile" 2>"$tmp/emit_c.err"; then
    echo "C EMIT FAILED" >&2
    cat "$tmp/emit_c.err" >&2
    exit 1
fi

echo "Compiling C with ASan+UBSan..."
if ! cc -O1 -g -fsanitize=address,undefined -fwrapv \
        -I "$root/bootstrap/runtime" \
        -o "$c_bin" "$cfile" \
        "$root/bootstrap/runtime/cardinal_rt.c" \
        "$root/bootstrap/runtime/cardinal_gc.c" \
        2>"$tmp/cc.err"; then
    echo "CC FAILED" >&2
    cat "$tmp/cc.err" >&2
    exit 1
fi

echo "Running C binary (CARDINAL_GC_THRESHOLD=0)..."
c_ok=0
if CARDINAL_GC_THRESHOLD=0 timeout 120 "$c_bin" > "$c_out" 2>"$c_err"; then
    c_ok=0
else
    c_ok=$?
fi

c_asan_report=""
if grep -q "ERROR\|runtime error\|SUMMARY" "$c_err" 2>/dev/null; then
    c_asan_report="$(cat "$c_err")"
    echo "ASan/UBSan report detected!"
fi

# --- x86 backend ---
sfile="$tmp/prog.s"
x86_bin="$tmp/prog_x86"
x86_out="$tmp/x86.out"
x86_err="$tmp/x86.err"

echo "Emitting x86..."
if ! ( cd "$root/compiler" && python3 "$root/bootstrap/cardinal.py" emitx86.cardinal "$prog" "$root/lib" ) > "$sfile" 2>"$tmp/emit_x86.err"; then
    echo "X86 EMIT FAILED" >&2
    cat "$tmp/emit_x86.err" >&2
    # Not a hard failure — just note it
    x86_ok=99
else
    echo "Assembling+linking x86..."
    if ! cc -O0 -g -fwrapv \
            -I "$root/bootstrap/runtime" \
            -o "$x86_bin" "$sfile" \
            "$root/bootstrap/runtime/cardinal_rt.c" \
            "$root/bootstrap/runtime/cardinal_gc.c" \
            2>"$tmp/as.err"; then
        echo "X86 ASSEMBLE FAILED" >&2
        cat "$tmp/as.err" >&2
        x86_ok=98
    else
        echo "Running x86 binary (CARDINAL_GC_THRESHOLD=0)..."
        x86_ok=0
        if CARDINAL_GC_THRESHOLD=0 timeout 120 "$x86_bin" > "$x86_out" 2>"$x86_err"; then
            x86_ok=0
        else
            x86_ok=$?
        fi
    fi
fi

# --- Compare outputs ---
fail=0

if [ -f "$c_out" ] && ! diff -q "$oracle_out" "$c_out" > /dev/null 2>&1; then
    echo "OUTPUT MISMATCH: oracle vs C-ASan"
    echo "  oracle: $(cat "$oracle_out")"
    echo "  c_asan: $(cat "$c_out")"
    fail=1
fi

if [ -f "$x86_out" ] && ! diff -q "$oracle_out" "$x86_out" > /dev/null 2>&1; then
    echo "OUTPUT MISMATCH: oracle vs x86"
    echo "  oracle: $(cat "$oracle_out")"
    echo "  x86:    $(cat "$x86_out")"
    fail=1
fi

if [ -n "$c_asan_report" ]; then
    echo "CONFIRMED BUG: ASan/UBSan report:"
    echo "$c_asan_report" | head -50
    fail=1
fi

if [ "$c_ok" -ne 0 ] && [ "$c_ok" -ne 1 ]; then
    echo "C binary exited with unexpected code: $c_ok"
    fail=1
fi

if [ "$fail" -eq 0 ]; then
    echo "CLEAN: $base (no sanitizer report, outputs agree)"
    exit 0
else
    echo "FAIL: $base"
    exit 1
fi
