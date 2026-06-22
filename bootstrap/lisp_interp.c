#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>

typedef struct lisp_object lisp_object_t;
typedef struct lisp_env lisp_env_t;
typedef struct lisp_closure lisp_closure_t;
typedef struct lisp_linked_list lisp_linked_list_t;
typedef struct lisp_buffer lisp_buffer_t;
typedef struct func_def func_def_t;

struct lisp_env {
    lisp_env_t *parent; // Pointer to parent environment for scoping
    struct {
        char *name;
        lisp_object_t *value;
    } *bindings; // Array of variable bindings
    size_t binding_count; // Number of bindings in the current environment
};

struct lisp_closure {
    lisp_object_t *(*func)(size_t, lisp_object_t**); // Non-NULL for builtins; NULL for interpreted closures
    lisp_object_t *lambda_list; // Parameter list (interpreted closures only)
    lisp_object_t *body;        // Body forms as a LISP_LIST (interpreted closures only)
    lisp_env_t *env;            // Environment captured at definition time
};

struct lisp_linked_list {
    lisp_object_t *value; // Pointer to the value of the list element
    lisp_linked_list_t *next; // Pointer to the next element in the list
};

struct lisp_buffer {
    unsigned char *data; // Raw bytes (not NUL-terminated)
    size_t length;       // Bytes currently used
    size_t capacity;     // Bytes allocated
};

struct lisp_object {
    enum {
        LISP_NIL = 0, LISP_TRUE = 1, LISP_FALSE = 2,
        LISP_UNSIGNED = (1 << 24), LISP_SIGNED = (1 << 25), LISP_FLOAT = (1 << 26),
        LISP_UINT8 = (LISP_UNSIGNED | 1), LISP_UINT16 = (LISP_UNSIGNED | 2), LISP_UINT32 = (LISP_UNSIGNED | 4), LISP_UINT64 = (LISP_UNSIGNED | 8),
        LISP_INT8 = (LISP_SIGNED | 1), LISP_INT16 = (LISP_SIGNED | 2), LISP_INT32 = (LISP_SIGNED | 4), LISP_INT64 = (LISP_SIGNED | 8),
        LISP_FLOAT32 = (LISP_FLOAT | 4), LISP_FLOAT64 = (LISP_FLOAT | 8),
        LISP_NUMERIC = (LISP_UNSIGNED | LISP_SIGNED | LISP_FLOAT), // Mask for numeric types
        LISP_SYMBOL = (1 << 27),
        LISP_LIST = (1 << 28),
        LISP_CLOSURE = (1 << 29),
        LISP_STRING = (1 << 30),
        LISP_BUFFER = (1 << 23) // distinct free bit; 1<<31 would overflow int
    } type;
    union {
        uint64_t unsigned_value;
        int64_t signed_value;
        double float_value;
        char *symbol_value;
        char *string_value; // NUL-terminated, escapes already decoded
        lisp_closure_t *closure_value;
        lisp_linked_list_t *list_value; // Array of pointers to lisp_objects
        lisp_buffer_t *buffer_value;
    };
};
lisp_object_t LISP_NIL_OBJ = { .type = LISP_NIL };

// Signature shared by every built-in primitive.
typedef lisp_object_t *(*builtin_fn)(size_t argc, lisp_object_t **argv);

struct func_def {
    const char *name;
    builtin_fn func; // Function pointer for built-in functions
};
// The primitives and func_table[] are defined further down, after the object
// constructors and the writer they depend on.

lisp_object_t* create_numeric_object(uint64_t value) {
    lisp_object_t *obj = malloc(sizeof(lisp_object_t));
    obj->type = LISP_UNSIGNED | 8; // Assuming 64-bit unsigned for simplicity
    obj->unsigned_value = value;
    return obj; // Return a pointer to the created object
}

/* ------------------------------------------------------------------ *
 *  Reader (parser): source text -> lisp_object_t trees.
 *
 *  Every object is heap-allocated and referenced by pointer.  Lists are
 *  singly-linked (lisp_linked_list_t); the empty list "()" reads as
 *  LISP_NIL.  Error reporting is deliberately crude: malformed input
 *  prints to stderr and exits -- enough to bootstrap, no more.
 *
 *  Recognised syntax:
 *     nil                       -> LISP_NIL
 *     #t  #f                    -> LISP_TRUE / LISP_FALSE
 *     123  -7  #xFF #o17 #b101  -> LISP_INT64
 *     1.5  -0.25  6e3           -> LISP_FLOAT64
 *     foo  +  list->of-chars    -> LISP_SYMBOL
 *     "hi\n"                    -> LISP_STRING (escapes decoded)
 *     (a b c)                   -> LISP_LIST
 *     'x                        -> (quote x)
 *     ; comment to end of line
 * ------------------------------------------------------------------ */

/* ================================================================== *
 *  Garbage collector: conservative mark & sweep.
 *
 *  Every lisp_object_t, lisp_linked_list_t (list cell) and lisp_env_t is
 *  allocated with a hidden gc_header_t prefix and threaded onto one global
 *  list.  Roots are found *conservatively*: we scan the C stack, the
 *  callee-saved registers (spilled via setjmp), the root environment, and a
 *  stack of explicitly-registered pointer regions (the argv arrays, which
 *  live off the C stack).  Any scanned word that exactly equals a known
 *  allocation address is treated as a live root and traced precisely.
 *
 *  Satellites owned by exactly one object (symbol/string text, the closure
 *  and buffer structs, a buffer's data, an env's bindings array and name
 *  strings) are freed together with their owner during the sweep.
 * ================================================================== */

enum { GC_OBJ, GC_NODE, GC_ENV };

typedef struct gc_header gc_header_t;
struct gc_header {
    gc_header_t *next; // global list of all allocations
    size_t kind;       // GC_OBJ / GC_NODE / GC_ENV
    size_t mark;
};

static gc_header_t *gc_all = NULL;       // every live allocation
static size_t gc_count = 0;              // number of allocations
static size_t gc_threshold = 100000;     // collect once this many are live
static int gc_enabled = 0;
static int gc_stress = 0;                 // if set, collect on every allocation (testing)
static void *gc_stack_bottom = NULL;     // captured in main()
static lisp_env_t *gc_root_env = NULL;   // the global environment

// Membership set of allocation payload addresses (open addressing).
static void **gc_set = NULL;
static size_t gc_set_cap = 0, gc_set_len = 0;

// Explicitly-registered root regions (off-stack arrays of object pointers).
static struct gc_region { void **base; size_t count; } *gc_regions = NULL;
static size_t gc_region_count = 0, gc_region_cap = 0;

static size_t ptr_hash(void *p) {
    uintptr_t x = (uintptr_t)p;
    x *= 0x9E3779B97F4A7C15ull;
    return (size_t)(x >> 29);
}

static int gc_set_contains(void *p) {
    if (!gc_set_cap) return 0;
    size_t mask = gc_set_cap - 1, i = ptr_hash(p) & mask;
    for (;;) {
        void *e = gc_set[i];
        if (!e) return 0;
        if (e == p) return 1;
        i = (i + 1) & mask;
    }
}

static void gc_set_put(void *p) { // assumes capacity is sufficient
    size_t mask = gc_set_cap - 1, i = ptr_hash(p) & mask;
    while (gc_set[i]) { if (gc_set[i] == p) return; i = (i + 1) & mask; }
    gc_set[i] = p;
    gc_set_len++;
}

