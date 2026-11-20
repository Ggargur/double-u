# double-u v2: Design da Linguagem

Documento consolidado das decisões tomadas na reformulação da v2. Use como referência ao implementar parser, type checker e codegen. A terminologia segue o [GLOSSARY.md](../GLOSSARY.md); as decisões estruturais estão registradas em [docs/adr/](adr/).

---

## 1. Identidade

double-u é uma linguagem compilada, **funcional primeiro**, voltada a jogos.

- **Funcional primeiro.** Tudo é expressão, variáveis são imutáveis por padrão, e a mutação é local e explícita.
- **Posse de valores.** Todo valor tem um dono. Empréstimos existem só em parâmetros, e não há GC nem lifetimes anotados ([ADR 0002](adr/0002-posse-segunda-classe-e-avaliacao-estrita.md)).
- **Estrita por padrão.** A laziness só existe quando é pedida explicitamente.
- **Jogos por biblioteca.** A linguagem não conhece entidades, componentes nem sistemas. Ela oferece compreensões, traits com HKT e posse, e isso basta para escrever um ECS no estilo do Bevy como biblioteca ([ADR 0001](adr/0001-ecs-como-biblioteca.md)).
- **IO separado.** Funções `io` emitem pedidos, e é o runtime quem os executa ([ADR 0004](adr/0004-io-como-pedidos.md)).
- **Sem herança.** O polimorfismo vem exclusivamente de traits.

---

## 2. Sintaxe básica

### Layout

Os blocos são delimitados por **indentação**. Uma quebra de linha separa instruções. Não há chaves de bloco nem formas de uma linha para blocos com múltiplas ligações.

### Comentários

```
// comentário de linha

/// Documentação: linhas `///` imediatamente antes de uma declaração.
fn +area(s: Shape) -> float = ...
```

`//` não é operador. A divisão inteira é a infixa `div`.

### Ligações

```
x = 42              // imutável
var hp = 100        // reatribuível
hp = hp - 10
```

Um bloco é uma sequência de ligações terminada por uma expressão, que é o valor do bloco. **Não existe `let ... in`**, porque o bloco já cumpre esse papel.

### `where`

O `where` dá a ordem inversa de leitura: primeiro o resultado, depois os detalhes. As ligações do `where` são estritas.

```
fn +energy(body: Body) -> float =
    kinetic + potential
  where
    kinetic   = 0.5 * body.mass * body.speed ** 2
    potential = body.mass * g * body.height
```

### `if` e `match`

Os dois são expressões.

```
dmg = if crit then base * 2 else base

dmg =
    if crit
        base * 2
    else if blocked
        0
    else
        base

msg = match e
    Hit(n) if n > 10 -> "crítico"
    Hit(n)           -> "acerto de {n}"
    Miss             -> "errou"
```

O `then` é opcional na forma em layout. Um `if` sem `else` só é aceito quando o resultado é `()`. O `match` é **exaustivo** e aceita padrões aninhados e guards (`if` depois do padrão).

### Literais

```
n  = 42                          // literais numéricos assumem o tipo esperado pelo contexto
xs = [1 2 3]                     // List(int)
m  = {"a": 1  "b": 2}            // Map(Text int)
t  = #(0 "zero")                 // tupla
v  = Vec(1.0 2.0)                // construtor posicional
v  = Vec(x: 1.0 y: 2.0)          // construtor nomeado
w  = v with y: 5.0               // cópia com um campo trocado
```

### Parênteses

| Forma | Significado |
|---|---|
| `f(a b)`, com o nome colado | lista de argumentos, onde o espaço separa os itens |
| `(expr)`, solto | **sempre** agrupamento de uma expressão |
| `#(a b)` | tupla |
| `[a b]` | lista |

Dentro de listas de argumentos, tuplas e literais de lista, **cada item é atômico**: um nome, um literal, um acesso com `.`, uma chamada `g(x)` ou algo entre parênteses. Expressões com operador ou infixa precisam de parênteses:

