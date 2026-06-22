/* Standalone test for the Cardinal GC (handle-table mark-and-sweep) using the
 * shadow-stack rooting API — the same mechanism the compiler emits. Precise
 * rooting makes the counts exact (no conservative-scan lag). */
#include "cardinal_gc.h"
#include <stdio.h>
#include <stdint.h>

static cl_handle make_node(cl_handle next) {
    cl_handle h = cl_gc_alloc(sizeof(cl_handle));
    *(cl_handle *)cl_gc_deref(h) = next;
    return h;
}
static void set_next(cl_handle node, cl_handle next) {
    *(cl_handle *)cl_gc_deref(node) = next;
}

/* A global is not on the shadow stack, so an object referenced only here is
 * collectable — used to force a stale handle for the use-after-free check. */
static cl_handle g_only_ref;

int main(int argc, char **argv) {
    cl_gc_init(0);
    cl_gc_set_threshold((uint64_t)1 << 40);   /* manual collection only */

    if (argc > 1 && argv[1][0] == 'u') {
        g_only_ref = make_node(CL_NULL);
        cl_gc_collect();                       /* not rooted -> reclaimed */
        printf("deref of a freed handle should panic:\n");
        cl_gc_deref(g_only_ref);               /* stale -> panic, exit 101 */
        printf("ERROR: stale deref did not panic\n");
        return 1;
    }

    int ok = 1;

    /* (1) reachable chain c->b->a survives while rooted, freed when dropped */
    cl_handle root = make_node(make_node(make_node(CL_NULL)));
    cl_gc_push_root(&root, sizeof root);
    cl_gc_collect();
    printf("rooted chain:        live=%llu (want 3)\n", (unsigned long long)cl_gc_live_count());
    ok &= (cl_gc_live_count() == 3);
    root = CL_NULL;
    cl_gc_collect();
    printf("dropped chain:       live=%llu (want 0)\n", (unsigned long long)cl_gc_live_count());
    ok &= (cl_gc_live_count() == 0);
    cl_gc_pop_roots(1);

    /* (2) a rooted cycle survives (traced), and is reclaimed when dropped */
    cl_handle x = make_node(CL_NULL);
    cl_handle y = make_node(x);
    set_next(x, y);                            /* x <-> y */
    cl_gc_push_root(&x, sizeof x);
    cl_gc_collect();
    printf("rooted cycle:        live=%llu (want 2)\n", (unsigned long long)cl_gc_live_count());
    ok &= (cl_gc_live_count() == 2);
    x = CL_NULL; y = CL_NULL;
    cl_gc_collect();
    printf("dropped cycle:       live=%llu (want 0)\n", (unsigned long long)cl_gc_live_count());
    ok &= (cl_gc_live_count() == 0);
    cl_gc_pop_roots(1);

    /* (3) mass cyclic garbage (never rooted) is fully reclaimed */
    for (int i = 0; i < 1000; i++) {
        cl_handle a = make_node(CL_NULL);
        cl_handle b = make_node(a);
        set_next(a, b);
        (void)b;
    }
    cl_gc_collect();
    printf("1000 cyclic garbage: live=%llu (want 0)\n", (unsigned long long)cl_gc_live_count());
    ok &= (cl_gc_live_count() == 0);

    printf(ok ? "GC TEST: PASS\n" : "GC TEST: FAIL\n");
    return ok ? 0 : 1;
}
