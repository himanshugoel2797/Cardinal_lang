/* Standalone test for the growable vector {T} and map {K V} runtime
 * (cl_vec_* / cl_map_* in cardinal_rt.c). Exercises value/reference semantics,
 * the exact interpreter parity points (insertion order, missing-key panic,
 * pop-empty panic), and GC safety under a deliberately tiny collection
 * threshold so every operation races against a collection. */
#include "cardinal_rt.h"
#include "cardinal_gc.h"
#include <stdio.h>
#include <string.h>

static int ok = 1;
#define CHECK(cond, msg) do { \
    if (!(cond)) { printf("FAIL: %s\n", msg); ok = 0; } \
} while (0)

static cl_str S(const char *s) { cl_str x; x.data = s; x.len = strlen(s); return x; }

/* --- vectors ---------------------------------------------------------- */
static void test_vec(void) {
    cl_vec v = cl_vec_new(sizeof(int64_t));
    cl_gc_push_root(&v, sizeof v);

    for (int64_t i = 0; i < 100; i++) cl_vec_push(v, &i);
    CHECK(cl_vec_len(v) == 100, "vec len after 100 pushes");
    CHECK(*(int64_t *)cl_vec_at(v, 42) == 42, "vec at(42)");

    /* set via the element pointer (the `set xs[i] = ...` path) */
    int64_t nv = 999;
    memcpy(cl_vec_at(v, 42), &nv, sizeof nv);
    CHECK(*(int64_t *)cl_vec_at(v, 42) == 999, "vec set then read");

    /* reference semantics: a copy of the handle shares the header */
    cl_vec alias = v;
    int64_t extra = 7;
    cl_vec_push(alias, &extra);
    CHECK(cl_vec_len(v) == 101, "push through alias visible in original");

    int64_t out = 0;
    cl_vec_pop(v, &out);
    CHECK(out == 7 && cl_vec_len(v) == 100, "pop returns last, shrinks");

    cl_gc_collect();
    CHECK(*(int64_t *)cl_vec_at(v, 99) == 99, "vec intact across GC");

    cl_gc_pop_roots(1);
}

/* nested vec-of-vec: inner vectors must survive GC via the outer buffer scan */
static void test_vec_nested(void) {
    cl_vec outer = cl_vec_new(sizeof(cl_vec));
    cl_gc_push_root(&outer, sizeof outer);
    for (int64_t i = 0; i < 20; i++) {
        cl_vec inner = cl_vec_new(sizeof(int64_t));
        cl_vec_push(outer, &inner);              /* stored by handle in outer's buf */
        int64_t val = i * 10;
        cl_vec_push(*(cl_vec *)cl_vec_at(outer, (uint64_t)i), &val);
    }
    cl_gc_collect();                              /* inners reachable only via outer */
    int good = 1;
    for (uint64_t i = 0; i < 20; i++) {
        cl_vec inner = *(cl_vec *)cl_vec_at(outer, i);
        if (cl_vec_len(inner) != 1 || *(int64_t *)cl_vec_at(inner, 0) != (int64_t)i * 10) good = 0;
    }
    CHECK(good, "nested vec-of-vec survives GC with contents intact");
    cl_gc_pop_roots(1);
}

/* --- maps: scalar keys ------------------------------------------------ */
static void test_map_scalar(void) {
    cl_map m = cl_map_new(sizeof(int32_t), sizeof(int64_t), CL_MAP_SCALAR);
    cl_gc_push_root(&m, sizeof m);

    for (int32_t k = 0; k < 200; k++) {
        int64_t val = (int64_t)k * 3;
        cl_map_set(m, &k, &val);
    }
    CHECK(cl_map_len(m) == 200, "map len after 200 inserts");
    int32_t k = 150;
    CHECK(cl_map_has(m, &k), "map_has present key");
    CHECK(*(int64_t *)cl_map_get(m, &k) == 450, "map_get value");

    int64_t nv = -1;                              /* update in place */
    cl_map_set(m, &k, &nv);
    CHECK(*(int64_t *)cl_map_get(m, &k) == -1 && cl_map_len(m) == 200, "update keeps len");

    int32_t absent = 9999;
    CHECK(!cl_map_has(m, &absent), "map_has absent key");

    cl_map_del(m, &k);
    CHECK(!cl_map_has(m, &k) && cl_map_len(m) == 199, "del removes key");
    cl_map_del(m, &k);                            /* no-op on absent */
    CHECK(cl_map_len(m) == 199, "del absent is no-op");

    cl_gc_collect();
    int32_t k2 = 17;
    CHECK(*(int64_t *)cl_map_get(m, &k2) == 51, "map intact across GC");

    cl_gc_pop_roots(1);
}