```
f((a + b) c)          // dois argumentos
f(a + b c)            // erro de leitura: + não é atômico
f(a cross b)          // TRÊS argumentos: a, cross, b
f((a cross b))        // um argumento
f(-1 x)               // `-` colado ao número é negação
```

### Interpolação de strings

```
"olá, {nome}!"                    // expressão qualquer dentro de {}
"total: {preco * qtd}"            // sem a regra de item atômico
"{pi:.2f}"                        // 3.14
"{hp:>5}"                         // alinhado à direita em 5 colunas
"{{literal}}"                     // {{ escapa uma chave
```

Só interpola o tipo que implementa a trait `Format`, que recebe o spec já parseado. Interpolar um tipo sem `Format` é erro de compilação.

```
impl Format(Vec) =
    fn format(v spec) = "({v.x} {v.y})"
```

---

## 3. Funções

### Declaração e visibilidade

```
fn +energy(body: Body) -> float = ...     // pública: + antes do nome
fn helper(x: int) -> int = ...            // privada ao módulo
```

O `+` vale para funções, tipos, campos e traits. As assinaturas de itens públicos são **obrigatoriamente anotadas**. Dentro de corpos e em itens privados, os tipos são inferidos.

### Chamada

```
distance(a b)                  // posicional
spawn(pos: p hp: 10)           // nomeado; não se mistura com posicional na mesma chamada
```

### Aplicação parcial

Não existe currying. A aplicação parcial é explícita, com `_`:

```
d   = distance(a _)            // fn(b) => distance(a b)
dbl = (_ * 2)                  // vale também para infixas
distance(a)                    // erro de aridade
```

### Lambdas

```
map(xs x => x * 2)
fold(xs 0 (acc x) => acc + x)
```

O `(a b)` antes de `=>` é uma lista de parâmetros, e é a única exceção à regra de que parênteses soltos sempre agrupam.

> **Decisão provisória:** o corpo de uma lambda se estende até o fim da lista onde ela está (como no Haskell). Por isso ela só pode ser o **último** item sem parênteses. Em qualquer outra posição, precisa de parênteses: `f((x => x * 2) xs)`. Ver §12.

### Fixidez

A fixidez de uma função é escolhida na declaração, e a forma da declaração espelha o uso:

```
fn +dot(a: Vec b: Vec) -> float = ...          // prefixa:  dot(a b)
fn +(a: Vec) cross (b: Vec) -> Vec = ...       // infixa:   a cross b
fn +(x: float).squared -> float = ...          // pós-fixa: x.squared
```

- Como a chamada prefixa exige parênteses, três átomos seguidos (`a cross b`) só podem ser uma aplicação infixa. O parser não precisa resolver nomes.
- **Operadores simbólicos** (`+`, `*`, `>>=`) são funções infixas com nome simbólico. Eles vêm sempre de traits (§4) e declaram precedência e associatividade na própria trait (`infixl 6`).
- **Infixas com nome em palavra** têm a precedência mais baixa e são **não-associativas**: `a cross b dot c` é erro e exige parênteses.

### Resolução de `x.nome`

1. Se `x` tem um campo `nome`, é acesso a campo. Módulos são valores, então `math.cross` também é acesso a campo.
2. Se não tem, procura-se uma função pós-fixa `nome` no escopo.
3. Se existirem **os dois**, é erro de compilação.

Para usar uma pós-fixa de outro módulo, ela precisa ser trazida para o escopo antes: `squared = math.squared`.

### Sem sobrecarga

Uma função livre tem um nome e uma assinatura por módulo. O polimorfismo por tipo vem sempre de traits.

---

## 4. Tipos

### Primitivos

- Numéricos com tamanho explícito: `i8 i16 i32 i64 u8 u16 u32 u64 f32 f64`, com os apelidos `int = i64` e `float = f64`.
- **Não há coerção implícita.** `i32 + f32` é erro, e a conversão é explícita: `x.f32`.
- `bool`, `()`.
- `Text`: imutável, UTF-8, com small-string optimization, **não copiável** (se move). Não há indexação por posição; usa-se `t.chars`, `t.bytes` ou `t.graphemes`, que são generators.

