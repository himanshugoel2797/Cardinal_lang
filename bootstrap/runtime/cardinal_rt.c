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

/* A str is a handle to this object; the UTF-8 bytes follow inline. */
typedef struct { uint64_t nbytes; } cl_str_hdr;
#define CL_STR_HDR sizeof(cl_str_hdr)

/* Bytes of a string + its byte length. Re-call after any allocation. */
static const char *str_bytes(cl_str s, uint64_t *nbytes) {
    cl_str_hdr *h = (cl_str_hdr *)cl_gc_deref(s);
    if (nbytes) *nbytes = h->nbytes;
    return (const char *)h + CL_STR_HDR;
}

/* Allocate an nbytes-long managed string; returns the handle and (via out) a
 * pointer to its (uninitialised) byte region. Single allocation: the caller may
 * fill the bytes before any further alloc. */
static cl_str str_alloc(uint64_t nbytes, char **bytes_out) {
    cl_handle h = cl_gc_alloc(CL_STR_HDR + nbytes);
    cl_str_hdr *hd = (cl_str_hdr *)cl_gc_deref(h);
    hd->nbytes = nbytes;
    *bytes_out = (char *)hd + CL_STR_HDR;
    return h;
}

cl_str cl_str_from_utf8(const char *bytes, uint64_t nbytes) {
    char *dst;
    cl_str s = str_alloc(nbytes, &dst);
    if (nbytes) memcpy(dst, bytes, nbytes);
    return s;
}

uint64_t cl_str_len(cl_str s) {
    uint64_t nbytes;
    const char *b = str_bytes(s, &nbytes);
    uint64_t n = 0;
    for (uint64_t i = 0; i < nbytes; i++)
        if (((unsigned char)b[i] & 0xC0) != 0x80) n++;   /* count UTF-8 leads */
    return n;
}

void cl_panic(cl_str msg) {
    uint64_t nbytes;
    const char *b = str_bytes(msg, &nbytes);
    fprintf(stderr, "panic: %.*s\n", (int)nbytes, b);
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
void cl_print_str(cl_str s)   { uint64_t n; const char *b = str_bytes(s, &n); fwrite(b, 1, n, stdout); }
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
        cl_str s; memcpy(&s, key, sizeof s);   /* key bytes hold a str handle */
        uint64_t n; const char *b = str_bytes(s, &n);
        return fnv1a(b, n);
    }
    return fnv1a(key, m->keysz);
}

