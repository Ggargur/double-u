// Runtime da double-u: objetos com contagem de referência, Text, Pending e o
// loop de ticks com a fila de pedidos da thread principal e o pool de workers
// (ADR 0004).
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include "runtime.h"

// ------------------------------------------------------------------ objetos
static void obj_free(Obj *o);
static W just(W x, W isptr);

W uu_alloc(W kind, W tag, W n, W mask) {
    size_t extra = (kind == K_TEXT) ? (size_t)n + 1 : (size_t)n * sizeof(W);
    Obj *o = (Obj *)malloc(sizeof(Obj) + extra);
    if (!o) { fputs("double-u: sem memória\n", stderr); exit(102); }
    o->rc = 1;
    o->kind = (uint32_t)kind;
    o->tag = (uint32_t)tag;
    o->n = (uint32_t)n;
    o->aux = 0;
    o->mask = (uint64_t)mask;
    return (W)o;
}

void uu_inc(W x) {
    Obj *o = (Obj *)x;
    if (o && o->rc >= 0) o->rc++;
}

void uu_dec(W x) {
    Obj *o = (Obj *)x;
    if (!o || o->rc < 0) return;
    if (--o->rc == 0) obj_free(o);
}

W uu_rc(W x) { return ((Obj *)x)->rc; }

static Obj nullaries[256];
W uu_nullary(W tag) {
    Obj *o = &nullaries[tag & 255];
    o->rc = -1;
    o->kind = K_DATA;
    o->tag = (uint32_t)tag;
    o->n = 0;
    return (W)o;
}

typedef struct Callback Callback;
typedef struct {
    int64_t rc;
    uint32_t kind, tag, n, aux;
    uint64_t mask;
    int state;      // 0 pendente, 1 resolvido
    int vptr;       // o valor é ponteiro?
    W value;
    Callback *cbs;
} Pending;

static void pending_free(Pending *p);

static void obj_free(Obj *o) {
    switch (o->kind) {
    case K_DATA:
    case K_CLOS:
        for (uint32_t i = 0; i < o->n && i < 64; i++)
            if (o->mask & (1ull << i)) uu_dec(o->f[i]);
        break;
    case K_PEND:
        pending_free((Pending *)o);
        return;
    case K_LIST:
        if (o->tag)
            for (uint32_t i = 0; i < o->n; i++) uu_dec(o->f[i]);
        break;
    default:
        break;
    }
    free(o);
}

// ------------------------------------------------------------------ Text
const char *uu_text_ptr(W t) { return (const char *)((Obj *)t)->f; }
W uu_text_len(W t) { return ((Obj *)t)->n; }

W uu_text_new(const char *s, size_t n) {
    W t = uu_alloc(K_TEXT, 0, (W)n, 0);
    memcpy(((Obj *)t)->f, s, n);
    ((char *)((Obj *)t)->f)[n] = 0;
    return t;
}

W uu_text_concat(W a, W b) {
    size_t na = uu_text_len(a), nb = uu_text_len(b);
    W t = uu_alloc(K_TEXT, 0, (W)(na + nb), 0);
    char *d = (char *)((Obj *)t)->f;
    memcpy(d, uu_text_ptr(a), na);
    memcpy(d + na, uu_text_ptr(b), nb);
    d[na + nb] = 0;
    uu_dec(a);
    uu_dec(b);
    return t;
}

W uu_text_eq_borrow(W a, W b) {
    return uu_text_len(a) == uu_text_len(b) && memcmp(uu_text_ptr(a), uu_text_ptr(b), uu_text_len(a)) == 0;
}

W uu_pow_i64(W a, W b) {
    W r = 1;
    if (b < 0) return a == 1 ? 1 : (a == -1 ? ((b & 1) ? -1 : 1) : 0);
    while (b) { if (b & 1) r *= a; a *= a; b >>= 1; }
    return r;
}

W uu_text_eq(W a, W b) {
    W r = uu_text_len(a) == uu_text_len(b) && memcmp(uu_text_ptr(a), uu_text_ptr(b), uu_text_len(a)) == 0;
    uu_dec(a);
    uu_dec(b);
    return r;
}