// Discard the set and allocate an empty one sized for `expected` entries.
static void gc_set_reset(size_t expected) {
    free(gc_set);
    size_t cap = 16;
    while (cap < (expected + 1) * 2) cap <<= 1;
    gc_set = calloc(cap, sizeof(void *));
    if (!gc_set) { perror("calloc"); exit(1); }
    gc_set_cap = cap;
    gc_set_len = 0;
}

static void gc_push_region(void **base, size_t count) {
    if (gc_region_count == gc_region_cap) {
        gc_region_cap = gc_region_cap ? gc_region_cap * 2 : 64;
        gc_regions = realloc(gc_regions, gc_region_cap * sizeof(*gc_regions));
        if (!gc_regions) { perror("realloc"); exit(1); }
    }
    gc_regions[gc_region_count].base = base;
    gc_regions[gc_region_count].count = count;
    gc_region_count++;
}
static void gc_pop_region(void) { if (gc_region_count) gc_region_count--; }

// Mark a payload pointer if it names a real allocation, then trace its refs.
static void gc_mark(void *payload) {
    if (!payload || !gc_set_contains(payload)) return;
    gc_header_t *h = (gc_header_t *)payload - 1;
    if (h->mark) return;
    h->mark = 1;
    switch (h->kind) {
        case GC_OBJ: {
            lisp_object_t *o = payload;
            if (o->type == LISP_LIST) gc_mark(o->list_value);
            else if (o->type == LISP_CLOSURE) {
                lisp_closure_t *c = o->closure_value;
                if (c) { gc_mark(c->lambda_list); gc_mark(c->body); gc_mark(c->env); }
            }
            break;
        }
        case GC_NODE: {
            // Iterate the spine so a long list doesn't recurse the C stack.
            lisp_linked_list_t *n = payload;
            for (;;) {
                gc_mark(n->value);
                lisp_linked_list_t *nx = n->next;
                if (!nx || !gc_set_contains(nx)) break;
                gc_header_t *hn = (gc_header_t *)nx - 1;
                if (hn->mark) break;
                hn->mark = 1;
                n = nx;
            }
            break;
        }
        case GC_ENV: {
            lisp_env_t *e = payload;
            for (size_t i = 0; i < e->binding_count; i++) gc_mark(e->bindings[i].value);
            gc_mark(e->parent);
            break;
        }
    }
}

// Free the satellite allocations a dying payload uniquely owns.
static void gc_free_payload(gc_header_t *h) {
    void *payload = (void *)(h + 1);
    if (h->kind == GC_OBJ) {
        lisp_object_t *o = payload;
        if (o->type == LISP_SYMBOL) free(o->symbol_value);
        else if (o->type == LISP_STRING) free(o->string_value);
        else if (o->type == LISP_CLOSURE) free(o->closure_value);
        else if (o->type == LISP_BUFFER && o->buffer_value) {
            free(o->buffer_value->data);
            free(o->buffer_value);
        }
    } else if (h->kind == GC_ENV) {
        lisp_env_t *e = payload;
        for (size_t i = 0; i < e->binding_count; i++) free(e->bindings[i].name);
        free(e->bindings);
    }
}

static void gc_scan_region(void *lo, void *hi) {
    void **p   = (void **)(((uintptr_t)lo + sizeof(void *) - 1) & ~(uintptr_t)(sizeof(void *) - 1));
    void **end = (void **)((uintptr_t)hi & ~(uintptr_t)(sizeof(void *) - 1)); // whole words only
    for (; p < end; p++) gc_mark(*p);
}

static void gc_collect(void) {
    jmp_buf regs;
    setjmp(regs);                 // spill callee-saved registers into this frame
    void *frame_top = &regs;      // everything from here up to the bottom is live stack

    for (gc_header_t *h = gc_all; h; h = h->next) h->mark = 0;

    gc_mark(gc_root_env);
    for (size_t r = 0; r < gc_region_count; r++)
        for (size_t i = 0; i < gc_regions[r].count; i++)
            gc_mark(gc_regions[r].base[i]);

    void *lo = frame_top, *hi = gc_stack_bottom;
    if (lo > hi) { void *t = lo; lo = hi; hi = t; }
    gc_scan_region(lo, hi);

    gc_header_t *survivors = NULL;
    size_t live = 0;
    for (gc_header_t *h = gc_all; h; ) {
        gc_header_t *next = h->next;
        if (h->mark) { h->next = survivors; survivors = h; live++; }
        else { gc_free_payload(h); free(h); }
        h = next;
    }
    gc_all = survivors;
    gc_count = live;

    gc_set_reset(live);
    for (gc_header_t *h = gc_all; h; h = h->next) gc_set_put((void *)(h + 1));

    gc_threshold = gc_stress ? 1 : (live * 2 > 100000 ? live * 2 : 100000);
}

static void *gc_alloc(size_t size, size_t kind) {
    if (gc_enabled && gc_count >= gc_threshold) gc_collect();
    if ((gc_set_len + 1) * 4 >= gc_set_cap * 3) {       // keep the set < 75% full
        gc_set_reset(gc_set_len * 2 + 16);
        for (gc_header_t *h = gc_all; h; h = h->next) gc_set_put((void *)(h + 1));
    }
    gc_header_t *h = calloc(1, sizeof(gc_header_t) + size);
    if (!h) { perror("calloc"); exit(1); }
    h->kind = kind;
    h->next = gc_all;
    gc_all = h;
    void *payload = (void *)(h + 1);
    gc_set_put(payload);
    gc_count++;
    return payload;
}

static void gc_init(void *stack_bottom) {
    gc_stack_bottom = stack_bottom;
    gc_enabled = 1;
    if (getenv("LISP_GC_STRESS")) { gc_stress = 1; gc_threshold = 1; }
}

static lisp_object_t *lisp_alloc(int type) {
    lisp_object_t *obj = gc_alloc(sizeof(lisp_object_t), GC_OBJ);
    obj->type = type;
    return obj;
}

static lisp_linked_list_t *alloc_node(void) {
    return gc_alloc(sizeof(lisp_linked_list_t), GC_NODE);
}

static lisp_env_t *alloc_env(lisp_env_t *parent) {
    lisp_env_t *e = gc_alloc(sizeof(lisp_env_t), GC_ENV);
    e->parent = parent;
    return e;
}

static lisp_object_t *make_symbol(const char *s, size_t len) {
    lisp_object_t *obj = lisp_alloc(LISP_SYMBOL);
    char *copy = malloc(len + 1);
    if (!copy) { perror("malloc"); exit(1); }
    memcpy(copy, s, len);
    copy[len] = '\0';
    obj->symbol_value = copy;
    return obj;
}

// Takes ownership of an already-decoded, NUL-terminated heap buffer.
static lisp_object_t *make_string(char *decoded) {
    lisp_object_t *obj = lisp_alloc(LISP_STRING);
    obj->string_value = decoded;
    return obj;
}

typedef struct {
    const char *src;
    size_t len;
    size_t pos;
} reader_t;

static int peek(reader_t *r) {
    return r->pos < r->len ? (unsigned char)r->src[r->pos] : -1;
}

// Whitespace and ';' line comments separate tokens but carry no value.
static void skip_atmosphere(reader_t *r) {
    for (;;) {
        int c = peek(r);
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v') {
            r->pos++;
        } else if (c == ';') {
            while ((c = peek(r)) != -1 && c != '\n') r->pos++;
        } else {
            return;
        }
    }
}

