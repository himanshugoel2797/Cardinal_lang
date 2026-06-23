# Arithmetic Test Findings

## CONFIRMED BUGS

---

### BUG 1 — Interpreter: float-precision loss in 64-bit integer division/modulo

**Root cause:** `interpreter.py` `arith()` uses `int(acc / x)` (Python float division) to truncate-toward-zero. Python floats have a 53-bit mantissa; i64/u64 values near `2^63` have 63 significant bits, so the float approximation is wrong by a few ULPs, producing the wrong quotient and remainder. Example: `int(9223372036854775807 / 1.0)` = `int(9.223372036854776e18)` = `9223372036854775808` which wraps in `wrap_int` to `-9223372036854775808` (INT64_MIN).

**Minimal repro:** `a25_bug_u64_div_mod_1.cardinal`
```
(/ 18446744073709551615u64 1u64)   # interp: 0                      C/x86: 18446744073709551615
(% 18446744073709551615u64 1u64)   # interp: 18446744073709551615   C/x86: 0
```

Also triggered in: a04, a18, a20, a21, a23, a26, a27, a30.

**Fix direction:** Replace `int(acc / x)` with true integer truncation-toward-zero (e.g. `math.trunc(acc / x)` is still float, so use `(acc // x) if (acc < 0) == (x < 0) else -((-acc) // x)` or `int(Fraction(acc, x))`).

---

### BUG 2 — x86 backend: SIGFPE on INT64_MIN / -1 (no hardware-exception guard)

**Root cause:** The x86 `idivq` instruction raises a hardware divide exception (`#DE`) when dividing `INT64_MIN` by `-1` because the mathematical result `+2^63` does not fit in a 64-bit signed register. The interpreter and C backend both handle this by returning the wrapped result `INT64_MIN`. The x86 backend emits a bare `idivq` without an overflow guard, so the process receives SIGFPE (exit 136).

**Minimal repro:** `a28_bug_x86_int64min_div.cardinal`
```
(/ (- 9223372036854775808i64) (- 1i64))
# interp: -9223372036854775808   C: -9223372036854775808   x86: SIGFPE (exit 136)
```

Note: i32 version passes because the 64-bit `idivq` used to implement i32 division produces a result that fits in 32 bits.

---

### BUG 3 — x86 backend: shift u64 by 64 yields 1, not 0

**Root cause:** x86 `shlq`/`shrq` instructions mask the shift count to 6 bits (range 0–63) for 64-bit operands. Shifting by `64u64` gives `64 & 63 = 0` → no shift → result is `1`. The interpreter and C backend correctly yield `0` (all bits shifted out). The x86 backend emits the raw shift instruction without guarding for `shift_count >= type_width`.

**Minimal repro:** `a14_shift_by_zero_and_width.cardinal`
```
(shl 1u64 64u64)   # interp: 0   C: 0   x86: 1
(shr 1u64 64u64)   # interp: 0   C: 0   x86: 1
```

Note: u32 shifted by 32u32 passes — the C backend folds the constant at compile time.

---

## ALL TESTS

| File | Result | Notes |
|------|--------|-------|
| a01_widths_overflow.cardinal | PASS | i8/i16/i32/i64/u8/u16/u32/u64 add/sub wrap |
| a02_mul_overflow.cardinal | PASS | Multiply overflow across all widths |
| a03_div_mod_signed.cardinal | FAIL | BUG 2 (x86 SIGFPE on INT64_MIN / -1) |
| a04_div_mod_unsigned.cardinal | FAIL | BUG 1 (interp float-precision on u64 div/mod) |
| a05_shifts_unsigned.cardinal | PASS | Unsigned shifts including by width-1 and width |
| a06_shifts_signed.cardinal | PASS | Signed arithmetic right-shift on all widths |
| a07_bitwise.cardinal | PASS | band/bor/bxor/bnot across widths and signs |
| a08_casts_narrowing.cardinal | PASS | Narrowing casts with truncation |
| a09_casts_widening.cardinal | PASS | Widening casts (sign-extension, zero-extension) |
| a10_casts_signed_unsigned.cardinal | PASS | Same-width signed/unsigned reinterpret |
| a11_comparisons_boundary.cardinal | PASS | Comparisons at type boundaries |
| a12_unary_minus.cardinal | PASS | Unary minus including INT_MIN of all widths |
| a13_chained_arith.cardinal | PASS | Chained/nested arithmetic |
| a14_shift_by_zero_and_width.cardinal | FAIL | BUG 3 (x86: shl/shr u64 by 64 gives 1 not 0) |
| a15_i8_i16_arith.cardinal | PASS | i8/i16/u8/u16 arithmetic including overflow |
| a16_cast_arith_combo.cardinal | PASS | Cast results then do arithmetic on them |
| a17_bitwise_not_signed.cardinal | PASS | bnot on all signed widths at all boundaries |
| a18_div_one.cardinal | FAIL | BUG 1 (interp: u64_max/1=0, u64_max%1=u64_max) |
| a19_hex_literals.cardinal | PASS | Hex literals and bitwise patterns |
| a20_u64_arithmetic.cardinal | FAIL | BUG 1 (interp: u64_max/2 off by one) |
| a21_i64_arithmetic.cardinal | FAIL | BUG 1 (interp: i64_max/1 wraps to INT64_MIN) |
| a22_cast_width_roundtrip.cardinal | PASS | Roundtrip casts across all width pairs |
| a23_bug_u64_div.cardinal | FAIL | BUG 1 minimal repro: u64_max / 3 |
| a24_bug_mod_neg1.cardinal | PASS | mod by -1 (i32 range, no precision issue) |
| a25_bug_u64_div_mod_1.cardinal | FAIL | BUG 1 minimal repro: u64_max / 1 and % 1 |
| a26_bug_i64max_ops.cardinal | FAIL | BUG 1 minimal repro: i64_max / 1 |
| a27_bug_i64_literal.cardinal | FAIL | BUG 1 minimal repro: i64_max / 1 via literal |
| a28_bug_x86_int64min_div.cardinal | FAIL | BUG 2 minimal repro: INT64_MIN / -1 |
| a29_bug_x86_int32min_div.cardinal | PASS | INT32_MIN / -1 (correctly wraps, no crash) |
| a30_bug_interp_u64_signed_div.cardinal | FAIL | BUG 1: large u64 div loses precision |

**Total: 30 tests, 3 confirmed bugs, 11 FAIL.**
