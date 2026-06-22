#include "cardinal_rt.h"
#include "cardinal_gc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

cl_array cl_array_new(uint32_t elemsz, uint64_t len) {
    cl_array a;
    a.elemsz = elemsz;
    a.len = len;
    a.data = cl_gc_alloc(len * (uint64_t)elemsz);   /* GC-managed, zeroed */
    return a;
}

void *cl_array_at(cl_array a, uint64_t i) {
    if (i >= a.len) {
        fprintf(stderr, "panic: index out of bounds: %llu (len %llu)\n",
                (unsigned long long)i, (unsigned long long)a.len);
        exit(101);
    }
    return (char *)cl_gc_deref(a.data) + i * a.elemsz;
}

uint64_t cl_str_len(cl_str s) {
    uint64_t n = 0;
    for (uint64_t i = 0; i < s.len; i++)
        if (((unsigned char)s.data[i] & 0xC0) != 0x80) n++;  /* count UTF-8 leads */
    return n;
}

void cl_panic(cl_str msg) {
    fprintf(stderr, "panic: %.*s\n", (int)msg.len, msg.data);
    exit(101);
}

void cl_panic_cstr(const char *msg) {
    fprintf(stderr, "panic: %s\n", msg);
    exit(101);
}

void cl_print_i64(int64_t v)  { printf("%lld", (long long)v); }
void cl_print_u64(uint64_t v) { printf("%llu", (unsigned long long)v); }
void cl_print_f64(double v)   { printf("%g", v); }
void cl_print_bool(int v)     { fputs(v ? "true" : "false", stdout); }
void cl_print_str(cl_str s)   { fwrite(s.data, 1, s.len, stdout); }
void cl_print_nl(void)        { putchar('\n'); }

/* ===================================================================== *
 *  Growable vectors {T} and maps {K V}  (DESIGN.md §5.3)
 *
 *  Both are GC handles to a header object so the value has reference
 *  semantics (copy = share). Element/key/value bytes live in GC-managed
 *  buffers and are kept 8-byte aligned, so handles they embed are found by
 *  the collector's conservative object scan (cardinal_gc.c:scan_range).
 *
 *  Rooting note: the GC scans the shadow stack and live objects only — NOT
 *  the C machine stack. So a freshly allocated object held in a local C
 *  variable is *not* a root. These functions stay safe by (a) keeping at
 *  most one un-rooted allocation in flight and storing it into a header
 *  that the caller already roots before the next allocation, and (b) using
 *  cl_gc_push_root explicitly where a function builds a new object across
 *  several allocations (cl_map_keys).
 * ===================================================================== */

static uint64_t round8(uint64_t n) { return (n + 7u) & ~(uint64_t)7u; }

/* ---- vectors ---------------------------------------------------------- */

typedef struct {
    uint64_t  len;       /* number of elements in use */
    uint64_t  cap;       /* element capacity of buf */
    uint32_t  elemsz;    /* bytes per element */
    cl_handle buf;       /* GC buffer of cap*elemsz bytes; CL_NULL when cap==0 */
} cl_vec_hdr;

cl_vec cl_vec_new(uint32_t elemsz) {
    cl_handle h = cl_gc_alloc(sizeof(cl_vec_hdr));   /* zeroed: len=cap=0, buf=null */
    cl_vec_hdr *v = (cl_vec_hdr *)cl_gc_deref(h);
    v->elemsz = elemsz ? elemsz : 1;
    return h;
}

/* Ensure room for one more element. `v` (the header) is reachable from the
 * caller's rooted handle, so the single buf allocation here is safe. */
static void vec_grow_if_needed(cl_handle h) {
    cl_vec_hdr *v = (cl_vec_hdr *)cl_gc_deref(h);
    if (v->len < v->cap) return;
    uint64_t ncap = v->cap ? v->cap * 2 : 4;
    cl_handle nbuf = cl_gc_alloc(ncap * (uint64_t)v->elemsz);   /* may collect: old buf still in v->buf */
    v = (cl_vec_hdr *)cl_gc_deref(h);                           /* re-deref (non-moving, but be explicit) */
    if (v->buf != CL_NULL && v->len)
        memcpy(cl_gc_deref(nbuf), cl_gc_deref(v->buf), v->len * (uint64_t)v->elemsz);
    v->buf = nbuf;
    v->cap = ncap;
}

