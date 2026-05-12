# double-u

An experimental programming language built around three non-negotiable principles: **no hierarchy**, **explicitness over magic**, and **capabilities over nominal types**.

```
entity Vector {
    x!: float
    y!: float
    z!: float

    fn magnitude() -> float { sqrt(squared_magnitude()) }
    fn dot(other: Vector) -> float {
        self.x * other.x + self.y * other.y + self.z * other.z
    }
    fn add!(other: Vector) -> Vector {
        Vector(self.x + other.x, self.y + other.y, self.z + other.z)
    }
}

fn main!() -> int {
    const a = Vector(1.0, 0.0, 0.0)
    const b = Vector(0.0, 1.0, 0.0)
    printf("%f\n", a.dot(b))   # 0.000000
    0
}
```

---

## Language overview

### No inheritance, ever

There is no `extends` (not in a regular sense i mean), no base class, no subtype polymorphism. Reuse and polymorphism happen exclusively through **composition** and **structural capabilities**.

### Structural capabilities

A capability is a set of method signatures. A value satisfies a capability if it has the required methods — no declaration, no `impl` block.

```
capability Movable = {move(float, float) -> unit}

fn integrate(thing: {move(float, float) -> unit}) {
    thing.move(0.0, 1.0)
}
```

Any entity that has a `move(float, float) -> unit` method satisfies `Movable` automatically.

### Entities

User-defined value types. No `class`, no `new`. Construction is a function call.

```
entity Position {
    x!: float        # public field
    y!: float        # public field
    cache: float     # private field
}

const p = Position(3.0, 4.0)   # implicit constructor
```

Fields and methods marked with `!` are public. Everything else is private to the module.

### Mutability

Explicit everywhere. Variables are `const` (deeply immutable) or `let` (mutable). Methods that mutate `self` are declared `mut fn`.

```
entity Counter {
    value: int

    mut fn increment!() -> unit { self.value = self.value + 1 }
    fn get!() -> int { self.value }
}

let c = Counter(0)
c.increment()   # OK
const d = Counter(0)
d.increment()   # error: d is deeply immutable
```

### Operator overloading

Operators desugar to method calls. `a + b` calls `a.add(b)`, `a - b` calls `a.sub(b)`, and so on. If your entity has the method, the operator works automatically.

```
entity Vec2 {
    x!: float
    y!: float
    fn add!(other: Vec2) -> Vec2 { Vec2(self.x + other.x, self.y + other.y) }
    fn mul!(scalar: float) -> Vec2 { Vec2(self.x * scalar, self.y * scalar) }
}

const v = Vec2(1.0, 2.0) + Vec2(3.0, 4.0)   # Vec2(4.0, 6.0)
const w = v * 2.0                             # Vec2(8.0, 12.0)
```

### Implicit self

Inside methods, unqualified names resolve to `self` fields when there is no local with the same name.

```
entity Vector {
    x!: float; y!: float; z!: float

    fn squared_magnitude() -> float {
        x * x + y * y + z * z   # self.x, self.y, self.z
    }
}
```

### Type aliases

`entity Point = Vector` makes `Point` and `Vector` the **same type**. All methods and operators work freely across the alias.

```
entity Point = Vector

const p: Point = Vector(1.0, 2.0, 3.0)
const v: Vector = p            # OK, same type
const sum = p + v              # OK, dispatches to Vector__add
```

### Generics

Orthogonal to capabilities. Work on functions, entities, and capabilities.

```
fn first!<T>(items: [T]) -> T { items[0] }

entity Box<T> {
    value!: T
}

fn move_all!<T: Movable>(things: [T]) {
    for t in things { t.move(0.0, 1.0) }
}
```

### Pattern matching

Ordered structural matching over entities, fields, and capabilities.

```
match shape {
    Circle(r)        => 3.14159 * r * r,
    Rectangle(w, h)  => w * h,
    _                => 0.0
}

match thing {
    {radius: float, height: float} => "cylinder",
    {radius: float}                => "circle",
    _                              => "unknown"
}
```

### Nullability

Types are non-null by default. Nullable types are written `T?`. Three operators handle null:

```
const len = name?.length ?? 0    # optional chain + coalesce
const val = lookup(key)!!        # non-null assertion (throws if null)
```

### Exceptions

Unchecked. Any function can throw any exception. Signatures do not declare exceptions.

```
exception DivisionByZero

fn divide!(a: float, b: float) -> float {
    if b == 0.0 { throw DivisionByZero }
    a / b
}

fn safe(a: float, b: float) -> float {
    try { divide(a, b) }
    catch DivisionByZero { 0.0 }
}
```

### Extension methods

Add methods to existing types, including primitives, without modifying original code. Extensions are scoped to the file that imports them.

```
extend Position {
    fn manhattan!(other: Position) -> float {
        abs(self.x - other.x) + abs(self.y - other.y)
    }
}
```

### Reactive events

