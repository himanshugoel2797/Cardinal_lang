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

---

# Campaign 2 — deeper differential sweep (`tests/adv_*`)

A second fleet (14 subagents, ~474 tests) targeted under-tested frontiers and
subsystem *interactions* rather than re-covering campaign 1. It found **4 distinct
bugs**, all fixed below. The clean subsystems: to_str/display (28), floats-deep
(30), match/patterns (45), maps (29), integer-boundary (30), control-flow (45),
recursion/ABI (22), GC-stress (16), checker-parity (50), lexer/parser (26).

## Confirmed bugs found AND fixed (4)

| # | Where | Bug | Fix |
|---|-------|-----|-----|
| J | `backend_x86.cardinal` `ICast` int→float | `u64 -> f64/f32` used `cvtsi2sd`, which treats the source as a *signed* 64-bit int, so any value ≥ 2^63 converted to a negative double (e.g. `u64_max` printed `-1` vs the correct `1.84467e+19`). | Standard unsigned-64 conversion: for a sign-bit-set value, halve with a sticky low bit, convert, then double back. NUL-free path unchanged. |
| K | `backend_x86.cardinal` container elem load/store | A value struct whose size is 3/5/6/7 bytes (e.g. `{u8,u8,u8}`) in an array/vec is `nwords==1`, so `load_sized` fell through to a **1-byte** load (dropping fields at offset >0 → read `10,0,0` for `{10,11,12}`) and `store_sized` *overran* to 8 bytes. | Route non-{1,2,4,8} sizes through the byte-accurate copy helpers (`copy_rax_to_slot`/`copy_scratch_to_rax`). |
| L | `backend_x86.cardinal` `ICallClosure` | A closure returning a >16B aggregate (e.g. `[i32]`/`cl_array`, 24B = SysV MEMORY) had no hidden-pointer return path, so the callee's `sret` ABI was unmet — the env/args were misplaced and the call **segfaulted**. | Mirror `emit_call`'s `hidden`/`base`: `&dst`→`%rdi`, env shifts to `%rsi`, args shift up one GP reg, result written through the sret pointer. |
| M | `backend_c.cardinal` + `backend_x86.cardinal` + runtime | A string literal with an **embedded NUL** (`"a\0b"`) went through `cl_strlit`/`strlen`: the C backend truncated to length 1, and the x86 `.asciz` escape emitted a raw NUL byte that broke the assembler. Interp gave the correct length 3. | New runtime `cl_strlit_n(bytes, nbytes)` interns/pins exactly like `cl_strlit` but takes an explicit length (`cl_strlit` now delegates). C emits `cl_strlit_n("…", sizeof("…")-1)`; x86 emits the literal with `.ascii` + an end label and passes `end-start` as the length. Behavior-identical for NUL-free literals. |

Note: the first attempt at M used `cl_str_from_utf8` directly, which returns an
*un-rooted* fresh allocation — string-literal results aren't GC-rooted at the use
site, so the self-host hit `use-after-free: stale handle`. The `cl_strlit_n`
interning/pinning variant is what preserves the permanently-rooted invariant.

## Verification gates after the fixes — all green
- C self-host fixed point: **cc1.c == cc2.c byte-identical (72682 lines)**.
- x86 self-host fixed point: **native x86 compiler emits identical C (72682 lines)**.
- e2e sweeps: arith+floats+aggregates+control **116/116**, collections+sums+gc
  **105/105** (one GC test flaked on a timeout under parallel load; passes solo).
- All 4 bug repros + siblings now PASS three-way; string regressions clean.

---

# Campaign 3 — Sonnet implementation-aware fleet (`tests/son_*`)

6 Sonnet agents read the backend/checker/lowering source to find fragile paths,
each tasked to find ≥5 ways to break the compiler. (A mid-run OOM crash from too
many simultaneous agents lost 4 agents' summaries, but their ~146 test files were
recovered and swept single-process.) Suites: son_abi (17), son_gc (17),
son_checker (41), son_lower (25), son_numeric (26), son_metamorphic (20).
Clean: son_gc (0 breaks even at GC_THRESHOLD=0), son_metamorphic (0), and
checker-parity over son_checker (AGREE=41, DIFF=0).

## Confirmed bugs found AND fixed (8)

