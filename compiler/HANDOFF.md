# Cardinal self-hosting compiler — handoff (next: x86_64 aggregates + closures)

Pick up here in a fresh session. This is the Cardinal-written compiler (`compiler/`)
that runs on the Python bootstrap interpreter and emits native code. **Goal:
self-host** — DONE for the C backend. Remote: `github.com/himanshugoel2797/Cardinal_lang`
(push over SSH; the `gh` token is expired).

**Status:** Stage 4 (sum types + match, closures), Stage 5 (C-backend self-host),
the reflection API, AND a full **native x86_64 direct-assembler backend** are all
DONE + committed. The x86 backend (`backend_x86.cardinal`, emits GNU `as` AT&T,
same IR + runtime ABI as the C backend) compiles the same 10/13 examples
byte-identically to the interpreter, is GC-sanitizer-clean, AND **self-hosts**:
the native x86-compiled compiler compiles the whole compiler to byte-identical C.
**The only remaining x86 gap is floats (xmm); the compiler doesn't use floats, so
it doesn't block x86 self-host.**

- **C self-host:** `sh compiler/selfhost.sh` → cc1.c == cc2.c byte-identical.
- **x86 self-host:** `sh compiler/selfhost_x86.sh` → the native x86 compiler emits
  identical C to the Python-hosted compiler.
- **x86 backend:** `sh compiler/ccrun_x86.sh <prog.cardinal>` (emit `.s`: `--emit`).
  Driver `emitx86.cardinal`; selector `backend.cardinal` ("x86_64"). Floats raise
  a clean `panic "x86: float ..."`.
- **Reflection (§5.6):** builtin `reflect` module — `reflect::typeof(x) -> TypeInfo
  {name,kind,fields,variants}` (static type → descriptor) and `reflect::variant_of(x)`
  (live sum/enum variant). Deferred: per-field type descriptors + byte offsets
  (need a shared C-ABI layout model — note the x86 backend now HAS one,
  `type_size/type_align/field_offset` in backend_x86.cardinal) and a type-id
  registry/lookup.

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
- **Value display + `to_str` (DESIGN §5.5/§5.6) — COMPLETE for struct/enum/sum.**
  `to_str(x)` is a type-dispatched builtin; **`io::print` is `print(to_str(x))`**
  (one formatter). Each struct/enum/sum gets a synthesized
  `cl_<mod>__<Type>_to_str(self T)` (lower_program **Pass 3**, always-emit,
  deterministic); user types recurse via CALLS. **User OVERRIDES**: a
  `func <Type>_to_str (x T) -> str` in the type's defining module replaces the
  autogen (native skips synthesis; interpreter dispatches via a `defmod` on
  StructV/EnumV/SumV; both checkers enforce the `(T)->str` signature). Floats via
  `%g`. A closure renders as `<closure>` in both backends.
- **Stage 4 — closures + sum types (committee-gated; commits `acf6be6`, `88946a3`):**
  - **Closures** (ported from `bootstrap/lower.py`): free-variable analysis →
    captured locals BOXED (1-slot heap cell, shared by-reference, `vs` IVal of IR
    type `Ptr` ⇒ boxed); `EFunc` bodies lifted to top-level functions taking a GC'd
    env array; value is `cl_closure {fn,env}`; named/imported function used as a
    value becomes a thunk-wrapping closure; indirect calls `ICallClosure`. A
    captured for-loop variable gets a FRESH per-iteration box (interpreter parity;
    beyond the Python ref which errors). `build_callmap` now resolves selectively-
    imported functions to their defining module.
  - **Sum types** (designed; no Python ref): a sum value is a `cl_handle` to a GC
    object `{int32 cl_tag; payload...}` — one C struct per variant
    (`cl_struct_<mod>__<Sum>__<Variant>`) + a tag-only header struct; tag = variant
    index. IR ops `ISumNew/ISumTag/ISumField`. Construction (incl. nullary BARE),
    `match` (tag if-chain, payload binding, `else`, exhaustive-trap), autogen +
    overridable sum `to_str`. The discriminant field is `cl_tag` so a user payload
    may be named `tag`. Recursive sums (AST trees) display/eval by runtime
    recursion. GC-safe at threshold 0 (conservative scan traces inline payloads).

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

## Stage 4 — DONE (committee-gated, see commits `acf6be6` closures, `88946a3` sums)

Both features land; see the Stage-4 bullet under "Current state" above for the
model. All `grep 'stage 4'` panic sites are gone. Verification that held:
closures/features/gcstress + sumtest byte-identical to the interpreter; GC
sanitizer stress at `CARDINAL_GC_THRESHOLD=0` clean (escaping closures, shared
cells, vec/map of closures, 100k+ recursive-sum churn with a bounded live set);
deterministic emission; difftest `AGREE=13`. Each milestone passed a 3-auditor
committee (parity / GC / design); committee-found bugs fixed before commit:
selective-import func-as-value mangling; sum field DISPLAY order (interpreter now
normalizes to declaration order, matching structs/§5.5); `tag` payload-field
collision (discriminant renamed `cl_tag` so user `tag` payloads work).