### Records e sum types

```
type +Vec   = { +x: float  +y: float }
type +Shape = Circle(radius: float) | Rect(w: float h: float)

fn +area(s: Shape) -> float = match s
    Circle(r) -> pi * r ** 2
    Rect(w h) -> w * h
```

### Genéricos

A aplicação de tipo usa a forma da chamada: `Maybe(int)`, `Result(T E)`, `Gen(int)`. As variáveis de tipo são as maiúsculas soltas, quantificadas implicitamente.

```
type +Maybe(T) = Just(T) | Nothing
```

### Traits

São nominais, com `impl` explícito e suporte a **higher-kinded types**.

```
trait +Monad(M) =
    fn +pure(x: A) -> M(A)
    fn +(m: M(A)) >>= (f: A -> M(B)) -> M(B) infixl 1

impl Monad(Maybe) =
    fn pure(x) = Just(x)
    fn (m) >>= (f) = match m
        Just(x) -> f(x)
        Nothing -> Nothing
```

- O `impl` não repete os tipos que a trait já define. A visibilidade dos métodos vem da trait.
- **Regra de órfão:** um `impl` só pode existir no módulo do tipo ou no da trait.
- As **restrições** usam `given`, no fim da assinatura (o `where` é das ligações):

```
fn +sum(xs: List(T)) -> T given Num(T) =
    fold(xs zero (a b) => a + b)
```

### Operadores vêm de traits

As traits do prelude (`Add`, `Sub`, `Mul`, `Div`, `Eq`, `Ord`, ...) definem os operadores e fixam a precedência para todos os tipos:

```
impl Add(Vec) =
    fn (a) + (b) = Vec((a.x + b.x) (a.y + b.y))
```

### Derivação automática

Todo `type` ganha automaticamente `Eq`, `Format`, `Hash` e `Copy` se todos os seus campos as tiverem. Para sair disso:
- escreva o seu próprio `impl` para sobrescrever
- use `@nocopy` ou `@noderive(Eq)` para recusar

### Ausência e erros

- **`Maybe(T)`** é a única forma de ausência. Não existe `null`.
- **`Result(T E)`** representa erros esperados. **`panic`** é reservado para bugs. Não há exceções.

---

## 5. Posse

### Modos de parâmetro

```
fn +length(v: Vec) -> float = ...               // empréstimo imutável (padrão)
fn +integrate(mut pos: Vec vel: Vec) -> () =    // empréstimo mutável
    pos = pos + vel
fn +push(own xs: List(T) own x: T) -> List(T)   // move: a função vira dona

integrate(!p v)              // `!` no argumento marca, NO USO, o que pode ser mutado
xs = push(xs item)           // xs e item são movidos
```

### Regras

- **Tipos copiáveis** (números, `bool`, e records ou sum types compostos só deles) são copiados implicitamente e nunca se movem.
- Os outros tipos se movem quando passados a `own`, retornados ou guardados num construtor. Usar a variável depois do move é erro, a menos que se escreva `.copy` (exige a trait `Clone`).
- Guardar um parâmetro **emprestado** dentro de algo (construtor, lista, generator, closure que escapa) é **erro**. O compilador sugere `own` ou `.copy`.
- Tipos `@nocopy` (handles de arquivo, de GPU) nunca são duplicados.
- Empréstimos nunca são guardados em campos nem capturados por closures que escapam. **Closures, aplicações parciais e generators capturam por move.**
- `xs = sort(xs)`, com `sort` recebendo `own`, reaproveita o buffer sem cópia. Por isso não existe sintaxe de mutação in-place.

---

## 6. Avaliação

### Estrita, com laziness explícita

```
cache = lazy expensive(x)      // Lazy(int)
v     = cache.force            // avalia na primeira vez e memoriza
```