void cl_vec_push(cl_vec h, const void *elem) {
    vec_grow_if_needed(h);
    cl_vec_hdr *v = (cl_vec_hdr *)cl_gc_deref(h);
    memcpy((char *)cl_gc_deref(v->buf) + v->len * (uint64_t)v->elemsz, elem, v->elemsz);
    v->len++;
}

void cl_vec_pop(cl_vec h, void *out) {
    cl_vec_hdr *v = (cl_vec_hdr *)cl_gc_deref(h);
    if (v->len == 0) cl_panic_cstr("pop from empty vector");
    v->len--;
    memcpy(out, (char *)cl_gc_deref(v->buf) + v->len * (uint64_t)v->elemsz, v->elemsz);
}

void *cl_vec_at(cl_vec h, uint64_t i) {
    cl_vec_hdr *v = (cl_vec_hdr *)cl_gc_deref(h);
    if (i >= v->len) {
        fprintf(stderr, "panic: index out of bounds: %llu (len %llu)\n",
                (unsigned long long)i, (unsigned long long)v->len);
        exit(101);
    }
    return (char *)cl_gc_deref(v->buf) + i * (uint64_t)v->elemsz;
}

uint64_t cl_vec_len(cl_vec h) {
    return ((cl_vec_hdr *)cl_gc_deref(h))->len;
}

/* ---- maps ------------------------------------------------------------- *
 * Compact-dict layout: an insertion-ordered `entries` buffer (so map_keys /
 * for-in iterate in insertion order, matching the interpreter) plus an
 * open-addressing `index` of int64 slots holding entry-index+1 into entries.
 *
 *   entry := [ uint64 state ][ key (round8 keysz) ][ val (round8 valsz) ]
 *   state: 1 = live, 0 = tombstone (deleted)
 *   index slot: 0 = empty, -1 = deleted (probe past it), n>0 = entry n-1
 */

typedef struct {
    uint64_t  len;        /* live entries */
    uint64_t  nent;       /* entries appended (incl tombstones) */
    uint64_t  ecap;       /* entry capacity of `entries` */
    uint64_t  icap;       /* slot count of `index` (power of two), 0 when none */
    uint32_t  keysz;
    uint32_t  valsz;
    uint32_t  stride;     /* per-entry byte stride */
    uint32_t  kind;       /* CL_MAP_SCALAR | CL_MAP_STR */
    cl_handle entries;
    cl_handle index;
} cl_map_hdr;

#define MAP_KEYOFF 8u
static uint64_t map_valoff(const cl_map_hdr *m) { return MAP_KEYOFF + round8(m->keysz); }

static uint64_t fnv1a(const void *p, uint64_t n) {
    const unsigned char *b = (const unsigned char *)p;
    uint64_t h = 1469598103934665603ull;
    for (uint64_t i = 0; i < n; i++) { h ^= b[i]; h *= 1099511628211ull; }
    return h;
}

static uint64_t map_hash_key(const cl_map_hdr *m, const void *key) {
    if (m->kind == CL_MAP_STR) {
        cl_str s; memcpy(&s, key, sizeof s);
        return fnv1a(s.data, s.len);
    }
    return fnv1a(key, m->keysz);
}

static int map_key_eq(const cl_map_hdr *m, const void *a, const void *b) {
    if (m->kind == CL_MAP_STR) {
        cl_str x, y; memcpy(&x, a, sizeof x); memcpy(&y, b, sizeof y);
        return x.len == y.len && (x.len == 0 || memcmp(x.data, y.data, x.len) == 0);
    }
    return memcmp(a, b, m->keysz) == 0;
}

static char *map_entry(const cl_map_hdr *m, void *entries_ptr, uint64_t i) {
    return (char *)entries_ptr + i * m->stride;
}

