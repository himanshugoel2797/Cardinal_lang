# GC Stress Test Findings

**Tests written:** 14
**Confirmed GC bugs (disagreement / sanitizer / crash):** 0
**All sanitizer clean under `CARDINAL_GC_THRESHOLD=0`:** yes

---

## CONFIRMED BUGS

*None found.* All 14 programs produce identical output across interp / C / x86, exit normally, and emit zero ASan/UBSan reports when compiled with `-fsanitize=address,undefined` and run under `CARDINAL_GC_THRESHOLD=0`.

---

## Test inventory

| File | What it stresses | run3 verdict | sanitizer (C+ASan, threshold=0) | x86 threshold=0 |
|------|-----------------|:------------:|:-------------------------------:|:---------------:|
| gc01_closure_churn | 200k freshly-allocated closures churned, previous = garbage | PASS (200000) | CLEAN | CLEAN |
| gc02_shared_cell | Two closures sharing one captured cell; inner inc loop 50k iters then cell read via escaped getter | PASS (50000) | CLEAN | CLEAN |
| gc03_vec_of_closures | 1000 closures in a vec, heavy alloc pressure before readback | PASS (499500) | CLEAN | CLEAN |
| gc04_string_concat_loop | 10k-iteration string concat, each step old string becomes garbage | PASS (10000) | CLEAN | CLEAN |
| gc05_vec_churn | 100k iterations: fresh vec allocated, read, discarded | PASS (4200000) | CLEAN | CLEAN |
| gc06_map_rebuild | 100k iterations: entire map discarded and reallocated each step | PASS (99999) | CLEAN | CLEAN |
| gc07_deep_sum_fold | Build 100k-node Cons chain, fold to i64 sum; all nodes live during build | PASS (4999950000) | CLEAN | CLEAN |
| gc08_nested_struct_churn | 100k struct-with-str-field allocations; last struct value read | PASS (99999) | CLEAN | CLEAN |
| gc09_escaping_closure_vec | 500 escaping closures in a vec, burn_heap before readback | PASS (124750) | CLEAN | CLEAN |
| gc10_map_of_closures | map[str->closure] of 200 entries, 20k-string-vec pressure between build and invoke | PASS (19900) | CLEAN | CLEAN |
| gc11_sum_churn_loop | 100k builds and immediate folds of a 3-node sum tree | PASS (100000) | CLEAN | CLEAN |
| gc12_return_fresh_alloc | Function returns fresh vec; caller allocates another before using it (tests return-value rooting) | PASS (-1794967296 i32 wrap) | CLEAN | CLEAN |
| gc13_vec_store_read_after_gc | 200 inner vecs in outer vec; 50k-alloc pressure; inner vecs read after | PASS (19900) | CLEAN | CLEAN |
| gc14_closure_mutation_checksum | Escaping accumulator closure called 30k times with periodic 200-vec burn every 100 iterations | PASS (30000) | CLEAN | CLEAN |

---

## Methodology

1. Each program verified with `sh tests/run3.sh` (interp vs C vs x86 must agree on stdout and ok/panic class).
2. C backend compiled with ASan+UBSan and run under `CARDINAL_GC_THRESHOLD=0` (collect on every allocation).
3. x86 backend assembled without sanitizers (hand asm) and run under `CARDINAL_GC_THRESHOLD=0`; output compared to oracle.
4. Sanitizer stderr searched for `ERROR`, `runtime error`, `SUMMARY`, `SEGV`, `heap-use`. None found in any run.

## Coverage areas

- Closure churn (gc01), shared cells (gc02), vec-of-closures (gc03), map-of-closures (gc10)
- String concat accumulation (gc04), vec alloc-and-drop (gc05), map rebuild (gc06)
- Deep sum-type Cons chain 100k nodes (gc07), sum-type churn (gc11)
- Struct with managed str field (gc08)
- Escaping closures into container with alloc pressure between store and read (gc09)
- Return-value rooting across function boundary (gc12)
- Nested vec (vec-of-vecs) surviving alloc wave (gc13)
- Mutable captured cell under periodic GC bursts (gc14)
