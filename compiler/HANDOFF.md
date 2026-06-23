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
1. **Extend the runtime** `bootstrap/runtime/cardinal_rt.{h,c}`:
   - **vec/map runtime — DONE** (in `cardinal_rt.{h,c}`; test
     `bootstrap/runtime/vecmap_test.c`, passes under ASan+UBSan incl. a
     tiny-GC-threshold stress). `cl_vec`/`cl_map` are bare `cl_handle`s to a
     header object (reference semantics: copy = share; growth/rehash mutate the
     shared header in place). API: `cl_vec_new(elemsz)/push/pop/at/len`;
     `cl_map_new(keysz,valsz,kind)/set/get/has/del/keys/len` with
     `CL_MAP_SCALAR` (raw keysz bytes) vs `CL_MAP_STR` (content hash/eq) — the
     two kinds the fixed per-map key type collapses to. Map is an
     insertion-ordered compact-dict (open-addr index + ordered entries w/
     tombstones) so `map_keys`/for-in match the interpreter's order, incl.
     del+reinsert moving a key to the end. Parity panics wired: `m[k]` missing →
     `"map key not found"`, `pop` empty → `"pop from empty vector"`, vec OOB →
     `"index out of bounds: i (len n)"`. Element/key/val bytes are 8-byte
     aligned in GC buffers so embedded handles are traced by the conservative
     object scan. Rooting rule respected (GC scans shadow stack + live objects,
     NOT the C stack): ≤1 un-rooted alloc in flight, stored into the
     caller-rooted header before the next alloc; `cl_map_keys` push-roots its
     result vec across the build loop.
   - **Heap strings — IN PROGRESS (this milestone).** ABI decided + specced in
     DESIGN §5.3: a `str` is a single `cl_handle` to a managed string object
     `{ u64 nbytes; UTF-8 bytes... }` (bytes inline). cl_str is now traced like
     any other handle — no raw-char* field, no static-vs-owned split. Work items:
     * Runtime (`cardinal_rt.{h,c}`): `typedef cl_handle cl_str`; rewrite
       `cl_print_str`/`cl_str_len`/`cl_panic` to deref; add `cl_str_from_utf8`
       and **interned `cl_strlit(const char*)`** — pointer-keyed intern in a
       permanently-rooted cl_map so a literal evaluates to a stable rooted handle
       (this is how the inlined-literal GC hazard is avoided: the backend emits
       literals inline as `cl_strlit("...")`, which must NOT be an un-rooted fresh
       alloc each eval). Builtins with the exact mangled names the backend emits
       (`cl_<mod>__<fn>`): `cl_strings__chars` (->cl_array of int32 codepoints),
       `__concat`, `__substr` (codepoint-indexed, Python-slice clamp — NO panic),
       `__from_char(int32 cp)`, `__eq`(bool, content); `cl_convert__ord/chr/
       int_to_str/str_to_int`. All str ops are CODEPOINT-indexed (interpreter str
       is a Python str): `len(str)`=codepoint count, `substr` slices codepoints,
       `chars` decodes to codepoints. Re-deref str args after any alloc (GC is
       non-moving so ptrs are stable, but be explicit); str args are caller-rooted.
     * vec/map STR-key path: keysz is now sizeof(cl_handle)=8; `map_hash_key`/
       `map_key_eq` STR branch must DEREF the handle to reach bytes (done).
     * Backend (`backend_c.cardinal`): `ImmStr` -> `cl_strlit("escaped")` (runtime
       strlens it — also fixes the old codepoint-vs-byte length bug); `managed()`
       += str + TMap; `ctype_t` += TMap (`cl_map`).
     * Lowerer (`lower.cardinal`): `len(str)` -> ICall `cl_str_len`; `==`/`!=` on
       str -> ICall `cl_strings__eq` (raw IBin would compare handles, not content).
     * No checker change -> difftest stays AGREE=13 automatically.
     CAVEATS to revisit: `cl_strlit` strlens, so embedded-NUL string literals
     truncate (compiler/tests use none; lexer likely can't make one). `cescape`
     (backend) still only escapes `"\ \n\t\r` — arbitrary control bytes in a
     literal emit invalid C (pre-existing). `str_to_int` panic-message text is
     best-effort, not byte-identical to Python's `repr` (stderr only; stdout
     parity holds).
