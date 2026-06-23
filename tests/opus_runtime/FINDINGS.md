# opus_runtime — strings / UTF-8 / to_str / runtime (Opus campaign)

13 tests. 5 confirmed bugs across 3 root causes. The interpreter is the oracle;
C and x86 share the C runtime.

## FIXED

1. **`convert::str_to_int` didn't wrap (interpreter)** — `str_to_int_overflow`.
   `(str_to_int "99999999999999999999")`: interp returned an unbounded Python int
   (`CInt(acc,"i64")` with no `wrap_int`); C/x86 wrap to i64. FIXED: interp now
   `wrap_int(..., "i64")` to match the C runtime. (interpreter.py)

2. **NaN formatting sign** — `float_g_inf_nan`. `(- inf inf)` printed: interp `nan`
   (Python `%g`), C/x86 `-nan` (glibc prints the sign bit). FIXED: the runtime
   prints `nan` for any NaN in cl_f64_to_str and cl_print_f64. (cardinal_rt.c)

## CONFIRMED, NOT YET FIXED (need deeper diagnosis or a semantics decision)

3. **x86 string corruption — to_str of an array with an EMPTY element, and string
   accumulation under GC pressure.** HIGH PRIORITY (memory-safety smell).
   - `x86_tostr_empty_elem` (deterministic): `to_str(["x" "y" ""])` → interp/C
     `[x y ]`, x86 `x y ]` (drops the leading `[`). The empty-element control
     (`x86_tostr_strarray`, no empty element) PASSES, so the empty string is the
     trigger — this is NOT purely GC timing.
   - `longstr_eq` (GC-pressure dependent): `set s = (concat s "ab")` ×1000 → interp/C
     len 2000, x86 len 0; `x86_nested_concat_arg`: `concat("A", concat("B","C"))`
     after a GC-pressure loop → x86 `AC` (drops `B`).
   NOTE: an initial fix attempt (conservatively GC-rooting the 14-word scratch
   parking region at each prologue) did NOT resolve the deterministic empty-element
   case — so the collected/corrupted value is not (only) a parked scratch arg. The
   real mechanism is still open: likely an x86-specific defect in empty-string
   concat or in how a to_str accumulator local survives an allocating append.
   Reverted that attempt; this needs a focused diagnosis (inspect the emitted asm
   for the empty-string concat path).

4. **`from_char` of a codepoint > 0x10FFFF** — `from_char_out_of_range`.
   `(from_char (chr 1200000u32))`: interp crashes with an uncaught Python
   `ValueError` (chr() out of range); C/x86 blindly UTF-8-encode 4 bytes. Both
   stdout AND status diverge, and the oracle leaks a host exception. Fix: validate
   the codepoint (reject > 0x10FFFF) with a clean Panic in BOTH the interpreter and
   the runtime (cl_strings__from_char), so all three agree.

5. **Printing a surrogate codepoint (U+D800)** — `print_surrogate`.
   `(println (from_char (chr 55296u32)))`: interp crashes with an uncaught Python
   `UnicodeEncodeError` (surrogates can't encode); C/x86 emit the 3-byte form.
   `len`/`chars` of a surrogate string agree — only the encode/print path diverges.
   Fix: reject surrogates (0xD800–0xDFFF) in from_char (interp + runtime), same as #4.

## HARDENED (passing, no divergence)
`substr_edges` (past-end / huge / u64-overflow count / codepoint boundary),
`utf8_multibyte` (len/chars/ord/from_char/concat/eq over 4/3/2-byte + combining
mark), `map_multibyte_key` (literal vs runtime-built equal-content keys),
`tostr_nested` (struct/array/vec/map formatting), `from_char_boundary` (U+10FFFF),
`x86_tostr_strarray` (non-empty string array — negative control for #3).
