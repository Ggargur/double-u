"""
WStr runtime — embedded C code for the hybrid SSO + arena string type.

This module exports a single constant `WSTR_RUNTIME_C` containing all the C
definitions needed by the generated code.  The string is pasted verbatim near
the top of every emitted `.c` file by `codegen_c.emit_c_program`.
"""

WSTR_RUNTIME_C = r"""
/* ── WStr runtime: SSO + arena string ─────────────────────────────────────── */

#define WSTR_SSO_CAP 30

/* ── Arena ─────────────────────────────────────────────────────────────────── */

typedef struct _WArenaBlock {
    char*                buf;
    long                 used;
    long                 cap;
    struct _WArenaBlock* next;
} _WArenaBlock;

typedef struct {
    _WArenaBlock* head;
    long          default_cap;
} _WArena;

static inline _WArenaBlock* _w_arena_block_new(long cap) {
    _WArenaBlock* b = (_WArenaBlock*)malloc(sizeof(_WArenaBlock));
    if (!b) {
        fprintf(stderr, "WStr arena: out of memory allocating block header\n");
        abort();
    }
    b->buf = (char*)malloc(cap);
    if (!b->buf) {
        fprintf(stderr, "WStr arena: out of memory allocating block buffer\n");
        abort();
    }
    b->used = 0;
    b->cap = cap;
    b->next = NULL;
    return b;
}

static inline _WArena* _w_arena_new(long cap) {
    if (cap < 64) cap = 64;
    _WArena* a = (_WArena*)malloc(sizeof(_WArena));
    if (!a) {
        fprintf(stderr, "WStr arena: out of memory allocating arena\n");
        abort();
    }
    a->default_cap = cap;
    a->head = _w_arena_block_new(cap);
    return a;
}

static inline void _w_arena_free(_WArena* a) {
    if (!a) return;
    _WArenaBlock* b = a->head;
    while (b) {
        _WArenaBlock* next = b->next;
        free(b->buf);
        free(b);
        b = next;
    }
    free(a);
}

static inline char* _w_arena_alloc(_WArena* a, long size) {
    if (size <= 0) size = 1;

    _WArenaBlock* h = a->head;
    if (!h || h->used + size > h->cap) {
        long new_cap = h ? h->cap : a->default_cap;
        while (new_cap < size) new_cap *= 2;
        _WArenaBlock* b = _w_arena_block_new(new_cap);
        b->next = h;
        a->head = b;
        h = b;
    }

    char* p = h->buf + h->used;
    h->used += size;
    return p;
}

/* ── WStr ──────────────────────────────────────────────────────────────────── */

typedef struct {
    long  len;
    char  sso[WSTR_SSO_CAP + 1];
    char* heap;          /* NULL  → data lives in sso              */
                         /* else  → points into arena or static    */
} WStr;

/* Macro: evaluates in caller's stack frame so SSO pointer stays valid. */
#define _wstr_data(s) ((s).heap ? (s).heap : (s).sso)

static inline long _wstr_len(WStr s) {
    return s.len;
}

static inline long _w_cstrlen(const char* s) {
    long n = 0;
    while (s[n] != '\0') n++;
    return n;
}

static inline void _w_memcpy(char* dst, const char* src, long n) {
    for (long i = 0; i < n; i++) dst[i] = src[i];
}

static inline bool _w_memeq(const char* a, const char* b, long n) {
    for (long i = 0; i < n; i++) {
        if (a[i] != b[i]) return false;
    }
    return true;
}

static inline WStr _wstr_from_lit(const char* s, long len) {
    WStr w;
    w.len = len;
    if (len <= WSTR_SSO_CAP) {
        _w_memcpy(w.sso, s, len);
        w.sso[len] = '\0';
        w.heap = NULL;
    } else {
        /* Point directly to the string literal (static storage). */
        w.sso[0] = '\0';
        w.heap = (char*)s;
    }
    return w;
}

static inline WStr _wstr_concat(_WArena* a, WStr x, WStr y) {
    WStr w;
    long total = x.len + y.len;
    w.len = total;
    const char* xd = _wstr_data(x);
    const char* yd = _wstr_data(y);
    if (total <= WSTR_SSO_CAP) {
        _w_memcpy(w.sso, xd, x.len);
        _w_memcpy(w.sso + x.len, yd, y.len);
        w.sso[total] = '\0';
        w.heap = NULL;
    } else {
        char* buf = _w_arena_alloc(a, total + 1);
        _w_memcpy(buf, xd, x.len);
        _w_memcpy(buf + x.len, yd, y.len);
        buf[total] = '\0';
        w.sso[0] = '\0';
        w.heap = buf;
    }
    return w;
}

static inline bool _wstr_eq(WStr a, WStr b) {
    if (a.len != b.len) return false;
    return _w_memeq(_wstr_data(a), _wstr_data(b), a.len);
}

static inline char _wstr_index(WStr s, long i) {
    return _wstr_data(s)[i];
}

static inline WStr _wstr_from_char(_WArena* a, char c) {
    (void)a;   /* always fits in SSO */
    WStr w;
    w.len = 1;
    w.sso[0] = c;
    w.sso[1] = '\0';
    w.heap = NULL;
    return w;
}

static inline WStr _wstr_from_snprintf(_WArena* a, const char* fmt, ...) {
    va_list ap, ap2;
    va_start(ap, fmt);
    va_copy(ap2, ap);

    /* Measure how many bytes we need. */
    int needed = vsnprintf(NULL, 0, fmt, ap);
    va_end(ap);

    WStr w;
    if (needed < 0) needed = 0;
    w.len = needed;
    if (needed <= WSTR_SSO_CAP) {
        vsnprintf(w.sso, WSTR_SSO_CAP + 1, fmt, ap2);
        w.heap = NULL;
    } else {
        char* buf = _w_arena_alloc(a, needed + 1);
        vsnprintf(buf, needed + 1, fmt, ap2);
        w.sso[0] = '\0';
        w.heap = buf;
    }
    va_end(ap2);
    return w;
}

/* ── end WStr runtime ─────────────────────────────────────────────────────── */
""".lstrip()
