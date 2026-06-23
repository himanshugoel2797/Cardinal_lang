# FINDINGS — Aggregates Test Suite

## CONFIRMED BUGS

### BUG-1: x86 backend crashes on arrays of structs whose byte size is not a multiple of 8

**Status:** CONFIRMED — interp=PASS, C=PASS, x86=COMPILER PANIC
**Panic message:** `panic: x86: multi-word container element size not a multiple of 8 (later)`
**Affects:** x86 backend only. The interpreter and C backend handle these correctly.

**Root cause area:** The x86 element-indexing code path explicitly bails out whenever a
struct element occupies more than one 8-byte word AND its total size is not divisible by 8.
This is an incomplete implementation stub — the "later" in the panic message confirms it was
a known placeholder.

**Structs that trigger it:** Any struct whose compiled size satisfies `size > 8 && size % 8 != 0`.
Concrete examples:
- `struct {i32, i32, i32}` — 12 bytes

**Structs that are NOT affected:**
- 4-byte structs (single i32): array indexing works (single word)
- 8-byte structs (one i64): works (one word)
- 16-byte structs (two i64, or i32+pad+i64): works (two complete words)
- 24-byte structs (three i64): works (three complete words)

**Trigger conditions (all trigger the compile-time panic):**
1. `arr[i].field` read — triggers the panic (ag29)
2. `set arr[i].field = v` — triggers the panic (ag27)
3. `set arr[i] = struct_val` — triggers the panic (ag26)
4. `for s in arr` — triggers the panic (ag29)

**Minimal repro (ag26_array_struct_nonmul8.cardinal):**

```
module ag26_array_struct_nonmul8
import io
struct S12
    a i32
    b i32
    c i32
end
func main () -> i32
    let arr = [(S12 a: 1i32 b: 2i32 c: 3i32) (S12 a: 4i32 b: 5i32 c: 6i32)]
    do (io::println arr[0].a)
    set arr[0] = (S12 a: 10i32 b: 20i32 c: 30i32)
    do (io::println arr[0].a)
    return 0i32
end
```

Run: `sh tests/run3.sh tests/aggregates/ag26_array_struct_nonmul8.cardinal`

Exact divergence:
- interp (exit 0): `1\n6\n10\n20\n30\n4`
- C      (exit 0): `1\n6\n10\n20\n30\n4`
- x86    (exit 1, compiler panic): (empty stdout)

---

## TEST RESULTS

| # | File | Description | Result |
|---|------|-------------|--------|
| 01 | ag01_struct_copy_semantics.cardinal | Struct copy/mutate, pass-by-value unchanged | PASS |
| 02 | ag02_struct_8byte.cardinal | 8-byte struct (one i64): make, pass, return | PASS |
| 03 | ag03_struct_16byte.cardinal | 16-byte struct (two i64): ABI boundary | PASS |
| 04 | ag04_struct_24byte.cardinal | 24-byte struct (three i64): MEMORY-class | PASS |
| 05 | ag05_struct_mixed_i8_i64.cardinal | Mixed {i8, i64}: alignment/padding, 16 bytes | PASS |
| 06 | ag06_struct_three_i32.cardinal | {i32, i32, i32} = 12B: construct, pass, return (no array) | PASS |
| 07 | ag07_nested_struct.cardinal | Nested structs, deep field set, copy independence | PASS |
| 08 | ag08_array_basics.cardinal | Array literal, index read/write, for-in sum | PASS |
| 09 | ag09_array_oob_high.cardinal | OOB: index == len panics in all three | PASS (status=1) |
| 10 | ag10_array_oob_neg.cardinal | OOB: negative index via u64 cast panics | PASS (status=1) |
| 11 | ag11_array_of_structs.cardinal | Array of {i32,i32} structs: index, field set, for-in | PASS |
| 12 | ag12_struct_with_array_field.cardinal | Struct containing an array field, 2D access | PASS |
| 13 | ag13_large_array_sum.cardinal | 1000-element array fill + sum via for-in | PASS |
| 14 | ag14_struct_return_field_inline.cardinal | Return struct, access field inline, chain | PASS |
| 15 | ag15_float_struct_abi.cardinal | {f64, i64} = 16B mixed float+int ABI | PASS |
| 16 | ag16_float_struct_24byte.cardinal | {f64, f64, i64} = 24B MEMORY-class float struct | PASS |
| 17 | ag17_nested_struct_large.cardinal | Vec3 (24B), dot product, add, copy independence | PASS |
| 18 | ag18_array_nested.cardinal | Array of arrays (2D), set arr[i][j], for-in | PASS |
| 19 | ag19_struct_with_str_field.cardinal | Struct with str (GC-managed) field, copy | PASS |
| 20 | ag20_struct_tostr.cardinal | to_str of structs, field order matches declaration | PASS |
| 21 | ag21_boxed_struct_large.cardinal | 32-byte struct (4 i64): multi-word boxed load/store | PASS |
| 22 | ag22_struct_in_array_mutation.cardinal | Array of 12-byte struct, set arr[i] and field | **FAIL** (BUG-1) |
| 23 | ag23_array_pass_return.cardinal | Array passed to fn and returned | PASS |
| 24 | ag24_struct_f32_f32_i64.cardinal | {f32, f32, i64} = 16B mixed SSE+GP ABI | PASS |
| 25 | ag25_nested_struct_copy_independence.cardinal | Deep nested copy: all levels independent | PASS |
| 26 | ag26_array_struct_nonmul8.cardinal | Minimal repro BUG-1: set arr[i] on 12B struct | **FAIL** (BUG-1) |
| 27 | ag27_array_struct_nonmul8_fieldset.cardinal | BUG-1 variant: set arr[i].field on 12B struct | **FAIL** (BUG-1) |
| 28 | ag28_array_struct_4byte.cardinal | 4-byte struct in array: single-word, no bug | PASS |
| 29 | ag29_array_struct_12byte_read_only.cardinal | 12B struct array: even read-only triggers BUG-1 | **FAIL** (BUG-1) |
| 30 | ag30_array_i32_i64_struct.cardinal | {i32, i64} = 16B (aligned): in array, works | PASS |

**Total: 30 tests. 1 confirmed bug. 4 test instances trigger BUG-1.**
