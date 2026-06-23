# opus_codegen — adversarial x86_64 codegen findings

Mission: break the Cardinal **x86_64 backend** (`compiler/backend_x86.cardinal`) by
stressing code generation (register/scratch reuse, deep/wide expressions, feature
interactions). Oracle = Python interpreter; references = interp + C backend; target
= x86 backend. A BUG = `sh tests/run3.sh <p>` FAIL where x86 disagrees with the
interp oracle.

25 tests across 9+ distinct register-pressure / clobber / ABI strategies.

## Backend model (why most "register pressure" attacks bounce)

The x86 backend is a **stack-slot machine**: every IR temp/local/param gets its own
`-N(%rbp)` frame slot; each instruction independently loads operands into
%rax/%rcx/%rdx, computes, stores back. There is **no register allocator holding
values across instructions**, so classic "two live temps collide in a register"
bugs cannot occur. Call arguments are **parked into a shared 112-byte (14-word)
scratch frame region** (memory) before any `%rsp` change or register load, so an
inner call's caller-saved clobbers cannot corrupt an already-parked sibling argument
(it lives in memory). This makes nested calls in arg lists, calls inside
index/field exprs, closures in arg lists, and string-literal args via cl_strlit_n
all safe.

The fertile area was the **float<->int conversion paths**, which are asymmetric:
int->float has a u64 special-case, float->int does not.

---

## CONFIRMED BUGS

### BUG 1 (x86 backend) — float -> u64 cast wrong for values >= 2^63

Files: 16b_f64_to_u64_bug.cardinal (minimal), 24_f2u64_boundary.cardinal (boundary);
also surfaced by 16_float_int_cast_extremes.cardinal and 17_more_float_casts.cardinal
(f32 variant).

Minimal repro:
    func main () -> i32
        let huge f64 = 1.5e19
        do (io::println (to_str (as huge u64)))   # > 2^63, within u64 range
        return 0i32
    end

Diverged output (as 1.5e19 u64):
  interp: 15000000000000000000   (correct)
  C:      15000000000000000000   (correct)
  x86:    9223372036854775808    (WRONG = 0x8000000000000000)

Boundary (24_f2u64_boundary): values < 2^63 (~9.22e18) correct on all three;
values >= 2^63 give 0x8000000000000000 on x86 only. Holds for both f64->u64 and
f32->u64 (17: as (1.0e19 f32) u64 -> x86 9223372036854775808 vs correct
9999999980506447872).

Root cause / offending sequence: emit_instr, ICast `fromf` branch
(backend_x86.cardinal ~line 2235-2241):
    cvtt<sd|ss>2siq %xmm0, %rax      # always SIGNED; target u64 ignored
`cvttsd2siq`/`cvttss2siq` are signed 64-bit truncations. A double >= 2^63 doesn't
fit in i64, so the CPU returns the integer-indefinite value 0x8000000000000000. The
target type u64 is never consulted. Narrower unsigned targets (u8/u16/u32) are fine
because their in-range values are < 2^63 (signed convert + zero-extend gives correct
bits); only u64 is broken. This is the exact mirror of the int->float u64
special-case the backend already has (ICast `tof`, ~line 2215). Fix: when toty is
u64 and the double >= 2^63, subtract 2^63, cvttsd2siq, OR back 0x8000000000000000.

### BUG 2 (C backend, NOT x86) — shift by a runtime count >= width

File: 15_shift_div_complex_count.cardinal. Recorded because run3.sh reports FAIL,
but the **x86 backend is CORRECT** (matches the interp oracle); divergence is
interp/x86 vs C. Noted so it is not mistaken for an x86 regression.

Minimal repro:
    func main () -> i32
        let counts = [64i64 65i64 100i64]
        let v = (as 9223372036854775807i64 u64)
        let i = 0i64
        while (< i 3i64)
            let c = (as counts[i] u64)
            do (io::println (to_str (shr v c)))
            set i = (+ i 1i64)
        end
        return 0i32
    end

shr (u64 max) c for c = 64,65,100:
  interp: 0, 0, 0                            (correct — shift >= width = 0)
  x86:    0, 0, 0                            (correct — emit_bin emits cmpq $bits,%rcx; jae guard)
  C:      9223372036854775807, 4611686018427387903, 134217727   (WRONG — raw shift
                                              masks count to 6 bits: 64->0, 65->1, 100->36)
Only with a runtime (non-folded) count >= width; a literal count is folded and all
agree. The C backend is missing the width guard the x86 backend has.

---

## HARDENED CASES (PASS — x86 matched interp + C)

01_nested_call_args         nested calls (int+float) as call args                 PASS
02_array_idx_call           arr[f(x)], set arr[g()]=h(), arr[arr[arr[0]]]         PASS
03_deep_mixed_expr          deep int/float tree, cmp results into arithmetic      PASS
04_many_args_spill          >6 int + >8 float args (GP + xmm stack spill)         PASS
05_setfield_call_arrelem    set arr[i].field=call, sub-word fields (u8/i16)       PASS
06_struct16_reg_boundary    16B struct arg straddling 6th GP reg boundary         PASS
07_float_struct_abi         structs of floats ({f64,f64},{f64,i64}) by value     PASS
08_bigstruct_byval          >16B struct (MEMORY) passed + returned by value       PASS
09_gc_nested_agg            vec-of-struct-holding-vec/str, GC mid-expression      PASS
10_vec_struct_field_set     set vec[i].field=call (cl_vec_at), str/float fields   PASS
11_checked_overflow         checked arithmetic in range, narrow widths (%rdx)     PASS
12_checked_panic            checked overflow -> all three panic identically       PASS
13_odd_size_elems           container elems sized 3/5/6/7/12 (byte-accurate copy) PASS
14_closure_in_args          closure calls (int+float) as args to another call    PASS
15_shift_div_complex_count  computed shift counts/divisors                        FAIL (C bug; x86 correct)
16_float_int_cast_extremes  float->int casts incl f64->u64                        FAIL (BUG 1)
16b_f64_to_u64_bug          minimal f64->u64 repro                                FAIL (BUG 1)
17_more_float_casts         f32->u64 (BUG 1), u32, negative->signed               FAIL (BUG 1 f32)
18_float_misc               float cmp in control flow, f32 arith, accumulator     PASS
19_float_divzero            float division by computed nonzero                    PASS
20_float_divzero_panic      float division by ordered zero -> all panic           PASS
21_sum_float_payload        sum type with float/struct/nullary payloads in match  PASS
22_map_float_struct_vals    maps with float values, struct values, int keys       PASS
23_calls_returning_aggs..   call args that are calls returning struct/float/str   PASS
24_f2u64_boundary           f64->u64 boundary characterization                    FAIL (BUG 1)

## Test-authoring notes (not bugs)
- Float literals need explicit type context: `let x f64 = 1.5`. A bare
  `let x = 1.5` / `(as 3.9 i64)` are rejected by the lowerer ("uninferable float
  literal"), though the interpreter accepts the latter (minor front-end asymmetry,
  not codegen).
- bool is not castable to int; use an if/helper.
- Sum match payloads use `case (Variant a b)`, not `case Variant(a b)`.
- Shift count type must match the operand type.
