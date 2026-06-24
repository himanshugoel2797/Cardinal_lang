# opus_sound — type-system soundness, round 2 (Opus campaign)

17 tests. Headline find: **neither checker does any literal-range / constant
validation**, so out-of-range constants reach radically different runtime
behavior across the three paths. 3 confirmed soundness families (8 repros), all
`checkcmp` AGREE (py=ok card=ok) yet diverge at runtime.

## CONFIRMED SOUNDNESS BREAKS — ALL FIXED

Resolution (coordinated lexer + both-checkers + interpreter + runtime change),
grounded in DESIGN: a *literal* that cannot fit its type is a compile error
(§7.2 / §539 Rust-style suffix), while arithmetic *overflow* wraps (§161 machine
semantics). All 8 repros now agree three-way (`run3`) or AGREE-on-reject in both
checkers (`checkcmp`):

- **Family 1 (arithmetic overflow → wraps):** the interpreter wrapped untyped
  arithmetic into the target width at the coerce boundary (was `_check_fits`
  panic), matching the backends. s04→64, s05→−31072, s06→0; all PASS three-way.
- **Family 1 single literals + Family 2 (out-of-range literal → reject):** both
  checkers range-check integer literals — suffixed against the suffix type, bare
  untyped against the inferred/context type (a per-op re-check pins comparison
  operands like s07). The Cardinal lexer (which accumulates in i64) now panics on
  a >u64 magnitude so s14 errors instead of silently wrapping. s07/s10/s14/s15
  AGREE-reject.
- **Family 3 (out-of-f32 literal → reject):** the user chose *reject* (Rust-style,
  consistent with ints) over saturation. Added a `convert::str_to_float` builtin
  (interp + both checker sigs + C runtime `cl_convert__str_to_float`/strtod); both
  checkers reject a float literal that rounds to ∞ at its target type (exact IEEE
  agreement: Python `struct.pack`, Cardinal `(as d f32)` + an `inf` test). The
  interpreter also clamps `_round_float` to a signed ∞ instead of raising an
  uncaught `OverflowError`. s17 AGREE-reject; PASS three-way (all reject).

Gates after the fix: difftest **AGREE=13/0**; C & x86 self-host **byte-identical
(73915 lines)**; sweeps **arith/floats/control/aggregates 116/116**,
**collections/sums/gc 105/105**.

## ORIGINAL REPORT (for reference)

### Family 1 — untyped literal / arithmetic folded into a smaller type, unchecked
`coerce(UntypedInt, Int)` returns the target with no value check. The interpreter
evaluates untyped arithmetic in arbitrary precision and `_check_fits` at the
coercion boundary → **panics**; the backends lower the arithmetic in the context
width → **silently wrap**. (Per DESIGN "overflow wraps", the backends are right and
the interpreter is wrong; bare out-of-range literals should be a checker error.)
- `s04`: `let x u8 = (* 200 200)` → interp PANIC; C/x86 print 64
- `s05`: `(* 1000 100)` -> i16 → interp PANIC; C/x86 -31072
- `s06`: `let x u32 = (shl 1 40)` → interp PANIC; C/x86 0
- `s07`: `if (< 5000000000 n)` (n i32) → interp PANIC; C/x86 silently take the
  else branch — **control-flow corruption**, the nastiest variant
- `s10`: `let xs [u8] = [10 300 20]` → interp PANIC; C/x86 44

### Family 2 — suffixed literal overflowing its OWN type, unchecked, 3-way split
Interpreter `coerce` fast-paths `v.ty == name` and returns the value verbatim (no
wrap, no check); the lexer's i64 value field can't even hold a >i64 literal.
- `s15`: `let x u8 = 300u8` → interp **300**, C **44**, x86 **300** (no two agree)
- `s14`: `let x i64 = 99999999999999999999i64` → interp keeps the full >i64 value,
  C/x86 7766279631452241919

### Family 3 — out-of-range float literal handled three ways (incl. crashes)
- `s17`: `let x f32 = 1.0e40f32` → interp **uncaught Python OverflowError**
  (`struct.pack("f")`, not a clean panic); C prints **inf**; x86 **assembler error**
  ("cannot create floating-point number") — a compiler crash.

## RECOMMENDED FIX (coordinated)
1. Lexer/parser: store integer literal values losslessly (detect >64-bit at lex).
2. Both checkers: range-check literal constants — suffixed (against the suffix
   type) and bare untyped (against the inferred/context type); reject out-of-range.
   Range-check float literals against their float type (or define saturation).
3. Interpreter: evaluate untyped-literal arithmetic in the inferred context width
   with wrapping (match DESIGN "overflow wraps" and the backends); wrap/validate
   suffixed literals instead of storing >width values; never raise an uncaught
   Python exception on a float literal.

## HARDENED (probed, no divergence)
- `s01` missing-return: NO return-path analysis in either checker (a `-> i32`
  function may fall off the end and is accepted) — all paths panic at runtime, so
  no divergence, but a checker hole worth closing for early diagnosis.
- `s02`/`s03` null-as-struct/str, `s08` struct-array copy, `s12` div-by-zero,
  `s13` INT_MIN/-1, `s16` map aliasing: consistent. `s11` float `%`: both reject.
- `s09` self-referential `struct Node { next Node }`: accepted by both checkers
  (no occurs/size check); confounded by the unimplemented `null` lowering — a
  by-value self-recursive struct is an infinite C type. Re-test once null lands.