// Characters that terminate a token.
static int is_delim(int c) {
    return c == -1 || c == '(' || c == ')' || c == '\'' || c == ';' || c == '"' ||
           c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
}

// Turn a non-empty bare token into the most specific type that fits:
// nil, boolean, integer, float, or (falling through) a symbol.
static lisp_object_t *classify_token(const char *tok, size_t len) {
    if (len == 3 && memcmp(tok, "nil", 3) == 0) return lisp_alloc(LISP_NIL);
    if (len == 2 && tok[0] == '#') {
        if (tok[1] == 't') return lisp_alloc(LISP_TRUE);
        if (tok[1] == 'f') return lisp_alloc(LISP_FALSE);
    }

    // strtoll/strtod need a NUL-terminated string; make a scratch copy.
    char *buf = malloc(len + 1);
    if (!buf) { perror("malloc"); exit(1); }
    memcpy(buf, tok, len);
    buf[len] = '\0';

    const char *digits = buf;
    int base = 10;
    if (len > 2 && buf[0] == '#') {        // #x / #o / #b radix prefixes
        if      (buf[1] == 'x') { base = 16; digits = buf + 2; }
        else if (buf[1] == 'o') { base = 8;  digits = buf + 2; }
        else if (buf[1] == 'b') { base = 2;  digits = buf + 2; }
    }

    char *end;
    long long iv = strtoll(digits, &end, base);
    if (end != digits && *end == '\0') {
        free(buf);
        lisp_object_t *obj = lisp_alloc(LISP_INT64);
        obj->signed_value = iv;
        return obj;
    }
    if (base == 10) {                      // only plain decimal can be a float
        double dv = strtod(buf, &end);
        if (end != buf && *end == '\0') {
            free(buf);
            lisp_object_t *obj = lisp_alloc(LISP_FLOAT64);
            obj->float_value = dv;
            return obj;
        }
    }

    lisp_object_t *obj = make_symbol(buf, len);
    free(buf);
    return obj;
}

static lisp_object_t *read_expr(reader_t *r);   // mutually recursive with read_list

static lisp_object_t *read_atom(reader_t *r) {
    size_t start = r->pos;
    while (!is_delim(peek(r))) r->pos++;
    return classify_token(r->src + start, r->pos - start);
}

static lisp_object_t *read_list(reader_t *r) {
    r->pos++;                              // consume '('
    lisp_linked_list_t *head = NULL, *tail = NULL;
    for (;;) {
        skip_atmosphere(r);
        int c = peek(r);
        if (c == -1) { fprintf(stderr, "read: unterminated list\n"); exit(1); }
        if (c == ')') { r->pos++; break; }

        lisp_linked_list_t *node = alloc_node();
        node->value = read_expr(r);
        node->next = NULL;
        if (tail) tail->next = node; else head = node;   // append, preserving order
        tail = node;
    }
    if (!head) return lisp_alloc(LISP_NIL);              // () == nil
    lisp_object_t *list = lisp_alloc(LISP_LIST);
    list->list_value = head;
    return list;
}

// "..."  -> LISP_STRING, decoding \n \t \r \\ \" \0 escapes. The opening
// quote has not been consumed yet. Grows the output buffer as it goes.
static lisp_object_t *read_string(reader_t *r) {
    r->pos++;                              // consume opening '"'
    size_t cap = 16, n = 0;
    char *buf = malloc(cap);
    if (!buf) { perror("malloc"); exit(1); }
    for (;;) {
        int c = peek(r);
        if (c == -1) { fprintf(stderr, "read: unterminated string\n"); exit(1); }
        r->pos++;
        if (c == '"') break;
        if (c == '\\') {                   // escape sequence
            int e = peek(r);
            if (e == -1) { fprintf(stderr, "read: dangling escape in string\n"); exit(1); }
            r->pos++;
            switch (e) {
                case 'n':  c = '\n'; break;
                case 't':  c = '\t'; break;
                case 'r':  c = '\r'; break;
                case '0':  c = '\0'; break;
                case '\\': c = '\\'; break;
                case '"':  c = '"';  break;
                default:   c = e;    break;  // unknown escape: keep char verbatim
            }
        }
        if (n + 1 >= cap) {                // leave room for the trailing NUL
            cap *= 2;
            buf = realloc(buf, cap);
            if (!buf) { perror("realloc"); exit(1); }
        }
        buf[n++] = (char)c;
    }
    buf[n] = '\0';
    return make_string(buf);
}

// 'x  ==>  (quote x)
static lisp_object_t *read_quote(reader_t *r) {
    r->pos++;                              // consume '\''
    lisp_object_t *quoted = read_expr(r);
    if (!quoted) { fprintf(stderr, "read: ' with nothing to quote\n"); exit(1); }
    lisp_linked_list_t *q   = alloc_node();
    lisp_linked_list_t *arg = alloc_node();
    q->value = make_symbol("quote", 5);
    q->next  = arg;
    arg->value = quoted;
    arg->next  = NULL;
    lisp_object_t *list = lisp_alloc(LISP_LIST);
    list->list_value = q;
    return list;
}

// Read a single expression. Returns NULL at clean end of input.
static lisp_object_t *read_expr(reader_t *r) {
    skip_atmosphere(r);
    int c = peek(r);
    if (c == -1)   return NULL;
    if (c == '(')  return read_list(r);
    if (c == '"')  return read_string(r);
    if (c == '\'') return read_quote(r);
    if (c == ')')  { fprintf(stderr, "read: unexpected ')'\n"); exit(1); }
    return read_atom(r);
}

// Convenience entry point: read every top-level form and return them as one
// LISP_LIST (an implicit program body). The evaluator can instead call
// read_expr() in a loop directly if it prefers a form at a time.
lisp_object_t* lisp_parse(size_t code_len, const char *code) {
    reader_t r = { code, code_len, 0 };
    lisp_linked_list_t *head = NULL, *tail = NULL;
    for (;;) {
        lisp_object_t *form = read_expr(&r);
        if (!form) break;
        lisp_linked_list_t *node = alloc_node();
        node->value = form;
        node->next = NULL;
        if (tail) tail->next = node; else head = node;
        tail = node;
    }
    lisp_object_t *program = lisp_alloc(LISP_LIST);
    program->list_value = head;            // NULL head => empty program
    return program;
}

lisp_object_t *lisp_eval(lisp_object_t *code, lisp_env_t *env); // mutually recursive with apply

// Shared singletons so booleans/nil don't allocate on every use.
lisp_object_t LISP_TRUE_OBJ  = { .type = LISP_TRUE };
lisp_object_t LISP_FALSE_OBJ = { .type = LISP_FALSE };

static lisp_object_t *lisp_error(const char *msg) {
    fprintf(stderr, "Error: %s\n", msg);
    exit(1);
}

static char *dup_cstr(const char *s) {
    size_t n = strlen(s) + 1;
    char *p = malloc(n);
    if (!p) { perror("malloc"); exit(1); }
    memcpy(p, s, n);
    return p;
}

/* ---- value constructors & numeric helpers ---- */

static lisp_object_t *make_int(int64_t v)   { lisp_object_t *o = lisp_alloc(LISP_INT64);   o->signed_value = v; return o; }
static lisp_object_t *make_float(double v)  { lisp_object_t *o = lisp_alloc(LISP_FLOAT64); o->float_value = v;  return o; }
static lisp_object_t *make_bool(int b)      { return b ? &LISP_TRUE_OBJ : &LISP_FALSE_OBJ; }

