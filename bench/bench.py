#!/usr/bin/env python3
"""
Cardinal profiling harness.

Measures two things across all execution paths:

  1. COMPILE TIME  -- how long the compiler takes to turn source into a binary,
                      broken into phases (compiler front-end -> codegen text,
                      then the system `cc` link step), for both the C and the
                      x86_64 back-ends, and for both ways of *running* the
                      compiler: hosted on the slow Python bootstrap interpreter
                      vs. the native self-hosted compiler binary.

  2. RUN TIME      -- how fast the generated code runs, for the C back-end, the
                      x86_64 back-end, and the tree-walking interpreter, reported
                      as throughput (work-units / second) so the three can be
                      compared even when they ran different workload sizes.

Execution paths (see compiler/ccrun.sh, ccrun_x86.sh, bootstrap/cardinal.py):

  interp        python3 bootstrap/cardinal.py <prog>            (tree-walk, no compile)
  C  back-end   emitir.cardinal  -> C  -> cc -O2 -> native binary
  x86 back-end  emitx86.cardinal -> .s -> cc -O2 -> native binary

The compiler front-end (emitir / emitx86) itself can be run two ways:
  * bootstrap : python3 bootstrap/cardinal.py emitir.cardinal <prog> lib   (Python-hosted)
  * native    : bench/.build/cc1 <prog> lib                                (self-hosted binary)

Usage:
  python3 bench/bench.py                 # full run
  python3 bench/bench.py --quick         # smaller sizes / fewer reps (smoke)
  python3 bench/bench.py --no-compiler-bench   # skip the slow whole-compiler timing
  python3 bench/bench.py --only fib,intloop
  python3 bench/bench.py --reps 7 --interp-timeout 30

Outputs a console report and writes bench/results/report.md + bench/results/raw.csv.
"""

import argparse
import csv
import os
import shutil
import statistics
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPILER = os.path.join(ROOT, "compiler")
RUNTIME = os.path.join(ROOT, "bootstrap", "runtime")
LIB = os.path.join(ROOT, "lib")
BUILD = os.path.join(ROOT, "bench", ".build")
PROGRAMS = os.path.join(ROOT, "bench", "programs")
RESULTS = os.path.join(ROOT, "bench", "results")
WORK = os.path.join(BUILD, "work")

CARDINAL = os.path.join(ROOT, "bootstrap", "cardinal.py")
EMITIR = os.path.join(COMPILER, "emitir.cardinal")
EMITX86 = os.path.join(COMPILER, "emitx86.cardinal")
RT_C = os.path.join(RUNTIME, "cardinal_rt.c")
GC_C = os.path.join(RUNTIME, "cardinal_gc.c")
CC1 = os.path.join(BUILD, "cc1")   # native self-hosted compiler, emits C
CCX = os.path.join(BUILD, "ccx")   # native self-hosted compiler, emits x86 asm


def fib_calls(n):
    """Number of `fib` invocations to compute fib(n) with the naive 2-call body."""
    a, b = 1, 1  # call-counts c(0)=1, c(1)=1; c(n)=c(n-1)+c(n-2)+1
    if n < 2:
        return 1
    cprev, cprevprev = 1, 1
    for _ in range(2, n + 1):
        cur = cprev + cprevprev + 1
        cprevprev, cprev = cprev, cur
    return cprev


# name -> (template, n_interp, n_native, units_fn, note)
# units_fn(n) returns the number of comparable work-units for workload size n,
# so throughput = units / runtime is size-independent and cross-backend-fair.
BENCHMARKS = {
    "intloop":   ("intloop",   300_000,  60_000_000, lambda n: n,          "integer add/mul/mod/xor per iter"),
    "floatloop": ("floatloop", 300_000,  60_000_000, lambda n: n,          "f64 add/mul/sub per iter"),
    "collatz":   ("collatz",   6_000,    3_000_000,  lambda n: n,          "branchy int; inner Collatz loop"),
    "matmul":    ("matmul",    600,      9_000,      lambda n: n * n,      "O(N^2) nested loops, int reduce"),
    "fib":       ("fib",       28,       40,         fib_calls,            "naive recursion; units = #calls"),
}

