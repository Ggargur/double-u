// Biblioteca C pequena usada pelos testes de @extern (ligada via `// flags: -l _teste.c`).
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdatomic.h>
#include <unistd.h>
#include <sys/syscall.h>

int32_t c_soma(int32_t a, int32_t b) { return a + b; }
double c_metade(double x) { return x / 2.0; }
float c_dobro32(float x) { return x * 2.0f; }
int64_t c_tamanho(const char *s) { return (int64_t)strlen(s); }
const char *c_saudacao(void) { return "olá do C"; }
int8_t c_negativo(int8_t x) { return (int8_t)-x; }

// 1 quando roda na thread principal do processo
int64_t c_na_principal(void) { return syscall(SYS_gettid) == getpid(); }

// imprime pelo mesmo stdout do runtime
void c_diz(const char *s) { printf("C: %s\n", s); }

// worker espera um sinal dado por uma chamada `main` em um tick posterior
static atomic_int sinal;
void c_sinaliza(void) { atomic_store(&sinal, 1); }
int64_t c_espera_sinal(void) {
    for (int i = 0; i < 5000; i++) {
        if (atomic_load(&sinal)) return 1;
        usleep(1000);
    }
    return 0;
}