// Wrap a chain of nodes as a LISP_LIST object (nil if the chain is empty).
static lisp_object_t *list_from_node(lisp_linked_list_t *node) {
    if (!node) return &LISP_NIL_OBJ;
    lisp_object_t *o = lisp_alloc(LISP_LIST);
    o->list_value = node;
    return o;
}

static int is_number(lisp_object_t *o) { return o && (o->type & LISP_NUMERIC); }

static double num_as_double(lisp_object_t *o) {
    if (o->type & LISP_FLOAT)    return o->float_value;
    if (o->type & LISP_UNSIGNED) return (double)o->unsigned_value;
    return (double)o->signed_value;
}

static int64_t num_as_int(lisp_object_t *o) {
    if (o->type & LISP_FLOAT)    return (int64_t)o->float_value;
    if (o->type & LISP_UNSIGNED) return (int64_t)o->unsigned_value;
    return o->signed_value;
}

// Truthiness: everything is true except nil, #f, and any numeric zero.
static int is_truthy(lisp_object_t *o) {
    if (!o) return 0;
    if (o->type == LISP_NIL || o->type == LISP_FALSE) return 0;
    if (o->type & LISP_FLOAT)    return o->float_value != 0.0;
    if (o->type & LISP_UNSIGNED) return o->unsigned_value != 0;
    if (o->type & LISP_SIGNED)   return o->signed_value != 0;
    return 1;
}

/* ---- environment mutation ---- */

// Define (or overwrite) a binding in this exact frame.
static void env_define(lisp_env_t *env, const char *name, lisp_object_t *value) {
    for (size_t i = 0; i < env->binding_count; i++) {
        if (strcmp(env->bindings[i].name, name) == 0) {
            env->bindings[i].value = value;
            return;
        }
    }
    env->bindings = realloc(env->bindings, (env->binding_count + 1) * sizeof(*env->bindings));
    if (!env->bindings) { perror("realloc"); exit(1); }
    env->bindings[env->binding_count].name = dup_cstr(name);
    env->bindings[env->binding_count].value = value;
    env->binding_count++;
}

// Assign to an existing binding anywhere up the chain. Returns 0 if unbound.
static int env_set(lisp_env_t *env, const char *name, lisp_object_t *value) {
    for (lisp_env_t *e = env; e; e = e->parent) {
        for (size_t i = 0; i < e->binding_count; i++) {
            if (strcmp(e->bindings[i].name, name) == 0) {
                e->bindings[i].value = value;
                return 1;
            }
        }
    }
    return 0;
}

/* ---- closures ---- */

static lisp_object_t *make_builtin(builtin_fn f) {
    lisp_object_t *o = lisp_alloc(LISP_CLOSURE);
    lisp_closure_t *c = calloc(1, sizeof(*c));
    if (!c) { perror("calloc"); exit(1); }
    c->func = f;
    o->closure_value = c;
    return o;
}

static lisp_object_t *make_closure(lisp_object_t *params, lisp_object_t *body, lisp_env_t *env) {
    lisp_object_t *o = lisp_alloc(LISP_CLOSURE);
    lisp_closure_t *c = calloc(1, sizeof(*c));
    if (!c) { perror("calloc"); exit(1); }
    c->func = NULL;
    c->lambda_list = params;
    c->body = body;
    c->env = env;
    o->closure_value = c;
    return o;
}

/* ---- printer / writer ---- */

// readable != 0 quotes strings and escapes them (write); 0 prints raw (display).
static void lisp_write(FILE *out, lisp_object_t *o, int readable) {
    if (!o) { fputs("nil", out); return; }
    switch (o->type) {
        case LISP_NIL:   fputs("nil", out); return;
        case LISP_TRUE:  fputs("#t",  out); return;
        case LISP_FALSE: fputs("#f",  out); return;
        case LISP_SYMBOL: fputs(o->symbol_value, out); return;
        case LISP_STRING:
            if (!readable) { fputs(o->string_value, out); return; }
            fputc('"', out);
            for (const char *p = o->string_value; *p; p++) {
                switch (*p) {
                    case '"':  fputs("\\\"", out); break;
                    case '\\': fputs("\\\\", out); break;
                    case '\n': fputs("\\n", out);  break;
                    case '\t': fputs("\\t", out);  break;
                    case '\r': fputs("\\r", out);  break;
                    default:   fputc(*p, out);
                }
            }
            fputc('"', out);
            return;
        case LISP_LIST:
            fputc('(', out);
            for (lisp_linked_list_t *n = o->list_value; n; n = n->next) {
                lisp_write(out, n->value, readable);
                if (n->next) fputc(' ', out);
            }
            fputc(')', out);
            return;
        case LISP_CLOSURE:
            fputs(o->closure_value->func ? "#<builtin>" : "#<closure>", out);
            return;
        case LISP_BUFFER:
            fprintf(out, "#<buffer length=%zu>", o->buffer_value->length);
            return;
        default:
            if (o->type & LISP_FLOAT)         fprintf(out, "%g", o->float_value);
            else if (o->type & LISP_UNSIGNED) fprintf(out, "%llu", (unsigned long long)o->unsigned_value);
            else if (o->type & LISP_SIGNED)   fprintf(out, "%lld", (long long)o->signed_value);
            else                              fprintf(out, "#<type %d>", o->type);
    }
}

/* ---- structural equality ---- */

static int lisp_equal(lisp_object_t *a, lisp_object_t *b) {
    if (a == b) return 1;
    if (!a || !b) return 0;
    if (is_number(a) && is_number(b)) return num_as_double(a) == num_as_double(b);
    if (a->type != b->type) return 0;
    switch (a->type) {
        case LISP_NIL: case LISP_TRUE: case LISP_FALSE: return 1;
        case LISP_SYMBOL: return strcmp(a->symbol_value, b->symbol_value) == 0;
        case LISP_STRING: return strcmp(a->string_value, b->string_value) == 0;
        case LISP_LIST: {
            lisp_linked_list_t *x = a->list_value, *y = b->list_value;
            for (; x && y; x = x->next, y = y->next)
                if (!lisp_equal(x->value, y->value)) return 0;
            return x == NULL && y == NULL;
        }
        default: return 0;
    }
}

/* ---- primitives ---- */

static void require_numbers(size_t argc, lisp_object_t **argv, const char *who) {
    for (size_t i = 0; i < argc; i++)
        if (!is_number(argv[i])) { fprintf(stderr, "Error: %s: expected a number\n", who); exit(1); }
}

static int any_float(size_t argc, lisp_object_t **argv) {
    for (size_t i = 0; i < argc; i++) if (argv[i]->type & LISP_FLOAT) return 1;
    return 0;
}

static lisp_object_t *prim_add(size_t argc, lisp_object_t **argv) {
    require_numbers(argc, argv, "+");
    if (any_float(argc, argv)) {
        double s = 0; for (size_t i = 0; i < argc; i++) s += num_as_double(argv[i]);
        return make_float(s);
    }
    int64_t s = 0; for (size_t i = 0; i < argc; i++) s += num_as_int(argv[i]);
    return make_int(s);
}

