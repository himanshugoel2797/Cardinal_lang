# Control-flow / Functions / Closures — Test Findings

Harness: sh tests/run3.sh tests/control/<file>.cardinal
Three paths: Python interpreter (ORACLE), C backend, x86_64 backend.
FAIL = DISAGREEMENT. PASS class=1 = all three agreed on panic.

---

## CONFIRMED BUGS

### BUG 1 — Untyped literal in closure inside for-loop: interpreter rejects, backends accept

File: c16b_closure_loop_int_literal.cardinal

Divergence:
  Interpreter: "error: cannot infer type of 'start'" — exit 1, no output
  C backend:   0 / 10 / 20 — exit 0
  x86 backend: 0 / 10 / 20 — exit 0

Minimal repro:
  for i = 0 to 3
      let start = (* i 10)   # i is i32; 10 is untyped; interpreter rejects
      let f = func () -> i32
          return start
      end
      do (push fns f)
  end

Root cause: bootstrap/typecheck.py does not propagate the i32 type of the
for-loop variable through a binary-op to infer an untyped literal, but
compiler/checker.cardinal does. Violates the difftest contract
(AGREE=13 is maintained for existing tests but this new code exposes the gap).

---

### BUG 2 — for-loop bound re-evaluation: interpreter snapshots once; C/x86 re-read each iteration

File: c18b_for_bound_mutation.cardinal

Divergence:
  Interpreter: 5  (evaluates upper-bound expression once at entry)
  C backend:   100 (re-reads the mutable slot each iteration)
  x86 backend: 100

Minimal repro:
  let limit = 5i32
  let count = 0i32
  for i = 0 to limit
      set count = (+ count 1)
      set limit = 100i32
  end
  do (io::println count)   # interp: 5, C/x86: 100

Root cause: The lowerer translates "for i = 0 to limit" into a
while (i < limit) loop that re-reads `limit` from its mutable slot each
iteration. The interpreter evaluates the bound expression exactly once.
DESIGN.md is silent; a ruling is needed. Fix: the lowerer should snapshot
the upper-bound expression into a temp before the loop header.

---

### BUG 3 — Deep recursion: Python interpreter crashes; C/x86 succeed

File: c20b_deep_recursion_overflow.cardinal

Divergence:
  Interpreter: RecursionError (Python stack exhausted) — exit 1, no output
  C backend:   125250 — exit 0
  x86 backend: 125250 — exit 0

Minimal repro: (sum_to 500) via simple linear recursion

Root cause: Python's default recursion limit (~1000) is exhausted at ~500
deep Cardinal recursion due to interpreter overhead per frame. The compiled
backends use the OS stack and succeed to much greater depth.
Fix: add sys.setrecursionlimit(50000) at bootstrap/cardinal.py startup.

---

## PASSING TESTS

c01_mutual_recursion.cardinal          even/odd mutual recursion                       PASS
c02_accumulator_recursion.cardinal     accumulator recursion, factorial                PASS
c03_tree_recursion_fib.cardinal        tree-recursive Fibonacci 0..14                  PASS
c04_for_loop_step.cardinal             for-loop step, empty ranges (lo==hi, lo>hi)     PASS
c05_nested_loops_break.cardinal        nested loops: break exits only inner; continue  PASS
c06_while_complex.cardinal             while with compound condition; continue          PASS
c07_if_chain.cardinal                  if/elsif/else chain; deeply nested if            PASS
c08_short_circuit.cardinal             and/or short-circuit with counter closures       PASS
c09_higher_order.cardinal              func as arg (typed), returned, stored; apply_n  PASS
c10_counter_closure.cardinal           two closures sharing one captured cell           PASS
c11_nested_closure.cardinal            nested closures (closure -> closure, 3 levels)   PASS
c12_closure_loop_capture.cardinal      for-loop var capture: fresh box per iteration   PASS
c13_closure_param_capture.cardinal     closure capturing function params               PASS
c14_closure_multi_type_capture.cardinal closure capturing int+str+bool+struct          PASS
c15_early_return.cardinal              early return, return from nested loop, unit ret  PASS
c16_vec_of_closures.cardinal           vec of closures, apply ops in sequence           PASS
c17_recursion_via_closure.cardinal     apply_n via HOF; iterative fib in closure       PASS
c18_for_modifying_bound.cardinal       for step edge cases (overshoot, exact, large)   PASS
c19_condition_side_effects.cardinal    side effects in loop condition                  PASS
c20_deep_recursion.cardinal            moderate-depth recursion (50-100 levels)        PASS
c21_return_from_loop.cardinal          return from 3-level nested for; return from while PASS
c22_closure_shared_mutation.cardinal   two closures (add/mul) sharing one cell         PASS
c23_while_continue.cardinal            continue in while; continue in loop+break       PASS
c24_for_in_closure_fresh_box.cardinal  KEY: 8-closure vec, mutating captured loop var  PASS
c25_falloff_trap.cardinal              value-returning fn falls off end: all 3 panic   PASS (class=1)
c26_closure_escape.cardinal            closures escaping frame; independent captures    PASS
c27_short_circuit_counter.cardinal     and/or return values; chained; not              PASS
c28_for_in_step_exact.cardinal         step lands exactly on bound; step > range       PASS
c29_hof_stored_in_struct.cardinal      function composition (HOF returning HOF)        PASS
c30_for_in_array.cardinal              for-in over array/vec; closure capture of for-in var PASS

## BUG REPRO FILES (expected FAIL)

c16b_closure_loop_int_literal.cardinal  BUG 1: type inference divergence (interp vs C/x86)
c18b_for_bound_mutation.cardinal        BUG 2: for-loop bound re-evaluation semantics
c20b_deep_recursion_overflow.cardinal   BUG 3: Python recursion limit (structural)

---

## Summary

Total tests: 31 (28 passing + 3 confirmed-bug repros)
PASS: 28
CONFIRMED BUGS: 3
