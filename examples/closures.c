#include "cardinal_rt.h"

int32_t cl_closures__lambda1(cl_handle v___env, int32_t v_x);
cl_closure cl_closures__adder(int32_t v_n);
int32_t cl_closures__lambda2(cl_handle v___env);
int32_t cl_closures__lambda3(cl_handle v___env);
int32_t cl_closures__pair(void);
int32_t cl_closures__lambda5(cl_handle v___env);
cl_closure cl_closures__lambda4(cl_handle v___env, int32_t v_b);
int32_t cl_closures__outer(int32_t v_a);
int32_t cl_closures__main(void);

int32_t cl_closures__lambda1(cl_handle v___env, int32_t v_x) {
    cl_handle t0 = {0};
    int32_t t1;
    int32_t t2;
    cl_gc_push_root(&t0, sizeof(t0));
    cl_gc_push_root(&v___env, sizeof(v___env));
    t0 = ((cl_handle*)cl_gc_deref(v___env))[0];
    t1 = *(int32_t*)cl_gc_deref(t0);
    t2 = (int32_t)((v_x) + (t1));
    cl_gc_pop_roots(2);
    return t2;
}

cl_closure cl_closures__adder(int32_t v_n) {
    cl_handle t0 = {0};
    cl_handle t1 = {0};
    cl_closure t2 = {0};
    cl_closure v_f_1 = {0};
    cl_gc_push_root(&t0, sizeof(t0));
    cl_gc_push_root(&t1, sizeof(t1));
    cl_gc_push_root(&t2, sizeof(t2));
    cl_gc_push_root(&v_f_1, sizeof(v_f_1));
    t0 = cl_gc_alloc(sizeof(int32_t));
    (*(int32_t*)cl_gc_deref(t0)) = v_n;
    t1 = cl_gc_alloc(1 * sizeof(cl_handle));
    ((cl_handle*)cl_gc_deref(t1))[0] = (cl_handle)(t0);
    t2 = (cl_closure){(void*)&cl_closures__lambda1, (cl_handle)(t1)};
    v_f_1 = t2;
    cl_gc_pop_roots(4);
    return v_f_1;
}

int32_t cl_closures__lambda2(cl_handle v___env) {
    cl_handle t0 = {0};
    int32_t t1;
    int32_t t2;
    int32_t t3;
    cl_gc_push_root(&t0, sizeof(t0));
    cl_gc_push_root(&v___env, sizeof(v___env));
    t0 = ((cl_handle*)cl_gc_deref(v___env))[0];
    t1 = *(int32_t*)cl_gc_deref(t0);
    t2 = (int32_t)((t1) + (((int32_t)1)));
    (*(int32_t*)cl_gc_deref(t0)) = t2;
    t3 = *(int32_t*)cl_gc_deref(t0);
    cl_gc_pop_roots(2);
    return t3;
}

int32_t cl_closures__lambda3(cl_handle v___env) {
    cl_handle t0 = {0};
    int32_t t1;
    cl_gc_push_root(&t0, sizeof(t0));
    cl_gc_push_root(&v___env, sizeof(v___env));
    t0 = ((cl_handle*)cl_gc_deref(v___env))[0];
    t1 = *(int32_t*)cl_gc_deref(t0);
    cl_gc_pop_roots(2);
    return t1;
}

int32_t cl_closures__pair(void) {
    cl_handle t0 = {0};
    cl_handle t1 = {0};
    cl_closure t2 = {0};
    cl_handle t3 = {0};
    cl_closure t4 = {0};
    int32_t t5;
    int32_t t6;
    int32_t t7;
    int32_t t8;
    cl_closure v_inc_1 = {0};
    cl_closure v_get_2 = {0};
    cl_gc_push_root(&t0, sizeof(t0));
    cl_gc_push_root(&t1, sizeof(t1));
    cl_gc_push_root(&t2, sizeof(t2));
    cl_gc_push_root(&t3, sizeof(t3));
    cl_gc_push_root(&t4, sizeof(t4));
    cl_gc_push_root(&v_inc_1, sizeof(v_inc_1));
    cl_gc_push_root(&v_get_2, sizeof(v_get_2));
    t0 = cl_gc_alloc(sizeof(int32_t));
    (*(int32_t*)cl_gc_deref(t0)) = ((int32_t)0);
    t1 = cl_gc_alloc(1 * sizeof(cl_handle));
    ((cl_handle*)cl_gc_deref(t1))[0] = (cl_handle)(t0);
    t2 = (cl_closure){(void*)&cl_closures__lambda2, (cl_handle)(t1)};
    v_inc_1 = t2;
    t3 = cl_gc_alloc(1 * sizeof(cl_handle));
    ((cl_handle*)cl_gc_deref(t3))[0] = (cl_handle)(t0);
    t4 = (cl_closure){(void*)&cl_closures__lambda3, (cl_handle)(t3)};
    v_get_2 = t4;
    t5 = ((int32_t(*)(cl_handle))(v_inc_1).fn)((v_inc_1).env);
    cl_print_i64(t5);
    cl_print_nl();
    t6 = ((int32_t(*)(cl_handle))(v_inc_1).fn)((v_inc_1).env);
    cl_print_i64(t6);
    cl_print_nl();
    t7 = ((int32_t(*)(cl_handle))(v_get_2).fn)((v_get_2).env);
    cl_print_i64(t7);
    cl_print_nl();
    t8 = *(int32_t*)cl_gc_deref(t0);
    cl_gc_pop_roots(7);
    return t8;
}

