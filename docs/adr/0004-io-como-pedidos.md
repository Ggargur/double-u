# IO como pedidos executados pelo runtime

Funções não fazem IO diretamente. Uma função marcada `io` na assinatura pode emitir pedidos de IO (salvar arquivo, desenhar, chamar FFI); o runtime os executa depois, fora do fluxo da função, e o resultado chega mais tarde. A marca `io` é o aceite explícito do programador de que o IO acontece separado e sem controle exato de quando. Input chega como snapshot no começo de cada tick; comandos de desenho são executados em ordem no fim do tick. O runtime da linguagem tem um loop genérico de ticks e uma thread de IO; o game loop propriamente dito é construído por biblioteca em cima disso (ver ADR 0001).

## Considered Options

- **IO livre** (OCaml, v1): simples, mas esconde efeitos e não separa o IO da lógica.
- **Futures com polling manual**: mais controle sobre quando olhar o resultado, porém mais estado para o programador gerenciar.
- **Só a marca `io` com chamadas síncronas**: honesto no tipo, mas o IO não acontece separado.

## Consequences

Toda declaração FFI (`@extern`, `@cImport`) é `io`. O runtime deixa de ser só uma biblioteca de suporte e passa a ter um loop e uma thread próprios.
