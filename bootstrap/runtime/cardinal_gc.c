#include "cardinal_gc.h"
#include "cardinal_rt.h"      /* cl_panic_cstr */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* Handle layout (64-bit):
 *   bits 48..63  TAG  (0xCA11)   — makes a handle distinguishable from data
 *   bits 32..47  generation
 *   bits  0..31  table index
 * A handle is never 0 (TAG != 0); 0 is reserved for null. */
#define CL_TAG        ((uint64_t)0xCA11)
#define MK_HANDLE(g, i)  (((uint64_t)CL_TAG << 48) | ((uint64_t)(g) << 32) | (uint32_t)(i))
#define H_TAG(h)      ((uint32_t)((h) >> 48))
#define H_GEN(h)      ((uint16_t)((h) >> 32))
#define H_IDX(h)      ((uint32_t)(h))

typedef struct {
    void    *ptr;          /* malloc'd object, NULL when slot is free */
    uint64_t size;         /* object size in bytes */
    uint16_t generation;   /* bumped on free -> stale handles become invalid */
    uint8_t  mark;
    uint8_t  used;
} cl_slot;

static cl_slot  *g_table;
static uint32_t  g_cap;
static uint32_t  g_live;
static uint64_t  g_bytes_live;
static uint64_t  g_bytes_since_gc;
static uint64_t  g_threshold = 1u << 20;   /* 1 MiB default; grows with live set */
static uint64_t  g_min_threshold = 1u << 20; /* floor for the adaptive threshold */
static uint64_t  g_collections;
static int       g_stats;

/* mark worklist */
static uint32_t *g_work;
static uint32_t  g_work_cap, g_work_len;

/* free-slot stack: indices of unused handle-table slots, so alloc_slot is O(1)
 * instead of linearly scanning g_table for a hole. Slots are pushed here when
 * freed (sweep) or first created (table growth), and popped on allocation. */
static uint32_t *g_free;
static uint32_t  g_free_cap, g_free_len;

/* shadow stack: precise roots, parallel arrays of (address, byte-size) */
static void    **g_root_addr;
static uint32_t *g_root_size;
static uint32_t  g_root_len, g_root_cap;

void cl_gc_push_root(void *addr, uint32_t nbytes) {
    if (g_root_len == g_root_cap) {
        g_root_cap = g_root_cap ? g_root_cap * 2 : 256;
        g_root_addr = (void **)realloc(g_root_addr, g_root_cap * sizeof(void *));
        g_root_size = (uint32_t *)realloc(g_root_size, g_root_cap * sizeof(uint32_t));
        if (!g_root_addr || !g_root_size) cl_panic_cstr("out of memory (shadow stack)");
    }
    g_root_addr[g_root_len] = addr;
    g_root_size[g_root_len] = nbytes;
    g_root_len++;
}

void cl_gc_pop_roots(uint32_t n) {
    g_root_len -= n;
}

/* pinned (permanent) roots — never popped; scanned like shadow-stack roots */
static void    **g_pin_addr;
static uint32_t *g_pin_size;
static uint32_t  g_pin_len, g_pin_cap;

void cl_gc_pin(void *addr, uint32_t nbytes) {
    if (g_pin_len == g_pin_cap) {
        g_pin_cap = g_pin_cap ? g_pin_cap * 2 : 16;
        g_pin_addr = (void **)realloc(g_pin_addr, g_pin_cap * sizeof(void *));
        g_pin_size = (uint32_t *)realloc(g_pin_size, g_pin_cap * sizeof(uint32_t));
        if (!g_pin_addr || !g_pin_size) cl_panic_cstr("out of memory (pinned roots)");
    }
    g_pin_addr[g_pin_len] = addr;
    g_pin_size[g_pin_len] = nbytes;
    g_pin_len++;
}

static void gc_print_stats(void) {
    fprintf(stderr, "gc: %llu collections, %llu objects live, %llu bytes live\n",
            (unsigned long long)g_collections,
            (unsigned long long)g_live,
            (unsigned long long)g_bytes_live);
}

void cl_gc_init(void *stack_base) {
    (void)stack_base;                 /* rooting is via the shadow stack now */
    const char *t = getenv("CARDINAL_GC_THRESHOLD");
    if (t) g_threshold = strtoull(t, NULL, 0);
    g_min_threshold = g_threshold;
    if (getenv("CARDINAL_GC_STATS")) { g_stats = 1; atexit(gc_print_stats); }
}

void cl_gc_set_threshold(uint64_t bytes) { g_threshold = bytes; g_min_threshold = bytes; }
uint64_t cl_gc_live_count(void)  { return g_live; }
uint64_t cl_gc_bytes_live(void)  { return g_bytes_live; }

static int valid_handle(uint64_t w, uint32_t *idx_out) {
    if (H_TAG(w) != CL_TAG) return 0;
    uint32_t i = H_IDX(w);
    if (i >= g_cap || !g_table[i].used) return 0;
    if (g_table[i].generation != H_GEN(w)) return 0;
    *idx_out = i;
    return 1;
}