/* Find the entry index for `key`, or -1. */
static int64_t map_find(const cl_map_hdr *m, const void *key) {
    if (m->icap == 0 || m->len == 0) return -1;
    char *ent = (char *)cl_gc_deref(m->entries);
    int64_t *idx = (int64_t *)cl_gc_deref(m->index);
    uint64_t mask = m->icap - 1;
    uint64_t h = map_hash_key(m, key) & mask;
    for (uint64_t probe = 0; probe <= mask; probe++) {
        int64_t slot = idx[h];
        if (slot == 0) return -1;            /* empty: key absent */
        if (slot > 0) {
            char *e = map_entry(m, ent, (uint64_t)(slot - 1));
            if (*(uint64_t *)e == 1u && map_key_eq(m, e + MAP_KEYOFF, key))
                return slot - 1;
        }
        h = (h + 1) & mask;                  /* linear probe (slot==-1 deleted: keep going) */
    }
    return -1;
}

/* Insert entry index `ei` into the open-addressing index (no growth/dup check;
 * caller guarantees capacity and uniqueness). */
static void map_index_put(cl_map_hdr *m, int64_t *idx, const char *entries_ptr, uint64_t ei) {
    uint64_t mask = m->icap - 1;
    const char *e = entries_ptr + ei * m->stride;
    uint64_t h = map_hash_key(m, e + MAP_KEYOFF) & mask;
    while (idx[h] > 0) h = (h + 1) & mask;   /* stop at empty(0) or deleted(-1) */
    idx[h] = (int64_t)(ei + 1);
}

/* Rebuild the index from live entries at a new capacity, compacting tombstones
 * out of `entries`. `m` is reachable from the caller's rooted handle. */
static void map_rehash(cl_handle h, uint64_t new_icap) {
    cl_map_hdr *m = (cl_map_hdr *)cl_gc_deref(h);
    uint64_t live = m->len;

    /* compact entries into a fresh buffer (also right-sizes ecap) */
    uint64_t necap = live < 4 ? 4 : live * 2;
    cl_handle nentries = cl_gc_alloc(necap * m->stride);   /* alloc #1 */
    m = (cl_map_hdr *)cl_gc_deref(h);
    if (m->entries != CL_NULL && m->nent) {
        char *src = (char *)cl_gc_deref(m->entries);
        char *dst = (char *)cl_gc_deref(nentries);
        uint64_t w = 0;
        for (uint64_t i = 0; i < m->nent; i++) {
            char *e = src + i * m->stride;
            if (*(uint64_t *)e == 1u) { memcpy(dst + w * m->stride, e, m->stride); w++; }
        }
    }
    m->entries = nentries;     /* reachable before next alloc */
    m->nent = live;
    m->ecap = necap;

    cl_handle nindex = cl_gc_alloc(new_icap * sizeof(int64_t));   /* alloc #2, zeroed = all empty */
    m = (cl_map_hdr *)cl_gc_deref(h);
    m->index = nindex;
    m->icap = new_icap;
    {
        int64_t *idx = (int64_t *)cl_gc_deref(m->index);
        char *ent = (char *)cl_gc_deref(m->entries);
        for (uint64_t i = 0; i < m->nent; i++) map_index_put(m, idx, ent, i);
    }
}

cl_map cl_map_new(uint32_t keysz, uint32_t valsz, int key_kind) {
    cl_handle h = cl_gc_alloc(sizeof(cl_map_hdr));   /* zeroed */
    cl_map_hdr *m = (cl_map_hdr *)cl_gc_deref(h);
    m->keysz  = keysz ? keysz : 1;
    m->valsz  = valsz;
    m->kind   = (key_kind == CL_MAP_STR) ? CL_MAP_STR : CL_MAP_SCALAR;
    m->stride = (uint32_t)(MAP_KEYOFF + round8(m->keysz) + round8(m->valsz));
    return h;
}

uint64_t cl_map_len(cl_map h) { return ((cl_map_hdr *)cl_gc_deref(h))->len; }

int cl_map_has(cl_map h, const void *key) {
    cl_map_hdr *m = (cl_map_hdr *)cl_gc_deref(h);
    return map_find(m, key) >= 0;
}

