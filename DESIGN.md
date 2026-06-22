# Cardinal — Language Design Document

> **Status:** early design. This document captures decisions already made and
> marks every open question with a `- [ ]` checkbox. We fill the holes
> cooperatively, then implement parser → interpreter → compiler.

A statement-structured, keyword-led, statically-typed systems language with
prefix (s-expression) arithmetic. Conceptually: **structured BASIC body +
Lisp arithmetic notation + Modula-2/C memory model.**

---

## 1. Design goals & non-goals

**Goals**
- Easy to *read* (procedural, statement-oriented).
- Easy to *parse* (keyword-led statements, no operator precedence).
- Easy to interpret and to compile to native code.
- Suitable for systems programming (sized numeric types, predictable memory).

**Non-goals (decided)**
- No operator precedence (prefix notation eliminates it).
- No expression-as-statement.
- No significant whitespace.

**Open**
- [ ] Final language name (working title: *Cardinal*; drop "lisp"?).
- [ ] Any explicit anti-goals worth stating (e.g. "no exceptions", "no GC")?

---

## 2. Locked decisions

These are settled unless we deliberately revisit them.

| # | Decision |
|---|----------|
| D1 | **Statement-structured**, not one big expression tree. |
| D2 | **Every statement leads with a keyword** (no-lookahead parsing). |
| D3 | **Prefix s-expression expressions**: `(+ a (* b c))`. No precedence. |
| D4 | **Function calls and operators share one expression shape**: `(f x y)`. |
| D5 | **No expression-as-statement.** A bare expression cannot be a statement. |
| D6 | **Explicit call statement** `do (expr)` for side-effecting/void calls. Statements are unparenthesized; only expressions are parenthesized. |
| D7 | **Assignment is keyword-led** (`let …`), never bare `x = …`. |
| D8 | **Closures supported** (first-class, capturing). Captured environments are heap/handle-allocated and GC-managed (§6). *(Reversed earlier "no closures" once the GC was accepted.)* |
| D9 | **Explicit block terminators** (e.g. `end`), not indentation. |
| D10 | **Statically typed.** |
| D11 | **Sized numeric types**; **no implicit promotion**, all conversions explicit. |
| D12 | **Modules** for namespacing. |
| D13 | Lifetimes are **stack-and-static** (no captured heap environments required by the core language). |

---

## 3. Lexical structure

**Literal syntax** (numeric/float/bool/char/string/null) is specified in §7.1.

**Decided:**

- **Comments**: `#` to end of line. (Block comments: none for now.)
- **Identifiers**: `[A-Za-z_][A-Za-z0-9_]*`, ASCII, **case-sensitive**.
- **Keywords are reserved.** Reserved set: `module import export func struct packed
  enum let const set do if elsif else while for to step in loop break continue
  return checked and or not as true false null band bor bxor bnot shl shr` plus the
  type names `i8 i16 i32 i64 u8 u16 u32 u64 f32 f64 bool char str handle unit`.
- **Statement terminator = newline.** One statement per line. **No INDENT/DEDENT**
  (blocks close with `end`); indentation is purely cosmetic. A newline inside an
  unclosed `(` or `[` is a **continuation** (so prefix expressions may span lines).
  Blank lines and comment-only lines are ignored. A block expression
  (`func … end`) carries its own terminator and may span lines as an RHS.
- **Punctuation/operator tokens**: `( ) [ ] : :: . -> = + - * / % < <= > >= == !=`.

**Open**

- [ ] Allow `;` as an optional same-line statement separator? *(lean: no)*
- [ ] Block comments later (`#[ … ]#`)?

---

## 4. Worked example

> A complete program in the current design. Statements are keyword-led and
> **unparenthesized**; expressions are **parenthesized prefix**. Detailed rules
> are in §5.3 (structs/arrays), §7 (expressions), §8 (statements), §9 (functions),
> §10 (modules).

```
module demo

import io

struct Point
    x i32
    y i32
end

export func dist2 (a Point) (b Point) -> i32
    let dx = (- a.x b.x)
    let dy = (- a.y b.y)
    return (+ (* dx dx) (* dy dy))
end

func main () -> i32
    let p = (Point x: 0 y: 0)
    let q = (Point x: 3 y: 4)
    let d = (dist2 p q)

    let xs [i32] = [10 20 30 40]
    let sum = 0i32
    for x in xs
        set sum = (+ sum x)
    end

    if (> d sum)
        do (io::println "d wins")
    else
        do (io::println "sum wins")
    end

    return 0i32
end
```

---

## 5. Type system

### 5.1 Primitive types

