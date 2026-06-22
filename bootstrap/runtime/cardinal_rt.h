/* Cardinal C-backend runtime (bootstrap, throwaway).
 *
 * This is the minimal C support the emitted code links against. It is NOT the
 * canonical Cardinal runtime (handle-table + mark-and-sweep GC) described in
 * DESIGN.md §6 — that one is written in Cardinal later. Arrays here are plain
 * malloc'd buffers (no GC; bootstrap programs leak, which is fine). */
#ifndef CARDINAL_RT_H
#define CARDINAL_RT_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#include "cardinal_gc.h"     /* cl_handle */

/* str — a single GC handle to a managed string object { u64 nbytes; UTF-8
 * bytes... } (DESIGN.md §5.3). Immutable; traced like any other handle. */
typedef cl_handle cl_str;
/* Managed objects live behind GC handles (DESIGN.md §6). */
typedef struct { cl_handle data; uint64_t len; uint32_t elemsz; } cl_array;  /* reference */
typedef struct { void *fn; cl_handle env; } cl_closure;          /* function value */

cl_array cl_array_new(uint32_t elemsz, uint64_t len);
void    *cl_array_at(cl_array a, uint64_t i);   /* bounds-checked; panics on OOB */

/* Construct a managed string from raw UTF-8 bytes (copied). */
cl_str   cl_str_from_utf8(const char *bytes, uint64_t nbytes);
/* Intern a NUL-terminated C string literal: returns a stable, permanently-rooted
 * handle (same pointer -> same handle). This is what emitted code uses for string
 * literals, so an inlined literal is never an un-rooted fresh allocation. */
cl_str   cl_strlit(const char *utf8_cstr);
uint64_t cl_str_len(cl_str s);                  /* codepoint count */

/* strings:: / convert:: builtins (names match the backend's cl_<mod>__<fn>). All
 * indexing is by Unicode codepoint, matching the interpreter's Python-str model. */
cl_array cl_strings__chars(cl_str s);                              /* -> [char] (int32 codepoints) */
cl_str   cl_strings__concat(cl_str a, cl_str b);
cl_str   cl_strings__substr(cl_str s, uint64_t start, uint64_t count);  /* slice clamps, never panics */
cl_str   cl_strings__from_char(int32_t cp);
bool     cl_strings__eq(cl_str a, cl_str b);                       /* content equality */
uint32_t cl_convert__ord(int32_t ch);
int32_t  cl_convert__chr(uint32_t v);
cl_str   cl_convert__int_to_str(int64_t v);
int64_t  cl_convert__str_to_int(cl_str s);                        /* panics if not an integer */

/* ---- Growable vector  {T}  (DESIGN.md §5.3) ------------------------------ *
 * Reference semantics: a vector value is a single GC handle to a header
 * object {len, cap, elemsz, buf}. Copying the value shares the header, so a
 * push through one alias is visible through every alias. Elements are stored
 * inline (elemsz bytes) in a GC-managed `buf`; any handles they contain are
 * found by the collector's conservative object scan (kept 8-byte aligned). */
typedef cl_handle cl_vec;
cl_vec   cl_vec_new(uint32_t elemsz);
void     cl_vec_push(cl_vec v, const void *elem);  /* copies elemsz bytes onto the end */
void     cl_vec_pop(cl_vec v, void *out);          /* copies last into out, shrinks; panics if empty */
void    *cl_vec_at(cl_vec v, uint64_t i);          /* bounds-checked element ptr (read + write) */
uint64_t cl_vec_len(cl_vec v);

/* ---- Growable map  {K V}  (DESIGN.md §5.3) ------------------------------- *
 * Reference semantics (handle to a header), insertion-ordered to match the
 * interpreter's map_keys / for-in order. Keys are value-semantic hashable
 * types; per-map the key type is fixed, so the runtime needs only two kinds:
 * CL_MAP_STR keys compare/hash by string content, CL_MAP_SCALAR keys by their
 * raw keysz bytes (int/char/bool/enum). */
typedef cl_handle cl_map;
#define CL_MAP_SCALAR 0
#define CL_MAP_STR    1
cl_map   cl_map_new(uint32_t keysz, uint32_t valsz, int key_kind);
void     cl_map_set(cl_map m, const void *key, const void *val);  /* insert or update */
void    *cl_map_get(cl_map m, const void *key);    /* ptr to value; panics "map key not found" if absent */
int      cl_map_has(cl_map m, const void *key);
void     cl_map_del(cl_map m, const void *key);    /* no-op if absent */
cl_vec   cl_map_keys(cl_map m);                    /* fresh cl_vec(keysz) of keys, insertion order */
uint64_t cl_map_len(cl_map m);

_Noreturn void cl_panic(cl_str msg);
_Noreturn void cl_panic_cstr(const char *msg);

void cl_print_i64(int64_t v);
void cl_print_u64(uint64_t v);
void cl_print_f64(double v);
void cl_print_bool(int v);
void cl_print_str(cl_str s);
void cl_print_char(int32_t cp);     /* the UTF-8 character itself (for aggregate display) */
void cl_print_nl(void);

#endif
