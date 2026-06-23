/* Cardinal canonical-style GC (bootstrap C reference implementation).
 *
 * Implements the DESIGN.md §6 model: managed objects live behind *generational
 * handles* (index + generation packed into a tagged 64-bit word) in a handle
 * table. Reclamation is mark-and-sweep. Roots and heap objects are scanned
 * *conservatively* for handle-looking words (the tag + generation make false
 * positives rare), so no per-object trace metadata is needed. Dereferencing a
 * stale handle is detected (generation mismatch) and panics — the use-after-free
 * safety the handle representation buys us.
 *
 * This C version is the reference/throwaway runtime; the canonical one is later
 * written in Cardinal and targets the self-hosted compiler. */
#ifndef CARDINAL_GC_H
#define CARDINAL_GC_H

#include <stdint.h>

typedef uint64_t cl_handle;            /* 0 == null */
#define CL_NULL ((cl_handle)0)

/* Call once at program start (also reads CARDINAL_GC_* env vars). The arg is
 * unused now that rooting is via the shadow stack; kept for API stability. */
void cl_gc_init(void *stack_base);

/* Shadow stack: precise rooting. The compiler pushes the address+size of each
 * managed local/temp/param on entry and pops them on return. The collector scans
 * exactly these byte ranges for roots — no conservative machine-stack scan, and
 * taking &var forces it to memory (no register-only roots). */
void cl_gc_push_root(void *addr, uint32_t nbytes);
void cl_gc_pop_roots(uint32_t n);

/* Pin a root PERMANENTLY (never popped), scanned alongside the shadow stack.
 * For runtime-internal globals (e.g. the string-literal intern table) that must
 * stay reachable for the whole program but are first created mid-frame: pushing
 * them on the LIFO shadow stack would be popped by the enclosing frame's
 * cl_gc_pop_roots and corrupt the stack discipline. */
void cl_gc_pin(void *addr, uint32_t nbytes);

/* Allocate a zeroed managed object of `size` bytes; returns its handle. May
 * trigger a collection first. */
cl_handle cl_gc_alloc(uint64_t size);

/* Resolve a handle to its backing pointer. Panics on null/invalid/stale. */
void *cl_gc_deref(cl_handle h);

/* Force a collection now. */
void cl_gc_collect(void);

/* Introspection (used by tests). */
uint64_t cl_gc_live_count(void);
uint64_t cl_gc_bytes_live(void);
void     cl_gc_set_threshold(uint64_t bytes);   /* auto-collect trigger */

#endif
