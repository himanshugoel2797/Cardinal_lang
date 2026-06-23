# Float Test Findings

## CONFIRMED BUGS

### BUG 1 — x86: float arguments to indirect closure calls passed in GP registers instead of xmm

**File:** `tests/floats/f13_closure_capture.cardinal`
**Status:** FAIL — interp and C agree, x86 diverges

**Minimal repro:**
```cardinal
module repro_bug1
import io
func make_adder (offset f64) -> func(f64 -> f64)
    let fn = func (x f64) -> f64
        return (+ x offset)
    end
    return fn
end
func main () -> i32
    let add5 = (make_adder 5.0)
    do (io::println (add5 1.0))   # expect 6, x86 outputs 5
    return 0i32
end
```

**Exact divergence:**
```
interp: 6
C:      6
x86:    5
```

**Root cause:** In `compiler/backend_x86.cardinal`, the `ICallClosure` case (around line 2485-2512) parks all call arguments using `emit_park_arg` into scratch slots and then loads them back using:
```
movq (scratch+w*8), argreg(w)      # argreg = %rdi, %rsi, %rdx, ...
```
This is correct for GP/integer args but wrong for float args. The SysV AMD64 ABI requires float arguments to be passed in xmm registers (%xmm0, %xmm1, ...), not GP registers. The closure receives its env pointer in %rdi (word 0 of scratch), and actual float args should be loaded into %xmm0, %xmm1, etc. — but the ICallClosure code passes them through %rsi, %rdx, etc. (GP registers). The closure body then reads from xmm registers (where nothing was placed), so it gets garbage — in this case zero, which happens to equal the original value from the xmm0 already holding the env-pointer from parameter passing.

By contrast, the regular ICall path (around line 1272) has a separate xmm counter and uses movsd/movss to load float args into %xmm0, %xmm1, etc. ICallClosure lacks this logic entirely.

Note: The second part of f13 (closure with f32 accumulator, no float function parameters) passes correctly because it has no float function arguments, only a captured/modified float cell.

---

### BUG 2 — C backend: float division by zero incorrectly panics; x86 returns IEEE 754 inf/nan

**File:** `tests/floats/f14_divzero_inf_nan.cardinal`
**Status:** FAIL — three-way divergence on both stdout and status class

**Minimal repro:**
```cardinal
module repro_bug2
import io
func p64 (x f64) -> f64
    return x
end
func main () -> i32
    do (io::println (/ (p64 1.0) (p64 0.0)))   # expect inf
    return 0i32
end
```

**Exact divergence:**
```
interp (exit 101): [no stdout]  stderr: panic: float division by zero
C      (exit 101): [no stdout]  stderr: panic: integer division by zero
x86    (exit   0): inf
                   -inf
                   -nan
                   inf
                   -nan
```

**Root cause:** In `compiler/backend_c.cardinal`, `emit_bin` (around line 552) handles the `/` and `%` operators with an unconditional zero-check guard regardless of the operand type `ty`:
```
if (("b") == 0) cl_panic_cstr("integer division by zero");
```
There is no check for `irt_is_float ty` before emitting this guard. In C, the expression `(double)0.0 == 0` evaluates to true, so the zero check triggers for float division by zero and calls `cl_panic_cstr("integer division by zero")` — wrong message for a float operation, and wrong behavior since IEEE 754 mandates that float /0 produces infinity/NaN.

The x86 backend correctly avoids this: it checks `irt_is_float ty` first and takes the SSE path (divsd/divss) with no zero check, producing IEEE 754 results.

The interpreter also panics on float /0 (bootstrap/interpreter.py line 1789: `raise Panic("float division by zero")`), but that's a separate interp-only issue.

**Fix needed (C backend only):** Wrap the zero-check block in `emit_bin` with `if (not (irt_is_float ty))` so float division skips the guard and produces IEEE 754 inf/NaN, matching x86.

---

## TEST RESULTS

| # | File | Status | Notes |
|---|------|--------|-------|
| 1 | f01_basic_arith_f64.cardinal | PASS | Basic f64 add/sub/mul/div; 0.1+0.2; large exponents via helper |
| 2 | f02_basic_arith_f32.cardinal | PASS | Basic f32 arithmetic; 1/3 rounding; 0.1+0.2 in f32 |
| 3 | f03_literal_formatting.cardinal | PASS | %g formatting: integer-valued floats, negatives, fractions |
| 4 | f04_large_small_magnitudes.cardinal | PASS | 1e15..1e100, 1e-5..1e-100; %g switch point around 1e6 |
| 5 | f05_float_to_int_cast.cardinal | PASS | f64->i32/i64 truncation toward zero, positive and negative |
| 6 | f06_int_to_float_cast.cardinal | PASS | i32/u32/i64->f64, i32->f32 (precision loss at 16777217) |
| 7 | f07_f32_f64_cast.cardinal | PASS | f32<->f64 round-trips; 0.1f32 widened to f64 reveals rounding |
| 8 | f08_comparisons.cardinal | PASS | ==, !=, <, >, <=, >= on f64; 0.1+0.2!=0.3 confirmed; f32 < |
| 9 | f09_negate.cardinal | PASS | Unary minus on f64/f32; double-negate; sign mask on x86 |
| 10 | f10_struct_field.cardinal | PASS | f64/f32 in struct fields; field arithmetic; to_str of struct |
| 11 | f11_array_vec.cardinal | PASS | Array of f64, vec of f32; indexing; for-in iteration |
| 12 | f12_func_arg_return.cardinal | PASS | f64/f32 as function args and return values; nested calls |
| 13 | f13_closure_capture.cardinal | FAIL | BUG 1: x86 passes float closure args in GP regs not xmm |
| 14 | f14_divzero_inf_nan.cardinal | FAIL | BUG 2: C backend panics on float /0; x86 gives inf/nan |
| 15 | f15_negative_zero.cardinal | PASS | -0.0 prints same as 0.0; -0.0 == 0.0; -0.0 not < 0.0 |
| 16 | f16_sum_type_float.cardinal | PASS | f64 in sum type payloads; match; area computation; to_str |
| 17 | f17_precision_f32_vs_f64.cardinal | PASS | f32 vs f64 precision differences; cumulative rounding |
| 18 | f18_map_float_values.cardinal | PASS | map[str]f64 and map[str]f32; store, load, arithmetic |
| 19 | f19_cast_boundary.cardinal | PASS | i32max/min to f32/f64; i64 large -> f64 precision loss |
| 20 | f20_comparison_edge.cardinal | PASS | Compute-same-value equality; round-trip cast; f32~f64 exact |
| 21 | f21_to_str_float.cardinal | PASS | to_str on f64/f32 scalars; to_str on struct with float field |
| 22 | f22_while_float.cardinal | PASS | Float as loop counter and accumulator; convergence loop |
| 23 | f23_mixed_cast_chain.cardinal | PASS | i32->f32->f64->i64->f64 chain; u8->f64; i8->f32; u32->f32 |

**Total: 23 tests, 21 PASS, 2 FAIL (2 confirmed bugs)**
