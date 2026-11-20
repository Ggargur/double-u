#ifndef DOUBLE_U_RUNTIME_H
#define DOUBLE_U_RUNTIME_H
#include <stdint.h>
#include <stddef.h>

typedef int64_t W;

// Cabeçalho comum de todo objeto no heap. rc < 0: imortal.
typedef struct Obj {
    int64_t rc;
    uint32_t kind;
    uint32_t tag;
    uint32_t n;
    uint32_t aux;
    uint64_t mask;  // campos f[i] que são ponteiros (DATA/CLOS)
    W f[];
} Obj;

enum { K_DATA = 0, K_CLOS = 1, K_TEXT = 2, K_LIST = 3, K_MAP = 4, K_PEND = 5, K_LAZY = 6, K_GEN = 7 };

W uu_alloc(W kind, W tag, W n, W mask);
void uu_inc(W x);
void uu_dec(W x);
W uu_text_new(const char *s, size_t n);
const char *uu_text_ptr(W t);
W uu_text_len(W t);

// gerados pelo compilador: chamam closures (convenção tailcc)
W uu_apply0(W clo);
W uu_apply1(W clo, W a);
W uu_apply2(W clo, W a, W b);

#endif