static lisp_object_t *prim_mul(size_t argc, lisp_object_t **argv) {
    require_numbers(argc, argv, "*");
    if (any_float(argc, argv)) {
        double p = 1; for (size_t i = 0; i < argc; i++) p *= num_as_double(argv[i]);
        return make_float(p);
    }
    int64_t p = 1; for (size_t i = 0; i < argc; i++) p *= num_as_int(argv[i]);
    return make_int(p);
}

static lisp_object_t *prim_sub(size_t argc, lisp_object_t **argv) {
    require_numbers(argc, argv, "-");
    if (argc == 0) return make_int(0);
    if (any_float(argc, argv)) {
        double s = num_as_double(argv[0]);
        if (argc == 1) return make_float(-s);
        for (size_t i = 1; i < argc; i++) s -= num_as_double(argv[i]);
        return make_float(s);
    }
    int64_t s = num_as_int(argv[0]);
    if (argc == 1) return make_int(-s);
    for (size_t i = 1; i < argc; i++) s -= num_as_int(argv[i]);
    return make_int(s);
}

static lisp_object_t *prim_div(size_t argc, lisp_object_t **argv) {
    require_numbers(argc, argv, "/");
    if (argc == 0) return lisp_error("/: needs at least one argument");
    if (any_float(argc, argv) || argc == 1) {
        double q = num_as_double(argv[0]);
        if (argc == 1) { if (q == 0) return lisp_error("/: division by zero"); return make_float(1.0 / q); }
        for (size_t i = 1; i < argc; i++) {
            double d = num_as_double(argv[i]);
            if (d == 0) return lisp_error("/: division by zero");
            q /= d;
        }
        return make_float(q);
    }
    int64_t q = num_as_int(argv[0]);
    for (size_t i = 1; i < argc; i++) {
        int64_t d = num_as_int(argv[i]);
        if (d == 0) return lisp_error("/: division by zero");
        q /= d;
    }
    return make_int(q);
}

// Generic numeric chain comparison: 0 = ; 1 < ; 2 > ; 3 <= ; 4 >=
static lisp_object_t *num_compare(size_t argc, lisp_object_t **argv, int op, const char *who) {
    require_numbers(argc, argv, who);
    for (size_t i = 1; i < argc; i++) {
        double a = num_as_double(argv[i - 1]), b = num_as_double(argv[i]);
        int ok = (op == 0) ? a == b : (op == 1) ? a < b : (op == 2) ? a > b
               : (op == 3) ? a <= b : a >= b;
        if (!ok) return make_bool(0);
    }
    return make_bool(1);
}

static lisp_object_t *prim_lt(size_t c, lisp_object_t **v)     { return num_compare(c, v, 1, "<"); }
static lisp_object_t *prim_gt(size_t c, lisp_object_t **v)     { return num_compare(c, v, 2, ">"); }
static lisp_object_t *prim_le(size_t c, lisp_object_t **v)     { return num_compare(c, v, 3, "<="); }
static lisp_object_t *prim_ge(size_t c, lisp_object_t **v)     { return num_compare(c, v, 4, ">="); }

static lisp_object_t *prim_equal(size_t argc, lisp_object_t **argv) {
    if (argc < 2) return make_bool(1);
    for (size_t i = 1; i < argc; i++)
        if (!lisp_equal(argv[i - 1], argv[i])) return make_bool(0);
    return make_bool(1);
}

static lisp_object_t *prim_not(size_t argc, lisp_object_t **argv) {
    if (argc != 1) return lisp_error("not: expects one argument");
    return make_bool(!is_truthy(argv[0]));
}

static lisp_object_t *prim_cons(size_t argc, lisp_object_t **argv) {
    if (argc != 2) return lisp_error("cons: expects two arguments");
    lisp_linked_list_t *node = alloc_node();
    node->value = argv[0];
    lisp_object_t *rest = argv[1];
    if (rest->type == LISP_NIL)       node->next = NULL;
    else if (rest->type == LISP_LIST) node->next = rest->list_value;
    else return lisp_error("cons: second argument must be a list or nil");
    return list_from_node(node);
}

static lisp_object_t *prim_car(size_t argc, lisp_object_t **argv) {
    if (argc != 1 || argv[0]->type != LISP_LIST || !argv[0]->list_value)
        return lisp_error("car: expects a non-empty list");
    return argv[0]->list_value->value;
}

static lisp_object_t *prim_cdr(size_t argc, lisp_object_t **argv) {
    if (argc != 1 || argv[0]->type != LISP_LIST || !argv[0]->list_value)
        return lisp_error("cdr: expects a non-empty list");
    return list_from_node(argv[0]->list_value->next);
}

static lisp_object_t *prim_list(size_t argc, lisp_object_t **argv) {
    lisp_linked_list_t *head = NULL, *tail = NULL;
    for (size_t i = 0; i < argc; i++) {
        lisp_linked_list_t *node = alloc_node();
        node->value = argv[i];
        node->next = NULL;
        if (tail) tail->next = node; else head = node;
        tail = node;
    }
    return list_from_node(head);
}

static lisp_object_t *prim_nullp(size_t argc, lisp_object_t **argv) {
    if (argc != 1) return lisp_error("null?: expects one argument");
    return make_bool(argv[0]->type == LISP_NIL);
}

static lisp_object_t *prim_pairp(size_t argc, lisp_object_t **argv) {
    if (argc != 1) return lisp_error("pair?: expects one argument");
    return make_bool(argv[0]->type == LISP_LIST && argv[0]->list_value != NULL);
}

static lisp_object_t *prim_display(size_t argc, lisp_object_t **argv) {
    for (size_t i = 0; i < argc; i++) lisp_write(stdout, argv[i], 0);
    return &LISP_NIL_OBJ;
}

static lisp_object_t *prim_write(size_t argc, lisp_object_t **argv) {
    for (size_t i = 0; i < argc; i++) lisp_write(stdout, argv[i], 1);
    return &LISP_NIL_OBJ;
}

static lisp_object_t *prim_newline(size_t argc, lisp_object_t **argv) {
    (void)argc; (void)argv;
    fputc('\n', stdout);
    return &LISP_NIL_OBJ;
}

static lisp_object_t *prim_print(size_t argc, lisp_object_t **argv) {
    for (size_t i = 0; i < argc; i++) lisp_write(stdout, argv[i], 0);
    fputc('\n', stdout);
    return &LISP_NIL_OBJ;
}

/* ---- file I/O ---- */

// (read-file path) -> whole file as a string. Errors out if it can't open.
static lisp_object_t *prim_read_file(size_t argc, lisp_object_t **argv) {
    if (argc != 1 || argv[0]->type != LISP_STRING)
        return lisp_error("read-file: expects a filename string");
    FILE *f = fopen(argv[0]->string_value, "rb");
    if (!f) { fprintf(stderr, "Error: read-file: cannot open '%s'\n", argv[0]->string_value); exit(1); }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz < 0) { fclose(f); return lisp_error("read-file: ftell failed"); }
    char *buf = malloc((size_t)sz + 1);
    if (!buf) { perror("malloc"); exit(1); }
    size_t n = fread(buf, 1, (size_t)sz, f);
    buf[n] = '\0';
    fclose(f);
    return make_string(buf);
}

static lisp_object_t *write_or_append(size_t argc, lisp_object_t **argv, const char *mode, const char *who) {
    if (argc != 2 || argv[0]->type != LISP_STRING || argv[1]->type != LISP_STRING) {
        fprintf(stderr, "Error: %s: expects (filename string)\n", who);
        exit(1);
    }
    FILE *f = fopen(argv[0]->string_value, mode);
    if (!f) { fprintf(stderr, "Error: %s: cannot open '%s'\n", who, argv[0]->string_value); exit(1); }
    fputs(argv[1]->string_value, f);
    fclose(f);
    return &LISP_NIL_OBJ;
}