## Stage 5 — DONE (self-host fixed point)
`sh compiler/selfhost.sh`: the Python-hosted compiler emits C for the whole
compiler → `cc1`; `cc1` recompiles the compiler → `cc2.c`; `cc1.c == cc2.c ==
cc3.c` byte-identical (~59k lines of C). Binaries built from the same source path
are identical (only residual diff is the C toolchain embedding the input path).
Enabled by native `fs::read_file`/`fs::exists`/`sys::args` (cardinal_rt.c) + the
`int main(int argc,char**argv)` wrapper setting `cl_sys_set_args`.

## x86_64 backend — DONE (self-hosts; commits through milestone 7)
A stack-slot machine (no regalloc): every IR temp/local/param → a size-aware frame
slot; ints computed in 64-bit then normalized to width; full SysV AMD64 ABI
(integer regs, 2-reg ≤16B aggregates, >16B MEMORY on the stack, hidden-pointer
returns, integer overflow args on the stack); precise GC shadow-stack rooting; call
args/values parked in frame SCRATCH slots (never push/pop across a call — that
misaligns %rsp). Covers scalars, control flow, calls+recursion, panic, strings,
io, sum types, vectors, maps, closures (boxes/env/16B cl_closure/thunks), arrays
(24B cl_array via hidden-ptr + MEMORY-class cl_array_at), and value structs
(IStructNew/ILoadField/PField, ≤16B in regs, >16B MEMORY). Verify with
`ccrun_x86.sh`; GC-stress with ASan+UBSan + `CARDINAL_GC_THRESHOLD=0`;
`selfhost_x86.sh` checks the whole-compiler self-emit identity. Two committee-found
ABI bugs were fixed: narrow call-return normalization (str ==) and multi-word
container elements (vec/map of closures).

**Only remaining x86 op:** floats (xmm) — ImmFloat (rodata .double), addsd/.../
ucomisd, cvtsi2sd/cvttsd2si, float args xmm0..7 / return xmm0, cl_f64_to_str. No
example and not the compiler uses floats, so this is the last loose end, not a
blocker.

## (historical) x86_64 backend — remaining milestones
`backend_x86.cardinal` is a stack-slot machine (no regalloc): every IR temp/local/
param → an 8-byte frame slot; ints computed in 64-bit then normalized to width;
SysV AMD64 ABI; precise GC shadow-stack rooting; call args + values parked in
**frame scratch slots** (NOT push/pop — a runtime call while args are pushed
misaligns %rsp; this was the recurring segfault). A C-ABI **layout model**
(`type_size`/`type_align`/`field_offset`/`struct_size`, from `IRModule.structs`)
already exists for sum/struct offsets. DONE: scalars (all int widths), bool/char/
enum, control flow, casts, calls+recursion, panic, string-literal pool (cl_strlit),
io of scalars, GC rooting, sum types (ISumNew/Tag/Field), vec + map ops. Each op
that's unimplemented raises `panic "x86: ..."`. Verify like the C backend but with
`ccrun_x86.sh`; GC-stress with the same ASan+UBSan + `CARDINAL_GC_THRESHOLD=0`.

Remaining (in rough order of ABI difficulty):
1. **Floats (xmm):** ImmFloat (rodata .double / movq immediate), float arith via
   xmm0/xmm1 (addsd/subsd/mulsd/divsd, f32 via *ss), float compares (ucomisd +
   setcc), int↔float casts (cvtsi2sd/cvttsd2si), float args (xmm0..7) + returns
   (xmm0), float to_str (cl_f64_to_str). Self-contained; unlocks float programs.
2. **By-value aggregates (the hard part — SysV ABI):** value structs
   (IStructNew/ILoadField + PField, struct params/returns) and arrays
   (cl_array is 24 bytes; IArrNew/Lit/Get/Set/Len; cl_array_new returns 24B via a
   hidden pointer, cl_array_at takes 24B by value on the stack). Classify each
   aggregate's eightbytes (≤16B → up to 2 regs by INTEGER/SSE class; >16B →
   MEMORY: on the stack as an arg, hidden-pointer return). Multi-word values need
   >8-byte frame slots. This unlocks demo/features/strtest.
3. **Closures:** `cl_closure {void* fn; cl_handle env}` is 16 bytes (2 slots /
   2-reg by value); IAlloc/ILoad (boxes), IEnvNew/Store/Load, IMakeClosure
   (load fn label addr + env into the 2-word value), ICallClosure (cast fn ptr,
   call through it with the SysV cast). The C backend's emission is the reference.

Smaller native gaps shared with the C backend: the **lib search-path** (emitir/
emitx86 only add the source dir + `.`, so programs importing `lib/` modules —
cdemo/usestd — fail; the self-host build doesn't need lib) and **fs::write_file**
(maptest; read_file/exists/args exist, write_file does not yet).

## Smaller deferred items (additive)
- **Native import search path** — `emitir.cardinal` only adds the source file's
  dir + `.` to the checker/lowerer searchdirs, so a program importing a `lib/`
  module (e.g. `examples/cdemo.cardinal`, `usestd.cardinal` use `array`/`math`)
  fails native compilation with "module not found" (they still type-check in
  difftest, which uses a wider search path). Pre-existing; relevant to Stage 5
  (the self-host build must add the compiler's module dirs). `maptest.cardinal`
  separately needs `fs::`/`sys::` runtime builtins declared in the backend.
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