void *cl_map_get(cl_map h, const void *key) {
    cl_map_hdr *m = (cl_map_hdr *)cl_gc_deref(h);
    int64_t ei = map_find(m, key);
    if (ei < 0) cl_panic_cstr("map key not found");
    return map_entry(m, cl_gc_deref(m->entries), (uint64_t)ei) + map_valoff(m);
}

void cl_map_set(cl_map h, const void *key, const void *val) {
    cl_map_hdr *m = (cl_map_hdr *)cl_gc_deref(h);

    /* update in place if present */
    int64_t ei = map_find(m, key);
    if (ei >= 0) {
        char *e = map_entry(m, cl_gc_deref(m->entries), (uint64_t)ei);
        memcpy(e + map_valoff(m), val, m->valsz);
        return;
    }

    /* grow the index when load factor would exceed ~0.7, or first insert */
    if (m->icap == 0 || (m->len + 1) * 10u >= m->icap * 7u) {
        uint64_t nic = m->icap ? m->icap * 2 : 8;
        map_rehash(h, nic);                  /* allocates; m may be stale after */
        m = (cl_map_hdr *)cl_gc_deref(h);
    }
    /* ensure entry capacity (rehash may have right-sized it already) */
    if (m->nent >= m->ecap) {
        uint64_t nec = m->ecap ? m->ecap * 2 : 4;
        cl_handle ne = cl_gc_alloc(nec * m->stride);   /* old entries still in m->entries */
        m = (cl_map_hdr *)cl_gc_deref(h);
        if (m->entries != CL_NULL && m->nent)
            memcpy(cl_gc_deref(ne), cl_gc_deref(m->entries), m->nent * m->stride);
        m->entries = ne;
        m->ecap = nec;
    }

    /* append new entry, then index it */
    uint64_t ni = m->nent;
    char *e = map_entry(m, cl_gc_deref(m->entries), ni);
    *(uint64_t *)e = 1u;                                /* live */
    memcpy(e + MAP_KEYOFF, key, m->keysz);
    memcpy(e + map_valoff(m), val, m->valsz);
    map_index_put(m, (int64_t *)cl_gc_deref(m->index), (char *)cl_gc_deref(m->entries), ni);
    m->nent++;
    m->len++;
}

void cl_map_del(cl_map h, const void *key) {
    cl_map_hdr *m = (cl_map_hdr *)cl_gc_deref(h);
    if (m->icap == 0 || m->len == 0) return;
    char *ent = (char *)cl_gc_deref(m->entries);
    int64_t *idx = (int64_t *)cl_gc_deref(m->index);
    uint64_t mask = m->icap - 1;
    uint64_t hh = map_hash_key(m, key) & mask;
    for (uint64_t probe = 0; probe <= mask; probe++) {
        int64_t slot = idx[hh];
        if (slot == 0) return;               /* empty: absent */
        if (slot > 0) {
            char *e = map_entry(m, ent, (uint64_t)(slot - 1));
            if (*(uint64_t *)e == 1u && map_key_eq(m, e + MAP_KEYOFF, key)) {
                *(uint64_t *)e = 0u;         /* tombstone the entry */
                idx[hh] = -1;                /* deleted slot: probes continue past it */
                m->len--;
                return;
            }
        }
        hh = (hh + 1) & mask;
    }
}

cl_vec cl_map_keys(cl_map h) {
    cl_map_hdr *m = (cl_map_hdr *)cl_gc_deref(h);
    cl_vec out = cl_vec_new(m->keysz);
    cl_gc_push_root(&out, sizeof out);       /* protect across the push loop's allocations */
    /* re-deref after each potential allocation: m and entries may be stale */
    uint64_t n = ((cl_map_hdr *)cl_gc_deref(h))->nent;
    for (uint64_t i = 0; i < n; i++) {
        cl_map_hdr *mm = (cl_map_hdr *)cl_gc_deref(h);
        char *e = map_entry(mm, cl_gc_deref(mm->entries), i);
        if (*(uint64_t *)e == 1u) cl_vec_push(out, e + MAP_KEYOFF);
    }
    cl_gc_pop_roots(1);
    return out;
}