// (write-file path str) truncates; (append-file path str) appends.
static lisp_object_t *prim_write_file(size_t argc, lisp_object_t **argv) {
    return write_or_append(argc, argv, "wb", "write-file");
}
static lisp_object_t *prim_append_file(size_t argc, lisp_object_t **argv) {
    return write_or_append(argc, argv, "ab", "append-file");
}

// (parse str) -> list of top-level forms, reusing the C reader.
static lisp_object_t *prim_parse(size_t argc, lisp_object_t **argv) {
    if (argc != 1 || argv[0]->type != LISP_STRING)
        return lisp_error("parse: expects a string");
    return lisp_parse(strlen(argv[0]->string_value), argv[0]->string_value);
}

/* ---- string conversions (construction is now done with byte buffers) ---- */

static lisp_object_t *prim_string_length(size_t argc, lisp_object_t **argv) {
    if (argc != 1 || argv[0]->type != LISP_STRING)
        return lisp_error("string-length: expects a string");
    return make_int((int64_t)strlen(argv[0]->string_value));
}

// (number->string n) -> textual form of an integer or float.
static lisp_object_t *prim_number_to_string(size_t argc, lisp_object_t **argv) {
    if (argc != 1 || !is_number(argv[0])) return lisp_error("number->string: expects a number");
    char tmp[64];
    lisp_object_t *o = argv[0];
    if (o->type & LISP_FLOAT)         snprintf(tmp, sizeof tmp, "%g", o->float_value);
    else if (o->type & LISP_UNSIGNED) snprintf(tmp, sizeof tmp, "%llu", (unsigned long long)o->unsigned_value);
    else                              snprintf(tmp, sizeof tmp, "%lld", (long long)o->signed_value);
    return make_string(dup_cstr(tmp));
}

// (string->number s) -> integer/float, or nil if it isn't exactly one number.
// Reuses the reader so #x/#o/#b/float/negative syntaxes match source code.
static lisp_object_t *prim_string_to_number(size_t argc, lisp_object_t **argv) {
    if (argc != 1 || argv[0]->type != LISP_STRING)
        return lisp_error("string->number: expects a string");
    lisp_object_t *forms = lisp_parse(strlen(argv[0]->string_value), argv[0]->string_value);
    lisp_linked_list_t *f = forms->type == LISP_LIST ? forms->list_value : NULL;
    if (f && !f->next && is_number(f->value)) return f->value;  // exactly one numeric form
    return &LISP_NIL_OBJ;
}

static lisp_object_t *prim_symbol_to_string(size_t argc, lisp_object_t **argv) {
    if (argc != 1 || argv[0]->type != LISP_SYMBOL)
        return lisp_error("symbol->string: expects a symbol");
    return make_string(dup_cstr(argv[0]->symbol_value));
}

static lisp_object_t *prim_string_to_symbol(size_t argc, lisp_object_t **argv) {
    if (argc != 1 || argv[0]->type != LISP_STRING)
        return lisp_error("string->symbol: expects a string");
    return make_symbol(argv[0]->string_value, strlen(argv[0]->string_value));
}

/* ---- byte buffers (growable, mutable; for accumulating output) ---- */

static lisp_object_t *make_buffer(void) {
    lisp_object_t *o = lisp_alloc(LISP_BUFFER);
    lisp_buffer_t *b = calloc(1, sizeof(*b));
    if (!b) { perror("calloc"); exit(1); }
    o->buffer_value = b;
    return o;
}

static void buffer_put(lisp_buffer_t *b, const void *src, size_t n) {
    if (b->length + n > b->capacity) {
        size_t cap = b->capacity ? b->capacity : 16;
        while (cap < b->length + n) cap *= 2;
        b->data = realloc(b->data, cap);
        if (!b->data) { perror("realloc"); exit(1); }
        b->capacity = cap;
    }
    memcpy(b->data + b->length, src, n);
    b->length += n;
}

static lisp_buffer_t *as_buffer(lisp_object_t *o, const char *who) {
    if (!o || o->type != LISP_BUFFER) { fprintf(stderr, "Error: %s: expects a buffer\n", who); exit(1); }
    return o->buffer_value;
}

static lisp_object_t *prim_make_buffer(size_t argc, lisp_object_t **argv) {
    (void)argv;
    if (argc != 0) return lisp_error("make-buffer: takes no arguments");
    return make_buffer();
}

static lisp_object_t *prim_buffer_p(size_t argc, lisp_object_t **argv) {
    if (argc != 1) return lisp_error("buffer?: expects one argument");
    return make_bool(argv[0]->type == LISP_BUFFER);
}

static lisp_object_t *prim_buffer_length(size_t argc, lisp_object_t **argv) {
    if (argc != 1) return lisp_error("buffer-length: expects (buffer)");
    return make_int((int64_t)as_buffer(argv[0], "buffer-length")->length);
}

// (buffer-append buf x) text-oriented append: string/symbol -> bytes,
// number -> decimal text, buffer -> its bytes. Returns the buffer.
static lisp_object_t *prim_buffer_append(size_t argc, lisp_object_t **argv) {
    if (argc != 2) return lisp_error("buffer-append: expects (buffer value)");
    lisp_buffer_t *b = as_buffer(argv[0], "buffer-append");
    lisp_object_t *x = argv[1];
    if (x->type == LISP_STRING)      buffer_put(b, x->string_value, strlen(x->string_value));
    else if (x->type == LISP_SYMBOL) buffer_put(b, x->symbol_value, strlen(x->symbol_value));
    else if (x->type == LISP_BUFFER) buffer_put(b, x->buffer_value->data, x->buffer_value->length);
    else if (is_number(x)) {
        char tmp[64];
        if (x->type & LISP_FLOAT)         snprintf(tmp, sizeof tmp, "%g", x->float_value);
        else if (x->type & LISP_UNSIGNED) snprintf(tmp, sizeof tmp, "%llu", (unsigned long long)x->unsigned_value);
        else                              snprintf(tmp, sizeof tmp, "%lld", (long long)x->signed_value);
        buffer_put(b, tmp, strlen(tmp));
    } else return lisp_error("buffer-append: unsupported value type");
    return argv[0];
}

// (buffer-append-string buf s) raw string bytes (no NUL).
static lisp_object_t *prim_buffer_append_string(size_t argc, lisp_object_t **argv) {
    if (argc != 2 || argv[1]->type != LISP_STRING) return lisp_error("buffer-append-string: expects (buffer string)");
    lisp_buffer_t *b = as_buffer(argv[0], "buffer-append-string");
    buffer_put(b, argv[1]->string_value, strlen(argv[1]->string_value));
    return argv[0];
}

// (buffer-append-byte buf n) appends the low 8 bits of n.
static lisp_object_t *prim_buffer_append_byte(size_t argc, lisp_object_t **argv) {
    if (argc != 2 || !is_number(argv[1])) return lisp_error("buffer-append-byte: expects (buffer number)");
    lisp_buffer_t *b = as_buffer(argv[0], "buffer-append-byte");
    unsigned char byte = (unsigned char)num_as_int(argv[1]);
    buffer_put(b, &byte, 1);
    return argv[0];
}

