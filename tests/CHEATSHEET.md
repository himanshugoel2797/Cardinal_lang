# Cardinal Lisp — test author's cheat sheet

You are writing **adversarial differential tests** for the Cardinal compiler.
A test is a `.cardinal` program. There are three execution paths that MUST agree:
- the Python **interpreter** (the ORACLE / source of truth),
- the **C backend** (`ccrun.sh`),
- the **x86_64 backend** (`ccrun_x86.sh`).

## What is a BUG (what you are hunting)
- **e2e divergence**: the three paths produce different stdout, or one panics
  while another succeeds. Found via `sh tests/run3.sh <prog.cardinal>` → `FAIL`.
- **checker divergence**: the Cardinal checker and the Python checker reach
  different ok/err verdicts on the same program. Found via
  `sh tests/checkcmp.sh <prog.cardinal>` → `DIFF`.
- **compiler crash**: a backend emits a `panic`/internal error ("unsupported
  instruction", "x86: ...") on a program the interpreter runs fine.

A program that fails to compile / type-check in ALL THREE paths the same way is
NOT a bug — it's just an invalid program (likely your syntax mistake). Real bugs
= DISAGREEMENT.

## Harnesses
- `sh tests/run3.sh <prog.cardinal>`  → PASS / FAIL (e2e three-way diff)
- `sh tests/checkcmp.sh <prog.cardinal>` → AGREE / DIFF (negative/checker tests)

Each `run3.sh` invocation is SLOW (~30–90s: it runs the whole Cardinal compiler
under Python twice plus the interpreter). Keep programs small and focused; you do
NOT need hundreds — 12–25 sharp tests beat 200 trivial ones.

## Syntax (verify against `examples/*.cardinal` — they all pass)
- First line: `module <name>`. Then `import io` / `import strings` / `import fs` etc.
- Function: `func name (a i32) (b i32) -> i32` … `end`. `main` returns `i32`.
- Bindings: `let x = …`  `const x = …` (immutable)  `set x = …` (reassign).
  `set arr[i].field = v` composable. `set m[k].field = v` is a COMPILE ERROR (maps copy).
- Void/side-effecting call statement: `do (io::println x)`.
- Prefix expressions only: `(+ a b)` `(* a (- b c))` `(<= a b)` `(== a b)` `(!= a b)`.
  Bitwise/shift: `(band a b)` `(bor a b)` `(bxor a b)` `(bnot a)` `(shl a n)` `(shr a n)`.
  Logic: `(and a b)` `(or a b)` `(not a)`.
- Cast: `(as x i32)`. No implicit promotion — mixing `1i32` and `2i64` in `(+ …)` is an ERROR.
- Literals: `1i32 2i64 3u8 4u64` (sized), `3.5`/`1.0e9` (float), `true false`, `null`,
  `'c'` char, `"str"`. **No bare negative literal** — write `(- 1i64)`, not `-1i64`.
  An UNSUFFIXED int literal infers its type from context; an uninferable literal is a
  compile ERROR (no fallback type).
- Types: `i8 i16 i32 i64 u8 u16 u32 u64 f32 f64 bool char str handle unit`.
- Control flow (NO single-line bodies; always multi-line + `end`):
  `if c` … `elsif c` … `else` … `end`
  `while c` … `end`
  `loop` … `break` / `continue` … `end`
  `for i = 0 to N` … `end`  (half-open: 0..N-1; index defaults to i32) optional `step S`
  `for x in coll` … `end`
  `return expr`
- Struct: `struct P` / `x i32` / `y i32` / `end`. Construct `(P x: 1 y: 2)`. Access `p.x`.
  Value semantics: copying a struct is a deep copy. Sized array field: `xs [i32]` etc.
- Array: type `[T]` (dynamic-size literal) or `(array T n)` (sized). Literal `[1 2 3]`.
  Index `a[i]` (bounds-checked → panic on OOB). `for x in a`.
- Vector `{T}`: `(vec T)`; `push`/`pop`; index `v[i]`; `len v`; `for x in v`.
- Map `{K V}`: `(map K V)`; `m[k]` r/w; `map_has`/`map_del`/`map_keys`; insertion-ordered.
  Keys: str(by content)/int/char/bool/enum. NOT struct keys (error).
- Enum: `enum Color` / `Red` / `Green` / `end`. Use `Color::Red`. Equality with `==`.
- Sum type: `type Tree` / `Leaf` / `Node (v i32) (l Tree) (r Tree)` / `end`.
  Nullary variants construct BARE: `Leaf` (NOT `(Leaf)`). With payload: `(Node v: 1 l: Leaf r: Leaf)`.
  `match t` / `case Leaf` … / `case Node(v l r)` … / `else` … / `end`. Must be exhaustive (or `else`).
- Closures: `func () -> i32` … `end` used as a value; captures by reference (shared cell).
  Function-typed param: `(f func(i32 -> i32))`. Capture of a for-loop var = fresh per iteration.
- Display: `to_str(x)` (type-dispatched); `io::print`/`io::println`. Floats print via `%g`.
- Comments: `#` to end of line.

## Gotchas (from the project HANDOFF — avoid wasting cycles)
- Keywords are reserved; don't use `step`, `to`, `in`, `loop`, etc. as identifiers.
- Don't shadow a function name you call with a local/binding of the same name.
- Deeply nested `strings::concat(...)` can mis-balance — build incrementally.
- Inline `asm` is native-only (interpreter panics) — DO NOT put asm in e2e tests.
- Integer overflow WRAPS (two's complement), no panic. Array index OOB DOES panic.

## Your deliverable
1. Write your `.cardinal` tests under your assigned directory.
2. Run each through the right harness.
3. Write `FINDINGS.md` in your directory: list every test, its verdict, and for
   each FAIL/DIFF a MINIMAL repro + what diverged (interp vs C vs x86 output).
   Clearly separate CONFIRMED BUGS from passing tests. Do NOT try to fix compiler
   source — just find and document precisely.
