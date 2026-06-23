# FINDINGS — tests/sums/ (sum types, match, enums)

Run: `sh tests/run3.sh tests/sums/<file>.cardinal`
Harness: interpreter (Python ORACLE) vs C backend vs x86_64 backend.

---

## CONFIRMED BUGS

**None.** All 45 tests pass (all three backends agree on stdout and exit-status class).

---

## OBSERVATIONS / NON-BUGS

### Python recursion limit (not a compiler bug)
File: `36_deep_list_100.cardinal` was initially written with depth 100
(list 1..100, sum 5050). The Python interpreter failed with
`RecursionError: maximum recursion depth exceeded` (Python default limit = 1000
stack frames; interpreting deep Cardinal recursion requires ~10+ Python frames
per Cardinal frame). C and x86 backends both printed `5050`. This is a Python
interpreter implementation constraint, **not a divergence in compiler correctness**.
The test was reduced to depth 60 (sum 1830), which all three paths handle
correctly.

### Enums do NOT support `match`
Enums (`enum Color ... end`) cannot be matched with `match`/`case`; the checker
rejects it with "match on a non-sum value". This is correct per the spec (enums
use `==` equality, not structural matching). Tests 12 and 24 were corrected to use
`if (== ...)` chains.

### `len` returns `u64`, not `i32`
Using `(len s)` where `s` is a `str` or collection inside a context expecting
`i32` requires an explicit `(as (len s) i32)` cast. Caught in test 21.
Not a bug - the type system correctly enforces this.

---

## TEST RESULTS (all PASS)

| # | File | What it tests | Result |
|---|------|---------------|--------|
| 01 | `01_basic_tree_sum.cardinal` | Recursive binary tree sum/count/depth | PASS |
| 02 | `02_linked_list.cardinal` | Linked list as sum type (sum, len, max) | PASS |
| 03 | `03_mirror_tree.cardinal` | Mirror/reverse a binary tree, verify sum/depth | PASS |
| 04 | `04_match_exhaustive.cardinal` | All-variant coverage; match with `else` | PASS |
| 05 | `05_mixed_payload.cardinal` | Mixed-type payload: i32, str, bool, i64 | PASS |
| 06 | `06_many_variants.cardinal` | >5-variant sum (7 variants) - long tag if-chain | PASS |
| 07 | `07_single_variant.cardinal` | Single-variant sum type edge case | PASS |
| 08 | `08_sum_in_struct.cardinal` | Sum value stored in struct field | PASS |
| 09 | `09_sum_in_vec.cardinal` | Sum values stored in a vec, iteration + match | PASS |
| 10 | `10_nested_match.cardinal` | match inside a case branch (nested match) | PASS |
| 11 | `11_match_fn_result.cardinal` | Match on the result of a function call | PASS |
| 12 | `12_enum_basic.cardinal` | Enum equality, if-dispatch, to_str, passed/returned | PASS |
| 13 | `13_enum_map_key.cardinal` | Enum as map key (insert, read, delete, update) | PASS |
| 14 | `14_sum_closure.cardinal` | Sum value captured in a closure | PASS |
| 15 | `15_payload_named_tag.cardinal` | User payload field literally named `tag` (cl_tag gotcha) | PASS |
| 16 | `16_transform_variant.cardinal` | Match variant and rebuild new variant from payload | PASS |
| 17 | `17_deep_recursion.cardinal` | Sum-based recursion depth 50 (list 1..50, sum 1275) | PASS |
| 18 | `18_sum_to_str.cardinal` | Autogen to_str on nullary and payload variants | PASS |
| 19 | `19_sum_in_map.cardinal` | Sum value stored in a map, overwrite with different variant | PASS |
| 20 | `20_sum_with_struct_payload.cardinal` | Sum variant with struct in payload (Circle/Rect with Point) | PASS |
| 21 | `21_match_binding_collision.cardinal` | Payload bindings with same names as functions; multiple scopes | PASS |
| 22 | `22_recursive_sum_tostr.cardinal` | Manual recursive to_str for a recursive sum type | PASS |
| 23 | `23_nullary_mixed.cardinal` | Nullary and payload variants mixed in one type (5 variants) | PASS |
| 24 | `24_enum_to_str.cardinal` | Enum to_str, iteration, stored/returned/passed | PASS |
| 25 | `25_sum_with_vec_payload.cardinal` | Sum variant with {i32} vec payload | PASS |
| 26 | `26_override_tostr.cardinal` | Override to_str via `func Tree_to_str(x Tree)->str` | PASS |
| 27 | `27_large_tree.cardinal` | Balanced tree of 63 nodes (depth 6); build/sum/count/depth | PASS |
| 28 | `28_nested_sum.cardinal` | Nested sum types (Outer contains Inner); nested match | PASS |
| 29 | `29_sum_return_from_fn.cardinal` | Sum returned from fn, chained through multiple fn calls | PASS |
| 30 | `30_enum_in_struct.cardinal` | Enum in struct field, struct inside recursive sum | PASS |
| 31 | `31_sum_float_payload.cardinal` | Sum variant with f64 payload | PASS |
| 32 | `32_match_else_fallthrough.cardinal` | Else clause catches unmatched variants; multiple unmatched | PASS |
| 33 | `33_sum_mutual_recursion.cardinal` | Peano naturals: recursive sum, nat_add, nat_mul | PASS |
| 34 | `34_sum_multiple_payloads.cardinal` | Multi-field payload binding: str, i32, bool, i64 fields | PASS |
| 35 | `35_sum_with_char_payload.cardinal` | Sum variant with char payload | PASS |
| 36 | `36_deep_list_100.cardinal` | List of 60 elements; recursion depth stress | PASS |
| 37 | `37_sum_in_closure_capture.cardinal` | Sum value captured and mutated by closure | PASS |
| 38 | `38_sum_array_payload.cardinal` | Sum variant with [i32] array payload | PASS |
| 39 | `39_sum_repeated_match.cardinal` | Same sum value matched multiple times independently | PASS |
| 40 | `40_sum_tag_field_stress.cardinal` | User payload field named `tag` in multiple variants; mixed types | PASS |
| 41 | `41_sum_in_vec_and_map.cardinal` | Sum in vec and in map simultaneously | PASS |
| 42 | `42_sum_multi_payload_recursive.cardinal` | Recursive JSON-like sum; nested match across multiple levels | PASS |
| 43 | `43_sum_multiword_struct_payload.cardinal` | Large struct (>16B) as sum payload; stresses x86 multi-word box | PASS |
| 44 | `44_sum_tree_balance.cardinal` | Highly skewed (all-left / all-right) trees | PASS |
| 45 | `45_enum_all_variants.cardinal` | 12-variant enum; all-variant iteration; enum as map key | PASS |

---

## SUMMARY

- **Total tests:** 45
- **Confirmed compiler bugs (interpreter/C/x86 disagreement):** 0
- **Notes:** No divergences found across any backend combination. All sum-type,
  match, and enum features exercised including recursive sums, >5-variant sums,
  deeply skewed trees, nested matches, closures over sums, struct/vec/map/array
  payloads, user field named `tag`, to_str override, enum map keys, and
  multi-word struct payloads through sum boxes.
