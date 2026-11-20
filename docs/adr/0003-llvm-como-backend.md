# LLVM via llvmlite como backend

O compilador (em Python) gera LLVM IR com `llvmlite`. A escolha vem de dois requisitos: tail calls garantidas em qualquer posição de cauda, inclusive recursão mútua (`tailcc` + `musttail`), e generators, que os intrínsecos `llvm.coro.*` implementam sem que a gente escreva máquinas de estado à mão.

## Considered Options

- **C + clang** (o backend da v1): `musttail` resolve tail calls, mas prende ao clang do mesmo jeito e obriga a compilar generators como máquinas de estado manuais.
- **Cranelift**: compila rápido e suporta tail calls, mas exige escrever o compilador em Rust.
- **QBE**: simples, mas sem tail calls garantidas nem coroutines.
