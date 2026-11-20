# double-u

Linguagem compilada, funcional primeiro, voltada a jogos. Valores têm dono; mutação é local e explícita. Conceitos de jogo (entidades, componentes, sistemas) vivem em bibliotecas, não na linguagem.

## Valores e posse

**Dono**:
A variável ou valor que contém outro valor e responde pelo seu tempo de vida. Todo valor tem exatamente um dono.
_Avoid_: owner, proprietário

**Move**:
Transferência de um valor para um novo dono; o dono anterior deixa de poder usá-lo. Acontece ao passar um argumento para um parâmetro `own`, ao retornar um valor e ao guardá-lo num construtor.
_Avoid_: transferência, cessão

**Tipo copiável**:
Tipo de dado simples (números, `bool`, e records ou sum types feitos só deles) que é copiado implicitamente em vez de movido.
_Avoid_: POD, value type, Copy type

**Empréstimo**:
Acesso temporário e somente leitura a um valor sem tirá-lo do dono. É o modo padrão de todo parâmetro; nunca é guardado em campo nem capturado por closure que escapa.
_Avoid_: borrow, referência, ponteiro

**Empréstimo mutável**:
Empréstimo que permite à função alterar o valor do chamador. Marcado com `mut` no parâmetro e com `!` no argumento, no ponto de uso (`integrate(!pos vel)`).
_Avoid_: inout, `&mut`, passagem por referência

## Avaliação

**Generator**:
Função cujo corpo contém `yield`; produz uma sequência de valores sob demanda, um de cada vez.
_Avoid_: iterator, stream, coroutine

**Valor preguiçoso**:
Expressão marcada explicitamente para ser avaliada só quando consumida pela primeira vez. Tudo que não é marcado é avaliado imediatamente.
_Avoid_: thunk, lazy, promessa

## Funções

**Fixidez**:
A posição em que uma função é escrita no uso em relação aos seus argumentos: prefixa `f(a b)`, infixa `a f b` ou pós-fixa `a.f`. É escolhida na declaração.
_Avoid_: notação, estilo de chamada

**Função infixa**:
Função de dois argumentos declarada para ser usada entre eles (`a cross b`). Operadores como `+` são funções infixas com nome simbólico, sempre definidas por traits.
_Avoid_: operador customizado, backtick function

**Função pós-fixa**:
Função de um argumento declarada para ser usada após ele com `.` (`x.squared`).
_Avoid_: método, extension method, getter

## IO

**Pedido**:
Descrição de uma operação de IO emitida por uma função `io` e executada depois pelo runtime, fora do fluxo da função que o emitiu.
_Avoid_: comando, Cmd, efeito, side effect, action

**Função io**:
Função marcada `io` na assinatura; a única que pode emitir pedidos. A marca é o aceite explícito de que o IO acontece separado e sem controle exato de quando.
_Avoid_: função impura, função com efeito

**Pending**:
Valor que representa o resultado ainda não disponível de um pedido. Pode ser consultado a cada tick ou encadeado por compreensão.
_Avoid_: Future, Promise, Task, handle

**Tick**:
Uma iteração do loop do runtime: começa com o snapshot de input, roda a lógica e termina executando os pedidos de desenho em ordem.
_Avoid_: frame, update, ciclo

## Compilador

**Builtin**:
Nome prefixado com `@` fornecido pelo compilador, não por biblioteca (`@import`, `@extern`, `@debug`, `@tail`).
_Avoid_: intrínseco, macro, diretiva

## Ausência

**Maybe**:
Tipo que representa um valor que pode estar ausente. É a única forma de ausência na linguagem; não existe `null`.
_Avoid_: Option, nullable, `T?`, null

## Módulos

**Item público**:
Declaração marcada com `+` antes do nome, visível fora do seu módulo. Sem `+`, o item é privado ao módulo.
_Avoid_: exportado, export, pub

## Compreensões

**Compreensão**:
Expressão `for ... <- ... yield` que extrai valores de uma ou mais fontes, filtra e produz um resultado. É a mesma forma para consultas, generators e encadeamento de monads.
_Avoid_: consulta, query, do-notation, LINQ, list comprehension

**Fonte**:
Qualquer valor de onde uma compreensão pode extrair valores: coleções, generators, Maybe, Result, ou estruturas de biblioteca como um mundo de ECS.
_Avoid_: queryable, data source, tabela
