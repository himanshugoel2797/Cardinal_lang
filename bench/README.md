# Cardinal profiling harness

Measures **compile time** and **generated-code runtime** across every execution
path, and compares them to the tree-walking interpreter.

```
python3 bench/bench.py            # full run (~5-7 min; includes whole-compiler timing)
python3 bench/bench.py --quick    # smaller sizes, 2 reps (smoke test, ~1 min)
python3 bench/bench.py --no-compiler-bench    # skip the slow whole-compiler timing
python3 bench/bench.py --only fib,intloop --reps 7
```

Results are printed and also written to `bench/results/report.md` and
`bench/results/raw.csv`.

## What it measures

Three execution paths (mirroring `compiler/ccrun.sh`, `ccrun_x86.sh`,
`bootstrap/cardinal.py`):

| path | pipeline |
|---|---|
| **interpreter** | `bootstrap/cardinal.py` walks the AST directly (no compile) |
| **C back-end** | `emitir.cardinal` → C → `cc -O2` → native binary |
| **x86 back-end** | `emitx86.cardinal` → `.s` → `cc -O2` → native binary |

The compiler front-end itself runs two ways, both timed:

- **bootstrap** — the compiler runs on the Python interpreter
  (`python3 cardinal.py emitir.cardinal …`). This is what `ccrun.sh` uses.
- **native** — the self-hosted compiler binary (`bench/.build/cc1`,
  `bench/.build/ccx`), built once into `bench/.build/` and reused.

### Compile time

Split into phases so you can see where time goes:

- **front-end (bootstrap)** — compiler-on-Python emits C / asm text
- **front-end (native)** — the self-hosted compiler binary emits the same
- **link** — `cc -O2` of the emitted C / asm into a binary

Plus a **whole-compiler throughput** section: how long each compiler host takes
to emit C for the *entire compiler* (~74k lines of C out), the most demanding
real input, reported as lines/sec.

### Runtime

Reported as **throughput = work-units / second**, which is workload-size
independent — so the interpreter (run at a small N) compares fairly against the
native back-ends (run at a large N, for measurement resolution). The harness
verifies correctness two ways every run: C-output vs x86-output on the same
source, and interpreter-output vs a native build of the interpreter-sized source.

## Benchmarks

Templated in `bench/programs/*.cardinal.tmpl` with an `__N__` size knob:

| name | exercises | work-unit |
|---|---|---|
| `intloop` | integer add/mul/mod/xor | iterations |
| `floatloop` | f64 add/mul/sub | iterations |
| `collatz` | data-dependent branches, inner loop | outer range |
| `matmul` | O(N²) nested loops, index overhead | N² |
| `fib` | naive recursion / call overhead | number of calls |

To add one: drop a `name.cardinal.tmpl` in `bench/programs/` (use `__N__` for the
size), then add an entry to `BENCHMARKS` in `bench.py` with its interp/native
sizes and a `units(n)` function.

## Notes

- `bench/.build/` holds the prebuilt native compilers and per-run work files; it
  is disposable (delete it to force a rebuild, or pass `--rebuild-compilers`).
- Other instances may be editing `compiler/` / `bootstrap/` concurrently. The
  native compilers are snapshots built at first run, so their numbers are stable;
  the bootstrap front-end reads sources live, and any failure there is reported
  per-phase rather than aborting the run.