// ------------------------------------------------------------------ formatação
// spec: record com campos fill align width precision kind
static size_t utf8_len(const char *s, size_t n) {
    size_t c = 0;
    for (size_t i = 0; i < n; i++) if ((s[i] & 0xC0) != 0x80) c++;
    return c;
}

static W pad(const char *s, size_t n, W spec, int numeric) {
    Obj *sp = (Obj *)spec;
    W fill = sp->f[0], align = sp->f[1], width = sp->f[2];
    uu_dec(spec);
    size_t len = utf8_len(s, n);
    if (width <= 0 || (size_t)width <= len) return uu_text_new(s, n);
    size_t padn = (size_t)width - len;
    char fb[8];
    int fl = 1;
    if (fill < 0x80) fb[0] = (char)fill;
    else if (fill < 0x800) { fb[0] = (char)(0xC0 | (fill >> 6)); fb[1] = (char)(0x80 | (fill & 63)); fl = 2; }
    else { fb[0] = (char)(0xE0 | (fill >> 12)); fb[1] = (char)(0x80 | ((fill >> 6) & 63)); fb[2] = (char)(0x80 | (fill & 63)); fl = 3; }
    if (align == 0) align = numeric ? 2 : 1;
    size_t left = 0, right = 0;
    if (align == 1) right = padn;
    else if (align == 2) left = padn;
    else if (align == 3) { left = padn / 2; right = padn - left; }
    else if (align == 4) left = padn;  // zeros depois do sinal
    char *buf = malloc(n + padn * fl + 1);
    size_t k = 0;
    if (align == 4 && n > 0 && (s[0] == '-' || s[0] == '+')) { buf[k++] = s[0]; s++; n--; }
    for (size_t i = 0; i < left; i++) { memcpy(buf + k, fb, fl); k += fl; }
    memcpy(buf + k, s, n); k += n;
    for (size_t i = 0; i < right; i++) { memcpy(buf + k, fb, fl); k += fl; }
    W t = uu_text_new(buf, k);
    free(buf);
    return t;
}

W uu_fmt_i64(W x, W spec) {
    char b[80];
    W kind = ((Obj *)spec)->f[4];
    int n;
    if (kind == 'x') n = snprintf(b, sizeof b, "%llx", (unsigned long long)x);
    else if (kind == 'X') n = snprintf(b, sizeof b, "%llX", (unsigned long long)x);
    else if (kind == 'b') {
        unsigned long long v = (unsigned long long)x; char t[70]; int k = 0;
        do { t[k++] = (char)('0' + (v & 1)); v >>= 1; } while (v);
        for (n = 0; n < k; n++) b[n] = t[k - 1 - n];
        b[n] = 0;
    } else if (kind == 'f' || kind == 'e') {
        W prec = ((Obj *)spec)->f[3];
        n = snprintf(b, sizeof b, kind == 'f' ? "%.*f" : "%.*e", (int)(prec < 0 ? 6 : prec), (double)x);
    } else n = snprintf(b, sizeof b, "%lld", (long long)x);
    return pad(b, (size_t)n, spec, 1);
}

W uu_fmt_u64(W x, W spec) {
    char b[80];
    W kind = ((Obj *)spec)->f[4];
    int n;
    if (kind == 'x') n = snprintf(b, sizeof b, "%llx", (unsigned long long)x);
    else n = snprintf(b, sizeof b, "%llu", (unsigned long long)x);
    return pad(b, (size_t)n, spec, 1);
}

static void fmt_shortest(char *b, size_t sz, double d) {
    // menor representação que volta ao mesmo valor, sempre com ponto decimal
    if (isnan(d)) { snprintf(b, sz, "nan"); return; }
    if (isinf(d)) { snprintf(b, sz, d < 0 ? "-inf" : "inf"); return; }
    for (int p = 1; p <= 17; p++) {
        snprintf(b, sz, "%.*g", p, d);
        if (strtod(b, NULL) == d) break;
    }
    if (!strpbrk(b, ".eEn")) strncat(b, ".0", sz - strlen(b) - 1);
}

