# Cardinal self-hosting compiler — handoff (next: Stage 4)

Pick up here in a fresh session. This is the Cardinal-written compiler (`compiler/`)
that runs on the Python bootstrap interpreter and emits standalone C. **Goal:
self-host** — the compiler compiles itself to a native binary, so the Python
bootstrap can be retired. **Stage 4 (sum types + match, closures) is the last big
language feature before the Stage-5 self-host fixed point.**

## Operating contract (read first — the user is strict about these)

- **DESIGN.md is the AUTHORITY.** It overrides the interpreter's behavior and any
  subagent's advice. Ground every decision in a DESIGN.md citation.
- **Stop and ASK the user on any DESIGN HOLE** — anything the design leaves
  unspecified/contradictory, or that would force you to invent behavior. Do not
  paper over it. (The user makes these calls; recent examples: `set m[k].field`
  semantics, `to_str` design, the `%g` float rule.) Use `AskUserQuestion`.
- **No fallback literal type (DESIGN §7.2).** Never invent/default a type. An
  uninferable literal is a compile error. The lowerer takes every type from the
  checker oracle (`checker::check_expr`/`resolve`/structural `Type` match) — never
  a literal default. (Sanctioned: the for-loop index defaults to i32 and
  `operand_ty`/`pick_int` default *only* at those checker-blessed language-rule
  sites.)
- **Gate progress on a subagent COMMITTEE.** At each milestone run 2–3 parallel
  auditors (distinct lenses: parity, GC-safety, integration/design), DESIGN-
  anchored, each REQUIRED to verify by running. Advance only on consensus. The
  committees have repeatedly caught real bugs (a GC use-after-free, an eager
  float-op bug) — trust the process. Don't wait for the user between milestones,
  but DO ask on holes.
