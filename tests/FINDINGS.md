# Adversarial compiler test campaign — findings & fixes

A fleet of subagents wrote ~330 differential tests across 8 subsystems, run
through three execution paths that must agree: the Python **interpreter (oracle)**,
the **C backend**, and the **x86_64 backend**. Negative tests additionally check
that the Cardinal checker and the Python reference checker reach the same verdict.

Harnesses (repo root):
- `sh tests/run3.sh <prog.cardinal>` — three-way e2e diff (interp / C / x86).
- `sh tests/checkcmp.sh <prog.cardinal>` — checker-parity (Cardinal vs Python).
- `sh tests/sweep.sh <dir>...` — run3 over every program in the given dirs.

## Confirmed bugs found AND fixed (9)

| # | Where | Bug | Fix |
|---|-------|-----|-----|
| A | `bootstrap/interpreter.py` (oracle) | i64/u64 `/` and `%` routed through Python float division (`int(acc/x)`), losing precision near 2^63 — e.g. `u64_max / 1` gave `0`. | Exact integer truncation-toward-zero (`abs//abs` with sign fixup). |
| B | `bootstrap/cardinal.py` (oracle) | Deep Cardinal recursion hit Python's recursion limit and `RecursionError`'d where the backends succeeded. | Raise recursion limit + run on a 512 MB-stack thread. |
| C | `backend_x86.cardinal` `ICallClosure` | Float args to a closure call were loaded into GP registers, not xmm — closure read garbage. | Full SysV classification (floats→xmm, env=GP reg 0); float return via xmm0. |
| D | `backend_x86.cardinal` container elem r/w | A struct element whose size isn't a multiple of 8 (e.g. 12-byte `{i32,i32,i32}`) in an array/vec panicked the compiler; copying `nwords*8` would also overrun the packed buffer. | Byte-accurate copy (full words + 4/2/1-byte tail). |
| E | `bootstrap/interpreter.py` (oracle) | The numeric for-loop index carried `ty=None` when bounds were untyped literals, so `(* i 10)` over the index produced an uninferable result and `let` failed at runtime — though both checkers accept it (index defaults to i32). | Default the index type to i32 when bounds are untyped (the checker-sanctioned rule). |
| F | `backend_x86.cardinal` signed `/`,`%` | `idivq` raised SIGFPE on `INT64_MIN / -1`; interp + C wrap to `INT64_MIN`. | Special-case divisor `-1`: quotient = `-dividend` (wraps), remainder 0. |
| G | `backend_x86.cardinal` `shl`/`shr` | Hardware masks the shift count to 6 bits, so a shift by ≥ width wrapped (e.g. `1u64 << 64` → 1); interp + C yield 0 / sign-fill. | Guard count ≥ width → 0 (shl / logical shr) or sign-fill (`sar $63`). |
| H | `backend_x86.cardinal` + `backend_c.cardinal` float `/`,`%` | Float divide-by-zero: oracle panics, C panicked with the wrong message ("integer…"), x86 produced IEEE inf/nan (didn't panic). | x86 now panics on an ordered ==0 divisor (skips NaN); C uses the correct "float division by zero" message. Both match the oracle. |
| I | `compiler/lower.cardinal` for-loop | The upper bound (and step) were re-read each iteration (a live Local), so mutating the bound var in the body changed the trip count; the oracle snapshots once at entry (DESIGN: `i = 0,1,…,n-1`). | Snapshot bound and step into temps at loop entry. |

## Verification gates after the fixes — all green
- Checker differential (`difftest.sh`): **AGREE=13, DIFF=0**.
- C self-host fixed point: **cc1.c == cc2.c byte-identical (72321 lines)**.
- x86 self-host fixed point: **native x86 compiler emits identical C**.
- e2e sweeps: arith+floats **53/53**, control+aggregates **63/63**,
  collections+sums+gc **105/105**, canonical examples **12/12** runnable programs
  (`mathx.cardinal` is a main-less library, exercised via `usemathx`).
- Checker negative-test sweep: **120/120 AGREE**.

## Subsystem reports
Per-area detail (every test + verdict) lives in each directory's `FINDINGS.md`:
`tests/{arith,floats,control,aggregates,collections,sums,gc,checker}/FINDINGS.md`.

Collections (46), sums/enums (45), GC stress (14, also clean under
ASan+UBSan at `CARDINAL_GC_THRESHOLD=0`), and the 120 checker tests found **no**
divergences — corroborating those subsystems.