static void fmt_shortest_f(char *b, size_t sz, float d) {
    if (isnan(d)) { snprintf(b, sz, "nan"); return; }
    if (isinf(d)) { snprintf(b, sz, d < 0 ? "-inf" : "inf"); return; }
    for (int p = 1; p <= 9; p++) {
        snprintf(b, sz, "%.*g", p, (double)d);
        if (strtof(b, NULL) == d) break;
    }
    if (!strpbrk(b, ".eEn")) strncat(b, ".0", sz - strlen(b) - 1);
}

static W fmt_float(double d, W spec, int is32) {
    char b[400];
    W prec = ((Obj *)spec)->f[3], kind = ((Obj *)spec)->f[4];
    int n;
    if (kind == 'e') n = snprintf(b, sizeof b, "%.*e", (int)(prec < 0 ? 6 : prec), d);
    else if (prec >= 0 || kind == 'f') n = snprintf(b, sizeof b, "%.*f", (int)(prec < 0 ? 6 : prec), d);
    else if (kind == 'd') n = snprintf(b, sizeof b, "%.0f", d);
    else {
        if (is32) fmt_shortest_f(b, sizeof b, (float)d); else fmt_shortest(b, sizeof b, d);
        n = (int)strlen(b);
    }
    return pad(b, (size_t)n, spec, 1);
}

W uu_fmt_f64(double d, W spec) { return fmt_float(d, spec, 0); }
W uu_fmt_f32(double d, W spec) { return fmt_float(d, spec, 1); }

W uu_fmt_text(W t, W spec) {
    Obj *sp = (Obj *)spec;
    W prec = sp->f[3];
    size_t n = uu_text_len(t);
    const char *s = uu_text_ptr(t);
    if (prec >= 0) {
        // corta em `prec` caracteres
        size_t c = 0, i = 0;
        for (; i < n; i++) {
            if ((s[i] & 0xC0) != 0x80) { if ((W)c == prec) break; c++; }
        }
        n = i;
    }
    W r = pad(s, n, spec, 0);
    uu_dec(t);
    return r;
}

// ------------------------------------------------------------------ pânico e debug
void uu_panic(W msg) {
    fflush(stdout);
    fprintf(stderr, "pânico: %.*s\n", (int)uu_text_len(msg), uu_text_ptr(msg));
    exit(101);
}

void uu_panic_c(const char *msg) {
    fflush(stdout);
    fprintf(stderr, "pânico: %s\n", msg);
    exit(101);
}

W uu_debug(W text) {
    fwrite(uu_text_ptr(text), 1, (size_t)uu_text_len(text), stdout);
    fputc('\n', stdout);
    uu_dec(text);
    return 0;
}

// ------------------------------------------------------------------ Pending
enum { CB_MAP, CB_BIND, CB_CHAIN };
struct Callback {
    int kind;
    W fn;
    W out;
    Callback *next;
};

static W pending_new(int vptr) {
    Pending *p = (Pending *)uu_alloc(K_PEND, 0, 0, 0);
    p = realloc(p, sizeof(Pending));
    p->state = 0;
    p->vptr = vptr;
    p->value = 0;
    p->cbs = NULL;
    return (W)p;
}

static void pending_free(Pending *p) {
    if (p->state == 1 && p->vptr) uu_dec(p->value);
    Callback *c = p->cbs;
    while (c) {
        Callback *n = c->next;
        if (c->fn) uu_dec(c->fn);
        uu_dec(c->out);
        free(c);
        c = n;
    }
    free(p);
}

static void pending_resolve(W pw, W v);