**Decided:**
- Signed ints: `i8 i16 i32 i64` (1/2/4/8 bytes).
- Unsigned ints: `u8 u16 u32 u64` (1/2/4/8 bytes).
- Floats: `f32 f64`.
- `bool`.
- `char` — a Unicode scalar value (decode point), distinct from `u8` (raw byte).
- `str` — text. *Note: not a scalar; a **managed reference type** (array-backed, handle + length, GC'd, reference semantics). Listed here for convenience.*
- `handle` — opaque type for the **foreign/raw** case (MMIO, FFI). *See note below on representation vs. static type.*

> **Representation vs. static type:** everything heap-allocated is *represented*
> as a tagged handle underneath, but arrays, strings, and closures keep their own
> **distinct static types** so the checker won't let a closure stand in for an
> array. The user-visible `handle` type is reserved for the opaque foreign/raw
> escape hatch only — it is *not* the common supertype of all heap things.

**Decided (this round):**

- `char` = 32-bit Unicode scalar value. `str` = UTF-8.
- **No `isize`/`usize`** — fixed widths only.
- **Array length & index type = `u64`** (unsigned, non-negative sizes; large enough to ignore overflow).

**Open**

- [ ] A unit/void type for functions that return nothing (name? `unit`/`void`/none?).

### 5.2 Numeric semantics

**Decided:**

- Operands of an n-ary op must share a type; widening/narrowing is an explicit cast.
- **Cast = `as`**, prefix form `(as value type)` → e.g. `(as x i64)` ("x as i64", value-first).
- **Integer overflow: machine semantics — defined two's-complement *wrapping* by default** (NOT undefined behavior; keeps the language UB-free).
- **Opt-in `checked … end` block** makes all arithmetic within it **trap** on
  overflow. Block form (not a line suffix) — consistent with the block-structured
  syntax and guards a whole region.
- **`checked` is lexical** — governs only arithmetic textually inside the block,
  not arithmetic in functions it calls.
- **Numeric literals are context-typed** (see §7.1): a bare literal takes its type
  from context; a suffix forces one.

**Open**

- [ ] Division/modulo semantics (truncation toward zero?, divide-by-zero → trap to stay UB-free?).
- [ ] Float behavior (IEEE-754 conformance, NaN handling).

### 5.3 Aggregate & user types

**Decided:**

- **Structs = value types** (see §6.3): inline layout, copied on pass/assign.
- **Arrays = managed reference type**, **length fixed at allocation time**
  (length may be a runtime value, but no resize after allocation). Bounds-checked
  indexing. Growable "vector" = a library construct on top (allocate + copy).
- **`str` = immutable, opaque managed type** (UTF-8 internally; representation
  hidden from the language). Mutation only via helpers that return new strings.

**Syntax (proposed):**

- **Struct declaration** — one `name type` field per line:
  ```
  struct Point
      x i32
      y i32
  end
  ```
- **Layout control** via a `packed` modifier (for MMIO/ABI); explicit alignment TBD:
  ```
  packed struct Regs
      status  u32
      control u32
  end
  ```
- **Struct construction** — named fields with `label:` (unambiguous, comma-free):
  ```
  let p = (Point x: 1 y: 2)
  ```
- **Array literal** — space-separated elements, type inferred: `[10 20 30]`.
- **Sized array allocation** — `(array i32 256)` (256-element, zero-initialized).
- **Array type** (for annotations) — `[T]`, e.g. `let xs [i32] = [1 2 3]`.
- **Vector** (growable) — a **builtin generic** type `{T}`, parallel to arrays:
  literal `{1 2 3}`, empty `(vec T)`, ops `push`/`pop` + reused `xs[i]` / `len` /
  `set xs[i] = …` / `for x in xs`. Reference semantics. Implemented as a
  compiler-blessed parameterized type (like `[T]` already is) — **no general
  generics, no unification, no bounds** — just special-cased in checker + runtime.
  Nests: `{{i32}}`, `{Expr}`, etc.
- **Map** (growable) — builtin generic `{K V}` (arity distinguishes it from the
  vector `{T}`). Empty `(map K V)`; access via `m[k]` / `set m[k] = v` (reused
  indexing); `map_has`/`map_del`/`map_keys` + `len`. Reference semantics. **Keys
  restricted to value-semantic hashable types** — `str` (content equality, the
  symbol-table case), integers, `char`, `bool`, `enum`; others rejected. This is
  the "bounds" problem solved by builtin special-casing rather than a constraint
  system. (Pointer/identity-keyed maps for arbitrary references: possible later.)
- **Field/index access** — `p.x`, `buf[i]` (§7 access sugar).
- **Enums = simple C-style named integer constants** (no payloads). Variants are
  accessed with `::` (scope resolution): `Status::NotFound`.
  ```
  enum Status
      Ok
      NotFound
      Denied
  end
  ```
- **Sum types (tagged unions)** — `type Name … end`, one variant per line with
  payloads as `(field type)` groups; nullary variants are bare names. Variant
  names are program-unique. Construction reuses labeled syntax: `(Add lhs: a rhs: b)`,
  nullary `Leaf`. **Sum values are reference types** (managed/handle-backed —
  required, since they're recursive: an AST node contains AST nodes).
  ```
  type Expr
      Num (value i32)
      Add (lhs Expr) (rhs Expr)
  end
  ```
  Discriminate with **`match`** (a statement, §8): `case (Add l r)` binds the
  payload positionally; `else` is the default; **non-`else` matches must be
  exhaustive** (missing variants are a compile error). Front-end (interpreter +
  type checker) implemented; C-backend lowering deferred.
- **No tuples** (for now). Value + error still uses a **result struct** (§11).

**Open**

- [ ] Explicit alignment control beyond `packed`.
- [ ] Enum backing type / explicit discriminant values (`Ok = 0`)?
- [ ] Type aliases / `typedef`.
- [ ] `match` as an *expression* (currently statement-only); wildcard patterns / nested patterns.

### 5.4 Type inference
- [ ] How much inference for `let` (full annotation required, or infer from RHS)?

---

## 6. Memory model

> The area that defines the "systems" character.

### 6.1 Decided

- **Pointers exist but are "safed."** No native pointer arithmetic. Raw
  addresses can only be *created and manipulated* through provided APIs.
- **Two distinct reference categories:**
  1. **Managed references** — language-allocated objects (arrays, structs).
     Accessed only via safe APIs (e.g. array indexing is bounds-checked).
     Subject to the runtime/GC.
  2. **Foreign/raw addresses** — MMIO, FFI, hardware. Wrapped in an **opaque
     handle** whose accessor API carries its own checks. **Never traced or
     collected** by the runtime; lifetime is external.
- **Arrays are a managed reference type** (reference semantics; bounds-checked).

### 6.2 Decided

- **Managed refs represented as generational handles** (index + generation into
  a runtime handle table), not raw addresses. Gives use-after-free detection for
  free and makes precise/compacting GC tractable in compiled code.
- **Reclamation: mark-and-sweep over the handle table**, non-moving to start;
  handle indirection leaves room to add compaction later.
- **Foreign addresses are a separate opaque handle type**, never traced.
- **Closure environments are managed (handle-allocated, GC'd)** like any other heap object.
- **Conservative GC.** The collector scans the stack and treats any word that
  decodes to a currently-live handle slot as a root. Handles carry a **tag in
  their encoding** (reserved high bits of the packed `(index, generation)` word)
  so handle words are distinguishable from numeric data; the generation counter
  plus tag make false retention negligible. (OS-level address-space control is
  available as an extra lever but not required by this scheme.)
- **`null` = the zero handle.** Handle slot 0 is permanently reserved/invalid,
  so any access through null **traps cleanly** (defined runtime error, never UB).
  `null` is a special bottom-ish type: a literal assignable to any reference type.
- **Working default: all references are nullable** (a deref of null traps).
  Non-null-by-default + opt-in optionals (`T?`) is a possible future upgrade.

### 6.3 Aggregates & GC scanning — Decided

- **Structs are value types** — copied on assign/pass, live inline (stack, inside
  other structs, contiguous in arrays), full layout control (MMIO/C-ABI/binary
  formats). Sharing/identity/nullability is opt-in by taking a **handle to** a struct.
- **GC scanning:** *conservative* on the stack (tagged handle words), *precise*
  inside heap objects (runtime knows each object's layout). Value structs may
  embed handle fields; both paths find them.

### 6.4 Open

- [ ] *(philosophy, deferrable)* keep nullable-everywhere, or move to non-null-by-default + optional types?
- [ ] Mutable vs. const references — one kind or several?
- [ ] Explicit `alloc`/`free` available alongside GC, or GC-only for managed memory?
- [ ] Manual deallocation interaction with handles (free now, generation bump → dangling-handle traps?).
- [ ] The concrete "pointer API" surface: pointer→array view, MMIO accessors, etc.

---

## 7. Expressions

**Decided:** prefix; calls and operators share one shape `(op args…)`; no
precedence (the parens *are* the grouping); operators are **builtins** (intrinsics
known to the type checker), not ordinary functions.

### 7.1 Proposed concrete syntax

> First concrete proposal — all open to revision.

**Literals**

- **Integer**: bare `42` is an *untyped numeric literal* that takes its type from
  context (the declared type, or the other operand). If the type cannot be
  inferred, it is a **compile error** — no implicit default. Suffix forces a type:
  `42i32`, `255u8`, `0u64`. Bases: `0xFF`, `0b1010`, `0o17` (suffixable: `0xFFu8`).
  Digit separator: `1_000_000`.
- **Float**: `3.14`, `1.0e-9`; suffix `f32` / `f64`.
- **Bool**: `true`, `false`.
- **Char** (32-bit scalar): `'a'`, `'\n'`, `'\t'`, `'\\'`, `'\u{1F600}'`.
- **String** (UTF-8): `"hi\n"`, same escapes, `\u{…}` for scalars.
- **Null**: `null` (bottom reference type; assignable to any reference type).

**Calls** — `(f x y z)`. Same shape as operators.

**Arithmetic** (operands share a type → same type; wraps by default, traps in `checked`)

- `(+ a b)` `(* a b)` — n-ary: `(+ a b c)`.
- `(- a b)` binary subtract; `(- a)` unary negate.
- `(/ a b)` `(% a b)` — binary.

**Comparison** (operands same type → `bool`; binary, no chaining)

- `(< a b)` `(<= a b)` `(> a b)` `(>= a b)` `(== a b)` `(!= a b)`

**Logical** (`bool` → `bool`; `and`/`or` short-circuit, n-ary)

- `(and a b …)` `(or a b …)` `(not a)`

**Bitwise** (word forms, chosen over cryptic `& | ^ ~`)

- `(band a b)` `(bor a b)` `(bxor a b)` `(bnot a)` `(shl a n)` `(shr a n)`

**Cast** — `(as value type)` → `(as x i64)`.

**Data access** (postfix sugar — the one readability exception to pure prefix)

- Field: `p.x`, chained `p.origin.x`. Pure form also available: `(. p x)`.
- Index: `a[i]` (bounds-checked). Pure form: `(index a i)`.
- Both are unambiguous primary-suffixes → they add **no** precedence.
- Accessors **auto-deref through a handle**: `h.x` / `h[i]` work whether the base
  is a value struct/array or a handle to one.

**References**

- `(ref place)` — take a handle to a value (opt-in reference semantics, §6).
- Detailed pointer / MMIO / array-view API: still open (§6.4).

### 7.2 Decided

- **Postfix `.field` / `[i]` sugar** confirmed (alongside pure `(. …)` / `(index …)`).
- **Bitwise = word forms** (`band bor bxor bnot shl shr`).
- **Typed-literal suffix = Rust-style** `42i32`, `255u8`, `3.14f64`. Hex (and `0b`/`0o`)
  bases available: `0xFFu8`, `0b1010i32`.
- **No fallback literal type.** A literal's type must be inferable from context;
  if it cannot be inferred, that is a **compile error** (explicit annotation
  required). There is no implicit `i32` default.

### 7.3 Open

- [ ] (Struct/array construction literal syntax now specified in §5.3.)

---

## 8. Statements & control flow

**Decided:** keyword-led; explicit `end` terminators; statements are **not**
parenthesized (only expressions are). Expressions appear in statement *slots*:
the RHS of `let`/`set`, conditions, call arguments, and `return` values.

### 8.1 Declaration & assignment

- **`let`** — new **mutable** variable; type optional (inferred from RHS):
  ```
  let x = (+ a b)
  let count i32 = 0
  ```
- **`const`** — immutable binding (value may be runtime-computed, fixed after init):
  ```
  const limit u32 = 1024u32
  ```
- **`set`** — mutate an existing *place* (variable, field, or element). Keeps
  assignment keyword-led; there is no bare `x = …`:
  ```
  set i = (+ i 1)
  set p.x = 10
  set buf[k] = 0u8
  ```

Variables are **mutable by default** (`let`); `const` is the immutable form.

### 8.2 Call statement

- **`do`** evaluates a call for its effects and discards the result — the *only*
  place a value may be discarded (§9.4):
  ```
  do (print "hello")
  ```

### 8.3 Conditionals

```
if (> x 0)
    do (print "pos")
elsif (== x 0)
    do (print "zero")
else
    do (print "neg")
end
```

### 8.4 Loops

```
while (< i n)
    set i = (+ i 1)
end

for i = 0 to n           # numeric: i = 0,1,…,n-1  (HALF-OPEN, matches indexing)
    do (use i)
end

for i = 0 to n step 2    # optional step
    ...
end

for x in items           # foreach over an array's elements
    do (use x)
end

loop                     # infinite; exit with break
    if (done)
        break
    end
end
```

- **`break`** / **`continue`** act on the nearest enclosing loop (no labels yet).

### 8.5 Other statements

- **`return`** — `return (expr)`, or bare `return` in a `unit` function.
- **`checked … end`** — overflow-trapping region (§5.2), lexical.

### 8.6 Scoping

- Each block (`if`/`while`/`for`/`loop` body, `func` body) is a lexical scope.
- Inner names shadow outer; a name lives until the end of its block.

### 8.7 Open

- [x] Multi-way branch — **`match`** over sum types, statement form, exhaustive (§5.3). Front-end done.
- [ ] Need an empty/no-op statement (`pass`/`nop`)?
- [ ] Labeled break/continue for nested loops — later?

---

## 9. Functions

**Decided (semantics):**

- First-class **closures** (capturing); environments GC-managed (§6).
- **Capture by reference** (captured locals boxed; mutations shared with enclosing scope).
- **Parameter passing: by value by default**; reference types (arrays, closures,
  `str`) pass by reference.

### 9.1 Definition

```
func add (a i32) (b i32) -> i32
    return (+ a b)
end
```

- Each parameter is a `(name type)` group — space-separated, no commas (consistent
  with the rest of the syntax).
- Return type after `->`. **Omit `-> …`** for a `unit` (no-value) function.
- Recursion and mutual recursion allowed regardless of definition order in a module.

### 9.2 Closures (anonymous functions)

An anonymous `func` is an **expression** that yields a closure:

```
let n = 10i32
let addn = func (x i32) -> i32
    return (+ x n)           # captures n by reference
end
do (print (addn 5))
```

### 9.3 Function types (callback parameters)

A function type is written `func(PARAMTYPES -> RET)`:

```
func apply (f func(i32 -> i32)) (x i32) -> i32
    return (f x)
end
```

### 9.4 Returns & discards

- **Single return value.** (Multiple results via a struct/tuple — §5.3 open.)
- A pure expression's value may **not** be silently discarded; the only discard
  path is the `do` call statement, intended for effectful/`unit` calls.

### 9.5 Entry point

- Program entry is **`func main () -> i32`** (process exit code). `-> unit` also allowed.

### 9.6 Open

- [ ] What's capturable — locals only, or also enclosing params? *(lean: both)*
- [ ] Default arguments / variadics? *(lean: no)*
- [ ] Should `do` warn when the callee actually returns a non-`unit` value being thrown away?

**Decided:** single return value only. Value + error is returned via a **result
struct** (§11), not multiple returns or tuples.

---

## 10. Modules

**Decided:**

- **One module per file; the file *is* the module and the unit of compilation.**
- **`module NAME`** at the top of the file declares it.
- **`import`** brings in another module — whole or selective:
  ```
  import io
  import math (sin cos)     # selective names, space-separated, no commas
  ```
- **Private by default; `export`** marks a declaration public:
  ```
  export func square (x i32) -> i32
      return (* x x)
  end
  ```
- **Qualified access uses `::`**: `io::println`, `math::sin`. Distinct from field
  access (`.`), so module paths and field access never need disambiguation — `::`
  always means a module path, `.` always means field/element access.

### 10.1 Open

- [ ] Cyclic module dependencies allowed?
- [ ] Import aliasing (`import math as m`)?
- [ ] Standard library: what's built-in (`print`, alloc, math) vs. an imported module?

---

## 11. Error handling

**Decided:**

- **No exceptions.** Two mechanisms only:
  1. **Panic** — for *unrecoverable* errors / bugs. Fatal: prints a diagnostic and
     aborts (halts/traps in a bare-metal/OS context). The runtime's clean traps
     all panic: null access, out-of-bounds index, divide-by-zero, overflow inside a
     `checked` block, failed allocation.
  2. **Return values** — for *recoverable*, expected failures (file not found,
     parse error). The callee returns a **result struct** bundling the value and an
     error code; the caller inspects it. Error codes are **simple enums** (§5.3).

**Open**

- [ ] Is panic ever catchable/recoverable (unwinding), or always fatal? *(lean: always fatal first — no unwinding machinery)*
- [ ] Panic mechanics: stack-unwind vs. immediate abort. *(lean: abort — no destructors; GC owns memory)*
- [ ] A `panic(msg)` builtin + any standard abort helpers.
- [ ] A standard result-struct convention/helper, or hand-rolled per call site?

---

## 12. Implementation plan

**Decided — two-tier, self-hosting:**

1. **Bootstrap interpreter** — a *throwaway* tree-walker in a **higher-level host
   language** (fast to write; e.g. Python). Its only job is to be correct enough to
   run the self-hosted compiler. Its runtime is a throwaway too — it need not be the
   canonical runtime.
2. **Self-hosted compiler + canonical runtime, written in Cardinal.** This is the
   real system. Two backends:
   - **C emitter** — portable, reuses existing C toolchains; the easier first backend.
   - **Direct x86_64** — native binary, no C dependency; needed for bare-metal / OS use.

**Runtime architecture:** the canonical runtime (handle table + mark-and-sweep GC
+ conservative stack scanner) is **written in Cardinal**, targeting the
self-hosted compiler. The bootstrap interpreter has its own simple stand-in
runtime in the host language — deliberately disposable, since it disappears once
the compiler self-hosts. (Conservative GC needs no stack maps, so it ports cleanly
to the C and x86_64 backends.)

**Bootstrap sequence:**

1. Build the bootstrap interpreter (+ its stand-in runtime) in the host language.
2. Write the Cardinal compiler and canonical runtime in Cardinal.
3. Run that compiler *under the interpreter* to compile itself → a native Cardinal
   compiler that can thereafter recompile itself. The bootstrap interpreter is then
   retired.

**Status — bootstrap toolchain built** under `bootstrap/`:

- `interpreter.py` — lexer + recursive-descent parser + tree-walking evaluator.
- `typecheck.py` — ahead-of-time static type checker (DESIGN.md §5, §10).
- `cardinal.py` — driver: type-check then run (`--no-check`, `--check-only`).

Language coverage: modules + cross-file imports, exported/private visibility,
structs (value semantics, copy-on-move), arrays (reference, bounds-checked,
`(array T n)` allocation, `len`), enums, **sum types + `match`** (front-end only —
C-backend lowering pending), functions, closures (capture by
reference), `let`/`const`/`set`, `if`/`elsif`/`else`, `while`, numeric + foreach
`for`, `loop`/`break`/`continue`, `return`, `do`, `checked` blocks (verified
lexical), sized-int wrapping arithmetic, context-typed numeric literals
(uninferable = error), comparison / short-circuit logical / bitwise ops, casts,
`null`, `panic`, the `io` module.

The **type checker** enforces statically: no implicit promotion, cast-required
conversions, literal inference (uninferable = error), `null` assignability,
field/element/argument/return type matching, `bool` conditions, mutability of
`set` targets, and module visibility / `::` paths. Verified to catch the full
battery of invalid programs and pass the valid examples.

**Standard library** (`lib/`, written in Cardinal): `math` (abs/min/max/clamp/pow)
and `array` (sum/contains/max/fill). Always on the module search path.

**Compiler backend** — the pipeline is now
`AST → type-check (annotates `.ctype`) → lower to IR → Backend.emit`:

- `ir.py` — target-independent IR: functions of basic blocks, SSA-free, with
  temporaries + named locals and explicit branches. Reuses the type lattice.
  Shared optimization passes belong here, not in any single backend.
- `backend.py` — abstract `Backend` interface + registry, so targets are
  swappable. The future `x86_64` backend consumes the **same IR** and emits
  relocatable objects directly (ELF preferred, for the custom OS).
- `lower.py` — AST→IR lowering.
- `backend_c.py` + `runtime/cardinal_rt.{h,c}` — the **C backend**: emits portable
  C and defers optimization to the host C compiler (`-O2 -fwrapv`; `checked`
  arithmetic via `__builtin_*_overflow`). Bootstrap runtime is throwaway
  (malloc'd arrays, no GC).
- `cardinalc.py` — compiler driver (`--backend`, `--emit`, `-o`, `--run`).
- `runtime/cardinal_gc.{h,c}` — the **GC runtime** (reference C implementation of
  the §6 model): generational tagged handles in a handle table, mark-and-sweep,
  conservative scanning of stack + heap objects for handle-looking words,
  use-after-free detection on a stale `deref`. Validated standalone by
  `runtime/gc_test.c` (rooted objects survive, **cyclic garbage reclaimed**,
  stale-handle deref panics) **and wired into the C backend**: arrays, closure
  boxes, and environments are now GC handles (`cl_array.data`, `cl_closure.env`,
  and `PtrT` all map to `cl_handle`); allocation goes through `cl_gc_alloc`,
  access through `cl_gc_deref`. **Rooting is precise via a shadow stack**: the
  backend zero-inits every managed local/temp/param, pushes its address+size on
  function entry (`cl_gc_push_root`), and pops on every return; the collector
  scans exactly those slots. Taking `&var` forces it to memory, so there is no
  register-only-root hazard even at `-O2`, and no conservative over-retention from
  stale machine-stack words (the standalone `gc_test` now reports exact live
  counts at `-O2`). Within each rooted slot, handle-finding is still a cheap
  tagged-word scan, which transparently covers structs/closures/arrays holding
  handles. `examples/gcstress.cardinal` (200k iterations, ~800k allocations) stays
  at ~2.3 MB RSS with the collector vs. ~48 MB disabled, same output. Tunable via
  `CARDINAL_GC_THRESHOLD` / `CARDINAL_GC_STATS`. The canonical runtime is
  ultimately rewritten in Cardinal; scanning global/module state and a moving/
  compacting collector remain future work.

The C backend compiles `examples/{demo,cdemo,features,closures}.cardinal` to
native binaries whose stdout is **byte-identical** to the interpreter (structs,
arrays, enums, recursion, cross-module stdlib calls, all control flow, casts,
wrapping/`checked` arithmetic, bounds/overflow panics).

**Closures** are supported via closure conversion at the lowering/IR level (so the
future x86_64 backend inherits it): free-variable analysis, **boxing** captured
variables on the heap for by-reference sharing, lambda lifting (each `func`
literal becomes a top-level function taking a `void* env`), environment arrays of
box pointers, and function-as-value **thunks** for passing named functions. The
closure value is `{fn, env}`; calls go indirect through it. Verified for
parameter capture, escaping closures, two closures sharing one cell, and
multi-level nested capture. Deferred in the C backend: **sum types + `match`**,
`null`/handles/`ref`, printing struct/array/enum values, and capturing a
`for`-loop variable.

**Deliberate punts in the bootstrap** (it is throwaway):

- Uses Python's objects/GC instead of the canonical handle-table + mark-and-sweep
  runtime — that runtime is written later in Cardinal.
- `(ref …)` supports simple variables only; `f32` precision approximated; the
  result-struct error convention isn't yet a library helper.
- Type checker checks the bodies of *all* loaded modules (main + every import,
  including the stdlib). Errors carry the module name and source line
  (`buggy: line 5: …`); AST nodes are stamped with `.line` at parse time.
- Stdlib is i32-specific (no generics yet).

**Open**

- [x] Backend order — **C emitter first** (done), then x86_64.
- [ ] x86_64 backend: a `Backend` over the same IR — target OS/ABI (System V AMD64? your own OS?), output format (ELF?), register allocation, calling convention.
- [x] Closures in the C backend — done (closure conversion at IR level).
- [x] GC runtime — reference C collector built + validated (`runtime/cardinal_gc.*`).
- [x] Wire the GC into the C backend — done (arrays/boxes/envs are handles; collection bounds memory under `-O2`).
- [x] Robust rooting — precise **shadow stack** (sound at `-O2`, exact live counts).
- [ ] Scan global/module state (managed top-level state isn't compiled yet); moving/compacting collector; the canonical GC rewritten in Cardinal.
- [ ] C backend gaps: **sum types + `match`** (tagged-union emission + tag switch), `null`/handles/`ref`, struct/array/enum printing, `for`-loop-variable capture.
- [ ] IR-level optimization passes (const-folding, DCE) shared across backends.
- [ ] Testing: golden tests + example programs; **self-compilation as the ultimate test**.

### 12.1 Path to self-hosting

The critical path is **not** "rewrite the Python compiler" — it's making the
**interpreter + type checker** (the bootstrap host) able to *run* a compiler whose
source is written in Cardinal. The Python C backend is a throwaway stage-0
reference and is off this path. The interpreter/checker must support every
language/stdlib feature the compiler's Cardinal source uses:

- [x] **Sum types + `match`** — AST/IR are tagged unions. (front-end done)
- [x] **String/char ops** — `strings` (chars/concat/substr/from_char/eq) and
  `convert` (ord/chr/int_to_str/str_to_int) builtin modules; `char` comparison.
  (lexer input + codegen text)
- [x] **Generics decision** — *no general generics.* Collections are **builtin
  compiler-blessed parameterized types** (special-cased like `[T]`), using `{…}`
  notation. Avoids a unification engine + bounds system.
- [x] **Growable vector** `{T}` — done (literal, `(vec T)`, `push`/`pop`, reused
  index/`len`/`for`).
- [x] **Map** `{K V}` — done (`(map K V)`, `m[k]`/`set m[k]=…`, `map_has`/`map_del`/
  `map_keys`/`len`; keys restricted to hashable value-semantic types).
- [x] **File I/O + argv** — `fs::read_file`/`write_file` (+ `_cb` callback variants,
  §13) and `sys::args() -> {str}`.

**All prerequisites are now in place** — the interpreter + checker can express a
compiler (sum types, strings, vectors, maps, file I/O, args). The compiler is being
ported to Cardinal (lexer → parser → checker → a backend), using the Python one as
the reference spec → run it under the interpreter to compile itself → fixed-point
check.

**Self-hosting compiler progress** (under `compiler/`, run by the bootstrap interpreter):

- [x] **Lexer** — `compiler/lexer.cardinal`. Port of `bootstrap/interpreter.py:lex`.
  `TokKind` sum type + `Tok {kind,line}` struct; `lex(str) -> {Tok}`. Handles
  comments, string/char literals with escapes, numbers (base prefixes, underscores,
  float fraction/exponent, type-suffix validation), idents/keywords/types,
  multi/single-char punctuation, and newline-as-terminator with bracket-depth
  suppression. Tested (`compiler/lextest.cardinal`, `compiler/lextest_edge.cardinal`)
  and reviewed for fidelity against the reference. Intentional divergences from the
  Python reference: float literals keep verbatim mantissa **text** (not a parsed
  float — no precision loss; the parser parses it), int literals accumulate in
  `i64` (the language's max int), and identifiers/suffixes are ASCII-only.
- [x] **Parser** — `compiler/parser.cardinal`. Recursive-descent port of
  `bootstrap/interpreter.py:Parser`. Defines the AST as sum types
  (`Ty`/`Expr`/`Stmt`/`Decl` + `Param`/`Variant`/`Field`/`Branch`/`Arm`/`Import`/
  `Module`), threads the cursor through a shared 1-element `{u64}` cell inside a
  value struct, and exposes `parse(str) -> Module` plus an AST pretty-printer
  (`module_str`). **Self-parses** (parses the lexer and the parser's own source),
  all examples, and the stdlib. Tested (`compiler/parsetest.cardinal`,
  `compiler/parsetest_edge.cardinal`, `compiler/parseall.cardinal`) and
  adversarially reviewed as behaviorally equivalent to the reference. Punt: AST
  nodes carry no source line numbers yet (tokens do — can be threaded later).
- [ ] **Type checker** in Cardinal — next: consume `Module` and port
  `bootstrap/typecheck.py` (sum types, bidirectional checking, the builtin
  collection/`{…}` rules, match exhaustiveness).
- [ ] **Backend** in Cardinal.

Implementation notes learned porting to Cardinal (apply to the checker too):
keyword-colliding identifiers are illegal, so field/var names like `step`/`packed`
must be renamed (used `stride`/`is_packed`); nullary sum variants construct **bare**
(`ENull`, not `(ENull)`); module-qualified **type names** (`lexer::Tok`) are not
supported in type position — use the bare imported name (`Tok`); cross-module
`match` uses bare variant names; `if`/`case` bodies must start on the next line;
optional fields are encoded as a `has_*` bool + sentinel.

Note: `compiler/parser.clp` is a stale artifact from the original Lisp-syntax design
and is unrelated to the current port (safe to delete).

---

## 13. Concurrency & async

**Decided: callbacks-first.** The foundational mechanism for completion / event
handling is the **closure-as-callback** (a closure passed as an argument, possibly
escaping and stored to fire later). No new language machinery — closures already
exist, and an escaping callback is exactly the heap-boxed, GC-managed closure case.

**Why callbacks over `async`/`await`:**

- **Free today.** A callback is just a closure; async would need a suspend/resume
  mechanism (state-machine transform *or* stackful coroutines), a scheduler/event
  loop, and **GC entanglement** (suspended computations hold live roots that the
  collector must scan/trace). That's "closures on hard mode."
- **Systems fit.** At the kernel/driver layer we want explicit control (ISRs,
  completion routines, deferred work) and **no hidden runtime**. Callbacks map
  onto that; async/await's implicit scheduler is application-level convenience.
- **Error model fit.** With panic + result-structs and *no exceptions*, async's
  main ergonomic win (exceptions across `await`) doesn't apply — you'd thread
  result-structs anyway. A callback taking a result-struct param is explicit and
  consistent with §11.

**How to apply / roadmap:**

1. Callbacks (closures) are the primitive. (Available now.)
2. Build a small completion + event-loop **library in Cardinal** over closures.
   (The GC is now wired into the C backend, so escaping callbacks no longer leak —
   that prerequisite is met.)
3. `async`/`await` is a possible **future sugar** layered over (2), added only if
   callback composition proves too painful — it's expensive to implement and must
   earn its way in.

**Still open:** the I/O completion / scheduling model (synchronous callbacks vs.
an event loop vs. coroutines), which both patterns depend on and which interacts
with the GC's rooting story for stored/suspended closures.

---

## 14. Open cross-cutting decisions (quick list)

- [ ] Language name.
- [ ] Overflow semantics.
- [x] Pointer/reference safety model — *safed pointers, generational handles, mark-and-sweep GC (§6).*
- [x] Closures — *kept (capturing), enabled by GC (D8).*
- [ ] Compiler backend target.
- [ ] Mutability default.
- [ ] String representation.
- [ ] Error-handling model.
- [x] Concurrency model — *callbacks-first (§13); async/await deferred as optional sugar.*

---

## 15. Glossary / conventions for this doc

- **Decided** / table in §2 = settled.
- `- [ ]` = open hole to fill.
- Code blocks = *illustrative*, not normative until the relevant hole is closed.