- **Commit at every verified checkpoint** (direct to `main`, the project's
  convention). End commit messages with the Co-Authored-By + Claude-Session
  trailers. Keep difftest green. Clean up stray `ccrun` binaries (the repo has a
  few tracked ELF binaries at root — `demo`/`fib`/`mathx`/`usemathx`/`t1`..`t4`/
  `main` — that ccrun rebuilds; `git checkout --` them, don't commit them).

## Current state — DONE and verified (native byte-identical to the interpreter)

Pipeline: `AST → typecheck (gate) → lower → IR → Backend.emit()` → C → cc + runtime.

- **Stage 0** scalars / control flow / functions+recursion / enums / casts /
  bitwise / `checked` / panic / io of scalars.
- **Stage 2** structs (value semantics, nested, composable `set arr[i].field=v`)
  + arrays (`[T]`, sized `(array T n)`, for-in, bounds-checked). GC rooting live.
- **Module-local type identity** (§10): a user type is `(module, name)`; same-named
  types in different modules coexist (mangled `cl_struct_<mod>__<name>`); cross-
  module type confusion is a clean error in both checkers.
- **Stage 3 — strings, vectors, maps (committee-gated):**
  - **Heap strings.** `str` = a `cl_handle` to a managed `{u64 nbytes; UTF-8 bytes}`
    object (DESIGN §5.3); traced like any handle. `cl_strlit` interns literals
    (pointer-keyed, **`cl_gc_pin`**'d — permanent roots must NOT go on the LIFO
    shadow stack). `strings::concat/substr/chars/from_char/eq`, `convert::*`,
    `len(str)`, str `==`/`!=` (content). All str ops are CODEPOINT-indexed.
  - **Vectors `{T}` + maps `{K V}`.** Runtime `cl_vec`/`cl_map` (handles to header
    objects; reference semantics; map is insertion-ordered). Full lowering:
    construction, literals, index r/w, `push`/`pop`/`map_has`/`map_del`/`map_keys`,
    `len`, for-in. Map keys: str(content)/int/char/bool/enum.
- **Value display + `to_str` (DESIGN §5.5/§5.6) — COMPLETE for struct/enum.**
  `to_str(x)` is a type-dispatched builtin; **`io::print` is `print(to_str(x))`**
  (one formatter). Each struct/enum gets a synthesized
  `cl_<mod>__<Type>_to_str(self T)` (lower_program **Pass 3**, always-emit,
  deterministic); user types recurse via CALLS (the model sum-type display reuses).
  **User OVERRIDES**: a `func <Type>_to_str (x T) -> str` in the type's defining
  module replaces the autogen (native skips synthesis; interpreter dispatches via
  a `defmod` on StructV/EnumV; both checkers enforce the `(T)->str` signature).
  Floats display via `%g` in both backends.

The Python reference (`bootstrap/lower.py`, `backend_c.py`, `ir.py`) is the
stage-0 throwaway port spec: it **HAS closures** (port them) but **NO sum types**
(design yourself) and no vec/map/strings.

## Files

`compiler/`:
- `lexer.cardinal`, `parser.cardinal` — front end; AST types (`Ty`/`Expr`/`Stmt`/
  `Decl` + `Param`/`Variant`/`Arm`...) live in parser, matched by bare name elsewhere.
- `checker.cardinal` — the type ORACLE. Already fully type-checks sums + match.
  Exports for the lowerer: `check_expr, resolve, new_ctx, build_sig, struct_fields`.
  Qualified-name helpers: `make_qual/split_at_dcolon/defining_module/resolve_user_type`.
- `ir.cardinal` — target-independent IR. `Instr` ALREADY has the closure ops
  (`IAlloc/ILoad/IEnvNew/IEnvStore/IEnvLoad/IMakeClosure/ICallClosure`). Builder
  fns are `ir::new_builder/fresh_temp/add_local/fresh_label/new_block/
  cur_terminated/emit` (call QUALIFIED as `ir::...`; ir exports nothing but
  unexported fns are still callable cross-module). Reset `lx.uq[0]=0` per function.
- `lower.cardinal` — AST→IR. "Option B": the checker is the sole type oracle; the
  `LX` struct holds mutable state behind 1-elem vec cells. Module/func emission in
  SORTED module-name order (determinism); Pass 1 structs, Pass 2 functions, Pass 3
  synthesized `_to_str` hooks. Type identity is the qualified string `"mod::Name"`
  (`qual`, `t_struct_name`, `to_str_sym`, `bare_name`).
- `backend_c.cardinal` — IR→C. `func_def` declares all locals/temps/params up
  front and **GC-roots every managed one** for the whole function (str/array/vec/
  map/struct/closure are managed); pop at every `IRet`. `cat` helper avoids deep
  `strings::concat` paren-imbalance (USE IT). PANICS on the closure/sum IR ops
  today (line ~721 "unsupported instruction").
- `backend.cardinal` — `emit_with(name, IRModule)` selector. `emitir.cardinal` —
  driver (type-checks as a gate, lowers, emits C).
- `ccrun.sh` — `sh compiler/ccrun.sh <prog.cardinal>` emits C, links the runtime,
  runs (`--emit` prints C, `--no-run` builds only). Passes the source dir on argv
  so imports resolve.
- `difftest.sh` — Cardinal checker vs Python checker over 13 files; must stay
  `AGREE=13 DIFF=0`.

`bootstrap/runtime/` (the C runtime the emitted code links):
- `cardinal_gc.{h,c}` — handle-table mark-and-sweep, NON-MOVING. Roots = the
  **shadow stack** (`cl_gc_push_root`/`pop_roots`, strict LIFO) + a **pinned list**
  (`cl_gc_pin`, permanent) + transitive conservative scan of live objects' bytes
  (8-byte-aligned handle-shaped words). The C machine stack is NOT scanned.
- `cardinal_rt.{h,c}` — `cl_str`/`cl_array`/`cl_vec`/`cl_map`/**`cl_closure`**
  (`{void *fn; cl_handle env}`, already defined), print helpers, string/vec/map
  ops. Unit tests: `gc_test.c`, `vecmap_test.c`.

## How to verify (always)

```sh
python3 bootstrap/cardinal.py --check-only compiler/<mod>.cardinal   # type-check a module
python3 bootstrap/cardinal.py <prog.cardinal> > /tmp/o.out           # oracle (interpreter)
sh compiler/ccrun.sh <prog.cardinal>          > /tmp/n.out           # native
diff /tmp/o.out /tmp/n.out                                           # THE correctness gate
sh compiler/difftest.sh                                              # expect AGREE=13 DIFF=0
```
- ANY `checker.cardinal` change MUST be mirrored verdict-equivalently in
  `bootstrap/typecheck.py` to keep difftest AGREE.
- GC-stress under sanitizers (the committee always does this): emit C, then
  `cc -O1 -g -fsanitize=address,undefined -fwrapv -I bootstrap/runtime -o /tmp/p \
   /tmp/p.c bootstrap/runtime/cardinal_rt.c bootstrap/runtime/cardinal_gc.c` and run
  `CARDINAL_GC_THRESHOLD=0 /tmp/p` (collect on every alloc) vs the oracle.
- Determinism (self-host §11): emit a program's C twice → byte-identical.

## Design decisions locked in (current)

- Module-local types (§10); §7.2 no-fallback-literal; deterministic emission.
- `cl_panic`/`cl_panic_cstr` are `_Noreturn`; a value-returning fall-off emits a
  trap (matches the interpreter's "fell off a value-returning fn" runtime error).
- `set m[k].field = v` is a compile error (maps hold value copies; `via_map` flag
  in both checkers). `set m[k]=v` and `set m[k][i]=v` (through a ref vec/array)
  are fine.
- Struct fields display in DECLARATION order (canonical); floats via `%g`;
  `to_str` overrides via the per-type `<Type>_to_str` hook.

## NEXT: Stage 4 — sum types + match, and closures  (committee-gated)

Two independent features; do them as separate committee-gated milestones. The
checker ALREADY fully type-checks both (module-local-qualified), so the work is
lower + backend + (for sums) a runtime representation. Grep the panic sites:
`grep -nE 'stage 4' compiler/lower.cardinal` →
- `451` EFunc (closure literal), `489` path-as-value (function-as-value thunk),
  `688` indirect/closure call  → **closures**
- `1046` sum variant literal, `1210` SMatch  → **sum types + match**
- `backend_c.cardinal:~721` "unsupported instruction" → the closure/sum IR ops.

### Closures (PORT from the Python reference)
`bootstrap/lower.py` HAS closure conversion — port it. The model: each `EFunc`
literal is lifted to a top-level function taking the environment; captured
variables are **boxed** (a 1-slot heap object) so mutations are shared; the
closure value is `cl_closure {fn, env}` and calls go indirect through it. The IR
ops already exist (`IAlloc`/`ILoad` for boxes, `IEnvNew`/`IEnvStore`/`IEnvLoad`
for the env array, `IMakeClosure`, `ICallClosure`); `cl_closure` is in the
runtime and `managed()` already covers it. Work:
1. **Lower** (`lower.cardinal`): closure conversion — free-variable analysis,
   boxing of captured locals, lift `EFunc` bodies to synthesized top-level
   functions (you can synthesize functions like Pass 3 does for `to_str`), emit
   `IMakeClosure`; lower a bare named-function-used-as-value (`lower_path` at 489)
   to a thunk closure; lower indirect/closure calls (688) to `ICallClosure`.
   Mirror `bootstrap/lower.py`'s algorithm.
2. **Backend** (`backend_c.cardinal`): emit the closure ops (port from
   `bootstrap/backend_c.py`). The env is a GC handle → root it; the conservative
   scan traces boxes/env. GC-stress at threshold 0 is the key check (escaping
   closures, two closures sharing one cell — see `examples/closures.cardinal`,
   `gcstress.cardinal`, `features.cardinal`).
3. Known deferral even in Python: capturing a `for`-loop variable — check what the
   interpreter does and match (or ping if it's a hole).

### Sum types + match (DESIGN it — NOT in the Python reference)
Sum values are **reference types** (managed/handle-backed, recursive — an AST node
contains AST nodes; DESIGN §5.3). The checker already type-checks construction +
`match` (exhaustive, payload binding, module-local-qualified). Work:
1. **Representation** (you design it; ping if §5.3 underspecifies): a sum value =
   a handle to a GC object holding a variant **tag** (int32, = variant index) +
   the payload fields. A per-sum-type tagged C struct/union, or a uniform
   `{tag, fields...}` sized to the largest variant. Payload handles must be
   GC-traced (8-byte aligned). Add IR + backend emission; likely no new runtime
   *functions* (just emitted structs + a tag switch).
2. **Lower** construction (`1046`): build the tagged object for the variant
   (nullary variants construct BARE — `Leaf`, not `(Leaf)`). **Lower `match`**
   (`SMatch`, `1210`): read the tag, switch/if-chain on the variant, bind payload
   fields into the arm scope, exec the arm; `else` default. Exhaustiveness is
   already enforced by the checker.
3. **Sum-type `to_str`** (closes the last §5.5 gap): the function model is ready —
   add a `synth_sum_to_str` (Pass 3) that switches on the tag and builds
   `(Variant f: <to_str field> ...)`, nullary `(Variant)` (match the interpreter's
   `_display` SumV form). Handle `TSum` in `lower_to_str`/`to_str_sym`, and EXTEND
   the checker `<Type>_to_str` hook-signature rule (`check_to_str_hook` /
   `_check_to_str_hook`) to also accept sum types. Verify recursive sums (e.g. an
   `Expr` tree) display correctly — runtime recursion over data terminates.

## THEN: Stage 5 — self-host fixed point
Compile the whole compiler with itself; iterate until `cc1 == cc2` byte-identical.
Determinism is already enforced (sorted module/type order, Pass 3 deterministic);
watch any new map-iteration-driven emission when closures/sums land.

## Smaller deferred items (additive, not Stage-4 blockers)
- **`mod::Type` qualified SYNTAX** (finishes §10): a `TyPath(mod,name)` Ty node in
  `parser.cardinal` + `interpreter.py` parse_type; handle in both `resolve`s and
  qualified construction `(mod::Point ...)`. Nothing needs it yet (no collisions;
  cross-module types work via unique-import bare name).
- **`for k in m`** directly over a map (both checkers reject today; only
  `for k in (map_keys m)`); DESIGN §5.3 lists `for k in m` — spec leads impl.
- **Runtime type descriptors** (§5.6, user chose IN scope) — a later subsystem for
  dynamic reflection; NOT needed by `to_str`.
- Minor: `cescape` only escapes `"\ \n\t\r` (control bytes → invalid C);
  `cl_strlit` strlens (embedded-NUL literals truncate); `str_to_int` panic-message
  text differs from the interpreter (stderr only; stdout parity holds).

## Gotchas (Cardinal language)
- Keyword collisions are illegal as identifiers (rename fields/vars: `step`→`stride`).
- Nullary sum variants construct BARE (`Leaf`, not `(Leaf)`); enum variants use
  `Enum::Variant`.
- No bare negative literal — unary minus is `(- x)` (e.g. `(- 1i64)`).
- No single-line `if`/`case`/loop body. Optional fields = `has_*` bool + sentinel.
- Mutable shared state = a 1-element vec cell inside a value struct (how `LX` works).
- Imported TYPES are referenced/constructed by bare name; cross-module FUNCTION
  calls need `export` + `mod::fn`. A binding name must not shadow a helper fn you
  call (e.g. don't bind a pattern var `val` if you call the `val` function — use
  `sval`/`vty`).
- Deeply nested `strings::concat` mis-balances parens → use the `cat`/vec builder.
- The whole program (all modules) compiles to ONE C file (whole-program), so
  cross-module synthesized calls link fine; functions are forward-declared.