static void pending_listen(W pw, int kind, W fn, W out) {
    Pending *p = (Pending *)pw;
    if (p->state == 1) {
        // já resolvido: dispara agora
        W v = p->value;
        if (p->vptr) uu_inc(v);
        if (kind == CB_MAP) {
            pending_resolve(out, uu_apply1(fn, v));
            uu_dec(out);
        } else if (kind == CB_BIND) {
            W q = uu_apply1(fn, v);
            pending_listen(q, CB_CHAIN, 0, out);
            uu_dec(q);
        } else {
            pending_resolve(out, v);
            uu_dec(out);
        }
        return;
    }
    Callback *c = malloc(sizeof(Callback));
    c->kind = kind;
    c->fn = fn;
    c->out = out;
    c->next = NULL;
    Callback **tail = &p->cbs;
    while (*tail) tail = &(*tail)->next;
    *tail = c;
}

// O último callback (map ou chain) é tratado em laço, não em recursão: um
// polling recursivo (`nextTick() >>= ...`) forma uma corrente com um elo por
// tick, e resolvê-la recursivamente estouraria a pilha.
static void pending_resolve(W pw, W v) {
    W held = 0;  // `out` herdado do callback anterior (referência nossa)
    for (;;) {
        Pending *p = (Pending *)pw;
        p->state = 1;
        p->value = v;
        Callback *c = p->cbs;
        p->cbs = NULL;
        uu_inc(pw);
        W next = 0, nv = 0;
        while (c) {
            Callback *n = c->next;
            if (!n && (c->kind == CB_CHAIN || c->kind == CB_MAP)) {
                W x = p->value;
                if (p->vptr) uu_inc(x);
                nv = c->kind == CB_MAP ? uu_apply1(c->fn, x) : x;
                next = c->out;
                free(c);
                break;
            }
            pending_listen(pw, c->kind, c->fn, c->out);
            free(c);
            c = n;
        }
        uu_dec(pw);
        if (held) uu_dec(held);
        if (!next) return;
        held = next;
        pw = next;
        v = nv;
    }
}

W uu_pending_pure(W x, W vptr) {
    W p = pending_new((int)vptr);
    pending_resolve(p, x);
    return p;
}

W uu_pending_map(W p, W f, W vptr) {
    W out = pending_new((int)vptr);
    uu_inc(out);
    pending_listen(p, CB_MAP, f, out);
    uu_dec(p);
    return out;
}

W uu_pending_bind(W p, W f, W vptr) {
    W out = pending_new((int)vptr);
    uu_inc(out);
    pending_listen(p, CB_BIND, f, out);
    uu_dec(p);
    return out;
}

W uu_pending_poll_state(W p) { return ((Pending *)p)->state; }
W uu_pending_value(W p) { return ((Pending *)p)->value; }

W uu_pending_poll(W p, W isptr) {
    Pending *pp = (Pending *)p;
    W r;
    if (pp->state == 1) {
        W v = pp->value;
        if (isptr) uu_inc(v);
        r = just(v, isptr);
    } else r = uu_nullary(1);
    uu_dec(p);
    return r;
}

// ------------------------------------------------------------------ pedidos e ticks
// Pedidos de thread principal (print, nextTick, FFI `main`) entram numa fila e
// rodam em ordem de emissão no fim do tick. Pedidos de worker (arquivos, sleepMs,
// FFI `worker`) vão para um pool de pthreads; o resultado volta como dado bruto
// e é entregue na thread principal entre ticks. Workers nunca tocam objetos com
// RC (o RC não é thread-safe): tudo o que cruza a fronteira é copiado antes.
typedef W (*Invoker)(W *slots);

typedef struct Req {
    int kind;
    W arg;
    W pending;
    Invoker inv;  // FFI `main`
    W *slots;
    W freemask, retkind;
    struct Req *next;
} Req;

enum { REQ_PRINT, REQ_TICK, REQ_FFI };

static Req *queue_head, *queue_tail;

static Req *emit(int kind, W arg, int vptr) {
    Req *r = calloc(1, sizeof(Req));
    r->kind = kind;
    r->arg = arg;
    r->pending = pending_new(vptr);
    uu_inc(r->pending);
    r->next = NULL;
    if (queue_tail) queue_tail->next = r; else queue_head = r;
    queue_tail = r;
    return r;
}

W uu_req_print(W text) { return emit(REQ_PRINT, text, 0)->pending; }
W uu_req_next_tick(void) { return emit(REQ_TICK, 0, 0)->pending; }