| # | Where | Bug | Fix |
|---|-------|-----|-----|
| N | `backend_x86.cardinal` `emit_thunk` | A named-function VALUE returning a >16B struct segfaulted: the thunk's GP-arg shift clobbered `%rdi` (the SysV MEMORY hidden return pointer). | Shift starts at index 1 for a MEMORY return, preserving the sret pointer. |
| O | `backend_x86.cardinal` `emit_thunk` | A named-function value with enough args to spill GP registers silently dropped the stack-passed args (`argreg` clamps ≥5 to `%r9`). | Detect the spill and emit a loud compile-time panic instead of a silent miscompile. (Full stack-arg forwarding remains a later milestone; lambdas already handle it.) |
| P | `backend_x86.cardinal` float compare | Ordered float comparisons with a runtime NaN were all wrong (`NaN<0`→true, `NaN==NaN`→true, `NaN!=NaN`→false): `ucomisd` sets ZF=PF=CF=1 on unordered, and `sete`/`setb`/`setbe` ignore the parity flag. (Missed by campaign-2 floats tests, which never branched on an unfoldable runtime NaN.) | `==`,`<`,`<=` AND with `setnp`; `!=` ORs with `setp`; `>`,`>=` already NaN-safe via `seta`/`setae`. |
| Q | `lower.cardinal` lambda naming | An anonymous function lifted as `cl_<mod>__lambdaN` collided with a user `func lambdaN` → duplicate C symbol / asm "already defined". | Name lifted lambdas `cl_<mod>__0lambdaN`; a digit-led component can't be a user identifier (`is_ident_start` = letter/`_`), so collision is impossible. |
| R | `lower.cardinal` `lower_name` | A module-level `const` used as a value — even `const A i32 = 42i32` — panicked `lower: cannot use as a value` in BOTH backends (the interpreter evaluates consts eagerly). | Inline the const's initializer expression at each use (backends have no global-const storage). |
| S | `lower.cardinal` `lower_fornum` | Mutating the numeric for-loop index inside the body changed the trip count (the index variable WAS the loop counter); the interpreter iterates an independent counter (interp 10 vs backends 3). | Drive the loop with a hidden counter; copy it into the user-visible index at the top of each iteration, so body mutations can't affect the count. |

(Bugs N/O were committed earlier with the son_abi suite; P/Q/R/S here.)

## Known divergences DOCUMENTED, not yet fixed (need a design call or are
## adversarial/milestone-sized) — repros under `tests/son_*`

- **Aggregate `==`** (son_checker t34–t40): both checkers accept `==`/`!=` on
  struct/array/vec/func, but the C backend emits `==` on a C struct (compile
  error) while interp + x86 do structural/word comparison. SOUNDNESS gap.
  Recommendation: the checker should reject `==` on `func`/closure (meaningless)
  and either implement structural equality in the C backend or reject it for
  struct/array/vec — a language-semantics decision.
- **Named-function-value thunk symbol collision** (son_lower t02, t20): a thunk
  is `<mangled>__thunk`; a user `func X__thunk` whose `X` is used as a value
  collides. Adversarial; fix needs a digit-led thunk symbol at 3 emit sites.
- **x86 int→f32 rounding at 2^24** (son_numeric n16, n25): one boundary value
  rounds to 16777218 vs the oracle's 16777216 (`cvtsi2ss` rounding).
- **float→int out-of-range** (son_numeric n05, n24): interp + x86 give 0, C gives
  INT_MIN — genuinely platform-defined; needs a chosen saturating semantics.
- **`null`/handle features** (son_checker t18, t24): `lower: null/handles not
  supported yet` (both backends) / a C handle type mismatch — an unimplemented
  area, fails loudly.
- **Enum shadows imported module** (son_checker t30, t31): `enum io` next to
  `import io` resolves `io::X` differently between interp (enum) and backends
  (module). Obscure name-resolution corner.

## Aggregate `==` (son_checker t34–t40) — FIXED
Both checkers now reject `==`/`!=` on struct/sum/array/vec/map/func (commit
"checker: reject == / != on aggregate and function types"). Equality is
restricted to numbers, char, bool, str, enum, null.

---

# Campaign 4 — Opus fleet (`tests/opus_*`, run one-at-a-time, RAM-safe)

## opus_sound (17 tests) — literal-range soundness FAMILY — ALL 8 FIXED
Headline find: **neither checker range-checked literals**. Out-of-range constants
diverged three ways — see `tests/opus_sound/FINDINGS.md`. Fixed per DESIGN: a
*literal* that can't fit its type is a compile error (§7.2/§539 Rust-style suffix),
while arithmetic *overflow* wraps (§161). (1) Untyped arithmetic now WRAPS in the
interpreter at the coerce boundary, matching the backends (s04→64, s05→−31072,
s06→0). (2) Both checkers range-check integer literals — suffixed vs the suffix
type, bare vs the inferred/context type, with a per-op re-check for comparison
operands (s07/s10/s14/s15 reject); the Cardinal lexer panics on a >u64 magnitude
(s14). (3) Out-of-f32 float literals are REJECTED (user's call, Rust-consistent):
new `convert::str_to_float` builtin (interp + both checkers + C runtime strtod);
both checkers reject a literal that rounds to ∞ at its target type; the interpreter
clamps `_round_float` to a signed ∞ instead of crashing (s17 rejects). Gates:
difftest AGREE=13/0; C & x86 self-host byte-identical (73915 lines); sweeps
116/116 + 105/105.

## opus_codegen (25 tests) — 2 fixed
x86 codegen / register-pressure stress; the park-args-to-scratch design shrugged
off the clobber attempts. Two bugs (mirrors of earlier fixes): x86 float->u64 cast
wrong for >= 2^63 (signed cvtt..2siq); C backend shift by a runtime count >= width
(raw machine shift). Both FIXED + gated. See `tests/opus_codegen/FINDINGS.md`.

## opus_runtime (13 tests) — 5 fixed
strings / UTF-8 / to_str / runtime. All 5 FIXED + gated: interp str_to_int wrap;
runtime NaN sign ("-nan"->"nan"); x86 empty-string-literal interning collision
(a regression from the embedded-NUL .ascii change — the "GC-pressure" symptom was
really a deterministic pointer-intern collision); chr of out-of-range / surrogate
codepoints now panics in interp + runtime. See `tests/opus_runtime/FINDINGS.md`.