`when` registers a watcher on a condition. Every time an assignment changes a value and the condition is true, the block executes.

```
fn main() -> int {
    let x = 0

    when x > 1 {
        println("x is {x}!")
    }

    x = 1    # x changed but x > 1 is false — nothing
    x = 3    # x changed and x > 1 — prints "x is 3!"
    x = 5    # x changed and x > 1 — prints "x is 5!"
    x = 5    # x did not change — nothing
    x = 3    # x changed and x > 1 — prints "x is 3!"

    0
}
```

### Concurrency

CSP-style with `spawn` and channels.

```
const ch: Channel<int> = Channel()

spawn { ch.send(42) }

const value = ch.receive()

select {
    v <- ch1           => process(v),
    timeout(1.second)  => give_up()
}
```

---

## Syntax at a glance

| Construct | Syntax |
|---|---|
| Immutable binding | `const x = 42` |
| Mutable binding | `let x = 0` |
| Function | `fn name!(params) -> Type { body }` |
| Mutating method | `mut fn name!(params) -> Type { body }` |
| Entity | `entity Name { field!: Type }` |
| Type alias | `entity Alias = OriginalType` |
| Capability | `capability Name = {method(Args) -> Ret}` |
| Extend | `extend Type { fn ... }` |
| Reactive event | `when cond { body }` |
| Early return | `^expression` |
| Nullable type | `T?` |
| Optional chain | `x?.field` |
| Null coalesce | `x ?? default` |
| Non-null assert | `x!!` |
| Public marker | `!` suffix on name |

---

## Compiler pipeline

```
source.uu
    │
    ▼
compiler/parser.py       # Lark Earley parser → AST
    │
    ▼
compiler/semantic.py     # module-level name resolution, duplicate detection
    │
    ▼
compiler/type_checker.py # type-ref resolution, generics, nullability, wildcards
    │
    ├──► compiler/runtime.py    # tree-walking interpreter (python, synchronous MVP)
    │
    └──► compiler/lowering.py   # AST → IR (entities, operators, implicit self)
              │
              ▼
         compiler/ir.py         # IR dataclass definitions
              │
              ▼
         compiler/codegen_c.py  # IR → C99 (structs, methods as free functions)
```

### Key lowering features

- **Entity structs** — `entity Foo { x: float }` → `typedef struct { double x; } Foo;`
- **Methods as free functions** — `Foo.bar(args)` → `Foo__bar(self, args)`
- **Operator desugaring** — `a + b` → `TypeName__add(a, b)` when `a` is an entity
- **Reverse dispatch** — `scalar * vector` → `Vector__mul(vector, scalar)`
- **Implicit self** — bare field/method names inside entity methods resolve to `self`
- **Type alias dispatch** — `Point__sub` resolves to `Vector__sub` automatically
- **Type inference** — `let v = entity_method()` infers the correct C type

---

## Usage

```sh
# Interpret directly
python cli.py run examples/tests.uu

# Compile to C and run natively
python cli.py compile examples/tests.uu --emit-c-only   # emits tests.c
gcc tests.c -o tests_bin && ./tests_bin

# Type-check only
python cli.py build examples/tests.uu

# Run all tests
python cli.py test
```

---

## Project layout

```
double-u/
├── cli.py              # command-line entry point
├── compiler/           # compiler pipeline (Python package)
│   ├── parser.py       # Lark grammar + AST transformer
│   ├── grammar.lark    # Earley grammar definition
│   ├── semantic.py     # module-level semantic analysis
│   ├── type_checker.py # structural type checking
│   ├── ir.py           # intermediate representation nodes
│   ├── lowering.py     # AST → IR lowering
│   ├── codegen_c.py    # IR → C code generation
│   └── runtime.py      # tree-walking interpreter
├── tests/              # automated test suite
├── examples/           # example .uu programs
│   └── tests.uu        # particle physics simulation
└── docs/
    ├── design_da_linguagem.md   # full language design (Portuguese)
    └── gramatica.ebnf           # formal EBNF grammar
```

---

## Design goals

| Goal | Mechanism |
|---|---|
| No class hierarchies | Structural capabilities replace interfaces and inheritance |
| Safe mutation | `const`/`let` + `mut fn` make mutation explicit and auditable |
| Operator ergonomics | Any entity with the right method gets the operator for free |
| Type-safe aliases | `entity Alias = Type` shares all methods; dispatch resolves to canonical name |
| Minimal ceremony | Implicit constructors, implicit self, no `new`, no `impl` |

---

## Status

Early-stage compiler. The following features are **implemented**:

- Parser (full grammar, Earley)
- Semantic name resolution and duplicate detection
- Structural type checking (TypeRef, generics, nullability, wildcards, Self)
- Tree-walking interpreter (entities, operators, pattern matching, exceptions, closures, spawn, reactive `when`)
- C backend (structs, methods, operator desugaring, type aliases, for loops, match, reactive `when`)

Not yet implemented: module system with file imports, full type inference, LSP, formatter.