A força é sempre explícita. As ligações de bloco e de `where` são estritas.

### Generators

Uma função cujo corpo contém `yield` é um generator. Ela retorna `Gen(T)` e produz valores sob demanda.

```
fn +fib() -> Gen(int) =
    var #(a b) = #(0 1)
    while true
        yield a
        #(a b) = #(b (a + b))
```

### Tail calls

Toda chamada em posição de cauda é garantida, inclusive em recursão mútua. A anotação `@tail f(x)` faz o compilador dar erro se a chamada **não** estiver em posição de cauda.

---

## 7. Compreensões

A mesma forma cobre loops, consultas, generators e encadeamento de monads.

```
// loop: executa pelo efeito, resultado ()
for e <- enemies
    hp = hp - e.damage

// loop mutando os elementos da fonte
for !pos vel <- world.query(Pos Vel)
    pos = pos + vel

// compreensão: produz valores
hits = for e <- enemies, e.alive
    yield e.damage

// monad (Maybe, Result, Pending, ...)
port = for cfg <- readConfig(path)
           p   <- lookup("port" cfg)
           yield p
```

### Açúcar

```
for a b <- src  body            ⇒  each(src (a b) => body)
for !a b <- src body            ⇒  eachMut(!src (mut a b) => body)
for x <- src, cond  yield e     ⇒  map(filter(src x => cond) x => e)
for x <- a  y <- b  yield e     ⇒  a >>= (x => map(b y => e))
```

- Vários nomes no `for` dispensam a tupla.
- O `!` no padrão só é permitido no loop (sem `yield`), porque numa compreensão com `yield` o empréstimo mutável escaparia.
- Uma **fonte** é qualquer tipo que implementa `each`/`eachMut` (loops) ou `Monad` (compreensões). O `World` de uma biblioteca de ECS é só mais uma fonte.

### `.?`

O `.?` é uma pós-fixa embutida para retorno antecipado, definida por tipo através da trait `Try`:

```
trait +Try(M) =
    fn +branch(m: M(A)) -> Either(A M(A))    // Left = segue com o valor, Right = sai com isso

fn +loadSave(path: Path) -> Result(Save LoadError) io =
    text  = readFile(path).?
    json  = parseJson(text).?
    level = lookup("level" json).okOr(MissingField("level")).?
    Ok(Save(level))
```

O comportamento por tipo vem da trait. O salto de retorno é feito pelo compilador, porque nenhuma função consegue retornar pela função que a chamou.

---

## 8. IO

### Funções `io` e pedidos

```
fn +update(mut w: World) -> () io =
    w.saving = save(w.state)            // emite um pedido e recebe Pending(SaveResult) na hora
```

- Só funções marcadas `io` podem emitir pedidos. A marca é o aceite explícito de que o IO acontece separado e sem controle exato de quando.
- **Coloração:** funções `io` chamam funções puras e outras funções `io`. Uma função pura **não pode** chamar uma `io`.
- Toda declaração FFI é `io`.

### `Pending(T)`

```
match w.saving.poll                     // Maybe(SaveResult); poll é puro
    Just(r) -> ...
    Nothing -> ()

fn +main() -> Pending(int) io =         // Pending é Monad: encadeamento por compreensão
    for text <- readFile("save.json")
        _    <- print(text)
        yield 0
```

Um `Pending` é um valor comum: pode circular por código puro e ser guardado no `World`.

### Runtime e ticks

- O programa começa em `main() -> Pending(int) io` e termina quando esse `Pending` resolve. O `int` é o código de saída.
- O runtime só sabe rodar **ticks** e executar pedidos. O game loop é biblioteca:
  ```
  fn +main() -> Pending(int) io =
      game.run(init update)
  ```
- O input chega como **snapshot** no começo do tick: é um pedido do tick anterior. Os pedidos de desenho rodam **em ordem no fim do tick**.

### Afinidade de thread

