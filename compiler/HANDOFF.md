# Cardinal self-hosting compiler — handoff notes

Pick up here in a fresh session. This is the Cardinal-written compiler that runs
on the Python bootstrap interpreter and emits standalone C. Goal: self-host
(the compiler compiles itself to a native binary), so the Python bootstrap can
be retired.

## Operating contract (read first — the user is strict about these)

- **DESIGN.md is the AUTHORITY.** It overrides the interpreter's behavior and
  overrides any subagent's advice. Ground every decision in a DESIGN.md citation.
- **Stop and PING the user (PushNotification) on any DESIGN HOLE** — anything the
  design leaves unspecified/contradictory, or that would force you to invent
  behavior. Do not paper over it. The user painstakingly designed this language.
- **No fallback literal type (DESIGN §7.2).** Never invent/default a type. An
  uninferable literal is a compile error. The lowerer takes every type from the
  checker oracle (`checker::check_expr` / `checker::resolve` / structural `Type`
  matching) — never a literal default. (Sanctioned exceptions, because the
  *checker itself* does them: for-loop index defaults to i32; `operand_ty`/
  literal defaulting only at those language-rule sites — currently `operand_ty`
  errors on all-untyped comparisons, which is correct.)
- **Be automated; gate progress on subagent CONSENSUS.** Run a design-compliance
  committee (2–3 parallel auditors, DESIGN-anchored, must verify by running) at
  each milestone; only advance on consensus. Don't wait for the user between
  stages — but do ping on holes.
- **Commit at every verified checkpoint.** Keep difftest green.

## Current state (as of commit 1bc4c05)

Pipeline (the swappable backend): `AST → typecheck → lower → IR → Backend.emit()`.

DONE and verified (native byte-identical to the interpreter):
- **Stage 0** scalars/control-flow/functions/enums/casts/bitwise/checked/panic/io.
- **Stage 2** structs + arrays (value semantics, nested, array-of-struct,
  `set arr[i].field = v`, for-in, sized `(array T n)`, GC rooting live).
- **Module-local type identity** (DESIGN §10): types are `(module, name)`;
  same-named structs in different modules coexist; cross-module type confusion is
  a clean error in both checkers.
- §7.2 enforcement, type-error gate in the pipeline, deterministic emission order.

NOT done: **Stage 1 was the throwaway direct emitter (deleted).** Stage 3
(strings/vec/map), Stage 4 (sum types/match + closures), Stage 5 (self-host
fixed point), and the `mod::Type` qualified SYNTAX are all pending (below).

## Files (compiler/)

- `ir.cardinal` — target-independent IR: `IRType` (TyOf/Ptr/VoidPtr), `IVal`,
  `IPlace` (PLocal/PField/PDeref/PArrElem), `Instr` (incl. closure ops + array
  ops + terminators), `Block/IRFunc/IRStruct/IRModule`, `FuncBuilder` (cell-based
  counters: `ir::new_builder/fresh_temp/add_local/fresh_label/new_block/
  cur_terminated/emit` — call them QUALIFIED as `ir::...`; ir exports nothing so
  unexported fns are still callable).
- `lower.cardinal` — AST→IR. Option B: holds a checker `Ctx` + parallel
  `{str Binding}` type-scope, calls `checker::check_expr` for every type. `LX`
  struct carries mutable state behind ref-vec cells. Module/func emission in
  SORTED module-name order (determinism). Type identity is the qualified string
  "mod::Name" (helper `qual`, `t_struct_name`).
- `backend_c.cardinal` — IR→C. `struct_cname` mangles "mod::Bare" →
  `cl_struct_mod__Bare`. GC rooting in `func_def`. `cat` helper avoids deep
  `strings::concat` paren-imbalance (USE IT for multi-piece strings).
- `backend.cardinal` — `emit_with(name, IRModule)` selector (the swap point).
- `emitir.cardinal` — driver: type-checks (gate), lowers, emits C.
- `checker.cardinal` — the type oracle. Exports for the lowerer:
  `check_expr, resolve, new_ctx, build_sig, struct_fields`. Qualified-name
  helpers: `make_qual/split_at_dcolon/defining_module/resolve_user_type`.
- `lexer.cardinal`, `parser.cardinal` — front end (AST types live here; matched
  by bare name from other modules).
- `ccrun.sh` — `sh compiler/ccrun.sh <prog.cardinal>` emits C, links the runtime,
  runs. `--emit` prints C; `--no-run` builds only. Surfaces compiler errors.
- `difftest.sh` — Cardinal checker vs Python checker over 13 files; must stay
  `AGREE=13 DIFF=0`.

The Python reference (`bootstrap/lower.py`, `backend_c.py`, `ir.py`) is the port
spec — but it is INCOMPLETE: it has closures but **no sum types/match and no
vec/map**, and the runtime has no vec/map either (see Stage 3).

## How to verify (always do this)

```sh
# type-check a compiler module
python3 bootstrap/cardinal.py --check-only compiler/<mod>.cardinal
# native vs interpreter parity (THE correctness gate)
python3 bootstrap/cardinal.py <prog.cardinal> > /tmp/o.out      # oracle
sh compiler/ccrun.sh <prog.cardinal>          > /tmp/n.out      # native
diff /tmp/o.out /tmp/n.out
# both checkers agree (run after ANY checker change, mirror in typecheck.py)
sh compiler/difftest.sh        # expect AGREE=13 DIFF=0
```
Any checker change in `checker.cardinal` MUST be mirrored in
`bootstrap/typecheck.py` (verdict-equivalent) to keep difftest AGREE.