// ---- FFI: o compilador gera um "invoker" C que lê slots brutos e chama a função
W uu_ffi_slots(W n) { return (W)calloc((size_t)(n > 0 ? n : 1), sizeof(W)); }

W uu_cstr_dup(W text) {
    size_t n = (size_t)uu_text_len(text);
    char *s = malloc(n + 1);
    memcpy(s, uu_text_ptr(text), n);
    s[n] = 0;
    uu_dec(text);
    return (W)s;
}

static W ffi_call(Invoker inv, W *slots, W freemask) {
    W r = inv(slots);
    for (int i = 0; i < 64; i++)
        if ((uint64_t)freemask & (1ull << i)) free((void *)slots[i]);
    free(slots);
    return r;
}

// retkind 1: CStr devolvido vira Text (copiado; a memória continua do C)
static W ffi_value(W raw, W retkind) {
    if (retkind == 1) {
        const char *s = (const char *)raw;
        return s ? uu_text_new(s, strlen(s)) : uu_text_new("", 0);
    }
    return raw;
}

// ---- pool de workers
typedef struct Job Job;
struct Job {
    void (*run)(Job *);      // no worker: só dados brutos
    void (*deliver)(Job *);  // na thread principal: monta valores e resolve
    W pending;
    char *s1, *s2;
    size_t n2;
    W i1;
    char *out;
    size_t outn;
    int err;
    Invoker inv;
    W *slots;
    W freemask, retkind, result;
    Job *next;
};

#define N_WORKERS 4
static pthread_mutex_t pool_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t jobs_cv = PTHREAD_COND_INITIALIZER;
static pthread_cond_t done_cv = PTHREAD_COND_INITIALIZER;
static Job *jobs_head, *jobs_tail, *done_head, *done_tail;
static int n_started;
static long outstanding;  // só a thread principal mexe

static void *worker_main(void *arg) {
    (void)arg;
    for (;;) {
        pthread_mutex_lock(&pool_mu);
        while (!jobs_head) pthread_cond_wait(&jobs_cv, &pool_mu);
        Job *j = jobs_head;
        jobs_head = j->next;
        if (!jobs_head) jobs_tail = NULL;
        pthread_mutex_unlock(&pool_mu);
        j->run(j);
        pthread_mutex_lock(&pool_mu);
        j->next = NULL;
        if (done_tail) done_tail->next = j; else done_head = j;
        done_tail = j;
        pthread_cond_signal(&done_cv);
        pthread_mutex_unlock(&pool_mu);
    }
    return NULL;
}

static W submit(Job *j, int vptr) {
    j->pending = pending_new(vptr);
    uu_inc(j->pending);
    j->next = NULL;
    outstanding++;
    pthread_mutex_lock(&pool_mu);
    while (n_started < N_WORKERS) {
        pthread_t t;
        if (pthread_create(&t, NULL, worker_main, NULL) != 0) break;
        pthread_detach(t);
        n_started++;
    }
    if (n_started == 0) {
        pthread_mutex_unlock(&pool_mu);
        uu_panic_c("não consegui criar threads de worker");
    }
    if (jobs_tail) jobs_tail->next = j; else jobs_head = j;
    jobs_tail = j;
    pthread_cond_signal(&jobs_cv);
    pthread_mutex_unlock(&pool_mu);
    return j->pending;
}

// entrega os trabalhos concluídos na thread principal; com `block`, espera um
static void deliver_done(int block) {
    if (outstanding == 0) return;
    pthread_mutex_lock(&pool_mu);
    while (block && !done_head) pthread_cond_wait(&done_cv, &pool_mu);
    Job *j = done_head;
    done_head = done_tail = NULL;
    pthread_mutex_unlock(&pool_mu);
    while (j) {
        Job *n = j->next;
        outstanding--;
        j->deliver(j);
        uu_dec(j->pending);
        free(j);
        j = n;
    }
}