void *cl_gc_deref(cl_handle h) {
    if (h == 0) cl_panic_cstr("null handle dereference");
    if (H_TAG(h) != CL_TAG) cl_panic_cstr("invalid handle");
    uint32_t i = H_IDX(h);
    if (i >= g_cap || !g_table[i].used || g_table[i].generation != H_GEN(h))
        cl_panic_cstr("use-after-free: stale handle");
    return g_table[i].ptr;
}

static void free_push(uint32_t i) {
    if (g_free_len == g_free_cap) {
        g_free_cap = g_free_cap ? g_free_cap * 2 : 256;
        g_free = (uint32_t *)realloc(g_free, g_free_cap * sizeof(uint32_t));
        if (!g_free) cl_panic_cstr("out of memory (gc freelist)");
    }
    g_free[g_free_len++] = i;
}

static uint32_t alloc_slot(void) {
    if (g_free_len) return g_free[--g_free_len];    /* O(1): reuse a free slot */
    uint32_t old = g_cap;                           /* table full -> grow */
    g_cap = g_cap ? g_cap * 2 : 64;
    g_table = (cl_slot *)realloc(g_table, g_cap * sizeof(cl_slot));
    if (!g_table) cl_panic_cstr("out of memory (handle table)");
    memset(g_table + old, 0, (g_cap - old) * sizeof(cl_slot));
    uint32_t first = old ? old : 1;                 /* index 0 reserved (null) */
    for (uint32_t i = first + 1; i < g_cap; i++)    /* seed free list with the rest */
        free_push(i);
    return first;
}

cl_handle cl_gc_alloc(uint64_t size) {
    if (g_bytes_since_gc > g_threshold) cl_gc_collect();
    uint32_t i = alloc_slot();
    void *p = calloc(1, size ? (size_t)size : 1);
    if (!p) cl_panic_cstr("out of memory");
    g_table[i].ptr = p;
    g_table[i].size = size;
    g_table[i].used = 1;
    g_table[i].mark = 0;
    g_live++;
    g_bytes_live += size;
    g_bytes_since_gc += size;
    return MK_HANDLE(g_table[i].generation, i);
}

static void work_push(uint32_t i) {
    if (g_work_len == g_work_cap) {
        g_work_cap = g_work_cap ? g_work_cap * 2 : 256;
        g_work = (uint32_t *)realloc(g_work, g_work_cap * sizeof(uint32_t));
        if (!g_work) cl_panic_cstr("out of memory (gc worklist)");
    }
    g_work[g_work_len++] = i;
}

/* Scan [lo,hi) for handle-looking words; mark + enqueue referenced objects. */
static void scan_range(void *lo, void *hi) {
    if (lo > hi) { void *t = lo; lo = hi; hi = t; }
    uintptr_t a = ((uintptr_t)lo + 7) & ~(uintptr_t)7;   /* 8-byte align */
    uintptr_t b = (uintptr_t)hi;
    for (uintptr_t p = a; p + sizeof(uint64_t) <= b; p += sizeof(uint64_t)) {
        uint64_t w = *(uint64_t *)p;
        uint32_t i;
        if (valid_handle(w, &i) && !g_table[i].mark) {
            g_table[i].mark = 1;
            work_push(i);
        }
    }
}

void cl_gc_collect(void) {
    g_collections++;
    for (uint32_t i = 0; i < g_cap; i++) g_table[i].mark = 0;
    g_work_len = 0;

    /* roots: precise — scan exactly the shadow-stack entries */
    for (uint32_t r = 0; r < g_root_len; r++)
        scan_range(g_root_addr[r], (char *)g_root_addr[r] + g_root_size[r]);

    /* pinned (permanent) roots — runtime-internal globals (frame-independent) */
    for (uint32_t r = 0; r < g_pin_len; r++)
        scan_range(g_pin_addr[r], (char *)g_pin_addr[r] + g_pin_size[r]);

    /* transitive: scan each reachable object's bytes for more handles */
    while (g_work_len) {
        uint32_t i = g_work[--g_work_len];
        scan_range(g_table[i].ptr, (char *)g_table[i].ptr + g_table[i].size);
    }

    /* sweep */
    for (uint32_t i = 1; i < g_cap; i++) {
        if (g_table[i].used && !g_table[i].mark) {
            free(g_table[i].ptr);
            g_table[i].ptr = NULL;
            g_table[i].used = 0;
            g_table[i].generation++;        /* invalidate stale handles */
            g_bytes_live -= g_table[i].size;
            g_live--;
            free_push(i);                   /* return slot to the free list */
        }
    }
    g_bytes_since_gc = 0;
    /* Adaptive trigger: collect again only after the live set grows by ~2x.
     * A fixed threshold with a growing live set makes total GC work O(n^2)
     * (O(n) collections, each scanning the O(n) live heap); scaling the
     * threshold with live size amortizes collection to ~O(n). */
    g_threshold = g_bytes_live * 2;
    if (g_threshold < g_min_threshold) g_threshold = g_min_threshold;
}
