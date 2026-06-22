#include "cardinal_rt.h"
#include <stdio.h>
#include <stdlib.h>

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
