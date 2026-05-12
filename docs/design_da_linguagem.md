# Design da Linguagem

Documento vivo consolidando as decisões de design tomadas até aqui. Use como referência ao implementar parser, type checker e runtime.

---

## 1. Filosofia

A linguagem é construída em torno de três princípios não-negociáveis:

1. **Sem hierarquia.** Não existe `extends`, `inherits`, classe pai, ou qualquer mecanismo de subtipo nominal. Reuso e polimorfismo se dão exclusivamente por **composição** e **capacidades estruturais**.
2. **Explicitude sobre magia.** Quando algo pode ser ambíguo (conflito de nomes, mutação, capacidade exigida), o compilador exige que o autor seja explícito. Conflito = erro de compilação, nunca resolução implícita.
3. **Capacidades antes de tipos nominais.** "O que algo *faz*" é mais importante que "o que algo *é*". Funções pedem capacidades, não tipos concretos.

---

## 2. Sintaxe básica

### Blocos

Blocos são delimitados por chaves `{}`. Tanto corpos de função quanto corpos de controle de fluxo:

```
fn foo(x: int) -> int {
    if x > 0 {
        x * 2
    } else {
        0
    }
}
```

Nota: `{}` é usado também para capacidades estruturais e para mapas. A desambiguação fica a cargo do parser via contexto sintático (posição de tipo vs. expressão vs. corpo).

### Separadores

**Newline é separador de statement.** `;` é opcional, útil apenas para colocar múltiplos statements em uma única linha:

```
const x = 1
const y = 2
const z = 3
```

```
const x = 1; const y = 2; const z = 3   // também OK
```

Sem `;` no fim de linha, sem ritual visual constante.

### Bindings

```
const x = 42        // imutável (deeply immutable — campos também não mudam)
let y = 0           // mutável

let mut_count = 0
mut_count = mut_count + 1   // OK

const config = Config(...)
config.host = "..."         // erro: const é deeply immutable
```

`const` impede tanto reassign quanto mutação de campos da entidade. `let` permite ambos.

### Literais

```
// números
const n = 42              // int
const x = 3.14            // float

// strings com interpolação
const name = "mundo"
const greeting = "olá, {name}!"

// listas
const xs = [1, 2, 3]              // [int]
const empty: [int] = []           // anotação necessária quando vazio

// mapas
const m = {"a": 1, "b": 2}        // {string: int}
const empty_map = {}              // mapa vazio (anotação ajuda quando ambíguo: {string: int})
```

**Regra de desambiguação de `{}`:** em posição de **expressão**, `{}` é sempre mapa vazio. Em posição de **corpo** (função, condicional, loop), `{}` é bloco vazio. O parser sabe a diferença pelo contexto sintático.

### Retorno

A **última expressão de um bloco é seu valor de retorno** (estilo Rust/OCaml). Para retorno antecipado, use o operador `^`:

```
fn abs(x: int) -> int {
    if x < 0 { ^-x }   // early return
    x                  // retorno implícito (última expressão)
}
```

`^` é apenas para retorno antecipado. Em fluxos simples, prefira deixar a última expressão falar.

### Nulabilidade

Tipos podem ser opcionais com sufixo `?`. Não há unions arbitrárias na linguagem — apenas nulabilidade.

```
fn find(xs: [int], target: int) -> int? {
    for x in xs {
        if x == target { ^x }
    }
    null
}
```

Operadores associados:

- `x?.field` — chain seguro: se `x` é null, resultado é null; senão, acessa `.field`.
- `x ?? default` — coalesce: usa `default` se `x` é null.
- `x!!` — asserção: lança exceção se `x` é null, senão devolve o valor não-null.

```
const len = name?.length ?? 0          // chain + coalesce
const value = lookup(key)!!            // asserção: confia que existe
```

Flow analysis: depois de `if x != null { ... }`, dentro do bloco `x` é tratado como não-null automaticamente.

### Comentários

```
# comentário de linha

"""
Docstring (documentação). Aparece imediatamente antes de uma
declaração e é capturada pelas ferramentas de doc.
"""
fn foo!(x: int) -> int { ... }
```

---

## 3. Tipos primitivos e entidades

A linguagem tem dois tipos de valores: **primitivos** (definidos pela linguagem) e **entidades** (definidas pelo usuário).

### Primitivos

`int`, `float`, `bool`, `string`, `unit`, mais coleções: `[T]` (lista), `{K: V}` (mapa). (Conjunto exato a definir.) Primitivos são sempre minúsculos para distinguir visualmente de entidades, que são `PascalCase`.

### Coerção numérica

**Não há coerção implícita entre tipos numéricos.** `int` não vira `float` automaticamente; conversão é sempre explícita:

```
const n: int = 5
const x: float = n          // erro: tipos incompatíveis
const x: float = n.to_float()    // OK
const y: float = float(n)        // OK (forma alternativa)
```

Igual aplica-se ao reverso (`float → int` perde precisão; precisa ser explícito) e a literais em contexto float:

```
const restitution: float = 1        // erro: 1 é int
const restitution: float = 1.0      // OK
```

O linter pode sugerir o `.0` quando o contexto deixa claro que float era pretendido.