int32_t cl_closures__lambda5(cl_handle v___env) {
    cl_handle t0 = {0};
    cl_handle t1 = {0};
    int32_t t2;
    int32_t t3;
    int32_t t4;
    cl_gc_push_root(&t0, sizeof(t0));
    cl_gc_push_root(&t1, sizeof(t1));
    cl_gc_push_root(&v___env, sizeof(v___env));
    t0 = ((cl_handle*)cl_gc_deref(v___env))[0];
    t1 = ((cl_handle*)cl_gc_deref(v___env))[1];
    t2 = *(int32_t*)cl_gc_deref(t0);
    t3 = *(int32_t*)cl_gc_deref(t1);
    t4 = (int32_t)((t2) + (t3));
    cl_gc_pop_roots(3);
    return t4;
}

cl_closure cl_closures__lambda4(cl_handle v___env, int32_t v_b) {
    cl_handle t0 = {0};
    cl_handle t1 = {0};
    cl_handle t2 = {0};
    cl_closure t3 = {0};
    cl_closure v_inner_1 = {0};
    cl_gc_push_root(&t0, sizeof(t0));
    cl_gc_push_root(&t1, sizeof(t1));
    cl_gc_push_root(&t2, sizeof(t2));
    cl_gc_push_root(&t3, sizeof(t3));
    cl_gc_push_root(&v_inner_1, sizeof(v_inner_1));
    cl_gc_push_root(&v___env, sizeof(v___env));
    t0 = ((cl_handle*)cl_gc_deref(v___env))[0];
    t1 = cl_gc_alloc(sizeof(int32_t));
    (*(int32_t*)cl_gc_deref(t1)) = v_b;
    t2 = cl_gc_alloc(2 * sizeof(cl_handle));
    ((cl_handle*)cl_gc_deref(t2))[0] = (cl_handle)(t0);
    ((cl_handle*)cl_gc_deref(t2))[1] = (cl_handle)(t1);
    t3 = (cl_closure){(void*)&cl_closures__lambda5, (cl_handle)(t2)};
    v_inner_1 = t3;
    cl_gc_pop_roots(6);
    return v_inner_1;
}

int32_t cl_closures__outer(int32_t v_a) {
    cl_handle t0 = {0};
    cl_handle t1 = {0};
    cl_closure t2 = {0};
    cl_closure t3 = {0};
    int32_t t4;
    cl_closure v_mk_1 = {0};
    cl_closure v_g_2 = {0};
    cl_gc_push_root(&t0, sizeof(t0));
    cl_gc_push_root(&t1, sizeof(t1));
    cl_gc_push_root(&t2, sizeof(t2));
    cl_gc_push_root(&t3, sizeof(t3));
    cl_gc_push_root(&v_mk_1, sizeof(v_mk_1));
    cl_gc_push_root(&v_g_2, sizeof(v_g_2));
    t0 = cl_gc_alloc(sizeof(int32_t));
    (*(int32_t*)cl_gc_deref(t0)) = v_a;
    t1 = cl_gc_alloc(1 * sizeof(cl_handle));
    ((cl_handle*)cl_gc_deref(t1))[0] = (cl_handle)(t0);
    t2 = (cl_closure){(void*)&cl_closures__lambda4, (cl_handle)(t1)};
    v_mk_1 = t2;
    t3 = ((cl_closure(*)(cl_handle, int32_t))(v_mk_1).fn)((v_mk_1).env, ((int32_t)10));
    v_g_2 = t3;
    t4 = ((int32_t(*)(cl_handle))(v_g_2).fn)((v_g_2).env);
    cl_gc_pop_roots(6);
    return t4;
}

int32_t cl_closures__main(void) {
    cl_closure t0 = {0};
    int32_t t1;
    int32_t t2;
    int32_t t3;
    int32_t t4;
    cl_closure v_add5_1 = {0};
    cl_gc_push_root(&t0, sizeof(t0));
    cl_gc_push_root(&v_add5_1, sizeof(v_add5_1));
    t0 = cl_closures__adder(((int32_t)5));
    v_add5_1 = t0;
    t1 = ((int32_t(*)(cl_handle, int32_t))(v_add5_1).fn)((v_add5_1).env, ((int32_t)10));
    cl_print_i64(t1);
    cl_print_nl();
    t2 = ((int32_t(*)(cl_handle, int32_t))(v_add5_1).fn)((v_add5_1).env, ((int32_t)100));
    cl_print_i64(t2);
    cl_print_nl();
    t3 = cl_closures__pair();
    cl_print_i64(t3);
    cl_print_nl();
    t4 = cl_closures__outer(((int32_t)7));
    cl_print_i64(t4);
    cl_print_nl();
    cl_gc_pop_roots(2);
    return ((int32_t)0);
}

int main(void) { int b; cl_gc_init(&b); return (int)cl_closures__main(); }
