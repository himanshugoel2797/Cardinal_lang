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

## FIXED (after root-causing — it was NOT a GC bug)

3. **x86 empty-string-literal interning collision.** The agent's "GC pressure"
   framing was a red herring; the three repros (`x86_tostr_empty_elem`,
   `longstr_eq`, `x86_nested_concat_arg`) share one DETERMINISTIC cause. Minimal:
   `(strings::concat "ab" "")` → x86 `""` while `(strings::concat "[" "")` → `"["`.
   Root cause: a regression from the embedded-NUL fix. String literals are emitted
   `.LstrN: .ascii "<bytes>"  .LstrN_end:` and the byte length is `end - start`. An
   EMPTY literal `.ascii ""` is 0 bytes, so its start label shares an address with
   the NEXT entry — and `cl_strlit_n` interns by POINTER, so the empty literal and
   its neighbour collide (whichever is evaluated first wins for both). In
   `longstr_eq` the `""` init collided with `"ab"`, so every `concat(s,"ab")`
   appended `""` → len 0. FIXED: emit a `.byte 0` separator after each entry's end
   label so every literal has a distinct address. (backend_x86.cardinal)

## FIXED

4 & 5. **`chr` of an invalid codepoint (> 0x10FFFF or a surrogate)** —
   `from_char_out_of_range`, `print_surrogate`. `(chr 1200000u32)` then from_char
   crashed the interpreter with an uncaught Python `ValueError`; `(chr 55296u32)`
   (U+D800) then println crashed it with a `UnicodeEncodeError`; the backends
   blindly encoded an invalid UTF-8 form and continued. FIXED: a char is a Unicode
   scalar value, so `chr` now rejects codepoints > 0x10FFFF and surrogates
   (0xD800–0xDFFF) with a clean Panic in BOTH the interpreter (_chr) and the
   runtime (cl_convert__chr). All three paths now panic consistently.

## HARDENED (passing, no divergence)
`substr_edges` (past-end / huge / u64-overflow count / codepoint boundary),
`utf8_multibyte` (len/chars/ord/from_char/concat/eq over 4/3/2-byte + combining
mark), `map_multibyte_key` (literal vs runtime-built equal-content keys),
`tostr_nested` (struct/array/vec/map formatting), `from_char_boundary` (U+10FFFF),
`x86_tostr_strarray` (non-empty string array — negative control for #3).