### Entidades

```
entity Position {
    x!: float       // campo público (exportado)
    y!: float
}
```

Sem `class`, sem `new`. Construção é chamada de função:

```
const p = Position(3.0, 4.0)
```

Veja [Visibilidade](#visibilidade) para detalhes do operador `!`.

#### Construtor implícito

Se nenhum construtor for declarado, a linguagem gera automaticamente um que recebe todos os campos na ordem de declaração. Isso cobre a maioria dos casos.

#### Construtor explícito

Quando há lógica de validação ou transformação:

```
entity Position {
    x: float
    y: float

    constructor(x: float, y: float) {
        if x.is_nan() or y.is_nan() {
            throw InvalidArgument("coordenadas inválidas")
        }
        self.x = x
        self.y = y
    }
}
```

Declarar um construtor desabilita o implícito.

#### Construtores nomeados

Use funções de fábrica em vez de múltiplos construtores. Mantém a linguagem simples:

```
fn origin() -> Position { Position(0.0, 0.0) }
fn from_polar(r: float, theta: float) -> Position {
    Position(r * cos(theta), r * sin(theta))
}
```

### Aliases de entidade

Para dar nome semântico a um tipo existente sem criar tipo novo:

```
entity Point = Vector
```

`Point` e `Vector` são o **mesmo tipo**. Operações entre eles funcionam livremente:

```
const p: Point = Vector(1.0, 2.0, 3.0)
const v: Vector = p             // OK, são o mesmo tipo
const sum = p + v               // OK
```

Para um tipo **distinto** que encapsula outro, use composição explícita:

```
entity Point {
    inner!: Vector
}
```

### Acesso a campos e métodos

Campos são acessados sem parênteses: `position.x`. Métodos com argumentos exigem parênteses: `position.distance_to(other)`.

**Métodos `fn` sem argumentos podem dispensar os parênteses** — açúcar para que se pareçam com campos no ponto de uso:

```
entity Circle {
    radius!: float

    fn area!() -> float { 3.14159 * self.radius * self.radius }
}

const c = Circle(2.0)
const r = c.radius     // campo
const a = c.area       // método zero-arg sem parênteses
const b = c.area()     // mesma coisa, com parênteses
```

**Métodos `mut fn` sempre exigem parênteses**, mesmo sem argumentos:

```
list.clear()    // OK
list.clear      // erro: mut fn requer parênteses
```

Isso mantém visualmente explícito quando uma chamada pode mutar o receptor. A distinção (campo / método puro / método mutador) existe na definição, mas no uso, campo e método puro zero-arg são intercambiáveis.

### Self implícito

Dentro de métodos, identificadores não-qualificados resolvem nesta ordem:

1. Locais e parâmetros do método.
2. Campos e métodos de `self`.

Isso permite escrever `x` em vez de `self.x` quando não há ambiguidade:

```
entity Vector {
    x!: float
    y!: float
    z!: float

    fn squared_magnitude!() -> float {
        x * x + y * y + z * z      // self.x, self.y, self.z implícitos
    }
}
```

**Quando há colisão de nome**, o compilador exige qualificação explícita:

```
fn translate!(x: float) -> Vector {
    Vector(self.x + x, self.y, self.z)    // erro sem self.x: ambíguo
}
```

A regra é: identificador não-qualificado vence o de campo apenas se o local/parâmetro existir; se não existir, o campo é usado. Em ambiguidade visual (parâmetro com mesmo nome de campo), o compilador rejeita o acesso não-qualificado e força `self.`.

---

## 4. Capacidades estruturais

O coração da linguagem. Uma **capacidade** é um conjunto de assinaturas de método. Um valor "tem" uma capacidade se possui todos os métodos exigidos — sem declaração explícita, sem `impl`.

### Sintaxe canônica

```
fn integrate(thing: {move(float, float) -> unit}) {
    thing.move(0.0, 1.0)
}
```

Observações:
- Tipos nos argumentos da capacidade não têm nomes (são contrato, não implementação).
- Tipo de retorno após `->`. Use `unit` quando não há retorno.

### Wildcard `_`

Quando o tipo não importa pra quem chama:

```
fn push(thing: {move(_, _)}) { ... }
```

Lê como "tem um método `move` que recebe dois argumentos, qualquer tipo". O compilador valida no call site.

### Operador `like`

Reusa uma assinatura existente sem reescrever:

```
fn push(thing: {move like Position.move}) { ... }
```

Útil quando a assinatura já está descrita por uma implementação canônica.

### Múltiplas capacidades

Use interseção `&`:

```
fn render(thing: {move(float, float) -> unit} & {draw(Canvas) -> unit}) { ... }
```

### Aliases nomeados

Para capacidades recorrentes:

```
capability Movable = {move(float, float) -> unit}
capability Drawable = {draw(Canvas) -> unit}

fn render_world(things: [Movable & Drawable], canvas: Canvas) {
    for thing in things {
        thing.move(0.0, 1.0)
        thing.draw(canvas)
    }
}
```

Aliases são **substituição textual em nível de tipo**. Não criam tipo nominal. Não criam hierarquia.

### Capacidades genéricas

```
capability Container<T> = {
    add(T) -> unit,
    get(int) -> T
}

fn fill<T>(c: Container<T>, items: [T]) { ... }
```

### Listas de capacidades

```
fn push_all(things: [Movable]) { ... }
```

### Estratégia de implementação

Recomendado: **dispatch dinâmico por padrão** (vtable estrutural construída no boundary), **monomorfização como otimização** quando o tipo concreto é conhecido em compilação.

---

## 5. Genéricos

Genéricos são ortogonais a capacidades e funcionam em funções, entidades e capacidades.

### Funções genéricas

```
fn first<T>(items: [T]) -> T { items[0] }

fn map<T, U>(items: [T], f: {call(T) -> U}) -> [U] { ... }
```

Inferência no call site quando possível: `first([1, 2, 3])` infere `T = int`. Forçar explicitamente: `first<int>([1, 2, 3])`.

### Bounds com capacidades

```
fn move_all<T: Movable>(things: [T]) {
    for t in things { t.move(0.0, 1.0) }
}
```

`<T: Movable>` significa "T é qualquer tipo que tem a capacidade `Movable`".

### Múltiplos bounds via `&`

```
fn render<T: Movable & Drawable>(thing: T, canvas: Canvas) { ... }
```

### Cláusula `where` para casos complexos

Quando há vários parâmetros com vários bounds, inline fica ilegível:

```
fn process<T, U>(item: T, container: U) -> T
where
    T: Movable & Drawable,
    U: Container<T>
{ ... }
```

### Entidades genéricas

```
entity Box<T> {
    value!: T
}

entity Pair<A, B> {
    first!: A
    second!: B
}

const p = Pair(1, "olá")    // Pair<int, string> inferido
```

### Parâmetros default

Útil em coleções:

```
entity HashMap<K, V = unit> { ... }

const set: HashMap<string> = HashMap()    // V = unit, vira um set
```

### Inferência local

```
const x = 42                  // int
const xs = [1.0, 2.0, 3.0]    // [float]
const p = Position(0.0, 0.0)  // Position
```

Tipo explícito quando a inferência falha ou pra documentar:

```
const xs: [int] = []          // sem isso, [] é ambíguo
```

### Variância

**Invariante por padrão.** `[Cat]` não é `[Animal]`, mesmo que `Cat` tenha mais capacidades que `Animal`. Em linguagens estruturais, isso evita uma classe inteira de bugs sutis. Pode ser revisitado depois se virar um problema prático.

### Associated types

**Adiados.** Adicionam complexidade significativa ao type checker. Capacidades genéricas resolvem 90% dos casos. Anotado como possível extensão futura.

---

## 6. Pattern matching

Matching estrutural sobre entidades, campos e capacidades. **Ordenado**: o primeiro caso que casa ganha.

### Modo nominal (entidades conhecidas)

Quando você sabe estaticamente que o valor é uma de N entidades:

```
match shape {
    Circle(r)       => 3.14159 * r * r,
    Rectangle(w, h) => w * h,
    Triangle(b, h)  => b * h / 2.0
}
```

Bindings posicionais ou nomeados (`Circle(radius: r)`).

### Modo estrutural por campos (discriminação por forma)

Quando o valor pode ser de qualquer entidade — discrimina pelos acessores presentes. Usa a **mesma sintaxe canônica de capacidades**: nome com tipo. Cada cláusula é um contrato, não só um nome.

```
match thing {
    {radius: float, height: float}   => Cylinder(radius, height),   // tem ambos
    {radius: float}                  => Circle(radius),             // só raio
    {width: float, height: float}    => Rectangle(width, height),   // largura e altura
    _                                => Unknown
}
```

Cada cláusula no padrão é simultaneamente uma **checagem** ("o valor tem um acessor com esse nome e tipo") e um **binding** (a variável recebe o valor com aquele tipo estaticamente).

**`{name: T}` casa tanto campo quanto método `fn` sem argumentos** (graças à equivalência sintática descrita em [Acesso a campos e métodos](#acesso-a-campos-e-métodos)). Use `_` quando o tipo não importar para discriminação:

```
match thing {
    {radius: _}    => "tem raio",
    _              => "não tem"
}
```

A ordem importa: `{radius: float, height: float}` precisa vir antes de `{radius: float}`, senão um cilindro casaria com círculo primeiro.

### Modo estrutural por capacidades (métodos com argumentos)

Para discriminar por métodos que recebem argumentos, a sintaxe canônica completa:

```
match thing {
    {move(float, float) -> unit, draw(Canvas) -> unit}   => render_animated(thing),
    {draw(Canvas) -> unit}                               => render_static(thing),
    _                                                    => skip()
}
```

Wildcards `_` continuam disponíveis para argumentos cujo tipo não importa: `{move(_, _) -> unit}`.

### Guards

Qualquer arm aceita uma cláusula `if`:

```
match value {
    Position(x, y) if x > 0.0       => "primeiro ou quarto quadrante",
    Position(x, _)                  => "x = {x}",
    {radius: float} if radius > 100 => "círculo grande",
    _                               => "outro"
}
```

### Exaustividade

- **Modo nominal**: o compilador checa exaustividade. Se você cobriu todas as entidades possíveis, `_` é desnecessário; se faltar alguma, é erro de compilação.
- **Modo estrutural**: exaustividade não é decidível (qualquer entidade futura pode satisfazer qualquer forma). `_` é praticamente obrigatório.

---

## 7. Mutabilidade (modelo híbrido)

Mutabilidade é **explícita**. O compilador exige marcação tanto na declaração de variável quanto no método que muta.

### Variáveis

```
const p = Position(0.0, 0.0)      // imutável
let q = Position(0.0, 0.0)        // mutável
```

Para detalhes sobre `const` vs `let`, veja [Bindings](#bindings).

### Métodos

```
entity Position {
    x!: float
    y!: float

    mut fn move!(dx: float, dy: float) {
        self.x = self.x + dx
        self.y = self.y + dy
    }

    fn distance_to!(other: Position) -> float {
        sqrt((other.x - self.x).squared() + (other.y - self.y).squared())
    }
}
```

`mut fn` pode mudar `self`. `fn` normal não pode (erro de compilação).

### Regra de chamada

Para chamar `mut fn`, a referência precisa ser mutável:

```
let p = Position(0.0, 0.0)
p.move(1.0, 0.0)   // OK

const q = Position(0.0, 0.0)
q.move(1.0, 0.0)   // erro: q é const (deeply immutable)
```

### Mutabilidade de retornos

Métodos `fn` (não-mutadores) podem devolver referências ao interior do `self` — a referência **herda a mutabilidade do contexto da chamada**. Se você chamou de dentro de um `mut fn` ou via uma variável `let`, vem mutável; se via `const` ou de dentro de uma `fn`, vem imutável.

Isso permite escrever helpers `fn` que retornam partes do `self` sem precisar duplicá-los como `mut fn`. A garantia "isso muta `self`" continua sendo dada exclusivamente por `mut fn`.

### Capacidades e mutação

A capacidade carrega a marca:

```
capability Movable = {mut move(float, float) -> unit}
```

Sem `mut` na capacidade, a função não pode ser mutadora.

---

## 8. Módulos, visibilidade e pacotes

Cada arquivo é um módulo. Imports explícitos, qualificação para desambiguar.

### Visibilidade {#visibilidade}

O operador **`!`** após o nome marca um item como **público** (exportado pelo módulo). Sem `!`, o item é **privado** (acessível apenas dentro do mesmo módulo).

**`!` aplica-se apenas a funções, métodos e campos.**

**Entidades e capacidades são sempre públicas.** Não há `!` nelas — uma vez que estão em escopo, podem ser usadas por qualquer módulo importador. O que controla acesso aos *internos* é o `!` nos campos e métodos.

```
entity Position {
    x!: float          // campo público
    y!: float
    cache: float       // campo privado (visível só no módulo)

    fn distance_to!(other: Position) -> float { ... }   // método público
    fn validate(p: Position) -> bool { ... }            // método privado
}

fn helper!(x: int) -> int { ... }    // função pública
fn internal(x: int) -> int { ... }   // função privada
```

Capacidades são sempre exportáveis:

```
capability Movable = {move(float, float) -> unit}
```

### Importação

```
import physics
import animation.{move as animate}

physics.move(p, 1.0, 0.0)   // qualificação completa
animate(sprite, frame)      // alias
```

### Resolução de conflitos

Se duas capacidades importadas têm `close()` e o tipo implementa ambas, o compilador exige qualificação:

```
thing.IO.close()
thing.Network.close()
```

Sem qualificação em caso ambíguo: erro de compilação.

### Estrutura de projeto

Um projeto é uma pasta contendo um manifesto e um diretório de código fonte. Subpastas formam pacotes hierárquicos (estilo Python).

```
my_project/
├── project.toml          # nome, versão, dependências
└── src/
    ├── main.lang
    ├── physics.lang
    └── graphics/
        ├── canvas.lang
        └── sprite.lang
```

Imports refletem a estrutura de pastas:

```
import physics
import graphics.canvas
import graphics.sprite.{draw}
```

### Gerenciador de pacotes

#### Manifesto (`project.toml`)

```
[package]
name = "meu_projeto"
version = "0.1.0"
authors = ["alice@exemplo.com"]
description = "Um projeto de exemplo"
entry = "src/main.lang"

[dependencies]
http = "^1.2.0"
json = "0.5.3"

[dev-dependencies]
testing = "^2.0"
```

Versionamento segue **SemVer**. Ranges suportados: `^1.2.0` (compatível com 1.2.x até < 2.0), `~1.2.0` (1.2.x apenas), `=1.2.3` (versão exata).

#### Lockfile (`project.lock`)

Gerado automaticamente pelo CLI. Fixa versões exatas de todas as dependências (incluindo transitivas) para builds reproduzíveis. Versionado no git.

#### Diretório de dependências

`.deps/` na raiz do projeto, no `.gitignore`. Cada projeto tem suas próprias — sem dependências globais (estilo Cargo/npm, não `site-packages`).

#### Comandos do CLI

- `lang new <nome>` — cria projeto novo com layout padrão.
- `lang build` — compila o projeto.
- `lang run` — compila e executa.
- `lang test` — descobre e executa funções `@test`.
- `lang add <pacote>` — adiciona dependência ao manifesto.
- `lang publish` — publica pacote no registry.

#### Registry

Um servidor central hospeda pacotes públicos. Registries privados ou locais podem ser configurados via `project.toml`.

---

## 9. Exceções (unchecked)

Qualquer função pode lançar qualquer exceção. Não é necessário declarar na assinatura.

### Lançar

```
fn divide(a: float, b: float) -> float {
    if b == 0.0 {
        throw DivisionByZero
    }
    a / b
}
```

### Capturar

```
try {
    const x = divide(10.0, 0.0)
    print(x)
} catch DivisionByZero {
    print("divisão por zero")
} catch e: ArithmeticError {
    print("erro aritmético: {e.message}")
}
```

### Definir exceções

```
exception InvalidArgument(message: string)
exception DivisionByZero
```

Exceções são entidades especiais — podem carregar dados, não podem ser estendidas (sem hierarquia).

---

## 10. Métodos de extensão

Permite adicionar métodos a tipos existentes (incluindo primitivos), sem modificar o código original.

### Sintaxe

```
extend Position {
    fn manhattan_distance(other: Position) -> float {
        abs(self.x - other.x) + abs(self.y - other.y)
    }
}
```

### Escopo

**Extensões valem apenas onde foram importadas.** Não há efeito global.

```
// arquivo a.lang
import geometry.extensions.manhattan
const d = p.manhattan_distance(q)   // OK

// arquivo b.lang (sem o import)
const d = p.manhattan_distance(q)   // erro: método não encontrado
```

### Regras

- **Não acessam campos privados.** Extensões usam a interface pública.
- **Não podem sobrescrever métodos existentes.** Conflito com método existente do tipo = erro de compilação.
- **Podem adicionar capacidades.** Se uma extensão adiciona `draw(Canvas)`, então `Position` satisfaz `Drawable` *nos arquivos que importam essa extensão*. Isso permite adaptar tipos a capacidades sem tocar no código original.

### Conflitos de extensão

Se dois imports adicionam o mesmo método, o compilador exige qualificação ou rejeita o programa.

---

## 11. Operadores

Operadores são açúcar sintático para chamadas de método com nomes convencionais. Não há `operator+` ou trait dedicada — se sua entidade tem um método com o nome correto, o operador funciona.

### Mapeamento

| Operador | Método | Assinatura típica |
|---|---|---|
| `a + b` | `add` | `add(Self) -> Self` |
| `a - b` | `sub` | `sub(Self) -> Self` |
| `a * b` | `mul` | `mul(Self) -> Self` |
| `a / b` | `div` | `div(Self) -> Self` |
| `a % b` | `mod` | `mod(Self) -> Self` |
| `-a` | `neg` | `neg() -> Self` |
| `a == b` | `equals` | `equals(Self) -> bool` (auto-derivado) |
| `a != b` | (negação de `equals`) | — |
| `a < b`, `a > b`, `a <= b`, `a >= b` | `compare` | `compare(Self) -> int` |
| `a[i]` | `get` | `get(K) -> V?` |
| `a[i] = v` | `set` | `set(K, V) -> unit` |
| `f(args)` | `call` | `call(...) -> R` |

### Comparação

`compare(other)` retorna um `int`: negativo se `self < other`, zero se iguais, positivo se `self > other`. Os quatro operadores de ordenação derivam dele.

### Capacidades correspondentes

Como tudo se expressa por capacidades, há um conjunto built-in associado:

```
capability Addable<T> = {add(T) -> Self}
capability Subtractable<T> = {sub(T) -> Self}
capability Multipliable<T> = {mul(T) -> Self}
capability Comparable<T> = {compare(T) -> int}
capability Indexable<K, V> = {get(K) -> V?, set(K, V) -> unit}
capability Callable<Args, R> = {call(Args) -> R}
```

Funções genéricas podem usá-las como bound:

```
fn sum<T: Addable<T>>(xs: [T], zero: T) -> T {
    let acc = zero
    for x in xs { acc = acc.add(x) }
    acc
}
```

### Exemplo: Vec2

```
entity Vec2 {
    x!: float
    y!: float

    fn add!(other: Vec2) -> Vec2 {
        Vec2(self.x + other.x, self.y + other.y)
    }

    fn sub!(other: Vec2) -> Vec2 {
        Vec2(self.x - other.x, self.y - other.y)
    }

    fn mul!(scalar: float) -> Vec2 {
        Vec2(self.x * scalar, self.y * scalar)
    }
}

const a = Vec2(1.0, 2.0)
const b = Vec2(3.0, 4.0)
const c = a + b           // a.add(b)  -> Vec2(4.0, 6.0)
const d = a * 2.0         // a.mul(2.0) -> Vec2(2.0, 4.0)
```

### Operadores via extensão

Operadores são apenas métodos. Logo, podem ser adicionados via `extend`:

```
extend Vec2 {
    fn div!(scalar: float) -> Vec2 {
        Vec2(self.x / scalar, self.y / scalar)
    }
}
```

Onde a extensão é importada, `vec / 2.0` passa a funcionar.

### Operadores compostos

`+=`, `-=`, `*=`, `/=`, `%=` desugar puramente:

```
a += b      // equivalente a: a = a + b
a -= b      // equivalente a: a = a - b
a *= b      // equivalente a: a = a * b
a /= b      // equivalente a: a = a / b
a %= b      // equivalente a: a = a % b
```

Requisitos: `a` precisa ser binding `let` (mutável) ou campo de uma entidade mutável no contexto, e o operador binário correspondente deve estar disponível para os tipos.

### Limitações

- **Receiver à esquerda.** `a + b` sempre chama `a.add(b)`, nunca `b.radd(a)`. Consequência: `2.0 * vec` não compila; escreva `vec * 2.0`.
- **Sem operador XOR.** `^` é reservado para early return. Quem precisar usa `a.xor(b)`.
- **Sem sobrecarga.** Um método `add` é único por entidade — não pode ter múltiplas definições por tipo de argumento. Use genéricos ou métodos diferentes.

---

## 12. Igualdade e derivação automática

### Igualdade estrutural por padrão

Toda entidade vem com igualdade estrutural automática: dois valores são iguais se todos os campos forem iguais (recursivamente).

```
const p1 = Position(1.0, 2.0)
const p2 = Position(1.0, 2.0)
p1 == p2   // true
```

Para primitivos, usa-se a igualdade natural do tipo.

### Capacidades derivadas automaticamente

Toda entidade implementa automaticamente os métodos:

- `equals(Self) -> bool` — igualdade estrutural
- `hash() -> int` — hash baseado em todos os campos
- `show() -> string` — representação textual (e.g., `"Position(x: 1.0, y: 2.0)"`)

Como capacidades são estruturais, isso significa que toda entidade satisfaz automaticamente:

```
capability Eq = {equals(Self) -> bool}
capability Hashable = {hash() -> int}
capability Showable = {show() -> string}
```

Sem cerimônia, sem `derive` annotation. O método existe; a capacidade é satisfeita.

### Sobrescrita

Para sobrescrever, basta declarar o método com a assinatura esperada. O compilador detecta e suprime a derivação automática:

```
entity Position {
    x!: float
    y!: float
    cache: float

    // ignora cache na comparação:
    fn equals!(other: Position) -> bool {
        self.x == other.x and self.y == other.y
    }
}
```

Quando há sobrescrita, `hash` deve ser sobrescrito junto se `equals` for sobrescrito (regra clássica: valores iguais devem ter hashes iguais). O compilador avisa se você sobrescrever só um dos dois.

---

## 13. Eventos reativos (`when`)

O `when` registra um watcher sobre uma condição. Toda vez que uma atribuição **muda um valor** e a condição é verdadeira, o bloco executa.

### Sintaxe

```
when <condição> {
    // corpo
}
```

### Semântica

O `when` não é um `if`. Ele é reativo: permanece ativo enquanto o escopo estiver vivo, e reavalia a condição após cada atribuição que efetivamente altera um valor.

Três critérios para o bloco executar:
1. Uma atribuição aconteceu.
2. O valor atribuído é diferente do anterior.
3. A condição avalia como `true`.

```
fn main() -> int {
    let x = 0

    when x > 1 {
        println("x is {x}!")
    }

    x = 1    # x mudou, mas x > 1 é false — nada
    x = 3    # x mudou e x > 1 — imprime "x is 3!"
    x = 5    # x mudou e x > 1 — imprime "x is 5!"
    x = 5    # x não mudou — nada
    x = 3    # x mudou e x > 1 — imprime "x is 3!"

    0
}
```

### Escopo e lifecycle

O `when` pode aparecer em qualquer lugar: funções, métodos, blocos. O watcher é **desativado automaticamente** quando o bloco onde foi registrado termina.

```
fn process() {
    let counter = 0

    if some_condition {
        when counter > 10 {
            println("limite atingido")
        }
        // watcher ativo aqui
    }
    // watcher desativado aqui (bloco do if terminou)
}
```

### Re-entrância

Se o corpo de um `when` fizer uma atribuição que poderia disparar outro `when`, a checagem aninhada é ignorada. Isso previne loops infinitos. A próxima atribuição "normal" (fora do corpo do watcher) fará uma nova avaliação.

### Compilação para C

No backend C, o `when` é compilado inline: antes de cada atribuição, o valor antigo é salvo; após a atribuição, se o valor mudou, a condição é avaliada. Não há overhead de runtime quando não há watchers ativos.

---

## 14. IO e concorrência

### IO sem rastreamento de efeitos

Funções podem realizar IO livremente. Não há monad IO, não há cláusula de efeitos, nada na assinatura indica "esta função faz IO".

```
fn read_config!() -> Config {
    const content = file.read("config.toml")
    parse(content)
}
```

Decisão deliberada por simplicidade, no espírito de Go ou Python. O custo é menos garantia estática sobre pureza; o ganho é menos cerimônia.

### Concorrência: spawn + canais

Modelo CSP, inspirado em Go.

#### Spawn

`spawn { ... }` lança um bloco em uma thread leve gerenciada pelo runtime:

```
spawn {
    process_data(input)
}
```

#### Canais

`Channel<T>` é um tipo built-in:

```
const ch: Channel<int> = Channel()

spawn {
    ch.send(42)
}

const value = ch.receive()   // bloqueia até receber
```

Canais podem ser fechados:

```
ch.close()

match ch.receive() {
    Some(v) => process(v),
    None    => println("canal fechado")
}
```

#### Select

Para multiplexar canais e timeouts:

```
select {
    v <- ch1            => process_a(v),
    v <- ch2            => process_b(v),
    timeout(1.second)   => println("timeout")
}
```

### O que está fora do escopo

- `async` / `await`
- Atores
- Memória transacional
- Memória compartilhada com locks (a recomendação é "compartilhe via canais")

A linguagem te dá um modelo de concorrência (lance trabalho, comunique via canais) e para por aí.

---

## 15. Metaprogramação

Dois mecanismos, deliberadamente conservadores: **atributos** e **reflexão em runtime**. Sem macros, sem geração de código em compile-time, sem templates Turing-completos.

### Atributos

Atributos são metadata anexa a declarações. Sintaxe: `@nome` ou `@nome(args)` antes da declaração.

```
@deprecated("use new_move em vez disso")
fn move!(p: Position, dx: float, dy: float) { ... }

@version("2.0")
@author("alice")
entity Config {
    host!: string
    port!: int
}
```

Atributos por si só não fazem nada — são informação. O que eles habilitam é leitura: pelo compilador, por ferramentas, ou em runtime via reflexão.

#### Atributos built-in

A linguagem reconhece um conjunto pequeno de atributos com semântica especial:

- `@deprecated(message: string)` — emite warning quando o item é usado.
- `@reflect` — habilita reflexão em runtime para a entidade (veja abaixo).
- `@inline` — sugere inlining ao compilador.
- `@test` — marca função como teste, executada pelo runner de testes.

Outros podem ser adicionados conforme a linguagem evolui.

#### Atributos customizados

Usuários podem declarar atributos próprios:

```
attribute MaxLength(value: int)
attribute Validates
```

Aplicação:

```
entity User {
    @MaxLength(100)
    name!: string

    @Validates
    fn check_email!() -> bool { ... }
}
```

Atributos customizados não têm efeito automático. Ficam disponíveis via reflexão para que código (de framework, biblioteca, usuário) os leia e tome decisões.

### Reflexão em runtime

Reflexão é **opt-in**: apenas entidades marcadas com `@reflect` carregam metadata em runtime. Isso mantém o custo explícito.

```
@reflect
entity Position {
    x!: float
    y!: float
}
```

Acesso à metadata via função built-in `reflect(value)`:

```
const p = Position(1.0, 2.0)
const info = reflect(p)

info.type_name           // "Position"
info.fields              // [Field("x", float, 1.0), Field("y", float, 2.0)]
info.capabilities        // ["Eq", "Hashable", "Showable"]
info.attributes          // []
```

Para entidades não marcadas com `@reflect`, `reflect(value)` é erro de compilação.

#### Capacidade Reflectable

Reflexão é exposta como uma capacidade:

```
capability Reflectable = {
    type_name() -> string,
    fields() -> [Field],
    capabilities() -> [string],
    attributes() -> [Attribute]
}
```

Toda entidade com `@reflect` automaticamente satisfaz `Reflectable`.

### Casos de uso típicos

- **Serialização genérica.** Uma função `to_json<T: Reflectable>(value: T) -> string` que funciona pra qualquer entidade marcada.
- **Frameworks de validação.** Lê `@MaxLength`, `@Validates`, etc. e age sobre eles.
- **Debug e logging.** Imprimir qualquer valor com seus campos sem implementar `show` manualmente.
- **Test runner.** Descobre funções `@test` por reflexão e as executa.

### O que não tem

- **Macros** de qualquer tipo — sintáticas, procedurais, ou de leitor.
- **CTFE arbitrário.** `const` aceita só expressões simples (literais e operadores).
- **Geração de código.** Nenhum mecanismo gera AST ou código-fonte em compile-time.

A escolha é deliberada: poder de metaprogramação custa em legibilidade, mensagens de erro, tempo de compilação e curva de aprendizado. Atributos + reflexão cobrem a vasta maioria dos casos reais.

---

## 16. Tooling

Plano de ferramentas planejadas, ordenado por prioridade (ordem de implementação).

### Primeira onda (essenciais)

- **Compilador** com mensagens de erro de qualidade, warnings opcionais, e os atributos built-in (`@deprecated`, `@inline`, `@reflect`, `@test`).
- **Formatter oficial.** Pega AST, imprime de novo com regras canônicas. Sem opções de configuração — estilo único na linguagem inteira (estilo `gofmt`/`rustfmt`). Evita debates sobre estilo desde o dia 1.
- **Test runner.** Descobre funções `@test` via reflexão, executa, reporta. Triviais asserções (`assert_eq`, `assert_throws`) na biblioteca padrão.
- **REPL.** Loop interativo para prototipagem e ensino. Mantém estado entre expressões.

### Segunda onda (qualidade de vida)

- **Linter integrado ao compilador.** Não como ferramenta separada — warnings nativos: variável não usada, import não usado, campo público sem docstring, dead code, `let` nunca reassignado (sugere `const`).
- **LSP (Language Server Protocol).** Autocomplete, hover, go-to-definition, find-references, rename. Item mais caro, mas o que mais aumenta a usabilidade da linguagem para devs profissionais. Implementável sobre `tower-lsp` (Rust) ou similar.

### Terceira onda (polimento)

- **Debugger via DAP (Debug Adapter Protocol).** Padrão da indústria; permite integração com VS Code, IntelliJ, etc. Antes disso, `print` resolve.
- **Profiler.** Para investigar performance.
- **Doc generator.** Lê docstrings (`"""..."""`) e gera HTML/Markdown. Análogo a `rustdoc`/`pydoc`.

### Decisões de filosofia

- **Tooling oficial e único.** Não múltiplos formatters, não múltiplos test runners. A linguagem provê uma resposta canônica.
- **Integração no compilador sempre que possível.** Linter dentro do compilador, atributos de teste reconhecidos pela linguagem. Reduz fragmentação.
- **Sem configuração desnecessária.** O formatter não tem opções. O linter tem warnings que você pode silenciar com `@allow(...)`, mas sem arquivos de configuração elaborados.

---

## 17. Resumo de palavras-chave

| Palavra-chave | Uso |
|---|---|
| `entity` | Define um tipo de dados |
| `capability` | Define um alias de capacidade estrutural |
| `extend` | Adiciona métodos a um tipo existente |
| `fn` | Função ou método (não-mutador) |
| `mut` | Marca método como mutador (`mut fn`) |
| `const` | Declara binding imutável (deeply) |
| `let` | Declara binding mutável |
| `null` | Valor de ausência para tipos `T?` |
| `constructor` | Construtor explícito de entidade |
| `match` | Pattern matching |
| `if` / `else` | Condicional |
| `for` / `in` | Iteração |
| `import` | Importa módulo ou item |
| `as` | Alias de import |
| `try` / `catch` | Tratamento de exceção |
| `throw` | Lança exceção |
| `exception` | Define tipo de exceção |
| `self` | Referência ao receptor em métodos |
| `like` | Reusa assinatura existente em capacidade |
| `where` | Cláusula de bounds para genéricos |
| `spawn` | Lança bloco em thread leve |
| `select` | Multiplexa canais |
| `when` | Registra watcher reativo sobre uma condição |
| `attribute` | Declara um atributo customizado |
| `^` (prefixo) | Early return de bloco |
| `?` (sufixo de tipo) | Marca tipo como nullable (`T?`) |
| `?.` | Optional chaining |
| `??` | Coalesce de null |
| `!!` | Asserção de não-null (lança se null) |
| `@` (prefixo) | Aplica atributo a declaração |
| `!` (sufixo de nome) | Marca função/método/campo como público |

---

## 18. Pontos em aberto

Decisões ainda não tomadas. Listadas aqui pra serem resolvidas conforme o design avança.

- **Associated types.** Adiados; revisitar se capacidades genéricas se mostrarem insuficientes na prática.
- **Interop com C / FFI.** Como chamar bibliotecas existentes. Provavelmente similar ao `extern "C"` do Rust.

---

## 19. Identidade da linguagem

Posicionamento informal: algo entre **Go** (interfaces estruturais implícitas, simplicidade), **Rust** (traits, segurança de mutabilidade, extensões via `impl`), e **OCaml** (tipos estruturais anônimos, pattern matching). Com inspiração de **ECS** (entity-component-system) no nível da semântica.

Casos de uso onde deve brilhar: jogos, simulações, sistemas com modelo de domínio rico, qualquer lugar onde "componentes plugáveis" é mais natural que "hierarquia de classes".

---

## Apêndice A: Exemplo prático — lista ligada genérica

Este exemplo serve como sanity check do design. Mostra capacidade genérica, entidade genérica recursiva, métodos com retorno nullable, e operadores de nulabilidade.

```
capability Indexable<T> = {
    get(int) -> T?,
    set(int, T) -> unit
}

entity Node<T> {
    value!: T
    next!: Node<T>?

    fn get!(index: int) -> T? {
        ^get_node(index, 0)?.value
    }

    mut fn set!(index: int, value: T) {
        const node = get_node(index, 0)
        if node != null { node.value = value }
    }

    fn get_node(index: int, i: int) -> Node<T>? {
        if index != i { ^next?.get_node(index, i + 1) }
        self
    }
}
```

Notas sobre o exemplo:

- `Node<T>` satisfaz `Indexable<T>` automaticamente — sem declaração explícita. Se a assinatura dos métodos casar com a capacidade, está implementado.
- `next!: Node<T>?` é um campo público que pode ser null (último nó da lista).
- `^get_node(...)?.value` combina early return, optional chaining, e last-expression-as-return.
- O helper `get_node` é privado (sem `!`) e devolve uma referência que herda mutabilidade do contexto, permitindo que `set!` mute `node.value` mesmo chamando uma `fn`.