## Design decisions locked in

- **Module-local types** (§10): identity `(module, name)`; bare names resolve
  local-first → unique import → ambiguity error; `mod::Type` qualifies.
- **§7.2 no fallback literal type**: uninferable literal = compile error
  (comparisons, `do`-discarded exprs, io args all enforced in both checkers).
- Backend swappable via IR; structs mangled `cl_struct_<mod>__<name>`.
- `cl_panic`/`cl_panic_cstr` are `_Noreturn`; value-returning fall-off emits a
  trap (matches interpreter's "fell off a value-returning fn" runtime error).

## Next work (priority order)

### A. Stage 3 — strings, vectors, maps  ← the self-hosting critical path
The compiler uses str/`{T}`/`{K V}` everywhere; it cannot self-compile without
them. **This is bigger than a port: the C RUNTIME has no vec/map and only
immutable static `cl_str`.** You must:
1. **Extend the runtime** `bootstrap/runtime/cardinal_rt.{h,c}` (and maybe
   `cardinal_gc.c`): GC-managed growable vector (`cl_vec_new/push/pop/at/len`,
   element-size-aware), hashable map (`cl_map_new/get/set/has/del/keys/len` —
   keys restricted to str(content)/int/char/bool/enum per §5.3), and heap
   strings (`strings::concat`/`substr`/`from_char`/`chars` need GC-allocated
   `cl_str`; today `cl_str` is `{const char*, len}` static). Decide the cl_str
   ABI for owned vs static and keep `cl_print_str`/`cl_str_len` working.
2. **Lower** them in `lower.cardinal`: replace the `(stage 3)` panics — EVecNew/
   EVec/EMapNew, vec/map index read+write, `len` of str/vec/map, for-in over
   vec, `push`/`pop`/`map_has`/`map_del`/`map_keys` builtins (see
   `checker::check_builtin_call` for the exact signatures). Emit `ICall` to the
   new runtime symbols; element/key/val types come from `check_expr` (never
   invent). Mirror the array path; vecs/maps are reference-semantic.
3. **Backend** emit those ICalls (mostly already generic) + `ctype_t` for TVec
   (`cl_vec`) / TMap (`cl_map`); ensure `managed()` covers TVec/TMap (TVec is in;
   ADD TMap — auditor flagged it missing). GC rooting must root vec/map slots.
4. Verify against the interpreter on str/vec/map programs (see examples/
   strtest, vectest, maptest, usestd) and add to the regression sweep.
DESIGN refs: §5.1, §5.3 (vec/map as builtin generics; map-key hashability).

### B. Step 2 — `mod::Type` qualified SYNTAX (finishes §10)
Additive; nothing currently needs it (no collisions; cross-module structs work
via unique-import). Touch points: add a `TyPath(mod,name)` Ty node in
`compiler/parser.cardinal` `parse_type` (after an ident, if next token is `::`)
AND in `bootstrap/interpreter.py` `parse_type`; handle TyPath in
`checker.cardinal resolve` and `typecheck.py resolve` (→ `resolve_user_type(mod,
name)`); honor a qualified construction head `(mod::Point ...)` in
`check_struct_lit` (both) and `lower_struct_lit`; handle TyPath wherever the
interpreter inspects type nodes. Keep both parsers/checkers in sync (difftest).

### C. Stage 4 — sum types + match, then closures  (APPROVAL GATE)
Highest hack-risk. Port closure conversion from `lower.py` (it HAS closures;
IR + backend_c.cardinal already have IAlloc/ILoad/IEnvNew/IEnvStore/IEnvLoad/
IMakeClosure/ICallClosure — backend currently panics on them, add emission from
`backend_c.py`). Sum types/match: NOT in the Python reference — design tagged
unions + a switch/if-chain on the variant tag yourself; the checker already
fully type-checks sums/match (module-local-qualified). Replace the `(stage 4)`
panics (EFunc, lower_path module-fn-as-value, indirect/closure call, sum variant
literal, SMatch). Run a committee before committing.

### D. Stage 5 — self-host fixed point
Compile the whole compiler with itself; iterate until `cc1 == cc2` byte-identical
C. Determinism is already enforced for module/struct order; watch thunk order
(sort by name) and any new map-iteration-driven emission when closures land.

## Staged-panic map (grep these to find exactly what to implement)
`grep -nE 'panic "lower:.*(stage 3|stage 4)' compiler/lower.cardinal`
`grep -n "unsupported instruction" compiler/backend_c.cardinal`

## Gotchas (Cardinal language)
- Keyword collisions are illegal as identifiers (`step`→`stride`, `func`/`enum`
  as fields renamed).
- Nullary sum variants construct BARE (`ImmUnit`, `TNone`), not `(ImmUnit)`.
- No bare negative literal — unary minus is `(- x)` (e.g. `(- 1i64)`).
- No single-line `if`/`case` body. Optional fields = `has_*` bool + sentinel.
- Mutable shared state = 1-element vec cell inside a value struct.
- Imported TYPES are referenced/constructed by bare name (no `import` of types
  needed); cross-module FUNCTION calls need `export` + `mod::fn`. `ir.cardinal`
  exports nothing, so its builder fns are callable as `ir::fn`.
- Deeply nested `strings::concat` easily mis-balances parens → use a `cat`/vec
  builder (present in backend_c.cardinal and emitir).
- Run programs that import user modules with the dir on argv so imports resolve;
  `ccrun.sh` passes the source's absolute dir automatically.