QUICK_OVERRIDE = {
    # smaller native sizes + tiny interp sizes for a fast smoke test
    "intloop":   (200_000, 2_000_000),
    "floatloop": (200_000, 2_000_000),
    "collatz":   (5_000,   200_000),
    "matmul":    (200,     1_500),
    "fib":       (25,      32),
}


class Phase:
    """Timing record for one measured step: min/median wall time over reps."""
    def __init__(self, label):
        self.label = label
        self.times = []
        self.error = None

    @property
    def best(self):
        return min(self.times) if self.times else None

    @property
    def median(self):
        return statistics.median(self.times) if self.times else None


def run_timed(cmd, reps, cwd=None, stdout_path=None, timeout=None, env=None):
    """Run cmd `reps` times, return (Phase, last_stdout_text).

    stdout is captured (and optionally tee'd to stdout_path on the last rep).
    On any non-zero exit / timeout / exception the Phase records .error and we
    stop early -- callers continue with whatever else they can measure.
    """
    ph = Phase(" ".join(os.path.basename(c) if "/" in c else c for c in cmd[:2]))
    last_out = ""
    for r in range(reps):
        try:
            t0 = time.perf_counter()
            proc = subprocess.run(
                cmd, cwd=cwd, env=env, timeout=timeout,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            dt = time.perf_counter() - t0
        except subprocess.TimeoutExpired:
            ph.error = f"timeout >{timeout}s"
            return ph, last_out
        except Exception as e:  # pragma: no cover - defensive
            ph.error = f"exec error: {e}"
            return ph, last_out
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            out = (proc.stdout or b"").decode("utf-8", "replace").strip()
            ph.error = f"exit {proc.returncode}: {(err or out)[:200]}"
            return ph, last_out
        ph.times.append(dt)
        last_out = (proc.stdout or b"").decode("utf-8", "replace")
        if stdout_path and r == reps - 1:
            with open(stdout_path, "w") as f:
                f.write(last_out)
    return ph, last_out


def ensure_native_compilers(reps=1, force=False):
    """Build the native self-hosted compilers (cc1 = C, ccx = x86) if missing.

    Returns (cc1_ok, ccx_ok, build_info) where build_info records timings if we
    actually built them this run.
    """
    info = {}
    os.makedirs(BUILD, exist_ok=True)
    # cc1: bootstrap-hosted compiler emits the whole compiler -> C, then cc.
    if force or not os.path.exists(CC1):
        c_path = os.path.join(BUILD, "cc1.c")
        ph, _ = run_timed(
            [sys.executable, CARDINAL, EMITIR, EMITIR, LIB],
            reps=1, cwd=COMPILER, stdout_path=c_path,
        )
        info["cc1_emit_bootstrap"] = ph
        if ph.error:
            return False, False, info
        lph, _ = run_timed(
            ["cc", "-O2", "-fwrapv", "-I", RUNTIME, "-o", CC1, c_path, RT_C, GC_C],
            reps=1,
        )
        info["cc1_link"] = lph
        if lph.error:
            return False, False, info
    cc1_ok = os.path.exists(CC1)
    # ccx: use cc1 to emit emitx86.cardinal -> C, then cc.
    if cc1_ok and (force or not os.path.exists(CCX)):
        cx_path = os.path.join(BUILD, "ccx.c")
        ph, _ = run_timed([CC1, EMITX86, LIB], reps=1, cwd=COMPILER, stdout_path=cx_path)
        info["ccx_emit_native"] = ph
        if not ph.error:
            lph, _ = run_timed(
                ["cc", "-O2", "-fwrapv", "-I", RUNTIME, "-o", CCX, cx_path, RT_C, GC_C],
                reps=1,
            )
            info["ccx_link"] = lph
    ccx_ok = os.path.exists(CCX)
    return cc1_ok, ccx_ok, info


def materialize(name, n):
    """Substitute N into a template, return the absolute path of the source."""
    tmpl = os.path.join(PROGRAMS, name + ".cardinal.tmpl")
    with open(tmpl) as f:
        src = f.read().replace("__N__", str(n))
    os.makedirs(WORK, exist_ok=True)
    out = os.path.join(WORK, f"{name}_{n}.cardinal")
    with open(out, "w") as f:
        f.write(src)
    return out


def fmt_t(seconds):
    if seconds is None:
        return "   --   "
    if seconds < 1e-3:
        return f"{seconds*1e6:7.0f}us"
    if seconds < 1.0:
        return f"{seconds*1e3:7.1f}ms"
    return f"{seconds:7.3f}s "


def fmt_thru(units, seconds):
    if seconds is None or seconds <= 0:
        return "      --    "
    r = units / seconds
    if r >= 1e9:
        return f"{r/1e9:8.2f} G/s"
    if r >= 1e6:
        return f"{r/1e6:8.2f} M/s"
    if r >= 1e3:
        return f"{r/1e3:8.2f} K/s"
    return f"{r:8.1f}  /s"


def main():
    ap = argparse.ArgumentParser(description="Cardinal compile-time + runtime profiler")
    ap.add_argument("--quick", action="store_true", help="small sizes, fewer reps (smoke test)")
    ap.add_argument("--reps", type=int, default=0, help="reps per measurement (default 5, quick 2)")
    ap.add_argument("--interp-timeout", type=float, default=60.0, help="per-run interpreter timeout (s)")
    ap.add_argument("--only", default="", help="comma list of benchmark names to run")
    ap.add_argument("--no-compiler-bench", action="store_true", help="skip whole-compiler throughput timing")
    ap.add_argument("--rebuild-compilers", action="store_true", help="force rebuild native cc1/ccx")
    args = ap.parse_args()

    reps = args.reps or (2 if args.quick else 5)
    run_reps = max(reps, 3)            # runtime is the headline -> a few extra reps
    selected = [s for s in args.only.split(",") if s] or list(BENCHMARKS)
    os.makedirs(RESULTS, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)

    print("=" * 78)
    print("Cardinal profiling harness")
    print(f"  root           {ROOT}")
    print(f"  reps           compile={reps}  run={run_reps}")
    print(f"  interp timeout {args.interp_timeout}s")
    print(f"  benchmarks     {', '.join(selected)}")
    print("=" * 78)

    print("\n[setup] ensuring native self-hosted compilers (cc1=C, ccx=x86) ...")
    t0 = time.perf_counter()
    cc1_ok, ccx_ok, build_info = ensure_native_compilers(force=args.rebuild_compilers)
    print(f"        cc1 {'OK' if cc1_ok else 'MISSING'} | ccx {'OK' if ccx_ok else 'MISSING'} "
          f"({time.perf_counter()-t0:.1f}s)")
    for k, ph in build_info.items():
        print(f"        built {k}: {fmt_t(ph.best)}{'  ['+ph.error+']' if ph.error else ''}")

    csv_rows = []
    md = []
    md.append("# Cardinal profiling report\n")
    md.append(f"_reps: compile={reps}, run={run_reps}; interp timeout {args.interp_timeout}s_\n")

    # -------------------------------------------------------------------------
    # SECTION 1: compiler throughput on a large input (the whole compiler).
    # -------------------------------------------------------------------------
    if not args.no_compiler_bench:
        print("\n" + "-" * 78)
        print("COMPILER THROUGHPUT  (front-end: source -> backend text, whole compiler)")
        print("-" * 78)
        nlines = sum(1 for _ in open(EMITIR)) if os.path.exists(EMITIR) else 0
        # measure lines of the emitted C as the 'output' size, and the whole
        # compiler module graph as input. We time emitting emitir.cardinal.
        big = []
        bph, _ = run_timed([sys.executable, CARDINAL, EMITIR, EMITIR, LIB], reps=1,
                           cwd=COMPILER, stdout_path=os.path.join(BUILD, "cmp_boot.c"))
        big.append(("bootstrap (Python-hosted)", bph))
        if cc1_ok:
            nph, _ = run_timed([CC1, EMITIR, LIB], reps=1, cwd=COMPILER,
                               stdout_path=os.path.join(BUILD, "cmp_native.c"))
            big.append(("native (self-hosted cc1)", nph))
        try:
            out_lines = sum(1 for _ in open(os.path.join(BUILD, "cmp_boot.c")))
        except OSError:
            out_lines = 0
        print(f"  input: the compiler's own module graph; emitted C ~= {out_lines} lines")
        md.append("\n## Compiler throughput (emitting the whole compiler)\n")
        md.append(f"Emitted C output: ~{out_lines} lines.\n")
        md.append("\n| compiler host | wall time | output throughput |")
        md.append("|---|---:|---:|")
        for label, ph in big:
            thru = fmt_thru(out_lines, ph.best) if ph.best else "   --   "
            note = f"  [{ph.error}]" if ph.error else ""
            print(f"  {label:30s} {fmt_t(ph.best)}   {thru} lines/s{note}")
            md.append(f"| {label} | {fmt_t(ph.best).strip()} | "
                      f"{thru.strip()} lines/s |")
            csv_rows.append(["compiler-throughput", label, "emit-whole-compiler",
                             ph.best or "", ph.error or ""])
        if len(big) == 2 and big[0][1].best and big[1][1].best:
            sp = big[0][1].best / big[1][1].best
            print(f"  -> native self-hosted compiler is {sp:.2f}x the bootstrap speed")
            md.append(f"\nNative self-hosted compiler is **{sp:.2f}x** the bootstrap (Python) speed.\n")

    # -------------------------------------------------------------------------
    # SECTION 2: per-benchmark compile-time phases + runtime.
    # -------------------------------------------------------------------------
    compile_table = []   # rows for compile-time table
    runtime_table = []   # rows for runtime table

    for name in selected:
        if name not in BENCHMARKS:
            print(f"  (skipping unknown benchmark '{name}')")
            continue
        tmpl, n_i, n_n, units_fn, note = BENCHMARKS[name]
        if args.quick and name in QUICK_OVERRIDE:
            n_i, n_n = QUICK_OVERRIDE[name]

        print("\n" + "-" * 78)
        print(f"BENCHMARK: {name}   ({note})")
        print(f"  workload: interp N={n_i:,}  native N={n_n:,}")
        print("-" * 78)

        src_native = materialize(tmpl, n_n)
        src_interp = materialize(tmpl, n_i)
        base = os.path.basename(src_native)[:-len(".cardinal")]
        cfile = os.path.join(WORK, base + ".c")
        sfile = os.path.join(WORK, base + ".s")
        cbin = os.path.join(WORK, base + ".cbin")
        xbin = os.path.join(WORK, base + ".xbin")

        # ---- compile-time phases (on the native-sized source) -------------
        # C front-end, bootstrap-hosted
        c_fe_boot, _ = run_timed([sys.executable, CARDINAL, EMITIR, src_native, LIB],
                                 reps=reps, cwd=COMPILER, stdout_path=cfile)
        # C front-end, native self-hosted
        c_fe_nat = Phase("cc1")
        if cc1_ok:
            c_fe_nat, _ = run_timed([CC1, src_native, LIB], reps=reps,
                                    cwd=COMPILER, stdout_path=cfile)
        # C link (cc -O2 of emitted C)
        c_link = Phase("cc-link")
        if not c_fe_boot.error or os.path.exists(cfile):
            c_link, _ = run_timed(["cc", "-O2", "-fwrapv", "-I", RUNTIME,
                                   "-o", cbin, cfile, RT_C, GC_C], reps=reps)
        # x86 front-end, bootstrap-hosted
        x_fe_boot, _ = run_timed([sys.executable, CARDINAL, EMITX86, src_native, LIB],
                                 reps=reps, cwd=COMPILER, stdout_path=sfile)
        # x86 front-end, native self-hosted
        x_fe_nat = Phase("ccx")
        if ccx_ok:
            x_fe_nat, _ = run_timed([CCX, src_native, LIB], reps=reps,
                                    cwd=COMPILER, stdout_path=sfile)
        # x86 link (cc -O2 of emitted asm)
        x_link = Phase("as-link")
        if os.path.exists(sfile):
            x_link, _ = run_timed(["cc", "-O2", "-fwrapv", "-I", RUNTIME,
                                   "-o", xbin, sfile, RT_C, GC_C], reps=reps)

        compile_table.append((name, c_fe_boot, c_fe_nat, c_link, x_fe_boot, x_fe_nat, x_link))

        # ---- runtime ------------------------------------------------------
        units_n = units_fn(n_n)
        c_run = Phase("c-run")
        x_run = Phase("x86-run")
        if os.path.exists(cbin):
            c_run, c_out = run_timed([cbin], reps=run_reps)
        else:
            c_out = None
        if os.path.exists(xbin):
            x_run, x_out = run_timed([xbin], reps=run_reps)
        else:
            x_out = None

        # interpreter runs the *small* interp-sized source under a timeout
        units_i = units_fn(n_i)
        i_run, i_out = run_timed([sys.executable, CARDINAL, src_interp],
                                 reps=2, timeout=args.interp_timeout)

        # ---- correctness cross-check --------------------------------------
        # native C vs x86 must agree on the same (native-sized) source.
        parity = "n/a"
        if c_out is not None and x_out is not None:
            parity = "OK" if c_out.strip() == x_out.strip() else "MISMATCH"
        # interpreter agrees with a C build of the *interp-sized* source.
        interp_parity = "n/a"
        if not i_run.error:
            isrc_c = os.path.join(WORK, base + "_iverify.c")
            ibin = os.path.join(WORK, base + "_iverify")
            vph, _ = run_timed([sys.executable, CARDINAL, EMITIR, src_interp, LIB],
                               reps=1, cwd=COMPILER, stdout_path=isrc_c)
            if not vph.error:
                lph, _ = run_timed(["cc", "-O2", "-fwrapv", "-I", RUNTIME, "-o", ibin,
                                    isrc_c, RT_C, GC_C], reps=1)
                if not lph.error:
                    _, vout = run_timed([ibin], reps=1)
                    interp_parity = "OK" if vout.strip() == i_out.strip() else "MISMATCH"

        runtime_table.append((name, units_n, units_i, c_run, x_run, i_run,
                              parity, interp_parity, n_n, n_i))

        # per-benchmark console summary
        print(f"  compile (native-sized N={n_n:,}):")
        print(f"    C  front-end  bootstrap {fmt_t(c_fe_boot.best)}   native {fmt_t(c_fe_nat.best)}   link {fmt_t(c_link.best)}")
        print(f"    x86 front-end bootstrap {fmt_t(x_fe_boot.best)}   native {fmt_t(x_fe_nat.best)}   link {fmt_t(x_link.best)}")
        print(f"  runtime:")
        print(f"    C    {fmt_t(c_run.best)}  {fmt_thru(units_n, c_run.best)}")
        print(f"    x86  {fmt_t(x_run.best)}  {fmt_thru(units_n, x_run.best)}")
        if i_run.error:
            print(f"    interp  {i_run.error}  (N={n_i:,})")
        else:
            print(f"    interp {fmt_t(i_run.best)}  {fmt_thru(units_i, i_run.best)}  (N={n_i:,})")
        print(f"  parity: C-vs-x86 {parity} | interp-vs-native {interp_parity}")

        for be, ph, un, nn in [("C", c_run, units_n, n_n), ("x86", x_run, units_n, n_n),
                               ("interp", i_run, units_i, n_i)]:
            csv_rows.append([name, be + "-run", f"N={nn}", ph.best or "", ph.error or ""])

    # -------------------------------------------------------------------------
    # Final tables (console + markdown).
    # -------------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("COMPILE TIME SUMMARY  (best of %d reps; front-end = compiler, link = cc)" % reps)
    print("=" * 78)
    hdr = f"{'bench':<10} | {'C fe boot':>10} {'C fe nat':>9} {'C link':>8} | {'x86 fe boot':>11} {'x86 fe nat':>10} {'x86 link':>8}"
    print(hdr)
    print("-" * len(hdr))
    md.append("\n## Compile time per benchmark (best of %d reps)\n" % reps)
    md.append("Front-end = the Cardinal compiler (bootstrap = on Python interpreter, "
              "native = self-hosted binary). Link = `cc -O2` of the emitted C/asm.\n")
    md.append("\n| bench | C fe boot | C fe native | C link | x86 fe boot | x86 fe native | x86 link |")
    md.append("|---|---:|---:|---:|---:|---:|---:|")
    for (name, cfb, cfn, cl, xfb, xfn, xl) in compile_table:
        print(f"{name:<10} | {fmt_t(cfb.best):>10} {fmt_t(cfn.best):>9} {fmt_t(cl.best):>8} | "
              f"{fmt_t(xfb.best):>11} {fmt_t(xfn.best):>10} {fmt_t(xl.best):>8}")
        md.append(f"| {name} | {fmt_t(cfb.best).strip()} | {fmt_t(cfn.best).strip()} | "
                  f"{fmt_t(cl.best).strip()} | {fmt_t(xfb.best).strip()} | "
                  f"{fmt_t(xfn.best).strip()} | {fmt_t(xl.best).strip()} |")

    print("\n" + "=" * 78)
    print("RUN TIME SUMMARY  (throughput = work-units/sec; speedup = native / interp)")
    print("=" * 78)
    hdr2 = f"{'bench':<10} | {'C thru':>12} {'x86 thru':>12} {'interp thru':>12} | {'C/interp':>9} {'x86/interp':>10} | parity"
    print(hdr2)
    print("-" * len(hdr2))
    md.append("\n## Runtime throughput (work-units / second)\n")
    md.append("Throughput is size-independent, so the interpreter (run at a smaller N) "
              "compares fairly to the native back-ends (run at a larger N).\n")
    md.append("\n| bench | C | x86 | interp | C / interp | x86 / interp | parity |")
    md.append("|---|---:|---:|---:|---:|---:|---|")
    for (name, un_n, un_i, cr, xr, ir, par, ipar, nn, ni) in runtime_table:
        c_thru = un_n / cr.best if cr.best else None
        x_thru = un_n / xr.best if xr.best else None
        i_thru = un_i / ir.best if (ir.best and not ir.error) else None
        c_sp = f"{c_thru/i_thru:8.0f}x" if (c_thru and i_thru) else ("  >interp" if c_thru and ir.error else "    --  ")
        x_sp = f"{x_thru/i_thru:9.0f}x" if (x_thru and i_thru) else ("   >interp" if x_thru and ir.error else "     --  ")
        i_disp = fmt_thru(un_i, ir.best) if not ir.error else "  timeout "
        print(f"{name:<10} | {fmt_thru(un_n, cr.best):>12} {fmt_thru(un_n, xr.best):>12} "
              f"{i_disp:>12} | {c_sp:>9} {x_sp:>10} | {par}/{ipar}")
        md.append(f"| {name} | {fmt_thru(un_n, cr.best).strip()} | {fmt_thru(un_n, xr.best).strip()} | "
                  f"{(i_disp if ir.error else fmt_thru(un_i, ir.best)).strip()} | "
                  f"{c_sp.strip()} | {x_sp.strip()} | {par}/{ipar} |")

    md.append("\n_parity = C-output-vs-x86-output / interpreter-output-vs-native-output "
              "(both must be OK)._\n")

    # write artifacts
    report_md = os.path.join(RESULTS, "report.md")
    raw_csv = os.path.join(RESULTS, "raw.csv")
    with open(report_md, "w") as f:
        f.write("\n".join(md) + "\n")
    with open(raw_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["benchmark", "phase", "size", "best_seconds", "error"])
        w.writerows(csv_rows)
    print("\n" + "=" * 78)
    print(f"wrote {report_md}")
    print(f"wrote {raw_csv}")
    print("=" * 78)


if __name__ == "__main__":
    main()