2. **vec/map LOWERING — DONE (committee-gated).** New IR (`ir.cardinal`):
   `PVecElem` lvalue + `IVecNew/IVecLit/IVecGet/IVecSet/IVecLen/IVecPush/IVecPop`
   and `IMapNew/IMapGet/IMapSet/IMapLen/IMapHas/IMapDel/IMapKeys`. Backend
   (`backend_c.cardinal`) emits the cl_vec_*/cl_map_* calls; ops that pass a
   key/val/elem BY POINTER emit a block-temp-and-&address:
   `{ KT _k = <key>; VT _v = <val>; cl_map_set(<m>, &_k, &_v); }` — GC-safe
   because the SOURCE IVals are rooted function temps/locals (the runtime copies
   bytes into the rooted collection; the throwaway temp need not be rooted).
   `map_kind` picks CL_MAP_STR for str keys else CL_MAP_SCALAR. Lowerer
   (`lower.cardinal`): `t_map_key/t_map_val`; EIndex/len/`set`/for-in dispatch on
   array-vs-vec-vs-map; EVecNew/EVec(`lower_vec_lit`)/EMapNew; push/pop/map_has/
   map_del/map_keys builtins. No checker change -> difftest stays AGREE=13.
   Verified byte-identical to the interpreter on a broad vec/map battery
   (push/pop/index/len/for-in/literals, vec-of-struct w/ `set v[i].field`,
   nested {{i32}}/{str {i32}}, map str/int/char/bool/enum keys, update-in-place,
   has/del, map_keys insertion order incl. del+reinsert) AND under ASan/UBSan at
   CARDINAL_GC_THRESHOLD=0 with managed elements/values (vec of str, map
   str->str). 3-auditor committee: GC-safety + parity APPROVE; design
   APPROVE-WITH-FIXES (the map-field hole below).
   STILL PENDING for full vec/map UX (NOT data-op blockers):
   * **Aggregate display — DONE (committee-gated, unanimous APPROVE).**
     `io::print`/`println` of array/vector/map/struct now emits the interpreter's
     `_display` form recursively: `[..]`, `{..}`, `{k: v ..}`, `(Name f: v ..)`,
     single-space separators. `lower_io` routes every arg through `lower_display`
     (in lower.cardinal) which dispatches on type and hand-emits the loops
     (lower_display_seq/map/struct; disp_str/disp_sep/disp_call helpers; bare_name
     strips the `mod::`). Map display uses `cl_map_keys`+`cl_map_get` (insertion
     order). Added runtime `cl_print_char`. Tightened the INTERPRETER: struct
     fields now display in DECLARATION order (eval_struct_lit builds the field
     dict in declaration order) so it matches the compiled struct layout — a
     canonical/deterministic choice (literal order was a host accident; only
     _display observed it). Verified byte-identical incl. deep nesting, multibyte
     chars, raw (unescaped) strings, map del+reinsert order, non-str keys, empties;
     ASan/UBSan clean at CARDINAL_GC_THRESHOLD=0 (nested cl_map_keys get distinct
     rooted slots). examples/vectest now PASSES natively. DESIGN §12 gap-list
     updated. maptest still needs Stage-4 callbacks.
   * **`to_str` + reflection (DESIGN §5.5/§5.6) — DESIGN DONE; impl step 1 DONE.**
     Generic `to_str(x) -> str` builtin (type-dispatched like `len`): interpreter
     (= `_display`, exposed), both checkers (difftest AGREE=13), and native
     lowering (`lower_to_str`/`lts_seq`/`lts_map`/`lts_struct`, builds a cl_str via
     a rooted accumulator + concat). Float display unified to `%g` in BOTH backends
     (closed the repr-vs-%g divergence). Committee-gated (parity+integration
     APPROVE; GC-safety APPROVE after the cl_strlit pin fix it surfaced — see
     commit 8444a71: permanent roots must use `cl_gc_pin`, NOT the LIFO shadow
     stack). **STEP 2 (pending):** (a) **function-ize** — emit a per-type
     `cl_<mod>__<Type>_to_str` FUNCTION (always-emitted, exported) instead of
     inline, and reroute io::print through `print(to_str(x))` so there's ONE
     formatter (removes the lower_display/lower_to_str duplication; add a tracked
     test round-tripping `io::print(x)` vs `io::print(to_str(x))`). (b) **enum**
     `to_str` (int->variant-name switch from the enum def — also closes io::print
     of enums). (c) **override** hooks: use a user-defined `<Type>_to_str` in the
     type's defining module iff present with the right sig, else autogen; wrong
     sig = error; mirror the lookup verdict-equivalently in both checkers. This is
     also the foundation for recursive **sum-type display** (Stage 4) — a
     per-type function recurses over data at runtime. (d) **runtime type
     descriptors** (§5.6, user chose IN scope) — its own later subsystem; NOT
     needed by to_str.
   * **`set m[k].field = v` — RESOLVED (user ruling): rejected in BOTH checkers.**
     Maps hold value-semantic copies, so taking a mutable field reference into a
     map element has no faithful semantics. `check_place` now threads a `via_map`
     flag (PlaceRes in checker.cardinal / the 3-tuple in typecheck.py): a map
     index sets via_map=true; a FieldAccess whose base is via_map is a compile
     error; an array/vector index clears via_map (those are references, so
     `set m[k][i]=v` and `set m[k][i].field=v` through a vec/array the map holds
     stay legal). Both checkers emit the identical message; difftest AGREE=13.
     `set m[k]=v` (whole value) and reading `m[k].field` remain fine. The native
     lower_place map-panic is now unreachable for valid programs (defense-in-depth).
   * **`for k in m` directly over a map** is rejected by BOTH checkers today
     (only `for k in (map_keys m)` works); DESIGN §5.3 lists `for k in m` as a
     contract, so spec leads impl — a future wiring item.
3. Verify against the interpreter on str/vec/map programs and add to the
   regression sweep. (examples/vectest, maptest need the aggregate-display +
   closures items above before they go green natively.)
DESIGN refs: §5.1, §5.3 (vec/map as builtin generics; map-key hashability;
insertion-order contract).

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
