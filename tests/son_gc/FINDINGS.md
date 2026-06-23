# GC / Rooting Adversarial Campaign — FINDINGS

Target: Cardinal Lisp garbage collector and shadow-stack rooting discipline.
Backends: Python interpreter (oracle), C backend, x86_64 backend.

## Method
Each test run two ways:
1. Three-way differential `sh tests/run3.sh <prog>` (interp vs C vs x86; stdout +
   ok/panic class). A rooting bug surfaces as a freed-object read -> wrong output
   or a `use-after-free: stale handle` panic in one backend only -> FAIL.
2. Aggressive native GC: `sh compiler/ccrun.sh <prog> --no-run` then
   `CARDINAL_GC_THRESHOLD=0 build/<prog>` (full mark/sweep on EVERY allocation),
   compared against the default-threshold native run. `CARDINAL_GC_STATS=1`
   confirmed collections fire (e.g. t12 = 3002 collections while a 3000-node list
   stays fully live).

## Result summary
- Tests written: 17
- Confirmed breaks: 0
- All 17 PASS the three-way diff AND give identical output under
  CARDINAL_GC_THRESHOLD=0 vs default threshold.

## Why the attacks fail (rooting model)
Inspected backend_c.cardinal (func_def ~L1122), backend_x86.cardinal (prologue
~L2884), and runtime cardinal_gc.c / cardinal_rt.c:
- lower.cardinal assigns EVERY subexpression to a fresh_temp; every fresh temp is
  registered in f.temps.
- C backend: each managed temp/local/param is a zero-init C var with
  cl_gc_push_root(&t, sizeof(t)) at entry; cl_gc_pop_roots(n) right before each
  return (after the return value is read; no alloc between).
- x86 backend: roots every managed slot once in prologue with type_size so the
  conservative scan_range finds handles embedded in multi-word value structs.
- So intermediate allocs in (f (alloc)(alloc)(alloc)), partially-built
  aggregates, captured cells, and value structs with embedded handles are all
  live on the shadow stack across any GC-triggering allocation.
- Runtime helpers (cl_vec_push/vec_grow_if_needed, cl_map_set/map_rehash,
  cl_map_keys, cl_strings__concat, cl_sys__args) keep at most one un-rooted alloc
  in flight, storing it into an already-reachable header before the next alloc,
  or push_root explicitly. The _e/_k/_v copy locals are redundant copies of
  already-rooted source temps.

## Tests (all PASS)
t01 temps_across_allocs    - 3 live heap temps while later args allocate
t02 nested_aggregate       - vec of vecs of strings built inline, GC mid-build
t03 map_of_vec_of_struct   - object reachable only via map->vec->struct->str
t04 reassign_sole_ref      - reassign sole reference then allocate heavily
t05 closure_captures_sole_root - captured cell is only root; churn then invoke
t06 linked_tree            - sum-type tree bottom-up, subtrees live as temps
t07 interior_handle_value_struct - value struct embedding str+vec, by value across allocs
t08 vec_resize_elem_in_flight - push fresh string forcing vec grow on same push
t09 map_str_value_resize   - map<int,str> fresh string values across rehashes
t10 arr_lit_managed_elems  - array literal of fresh strings across element allocs
t11 deep_nested_call_args  - 6-deep nested calls, every arg fresh, all live
t12 sumlist_accumulate     - 3000-node cons list, sole root, GC forced each step
t13 map_get_across_alloc   - managed value read into temp, churn, then used
t14 struct_field_chain     - struct->vec->struct->str, in-place set + churn
t15 vec_of_closures        - 1000 closures in vec, env reachable only via .env; churn then invoke
t16 map_in_struct_churn    - value struct embedding map<str,str>, rehash + churn
t17 mutual_rec_alloc       - mutual recursion passing fresh strings through deep stack

## Confirmed bugs
None. The GC rooting discipline (precise shadow stack + conservative intra-object
scan + pinned globals) is correct for every program tested, including under
CARDINAL_GC_THRESHOLD=0 (full collection on every allocation).
