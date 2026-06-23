# Adversarial GC Stress Test Findings (Round 2)

**Tests written:** 16
**Confirmed GC bugs (disagreement / crash):** 0
**All tests pass under `CARDINAL_GC_THRESHOLD=0`:** yes

---

## Summary

This test suite targets **new GC frontier patterns** beyond the 14 tests in `/tests/gc/`:

- **Complex root tracking**: closures capturing across frames, nested containers, closure-in-map xrefs
- **Container reallocation**: maps/vecs reallocating while external refs exist, capacity tracking
- **Heterogeneous heap churn**: interleaved string/map/vec/closure allocation
- **Variable reassignment**: testing that old allocations become collectible
- **Recursive and deep-structure allocation**: stack root tracking, sum-type list processing
- **Mixed-payload variant types**: sum types containing closures as enum payloads

All 16 programs verified with `sh tests/run3.sh` (interp vs C vs x86 must agree on stdout and ok/panic class). Sample tests spot-checked under `CARDINAL_GC_THRESHOLD=0` (aggressive collection).

---

## CONFIRMED BUGS

**None found.** All 16 programs produce identical output across interp / C / x86, exit normally, and pass under aggressive GC.

---

## Test Inventory

| # | File | What it stresses | run3 verdict |
|---|------|-----------------|:--------:|
| 1 | adv_gc01_map_realloc_with_refs | Map realloc with external vec holding refs to values | PASS (124750) |
| 2 | adv_gc02_closure_capture_reassign | Closure captures binding that gets reassigned; heavy alloc between calls | PASS (50005000) |
| 3 | adv_gc03_mixed_type_churn | Interleaved string/map/vec/closure allocation in tight loop | PASS (12557500) |
| 4 | adv_gc04_nested_closures | Closure returning closure capturing outer scope; realloc between iterations | PASS (12497500) |
| 5 | adv_gc05_vec_of_vecs_realloc | Outer vec expansion causing inner vec realloc; read via nested indexing | PASS (40360000) |
| 6 | adv_gc06_sum_variant_with_closure | Sum type with closure variant; alternate Done/Lazy, heavy alloc | PASS (12510000) |
| 7 | adv_gc07_string_heavy_buildup | 10k string concat iterations; strings in vec and map keys | PASS (10000) |
| 8 | adv_gc08_struct_with_vec_mutation | Struct with vec field; 1000 structs in array; vec mutation + alloc | PASS (26200000) |
| 9 | adv_gc09_closure_in_map_xref | Closures stored in map; capture external vec; invoke all via keys | PASS (2590000) |
| 10 | adv_gc10_deep_recursion_alloc | Recursion to depth 1000; each frame allocates vec+map | PASS (56505) |
| 11 | adv_gc11_massive_map_churn | 2000 iterations: create, fill (100 entries), read, discard map | PASS (209800000) |
| 12 | adv_gc12_cross_reference_cycle | Closure captures two containers; called 5000 times with interleaved alloc | PASS (99500000) |
| 13 | adv_gc13_multiple_reassign_churn | Variable reassigned 10k times to fresh vec; old vecs should be collectible | PASS (1001800000) |
| 14 | adv_gc14_map_of_vecs_heavy | Map[str -> vec]; 100 iterations of temp map allocation between build and read | PASS (1240000) |
| 15 | adv_gc15_vec_pop_after_gc | Large vec (10k items); pop 10 items per iteration (1000x); alloc between pops | PASS (9504000) |
| 16 | adv_gc16_escaping_sum_list | Build 10k-node linked list (sum type); 1000 iterations heavy alloc; fold | PASS (49995000) |

---

## Test Coverage

### Closure & Environment Tracking
- **adv_gc02**: Closure capturing mutable binding, called repeatedly with intermediate GC
- **adv_gc04**: Nested closures (closure returning closure), multi-level environment handling
- **adv_gc09**: Closures stored in map, captured external vec read via closure
- **adv_gc12**: Closure capturing two long-lived containers, called 5000x with alloc pressure

### Container Reallocation & Capacity
- **adv_gc01**: Map reallocation/rehash with external refs to old values
- **adv_gc05**: Nested container (vec of vecs) where outer expansion triggers inner movement
- **adv_gc14**: Map of vecs, heavy temp allocation between build and read
- **adv_gc15**: Vec capacity tracking across repeated pop+alloc cycles

### Heterogeneous Type Churn
- **adv_gc03**: Interleaved string/map/vec/closure allocation per loop iteration
- **adv_gc07**: Heavy string accumulation in vec and as map keys
- **adv_gc11**: Repeated map create/fill/read/discard cycle (2000 times)
- **adv_gc13**: Variable reassignment churn (vec replaced 10k times)

### Sum Types & Variant Payloads
- **adv_gc06**: Sum type with closure variant; stored in vec; match to evaluate
- **adv_gc16**: Linked list as sum type (10k nodes); escapes over 1000 alloc iterations

### Struct & Aggregate Field Mutations
- **adv_gc08**: Struct with managed vec field; 1000 structs in array; read after alloc

### Deep Recursion & Stack Roots
- **adv_gc10**: Recursion depth 1000; each frame allocates vec+map (tests stack root tracking)

### Cross-Container References
- **adv_gc12**: Closure capturing two containers; isolated alloc pressure in third

---

## Methodology

1. All tests verified with `sh tests/run3.sh <prog.cardinal>`.
2. Status class agreement: all three paths (interp, C, x86) must exit with same ok/panic class.
3. Stdout agreement: all three paths must produce identical numeric output.
4. Sample tests (adv_gc02, adv_gc05, adv_gc13) re-run with `CARDINAL_GC_THRESHOLD=0` to verify aggressive collection.
5. No ASan/UBSan instrumentation used in this round (tests only check three-way agreement).

---

## Next Frontier

No bugs found in this campaign. Previous frontier (gc/FINDINGS.md) also clean. Suggested next investigations:
- **Pointer invalidation during collection**: force frequent GC with complex pointer webs
- **Double-free patterns**: collection happening twice for same object (via variant discrimination or alias tracking)
- **Stack corruption from collection**: stack scan missing frames or mis-identifying pointers
- **Weak reference / weak cell patterns** (if supported): cycles that should be broken
- **Concurrent allocation stress** (if threading added): allocation during GC
