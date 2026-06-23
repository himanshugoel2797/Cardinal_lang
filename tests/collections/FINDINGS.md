# Collections Test Findings

**Test area:** Strings, Vectors, Maps — all runtime collection types.  
**Harness:** `sh tests/run3.sh` — three-way differential (interpreter ORACLE, C backend, x86_64 backend).  
**Total tests:** 46  
**CONFIRMED BUGS:** 0  
**All tests PASS.**

---

## CONFIRMED BUGS

_None found._ All 46 tests agree across interpreter, C backend, and x86_64 backend on both stdout and exit-status class (0=ok / 1=panic).

---

## Test Results

### STRINGS

| Test file | Verdict | Description |
|-----------|---------|-------------|
| str_concat.cardinal | PASS (class=0) | Chained concat, empty strings, loop concat, newline in literal |
| str_substr.cardinal | PASS (class=0) | Normal slices, empty result, full string, out-of-range clamping |
| str_substr_edge.cardinal | PASS (class=0) | Off-by-one at each codepoint in mixed 1/2/3/4-byte string "a😀日é" |
| str_substr_zero_count.cardinal | PASS (class=0) | count=0 at every valid position returns empty string |
| str_utf8.cardinal | PASS (class=0) | len via chars: "héllo"=5, "日本語"=3, "a😀b"=3; codepoint-level substr |
| str_utf8_concat.cardinal | PASS (class=0) | Concat of multi-byte strings, substr on concatenated multi-byte result |
| str_unicode_boundary.cardinal | PASS (class=0) | 1<->2-byte boundary (~copyright), 2<->3-byte, 3<->4-byte substr |
| str_chars.cardinal | PASS (class=0) | chars of ASCII & multi-byte; from_char round-trip including multi-byte |
| str_chars_iter_utf8.cardinal | PASS (class=0) | Iterate chars of "h日é😀"; verify ord values; rebuild string via from_char |
| str_from_char_ord.cardinal | PASS (class=0) | from_char + ord round-trip for ASCII, 2-byte, 3-byte, 4-byte codepoints |
| str_eq.cardinal | PASS (class=0) | String equality by content: literals, built strings, UTF-8 strings |
| str_escape.cardinal | PASS (class=0) | Escape sequences: \n \t \\ \" in literals; len("\n")=1 codepoint |
| str_empty_ops.cardinal | PASS (class=0) | All ops on empty string: chars, substr, concat, eq |
| str_len_utf8.cardinal | PASS (class=0) | chars len is codepoint count: ASCII, 2-byte, 3-byte, 4-byte strings |
| str_convert.cardinal | PASS (class=0) | int_to_str, str_to_int: basic, negative, round-trip, whitespace trim |
| str_convert_signed.cardinal | PASS (class=0) | str_to_int: i64::MAX, i32::MAX, i32::MIN, leading zeros, +/- signs |
| str_convert_panic.cardinal | PASS (class=1) | str_to_int("abc") panics on all three paths |
| str_convert_panic2.cardinal | PASS (class=1) | str_to_int("") panics on all three paths |
| str_concat_loop1000.cardinal | PASS (class=0) | Build 100-number string via loop; check len=289, first/last substr |
| str_mapkey.cardinal | PASS (class=0) | String as map key: content-keyed (built+literal same content = same slot) |
| str_to_str.cardinal | PASS (class=0) | to_str on i32, i64, bool, char (ASCII/multi-byte), str (ASCII/UTF-8) |
| str_display.cardinal | PASS (class=0) | Vec display via println: {1 2 3}, {a b}, {true false true}, {} |

### VECTORS