static int map_key_eq(const cl_map_hdr *m, const void *a, const void *b) {
    if (m->kind == CL_MAP_STR) {
        cl_str x, y; memcpy(&x, a, sizeof x); memcpy(&y, b, sizeof y);
        uint64_t nx, ny;
        const char *bx = str_bytes(x, &nx);
        const char *by = str_bytes(y, &ny);
        return nx == ny && (nx == 0 || memcmp(bx, by, nx) == 0);
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

/* ===================================================================== *
 *  Heap strings: builtins (DESIGN.md §5.3). A str is a managed handle to
 *  { u64 nbytes; UTF-8 bytes }. All indexing is by Unicode codepoint, to
 *  match the interpreter (a runtime string there is a Python str). The
 *  collector is non-moving, so a pointer from cl_gc_deref stays valid across
 *  a later allocation — but we re-deref str arguments after any alloc for
 *  clarity. String arguments are kept alive by the caller's roots.
 * ===================================================================== */

/* UTF-8: bytes for codepoint cp written to buf (>=4 bytes); returns byte count. */
static int utf8_encode(uint32_t cp, char *buf) {
    if (cp < 0x80) { buf[0] = (char)cp; return 1; }
    if (cp < 0x800) {
        buf[0] = (char)(0xC0 | (cp >> 6));
        buf[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    if (cp < 0x10000) {
        buf[0] = (char)(0xE0 | (cp >> 12));
        buf[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        buf[2] = (char)(0x80 | (cp & 0x3F));
        return 3;
    }
    buf[0] = (char)(0xF0 | (cp >> 18));
    buf[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
    buf[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
    buf[3] = (char)(0x80 | (cp & 0x3F));
    return 4;
}

/* Print the UTF-8 encoding of a codepoint (used by aggregate display). */
void cl_print_char(int32_t cp) {
    char b[4];
    int n = utf8_encode((uint32_t)cp, b);
    fwrite(b, 1, (size_t)n, stdout);
}

/* Decode one codepoint at b[i..nbytes); advance *i past it. */
static uint32_t utf8_decode(const char *b, uint64_t nbytes, uint64_t *i) {
    unsigned char c = (unsigned char)b[*i];
    uint32_t cp; int extra;
    if (c < 0x80)      { cp = c;          extra = 0; }
    else if (c < 0xE0) { cp = c & 0x1F;   extra = 1; }
    else if (c < 0xF0) { cp = c & 0x0F;   extra = 2; }
    else               { cp = c & 0x07;   extra = 3; }
    (*i)++;
    for (int k = 0; k < extra && *i < nbytes; k++, (*i)++)
        cp = (cp << 6) | ((unsigned char)b[*i] & 0x3F);
    return cp;
}

cl_str cl_strings__concat(cl_str a, cl_str b) {
    uint64_t na, nb;
    str_bytes(a, &na);
    str_bytes(b, &nb);
    char *dst;
    cl_str s = str_alloc(na + nb, &dst);        /* may collect; a,b caller-rooted */
    memcpy(dst,      str_bytes(a, &na), na);    /* re-deref a,b after alloc */
    memcpy(dst + na, str_bytes(b, &nb), nb);
    return s;
}

cl_str cl_strings__substr(cl_str s, uint64_t start, uint64_t count) {
    uint64_t nbytes;
    const char *b = str_bytes(s, &nbytes);
    uint64_t end = start + count;
    if (end < start) end = (uint64_t)-1;        /* overflow -> "to the end" */
    /* Byte offsets of codepoint #start (lo) and #end (hi). Python slice clamps:
     * an index past the end maps to the end (nbytes); never panics. */
    uint64_t lo = nbytes, hi = nbytes;
    if (start == 0) lo = 0;
    if (end == 0)   hi = 0;
    uint64_t cp = 0, i = 0;
    while (i < nbytes) {
        utf8_decode(b, nbytes, &i);             /* i now = byte offset after cp+1 codepoints */
        cp++;
        if (cp == start) lo = i;
        if (cp == end)   hi = i;
    }
    if (hi < lo) hi = lo;
    uint64_t n = hi - lo;
    char *dst;
    cl_str out = str_alloc(n, &dst);            /* may collect; s caller-rooted */
    if (n) memcpy(dst, str_bytes(s, &nbytes) + lo, n);
    return out;
}

cl_array cl_strings__chars(cl_str s) {
    uint64_t nbytes;
    const char *b = str_bytes(s, &nbytes);
    uint64_t ncp = 0;
    for (uint64_t i = 0; i < nbytes; i++)
        if (((unsigned char)b[i] & 0xC0) != 0x80) ncp++;
    cl_array a = cl_array_new(sizeof(int32_t), ncp);   /* alloc; s caller-rooted */
    b = str_bytes(s, &nbytes);                          /* re-deref after alloc */
    int32_t *out = (int32_t *)cl_gc_deref(a.data);
    uint64_t i = 0, k = 0;
    while (i < nbytes) out[k++] = (int32_t)utf8_decode(b, nbytes, &i);
    return a;
}

cl_str cl_strings__from_char(int32_t cp) {
    char buf[4];
    int n = utf8_encode((uint32_t)cp, buf);
    return cl_str_from_utf8(buf, (uint64_t)n);
}

bool cl_strings__eq(cl_str a, cl_str b) {
    uint64_t na, nb;
    const char *ba = str_bytes(a, &na);
    const char *bb = str_bytes(b, &nb);
    return na == nb && (na == 0 || memcmp(ba, bb, na) == 0);
}

uint32_t cl_convert__ord(int32_t ch) { return (uint32_t)ch; }
int32_t  cl_convert__chr(uint32_t v) { return (int32_t)v; }

cl_str cl_convert__int_to_str(int64_t v) {
    char buf[24];
    int n = snprintf(buf, sizeof buf, "%lld", (long long)v);
    return cl_str_from_utf8(buf, (uint64_t)n);
}

cl_str cl_u64_to_str(uint64_t v) {
    char buf[24];
    int n = snprintf(buf, sizeof buf, "%llu", (unsigned long long)v);
    return cl_str_from_utf8(buf, (uint64_t)n);
}

cl_str cl_f64_to_str(double v) {
    char buf[32];
    int n = snprintf(buf, sizeof buf, "%g", v);
    return cl_str_from_utf8(buf, (uint64_t)n);
}

cl_str cl_bool_to_str(int v) {
    return v ? cl_str_from_utf8("true", 4) : cl_str_from_utf8("false", 5);
}

int64_t cl_convert__str_to_int(cl_str s) {
    uint64_t nbytes;
    const char *b = str_bytes(s, &nbytes);
    /* Python int(): optional surrounding whitespace, optional sign, base-10. */
    uint64_t i = 0, j = nbytes;
    while (i < j && (b[i] == ' ' || b[i] == '\t' || b[i] == '\n' || b[i] == '\r')) i++;
    while (j > i && (b[j-1] == ' ' || b[j-1] == '\t' || b[j-1] == '\n' || b[j-1] == '\r')) j--;
    int neg = 0;
    if (i < j && (b[i] == '+' || b[i] == '-')) { neg = (b[i] == '-'); i++; }
    if (i >= j) cl_panic_cstr("str_to_int: not an integer");
    int64_t acc = 0;
    for (; i < j; i++) {
        if (b[i] < '0' || b[i] > '9') cl_panic_cstr("str_to_int: not an integer");
        acc = acc * 10 + (b[i] - '0');
    }
    return neg ? -acc : acc;
}

/* String-literal interning: a literal's C-string pointer is stable, so map it to
 * a managed string held forever in a permanently-rooted map. Emitted code calls
 * cl_strlit("...") inline; this guarantees a stable, already-rooted handle (no
 * un-rooted fresh allocation per evaluation). */
static cl_map g_strlit_intern = CL_NULL;

cl_str cl_strlit_n(const char *bytes, uint64_t nbytes) {
    uint64_t key = (uint64_t)(uintptr_t)bytes;
    if (g_strlit_intern == CL_NULL) {
        g_strlit_intern = cl_map_new(sizeof(uint64_t), sizeof(cl_str), CL_MAP_SCALAR);
        /* PIN, not push_root: cl_strlit is called mid-frame, and a LIFO push here
         * would be popped by the enclosing frame's cl_gc_pop_roots. */
        cl_gc_pin(&g_strlit_intern, sizeof g_strlit_intern);
    }
    if (cl_map_has(g_strlit_intern, &key))
        return *(cl_str *)cl_map_get(g_strlit_intern, &key);
    cl_str s = cl_str_from_utf8(bytes, nbytes);
    cl_gc_push_root(&s, sizeof s);              /* protect across map_set's alloc */
    cl_map_set(g_strlit_intern, &key, &s);      /* now also rooted via the map */
    cl_gc_pop_roots(1);
    return s;
}

cl_str cl_strlit(const char *utf8_cstr) {
    return cl_strlit_n(utf8_cstr, (uint64_t)strlen(utf8_cstr));
}

/* --------------------------------------------------------------------------- *
 * fs:: / sys:: builtins — the support the self-hosted compiler needs to read its
 * own source files and command-line arguments. Names match the backend's
 * cl_<mod>__<fn> mangling. (Bootstrap-grade: blocking stdio, no async.)
 * --------------------------------------------------------------------------- */

/* Copy a managed str into a fresh NUL-terminated C buffer (caller frees). */
static char *cl_str_cstr(cl_str s) {
    uint64_t n;
    const char *b = str_bytes(s, &n);
    char *p = (char *)malloc(n + 1);
    if (!p) { fprintf(stderr, "panic: out of memory\n"); exit(101); }
    if (n) memcpy(p, b, n);
    p[n] = '\0';
    return p;
}

/* fs::read_file(path) -> str : the whole file's bytes as a managed string. */
cl_str cl_fs__read_file(cl_str path) {
    char *p = cl_str_cstr(path);
    FILE *f = fopen(p, "rb");
    if (!f) { fprintf(stderr, "panic: read_file: %s\n", p); free(p); exit(101); }
    size_t cap = 4096, len = 0;
    char *buf = (char *)malloc(cap);
    if (!buf) { fprintf(stderr, "panic: out of memory\n"); exit(101); }
    char tmp[4096];
    size_t got;
    while ((got = fread(tmp, 1, sizeof tmp, f)) > 0) {
        if (len + got > cap) {
            while (len + got > cap) cap *= 2;
            buf = (char *)realloc(buf, cap);
            if (!buf) { fprintf(stderr, "panic: out of memory\n"); exit(101); }
        }
        memcpy(buf + len, tmp, got);
        len += got;
    }
    fclose(f);
    cl_str s = cl_str_from_utf8(buf, (uint64_t)len);
    free(buf);
    free(p);
    return s;
}

/* fs::exists(path) -> bool : true if the path exists (file or directory). */
bool cl_fs__exists(cl_str path) {
    char *p = cl_str_cstr(path);
    FILE *f = fopen(p, "rb");
    bool ok = (f != NULL);
    if (f) fclose(f);
    free(p);
    return ok;
}

/* fs::write_file(path, contents) -> unit : write contents verbatim (the full byte
 * length, so embedded NULs are preserved), truncating any existing file. */
void cl_fs__write_file(cl_str path, cl_str contents) {
    char *p = cl_str_cstr(path);
    FILE *f = fopen(p, "wb");
    if (!f) { fprintf(stderr, "panic: write_file: %s\n", p); free(p); exit(101); }
    uint64_t n = 0;
    const char *b = str_bytes(contents, &n);
    if (n > 0) fwrite(b, 1, n, f);
    fclose(f);
    free(p);
}

/* A closure for `func(str) -> unit` / `func(bool) -> unit` is a {fn, env} pair;
 * the code pointer takes the env handle as its leading argument. */
typedef void (*cl_cb_str)(cl_handle env, cl_str s);
typedef void (*cl_cb_bool)(cl_handle env, bool b);

/* fs::read_file_cb(path, cb) -> unit : read the file, then call cb(contents). The
 * fresh string is rooted across the callback (which may allocate / trigger GC). */
void cl_fs__read_file_cb(cl_str path, cl_closure cb) {
    cl_str contents = cl_fs__read_file(path);
    cl_gc_push_root(&contents, sizeof contents);
    ((cl_cb_str)cb.fn)(cb.env, contents);
    cl_gc_pop_roots(1);
}

/* fs::write_file_cb(path, contents, cb) -> unit : write the file, then call cb(ok)
 * with whether the write succeeded (no panic on failure). */
void cl_fs__write_file_cb(cl_str path, cl_str contents, cl_closure cb) {
    char *p = cl_str_cstr(path);
    bool ok = true;
    FILE *f = fopen(p, "wb");
    if (!f) {
        ok = false;
    } else {
        uint64_t n = 0;
        const char *b = str_bytes(contents, &n);
        if (n > 0 && fwrite(b, 1, n, f) != n) ok = false;
        fclose(f);
    }
    free(p);
    ((cl_cb_bool)cb.fn)(cb.env, ok);
}

/* Program arguments (set from main's argv; arg[0] is the first arg AFTER the
 * program name, matching the interpreter's sys::args). */
static int    g_argc = 0;
static char **g_argv = NULL;
void cl_sys_set_args(int argc, char **argv) { g_argc = argc; g_argv = argv; }

/* sys::args() -> {str} : the program arguments (excludes the program name). */
cl_vec cl_sys__args(void) {
    cl_vec v = cl_vec_new(sizeof(cl_str));
    cl_gc_push_root(&v, sizeof v);                  /* survive per-arg allocs + grows */
    for (int i = 1; i < g_argc; i++) {
        cl_str s = cl_str_from_utf8(g_argv[i], (uint64_t)strlen(g_argv[i]));
        cl_gc_push_root(&s, sizeof s);              /* survive cl_vec_push's grow */
        cl_vec_push(v, &s);
        cl_gc_pop_roots(1);
    }
    cl_gc_pop_roots(1);
    return v;
}