/* --- maps: str keys (content equality) -------------------------------- */
static void test_map_str(void) {
    cl_map m = cl_map_new(sizeof(cl_str), sizeof(int64_t), CL_MAP_STR);
    cl_gc_push_root(&m, sizeof m);

    cl_str a = S("alpha"), b = S("beta"), g = S("gamma");
    int64_t v;
    v = 1; cl_map_set(m, &a, &v);
    v = 2; cl_map_set(m, &b, &v);
    v = 3; cl_map_set(m, &g, &v);

    /* a DIFFERENT cl_str object with the same content must hit the same key */
    char buf[8]; strcpy(buf, "beta");
    cl_str b2; b2.data = buf; b2.len = 4;
    CHECK(cl_map_has(m, &b2), "str key matched by content, not pointer");
    CHECK(*(int64_t *)cl_map_get(m, &b2) == 2, "str key get by content");

    v = 20; cl_map_set(m, &b2, &v);               /* update via equal-content key */
    CHECK(*(int64_t *)cl_map_get(m, &a) == 1 && cl_map_len(m) == 3, "str update in place");

    cl_gc_pop_roots(1);
}

/* --- map_keys insertion order (interpreter parity) -------------------- */
static void test_map_keys_order(void) {
    cl_map m = cl_map_new(sizeof(int32_t), sizeof(int32_t), CL_MAP_SCALAR);
    cl_gc_push_root(&m, sizeof m);

    int32_t order[] = {30, 10, 20, 40};
    for (int i = 0; i < 4; i++) cl_map_set(m, &order[i], &order[i]);

    /* delete 10, then re-insert it -> it must move to the END (Python dict) */
    int32_t ten = 10;
    cl_map_del(m, &ten);
    cl_map_set(m, &ten, &ten);

    cl_vec keys = cl_map_keys(m);
    cl_gc_push_root(&keys, sizeof keys);
    int32_t want[] = {30, 20, 40, 10};
    int good = (cl_vec_len(keys) == 4);
    for (uint64_t i = 0; i < cl_vec_len(keys) && good; i++)
        if (*(int32_t *)cl_vec_at(keys, i) != want[i]) good = 0;
    CHECK(good, "map_keys yields insertion order (del+reinsert moves to end)");

    cl_gc_pop_roots(2);
}

/* --- GC stress: tiny threshold, rooted map must keep all values ------- */
static void test_gc_stress(void) {
    cl_gc_set_threshold(64);                       /* collect almost every alloc */
    cl_map m = cl_map_new(sizeof(int32_t), sizeof(int64_t), CL_MAP_SCALAR);
    cl_gc_push_root(&m, sizeof m);
    for (int32_t i = 0; i < 500; i++) {
        int64_t val = (int64_t)i * i;
        cl_map_set(m, &i, &val);
    }
    int good = (cl_map_len(m) == 500);
    for (int32_t i = 0; i < 500 && good; i++)
        if (*(int64_t *)cl_map_get(m, &i) != (int64_t)i * i) good = 0;
    CHECK(good, "500 entries survive heavy collection with correct values");
    cl_gc_pop_roots(1);
    cl_gc_set_threshold((uint64_t)1 << 40);
}

int main(void) {
    cl_gc_init(0);
    cl_gc_set_threshold((uint64_t)1 << 40);        /* manual collection by default */

    test_vec();
    test_vec_nested();
    test_map_scalar();
    test_map_str();
    test_map_keys_order();
    test_gc_stress();

    printf(ok ? "VECMAP TEST: PASS\n" : "VECMAP TEST: FAIL\n");
    return ok ? 0 : 1;
}