| Test file | Verdict | Description |
|-----------|---------|-------------|
| vec_basic.cardinal | PASS (class=0) | push, pop, index, set, len, for-in, literal {…} |
| vec_oob_panic.cardinal | PASS (class=1) | OOB index v[5] on 3-element vec panics on all paths |
| vec_pop_empty_panic.cardinal | PASS (class=1) | pop on empty vec panics on all paths |
| vec_growth.cardinal | PASS (class=0) | Push 100 items; check element at [0],[50],[99]; pop until empty |
| vec_strings.cardinal | PASS (class=0) | Vec of strings; UTF-8 push/index; set element; pop |
| vec_ref_semantics.cardinal | PASS (class=0) | Mutation inside fn visible outside (reference semantics); push+set_first |
| vec_nested.cardinal | PASS (class=0) | {{i32}} nested vec; index outer[i][j]; mut via outer writes through |
| vec_struct.cardinal | PASS (class=0) | Vec of structs; set pts[i].field; for-in sum; pop returns copy |
| vec_closures.cardinal | PASS (class=0) | Vec of closures (func(i32->i32)); push via make_adder; for-in call; pop |
| vec_float.cardinal | PASS (class=0) | Vec of f64; push, index, for-in sum, pop |

### MAPS

| Test file | Verdict | Description |
|-----------|---------|-------------|
| map_basic.cardinal | PASS (class=0) | str->i32: insert, read, overwrite, map_has, map_del, len, map_keys |
| map_insertion_order.cardinal | PASS (class=0) | Delete middle, re-insert at end; overwrite keeps original position |
| map_missing_key_panic.cardinal | PASS (class=1) | Read missing key panics on all paths |
| map_int_key.cardinal | PASS (class=0) | i32 keys including negative; insert, read, del, map_has, map_keys order |
| map_bool_key.cardinal | PASS (class=0) | bool keys: true/false; overwrite, del, map_has |
| map_char_key.cardinal | PASS (class=0) | char keys including multi-byte ('e-accent'); del, map_has |
| map_enum_key.cardinal | PASS (class=0) | Enum keys (Color::Red/Green/Blue); del, map_has, map_keys, for-in |
| map_many_entries.cardinal | PASS (class=0) | 50 entries to stress hashing/resize; delete evens; check odds remain |
| map_str_value.cardinal | PASS (class=0) | i32->str map with UTF-8 values; strings ops on retrieved value |
| map_struct_value.cardinal | PASS (class=0) | str->Pair struct value; read-modify-write back pattern |
| map_vec_value.cardinal | PASS (class=0) | str->{i32} map; check vec len and elements through map |
| map_del_idempotent.cardinal | PASS (class=0) | map_del on already-deleted and nonexistent keys is no-op (not panic) |
| map_overwrite_many.cardinal | PASS (class=0) | 100 overwrites of same key; len stays 1; last value visible |
| map_keys_after_reinsert.cardinal | PASS (class=0) | Heavy insert/delete/re-insert cycle; map_keys order verified |

---

## Areas Investigated

### Multi-byte UTF-8 (highest divergence risk)
Tested exhaustively: 2-byte (e-accent, copyright), 3-byte (CJK, trade-mark), 4-byte (emoji)
codepoints in strings::chars, strings::substr, strings::from_char, strings::eq,
strings::concat, and len (via chars). All paths agree. The C runtime's utf8_decode /
utf8_encode / codepoint-indexed substr matches Python's native codepoint semantics exactly.

### substr boundary off-by-one
Tested (strings::substr s start 1) for each codepoint in mixed-width strings, and at
positions 0, len, >len. The C substr implementation's lo/hi codepoint tracking is correct.

### Panic agreement
All panic tests (str_to_int non-numeric, str_to_int empty, vec OOB, vec pop-empty,
map missing-key) agree on class=1 across all paths.

### Map key types
Tested all documented key types: str (content-keyed), i32 (including negative), bool, char
(including multi-byte), enum. All work correctly including insertion-order preservation.

### Vec reference semantics
Confirmed: pushing to a vec from inside a function mutates the caller's vec.
Mutation of outer[i][j] in nested vec propagates to the original inner vec reference.

---

## Notes on Tests Revised During Authoring

- vec_closures: Cannot use inline 'func ... end' as a vec literal element (parser rejects
  'func' token in expression position). Fixed to use named helper returning closure.
  Not a backend bug — uniform compile-time rejection across all paths.
- str_concat_loop1000: Required explicit i64 literals for int_to_str (takes i64) and
  explicit (as (len cs) i32) cast. Type-system constraint, not a backend bug.
