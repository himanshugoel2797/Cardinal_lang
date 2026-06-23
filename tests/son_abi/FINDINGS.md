# son_abi — SysV ABI / calling-convention torture (Sonnet campaign)

17 tests probing the x86_64 SysV ABI. All 5 breaks found are in ONE place: the
**named-function-as-value thunk** (`emit_thunk` in `compiler/backend_x86.cardinal`),
which adapts a closure call (env in a register) to a plain call by shifting GP args
down one register and tail-jumping. It was an acknowledged-incomplete milestone
(it already had explicit "later milestone" panics). Lambdas (anonymous closures)
handle all these shapes correctly — only the named-function adapter was partial.

## Breaks found (5) and their resolution

| test | trigger | original symptom | resolution |
|------|---------|------------------|------------|
| t15_thunk_bigret | named-fn value returning a >16B struct | thunk's arg shift overwrote `%rdi` (the SysV MEMORY hidden return pointer) with env → **SIGSEGV** | **FIXED** — shift now starts at index 1 when the return is MEMORY-class, preserving the sret pointer. Output correct three-way. |
| t13_thunk_6int_args | named-fn value with 6 int args | 6th arg spilled to stack in the closure call; `argreg` clamps idx≥5 to `%r9`, so the thunk emitted `movq %r9,%r9` and never loaded the stack arg → **silently wrong** | **HARDENED** — now a loud compile-time panic ("function-value thunk with stack-spilled args"); no longer a silent miscompile. Full support is a later milestone. |
| t14_thunk_7int_args | named-fn value with 7 int args | two stack-spilled args lost → silently wrong | same as t13 |
| t09_thunk_struct_param | named-fn value taking a struct by value | compile panic ("by-value struct thunk param") | unchanged — loud panic; lambdas handle struct params |
| t11_thunk_array_param | named-fn value taking an array by value | compile panic ("by-value array thunk param") | unchanged — loud panic |

## Passing (hardened) cases — 12
Float-param thunks (xmm regs untouched by the GP shift), lambda struct params
(bypass the thunk), 16B struct at the GP-reg boundary, interleaved int/float/struct
args, nested structs, return of a 12B struct, 5-int function value (just fits in
registers), closure-typed param. All agree three-way.

## Latent observation (not a run3 divergence today)
The x86 backend classifies every aggregate arg/return as all-INTEGER eightbytes —
no per-field SSE classification. It is internally consistent (x86↔C agree on
stdout), so it is not a differential bug, but x86-compiled code calling a genuine
SysV C callee that expects float eightbytes in XMM would mismatch. Noting for a
future ABI-completeness milestone.

## Fix verification
Both self-host fixed points byte-identical after the fix (72773 lines); the
working thunk cases (t10/t16/t17) unaffected.