// Result: Ok(x) tem tag 0, Err(e) tag 1
static W result_obj(int ok, W v, int vptr) {
    W o = uu_alloc(K_DATA, ok ? 0 : 1, 1, vptr ? 1 : 0);
    ((Obj *)o)->f[0] = v;
    return o;
}

static W err_text(const char *path, int err) {
    char buf[512];
    int n = snprintf(buf, sizeof buf, "%s: %s", path, strerror(err));
    if (n < 0) n = 0;
    if ((size_t)n >= sizeof buf) n = (int)sizeof buf - 1;
    return uu_text_new(buf, (size_t)n);
}

// sleepMs
static void run_sleep(Job *j) {
    struct timespec ts = { (time_t)(j->i1 / 1000), (long)(j->i1 % 1000) * 1000000L };
    while (nanosleep(&ts, &ts) != 0 && errno == EINTR) {}
}
static void deliver_unit(Job *j) { pending_resolve(j->pending, 0); }

W uu_req_sleep_ms(W ms) {
    Job *j = calloc(1, sizeof(Job));
    j->run = run_sleep;
    j->deliver = deliver_unit;
    j->i1 = ms < 0 ? 0 : ms;
    return submit(j, 0);
}

// readFile
static void run_read(Job *j) {
    FILE *f = fopen(j->s1, "rb");
    if (!f) { j->err = errno; return; }
    size_t cap = 4096, n = 0, k;
    char *buf = malloc(cap);
    while ((k = fread(buf + n, 1, cap - n, f)) > 0) {
        n += k;
        if (n == cap) { cap *= 2; buf = realloc(buf, cap); }
    }
    if (ferror(f)) { j->err = errno ? errno : EIO; free(buf); buf = NULL; }
    fclose(f);
    j->out = buf;
    j->outn = n;
}
static void deliver_read(Job *j) {
    W r = j->err ? result_obj(0, err_text(j->s1, j->err), 1)
                 : result_obj(1, uu_text_new(j->out, j->outn), 1);
    free(j->s1);
    free(j->out);
    pending_resolve(j->pending, r);
}

W uu_req_read_file(W path) {
    Job *j = calloc(1, sizeof(Job));
    j->run = run_read;
    j->deliver = deliver_read;
    j->s1 = (char *)uu_cstr_dup(path);
    return submit(j, 1);
}

// writeFile
static void run_write(Job *j) {
    FILE *f = fopen(j->s1, "wb");
    if (!f) { j->err = errno; return; }
    if (j->n2 && fwrite(j->s2, 1, j->n2, f) != j->n2) j->err = errno ? errno : EIO;
    if (fclose(f) != 0 && !j->err) j->err = errno ? errno : EIO;
}
static void deliver_write(Job *j) {
    W r = j->err ? result_obj(0, err_text(j->s1, j->err), 1) : result_obj(1, 0, 0);
    free(j->s1);
    free(j->s2);
    pending_resolve(j->pending, r);
}

W uu_req_write_file(W path, W content) {
    Job *j = calloc(1, sizeof(Job));
    j->run = run_write;
    j->deliver = deliver_write;
    j->s1 = (char *)uu_cstr_dup(path);
    j->n2 = (size_t)uu_text_len(content);
    j->s2 = (char *)uu_cstr_dup(content);
    return submit(j, 1);
}

// FFI: `main` vai para a fila do tick; `worker`, para o pool
static void run_ffi(Job *j) { j->result = ffi_call(j->inv, j->slots, j->freemask); }
static void deliver_ffi(Job *j) { pending_resolve(j->pending, ffi_value(j->result, j->retkind)); }

W uu_req_ffi(W inv, W slots, W worker, W retkind, W freemask) {
    if (worker) {
        Job *j = calloc(1, sizeof(Job));
        j->run = run_ffi;
        j->deliver = deliver_ffi;
        j->inv = (Invoker)inv;
        j->slots = (W *)slots;
        j->freemask = freemask;
        j->retkind = retkind;
        return submit(j, retkind == 1);
    }
    Req *r = emit(REQ_FFI, 0, retkind == 1);
    r->inv = (Invoker)inv;
    r->slots = (W *)slots;
    r->freemask = freemask;
    r->retkind = retkind;
    return r->pending;
}

