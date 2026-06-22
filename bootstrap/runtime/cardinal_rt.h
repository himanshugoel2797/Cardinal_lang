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

typedef struct { const char *data; uint64_t len; } cl_str;       /* UTF-8, immutable (static) */
/* Managed objects live behind GC handles (DESIGN.md §6). */
typedef struct { cl_handle data; uint64_t len; uint32_t elemsz; } cl_array;  /* reference */
typedef struct { void *fn; cl_handle env; } cl_closure;          /* function value */

cl_array cl_array_new(uint32_t elemsz, uint64_t len);
void    *cl_array_at(cl_array a, uint64_t i);   /* bounds-checked; panics on OOB */
uint64_t cl_str_len(cl_str s);                  /* codepoint count */

void cl_panic(cl_str msg);
void cl_panic_cstr(const char *msg);

void cl_print_i64(int64_t v);
void cl_print_u64(uint64_t v);
void cl_print_f64(double v);
void cl_print_bool(int v);
void cl_print_str(cl_str s);
void cl_print_nl(void);

#endif