| Declaração | Onde roda |
|---|---|
| `@extern(... main)` (padrão) | thread principal, em ordem, no fim do tick: render, input, janela |
| `@extern(... worker)` | pool de workers, sem ordem garantida: arquivo, rede, descompressão |

### `@debug`

`@debug(expr)` imprime de forma síncrona, não exige `io`, retorna o próprio `expr` e é removido no build de release. É a única exceção ao modelo de IO.

---

## 9. Módulos

Cada arquivo é um módulo. Os módulos são **valores**, no estilo Zig:

```
ecs  = @import("ecs")
math = @import("./math")

ecs.World
squared = math.squared      // trazer uma pós-fixa para o escopo
```

### Builtins

Nomes com `@` são fornecidos pelo compilador: `@import`, `@extern`, `@cImport`, `@debug`, `@tail`, `@nocopy`, `@noderive`.

### FFI com C

```
InitWindow = @extern("C" "InitWindow" (w: i32 h: i32 title: CStr) -> () main)
rl         = @cImport("raylib.h")      // segunda fase: gera as declarações a partir do header
```

Primeiro vem a declaração manual com `@extern`, e depois o `@cImport` via libclang.

---

## 10. Implementação

- O compilador é escrito em **Python** e gera LLVM IR com **llvmlite** ([ADR 0003](adr/0003-llvm-como-backend.md)).
- Tail calls usam `tailcc` mais `musttail`. Generators usam os intrínsecos `llvm.coro.*`.
- O runtime traz o loop de ticks, a fila de pedidos, a thread principal e o pool de workers.
- Self-hosting fica para muito depois.

---

## 11. O que saiu em relação à v1

| v1 | v2 |
|---|---|
| `entity`, componentes, `when` | biblioteca de ECS sobre compreensões ([ADR 0001](adr/0001-ecs-como-biblioteca.md)) |
| capacidades estruturais | traits nominais com HKT |
| `!` como marcador de público | `+` antes do nome |
| `mut fn` | empréstimo `mut` no parâmetro e `!` no argumento |
| `T?`, `?.`, `??`, `!!` | `Maybe(T)` e `.?` |
| exceções unchecked | `Result` e `panic` |
| `^` retorno antecipado | `.?` para monads; nos demais casos, a última expressão |
| coerção numérica | conversão explícita |
| chaves e `const`/`let` | layout, com ligação imutável e `var` |
| `"""docstring"""`, `#` | `///`, `//` |
| backend C | LLVM via llvmlite |

---

## 12. Pontos em aberto

- **Lambdas em listas de argumentos.** A regra provisória (o corpo se estende até o fim da lista, então a lambda só pode ser o último item sem parênteses) precisa ser confirmada.
- **Concorrência.** Fora do escopo por enquanto. O modelo de posse sem aliasing mutável deve facilitar a adição depois.
- **Efeitos tipados.** Hoje a marca `io` é binária. Efeitos mais finos ficam para uma versão futura.
- **Gerenciador de pacotes e build na própria linguagem** (estilo `build.zig`).
- **`@cImport`.** Fica para a segunda fase da FFI.

---

## Apêndice: exemplo

```
ecs  = @import("ecs")
game = @import("game")

type +Pos = { +x: f32  +y: f32 }
type +Vel = { +x: f32  +y: f32 }

impl Add(Pos) =
    fn (a) + (b) = Pos((a.x + b.x) (a.y + b.y))

/// Move toda entidade que tem posição e velocidade.
fn +movement(mut world: ecs.World) -> () =
    for !pos vel <- world.query(Pos Vel)
        pos = pos + Pos(vel.x vel.y)

/// Desenha cada entidade.
fn +render(world: ecs.World) -> () io =
    for pos <- world.query(Pos)
        game.drawCircle(pos 4.0 game.white)

fn +update(mut world: ecs.World) -> () io =
    movement(!world)
    render(world)
    @debug("entidades: {world.count}")

fn +main() -> Pending(int) io =
    game.run(ecs.World.empty update)
```
