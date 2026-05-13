"""
Basic smoke tests for the parser.
Run with: python test_parser.py
"""

import sys
from compiler.parser import parse, parse_tree, Program, FunctionDecl, EntityDecl, Binding

_failures = 0


def check(label: str, src: str):
    global _failures
    try:
        ast = parse(src + "\n")
        print(f"  OK  {label}")
        return ast
    except Exception as e:
        print(f"  FAIL {label}: {e}")
        _failures += 1
        return None


def check_fail(label: str, src: str, must_contain: str = "Syntax error at line"):
    global _failures
    try:
        parse(src + "\n")
        print(f"  FAIL {label}: expected syntax error, but parsed successfully")
        _failures += 1
    except Exception as e:
        msg = str(e)
        if must_contain and must_contain not in msg:
            print(f"  FAIL {label}: error message missing '{must_contain}'")
            _failures += 1
        else:
            print(f"  OK  {label}")


def section(name: str):
    print(f"\n── {name} {'─' * (50 - len(name))}")


def main():
    section("Bindings")
    check("const int",        'const x = 42\n')
    check("let float",        'let y = 3.14\n')
    check("typed binding",    'const s: string = "hello"\n')
    check("null binding",     'let z: int? = null\n')
    check("compound assign", """\
fn bump!() -> int {
    let c = 1
    c += 2
    c
}
""")

    section("Arithmetic expressions")
    check("add",              'const a = 1 + 2\n')
    check("precedence",       'const b = 1 + 2 * 3\n')
    check("unary neg",        'const c = -1\n')
    check("coalesce",         'const d = x ?? 0\n')
    check("non-null",         'const e = x!!\n')

    section("Strings")
    check("plain string",     'const s = "hello world"\n')
    check("interpolation",    'const g = "hi {name}!"\n')

    section("Collections")
    check("list literal",     'const xs = [1, 2, 3]\n')
    check("empty list typed", 'const ys: [int] = []\n')
    check("map literal",      'const m = {"a": 1, "b": 2}\n')
    check("empty map",        'const m = {}\n')

    section("Functions")
    check("simple fn", 'fn add!(a: int, b: int) -> int { a + b }\n')
    check("early return", """\
fn abs!(x: int) -> int {
    if x < 0 { ^-x }
    x
}
""")
    check("where inline", 'fn wrap!<T>(v: T) -> T where T: int { v }\n')

    section("Entities")
    check("basic entity", """\
entity Position {
    x!: float
    y!: float
}
""")
    check("entity with method", """\
entity Circle {
    radius!: float
    fn area!() -> float { 3.14159 * radius * radius }
}
""")
    check("entity with constructor", """\
entity Position {
    x: float
    y: float
    constructor(x: float, y: float) {
        self.x = x
        self.y = y
    }
}
""")
    check("entity alias", 'entity Id = int\n')

    section("Capabilities")
    check("capability decl", 'capability Movable = {mut move(float, float) -> unit}\n')
    check("capability alias", 'capability Drawable = {draw(Canvas) -> unit}\n')
    check("capability wildcard args", 'fn push!(thing: {move(_, _)}) -> unit { }\n')
    check("capability like", 'capability Pushable = {push like vector.push}\n')
    check("component requires", """\
capability Movable = {move(float, float) -> unit}
component Collider requires Movable {
    radius!: float
}
""")

    section("Control flow")
    check("if else", """\
fn max!(a: int, b: int) -> int {
    if a > b { a } else { b }
}
""")
    check("for loop", """\
fn sum!(xs: [int]) -> int {
    let acc = 0
    for x in xs { acc = acc + x }
    acc
}
""")
    check("match", """\
fn describe!(x: int) -> string {
    match x {
        0 => "zero",
        _ => "other"
    }
}
""")
    check("match structural wildcard type", """\
fn describe_shape!(thing: Shape) -> string {
    match thing {
        {radius: _} => "tem raio",
        _ => "outro"
    }
}
""")
    check("match guard", """\
fn quadrant!(p: Point) -> string {
    match p {
        Point(x: x, y: y) if x > 0 and y > 0 => "q1",
        _ => "other"
    }
}
""")

    section("Try/catch")
    check("try catch", """\
fn safe_div!(a: float, b: float) -> float {
    try {
        divide(a, b)
    } catch DivisionByZero {
        0.0
    }
}
""")
    check("try catch named", """\
fn safe_div2!(a: float, b: float) -> float {
    try { divide(a, b) }
    catch err: DivisionByZero { 0.0 }
}
""")

    section("Generics")
    check("generic fn", 'fn first!<T>(items: [T]) -> T { items[0] }\n')
    check("generic entity", """\
entity Box<T> {
    value!: T
}
""")
    check("where clause", """\
fn process!<T, U>(item: T, container: U) -> T
where
    T: Movable,
    U: Container<T>
{ item }
""")

    section("Attributes")
    check("attribute decl", 'attribute MaxLength(value: int)\n')
    check("attribute use", """\
@deprecated("use new_fn instead")
fn old_fn!() -> unit { }
""")

    section("Extend")
    check("extend", """\
extend Position {
    fn manhattan!(other: Position) -> float {
        abs(x - other.x) + abs(y - other.y)
    }
}
""")

    section("Exceptions")
    check("exception decl", 'exception DivisionByZero\n')
    check("exception with payload", 'exception InvalidArg(message: string)\n')
    check("throw", """\
fn divide!(a: float, b: float) -> float {
    if b == 0.0 { throw DivisionByZero }
    a / b
}
""")

    section("Nullability operators")
    check("optional chain + coalesce", 'const len = name?.length ?? 0\n')

    section("Imports")
    check("simple import", 'import physics\n')
    check("import with items", 'import animation.{move as animate}\n')
    check("dotted import", 'import graphics.canvas\n')

    section("Spawn & select")
    check("spawn", """\
fn run!() -> unit {
    spawn { process_data(input) }
}
""")
    check("select", """\
fn multiplex!() -> unit {
    select {
        v <- ch1 => process_a(v),
        timeout(1) => done()
    }
}
""")

    section("Full program")
    check("linked list node", """\
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
}
""")

    section("Syntax errors")
    check_fail("binding missing value", 'const x =')
    check_fail("import alias missing name", 'import animation.{move as}')
    check_fail("where missing bound", """\
fn bad!<T>(x: T) -> T
where
    T
{ x }
""")
    check_fail("select arm missing body", """\
fn multiplex!() -> unit {
    select {
        v <- ch1 =>,
        timeout(1) => done()
    }
}
""")
    check_fail("missing comma in args", 'fn bad!(x: int y: int) -> int { x }\n')
    check_fail("unterminated list", 'const xs = [1, 2\n')

    if _failures:
        print(f"\n{_failures} failure(s).")
        sys.exit(1)
    print("\nAll parser smoke tests passed.")


if __name__ == "__main__":
    main()