static void exec_req(Req *r) {
    switch (r->kind) {
    case REQ_PRINT:
        fwrite(uu_text_ptr(r->arg), 1, (size_t)uu_text_len(r->arg), stdout);
        fputc('\n', stdout);
        uu_dec(r->arg);
        pending_resolve(r->pending, 0);
        break;
    case REQ_TICK:
        pending_resolve(r->pending, 0);
        break;
    case REQ_FFI:
        pending_resolve(r->pending, ffi_value(ffi_call(r->inv, r->slots, r->freemask), r->retkind));
        break;
    }
    uu_dec(r->pending);
}

int uu_run(W main_pending) {
    Pending *mp = (Pending *)main_pending;
    while (mp->state == 0) {
        Req *r = queue_head;
        queue_head = queue_tail = NULL;
        if (!r) {
            if (outstanding > 0) {
                // nada para a thread principal: dorme até um worker terminar
                deliver_done(1);
                continue;
            }
            fflush(stdout);
            fputs("double-u: o Pending de `main` nunca resolve (nenhum pedido em andamento)\n", stderr);
            return 101;
        }
        // fim do tick: pedidos da thread principal, em ordem de emissão
        while (r) {
            Req *n = r->next;
            exec_req(r);
            free(r);
            r = n;
        }
        // entre ticks: resultados prontos dos workers
        deliver_done(0);
    }
    int code = (int)mp->value;
    fflush(stdout);
    return code;
}

// ------------------------------------------------------------------ List
// K_LIST: n = tamanho, aux = capacidade, tag = elementos são ponteiros
static W list_alloc(W cap, W isptr) {
    if (cap < 4) cap = 4;
    W l = uu_alloc(K_LIST, isptr, cap, 0);
    ((Obj *)l)->aux = (uint32_t)cap;
    ((Obj *)l)->n = 0;
    return l;
}

W uu_list_new(W n, W isptr) {
    W l = list_alloc(n, isptr);
    ((Obj *)l)->n = (uint32_t)n;
    return l;
}

W uu_list_len(W xs) { W n = ((Obj *)xs)->n; uu_dec(xs); return n; }

static W just(W x, W isptr) {
    W o = uu_alloc(K_DATA, 0, 1, isptr ? 1 : 0);
    ((Obj *)o)->f[0] = x;
    return o;
}

W uu_list_get(W xs, W i) {
    Obj *o = (Obj *)xs;
    W r;
    if (i < 0 || i >= (W)o->n) r = uu_nullary(1);
    else {
        W x = o->f[i];
        if (o->tag) uu_inc(x);
        r = just(x, o->tag);
    }
    uu_dec(xs);
    return r;
}

W uu_list_at(W xs, W i) {
    Obj *o = (Obj *)xs;
    if (i < 0 || i >= (W)o->n) uu_panic_c("índice fora da lista");
    W x = o->f[i];
    if (o->tag) uu_inc(x);
    uu_dec(xs);
    return x;
}

static W list_unique(W xs, W extra) {
    // garante dono único com espaço para mais `extra` elementos (reaproveita o buffer)
    Obj *o = (Obj *)xs;
    if (o->rc == 1) {
        if (o->n + extra > o->aux) {
            W cap = (W)o->aux * 2;
            if (cap < (W)(o->n + extra)) cap = o->n + extra;
            o = realloc(o, sizeof(Obj) + (size_t)cap * sizeof(W));
            o->aux = (uint32_t)cap;
        }
        return (W)o;
    }
    W l = list_alloc(o->n + extra, o->tag);
    Obj *d = (Obj *)l;
    for (uint32_t i = 0; i < o->n; i++) {
        d->f[i] = o->f[i];
        if (o->tag) uu_inc(o->f[i]);
    }
    d->n = o->n;
    uu_dec(xs);
    return l;
}

W uu_list_push(W xs, W x) {
    xs = list_unique(xs, 1);
    Obj *o = (Obj *)xs;
    o->f[o->n++] = x;
    return xs;
}