// (buffer-append-int buf value nbytes) little-endian raw integer, nbytes 1..8.
static lisp_object_t *prim_buffer_append_int(size_t argc, lisp_object_t **argv) {
    if (argc != 3 || !is_number(argv[1]) || !is_number(argv[2]))
        return lisp_error("buffer-append-int: expects (buffer value nbytes)");
    int64_t nbytes = num_as_int(argv[2]);
    if (nbytes < 1 || nbytes > 8) return lisp_error("buffer-append-int: nbytes must be 1..8");
    lisp_buffer_t *b = as_buffer(argv[0], "buffer-append-int");
    uint64_t v = (uint64_t)num_as_int(argv[1]);
    unsigned char bytes[8];
    for (int64_t i = 0; i < nbytes; i++) bytes[i] = (unsigned char)(v >> (8 * i)); // little-endian
    buffer_put(b, bytes, (size_t)nbytes);
    return argv[0];
}

static lisp_object_t *prim_buffer_ref(size_t argc, lisp_object_t **argv) {
    if (argc != 2 || !is_number(argv[1])) return lisp_error("buffer-ref: expects (buffer index)");
    lisp_buffer_t *b = as_buffer(argv[0], "buffer-ref");
    int64_t i = num_as_int(argv[1]);
    if (i < 0 || (size_t)i >= b->length) return lisp_error("buffer-ref: index out of range");
    return make_int(b->data[i]);
}

// (buffer-set! buf index byte) overwrite an existing byte (e.g. backpatching).
static lisp_object_t *prim_buffer_set(size_t argc, lisp_object_t **argv) {
    if (argc != 3 || !is_number(argv[1]) || !is_number(argv[2]))
        return lisp_error("buffer-set!: expects (buffer index byte)");
    lisp_buffer_t *b = as_buffer(argv[0], "buffer-set!");
    int64_t i = num_as_int(argv[1]);
    if (i < 0 || (size_t)i >= b->length) return lisp_error("buffer-set!: index out of range");
    b->data[i] = (unsigned char)num_as_int(argv[2]);
    return argv[0];
}

// (buffer->string buf) copy the bytes into a NUL-terminated string.
static lisp_object_t *prim_buffer_to_string(size_t argc, lisp_object_t **argv) {
    if (argc != 1) return lisp_error("buffer->string: expects (buffer)");
    lisp_buffer_t *b = as_buffer(argv[0], "buffer->string");
    char *buf = malloc(b->length + 1);
    if (!buf) { perror("malloc"); exit(1); }
    memcpy(buf, b->data, b->length);
    buf[b->length] = '\0';
    return make_string(buf);
}

// (buffer-write-file path buf) write the raw bytes to disk.
static lisp_object_t *prim_buffer_write_file(size_t argc, lisp_object_t **argv) {
    if (argc != 2 || argv[0]->type != LISP_STRING) return lisp_error("buffer-write-file: expects (path buffer)");
    lisp_buffer_t *b = as_buffer(argv[1], "buffer-write-file");
    FILE *f = fopen(argv[0]->string_value, "wb");
    if (!f) { fprintf(stderr, "Error: buffer-write-file: cannot open '%s'\n", argv[0]->string_value); exit(1); }
    if (b->length) fwrite(b->data, 1, b->length, f);
    fclose(f);
    return &LISP_NIL_OBJ;
}

func_def_t func_table[] = {
    {"+", prim_add},   {"add", prim_add},
    {"-", prim_sub},   {"subtract", prim_sub},
    {"*", prim_mul},   {"multiply", prim_mul},
    {"/", prim_div},   {"divide", prim_div},
    {"=", prim_equal}, {"<", prim_lt}, {">", prim_gt}, {"<=", prim_le}, {">=", prim_ge},
    {"cons", prim_cons}, {"car", prim_car}, {"cdr", prim_cdr}, {"list", prim_list},
    {"null?", prim_nullp}, {"pair?", prim_pairp},
    {"eq?", prim_equal}, {"equal?", prim_equal}, {"not", prim_not},
    {"print", prim_print}, {"display", prim_display},
    {"write", prim_write}, {"newline", prim_newline},
    {"read-file", prim_read_file}, {"write-file", prim_write_file},
    {"append-file", prim_append_file}, {"parse", prim_parse},
    {"string-length", prim_string_length},
    {"number->string", prim_number_to_string}, {"int->string", prim_number_to_string},
    {"string->number", prim_string_to_number},
    {"symbol->string", prim_symbol_to_string}, {"string->symbol", prim_string_to_symbol},
    {"make-buffer", prim_make_buffer}, {"buffer?", prim_buffer_p},
    {"buffer-length", prim_buffer_length}, {"buffer-append", prim_buffer_append},
    {"buffer-append-string", prim_buffer_append_string}, {"buffer-append-byte", prim_buffer_append_byte},
    {"buffer-append-int", prim_buffer_append_int},
    {"buffer-ref", prim_buffer_ref}, {"buffer-set!", prim_buffer_set},
    {"buffer->string", prim_buffer_to_string}, {"buffer-write-file", prim_buffer_write_file},
};
#define FUNC_TABLE_SIZE (sizeof(func_table) / sizeof(func_table[0]))

static void register_builtins(lisp_env_t *env) {
    for (size_t i = 0; i < FUNC_TABLE_SIZE; i++)
        env_define(env, func_table[i].name, make_builtin(func_table[i].func));
}

/* ---- apply & the special forms eval delegates to ---- */

// Build a fresh call frame for an interpreted closure, binding parameters to
// the evaluated arguments. Arity mismatches abort.
static lisp_env_t *closure_call_env(lisp_closure_t *c, size_t argc, lisp_object_t **argv) {
    lisp_env_t *call = alloc_env(c->env);
    lisp_linked_list_t *p = (c->lambda_list && c->lambda_list->type == LISP_LIST)
                          ? c->lambda_list->list_value : NULL;
    size_t i = 0;
    for (; p; p = p->next, i++) {
        if (i >= argc) lisp_error("too few arguments to closure");
        if (p->value->type != LISP_SYMBOL) lisp_error("parameter is not a symbol");
        env_define(call, p->value->symbol_value, argv[i]);
    }
    if (i < argc) lisp_error("too many arguments to closure");
    return call;
}

static lisp_object_t *eval_define(lisp_linked_list_t *a, lisp_env_t *env) {
    if (!a) return lisp_error("define: missing name");
    lisp_object_t *target = a->value;
    if (target->type == LISP_SYMBOL) {
        lisp_object_t *val = a->next ? lisp_eval(a->next->value, env) : &LISP_NIL_OBJ;
        env_define(env, target->symbol_value, val);
        return val;
    }
    if (target->type == LISP_LIST && target->list_value) {       // (define (f params...) body...)
        lisp_linked_list_t *sig = target->list_value;
        if (sig->value->type != LISP_SYMBOL) return lisp_error("define: function name must be a symbol");
        lisp_object_t *clo = make_closure(list_from_node(sig->next), list_from_node(a->next), env);
        env_define(env, sig->value->symbol_value, clo);
        return clo;
    }
    return lisp_error("define: bad target");
}

// Evaluator. Written as a loop rather than recursion: tail positions (the
// taken branch of `if`, the last form of `begin`/`let`/a closure body) rebind
// `code`/`env` and `continue` instead of calling lisp_eval again, so tail
// recursion runs in constant C-stack space. Non-tail sub-evaluations (the
// condition of `if`, every argument, all-but-last body forms) still recurse.
lisp_object_t *lisp_eval(lisp_object_t *code, lisp_env_t *env) {
    for (;;) {
        switch (code->type) {
            case LISP_NIL:
            case LISP_TRUE:
            case LISP_FALSE:
            case LISP_INT8:  case LISP_INT16:  case LISP_INT32:  case LISP_INT64:
            case LISP_UINT8: case LISP_UINT16: case LISP_UINT32: case LISP_UINT64:
            case LISP_FLOAT32: case LISP_FLOAT64:
            case LISP_STRING:
            case LISP_CLOSURE:
                return code; // Self-evaluating

            case LISP_SYMBOL: {
                const char *var_name = code->symbol_value;
                for (lisp_env_t *e = env; e; e = e->parent)
                    for (size_t i = 0; i < e->binding_count; i++)
                        if (strcmp(var_name, e->bindings[i].name) == 0)
                            return e->bindings[i].value;
                fprintf(stderr, "Error: unbound variable '%s'\n", var_name);
                exit(1);
            }

            case LISP_LIST: {
                lisp_linked_list_t *list = code->list_value;
                if (!list) return &LISP_NIL_OBJ;
                lisp_object_t *first = list->value;

                // Special forms first; they break the "evaluate every arg" rule.
                if (first->type == LISP_SYMBOL) {
                    const char *s = first->symbol_value;
                    if (strcmp(s, "quote") == 0)
                        return list->next ? list->next->value : &LISP_NIL_OBJ;
                    if (strcmp(s, "if") == 0) {
                        lisp_linked_list_t *a = list->next;
                        if (!a || !a->next) return lisp_error("if: needs a condition and a then-branch");
                        if (is_truthy(lisp_eval(a->value, env))) {
                            code = a->next->value;                 // tail: then-branch
                        } else if (a->next->next) {
                            code = a->next->next->value;           // tail: else-branch
                        } else {
                            return &LISP_NIL_OBJ;
                        }
                        continue;
                    }
                    if (strcmp(s, "define") == 0) return eval_define(list->next, env);
                    if (strcmp(s, "lambda") == 0 || strcmp(s, "fn") == 0) {
                        lisp_linked_list_t *a = list->next;
                        if (!a) return lisp_error("lambda: missing parameter list");
                        return make_closure(a->value, list_from_node(a->next), env);
                    }
                    if (strcmp(s, "begin") == 0) {
                        lisp_linked_list_t *f = list->next;
                        if (!f) return &LISP_NIL_OBJ;
                        while (f->next) { lisp_eval(f->value, env); f = f->next; }
                        code = f->value;                           // tail: last form
                        continue;
                    }
                    if (strcmp(s, "let") == 0) {
                        lisp_linked_list_t *a = list->next;
                        if (!a) return lisp_error("let: missing bindings");
                        lisp_env_t *scope = alloc_env(env);
                        lisp_object_t *binds = a->value;
                        lisp_linked_list_t *bl = (binds->type == LISP_LIST) ? binds->list_value : NULL;
                        for (; bl; bl = bl->next) {                 // each binding is (name value)
                            lisp_object_t *pair = bl->value;
                            if (pair->type != LISP_LIST || !pair->list_value || !pair->list_value->next)
                                return lisp_error("let: malformed binding");
                            lisp_object_t *name = pair->list_value->value;
                            if (name->type != LISP_SYMBOL) return lisp_error("let: binding name must be a symbol");
                            // Values evaluated in the OUTER env (parallel let).
                            env_define(scope, name->symbol_value,
                                       lisp_eval(pair->list_value->next->value, env));
                        }
                        lisp_linked_list_t *body = a->next;
                        if (!body) return &LISP_NIL_OBJ;
                        while (body->next) { lisp_eval(body->value, scope); body = body->next; }
                        env = scope; code = body->value;           // tail: last body form
                        continue;
                    }
                    if (strcmp(s, "set!") == 0) {
                        lisp_linked_list_t *a = list->next;
                        if (!a || !a->next || a->value->type != LISP_SYMBOL)
                            return lisp_error("set!: needs a symbol and a value");
                        lisp_object_t *val = lisp_eval(a->next->value, env);
                        if (!env_set(env, a->value->symbol_value, val))
                            return lisp_error("set!: unbound variable");
                        return val;
                    }
                }

                // Ordinary application: evaluate operator and every argument.
                lisp_object_t *op = lisp_eval(first, env);
                size_t argc = 0;
                for (lisp_linked_list_t *n = list->next; n; n = n->next) argc++;
                // calloc (zeroed) + register as a GC root region: argv lives off
                // the C stack, so the collector can't see it by scanning.
                lisp_object_t **argv = argc ? calloc(argc, sizeof(*argv)) : NULL;
                if (argc && !argv) { perror("calloc"); exit(1); }
                gc_push_region((void **)argv, argc);
                size_t i = 0;
                for (lisp_linked_list_t *n = list->next; n; n = n->next)
                    argv[i++] = lisp_eval(n->value, env);

                if (!op || op->type != LISP_CLOSURE) {
                    gc_pop_region(); free(argv);
                    return lisp_error("attempt to call a non-function");
                }
                lisp_closure_t *c = op->closure_value;
                if (c->func) {                                     // builtin: plain call
                    lisp_object_t *result = c->func(argc, argv);
                    gc_pop_region(); free(argv);
                    return result;
                }
                // interpreted closure: bind args, then tail-loop on the body
                lisp_env_t *call = closure_call_env(c, argc, argv);
                gc_pop_region(); free(argv);
                lisp_linked_list_t *body = (c->body && c->body->type == LISP_LIST)
                                         ? c->body->list_value : NULL;
                if (!body) return &LISP_NIL_OBJ;
                while (body->next) { lisp_eval(body->value, call); body = body->next; }
                env = call; code = body->value;                    // tail call
                continue;
            }

            default:
                fprintf(stderr, "Error: unknown object type %d\n", code->type);
                exit(1);
        }
    }
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <lisp_code>\n", argv[0]);
        return 1;
    }

    gc_init(&argc); // a local near the base of the stack marks the scan bottom

    const char *lisp_code_file = argv[1];
    FILE *file = fopen(lisp_code_file, "r");
    if (!file) {
        perror("Error opening file");
        return 1;
    }

    fseek(file, 0, SEEK_END);
    long file_size = ftell(file);
    fseek(file, 0, SEEK_SET);
    char *code = (char *)malloc(file_size + 1);
    if (!code) {
        perror("Memory allocation failed");
        fclose(file);
        return 1;
    }
    fread(code, 1, file_size, file);
    code[file_size] = '\0'; // Null-terminate the string

    // Parse the whole file into a list of top-level forms, then evaluate each
    // in a shared root environment. The parser copies every symbol/string it
    // needs, so freeing `code` afterwards is safe.
    lisp_object_t *program = lisp_parse((size_t)file_size, code);
    gc_push_region((void **)&program, 1); // root the AST explicitly (it may sit above the scan bottom)
    lisp_env_t *global_env = alloc_env(NULL);
    gc_root_env = global_env;              // keep the global env (and all it reaches) alive
    register_builtins(global_env);
    for (lisp_linked_list_t *form = program->list_value; form; form = form->next) {
        lisp_eval(form->value, global_env);
    }

    free(code);
    fclose(file);
    return 0;
}