W uu_list_set(W xs, W i, W x) {
    xs = list_unique(xs, 0);
    Obj *o = (Obj *)xs;
    if (i < 0 || i >= (W)o->n) uu_panic_c("índice fora da lista");
    if (o->tag) uu_dec(o->f[i]);
    o->f[i] = x;
    return xs;
}

W uu_list_concat(W a, W b) {
    Obj *ob = (Obj *)b;
    a = list_unique(a, ob->n);
    Obj *oa = (Obj *)a;
    for (uint32_t i = 0; i < ob->n; i++) {
        oa->f[oa->n++] = ob->f[i];
        if (ob->tag) uu_inc(ob->f[i]);
    }
    uu_dec(b);
    return a;
}

W uu_list_fold(W xs, W acc, W f) {
    Obj *o = (Obj *)xs;
    for (uint32_t i = 0; i < o->n; i++) {
        W x = o->f[i];
        if (o->tag) uu_inc(x);
        uu_inc(f);
        acc = uu_apply2(f, acc, x);
    }
    uu_dec(f);
    uu_dec(xs);
    return acc;
}

W uu_list_each(W xs, W f) {
    Obj *o = (Obj *)xs;
    for (uint32_t i = 0; i < o->n; i++) {
        W x = o->f[i];
        if (o->tag) uu_inc(x);
        uu_inc(f);
        uu_apply1(f, x);
    }
    uu_dec(f);
    uu_dec(xs);
    return 0;
}

W uu_list_map(W xs, W f, W outptr) {
    Obj *o = (Obj *)xs;
    W l = list_alloc(o->n, outptr);
    for (uint32_t i = 0; i < o->n; i++) {
        W x = o->f[i];
        if (o->tag) uu_inc(x);
        uu_inc(f);
        ((Obj *)l)->f[i] = uu_apply1(f, x);
        ((Obj *)l)->n = i + 1;
    }
    uu_dec(f);
    uu_dec(xs);
    return l;
}

W uu_list_filter(W xs, W f) {
    Obj *o = (Obj *)xs;
    W l = list_alloc(o->n, o->tag);
    Obj *d = (Obj *)l;
    for (uint32_t i = 0; i < o->n; i++) {
        W x = o->f[i];
        if (o->tag) uu_inc(x);
        uu_inc(f);
        if (uu_apply1(f, x)) {
            if (o->tag) uu_inc(x);
            d->f[d->n++] = x;
        }
    }
    uu_dec(f);
    uu_dec(xs);
    return l;
}

W uu_list_bind(W xs, W f, W outptr) {
    Obj *o = (Obj *)xs;
    W l = list_alloc(o->n, outptr);
    for (uint32_t i = 0; i < o->n; i++) {
        W x = o->f[i];
        if (o->tag) uu_inc(x);
        uu_inc(f);
        l = uu_list_concat(l, uu_apply1(f, x));
    }
    uu_dec(f);
    uu_dec(xs);
    return l;
}

W uu_list_pure(W x, W isptr) {
    W l = list_alloc(1, isptr);
    ((Obj *)l)->f[0] = x;
    ((Obj *)l)->n = 1;
    return l;
}

W uu_list_range(W a, W b) {
    W n = b > a ? b - a : 0;
    W l = list_alloc(n, 0);
    for (W i = 0; i < n; i++) ((Obj *)l)->f[i] = a + i;
    ((Obj *)l)->n = (uint32_t)n;
    return l;
}

W uu_text_len_prim(W t) {
    W n = (W)utf8_len(uu_text_ptr(t), (size_t)uu_text_len(t));
    uu_dec(t);
    return n;
}

W uu_list_each_mut(W slot, W f) {
    W *sp = (W *)slot;
    *sp = list_unique(*sp, 0);
    Obj *o = (Obj *)*sp;
    for (uint32_t i = 0; i < o->n; i++) {
        uu_inc(f);
        uu_apply1(f, (W)&o->f[i]);
    }
    uu_dec(f);
    return 0;
}
